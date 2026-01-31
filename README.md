# Telegram Scraper API

FastAPI-based server for scraping and caching Telegram messages with intelligent gap detection and streaming support.

## Features

- 🚀 **RESTful API** with FastAPI
- 📡 **Real-time streaming** via Server-Sent Events (SSE)
- 💾 **Smart caching** - downloads only missing data, serves cached content instantly
- 📦 **Chunked delivery** - configurable batch sizes for efficient data transfer
- 🔐 **Header-based authentication** - simple X-Telegram-Username header
- 🎯 **Media support** - download and serve media files with UUID-based access
- 📊 **SQLite caching** - persistent message and media storage with atomic commits
- ⚙️ **Flexible configuration** - YAML, environment variables, or CLI parameters

## Quick Start

### Installation

```bash
# Clone the repository
cd telegram_scraper_cli

# Install with pip
pip install -e .
```

### Prerequisites

Get Telegram API credentials:
1. Visit https://my.telegram.org/apps
2. Log in with your phone number
3. Create a new application
4. Note your `api_id` and `api_hash`

### Setup

**1. Create configuration file:**

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

**2. Authenticate a user:**

```bash
tgsc-auth john_doe --config config.yaml
```

Follow the prompts to enter your phone and verification code.

**3. Start the server:**

```bash
tgsc-server --config config.yaml
```

**4. Test the API:**

```bash
# Find channels
curl -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v1/find-channels?search_by=username&query=telegram"

# Get message history
curl -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v1/history/-1001234567890?start_date=2024-01-01&end_date=2024-01-31&chunk_size=250"

# Download media
curl -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v1/files/abc-123-uuid" -o photo.jpg"
```

Visit `http://localhost:8000/docs` for interactive API documentation.

## API Endpoints

### 1. Find Channels

```http
GET /api/v1/find-channels?search_by={criteria}&query={query}
Header: X-Telegram-Username: your_username
```

Search for channels by username, ID, or title.

### 2. Message History

```http
GET /api/v1/history/{channel_id}?start_date={date}&end_date={date}&chunk_size={size}
Header: X-Telegram-Username: your_username
```

Stream message history with smart caching:
- `chunk_size=250` - Stream in chunks (Server-Sent Events)
- `chunk_size=0` - Return all messages at once
- `force_refresh=true` - Bypass cache and re-download

### 3. Media Files

```http
GET /api/v1/files/{uuid}
Header: X-Telegram-Username: your_username
```

Download media file by UUID (provided in message response).

## Architecture

### Smart Caching System

The server intelligently manages cache:

1. **First Request**: Downloads from Telegram → Saves to SQLite → Returns to client
2. **Subsequent Requests**: 
   - Checks cache for date range
   - Downloads only missing gaps
   - Returns combined results (cache + new data)
3. **Atomic Commits**: Each batch commits atomically - no partial data on errors

### Two-Buffer Streaming

- **Telegram batch** (configurable): Internal download chunks
- **Client batch** (per-request): API response chunks
- Independent sizes for optimal performance

### Database Schema

- `messages` - Message content and metadata
- `media_files` - UUID → file path mapping
- `channels` - Channel information

**No gaps guarantee**: If data exists for a range in cache, it's complete and valid.

## Configuration

### Priority

`CLI Parameters > Environment Variables > YAML > Defaults`

### YAML (`config.yaml`)

```yaml
api_id: "YOUR_API_ID"
api_hash: "YOUR_API_HASH"
download_media: true
max_media_size_mb: 50
telegram_batch_size: 100
output_path: "./output"
sessions_path: "./sessions"
host: "0.0.0.0"
port: 8000
```

### Environment Variables

```bash
export TELEGRAM_API_ID="YOUR_API_ID"
export TELEGRAM_API_HASH="YOUR_API_HASH"
export DOWNLOAD_MEDIA=true
export MAX_MEDIA_SIZE_MB=50
```

### CLI Parameters

```bash
tgsc-server \
  --api-id YOUR_ID \
  --api-hash YOUR_HASH \
  --download-media \
  --port 8000
```

## Documentation

- **[SERVER_README.md](SERVER_README.md)** - Complete server documentation
- **[QUICKSTART.md](QUICKSTART.md)** - 5-minute setup guide
- **[CHANGES_ATOMIC_COMMITS.md](CHANGES_ATOMIC_COMMITS.md)** - Database schema details
- **http://localhost:8000/docs** - Interactive API docs (when running)

## Project Structure

```
telegram_scraper_cli/
├── src/telegram_scraper_cli/
│   ├── api/              # FastAPI routes
│   │   ├── channels.py   # Channel search
│   │   ├── history.py    # Message history  
│   │   ├── files.py      # Media serving
│   │   └── auth.py       # Authentication
│   ├── server.py         # FastAPI server
│   ├── authenticate.py   # Auth CLI tool
│   ├── config.py         # Configuration
│   ├── scraper.py        # Cache-aware scraping
│   ├── models.py         # Data models
│   ├── media_downloader.py  # Media download logic
│   └── db_helper.py      # Database utilities
├── config.example.yaml   # Example configuration
└── README.md            # This file
```

## JavaScript Client Example

```javascript
const API_BASE = 'http://localhost:8000/api/v1';
const USERNAME = 'john_doe';

// Stream messages
const es = new EventSource(
  `${API_BASE}/history/123?start_date=2024-01-01&end_date=2024-01-31&chunk_size=250`
);

es.onmessage = (event) => {
  const data = JSON.parse(event.data);
  data.messages.forEach(msg => {
    displayMessage(msg);
    
    // Download media if present
    if (msg.media_uuid) {
      fetchMedia(msg.media_uuid);
    }
  });
};

async function fetchMedia(uuid) {
  const response = await fetch(`${API_BASE}/files/${uuid}`, {
    headers: { 'X-Telegram-Username': USERNAME }
  });
  const blob = await response.blob();
  // Display media...
}
```

## Development

### Run in development mode

```bash
uvicorn telegram_scraper_cli.server:app --reload --host 0.0.0.0 --port 8000
```

### Check linting

```bash
ruff check src/
```

## Troubleshooting

### "User not authenticated"

Authenticate first:

```bash
tgsc-auth your_username --config config.yaml
```

### "Missing X-Telegram-Username header"

All API requests require this header:

```bash
curl -H "X-Telegram-Username: your_username" ...
```

### Port already in use

```bash
tgsc-server --port 9000
```

## License

[Your License Here]

## Acknowledgement

Built based on: https://github.com/unnohwn/telegram-scraper
