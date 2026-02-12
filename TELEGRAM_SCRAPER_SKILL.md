# Telegram Scraper Server — Skill File

> Curl-based reference for interacting with the Telegram Scraper API.
> Default base URL: `http://localhost:8000`  
> API prefix: `/api/v3`

---

## Authentication

All data endpoints require the header `X-Telegram-Username: <username>`.
The user must have a valid `.session` file on the server (created via QR auth or CLI).

> **Note:** The examples below use `john_doe` as a placeholder. Replace it with any
> username you choose — it can be arbitrary, but it **must stay the same** across
> authentication and all subsequent API requests. The server uses this identifier to
> look up the matching Telegram session file, so a mismatch will result in a 401 error.

---

## Health & Info

```bash
# Root — list available endpoints
curl http://localhost:8000/

# Health check
curl http://localhost:8000/health
```

---

## QR Code Authentication

### Start a QR login session

```bash
curl -X POST http://localhost:8000/api/v3/auth/qr \
  -H "Content-Type: application/json" \
  -d '{"username": "john_doe"}'
# → {"token": "abc123...", "qr_url": "tg://login?token=...", "message": "..."}
```

Force re-auth for an existing session:

```bash
curl -X POST http://localhost:8000/api/v3/auth/qr \
  -H "Content-Type: application/json" \
  -d '{"username": "john_doe", "force": true}'
```

### Poll QR status

```bash
curl http://localhost:8000/api/v3/auth/qr/{token}
# → {"status": "pending", "qr_url": "tg://login?token=FRESH...", ...}
# → {"status": "password_required", ...}
# → {"status": "success", ...}
# → {"status": "expired", ...}
# → {"status": "error", "error": "...", ...}
```

Render the `qr_url` as a QR code, scan it in **Telegram → Settings → Devices → Link Desktop Device**.  
The `qr_url` auto-refreshes every ~25 seconds — re-render it on each poll.

**Render QR in terminal with Python:**

```bash
pip install qrcode
python -c "import qrcode; qrcode.make('tg://login?token=...').save('/tmp/qr.png')"
# or print directly to terminal:
python -c "import qrcode; qr = qrcode.QRCode(); qr.add_data('tg://login?token=...'); qr.print_ascii()"
```

### Submit 2FA password (when status == "password_required")

```bash
curl -X POST http://localhost:8000/api/v3/auth/qr/{token}/2fa \
  -H "Content-Type: application/json" \
  -d '{"password": "your_2fa_password"}'
```

### Cancel a pending QR session

```bash
curl -X DELETE http://localhost:8000/api/v3/auth/qr/{token}
```

---

## Search Dialogs

Search all Telegram dialogs (chats, groups, supergroups, channels, bots, Saved Messages).
All query parameters are optional — omit everything to list all dialogs.

```bash
# List all dialogs (default: 50 results, sorted by last_message desc)
curl -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v3/search/dialogs"

# Fuzzy search by title
curl -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v3/search/dialogs?q=crypto&min_score=0.6"

# Exact (substring) search
curl -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v3/search/dialogs?q=crypto&match=exact"

# Filter by type (repeat for multiple)
curl -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v3/search/dialogs?type=group&type=supergroup"

# Filter by folder (by name or numeric ID)
curl -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v3/search/dialogs?folder=Work"

# Complex filters
curl -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v3/search/dialogs?min_messages=100&last_message_after=2024-01-01&is_archived=false"

# Pagination
curl -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v3/search/dialogs?limit=20&offset=40"

# Sort by participants descending
curl -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v3/search/dialogs?sort=participants&order=desc"

# Saved Messages
curl -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v3/search/dialogs?type=saved"
```

### Query parameters

| Parameter              | Type       | Default        | Description                                                              |
|------------------------|------------|----------------|--------------------------------------------------------------------------|
| `q`                    | string     | —              | Search query on dialog title                                             |
| `match`                | enum       | `fuzzy`        | `fuzzy` (scored) or `exact` (substring)                                  |
| `min_score`            | float 0–1  | `0.8`          | Fuzzy score threshold (only with `match=fuzzy`)                          |
| `type`                 | enum[]     | —              | `user`, `group`, `supergroup`, `channel`, `bot`, `saved` (repeatable)    |
| `folder`               | string/int | —              | Folder name (case-insensitive) or folder ID                              |
| `is_archived`          | bool       | —              | Filter by archive status                                                 |
| `min_messages`         | int        | —              | Minimum approximate message count                                        |
| `max_messages`         | int        | —              | Maximum approximate message count                                        |
| `min_participants`     | int        | —              | Minimum member/participant count                                         |
| `max_participants`     | int        | —              | Maximum member/participant count                                         |
| `last_message_after`   | date       | —              | Last message date lower bound (`YYYY-MM-DD`)                             |
| `last_message_before`  | date       | —              | Last message date upper bound (`YYYY-MM-DD`)                             |
| `created_after`        | date       | —              | Creation date lower bound (`YYYY-MM-DD`, channels/groups)                |
| `created_before`       | date       | —              | Creation date upper bound (`YYYY-MM-DD`, channels/groups)                |
| `is_creator`           | bool       | —              | Only dialogs you created                                                 |
| `has_username`         | bool       | —              | Only dialogs with/without a public `@username`                           |
| `is_verified`          | bool       | —              | Only verified entities                                                   |
| `sort`                 | enum       | `last_message` | `last_message`, `messages`, `title`, `participants`, `unread`            |
| `order`                | enum       | `desc`         | `asc` or `desc`                                                          |
| `limit`                | int 1–500  | `50`           | Page size                                                                |
| `offset`               | int        | `0`            | Results to skip (pagination)                                             |

### Response shape

```json
{
  "total": 142,
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
      "message_count": 5320,
      "unread_count": 12,
      "participants_count": 48000,
      "last_message_date": "2025-12-15T10:30:00+00:00",
      "last_message_preview": "Bitcoin hits new ATH...",
      "created_date": "2020-03-10T08:00:00+00:00"
    }
  ]
}
```

---

## List Folders

```bash
curl -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v3/folders"
# → [{"id": 0, "title": "All Chats", "is_default": true}, {"id": 2, "title": "Work", "is_default": false}, ...]
```

---

## Message History (SSE Streaming)

Fetch message history for a channel. Returns **Server-Sent Events** — each `data:` frame contains a batch of messages.

```bash
# Default: 100 messages per chunk, all time
curl -N -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v3/history/-1001234567890"

# With date range
curl -N -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v3/history/-1001234567890?start_date=2024-01-01&end_date=2024-01-31"

# Smaller chunks for faster first paint
curl -N -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v3/history/-1001234567890?chunk_size=50"

# Datetime precision
curl -N -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v3/history/-1001234567890?start_date=2024-06-15%2012:00:00&end_date=2024-06-15%2018:00:00"

# Force re-download (bypass cache)
curl -N -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v3/history/-1001234567890?force_refresh=true"
```

> **Tip:** Use `curl -N` (no buffering) to see SSE chunks as they arrive.

### Query parameters

| Parameter       | Type   | Default | Description                                                 |
|-----------------|--------|---------|-------------------------------------------------------------|
| `start_date`    | string | —       | `YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS` (defaults to chat beginning) |
| `end_date`      | string | —       | `YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS` (defaults to now)    |
| `chunk_size`    | int >0 | `100`   | Messages per SSE chunk                                      |
| `force_refresh` | bool   | `false` | Bypass cache, re-download from Telegram                     |

### SSE chunk shape

Each SSE frame (`data: ...`) contains:

```json
{
  "messages": [
    {
      "message_id": 12345,
      "date": "2024-01-15 09:30:00",
      "sender_id": 987654321,
      "first_name": "Alice",
      "last_name": "Smith",
      "username": "alice",
      "message": "Hello world!",
      "media": {
        "type": "MessageMediaPhoto",
        "uuid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "filename": "photo.jpg",
        "size": 245760
      },
      "reply_to": null,
      "post_author": null,
      "is_forwarded": 0,
      "forwarded_from_channel_id": null
    }
  ]
}
```

---

## Download Media Files

Download a media file by UUID (from the `media.uuid` field in message responses).

```bash
# Download to file
curl -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v3/files/a1b2c3d4-e5f6-7890-abcd-ef1234567890" \
  -o photo.jpg

# Stream to stdout
curl -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v3/files/a1b2c3d4-e5f6-7890-abcd-ef1234567890" \
  --output -
```

---

## Typical Workflow

```bash
USERNAME="john_doe"
BASE="http://localhost:8000/api/v3"
AUTH="-H X-Telegram-Username:${USERNAME}"

# 1. Authenticate (one-time)
curl -X POST "$BASE/auth/qr" \
  -H "Content-Type: application/json" \
  -d "{\"username\": \"$USERNAME\"}"
# → scan QR, poll status until success

# 1b. If 2FA is enabled (poll returns status "password_required"):
curl -X POST "$BASE/auth/qr/{token}/2fa" \
  -H "Content-Type: application/json" \
  -d '{"password": "your_2fa_password"}'

# 2. Find a channel
curl $AUTH "$BASE/search/dialogs?q=programming&type=channel&type=supergroup"

# 3. Grab message history (use channel ID from step 2)
curl -N $AUTH "$BASE/history/-1001234567890?start_date=2025-01-01&chunk_size=200"

# 4. Download an attached photo (use UUID from step 3)
curl $AUTH "$BASE/files/a1b2c3d4-e5f6-7890-abcd-ef1234567890" -o photo.jpg
```

---

## Error Responses

All errors follow the shape `{"detail": "..."}` with an appropriate HTTP status code.

| Status | Meaning                           |
|--------|-----------------------------------|
| 400    | Bad request (invalid dates, etc.) |
| 401    | Missing or invalid `X-Telegram-Username` header / no session |
| 404    | Resource not found (media, folder, QR session) |
| 409    | Conflict (session already exists, 2FA state mismatch) |
| 429    | Telegram rate limit — check `Retry-After` header |
| 500    | Server error                      |
| 502    | Failed to connect to Telegram     |

---

## Interactive Docs

When the server is running, visit:
- **Swagger UI:** http://localhost:8000/docs
- **ReDoc:** http://localhost:8000/redoc

