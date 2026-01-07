from dotenv import load_dotenv
import argparse
import logging
import os
import sys
import asyncio

# from telegram_scraper_cli.scraper import OptimizedTelegramScraper

# Set up basic logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Telegram Channel Scraper - Scrape messages and media from Telegram channels",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
        Examples:
        # Scrape specific channels (media download enabled by default)
        python telegram-scraper.py --channels -1001234567890 -1009876543210

        # Scrape channels without media download
        python telegram-scraper.py --channels -1001234567890 --no-scrape-media

        # Use API credentials from command line
        python telegram-scraper.py --channels -1001234567890 --api-credentials "12345:abcdef1234567890"

        # Use API credentials from .env file (default)
        python telegram-scraper.py --channels -1001234567890
        """,
    )

    parser.add_argument(
        "--channels",
        nargs="+",
        help="List of channel IDs to scrape (e.g., -1001234567890 -1009876543210)",
    )

    parser.add_argument(
        "--no-scrape-media",
        action="store_false",
        dest="scrape_media",
        help="Disable media download (media download is enabled by default)",
    )

    parser.add_argument(
        "--api-credentials",
        type=str,
        default=None,
        help='API credentials in format "api_id:api_hash" (overrides .env file)',
    )

    parser.add_argument(
        "--log-level",
        type=str,
        default="DEBUG",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set the logging level (default: INFO)",
    )

    return parser.parse_args()


async def extract_api_credentials(api_credentials: str) -> tuple[int, str]:
    api_id = None
    api_hash = None

    info_message = "Please provide API credentials in the format 'api_id:api_hash' or set API_ID and API_HASH in the .env file."

    if not api_credentials:
        api_id = os.getenv("TELEGRAM_API_ID")
        api_hash = os.getenv("TELEGRAM_API_HASH")
        if not api_id or not api_hash:
            raise ValueError(f"API credentials not found. {info_message}")
        return api_id, api_hash

    try:
        credentials = api_credentials.split(":")
        if len(credentials) != 2:
            raise ValueError
        api_id = int(credentials[0].strip())
        api_hash = credentials[1].strip()
    except ValueError:
        logger.error(f"Invalid API credentials format. {info_message}")
        raise ValueError(f"Invalid API credentials format")

    return api_id, api_hash


async def main():
    args = parse_args()

    # Set log level
    logging.getLogger().setLevel(getattr(logging, args.log_level.upper()))

    # Load environment variables from .env file
    load_dotenv()
    api_id, api_hash = await extract_api_credentials(args.api_credentials)

    logger.debug(f"API ID: {api_id}, API Hash: {api_hash[:8]}...")
    logger.debug(f"Scrape media: {args.scrape_media}")

    # scraper = OptimizedTelegramScraper()
    # await scraper.run(
    #     api_id=api_id,
    #     api_hash=api_hash,
    #     channel_ids=args.channels,
    #     scrape_media=getattr(args, "scrape_media", None),
    # )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Program interrupted. Exiting...")
        sys.exit()
    except Exception as e:
        logger.exception("An error occurred:")
        sys.exit(1)
