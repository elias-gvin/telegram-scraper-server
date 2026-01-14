import logging
import os
from typing import List, Dict, Optional, Any
from telethon import TelegramClient
from telethon.tl.types import Channel, Chat
import asyncio
from dotenv import load_dotenv
from rapidfuzz import fuzz
from dataclasses import dataclass

from .auth import authorize_telegram_client

logger = logging.getLogger(__name__)


@dataclass
class ChannelInfo:
    title: str
    id: str
    username: Optional[str]
    type: str
    participants_count: Optional[int]

    def __str__(self) -> str:
        return (
            f"Title: {self.title} \n"
            f"ID: {self.id} \n"
            f"Type: {self.type} \n"
            f"Username: @{self.username if self.username else 'N/A'} \n"
            f"Participants: {self.participants_count if self.participants_count is not None else 'N/A'}"
        )


async def get_channels_info(
    client: TelegramClient, limit: Optional[int] = None
) -> List[ChannelInfo]:
    if not client.is_connected():
        raise ConnectionError("Telegram client is not connected")

    if not await client.is_user_authorized():
        raise ConnectionError("Telegram client is not authorized")

    results = []

    logger.info("Listing all channels and groups...")

    try:
        async for dialog in client.iter_dialogs(limit=limit):
            entity = dialog.entity

            is_system_dialog = dialog.id == 777000
            is_channel_or_chat = isinstance(entity, Channel) or isinstance(entity, Chat)
            if is_system_dialog or not is_channel_or_chat:
                continue

            channel_type = (
                "Channel"
                if isinstance(entity, Channel) and entity.broadcast
                else "Group"
            )
            username = getattr(entity, "username", None) or ""
            participants_count = getattr(entity, "participants_count", None)

            result = ChannelInfo(
                title=dialog.title or "",
                id=str(dialog.id),
                username=username if username else None,
                type=channel_type,
                participants_count=participants_count,
            )

            results.append(result)

        logger.info(f"✅ Found {len(results)} channels/groups")
        return results

    except Exception as e:
        logger.error(f"Error listing channels: {e}")
        raise


async def search_channels_by_title(
    client: TelegramClient,
    search_query: str,
    similarity_threshold: int = 100,
) -> List[Tuple[ChannelInfo, float]]:
    if not client.is_connected():
        raise ConnectionError("Telegram client is not connected")

    if not await client.is_user_authorized():
        raise ConnectionError("Telegram client is not authorized")

    channels_info = await get_channels_info(client, limit=None)

    logger.info(
        f"Searching for channels/groups matching '{search_query}' (similarity_threshold: {similarity_threshold})..."
    )

    search_results = []
    for channel_info in channels_info:
        title_score = fuzz.partial_ratio(
            search_query.lower(), channel_info.title.lower()
        )
        if title_score >= similarity_threshold:
            search_results.append((channel_info, title_score))
    return search_results


# TODO: add ability to specify session name and credentials from command line.
# TODO: add ability to search by name.
async def main():
    # args = parse_args()
    # logging.getLogger().setLevel(getattr(logging, args.log_level.upper()))

    load_dotenv()
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    session_name = os.getenv("TELEGRAM_SESSION_NAME", "session")
    # TODO: add proper checks for read values.
    client = await authorize_telegram_client(api_id, api_hash, session_name)
    results = await search_channels_by_title(client, "saved", similarity_threshold=80)
    for result, score in results:
        print(f"{result}")
        print(f"Score: {score}\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(0)
