"""Channel and dialog search API endpoints."""

from fastapi import APIRouter, Query, Depends, HTTPException
from typing import Annotated, Optional, List
from pydantic import BaseModel
from enum import Enum
from difflib import SequenceMatcher
from datetime import datetime, timezone
import logging

from telethon import TelegramClient
from telethon.tl.types import Channel, Chat, User as TelethonUser
from telethon.errors import FloodWaitError

from .auth_utils import get_telegram_client


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/v1", tags=["channels"])


# --- Find-channels models ---


class SearchBy(str, Enum):
    """Search criteria for finding channels."""

    username = "username"
    channel_id = "channel_id"
    title = "title"


class ChannelInfo(BaseModel):
    """Channel information response."""

    channel_id: int
    title: str
    username: Optional[str] = None
    participants_count: Optional[int] = None
    description: Optional[str] = None


# --- Dialog listing models ---


class DialogType(str, Enum):
    """Type of Telegram dialog."""

    user = "user"
    group = "group"
    supergroup = "supergroup"
    channel = "channel"
    bot = "bot"


class DialogInfo(BaseModel):
    """Dialog information response."""

    id: int
    title: str
    type: DialogType
    username: Optional[str] = None
    unread_count: int = 0
    message_count: Optional[int] = None  # approximate, from top message ID
    last_message_date: Optional[str] = None  # ISO 8601
    participants_count: Optional[int] = None


# --- Shared helpers ---


def fuzzy_match_score(text: str, query: str) -> float:
    """Calculate fuzzy match score between text and query (0.0 to 1.0)."""
    return SequenceMatcher(None, text.lower(), query.lower()).ratio()


def entity_to_channel_info(entity) -> ChannelInfo:
    """Convert Telethon entity to ChannelInfo model."""
    return ChannelInfo(
        channel_id=entity.id,
        title=getattr(entity, "title", "") or str(entity.id),
        username=getattr(entity, "username", None),
        participants_count=getattr(entity, "participants_count", None),
        description=getattr(entity, "about", None),
    )


def classify_dialog(dialog) -> DialogType:
    """Classify a Telethon Dialog into a DialogType."""
    entity = dialog.entity
    if dialog.is_user:
        if getattr(entity, "bot", False):
            return DialogType.bot
        return DialogType.user
    if dialog.is_group:
        return DialogType.group
    # dialog.is_channel covers both supergroups and broadcast channels
    if getattr(entity, "megagroup", False):
        return DialogType.supergroup
    return DialogType.channel


def dialog_to_dialog_info(dialog) -> DialogInfo:
    """Convert a Telethon Dialog to a DialogInfo model."""
    entity = dialog.entity
    # Build a human-readable title
    if dialog.is_user:
        first = getattr(entity, "first_name", "") or ""
        last = getattr(entity, "last_name", "") or ""
        title = f"{first} {last}".strip() or str(entity.id)
    else:
        title = getattr(entity, "title", "") or str(entity.id)

    # Approximate message count from the top message ID
    message_count = None
    if dialog.message is not None:
        message_count = dialog.message.id

    # Last message date
    last_message_date = None
    if dialog.date is not None:
        last_message_date = dialog.date.isoformat()

    return DialogInfo(
        id=entity.id,
        title=title,
        type=classify_dialog(dialog),
        username=getattr(entity, "username", None),
        unread_count=dialog.unread_count or 0,
        message_count=message_count,
        last_message_date=last_message_date,
        participants_count=getattr(entity, "participants_count", None),
    )


@router.get(
    "/find-channels",
    response_model=List[ChannelInfo],
    summary="Search for Telegram channels",
    description="""
    Search for channels by username, ID, or title.
    
    Examples:
    - `/api/v1/find-channels?search_by=username&query=durov`
    - `/api/v1/find-channels?search_by=channel_id&query=-1001234567890`
    - `/api/v1/find-channels?search_by=title&query=crypto&title_threshold=0.7`
    """,
)
async def find_channels(
    search_by: Annotated[SearchBy, Query(description="Search criteria")],
    query: Annotated[str, Query(description="Search query")],
    title_threshold: Annotated[
        float,
        Query(
            ge=0.0,
            le=1.0,
            description="Fuzzy match threshold for title search (0.0-1.0)",
        ),
    ] = 0.8,
    client: TelegramClient = Depends(get_telegram_client),
) -> List[ChannelInfo]:
    """
    Find channels based on search criteria.

    Requires X-Telegram-Username header for authentication.
    """
    try:
        if search_by == SearchBy.channel_id:
            # Direct lookup by ID
            try:
                channel_id = int(query)
                entity = await client.get_entity(channel_id)
                return [entity_to_channel_info(entity)]
            except ValueError:
                raise HTTPException(
                    status_code=400, detail=f"Invalid channel ID: {query}"
                )

        elif search_by == SearchBy.username:
            # Lookup by username
            entity = await client.get_entity(query)
            return [entity_to_channel_info(entity)]

        elif search_by == SearchBy.title:
            # Fuzzy search in user's dialogs
            channels = []
            async for dialog in client.iter_dialogs():
                entity = dialog.entity

                # Skip system dialog (Telegram Service Notifications)
                is_system_dialog = dialog.id == 777000
                # Only include channels and chats/groups
                is_channel_or_chat = isinstance(entity, Channel) or isinstance(
                    entity, Chat
                )

                if is_system_dialog or not is_channel_or_chat:
                    continue

                score = fuzzy_match_score(dialog.title, query)
                if score >= title_threshold:
                    channels.append(entity_to_channel_info(dialog.entity))

            # Sort by match score (best matches first)
            channels.sort(key=lambda c: fuzzy_match_score(c.title, query), reverse=True)
            return channels

    except FloodWaitError as e:
        logger.error("Telegram rate limit in find_channels: retry-after %ds", e.seconds)
        raise HTTPException(
            status_code=429,
            detail=f"Telegram rate limit exceeded. Retry after {e.seconds} seconds.",
            headers={"Retry-After": str(e.seconds)},
        )
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Channel not found: {e}")

    return []


def _parse_date(date_str: str) -> datetime:
    """Parse a date string into a timezone-aware datetime."""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise HTTPException(
        status_code=400,
        detail=f"Invalid date format: '{date_str}'. Use 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS'",
    )


@router.get(
    "/dialogs",
    response_model=List[DialogInfo],
    summary="List all dialogs with optional filters",
    description="""
    List all Telegram dialogs (personal chats, groups, supergroups, channels, bots)
    with optional filtering.

    **Filters** (all optional – omit to return everything):
    - `type`: one or more dialog types to include (repeat the param for multiple)
    - `query` + `title_threshold`: fuzzy title search
    - `min_messages` / `max_messages`: approximate message count range
    - `last_message_after` / `last_message_before`: last-message date window

    Examples:
    - `/api/v1/dialogs` — all dialogs
    - `/api/v1/dialogs?type=group&type=supergroup` — only groups
    - `/api/v1/dialogs?min_messages=100&last_message_after=2024-01-01`
    - `/api/v1/dialogs?query=work&title_threshold=0.4`
    """,
)
async def list_dialogs(
    type: Annotated[
        Optional[List[DialogType]],
        Query(description="Filter by dialog type(s). Repeat for multiple."),
    ] = None,
    query: Annotated[
        Optional[str],
        Query(description="Fuzzy search on dialog title"),
    ] = None,
    title_threshold: Annotated[
        float,
        Query(
            ge=0.0,
            le=1.0,
            description="Fuzzy match threshold for title search (0.0-1.0)",
        ),
    ] = 0.5,
    min_messages: Annotated[
        Optional[int],
        Query(ge=0, description="Minimum approximate message count"),
    ] = None,
    max_messages: Annotated[
        Optional[int],
        Query(ge=0, description="Maximum approximate message count"),
    ] = None,
    last_message_after: Annotated[
        Optional[str],
        Query(description="Only dialogs with last message after this date (YYYY-MM-DD)"),
    ] = None,
    last_message_before: Annotated[
        Optional[str],
        Query(description="Only dialogs with last message before this date (YYYY-MM-DD)"),
    ] = None,
    client: TelegramClient = Depends(get_telegram_client),
) -> List[DialogInfo]:
    """
    List all dialogs with optional filters.

    Requires X-Telegram-Username header for authentication.
    """
    # Pre-parse date filters so we fail fast on bad input
    after_dt = _parse_date(last_message_after) if last_message_after else None
    before_dt = _parse_date(last_message_before) if last_message_before else None

    if after_dt and before_dt and after_dt >= before_dt:
        raise HTTPException(
            status_code=400,
            detail="last_message_after must be before last_message_before",
        )

    try:
        results: List[DialogInfo] = []
        async for dialog in client.iter_dialogs():
            # Always skip Telegram Service Notifications
            if dialog.id == 777000:
                continue

            dialog_type = classify_dialog(dialog)

            # --- Filter: type ---
            if type and dialog_type not in type:
                continue

            # --- Filter: fuzzy title ---
            if query:
                title = dialog.title or ""
                if fuzzy_match_score(title, query) < title_threshold:
                    continue

            # --- Filter: message count ---
            msg_count = dialog.message.id if dialog.message else None
            if min_messages is not None and (msg_count is None or msg_count < min_messages):
                continue
            if max_messages is not None and (msg_count is None or msg_count > max_messages):
                continue

            # --- Filter: last message date ---
            msg_date = dialog.date  # datetime or None
            if after_dt and (msg_date is None or msg_date < after_dt):
                continue
            if before_dt and (msg_date is None or msg_date > before_dt):
                continue

            results.append(dialog_to_dialog_info(dialog))

        # Sort by last message date descending (most recent first)
        results.sort(
            key=lambda d: d.last_message_date or "",
            reverse=True,
        )
        return results

    except FloodWaitError as e:
        logger.error("Telegram rate limit in list_dialogs: retry-after %ds", e.seconds)
        raise HTTPException(
            status_code=429,
            detail=f"Telegram rate limit exceeded. Retry after {e.seconds} seconds.",
            headers={"Retry-After": str(e.seconds)},
        )
