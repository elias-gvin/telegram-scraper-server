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

# Put virtualenv on PATH
ENV PATH="/app/.venv/bin:$PATH"

# Telegram credentials must be provided via env vars at runtime:
#   -e TELEGRAM_API_ID=... -e TELEGRAM_API_HASH=...

EXPOSE 8000

ENTRYPOINT ["tgsc-server"]
# Default: data directory at /app/data (mount as volume to persist)
CMD ["--data-dir", "/app/data"]
