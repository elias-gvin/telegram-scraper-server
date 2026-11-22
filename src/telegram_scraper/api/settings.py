"""Runtime settings: read and partially update; changes apply immediately and go to settings.yaml."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..config import RuntimeSettings, ServerConfig, save_settings
from .deps import get_config

router = APIRouter(prefix="/settings", tags=["settings"])
logger = logging.getLogger(__name__)


class DownloadFileTypesUpdate(BaseModel):
    photos: bool | None = None
    videos: bool | None = None
    voice_messages: bool | None = None
    video_messages: bool | None = None
    stickers: bool | None = None
    gifs: bool | None = None
    audio: bool | None = None
    files: bool | None = None


class SettingsUpdate(BaseModel):
    download_media: bool | None = None
    max_media_size_mb: float | None = Field(
        default=None, ge=0, description="0 or null: no limit"
    )
    telegram_batch_size: int | None = Field(default=None, gt=0)
    refresh_days: int | None = Field(
        default=None, ge=0, description="Days of history each sync re-reads"
    )
    download_file_types: DownloadFileTypesUpdate | None = None


def apply_update(settings: RuntimeSettings, body: SettingsUpdate) -> RuntimeSettings:
    """A copy of `settings` with the fields supplied in `body` changed."""
    update = body.model_dump(exclude_unset=True)
    new = settings.model_copy(deep=True)
    for key in ("download_media", "telegram_batch_size", "refresh_days"):
        if update.get(key) is not None:
            setattr(new, key, update[key])
    if "max_media_size_mb" in update:
        new.max_media_size_mb = update["max_media_size_mb"] or None
    if body.download_file_types is not None:
        types = body.download_file_types.model_dump(
            exclude_unset=True, exclude_none=True
        )
        new.download_file_types = new.download_file_types.model_copy(update=types)
    return new


@router.get("", response_model=RuntimeSettings, summary="Get current server settings")
async def get_settings(config: ServerConfig = Depends(get_config)):
    return config.settings


@router.patch(
    "",
    response_model=RuntimeSettings,
    summary="Update server settings (only supplied fields)",
)
async def update_settings(
    body: SettingsUpdate, config: ServerConfig = Depends(get_config)
):
    update = body.model_dump(exclude_unset=True)
    if not update:
        raise HTTPException(status_code=422, detail="No fields provided for update")

    s = config.settings = apply_update(config.settings, body)

    try:
        save_settings(config)
    except OSError as e:
        logger.exception("Failed to save settings")
        raise HTTPException(
            status_code=500, detail=f"Settings applied but NOT saved to file: {e}"
        ) from e
    logger.info("Settings updated: %s", update)
    return s
