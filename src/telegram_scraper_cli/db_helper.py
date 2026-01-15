
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence, Tuple
from astuple import astuple


@dataclass(frozen=True)
class ChannelDbPaths:
    channel_dir: Path
    db_file: Path
    media_dir: Path


_DESIRED_COLS: Tuple[str, ...] = (
    "id",
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
    "forwarded_from_channel_id INTEGER"
)


def channel_db_paths(output_dir: Path, channel_id: str) -> ChannelDbPaths:
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
    channel_id: str,
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


def configure_connection(conn: sqlite3.Connection) -> None:
    """Apply SQLite pragmas for better ingest performance and concurrency."""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")


def ensure_messages_schema(conn: sqlite3.Connection) -> None:
    """
    Ensure `messages` table exists with the desired schema.

    If schema changed, migrate by creating `messages_new`, copying available columns,
    and swapping tables (SQLite cannot DROP COLUMN).
    """
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='messages'")
    table_exists = cur.fetchone() is not None

    if not table_exists:
        conn.execute(f"CREATE TABLE IF NOT EXISTS messages ({_DESIRED_COLUMNS_SQL})")
    else:
        cur.execute("PRAGMA table_info(messages)")
        existing_cols = [row[1] for row in cur.fetchall()]

        if set(existing_cols) != set(_DESIRED_COLS):
            existing_set = set(existing_cols)
            insert_cols = [c for c in _DESIRED_COLS if c != "id"]

            def select_expr(col: str) -> str:
                if col in existing_set:
                    return col
                if col == "is_forwarded":
                    return "0 AS is_forwarded"
                if col == "forwarded_from_channel_id":
                    return "NULL AS forwarded_from_channel_id"
                if col == "message":
                    return "'' AS message"
                return f"NULL AS {col}"

            select_exprs = [select_expr(c) for c in insert_cols]

            # Atomic table swap.
            conn.execute("BEGIN")
            try:
                conn.execute(
                    f"CREATE TABLE IF NOT EXISTS messages_new ({_DESIRED_COLUMNS_SQL})"
                )
                conn.execute(
                    f"INSERT INTO messages_new ({', '.join(insert_cols)}) "
                    f"SELECT {', '.join(select_exprs)} FROM messages"
                )
                conn.execute("DROP TABLE messages")
                conn.execute("ALTER TABLE messages_new RENAME TO messages")
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    conn.execute("CREATE INDEX IF NOT EXISTS idx_message_id ON messages(message_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_date ON messages(date)")
    configure_connection(conn)
    conn.commit()


def batch_upsert_messages(
    conn: sqlite3.Connection,
    messages: Sequence[object],
    *,
    replace_existing: bool,
) -> None:
    if not messages:
        return

    # NOTE: this will work only if msg structure is similar to database schema!
    data = [astuple(msg) for msg in messages]

    if replace_existing:
        conn.executemany(
            """
            INSERT INTO messages
              (message_id, date, sender_id, first_name, last_name, username,
               message, media_type, media_path, reply_to, post_author,
               is_forwarded, forwarded_from_channel_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(message_id) DO UPDATE SET
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
              (message_id, date, sender_id, first_name, last_name, username,
               message, media_type, media_path, reply_to, post_author,
               is_forwarded, forwarded_from_channel_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
