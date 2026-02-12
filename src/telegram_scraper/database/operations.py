"""Database operations using SQLModel ORM."""

from __future__ import annotations

import uuid as uuid_lib
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Sequence, List, Tuple
from sqlmodel import Session, select

from .models import Dialog, User, Message, MediaFile


def upsert_dialog(
    session: Session,
    *,
    dialog_id: str | int,
    name: str,
    username: str | None = None,
) -> Dialog:
    """
    Insert or update dialog information.

    Args:
        session: SQLModel session
        dialog_id: Dialog ID
        name: Dialog name/title
        username: Dialog username (e.g., @username)

    Returns:
        Dialog object
    """
    dialog = session.get(Dialog, int(dialog_id))

    if dialog:
        # Update existing
        dialog.name = name
        if username is not None:
            dialog.username = username
    else:
        # Create new
        dialog = Dialog(
            dialog_id=int(dialog_id),
            name=name,
            username=username,
        )
        session.add(dialog)

    session.commit()
    session.refresh(dialog)
    return dialog


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
    dialog_id: str | int,
    replace_existing: bool = True,
    auto_commit: bool = True,
) -> None:
    """
    Insert or update messages in the database.
    Also upserts user information for each message sender.

    Args:
        session: SQLModel session
        messages: Sequence of message objects with MessageData attributes
        dialog_id: Dialog ID these messages belong to
        replace_existing: If True, update existing messages; if False, skip duplicates
        auto_commit: If True, commit after batch insert; if False, caller manages transaction
    """
    if not messages:
        return

    for msg in messages:
        sender_id = int(getattr(msg, "sender_id"))

        # Upsert user first (no FK, but keep user table updated)
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
            select(Message).where(
                Message.dialog_id == int(dialog_id),
                Message.message_id == int(getattr(msg, "message_id")),
            )
        ).first()

        if existing:
            if replace_existing:
                # Update existing message
                existing.dialog_id = int(dialog_id)
                existing.date = str(getattr(msg, "date"))
                existing.edit_date = getattr(msg, "edit_date", None)
                existing.sender_id = sender_id
                existing.message = str(getattr(msg, "message"))
                existing.reply_to = getattr(msg, "reply_to", None)
                existing.post_author = getattr(msg, "post_author", None)
                existing.is_forwarded = int(getattr(msg, "is_forwarded"))
                existing.forwarded_from_channel_id = getattr(
                    msg, "forwarded_from_channel_id", None
                )
                # media_uuid updated separately by store_media_with_uuid
        else:
            # Insert new message
            new_message = Message(
                dialog_id=int(dialog_id),
                message_id=int(getattr(msg, "message_id")),
                date=str(getattr(msg, "date")),
                edit_date=getattr(msg, "edit_date", None),
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
    dialog_id: int,
    message_id: int,
    file_size: int,
    media_type: str,
    original_filename: Optional[str] = None,
    file_path: Optional[str] = None,
) -> str:
    """
    Store media metadata with UUID and link it to the message.

    A MediaFile record is **always** created when a message has media,
    even if the file was not downloaded.  ``file_path`` is ``None`` when
    the download was skipped (e.g. size limit, download_media=false).

    Args:
        session: SQLModel session
        dialog_id: Dialog ID (used to find the message)
        message_id: Message ID (used to find the message)
        file_size: Telegram-reported size in bytes
        media_type: Telegram media type (e.g., 'MessageMediaPhoto')
        original_filename: Original filename from Telegram (None for photos)
        file_path: Full file path on disk, or None if not downloaded

    Returns:
        UUID string
    """
    # Check if a MediaFile is already linked to this message
    message = session.exec(
        select(Message).where(
            Message.dialog_id == dialog_id, Message.message_id == message_id
        )
    ).first()

    existing_uuid = message.media_uuid if message else None

    if existing_uuid:
        # Update the existing MediaFile entry
        existing = session.get(MediaFile, existing_uuid)
        if existing:
            existing.original_filename = original_filename
            existing.file_size = file_size
            existing.media_type = media_type or "unknown"
            if file_path is not None:
                existing.file_path = file_path
            session.commit()
            return existing_uuid

    # Create new MediaFile entry
    media_uuid = generate_media_uuid()

    media_file = MediaFile(
        uuid=media_uuid,
        file_size=file_size,
        media_type=media_type or "unknown",
        original_filename=original_filename,
        file_path=file_path,
    )
    session.add(media_file)

    # Update message's media_uuid (FK reference)
    if message:
        message.media_uuid = media_uuid

    session.commit()
    return media_uuid


def update_media_file_path(session: Session, media_uuid: str, file_path: str) -> None:
    """
    Set file_path on an existing MediaFile (used by repair_media).

    Also updates file_size to the actual on-disk size.
    """
    media_file = session.get(MediaFile, media_uuid)
    if media_file:
        media_file.file_path = file_path
        try:
            media_file.file_size = Path(file_path).stat().st_size
        except Exception:
            pass
        session.commit()


def get_media_uuid_by_message_id(
    session: Session, dialog_id: int, message_id: int
) -> Optional[str]:
    """Get media UUID for a message by querying the message directly."""
    message = session.exec(
        select(Message).where(
            Message.dialog_id == dialog_id, Message.message_id == message_id
        )
    ).first()

    return message.media_uuid if message else None


def get_media_info_by_uuid(session: Session, media_uuid: str) -> Optional[dict]:
    """
    Get media file info by UUID.

    Returns:
        Dict with keys: uuid, file_path, file_size, media_type
        or None if not found
    """
    media_file = session.get(MediaFile, media_uuid)

    if media_file:
        return {
            "uuid": media_file.uuid,
            "original_filename": media_file.original_filename,
            "file_path": media_file.file_path,
            "file_size": media_file.file_size,
            "media_type": media_file.media_type,
        }
    return None


def get_cached_date_range(
    session: Session, dialog_id: str | int
) -> Optional[Tuple[datetime, datetime]]:
    """
    Get the date range of cached messages for a dialog.

    Returns:
        Tuple of (min_date, max_date) or None if no messages
    """
    # Get min and max dates using raw SQL for efficiency
    from sqlalchemy import func, select as sa_select

    result = session.exec(
        sa_select(func.min(Message.date), func.max(Message.date)).where(
            Message.dialog_id == int(dialog_id)
        )
    ).first()

    if result and result[0] and result[1]:
        min_date = datetime.fromisoformat(result[0])
        max_date = datetime.fromisoformat(result[1])
        return (min_date, max_date)
    return None


def iter_messages_in_range(
    session: Session,
    dialog_id: str | int,
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
                Message.dialog_id == int(dialog_id),
                Message.date >= start_date.strftime("%Y-%m-%d %H:%M:%S"),
                Message.date <= end_date.strftime("%Y-%m-%d %H:%M:%S"),
            )
            .order_by(Message.date)
            .offset(offset)
            .limit(batch_size)
        )

        results = session.exec(statement).all()

        if not results:
            break

        # Convert to dicts for compatibility with existing code
        # Note: full media info retrieved separately via MediaFile relationship
        batch = [
            {
                "id": msg.id,
                "dialog_id": msg.dialog_id,
                "message_id": msg.message_id,
                "date": msg.date,
                "edit_date": msg.edit_date,
                "sender_id": msg.sender_id,
                "first_name": user.first_name if user else None,
                "last_name": user.last_name if user else None,
                "username": user.username if user else None,
                "message": msg.message,
                "reply_to": msg.reply_to,
                "post_author": msg.post_author,
                "is_forwarded": msg.is_forwarded,
                "forwarded_from_channel_id": msg.forwarded_from_channel_id,
                "media_uuid": msg.media_uuid,
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
