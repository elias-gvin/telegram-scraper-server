"""Web-based QR code authentication endpoints.

Flow
----
1. POST /auth/qr              → start QR login, get back {token, qr_url}
2. GET  /auth/qr/{token}      → poll status (pending / password_required / success / expired / error)
3. POST /auth/qr/{token}/2fa  → submit 2FA password (only when status == password_required)
4. DELETE /auth/qr/{token}     → cancel a pending session
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from ..config import ServerConfig
from . import auth_utils

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_config: ServerConfig | None = None

# Pending QR sessions keyed by token
_qr_sessions: dict[str, _QRSession] = {}  # type: ignore[name-defined]  # forward ref


def set_config(config: ServerConfig):
    global _config
    _config = config


# ---------------------------------------------------------------------------
# QR session state machine
# ---------------------------------------------------------------------------

QR_TOKEN_REFRESH_SECONDS = 25  # Recreate QR before Telegram's ~30s server-side expiry
QR_OVERALL_TIMEOUT_SECONDS = 300  # 5 minutes total for the whole auth flow
QR_2FA_TIMEOUT_SECONDS = 120  # 2 minutes to enter 2FA password


class QRStatus(str, Enum):
    pending = "pending"
    password_required = "password_required"
    success = "success"
    expired = "expired"
    error = "error"


class _QRSession:
    """Internal state for one QR login attempt."""

    __slots__ = (
        "token",
        "username",
        "status",
        "error_detail",
        "client",
        "qr_login",
        "qr_url",
        "created_at",
        "_wait_task",
        "_password_future",
    )

    def __init__(
        self,
        token: str,
        username: str,
        client: TelegramClient,
        qr_login,
        qr_url: str,
    ):
        self.token = token
        self.username = username
        self.status: QRStatus = QRStatus.pending
        self.error_detail: str | None = None
        self.client = client
        self.qr_login = qr_login
        self.qr_url = qr_url
        self.created_at = datetime.now(timezone.utc)
        self._wait_task: asyncio.Task | None = None
        self._password_future: asyncio.Future | None = None

    async def cleanup(self):
        """Cancel background task and disconnect client."""
        if self._wait_task and not self._wait_task.done():
            self._wait_task.cancel()
            try:
                await self._wait_task
            except (asyncio.CancelledError, Exception):
                pass
        if self.client.is_connected():
            await self.client.disconnect()


# Fix forward reference
_qr_sessions: dict[str, _QRSession] = {}


# ---------------------------------------------------------------------------
# Background waiter — runs as an asyncio Task per QR session
# ---------------------------------------------------------------------------


async def _wait_for_qr_scan(session: _QRSession):
    """Wait for the user to scan the QR code; update session status.

    Telegram QR tokens expire every ~30 seconds on the server side.
    This task loops, recreating the token (and updating ``session.qr_url``)
    so the polling client always gets a fresh URL.  The overall flow has a
    generous 5-minute timeout.
    """
    deadline = asyncio.get_event_loop().time() + QR_OVERALL_TIMEOUT_SECONDS

    try:
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                session.status = QRStatus.expired
                logger.info("QR auth overall timeout for user '%s'", session.username)
                break

            wait_time = min(QR_TOKEN_REFRESH_SECONDS, remaining)

            try:
                await asyncio.wait_for(session.qr_login.wait(), timeout=wait_time)
                # Scan succeeded, no 2FA
                session.status = QRStatus.success
                logger.info(
                    "QR auth success for user '%s' (token %s)",
                    session.username,
                    session.token,
                )
                break

            except asyncio.TimeoutError:
                # Token expired on Telegram's side — recreate and update URL
                try:
                    await session.qr_login.recreate()
                    session.qr_url = session.qr_login.url
                    logger.debug(
                        "Recreated QR token for '%s', new url: %s",
                        session.username,
                        session.qr_url,
                    )
                except Exception as exc:
                    session.status = QRStatus.error
                    session.error_detail = f"Failed to refresh QR token: {exc}"
                    logger.error(
                        "QR recreate failed for '%s': %s",
                        session.username,
                        exc,
                        exc_info=True,
                    )
                    break

    except SessionPasswordNeededError:
        # Need 2FA password
        session.status = QRStatus.password_required
        logger.info("QR auth requires 2FA for user '%s'", session.username)

        # Wait for the password to be supplied via the /2fa endpoint
        loop = asyncio.get_running_loop()
        session._password_future = loop.create_future()
        try:
            password = await asyncio.wait_for(
                session._password_future, timeout=QR_2FA_TIMEOUT_SECONDS
            )
            await session.client.sign_in(password=password)
            session.status = QRStatus.success
            logger.info("2FA auth success for user '%s'", session.username)
        except asyncio.TimeoutError:
            session.status = QRStatus.expired
            session.error_detail = "Timed out waiting for 2FA password"
        except Exception as exc:
            session.status = QRStatus.error
            session.error_detail = f"2FA sign-in failed: {exc}"
            logger.error("2FA failed for '%s': %s", session.username, exc)

    except asyncio.CancelledError:
        # Cancelled by cleanup — don't touch status
        raise

    except Exception as exc:
        session.status = QRStatus.error
        session.error_detail = str(exc)
        logger.error("QR auth error for '%s': %s", session.username, exc, exc_info=True)

    finally:
        # On success the session file is already written by Telethon.
        # Disconnect the temporary client (the API client pool will
        # create its own connection when the user starts making requests).
        if session.status not in (QRStatus.pending, QRStatus.password_required):
            if session.client.is_connected():
                await session.client.disconnect()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class QRStartRequest(BaseModel):
    username: str
    force: bool = False


class QRStartResponse(BaseModel):
    token: str
    qr_url: str
    message: str


class QRStatusResponse(BaseModel):
    status: QRStatus
    username: str
    qr_url: Optional[str] = None
    error: Optional[str] = None
    message: str


class TwoFARequest(BaseModel):
    password: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/qr",
    response_model=QRStartResponse,
    summary="Start QR code authentication",
    description=(
        "Initiates a QR code login. Returns a `qr_url` that should be rendered as "
        "a QR code (or opened as a `tg://` deep-link on mobile). "
        "Poll `GET /auth/qr/{token}` for status updates."
    ),
)
async def start_qr_auth(body: QRStartRequest):
    if _config is None:
        raise HTTPException(
            status_code=500, detail="Server configuration not initialized"
        )

    username = body.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="username must not be empty")

    # Check if user already has a valid session
    session_file = _config.sessions_path / f"{username}.session"
    if session_file.exists():
        if not body.force:
            raise HTTPException(
                status_code=409,
                detail=f"User '{username}' already has a session. "
                f"Use force=true to re-authenticate, or use a different username.",
            )
        # Force re-auth: evict from client pool and remove old session file
        await auth_utils.evict_client(username)
        session_file.unlink(missing_ok=True)
        logger.info("Force re-auth: removed existing session for '%s'", username)

    # Create a temporary Telegram client for this auth flow
    session_path = str(_config.sessions_path / username)
    client = TelegramClient(session_path, _config.api_id, _config.api_hash)

    try:
        await client.connect()
        qr_login = await client.qr_login()
    except Exception as exc:
        if client.is_connected():
            await client.disconnect()
        logger.error("Failed to start QR login: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail=f"Failed to start QR login: {exc}")

    token = uuid.uuid4().hex
    session = _QRSession(
        token=token,
        username=username,
        client=client,
        qr_login=qr_login,
        qr_url=qr_login.url,
    )
    _qr_sessions[token] = session

    # Start background task that waits for the scan
    session._wait_task = asyncio.create_task(_wait_for_qr_scan(session))

    return QRStartResponse(
        token=token,
        qr_url=qr_login.url,
        message=(
            "Scan the QR code with Telegram: "
            "Settings → Devices → Scan QR. "
            "Then poll GET /auth/qr/{token} for status."
        ),
    )


@router.get(
    "/qr/{token}",
    response_model=QRStatusResponse,
    summary="Poll QR authentication status",
    description="Returns the current status of a QR login session.",
)
async def get_qr_status(token: str):
    session = _qr_sessions.get(token)
    if session is None:
        raise HTTPException(
            status_code=404, detail="QR session not found or already cleaned up"
        )

    messages = {
        QRStatus.pending: "Waiting for QR code scan… (qr_url is auto-refreshed, re-render it each poll)",
        QRStatus.password_required: "QR scanned. Please submit your 2FA password via POST /auth/qr/{token}/2fa",
        QRStatus.success: f"Authenticated! Use header 'X-Telegram-Username: {session.username}' for API requests.",
        QRStatus.expired: "Session expired. Please start a new one with POST /auth/qr.",
        QRStatus.error: f"Authentication error: {session.error_detail}",
    }

    # Include the latest QR URL while still pending so the client can re-render it
    qr_url = session.qr_url if session.status == QRStatus.pending else None

    response = QRStatusResponse(
        status=session.status,
        username=session.username,
        qr_url=qr_url,
        error=session.error_detail,
        message=messages[session.status],
    )

    # Auto-cleanup terminal states
    if session.status in (QRStatus.success, QRStatus.expired, QRStatus.error):
        _qr_sessions.pop(token, None)

    return response


@router.post(
    "/qr/{token}/2fa",
    response_model=QRStatusResponse,
    summary="Submit 2FA password",
    description="Submit your two-factor authentication password after QR scan.",
)
async def submit_2fa_password(token: str, body: TwoFARequest):
    session = _qr_sessions.get(token)
    if session is None:
        raise HTTPException(
            status_code=404, detail="QR session not found or already cleaned up"
        )

    if session.status != QRStatus.password_required:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot submit 2FA: session status is '{session.status.value}', expected 'password_required'",
        )

    if session._password_future is None or session._password_future.done():
        raise HTTPException(
            status_code=409, detail="Password already submitted or session expired"
        )

    # Deliver the password to the waiting background task
    session._password_future.set_result(body.password)

    # Give the background task a moment to process
    for _ in range(30):
        await asyncio.sleep(0.1)
        if session.status != QRStatus.password_required:
            break

    messages = {
        QRStatus.success: f"Authenticated! Use header 'X-Telegram-Username: {session.username}' for API requests.",
        QRStatus.error: f"2FA failed: {session.error_detail}",
        QRStatus.password_required: "Still processing…",
        QRStatus.pending: "Still processing…",
        QRStatus.expired: "Session expired.",
    }

    response = QRStatusResponse(
        status=session.status,
        username=session.username,
        qr_url=None,
        error=session.error_detail,
        message=messages.get(session.status, "Unknown state"),
    )

    # Auto-cleanup terminal states
    if session.status in (QRStatus.success, QRStatus.expired, QRStatus.error):
        _qr_sessions.pop(token, None)

    return response


@router.delete(
    "/qr/{token}",
    summary="Cancel QR authentication",
    description="Cancel a pending QR login and clean up resources.",
)
async def cancel_qr_auth(token: str):
    session = _qr_sessions.pop(token, None)
    if session is None:
        raise HTTPException(
            status_code=404, detail="QR session not found or already cleaned up"
        )

    await session.cleanup()

    # Remove the partial session file if auth didn't complete
    session_file = _config.sessions_path / f"{session.username}.session"
    if session_file.exists() and session.status != QRStatus.success:
        session_file.unlink(missing_ok=True)

    return {"detail": "QR authentication session cancelled"}


# ---------------------------------------------------------------------------
# Cleanup helper (called on server shutdown)
# ---------------------------------------------------------------------------


async def cleanup_qr_sessions():
    """Cancel all pending QR sessions. Called during server shutdown."""
    for token, session in list(_qr_sessions.items()):
        await session.cleanup()
        # Remove partial session files
        if session.status != QRStatus.success:
            session_file = _config.sessions_path / f"{session.username}.session"
            if session_file.exists():
                session_file.unlink(missing_ok=True)
    _qr_sessions.clear()
