"""Example usage of the Telegram scraper tools."""

import asyncio
import logging
from pathlib import Path
from telegram_scraper_cli import (
    authorize_telegram_client,
    search_channels,
    list_all_channels,
    dump_channel
)

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


async def example_authorization():
    """Example: Authorize Telegram client."""
    api_id = 12345  # Your API ID
    api_hash = "your_api_hash_here"  # Your API Hash
    
    client = await authorize_telegram_client(api_id, api_hash, session_name='session')
    
    # Use the client for other operations...
    # Don't forget to disconnect when done
    await client.disconnect()


async def example_search():
    """Example: Search for channels/groups."""
    api_id = 12345
    api_hash = "your_api_hash_here"
    
    # First, authorize
    client = await authorize_telegram_client(api_id, api_hash)
    
    try:
        # Search for channels matching a query
        results = await search_channels(client, "python")
        for result in results:
            logger.info(f"Found: {result['title']} (ID: {result['id']})")
        
        # Or list all channels
        all_channels = await list_all_channels(client)
        logger.info(f"Total channels/groups: {len(all_channels)}")
        
    finally:
        await client.disconnect()


async def example_dump():
    """Example: Dump messages from a channel."""
    api_id = 12345
    api_hash = "your_api_hash_here"
    channel_id = "-1001234567890"  # Channel ID
    output_dir = Path("./output")
    
    # First, authorize
    client = await authorize_telegram_client(api_id, api_hash)
    
    try:
        # Dump channel with date range
        await dump_channel(
            client=client,
            output_dir=output_dir,
            channel_id=channel_id,
            start_date="2024-01-01",
            end_date="2024-12-31",
            scrape_media=True
        )
        
        # Or dump without date limits
        await dump_channel(
            client=client,
            output_dir=output_dir,
            channel_id=channel_id,
            scrape_media=True
        )
        
    finally:
        await client.disconnect()


async def complete_workflow():
    """Example: Complete workflow - authorize, search, then dump."""
    api_id = 12345
    api_hash = "your_api_hash_here"
    output_dir = Path("./output")
    
    # Step 1: Authorize
    logger.info("Step 1: Authorizing...")
    client = await authorize_telegram_client(api_id, api_hash)
    
    try:
        # Step 2: Search for channels
        logger.info("Step 2: Searching for channels...")
        results = await search_channels(client, "python")
        
        if not results:
            logger.warning("No channels found")
            return
        
        # Step 3: Dump the first result
        logger.info("Step 3: Dumping channel...")
        channel_id = results[0]['id']
        await dump_channel(
            client=client,
            output_dir=output_dir,
            channel_id=channel_id,
            scrape_media=True
        )
        
    finally:
        await client.disconnect()


if __name__ == "__main__":
    # Run one of the examples
    # asyncio.run(example_authorization())
    # asyncio.run(example_search())
    # asyncio.run(example_dump())
    asyncio.run(complete_workflow())

