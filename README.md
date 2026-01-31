# Telegram Scraper CLI & API

A comprehensive Telegram channel and group message downloader with both CLI and API server interfaces.

## Features

### CLI Tool
- 📥 Download messages from Telegram channels and groups
- 💾 SQLite-based caching for efficient data management
- 🔍 Search and filter messages
- 📤 Export to CSV/JSON formats

### API Server (NEW!)
- 🚀 RESTful API with FastAPI
- 📡 Real-time streaming with Server-Sent Events (SSE)
- 💾 Smart caching - downloads only missing data
- 🎯 Media file serving with UUID-based access
- 🔐 Header-based authentication
- ⚙️ Flexible configuration (YAML/ENV/CLI)

## Quick Start

### Installation

```bash
# Clone the repository
git clone <repo-url>
cd telegram_scraper_cli

# Install with pip
pip install -e .

# Or with poetry
poetry install
```

### Prerequisites

You must obtain Telegram API credentials:
1. Visit https://my.telegram.org/apps
2. Log in with your phone number
3. Create a new application
4. Note your `api_id` and `api_hash`

## Usage

### 1. CLI Tool

```bash
# Authenticate
tgsc auth --username your_username

# Scrape messages
tgsc scrape --channel-id -1001234567890 --start-date 2024-01-01 --end-date 2024-01-31

# Search messages
tgsc search --query "keyword" --channel-id -1001234567890

# Export to CSV
tgsc export --channel-id -1001234567890 --format csv --output messages.csv
```

### 2. API Server

#### Step 1: Configure

Create `config.yaml`:

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

#### Step 2: Authenticate Users

```bash
tgsc-auth john_doe --config config.yaml
```

#### Step 3: Start Server

```bash
tgsc-server --config config.yaml
```

#### Step 4: Use API

```bash
# Find channels
curl -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v1/find-channels?search_by=username&query=durov"

# Get message history (streaming)
curl -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v1/history/-1001234567890?start_date=2024-01-01&end_date=2024-01-31&chunk_size=250"

# Download media file
curl -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v1/files/abc-123-uuid" -o media.jpg
```

Visit `http://localhost:8000/docs` for interactive API documentation.

## Documentation

- [Server Documentation](SERVER_README.md) - Complete API server guide
- [API Reference](http://localhost:8000/docs) - Interactive API docs (when server is running)

## Configuration

### YAML Configuration

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

All settings can be overridden via CLI:

```bash
tgsc-server --api-id YOUR_ID --api-hash YOUR_HASH --port 9000
```

## Project Structure

```
telegram_scraper_cli/
├── src/telegram_scraper_cli/
│   ├── api/              # FastAPI routes
│   │   ├── channels.py   # Channel search endpoint
│   │   ├── history.py    # Message history endpoint
│   │   ├── files.py      # Media file serving
│   │   └── auth.py       # API authentication helpers
│   ├── server.py         # FastAPI server
│   ├── config.py         # Configuration management
│   ├── streaming_scraper.py  # Cache-aware streaming
│   ├── scrape.py         # Core scraping logic
│   ├── db_helper.py      # Database utilities
│   └── authenticate.py   # Authentication & CLI tool
├── config.example.yaml   # Example configuration
├── SERVER_README.md      # Detailed server docs
└── README.md            # This file
```

## Architecture

### Smart Caching System

The server uses an intelligent caching system:

1. **First Request**: Downloads from Telegram → Saves to SQLite → Returns to client
2. **Subsequent Requests**: 
   - Checks cache for date range
   - Downloads only missing gaps
   - Returns combined results (cache + new data)

### Database Schema

- `messages` - Message content and metadata
- `media_files` - UUID → file path mapping
- `channels` - Channel information
- `scrape_runs` - Scraping history and statistics

## Development Status

⚠️ **This project is not production-ready. Use with caution.**

## Acknowledgement

This repository was built based on:
https://github.com/unnohwn/telegram-scraper

## License

[Your License Here]