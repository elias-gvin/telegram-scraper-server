"""Authentication utilities for API."""

from fastapi import Header, HTTPException, Depends
from typing import Annotated
from telethon import TelegramClient
import asyncio

from ..config import ServerConfig


# Global config (will be set by server.py)
_config: ServerConfig = None

# Client pool to reuse Telegram clients per user
_client_pool: dict[str, TelegramClient] = {}
_client_locks: dict[str, asyncio.Lock] = {}
_pool_lock: asyncio.Lock = asyncio.Lock()  # Protects pool and locks dict


def set_config(config: ServerConfig):
    """Set global config for auth module."""
    global _config
    _config = config


async def get_authenticated_user(
    x_telegram_username: Annotated[str, Header()] = None,
) -> str:
    """
    Get authenticated user from X-Telegram-Username header.
    Validates that user has active Telegram session.

    Raises:
        HTTPException: If user is not authenticated

    Returns:
        Username string
    """
    if not x_telegram_username:
        raise HTTPException(
            status_code=401, detail="Missing X-Telegram-Username header"
        )

    if _config is None:
        raise HTTPException(
            status_code=500, detail="Server configuration not initialized"
        )

    # Check if user has valid Telegram session
    session_file = _config.sessions_path / f"{x_telegram_username}.session"
    if not session_file.exists():
        raise HTTPException(
            status_code=401,
            detail=f"User '{x_telegram_username}' not authenticated. Please run authentication first.",
        )

    return x_telegram_username


async def get_telegram_client(
    username: str = Depends(get_authenticated_user),
):
    """
    Get Telegram client for authenticated user.

    Uses a client pool to reuse connections across requests, preventing
    "database is locked" errors when multiple requests access the same
    session file concurrently.

    Args:
        username: Authenticated username (from dependency)

    Yields:
        Connected and authorized TelegramClient

    Raises:
        HTTPException: If session is invalid or not authorized
    """
    if _config is None:
        raise HTTPException(
            status_code=500, detail="Server configuration not initialized"
        )

    # Get or create per-user lock (protected by pool lock)
    async with _pool_lock:
        if username not in _client_locks:
            _client_locks[username] = asyncio.Lock()
        lock = _client_locks[username]

    # Acquire per-user lock to prevent concurrent client creation for same user
    async with lock:
        # Check if client already exists and is connected
        if username in _client_pool:
            client = _client_pool[username]
            if client.is_connected():
                # Verify still authorized
                if await client.is_user_authorized():
                    yield client
                    return
                else:
                    # Session expired, remove from pool
                    await client.disconnect()
                    del _client_pool[username]

        # Create new client
        session_path = str(_config.sessions_path / username)
        client = TelegramClient(session_path, _config.api_id, _config.api_hash)

        try:
            await client.connect()

            if not await client.is_user_authorized():
                raise HTTPException(
                    status_code=401,
                    detail=f"Telegram session for '{username}' is not authorized",
                )

            # Add to pool for reuse
            _client_pool[username] = client

            yield client

        except Exception:
            # On error, disconnect and don't add to pool
            if client.is_connected():
                await client.disconnect()
            raise


async def cleanup_clients():
    """
    Cleanup all pooled Telegram clients.

    Should be called on server shutdown to gracefully disconnect all clients.
    """
    for username, client in list(_client_pool.items()):
        if client.is_connected():
            await client.disconnect()
    _client_pool.clear()
    _client_locks.clear()
