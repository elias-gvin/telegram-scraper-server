# """Telegram Scraper CLI - Tools for authorization, search, and dumping."""

# from .authenticate import authorize_telegram_client
# from .search import search_channels, list_all_channels
# from .dump import dump_channel
# from .scraper import OptimizedTelegramScraper, ScrapeParams, MessageData

# __all__ = [
#     'authorize_telegram_client',
#     'search_channels',
#     'list_all_channels',
#     'dump_channel',
#     'OptimizedTelegramScraper',
#     'ScrapeParams',
#     'MessageData',
# ]

import warnings

# Suppress Telethon's experimental async sessions warning
warnings.filterwarnings(
    "ignore",
    message=".*async sessions support is an experimental feature.*",
    category=UserWarning,
)
