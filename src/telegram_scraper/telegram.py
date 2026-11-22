"""The one Telegram client of this process, and entity helpers."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from telethon import TelegramClient, utils

ACCOUNT_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class NotAuthorized(RuntimeError):
    pass


class DialogNotFound(LookupError):
    pass


def check_account_name(name: str) -> str:
    if not ACCOUNT_NAME.match(name):
        raise ValueError(
            f"Invalid account name {name!r}: use 1-64 letters, digits, '_' or '-'"
        )
    return name


async def connect(session_path: Path, api_id, api_hash) -> TelegramClient:
    """Connected, authorized client for an existing session file."""
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


async def resolve(client, dialog_id: int):
    """Entity for a marked dialog id. Fills Telethon's entity cache from the dialog list if needed."""
    try:
        return await client.get_entity(dialog_id)
    except ValueError:
        await client.get_dialogs()
    try:
        return await client.get_entity(dialog_id)
    except ValueError as e:
        raise DialogNotFound(
            f"Dialog {dialog_id} not found (use the marked id from /search/dialogs)"
        ) from e


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
