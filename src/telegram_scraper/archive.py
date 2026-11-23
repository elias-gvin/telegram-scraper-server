"""Local message archive: one SQLite file per Telegram account.

`messages.raw` holds the full Telethon message (`to_dict()` as JSON) and is the
source of truth; the other message columns only exist for querying.

Each dialog's archived messages form ONE contiguous id interval
[low_id, high_id] (see sync.py), stored in `dialogs` and updated in the same
transaction as the messages.
"""

from __future__ import annotations

import base64
import json
import sqlite3
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

# One entry per schema version (PRAGMA user_version). Append, never edit.
MIGRATIONS = [
    """
    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE dialogs (
        dialog_id     INTEGER PRIMARY KEY,
        title         TEXT,
        username      TEXT,
        low_id        INTEGER,
        high_id       INTEGER,
        reached_start INTEGER NOT NULL DEFAULT 0,
        migrated_from INTEGER,
        synced_at     INTEGER
    );
    CREATE TABLE messages (
        dialog_id  INTEGER NOT NULL,
        message_id INTEGER NOT NULL,
        date       INTEGER NOT NULL,
        sender_id  INTEGER,
        deleted    INTEGER NOT NULL DEFAULT 0,
        raw        TEXT NOT NULL,
        PRIMARY KEY (dialog_id, message_id)
    );
    CREATE INDEX messages_by_date ON messages (dialog_id, date);
    CREATE TABLE peers (
        peer_id    INTEGER PRIMARY KEY,
        first_name TEXT,
        last_name  TEXT,
        title      TEXT,
        username   TEXT
    );
    CREATE TABLE media (
        dialog_id  INTEGER NOT NULL,
        message_id INTEGER NOT NULL,
        media_key  INTEGER NOT NULL,
        status     TEXT NOT NULL,  -- pending | downloaded | skipped:<why> | failed:<why>
        path       TEXT,           -- relative to the media directory
        PRIMARY KEY (dialog_id, message_id)
    );
    """,
    """
    -- What could not be archived; message_id 0 = the dialog as a whole
    CREATE TABLE problems (
        dialog_id  INTEGER NOT NULL,
        message_id INTEGER NOT NULL,
        problem    TEXT NOT NULL,
        PRIMARY KEY (dialog_id, message_id)
    );
    """,
    """
    -- Telegram API layer of the OLDEST read among this dialog's archived messages
    -- (NULL = unknown: archived before this was tracked). See sync.py.
    ALTER TABLE dialogs ADD COLUMN layer INTEGER;
    """,
    """
    -- The layer moves to each message: the layer it was last read with (NULL = unknown).
    -- The dialog's value was the oldest layer among its messages, so it is a safe
    -- lower bound for every one of them.
    ALTER TABLE messages ADD COLUMN layer INTEGER;
    UPDATE messages SET layer = (
        SELECT d.layer FROM dialogs d WHERE d.dialog_id = messages.dialog_id
    );
    ALTER TABLE dialogs DROP COLUMN layer;
    """,
]


def dump_raw(message) -> str:
    """Telethon message -> JSON. Dates become unix seconds, bytes become base64."""

    def encode(value):
        if isinstance(value, datetime):
            return int(value.timestamp())
        if isinstance(value, bytes):
            return base64.b64encode(value).decode()
        raise TypeError(f"cannot serialize {type(value).__name__}")

    return json.dumps(message.to_dict(), default=encode, ensure_ascii=False)


class AccountMismatch(RuntimeError):
    pass


class Archive:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
            self.db.executescript(
                f"BEGIN; {script}; PRAGMA user_version = {number}; COMMIT;"
            )

    def close(self):
        self.db.close()

    def transaction(self) -> sqlite3.Connection:
        """`with archive.transaction():` commits on success, rolls back on error."""
        return self.db

    # --- account -----------------------------------------------------------
    def bind_account(self, user_id: int) -> None:
        """Private-chat message ids are per account: never mix two accounts in one archive."""
        row = self.db.execute(
            "SELECT value FROM meta WHERE key = 'account_user_id'"
        ).fetchone()
        if row is None:
            with self.db:
                self.db.execute(
                    "INSERT INTO meta VALUES ('account_user_id', ?)", (str(user_id),)
                )
        elif int(row[0]) != user_id:
            raise AccountMismatch(
                f"This archive belongs to Telegram user {row[0]}, but the session is user {user_id}. "
                "Use a separate --data-dir per account."
            )

    # --- dialogs -------------------------------------------------------------
    def dialog(self, dialog_id: int) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM dialogs WHERE dialog_id = ?", (dialog_id,)
        ).fetchone()

    def save_dialog(
        self,
        dialog_id: int,
        title: str,
        username: str | None,
        migrated_from: int | None,
    ):
        with self.db:
            self.db.execute(
                """INSERT INTO dialogs (dialog_id, title, username, migrated_from) VALUES (?, ?, ?, ?)
                   ON CONFLICT (dialog_id) DO UPDATE SET
                     title = excluded.title, username = excluded.username,
                     migrated_from = coalesce(excluded.migrated_from, migrated_from)""",
                (dialog_id, title, username, migrated_from),
            )

    def finish_sync(self, dialog_id: int, reached_start: bool, synced_at: int):
        with self.db:
            self.db.execute(
                """UPDATE dialogs SET reached_start = max(reached_start, ?), synced_at = ?
                   WHERE dialog_id = ?""",
                (int(reached_start), synced_at, dialog_id),
            )

    # --- writes (call inside `with archive.transaction():`) -----------------
    def save_message(
        self,
        dialog_id: int,
        message_id: int,
        date: int,
        sender_id,
        raw: str,
        layer: int,
    ):
        """`layer`: the Telegram API layer of the client that read the message."""
        self.db.execute(
            """INSERT INTO messages (dialog_id, message_id, date, sender_id, raw, layer)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT (dialog_id, message_id) DO UPDATE SET
                 date = excluded.date, sender_id = excluded.sender_id, raw = excluded.raw,
                 layer = excluded.layer, deleted = 0""",
            (dialog_id, message_id, date, sender_id, raw, layer),
        )

    def extend_coverage(self, dialog_id: int, low_id: int, high_id: int):
        self.db.execute(
            """UPDATE dialogs SET low_id = min(coalesce(low_id, ?), ?), high_id = max(coalesce(high_id, ?), ?)
               WHERE dialog_id = ?""",
            (low_id, low_id, high_id, high_id, dialog_id),
        )

    def save_peer(
        self, peer_id: int, first_name=None, last_name=None, title=None, username=None
    ):
        """Never overwrites a known value with NULL."""
        self.db.execute(
            """INSERT INTO peers VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (peer_id) DO UPDATE SET
                 first_name = coalesce(excluded.first_name, first_name),
                 last_name  = coalesce(excluded.last_name, last_name),
                 title      = coalesce(excluded.title, title),
                 username   = coalesce(excluded.username, username)""",
            (peer_id, first_name, last_name, title, username),
        )

    def expect_media(self, dialog_id: int, message_id: int, media_key: int):
        """Register a media file; a different file (edited message) resets it to pending."""
        self.db.execute(
            """INSERT INTO media (dialog_id, message_id, media_key, status) VALUES (?, ?, ?, 'pending')
               ON CONFLICT (dialog_id, message_id) DO UPDATE SET
                 media_key = excluded.media_key, status = 'pending', path = NULL
               WHERE media.media_key != excluded.media_key""",
            (dialog_id, message_id, media_key),
        )

    # --- single-statement writes --------------------------------------------
    def mark_deleted(self, dialog_id: int, message_ids):
        with self.db:
            self.db.executemany(
                "UPDATE messages SET deleted = 1 WHERE dialog_id = ? AND message_id = ?",
                [(dialog_id, i) for i in message_ids],
            )

    def set_media(
        self,
        dialog_id: int,
        message_id: int,
        status: str,
        path: str | None = None,
        problem: str | None = None,
    ):
        with self.db:
            self.db.execute(
                "UPDATE media SET status = ?, path = ? WHERE dialog_id = ? AND message_id = ?",
                (status, path, dialog_id, message_id),
            )
            self.set_problem(dialog_id, message_id, problem)

    def set_problem(self, dialog_id: int, message_id: int, problem: str | None):
        """Record (or clear, with None) why a message is not fully archived."""
        if problem is None:
            self.db.execute(
                "DELETE FROM problems WHERE dialog_id = ? AND message_id = ?",
                (dialog_id, message_id),
            )
        else:
            self.db.execute(
                "INSERT OR REPLACE INTO problems VALUES (?, ?, ?)",
                (dialog_id, message_id, problem),
            )

    # --- reads ---------------------------------------------------------------
    def live_ids(self, dialog_id: int, low_id: int, high_id: int) -> set[int]:
        """Ids of not-deleted messages in [low_id, high_id]."""
        rows = self.db.execute(
            """SELECT message_id FROM messages WHERE dialog_id = ? AND deleted = 0
               AND message_id BETWEEN ? AND ?""",
            (dialog_id, low_id, high_id),
        )
        return {r[0] for r in rows}

    def first_id_since(
        self, dialog_id: int, low_id: int, high_id: int, since: int
    ) -> int | None:
        """Lowest id in [low_id, high_id] of a message dated `since` or later."""
        return self.db.execute(
            """SELECT min(message_id) FROM messages WHERE dialog_id = ?
               AND message_id BETWEEN ? AND ? AND date >= ?""",
            (dialog_id, low_id, high_id, since),
        ).fetchone()[0]

    def message_date(self, dialog_id: int, message_id: int) -> int | None:
        row = self.db.execute(
            "SELECT date FROM messages WHERE dialog_id = ? AND message_id = ?",
            (dialog_id, message_id),
        ).fetchone()
        return row[0] if row else None

    def problems(self, dialog_id: int) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM problems WHERE dialog_id = ? ORDER BY message_id",
            (dialog_id,),
        )
        return [dict(r) for r in rows]

    def message_count(self, dialog_id: int) -> int:
        """Archived messages that still exist on Telegram (as far as we know)."""
        return self.db.execute(
            "SELECT count(*) FROM messages WHERE dialog_id = ? AND deleted = 0",
            (dialog_id,),
        ).fetchone()[0]

    def oldest_layer(self, dialog_id: int) -> int | None:
        """Oldest layer among the dialog's messages that still exist (deleted ones can
        never be re-read). None if any of them has an unknown layer, or there are none."""
        row = self.db.execute(
            """SELECT count(*) AS n, count(layer) AS known, min(layer) AS oldest
               FROM messages WHERE dialog_id = ? AND deleted = 0""",
            (dialog_id,),
        ).fetchone()
        return row["oldest"] if row["n"] and row["known"] == row["n"] else None

    def media_rows(self, dialog_id: int) -> list[sqlite3.Row]:
        return self.db.execute(
            """SELECT m.message_id, m.raw, media.status, media.path
               FROM media JOIN messages m USING (dialog_id, message_id)
               WHERE media.dialog_id = ? ORDER BY m.message_id""",
            (dialog_id,),
        ).fetchall()

    def media(self, dialog_id: int, message_id: int) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM media WHERE dialog_id = ? AND message_id = ?",
            (dialog_id, message_id),
        ).fetchone()

    def peer(self, peer_id: int | None) -> dict | None:
        if peer_id is None:
            return None
        row = self.db.execute(
            "SELECT * FROM peers WHERE peer_id = ?", (peer_id,)
        ).fetchone()
        return dict(row) if row else None

    def read(
        self,
        dialog_id: int,
        start: int,
        end: int,
        newest_first: bool = False,
        batch: int = 100,
    ) -> Iterator[list[sqlite3.Row]]:
        """Messages with start <= date <= end, ordered by id, in batches (keyset pagination)."""
        op, order = ("<", "DESC") if newest_first else (">", "ASC")
        cursor = None
        while True:
            rows = self.db.execute(
                f"""SELECT m.*, media.status AS media_status, media.path AS media_path FROM messages m
                    LEFT JOIN media USING (dialog_id, message_id)
                    WHERE m.dialog_id = ? AND m.date BETWEEN ? AND ?
                      AND (? IS NULL OR m.message_id {op} ?)
                    ORDER BY m.message_id {order} LIMIT ?""",
                (dialog_id, start, end, cursor, cursor, batch),
            ).fetchall()
            if not rows:
                return
            yield rows
            cursor = rows[-1]["message_id"]
