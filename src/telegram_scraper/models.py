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
    channel_id: int

    # Extended (not from Telegram API)
    channel_name: Optional[str] = None

    # Timestamps
    date: str  # YYYY-MM-DD HH:MM:SS
    edit_date: Optional[str]  # YYYY-MM-DD HH:MM:SS or None

    # Sender (may be orphaned)
    sender_id: int
    first_name: Optional[str]
    last_name: Optional[str]
    username: Optional[str]

    # Content
    message: str

    # Context
    reply_to: Optional[int]
    post_author: Optional[str]
    is_forwarded: int
    forwarded_from_channel_id: Optional[int]

    # Media (filled after download)
    media_type: Optional[str]  # Telegram class name
    media_uuid: Optional[str]  # Generated UUID
    media_path: Optional[str]  # Local file path
    media_size: Optional[int]  # Bytes



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
