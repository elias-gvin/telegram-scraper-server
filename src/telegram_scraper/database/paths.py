"""Path utilities for channel databases and media."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ChannelDbPaths:
    """Canonical paths for a channel's database and media directory."""

    channel_dir: Path
    db_file: Path
    media_dir: Path


def channel_db_paths(output_dir: Path, channel_id: str | int) -> ChannelDbPaths:
    """
    Compute canonical on-disk paths for a channel's DB and media.

    Layout:
      <output_dir>/<channel_id>/<channel_id>.db
      <output_dir>/<channel_id>/media/

    Args:
        output_dir: Base output directory
        channel_id: Channel ID

    Returns:
        ChannelDbPaths with all canonical paths for the channel
    """
    output_dir = Path(output_dir)
    channel_dir = output_dir / str(channel_id)
    db_file = channel_dir / f"{channel_id}.db"
    media_dir = channel_dir / "media"
    return ChannelDbPaths(channel_dir=channel_dir, db_file=db_file, media_dir=media_dir)


def ensure_channel_directories(
    output_dir: Path, channel_id: str | int
) -> ChannelDbPaths:
    """
    Create the channel directory structure if it doesn't exist.

    Args:
        output_dir: Base output directory
        channel_id: Channel ID

    Returns:
        ChannelDbPaths with all canonical paths for the channel
    """
    paths = channel_db_paths(output_dir, channel_id)
    paths.channel_dir.mkdir(parents=True, exist_ok=True)
    paths.media_dir.mkdir(parents=True, exist_ok=True)
    return paths

