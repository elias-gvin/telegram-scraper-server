"""Data models for Telegram Scraper."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class MessageData:
    """Message data model."""
    message_id: int
    date: str
    sender_id: int
    first_name: Optional[str]
    last_name: Optional[str]
    username: Optional[str]
    message: str
    media_type: Optional[str]
    media_path: Optional[str]
    reply_to: Optional[int]
    post_author: Optional[str]
    is_forwarded: int
    forwarded_from_channel_id: Optional[int]
    # Extended fields (set dynamically)
    media_uuid: Optional[str] = None
    media_size: Optional[int] = None


@dataclass
class DateRange:
    """Date range with start and end."""
    start: "datetime"  # type: ignore
    end: "datetime"  # type: ignore


@dataclass
class TimelineSegment:
    """Timeline segment marking cache or download region."""
    start: "datetime"  # type: ignore
    end: "datetime"  # type: ignore
    source: str  # "cache" or "telegram"

