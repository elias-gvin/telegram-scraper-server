"""The one Telegram client of this process, and entity helpers."""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

from telethon import TelegramClient, utils
from telethon.errors import AuthKeyDuplicatedError, BadRequestError, UnauthorizedError

ACCOUNT_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class NotAuthorized(RuntimeError):
    pass


class DialogNotFound(LookupError):
    pass


# Telegram errors meaning the session is no longer valid (revoked, logged out, ...).
SESSION_LOST = (UnauthorizedError, AuthKeyDuplicatedError)


def check_account_name(name: str) -> str:
    if not ACCOUNT_NAME.match(name):
        raise ValueError(
            f"Invalid account name {name!r}: use 1-64 letters, digits, '_' or '-'"
        )
    return name


async def connect(session_path: Path, api_id, api_hash) -> TelegramClient:
    """Connected, authorized client for an existing session file."""
    if not Path(
        f"{session_path}.session"
    ).exists():  # don't let Telethon create an empty one
        raise NotAuthorized(
            f"No Telegram session for '{Path(session_path).name}': run tgsc-auth or POST /auth/qr"
        )
    client = TelegramClient(
        str(session_path), int(api_id), str(api_hash), flood_sleep_threshold=120
    )
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise NotAuthorized(
            f"Session {session_path} is not authorized: run tgsc-auth or POST /auth/qr"
        )
    return client


class TelegramHolder:
    """Creates the client lazily. The lock covers creation only, never a request."""

    def __init__(self, session_path: Path, api_id, api_hash, on_connect=None):
        self.session_path, self.api_id, self.api_hash = session_path, api_id, api_hash
        self.on_connect = (
            on_connect  # async callback(client), e.g. bind the archive to the account
        )
        self._client: TelegramClient | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> TelegramClient:
        async with self._lock:
            if self._client is None or not self._client.is_connected():
                client = await connect(self.session_path, self.api_id, self.api_hash)
                try:
                    if self.on_connect:
                        await self.on_connect(client)
                except BaseException:
                    await client.disconnect()
                    raise
                self._client = client
            return self._client

    async def reset(self):
        """Drop the client (after re-authentication or on shutdown)."""
        async with self._lock:
            if self._client is not None and self._client.is_connected():
                await self._client.disconnect()
            self._client = None


# Re-downloading the whole dialog list is expensive: at most once per client per 5 min.
DIALOG_REFRESH_SECONDS = 300
_dialogs_refreshed_at: dict[int, float] = {}


async def resolve(client, dialog_id: int):
    """Entity for a *marked* dialog id (a bare number is ambiguous: user or channel?)."""
    entity = await _lookup(client, dialog_id)
    marked = utils.get_peer_id(entity)
    if marked != dialog_id:
        raise DialogNotFound(
            f"Dialog id {dialog_id} is not a marked id; did you mean {marked}?"
        )
    return entity


async def _lookup(client, dialog_id: int):
    """Entity for a marked dialog id. If Telethon doesn't know it yet, refresh its
    entity cache from the dialog list (rate-limited) and try once more."""
    try:
        return await client.get_entity(dialog_id)
    except (
        BadRequestError
    ) as e:  # Telegram: invalid id, or no access (e.g. CHANNEL_PRIVATE)
        raise DialogNotFound(
            f"Dialog {dialog_id} not found or not accessible: {e}"
        ) from e
    except ValueError:  # not in Telethon's entity cache yet
        pass
    last = _dialogs_refreshed_at.get(id(client), float("-inf"))
    if time.monotonic() - last >= DIALOG_REFRESH_SECONDS:
        _dialogs_refreshed_at[id(client)] = time.monotonic()
        await client.get_dialogs()
        try:
            return await client.get_entity(dialog_id)
        except (ValueError, BadRequestError):
            pass
    raise DialogNotFound(
        f"Dialog {dialog_id} not found (use the marked id from /search/dialogs)"
    )


def peer_fields(entity) -> dict:
    """Columns of the archive `peers` table for a User / Chat / Channel."""
    return {
        "first_name": getattr(entity, "first_name", None),
        "last_name": getattr(entity, "last_name", None),
        "title": getattr(entity, "title", None),
        "username": getattr(entity, "username", None),
    }


def title_of(entity) -> str:
    fields = peer_fields(entity)
    name = fields["title"] or " ".join(
        x for x in (fields["first_name"], fields["last_name"]) if x
    )
    return name or str(utils.get_peer_id(entity))
