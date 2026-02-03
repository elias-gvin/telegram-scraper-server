"""SQLModel database models for Telegram Scraper."""

from sqlmodel import SQLModel, Field, Relationship
from typing import Optional


class Channel(SQLModel, table=True):
    """Channel metadata table."""
    
    __tablename__ = "channels"
    
    channel_id: str = Field(primary_key=True)
    channel_name: Optional[str] = None
    creator_id: Optional[int] = Field(default=None, foreign_key="users.user_id")
    
    # Relationships
    messages: list["Message"] = Relationship(back_populates="channel")
    creator: Optional["User"] = Relationship()


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
    mime_type: Optional[str] = None
    created_at: str
    
    # Relationships
    message: Optional["Message"] = Relationship(
        back_populates="media_file",
        sa_relationship_kwargs={"foreign_keys": "[Message.media_uuid]", "uselist": False}
    )


class Message(SQLModel, table=True):
    """Message data table."""
    
    __tablename__ = "messages"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    channel_id: str = Field(foreign_key="channels.channel_id", index=True)
    message_id: int = Field(unique=True, index=True)
    date: str = Field(index=True)
    sender_id: int = Field(foreign_key="users.user_id", index=True)
    message: str
    media_uuid: Optional[str] = Field(default=None, foreign_key="media_files.uuid")
    reply_to: Optional[int] = None
    post_author: Optional[str] = None
    is_forwarded: int
    forwarded_from_channel_id: Optional[int] = None
    
    # Relationships
    channel: Optional[Channel] = Relationship(back_populates="messages")
    sender: Optional[User] = Relationship(back_populates="messages")
    media_file: Optional[MediaFile] = Relationship(
        back_populates="message",
        sa_relationship_kwargs={"foreign_keys": "[Message.media_uuid]", "uselist": False}
    )
