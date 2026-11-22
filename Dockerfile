FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /bin/uv

# Use the image's Python, compile bytecode, copy (not link) files into the venv
ENV UV_PYTHON_DOWNLOADS=0 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Dependencies first (cached layer), exactly as locked
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-install-project

# Then the project itself, as a regular (non-editable) install
COPY src/ src/
COPY README.md ./
RUN uv sync --locked --no-dev --no-editable


# --- Runtime stage ---
FROM python:3.12-slim

WORKDIR /app

# The venv contains the dependencies and the installed project
COPY --from=builder /app/.venv .venv

# Run as an unprivileged user; /app/data holds sessions, archive, media and the API token
RUN useradd --create-home --uid 1000 scraper && mkdir -p /app/data && chown scraper /app/data
USER scraper

ENV PATH="/app/.venv/bin:$PATH"

# Required at runtime:
#   -e TELEGRAM_API_ID=... -e TELEGRAM_API_HASH=... -e TGSC_ACCOUNT=<session name>

EXPOSE 8000

ENTRYPOINT ["tgsc-server"]
# Inside a container the server must listen on all interfaces; publish the port
# on the host's loopback only: -p 127.0.0.1:8000:8000
CMD ["--host", "0.0.0.0", "--data-dir", "/app/data"]
