"""Tests for POST /api/v3/sync/{dialog_id} — cache fill + SyncReport."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from telegram_scraper.database import dialog_db_paths, get_session
from telegram_scraper.database.models import Message
from telegram_scraper.media_downloader import MediaMetadata

from .mock_telegram import MockTelegramClient, FakeMessage, FakeUser


def make_messages(
    count: int,
    start_date: datetime,
    delta: timedelta = timedelta(hours=1),
) -> list[FakeMessage]:
    """Generate a chronological sequence of fake messages."""
    return [
        FakeMessage(
            id=i + 1,
            message=f"Message {i + 1}",
            text=f"Message {i + 1}",
            date=start_date + delta * i,
            sender_id=100,
            _sender=FakeUser(id=100, first_name="Alice"),
        )
        for i in range(count)
    ]


DIALOG_ID = 12345
START = datetime(2025, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def mock_client():
    """Client pre-loaded with 5 messages for DIALOG_ID."""
    mc = MockTelegramClient()
    mc.set_messages(DIALOG_ID, make_messages(5, START))
    return mc


class TestSyncBasic:
    """POST /api/v3/sync/{dialog_id} — basic behaviour."""

    @pytest.mark.asyncio
    async def test_returns_200(self, client):
        resp = await client.post(
            f"/api/v3/sync/{DIALOG_ID}",
            params={"start_date": "2025-01-01", "end_date": "2025-01-02"},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_response_shape(self, client):
        resp = await client.post(
            f"/api/v3/sync/{DIALOG_ID}",
            params={"start_date": "2025-01-01", "end_date": "2025-01-02"},
        )
        data = resp.json()
        assert data["dialog_id"] == DIALOG_ID
        assert "messages_downloaded" in data
        assert "messages_with_media" in data
        assert "media_downloaded" in data
        assert "media_skipped" in data

    @pytest.mark.asyncio
    async def test_messages_downloaded_count(self, client):
        resp = await client.post(
            f"/api/v3/sync/{DIALOG_ID}",
            params={"start_date": "2025-01-01", "end_date": "2025-01-02"},
        )
        assert resp.json()["messages_downloaded"] == 5

    @pytest.mark.asyncio
    async def test_start_end_date_filters(self, client):
        resp_wide = await client.post(
            f"/api/v3/sync/{DIALOG_ID}",
            params={"start_date": "2025-01-01", "end_date": "2025-01-02"},
        )
        resp_narrow = await client.post(
            f"/api/v3/sync/{DIALOG_ID}",
            params={
                "start_date": "2025-01-01",
                "end_date": "2025-01-01 02:00:00",
            },
        )
        assert (
            resp_narrow.json()["messages_downloaded"]
            < resp_wide.json()["messages_downloaded"]
        )

    @pytest.mark.asyncio
    async def test_force_refresh_redownloads(self, client, server_config):
        # End at last message time so find_gaps has no trailing telegram segment
        # (avoids re-fetching the newest row due to open-ended range).
        params = {"start_date": "2025-01-01", "end_date": "2025-01-01 04:00:00"}
        r1 = await client.post(f"/api/v3/sync/{DIALOG_ID}", params=params)
        assert r1.json()["messages_downloaded"] == 5

        r2 = await client.post(f"/api/v3/sync/{DIALOG_ID}", params=params)
        assert r2.json()["messages_downloaded"] == 0

        r3 = await client.post(
            f"/api/v3/sync/{DIALOG_ID}",
            params={**params, "force_refresh": "true"},
        )
        assert r3.json()["messages_downloaded"] == 5


class TestSyncCaching:
    """SQLite persistence and gap-only downloads."""

    @pytest.mark.asyncio
    async def test_messages_stored_in_db(self, client, server_config):
        resp = await client.post(
            f"/api/v3/sync/{DIALOG_ID}",
            params={"start_date": "2025-01-01", "end_date": "2025-01-02"},
        )
        assert resp.json()["messages_downloaded"] == 5

        paths = dialog_db_paths(server_config.dialogs_dir, DIALOG_ID)
        with get_session(paths.db_file) as session:
            from sqlmodel import select

            db_rows = session.exec(select(Message)).all()
            assert len(db_rows) == 5

    @pytest.mark.asyncio
    async def test_second_sync_no_download_when_cached(self, client, mock_client):
        params = {"start_date": "2025-01-01", "end_date": "2025-01-02"}
        assert (await client.post(f"/api/v3/sync/{DIALOG_ID}", params=params)).json()[
            "messages_downloaded"
        ] == 5

        new_start = START + timedelta(hours=5)
        extra = make_messages(3, new_start)
        for m in extra:
            m.id += 100
        mock_client.set_messages(DIALOG_ID, extra)

        r2 = await client.post(f"/api/v3/sync/{DIALOG_ID}", params=params)
        assert r2.json()["messages_downloaded"] == 3


class TestSyncErrors:
    """Validation."""

    @pytest.mark.asyncio
    async def test_bad_start_date_returns_400(self, client):
        resp = await client.post(
            f"/api/v3/sync/{DIALOG_ID}",
            params={"start_date": "not-a-date", "end_date": "2025-01-02"},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_start_after_end_returns_zeros(self, client):
        resp = await client.post(
            f"/api/v3/sync/{DIALOG_ID}",
            params={"start_date": "2099-01-02", "end_date": "2099-01-01"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["messages_downloaded"] == 0
        assert data["messages_with_media"] == 0


class TestSyncMediaStats:
    """Media counters from Telegram download path."""

    @pytest.mark.asyncio
    async def test_skipped_when_download_disabled(self, client):
        """metadata present, download_media off -> media_skipped."""

        def fake_get_metadata(message):
            if message.id == 1:
                return MediaMetadata(media_type="photos", file_size=100)
            return None

        with patch(
            "telegram_scraper.scraper.get_media_metadata",
            side_effect=fake_get_metadata,
        ):
            resp = await client.post(
                f"/api/v3/sync/{DIALOG_ID}",
                params={"start_date": "2025-01-01", "end_date": "2025-01-02"},
            )

        data = resp.json()
        assert data["messages_downloaded"] == 5
        assert data["messages_with_media"] == 1
        assert data["media_downloaded"] == 0
        assert data["media_skipped"] == 1
