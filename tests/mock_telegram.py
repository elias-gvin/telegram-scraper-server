"""Fake Telethon objects for testing — no real Telegram API calls."""

from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional, List


# ---------------------------------------------------------------------------
# Fake Telethon entity / user types
# ---------------------------------------------------------------------------


@dataclass
class FakeUser:
    """Mimics telethon.tl.types.User (used as sender or 'me')."""

    id: int
    first_name: str = "Test"
    last_name: Optional[str] = "User"
    username: Optional[str] = "testuser"
    bot: bool = False
    verified: bool = False
    phone: Optional[str] = None


@dataclass
class FakeEntity:
    """Mimics a channel / group / user entity returned by get_entity()."""

    id: int
    title: str = "Test Channel"
    username: Optional[str] = None
    megagroup: bool = False
    creator: bool = False
    verified: bool = False
    participants_count: Optional[int] = None
    date: Optional[datetime] = None
    # For user-type entities
    first_name: Optional[str] = None
    last_name: Optional[str] = None


# ---------------------------------------------------------------------------
# Fake message
# ---------------------------------------------------------------------------


@dataclass
class FakeMessage:
    """Mimics telethon.tl.types.Message."""

    id: int
    text: str = ""
    message: str = ""
    date: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    sender_id: int = 100
    media: None = None
    reply_to: None = None
    reply_to_msg_id: None = None
    post_author: Optional[str] = None
    fwd_from: None = None
    edit_date: None = None
    file: None = None

    _sender: Optional[FakeUser] = field(default=None, repr=False)

    async def get_sender(self):
        return self._sender or FakeUser(id=self.sender_id)

    async def download_media(self, file=None):
        return None


# ---------------------------------------------------------------------------
# Fake dialog
# ---------------------------------------------------------------------------


@dataclass
class FakeDialog:
    """Mimics telethon Dialog returned by client.iter_dialogs()."""

    entity: FakeEntity
    message: Optional[FakeMessage] = None
    date: Optional[datetime] = field(default_factory=lambda: datetime.now(timezone.utc))
    unread_count: int = 0
    archived: bool = False

    # Telethon classification flags
    is_user: bool = False
    is_group: bool = False
    is_channel: bool = True

    @property
    def id(self):
        return self.entity.id

    @property
    def title(self):
        if self.is_user:
            first = getattr(self.entity, "first_name", "") or ""
            last = getattr(self.entity, "last_name", "") or ""
            return f"{first} {last}".strip() or str(self.entity.id)
        return self.entity.title


# ---------------------------------------------------------------------------
# Fake folder / dialog‑filter objects
# ---------------------------------------------------------------------------


class FakeTotalList(list):
    """Mimics Telethon's TotalList returned by client.get_messages()."""

    def __init__(self, *args, total: int = 0, **kwargs):
        super().__init__(*args, **kwargs)
        self.total = total


@dataclass
class FakeDialogFilterTitle:
    text: str


@dataclass
class FakeDialogFilter:
    id: int
    title: FakeDialogFilterTitle


@dataclass
class FakeDialogFilterDefault:
    """Mimics DialogFilterDefault which has no id attribute."""

    pass  # no `id` attr


@dataclass
class FakeDialogFilterResult:
    """Wraps the list returned by GetDialogFiltersRequest."""

    filters: list


# ---------------------------------------------------------------------------
# Mock Telegram Client
# ---------------------------------------------------------------------------


class MockTelegramClient:
    """
    Drop-in replacement for TelegramClient.

    Configure with lists of fake dialogs / messages before use.
    Tests inject this via FastAPI dependency overrides.
    """

    def __init__(
        self,
        dialogs: Optional[List[FakeDialog]] = None,
        messages: Optional[List[FakeMessage]] = None,
        me: Optional[FakeUser] = None,
        folders: Optional[List] = None,
    ):
        self.dialogs = dialogs or []
        self.messages = messages or []
        self._me = me or FakeUser(id=999, first_name="Test", username="testuser")
        self._folders = folders or []
        self._messages_by_dialog: dict[int, List[FakeMessage]] = {}
        # Explicit overrides for message counts returned by get_messages().
        # If not set for an entity, falls back to the dialog's top message ID.
        self._message_counts: dict[int, int] = {}

    # -- helpers for test setup --

    def set_messages(self, dialog_id: int, messages: List[FakeMessage]):
        """Assign messages for a specific dialog."""
        self._messages_by_dialog[dialog_id] = messages

    # -- TelegramClient API surface used by production code --

    async def get_me(self):
        return self._me

    async def get_entity(self, entity_id):
        return FakeEntity(id=entity_id, title=f"Dialog {entity_id}")

    async def iter_dialogs(self, **kwargs):
        for d in self.dialogs:
            yield d

    async def get_messages(self, entity, limit=0, **kwargs):
        """Mimics client.get_messages(). With limit=0, returns an empty
        FakeTotalList whose .total is the real message count."""
        entity_id = entity.id if hasattr(entity, "id") else entity
        # Explicit override takes priority
        if entity_id in self._message_counts:
            count = self._message_counts[entity_id]
        else:
            # Fall back to the dialog's top message ID (for backward compat)
            count = 0
            for d in self.dialogs:
                if d.entity.id == entity_id and d.message:
                    count = d.message.id
                    break
        if limit == 0:
            return FakeTotalList(total=count)
        # Non-zero limit: return actual messages from the dialog store
        msgs = self._messages_by_dialog.get(entity_id, self.messages)[:limit]
        result = FakeTotalList(
            msgs, total=len(self._messages_by_dialog.get(entity_id, self.messages))
        )
        return result

    async def iter_messages(self, entity, offset_date=None, reverse=False, **kwargs):
        entity_id = entity.id if hasattr(entity, "id") else entity
        messages = self._messages_by_dialog.get(entity_id, self.messages)

        sorted_msgs = sorted(messages, key=lambda m: m.date, reverse=(not reverse))
        for msg in sorted_msgs:
            if offset_date and reverse and msg.date < offset_date:
                continue
            if offset_date and not reverse and msg.date > offset_date:
                continue
            yield msg

    async def __call__(self, request):
        """Handle client(SomeRequest()) calls (e.g. GetDialogFiltersRequest)."""
        return FakeDialogFilterResult(filters=self._folders)

    # -- connection lifecycle (no-ops) --

    def is_connected(self):
        return True

    async def is_user_authorized(self):
        return True

    async def connect(self):
        pass

    async def disconnect(self):
        pass
