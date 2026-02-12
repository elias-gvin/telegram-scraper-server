"""SQLModel database models for Telegram Scraper."""

from sqlmodel import SQLModel, Field, UniqueConstraint
from typing import Optional


# NO relationships, just raw IDs stored as integers/strings
# This allows orphaned references and simpler data model


class Dialog(SQLModel, table=True):
    """Dialog metadata - standalone, no relationships."""

    __tablename__ = "dialogs"

    dialog_id: int = Field(primary_key=True)
    name: str
    username: Optional[str] = None  # @username


class User(SQLModel, table=True):
    """User/sender info - standalone, no relationships."""

    __tablename__ = "users"

    user_id: int = Field(primary_key=True)
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    username: Optional[str] = None


class MediaFile(SQLModel, table=True):
    """Media file metadata — always created when a message has media.

    file_path is NULL when the media was not downloaded (e.g. skipped by
    size limit or download_media=false).  All other fields are always
    populated from Telegram metadata so we can make repair decisions
    without hitting the Telegram API.
    """

    __tablename__ = "media_files"

    uuid: str = Field(primary_key=True)  # UUID v4

    # Always populated (from Telegram metadata, no download needed)
    file_size: int  # Telegram-reported size in bytes
    media_type: str  # Telegram type: MessageMediaPhoto, MessageMediaDocument, etc.

    # Original filename from Telegram (NULL for media types that don't carry one, e.g. photos)
    original_filename: Optional[str] = None

    # Only populated when the file is actually downloaded
    file_path: Optional[str] = None  # NULL = not downloaded yet


class Message(SQLModel, table=True):
    """Message data - no FKs except media_uuid."""

    __tablename__ = "messages"
    # Note: message_id is not unique across dialogs, so we need to use a unique constraint to ensure that each message_id is unique within a dialog.
    __table_args__ = (
        UniqueConstraint("dialog_id", "message_id", name="uq_dialog_message"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)  # Auto-increment

    # Dialog context (no FK)
    dialog_id: int = Field(index=True)

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
    )  # message_id of replied-to message (in same dialog)

    # Author/forward info
    post_author: Optional[str] = None
    is_forwarded: int  # 0 or 1
    forwarded_from_channel_id: Optional[int] = None  # No FK

    # Media reference (EXCEPTION: has FK since we always create MediaFile entry)
    media_uuid: Optional[str] = Field(
        default=None, foreign_key="media_files.uuid", index=True
    )
