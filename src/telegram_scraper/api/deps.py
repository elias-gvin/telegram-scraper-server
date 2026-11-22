"""Shared FastAPI dependencies."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from fastapi import Header, HTTPException, Request

from ..archive import Archive
from ..config import ServerConfig


def get_config(request: Request) -> ServerConfig:
    return request.app.state.config


def get_archive(request: Request) -> Archive:
    return request.app.state.archive


async def get_client(request: Request):
    """The connected Telegram client (NotAuthorized -> 401 via the app's exception handler)."""
    return await request.app.state.telegram.get()


def require_token(request: Request, authorization: str | None = Header(default=None)):
    expected = f"Bearer {request.app.state.config.token}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(
            status_code=401,
            detail="Missing or wrong 'Authorization: Bearer <token>' (the token is in {data_dir}/token)",
        )


def parse_date(value: str | None, name: str) -> datetime | None:
    """'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS' (UTC) -> aware datetime."""
    if value is None:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    raise HTTPException(
        status_code=400,
        detail=f"Invalid {name} '{value}': use 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS'",
    )
