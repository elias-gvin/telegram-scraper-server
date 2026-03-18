"""Standalone Telegram client factory for library use (no FastAPI)."""

from __future__ import annotations

from pathlib import Path

from telethon import TelegramClient

from .config import load_credentials_from_env


async def get_client(
    username: str,
    data_dir: Path,
    api_id: str | int | None = None,
    api_hash: str | None = None,
) -> TelegramClient:
    """
    Connect to Telegram using an existing session file.

    Session path: ``{data_dir}/sessions/{username}.session``

    Args:
        username: Session name (same as used with ``tgsc-auth``).
        data_dir: Root data directory containing ``sessions/``.
        api_id: Telegram API ID (from env if omitted).
        api_hash: Telegram API hash (from env if omitted).

    Returns:
        Connected, authorized ``TelegramClient``.

    Raises:
        RuntimeError: If session is missing or not authorized.
    """
    if api_id is None or api_hash is None:
        creds = load_credentials_from_env()
        api_id = api_id if api_id is not None else creds["api_id"]
        api_hash = api_hash if api_hash is not None else creds["api_hash"]

    data_dir = Path(data_dir)
    sessions_dir = data_dir / "sessions"
    session_file = sessions_dir / f"{username}.session"
    if not session_file.exists():
        raise RuntimeError(
            f"No session file at {session_file}. Run tgsc-auth {username} first."
        )

    session_path = str(sessions_dir / username)
    client = TelegramClient(
        session_path,
        int(api_id),
        str(api_hash),
        flood_sleep_threshold=120,
    )
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise RuntimeError(
            f"Telegram session for '{username}' is not authorized. Re-run tgsc-auth."
        )
    return client
