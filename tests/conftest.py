"""Shared fixtures: temp data dir, config with a known token, app with a fake Telegram."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from telegram_scraper.config import RuntimeSettings, ServerConfig
from telegram_scraper.server import create_app

from .fake_telegram import FakeTelegram

TOKEN = "test-token"
API = "/api/v4"


class FakeHolder:
    """Stands in for TelegramHolder: always returns the given client."""

    def __init__(self, client):
        self.client = client

    async def get(self):
        return self.client

    async def reset(self):
        pass


@pytest.fixture
def server_config(tmp_path):
    data = tmp_path / "data"
    return ServerConfig(
        api_id="12345",
        api_hash="fakehash",
        data_dir=data,
        account="testuser",
        token=TOKEN,
        settings=RuntimeSettings(
            download_media=False, telegram_batch_size=50, max_media_size_mb=None
        ),
        settings_path=data / "settings.yaml",
    )


@pytest.fixture
def tg():
    """The Telegram client the app talks to. Override per test module."""
    return FakeTelegram()


@pytest.fixture
def app(server_config, tg):
    return create_app(server_config, telegram=FakeHolder(tg))


@pytest_asyncio.fixture
async def client(app):
    """HTTP client that sends the right token."""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as c:
        yield c
