"""Message search API endpoints.

Uses Telegram's native search API:
- messages.search     — search within a specific dialog
- messages.searchGlobal — search across all dialogs
"""

from fastapi import APIRouter, Query, Path, Depends, HTTPException
from typing import Annotated, Optional, List
from pydantic import BaseModel, AliasChoices
import logging

from telethon import TelegramClient
from telethon.errors import FloodWaitError

from .auth_utils import get_telegram_client


router = APIRouter(tags=["search"])
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class SearchMessageResult(BaseModel):
    """A single message matching the search query."""

    message_id: int
    dialog_id: int
    dialog_name: Optional[str] = None
    date: str  # YYYY-MM-DD HH:MM:SS (UTC)
    edit_date: Optional[str] = None
    sender_id: Optional[int] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    username: Optional[str] = None
    message: str
    reply_to: Optional[int] = None
    post_author: Optional[str] = None
    is_forwarded: int
    forwarded_from_channel_id: Optional[int] = None


class SearchMessagesResponse(BaseModel):
    """Paginated message search response."""

    query: str
    total: int
    limit: int
    results: List[SearchMessageResult]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_date(date_str: str):
    """Validate and parse a date string into a timezone-aware datetime."""
    from datetime import datetime, timezone

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


def _message_to_result(msg) -> Optional[SearchMessageResult]:
    """Convert a Telethon Message to a SearchMessageResult."""
    if msg is None or not hasattr(msg, "id"):
        return None

    # Sender info
    sender = msg.sender
    sender_id = None
    first_name = None
    last_name = None
    username = None

    if sender:
        sender_id = sender.id
        first_name = getattr(sender, "first_name", None)
        last_name = getattr(sender, "last_name", None)
        username = getattr(sender, "username", None)

    # Dialog info
    chat = msg.chat
    dialog_id = msg.chat_id or 0
    dialog_name = None
    if chat:
        dialog_name = getattr(chat, "title", None)
        if not dialog_name:
            # For user chats, build name from first/last
            fn = getattr(chat, "first_name", "") or ""
            ln = getattr(chat, "last_name", "") or ""
            dialog_name = f"{fn} {ln}".strip() or None

    # Date formatting
    date_str = msg.date.strftime("%Y-%m-%d %H:%M:%S") if msg.date else ""
    edit_date_str = (
        msg.edit_date.strftime("%Y-%m-%d %H:%M:%S") if msg.edit_date else None
    )

    # Forward info
    is_forwarded = 1 if msg.forward else 0
    forwarded_from_channel_id = None
    if msg.forward and hasattr(msg.forward, "chat_id"):
        forwarded_from_channel_id = msg.forward.chat_id

    # Reply info
    reply_to = None
    if msg.reply_to:
        reply_to = getattr(msg.reply_to, "reply_to_msg_id", None)

    return SearchMessageResult(
        message_id=msg.id,
        dialog_id=dialog_id,
        dialog_name=dialog_name,
        date=date_str,
        edit_date=edit_date_str,
        sender_id=sender_id,
        first_name=first_name,
        last_name=last_name,
        username=username,
        message=msg.text or "",
        reply_to=reply_to,
        post_author=getattr(msg, "post_author", None),
        is_forwarded=is_forwarded,
        forwarded_from_channel_id=forwarded_from_channel_id,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/search/messages/{dialog_id}",
    response_model=SearchMessagesResponse,
    summary="Search messages within a specific chat",
    description="""
Search for messages containing specific words or phrases within a single dialog
using Telegram's native `messages.search` API.

**No pre-caching required** — searches are performed directly against the Telegram servers.

Examples:
- `/api/v3/search/messages/123?q=bitcoin`
- `/api/v3/search/messages/123?q=meeting&start_date=2024-01-01&end_date=2024-06-30`
- `/api/v3/search/messages/123?q=urgent&from_user=456789`
""",
)
async def search_messages_in_dialog(
    dialog_id: Annotated[int, Path(description="Dialog ID to search within")],
    query: Annotated[
        str,
        Query(
            min_length=1,
            description="Search query (word or phrase to find)",
            validation_alias=AliasChoices("q", "query"),
        ),
    ],
    start_date: Annotated[
        Optional[str],
        Query(
            description="Only messages before this date (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS). "
            "Acts as upper bound — Telegram returns messages older than this date."
        ),
    ] = None,
    end_date: Annotated[
        Optional[str],
        Query(
            description="Only messages after this date (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS). "
            "Acts as lower bound — messages newer than this date."
        ),
    ] = None,
    from_user: Annotated[
        Optional[int],
        Query(description="Only messages sent by this user ID"),
    ] = None,
    limit: Annotated[
        int,
        Query(ge=1, le=500, description="Maximum number of results (max 500)"),
    ] = 50,
    client: TelegramClient = Depends(get_telegram_client),
) -> SearchMessagesResponse:
    """
    Search for messages within a specific dialog via Telegram API.

    Requires X-Telegram-Username header for authentication.
    """
    # Validate dates
    offset_date = _parse_date(start_date) if start_date else None
    min_date = _parse_date(end_date) if end_date else None

    if offset_date and min_date and min_date >= offset_date:
        raise HTTPException(
            status_code=400, detail="end_date must be before start_date"
        )

    try:
        # Build iter_messages kwargs
        kwargs = {
            "entity": dialog_id,
            "search": query,
            "limit": limit,
        }
        if offset_date:
            kwargs["offset_date"] = offset_date
        if from_user:
            kwargs["from_user"] = from_user

        results: list[SearchMessageResult] = []
        total = 0

        async for msg in client.iter_messages(**kwargs):
            # Apply min_date filter (end_date = lower bound)
            if min_date and msg.date and msg.date < min_date:
                break  # Messages are ordered newest→oldest, so we can stop

            result = _message_to_result(msg)
            if result:
                results.append(result)
                total += 1

        return SearchMessagesResponse(
            query=query,
            total=total,
            limit=limit,
            results=results,
        )

    except FloodWaitError as e:
        logger.error(
            "Telegram rate limit in search_messages: retry-after %ds", e.seconds
        )
        raise HTTPException(
            status_code=429,
            detail=f"Telegram rate limit exceeded. Retry after {e.seconds} seconds.",
            headers={"Retry-After": str(e.seconds)},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error searching messages in dialog {dialog_id}: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get(
    "/search/messages",
    response_model=SearchMessagesResponse,
    summary="Search messages across all chats",
    description="""
Search for messages containing specific words or phrases across **all** Telegram dialogs
using Telegram's native `messages.searchGlobal` API.

**No pre-caching required** — searches are performed directly against the Telegram servers.

Examples:
- `/api/v3/search/messages?q=bitcoin`
- `/api/v3/search/messages?q=meeting&start_date=2024-06-01`
- `/api/v3/search/messages?q=invoice&limit=100`
""",
)
async def search_messages_global(
    query: Annotated[
        str,
        Query(
            min_length=1,
            description="Search query (word or phrase to find)",
            validation_alias=AliasChoices("q", "query"),
        ),
    ],
    start_date: Annotated[
        Optional[str],
        Query(
            description="Only messages before this date (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS). "
            "Acts as upper bound — Telegram returns messages older than this date."
        ),
    ] = None,
    end_date: Annotated[
        Optional[str],
        Query(
            description="Only messages after this date (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS). "
            "Acts as lower bound — messages newer than this date."
        ),
    ] = None,
    limit: Annotated[
        int,
        Query(ge=1, le=500, description="Maximum number of results (max 500)"),
    ] = 50,
    client: TelegramClient = Depends(get_telegram_client),
) -> SearchMessagesResponse:
    """
    Search for messages across all dialogs via Telegram API (global search).

    Requires X-Telegram-Username header for authentication.
    """
    # Validate dates
    offset_date = _parse_date(start_date) if start_date else None
    min_date = _parse_date(end_date) if end_date else None

    if offset_date and min_date and min_date >= offset_date:
        raise HTTPException(
            status_code=400, detail="end_date must be before start_date"
        )

    try:
        # entity=None triggers messages.searchGlobal
        kwargs = {
            "entity": None,
            "search": query,
            "limit": limit,
        }
        if offset_date:
            kwargs["offset_date"] = offset_date

        results: list[SearchMessageResult] = []
        total = 0

        async for msg in client.iter_messages(**kwargs):
            # Apply min_date filter (end_date = lower bound)
            if min_date and msg.date and msg.date < min_date:
                break

            result = _message_to_result(msg)
            if result:
                results.append(result)
                total += 1

        return SearchMessagesResponse(
            query=query,
            total=total,
            limit=limit,
            results=results,
        )

    except FloodWaitError as e:
        logger.error(
            "Telegram rate limit in search_messages_global: retry-after %ds",
            e.seconds,
        )
        raise HTTPException(
            status_code=429,
            detail=f"Telegram rate limit exceeded. Retry after {e.seconds} seconds.",
            headers={"Retry-After": str(e.seconds)},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in global message search: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
