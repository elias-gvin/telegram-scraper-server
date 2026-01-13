"""Tool for dumping/scraping messages from a Telegram channel or group."""

import sqlite3
import logging
from pathlib import Path
from typing import Optional
from telethon import TelegramClient
from telethon.tl.types import PeerChannel

from .scraper import OptimizedTelegramScraper, ScrapeParams

logger = logging.getLogger(__name__)


async def dump_channel(
    client: TelegramClient,
    output_dir: Path,
    channel_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    scrape_media: bool = True
) -> None:
    """
    Dump/scrape messages from a Telegram channel or group.
    
    Args:
        client: Authorized TelegramClient instance
        output_dir: Directory where database and media files will be stored
        channel_id: Channel/Group ID (e.g., '-1001234567890')
        start_date: Optional start date for scraping (format: YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)
        end_date: Optional end date for scraping (format: YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)
        scrape_media: Whether to download media files (default: True)
        
    Raises:
        ConnectionError: If client is not connected or authorized
    """
    if not client.is_connected():
        raise ConnectionError("Telegram client is not connected")
    
    if not await client.is_user_authorized():
        raise ConnectionError("Telegram client is not authorized")
    
    db_connection = None
    
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
        
        # Get channel entity to extract channel name (optional, for logging)
        try:
            entity = await client.get_entity(PeerChannel(int(channel_id)) if channel_id.startswith('-') else channel_id)
            channel_name = getattr(entity, 'title', channel_id) or channel_id
        except Exception:
            channel_name = channel_id
        
        # Create scrape parameters
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
        logger.info(f"Starting dump for channel {channel_id} ({channel_name})...")
        await scraper.scrape_channel()
        logger.info("✅ Dump completed successfully")
        
    except Exception as e:
        logger.error(f"Error during dump: {e}", exc_info=True)
        raise
    finally:
        # Cleanup
        if db_connection:
            db_connection.close()
            logger.info("Database connection closed")

