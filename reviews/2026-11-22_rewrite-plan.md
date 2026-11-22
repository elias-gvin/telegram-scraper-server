# Rewrite plan: telegram-scraper-server → a correct local archive

> **Historical plan, already implemented. Not instructions.** It was implemented on branch `rewrite-archive` (commits after `4cbade3`). Section 9 lists where the implementation differs from this plan. The code and `README.md` are the source of truth; where they disagree with this file, they win. See `reviews/README.md`.

Status when written: **plan only, nothing implemented.**

Inputs:
- the review `2026-11-22_opus-5.5_review_with-live-export-diff.md`;
- the export report `2026-11-22_export-fidelity_tool-vs-native.md`;
- your answers to the decision questions (recorded in §2).

## 1. Principles (yours, in priority order)

1. **Correctness.** No silent data loss. Anything that cannot be saved is flagged
   and reported loudly.
2. **Small, readable codebase.** Keep old code where it is correct, and rewrite
   only what is wrong.
3. **Edge cases are encapsulated.** Telegram's odd cases live in one place (the
   serializer), not spread around.
4. **UX decisions are yours.** They are listed in §2.

## 2. Decisions (answered)

| Topic | Decision |
|---|---|
| Content that cannot be saved (Telethon-unsupported message, failed media download) | **Finish the sync, flag the message (`incomplete`), report every problem** in the sync response with non-zero counts. |
| `GET /history` for a range that is not synced yet | **Auto-sync the missing part, then read** from the archive. |
| Refresh of edits / reactions / deletions | **Each sync re-reads the last N days** (default 30, a setting). `full=true` re-reads the whole chat. **Deleted messages are kept** and marked `deleted: true`. |
| Accounts / access | **One Telegram account per server** (`--account`). A random **bearer token** is created in `data/token` on first start. The username header is removed. |
| Message shape | **Current flat field names + new fields.** The **raw Telegram message is also stored**, so it can be re-parsed later. |
| Dialog ids | **Marked ids everywhere** (`-100…` channels, `-…` basic groups, positive users). Check during implementation that nothing breaks. Git history shows no deliberate reason for bare ids: they came from `entity.id` in the dialog search. |
| Old cache (`data/dialogs/`) | **Discard and re-sync.** No migration code. I will not delete it; you remove it. |
| Git | **Branch `rewrite-archive`, one commit per phase**, with tests green at each commit. |

## 3. Target design

### 3.1 Storage: one SQLite file per account, plain `sqlite3`

Why I replace SQLModel with the stdlib `sqlite3`:

- Every write here is an upsert (`INSERT … ON CONFLICT DO UPDATE`), and every read
  is one indexed query. The ORM adds a session lifecycle, engines and a model layer
  for nothing.
- About 690 lines in `database/` + `models.py` become one module of about 200
  lines.
- This removes three problems from the review:
  - **fake atomicity**: one `with conn:` per batch, and no helper ever commits;
  - **one engine per call**;
  - **N+1 queries**.

The file is `data/archive.db`. Media goes to `data/media/<dialog_id>/<message_id>-<name>`.

```sql
dialogs  (dialog_id PK, title, username, kind,
          low_id, high_id,          -- covered message-id interval, always contiguous
          reached_start INTEGER,    -- 1 when backfill hit the first message of the chat
          synced_at INTEGER)
messages (dialog_id, message_id, PK(dialog_id, message_id),
          date INTEGER, edit_date INTEGER, sender_id INTEGER,
          deleted INTEGER DEFAULT 0,
          raw TEXT NOT NULL)        -- message.to_dict() as JSON: the source of truth
peers    (peer_id PK, name, username, kind)   -- users/chats/channels seen as sender or forward source
media    (dialog_id, message_id, PK(...),
          status TEXT,              -- downloaded | skipped:<reason> | failed:<error>
          path TEXT, size INTEGER)
```

- **Schema version.** `PRAGMA user_version` plus an ordered list of migration SQL
  strings, about 10 lines. It is used for future changes, not for the old cache.
- **The flat columns** (`date`, `edit_date`, `sender_id`) exist only for querying.
  **Everything the API returns is derived from `raw`.** A future field therefore
  means a serializer change, not a re-download.

### 3.2 Sync: id-based, extends only from the edges

Message ids only grow within a chat. The archive keeps **one contiguous covered
interval** `[low_id, high_id]` per dialog, and it is written **in the same
transaction** as each batch of messages.

```
sync(dialog, since=None, full=False):
  1. newer:    iter_messages(min_id=high_id, reverse=True)     -> batches, high_id grows
  2. older:    if not reached_start and (since is None or oldest covered date > since):
               iter_messages(offset_id=low_id)  (newest->oldest) until date < since
                                                -> batches, low_id shrinks; reached_start=1 when exhausted
  3. refresh:  re-read messages from (now - N days), or everything if full=True
               -> upsert unconditionally (edits, reactions, poll votes)
               -> ids in that window that Telegram no longer returns -> deleted=1
  4. media:    for each new/changed message with downloadable media allowed by settings
  5. report:   counts + list of problems (incomplete messages, failed media)
```

- **No holes by construction.** Sync never fetches an isolated middle window. An
  interrupted sync leaves a smaller, but still contiguous, interval, and the next
  sync continues from its edges.
- **No duplicates.** Rows are keyed by `(dialog_id, message_id)` and read by id.
- **History is "since date X to now".** You cannot sync only a middle window
  without also covering everything up to now. For your use (archiving chats you
  care about) this is what you want. For a huge channel where you only want
  2020–2021, it means syncing 2020 → now.
- Media is downloaded **during sync only**. History never downloads, so streams do
  not stall. The old `repair_media` becomes "sync downloads media that is
  allowed by settings and not yet on disk".
- **Atomic file replace.** Download to `*.part`, then `os.replace`. Force
  re-download never deletes the good copy first.

### 3.3 One serializer: `raw dict → API dict`

`serialize(raw, peers) -> dict` is the **single** place that knows Telegram's
shapes. History (from the archive) and search (live) both use it. That replaces
three hand-written serializers.

Output: today's flat fields (`message_id, date, edit_date, sender_id,
first_name, last_name, username, message, reply_to, reply_quote_text,
reply_quote_offset, post_author, is_forwarded, forwarded_from_*, media_*`), plus:

| New field | Content |
|---|---|
| `type` | `"message"` or `"service"` |
| `action`, `action_data` | for service messages, e.g. `"pin_message"` with `{"message_id": 123}`. Pins no longer fake `reply_to`. |
| `entities` | `[{type, offset, length, url?, user_id?, custom_emoji_id?, language?}]`. This keeps the URL of every `text_link`. |
| `reactions` | `[{emoji \| custom_emoji_id \| paid, count, recent?: [{from_id, date}]}]` |
| `poll` | `{question, answers: [{text, voters}], total_voters, closed, quiz}` |
| `attachment` | non-file media: `{type: geo \| venue \| contact \| dice \| game \| invoice \| story \| webpage \| …, …}` |
| `incomplete` | `"unsupported_by_telegram_library"` / `"media_failed: …"` / `"unknown_media: <TL type>"`. Absent when the message is complete. |
| `deleted` | `true` if Telegram no longer has it. Absent otherwise. |
| `media_status` | `downloaded` / `skipped` / `failed` |

**Edge-case policy.** Any TL type the serializer does not know becomes
`incomplete: "unknown_…: <TL name>"` and is counted in the sync report. Nothing
is silently empty, and the raw is still stored.

New fields are **omitted when empty**. The old fields keep their current null
behaviour.

### 3.4 HTTP API (breaking changes → `/api/v4`)

| Endpoint | Change |
|---|---|
| all | `Authorization: Bearer <data/token>` instead of `X-Telegram-Username`. CORS middleware removed. |
| `POST /auth/qr…` | requires the token too. The account name comes from `--account`, not from the request body. |
| `GET /history/{dialog_id}` | sync the missing part → read from the archive by `message_id` (keyset), `reverse` supported. SSE: `data:` batches, then **`event: done`** `{count, covered_from, synced_at, problems}` or **`event: error`**. |
| `POST /sync/{dialog_id}` | `since`, `full`. Returns counts + the `problems` list. |
| `GET /files/{dialog_id}/{message_id}` | direct lookup. The UUID scan over all DBs is gone. |
| `GET /search/messages[/{dialog_id}]` | uses the serializer. **`start_date` = lower bound, `end_date` = upper bound** (fixed). `total` renamed to `count` (it is the page size). |
| `GET /search/dialogs`, `/folders` | marked ids. Sorting by message count happens after the counts are fetched. Otherwise kept as is. |
| `/settings` | adds `refresh_days`. `repair_media` is removed (sync always fills allowed, missing media). |

The library API in `telegram_scraper/__init__.py` changes as well. Nothing else in
your workspace imports it; I checked. It will export `Archive`, `sync_dialog` and
`serialize`.

### 3.5 Concurrency

- **One Telethon client per process.** A lock is held **only while the client is
  created**, never across a request or a stream. Telethon handles concurrent
  requests on one client.
- **One sync per dialog at a time** (a per-dialog `asyncio.Lock`). A second
  history request for the same dialog waits for the running sync, then reads.

## 4. File-by-file

Current `src/` is about 4,800 lines.

| File | Now | Action | After (est.) |
|---|---:|---|---:|
| `scraper.py` | 700 | **delete** → `sync.py` | — |
| `database/*` + `models.py` | 780 | **delete** → `archive.py` | — |
| `client.py`, `api/auth_utils.py`, `api/deps.py` | 225 | **delete** → `telegram.py` + a token dependency in `server.py` | — |
| `sync.py` | — | new: §3.2 | ~200 |
| `archive.py` | — | new: §3.1, sqlite3 | ~200 |
| `serialize.py` | — | new: §3.3, the only edge-case home | ~220 |
| `telegram.py` | — | new: single client holder, id normalization | ~70 |
| `media_downloader.py` | 230 | keep classification. Download via temp + replace. Return metadata. | ~170 |
| `config.py` | 216 | keep. Add `account`, `refresh_days`, token file. Drop `repair_media`. | ~200 |
| `server.py` | 222 | keep. Token dependency, no CORS, Docker `--host`. | ~170 |
| `api/history.py` | 351 | rewrite around archive + sync | ~160 |
| `api/search.py` | 382 | keep the queries. Use the serializer, fix dates. | ~200 |
| `api/dialogs.py` | 625 | **keep**. Marked ids, the sort fix. | ~600 |
| `api/auth.py` | 443 | keep the QR state machine. Account from config, token required. | ~400 |
| `api/files.py` | 123 | simplify | ~50 |
| `api/settings.py` | 185 | keep. Small field changes. | ~170 |
| `authenticate.py` | 360 | **keep** | ~360 |
| **Total** | **~4,800** | | **~3,200** |

## 5. Mapping review items → plan

| Review item | Covered by |
|---|---|
| §2.1 hole, §2.2 stale edits, §2.3 duplicates | §3.2 (id-based coverage, refresh window, PK reads) |
| §2.4 truncated stream | §3.4 `done`/`error` events |
| §2.5 fake atomicity | §3.1 one transaction per batch |
| §2.6 OFFSET by date | keyset by `message_id` |
| §3 export losses + Addendum 3 | §3.3 serializer + raw |
| §4 named mess: three serializers, N+1, engines, `sender_id or 0`, `upsert_user` writing nulls, UUID scan, 2013 FIXME, dead code | removed by the new modules. The peer upsert never overwrites a value with null. An unknown sender is `null`, not `0`. |
| §5.2 auth, CORS, path traversal, `force` delete, cross-account cache | §2 decision + §3.4. The account name is validated `^[A-Za-z0-9_-]{1,64}$`. |
| §5.3 lock across yield | §3.5 |
| §7 migrations | `user_version` |
| §7 timezone | dates as unix seconds in storage. API dates as ISO 8601 with `Z`. **This is a small output change.** |
| Addendum 1: Docker host, force-delete media, search dates, `total`, `/files` without auth, settings write failure, root container, ruff | all in. A settings write failure returns 500. `USER` added to the `Dockerfile`. Ruff runs clean on the new/changed files only, not on the untouched `dialogs.py`/`authenticate.py`. |
| Unsupported `rich_message` | `incomplete` flag + report. The content itself cannot be recovered until Telethon supports it. |

**Out of scope:**
- the dialog search redesign (it works and is not about export correctness);
- background sync jobs. A resumable sync makes them unnecessary: if a long
  `/sync` call times out, calling it again continues where it stopped.
- async SQLite. With one user, blocking for a few ms per batch is fine.

**Deviation from the review:** the review said "don't spend a week migrating
storage". I do replace the storage layer. The reason is that the new
coverage/raw model touches every table anyway, and plain `sqlite3` is **fewer**
lines than the current ORM code (about 200 vs about 780).

## 6. Tests: written first, behaviour only

### 6.1 Test double: real Telethon objects

The old `FakeMessage` dataclass is why none of the fidelity losses were caught.
The new fake client:

- builds **real** `telethon.tl.patched.Message` / `MessageService` objects with
  real TL media, actions, entities, reactions and forward headers;
- implements the `iter_messages` parameters the sync uses (`min_id`, `offset_id`,
  `reverse`, `limit`);
- supports `get_entity`, `get_messages(ids=…)` and `download_media`;
- has test hooks:
  - edit, react to or delete a message "on the server" between syncs;
  - fail after message N, to simulate an interrupted sync;
  - make a media download fail.

### 6.2 Suites

The goal is about 40 focused tests, not hundreds.

1. **`test_export_fidelity.py`**: one "catalog chat" with every kind we know:
   - plain text;
   - text with bold / `text_link` / code / custom emoji;
   - reply + quote;
   - edited message;
   - forwards from a user, a channel and a hidden sender;
   - service messages: pin, title change, phone call, join;
   - poll;
   - reactions;
   - photo, voice, audio, sticker;
   - geo, contact;
   - `MessageMediaUnsupported`;
   - an unknown TL media type.

   Sync, then history. The assertion is a table: *each kind → the fields that must
   appear*, with no empty plain messages for service/media/poll rows. Unsupported
   and unknown kinds must come out as `incomplete` and appear in the sync
   `problems`.
2. **`test_sync.py`**: the invariants:
   - first sync with `since` → only that range; history for an older range
     auto-extends; **no holes, no duplicates** for any sequence of requests;
   - new messages are picked up incrementally;
   - an **interrupted sync** surfaces the error, and the next sync completes with
     no holes;
   - edits, reactions and poll votes are refreshed inside the window; `full=true`
     refreshes outside it;
   - a deleted message is kept with `deleted: true`;
   - a failed media download → `incomplete` + a problem in the report, and the
     message is still saved.
3. **`test_history_api.py`**:
   - the stream ends with `done` and the right `count`;
   - a failure mid-stream ends with an `error` event;
   - order by id ascending/descending;
   - date bounds are inclusive and correct.
4. **`test_access.py`**:
   - every route returns 401 without the token or with a wrong token, `/auth/qr`
     included;
   - a cross-origin preflight is not granted;
   - a bad account name is rejected at startup.
5. **`test_files.py`**:
   - a file is served by dialog/message;
   - a skipped/failed download → 404 with the reason;
   - a failed re-download keeps the old file.
6. **Existing** `test_dialogs.py`, `test_settings.py`, `test_auth.py`: adapted to
   the token and marked ids, not rewritten.

### 6.3 Live acceptance

The comparison script goes into `scripts/`. On your five chats it must give:

| Check | Expected |
|---|---|
| text / date / edited / reply_to / forward / sender mismatches | **0**, as before |
| service messages typed as `service` with an `action` | **119** (114 pins with no `reply_to`) |
| messages with `reactions` | **588** |
| `text_link` URLs present in `entities` | **33** |
| other formatting present in `entities` | **92** |
| poll with question and answers | **1** |
| rich posts flagged `incomplete` and listed in `problems` | **2** |
| `audio_file` → `audio` | **3** |

## 7. Phases

Each phase is one commit on `rewrite-archive`, with all tests green.

0. **Branch + harness.** Create the branch. Add the comparison script to
   `scripts/`. Add the new fake Telegram client.
1. **Tests first.** Write §6.2 suites 1–5 against the new API. They are expected
   to fail at first. **Decided:** each test file is committed together with the
   phase that makes it pass, so every commit is green.
2. **`archive.py` + `serialize.py`.** Fidelity tests pass at the unit level
   (raw → dict).
3. **`sync.py` + `telegram.py`.** The sync invariant tests pass.
4. **API:**
   - history/sync/files on the new core;
   - token auth, CORS removed, single account, marked ids;
   - search on the serializer, date fix;
   - the old modules deleted.

   All suites pass.
5. **Docs + packaging:** README, `TELEGRAM_SCRAPER_SKILL.md` (the agent's
   contract), the `Dockerfile` (`--host 0.0.0.0`, non-root), the version bump to
   `0.3.0`.
6. **Live acceptance** (§6.3) on your five chats, then a short report appended to
   the export-fidelity doc.

## 7a. Message id scope (checked against core.telegram.org/api/updates and /api/channel)

What the official docs say:

- **Private chats and basic groups** share **one id sequence per account**. Ids
  are therefore **not dense** inside one chat: other chats take numbers in
  between. They also differ between accounts, so the same DM has different ids
  for you and for the other person.
- **Channels and supergroups** each have **their own sequence**, which is the
  same for all accounts.
- All of these sequences are **monotonically increasing**, and "message IDs are
  used to order messages in chats". Secret chats are the exception, but Telethon
  cannot access them anyway.
- **Scheduled messages** have their own ids until they are sent, and they are not
  part of history.

How each part of the design deals with this:

| Design part | Problem? | Why |
|---|---|---|
| PK `(dialog_id, message_id)`, read by id | **no** | An id is unique *within* a dialog. That is all the key needs. |
| Coverage `[low_id, high_id]` per dialog | **no** | It means "every message *of this dialog* with an id in this range is stored". It never assumes ids are dense. Sync uses `min_id`/`offset_id`, which skip the other chats' numbers. |
| Order by `message_id` | **no** | The docs define id order as chat order. |
| Deletion detection in the refresh window | **no**, if it is done right | Compare **stored ids of this dialog** with the ids Telegram returned. Never treat "a missing number in the range" as deleted. A test will cover this. |
| Media path `media/<dialog>/<message_id>` | **no** | Unique per dialog. |
| One archive, different account | **yes → add a guard** | Private-chat ids are per account. Opening the archive with another account would mix id spaces. **Fix:** store the account's Telegram user id in the archive on the first sync and refuse to start with a different one, with a loud error. About 5 lines. |
| Cross-chat replies | **yes → serializer** | `reply_to_msg_id` can point into another chat (`reply_to_peer_id`). **Fix:** output `reply_to_peer` when it is set, so the id is not read as same-chat. |
| **Basic group → supergroup migration** | **yes → this is a real loss risk** | After migration the supergroup is a new dialog with its own id space. Messages from before the migration stay in the old basic group, and syncing the supergroup does **not** return them. **Fix:** when the channel has `migrated_from_chat_id`, sync also archives the old chat as its own dialog. The sync report, the `done` event and `/search/dialogs` then show `migrated_from`, so the split is visible, not silent. Merging both into one history stream is left out to keep the code simple. |

## 8. Risks / things to verify while implementing

- **Marked ids.** `get_entity(-100…)` must resolve chats that are not in the
  session cache. I will run it live on all five chats, including the DM and both
  basic groups.
- **Basic group → supergroup migration.** The chat gets a new id and the message
  ids restart. This is out of scope, but the service message
  `MessageActionChatMigrateTo` will be visible (typed + `action_data` with the new
  id), not lost.
- **Refresh cost.** Re-reading 30 days of a busy channel can be many requests.
  This is acceptable (Telethon sleeps on flood waits up to 120 s), and the window
  is a setting.
- **`raw` size.** About 1–2 KB per message is expected. The spike measures it on
  your chats.
- **End-of-sync count check.** If Telegram's `get_messages(limit=0).total`
  matches the archived count reliably, it becomes an extra loud check. The spike
  checks this; otherwise it is dropped.

## 9. Implementation notes (what changed against this plan)

Implemented on branch `rewrite-archive`, one commit per phase. Changes and
additions against the plan:

- **History does not download media.** As planned; this needed a `media=False`
  option in `sync_dialog`.
- **`problems` are persistent.** They are stored in an archive table, and every
  sync report lists *all* current problems of the dialog, not only those found
  in that call. `message_id: 0` marks a problem with the whole dialog.
- **End-of-sync count check (added).** The spike showed that Telegram's
  `get_messages(limit=0).total` equals the number of messages actually iterated
  (5849/5849, 4402/4402, 2725/2725). After a sync that reached the first message
  of a chat, an archive holding *fewer* messages than Telegram reports is
  recorded as a `count_mismatch` problem.
- **`migrated_from` is not shown in `/search/dialogs`.** It would cost one
  `GetFullChannel` call per supergroup. Instead, basic groups show
  `migrated_to`, which is free. The sync report still shows `migrated_from`, and
  sync archives the old group.
- **Raw size:** about 2.5–3 KB per message on your chats.
- **The `sqlmodel` dependency is still in `pyproject.toml`.** Poetry was not
  available to update `poetry.lock`. Run `poetry remove sqlmodel`.
- **The dialogs tests keep the old `tests/mock_telegram.py`.** The entities are
  now real Telethon types. Everything else uses `tests/fake_telegram.py`.
- **Ruff runs clean on all new and rewritten files.** The untouched legacy parts
  (QR login internals, dialog search, the auth CLI) still have style warnings.
