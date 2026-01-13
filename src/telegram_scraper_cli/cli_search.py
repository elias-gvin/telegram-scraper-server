"""CLI tool for searching Telegram channels and groups."""

import asyncio
import argparse
import logging
import os
import sys
from dotenv import load_dotenv
from telethon import TelegramClient

from .auth import authorize_telegram_client
from .search import search_channels, list_all_channels

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Search for Telegram channels and groups",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Search for channels matching a query
  python -m telegram_scraper_cli.cli_search --query python

  # List all channels
  python -m telegram_scraper_cli.cli_search --list-all

  # Search with limit
  python -m telegram_scraper_cli.cli_search --query python --limit 10
        """,
    )
    
    parser.add_argument(
        "--query",
        type=str,
        help="Search query (channel/group name or username)",
    )
    
    parser.add_argument(
        "--list-all",
        action="store_true",
        help="List all accessible channels and groups",
    )
    
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of results to return",
    )
    
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set the logging level (default: INFO)",
    )
    
    return parser.parse_args()


async def main():
    """Main function for search CLI."""
    args = parse_args()
    
    # Set log level
    logging.getLogger().setLevel(getattr(logging, args.log_level.upper()))
    
    # Load environment variables
    load_dotenv()
    
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    
    if not api_id or not api_hash:
        logger.error("TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in .env file")
        sys.exit(1)
    
    try:
        api_id = int(api_id)
    except ValueError:
        logger.error("TELEGRAM_API_ID must be a valid integer")
        sys.exit(1)
    
    session_name = os.getenv("TELEGRAM_SESSION_NAME", "session")
    
    if not args.query and not args.list_all:
        logger.error("Either --query or --list-all must be specified")
        sys.exit(1)
    
    client = None
    try:
        # Authorize (will use existing session if available)
        logger.info("Connecting to Telegram...")
        client = await authorize_telegram_client(api_id, api_hash, session_name)
        
        if args.list_all:
            print("Listing all channels and groups...")
            results = await list_all_channels(client, limit=args.limit)
        else:
            print(f"Searching for channels/groups matching '{args.query}'...")
            results = await search_channels(client, args.query, limit=args.limit)
        
        # Print results
        if not results:
            print("No results found")
        else:
            print(f"\n{'='*80}")
            print(f"Found {len(results)} result(s):")
            print(f"{'='*80}")
            for i, result in enumerate(results, 1):
                print(f"\n[{i}] {result['title']}")
                print(f"    ID: {result['id']}")
                print(f"    Type: {result['type']}")
                if result['username']:
                    print(f"    Username: @{result['username']}")
                if result['participants_count']:
                    print(f"    Participants: {result['participants_count']}")
            print(f"\n{'='*80}")
        
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        logger.error(f"Error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        if client:
            await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(0)

