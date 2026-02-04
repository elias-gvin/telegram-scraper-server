"""Database operations using SQLModel ORM."""

from __future__ import annotations

import uuid as uuid_lib
import mimetypes
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Sequence, List, Tuple
from sqlmodel import Session, select

from .models import Channel, User, Message, MediaFile


def upsert_channel(
    session: Session,
    *,
    channel_id: str | int,
    channel_name: str,
    creator_id: int | None = None,
) -> Channel:
    """
    Insert or update channel information.

    Args:
        session: SQLModel session
        channel_id: Channel ID
        channel_name: Channel name/title
        creator_id: Telegram user ID of channel creator/owner (optional)

    Returns:
        Channel object
    """
    channel = session.get(Channel, str(channel_id))

    if channel:
        # Update existing
        channel.channel_name = channel_name
        if creator_id is not None:
            channel.creator_id = creator_id
    else:
        # Create new
        channel = Channel(
            channel_id=str(channel_id),
            channel_name=channel_name,
            creator_id=creator_id,
        )
        session.add(channel)

    session.commit()
    session.refresh(channel)
    return channel


def upsert_user(
    session: Session,
    *,
    user_id: int,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    username: Optional[str] = None,
    auto_commit: bool = True,
) -> User:
    """
    Insert or update user information.

    Args:
        session: SQLModel session
        user_id: Telegram user ID
        first_name: User's first name
        last_name: User's last name
        username: User's username
        auto_commit: If True, commit after upsert; if False, caller manages transaction

    Returns:
        User object
    """
    user = session.get(User, user_id)

    if user:
        # Update existing
        user.first_name = first_name
        user.last_name = last_name
        user.username = username
    else:
        # Create new
        user = User(
            user_id=user_id,
            first_name=first_name,
            last_name=last_name,
            username=username,
        )
        session.add(user)

    if auto_commit:
        session.commit()
        session.refresh(user)
    return user


def batch_upsert_messages(
    session: Session,
    messages: Sequence[object],
    *,
    channel_id: str | int,
    replace_existing: bool = True,
    auto_commit: bool = True,
) -> None:
    """
    Insert or update messages in the database.
    Also upserts user information for each message sender.

    Args:
        session: SQLModel session
        messages: Sequence of message objects with MessageData attributes
        channel_id: Channel ID these messages belong to
        replace_existing: If True, update existing messages; if False, skip duplicates
        auto_commit: If True, commit after batch insert; if False, caller manages transaction
    """
    if not messages:
        return

    for msg in messages:
        sender_id = int(getattr(msg, "sender_id"))

        # Upsert user first (to satisfy foreign key), without committing
        upsert_user(
            session,
            user_id=sender_id,
            first_name=getattr(msg, "first_name", None),
            last_name=getattr(msg, "last_name", None),
            username=getattr(msg, "username", None),
            auto_commit=False,
        )

        # Check if message exists
        existing = session.exec(
            select(Message).where(Message.message_id == int(getattr(msg, "message_id")))
        ).first()

        if existing:
            if replace_existing:
                # Update existing message
                existing.channel_id = str(channel_id)
                existing.date = str(getattr(msg, "date"))
                existing.sender_id = sender_id
                existing.message = str(getattr(msg, "message"))
                existing.reply_to = getattr(msg, "reply_to", None)
                existing.post_author = getattr(msg, "post_author", None)
                existing.is_forwarded = int(getattr(msg, "is_forwarded"))
                existing.forwarded_from_channel_id = getattr(
                    msg, "forwarded_from_channel_id", None
                )
        else:
            # Insert new message
            new_message = Message(
                channel_id=str(channel_id),
                message_id=int(getattr(msg, "message_id")),
                date=str(getattr(msg, "date")),
                sender_id=sender_id,
                message=str(getattr(msg, "message")),
                reply_to=getattr(msg, "reply_to", None),
                post_author=getattr(msg, "post_author", None),
                is_forwarded=int(getattr(msg, "is_forwarded")),
                forwarded_from_channel_id=getattr(
                    msg, "forwarded_from_channel_id", None
                ),
            )
            session.add(new_message)

    if auto_commit:
        session.commit()


# Removed set_message_media_path - media info now stored in MediaFile table only


def generate_media_uuid() -> str:
    """Generate UUID for media file."""
    return str(uuid_lib.uuid4())


def store_media_with_uuid(
    session: Session,
    message_id: int,
    file_path: str,
    file_size: Optional[int] = None,
    mime_type: Optional[str] = None,
) -> str:
    """
    Store media file info with UUID and update the message's media_uuid reference.

    Args:
        session: SQLModel session
        message_id: Message ID this media belongs to
        file_path: File system path to media file
        file_size: File size in bytes (optional)
        mime_type: MIME type (optional, will be guessed if not provided)

    Returns:
        UUID string
    """
    media_uuid = generate_media_uuid()

    # Guess MIME type if not provided
    if mime_type is None:
        mime_type, _ = mimetypes.guess_type(file_path)

    # Get file size if not provided
    if file_size is None:
        try:
            file_size = Path(file_path).stat().st_size
        except Exception:
            file_size = None

    # Check if media file already exists for this message
    existing = session.exec(
        select(MediaFile).where(MediaFile.message_id == message_id)
    ).first()

    if existing:
        # Update existing
        existing.uuid = media_uuid
        existing.file_path = file_path
        existing.file_size = file_size
        existing.mime_type = mime_type
    else:
        # Create new
        media_file = MediaFile(
            uuid=media_uuid,
            message_id=message_id,
            file_path=file_path,
            file_size=file_size,
            mime_type=mime_type,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        session.add(media_file)

    # Update the message's media_uuid reference
    message = session.exec(
        select(Message).where(Message.message_id == message_id)
    ).first()
    if message:
        message.media_uuid = media_uuid

    session.commit()
    return media_uuid


def get_media_uuid_by_message_id(session: Session, message_id: int) -> Optional[str]:
    """Get media UUID for a message by querying the message directly."""
    message = session.exec(
        select(Message).where(Message.message_id == message_id)
    ).first()

    return message.media_uuid if message else None


def get_media_info_by_uuid(session: Session, media_uuid: str) -> Optional[dict]:
    """
    Get media file info by UUID.

    Returns:
        Dict with keys: uuid, message_id, file_path, file_size, mime_type, created_at
        or None if not found
    """
    media_file = session.get(MediaFile, media_uuid)

    if media_file:
        return {
            "uuid": media_file.uuid,
            "message_id": media_file.message_id,
            "file_path": media_file.file_path,
            "file_size": media_file.file_size,
            "mime_type": media_file.mime_type,
            "created_at": media_file.created_at,
        }
    return None


def get_cached_date_range(
    session: Session, channel_id: str | int
) -> Optional[Tuple[datetime, datetime]]:
    """
    Get the date range of cached messages for a channel.

    Returns:
        Tuple of (min_date, max_date) or None if no messages
    """
    # Get min and max dates using raw SQL for efficiency
    from sqlalchemy import func, select as sa_select

    result = session.exec(
        sa_select(func.min(Message.date), func.max(Message.date)).where(
            Message.channel_id == str(channel_id)
        )
    ).first()

    if result and result[0] and result[1]:
        min_date = datetime.fromisoformat(result[0])
        max_date = datetime.fromisoformat(result[1])
        return (min_date, max_date)
    return None


def iter_messages_in_range(
    session: Session,
    channel_id: str | int,
    start_date: datetime,
    end_date: datetime,
    batch_size: int = 100,
):
    """
    Iterate over messages in a date range in batches.
    Joins with User table to include sender information.

    Yields batches of Message objects converted to dicts.
    """
    offset = 0
    while True:
        # Join Message with User to get sender info
        statement = (
            select(Message, User)
            .join(User, Message.sender_id == User.user_id, isouter=True)
            .where(
                Message.channel_id == str(channel_id),
                Message.date >= start_date.isoformat(),
                Message.date <= end_date.isoformat(),
            )
            .order_by(Message.date)
            .offset(offset)
            .limit(batch_size)
        )

        results = session.exec(statement).all()

        if not results:
            break

        # Convert to dicts for compatibility with existing code
        # Note: media_type/media_path retrieved separately via MediaFile relationship
        batch = [
            {
                "id": msg.id,
                "channel_id": msg.channel_id,
                "message_id": msg.message_id,
                "date": msg.date,
                "sender_id": msg.sender_id,
                "first_name": user.first_name if user else None,
                "last_name": user.last_name if user else None,
                "username": user.username if user else None,
                "message": msg.message,
                "reply_to": msg.reply_to,
                "post_author": msg.post_author,
                "is_forwarded": msg.is_forwarded,
                "forwarded_from_channel_id": msg.forwarded_from_channel_id,
            }
            for msg, user in results
        ]

        yield batch
        offset += batch_size


def check_db_connection(session: Session) -> bool:
    """Test if database connection is working."""
    try:
        session.exec(select(1))
        return True
    except Exception:
        return False
