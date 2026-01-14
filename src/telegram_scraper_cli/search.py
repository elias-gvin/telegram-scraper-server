"""Tool for searching Telegram channels and groups by name."""

import logging
import os
from typing import List, Dict, Optional, Any
from telethon import TelegramClient
from telethon.tl.types import Channel, Chat
import asyncio
from dotenv import load_dotenv
from rapidfuzz import fuzz

from .auth import authorize_telegram_client

logger = logging.getLogger(__name__)


async def search_channels(
    client: TelegramClient,
    search_query: str,
    limit: Optional[int] = None,
    min_similarity: int = 50,
    use_fuzzy: bool = True
) -> List[Dict[str, Any]]:
    """
    Search for channels and groups by name using fuzzy matching.
    
    Args:
        client: Authorized TelegramClient instance
        search_query: Search query (channel/group name or username)
        limit: Maximum number of results to return (None for all, applies to dialog iteration)
        min_similarity: Minimum similarity score (0-100) for fuzzy matching (default: 50)
        use_fuzzy: Whether to use fuzzy matching (default: True). If False, uses exact substring matching.
        
    Returns:
        List of dictionaries containing channel/group information, sorted by similarity score (highest first):
        {
            'title': str,
            'id': str,
            'username': Optional[str],
            'type': str,  # 'Channel' or 'Group'
            'participants_count': Optional[int],
            'similarity_score': float  # Similarity score (0-100)
        }
    """
    if not client.is_connected():
        raise ConnectionError("Telegram client is not connected")
    
    if not await client.is_user_authorized():
        raise ConnectionError("Telegram client is not authorized")
    
    results = []
    search_lower = search_query.lower()
    
    logger.info(f"Searching for channels/groups matching '{search_query}' (fuzzy: {use_fuzzy}, min_similarity: {min_similarity})...")
    
    try:
        async for dialog in client.iter_dialogs(limit=limit):
            entity = dialog.entity
            
            # Skip system dialogs and non-channel/chat entities
            if dialog.id == 777000 or (not isinstance(entity, Channel) and not isinstance(entity, Chat)):
                continue
            
            # Get channel/group information
            title = dialog.title or ""
            username = getattr(entity, 'username', None) or ""
            channel_type = "Channel" if isinstance(entity, Channel) and entity.broadcast else "Group"
            participants_count = getattr(entity, 'participants_count', None)
            
            # Calculate similarity scores
            similarity_score = 0.0
            matched = False
            
            if use_fuzzy:
                # Calculate fuzzy match scores for title and username
                title_score = fuzz.partial_ratio(search_query.lower(), title.lower())
                username_score = fuzz.partial_ratio(search_query.lower(), username.lower()) if username else 0
                
                # Use the highest score between title and username
                similarity_score = max(title_score, username_score)
                
                # Also check exact ID match for bonus
                if search_query in str(dialog.id):
                    similarity_score = max(similarity_score, 100.0)
                
                # Include if similarity meets threshold
                if similarity_score >= min_similarity:
                    matched = True
            else:
                # Exact substring matching (original behavior)
                if (search_lower in title.lower() or 
                    search_lower in username.lower() or
                    search_query in str(dialog.id)):
                    matched = True
                    # Calculate a simple similarity for exact matches
                    if search_lower == title.lower() or search_lower == username.lower():
                        similarity_score = 100.0
                    else:
                        similarity_score = 80.0
            
            if matched:
                result = {
                    'title': title,
                    'id': str(dialog.id),
                    'username': username if username else None,
                    'type': channel_type,
                    'participants_count': participants_count,
                    'similarity_score': round(similarity_score, 2)
                }
                results.append(result)
                
                logger.info(f"Found: {title} (ID: {dialog.id}, Type: {channel_type}, Similarity: {similarity_score:.2f}%)")
        
        # Sort by similarity score (highest first)
        results.sort(key=lambda x: x['similarity_score'], reverse=True)
        
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
    limit = 100 # TODO: add ability to specify limit from command line.
    results = await list_all_channels(client, limit=limit)
    
    # Print results
    if not results:
        print("No results found")
    else:
        print(f"\nFound {len(results)} result(s):\n")
        for i, result in enumerate(results, 1):
            print(f"    Title: {result['title']}")
            print(f"    ID: {result['id']}")
            print(f"    Type: {result['type']}")
            if result['username']:
                print(f"    Username: @{result['username']}")
            if result['participants_count']:
                print(f"    Participants: {result['participants_count']}")
            print()

    result = await search_channels(client, "SAVED INFO (REGULAR)", limit=limit)
    if not result:
        print("No results found")
    else:
        print(f"\nFound {len(result)} result(s):\n")
        for i, result in enumerate(result, 1):
            print(f"    Title: {result['title']}")
            print(f"    ID: {result['id']}")
            print(f"    Type: {result['type']}")
            if result['username']:
                print(f"    Username: @{result['username']}")
            if result['participants_count']:
                print(f"    Participants: {result['participants_count']}")
            if 'similarity_score' in result:
                print(f"    Similarity: {result['similarity_score']}%")
            print()

if __name__ == "__main__":
    asyncio.run(main())