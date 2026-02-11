"""Shared test fixtures — app, HTTP client, temp dirs, mock Telegram client."""

from __future__ import annotations

import pytest
import pytest_asyncio
import yaml
from httpx import AsyncClient, ASGITransport

from telegram_scraper.server import create_app
from telegram_scraper.config import ServerConfig
from telegram_scraper.api.auth_utils import get_telegram_client, get_authenticated_user

from .mock_telegram import MockTelegramClient


# ---------------------------------------------------------------------------
# Temp directories
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_data_dir(tmp_path):
    """Temporary data directory with channels, sessions, and settings.yaml."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "channels").mkdir()
    (data / "sessions").mkdir()
    # Auth checks that <username>.session exists on disk
    (data / "sessions" / "testuser.session").touch()

    # Create a settings.yaml
    settings = {
        "download_media": False,
        "max_media_size_mb": None,
        "telegram_batch_size": 50,
    }
    with open(data / "settings.yaml", "w") as f:
        yaml.dump(settings, f)

    return data


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@pytest.fixture
def server_config(tmp_data_dir):
    """ServerConfig wired to temp directories — no real Telegram creds needed."""
    return ServerConfig(
        api_id="12345",
        api_hash="fakehash",
        data_dir=tmp_data_dir,
        download_media=False,
        telegram_batch_size=50,
        max_media_size_mb=None,
        settings_path=tmp_data_dir / "settings.yaml",
    )


# ---------------------------------------------------------------------------
# Mock Telegram client
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_client():
    """Default (empty) MockTelegramClient.

    Override in individual test modules to supply custom dialogs / messages.
    """
    return MockTelegramClient()


# ---------------------------------------------------------------------------
# FastAPI app with dependency overrides
# ---------------------------------------------------------------------------


@pytest.fixture
def app(server_config, mock_client):
    """Create the real FastAPI app but swap the Telegram client for a mock."""
    application = create_app(server_config)

    # --- Override Telegram dependencies ---

    async def override_get_client():
        yield mock_client

    async def override_get_user():
        return "testuser"

    application.dependency_overrides[get_telegram_client] = override_get_client
    application.dependency_overrides[get_authenticated_user] = override_get_user

    yield application

    application.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Async HTTP test client (hits real FastAPI routes via ASGI transport)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(app):
    """httpx AsyncClient talking to the in-process FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
