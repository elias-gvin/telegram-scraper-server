"""Tests for the history endpoint — streaming, caching, error handling."""

from __future__ import annotations

import json
import pytest
from datetime import datetime, timezone, timedelta

from telegram_scraper.api.history import MessageResponse
from telegram_scraper.database import dialog_db_paths, get_session
from telegram_scraper.database.models import Message

from .mock_telegram import (
    MockTelegramClient,
    FakeMessage,
    FakeUser,
    FakeMessageReplyHeader,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def parse_sse(text: str) -> list[dict]:
    """Extract all messages from an SSE response body."""
    all_messages: list[dict] = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if line.startswith("data: "):
            payload = json.loads(line[len("data: ") :])
            all_messages.extend(payload["messages"])
    return all_messages


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

DIALOG_ID = 12345
START = datetime(2025, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def mock_client():
    """Client pre-loaded with 5 messages for DIALOG_ID."""
    mc = MockTelegramClient()
    mc.set_messages(DIALOG_ID, make_messages(5, START))
    return mc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHistoryBasic:
    """GET /api/v3/history/{dialog_id} — basic behaviour."""

    @pytest.mark.asyncio
    async def test_returns_sse_stream(self, client):
        resp = await client.get(
            f"/api/v3/history/{DIALOG_ID}",
            params={"start_date": "2025-01-01", "end_date": "2025-01-02"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

    @pytest.mark.asyncio
    async def test_returns_messages(self, client):
        resp = await client.get(
            f"/api/v3/history/{DIALOG_ID}",
            params={"start_date": "2025-01-01", "end_date": "2025-01-02"},
        )
        messages = parse_sse(resp.text)
        assert len(messages) > 0

    @pytest.mark.asyncio
    async def test_message_shape(self, client):
        """Each message dict matches MessageResponse schema."""
        resp = await client.get(
            f"/api/v3/history/{DIALOG_ID}",
            params={"start_date": "2025-01-01", "end_date": "2025-01-02"},
        )
        messages = parse_sse(resp.text)
        for m in messages:
            MessageResponse(**m)

    @pytest.mark.asyncio
    async def test_reply_quote_fields_follow_history_null_behavior(
        self, client, mock_client
    ):
        quoted = FakeMessage(
            id=999,
            message="Replying with quote",
            text="Replying with quote",
            date=START + timedelta(minutes=1),
            sender_id=100,
            _sender=FakeUser(id=100, first_name="Alice"),
            reply_to=FakeMessageReplyHeader(
                reply_to_msg_id=123,
                quote_text="selected phrase",
                quote_offset=7,
            ),
        )
        plain = FakeMessage(
            id=1000,
            message="Regular reply",
            text="Regular reply",
            date=START + timedelta(minutes=2),
            sender_id=100,
            _sender=FakeUser(id=100, first_name="Alice"),
            reply_to=FakeMessageReplyHeader(reply_to_msg_id=124),
        )
        mock_client.set_messages(DIALOG_ID, [quoted, plain])

        resp = await client.get(
            f"/api/v3/history/{DIALOG_ID}",
            params={"start_date": "2025-01-01", "end_date": "2025-01-02"},
        )
        messages = parse_sse(resp.text)

        quoted_row = next(m for m in messages if m["message_id"] == 999)
        plain_row = next(m for m in messages if m["message_id"] == 1000)

        assert quoted_row["reply_quote_text"] == "selected phrase"
        assert quoted_row["reply_quote_offset"] == 7
        assert "reply_quote_text" in plain_row
        assert plain_row["reply_quote_text"] is None
        assert "reply_quote_offset" in plain_row
        assert plain_row["reply_quote_offset"] is None

    @pytest.mark.asyncio
    async def test_date_range_filters_messages(self, client):
        """Requesting a narrow window returns fewer messages."""
        resp_wide = await client.get(
            f"/api/v3/history/{DIALOG_ID}",
            params={"start_date": "2025-01-01", "end_date": "2025-01-02"},
        )
        resp_narrow = await client.get(
            f"/api/v3/history/{DIALOG_ID}",
            params={
                "start_date": "2025-01-01",
                "end_date": "2025-01-01 02:00:00",
            },
        )
        wide = parse_sse(resp_wide.text)
        narrow = parse_sse(resp_narrow.text)
        assert len(narrow) <= len(wide)


class TestHistoryErrors:
    """Validation / error cases."""

    @pytest.mark.asyncio
    async def test_start_after_end_returns_400(self, client):
        resp = await client.get(
            f"/api/v3/history/{DIALOG_ID}",
            params={"start_date": "2025-06-01", "end_date": "2025-01-01"},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_bad_date_format_returns_400(self, client):
        resp = await client.get(
            f"/api/v3/history/{DIALOG_ID}",
            params={"start_date": "not-a-date"},
        )
        assert resp.status_code == 400


class TestHistoryCaching:
    """Verify that messages are persisted in the SQLite cache."""

    @pytest.mark.asyncio
    async def test_messages_stored_in_db(self, client, server_config):
        """After a request, messages should be in the dialog DB."""
        resp = await client.get(
            f"/api/v3/history/{DIALOG_ID}",
            params={"start_date": "2025-01-01", "end_date": "2025-01-02"},
        )
        api_msgs = parse_sse(resp.text)
        assert len(api_msgs) > 0

        # Verify directly in SQLite
        paths = dialog_db_paths(server_config.dialogs_dir, DIALOG_ID)
        with get_session(paths.db_file) as session:
            from sqlmodel import select

            db_rows = session.exec(select(Message)).all()
            assert len(db_rows) == len(api_msgs)

    @pytest.mark.asyncio
    async def test_second_request_uses_cache(self, client, mock_client):
        """Second identical request should serve from DB, not from Telegram mock."""
        params = {"start_date": "2025-01-01", "end_date": "2025-01-01 04:30:00"}

        # 1st request — scraper downloads from mock → writes to DB
        resp1 = await client.get(f"/api/v3/history/{DIALOG_ID}", params=params)
        msgs1 = parse_sse(resp1.text)
        assert len(msgs1) > 0

        # Wipe mock so Telegram "has nothing"
        mock_client.set_messages(DIALOG_ID, [])

        # 2nd request — cache covers most of the range; telegram gap yields nothing
        resp2 = await client.get(f"/api/v3/history/{DIALOG_ID}", params=params)
        msgs2 = parse_sse(resp2.text)

        # Cached messages should still be returned
        assert len(msgs2) == len(msgs1)

    @pytest.mark.asyncio
    async def test_force_refresh_bypasses_cache(self, client, mock_client):
        """force_refresh=true re-downloads from Telegram even if cached."""
        params = {"start_date": "2025-01-01", "end_date": "2025-01-02"}

        # Populate cache
        resp = await client.get(f"/api/v3/history/{DIALOG_ID}", params=params)
        assert len(parse_sse(resp.text)) > 0

        # Empty the mock
        mock_client.set_messages(DIALOG_ID, [])

        # force_refresh should bypass cache → nothing from empty mock
        resp = await client.get(
            f"/api/v3/history/{DIALOG_ID}",
            params={**params, "force_refresh": "true"},
        )
        msgs = parse_sse(resp.text)
        assert len(msgs) == 0


class TestHistoryReverse:
    """GET /api/v3/history/{dialog_id} — reverse parameter (ordering)."""

    @pytest.mark.asyncio
    async def test_reverse_true_returns_oldest_first(self, client):
        """Explicit reverse=true returns messages in ascending date order."""
        resp = await client.get(
            f"/api/v3/history/{DIALOG_ID}",
            params={
                "start_date": "2025-01-01",
                "end_date": "2025-01-02",
                "reverse": "true",
            },
        )
        assert resp.status_code == 200
        messages = parse_sse(resp.text)
        assert len(messages) >= 1
        dates = [m["date"] for m in messages]
        assert dates == sorted(dates)

    @pytest.mark.asyncio
    async def test_reverse_false_returns_newest_first(self, client):
        """reverse=false returns messages in descending date order."""
        resp = await client.get(
            f"/api/v3/history/{DIALOG_ID}",
            params={
                "start_date": "2025-01-01",
                "end_date": "2025-01-02",
                "reverse": "false",
            },
        )
        assert resp.status_code == 200
        messages = parse_sse(resp.text)
        assert len(messages) >= 1
        dates = [m["date"] for m in messages]
        assert dates == sorted(dates, reverse=True)

    @pytest.mark.asyncio
    async def test_reverse_default_is_true(self, client):
        """Omitting reverse param defaults to oldest-first (same as reverse=true)."""
        resp = await client.get(
            f"/api/v3/history/{DIALOG_ID}",
            params={"start_date": "2025-01-01", "end_date": "2025-01-02"},
        )
        assert resp.status_code == 200
        messages = parse_sse(resp.text)
        assert len(messages) >= 1
        dates = [m["date"] for m in messages]
        assert dates == sorted(dates)
