"""Channel search API endpoints."""

from fastapi import APIRouter, Query, Depends, HTTPException
from typing import Annotated, Optional, List
from pydantic import BaseModel, Field
from enum import Enum
from difflib import SequenceMatcher
from telethon import TelegramClient
from telethon.tl.types import Channel, Chat

from .auth import get_telegram_client


router = APIRouter(prefix="/api/v1", tags=["channels"])


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
    """
)
async def find_channels(
    search_by: Annotated[SearchBy, Query(description="Search criteria")],
    query: Annotated[str, Query(description="Search query")],
    title_threshold: Annotated[
        float,
        Query(ge=0.0, le=1.0, description="Fuzzy match threshold for title search (0.0-1.0)")
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
                raise HTTPException(status_code=400, detail=f"Invalid channel ID: {query}")
            except Exception as e:
                raise HTTPException(status_code=404, detail=f"Channel not found: {e}")
        
        elif search_by == SearchBy.username:
            # Lookup by username
            try:
                entity = await client.get_entity(query)
                return [entity_to_channel_info(entity)]
            except Exception as e:
                raise HTTPException(status_code=404, detail=f"Channel not found: {e}")
        
        elif search_by == SearchBy.title:
            # Fuzzy search in user's dialogs
            channels = []
            async for dialog in client.iter_dialogs():
                entity = dialog.entity
                
                # Skip system dialog (Telegram Service Notifications)
                is_system_dialog = dialog.id == 777000
                # Only include channels and chats/groups
                is_channel_or_chat = isinstance(entity, Channel) or isinstance(entity, Chat)
                
                if is_system_dialog or not is_channel_or_chat:
                    continue

                score = fuzzy_match_score(dialog.title, query)
                if score >= title_threshold:
                    channels.append(entity_to_channel_info(dialog.entity))
            
            # Sort by match score (best matches first)
            channels.sort(
                key=lambda c: fuzzy_match_score(c.title, query),
                reverse=True
            )
            return channels
        
        return []
    
    finally:
        # Clean up client connection
        if client.is_connected():
            await client.disconnect()

