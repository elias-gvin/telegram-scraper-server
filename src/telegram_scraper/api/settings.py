"""Server settings API endpoints.

Allows clients to read and update runtime configuration parameters
such as download_media, max_media_size_mb, and telegram_batch_size.
Changes are applied immediately. Pass ``persist=true`` to also save to config.yaml.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth_utils import get_authenticated_user
from ..config import ServerConfig, save_config_to_yaml


router = APIRouter(prefix="/settings", tags=["settings"])
logger = logging.getLogger(__name__)


# Global config (will be set by server.py)
_config: ServerConfig = None


def set_config(config: ServerConfig):
    """Set global config for settings module."""
    global _config
    _config = config


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class SettingsResponse(BaseModel):
    """Current server settings (read-only view)."""

    download_media: bool
    max_media_size_mb: Optional[float]
    telegram_batch_size: int


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
):
    if _config is None:
        raise HTTPException(
            status_code=500, detail="Server configuration not initialized"
        )

    return SettingsResponse(
        download_media=_config.download_media,
        max_media_size_mb=_config.max_media_size_mb,
        telegram_batch_size=_config.telegram_batch_size,
    )


@router.patch(
    "",
    response_model=SettingsResponse,
    summary="Update server settings",
    description="""
    Partially update server settings. Only supplied fields are changed.

    Changes take effect immediately. Pass `?persist=true` as a query
    parameter to also write changes to `config.yaml` so they survive
    server restarts.

    Accepted body fields:
    - `download_media` — enable / disable media download
    - `max_media_size_mb` — max media size in MB (`0` or `null` → no limit)
    - `telegram_batch_size` — batch size for Telegram downloads (must be > 0)
    """,
)
async def update_settings(
    body: SettingsUpdate,
    persist: bool = Query(
        default=False,
        description="If true, also save changes to config.yaml so they survive restarts",
    ),
    username: str = Depends(get_authenticated_user),
):
    if _config is None:
        raise HTTPException(
            status_code=500, detail="Server configuration not initialized"
        )

    # Check that at least one field is provided
    update_data = body.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(
            status_code=422, detail="No fields provided for update"
        )

    # Apply changes to in-memory config
    if body.download_media is not None:
        _config.download_media = body.download_media

    if "max_media_size_mb" in update_data:
        # Treat 0 as "no limit"
        val = body.max_media_size_mb
        _config.max_media_size_mb = None if (val is None or val == 0) else val

    if body.telegram_batch_size is not None:
        _config.telegram_batch_size = body.telegram_batch_size

    # Persist to YAML only when explicitly requested
    if persist and _config.config_path:
        try:
            save_config_to_yaml(_config)
            logger.info("Settings persisted to %s", _config.config_path)
        except Exception:
            logger.exception("Failed to persist settings to config file")

    logger.info("Settings updated by %s: %s", username, update_data)

    return SettingsResponse(
        download_media=_config.download_media,
        max_media_size_mb=_config.max_media_size_mb,
        telegram_batch_size=_config.telegram_batch_size,
    )

