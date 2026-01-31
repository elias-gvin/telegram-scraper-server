"""Message history API endpoints."""

from fastapi import APIRouter, Query, Path, Depends, HTTPException
from fastapi.responses import StreamingResponse
from typing import Annotated, List
from pydantic import BaseModel
from datetime import datetime
import json
import logging

from telethon import TelegramClient

from .auth import get_telegram_client
from ..config import ServerConfig
from .. import db_helper
from ..scraper import stream_messages_with_cache


router = APIRouter(prefix="/api/v1", tags=["history"])
logger = logging.getLogger(__name__)


# Global config (will be set by server.py)
_config: ServerConfig = None


def set_config(config: ServerConfig):
    """Set global config for history module."""
    global _config
    _config = config


class MessageResponse(BaseModel):
    """Message data response model."""
    message_id: int
    date: str
    sender_id: int
    first_name: str | None
    last_name: str | None
    username: str | None
    message: str
    media_type: str | None
    media_uuid: str | None
    media_size: int | None
    reply_to: int | None
    post_author: str | None
    is_forwarded: int
    forwarded_from_channel_id: int | None


class MessagesListResponse(BaseModel):
    """Response for non-streaming messages request."""
    messages: List[MessageResponse]


def parse_date(date_str: str) -> datetime:
    """Parse date string to datetime."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            return datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid date format: {date_str}. Use 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS'"
            )


@router.get(
    "/history/{channel_id}",
    summary="Get message history",
    description="""
    Stream message history with smart caching.
    
    - chunk_size=0: Return all messages as single JSON array
    - chunk_size>0: Stream messages in chunks via Server-Sent Events (SSE)
    
    Examples:
    - `/api/v1/history/123?start_date=2024-01-01&end_date=2024-01-31&chunk_size=250`
    - `/api/v1/history/123?start_date=2024-01-01&end_date=2024-01-31&chunk_size=0` (all at once)
    - `/api/v1/history/123?start_date=2024-01-01&end_date=2024-01-31&force_refresh=true` (bypass cache)
    """
)
async def get_history(
    channel_id: Annotated[int, Path(description="Channel ID")],
    start_date: Annotated[str, Query(description="Start date (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)")],
    end_date: Annotated[str, Query(description="End date (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)")],
    chunk_size: Annotated[
        int,
        Query(ge=0, description="Chunk size (0 = return all messages in one response)")
    ] = 250,
    force_refresh: Annotated[
        bool,
        Query(description="Force re-download even if cached")
    ] = False,
    client: TelegramClient = Depends(get_telegram_client),
):
    """
    Get message history for a channel.
    
    Requires X-Telegram-Username header for authentication.
    """
    if _config is None:
        raise HTTPException(status_code=500, detail="Server configuration not initialized")
    
    try:
        # Parse dates
        start_dt = parse_date(start_date)
        end_dt = parse_date(end_date)
        
        if start_dt >= end_dt:
            raise HTTPException(status_code=400, detail="start_date must be before end_date")
        
        # Open/create channel database
        conn = db_helper.open_channel_db(
            output_dir=_config.output_path,
            channel_id=channel_id,
            check_same_thread=False
        )
        
        # Ensure schema exists
        db_helper.ensure_schema(conn)
        
        # Upsert channel info
        try:
            entity = await client.get_entity(channel_id)
            channel_name = getattr(entity, "title", None) or str(channel_id)
            me = await client.get_me()
            username = getattr(me, "username", None)
            user_str = f"@{username}" if username else str(getattr(me, "id", ""))
            
            db_helper.upsert_channel(
                conn,
                channel_id=str(channel_id),
                channel_name=channel_name,
                user=user_str
            )
        except Exception as e:
            logger.warning(f"Could not update channel info: {e}")
        
        if chunk_size == 0:
            # Return all messages at once (not streamed)
            messages = []
            async for batch in stream_messages_with_cache(
                client, conn, channel_id, start_dt, end_dt,
                telegram_batch_size=_config.telegram_batch_size,
                client_batch_size=1000,  # Large chunks internally
                force_refresh=force_refresh,
                scrape_media=_config.download_media,
                max_media_size_mb=_config.max_media_size_mb,
                output_dir=_config.output_path,
            ):
                messages.extend(batch)
            
            return {"messages": messages}
        
        else:
            # Stream messages in chunks via SSE
            async def event_stream():
                try:
                    async for batch in stream_messages_with_cache(
                        client, conn, channel_id, start_dt, end_dt,
                        telegram_batch_size=_config.telegram_batch_size,
                        client_batch_size=chunk_size,
                        force_refresh=force_refresh,
                        scrape_media=_config.download_media,
                        max_media_size_mb=_config.max_media_size_mb,
                        output_dir=_config.output_path,
                    ):
                        yield f"data: {json.dumps({'messages': batch})}\n\n"
                finally:
                    # Cleanup
                    conn.close()
                    if client.is_connected():
                        await client.disconnect()
            
            return StreamingResponse(event_stream(), media_type="text/event-stream")
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in get_history: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

