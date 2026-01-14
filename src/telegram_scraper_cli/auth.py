import asyncio
import logging
import os
import sys
from dotenv import load_dotenv
from telethon import TelegramClient
import logging
from io import StringIO
from pathlib import Path
import qrcode
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

logging.basicConfig(
    level=logging.ERROR,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


def _display_qr_code_ascii(qr_login) -> None:
    """Display QR code as ASCII art."""
    qr = qrcode.QRCode(box_size=1, border=1)
    qr.add_data(qr_login.url)
    qr.make()

    f = StringIO()
    qr.print_ascii(out=f)
    f.seek(0)
    print(f.read())


async def _qr_code_auth(client: TelegramClient) -> bool:
    """Authenticate using QR code."""
    print("\nPlease scan the QR code with your Telegram app:")
    print("1. Open Telegram on your phone")
    print("2. Go to Settings > Devices > Scan QR")
    print("3. Scan the code below\n")

    try:
        qr_login = await client.qr_login()
        _display_qr_code_ascii(qr_login)
        print("\nScan the QR code with your Telegram app...")

        await qr_login.wait()
        print("\n✅ Successfully logged in via QR code!")
        return True
    except SessionPasswordNeededError:
        password = input("Two-factor authentication enabled. Enter your password: ")
        try:
            await client.sign_in(password=password)
            print("\n✅ Successfully logged in with 2FA!")
            return True
        except Exception as e:
            print(f"\n❌ 2FA authentication failed: {e}")
            logger.error(f"2FA authentication failed: {e}", exc_info=True)
            return False
    except Exception as e:
        print(f"\n❌ QR code authentication failed: {e}")
        logger.error(f"QR code authentication failed: {e}", exc_info=True)
        return False


async def _phone_auth(client: TelegramClient) -> bool:
    """Authenticate using phone number."""
    try:
        phone = input("Enter your phone number: ")
        await client.send_code_request(phone)
        code = input("Enter the code you received: ")

        try:
            await client.sign_in(phone, code)
            print("\n✅ Successfully logged in via phone!")
            return True
        except SessionPasswordNeededError:
            password = input("Two-factor authentication enabled. Enter your password: ")
            await client.sign_in(password=password)
            print("\n✅ Successfully logged in with 2FA!")
            return True
    except Exception as e:
        print(f"\n❌ Phone authentication failed: {e}")
        logger.error(f"Phone authentication failed: {e}", exc_info=True)
        return False


async def authorize_telegram_client(
    api_id: int, api_hash: str, session_name: str = "session"
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
            print("✅ Already authenticated!")
            return client

        # Need to authenticate
        print("\n=== Authentication Required ===")
        print("Choose authentication method:")
        print("[1] QR Code (Recommended - No phone number needed)")
        print("[2] Phone Number (Traditional method)")

        while True:
            choice = input("Enter your choice (1 or 2): ").strip()
            if choice in ["1", "2"]:
                break
            print("Please enter 1 or 2")

        if choice == "1":
            success = await _qr_code_auth(client)
        else:
            success = await _phone_auth(client)

        if not success:
            await client.disconnect()
            raise ConnectionError("Failed to authorize Telegram client")

        print("✅ Authorization successful!")
        return client

    except Exception as e:
        if client.is_connected():
            await client.disconnect()
        logger.error(f"Authorization failed: {e}")
        raise


# TODO: add ability to specify session name and credentials from command line.
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
