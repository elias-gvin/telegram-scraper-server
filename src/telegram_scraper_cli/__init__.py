"""Telegram Scraper CLI - Tools for authorization, search, and dumping."""

from .auth import authorize_telegram_client
from .search import search_channels, list_all_channels
from .dump import dump_channel
from .scraper import OptimizedTelegramScraper, ScrapeParams, MessageData

__all__ = [
    'authorize_telegram_client',
    'search_channels',
    'list_all_channels',
    'dump_channel',
    'OptimizedTelegramScraper',
    'ScrapeParams',
    'MessageData',
]

