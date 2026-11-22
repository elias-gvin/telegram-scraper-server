# Export fidelity: telegram-scraper-server vs Telegram Desktop native export

> **Historical test report. Not instructions.** The first part measures the OLD code (`4cbade3`); the section "After the fix" measures the rewritten code on branch `rewrite-archive`. The comparison script lives in `scripts/compare_with_native_export.py`. See `reviews/README.md`.

Date: 2026-11-22. Tool version: `main` @ `4cbade3` (before the fixes).

## ⚠️ Scope: this is a partial test

The ground truth is five native exports (Telegram Desktop → Export chat history →
JSON) in `~/workspace/tg_chat_exports_samples`. They are chats I want saved, not a
representative sample of Telegram:

| Chat | Kind (native `type`) | Messages in window |
|---|---|---:|
| Diary | private_channel | 105 |
| SAVED INFO | private_group (basic group) | 964 |
| Samosbor RPG | private_channel | 443 |
| Ира Яворская | personal_chat (DM) | 1822 |
| Перестройка | private_group (basic group) | 85 |

**These exports do not cover:**

- large public channels or big supergroups;
- forum topics;
- comments / discussion groups;
- anonymous admins;
- bots and inline bots;
- many media kinds: polls (only 1 sample), geo / venue / contact / dice / games /
  invoices / stories / giveaways, paid media, quizzes;
- service events other than pin, phone call and title change (join/leave,
  invites, migrations, boosts, …).

A "0 mismatches" result below means **"no mismatches among what these chats
contain"**. It does not mean "the tool handles everything". Anything that is not
present in the samples is **untested**.

## Method

1. Sync each chat with the tool's own `sync_messages_to_cache()`:
   - window 2026-09-01 .. 2026-11-23 UTC;
   - `download_media=false` (media metadata is still recorded);
   - a scratch data directory.
2. Join the tool's SQLite rows with the native `result.json` on **message id**.
3. Compare timestamps with `date_unixtime`, because native `date` is local time
   and the tool stores UTC.
4. Count mismatches per field. Only counts were recorded, never message content.

The comparison script used for this run is not in the repository yet. It is
proposed as fix step 0 below, so that the same check can be re-run on any newly
exported chat. This matters most for the large groups and channels that are
missing from this test.

## Results before the fix

### Correct: 0 mismatches in all five chats

| Field | Checked | Mismatches |
|---|---:|---:|
| message presence (both directions) | 3419 | 0 missing. 1 extra, posted after the export was made. |
| date | 3419 | 0 |
| plain text | 3300 regular messages | 0 |
| edited flag + edit timestamp | 842 edited | 0 lost, 0 extra, 0 time mismatches |
| reply_to (regular messages) | 709 | 0 |
| forward flag / name / source id | 467 (49 hidden-sender) | 0 |
| sender id | 3300 | 0 |
| media presence | all | 0 lost |

### Lost or wrong

| Detail | Messages affected | What the tool did |
|---|---:|---|
| **Service events** (114 pin, 3 phone_call, 2 edit_group_title) | 119 | Stored as **regular messages with empty text**. The action type was lost. |
| — pins with a fake `reply_to` | 114 | The pinned message id ended up in `reply_to`, so the rows looked like empty replies. |
| **Reactions** | 588 (31% of the DM) | Not stored at all. |
| **URL behind `text_link`** | 33 | Only the visible text was kept, so the URL was gone. |
| Other formatting (bold, italic, strike, code, pre, blockquote, custom emoji) | 92 | Plain text only. |
| **Poll** | 1 | An empty message. The question and answers were gone. |
| **`rich_message` posts** (structured posts with headings, lists and quotes) | 2 | **Completely empty.** Telethon 1.40 (layer 214) receives `MessageMediaUnsupported` with no text, so nothing reaches the tool, and the tool gave no sign that something was missing. |
| Media kind `audio_file` | 3 | Classified as a generic `files`. |
| Media details (mime type, duration, width/height, sticker emoji, spoiler) | ~600 | Not stored. |

### Not about a single export, but important

These are cache behaviours that show up on the second and later requests, not on a
clean first sync. They are described in the main review:

- holes between two synced date ranges are never fetched;
- edits made after the first sync are never picked up;
- the message at each cache/Telegram boundary is duplicated.

## Proposed fixes (not implemented yet)

In order of value. Steps 0–2 are prerequisites, and steps 3–8 close the gaps
found above.

**0. Commit the comparison script** as `scripts/compare_with_native_export.py`.

- Inputs: a native export directory, a tool DB, and a time window.
- Output: per-field mismatch counts only (no message content).
- Extend it with every fix below, so each fix is verified against real exports.
- Re-run it whenever you export a new kind of chat (large channel, forum,
  supergroup with comments).

**1. Schema auto-migration** (prerequisite, also fixes a live bug).

- The problem: `create_all` never adds columns. Your existing cache DB is already
  missing `reply_quote_text` / `reply_quote_offset`, so the next sync into it will
  fail.
- The fix: on DB open, compare `PRAGMA table_info` with the model and run
  `ALTER TABLE … ADD COLUMN` for missing nullable columns.
- This is about 20 lines of code. It must land before any new column is added.

**2. Store the raw message** (future-proofing).

- Add a `raw` JSON column with `message.to_dict()`. Use a JSON encoder for
  `datetime` and `bytes`.
- Every field Telegram sends is then kept, even ones not used yet: media
  details, `grouped_id`, `via_bot`, views, topics, and so on.
- A new output field later means a code change, not a re-download.

**3. Service messages.**

- Add `message_type: "message" | "service"`.
- Add `action` as a snake_case name from the Telethon class, for example
  `MessageActionPinMessage` → `pin_message`. Add `action_data` with the action's
  fields (title, call duration/reason, invited users, …).
- For pins, put the pinned id in `action_data` and **not** in `reply_to`.

**4. Formatting and link URLs.**

- Add `entities` as a list of `{type, offset, length, url?, user_id?,
  custom_emoji_id?, language?}`, using Telegram's native offsets (UTF-16).
- This keeps every `text_link` URL and all formatting, and it maps 1:1 to
  native `text_entities`.

**5. Reactions.**

- Add `reactions` as a list of `{emoji | custom_emoji_id | paid, count}`.
- Add `recent: [{from_id, date}]` when Telegram provides it (DMs and small
  groups).

**6. Polls and other non-file media.**

- Add `poll` with question, answers, voter counts, `closed` and `quiz`.
- Add a generic `attachment` field for geo / venue / contact / dice / game /
  invoice / story / webpage preview.

**7. Unsupported messages.**

- If Telethon gives `MessageMediaUnsupported`, set `unsupported: true` (or
  `attachment.type = "unsupported"`). Today these rows look like normal empty
  messages; with the flag they are visibly incomplete.
- Track Telethon releases for a newer layer that understands `rich_message`.
  Nothing in this tool can recover that content until the library supports it.

**8. Media details.**

- Store `mime_type`, `duration`, `width`, `height`, `sticker_emoji` and
  `spoiler` in `media_files`.
- Add an `audio` category, so that `audio_file` stops being `files`.

**Out of scope for this report:** the cache coverage bugs (holes, stale edits,
duplicates). They need a design change (id-based sync + read-only history), which
is described in the main review. That should be a separate decision, and probably
a separate change.

### Open questions before implementing

- **Output shape.**
  - Option 1: add the new fields to the existing flat message.
  - Option 2: mirror Telegram Desktop's export format (`text_entities`,
    `reactions`, `type: "service"`, `action`).

  Mirroring the native format makes the comparison script trivial and lets
  existing tooling for native exports read your data.
- **Null fields.** History currently emits `null` for absent fields, while search
  omits them (`exclude_none`). The new fields would mostly be absent, so decide
  whether history should also omit nulls.
- **Existing caches.** Old rows will not have the new fields until they are
  re-synced (`force_refresh=true`). Is that acceptable, or should there be a
  one-off backfill command?

## After the fix (branch `rewrite-archive`, 2026-11-22)

I synced the same five chats with the rewritten tool for the same window, with
media downloads on for Перестройка only (5 MB limit). Then I compared them with
`scripts/compare_with_native_export.py`. The window and the caveats are the same
as above: **this is still a partial test.**

### Still correct: 0 mismatches in all five chats

| Field | Checked | Mismatches |
|---|---:|---:|
| message presence | 3,419 | 0 missing. 1 extra: SAVED INFO, posted after the export. |
| plain text, sender, reply_to | 3,300 regular | 0 |
| edited flag | 841 | 0 |
| forward flag / name / source id | 467 | 0 |
| media type (now with `audio`) | 569 | 0 |

### Previously lost, now kept

| Detail | Before | After |
|---|---:|---:|
| Service events typed as `service` + `action` | 0 / 119 | **119 / 119**, and none with a fake `reply_to` |
| Reactions with matching total counts | 0 / 588 | **588 / 588** |
| Messages whose `text_link` URLs are all present in `entities` | 0 / 34 | **34 / 34** |
| Messages whose formatting entities are all present | 0 / 92 | **92 / 92** |
| Poll question (answers and voters too) | 0 / 1 | **1 / 1** |
| Rich posts | silently empty | **2 / 2 flagged** `incomplete: unsupported_by_telegram_library` and listed in the sync `problems` |
| Media files (Перестройка) | — | 9 downloaded, 2 skipped by the size limit, 0 failed, no `.part` leftovers |

Every archived message (3,420) went through the serializer without an error.

### Still not covered by any test data

The scope warning at the top still applies:

- large channels and supergroups;
- forum topics;
- comments;
- bots;
- other media kinds (geo, venue, dice, paid media, giveaways, stories);
- other service events.

Re-run the comparison script whenever you export such a chat.

## Rich posts and the full comparison (Telethon 1.46, 2026-11-22)

**Rich posts.** Telethon 1.40 (layer 214) received the two rich posts as an
empty `MessageMediaUnsupported`. Telethon 1.46 (layer 233) receives the complete
`rich_message` structure: 5.8 KB and 13.5 KB of raw data. After the upgrade:

- the archive stores the full structure in the raw message;
- the API returns it as plain text (`rich_text`);
- a full re-sync replaced the empty placeholders and cleared both problems.

**Comparison.** The comparison now only covers the period the export covers.
Before, it counted older archived messages as "only in archive". All five
chats, full histories archived:

| Chat | Native msgs | Missing | Only in archive | Field mismatches | Rich text matches the native export |
|---|---:|---:|---:|---:|---|
| Diary | 105 | 0 | 0 | 0 | — |
| SAVED INFO | 964 | 0 | 0 | 0 | 1/1 |
| Samosbor RPG | 445 | 0 | 0 | 0 | — |
| Ира Яворская | 1,822 | 0 | 0 | 0 | — |
| Перестройка | 85 | 0 | 0 | 0 | 1/1 |

**Full-history run (all five chats, since the first message):**

- 178,596 messages archived;
- every one serialized without error;
- no count mismatches against Telegram.

Remaining problems: two DM messages whose self-destructing media expired
(`media_unavailable`).
