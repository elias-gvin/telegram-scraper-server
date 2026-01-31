# Quick Start Guide

Get up and running with Telegram Scraper API in 5 minutes!

## Prerequisites

- Python 3.10 or higher
- Telegram API credentials ([Get them here](https://my.telegram.org/apps))

## Installation

```bash
# 1. Clone and navigate to project
cd telegram_scraper_cli

# 2. Install dependencies
pip install -e .
# OR with poetry
poetry install
```

## Setup

### Step 1: Create Configuration File

Copy the example config and edit it:

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml` with your credentials:

```yaml
api_id: "YOUR_API_ID"        # From my.telegram.org
api_hash: "YOUR_API_HASH"    # From my.telegram.org
download_media: true
max_media_size_mb: 50
output_path: "./output"
sessions_path: "./sessions"
host: "0.0.0.0"
port: 8000
```

### Step 2: Authenticate a User

Before starting the server, authenticate at least one user:

```bash
tgsc-auth john_doe --config config.yaml
```

Follow the prompts:
1. Enter your phone number (with country code, e.g., +1234567890)
2. Enter the code sent to your Telegram
3. If you have 2FA, enter your password

You should see:
```
✓ Authentication successful!
  Session saved to: ./sessions/john_doe.session
```

### Step 3: Start the Server

```bash
tgsc-server --config config.yaml
```

You should see:
```
INFO:     Started server process
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000
```

### Step 4: Test the API

Open another terminal and try:

```bash
# 1. Check server health
curl http://localhost:8000/health

# 2. Find a channel (replace john_doe with your username)
curl -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v1/find-channels?search_by=username&query=telegram"

# 3. Get message history
curl -H "X-Telegram-Username: john_doe" \
  "http://localhost:8000/api/v1/history/-1001234567890?start_date=2024-01-01&end_date=2024-01-31&chunk_size=10"
```

### Step 5: Explore API Documentation

Visit http://localhost:8000/docs in your browser for interactive API documentation!

## Common Issues

### "User not authenticated"

Make sure you ran `tgsc-auth` first:

```bash
tgsc-auth your_username --config config.yaml
```

### "Missing X-Telegram-Username header"

All API requests need this header:

```bash
curl -H "X-Telegram-Username: your_username" ...
```

### Port already in use

Change the port in config.yaml or use CLI:

```bash
tgsc-server --config config.yaml --port 9000
```

### Dependencies not installed

Make sure you installed with `-e`:

```bash
pip install -e .
```

## Next Steps

- Read [SERVER_README.md](SERVER_README.md) for detailed documentation
- Visit http://localhost:8000/docs for interactive API docs
- Try the JavaScript client examples in SERVER_README.md

## Example: Complete Workflow

```bash
# 1. Install
pip install -e .

# 2. Configure
cp config.example.yaml config.yaml
# Edit config.yaml with your API credentials

# 3. Authenticate
tgsc-auth alice --config config.yaml
# Enter phone and code when prompted

# 4. Start server
tgsc-server --config config.yaml

# 5. In another terminal, test API
curl -H "X-Telegram-Username: alice" \
  "http://localhost:8000/api/v1/find-channels?search_by=title&query=news"

# 6. Get messages with streaming
curl -H "X-Telegram-Username: alice" \
  "http://localhost:8000/api/v1/history/-1001234567890?start_date=2024-01-01&end_date=2024-01-31&chunk_size=100"
```

## Help

- Full documentation: [SERVER_README.md](SERVER_README.md)
- API reference: http://localhost:8000/docs
- Report issues: [GitHub Issues](your-repo/issues)

Happy scraping! 🚀

