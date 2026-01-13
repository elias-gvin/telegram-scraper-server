"""Tool for Telegram client authorization."""

import logging
from pathlib import Path
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

logger = logging.getLogger(__name__)


async def authorize_telegram_client(
    api_id: int,
    api_hash: str,
    session_name: str = 'session'
) -> TelegramClient:
    """
    Create, connect, and authorize a Telegram client.
    
    Args:
        api_id: Telegram API ID
        api_hash: Telegram API Hash
        session_name: Name for the session file (default: 'session')
        
    Returns:
        Authorized TelegramClient instance
        
    Raises:
        ConnectionError: If connection or authorization fails
    """
    client = TelegramClient(session_name, api_id, api_hash)
    
    try:
        logger.info("Connecting to Telegram...")
        await client.connect()
        logger.info("✅ Connected to Telegram")
        
        # Check if already authorized
        if await client.is_user_authorized():
            logger.info("✅ Already authenticated!")
            return client
        
        # Need to authenticate
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
            success = await _qr_code_auth(client)
        else:
            success = await _phone_auth(client)
        
        if not success:
            await client.disconnect()
            raise ConnectionError("Failed to authorize Telegram client")
        
        logger.info("✅ Authorization successful!")
        return client
        
    except Exception as e:
        if client.is_connected():
            await client.disconnect()
        logger.error(f"Authorization failed: {e}")
        raise


async def _qr_code_auth(client: TelegramClient) -> bool:
    """Authenticate using QR code."""
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


async def _phone_auth(client: TelegramClient) -> bool:
    """Authenticate using phone number."""
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

