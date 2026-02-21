"""Tests for the settings endpoints (GET / PATCH /api/v3/settings)."""

from __future__ import annotations

import pytest
import yaml


# ---------------------------------------------------------------------------
# GET /settings
# ---------------------------------------------------------------------------


class TestGetSettings:
    """GET /api/v3/settings"""

    @pytest.mark.asyncio
    async def test_returns_current_values(self, client, server_config):
        resp = await client.get("/api/v3/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["download_media"] == server_config.settings.download_media
        assert data["max_media_size_mb"] == server_config.settings.max_media_size_mb
        assert data["telegram_batch_size"] == server_config.settings.telegram_batch_size

    @pytest.mark.asyncio
    async def test_response_has_no_extra_fields(self, client):
        resp = await client.get("/api/v3/settings")
        assert resp.status_code == 200
        assert set(resp.json().keys()) == {
            "download_media",
            "max_media_size_mb",
            "telegram_batch_size",
            "repair_media",
            "download_file_types",
        }

    @pytest.mark.asyncio
    async def test_download_file_types_default_all_true(self, client):
        resp = await client.get("/api/v3/settings")
        assert resp.status_code == 200
        ft = resp.json()["download_file_types"]
        assert ft == {
            "photos": True,
            "videos": True,
            "voice_messages": True,
            "video_messages": True,
            "stickers": True,
            "gifs": True,
            "files": True,
        }

    @pytest.mark.asyncio
    async def test_download_file_types_has_all_keys(self, client):
        resp = await client.get("/api/v3/settings")
        assert resp.status_code == 200
        ft = resp.json()["download_file_types"]
        assert set(ft.keys()) == {
            "photos",
            "videos",
            "voice_messages",
            "video_messages",
            "stickers",
            "gifs",
            "files",
        }


# ---------------------------------------------------------------------------
# PATCH /settings — basic updates
# ---------------------------------------------------------------------------


class TestUpdateSettings:
    """PATCH /api/v3/settings"""

    @pytest.mark.asyncio
    async def test_update_download_media(self, client, server_config):
        assert server_config.settings.download_media is False  # conftest default

        resp = await client.patch("/api/v3/settings", json={"download_media": True})
        assert resp.status_code == 200
        assert resp.json()["download_media"] is True
        # In-memory config should be updated
        assert server_config.settings.download_media is True

    @pytest.mark.asyncio
    async def test_update_max_media_size_mb(self, client, server_config):
        resp = await client.patch("/api/v3/settings", json={"max_media_size_mb": 50})
        assert resp.status_code == 200
        assert resp.json()["max_media_size_mb"] == 50
        assert server_config.settings.max_media_size_mb == 50

    @pytest.mark.asyncio
    async def test_update_telegram_batch_size(self, client, server_config):
        resp = await client.patch("/api/v3/settings", json={"telegram_batch_size": 200})
        assert resp.status_code == 200
        assert resp.json()["telegram_batch_size"] == 200
        assert server_config.settings.telegram_batch_size == 200

    @pytest.mark.asyncio
    async def test_update_multiple_fields(self, client, server_config):
        resp = await client.patch(
            "/api/v3/settings",
            json={
                "download_media": True,
                "max_media_size_mb": 100,
                "telegram_batch_size": 500,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["download_media"] is True
        assert data["max_media_size_mb"] == 100
        assert data["telegram_batch_size"] == 500

    @pytest.mark.asyncio
    async def test_partial_update_leaves_other_fields_unchanged(
        self, client, server_config
    ):
        original_batch = server_config.settings.telegram_batch_size
        resp = await client.patch("/api/v3/settings", json={"download_media": True})
        assert resp.status_code == 200
        assert resp.json()["telegram_batch_size"] == original_batch

    @pytest.mark.asyncio
    async def test_empty_body_returns_422(self, client):
        resp = await client.patch("/api/v3/settings", json={})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_max_media_size_zero_means_no_limit(self, client, server_config):
        resp = await client.patch("/api/v3/settings", json={"max_media_size_mb": 0})
        assert resp.status_code == 200
        assert resp.json()["max_media_size_mb"] is None
        assert server_config.settings.max_media_size_mb is None

    @pytest.mark.asyncio
    async def test_max_media_size_null_means_no_limit(self, client, server_config):
        # First set to a value
        await client.patch("/api/v3/settings", json={"max_media_size_mb": 10})
        assert server_config.settings.max_media_size_mb == 10

        # Then set to null
        resp = await client.patch("/api/v3/settings", json={"max_media_size_mb": None})
        assert resp.status_code == 200
        assert resp.json()["max_media_size_mb"] is None
        assert server_config.settings.max_media_size_mb is None


# ---------------------------------------------------------------------------
# PATCH /settings — download_file_types
# ---------------------------------------------------------------------------


class TestUpdateDownloadFileTypes:
    """PATCH /api/v3/settings — download_file_types nested field"""

    @pytest.mark.asyncio
    async def test_disable_single_type(self, client, server_config):
        resp = await client.patch(
            "/api/v3/settings",
            json={"download_file_types": {"photos": False}},
        )
        assert resp.status_code == 200
        ft = resp.json()["download_file_types"]
        assert ft["photos"] is False
        assert ft["videos"] is True
        assert server_config.settings.download_file_types.photos is False

    @pytest.mark.asyncio
    async def test_disable_multiple_types(self, client, server_config):
        resp = await client.patch(
            "/api/v3/settings",
            json={
                "download_file_types": {
                    "videos": False,
                    "stickers": False,
                    "gifs": False,
                }
            },
        )
        assert resp.status_code == 200
        ft = resp.json()["download_file_types"]
        assert ft["videos"] is False
        assert ft["stickers"] is False
        assert ft["gifs"] is False
        assert ft["photos"] is True

    @pytest.mark.asyncio
    async def test_re_enable_type(self, client, server_config):
        await client.patch(
            "/api/v3/settings",
            json={"download_file_types": {"voice_messages": False}},
        )
        assert server_config.settings.download_file_types.voice_messages is False

        resp = await client.patch(
            "/api/v3/settings",
            json={"download_file_types": {"voice_messages": True}},
        )
        assert resp.status_code == 200
        assert resp.json()["download_file_types"]["voice_messages"] is True
        assert server_config.settings.download_file_types.voice_messages is True

    @pytest.mark.asyncio
    async def test_partial_update_leaves_other_file_types_unchanged(
        self, client, server_config
    ):
        await client.patch(
            "/api/v3/settings",
            json={"download_file_types": {"files": False}},
        )
        resp = await client.get("/api/v3/settings")
        ft = resp.json()["download_file_types"]
        assert ft["files"] is False
        assert ft["photos"] is True
        assert ft["videos"] is True

    @pytest.mark.asyncio
    async def test_download_file_types_persisted_to_yaml(self, client, server_config):
        import yaml

        resp = await client.patch(
            "/api/v3/settings",
            json={"download_file_types": {"video_messages": False, "stickers": False}},
        )
        assert resp.status_code == 200

        saved = yaml.safe_load(server_config.settings_path.read_text())
        assert saved["download_file_types"]["video_messages"] is False
        assert saved["download_file_types"]["stickers"] is False
        assert saved["download_file_types"]["photos"] is True

    @pytest.mark.asyncio
    async def test_get_reflects_patched_file_types(self, client):
        await client.patch(
            "/api/v3/settings",
            json={"download_file_types": {"gifs": False}},
        )
        resp = await client.get("/api/v3/settings")
        assert resp.status_code == 200
        assert resp.json()["download_file_types"]["gifs"] is False


# ---------------------------------------------------------------------------
# PATCH /settings — validation
# ---------------------------------------------------------------------------


class TestUpdateSettingsValidation:
    """PATCH /api/v3/settings — input validation"""

    @pytest.mark.asyncio
    async def test_negative_max_media_size_rejected(self, client):
        resp = await client.patch("/api/v3/settings", json={"max_media_size_mb": -5})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_zero_batch_size_rejected(self, client):
        resp = await client.patch("/api/v3/settings", json={"telegram_batch_size": 0})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_negative_batch_size_rejected(self, client):
        resp = await client.patch("/api/v3/settings", json={"telegram_batch_size": -1})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# PATCH /settings — auto-persistence to settings.yaml
# ---------------------------------------------------------------------------


class TestUpdateSettingsPersistence:
    """PATCH /api/v3/settings always saves to settings.yaml"""

    @pytest.mark.asyncio
    async def test_patch_writes_yaml(self, client, server_config):
        resp = await client.patch(
            "/api/v3/settings",
            json={"download_media": True, "telegram_batch_size": 250},
        )
        assert resp.status_code == 200

        # settings.yaml should be updated
        saved = yaml.safe_load(server_config.settings_path.read_text())
        assert saved["download_media"] is True
        assert saved["telegram_batch_size"] == 250

    @pytest.mark.asyncio
    async def test_patch_without_settings_path_still_succeeds(
        self, client, server_config
    ):
        """If settings_path is None, PATCH should still apply in-memory."""
        server_config.settings_path = None

        resp = await client.patch("/api/v3/settings", json={"download_media": True})
        assert resp.status_code == 200
        assert resp.json()["download_media"] is True

    @pytest.mark.asyncio
    async def test_get_reflects_patched_values(self, client):
        """GET after PATCH should return updated values."""
        await client.patch(
            "/api/v3/settings",
            json={"download_media": True, "telegram_batch_size": 999},
        )
        resp = await client.get("/api/v3/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["download_media"] is True
        assert data["telegram_batch_size"] == 999
