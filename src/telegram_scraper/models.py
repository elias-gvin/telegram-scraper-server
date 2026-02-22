"""Data models for Telegram Scraper."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Literal


# See https://core.telegram.org/constructor/message for more information about the fields.
@dataclass
class MessageData:
    """Message data model - matches Telegram API + our extensions."""

    # Core message identity
    message_id: int
    dialog_id: int

    # Timestamps
    date: str  # YYYY-MM-DD HH:MM:SS

    # Sender (may be orphaned)
    sender_id: int

    # Content
    message: str

    # Context
    is_forwarded: int

    # Extended (not from Telegram API)
    dialog_name: Optional[str] = None

    # Timestamps (optional)
    edit_date: Optional[str] = None  # YYYY-MM-DD HH:MM:SS or None

    # Sender (optional)
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    username: Optional[str] = None

    # Context (optional)
    reply_to: Optional[int] = None
    post_author: Optional[str] = None
    forwarded_from_channel_id: Optional[int] = None

    # Media (metadata always filled when media exists; path only when downloaded)
    media_type: Optional[str] = None  # MediaCategory value: "photos", "videos", etc.
    media_uuid: Optional[str] = None  # Generated UUID
    media_original_filename: Optional[str] = (
        None  # Original filename from Telegram (None for photos)
    )
    media_path: Optional[str] = None  # Local file path (None if not downloaded)
    media_size: Optional[int] = None  # Telegram-reported size in bytes


@dataclass
class DateRange:
    """Date range with start and end."""

    start: datetime
    end: datetime


@dataclass
class TimelineSegment:
    """Timeline segment marking cache or download region."""

    start: datetime
    end: datetime
    source: Literal["cache", "telegram"]
