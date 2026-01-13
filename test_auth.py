#!/usr/bin/env python3
"""Test script for Telegram authorization."""

import asyncio
import logging
import os
from dotenv import load_dotenv
from telegram_scraper_cli import authorize_telegram_client

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


async def test_authorization():
    """Test Telegram client authorization."""
    # Load environment variables
    load_dotenv()
    
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    
    if not api_id or not api_hash:
        logger.error("TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in .env file")
        return
    
    try:
        api_id = int(api_id)
    except ValueError:
        logger.error("TELEGRAM_API_ID must be a valid integer")
        return
    
    logger.info("Starting authorization test...")
    logger.info(f"API ID: {api_id}")
    logger.info(f"API Hash: {api_hash[:8]}...")
    
    client = None
    try:
        client = await authorize_telegram_client(
            api_id=api_id,
            api_hash=api_hash,
            session_name='test_session'
        )
        
        logger.info("✅ Authorization successful!")
        logger.info(f"Session file: test_session.session")
        
        # Verify we're authorized
        if await client.is_user_authorized():
            logger.info("✅ Client is authorized and ready to use")
        else:
            logger.warning("⚠️ Client is not authorized")
            
    except Exception as e:
        logger.error(f"❌ Authorization failed: {e}", exc_info=True)
    finally:
        if client:
            await client.disconnect()
            logger.info("Disconnected from Telegram")


if __name__ == "__main__":
    asyncio.run(test_authorization())

