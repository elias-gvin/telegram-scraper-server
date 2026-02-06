"""API routes for Telegram Scraper server."""

from .dialogs import router as dialogs_router
from .history import router as history_router
from .files import router as files_router

__all__ = ["dialogs_router", "history_router", "files_router"]
