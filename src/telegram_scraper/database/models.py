"""SQLModel database models for Telegram Scraper."""

from sqlmodel import SQLModel, Field, UniqueConstraint
from typing import Optional


# NO relationships, just raw IDs stored as integers/strings
# This allows orphaned references and simpler data model


class Channel(SQLModel, table=True):
    """Channel metadata - standalone, no relationships."""

    __tablename__ = "channels"

    channel_id: int = Field(primary_key=True)
    channel_name: str
    channel_username: Optional[str] = None  # @channelname
    creator_id: Optional[int] = None  # Just an int, no FK


class User(SQLModel, table=True):
    """User/sender info - standalone, no relationships."""

    __tablename__ = "users"

    user_id: int = Field(primary_key=True)
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    username: Optional[str] = None


class MediaFile(SQLModel, table=True):
    """Media file storage - standalone, no relationships."""

    __tablename__ = "media_files"

    uuid: str = Field(primary_key=True)  # UUID v4

    # Message reference (no FK)
    channel_id: int = Field(index=True)
    message_id: int = Field(index=True)

    # File info
    file_path: str
    file_name: str
    file_size: int  # Bytes
    mime_type: Optional[str] = None
    media_type: str  # Telegram type: MessageMediaPhoto, MessageMediaDocument, etc.


class Message(SQLModel, table=True):
    """Message data - no FKs except media_uuid."""

    __tablename__ = "messages"
    # Note: message_id is not unique across channels, so we need to use a unique constraint to ensure that each message_id is unique within a channel.
    __table_args__ = (
        UniqueConstraint("channel_id", "message_id", name="uq_channel_message"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)  # Auto-increment

    # Channel context (no FK)
    channel_id: int = Field(index=True)

    # Message identity
    message_id: int = Field(index=True)

    # Timestamps
    date: str = Field(index=True)  # ISO format YYYY-MM-DD HH:MM:SS
    edit_date: Optional[str] = None  # ISO format, NULL if never edited

    # Sender info (no FK)
    sender_id: int = Field(index=True)

    # Message content
    message: str  # Text content (empty string if no text)

    # Reply context (no FK)
    reply_to: Optional[int] = Field(
        default=None, index=True
    )  # message_id of replied-to message (in same channel)

    # Author/forward info
    post_author: Optional[str] = None
    is_forwarded: int  # 0 or 1
    forwarded_from_channel_id: Optional[int] = None  # No FK

    # Media reference (EXCEPTION: has FK since we always create MediaFile entry)
    media_uuid: Optional[str] = Field(
        default=None, foreign_key="media_files.uuid", index=True
    )
