"""Media download utilities for Telegram messages."""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Literal

from telethon.tl.types import (
    MessageMediaPhoto,
    MessageMediaDocument,
    MessageMediaWebPage,
)
from telethon.errors import FloodWaitError


@dataclass(frozen=True)
class MediaMetadata:
    """Telegram media metadata extracted without downloading."""

    media_type: str  # e.g. "MessageMediaPhoto"
    file_size: int  # Telegram-reported size in bytes
    original_filename: Optional[str] = None  # e.g. "doc.mp4"; None for photos


@dataclass(frozen=True)
class MediaDownloadResult:
    """Result of media download operation."""

    status: Literal["downloaded", "skipped", "failed"]
    path: Optional[str] = None
    error_text: Optional[str] = None


def get_media_metadata(message) -> Optional[MediaMetadata]:
    """
    Extract media metadata from a Telegram message without downloading.

    Returns None if the message has no downloadable media.
    """
    if not message.media:
        return None
    if isinstance(message.media, MessageMediaWebPage):
        return None

    media_type = message.media.__class__.__name__

    # Get size from Telegram metadata
    msg_file = getattr(message, "file", None)
    size_bytes = getattr(msg_file, "size", None)
    if size_bytes is None:
        doc = getattr(getattr(message, "media", None), "document", None)
        size_bytes = getattr(doc, "size", None)

    # Photos and some media types don't carry a filename; documents usually do.
    if not isinstance(message.media, (MessageMediaPhoto, MessageMediaDocument)):
        return None

    original_filename = getattr(
        getattr(message, "file", None), "name", None
    )

    return MediaMetadata(
        media_type=media_type,
        file_size=size_bytes or 0,
        original_filename=original_filename,  # None for photos
    )


async def download_media(
    message,  # Telethon Message object
    output_dir: Path,
    channel_id: int,
    max_media_size_mb: Optional[float] = None,
    force_redownload: bool = False,
) -> MediaDownloadResult:
    """
    Download media from a Telegram message.

    Args:
        message: Telethon Message object
        output_dir: Base output directory
        channel_id: Channel ID for organizing media
        max_media_size_mb: Optional size limit in MB (None = no limit)

    Returns:
        MediaDownloadResult with status and path
    """
    if not message.media:
        return MediaDownloadResult(status="skipped")

    if isinstance(message.media, MessageMediaWebPage):
        return MediaDownloadResult(status="skipped")

    try:
        # Determine media folder path
        db_dir = (
            output_dir
            if output_dir.name == str(channel_id)
            else (output_dir / str(channel_id))
        )

        media_folder = db_dir / "media"
        media_folder.mkdir(parents=True, exist_ok=True)

        # Check if file already exists
        existing_files = list(media_folder.glob(f"{message.id}-*"))
        if existing_files and not force_redownload:
            return MediaDownloadResult(status="downloaded", path=str(existing_files[0]))
        elif existing_files and force_redownload:
            # Remove old files before re-downloading
            for f in existing_files:
                f.unlink(missing_ok=True)

        # Check media size limit
        if max_media_size_mb is not None:
            try:
                max_bytes = int(float(max_media_size_mb) * 1024 * 1024)
            except (TypeError, ValueError):
                max_bytes = None

            if max_bytes is not None and max_bytes >= 0:
                msg_file = getattr(message, "file", None)
                size_bytes = getattr(msg_file, "size", None)
                if size_bytes is None:
                    # Fallback for some media types
                    doc = getattr(getattr(message, "media", None), "document", None)
                    size_bytes = getattr(doc, "size", None)

                if isinstance(size_bytes, int) and size_bytes > max_bytes:
                    return MediaDownloadResult(
                        status="skipped",
                        error_text=f"skipped_by_size_limit bytes={size_bytes} max_bytes={max_bytes}",
                    )

        # Determine filename
        if isinstance(message.media, MessageMediaPhoto):
            original_name = getattr(message.file, "name", None) or "photo.jpg"
            ext = "jpg"
        elif isinstance(message.media, MessageMediaDocument):
            ext = getattr(message.file, "ext", "bin") if message.file else "bin"
            original_name = getattr(message.file, "name", None) or f"document.{ext}"
        else:
            return MediaDownloadResult(status="skipped")

        base_name = Path(original_name).stem
        extension = Path(original_name).suffix or f".{ext}"
        unique_filename = f"{message.id}-{base_name}{extension}"
        media_path = media_folder / unique_filename

        # Download with retries
        for attempt in range(3):
            try:
                downloaded_path = await message.download_media(file=str(media_path))
                if downloaded_path and Path(downloaded_path).exists():
                    return MediaDownloadResult(
                        status="downloaded", path=downloaded_path
                    )
                else:
                    return MediaDownloadResult(
                        status="failed", error_text="download_returned_empty_path"
                    )
            except FloodWaitError as e:
                if attempt < 2:
                    await asyncio.sleep(e.seconds)
                else:
                    return MediaDownloadResult(
                        status="failed",
                        error_text=f"FloodWaitError seconds={e.seconds}",
                    )
            except Exception as e:
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
                else:
                    return MediaDownloadResult(
                        status="failed",
                        error_text=f"{type(e).__name__}: {e}",
                    )

        return MediaDownloadResult(
            status="failed", error_text="download_exhausted_retries"
        )
    except Exception as e:
        return MediaDownloadResult(
            status="failed", error_text=f"{type(e).__name__}: {e}"
        )
