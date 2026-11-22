---
name: telegram-scraper
description: Access Telegram message history via a local API server (localhost:8000). Use when user asks to search Telegram dialogs, retrieve or archive messages, list folders, download media, or analyze Telegram conversations.
---

# Telegram Scraper API (v4)

A local server for **one** Telegram account. It keeps an archive (SQLite + media
files) of the dialogs you read or sync. Base URL: `http://localhost:8000/api/v4`.

**Endpoints and parameters:** read them from the server itself. No token is
needed for these:

```bash
curl -s http://localhost:8000/openapi.json        # machine-readable: every route, parameter, description
# Human-readable: http://localhost:8000/docs
```

This file only covers what the OpenAPI schema cannot tell you.

## Security

- **NEVER save or persist** the user's Telegram API ID, API hash or 2FA password.
  Ask for them when needed and pass them directly to commands.
- Every `/api/v4/...` request needs `Authorization: Bearer $TOKEN`. The token is
  in `{data_dir}/token`, created on the server's first start:

```bash
TOKEN=$(cat ./data/token)                                   # bare metal
TOKEN=$(docker exec telegram-scraper cat /app/data/token)   # Docker
AUTH="Authorization: Bearer $TOKEN"
```

## Setup

```bash
curl -s http://localhost:8000/health        # → {"status": "healthy"} if it is running
```

To start it, ask for API credentials (https://my.telegram.org/apps) and the
**account name** (the session name chosen at login):

```bash
# Docker
docker run -d -p 127.0.0.1:8000:8000 -e TELEGRAM_API_ID=... -e TELEGRAM_API_HASH=... \
  -e TGSC_ACCOUNT=ACCOUNT_NAME -v /root/telegram-scraper-data:/app/data \
  --name telegram-scraper telegram-scraper:latest
# Bare metal
TELEGRAM_API_ID=... TELEGRAM_API_HASH=... tgsc-server --account ACCOUNT_NAME --data-dir ./data
```

**Login (once per account).** A `401` saying "session is not authorized" means
the login must be done (again).

- **Terminal:** `tgsc-auth ACCOUNT_NAME --data-dir ./data` (interactive).
- **API:**
  1. `POST /auth/qr`. Use body `{"force": true}` to replace an existing session.
  2. Render the returned `qr_url` as a QR image for the user: Telegram →
     Settings → Devices → Link Desktop Device.
  3. Poll `GET /auth/qr/{token}` every 2–3 s and re-render `qr_url` each time
     (it refreshes about every 25 s).
  4. On `password_required`, `POST /auth/qr/{token}/2fa` with `{"password": ...}`.

## Dialog ids

All dialog ids are **marked ids**, exactly as `/search/dialogs` returns them:
channels and supergroups are `-100…`, basic groups are `-…`, and users are
positive. Bare ids are not accepted (`404`).

## Reading history: `GET /history/{dialog_id}` (Server-Sent Events, use `curl -N`)

The server first syncs **only the missing part** of the requested range from
Telegram into the archive, then streams it from the archive, ordered by message
id. It does not re-read messages it already has and does not download media; for
that, use `POST /sync`.

```
data: {"messages": [ {...}, ... ]}          ← one per chunk
event: done
data: {"count": 1234, "incomplete": 2, "sync": {...sync report...}}
```

or, on failure:

```
event: error
data: {"error": "FloodWaitError: ...", "retry_after": 300}
```

**A stream without a final `done` event is incomplete.** Never present it as the
full history.

## Message object

Fields that are always present (`null` when absent):

| Field | Meaning |
|---|---|
| `message_id`, `date`, `edit_date` | ISO 8601 UTC (`2024-01-15T14:30:00Z`). `edit_date` set = the message was edited |
| `type` | `message` or `service` |
| `sender_id`, `first_name`, `last_name`, `username` | Sender. For channel posts, `sender_id` is the channel |
| `message` | Plain text (`""` if none). Formatting is in `entities` |
| `reply_to`, `reply_quote_text`, `reply_quote_offset` | Replied-to message id (same chat unless `reply_to_peer` is set) and the quoted fragment |
| `post_author` | Channel post signature |
| `is_forwarded` (0/1), `forwarded_from_user_id`, `forwarded_from_channel_id`, `forwarded_from_name`, `forwarded_from_date` | Forward origin. Hidden senders only have a name |
| `media_type`, `media_size`, `media_original_filename` | `photos`, `videos`, `voice_messages`, `video_messages`, `stickers`, `gifs`, `audio`, `files` |

Fields present only when they apply:

| Field | Meaning |
|---|---|
| `sender_name` | Display name of the sender (user name or channel title) |
| `rich_text` | Rich posts (structured messages with headings and lists): their text, flattened. `message` is empty for these |
| `action`, `action_data` | Service messages, e.g. `pin_message` with `{"message_id": 12}`, `chat_edit_title` with `{"title": …}`, `phone_call`, `chat_add_user` |
| `entities` | `[{type, offset, length, url?, …}]`. `text_url` carries the hidden link URL. Offsets are UTF-16 |
| `reactions` | `[{emoji \| custom_emoji_id \| paid, count, recent?: [{from_id, date}]}]` |
| `poll` | `{question, answers: [{text, voters}], total_voters, closed, quiz, multiple_choice}` |
| `attachment` | Non-file media: `{type: webpage \| geo \| venue \| contact \| dice \| game \| invoice \| story \| …}` |
| `media_mime_type`, `media_duration`, `media_status` | `media_status`: `pending` / `downloaded` / `skipped` / `failed` / `unavailable` / `missing` (archived, but the file is gone from disk; the next `/sync` re-downloads it) |
| `media_file` | Downloaded files only: path **relative to `{data_dir}/media`**, e.g. `-1001777888999/12405-photo.jpg` |
| `reply_to_peer` | The reply points into **another** chat |
| `topic_id`, `reply_to_story` | Forum topic / reply to a story |
| `deleted` | `true` = deleted on Telegram; the archived copy is kept |
| `incomplete` | **The message is not fully archived.** Values: `unsupported_by_telegram_library` (content invisible to the client library), `unknown_media:<type>`, `media_unavailable`, `media_failed: <error>`, `media_missing_on_disk`, `rich_media_not_downloaded`, `rich_message_partial`, `raw_error: …`, `serializer_error: …`. Always mention this when quoting such a message |

Search results (`/search/messages…`, live on Telegram) use the same shape, plus
`dialog_id` and `dialog_name`.

## Syncing: `POST /sync/{dialog_id}`

This archives new messages and extends history back to `since` (default: the
whole chat). An optional JSON body overrides settings **for this call only**,
with the same fields as `PATCH /settings`. Example:
`{"max_media_size_mb": 500, "refresh_days": 90}`. Nothing is saved. Media
skipped because of an override is checked again by later syncs, with whatever
settings those use. It also re-reads the last `refresh_days` (setting, default 30) for
edits, reactions, poll votes and deletions (`full=true`: re-read everything), and
downloads media allowed by the settings. It is **resumable**: if it times out or
fails, call it again.

The response contains counts plus:

- `problems`: **everything currently not fully archived** in the dialog,
  `[{dialog_id, message_id, problem}]`. `message_id: 0` means the dialog as a
  whole; `count_mismatch` means Telegram reports more messages than the archive
  holds. **Always report problems to the user.**
- `migrated_from`: the group was upgraded from a basic group. Its older history
  is archived under that id; read it with `/history/{migrated_from}`.
- `reached_start`, `covered_from`: how far back the archive goes.

## Media

**On the server's machine:** read the files directly.

- **Path:** each downloaded message in `/history` has `media_file`, relative to
  the media folder. Join the two: `{data_dir}/media/{media_file}`.
- **Media folder:** the server does not report it. Configure it in the client
  (e.g. `./data/media`; for Docker, the host side of the `/app/data` volume
  mount).
- **Storing:** keep (dialog_id, message_id) or `media_file`, never the absolute
  path.

**Over HTTP:** `GET /files/{dialog_id}/{message_id}` returns the file.

- `?metadata_only=true` returns `{file_path, media_file, size}` instead.
- A `404` explains why there is no file: no media, skipped by settings, failed,
  not downloaded yet, or missing on disk. Run `POST /sync`.

## Errors worth knowing

| Code | Meaning |
|---|---|
| `401` | Wrong or missing token, **or** the Telegram session is not authorized: log in again |
| `404` | Unknown dialog (use a marked id from `/search/dialogs`) |
| `409` | Session exists (use `force`), or the archive belongs to another Telegram account |
| `429` | Telegram rate limit: tell the user the `Retry-After` seconds |

## Tips

1. For a big chat, `POST /sync/{id}` first (repeat until it returns), then read
   with `/history`.
2. Run `POST /sync/{id}?full=true` occasionally to catch edits and deletions
   older than `refresh_days`.
3. `/search/dialogs` finds a chat's id. Search is live and needs no archive.
4. Data lives in `{data_dir}`: `archive.db` (raw Telegram messages included),
   `media/`, `sessions/`, `settings.yaml`, `token`.
