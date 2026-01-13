"""Runner module for setting up connections and running the Telegram scraper."""

import sqlite3
import asyncio
import logging
from pathlib import Path
from typing import Optional, Tuple
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from .scraper import OptimizedTelegramScraper, ScrapeParams

logger = logging.getLogger(__name__)


async def ensure_telegram_authorized(client: TelegramClient) -> bool:
    """
    Ensure the Telegram client is authorized. If not, prompt for authentication.
    
    Args:
        client: TelegramClient instance
        
    Returns:
        True if authorized, False otherwise
    """
    try:
        if await client.is_user_authorized():
            logger.info("✅ Already authenticated!")
            return True
        
        logger.info("\n=== Authentication Required ===")
        logger.info("Choose authentication method:")
        logger.info("[1] QR Code (Recommended - No phone number needed)")
        logger.info("[2] Phone Number (Traditional method)")
        
        while True:
            choice = input("Enter your choice (1 or 2): ").strip()
            if choice in ['1', '2']:
                break
            logger.warning("Please enter 1 or 2")
        
        if choice == '1':
            return await qr_code_auth(client)
        else:
            return await phone_auth(client)
            
    except Exception as e:
        logger.error(f"Authorization check failed: {e}")
        return False


async def qr_code_auth(client: TelegramClient) -> bool:
    """
    Authenticate using QR code.
    
    Args:
        client: TelegramClient instance
        
    Returns:
        True if authentication successful, False otherwise
    """
    logger.info("\nPlease scan the QR code with your Telegram app:")
    logger.info("1. Open Telegram on your phone")
    logger.info("2. Go to Settings > Devices > Scan QR")
    logger.info("3. Scan the code below\n")
    
    try:
        qr_login = await client.qr_login()
        logger.info(f"\nQR Code URL: {qr_login.url}")
        logger.info("Scan the QR code with your Telegram app...")
        
        await qr_login.wait()
        logger.info("\n✅ Successfully logged in via QR code!")
        return True
    except SessionPasswordNeededError:
        password = input("Two-factor authentication enabled. Enter your password: ")
        try:
            await client.sign_in(password=password)
            logger.info("\n✅ Successfully logged in with 2FA!")
            return True
        except Exception as e:
            logger.error(f"\n❌ 2FA authentication failed: {e}")
            return False
    except Exception as e:
        logger.error(f"\n❌ QR code authentication failed: {e}")
        return False


async def phone_auth(client: TelegramClient) -> bool:
    """
    Authenticate using phone number.
    
    Args:
        client: TelegramClient instance
        
    Returns:
        True if authentication successful, False otherwise
    """
    try:
        phone = input("Enter your phone number: ")
        await client.send_code_request(phone)
        code = input("Enter the code you received: ")
        
        try:
            await client.sign_in(phone, code)
            logger.info("\n✅ Successfully logged in via phone!")
            return True
        except SessionPasswordNeededError:
            password = input("Two-factor authentication enabled. Enter your password: ")
            await client.sign_in(password=password)
            logger.info("\n✅ Successfully logged in with 2FA!")
            return True
    except Exception as e:
        logger.error(f"\n❌ Phone authentication failed: {e}")
        return False


async def run_scraper(
    output_dir: Path,
    api_id: int,
    api_hash: str,
    channel_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    scrape_media: bool = True,
    session_name: str = 'session'
) -> None:
    """
    Main function to set up connections and run the scraper.
    
    Args:
        output_dir: Directory where database and media files will be stored
        api_id: Telegram API ID
        api_hash: Telegram API Hash
        channel_id: Channel ID (e.g., '-1001234567890')
        start_date: Optional start date for scraping (format: YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)
        end_date: Optional end date for scraping (format: YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)
        scrape_media: Whether to download media files (default: True)
        session_name: Name for the Telegram session file (default: 'session')
    """
    db_connection = None
    client = None
    
    try:
        # Create database connection
        logger.info(f"Creating database connection for channel {channel_id}...")
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        channel_dir = output_dir / channel_id
        channel_dir.mkdir(parents=True, exist_ok=True)
        
        db_file = channel_dir / f'{channel_id}.db'
        db_connection = sqlite3.connect(str(db_file), check_same_thread=False)
        logger.info("✅ Database connection created")
        
        # Create and connect Telegram client
        logger.info("Creating Telegram client...")
        client = TelegramClient(session_name, api_id, api_hash)
        await client.connect()
        logger.info("✅ Telegram client connected")
        
        # Ensure client is authorized
        if not await ensure_telegram_authorized(client):
            raise ConnectionError("Failed to authorize Telegram client")
        
        # Get channel entity to extract channel name (optional, for logging)
        try:
            from telethon.tl.types import PeerChannel
            entity = await client.get_entity(PeerChannel(int(channel_id)) if channel_id.startswith('-') else channel_id)
            channel_name = getattr(entity, 'title', channel_id) or channel_id
        except Exception:
            channel_name = channel_id
        
        # Create scrape parameters
        # Note: ScrapeParams expects Tuple[str, int] but only uses channel[0]
        # We'll use the channel_id as string and 0 as placeholder int
        scrape_params = ScrapeParams(
            start_date=start_date,
            end_date=end_date,
            channel=(channel_id, 0),  # Tuple[str, int] - int is not used by scraper
            scrape_media=scrape_media,
            output_dir=Path(output_dir)
        )
        
        # Create scraper instance
        scraper = OptimizedTelegramScraper(
            client=client,
            db_connection=db_connection,
            scrape_params=scrape_params
        )
        
        # Run the scraper
        logger.info(f"Starting scrape for channel {channel_id} ({channel_name})...")
        await scraper.scrape_channel()
        logger.info("✅ Scraping completed successfully")
        
    except Exception as e:
        logger.error(f"Error during scraping: {e}", exc_info=True)
        raise
    finally:
        # Cleanup
        if db_connection:
            db_connection.close()
            logger.info("Database connection closed")
        
        if client:
            await client.disconnect()
            logger.info("Telegram client disconnected")

