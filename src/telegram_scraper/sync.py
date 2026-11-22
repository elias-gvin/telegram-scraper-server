"""Sync one dialog from Telegram into the archive.

Message ids only grow within a chat, so the archive keeps ONE contiguous id
interval per dialog and only ever extends it from its edges:

  1. newer:   everything above high_id                (oldest -> newest)
  2. older:   everything below low_id, down to `since` (newest -> oldest)
  3. refresh: re-read the last N days (or everything with full=True) to pick up
              edits, reactions, poll votes and deletions
  4. media:   download files the settings allow and that are not on disk yet

Each batch of messages and the new interval are written in one transaction, so
an interrupted sync leaves a smaller but still hole-free interval. Problems
(unreadable messages, failed downloads) never stop the sync; they are flagged on
the message and listed in the report.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telethon import utils
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.types import Channel

from . import media as media_files
from .archive import Archive, dump_raw
from .config import RuntimeSettings
from .serialize import incomplete, iso
from .telegram import peer_fields, resolve, title_of

logger = logging.getLogger(__name__)

_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


@dataclass
class SyncReport:
    dialog_id: int
    new_messages: int = 0
    refreshed_messages: int = 0
    deleted_messages: int = 0
    media_downloaded: int = 0
    media_skipped: int = 0
    media_failed: int = 0
    reached_start: bool = False
    covered_from: str | None = None  # date of the oldest archived message
    migrated_from: int | None = None  # old basic group, archived as its own dialog
    problems: list[dict] = field(default_factory=list)

    def problem(self, message_id: int, what: str, dialog_id: int | None = None):
        entry = {
            "dialog_id": dialog_id or self.dialog_id,
            "message_id": message_id,
            "problem": what,
        }
        if entry not in self.problems:
            self.problems.append(entry)


@dataclass
class _Context:
    client: object
    archive: Archive
    dialog_id: int
    batch_size: int
    report: SyncReport
    known_peers: set[int] = field(default_factory=set)


async def sync_dialog(
    client,
    archive: Archive,
    entity,
    settings: RuntimeSettings,
    media_dir: Path,
    *,
    since: datetime | None = None,
    refresh: bool = True,
    full: bool = False,
) -> SyncReport:
    """Bring the archive of `entity` up to date. `since=None` means the whole history."""
    did = utils.get_peer_id(entity)
    async with _locks[did]:
        migrated_from = await _migrated_from(client, entity)
        archive.save_dialog(
            did, title_of(entity), getattr(entity, "username", None), migrated_from
        )
        state = archive.dialog(did)
        report = SyncReport(did, migrated_from=migrated_from)
        ctx = _Context(client, archive, did, settings.telegram_batch_size, report)

        reached_start = bool(state["reached_start"])
        if state["high_id"] is not None or reached_start:
            new, _ = await _fetch(
                ctx,
                client.iter_messages(
                    entity, min_id=state["high_id"] or 0, reverse=True
                ),
            )
            report.new_messages += len(new)
        if not reached_start and _needs_older(archive, did, state["low_id"], since):
            older, reached_start = await _fetch(
                ctx, client.iter_messages(entity, max_id=state["low_id"] or 0), since
            )
            report.new_messages += len(older)
        if refresh and state["high_id"] is not None:
            await _refresh(
                ctx, entity, state["low_id"], state["high_id"], settings, full
            )
        await _download_media(ctx, entity, settings, media_dir)
        archive.finish_sync(
            did, reached_start, int(datetime.now(timezone.utc).timestamp())
        )

        final = archive.dialog(did)
        report.reached_start = bool(final["reached_start"])
        if final["low_id"] is not None:
            report.covered_from = iso(archive.message_date(did, final["low_id"]))

    if migrated_from is not None:
        old = await sync_dialog(
            client,
            archive,
            await resolve(client, migrated_from),
            settings,
            media_dir,
            since=since,
            refresh=refresh,
            full=full,
        )
        report.problems += old.problems
    return report


def _needs_older(
    archive: Archive, did: int, low_id: int | None, since: datetime | None
) -> bool:
    if low_id is None or since is None:
        return True
    return archive.message_date(did, low_id) >= since.timestamp()


async def _migrated_from(client, entity) -> int | None:
    """Marked id of the basic group this supergroup was upgraded from (its history stays there)."""
    if not (isinstance(entity, Channel) and entity.megagroup):
        return None
    full = await client(GetFullChannelRequest(entity))
    chat_id = full.full_chat.migrated_from_chat_id
    return -chat_id if chat_id else None


async def _fetch(
    ctx: _Context, messages, stop_before: datetime | None = None
) -> tuple[list[int], bool]:
    """Save messages in batches. Returns (saved ids, True if the iterator was exhausted)."""
    saved, batch = [], []
    async for msg in messages:
        if stop_before and msg.date < stop_before:
            saved += await _save(ctx, batch)
            return saved, False
        batch.append(msg)
        if len(batch) >= ctx.batch_size:
            saved += await _save(ctx, batch)
            batch = []
    saved += await _save(ctx, batch)
    return saved, True


async def _save(ctx: _Context, messages) -> list[int]:
    if not messages:
        return []
    peers = await _peers(ctx, messages)
    with ctx.archive.transaction():
        for pid, fields in peers.items():
            ctx.archive.save_peer(pid, **fields)
        for msg in messages:
            raw = dump_raw(msg)
            ctx.archive.save_message(
                ctx.dialog_id, msg.id, int(msg.date.timestamp()), msg.sender_id, raw
            )
            data = json.loads(raw)
            if info := media_files.media_info(data.get("media")):
                ctx.archive.expect_media(ctx.dialog_id, msg.id, info.key)
            if why := incomplete(data):
                ctx.report.problem(msg.id, why)
        ids = [m.id for m in messages]
        ctx.archive.extend_coverage(ctx.dialog_id, min(ids), max(ids))
    return ids


async def _peers(ctx: _Context, messages) -> dict[int, dict]:
    """Names of senders, chats and forward sources, so the API can show them later."""
    found = {}
    for msg in messages:
        for entity in (msg.sender, msg.chat):
            if entity is not None:
                found[utils.get_peer_id(entity)] = peer_fields(entity)
        fwd = msg.fwd_from
        source = fwd and (fwd.from_id or fwd.saved_from_peer)
        if (
            not source
            or utils.get_peer_id(source) in found
            or utils.get_peer_id(source) in ctx.known_peers
        ):
            continue
        pid = utils.get_peer_id(source)
        ctx.known_peers.add(pid)
        entity = msg.forward and (msg.forward.sender or msg.forward.chat)
        if entity is None or utils.get_peer_id(entity) != pid:
            try:
                entity = await ctx.client.get_entity(source)
            except Exception as e:  # noqa: BLE001 -- hidden/inaccessible: the forward keeps its from_name
                logger.debug("Cannot resolve forward source %s: %s", pid, e)
                continue
        found[pid] = peer_fields(entity)
    return found


async def _refresh(
    ctx: _Context,
    entity,
    low_id: int,
    high_id: int,
    settings: RuntimeSettings,
    full: bool,
):
    """Re-read already archived messages; anything Telegram no longer returns is marked deleted."""
    if not full and settings.refresh_days == 0:
        return
    since = (
        None
        if full
        else datetime.now(timezone.utc) - timedelta(days=settings.refresh_days)
    )
    seen, _ = await _fetch(
        ctx,
        ctx.client.iter_messages(entity, max_id=high_id + 1, min_id=low_id - 1),
        since,
    )
    gone = ctx.archive.live_ids(
        ctx.dialog_id, low_id, high_id, since and int(since.timestamp())
    ) - set(seen)
    ctx.archive.mark_deleted(ctx.dialog_id, gone)
    ctx.report.refreshed_messages = len(seen)
    ctx.report.deleted_messages = len(gone)


async def _download_media(
    ctx: _Context, entity, settings: RuntimeSettings, media_dir: Path
):
    wanted = {}
    for row in ctx.archive.media_without_file(ctx.dialog_id):
        info = media_files.media_info(json.loads(row["raw"]).get("media"))
        if why := media_files.skip_reason(info, settings):
            ctx.archive.set_media(ctx.dialog_id, row["message_id"], f"skipped:{why}")
            ctx.report.media_skipped += 1
        else:
            wanted[row["message_id"]] = info

    ids = list(wanted)
    for start in range(0, len(ids), 100):
        chunk = ids[start : start + 100]
        for mid, msg in zip(chunk, await ctx.client.get_messages(entity, ids=chunk)):
            try:
                if msg is None:
                    raise LookupError("message no longer exists on Telegram")
                path = await media_files.download(
                    msg, wanted[mid], media_dir / str(ctx.dialog_id)
                )
                ctx.archive.set_media(
                    ctx.dialog_id, mid, "downloaded", str(path.relative_to(media_dir))
                )
                ctx.report.media_downloaded += 1
            except Exception as e:  # noqa: BLE001 -- recorded on the message and reported
                ctx.archive.set_media(
                    ctx.dialog_id, mid, f"failed:{type(e).__name__}: {e}"
                )
                ctx.report.media_failed += 1
                ctx.report.problem(mid, f"media_failed: {type(e).__name__}: {e}")
