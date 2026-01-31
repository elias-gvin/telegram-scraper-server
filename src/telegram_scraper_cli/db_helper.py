from __future__ import annotations

import sqlite3
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence, Tuple
from datetime import datetime, timezone


@dataclass(frozen=True)
class ChannelDbPaths:
    channel_dir: Path
    db_file: Path
    media_dir: Path


class SchemaMismatchError(RuntimeError):
    """Raised when an existing SQLite schema does not match what the app expects."""


_DESIRED_COLS: Tuple[str, ...] = (
    "id",
    "channel_id",
    "message_id",
    "date",
    "sender_id",
    "first_name",
    "last_name",
    "username",
    "message",
    "media_type",
    "media_path",
    "reply_to",
    "post_author",
    "is_forwarded",
    "forwarded_from_channel_id",
)

_DESIRED_COLUMNS_SQL = (
    "id INTEGER PRIMARY KEY, "
    "channel_id TEXT NOT NULL, "
    "message_id INTEGER UNIQUE, "
    "date TEXT, "
    "sender_id INTEGER, "
    "first_name TEXT, "
    "last_name TEXT, "
    "username TEXT, "
    "message TEXT, "
    "media_type TEXT, "
    "media_path TEXT, "
    "reply_to INTEGER, "
    "post_author TEXT, "
    "is_forwarded INTEGER, "
    "forwarded_from_channel_id INTEGER, "
    "FOREIGN KEY(channel_id) REFERENCES channels(channel_id)"
)

_CHANNELS_COLS: Tuple[str, ...] = ("channel_id", "channel_name", "user")

_ALLOWED_MESSAGES_ORDER_BY: frozenset[str] = frozenset({"date", "message_id", "id"})


def channel_db_paths(output_dir: Path, channel_id: str | int) -> ChannelDbPaths:
    """
    Compute canonical on-disk paths for a channel's DB and media.

    Layout:
      <output_dir>/<channel_id>/<channel_id>.db
      <output_dir>/<channel_id>/media/
    """
    output_dir = Path(output_dir)
    channel_dir = output_dir / str(channel_id)
    db_file = channel_dir / f"{channel_id}.db"
    media_dir = channel_dir / "media"
    return ChannelDbPaths(channel_dir=channel_dir, db_file=db_file, media_dir=media_dir)


def open_channel_db(
    *,
    output_dir: Path,
    channel_id: str | int,
    check_same_thread: bool = False,
) -> sqlite3.Connection:
    """
    Open (and create if needed) the channel SQLite database connection.

    Note: returns a raw sqlite3.Connection; callers own lifecycle (close()).
    """
    paths = channel_db_paths(output_dir, channel_id)
    paths.channel_dir.mkdir(parents=True, exist_ok=True)
    # media_dir creation is optional; scraper may create lazily
    conn = sqlite3.connect(str(paths.db_file), check_same_thread=check_same_thread)
    return conn


def open_db_file(db_file: Path, *, row_factory: bool = True) -> sqlite3.Connection:
    """
    Open an existing SQLite DB file (generic helper).

    This is intentionally separate from `open_channel_db()` because it doesn't assume
    any on-disk layout and is useful for tooling like exports.
    """
    conn = sqlite3.connect(str(db_file))
    if row_factory:
        conn.row_factory = sqlite3.Row
    # Keep behavior consistent with the rest of the app.
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def configure_connection(conn: sqlite3.Connection) -> None:
    """Apply SQLite pragmas for better ingest performance and concurrency."""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")


def ensure_messages_schema(
    conn: sqlite3.Connection, *, create_missing_tables: bool = True
) -> None:
    """
    Ensure `messages` table exists with the desired schema.

    If the table exists but schema differs, raise SchemaMismatchError.
    """
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='messages'")
    table_exists = cur.fetchone() is not None

    if not table_exists:
        if not create_missing_tables:
            raise SchemaMismatchError(
                "Missing required table 'messages' (refusing to create tables in validate-only mode)."
            )
        conn.execute(f"CREATE TABLE IF NOT EXISTS messages ({_DESIRED_COLUMNS_SQL})")
    else:
        cur.execute("PRAGMA table_info(messages)")
        existing_cols = [row[1] for row in cur.fetchall()]

        if set(existing_cols) != set(_DESIRED_COLS):
            existing_set = set(existing_cols)
            desired_set = set(_DESIRED_COLS)
            missing = sorted(desired_set - existing_set)
            extra = sorted(existing_set - desired_set)

            raise SchemaMismatchError(
                "Existing SQLite schema for table 'messages' does not match expected schema. "
                f"missing={missing or []}, extra={extra or []}. "
                "Refusing to auto-migrate (no tables were modified)."
            )

    conn.execute("CREATE INDEX IF NOT EXISTS idx_message_id ON messages(message_id)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_channel_id ON messages(channel_id)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_date ON messages(date)")
    configure_connection(conn)
    conn.commit()


def ensure_metadata_schema(
    conn: sqlite3.Connection, *, create_missing_tables: bool = True
) -> None:
    """
    Ensure metadata tables exist:
      - channels

    If a table exists but schema differs, raise SchemaMismatchError.
    """
    cur = conn.cursor()

    # channels
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='channels'")
    channels_exists = cur.fetchone() is not None
    if not channels_exists:
        if not create_missing_tables:
            raise SchemaMismatchError(
                "Missing required table 'channels' (refusing to create tables in validate-only mode)."
            )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS channels (
              channel_id TEXT PRIMARY KEY,
              channel_name TEXT,
              user TEXT
            )
            """
        )
    else:
        cur.execute("PRAGMA table_info(channels)")
        existing = [row[1] for row in cur.fetchall()]
        if set(existing) != set(_CHANNELS_COLS):
            raise SchemaMismatchError(
                "Existing SQLite schema for table 'channels' does not match expected schema. "
                f"expected={list(_CHANNELS_COLS)}, got={existing}."
            )

    conn.commit()


def ensure_media_files_schema(
    conn: sqlite3.Connection, *, create_missing_tables: bool = True
) -> None:
    """
    Ensure media_files table exists for UUID → file path mapping.
    """
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='media_files'")
    table_exists = cur.fetchone() is not None
    
    if not table_exists:
        if not create_missing_tables:
            raise SchemaMismatchError(
                "Missing required table 'media_files' (refusing to create tables in validate-only mode)."
            )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS media_files (
              uuid TEXT PRIMARY KEY,
              message_id INTEGER UNIQUE NOT NULL,
              file_path TEXT NOT NULL,
              file_size INTEGER,
              mime_type TEXT,
              created_at TEXT NOT NULL,
              FOREIGN KEY(message_id) REFERENCES messages(message_id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_media_uuid ON media_files(uuid)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_media_message_id ON media_files(message_id)")
    
    conn.commit()


def ensure_schema(
    conn: sqlite3.Connection, *, create_missing_tables: bool = True
) -> None:
    """Ensure (or validate) all tables required by the app exist."""
    ensure_metadata_schema(conn, create_missing_tables=create_missing_tables)
    ensure_messages_schema(conn, create_missing_tables=create_missing_tables)
    ensure_media_files_schema(conn, create_missing_tables=create_missing_tables)


def _export_messages_to_csv(
    *,
    cursor: sqlite3.Cursor,
    output_file: Path,
    columns: Sequence[str],
    batch_size: int,
) -> int:
    row_count = 0
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(list(columns))
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            writer.writerows([tuple(row) for row in rows])
            row_count += len(rows)
    return row_count


def _export_messages_to_json(
    *,
    cursor: sqlite3.Cursor,
    output_file: Path,
    batch_size: int,
    json_indent: Optional[int],
) -> int:
    row_count = 0
    with open(output_file, "w", encoding="utf-8") as f:
        f.write("[\n")
        first = True
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            for row in rows:
                if not first:
                    f.write(",\n")
                else:
                    first = False
                # With `open_db_file(..., row_factory=True)`, rows are sqlite3.Row
                json.dump(dict(row), f, ensure_ascii=False, indent=json_indent)
                row_count += 1
        f.write("\n]\n")
    return row_count


def export_messages(
    conn: sqlite3.Connection,
    *,
    output_file: Path,
    export_format: str,
    order_by: str = "date",
    json_indent: Optional[int] = 2,
    batch_size: int = 1000,
    validate_only: bool = True,
) -> int:
    """
    Export `messages` table to a file.

    Args:
        conn: sqlite connection (recommended to be opened with row_factory=True for JSON export).
        output_file: where to write.
        export_format: "csv" or "json".
        order_by: one of {"date","message_id","id"} (allowlist to avoid SQL injection).
        validate_only: if True, refuses to create missing tables (export should fail fast).

    Returns:
        Row count written.
    """
    if export_format not in ("csv", "json"):
        raise ValueError("export_format must be 'csv' or 'json'")

    ensure_schema(conn, create_missing_tables=not validate_only)

    if order_by not in _ALLOWED_MESSAGES_ORDER_BY:
        raise ValueError(
            f"order_by must be one of {sorted(_ALLOWED_MESSAGES_ORDER_BY)}"
        )

    cursor = conn.cursor()
    cursor.execute(f"SELECT * FROM messages ORDER BY {order_by}")
    columns = [desc[0] for desc in cursor.description]

    if export_format == "csv":
        return _export_messages_to_csv(
            cursor=cursor,
            output_file=output_file,
            columns=columns,
            batch_size=batch_size,
        )

    # JSON export expects row-like objects to be convertible to dict()
    return _export_messages_to_json(
        cursor=cursor,
        output_file=output_file,
        batch_size=batch_size,
        json_indent=json_indent,
    )


def upsert_channel(
    conn: sqlite3.Connection,
    *,
    channel_id: str | int,
    channel_name: str,
    user: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO channels (channel_id, channel_name, user)
        VALUES (?, ?, ?)
        ON CONFLICT(channel_id) DO UPDATE SET
          channel_name=excluded.channel_name,
          user=excluded.user
        """,
        (str(channel_id), channel_name, user),
    )
    conn.commit()


def batch_upsert_messages(
    conn: sqlite3.Connection,
    messages: Sequence[object],
    *,
    channel_id: str | int,
    replace_existing: bool,
) -> None:
    """
    Insert or update messages in the database.
    
    Args:
        conn: Database connection
        messages: Sequence of message objects with attributes matching MessageData
        channel_id: Channel ID these messages belong to
        replace_existing: If True, update existing messages; if False, skip duplicates
    """
    if not messages:
        return

    data = [
        (
            str(channel_id),
            int(getattr(msg, "message_id")),
            str(getattr(msg, "date")),
            int(getattr(msg, "sender_id")),
            getattr(msg, "first_name", None),
            getattr(msg, "last_name", None),
            getattr(msg, "username", None),
            str(getattr(msg, "message")),
            getattr(msg, "media_type", None),
            getattr(msg, "media_path", None),
            getattr(msg, "reply_to", None),
            getattr(msg, "post_author", None),
            int(getattr(msg, "is_forwarded")),
            getattr(msg, "forwarded_from_channel_id", None),
        )
        for msg in messages
    ]

    if replace_existing:
        conn.executemany(
            """
            INSERT INTO messages
              (channel_id, message_id, date, sender_id, first_name, last_name, username,
               message, media_type, media_path, reply_to, post_author,
               is_forwarded, forwarded_from_channel_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(message_id) DO UPDATE SET
              channel_id=excluded.channel_id,
              date=excluded.date,
              sender_id=excluded.sender_id,
              first_name=excluded.first_name,
              last_name=excluded.last_name,
              username=excluded.username,
              message=excluded.message,
              media_type=excluded.media_type,
              media_path=excluded.media_path,
              reply_to=excluded.reply_to,
              post_author=excluded.post_author,
              is_forwarded=excluded.is_forwarded,
              forwarded_from_channel_id=excluded.forwarded_from_channel_id
            """,
            data,
        )
    else:
        conn.executemany(
            """
            INSERT OR IGNORE INTO messages
              (channel_id, message_id, date, sender_id, first_name, last_name, username,
               message, media_type, media_path, reply_to, post_author,
               is_forwarded, forwarded_from_channel_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            data,
        )

    conn.commit()


def set_message_media_path(
    conn: sqlite3.Connection, *, message_id: int, media_path: str
) -> None:
    conn.execute(
        "UPDATE messages SET media_path = ? WHERE message_id = ?",
        (media_path, message_id),
    )
    conn.commit()


def check_db_connection(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("SELECT 1")
        return True
    except (sqlite3.ProgrammingError, sqlite3.OperationalError):
        return False


# ============================================================================
# Media UUID Management
# ============================================================================

import uuid as uuid_lib
import mimetypes


def generate_media_uuid() -> str:
    """Generate UUID for media file."""
    return str(uuid_lib.uuid4())


def store_media_with_uuid(
    conn: sqlite3.Connection,
    message_id: int,
    file_path: str,
    file_size: Optional[int] = None,
    mime_type: Optional[str] = None,
) -> str:
    """
    Store media file info with UUID and return the UUID.
    
    Args:
        conn: Database connection
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
    
    conn.execute(
        """
        INSERT INTO media_files (uuid, message_id, file_path, file_size, mime_type, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(message_id) DO UPDATE SET
            uuid=excluded.uuid,
            file_path=excluded.file_path,
            file_size=excluded.file_size,
            mime_type=excluded.mime_type
        """,
        (media_uuid, message_id, file_path, file_size, mime_type, datetime.now(timezone.utc).isoformat())
    )
    conn.commit()
    
    return media_uuid


def get_media_uuid_by_message_id(
    conn: sqlite3.Connection,
    message_id: int
) -> Optional[str]:
    """Get media UUID for a message."""
    cursor = conn.execute(
        "SELECT uuid FROM media_files WHERE message_id = ?",
        (message_id,)
    )
    row = cursor.fetchone()
    return row[0] if row else None


def get_media_info_by_uuid(
    conn: sqlite3.Connection,
    media_uuid: str
) -> Optional[dict]:
    """
    Get media file info by UUID.
    
    Returns:
        Dict with keys: uuid, message_id, file_path, file_size, mime_type, created_at
        or None if not found
    """
    cursor = conn.execute(
        "SELECT uuid, message_id, file_path, file_size, mime_type, created_at FROM media_files WHERE uuid = ?",
        (media_uuid,)
    )
    row = cursor.fetchone()
    if row:
        return {
            "uuid": row[0],
            "message_id": row[1],
            "file_path": row[2],
            "file_size": row[3],
            "mime_type": row[4],
            "created_at": row[5],
        }
    return None


def get_cached_date_range(
    conn: sqlite3.Connection,
    channel_id: str | int
) -> Optional[Tuple[datetime, datetime]]:
    """
    Get the date range of cached messages for a channel.
    
    Returns:
        Tuple of (min_date, max_date) or None if no messages
    """
    cursor = conn.execute(
        "SELECT MIN(date), MAX(date) FROM messages WHERE channel_id = ?",
        (str(channel_id),)
    )
    row = cursor.fetchone()
    if row and row[0] and row[1]:
        min_date = datetime.fromisoformat(row[0])
        max_date = datetime.fromisoformat(row[1])
        return (min_date, max_date)
    return None


def iter_messages_in_range(
    conn: sqlite3.Connection,
    channel_id: str | int,
    start_date: datetime,
    end_date: datetime,
    batch_size: int = 100
):
    """
    Iterate over messages in a date range in batches.
    
    Yields batches of sqlite3.Row objects.
    """
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        """
        SELECT * FROM messages
        WHERE channel_id = ? AND date >= ? AND date <= ?
        ORDER BY date ASC
        """,
        (str(channel_id), start_date.isoformat(), end_date.isoformat())
    )
    
    while True:
        batch = cursor.fetchmany(batch_size)
        if not batch:
            break
        yield batch
