import warnings

# Suppress Telethon's experimental async sessions warning
warnings.filterwarnings(
    "ignore",
    message=".*async sessions support is an experimental feature.*",
    category=UserWarning,
)

from .client import get_client
from .config import RuntimeSettings, load_credentials_from_env
from .database import (
    create_db_and_tables,
    dialog_db_paths,
    ensure_dialog_directories,
    get_session,
    operations,
)
from .database.models import Dialog, MediaFile, Message
from .database.session import get_engine
from .models import MessageData, SyncStats
from .scraper import stream_messages_with_cache, sync_messages_to_cache

__all__ = [
    "MessageData",
    "SyncStats",
    "RuntimeSettings",
    "create_db_and_tables",
    "dialog_db_paths",
    "ensure_dialog_directories",
    "get_client",
    "get_engine",
    "get_session",
    "load_credentials_from_env",
    "operations",
    "stream_messages_with_cache",
    "sync_messages_to_cache",
    "Dialog",
    "MediaFile",
    "Message",
]
