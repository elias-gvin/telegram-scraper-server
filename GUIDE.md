# Telegram Scraper API - Complete Guide

FastAPI server for scraping and caching Telegram messages with smart gap detection.

## Table of Contents

- [Quick Start](#quick-start)
- [API Reference](#api-reference)
- [Configuration](#configuration)
- [Caching System](#caching-system)
- [Client Examples](#client-examples)
- [Troubleshooting](#troubleshooting)

---

## Quick Start

### Prerequisites

- Python 3.10+
- Telegram API credentials from [my.telegram.org/apps](https://my.telegram.org/apps)

### Installation

```bash
cd telegram_scraper_cli
pip install -e .
```

### Setup (3 Steps)

**1. Create config file:**

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml`:

```yaml
api_id: "YOUR_API_ID"
api_hash: "YOUR_API_HASH"
download_media: true
max_media_size_mb: 50
output_path: "./output"
sessions_path: "./sessions"
host: "0.0.0.0"
port: 8000
```

**2. Authenticate:**

```bash
tgsc-auth john_doe --config config.yaml
```

Enter your phone number and verification code when prompted.

**3. Start server:**

```bash
tgsc-server --config config.yaml
```

Server runs at `http://localhost:8000` • Docs at `http://localhost:8000/docs`

### Test It

```bash
# Find channels
curl -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v1/find-channels?search_by=username&query=telegram"

# Get messages
curl -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v1/history/123?start_date=2024-01-01&end_date=2024-01-31"
```

---

## API Reference

All endpoints require `X-Telegram-Username` header for authentication.

### Find Channels

Search for Telegram channels.

```http
GET /api/v1/find-channels?search_by={criteria}&query={query}
```

**Parameters:**
- `search_by`: `username` | `channel_id` | `title`
- `query`: Search query
- `title_threshold`: Fuzzy match threshold 0.0-1.0 (default: 0.8)

**Example:**

```bash
curl -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v1/find-channels?search_by=title&query=crypto&title_threshold=0.7"
```

**Response:**

```json
[
  {
    "channel_id": -1001234567890,
    "title": "Crypto News",
    "username": "cryptonews",
    "participants_count": 50000,
    "description": "Latest cryptocurrency news"
  }
]
```

### Get Message History

Stream or bulk download message history.

```http
GET /api/v1/history/{channel_id}?start_date={date}&end_date={date}
```

**Parameters:**
- `channel_id`: Channel ID (path parameter)
- `start_date`: Start date (`YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS`)
- `end_date`: End date (`YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS`)
- `chunk_size`: Batch size (default: 250, use 0 for all at once)
- `force_refresh`: Bypass cache (default: false)

**Streaming (chunk_size > 0):**

```bash
curl -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v1/history/-1001234567890?start_date=2024-01-01&end_date=2024-01-31&chunk_size=250"
```

Response: Server-Sent Events (SSE)

```
data: {"messages": [{...}, {...}, ...]}

data: {"messages": [{...}, {...}, ...]}
```

**Bulk (chunk_size = 0):**

```bash
curl -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v1/history/-1001234567890?start_date=2024-01-01&end_date=2024-01-31&chunk_size=0"
```

Response: Single JSON

```json
{
  "messages": [
    {
      "message_id": 123,
      "date": "2024-01-01 12:00:00",
      "sender_id": 456,
      "first_name": "John",
      "last_name": "Doe",
      "username": "johndoe",
      "message": "Hello world",
      "media_type": "MessageMediaPhoto",
      "media_uuid": "abc-123-uuid",
      "media_size": 2048576,
      "reply_to": 122,
      "post_author": "John Doe",
      "is_forwarded": 0,
      "forwarded_from_channel_id": null
    }
  ]
}
```

### Download Media

Download media file by UUID.

```http
GET /api/v1/files/{file_uuid}
```

**Example:**

```bash
curl -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v1/files/abc-123-uuid" -o photo.jpg
```

---

## Configuration

### Priority

`CLI Parameters > Environment Variables > YAML > Defaults`

### YAML Configuration

```yaml
# Telegram credentials (required)
api_id: "YOUR_API_ID"
api_hash: "YOUR_API_HASH"

# Download settings
download_media: true
max_media_size_mb: 50  # null for unlimited
telegram_batch_size: 100

# Storage
output_path: "./output"
sessions_path: "./sessions"

# Server
host: "0.0.0.0"
port: 8000
```

### Environment Variables

```bash
export TELEGRAM_API_ID="YOUR_API_ID"
export TELEGRAM_API_HASH="YOUR_API_HASH"
export DOWNLOAD_MEDIA=true
export MAX_MEDIA_SIZE_MB=50
export OUTPUT_PATH="./output"
export SESSIONS_PATH="./sessions"
export SERVER_HOST="0.0.0.0"
export SERVER_PORT=8000
```

### CLI Parameters

```bash
tgsc-server \
  --config config.yaml \
  --api-id YOUR_ID \
  --api-hash YOUR_HASH \
  --download-media \
  --max-media-size-mb 100 \
  --port 9000
```

---

## Caching System

The server uses intelligent caching with atomic commits.

### How It Works

**First Request:**
1. Downloads messages from Telegram
2. Saves to SQLite database
3. Returns to client

**Subsequent Requests:**
1. Checks cache for date range
2. If fully cached → Returns instantly
3. If partially cached → Downloads only gaps
4. Returns combined results

**Force Refresh:**
```bash
curl -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v1/history/123?start_date=2024-01-01&end_date=2024-01-31&force_refresh=true"
```

### Storage Structure

```
output/
├── -1001234567890/          # Channel directory
│   ├── -1001234567890.db   # SQLite database
│   └── media/               # Media files
│       ├── 123-photo.jpg
│       └── 124-video.mp4
```

### Database Tables

- **`messages`** - Message content and metadata
- **`channels`** - Channel information
- **`media_files`** - UUID → file path mapping

### Atomic Commits

Each batch (100 messages) commits atomically:
- ✅ Success → All messages saved
- ❌ Error → Nothing saved (rollback)
- **Guarantee:** If data exists for a date range, it's complete (no gaps)

---

## Client Examples

### JavaScript (Streaming)

```javascript
const API_BASE = 'http://localhost:8000/api/v1';
const USERNAME = 'john_doe';

// Stream messages
const es = new EventSource(
  `${API_BASE}/history/-1001234567890?start_date=2024-01-01&end_date=2024-01-31&chunk_size=250`
);

es.onmessage = (event) => {
  const data = JSON.parse(event.data);
  
  data.messages.forEach(msg => {
    console.log(msg.message);
    
    // Download media if present
    if (msg.media_uuid) {
      downloadMedia(msg.media_uuid);
    }
  });
};

es.onerror = () => {
  console.error('Connection error');
  es.close();
};

async function downloadMedia(uuid) {
  const response = await fetch(`${API_BASE}/files/${uuid}`, {
    headers: { 'X-Telegram-Username': USERNAME }
  });
  const blob = await response.blob();
  // Display or save blob...
}
```

### JavaScript (Bulk)

```javascript
async function getAllMessages(channelId, startDate, endDate) {
  const response = await fetch(
    `${API_BASE}/history/${channelId}?start_date=${startDate}&end_date=${endDate}&chunk_size=0`,
    {
      headers: { 'X-Telegram-Username': USERNAME }
    }
  );
  
  const data = await response.json();
  return data.messages;
}

// Usage
const messages = await getAllMessages(-1001234567890, '2024-01-01', '2024-01-31');
console.log(`Got ${messages.length} messages`);
```

### Python

```python
import requests

API_BASE = 'http://localhost:8000/api/v1'
USERNAME = 'john_doe'
HEADERS = {'X-Telegram-Username': USERNAME}

# Find channels
response = requests.get(
    f'{API_BASE}/find-channels',
    params={'search_by': 'title', 'query': 'crypto'},
    headers=HEADERS
)
channels = response.json()

# Get messages (bulk)
response = requests.get(
    f'{API_BASE}/history/-1001234567890',
    params={
        'start_date': '2024-01-01',
        'end_date': '2024-01-31',
        'chunk_size': 0
    },
    headers=HEADERS
)
messages = response.json()['messages']

# Download media
media_uuid = messages[0]['media_uuid']
if media_uuid:
    response = requests.get(
        f'{API_BASE}/files/{media_uuid}',
        headers=HEADERS
    )
    with open('media.jpg', 'wb') as f:
        f.write(response.content)
```

### cURL

```bash
# Find channels
curl -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v1/find-channels?search_by=username&query=durov"

# Get messages (streaming)
curl -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v1/history/-1001234567890?start_date=2024-01-01&end_date=2024-01-31&chunk_size=250"

# Get messages (bulk)
curl -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v1/history/-1001234567890?start_date=2024-01-01&end_date=2024-01-31&chunk_size=0"

# Download media
curl -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v1/files/abc-123-uuid" -o media.jpg

# Force refresh cache
curl -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v1/history/-1001234567890?start_date=2024-01-01&end_date=2024-01-31&force_refresh=true"
```

---

## Troubleshooting

### "User not authenticated"

**Problem:** Session file doesn't exist or is invalid.

**Solution:**

```bash
tgsc-auth your_username --config config.yaml
```

### "Missing X-Telegram-Username header"

**Problem:** API request missing authentication header.

**Solution:** Add header to all requests:

```bash
curl -H "X-Telegram-Username: your_username" ...
```

### Port already in use

**Problem:** Port 8000 is occupied.

**Solution:** Use a different port:

```bash
tgsc-server --config config.yaml --port 9000
```

Or edit `config.yaml`:

```yaml
port: 9000
```

### Media not downloading

**Problem:** Media download disabled or size limit too low.

**Solution:** Check configuration:

```yaml
download_media: true
max_media_size_mb: 50  # Increase or set to null for unlimited
```

### Invalid date format

**Problem:** Date not in correct format.

**Solution:** Use `YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS`:

```bash
start_date=2024-01-01
start_date=2024-01-01%2012:00:00  # URL encoded space
```

### Database locked

**Problem:** Multiple server instances accessing same database.

**Solution:** Stop other server instances:

```bash
pkill -f tgsc-server
tgsc-server --config config.yaml
```

### Connection timeout

**Problem:** Network issues or Telegram rate limiting.

**Solution:** Wait and retry. The cache ensures no partial data is saved.

---

## Advanced Topics

### Multiple Users

Authenticate multiple users for different API clients:

```bash
tgsc-auth alice --config config.yaml
tgsc-auth bob --config config.yaml
```

Each user makes requests with their own header:

```bash
curl -H "X-Telegram-Username: alice" ...
curl -H "X-Telegram-Username: bob" ...
```

### Development Mode

Run server with auto-reload:

```bash
uvicorn telegram_scraper_cli.server:app --reload --host 0.0.0.0 --port 8000
```

### Production Deployment

Use a production ASGI server:

```bash
gunicorn telegram_scraper_cli.server:app -w 4 -k uvicorn.workers.UvicornWorker
```

### API Documentation

Interactive docs available at:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

---

## Architecture

### Two-Buffer Streaming

- **Telegram batch** (100 messages): Internal download chunks
- **Client batch** (configurable): API response chunks
- Independent sizes optimize for both Telegram API and client needs

### Cache-Aware Gap Detection

1. Check cache for requested date range
2. Find gaps (missing data)
3. Download only gaps from Telegram
4. Serve combined results (cache + new)

### Atomic Guarantees

- Each 100-message batch uses `BEGIN IMMEDIATE` transaction
- Rollback on any error
- No partial data possible
- Cache is always consistent

---

## Project Structure

```
telegram_scraper_cli/
├── src/telegram_scraper_cli/
│   ├── api/              # API routes
│   │   ├── channels.py   # Channel search
│   │   ├── history.py    # Message history
│   │   ├── files.py      # Media serving
│   │   └── auth.py       # Authentication
│   ├── server.py         # FastAPI app
│   ├── authenticate.py   # Auth CLI tool
│   ├── config.py         # Configuration
│   ├── scraper.py        # Cache-aware scraping
│   ├── models.py         # Data models
│   ├── media_downloader.py  # Media logic
│   └── db_helper.py      # Database utilities
├── config.example.yaml   # Config template
├── GUIDE.md             # This file
└── README.md            # Overview
```

---

## License

[Your License]

## Acknowledgement

Based on: https://github.com/unnohwn/telegram-scraper

