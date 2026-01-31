"""CLI tool for authenticating users with Telegram."""

import argparse
import asyncio
import sys
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from .config import load_config


async def authenticate_user(username: str, api_id: str, api_hash: str, sessions_path: Path):
    """
    Authenticate user and create session file.
    
    Args:
        username: Username for session file
        api_id: Telegram API ID
        api_hash: Telegram API hash
        sessions_path: Path to sessions directory
    """
    # Ensure sessions directory exists
    sessions_path.mkdir(parents=True, exist_ok=True)
    
    session_file = sessions_path / username
    
    client = TelegramClient(str(session_file), api_id, api_hash)
    
    print(f"Authenticating user: {username}")
    print("=" * 60)
    
    await client.connect()
    
    if await client.is_user_authorized():
        print(f"✓ User '{username}' is already authenticated!")
        me = await client.get_me()
        print(f"  Logged in as: {me.first_name} {me.last_name or ''} (@{me.username or 'no username'})")
        print(f"  Session file: {session_file}.session")
        await client.disconnect()
        return 0
    
    # Not authorized, start authentication flow
    phone = input("Enter your phone number (with country code, e.g., +1234567890): ")
    
    await client.send_code_request(phone)
    print(f"\n✓ Code sent to {phone}")
    
    code = input("Enter the code you received: ")
    
    try:
        await client.sign_in(phone, code)
    except SessionPasswordNeededError:
        # 2FA is enabled
        password = input("Two-factor authentication is enabled. Enter your password: ")
        await client.sign_in(password=password)
    
    # Check if signed in successfully
    if await client.is_user_authorized():
        me = await client.get_me()
        print("\n" + "=" * 60)
        print("✓ Authentication successful!")
        print(f"  Logged in as: {me.first_name} {me.last_name or ''} (@{me.username or 'no username'})")
        print(f"  User ID: {me.id}")
        print(f"  Session saved to: {session_file}.session")
        print("=" * 60)
        print(f"\nYou can now use the API with header:")
        print(f"  X-Telegram-Username: {username}")
        await client.disconnect()
        return 0
    else:
        print("\n✗ Authentication failed!")
        await client.disconnect()
        return 1


def main():
    """Main entry point for authentication CLI."""
    parser = argparse.ArgumentParser(
        description="Authenticate with Telegram and create session file",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    
    parser.add_argument(
        "username",
        type=str,
        help="Username for session file (can be any identifier)"
    )
    
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to YAML configuration file"
    )
    
    parser.add_argument(
        "--api-id",
        type=str,
        help="Telegram API ID (overrides config)"
    )
    
    parser.add_argument(
        "--api-hash",
        type=str,
        help="Telegram API hash (overrides config)"
    )
    
    parser.add_argument(
        "--sessions-path",
        type=Path,
        help="Path to sessions directory (overrides config)"
    )
    
    args = parser.parse_args()
    
    # Build CLI overrides
    cli_overrides = {}
    if args.api_id:
        cli_overrides["api_id"] = args.api_id
    if args.api_hash:
        cli_overrides["api_hash"] = args.api_hash
    if args.sessions_path:
        cli_overrides["sessions_path"] = args.sessions_path
    
    # Load config
    try:
        config = load_config(config_path=args.config, cli_overrides=cli_overrides)
    except ValueError as e:
        print(f"Configuration error: {e}")
        print("\nYou must provide api_id and api_hash via:")
        print("  1. Config file (--config config.yaml)")
        print("  2. Environment variables (TELEGRAM_API_ID, TELEGRAM_API_HASH)")
        print("  3. CLI parameters (--api-id, --api-hash)")
        return 1
    
    # Run authentication
    try:
        return asyncio.run(authenticate_user(
            args.username,
            config.api_id,
            config.api_hash,
            config.sessions_path
        ))
    except KeyboardInterrupt:
        print("\n\n✗ Authentication cancelled by user")
        return 1
    except Exception as e:
        print(f"\n✗ Error during authentication: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

