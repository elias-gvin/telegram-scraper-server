"""History (read from the archive) and sync (archive from Telegram) endpoints."""

from __future__ import annotations

import functools
import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, Request
from fastapi.responses import StreamingResponse
from telethon import utils

from ..archive import Archive
from ..config import ServerConfig
from ..serialize import serialize_row
from ..sync import sync_dialog
from ..telegram import SESSION_LOST, resolve
from .deps import get_archive, get_client, get_config, parse_date
from .settings import SettingsUpdate, apply_update

router = APIRouter(tags=["history"])
logger = logging.getLogger(__name__)

DialogId = Annotated[
    int,
    Path(description="Marked dialog id (-100… channels, -… groups, users positive)"),
]


def sse(data: dict, event: str | None = None) -> str:
    head = f"event: {event}\n" if event else ""
    return f"{head}data: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get(
    "/history/{dialog_id}",
    response_class=StreamingResponse,
    summary="Stream message history (syncs missing messages first)",
    description="""
Server-Sent Events. First the missing part of the requested range is synced
from Telegram into the local archive, then messages are streamed from the
archive ordered by message id:

- `data: {"messages": [...]}` — one event per chunk
- `event: done` with `{"count", "incomplete", "sync": {...report...}}` — the stream is complete
- `event: error` with `{"error", "retry_after"}` — the stream is NOT complete

A stream that ends without `done` is incomplete. History neither re-reads
already archived messages nor downloads media; call `POST /sync/{dialog_id}`
for edits, reactions, deletions and media files.
""",
)
async def get_history(
    request: Request,
    dialog_id: DialogId,
    start_date: Annotated[
        str | None, Query(description="Inclusive, UTC. Default: beginning of the chat")
    ] = None,
    end_date: Annotated[
        str | None, Query(description="Inclusive, UTC. Default: now")
    ] = None,
    chunk_size: Annotated[int, Query(gt=0, le=1000)] = 100,
    reverse: Annotated[
        bool, Query(description="True: oldest first. False: newest first")
    ] = True,
    client=Depends(get_client),
    archive: Archive = Depends(get_archive),
    config: ServerConfig = Depends(get_config),
):
    start = parse_date(start_date, "start_date")
    end = parse_date(end_date, "end_date") or datetime.now(UTC)
    if start and start >= end:
        raise HTTPException(
            status_code=400, detail="start_date must be before end_date"
        )
    entity = await resolve(client, dialog_id)
    did = utils.get_peer_id(entity)

    async def stream():
        try:
            report = await sync_dialog(
                client,
                archive,
                entity,
                config.settings,
                config.media_dir,
                since=start,
                refresh=False,
                media=False,
            )
            peer = functools.cache(archive.peer)
            count = incomplete = 0
            low = int(start.timestamp()) if start else 0
            for rows in archive.read(
                did, low, int(end.timestamp()), not reverse, chunk_size
            ):
                messages = [
                    _with_file(serialize_row(r, peer), r, config.media_dir)
                    for r in rows
                ]
                count += len(messages)
                incomplete += sum("incomplete" in m for m in messages)
                yield sse({"messages": messages})
            yield sse(
                {"count": count, "incomplete": incomplete, "sync": asdict(report)},
                "done",
            )
        except Exception as e:
            logger.exception("History stream for %s failed", did)
            if isinstance(e, SESSION_LOST):
                await request.app.state.telegram.reset()
            yield sse(
                {
                    "error": f"{type(e).__name__}: {e}",
                    "retry_after": getattr(e, "seconds", None),
                },
                "error",
            )

    return StreamingResponse(stream(), media_type="text/event-stream")


def _with_file(message: dict, row, media_dir) -> dict:
    """Add `media_file` (relative to {data_dir}/media) for a downloaded file.
    A file the archive calls downloaded but that is gone from disk is flagged, not hidden."""
    if row["media_status"] == "downloaded":
        if (media_dir / row["media_path"]).is_file():
            message["media_file"] = row["media_path"]
        else:
            message.update(media_status="missing", incomplete="media_missing_on_disk")
    return message


@router.post(
    "/sync/{dialog_id}",
    summary="Sync a dialog into the local archive",
    description="""
Archives new messages, extends history back to `since` (default: the whole
chat), re-reads the last `refresh_days` (setting) to pick up edits, reactions
and deletions (`full=true`: re-read everything), and downloads media allowed by
the settings. Returns counts and every problem found (unreadable messages,
failed downloads). An interrupted sync can simply be called again.
""",
)
async def sync_endpoint(
    dialog_id: DialogId,
    since: Annotated[
        str | None,
        Query(description="Archive back to this date (UTC). Default: whole chat"),
    ] = None,
    full: Annotated[
        bool, Query(description="Re-read every archived message, not only recent ones")
    ] = False,
    overrides: SettingsUpdate | None = Body(
        default=None,
        description="Settings for this call only (same fields as PATCH /settings); "
        "nothing is saved. Media skipped now is re-checked by later syncs.",
    ),
    client=Depends(get_client),
    archive: Archive = Depends(get_archive),
    config: ServerConfig = Depends(get_config),
) -> dict:
    entity = await resolve(client, dialog_id)
    settings = (
        apply_update(config.settings, overrides) if overrides else config.settings
    )
    report = await sync_dialog(
        client,
        archive,
        entity,
        settings,
        config.media_dir,
        since=parse_date(since, "since"),
        full=full,
    )
    return asdict(report)
