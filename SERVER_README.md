# Telegram Scraper API Server

FastAPI-based server for scraping and caching Telegram messages with smart cache management.

## Features

- 🚀 **RESTful API** with streaming support (Server-Sent Events)
- 💾 **Smart caching** - downloads only missing data, serves cached content instantly
- 📦 **Chunked streaming** - configurable batch sizes for efficient data transfer
- 🔐 **Header-based authentication** - simple X-Telegram-Username header
- 🎯 **Media support** - download and serve media files with UUID-based access
- 📊 **SQLite caching** - persistent message and media storage
- ⚙️ **Flexible configuration** - YAML, environment variables, or CLI parameters

## Quick Start

### 1. Installation

```bash
# Install with pip (from project directory)
pip install -e .

# Or with poetry
poetry install
```

### 2. Configuration

Create a `config.yaml` file (copy from `config.example.yaml`):

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

Or use environment variables:

```bash
export TELEGRAM_API_ID="YOUR_API_ID"
export TELEGRAM_API_HASH="YOUR_API_HASH"
export DOWNLOAD_MEDIA=true
export MAX_MEDIA_SIZE_MB=50
export OUTPUT_PATH="./output"
export SESSIONS_PATH="./sessions"
```

### 3. Authenticate User

Before using the API, users must authenticate with Telegram:

```bash
# Using the CLI tool
tgsc auth --username john_doe
```

This creates a session file in `./sessions/john_doe.session`.

### 4. Start Server

```bash
# Using config file
tgsc-server --config config.yaml

# Or with CLI parameters (overrides config)
tgsc-server \
  --api-id YOUR_API_ID \
  --api-hash YOUR_API_HASH \
  --download-media \
  --port 8000

# Or with environment variables
tgsc-server
```

The server will start at `http://localhost:8000` (or your configured host/port).

## API Documentation

Once running, visit:
- **Interactive docs**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

## API Endpoints

### 1. Find Channels

Search for Telegram channels by username, ID, or title.

```bash
GET /api/v1/find-channels?search_by={criteria}&query={query}
```

**Headers:**
- `X-Telegram-Username`: Your Telegram username (authenticated)

**Parameters:**
- `search_by`: `username`, `channel_id`, or `title`
- `query`: Search query
- `title_threshold`: Fuzzy match threshold for title search (0.0-1.0, default: 0.8)

**Example:**

```bash
curl -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v1/find-channels?search_by=username&query=durov"
```

**Response:**

```json
[
  {
    "channel_id": -1001234567890,
    "title": "Pavel Durov",
    "username": "durov",
    "participants_count": 1000000,
    "description": "Founder of Telegram"
  }
]
```

### 2. Get Message History

Retrieve message history with smart caching.

```bash
GET /api/v1/history/{channel_id}?start_date={date}&end_date={date}
```

**Headers:**
- `X-Telegram-Username`: Your Telegram username (authenticated)

**Parameters:**
- `channel_id`: Telegram channel ID (path parameter)
- `start_date`: Start date (`YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS`)
- `end_date`: End date (`YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS`)
- `chunk_size`: Batch size (default: 250, use 0 for all messages at once)
- `force_refresh`: Bypass cache and re-download (default: false)

**Example (streaming):**

```bash
curl -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v1/history/-1001234567890?start_date=2024-01-01&end_date=2024-01-31&chunk_size=250"
```

**Response (SSE stream):**

```
data: {"messages": [{"message_id": 1, "date": "2024-01-01 12:00:00", "message": "Hello", ...}, ...]}

data: {"messages": [{"message_id": 251, "date": "2024-01-02 08:30:00", "message": "World", ...}, ...]}
```

**Example (all at once):**

```bash
curl -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v1/history/-1001234567890?start_date=2024-01-01&end_date=2024-01-31&chunk_size=0"
```

**Response:**

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

### 3. Download Media File

Download media file by UUID.

```bash
GET /api/v1/files/{file_uuid}
```

**Headers:**
- `X-Telegram-Username`: Your Telegram username (authenticated)

**Example:**

```bash
curl -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v1/files/abc-123-uuid-456" \
  -o photo.jpg
```

## How Caching Works

The server uses a smart caching system:

1. **First Request**: Downloads messages from Telegram → Saves to SQLite → Returns to client
2. **Subsequent Requests**: 
   - Checks cache for date range
   - If fully cached: Returns instantly from database
   - If partially cached: Downloads only missing gaps
   - Returns combined results (cache + new downloads)

### Cache Storage

```
output/
├── -1001234567890/           # Channel directory
│   ├── -1001234567890.db    # SQLite database (messages + metadata)
│   └── media/                # Media files
│       ├── 123-photo.jpg
│       └── 124-video.mp4
└── -1009876543210/
    ├── -1009876543210.db
    └── media/
```

### Force Refresh

Use `force_refresh=true` to bypass cache and re-download:

```bash
curl -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v1/history/123?start_date=2024-01-01&end_date=2024-01-31&force_refresh=true"
```

## Configuration Options

### YAML Configuration

```yaml
api_id: "YOUR_API_ID"              # Required: Telegram API ID
api_hash: "YOUR_API_HASH"          # Required: Telegram API hash
download_media: true               # Enable media download
max_media_size_mb: 50              # Max file size (null = unlimited)
telegram_batch_size: 100           # Download batch size
output_path: "./output"            # Cache directory
sessions_path: "./sessions"        # Session files directory
host: "0.0.0.0"                    # Server host
port: 8000                         # Server port
```

### Environment Variables

- `TELEGRAM_API_ID`: API ID
- `TELEGRAM_API_HASH`: API hash
- `DOWNLOAD_MEDIA`: true/false
- `MAX_MEDIA_SIZE_MB`: Max file size
- `TELEGRAM_BATCH_SIZE`: Batch size
- `OUTPUT_PATH`: Cache directory
- `SESSIONS_PATH`: Sessions directory
- `SERVER_HOST`: Host
- `SERVER_PORT`: Port

### CLI Parameters

```bash
tgsc-server \
  --config config.yaml \            # Config file
  --api-id YOUR_ID \                # Override API ID
  --api-hash YOUR_HASH \            # Override API hash
  --download-media \                # Enable media
  --no-download-media \             # Disable media
  --max-media-size-mb 100 \         # Max size (0 = unlimited)
  --telegram-batch-size 200 \       # Batch size
  --output-path ./data \            # Cache dir
  --sessions-path ./sessions \      # Sessions dir
  --host 127.0.0.1 \                # Host
  --port 9000                       # Port
```

**Priority**: CLI > Environment Variables > YAML > Defaults

## JavaScript Client Example

```javascript
const API_BASE = 'http://localhost:8000/api/v1';
const USERNAME = 'john_doe';

// Helper for auth header
const headers = {
  'X-Telegram-Username': USERNAME
};

// Find channels
async function findChannels(query) {
  const response = await fetch(
    `${API_BASE}/find-channels?search_by=title&query=${query}`,
    { headers }
  );
  return await response.json();
}

// Stream messages
function streamMessages(channelId, startDate, endDate) {
  const url = `${API_BASE}/history/${channelId}?start_date=${startDate}&end_date=${endDate}&chunk_size=250`;
  
  const eventSource = new EventSource(url);
  
  eventSource.onmessage = (event) => {
    const data = JSON.parse(event.data);
    console.log(`Received ${data.messages.length} messages`);
    
    data.messages.forEach(msg => {
      displayMessage(msg);
      
      // Download media if present
      if (msg.media_uuid) {
        const mediaUrl = `${API_BASE}/files/${msg.media_uuid}`;
        // Fetch and display media...
      }
    });
  };
  
  return eventSource;
}

// Get all messages at once
async function getAllMessages(channelId, startDate, endDate) {
  const response = await fetch(
    `${API_BASE}/history/${channelId}?start_date=${startDate}&end_date=${endDate}&chunk_size=0`,
    { headers }
  );
  return await response.json();
}
```

## Troubleshooting

### "User not authenticated" error

Make sure the user has authenticated:

```bash
tgsc auth --username your_username
```

### "Missing X-Telegram-Username header"

All API requests require the header:

```bash
curl -H "X-Telegram-Username: your_username" ...
```

### Media files not downloading

Check configuration:

```yaml
download_media: true
max_media_size_mb: 50  # Increase or set to null for unlimited
```

### Port already in use

Change the port:

```bash
tgsc-server --port 9000
```

## Development

### Running in development mode

```bash
uvicorn telegram_scraper_cli.server:app --reload --host 0.0.0.0 --port 8000
```

### Running tests

```bash
pytest tests/
```

## License

[Your License Here]

