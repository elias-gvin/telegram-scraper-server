FROM python:3.12-slim AS builder

# Install Poetry
ENV POETRY_VERSION=2.1.1 \
    POETRY_HOME="/opt/poetry" \
    POETRY_VIRTUALENVS_IN_PROJECT=true \
    POETRY_NO_INTERACTION=1
RUN pip install --no-cache-dir "poetry==$POETRY_VERSION"

WORKDIR /app

# Install dependencies first (layer caching)
COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-root

# Copy source and install the project itself
COPY src/ src/
COPY README.md ./
RUN poetry install --only main


# --- Runtime stage ---
FROM python:3.12-slim

WORKDIR /app

# Copy virtualenv and source from builder
COPY --from=builder /app/.venv .venv
COPY --from=builder /app/src src

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
