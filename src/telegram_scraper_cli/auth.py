import asyncio
import logging
import os
import sys
from dotenv import load_dotenv
from io import StringIO
from pathlib import Path
import qrcode
from telethon import TelegramClient
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    PhoneNumberInvalidError,
    FloodWaitError,
)

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
        try:
            await client.send_code_request(phone)
        except PhoneNumberInvalidError:
            print("\n❌ Invalid phone number. Please check and try again.")
            logger.error("Invalid phone number provided")
            return False
        except FloodWaitError as e:
            print(
                f"\n❌ Too many requests. Please wait {e.seconds} seconds before trying again."
            )
            logger.error(f"FloodWaitError: Wait {e.seconds} seconds")
            return False
        except Exception as e:
            print(f"\n❌ Failed to send code: {e}")
            logger.error(f"Failed to send code: {e}", exc_info=True)
            return False

        max_attempts = 3
        for attempt in range(max_attempts):
            if attempt > 0:
                print(f"\nAttempt {attempt + 1} of {max_attempts}")
            code = input("Enter the code you received: ")

            try:
                await client.sign_in(phone, code)
                print("\n✅ Successfully logged in via phone!")
                return True
            except PhoneCodeInvalidError:
                if attempt < max_attempts - 1:
                    print(
                        f"\n❌ Invalid code. Please try again ({max_attempts - attempt - 1} attempt(s) remaining)."
                    )
                    logger.warning(f"Invalid phone code (attempt {attempt + 1})")
                else:
                    print("\n❌ Invalid code. Maximum attempts reached.")
                    logger.error("Invalid phone code: Maximum attempts reached")
                    return False
            except PhoneCodeExpiredError:
                print("\n❌ The code has expired. Please request a new code.")
                logger.error("Phone code expired")
                return False
            except SessionPasswordNeededError:
                password = input(
                    "Two-factor authentication enabled. Enter your password: "
                )
                try:
                    await client.sign_in(password=password)
                    print("\n✅ Successfully logged in with 2FA!")
                    return True
                except Exception as e:
                    print(f"\n❌ 2FA authentication failed: {e}")
                    logger.error(f"2FA authentication failed: {e}", exc_info=True)
                    return False
            except FloodWaitError as e:
                print(
                    f"\n❌ Too many requests. Please wait {e.seconds} seconds before trying again."
                )
                logger.error(f"FloodWaitError during sign-in: Wait {e.seconds} seconds")
                return False
            except Exception as e:
                print(f"\n❌ Authentication failed: {e}")
                logger.error(f"Phone authentication failed: {e}", exc_info=True)
                return False

        return False
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
            raise ConnectionError(
                "Failed to authorize Telegram client. Please check your credentials and try again."
            )

        print("✅ Authorization successful!")
        return client

    except ConnectionError:
        # Re-raise ConnectionError as-is (we may have raised it above)
        if client.is_connected():
            await client.disconnect()
        raise
    except Exception as e:
        if client.is_connected():
            await client.disconnect()
        logger.error(f"Authorization failed: {e}", exc_info=True)
        raise ConnectionError(
            f"Failed to connect or authorize Telegram client: {e}"
        ) from e


if __name__ == "__main__":
    pass
