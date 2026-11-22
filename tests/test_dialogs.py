"""Tests for dialog search and folder listing endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from telethon.tl import types

from .mock_telegram import (
    FakeDialog,
    FakeDialogFilter,
    FakeDialogFilterDefault,
    FakeDialogFilterTitle,
    FakeMessage,
    FakeUser,
    MockTelegramClient,
)

# ---------------------------------------------------------------------------
# Fixtures — provide a mock client pre-loaded with sample dialogs
# ---------------------------------------------------------------------------


def channel(id, title, **kw):
    return types.Channel(
        id=id,
        title=title,
        photo=types.ChatPhotoEmpty(),
        date=datetime(2020, 1, 1, tzinfo=timezone.utc),
        **kw,
    )


@pytest.fixture
def tg():
    """Client with a handful of realistic dialogs."""
    now = datetime.now(timezone.utc)

    dialogs = [
        # Broadcast channel
        FakeDialog(
            entity=channel(
                100,
                "Crypto Traders",
                username="crypto",
                participants_count=5000,
                verified=True,
            ),
            message=FakeMessage(id=500, text="Latest BTC update"),
            date=now,
            is_channel=True,
        ),
        # Supergroup (megagroup)
        FakeDialog(
            entity=channel(200, "Python Devs", participants_count=1200, megagroup=True),
            message=FakeMessage(id=300, text="New PEP discussion"),
            date=now,
            is_channel=True,
            is_group=False,
        ),
        # Regular group
        FakeDialog(
            entity=types.Chat(
                id=300,
                title="Family Group",
                photo=types.ChatPhotoEmpty(),
                participants_count=4,
                date=now,
                version=1,
            ),
            message=FakeMessage(id=50, text="Hello!"),
            date=now,
            is_group=True,
            is_channel=False,
        ),
        # Private user chat
        FakeDialog(
            entity=types.User(id=400, first_name="Alice", last_name="Smith"),
            message=FakeMessage(id=20, text="Hey there"),
            date=now,
            is_user=True,
            is_group=False,
            is_channel=False,
        ),
        # Bot
        FakeDialog(
            entity=types.User(id=500, first_name="HelperBot", bot=True),
            message=FakeMessage(id=10, text="/start"),
            date=now,
            is_user=True,
            is_group=False,
            is_channel=False,
        ),
        # Archived channel
        FakeDialog(
            entity=channel(600, "Old News"),
            message=FakeMessage(id=1000, text="Archive me"),
            date=now,
            archived=True,
            is_channel=True,
        ),
    ]

    # Folders — include_peers link folders to specific dialogs
    folders = [
        FakeDialogFilterDefault(),
        FakeDialogFilter(
            id=2,
            title=FakeDialogFilterTitle("Work"),
            include_peers=[
                types.InputPeerChannel(channel_id=100, access_hash=0),  # Crypto Traders
                types.InputPeerChannel(channel_id=200, access_hash=0),  # Python Devs
            ],
        ),
        FakeDialogFilter(
            id=3,
            title=FakeDialogFilterTitle("Personal"),
            include_peers=[
                types.InputPeerChat(chat_id=300),  # Family Group
                types.InputPeerUser(user_id=400, access_hash=0),  # Alice Smith
            ],
        ),
    ]

    return MockTelegramClient(
        dialogs=dialogs,
        folders=folders,
        me=FakeUser(id=999, first_name="Test", username="testuser"),
    )


# ---------------------------------------------------------------------------
# Tests — search/dialogs
# ---------------------------------------------------------------------------


class TestSearchDialogs:
    """GET /api/v4/search/dialogs"""

    @pytest.mark.asyncio
    async def test_returns_all_dialogs(self, client):
        resp = await client.get("/api/v4/search/dialogs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 6
        assert len(data["results"]) == 6

    @pytest.mark.asyncio
    async def test_response_shape(self, client):
        """Every result must have the expected fields."""
        resp = await client.get("/api/v4/search/dialogs")
        data = resp.json()
        for r in data["results"]:
            assert "id" in r
            assert "type" in r
            assert "title" in r

    @pytest.mark.asyncio
    async def test_fuzzy_search(self, client):
        resp = await client.get(
            "/api/v4/search/dialogs", params={"q": "crypto", "min_score": "0.5"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert any("Crypto" in r["title"] for r in data["results"])

    @pytest.mark.asyncio
    async def test_exact_search(self, client):
        resp = await client.get(
            "/api/v4/search/dialogs", params={"q": "python", "match": "exact"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert all("python" in r["title"].lower() for r in data["results"])

    @pytest.mark.asyncio
    async def test_filter_by_type_channel(self, client):
        resp = await client.get("/api/v4/search/dialogs", params={"type": "channel"})
        assert resp.status_code == 200
        data = resp.json()
        assert all(r["type"] == "channel" for r in data["results"])

    @pytest.mark.asyncio
    async def test_filter_archived(self, client):
        resp = await client.get(
            "/api/v4/search/dialogs", params={"is_archived": "true"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert all(r["is_archived"] for r in data["results"])
        assert data["total"] >= 1

    @pytest.mark.asyncio
    async def test_filter_not_archived(self, client):
        resp = await client.get(
            "/api/v4/search/dialogs", params={"is_archived": "false"}
        )
        data = resp.json()
        assert all(not r["is_archived"] for r in data["results"])

    @pytest.mark.asyncio
    async def test_filter_verified(self, client):
        resp = await client.get(
            "/api/v4/search/dialogs", params={"is_verified": "true"}
        )
        data = resp.json()
        assert data["total"] >= 1
        assert all(r["is_verified"] for r in data["results"])

    @pytest.mark.asyncio
    async def test_pagination_limit(self, client):
        resp = await client.get(
            "/api/v4/search/dialogs", params={"limit": "2", "offset": "0"}
        )
        data = resp.json()
        assert len(data["results"]) == 2
        assert data["total"] == 6  # total is still full count

    @pytest.mark.asyncio
    async def test_pagination_offset(self, client):
        resp = await client.get(
            "/api/v4/search/dialogs", params={"limit": "2", "offset": "4"}
        )
        data = resp.json()
        assert len(data["results"]) == 2
        assert data["offset"] == 4

    @pytest.mark.asyncio
    async def test_min_messages_filter(self, client):
        resp = await client.get(
            "/api/v4/search/dialogs", params={"min_messages": "100"}
        )
        data = resp.json()
        for r in data["results"]:
            assert r["message_count"] is not None
            assert r["message_count"] >= 100

    @pytest.mark.asyncio
    async def test_message_count_uses_real_count_not_message_id(self, client, tg):
        """message_count must come from get_messages().total, NOT from the
        top message ID. Regression test for the 884218-instead-of-400 bug."""
        # Override the count for entity 300 (Family Group, msg.id=50) to 42
        tg._message_counts[300] = 42
        resp = await client.get(
            "/api/v4/search/dialogs", params={"q": "Family", "match": "exact"}
        )
        data = resp.json()
        assert data["total"] == 1
        result = data["results"][0]
        assert result["title"] == "Family Group"
        # Without the fix this would return 50 (the message ID)
        assert result["message_count"] == 42

    @pytest.mark.asyncio
    async def test_sort_by_title_asc(self, client):
        resp = await client.get(
            "/api/v4/search/dialogs",
            params={"sort": "title", "order": "asc"},
        )
        data = resp.json()
        titles = [r["title"].lower() for r in data["results"]]
        assert titles == sorted(titles)


# ---------------------------------------------------------------------------
# Tests — folders
# ---------------------------------------------------------------------------


class TestFolders:
    """GET /api/v4/folders"""

    @pytest.mark.asyncio
    async def test_list_folders(self, client):
        resp = await client.get("/api/v4/folders")
        assert resp.status_code == 200
        folders = resp.json()
        assert isinstance(folders, list)
        # Only custom folders (Work, Personal); built-in Default is excluded
        assert len(folders) == 2

    @pytest.mark.asyncio
    async def test_folder_shape(self, client):
        resp = await client.get("/api/v4/folders")
        for f in resp.json():
            assert "id" in f
            assert "title" in f

    @pytest.mark.asyncio
    async def test_folder_names(self, client):
        resp = await client.get("/api/v4/folders")
        titles = {f["title"] for f in resp.json()}
        assert "Work" in titles
        assert "Personal" in titles

    @pytest.mark.asyncio
    async def test_folders_without_include_dialogs(self, client):
        """Default request does not include dialogs field (or null)."""
        resp = await client.get("/api/v4/folders")
        assert resp.status_code == 200
        for f in resp.json():
            assert "dialogs" not in f or f.get("dialogs") is None

    @pytest.mark.asyncio
    async def test_folders_with_include_dialogs(self, client):
        """include_dialogs=true returns dialog refs with correct IDs and titles."""
        resp = await client.get("/api/v4/folders", params={"include_dialogs": "true"})
        assert resp.status_code == 200
        folders = resp.json()
        work = next(f for f in folders if f["title"] == "Work")
        assert "dialogs" in work
        ids = {d["id"] for d in work["dialogs"]}
        titles = {d["title"] for d in work["dialogs"]}
        assert ids == {-1000000000100, -1000000000200}
        assert titles == {"Crypto Traders", "Python Devs"}
        personal = next(f for f in folders if f["title"] == "Personal")
        assert {d["id"] for d in personal["dialogs"]} == {-300, 400}

    @pytest.mark.asyncio
    async def test_folder_dialog_titles_use_names_for_users(self, client):
        """User entities get first_name + last_name as title, not entity title."""
        resp = await client.get("/api/v4/folders", params={"include_dialogs": "true"})
        assert resp.status_code == 200
        personal = next(f for f in resp.json() if f["title"] == "Personal")
        alice = next(d for d in personal["dialogs"] if d["id"] == 400)
        assert alice["title"] == "Alice Smith"


# ---------------------------------------------------------------------------
# Tests — root & health
# ---------------------------------------------------------------------------


class TestMeta:
    @pytest.mark.asyncio
    async def test_health(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"


@pytest.mark.asyncio
async def test_message_counts_are_requested_one_at_a_time(client, tg):
    """Never burst Telegram with parallel getHistory calls."""
    import asyncio

    in_flight, peak = 0, 0
    original = tg.get_messages

    async def get_messages(*a, **kw):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0)
        try:
            return await original(*a, **kw)
        finally:
            in_flight -= 1

    tg.get_messages = get_messages
    resp = await client.get("/api/v4/search/dialogs", params={"sort": "messages"})
    assert resp.status_code == 200 and peak == 1
