"""Tests for the web-based QR authentication endpoints."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
import yaml
from httpx import AsyncClient, ASGITransport

from telegram_scraper.server import create_app
from telegram_scraper.config import ServerConfig
from telegram_scraper.api import auth as api_qr_auth


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_data_dir(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "channels").mkdir()
    (data / "sessions").mkdir()

    settings = {
        "download_media": False,
        "max_media_size_mb": None,
        "telegram_batch_size": 50,
    }
    with open(data / "settings.yaml", "w") as f:
        yaml.dump(settings, f)
    return data


@pytest.fixture
def server_config(tmp_data_dir):
    return ServerConfig(
        api_id="12345",
        api_hash="fakehash",
        data_dir=tmp_data_dir,
        download_media=False,
        telegram_batch_size=50,
        max_media_size_mb=None,
        settings_path=tmp_data_dir / "settings.yaml",
    )


@pytest.fixture
def app(server_config):
    """Create the real FastAPI app (no dependency overrides — auth endpoints
    create their own TelegramClient, which we mock at the Telethon level)."""
    application = create_app(server_config)
    yield application


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    # Cleanup any leftover QR sessions after each test
    await api_qr_auth.cleanup_qr_sessions()


# ---------------------------------------------------------------------------
# Helpers — fake QR login object
# ---------------------------------------------------------------------------


@dataclass
class FakeQRLogin:
    """Mimics telethon's QRLogin object.

    The background waiter loops calling ``wait()`` with short timeouts,
    then ``recreate()`` when the token expires.  This fake lets tests
    control when the scan "completes" via ``resolve()``.
    """

    url: str = "tg://login?token=abc123faketoken"
    _wait_event: asyncio.Event | None = None
    _raise_on_wait: BaseException | None = None
    _recreate_count: int = 0

    def __post_init__(self):
        self._wait_event = asyncio.Event()

    async def wait(self):
        """Block until resolved or raise the configured exception."""
        await self._wait_event.wait()
        if self._raise_on_wait:
            raise self._raise_on_wait

    async def recreate(self):
        """Simulate QR token refresh (called when token expires)."""
        self._recreate_count += 1
        self.url = f"tg://login?token=refreshed_{self._recreate_count}"
        # Reset event so next wait() blocks again
        self._wait_event = asyncio.Event()

    def resolve(self, error: BaseException | None = None):
        """Simulate scan completing (optionally with an error)."""
        self._raise_on_wait = error
        self._wait_event.set()


def _make_mock_client(qr_login: FakeQRLogin):
    """Build a mock TelegramClient that returns the given FakeQRLogin."""
    mock = AsyncMock()
    mock.connect = AsyncMock()
    mock.disconnect = AsyncMock()
    mock.is_connected = MagicMock(return_value=True)
    mock.qr_login = AsyncMock(return_value=qr_login)
    mock.sign_in = AsyncMock()
    return mock


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStartQRAuth:
    """POST /api/v2/auth/qr"""

    async def test_start_returns_token_and_url(self, client, server_config):
        fake_qr = FakeQRLogin()
        mock_client = _make_mock_client(fake_qr)

        with patch(
            "telegram_scraper.api.auth.TelegramClient", return_value=mock_client
        ):
            resp = await client.post("/api/v2/auth/qr", json={"username": "newuser"})

        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert data["qr_url"] == fake_qr.url
        assert "message" in data

        # Cleanup: resolve the waiting QR so background task finishes
        fake_qr.resolve()
        await asyncio.sleep(0.1)

    async def test_start_rejects_empty_username(self, client):
        with patch(
            "telegram_scraper.api.auth.TelegramClient", return_value=AsyncMock()
        ):
            resp = await client.post("/api/v2/auth/qr", json={"username": "  "})
        assert resp.status_code == 400

    async def test_start_rejects_existing_session(self, client, server_config):
        """If a .session file already exists, return 409."""
        (server_config.sessions_dir / "existing.session").touch()
        resp = await client.post("/api/v2/auth/qr", json={"username": "existing"})
        assert resp.status_code == 409
        assert "force" in resp.json()["detail"].lower()

    async def test_force_reauth_removes_old_session(self, client, server_config):
        """With force=true, an existing session should be deleted and re-auth started."""
        (server_config.sessions_dir / "reauth.session").touch()

        fake_qr = FakeQRLogin()
        mock_client = _make_mock_client(fake_qr)

        with patch(
            "telegram_scraper.api.auth.TelegramClient", return_value=mock_client
        ):
            resp = await client.post(
                "/api/v2/auth/qr", json={"username": "reauth", "force": True}
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert data["qr_url"] == fake_qr.url

        # Old session file should have been deleted
        assert not (server_config.sessions_dir / "reauth.session").exists()

        fake_qr.resolve()
        await asyncio.sleep(0.1)

    async def test_start_handles_connect_failure(self, client):
        mock_client = AsyncMock()
        mock_client.connect = AsyncMock(side_effect=ConnectionError("failed"))
        mock_client.is_connected = MagicMock(return_value=False)

        with patch(
            "telegram_scraper.api.auth.TelegramClient", return_value=mock_client
        ):
            resp = await client.post("/api/v2/auth/qr", json={"username": "failuser"})
        assert resp.status_code == 502


class TestQRStatus:
    """GET /api/v2/auth/qr/{token}"""

    async def test_pending_status_includes_qr_url(self, client):
        fake_qr = FakeQRLogin()
        mock_client = _make_mock_client(fake_qr)

        with patch(
            "telegram_scraper.api.auth.TelegramClient", return_value=mock_client
        ):
            start = await client.post("/api/v2/auth/qr", json={"username": "polluser"})
        token = start.json()["token"]

        resp = await client.get(f"/api/v2/auth/qr/{token}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "pending"
        assert data["qr_url"] is not None  # URL returned while pending

        fake_qr.resolve()
        await asyncio.sleep(0.1)

    async def test_success_after_scan(self, client):
        fake_qr = FakeQRLogin()
        mock_client = _make_mock_client(fake_qr)

        with patch(
            "telegram_scraper.api.auth.TelegramClient", return_value=mock_client
        ):
            start = await client.post("/api/v2/auth/qr", json={"username": "scanuser"})
        token = start.json()["token"]

        # Simulate successful scan
        fake_qr.resolve()
        await asyncio.sleep(0.2)

        resp = await client.get(f"/api/v2/auth/qr/{token}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["qr_url"] is None  # No URL after success

    async def test_qr_url_refreshes_on_token_expiry(self, client):
        """When the QR token expires the background task calls recreate(),
        and the next poll should return the updated URL."""
        fake_qr = FakeQRLogin()
        mock_client = _make_mock_client(fake_qr)

        # Patch must stay active while the background task runs (not just during POST)
        with (
            patch("telegram_scraper.api.auth.TelegramClient", return_value=mock_client),
            patch("telegram_scraper.api.auth.QR_TOKEN_REFRESH_SECONDS", 0.2),
        ):
            start = await client.post(
                "/api/v2/auth/qr", json={"username": "refreshuser"}
            )
            token = start.json()["token"]
            original_url = start.json()["qr_url"]

            # Wait long enough for at least one recreate cycle
            await asyncio.sleep(0.5)

            resp = await client.get(f"/api/v2/auth/qr/{token}")
            data = resp.json()
            assert data["status"] == "pending"
            assert data["qr_url"] != original_url  # URL was refreshed
            assert fake_qr._recreate_count >= 1

            fake_qr.resolve()
            await asyncio.sleep(0.1)

    async def test_not_found(self, client):
        resp = await client.get("/api/v2/auth/qr/nonexistent")
        assert resp.status_code == 404

    async def test_success_auto_cleans_up(self, client):
        """After returning success once, the token should be removed."""
        fake_qr = FakeQRLogin()
        mock_client = _make_mock_client(fake_qr)

        with patch(
            "telegram_scraper.api.auth.TelegramClient", return_value=mock_client
        ):
            start = await client.post("/api/v2/auth/qr", json={"username": "cleanuser"})
        token = start.json()["token"]

        fake_qr.resolve()
        await asyncio.sleep(0.2)

        # First poll — success
        resp1 = await client.get(f"/api/v2/auth/qr/{token}")
        assert resp1.json()["status"] == "success"

        # Second poll — gone
        resp2 = await client.get(f"/api/v2/auth/qr/{token}")
        assert resp2.status_code == 404


class TestTwoFactor:
    """POST /api/v2/auth/qr/{token}/2fa"""

    async def test_2fa_flow(self, client):
        from telethon.errors import SessionPasswordNeededError

        fake_qr = FakeQRLogin()
        mock_client = _make_mock_client(fake_qr)

        with patch(
            "telegram_scraper.api.auth.TelegramClient", return_value=mock_client
        ):
            start = await client.post("/api/v2/auth/qr", json={"username": "tfauser"})
        token = start.json()["token"]

        # Simulate scan that triggers 2FA
        fake_qr.resolve(error=SessionPasswordNeededError(request=None))
        await asyncio.sleep(0.2)

        # Status should be password_required
        status = await client.get(f"/api/v2/auth/qr/{token}")
        assert status.json()["status"] == "password_required"

        # Submit password
        resp = await client.post(
            f"/api/v2/auth/qr/{token}/2fa", json={"password": "my2fapassword"}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"

    async def test_2fa_wrong_status(self, client):
        fake_qr = FakeQRLogin()
        mock_client = _make_mock_client(fake_qr)

        with patch(
            "telegram_scraper.api.auth.TelegramClient", return_value=mock_client
        ):
            start = await client.post(
                "/api/v2/auth/qr", json={"username": "wrongstate"}
            )
        token = start.json()["token"]

        # Try submitting 2FA while still pending
        resp = await client.post(
            f"/api/v2/auth/qr/{token}/2fa", json={"password": "nope"}
        )
        assert resp.status_code == 409

        fake_qr.resolve()
        await asyncio.sleep(0.1)


class TestCancelQR:
    """DELETE /api/v2/auth/qr/{token}"""

    async def test_cancel_pending(self, client):
        fake_qr = FakeQRLogin()
        mock_client = _make_mock_client(fake_qr)

        with patch(
            "telegram_scraper.api.auth.TelegramClient", return_value=mock_client
        ):
            start = await client.post("/api/v2/auth/qr", json={"username": "cancelme"})
        token = start.json()["token"]

        resp = await client.delete(f"/api/v2/auth/qr/{token}")
        assert resp.status_code == 200

        # Token should now be gone
        resp2 = await client.get(f"/api/v2/auth/qr/{token}")
        assert resp2.status_code == 404

    async def test_cancel_nonexistent(self, client):
        resp = await client.delete("/api/v2/auth/qr/doesnotexist")
        assert resp.status_code == 404
