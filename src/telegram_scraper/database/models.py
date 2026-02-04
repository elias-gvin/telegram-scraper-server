"""SQLModel database models for Telegram Scraper."""

from sqlmodel import SQLModel, Field, Relationship, UniqueConstraint
from typing import Optional


class Channel(SQLModel, table=True):
    """Channel metadata table."""

    __tablename__ = "channels"

    channel_id: str = Field(primary_key=True)
    channel_name: Optional[str] = None
    creator_id: Optional[int] = None  # No foreign key - allows orphaned creator IDs

    # Relationships
    messages: list["Message"] = Relationship(back_populates="channel")
    creator: Optional["User"] = Relationship(
        sa_relationship_kwargs={
            "foreign_keys": "[Channel.creator_id]",
            "primaryjoin": "User.user_id == Channel.creator_id",
        }
    )


class User(SQLModel, table=True):
    """User data table."""

    __tablename__ = "users"

    user_id: int = Field(primary_key=True)
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    username: Optional[str] = None

    # Relationships
    messages: list["Message"] = Relationship(back_populates="sender")


class MediaFile(SQLModel, table=True):
    """Media files UUID mapping table."""

    __tablename__ = "media_files"

    uuid: str = Field(primary_key=True)
    message_id: int = Field(unique=True, index=True)  # For reverse lookup
    file_path: str
    file_size: Optional[int] = None
    media_type: Optional[str] = (
        None  # Telegram media type (e.g., 'MessageMediaPhoto', 'MessageMediaDocument')
    )
    created_at: str

    # Relationships
    message: Optional["Message"] = Relationship(
        back_populates="media_file",
        sa_relationship_kwargs={
            "foreign_keys": "[Message.media_uuid]",
            "uselist": False,
        },
    )


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


class Message(SQLModel, table=True):
    """Message data table."""

    __tablename__ = "messages"
    # Note: message_id is not unique across channels, so we need to use a unique constraint to ensure that each message_id is unique within a channel.
    __table_args__ = (
        UniqueConstraint("channel_id", "message_id", name="uq_channel_message"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    channel_id: str = Field(index=True)  # No foreign key - allows orphaned channel IDs
    message_id: int = Field(index=True)
    date: str = Field(index=True)
    sender_id: int = Field(index=True)  # No foreign key - allows orphaned sender IDs
    message: str
    media_uuid: Optional[str] = Field(default=None, foreign_key="media_files.uuid")
    reply_to: Optional[int] = Field(
        default=None, index=True
    )  # message_id of replied-to message (in same channel)
    post_author: Optional[str] = None
    is_forwarded: int
    forwarded_from_channel_id: Optional[int] = (
        None  # TODO: should we add a link to the channel table?
    )

    # Relationships
    channel: Optional[Channel] = Relationship(
        back_populates="messages",
        sa_relationship_kwargs={
            "foreign_keys": "[Message.channel_id]",
            "primaryjoin": "Channel.channel_id == Message.channel_id",
        },
    )
    sender: Optional[User] = Relationship(
        back_populates="messages",
        sa_relationship_kwargs={
            "foreign_keys": "[Message.sender_id]",
            "primaryjoin": "User.user_id == Message.sender_id",
        },
    )
    media_file: Optional[MediaFile] = Relationship(
        back_populates="message",
        sa_relationship_kwargs={
            "foreign_keys": "[Message.media_uuid]",
            "uselist": False,
        },
    )
    # Self-referential: the message this message replies to
    reply_to_message: Optional["Message"] = Relationship(
        sa_relationship_kwargs={
            "foreign_keys": "[Message.reply_to]",
            "primaryjoin": "and_(Message.channel_id == remote(Message.channel_id), Message.reply_to == remote(Message.message_id))",
            "uselist": False,
        }
    )
