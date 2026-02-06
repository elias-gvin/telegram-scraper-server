"""Cache-aware message scraper for Telegram."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import AsyncIterator, List, Optional
from pathlib import Path

from telethon import TelegramClient
from telethon.tl.types import (
    MessageMediaWebPage,
    User,
    PeerChannel,
)
from sqlmodel import Session

from .database import operations
from .models import MessageData, DateRange, TimelineSegment
from .media_downloader import download_media


logger = logging.getLogger(__name__)


def transform_message_to_response(msg_dict: dict) -> dict:
    """
    Transform message dict to API response format.

    Removes internal database fields and keeps all user-facing fields flat.
    """
    # Remove internal database fields that shouldn't be in API response
    msg_dict.pop("id", None)
    msg_dict.pop("channel_id", None)
    msg_dict.pop("media_path", None)

    # Keep media fields flat (media_type, media_uuid, media_size, media_filename)
    # No transformation needed - just return the cleaned dict

    return msg_dict


def merge_overlapping_ranges(ranges: List[DateRange]) -> List[DateRange]:
    """Merge overlapping or adjacent date ranges."""
    if not ranges:
        return []

    sorted_ranges = sorted(ranges, key=lambda r: r.start)
    merged = [sorted_ranges[0]]

    for current in sorted_ranges[1:]:
        last = merged[-1]
        # If current overlaps or is adjacent to last, merge them
        if current.start <= last.end:
            merged[-1] = DateRange(last.start, max(last.end, current.end))
        else:
            merged.append(current)

    return merged


def find_gaps(
    requested: DateRange, cached_range: Optional[DateRange]
) -> List[DateRange]:
    """
    Find gaps between requested range and cached range.

    Returns list of ranges that need to be downloaded.
    """
    if not cached_range:
        return [requested]

    gaps = []

    # Gap before cached range
    if requested.start < cached_range.start:
        gap_end = min(cached_range.start, requested.end)
        gaps.append(DateRange(requested.start, gap_end))

    # Gap after cached range
    if requested.end > cached_range.end:
        gap_start = max(cached_range.end, requested.start)
        gaps.append(DateRange(gap_start, requested.end))

    return gaps


def find_covered_range(
    requested: DateRange, cached_range: Optional[DateRange]
) -> Optional[DateRange]:
    """Find intersection of requested range and cached range."""
    if not cached_range:
        return None

    overlap_start = max(requested.start, cached_range.start)
    overlap_end = min(requested.end, cached_range.end)

    if overlap_start < overlap_end:
        return DateRange(overlap_start, overlap_end)

    return None


def build_timeline(
    covered: Optional[DateRange], gaps: List[DateRange]
) -> List[TimelineSegment]:
    """
    Build chronological timeline of cache vs telegram segments.
    """
    segments = []

    # Add gaps (download from Telegram)
    for gap in gaps:
        segments.append(TimelineSegment(gap.start, gap.end, "telegram"))

    # Add covered range (from cache)
    if covered:
        segments.append(TimelineSegment(covered.start, covered.end, "cache"))

    # Sort chronologically
    segments.sort(key=lambda s: s.start)

    return segments


async def download_from_telegram_batched(
    client: TelegramClient,
    session: Session,
    channel_id: int,
    start_date: datetime,
    end_date: datetime,
    batch_size: int,
    scrape_media: bool,
    max_media_size_mb: Optional[float],
    output_dir: Path,
    force_redownload: bool = False,
) -> AsyncIterator[List[MessageData]]:
    """
    Download messages from Telegram in batches and save to DB.

    Yields batches of MessageData.
    """
    batch = []
    entity = await client.get_entity(channel_id)

    # Get channel name from entity
    channel_name = getattr(entity, "title", None)

    # Make dates timezone-aware
    if start_date.tzinfo is None:
        start_date = start_date.replace(tzinfo=timezone.utc)
    if end_date.tzinfo is None:
        end_date = end_date.replace(tzinfo=timezone.utc)

    async for message in client.iter_messages(
        entity, offset_date=start_date, reverse=True
    ):
        if message.date > end_date:
            break

        try:
            sender = await message.get_sender()

            # Handle forwarded messages
            fwd_from = getattr(message, "fwd_from", None)
            is_forwarded = 1 if fwd_from else 0
            forwarded_from_channel_id = None
            if fwd_from:
                peer = getattr(fwd_from, "from_id", None) or getattr(
                    fwd_from, "saved_from_peer", None
                )
                if isinstance(peer, PeerChannel):
                    forwarded_from_channel_id = peer.channel_id
                else:
                    forwarded_from_channel_id = getattr(peer, "channel_id", None)

            # Get media info
            media_type = message.media.__class__.__name__ if message.media else None
            media_path = None
            media_size = None

            # Download media if requested
            if (
                scrape_media
                and message.media
                and not isinstance(message.media, MessageMediaWebPage)
            ):
                result = await download_media(
                    message,
                    output_dir=output_dir,
                    channel_id=channel_id,
                    max_media_size_mb=max_media_size_mb,
                    force_redownload=force_redownload,
                )
                if result.status == "downloaded" and result.path:
                    media_path = result.path
                    try:
                        media_size = Path(media_path).stat().st_size
                    except Exception:
                        media_size = None

            msg_data = MessageData(
                message_id=message.id,
                channel_id=channel_id,
                channel_name=channel_name,
                date=message.date.strftime("%Y-%m-%d %H:%M:%S"),
                edit_date=message.edit_date.strftime("%Y-%m-%d %H:%M:%S")
                if message.edit_date
                else None,
                sender_id=message.sender_id or 0,
                first_name=getattr(sender, "first_name", None)
                if isinstance(sender, User)
                else None,
                last_name=getattr(sender, "last_name", None)
                if isinstance(sender, User)
                else None,
                username=getattr(sender, "username", None)
                if isinstance(sender, User)
                else None,
                message=message.message or "",
                media_type=media_type,
                media_path=media_path,
                media_size=media_size,
                media_uuid=None,  # Will be set after insertion
                reply_to=message.reply_to_msg_id if message.reply_to else None,
                post_author=message.post_author,
                is_forwarded=is_forwarded,
                forwarded_from_channel_id=forwarded_from_channel_id,
            )

            batch.append(msg_data)

            # Yield when batch is full
            if len(batch) >= batch_size:
                # Save batch to DB atomically
                try:
                    # First insert messages
                    _batch_insert_messages(session, batch, channel_id)
                    # Then store media UUIDs (requires messages to exist due to FK)
                    for msg in batch:
                        if msg.media_path:
                            msg.media_uuid = operations.store_media_with_uuid(
                                session,
                                channel_id=channel_id,
                                message_id=msg.message_id,
                                file_path=msg.media_path,
                                file_size=msg.media_size,
                                media_type=msg.media_type,
                            )
                    session.commit()
                except Exception as e:
                    session.rollback()
                    logger.error(f"Failed to save batch: {e}")
                    raise

                yield batch
                batch = []

        except Exception as e:
            logger.error(f"Error processing message {message.id}: {e}")
            # Don't continue on error - fail the whole batch to maintain consistency
            raise

    # Yield remaining messages
    if batch:
        try:
            # First insert messages
            _batch_insert_messages(session, batch, channel_id)
            # Then store media UUIDs (requires messages to exist due to FK)
            for msg in batch:
                if msg.media_path:
                    msg.media_uuid = operations.store_media_with_uuid(
                        session,
                        channel_id=channel_id,
                        message_id=msg.message_id,
                        file_path=msg.media_path,
                        file_size=msg.media_size,
                        media_type=msg.media_type,
                    )
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to save final batch: {e}")
            raise

        yield batch


def _batch_insert_messages(
    session: Session,
    messages: List[MessageData],
    channel_id: str | int,
) -> None:
    """
    Insert messages without auto-committing (caller manages transaction).
    This is a helper for atomic batch operations.
    """
    if not messages:
        return

    # Use operations batch upsert without auto-commit
    operations.batch_upsert_messages(
        session,
        messages,
        channel_id=channel_id,
        replace_existing=True,
        auto_commit=False,
    )
    # NO COMMIT - caller manages transaction


async def stream_messages_with_cache(
    client: TelegramClient,
    session: Session,
    channel_id: int,
    start_date: datetime,
    end_date: datetime,
    telegram_batch_size: int,
    client_batch_size: int,
    force_refresh: bool,
    scrape_media: bool,
    max_media_size_mb: Optional[float],
    output_dir: Path,
) -> AsyncIterator[List[dict]]:
    """
    Stream messages with cache awareness.

    Yields batches of message dictionaries ready for API response.
    """
    client_buffer = []

    if force_refresh:
        # Download everything, ignore cache
        segments = [TimelineSegment(start_date, end_date, "telegram")]
    else:
        # Check cache and find gaps
        cached_range_tuple = operations.get_cached_date_range(session, channel_id)
        # Convert tuple to DateRange object (ensure timezone-aware)
        if cached_range_tuple:
            # Database dates might be timezone-naive, so add UTC timezone if needed
            cached_start = cached_range_tuple[0]
            cached_end = cached_range_tuple[1]
            if cached_start.tzinfo is None:
                cached_start = cached_start.replace(tzinfo=timezone.utc)
            if cached_end.tzinfo is None:
                cached_end = cached_end.replace(tzinfo=timezone.utc)
            cached_range = DateRange(cached_start, cached_end)
        else:
            cached_range = None

        requested = DateRange(start_date, end_date)

        gaps = find_gaps(requested, cached_range)
        covered = find_covered_range(requested, cached_range)
        segments = build_timeline(covered, gaps)

    # Stream through timeline
    for segment in segments:
        if segment.source == "cache":
            # Read from cache
            for batch in operations.iter_messages_in_range(
                session,
                channel_id,
                segment.start,
                segment.end,
                batch_size=telegram_batch_size,
            ):
                for row in batch:
                    # row is already a dict from operations
                    msg_dict = row

                    # Get media info from MediaFile table
                    media_uuid = operations.get_media_uuid_by_message_id(
                        session, channel_id, msg_dict["message_id"]
                    )

                    if media_uuid:
                        media_info = operations.get_media_info_by_uuid(
                            session, media_uuid
                        )
                        if media_info:
                            msg_dict["media_type"] = (
                                media_info.get("media_type") or "unknown"
                            )
                            msg_dict["media_uuid"] = media_info.get("uuid")
                            msg_dict["media_size"] = media_info.get("file_size")
                            file_path = media_info.get("file_path")
                            msg_dict["media_filename"] = (
                                Path(file_path).name if file_path else None
                            )
                        else:
                            msg_dict["media_type"] = None
                            msg_dict["media_uuid"] = None
                            msg_dict["media_size"] = None
                            msg_dict["media_filename"] = None
                    else:
                        msg_dict["media_type"] = None
                        msg_dict["media_uuid"] = None
                        msg_dict["media_size"] = None
                        msg_dict["media_filename"] = None

                    # Transform to nested response format
                    msg_dict = transform_message_to_response(msg_dict)
                    client_buffer.append(msg_dict)

                    # Yield when buffer full
                    while len(client_buffer) >= client_batch_size:
                        yield client_buffer[:client_batch_size]
                        client_buffer = client_buffer[client_batch_size:]

        else:  # segment.source == "telegram"
            # Download from Telegram
            async for telegram_batch in download_from_telegram_batched(
                client,
                session,
                channel_id,
                segment.start,
                segment.end,
                telegram_batch_size,
                scrape_media,
                max_media_size_mb,
                output_dir,
                force_redownload=force_refresh,
            ):
                # Convert MessageData to dict
                for msg in telegram_batch:
                    # Extract filename from media_path if present
                    media_filename = None
                    if msg.media_path:
                        media_filename = Path(msg.media_path).name

                    msg_dict = {
                        "message_id": msg.message_id,
                        "date": msg.date,
                        "edit_date": msg.edit_date,
                        "sender_id": msg.sender_id,
                        "first_name": msg.first_name,
                        "last_name": msg.last_name,
                        "username": msg.username,
                        "message": msg.message,
                        "reply_to": msg.reply_to,
                        "post_author": msg.post_author,
                        "is_forwarded": msg.is_forwarded,
                        "forwarded_from_channel_id": msg.forwarded_from_channel_id,
                        "media_type": msg.media_type,
                        "media_uuid": msg.media_uuid,
                        "media_filename": media_filename,
                        "media_size": msg.media_size,
                    }
                    # Transform to nested response format
                    msg_dict = transform_message_to_response(msg_dict)
                    client_buffer.append(msg_dict)

                # Yield when buffer full
                while len(client_buffer) >= client_batch_size:
                    yield client_buffer[:client_batch_size]
                    client_buffer = client_buffer[client_batch_size:]

    # Flush remainder
    if client_buffer:
        yield client_buffer
