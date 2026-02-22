---
name: telegram-scraper
description: Access Telegram message history via a local API server (localhost:8000). Use when user asks to search Telegram dialogs, retrieve messages, list folders, download media, or analyze Telegram conversations.
---

# Telegram Scraper API

Local FastAPI server providing authenticated access to Telegram message history, dialog search, media files, and folder management. Base URL: `http://localhost:8000`

## Security

**NEVER save or persist** the user's Telegram API ID, API Hash, or 2FA password. Request credentials on-the-fly and pass them directly to commands.

## Rate Limiting (FloodWaitError)

Telegram rate-limits aggressive API usage. The server's Telethon client auto-sleeps up to **120 seconds** on `FloodWaitError`. If a `429` response is returned:

1. **Notify the user** with the wait time from the `Retry-After` header
2. Suggest reducing `chunk_size` (50–100) or lowering `telegram_batch_size` via `/api/v3/settings`

All `429` responses follow this format:

```json
{"detail": "Telegram rate limit exceeded. Retry after 42 seconds."}
```

## Server Setup

### 1. Check if running

```bash
curl -s http://localhost:8000/health
# → {"status": "healthy"}
```

### 2. Launch the server

Ask the user for Telegram API credentials from https://my.telegram.org/apps.

**Docker:**

```bash
# Load image if needed (adjust path to your .tar location)
docker images | grep -q telegram-scraper || \
  docker load < /path/to/telegram-scraper-latest.tar

# Start (or restart existing container)
if docker ps -a --format '{{.Names}}' | grep -q '^telegram-scraper$'; then
  docker start telegram-scraper
else
  mkdir -p /root/telegram-scraper-data
  docker run -d -p 8000:8000 \
    -e TELEGRAM_API_ID=USER_PROVIDED_API_ID \
    -e TELEGRAM_API_HASH=USER_PROVIDED_API_HASH \
    -v /root/telegram-scraper-data:/app/data \
    --name telegram-scraper \
    telegram-scraper:latest
fi
```

**Bare-metal (Poetry):**

```bash
export TELEGRAM_API_ID=USER_PROVIDED_API_ID
export TELEGRAM_API_HASH=USER_PROVIDED_API_HASH
tgsc-server --data-dir ./data --port 8000
```

### 3. QR Code Authentication

**Start QR login:**

```bash
curl -s -X POST http://localhost:8000/api/v3/auth/qr \
  -H "Content-Type: application/json" \
  -d '{"username": "SESSION_NAME"}'
```

Response: `{"token": "abc123...", "qr_url": "tg://login?token=...", "message": "..."}`

Use `{"username": "SESSION_NAME", "force": true}` to re-authenticate an existing session (otherwise returns `409` if session file already exists).

**Send the QR code to the user** — render `qr_url` as a QR image. Instruct them: Telegram → Settings → Devices → Link Desktop Device → Scan.

**Poll for status** (every 2–3s; `qr_url` auto-refreshes every ~25s — re-render each time):

```bash
curl -s http://localhost:8000/api/v3/auth/qr/$TOKEN
```

Response: `{"status": "pending|password_required|success|expired|error", "username": "...", "qr_url": "...", "error": null, "message": "..."}`

- `pending` → keep polling, re-render `qr_url` if changed
- `password_required` → submit 2FA (see below)
- `success` → done, session saved
- `expired` / `error` → start over with POST

**Submit 2FA password** (only when status is `password_required`):

```bash
curl -s -X POST http://localhost:8000/api/v3/auth/qr/$TOKEN/2fa \
  -H "Content-Type: application/json" \
  -d '{"password": "USER_2FA_PASSWORD"}'
```

**Cancel a pending QR session:**

```bash
curl -X DELETE http://localhost:8000/api/v3/auth/qr/$TOKEN
```

### 4. Verify authentication

```bash
curl -s -H "X-Telegram-Username: SESSION_NAME" \
  "http://localhost:8000/api/v3/search/dialogs?limit=3"
```

Sessions persist in `{data_dir}/sessions/` and survive server restarts.

---

## API Reference

All endpoints require the `X-Telegram-Username` header with the session name used during authentication.

### 1. Search Dialogs

`GET /api/v3/search/dialogs`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `q` or `query` | string | — | Search query on dialog title (omit for all); both parameter names accepted |
| `match` | enum | `fuzzy` | `fuzzy` (scored) or `exact` (substring) |
| `min_score` | float | `0.8` | Fuzzy score threshold (0.0–1.0) |
| `type` | enum[] | — | `user`, `group`, `supergroup`, `channel`, `bot`, `saved`, `me`. Repeat for multiple: `?type=group&type=supergroup` |
| `folder` | string/int | — | Folder ID or name (case-insensitive) |
| `is_archived` | bool | — | Filter by archive status |
| `min_messages` | int | — | Minimum message count |
| `max_messages` | int | — | Maximum message count |
| `min_participants` | int | — | Minimum participant count |
| `max_participants` | int | — | Maximum participant count |
| `last_message_after` | date | — | `YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS` |
| `last_message_before` | date | — | `YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS` |
| `created_after` | date | — | `YYYY-MM-DD` (channels/groups only) |
| `created_before` | date | — | `YYYY-MM-DD` (channels/groups only) |
| `is_creator` | bool | — | Only dialogs you created |
| `has_username` | bool | — | Only dialogs with/without public @username |
| `is_verified` | bool | — | Only verified entities |
| `sort` | enum | `last_message` | `last_message`, `messages`, `title`, `participants`, `unread` |
| `order` | enum | `desc` | `desc` or `asc` |
| `limit` | int | `50` | Page size (1–500) |
| `offset` | int | `0` | Skip N results for pagination |

**Response:**

```json
{
  "total": 42,
  "offset": 0,
  "limit": 50,
  "results": [
    {
      "id": -1001234567890,
      "type": "channel",
      "title": "Crypto News",
      "username": "cryptonews",
      "is_creator": false,
      "is_verified": true,
      "is_archived": false,
      "message_count": 1523,
      "unread_count": 5,
      "participants_count": 45231,
      "last_message_date": "2024-01-15T14:30:00+00:00",
      "last_message_preview": "Breaking: Bitcoin hits...",
      "created_date": "2020-03-10T12:00:00+00:00"
    }
  ]
}
```

**Examples:**

```bash
# Fuzzy search channels
curl -s -H "X-Telegram-Username: $USERNAME" \
  "http://localhost:8000/api/v3/search/dialogs?q=crypto&type=channel&min_score=0.6"

# Groups + supergroups sorted by participants
curl -s -H "X-Telegram-Username: $USERNAME" \
  "http://localhost:8000/api/v3/search/dialogs?type=group&type=supergroup&sort=participants&order=desc"

# Saved Messages
curl -s -H "X-Telegram-Username: $USERNAME" \
  "http://localhost:8000/api/v3/search/dialogs?type=saved"
```

### 2. List Folders

`GET /api/v3/folders`

```bash
curl -s -H "X-Telegram-Username: $USERNAME" \
  "http://localhost:8000/api/v3/folders"
```

**Response:**

```json
[
  {"id": 0, "title": "All Chats", "is_default": true},
  {"id": 1, "title": "Work", "is_default": false}
]
```

### 3. Search Messages

Search for messages containing specific words or phrases using Telegram's native search API. **No pre-caching required.**

#### Within a specific dialog

`GET /api/v3/search/messages/{dialog_id}`

Uses Telegram's `messages.search` — works on any dialog, even if not previously scraped.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `q` or `query` | string | *required* | Search query (word or phrase to find); both parameter names accepted |
| `start_date` | string | — | Upper bound — messages before this date (`YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS`) |
| `end_date` | string | — | Lower bound — messages after this date (`YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS`) |
| `from_user` | int | — | Only messages sent by this user ID |
| `limit` | int | `50` | Maximum results (1–500) |

#### Across all dialogs (global search)

`GET /api/v3/search/messages`

Uses Telegram's `messages.searchGlobal` — searches across all your chats at once.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `q` or `query` | string | *required* | Search query (word or phrase to find); both parameter names accepted |
| `start_date` | string | — | Upper bound — messages before this date (`YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS`) |
| `end_date` | string | — | Lower bound — messages after this date (`YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS`) |
| `limit` | int | `50` | Maximum results (1–500) |

**Response:**

```json
{
  "query": "bitcoin",
  "total": 12,
  "limit": 50,
  "results": [
    {
      "message_id": 4567,
      "dialog_id": -1001234567890,
      "dialog_name": "Crypto News",
      "date": "2024-01-15 14:30:00",
      "edit_date": null,
      "sender_id": 123456789,
      "first_name": "John",
      "last_name": "Doe",
      "username": "johndoe",
      "message": "Bitcoin just hit a new all-time high!",
      "reply_to": null,
      "post_author": null,
      "is_forwarded": 0,
      "forwarded_from_channel_id": null
    }
  ]
}
```

**Examples:**

```bash
# Search in a specific chat
curl -s -H "X-Telegram-Username: $USERNAME" \
  "http://localhost:8000/api/v3/search/messages/-1001234567890?q=bitcoin"

# Global search across all chats
curl -s -H "X-Telegram-Username: $USERNAME" \
  "http://localhost:8000/api/v3/search/messages?q=meeting"

# Search with date range and sender filter
curl -s -H "X-Telegram-Username: $USERNAME" \
  "http://localhost:8000/api/v3/search/messages/-1001234567890?q=urgent&start_date=2024-06-30&end_date=2024-01-01&from_user=123456789"

# Global search with limit
curl -s -H "X-Telegram-Username: $USERNAME" \
  "http://localhost:8000/api/v3/search/messages?q=invoice&limit=100"
```

### 4. Message History (SSE Streaming)

`GET /api/v3/history/{dialog_id}`

Always use `curl -N` to enable streaming.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `start_date` | string | chat beginning | `YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS` |
| `end_date` | string | now | `YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS` |
| `chunk_size` | int | `100` | Messages per SSE chunk (must be > 0) |
| `force_refresh` | bool | `false` | Bypass cache and re-download from Telegram |

Messages are cached in SQLite per dialog. First request downloads from Telegram; subsequent requests serve from cache (only downloading missing date ranges). Use `force_refresh=true` to bypass.

**Examples:**

```bash
# All messages
curl -N -s -H "X-Telegram-Username: $USERNAME" \
  "http://localhost:8000/api/v3/history/-1001234567890"

# Date range with smaller chunks
curl -N -s -H "X-Telegram-Username: $USERNAME" \
  "http://localhost:8000/api/v3/history/-1001234567890?start_date=2024-01-01&end_date=2024-01-31&chunk_size=50"
```

**SSE format** — each event contains a `messages` array:

```
data: {"messages": [{...}, {...}]}

data: {"messages": [{...}, {...}]}
```

**Message object (flat structure):**

```json
{
  "message_id": 12345,
  "date": "2024-01-15 14:30:00",
  "edit_date": null,
  "sender_id": 123456789,
  "first_name": "John",
  "last_name": "Doe",
  "username": "johndoe",
  "message": "Message content here",
  "reply_to": 12344,
  "post_author": null,
  "is_forwarded": 0,
  "forwarded_from_channel_id": null,
  "media_type": "MessageMediaPhoto",
  "media_uuid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "media_original_filename": null,
  "media_size": 2458624
}
```

Key details:
- `date` / `edit_date` format: `YYYY-MM-DD HH:MM:SS` (UTC), not ISO 8601
- `media_type` uses Telegram class names: `MessageMediaPhoto`, `MessageMediaDocument`, `MessageMediaWebPage`, etc.
- `media_uuid` is `null` when no media; use it with the files endpoint to download
- `media_original_filename` is `null` for photos (only documents/audio carry filenames)
- `is_forwarded`: `0` or `1` (integer, not boolean)

### 5. Download Media

`GET /api/v3/files/{file_uuid}`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `metadata_only` | bool | `false` | Return JSON metadata instead of file content |

**Download file:**

```bash
curl -s -H "X-Telegram-Username: $USERNAME" \
  "http://localhost:8000/api/v3/files/$FILE_UUID" -o photo.jpg
```

**Get metadata only** (useful for local file access):

```bash
curl -s -H "X-Telegram-Username: $USERNAME" \
  "http://localhost:8000/api/v3/files/$FILE_UUID?metadata_only=true"
```

**Metadata response:**

```json
{
  "file_path": "/app/data/dialogs/-1001234567890/media/a1b2c3d4.jpg",
  "original_filename": "document.pdf",
  "size": 2458624
}
```

**Local file access (Docker):** When server runs locally in Docker, you can skip HTTP download and copy files directly. Map `file_path` from container to host based on your volume mount:

```bash
# Container: /app/data/...  →  Host: /root/telegram-scraper-data/...
HOST_PATH="${CONTAINER_PATH/\/app\/data/\/root\/telegram-scraper-data}"
cp "$HOST_PATH" ./my_file.jpg
```

### 6. Settings

`GET /api/v3/settings` — read current settings
`PATCH /api/v3/settings` — update (partial, only supplied fields change)

**Defaults:**

| Setting | Default | Description |
|---------|---------|-------------|
| `download_media` | `true` | Download media files during history scraping |
| `max_media_size_mb` | `20` | Max media size in MB. `0` or `null` = no limit. **Media over 20MB is skipped by default.** |
| `telegram_batch_size` | `100` | Batch size for Telegram API downloads (must be > 0) |
| `repair_media` | `true` | Re-download media previously skipped due to size limits or disabled downloads |
| `download_file_types` | object (see below) | Per-type download toggles: `photos`, `videos`, `voice_messages`, `video_messages`, `stickers`, `gifs`, `files`. Defaults: all `true` except `videos: false`. |

**Update example:**

```bash
curl -s -X PATCH -H "X-Telegram-Username: $USERNAME" \
  -H "Content-Type: application/json" \
  -d '{"download_media": false, "max_media_size_mb": 50}' \
  "http://localhost:8000/api/v3/settings"
```

Changes take effect immediately and persist to `settings.yaml`.

---

## Error Responses

All errors return `{"detail": "..."}` with the appropriate HTTP status code.

| Code | Meaning |
|------|---------|
| `400` | Invalid parameters (bad date format, start_date >= end_date) |
| `401` | Missing `X-Telegram-Username` header or session not authenticated |
| `404` | QR session / media file / folder not found |
| `409` | Session already exists (use `force: true`) or wrong 2FA state |
| `422` | No fields provided for settings update |
| `429` | Telegram rate limit — check `Retry-After` header for wait seconds |
| `500` | Internal server error |
| `502` | Failed to connect to Telegram for QR login |

---

## Data Directory Layout

```
{data_dir}/                   (default: ./data, Docker: /app/data)
├── sessions/                 Telegram session files
├── dialogs/                  Per-dialog SQLite databases + media
│   └── {dialog_id}/
│       ├── {dialog_id}.db
│       └── media/
└── settings.yaml             Runtime settings
```

Docker volume mount maps `/app/data` → host path (e.g. `/root/telegram-scraper-data`).

---

## Quick Tips

1. **Always use `-N` with curl** for SSE streaming endpoints (`/history/`)
2. **Use `chunk_size=50`** for large histories to reduce rate limit risk
3. **Media is capped at 20MB by default** — increase `max_media_size_mb` via settings if needed
4. **Use `force_refresh=true` sparingly** — prefer cached data
5. **Fuzzy search**: `min_score=0.6` for broad results, `0.8`+ for precise
6. **Check server logs**: `docker logs telegram-scraper` for troubleshooting
7. **Interactive API docs**: http://localhost:8000/docs (Swagger UI)
