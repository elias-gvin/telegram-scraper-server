"""CLI tool for Telegram authorization."""

import asyncio
import logging
import os
import sys
from dotenv import load_dotenv
from telethon import TelegramClient

from .auth import authorize_telegram_client

logging.basicConfig(
    level=logging.ERROR,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


async def main():
    """Main function for authorization CLI."""
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
    
    print("Starting Telegram authorization...")
    print(f"Using session file: {session_name}.session")
    
    client = None
    try:
        client = await authorize_telegram_client(api_id, api_hash, session_name)
        print("✅ Authorization successful! You can now use other tools.")
    except Exception as e:
        print(f"❌ Authorization failed: {e}", file=sys.stderr)
        logger.error(f"Authorization failed: {e}", exc_info=True)
        sys.exit(1)
    finally:
        if client:
            await client.disconnect()
            logger.debug("Disconnected from Telegram")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(0)

