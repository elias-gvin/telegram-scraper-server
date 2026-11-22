"""API routes for Telegram Scraper server."""

from .auth import router as auth_router
from .dialogs import router as dialogs_router
from .files import router as files_router
from .history import router as history_router
from .search import router as search_router
from .settings import router as settings_router

API_VERSION = "v4"
API_PREFIX = f"/api/{API_VERSION}"

__all__ = [
    "API_PREFIX",
    "API_VERSION",
    "auth_router",
    "dialogs_router",
    "files_router",
    "history_router",
    "search_router",
    "settings_router",
]
