"""Configuration management for Telegram Scraper API Server.

Configuration sources (no overlap):
- api_id, api_hash  → env vars / .env only
- data_dir          → CLI --data-dir (default ./data)
- host, port        → CLI --host / --port
- settings file     → CLI --settings or {data_dir}/settings.yaml
- download_media, max_media_size_mb, telegram_batch_size → settings.yaml
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

from dotenv import load_dotenv
import yaml


logger = logging.getLogger(__name__)

# Default values for runtime-tunable settings
SETTINGS_DEFAULTS = {
    "download_media": True,
    "max_media_size_mb": 20,
    "telegram_batch_size": 100,
}


@dataclass
class ServerConfig:
    """Server configuration."""

    # Telegram API credentials (from env vars)
    api_id: str
    api_hash: str

    # Data directory (from CLI)
    data_dir: Path = field(default_factory=lambda: Path("./data"))

    # Server settings (from CLI)
    host: str = "0.0.0.0"
    port: int = 8000

    # Runtime-tunable settings (from settings.yaml)
    download_media: bool = True
    max_media_size_mb: Optional[float] = 20  # None = no limit
    telegram_batch_size: int = 100

    # Internal: path to the active settings.yaml file
    settings_path: Optional[Path] = field(default=None, repr=False)

    def __post_init__(self):
        """Convert string paths and create directories."""
        if isinstance(self.data_dir, str):
            self.data_dir = Path(self.data_dir)
        if isinstance(self.settings_path, str):
            self.settings_path = Path(self.settings_path)

        # Create directory structure
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.channels_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    @property
    def channels_dir(self) -> Path:
        """Directory for per-channel databases and media."""
        return self.data_dir / "channels"

    @property
    def sessions_dir(self) -> Path:
        """Directory for Telegram session files."""
        return self.data_dir / "sessions"


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


def load_settings(settings_path: Path) -> dict:
    """Load runtime-tunable settings from a YAML file."""
    with open(settings_path, "r") as f:
        data = yaml.safe_load(f) or {}

    # Only extract known tunable keys
    result = {}
    if "download_media" in data:
        result["download_media"] = bool(data["download_media"])
    if "max_media_size_mb" in data:
        val = data["max_media_size_mb"]
        result["max_media_size_mb"] = None if val is None else float(val)
    if "telegram_batch_size" in data:
        result["telegram_batch_size"] = int(data["telegram_batch_size"])

    return result


def save_settings(config: ServerConfig) -> None:
    """
    Persist runtime-tunable settings to settings.yaml.

    Writes only the 3 tunable params — no secrets, no infrastructure.
    """
    if not config.settings_path:
        logger.warning("No settings_path set — cannot persist settings")
        return

    data = {
        "download_media": config.download_media,
        "max_media_size_mb": config.max_media_size_mb,
        "telegram_batch_size": config.telegram_batch_size,
    }

    with open(config.settings_path, "w") as f:
        f.write("# Telegram Scraper — Runtime Settings\n")
        f.write("# These values can be changed via the /settings API endpoint.\n\n")
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def resolve_settings_file(
    data_dir: Path, cli_settings_path: Optional[Path] = None
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
            # Write defaults
            data = dict(SETTINGS_DEFAULTS)
            with open(canonical, "w") as f:
                f.write(
                    "# Telegram Scraper — Runtime Settings\n"
                    "# These values can be changed via the /settings API endpoint.\n\n"
                )
                yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    logger.info("Using settings file: %s", canonical)
    return canonical
