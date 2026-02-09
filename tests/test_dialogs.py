"""Tests for dialog search and folder listing endpoints."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from .mock_telegram import (
    MockTelegramClient,
    FakeDialog,
    FakeEntity,
    FakeMessage,
    FakeUser,
    FakeDialogFilter,
    FakeDialogFilterTitle,
    FakeDialogFilterDefault,
)


# ---------------------------------------------------------------------------
# Fixtures — provide a mock client pre-loaded with sample dialogs
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_client():
    """Client with a handful of realistic dialogs."""
    now = datetime.now(timezone.utc)

    dialogs = [
        # Broadcast channel
        FakeDialog(
            entity=FakeEntity(
                id=100,
                title="Crypto Traders",
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
            entity=FakeEntity(
                id=200,
                title="Python Devs",
                participants_count=1200,
                megagroup=True,
            ),
            message=FakeMessage(id=300, text="New PEP discussion"),
            date=now,
            is_channel=True,
            is_group=False,
        ),
        # Regular group
        FakeDialog(
            entity=FakeEntity(id=300, title="Family Group"),
            message=FakeMessage(id=50, text="Hello!"),
            date=now,
            is_group=True,
            is_channel=False,
        ),
        # Private user chat
        FakeDialog(
            entity=FakeEntity(id=400, first_name="Alice", last_name="Smith"),
            message=FakeMessage(id=20, text="Hey there"),
            date=now,
            is_user=True,
            is_group=False,
            is_channel=False,
        ),
        # Bot
        FakeDialog(
            entity=FakeEntity(id=500, first_name="HelperBot"),
            message=FakeMessage(id=10, text="/start"),
            date=now,
            is_user=True,
            is_group=False,
            is_channel=False,
        ),
        # Archived channel
        FakeDialog(
            entity=FakeEntity(id=600, title="Old News"),
            message=FakeMessage(id=1000, text="Archive me"),
            date=now,
            archived=True,
            is_channel=True,
        ),
    ]

    # Folders
    folders = [
        FakeDialogFilterDefault(),
        FakeDialogFilter(id=2, title=FakeDialogFilterTitle("Work")),
        FakeDialogFilter(id=3, title=FakeDialogFilterTitle("Personal")),
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
    """GET /api/v2/search/dialogs"""

    @pytest.mark.asyncio
    async def test_returns_all_dialogs(self, client):
        resp = await client.get("/api/v2/search/dialogs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 6
        assert len(data["results"]) == 6

    @pytest.mark.asyncio
    async def test_response_shape(self, client):
        """Every result must have the expected fields."""
        resp = await client.get("/api/v2/search/dialogs")
        data = resp.json()
        for r in data["results"]:
            assert "id" in r
            assert "type" in r
            assert "title" in r

    @pytest.mark.asyncio
    async def test_fuzzy_search(self, client):
        resp = await client.get(
            "/api/v2/search/dialogs", params={"q": "crypto", "min_score": "0.5"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert any("Crypto" in r["title"] for r in data["results"])

    @pytest.mark.asyncio
    async def test_exact_search(self, client):
        resp = await client.get(
            "/api/v2/search/dialogs", params={"q": "python", "match": "exact"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert all("python" in r["title"].lower() for r in data["results"])

    @pytest.mark.asyncio
    async def test_filter_by_type_channel(self, client):
        resp = await client.get("/api/v2/search/dialogs", params={"type": "channel"})
        assert resp.status_code == 200
        data = resp.json()
        assert all(r["type"] == "channel" for r in data["results"])

    @pytest.mark.asyncio
    async def test_filter_archived(self, client):
        resp = await client.get(
            "/api/v2/search/dialogs", params={"is_archived": "true"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert all(r["is_archived"] for r in data["results"])
        assert data["total"] >= 1

    @pytest.mark.asyncio
    async def test_filter_not_archived(self, client):
        resp = await client.get(
            "/api/v2/search/dialogs", params={"is_archived": "false"}
        )
        data = resp.json()
        assert all(not r["is_archived"] for r in data["results"])

    @pytest.mark.asyncio
    async def test_filter_verified(self, client):
        resp = await client.get(
            "/api/v2/search/dialogs", params={"is_verified": "true"}
        )
        data = resp.json()
        assert data["total"] >= 1
        assert all(r["is_verified"] for r in data["results"])

    @pytest.mark.asyncio
    async def test_pagination_limit(self, client):
        resp = await client.get(
            "/api/v2/search/dialogs", params={"limit": "2", "offset": "0"}
        )
        data = resp.json()
        assert len(data["results"]) == 2
        assert data["total"] == 6  # total is still full count

    @pytest.mark.asyncio
    async def test_pagination_offset(self, client):
        resp = await client.get(
            "/api/v2/search/dialogs", params={"limit": "2", "offset": "4"}
        )
        data = resp.json()
        assert len(data["results"]) == 2
        assert data["offset"] == 4

    @pytest.mark.asyncio
    async def test_min_messages_filter(self, client):
        resp = await client.get(
            "/api/v2/search/dialogs", params={"min_messages": "100"}
        )
        data = resp.json()
        for r in data["results"]:
            assert r["message_count"] is not None
            assert r["message_count"] >= 100

    @pytest.mark.asyncio
    async def test_sort_by_title_asc(self, client):
        resp = await client.get(
            "/api/v2/search/dialogs",
            params={"sort": "title", "order": "asc"},
        )
        data = resp.json()
        titles = [r["title"].lower() for r in data["results"]]
        assert titles == sorted(titles)


# ---------------------------------------------------------------------------
# Tests — folders
# ---------------------------------------------------------------------------


class TestFolders:
    """GET /api/v2/folders"""

    @pytest.mark.asyncio
    async def test_list_folders(self, client):
        resp = await client.get("/api/v2/folders")
        assert resp.status_code == 200
        folders = resp.json()
        assert isinstance(folders, list)
        # We set up Work + Personal + Default
        assert len(folders) == 3

    @pytest.mark.asyncio
    async def test_folder_shape(self, client):
        resp = await client.get("/api/v2/folders")
        for f in resp.json():
            assert "id" in f
            assert "title" in f
            assert "is_default" in f

    @pytest.mark.asyncio
    async def test_folder_names(self, client):
        resp = await client.get("/api/v2/folders")
        titles = {f["title"] for f in resp.json()}
        assert "Work" in titles
        assert "Personal" in titles


# ---------------------------------------------------------------------------
# Tests — root & health
# ---------------------------------------------------------------------------


class TestMeta:
    @pytest.mark.asyncio
    async def test_root(self, client):
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "Telegram Scraper API" in resp.json()["name"]

    @pytest.mark.asyncio
    async def test_health(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"
