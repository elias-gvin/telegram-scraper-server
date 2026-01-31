"""CLI tool for authenticating users with Telegram."""

import asyncio
import logging
import sys
from io import StringIO
from pathlib import Path

import click
import qrcode
from telethon import TelegramClient
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    PhoneNumberInvalidError,
    FloodWaitError,
)

from .config import load_config

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
    click.echo(f.read())


async def _qr_code_auth(client: TelegramClient) -> bool:
    """Authenticate using QR code."""
    click.echo("\nPlease scan the QR code with your Telegram app:")
    click.echo("1. Open Telegram on your phone")
    click.echo("2. Go to Settings > Devices > Scan QR")
    click.echo("3. Scan the code below\n")

    try:
        qr_login = await client.qr_login()
        _display_qr_code_ascii(qr_login)
        click.echo("\nScan the QR code with your Telegram app...")

        await qr_login.wait()
        click.secho("\n✅ Successfully logged in via QR code!", fg="green")
        return True
    except SessionPasswordNeededError:
        password = click.prompt(
            "Two-factor authentication enabled. Enter your password",
            hide_input=True
        )
        try:
            await client.sign_in(password=password)
            click.secho("\n✅ Successfully logged in with 2FA!", fg="green")
            return True
        except Exception as e:
            click.secho(f"\n❌ 2FA authentication failed: {e}", fg="red")
            logger.error(f"2FA authentication failed: {e}", exc_info=True)
            return False
    except Exception as e:
        click.secho(f"\n❌ QR code authentication failed: {e}", fg="red")
        logger.error(f"QR code authentication failed: {e}", exc_info=True)
        return False


async def _phone_auth(client: TelegramClient) -> bool:
    """Authenticate using phone number."""
    try:
        phone = click.prompt("Enter your phone number")
        try:
            await client.send_code_request(phone)
        except PhoneNumberInvalidError:
            click.secho("\n❌ Invalid phone number. Please check and try again.", fg="red")
            logger.error("Invalid phone number provided")
            return False
        except FloodWaitError as e:
            click.secho(
                f"\n❌ Too many requests. Please wait {e.seconds} seconds before trying again.",
                fg="red"
            )
            logger.error(f"FloodWaitError: Wait {e.seconds} seconds")
            return False
        except Exception as e:
            click.secho(f"\n❌ Failed to send code: {e}", fg="red")
            logger.error(f"Failed to send code: {e}", exc_info=True)
            return False

        max_attempts = 3
        for attempt in range(max_attempts):
            if attempt > 0:
                click.echo(f"\nAttempt {attempt + 1} of {max_attempts}")
            code = click.prompt("Enter the code you received")

            try:
                await client.sign_in(phone, code)
                click.secho("\n✅ Successfully logged in via phone!", fg="green")
                return True
            except PhoneCodeInvalidError:
                if attempt < max_attempts - 1:
                    click.secho(
                        f"\n❌ Invalid code. Please try again ({max_attempts - attempt - 1} attempt(s) remaining).",
                        fg="red"
                    )
                    logger.warning(f"Invalid phone code (attempt {attempt + 1})")
                else:
                    click.secho("\n❌ Invalid code. Maximum attempts reached.", fg="red")
                    logger.error("Invalid phone code: Maximum attempts reached")
                    return False
            except PhoneCodeExpiredError:
                click.secho("\n❌ The code has expired. Please request a new code.", fg="red")
                logger.error("Phone code expired")
                return False
            except SessionPasswordNeededError:
                password = click.prompt(
                    "Two-factor authentication enabled. Enter your password",
                    hide_input=True
                )
                try:
                    await client.sign_in(password=password)
                    click.secho("\n✅ Successfully logged in with 2FA!", fg="green")
                    return True
                except Exception as e:
                    click.secho(f"\n❌ 2FA authentication failed: {e}", fg="red")
                    logger.error(f"2FA authentication failed: {e}", exc_info=True)
                    return False
            except FloodWaitError as e:
                click.secho(
                    f"\n❌ Too many requests. Please wait {e.seconds} seconds before trying again.",
                    fg="red"
                )
                logger.error(f"FloodWaitError during sign-in: Wait {e.seconds} seconds")
                return False
            except Exception as e:
                click.secho(f"\n❌ Authentication failed: {e}", fg="red")
                logger.error(f"Phone authentication failed: {e}", exc_info=True)
                return False

        return False
    except Exception as e:
        click.secho(f"\n❌ Phone authentication failed: {e}", fg="red")
        logger.error(f"Phone authentication failed: {e}", exc_info=True)
        return False


async def authorize_telegram_client(
    api_id: int, 
    api_hash: str, 
    session_name: str = "session",
    show_user_info: bool = False
) -> TelegramClient:
    """
    Create, connect, and authorize a Telegram client.

    Args:
        api_id: Telegram API ID
        api_hash: Telegram API Hash
        session_name: Name for the session file (default: 'session')
        show_user_info: Whether to display user info after successful auth

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
            click.secho("✅ Already authenticated!", fg="green")
            if show_user_info:
                await _display_user_info(client, session_name)
            return client

        # Need to authenticate
        click.secho("\n=== Authentication Required ===", bold=True)
        click.echo("Choose authentication method:")
        click.echo("[1] QR Code (Recommended - No phone number needed)")
        click.echo("[2] Phone Number (Traditional method)")

        while True:
            choice = click.prompt("Enter your choice (1 or 2)", type=str).strip()
            if choice in ["1", "2"]:
                break
            click.echo("Please enter 1 or 2")

        if choice == "1":
            success = await _qr_code_auth(client)
        else:
            success = await _phone_auth(client)

        if not success:
            await client.disconnect()
            raise ConnectionError(
                "Failed to authorize Telegram client. Please check your credentials and try again."
            )

        click.secho("✅ Authorization successful!", fg="green")
        
        if show_user_info:
            await _display_user_info(client, session_name)
        
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


async def _display_user_info(client: TelegramClient, session_name: str) -> None:
    """Display authenticated user information."""
    try:
        me = await client.get_me()
        click.echo("\n" + "=" * 60)
        click.secho("📋 User Information:", bold=True)
        click.echo(f"  Name: {me.first_name} {me.last_name or ''}".strip())
        if me.username:
            click.echo(f"  Username: @{me.username}")
        click.echo(f"  User ID: {me.id}")
        click.echo(f"  Phone: {me.phone or 'N/A'}")
        click.echo(f"  Session: {session_name}.session")
        click.echo("=" * 60)
    except Exception as e:
        logger.warning(f"Could not fetch user info: {e}")


async def authenticate_user_cli(
    username: str, 
    api_id: int, 
    api_hash: str, 
    sessions_path: Path
) -> int:
    """
    CLI wrapper for user authentication with named session files.
    
    Args:
        username: Username identifier for the session file
        api_id: Telegram API ID
        api_hash: Telegram API hash
        sessions_path: Path to sessions directory
    
    Returns:
        Exit code (0 for success, 1 for failure)
    """
    # Ensure sessions directory exists
    sessions_path.mkdir(parents=True, exist_ok=True)
    
    session_file = str(sessions_path / username)
    
    click.secho(f"🔐 Authenticating user: {username}", bold=True)
    click.echo("=" * 60)
    
    try:
        client = await authorize_telegram_client(
            api_id=api_id,
            api_hash=api_hash,
            session_name=session_file,
            show_user_info=True
        )
        
        click.echo(f"\n✅ You can now use this session with:")
        click.secho(f"   X-Telegram-Username: {username}", fg="cyan")
        click.echo()
        
        await client.disconnect()
        return 0
        
    except ConnectionError as e:
        click.secho(f"\n❌ Authentication failed: {e}", fg="red")
        return 1
    except Exception as e:
        click.secho(f"\n❌ Unexpected error: {e}", fg="red")
        logger.error(f"Unexpected error during CLI authentication: {e}", exc_info=True)
        return 1


@click.command()
@click.argument("username", type=str)
@click.option(
    "--config",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Path to YAML configuration file"
)
@click.option(
    "--api-id",
    type=str,
    default=None,
    help="Telegram API ID (overrides config)"
)
@click.option(
    "--api-hash",
    type=str,
    default=None,
    help="Telegram API hash (overrides config)"
)
@click.option(
    "--sessions-path",
    type=click.Path(path_type=Path),
    default=None,
    help="Path to sessions directory (overrides config)"
)
def main(username, config, api_id, api_hash, sessions_path):
    """
    Authenticate with Telegram and create session file.
    
    USERNAME is the identifier for the session file (can be any name).
    
    \b
    Examples:
      # Authenticate with config file
      tgsc-auth myusername --config config.yaml
      
      # Authenticate with explicit credentials
      tgsc-auth myusername --api-id 12345 --api-hash abc123
      
      # Use environment variables (TELEGRAM_API_ID, TELEGRAM_API_HASH)
      tgsc-auth myusername
    """
    # Build CLI overrides
    cli_overrides = {}
    if api_id:
        cli_overrides["api_id"] = api_id
    if api_hash:
        cli_overrides["api_hash"] = api_hash
    if sessions_path:
        cli_overrides["sessions_path"] = sessions_path
    
    # Load config
    try:
        loaded_config = load_config(config_path=config, cli_overrides=cli_overrides)
    except ValueError as e:
        click.secho(f"❌ Configuration error: {e}", fg="red")
        click.echo("\nYou must provide api_id and api_hash via:")
        click.echo("  1. Config file (--config config.yaml)")
        click.echo("  2. Environment variables (TELEGRAM_API_ID, TELEGRAM_API_HASH)")
        click.echo("  3. CLI parameters (--api-id, --api-hash)")
        sys.exit(1)
    
    # Run authentication
    try:
        exit_code = asyncio.run(authenticate_user_cli(
            username,
            loaded_config.api_id,
            loaded_config.api_hash,
            loaded_config.sessions_path
        ))
        sys.exit(exit_code)
    except KeyboardInterrupt:
        click.secho("\n\n❌ Authentication cancelled by user", fg="red")
        sys.exit(1)
    except Exception as e:
        click.secho(f"\n❌ Error during authentication: {e}", fg="red")
        logger.error(f"CLI error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

