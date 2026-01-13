"""Tool for searching Telegram channels and groups by name."""

import logging
from typing import List, Dict, Optional, Any
from telethon import TelegramClient
from telethon.tl.types import Channel, Chat

logger = logging.getLogger(__name__)


async def search_channels(
    client: TelegramClient,
    search_query: str,
    limit: Optional[int] = None
) -> List[Dict[str, Any]]:
    """
    Search for channels and groups by name.
    
    Args:
        client: Authorized TelegramClient instance
        search_query: Search query (channel/group name or username)
        limit: Maximum number of results to return (None for all)
        
    Returns:
        List of dictionaries containing channel/group information:
        {
            'title': str,
            'id': str,
            'username': Optional[str],
            'type': str,  # 'Channel' or 'Group'
            'participants_count': Optional[int]
        }
    """
    if not client.is_connected():
        raise ConnectionError("Telegram client is not connected")
    
    if not await client.is_user_authorized():
        raise ConnectionError("Telegram client is not authorized")
    
    results = []
    search_lower = search_query.lower()
    
    logger.info(f"Searching for channels/groups matching '{search_query}'...")
    
    try:
        async for dialog in client.iter_dialogs(limit=limit):
            entity = dialog.entity
            
            # Skip system dialogs and non-channel/chat entities
            if dialog.id == 777000 or (not isinstance(entity, Channel) and not isinstance(entity, Chat)):
                continue
            
            # Check if matches search query
            title = dialog.title or ""
            username = getattr(entity, 'username', None) or ""
            
            if (search_lower in title.lower() or 
                search_lower in username.lower() or
                search_query in str(dialog.id)):
                
                channel_type = "Channel" if isinstance(entity, Channel) and entity.broadcast else "Group"
                participants_count = getattr(entity, 'participants_count', None)
                
                result = {
                    'title': title,
                    'id': str(dialog.id),
                    'username': username if username else None,
                    'type': channel_type,
                    'participants_count': participants_count
                }
                results.append(result)
                
                logger.info(f"Found: {title} (ID: {dialog.id}, Type: {channel_type}, Username: @{username})")
        
        logger.info(f"✅ Found {len(results)} matching channels/groups")
        return results
        
    except Exception as e:
        logger.error(f"Error searching channels: {e}")
        raise


async def list_all_channels(
    client: TelegramClient,
    limit: Optional[int] = None
) -> List[Dict[str, Any]]:
    """
    List all channels and groups the user has access to.
    
    Args:
        client: Authorized TelegramClient instance
        limit: Maximum number of results to return (None for all)
        
    Returns:
        List of dictionaries containing channel/group information
    """
    if not client.is_connected():
        raise ConnectionError("Telegram client is not connected")
    
    if not await client.is_user_authorized():
        raise ConnectionError("Telegram client is not authorized")
    
    results = []
    
    logger.info("Listing all channels and groups...")
    
    try:
        async for dialog in client.iter_dialogs(limit=limit):
            entity = dialog.entity
            
            # Skip system dialogs and non-channel/chat entities
            if dialog.id == 777000 or (not isinstance(entity, Channel) and not isinstance(entity, Chat)):
                continue
            
            channel_type = "Channel" if isinstance(entity, Channel) and entity.broadcast else "Group"
            username = getattr(entity, 'username', None) or ""
            participants_count = getattr(entity, 'participants_count', None)
            
            result = {
                'title': dialog.title or "",
                'id': str(dialog.id),
                'username': username if username else None,
                'type': channel_type,
                'participants_count': participants_count
            }
            results.append(result)
        
        logger.info(f"✅ Found {len(results)} channels/groups")
        return results
        
    except Exception as e:
        logger.error(f"Error listing channels: {e}")
        raise

