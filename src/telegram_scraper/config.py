"""Configuration management for Telegram Scraper API Server."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field
from dotenv import load_dotenv
import yaml


@dataclass
class ServerConfig:
    """Server configuration with defaults."""

    # Telegram API credentials
    api_id: str
    api_hash: str

    # Download settings
    download_media: bool = True
    max_media_size_mb: Optional[float] = None  # None = no limit
    telegram_batch_size: int = 100  # Internal download chunk size

    # Storage
    output_path: Path = field(default_factory=lambda: Path("./data/output"))
    sessions_path: Path = field(default_factory=lambda: Path("./data/sessions"))

    # Server settings
    host: str = "0.0.0.0"
    port: int = 8000

    def __post_init__(self):
        """Convert string paths to Path objects."""
        if isinstance(self.output_path, str):
            self.output_path = Path(self.output_path)
        if isinstance(self.sessions_path, str):
            self.sessions_path = Path(self.sessions_path)

        # Create directories if they don't exist
        self.output_path.mkdir(parents=True, exist_ok=True)
        self.sessions_path.mkdir(parents=True, exist_ok=True)


def load_config_from_yaml(config_path: Path) -> dict:
    """Load configuration from YAML file."""
    if not config_path.exists():
        return {}

    with open(config_path, "r") as f:
        return yaml.safe_load(f) or {}


def load_config_from_env() -> dict:
    """Load configuration from environment variables."""
    config = {}

    # Telegram credentials
    if api_id := os.getenv("TELEGRAM_API_ID"):
        config["api_id"] = api_id
    if api_hash := os.getenv("TELEGRAM_API_HASH"):
        config["api_hash"] = api_hash

    # Download settings
    if download_media := os.getenv("DOWNLOAD_MEDIA"):
        config["download_media"] = download_media.lower() in ("true", "1", "yes")
    if max_media_size := os.getenv("MAX_MEDIA_SIZE_MB"):
        try:
            config["max_media_size_mb"] = float(max_media_size)
        except ValueError:
            pass
    if batch_size := os.getenv("TELEGRAM_BATCH_SIZE"):
        try:
            config["telegram_batch_size"] = int(batch_size)
        except ValueError:
            pass

    # Storage paths
    if output_path := os.getenv("OUTPUT_PATH"):
        config["output_path"] = output_path
    if sessions_path := os.getenv("SESSIONS_PATH"):
        config["sessions_path"] = sessions_path

    # Server settings
    if host := os.getenv("SERVER_HOST"):
        config["host"] = host
    if port := os.getenv("SERVER_PORT"):
        try:
            config["port"] = int(port)
        except ValueError:
            pass

    return config


def load_config(
    config_path: Optional[Path] = None, cli_overrides: Optional[dict] = None
) -> ServerConfig:
    """
    Load configuration with priority: CLI > ENV > YAML > Defaults

    Args:
        config_path: Path to YAML config file
        cli_overrides: Dict of CLI parameter overrides

    Returns:
        ServerConfig instance
    """
    load_dotenv()

    # Start with empty dict
    config_data = {}

    # 1. Load from YAML (lowest priority)
    if config_path:
        yaml_config = load_config_from_yaml(config_path)
        config_data.update(yaml_config)

    # 2. Override with environment variables
    env_config = load_config_from_env()
    config_data.update(env_config)

    # 3. Override with CLI parameters (highest priority)
    if cli_overrides:
        config_data.update({k: v for k, v in cli_overrides.items() if v is not None})

    # Validate required fields
    if "api_id" not in config_data or "api_hash" not in config_data:
        raise ValueError(
            "Missing required configuration: api_id and api_hash must be provided "
            "via config file, environment variables, or CLI parameters"
        )

    return ServerConfig(**config_data)
