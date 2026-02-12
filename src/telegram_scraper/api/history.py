"""Message history API endpoints."""

from fastapi import APIRouter, Query, Path, Depends, HTTPException
from fastapi.responses import StreamingResponse
from typing import Annotated, List, Optional
from pydantic import BaseModel
from datetime import datetime, timezone
import json
import logging

from telethon import TelegramClient
from telethon.errors import FloodWaitError

from .auth_utils import get_telegram_client
from .deps import get_config
from ..config import ServerConfig
from ..database import operations
from ..database import (
    get_engine,
    create_db_and_tables,
    get_session,
    ensure_dialog_directories,
)
from ..scraper import stream_messages_with_cache


router = APIRouter(tags=["history"])
logger = logging.getLogger(__name__)


class MediaInfo(BaseModel):
    """Media information for a message."""

    type: str
    uuid: str
    original_filename: str | None
    size: int


class MessageResponse(BaseModel):
    """Message data response model."""

    message_id: int
    date: str
    sender_id: int
    first_name: str | None
    last_name: str | None
    username: str | None
    message: str
    media: MediaInfo | None
    reply_to: int | None
    post_author: str | None
    is_forwarded: int
    forwarded_from_channel_id: int | None


class MessagesListResponse(BaseModel):
    """Response for non-streaming messages request."""

    messages: List[MessageResponse]


def parse_date(date_str: str) -> datetime:
    """Parse date string to datetime (assumes UTC if no timezone specified)."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid date format: {date_str}. Use 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS'",
            )


@router.get(
    "/history/{dialog_id}",
    summary="Get message history",
    description="""
    Stream message history with smart caching via Server-Sent Events (SSE).
    
    - Messages are always streamed in chunks (SSE format)
    - chunk_size: Number of messages per chunk (default: 100, must be > 0)
    - start_date: Optional, defaults to beginning of chat
    - end_date: Optional, defaults to current time
    - force_refresh: Bypass cache and re-download from Telegram
    
    Examples:
    - `/api/v1/history/123` (all messages, default 100/chunk)
    - `/api/v1/history/123?chunk_size=50` (smaller chunks for faster updates)
    - `/api/v1/history/123?start_date=2024-01-01&end_date=2024-01-31`
    - `/api/v1/history/123?end_date=2024-01-31` (from beginning to Jan 31)
    - `/api/v1/history/123?start_date=2024-01-01` (from Jan 1 to now)
    - `/api/v1/history/123?force_refresh=true` (bypass cache)
    """,
)
async def get_history(
    dialog_id: Annotated[int, Path(description="Dialog ID")],
    start_date: Annotated[
        Optional[str],
        Query(
            description="Start date (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS), defaults to chat beginning"
        ),
    ] = None,
    end_date: Annotated[
        Optional[str],
        Query(
            description="End date (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS), defaults to now"
        ),
    ] = None,
    chunk_size: Annotated[
        int,
        Query(gt=0, description="Number of messages per chunk in streaming response"),
    ] = 100,
    force_refresh: Annotated[
        bool, Query(description="Force re-download even if cached")
    ] = False,
    client: TelegramClient = Depends(get_telegram_client),
    config: ServerConfig = Depends(get_config),
):
    """
    Get message history for a dialog.

    Requires X-Telegram-Username header for authentication.
    """
    try:
        # Parse dates - use defaults if not provided
        if start_date:
            start_dt = parse_date(start_date)
        else:
            # FIXME: Ugly vibe-coded approach to handle very old messages. Fix it
            # Default to beginning of Telegram (or a very early date)
            start_dt = datetime(2013, 1, 1, tzinfo=timezone.utc)

        if end_date:
            end_dt = parse_date(end_date)
        else:
            # Default to current time
            end_dt = datetime.now(timezone.utc)

        if start_dt >= end_dt:
            raise HTTPException(
                status_code=400, detail="start_date must be before end_date"
            )

        # Ensure dialog directory structure and initialize database
        paths = ensure_dialog_directories(config.dialogs_dir, dialog_id)

        # Create engine and tables
        engine = get_engine(paths.db_file, check_same_thread=False)
        create_db_and_tables(engine)

        # Upsert dialog info
        try:
            entity = await client.get_entity(dialog_id)
            dialog_name = getattr(entity, "title", None) or str(dialog_id)
            dialog_username = getattr(entity, "username", None)

            with get_session(paths.db_file, check_same_thread=False) as session:
                operations.upsert_dialog(
                    session,
                    dialog_id=str(dialog_id),
                    name=dialog_name,
                    username=dialog_username,
                )
        except Exception as e:
            logger.warning(f"Could not update dialog info: {e}")

        batch_size = chunk_size

        async def event_stream():
            with get_session(paths.db_file, check_same_thread=False) as session:
                try:
                    async for batch in stream_messages_with_cache(
                        client,
                        session,
                        dialog_id,
                        start_dt,
                        end_dt,
                        telegram_batch_size=config.telegram_batch_size,
                        client_batch_size=batch_size,
                        force_refresh=force_refresh,
                        scrape_media=config.download_media,
                        max_media_size_mb=config.max_media_size_mb,
                        output_dir=config.dialogs_dir,
                        repair_media=config.repair_media,
                    ):
                        yield f"data: {json.dumps({'messages': batch})}\n\n"
                finally:
                    # Session cleanup happens automatically with context manager
                    pass

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    except HTTPException:
        raise
    except FloodWaitError as e:
        logger.error("Telegram rate limit in get_history: retry-after %ds", e.seconds)
        raise HTTPException(
            status_code=429,
            detail=f"Telegram rate limit exceeded. Retry after {e.seconds} seconds.",
            headers={"Retry-After": str(e.seconds)},
        )
    except Exception as e:
        logger.error(f"Error in get_history: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
