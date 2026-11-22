"""Telegram Scraper: archive Telegram dialogs locally and serve them over HTTP."""

import warnings

# Suppress Telethon's experimental async sessions warning
warnings.filterwarnings(
    "ignore",
    message=".*async sessions support is an experimental feature.*",
    category=UserWarning,
)

from .archive import Archive
from .config import RuntimeSettings, load_credentials_from_env
from .serialize import serialize, serialize_row
from .sync import SyncReport, sync_dialog
from .telegram import connect, resolve

__all__ = [
    "Archive",
    "RuntimeSettings",
    "SyncReport",
    "connect",
    "load_credentials_from_env",
    "resolve",
    "serialize",
    "serialize_row",
    "sync_dialog",
]
