"""Server settings API endpoints.

Allows clients to read and update runtime configuration parameters
such as download_media, max_media_size_mb, and telegram_batch_size.
Changes are applied immediately and saved to settings.yaml.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .auth_utils import get_authenticated_user
from .deps import get_config
from ..config import ServerConfig, save_settings


router = APIRouter(prefix="/settings", tags=["settings"])
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class DownloadFileTypesResponse(BaseModel):
    """Per-file-type download toggles (API response view)."""

    photos: bool
    videos: bool
    voice_messages: bool
    video_messages: bool
    stickers: bool
    gifs: bool
    files: bool


class DownloadFileTypesUpdate(BaseModel):
    """Partial update for per-file-type download toggles."""

    photos: Optional[bool] = None
    videos: Optional[bool] = None
    voice_messages: Optional[bool] = None
    video_messages: Optional[bool] = None
    stickers: Optional[bool] = None
    gifs: Optional[bool] = None
    files: Optional[bool] = None


class SettingsResponse(BaseModel):
    """Current server settings (read-only view)."""

    download_media: bool
    max_media_size_mb: Optional[float]
    telegram_batch_size: int
    repair_media: bool
    download_file_types: DownloadFileTypesResponse


class SettingsUpdate(BaseModel):
    """
    Partial update for server settings.

    Only the fields that are provided will be updated.
    """

    download_media: Optional[bool] = Field(
        default=None, description="Enable/disable media download"
    )
    max_media_size_mb: Optional[float] = Field(
        default=None,
        description="Maximum media file size in MB (0 or null for no limit)",
        ge=0,
    )
    telegram_batch_size: Optional[int] = Field(
        default=None,
        description="Internal batch size for downloading from Telegram",
        gt=0,
    )
    repair_media: Optional[bool] = Field(
        default=None,
        description="If true, re-download media that was previously skipped due to parameter restrictions",
    )
    download_file_types: Optional[DownloadFileTypesUpdate] = Field(
        default=None,
        description="Per-file-type download toggles (photos, videos, voice_messages, video_messages, stickers, gifs, files)",
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=SettingsResponse,
    summary="Get current server settings",
    description="Returns the current values of configurable server parameters.",
)
async def get_settings(
    username: str = Depends(get_authenticated_user),
    config: ServerConfig = Depends(get_config),
):
    s = config.settings
    return SettingsResponse(
        download_media=s.download_media,
        max_media_size_mb=s.max_media_size_mb,
        telegram_batch_size=s.telegram_batch_size,
        repair_media=s.repair_media,
        download_file_types=DownloadFileTypesResponse.model_validate(
            s.download_file_types.model_dump()
        ),
    )


@router.patch(
    "",
    response_model=SettingsResponse,
    summary="Update server settings",
    description="""
    Partially update server settings. Only supplied fields are changed.

    Changes take effect immediately and are saved to settings.yaml.

    Accepted fields:
    - `download_media` — enable / disable media download
    - `max_media_size_mb` — max media size in MB (`0` or `null` → no limit)
    - `telegram_batch_size` — batch size for Telegram downloads (must be > 0)
    - `repair_media` — if true, re-download previously skipped media when settings now allow it
    """,
)
async def update_settings(
    body: SettingsUpdate,
    username: str = Depends(get_authenticated_user),
    config: ServerConfig = Depends(get_config),
):
    # Check that at least one field is provided
    update_data = body.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(status_code=422, detail="No fields provided for update")

    # Apply changes to in-memory config
    s = config.settings
    if body.download_media is not None:
        s.download_media = body.download_media

    if "max_media_size_mb" in update_data:
        # Treat 0 as "no limit"
        val = body.max_media_size_mb
        s.max_media_size_mb = None if (val is None or val == 0) else val

    if body.telegram_batch_size is not None:
        s.telegram_batch_size = body.telegram_batch_size

    if body.repair_media is not None:
        s.repair_media = body.repair_media

    if body.download_file_types is not None:
        update_ft = body.download_file_types.model_dump(
            exclude_unset=True, exclude_none=True
        )
        s.download_file_types = s.download_file_types.model_copy(update=update_ft)

    # Persist to settings.yaml
    try:
        save_settings(config)
        logger.info("Settings saved to %s", config.settings_path)
    except Exception:
        logger.exception("Failed to save settings to file")

    logger.info("Settings updated by %s: %s", username, update_data)

    s = config.settings
    return SettingsResponse(
        download_media=s.download_media,
        max_media_size_mb=s.max_media_size_mb,
        telegram_batch_size=s.telegram_batch_size,
        repair_media=s.repair_media,
        download_file_types=DownloadFileTypesResponse.model_validate(
            s.download_file_types.model_dump()
        ),
    )
