"""Data models for Telegram Scraper."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


# msg_data = MessageData(
#     message_id=message.id,
#     date=message.date.strftime('%Y-%m-%d %H:%M:%S'),
#     sender_id=message.sender_id,
#     first_name=getattr(sender, 'first_name', None) if isinstance(sender, User) else None,
#     last_name=getattr(sender, 'last_name', None) if isinstance(sender, User) else None,
#     username=getattr(sender, 'username', None) if isinstance(sender, User) else None,
#     message=message.message or '',
#     media_type=message.media.__class__.__name__ if message.media else None,
#     media_path=None,
#     reply_to=message.reply_to_msg_id if message.reply_to else None,
#     post_author=message.post_author,
#     views=message.views,
#     forwards=message.forwards,
#     reactions=reactions_str
# )


# See https://core.telegram.org/constructor/message for more information about the fields.
@dataclass
class MessageData:
    """Message data model - matches Telegram API + our extensions."""

    # Core message identity
    message_id: int
    channel_id: int

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
    media_filename: Optional[str]  # Original filename
    media_size: Optional[int]  # Bytes

    # Extended (not from Telegram API)
    channel_name: Optional[str] = None  # Filled from entity


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
    source: str  # "cache" or "telegram"
