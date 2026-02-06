"""Database engine and session management for SQLModel."""

from __future__ import annotations

from sqlmodel import create_engine, Session, SQLModel
from sqlalchemy import event
from pathlib import Path
from typing import Generator
from contextlib import contextmanager


def get_engine(db_path: Path, check_same_thread: bool = True):
    """
    Create SQLAlchemy engine with SQLite pragmas for performance.

    Args:
        db_path: Path to SQLite database file
        check_same_thread: SQLite thread safety setting. Set to False for async/
                          multi-threaded usage (FastAPI). Default True for safety.

    Returns:
        SQLAlchemy engine configured for SQLite with WAL mode
    """
    url = f"sqlite:///{db_path}"
    connect_args = {"check_same_thread": check_same_thread}

    # echo=False for production, echo=True for debugging SQL queries
    engine = create_engine(url, connect_args=connect_args, echo=False)

    # Configure SQLite pragmas for better performance and concurrency
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def create_db_and_tables(engine):
    """
    Create all tables if they don't exist.

    This is idempotent - safe to call multiple times.
    """
    SQLModel.metadata.create_all(engine)


@contextmanager
def get_session(
    db_path: Path, check_same_thread: bool = True
) -> Generator[Session, None, None]:
    """
    Context manager for database sessions.

    Usage:
        with get_session(db_path) as session:
            # Use session for queries
            ...

    Args:
        db_path: Path to SQLite database file
        check_same_thread: SQLite thread safety setting

    Yields:
        SQLModel Session
    """
    engine = get_engine(db_path, check_same_thread)
    with Session(engine) as session:
        yield session
