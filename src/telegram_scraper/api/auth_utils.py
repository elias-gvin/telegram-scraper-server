"""Authentication utilities for API."""

from fastapi import Header, HTTPException, Depends
from typing import Annotated
from telethon import TelegramClient

from ..config import ServerConfig


# Global config (will be set by server.py)
_config: ServerConfig = None


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

    This is an async generator dependency that automatically handles
    client cleanup after the request completes.

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

    session_path = str(_config.sessions_path / username)

    client = TelegramClient(session_path, _config.api_id, _config.api_hash)

    try:
        await client.connect()

        if not await client.is_user_authorized():
            raise HTTPException(
                status_code=401,
                detail=f"Telegram session for '{username}' is not authorized",
            )

        yield client

    finally:
        # Always disconnect the client after request completes
        if client.is_connected():
            await client.disconnect()
