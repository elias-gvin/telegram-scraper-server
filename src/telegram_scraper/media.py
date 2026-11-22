"""Media files: classification (from the stored raw message) and download."""

from __future__ import annotations

import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path

from .config import MediaCategory, RuntimeSettings


@dataclass(frozen=True)
class MediaInfo:
    """What we know about a downloadable media file without downloading it."""

    key: int  # Telegram photo/document id: changes when an edit replaces the file
    category: MediaCategory
    size: int
    filename: str | None = None
    mime_type: str | None = None
    duration: float | None = None


def media_info(media: dict | None) -> MediaInfo | None:
    """Classify `raw["media"]` (a Telethon to_dict()). None = no downloadable file."""
    if not media:
        return None
    if (
        media["_"] == "MessageMediaPhoto"
        and (media.get("photo") or {}).get("_") == "Photo"
    ):
        photo = media["photo"]
        sizes = [s.get("size") or max(s.get("sizes") or [0]) for s in photo["sizes"]]
        return MediaInfo(
            photo["id"],
            MediaCategory.PHOTOS,
            max(sizes, default=0),
            mime_type="image/jpeg",
        )
    if (
        media["_"] == "MessageMediaDocument"
        and (media.get("document") or {}).get("_") == "Document"
    ):
        doc = media["document"]
        attrs = {a["_"]: a for a in doc["attributes"]}
        video, audio = (
            attrs.get("DocumentAttributeVideo"),
            attrs.get("DocumentAttributeAudio"),
        )
        if "DocumentAttributeSticker" in attrs:
            category = MediaCategory.STICKERS
        elif "DocumentAttributeAnimated" in attrs:
            category = MediaCategory.GIFS
        elif audio and audio.get("voice"):
            category = MediaCategory.VOICE_MESSAGES
        elif video:
            category = (
                MediaCategory.VIDEO_MESSAGES
                if video.get("round_message")
                else MediaCategory.VIDEOS
            )
        elif audio:
            category = MediaCategory.AUDIO
        else:
            category = MediaCategory.FILES
        return MediaInfo(
            key=doc["id"],
            category=category,
            size=doc["size"],
            filename=attrs.get("DocumentAttributeFilename", {}).get("file_name"),
            mime_type=doc.get("mime_type"),
            duration=(video or audio or {}).get("duration"),
        )
    return None


def skip_reason(info: MediaInfo, settings: RuntimeSettings) -> str | None:
    """Why the current settings forbid downloading this file, or None if allowed."""
    if not settings.download_media:
        return "download_media_disabled"
    if not getattr(settings.download_file_types, info.category):
        return f"type_disabled:{info.category}"
    limit = settings.max_media_size_mb
    if limit and info.size > limit * 1024 * 1024:
        return f"too_large:{info.size}"
    return None


def file_name(message_id: int, info: MediaInfo) -> str:
    """`<message_id>-<name><ext>`; the Telegram file name is stripped of any directory parts."""
    name = Path(info.filename or info.category.rstrip("s")).name
    stem, ext = os.path.splitext(name)
    if not ext:
        ext = mimetypes.guess_extension(info.mime_type or "") or ".bin"
    return f"{message_id}-{stem}{ext}"


async def download(message, info: MediaInfo, directory: Path) -> Path:
    """Download into `directory` atomically: a failed download never leaves a broken file."""
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / file_name(message.id, info)
    partial = target.with_name(target.name + ".part")
    try:
        result = await message.download_media(file=str(partial))
        if not result or not Path(result).exists():
            raise RuntimeError("download returned no file")
        os.replace(result, target)
    finally:
        partial.unlink(missing_ok=True)
    return target
