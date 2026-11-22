"""Live message search on Telegram (no archive involved).

- messages.search       — within one dialog
- messages.searchGlobal — across all dialogs

Results use the same message shape as /history, plus dialog_id / dialog_name.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel
from telethon import utils

from ..archive import dump_raw
from ..serialize import display_name, serialize
from ..telegram import peer_fields, resolve
from .deps import get_client, parse_date

router = APIRouter(tags=["search"])

Q = Annotated[str, Query(alias="q", min_length=1, description="Word or phrase to find")]
Start = Annotated[
    str | None, Query(description="Only messages on/after this date (UTC)")
]
End = Annotated[
    str | None, Query(description="Only messages on/before this date (UTC)")
]
Limit = Annotated[int, Query(ge=1, le=500, description="Maximum number of results")]


class SearchMessagesResponse(BaseModel):
    query: str
    count: int  # number of results returned (not the number of matches on Telegram)
    limit: int
    results: list[dict[str, Any]]


def _serialize_live(msg) -> dict:
    """A live Telethon message -> the /history message shape (+ dialog_id, dialog_name)."""
    peers = {}
    forward = msg.forward
    for entity in (
        msg.sender,
        msg.chat,
        forward and forward.sender,
        forward and forward.chat,
    ):
        if entity is not None:
            peers[utils.get_peer_id(entity)] = peer_fields(entity)
    out = serialize(json.loads(dump_raw(msg)), msg.sender_id, peers.get)
    out["dialog_id"] = msg.chat_id
    out["dialog_name"] = display_name(peers.get(msg.chat_id))
    return out


async def _search(
    client, entity, query, start_date, end_date, limit, **kw
) -> SearchMessagesResponse:
    start = parse_date(start_date, "start_date")
    end = parse_date(end_date, "end_date")
    if start and end and start >= end:
        raise HTTPException(
            status_code=400, detail="start_date must be before end_date"
        )
    results = []
    # Telegram returns newest first, starting below offset_date (= our upper bound).
    async for msg in client.iter_messages(
        entity, search=query, limit=limit, offset_date=end, **kw
    ):
        if start and msg.date < start:
            break
        results.append(_serialize_live(msg))
    return SearchMessagesResponse(
        query=query, count=len(results), limit=limit, results=results
    )


@router.get(
    "/search/messages/{dialog_id}",
    response_model=SearchMessagesResponse,
    summary="Search messages within one dialog",
)
async def search_messages_in_dialog(
    dialog_id: Annotated[int, Path(description="Marked dialog id")],
    query: Q,
    start_date: Start = None,
    end_date: End = None,
    from_user: Annotated[
        int | None, Query(description="Only messages sent by this user id")
    ] = None,
    limit: Limit = 50,
    client=Depends(get_client),
):
    entity = await resolve(client, dialog_id)
    extra = {"from_user": from_user} if from_user else {}
    return await _search(client, entity, query, start_date, end_date, limit, **extra)


@router.get(
    "/search/messages",
    response_model=SearchMessagesResponse,
    summary="Search messages in all dialogs",
)
async def search_messages_global(
    query: Q,
    start_date: Start = None,
    end_date: End = None,
    limit: Limit = 50,
    client=Depends(get_client),
):
    return await _search(client, None, query, start_date, end_date, limit)
