"""API routes for Telegram Scraper server."""

from .channels import router as channels_router
from .history import router as history_router
from .files import router as files_router

__all__ = ["channels_router", "history_router", "files_router"]
