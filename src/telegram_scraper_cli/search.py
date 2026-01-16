import logging
import os
import sys
from typing import List, Optional, Literal
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


@dataclass(frozen=True)
class SearchParams:
    """
    Controls which channel fields are searched and how fuzzy matching is applied.
    """

    search_by_username: bool = True
    search_by_channel_id: bool = True
    search_by_title: bool = True
    # 0-100 (RapidFuzz ratio score). 100 means exact match.
    title_similarity_threshold: int = 100


@dataclass(frozen=True)
class SearchResult:
    channel: ChannelInfo
    score: float
    matched_on: Literal["id", "username", "title"]


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


async def search_channels(
    client: TelegramClient,
    search_query: str,
    params: SearchParams = SearchParams(),
) -> List[SearchResult]:
    """
    Search the user's dialogs (channels + groups) using enabled matching strategies.

    - channel id: exact match against dialog id
    - username: fuzzy partial ratio against @username (or raw username)
    - title: fuzzy partial ratio against dialog title (threshold controlled by params)
    """
    if not client.is_connected():
        raise ConnectionError("Telegram client is not connected")

    if not await client.is_user_authorized():
        raise ConnectionError("Telegram client is not authorized")

    query = (search_query or "").strip()
    if not query:
        return []

    channels_info = await get_channels_info(client, limit=None)

    logger.info(
        "Searching channels/groups for '%s' (by_id=%s, by_username=%s, by_title=%s, title_threshold=%s)...",
        query,
        params.search_by_channel_id,
        params.search_by_username,
        params.search_by_title,
        params.title_similarity_threshold,
    )

    results: List[SearchResult] = []
    normalized_query = query.lower().lstrip("@")

    for channel_info in channels_info:
        # Search by channel id (exact).
        if params.search_by_channel_id:
            if normalized_query == str(channel_info.id).lower():
                results.append(SearchResult(channel=channel_info, score=100.0, matched_on="id"))
                continue

        # Search by username (fuzzy, no threshold beyond >0).
        if params.search_by_username and channel_info.username:
            uname_score = float(
                fuzz.partial_ratio(normalized_query, channel_info.username.lower().lstrip("@"))
            )
            if uname_score > 0:
                results.append(SearchResult(channel=channel_info, score=uname_score, matched_on="username"))
                continue

        # Search by title (fuzzy, thresholded).
        if params.search_by_title:
            title_score = float(fuzz.partial_ratio(normalized_query, channel_info.title.lower()))
            if title_score >= float(params.title_similarity_threshold):
                results.append(SearchResult(channel=channel_info, score=title_score, matched_on="title"))

    # Higher score first, then stable-ish by title.
    results.sort(key=lambda r: (-r.score, (r.channel.title or "").lower()))
    return results

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
    params = SearchParams(
        search_by_username=True,
        search_by_channel_id=True,
        search_by_title=True,
        title_similarity_threshold=80,
    )
    results = await search_channels(client, "NotesScraperTest", params=params)
    for r in results:
        print(f"{r.channel}")
        print(f"Matched on: {r.matched_on}")
        print(f"Score: {r.score}\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(0)
