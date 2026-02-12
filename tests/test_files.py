"""Tests for the file-serving endpoint."""

from __future__ import annotations

import pytest

from telegram_scraper.models import MessageData
from telegram_scraper.database import (
    get_engine,
    create_db_and_tables,
    get_session,
    ensure_channel_directories,
)
from telegram_scraper.database import operations


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CHANNEL_ID = 99999


def _make_msg(msg_id: int) -> MessageData:
    return MessageData(
        message_id=msg_id,
        channel_id=CHANNEL_ID,
        date="2025-01-01 00:00:00",
        sender_id=100,
        message="test message",
        is_forwarded=0,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def media_uuid(server_config):
    """
    Create a fake channel DB + media file on disk and return the UUID.
    """
    paths = ensure_channel_directories(server_config.channels_dir, CHANNEL_ID)
    engine = get_engine(paths.db_file)
    create_db_and_tables(engine)

    # Write a tiny fake JPEG to disk
    fake_file = paths.media_dir / "1-photo.jpg"
    fake_file.write_bytes(b"\xff\xd8fake-jpeg-data")

    with get_session(paths.db_file) as session:
        operations.upsert_channel(
            session, channel_id=CHANNEL_ID, channel_name="Test Channel"
        )
        operations.batch_upsert_messages(session, [_make_msg(1)], channel_id=CHANNEL_ID)
        uuid = operations.store_media_with_uuid(
            session,
            channel_id=CHANNEL_ID,
            message_id=1,
            file_size=fake_file.stat().st_size,
            media_type="MessageMediaPhoto",
            original_filename=None,  # Photos don't have original filenames
            file_path=str(fake_file),
        )
    return uuid


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFileServing:
    """GET /api/v2/files/{file_uuid}"""

    @pytest.mark.asyncio
    async def test_download_by_uuid(self, client, media_uuid):
        resp = await client.get(f"/api/v2/files/{media_uuid}")
        assert resp.status_code == 200
        assert b"fake-jpeg-data" in resp.content

    @pytest.mark.asyncio
    async def test_content_type_is_image(self, client, media_uuid):
        resp = await client.get(f"/api/v2/files/{media_uuid}")
        assert "image/jpeg" in resp.headers["content-type"]

    @pytest.mark.asyncio
    async def test_metadata_only_returns_json(self, client, media_uuid):
        resp = await client.get(f"/api/v2/files/{media_uuid}?metadata_only=true")
        assert resp.status_code == 200
        data = resp.json()
        assert "file_path" in data
        assert "original_filename" in data
        assert "size" in data
        assert (
            data["original_filename"] is None
        )  # Photos don't carry an original filename
        assert data["size"] > 0

    @pytest.mark.asyncio
    async def test_metadata_only_not_found(self, client):
        resp = await client.get(
            "/api/v2/files/nonexistent-uuid-1234?metadata_only=true"
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_not_found_uuid(self, client):
        resp = await client.get("/api/v2/files/nonexistent-uuid-1234")
        assert resp.status_code == 404
