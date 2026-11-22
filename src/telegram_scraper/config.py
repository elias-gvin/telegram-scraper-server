"""Configuration management for Telegram Scraper API Server.

Configuration sources (no overlap):
- api_id, api_hash  → env vars / .env only
- data_dir, account → CLI --data-dir (default ./data) / --account
- host, port        → CLI --host / --port
- API token         → {data_dir}/token (created on first start)
- settings file     → CLI --settings or {data_dir}/settings.yaml
- download_media, max_media_size_mb, telegram_batch_size, refresh_days,
  download_file_types → settings.yaml (editable via the /settings API)
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class MediaCategory(StrEnum):
    PHOTOS = "photos"
    VIDEOS = "videos"
    VOICE_MESSAGES = "voice_messages"
    VIDEO_MESSAGES = "video_messages"
    STICKERS = "stickers"
    GIFS = "gifs"
    AUDIO = "audio"
    FILES = "files"


class DownloadFileTypes(BaseModel):
    """Per-file-type download toggles."""

    photos: bool = True
    videos: bool = True
    voice_messages: bool = True
    video_messages: bool = True
    stickers: bool = True
    gifs: bool = True
    audio: bool = True
    files: bool = True


class RuntimeSettings(BaseModel):
    """Runtime-tunable settings (from settings.yaml)."""

    download_media: bool = True
    max_media_size_mb: float | None = Field(default=20, ge=0)  # None/0 = no limit
    telegram_batch_size: int = Field(default=100, gt=0)
    refresh_days: int = Field(default=30, ge=0)  # sync re-reads this many recent days
    download_file_types: DownloadFileTypes = Field(default_factory=DownloadFileTypes)


@dataclass
class ServerConfig:
    """Server configuration. One server = one Telegram account = one data directory."""

    # Telegram API credentials (from env vars)
    api_id: str
    api_hash: str

    # From CLI
    data_dir: Path = field(default_factory=lambda: Path("./data"))
    account: str = "default"  # session name: {data_dir}/sessions/{account}.session
    host: str = "127.0.0.1"
    port: int = 8000

    # Runtime-tunable settings (from settings.yaml)
    settings: RuntimeSettings = field(default_factory=RuntimeSettings)
    settings_path: Path | None = field(default=None, repr=False)

    # Bearer token every API request must carry (from {data_dir}/token when None)
    token: str | None = field(default=None, repr=False)

    def __post_init__(self):
        self.data_dir = Path(self.data_dir)
        if self.settings_path is not None:
            self.settings_path = Path(self.settings_path)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.media_dir.mkdir(parents=True, exist_ok=True)
        if self.token is None:
            self.token = load_or_create_token(self.data_dir / "token")

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / "sessions"

    @property
    def session_path(self) -> Path:
        return self.sessions_dir / self.account

    @property
    def archive_path(self) -> Path:
        return self.data_dir / "archive.db"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"


def load_or_create_token(path: Path) -> str:
    """Read the API token, creating a random one (readable by the owner only) on first start."""
    if not path.exists():
        path.write_text(secrets.token_urlsafe(32) + "\n")
        path.chmod(0o600)
    return path.read_text().strip()


def load_credentials_from_env() -> dict:
    """
    Load Telegram API credentials from environment variables / .env file.

    Returns:
        Dict with api_id and api_hash (if found).

    Raises:
        ValueError if credentials are missing.
    """
    load_dotenv()

    creds = {}
    if api_id := os.getenv("TELEGRAM_API_ID"):
        creds["api_id"] = api_id
    if api_hash := os.getenv("TELEGRAM_API_HASH"):
        creds["api_hash"] = api_hash

    if "api_id" not in creds or "api_hash" not in creds:
        raise ValueError(
            "Missing Telegram API credentials.\n"
            "Set TELEGRAM_API_ID and TELEGRAM_API_HASH as environment variables\n"
            "or in a .env file in the project root.\n\n"
            "Get credentials at https://my.telegram.org/apps"
        )

    return creds


def load_settings(settings_path: Path) -> RuntimeSettings:
    """Load runtime-tunable settings from a YAML file."""
    with open(settings_path, "r") as f:
        data = yaml.safe_load(f) or {}
    return RuntimeSettings.model_validate(data)


def save_settings(config: ServerConfig) -> None:
    """
    Persist runtime-tunable settings to settings.yaml.

    Writes only the tunable settings — no secrets, no infrastructure.
    """
    if not config.settings_path:
        logger.warning("No settings_path set — cannot persist settings")
        return

    partial = config.settings_path.with_suffix(".yaml.part")
    with open(partial, "w") as f:
        f.write("# Telegram Scraper — Runtime Settings\n")
        f.write("# These values can be changed via the /settings API endpoint.\n\n")
        yaml.dump(
            config.settings.model_dump(), f, default_flow_style=False, sort_keys=False
        )
    os.replace(partial, config.settings_path)


def resolve_settings_file(
    data_dir: Path, cli_settings_path: Path | None = None
) -> Path:
    """
    Determine which settings.yaml to use and return the canonical path
    (always inside the data directory).

    Behaviour:
    - If --settings is given: copy that file into data_dir/settings.yaml
      (warn if overwriting). Return data_dir/settings.yaml.
    - If --settings is NOT given: look for data_dir/settings.yaml.
      If missing, create one with defaults. Return data_dir/settings.yaml.
    """
    canonical = data_dir / "settings.yaml"
    data_dir.mkdir(parents=True, exist_ok=True)

    if cli_settings_path is not None:
        # User provided an explicit settings file
        cli_settings_path = Path(cli_settings_path)
        if not cli_settings_path.exists():
            raise ValueError(
                f"Settings file not found: {cli_settings_path}\n"
                f"Provide a valid path or omit --settings to use defaults."
            )

        if canonical.exists():
            logger.warning(
                "Overwriting existing %s with %s", canonical, cli_settings_path
            )

        shutil.copy2(cli_settings_path, canonical)
        logger.info("Settings imported from %s → %s", cli_settings_path, canonical)
    else:
        # No --settings flag
        if not canonical.exists():
            # First launch: create settings.yaml with defaults
            logger.info(
                "No settings.yaml found in %s — creating with defaults", data_dir
            )
            with open(canonical, "w") as f:
                f.write(
                    "# Telegram Scraper — Runtime Settings\n"
                    "# These values can be changed via the /settings API endpoint.\n\n"
                )
                yaml.dump(
                    RuntimeSettings().model_dump(),
                    f,
                    default_flow_style=False,
                    sort_keys=False,
                )

    logger.info("Using settings file: %s", canonical)
    return canonical
