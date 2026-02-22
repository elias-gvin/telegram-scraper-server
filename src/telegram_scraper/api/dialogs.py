"""Dialog search and folder listing API endpoints."""

from fastapi import APIRouter, Query, Depends, HTTPException
from typing import Annotated, Optional, List
from pydantic import BaseModel
from enum import Enum
from difflib import SequenceMatcher
from datetime import datetime, timezone
import asyncio
import logging

from telethon import TelegramClient
from telethon.tl.functions.messages import GetDialogFiltersRequest
from telethon.errors import FloodWaitError

from .auth_utils import get_telegram_client


logger = logging.getLogger(__name__)


router = APIRouter(tags=["dialogs"])


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class DialogType(str, Enum):
    """Type of Telegram dialog."""

    user = "user"
    group = "group"
    supergroup = "supergroup"
    channel = "channel"
    bot = "bot"
    saved = "saved"
    me = "me"  # alias for saved (accepted as input, output always uses "saved")


class MatchMode(str, Enum):
    """Text matching mode for query searches."""

    fuzzy = "fuzzy"
    exact = "exact"


class SortField(str, Enum):
    """Available sort fields for dialog search results."""

    last_message = "last_message"
    messages = "messages"
    title = "title"
    participants = "participants"
    unread = "unread"


class SortOrder(str, Enum):
    """Sort direction."""

    asc = "asc"
    desc = "desc"


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class DialogInfo(BaseModel):
    """Dialog information returned by the search endpoint."""

    id: int
    type: DialogType
    title: str
    username: Optional[str] = None
    is_creator: bool = False
    is_verified: bool = False
    is_archived: bool = False
    message_count: Optional[int] = None  # actual count fetched via messages.getHistory
    unread_count: int = 0
    participants_count: Optional[int] = None
    last_message_date: Optional[str] = None  # ISO 8601
    last_message_preview: Optional[str] = None
    created_date: Optional[str] = None  # ISO 8601, channels/groups only


class DialogSearchResponse(BaseModel):
    """Paginated dialog search response."""

    total: int
    offset: int
    limit: int
    results: List[DialogInfo]


class FolderInfo(BaseModel):
    """Telegram folder information."""

    id: int
    title: str
    is_default: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_date(date_str: str) -> datetime:
    """Parse a date string into a timezone-aware UTC datetime."""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise HTTPException(
        status_code=400,
        detail=f"Invalid date format: '{date_str}'. Use 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS'",
    )


def _search_score(title: str, query: str) -> float:
    """Combined fuzzy + substring search score (0.0 to 1.0).

    Substring matches get a high base score so that partial queries like
    'crypt' still rank 'Crypto Traders' highly.
    """
    t, q = title.lower(), query.lower()
    if q in t:
        # Substring hit: 0.8 base + bonus for how much of the title is covered
        return 0.8 + 0.2 * (len(q) / len(t))
    return SequenceMatcher(None, t, q).ratio()


def _dialog_title(dialog) -> str:
    """Return the display title for a dialog (same logic as _dialog_to_info)."""
    entity = dialog.entity
    if dialog.is_user:
        first = getattr(entity, "first_name", "") or ""
        last = getattr(entity, "last_name", "") or ""
        return f"{first} {last}".strip() or str(entity.id)
    return getattr(entity, "title", "") or str(entity.id)


def _classify_dialog(dialog, my_id: int) -> DialogType:
    """Classify a Telethon Dialog into a DialogType."""
    entity = dialog.entity
    if dialog.is_user:
        if entity.id == my_id:
            return DialogType.saved
        if getattr(entity, "bot", False):
            return DialogType.bot
        return DialogType.user
    if dialog.is_group:
        return DialogType.group
    # is_channel covers both supergroups and broadcast channels
    if getattr(entity, "megagroup", False):
        return DialogType.supergroup
    return DialogType.channel


def _dialog_to_info(dialog, my_id: int) -> DialogInfo:
    """Convert a Telethon Dialog to a DialogInfo response model."""
    entity = dialog.entity

    # Human-readable title
    if dialog.is_user:
        first = getattr(entity, "first_name", "") or ""
        last = getattr(entity, "last_name", "") or ""
        title = f"{first} {last}".strip() or str(entity.id)
    else:
        title = getattr(entity, "title", "") or str(entity.id)

    # Last message metadata
    last_message_date = dialog.date.isoformat() if dialog.date else None
    last_message_preview = None
    if dialog.message and dialog.message.text:
        text = dialog.message.text
        last_message_preview = text[:120] + ("..." if len(text) > 120 else "")

    # Creation date (channels/supergroups/groups store this on the entity)
    created_date = None
    entity_date = getattr(entity, "date", None)
    if entity_date is not None:
        created_date = entity_date.isoformat()

    return DialogInfo(
        id=entity.id,
        type=_classify_dialog(dialog, my_id),
        title=title,
        username=getattr(entity, "username", None),
        is_creator=getattr(entity, "creator", False) or False,
        is_verified=getattr(entity, "verified", False) or False,
        is_archived=dialog.archived,
        message_count=None,  # message_count will be filled later from messages.getHistory
        unread_count=dialog.unread_count or 0,
        participants_count=getattr(entity, "participants_count", None),
        last_message_date=last_message_date,
        last_message_preview=last_message_preview,
        created_date=created_date,
    )


def _sort_key(d: DialogInfo, field: SortField):
    """Return a sortable value for the given field."""
    if field == SortField.last_message:
        return d.last_message_date or ""
    if field == SortField.messages:
        return d.message_count or 0
    if field == SortField.title:
        return d.title.lower()
    if field == SortField.participants:
        return d.participants_count or 0
    if field == SortField.unread:
        return d.unread_count
    return ""


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/search/dialogs",
    response_model=DialogSearchResponse,
    summary="Search and filter all dialogs",
    description="""
Search all Telegram dialogs (personal chats, groups, supergroups, channels, bots, Saved Messages)
with optional filtering, fuzzy/exact text search, sorting, and pagination.

All parameters are optional. Omit everything to list all dialogs.

**Text search** (`q`):
- Omit `q` to return all dialogs (subject to other filters).
- `match=fuzzy` (default): scored match using substring + fuzzy ratio, filtered by `min_score`.
- `match=exact`: case-insensitive substring check.

**Type filter** (`type`):
Repeat the parameter for multiple types: `?type=group&type=supergroup`.
Both `saved` and `me` refer to your Saved Messages dialog.

**Folder filter** (`folder`):
Pass a folder ID (int) or folder name (string, case-insensitive).
Use `GET /api/v1/folders` to discover available folders.

Examples:
- `/api/v1/search/dialogs` -- all dialogs
- `/api/v1/search/dialogs?q=crypto&min_score=0.6`
- `/api/v1/search/dialogs?type=group&type=supergroup`
- `/api/v1/search/dialogs?min_messages=100&last_message_after=2024-01-01`
- `/api/v1/search/dialogs?folder=Work&is_archived=false`
- `/api/v1/search/dialogs?type=saved` or `?type=me`
""",
)
async def search_dialogs(
    query: Annotated[
        Optional[str],
        Query(
            alias="q",
            description="Search query on dialog title. Omit to return all.",
        ),
    ] = None,
    match: Annotated[
        MatchMode,
        Query(description="Matching mode: fuzzy (scored) or exact (substring)"),
    ] = MatchMode.fuzzy,
    min_score: Annotated[
        float,
        Query(
            ge=0.0,
            le=1.0,
            description="Minimum fuzzy score threshold (only used when match=fuzzy)",
        ),
    ] = 0.8,
    type: Annotated[
        Optional[List[DialogType]],
        Query(description="Filter by dialog type(s). Repeat for multiple."),
    ] = None,
    folder: Annotated[
        Optional[str],
        Query(
            description="Folder ID (int) or folder name (case-insensitive, exact match). "
            "Use GET /api/v1/folders to list available folders.",
        ),
    ] = None,
    is_archived: Annotated[
        Optional[bool],
        Query(description="Filter by archive status. Omit to include both."),
    ] = None,
    min_messages: Annotated[
        Optional[int],
        Query(ge=0, description="Minimum approximate message count"),
    ] = None,
    max_messages: Annotated[
        Optional[int],
        Query(ge=0, description="Maximum approximate message count"),
    ] = None,
    min_participants: Annotated[
        Optional[int],
        Query(ge=0, description="Minimum participant/member count"),
    ] = None,
    max_participants: Annotated[
        Optional[int],
        Query(ge=0, description="Maximum participant/member count"),
    ] = None,
    last_message_after: Annotated[
        Optional[str],
        Query(description="Last message date lower bound (YYYY-MM-DD)"),
    ] = None,
    last_message_before: Annotated[
        Optional[str],
        Query(description="Last message date upper bound (YYYY-MM-DD)"),
    ] = None,
    created_after: Annotated[
        Optional[str],
        Query(description="Creation date lower bound (YYYY-MM-DD, channels/groups)"),
    ] = None,
    created_before: Annotated[
        Optional[str],
        Query(description="Creation date upper bound (YYYY-MM-DD, channels/groups)"),
    ] = None,
    is_creator: Annotated[
        Optional[bool],
        Query(description="Only dialogs you created"),
    ] = None,
    has_username: Annotated[
        Optional[bool],
        Query(
            description="Only dialogs with (true) or without (false) a public @username"
        ),
    ] = None,
    is_verified: Annotated[
        Optional[bool],
        Query(description="Only verified entities"),
    ] = None,
    sort: Annotated[
        SortField,
        Query(description="Sort field"),
    ] = SortField.last_message,
    order: Annotated[
        SortOrder,
        Query(description="Sort direction"),
    ] = SortOrder.desc,
    limit: Annotated[
        int,
        Query(ge=1, le=500, description="Page size"),
    ] = 50,
    offset: Annotated[
        int,
        Query(ge=0, description="Number of results to skip (for pagination)"),
    ] = 0,
    client: TelegramClient = Depends(get_telegram_client),
) -> DialogSearchResponse:
    """
    Search and filter all Telegram dialogs.

    Requires X-Telegram-Username header for authentication.
    """
    # --- Pre-validate date params ---
    lm_after = _parse_date(last_message_after) if last_message_after else None
    lm_before = _parse_date(last_message_before) if last_message_before else None
    cr_after = _parse_date(created_after) if created_after else None
    cr_before = _parse_date(created_before) if created_before else None

    if lm_after and lm_before and lm_after >= lm_before:
        raise HTTPException(
            status_code=400,
            detail="last_message_after must be before last_message_before",
        )
    if cr_after and cr_before and cr_after >= cr_before:
        raise HTTPException(
            status_code=400,
            detail="created_after must be before created_before",
        )

    # --- Normalize type filter: treat "me" as "saved" ---
    type_set: set[DialogType] | None = None
    if type:
        type_set = {DialogType.saved if t == DialogType.me else t for t in type}

    # --- Resolve folder param (name → ID) ---
    folder_id: int | None = None
    if folder is not None:
        try:
            folder_id = int(folder)
        except ValueError:
            # Resolve by name
            try:
                result = await client(GetDialogFiltersRequest())
                for f in result.filters:
                    f_title_obj = getattr(f, "title", None)
                    # title can be a TextWithEntities object or None
                    f_title = (
                        getattr(f_title_obj, "text", None) if f_title_obj else None
                    )
                    if f_title and f_title.lower() == folder.lower():
                        folder_id = f.id
                        break
                if folder_id is None:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Folder '{folder}' not found. Use GET /api/v1/folders to list available folders.",
                    )
            except FloodWaitError as e:
                raise HTTPException(
                    status_code=429,
                    detail=f"Telegram rate limit exceeded. Retry after {e.seconds} seconds.",
                    headers={"Retry-After": str(e.seconds)},
                )

    try:
        me = await client.get_me()
        my_id = me.id

        # Determine whether to sort by score (when fuzzy search is active)
        sort_by_score = query is not None and match == MatchMode.fuzzy

        scored_results: list[tuple[float, DialogInfo, object]] = []

        iter_kwargs = {}
        if folder_id is not None:
            iter_kwargs["folder"] = folder_id

        async for dialog in client.iter_dialogs(**iter_kwargs):
            # Always skip Telegram Service Notifications
            if dialog.id == 777000:
                continue

            entity = dialog.entity
            dialog_type = _classify_dialog(dialog, my_id)

            # --- Filter: type ---
            if type_set and dialog_type not in type_set:
                continue

            # --- Filter: archived ---
            if is_archived is not None and dialog.archived != is_archived:
                continue

            # --- Filter: text search ---
            score = 0.0
            if query:
                title = _dialog_title(dialog)
                if match == MatchMode.fuzzy:
                    score = _search_score(title, query)
                    if score < min_score:
                        continue
                else:  # exact
                    if query.lower() not in title.lower():
                        continue

            # --- Filter: message count ---
            msg_count = dialog.message.id if dialog.message else None
            if min_messages is not None and (
                msg_count is None or msg_count < min_messages
            ):
                continue
            if max_messages is not None and (
                msg_count is None or msg_count > max_messages
            ):
                continue

            # --- Filter: participants ---
            p_count = getattr(entity, "participants_count", None)
            if min_participants is not None and (
                p_count is None or p_count < min_participants
            ):
                continue
            if max_participants is not None and (
                p_count is None or p_count > max_participants
            ):
                continue

            # --- Filter: last message date ---
            msg_date = dialog.date
            if lm_after and (msg_date is None or msg_date < lm_after):
                continue
            if lm_before and (msg_date is None or msg_date > lm_before):
                continue

            # --- Filter: creation date ---
            entity_date = getattr(entity, "date", None)
            if cr_after and (entity_date is None or entity_date < cr_after):
                continue
            if cr_before and (entity_date is None or entity_date > cr_before):
                continue

            # --- Filter: is_creator ---
            if is_creator is not None:
                entity_is_creator = getattr(entity, "creator", False) or False
                if entity_is_creator != is_creator:
                    continue

            # --- Filter: has_username ---
            if has_username is not None:
                entity_username = getattr(entity, "username", None)
                if has_username and not entity_username:
                    continue
                if not has_username and entity_username:
                    continue

            # --- Filter: is_verified ---
            if is_verified is not None:
                entity_verified = getattr(entity, "verified", False) or False
                if entity_verified != is_verified:
                    continue

            info = _dialog_to_info(dialog, my_id)
            scored_results.append((score, info, dialog.entity))

        # --- Sort ---
        if sort_by_score:
            # Primary: score descending, then by chosen sort field as tiebreaker
            scored_results.sort(
                key=lambda triple: (-triple[0], _sort_key(triple[1], sort)),
            )
        else:
            reverse = order == SortOrder.desc
            scored_results.sort(
                key=lambda triple: _sort_key(triple[1], sort),
                reverse=reverse,
            )

        all_infos = [info for _, info, _ in scored_results]
        all_entities = [entity for _, _, entity in scored_results]
        total = len(all_infos)

        # --- Paginate ---
        page = all_infos[offset : offset + limit]
        page_entities = all_entities[offset : offset + limit]

        # --- Fetch actual message counts for the page ---
        async def _get_count(entity):
            try:
                result = await client.get_messages(entity, limit=0)
                return result.total
            except Exception:
                return None

        counts = await asyncio.gather(*[_get_count(e) for e in page_entities])
        for info, count in zip(page, counts):
            if count is not None:
                info.message_count = count

        return DialogSearchResponse(
            total=total,
            offset=offset,
            limit=limit,
            results=page,
        )

    except FloodWaitError as e:
        logger.error(
            "Telegram rate limit in search_dialogs: retry-after %ds", e.seconds
        )
        raise HTTPException(
            status_code=429,
            detail=f"Telegram rate limit exceeded. Retry after {e.seconds} seconds.",
            headers={"Retry-After": str(e.seconds)},
        )


@router.get(
    "/folders",
    response_model=List[FolderInfo],
    summary="List all Telegram folders",
    description="Returns all Telegram folders (built-in and custom) for the authenticated user.",
)
async def list_folders(
    client: TelegramClient = Depends(get_telegram_client),
) -> List[FolderInfo]:
    """
    List all Telegram folders.

    Requires X-Telegram-Username header for authentication.
    """
    try:
        result = await client(GetDialogFiltersRequest())

        folders: list[FolderInfo] = []
        for f in result.filters:
            f_id = getattr(f, "id", None)

            if f_id is None:
                # DialogFilterDefault has no id — represent it as the "All Chats" default
                folders.append(FolderInfo(id=0, title="All Chats", is_default=True))
                continue

            # title is a TextWithEntities object on DialogFilter / DialogFilterChatlist
            f_title_obj = getattr(f, "title", None)
            f_title = getattr(f_title_obj, "text", None) if f_title_obj else None

            # IDs 0 and 1 are built-in (All Chats / Archive)
            is_default = f_id in (0, 1)

            title = f_title or (
                "All Chats"
                if f_id == 0
                else "Archive"
                if f_id == 1
                else f"Folder {f_id}"
            )

            folders.append(FolderInfo(id=f_id, title=title, is_default=is_default))

        return folders

    except FloodWaitError as e:
        logger.error("Telegram rate limit in list_folders: retry-after %ds", e.seconds)
        raise HTTPException(
            status_code=429,
            detail=f"Telegram rate limit exceeded. Retry after {e.seconds} seconds.",
            headers={"Retry-After": str(e.seconds)},
        )
