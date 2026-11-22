"""In-memory Telegram for tests, built from *real* Telethon TL objects.

Messages are real `telethon.tl.patched.Message` / `MessageService` instances, so
`to_dict()`, `.sender`, `.forward`, `.file` behave like in production.  Only the
network is faked.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from telethon import utils
from telethon._updates import EntityCache as MbEntityCache
from telethon.tl import types
from telethon.tl.patched import Message, MessageService

ME = types.User(id=1, first_name="Me", username="me")
ALICE = types.User(id=100, first_name="Alice", last_name="A", username="alice")
BOB = types.User(id=200, first_name="Bob")
CHANNEL = types.Channel(
    id=5000,
    title="Test Channel",
    photo=types.ChatPhotoEmpty(),
    date=datetime(2020, 1, 1, tzinfo=timezone.utc),
    username="testchan",
    broadcast=True,
)
GROUP = types.Chat(
    id=6000,
    title="Test Group",
    photo=types.ChatPhotoEmpty(),
    participants_count=3,
    date=datetime(2020, 1, 1, tzinfo=timezone.utc),
    version=1,
)

SUPERGROUP = types.Channel(
    id=7000,
    title="Upgraded Group",
    photo=types.ChatPhotoEmpty(),
    date=datetime(2020, 1, 1, tzinfo=timezone.utc),
    megagroup=True,
)

T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
_ids = itertools.count(10_000)


def peer(entity):
    return utils.get_peer(entity)


def text_msg(chat, id, text="", *, sender=ALICE, date=None, **kw) -> Message:
    """A regular message in `chat` (entity) from `sender` (None = channel post)."""
    if sender is None:
        kw.setdefault("post", True)
    return Message(
        id=id,
        peer_id=peer(chat),
        date=date or T0 + timedelta(hours=id),
        message=text,
        from_id=peer(sender) if sender is not None else None,
        **kw,
    )


def service_msg(
    chat, id, action, *, sender=ALICE, date=None, reply_to=None
) -> MessageService:
    return MessageService(
        id=id,
        peer_id=peer(chat),
        date=date or T0 + timedelta(hours=id),
        action=action,
        from_id=peer(sender) if sender is not None else None,
        reply_to=reply_to,
    )


def photo(size=1000) -> types.MessageMediaPhoto:
    return types.MessageMediaPhoto(
        photo=types.Photo(
            id=next(_ids),
            access_hash=1,
            file_reference=b"x",
            date=T0,
            dc_id=1,
            sizes=[types.PhotoSize(type="y", w=10, h=10, size=size)],
        )
    )


def document(
    *attributes, mime="application/octet-stream", size=1000
) -> types.MessageMediaDocument:
    return types.MessageMediaDocument(
        document=types.Document(
            id=next(_ids),
            access_hash=1,
            file_reference=b"x",
            date=T0,
            dc_id=1,
            mime_type=mime,
            size=size,
            attributes=list(attributes),
        )
    )


class FakeTelegram:
    """Implements the subset of TelegramClient the app uses."""

    def __init__(self):
        self.entities = {
            utils.get_peer_id(e): e
            for e in (ME, ALICE, BOB, CHANNEL, GROUP, SUPERGROUP)
        }
        self.chats: dict[int, dict[int, Message]] = {}
        self.migrated_from: dict[
            int, int
        ] = {}  # channel marked id -> old chat marked id
        self.fail_after: int | None = None  # raise after yielding this many messages
        self.fail_download: set[int] = set()  # message ids whose download raises
        self.connected = True
        # Read by Message._finish_init:
        self._self_id = ME.id
        self._mb_entity_cache = MbEntityCache()

    # --- test helpers ------------------------------------------------------
    def add(self, chat, *messages):
        box = self.chats.setdefault(utils.get_peer_id(chat), {})
        for m in messages:
            box[m.id] = m

    def edit(self, chat, id, **fields):
        m = self.chats[utils.get_peer_id(chat)][id]
        for k, v in fields.items():
            setattr(m, k, v)

    def delete(self, chat, id):
        del self.chats[utils.get_peer_id(chat)][id]

    # --- client API ----------------------------------------------------------
    def is_connected(self):
        return self.connected

    async def is_user_authorized(self):
        return True

    async def disconnect(self):
        self.connected = False

    async def get_me(self):
        return ME

    async def get_entity(self, x):
        key = x if isinstance(x, int) else utils.get_peer_id(x)
        if key not in self.entities:
            raise ValueError(f"Could not find the input entity for {x!r}")
        return self.entities[key]

    async def get_input_entity(self, x):
        return utils.get_input_peer(await self.get_entity(x))

    async def get_dialogs(self, *a, **kw):
        return []

    def _finish(self, m):
        m._finish_init(self, self.entities, None)
        return m

    async def iter_messages(
        self,
        entity,
        limit=None,
        *,
        min_id=0,
        max_id=0,
        offset_id=0,
        reverse=False,
        offset_date=None,
        search=None,
        from_user=None,
        **_,
    ):
        msgs = sorted(
            self.chats.get(utils.get_peer_id(entity), {}).values(), key=lambda m: m.id
        )
        if not reverse:
            msgs.reverse()
            upper = max(max_id, offset_id)
            if upper:
                msgs = [m for m in msgs if m.id < upper]
            if offset_date:
                msgs = [m for m in msgs if m.date < offset_date]
        else:
            lower = max(min_id, offset_id)
            msgs = [m for m in msgs if m.id > lower and (not max_id or m.id < max_id)]
        if min_id:
            msgs = [m for m in msgs if m.id > min_id]
        if search:
            msgs = [
                m
                for m in msgs
                if search.lower() in (getattr(m, "message", "") or "").lower()
            ]
        if from_user is not None:
            uid = utils.get_peer_id(await self.get_entity(from_user))
            msgs = [m for m in msgs if m.sender_id == uid]
        for n, m in enumerate(msgs[:limit] if limit else msgs):
            if self.fail_after is not None and n >= self.fail_after:
                raise ConnectionError("simulated network failure")
            yield self._finish(m)

    async def get_messages(self, entity, limit=None, ids=None, **kw):
        if ids is not None:
            box = self.chats.get(utils.get_peer_id(entity), {})
            many = isinstance(ids, list)
            found = [
                self._finish(box[i]) if i in box else None
                for i in (ids if many else [ids])
            ]
            return found if many else found[0]
        return [m async for m in self.iter_messages(entity, limit, **kw)]

    async def download_media(self, message, file=None, **kw):
        if message.id in self.fail_download:
            raise ConnectionError("simulated download failure")
        Path(file).write_bytes(b"media-bytes-%d" % message.id)
        return str(file)

    async def __call__(self, request):
        # Only GetFullChannelRequest is used (to detect basic group migration).
        old = self.migrated_from.get(utils.get_peer_id(request.channel))
        full = SimpleNamespace(migrated_from_chat_id=-old if old else None)
        return SimpleNamespace(full_chat=full)
