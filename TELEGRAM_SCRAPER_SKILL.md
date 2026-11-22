---
name: telegram-scraper
description: Access Telegram message history via a local API server (localhost:8000). Use when user asks to search Telegram dialogs, retrieve or archive messages, list folders, download media, or analyze Telegram conversations.
---

# Telegram Scraper API (v4)

A local FastAPI server for **one** Telegram account. It keeps a local archive
(SQLite + media files) of the dialogs you read or sync, and serves it over HTTP.
Base URL: `http://localhost:8000/api/v4`.

## Security

- **NEVER save or persist** the user's Telegram API ID, API hash or 2FA password.
  Ask for them when needed and pass them directly to commands.
- Every `/api/v4/...` request needs `Authorization: Bearer $TOKEN`. The token
  is created on the server's first start, in `{data_dir}/token`:

```bash
TOKEN=$(cat ./data/token)          # bare metal
TOKEN=$(docker exec telegram-scraper cat /app/data/token)   # Docker
AUTH="Authorization: Bearer $TOKEN"
```

## Rate limiting

Telethon sleeps automatically on Telegram flood waits of up to 120 s. Longer
waits return `429` with a `Retry-After` header (inside a history stream: an
`error` event with `retry_after`). If that happens, tell the user the wait time,
then retry.

## Server setup

### 1. Check if it is running

```bash
curl -s http://localhost:8000/health    # → {"status": "healthy"}   (no token needed)
```

### 2. Launch

Ask the user for API credentials from https://my.telegram.org/apps and for the
**account name** (the session name chosen at login, e.g. `odetilie`).

**Docker:**

```bash
mkdir -p /root/telegram-scraper-data
docker run -d -p 127.0.0.1:8000:8000 \
  -e TELEGRAM_API_ID=USER_PROVIDED_API_ID \
  -e TELEGRAM_API_HASH=USER_PROVIDED_API_HASH \
  -e TGSC_ACCOUNT=ACCOUNT_NAME \
  -v /root/telegram-scraper-data:/app/data \
  --name telegram-scraper telegram-scraper:latest
```

**Bare metal:**

```bash
export TELEGRAM_API_ID=USER_PROVIDED_API_ID TELEGRAM_API_HASH=USER_PROVIDED_API_HASH
tgsc-server --account ACCOUNT_NAME --data-dir ./data --port 8000
```

### 3. Log in (only once per account)

Either run `tgsc-auth ACCOUNT_NAME --data-dir ./data` in a terminal (interactive),
or use QR login over the API:

```bash
curl -s -X POST -H "$AUTH" http://localhost:8000/api/v4/auth/qr
# → {"token": "<qr-session>", "qr_url": "tg://login?token=...", "message": "..."}
```

- Add `-H "Content-Type: application/json" -d '{"force": true}'` to replace an
  existing session (otherwise `409`).
- Render `qr_url` as a QR image for the user: Telegram → Settings → Devices →
  Link Desktop Device.
- Poll every 2–3 s: `curl -s -H "$AUTH" http://localhost:8000/api/v4/auth/qr/<qr-session>`.
  - `status`: `pending` (re-render `qr_url`, it refreshes every ~25 s) |
    `password_required` | `success` | `expired` | `error`.
- 2FA: `curl -s -X POST -H "$AUTH" -H "Content-Type: application/json" -d '{"password": "..."}' .../auth/qr/<qr-session>/2fa`.
- Cancel: `curl -X DELETE -H "$AUTH" .../auth/qr/<qr-session>`.

A `401` saying the session is not authorized means the login must be redone.

---

## Dialog ids

All dialog ids are **marked ids**, exactly as `/search/dialogs` returns them:
channels and supergroups are `-100…`, basic groups are `-…`, and users are
positive. Bare ids (without the `-100` prefix) are not accepted.

## API reference

### 1. Search dialogs — `GET /search/dialogs`

| Parameter | Default | Description |
|---|---|---|
| `q` | — | Search on the dialog title (omit for all) |
| `match` | `fuzzy` | `fuzzy` (scored) or `exact` (substring) |
| `min_score` | `0.8` | Fuzzy threshold 0.0–1.0 |
| `type` | — | `user`, `group`, `supergroup`, `channel`, `bot`, `saved` (`me`). Repeatable |
| `is_archived`, `is_creator`, `has_username`, `is_verified` | — | Boolean filters |
| `min_messages`, `max_messages` | — | Approximate (by last message id) |
| `min_participants`, `max_participants` | — | Member count |
| `last_message_after/_before`, `created_after/_before` | — | `YYYY-MM-DD` |
| `sort` | `last_message` | `last_message`, `messages` (real counts), `title`, `participants`, `unread` |
| `order` | `desc` | `desc` / `asc` |
| `limit`, `offset` | `50`, `0` | Pagination (limit ≤ 500) |

Result items: `id` (marked), `type`, `title`, `username`, `is_creator`,
`is_verified`, `is_archived`, `message_count`, `unread_count`,
`participants_count`, `last_message_date`, `last_message_preview`,
`created_date`, and `migrated_to` (for a basic group that was upgraded: the
supergroup's id).

```bash
curl -s -H "$AUTH" "http://localhost:8000/api/v4/search/dialogs?q=crypto&type=channel&min_score=0.6"
```

### 2. Folders — `GET /folders[?include_dialogs=true]`

This returns custom folders only. With `include_dialogs=true`, each folder lists
its explicitly included dialogs as `{id, title}`.

### 3. Message history — `GET /history/{dialog_id}` (SSE; use `curl -N`)

| Parameter | Default | Description |
|---|---|---|
| `start_date` | beginning of the chat | Inclusive, UTC, `YYYY-MM-DD[ HH:MM:SS]` |
| `end_date` | now | Inclusive, UTC |
| `chunk_size` | `100` | Messages per event (1–1000) |
| `reverse` | `true` | `true` = oldest first, `false` = newest first |

What happens: the server first syncs **only the missing part** of the range from
Telegram into the archive, then streams it from the archive, ordered by message
id. It does **not** re-read messages it already has and does **not** download
media. For those, use `POST /sync`.

```
data: {"messages": [ {...}, ... ]}
...
event: done
data: {"count": 1234, "incomplete": 2, "sync": { ...sync report... }}
```

or, on failure:

```
event: error
data: {"error": "FloodWaitError: ...", "retry_after": 300}
```

**A stream without a `done` event is incomplete.** Never treat it as the full
history.

```bash
curl -N -s -H "$AUTH" "http://localhost:8000/api/v4/history/-1001234567890?start_date=2024-01-01&end_date=2024-01-31"
```

#### Message object

Fields that are **always present** (`null` when absent):

| Field | Meaning |
|---|---|
| `message_id`, `date`, `edit_date` | Dates are ISO 8601 UTC, e.g. `2024-01-15T14:30:00Z`. `edit_date` set = the message was edited |
| `sender_id`, `first_name`, `last_name`, `username` | Sender. For channel posts, `sender_id` is the channel |
| `message` | Plain text (`""` if none) |
| `reply_to`, `reply_quote_text`, `reply_quote_offset` | Reply target id in this chat, and the quoted fragment |
| `post_author` | Channel post signature |
| `is_forwarded` (0/1), `forwarded_from_user_id`, `forwarded_from_channel_id`, `forwarded_from_name`, `forwarded_from_date` | Forward origin. Hidden senders only have a name |
| `media_type`, `media_size`, `media_original_filename` | `photos`, `videos`, `voice_messages`, `video_messages`, `stickers`, `gifs`, `audio`, `files` |
| `type` | `message` or `service` |

Fields that are **only present when they apply**:

| Field | Meaning |
|---|---|
| `sender_name` | Display name of the sender (user name or channel title) |
| `action`, `action_data` | Service messages: e.g. `pin_message` with `{"message_id": 12}`, `chat_edit_title` with `{"title": ...}`, `phone_call`, `chat_add_user`, … |
| `entities` | Formatting: `[{type, offset, length, url?, ...}]`. `text_url` carries the link URL. Offsets are UTF-16 |
| `reactions` | `[{emoji \| custom_emoji_id \| paid, count, recent?: [{from_id, date}]}]` |
| `poll` | `{question, answers: [{text, voters}], total_voters, closed, quiz, multiple_choice}` |
| `attachment` | Non-file media: `{type: webpage \| geo \| venue \| contact \| dice \| game \| invoice \| story \| ...}` |
| `media_mime_type`, `media_duration`, `media_status` | `media_status`: `pending`, `downloaded`, `skipped`, `failed` or `unavailable` |
| `reply_to_peer` | The reply points into **another** chat (`reply_to` is that chat's message id) |
| `topic_id`, `reply_to_story` | Forum topic id / reply to a story |
| `incomplete` | **The message could not be fully archived.** Values: `unsupported_by_telegram_library` (content invisible to the client library), `unknown_media:<type>`, `media_unavailable`, `media_failed: <error>` |
| `deleted` | `true` = deleted on Telegram; the archived copy is kept |

Always check `incomplete` before claiming a message's content is complete.

### 4. Sync — `POST /sync/{dialog_id}`

This archives new messages and extends history back to `since` (default: the
whole chat). It also re-reads the last `refresh_days` (default 30) to pick up
edits, reactions, poll votes and deletions, and downloads the media the settings
allow. An interrupted or timed-out sync can simply be called again: it continues
where it stopped.

| Parameter | Default | Description |
|---|---|---|
| `since` | whole chat | Archive back to this date (UTC) |
| `full` | `false` | Re-read **every** archived message (for old edits or deletions) |

```json
{
  "dialog_id": -1001234567890,
  "new_messages": 1423, "refreshed_messages": 210, "deleted_messages": 1,
  "media_downloaded": 52, "media_skipped": 35, "media_failed": 0,
  "reached_start": true, "covered_from": "2019-03-01T10:00:00Z",
  "migrated_from": null,
  "problems": [{"dialog_id": -1001234567890, "message_id": 881, "problem": "unsupported_by_telegram_library"}]
}
```

- `problems` lists **everything currently not fully archived** in the dialog,
  not only what this call found. `message_id: 0` is a problem with the dialog as
  a whole, e.g. `count_mismatch` (Telegram reports more messages than the archive
  holds). **Report every problem to the user.**
- `migrated_from`: the group was upgraded from a basic group. Its older history
  lives in that dialog, which is synced too; read it with `/history/{migrated_from}`.

### 5. Media — `GET /files/{dialog_id}/{message_id}`

```bash
curl -s -H "$AUTH" "http://localhost:8000/api/v4/files/-1001234567890/4567" -o file
curl -s -H "$AUTH" "http://localhost:8000/api/v4/files/-1001234567890/4567?metadata_only=true"
# → {"file_path": "/app/data/media/-1001234567890/4567-photo.jpg", "size": 2458624}
```

A `404` explains why: there is no media, or it was skipped, failed or not
downloaded yet. Run `POST /sync` to download it.

### 6. Search messages (live on Telegram, not the archive)

- `GET /search/messages/{dialog_id}?q=...`: within one dialog (also `from_user`)
- `GET /search/messages?q=...`: all dialogs
- `start_date` = lower bound and `end_date` = upper bound (UTC). `limit` ≤ 500.

The response is `{query, count, limit, results}`. Each result is a message object
(as above) plus `dialog_id` and `dialog_name`. `count` is the number of results
returned, not the total number of matches.

### 7. Settings — `GET /settings`, `PATCH /settings` (partial)

| Setting | Default | Description |
|---|---|---|
| `download_media` | `true` | Download media during `/sync` |
| `max_media_size_mb` | `20` | `0`/`null` = no limit |
| `telegram_batch_size` | `100` | Messages per archive transaction |
| `refresh_days` | `30` | Days each sync re-reads for edits, reactions and deletions (`0` = off) |
| `download_file_types` | all `true` | `photos`, `videos`, `voice_messages`, `video_messages`, `stickers`, `gifs`, `audio`, `files` |

Media skipped by settings is downloaded by a later `/sync` once the settings
allow it.

---

## Errors

`{"detail": "..."}` with:

| Code | Meaning |
|---|---|
| `400` | Bad date format, or start ≥ end |
| `401` | Missing or wrong token, **or** the Telegram session is not authorized (log in again) |
| `404` | Dialog not found (use a marked id), no media, or QR session not found |
| `409` | Session exists (use `force`), wrong 2FA state, or the archive belongs to another Telegram account |
| `422` | Invalid parameters |
| `429` | Telegram rate limit (`Retry-After`) |
| `500` | Server error (e.g. settings could not be saved) |
| `502` | Could not reach Telegram for QR login |

## Data directory

```
{data_dir}/            (default ./data, Docker /app/data)
├── token              API token
├── settings.yaml      runtime settings
├── sessions/          Telegram session(s)
├── archive.db         all archived messages (raw Telegram JSON included)
└── media/{dialog_id}/{message_id}-{name}
```

## Tips

1. Use `curl -N` for `/history`, and check for the final `done` event.
2. For a big chat, run `POST /sync/{id}` first (repeat if it times out), then
   read with `/history`.
3. Use `POST /sync/{id}?full=true` occasionally to catch edits and deletions
   older than `refresh_days`.
4. Search is live and does not need the archive. History needs a sync, which
   happens automatically.
5. Interactive API docs: http://localhost:8000/docs
