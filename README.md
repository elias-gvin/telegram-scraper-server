# Telegram Scraper API

A local archive of your Telegram dialogs (SQLite + media files) with an HTTP API,
built for one Telegram account and for an AI agent (see
[`TELEGRAM_SCRAPER_SKILL.md`](TELEGRAM_SCRAPER_SKILL.md)).

- **Nothing is silently lost.**
  - The full raw Telegram message is stored.
  - Anything that cannot be represented or downloaded is flagged `incomplete`
    on the message and listed in the sync report.
  - At the end of a full sync, the number of archived messages is checked
    against Telegram's own count.
- **No gaps, no duplicates.** Each dialog's archive is one continuous range of
  message ids. It only grows from its edges, and every batch is written in one
  transaction. An interrupted sync just continues next time.
- **Edits, reactions, poll votes and deletions** are picked up by re-reading the
  last N days on every sync. `full=true` re-reads everything. Deleted messages
  are kept and marked.
- **Fully captured:** text with formatting (including link URLs), replies and
  quotes, forwards, edits, service events (pins, calls, title changes, …),
  reactions, polls, contacts, locations, web previews, and media metadata and
  files.

## Quick start

Requires [uv](https://docs.astral.sh/uv/); it installs Python 3.12 itself if needed.

```bash
uv sync                                 # Python 3.12 + locked dependencies in .venv
cp .env.example .env                    # set TELEGRAM_API_ID / TELEGRAM_API_HASH (https://my.telegram.org/apps)
uv run tgsc-auth myaccount              # log in once (QR code or phone)
uv run tgsc-server --account myaccount  # http://127.0.0.1:8000, docs at /docs
```

On first start the server writes a random API token to `data/token`. Every
request needs it:

```bash
AUTH="Authorization: Bearer $(cat data/token)"
curl -s -H "$AUTH" "http://localhost:8000/api/v4/search/dialogs?q=family"
curl -s -X POST -H "$AUTH" "http://localhost:8000/api/v4/sync/-1001234567890"
curl -N -s -H "$AUTH" "http://localhost:8000/api/v4/history/-1001234567890?start_date=2024-01-01"
```

You can also log in through the API (`POST /api/v4/auth/qr`, then poll
`GET /api/v4/auth/qr/{token}`). See the skill file for the flow.

### Docker

```bash
docker build -t telegram-scraper-server .
docker run -d -p 127.0.0.1:8000:8000 \
  -e TELEGRAM_API_ID=... -e TELEGRAM_API_HASH=... -e TGSC_ACCOUNT=myaccount \
  -v ./data:/app/data telegram-scraper-server
```

The container runs as uid 1000, so the mounted `data/` directory must be
writable by that user.

## How it works

```
POST /sync/{id}      Telegram ──► archive.db + media/      (newer, older, refresh, media)
GET  /history/{id}   sync the missing part (no refresh, no media) ──► stream from archive.db
GET  /files/{id}/{message_id}                               archived media file
GET  /search/...     live Telegram search (same message shape)
```

| Module | Role |
|---|---|
| `sync.py` | Brings one dialog's archive up to date (id-based, resumable) |
| `archive.py` | SQLite storage: raw messages, coverage per dialog, peers, media, problems |
| `serialize.py` | The **only** place that knows Telegram message shapes: raw → API dict |
| `media.py` | Media classification from the raw message, atomic downloads |
| `telegram.py` | The one Telethon client of the process, id resolution |
| `api/` | FastAPI routes |

The message format, every endpoint and all error codes are documented in
[`TELEGRAM_SCRAPER_SKILL.md`](TELEGRAM_SCRAPER_SKILL.md) and at `/docs`.

## Configuration

| What | Where |
|---|---|
| `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` | environment or `.env` |
| account, data dir, host, port | `tgsc-server --account NAME --data-dir ./data --host 127.0.0.1 --port 8000` (`TGSC_ACCOUNT` env works too) |
| `download_media`, `max_media_size_mb`, `telegram_batch_size`, `refresh_days`, `download_file_types` | `data/settings.yaml`, or `PATCH /api/v4/settings` |

```
data/
├── token          API token (created on first start, mode 600)
├── settings.yaml
├── sessions/      Telethon session files
├── archive.db     all archived messages
└── media/<dialog_id>/<message_id>-<name>
```

One data directory belongs to one Telegram account. The archive refuses a
session of a different account, because private-chat message ids are per account.

## Checking export fidelity

Export a chat from Telegram Desktop (JSON), sync the same chat, and compare:

```bash
python scripts/compare_with_native_export.py ./data -1001234567890 "path/to/ChatExport (Chat)"
```

It prints mismatch counts per field (no message content).

## Development

```bash
uv run pytest            # tests use a fake Telegram built from real Telethon objects
uv run ruff check src/
```

## Known limits

- **Rich posts** (Telegram's structured messages with headings and lists) are
  archived complete in the raw message. The API returns them as plain text
  (`rich_text`), with the structure flattened. Files embedded in a rich post are
  not downloaded and are flagged.
- **New Telegram features.** Content that the pinned Telethon version cannot read
  arrives as an empty placeholder, flagged `unsupported_by_telegram_library`.
  Upgrading Telethon fixes this: the rich posts were invisible with 1.40 and are
  complete with 1.46.
- **Old edits and deletions.** Edits and deletions older than `refresh_days` are
  only seen with `POST /sync/{id}?full=true`.
- **History is "from a date until now".** A dialog cannot be archived as an
  isolated middle window.

## License

[MIT License](LICENSE). Originally based on https://github.com/unnohwn/telegram-scraper
