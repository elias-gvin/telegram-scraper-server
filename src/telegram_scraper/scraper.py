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
    PeerUser,
)
from sqlmodel import Session

from .database import operations
from .models import MessageData, DateRange, TimelineSegment, SyncStats
from .media_downloader import download_media, get_media_metadata
from .config import RuntimeSettings


logger = logging.getLogger(__name__)


def transform_message_to_response(msg_dict: dict) -> dict:
    """
    Transform message dict to API response format.

    Removes internal database fields and keeps all user-facing fields flat.
    """
    # Remove internal database fields that shouldn't be in API response
    msg_dict.pop("id", None)
    msg_dict.pop("dialog_id", None)
    msg_dict.pop("media_path", None)

    # Keep media fields flat (media_type, media_uuid, media_size, media_original_filename)
    # No further transformation needed - just return the cleaned dict

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
    dialog_id: int,
    start_date: datetime,
    end_date: datetime,
    batch_size: int,
    settings: RuntimeSettings,
    output_dir: Path,
    force_redownload: bool = False,
    reverse: bool = True,
) -> AsyncIterator[List[MessageData]]:
    """
    Download messages from Telegram in batches and save to DB.

    Yields batches of MessageData.
    """
    batch = []
    entity = await client.get_entity(dialog_id)

    # Get dialog name from entity
    dialog_name = getattr(entity, "title", None)

    # Make dates timezone-aware
    if start_date.tzinfo is None:
        start_date = start_date.replace(tzinfo=timezone.utc)
    if end_date.tzinfo is None:
        end_date = end_date.replace(tzinfo=timezone.utc)

    if reverse:
        iter_kwargs = {"offset_date": start_date, "reverse": True}
    else:
        iter_kwargs = {"offset_date": end_date, "reverse": False}

    # Cache resolved forward-entity info to avoid redundant get_entity() calls
    fwd_entity_cache: dict[tuple[str, int], dict] = {}

    async for message in client.iter_messages(entity, **iter_kwargs):
        if reverse and message.date > end_date:
            break
        if not reverse and message.date < start_date:
            break

        try:
            sender = await message.get_sender()

            # Handle forwarded messages
            fwd_from = getattr(message, "fwd_from", None)
            is_forwarded = 1 if fwd_from else 0
            forwarded_from_channel_id = None
            forwarded_from_user_id = None
            forwarded_from_name = None
            forwarded_from_date = None
            fwd_first_name = None
            fwd_last_name = None
            fwd_username = None
            fwd_channel_name = None
            fwd_channel_username = None

            if fwd_from:
                if getattr(fwd_from, "date", None):
                    forwarded_from_date = fwd_from.date.strftime("%Y-%m-%d %H:%M:%S")
                peer = getattr(fwd_from, "from_id", None) or getattr(
                    fwd_from, "saved_from_peer", None
                )
                if isinstance(peer, PeerUser):
                    forwarded_from_user_id = peer.user_id
                    key = ("user", peer.user_id)
                    if key not in fwd_entity_cache:
                        try:
                            fwd_entity = await client.get_entity(peer)
                            if isinstance(fwd_entity, User):
                                fn = getattr(fwd_entity, "first_name", None)
                                ln = getattr(fwd_entity, "last_name", None)
                                un = getattr(fwd_entity, "username", None)
                                name = (
                                    " ".join(
                                        p for p in (fn or "", ln or "") if p
                                    ).strip()
                                    or None
                                )
                                fwd_entity_cache[key] = {
                                    "fwd_first_name": fn,
                                    "fwd_last_name": ln,
                                    "fwd_username": un,
                                    "forwarded_from_name": name,
                                }
                            else:
                                fwd_entity_cache[key] = {}
                        except Exception as e:
                            logger.debug(
                                "Could not resolve forward user %s: %s",
                                peer.user_id,
                                e,
                            )
                            fwd_entity_cache[key] = {}
                    cached = fwd_entity_cache.get(key, {})
                    fwd_first_name = cached.get("fwd_first_name")
                    fwd_last_name = cached.get("fwd_last_name")
                    fwd_username = cached.get("fwd_username")
                    forwarded_from_name = cached.get("forwarded_from_name")
                elif isinstance(peer, PeerChannel):
                    forwarded_from_channel_id = peer.channel_id
                    key = ("channel", peer.channel_id)
                    if key not in fwd_entity_cache:
                        try:
                            fwd_entity = await client.get_entity(peer)
                            title = getattr(fwd_entity, "title", None)
                            un = getattr(fwd_entity, "username", None)
                            fwd_entity_cache[key] = {
                                "fwd_channel_name": title,
                                "fwd_channel_username": un,
                                "forwarded_from_name": title,
                            }
                        except Exception as e:
                            logger.debug(
                                "Could not resolve forward channel %s: %s",
                                peer.channel_id,
                                e,
                            )
                            fwd_entity_cache[key] = {}
                    cached = fwd_entity_cache.get(key, {})
                    fwd_channel_name = cached.get("fwd_channel_name")
                    fwd_channel_username = cached.get("fwd_channel_username")
                    forwarded_from_name = cached.get("forwarded_from_name")
                if not forwarded_from_name:
                    forwarded_from_name = getattr(fwd_from, "from_name", None)

            # Always collect media metadata (even if we don't download)
            media_type = None
            media_path = None
            media_size = None
            media_original_filename = None

            metadata = get_media_metadata(message)
            if metadata:
                media_type = metadata.media_type
                media_size = metadata.file_size
                media_original_filename = metadata.original_filename

                # Actually download only if settings allow
                if settings.download_media:
                    result = await download_media(
                        message,
                        output_dir=output_dir,
                        dialog_id=dialog_id,
                        force_redownload=force_redownload,
                        settings=settings,
                    )
                    if result.status == "downloaded" and result.path:
                        media_path = result.path
                        try:
                            media_size = Path(media_path).stat().st_size
                        except Exception:
                            pass

            msg_data = MessageData(
                message_id=message.id,
                dialog_id=dialog_id,
                dialog_name=dialog_name,
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
                media_original_filename=media_original_filename,
                media_path=media_path,
                media_size=media_size,
                media_uuid=None,  # Will be set after insertion
                reply_to=message.reply_to_msg_id if message.reply_to else None,
                reply_quote_text=getattr(message.reply_to, "quote_text", None)
                if message.reply_to
                else None,
                reply_quote_offset=getattr(message.reply_to, "quote_offset", None)
                if message.reply_to
                else None,
                post_author=message.post_author,
                is_forwarded=is_forwarded,
                forwarded_from_channel_id=forwarded_from_channel_id,
                forwarded_from_user_id=forwarded_from_user_id,
                forwarded_from_name=forwarded_from_name,
                forwarded_from_date=forwarded_from_date,
                fwd_first_name=fwd_first_name,
                fwd_last_name=fwd_last_name,
                fwd_username=fwd_username,
                fwd_channel_name=fwd_channel_name,
                fwd_channel_username=fwd_channel_username,
            )

            batch.append(msg_data)

            # Yield when batch is full
            if len(batch) >= batch_size:
                # Save batch to DB atomically
                try:
                    # First insert messages
                    _batch_insert_messages(session, batch, dialog_id)
                    # Then store media metadata (requires messages to exist due to FK)
                    # Always create MediaFile when media exists, even if not downloaded
                    for msg in batch:
                        if msg.media_type:
                            msg.media_uuid = operations.store_media_with_uuid(
                                session,
                                dialog_id=dialog_id,
                                message_id=msg.message_id,
                                file_size=msg.media_size or 0,
                                media_type=msg.media_type,
                                original_filename=msg.media_original_filename,
                                file_path=msg.media_path,  # None if not downloaded
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
            _batch_insert_messages(session, batch, dialog_id)
            # Then store media metadata (requires messages to exist due to FK)
            for msg in batch:
                if msg.media_type:
                    msg.media_uuid = operations.store_media_with_uuid(
                        session,
                        dialog_id=dialog_id,
                        message_id=msg.message_id,
                        file_size=msg.media_size or 0,
                        media_type=msg.media_type,
                        original_filename=msg.media_original_filename,
                        file_path=msg.media_path,  # None if not downloaded
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
    dialog_id: str | int,
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
        dialog_id=dialog_id,
        replace_existing=True,
        auto_commit=False,
    )
    # NO COMMIT - caller manages transaction


def compute_segments(
    session: Session,
    dialog_id: int,
    start_date: datetime,
    end_date: datetime,
    force_refresh: bool,
) -> List[TimelineSegment]:
    """
    Build cache vs telegram timeline segments for the requested date range.

    Segments are in chronological order (same as build_timeline output).
    Callers that need newest-first iteration (reverse=False) should reverse
    the list themselves, matching stream_messages_with_cache.
    """
    if force_refresh:
        return [TimelineSegment(start_date, end_date, "telegram")]

    cached_range_tuple = operations.get_cached_date_range(session, dialog_id)
    if cached_range_tuple:
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
    return build_timeline(covered, gaps)


async def stream_messages_with_cache(
    client: TelegramClient,
    session: Session,
    dialog_id: int,
    start_date: datetime,
    end_date: datetime,
    settings: RuntimeSettings,
    client_batch_size: int,
    force_refresh: bool,
    output_dir: Path,
    reverse: bool = True,
) -> AsyncIterator[List[dict]]:
    """
    Stream messages with cache awareness.

    When *settings.repair_media* is ``True`` and a cached message has media
    metadata but no file on disk, the media is re-downloaded if the current
    settings now allow it.

    Yields batches of message dictionaries ready for API response.
    """
    client_buffer = []

    segments = compute_segments(session, dialog_id, start_date, end_date, force_refresh)

    # When reverse=False (newest-first), process segments from end_date toward
    # start_date so that messages stream in descending order.
    if not reverse:
        segments.reverse()

    # Pre-compute max bytes for repair comparison
    if settings.max_media_size_mb is not None:
        try:
            _repair_max_bytes = int(float(settings.max_media_size_mb) * 1024 * 1024)
        except (TypeError, ValueError):
            _repair_max_bytes = None
    else:
        _repair_max_bytes = None  # no limit

    retrieve_messages_batch_size = settings.telegram_batch_size
    # Stream through timeline
    for segment in segments:
        if segment.source == "cache":
            # Read from cache
            for batch in operations.iter_messages_in_range(
                session,
                dialog_id,
                segment.start,
                segment.end,
                batch_size=retrieve_messages_batch_size,
                reverse=reverse,
            ):
                for row in batch:
                    # row is already a dict from operations
                    msg_dict = row

                    # Get media info from MediaFile table
                    media_uuid = msg_dict.pop("media_uuid", None)

                    if media_uuid:
                        media_info = operations.get_media_info_by_uuid(
                            session, media_uuid
                        )
                    else:
                        media_info = None

                    # --- Repair skipped media ---
                    if (
                        settings.repair_media
                        and settings.download_media
                        and media_info
                        and not media_info.get("file_path")
                    ):
                        # Media metadata exists but file was never downloaded.
                        # Check if current settings now allow it.
                        cached_size = media_info.get("file_size", 0)
                        should_download = (
                            _repair_max_bytes is None
                            or cached_size <= _repair_max_bytes
                        )

                        if should_download:
                            try:
                                tg_msg = await client.get_messages(
                                    dialog_id, ids=msg_dict["message_id"]
                                )
                                if (
                                    tg_msg
                                    and tg_msg.media
                                    and not isinstance(
                                        tg_msg.media, MessageMediaWebPage
                                    )
                                ):
                                    result = await download_media(
                                        tg_msg,
                                        output_dir=output_dir,
                                        dialog_id=dialog_id,
                                        settings=settings,
                                    )
                                    if result.status == "downloaded" and result.path:
                                        operations.update_media_file_path(
                                            session, media_uuid, result.path
                                        )
                                        # Refresh media_info so the response
                                        # reflects the newly downloaded file
                                        media_info = operations.get_media_info_by_uuid(
                                            session, media_uuid
                                        )
                                        logger.info(
                                            "Repaired media for message %d (uuid=%s)",
                                            msg_dict["message_id"],
                                            media_uuid,
                                        )
                            except Exception as e:
                                logger.warning(
                                    "Failed to repair media for message %d: %s",
                                    msg_dict["message_id"],
                                    e,
                                )

                    # Build media fields for response
                    if media_info:
                        msg_dict["media_type"] = media_info.get("media_type")
                        msg_dict["media_uuid"] = media_info.get("uuid")
                        msg_dict["media_size"] = media_info.get("file_size")
                        msg_dict["media_original_filename"] = media_info.get(
                            "original_filename"
                        )
                    else:
                        msg_dict["media_type"] = None
                        msg_dict["media_uuid"] = None
                        msg_dict["media_size"] = None
                        msg_dict["media_original_filename"] = None

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
                dialog_id,
                segment.start,
                segment.end,
                retrieve_messages_batch_size,
                settings,
                output_dir,
                force_redownload=force_refresh,
                reverse=reverse,
            ):
                # Convert MessageData to dict
                for msg in telegram_batch:
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
                        "reply_quote_text": msg.reply_quote_text,
                        "reply_quote_offset": msg.reply_quote_offset,
                        "post_author": msg.post_author,
                        "is_forwarded": msg.is_forwarded,
                        "forwarded_from_channel_id": msg.forwarded_from_channel_id,
                        "forwarded_from_user_id": msg.forwarded_from_user_id,
                        "forwarded_from_name": msg.forwarded_from_name,
                        "forwarded_from_date": msg.forwarded_from_date,
                        "media_type": msg.media_type,
                        "media_uuid": msg.media_uuid,
                        "media_original_filename": msg.media_original_filename,
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


async def sync_messages_to_cache(
    client: TelegramClient,
    session: Session,
    dialog_id: int,
    start_date: datetime,
    end_date: datetime,
    settings: RuntimeSettings,
    force_refresh: bool,
    output_dir: Path,
    reverse: bool = True,
) -> SyncStats:
    """
    Download only Telegram gap segments into the cache and return aggregate stats.

    Does not read or yield cached rows. Uses the same segment logic as
    stream_messages_with_cache (with matching *reverse* ordering).
    """
    messages_downloaded = 0
    messages_with_media = 0
    media_downloaded = 0
    media_skipped = 0

    segments = compute_segments(session, dialog_id, start_date, end_date, force_refresh)
    if not reverse:
        segments = list(segments)
        segments.reverse()

    retrieve_messages_batch_size = settings.telegram_batch_size
    for segment in segments:
        if segment.source != "telegram":
            continue
        async for telegram_batch in download_from_telegram_batched(
            client,
            session,
            dialog_id,
            segment.start,
            segment.end,
            retrieve_messages_batch_size,
            settings,
            output_dir,
            force_redownload=force_refresh,
            reverse=reverse,
        ):
            for msg in telegram_batch:
                messages_downloaded += 1
                if msg.media_type:
                    messages_with_media += 1
                    if msg.media_path:
                        media_downloaded += 1
                    else:
                        media_skipped += 1

    return SyncStats(
        messages_downloaded=messages_downloaded,
        messages_with_media=messages_with_media,
        media_downloaded=media_downloaded,
        media_skipped=media_skipped,
    )
