"""API routes for Telegram Scraper server."""

from .dialogs import router as dialogs_router
from .history import router as history_router
from .files import router as files_router
from .auth import router as auth_router
from .settings import router as settings_router
from .search import router as search_router

API_VERSION = "v3"
API_PREFIX = f"/api/{API_VERSION}"

__all__ = [
    "dialogs_router",
    "history_router",
    "files_router",
    "auth_router",
    "settings_router",
    "search_router",
    "API_VERSION",
    "API_PREFIX",
]
