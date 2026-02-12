"""Path utilities for dialog databases and media."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DialogDbPaths:
    """Canonical paths for a dialog's database and media directory."""

    dialog_dir: Path
    db_file: Path
    media_dir: Path


def dialog_db_paths(output_dir: Path, dialog_id: str | int) -> DialogDbPaths:
    """
    Compute canonical on-disk paths for a dialog's DB and media.

    Layout:
      <output_dir>/<dialog_id>/<dialog_id>.db
      <output_dir>/<dialog_id>/media/

    Args:
        output_dir: Base output directory
        dialog_id: Dialog ID

    Returns:
        DialogDbPaths with all canonical paths for the dialog
    """
    output_dir = Path(output_dir)
    dialog_dir = output_dir / str(dialog_id)
    db_file = dialog_dir / f"{dialog_id}.db"
    media_dir = dialog_dir / "media"
    return DialogDbPaths(dialog_dir=dialog_dir, db_file=db_file, media_dir=media_dir)


def ensure_dialog_directories(output_dir: Path, dialog_id: str | int) -> DialogDbPaths:
    """
    Create the dialog directory structure if it doesn't exist.

    Args:
        output_dir: Base output directory
        dialog_id: Dialog ID

    Returns:
        DialogDbPaths with all canonical paths for the dialog
    """
    paths = dialog_db_paths(output_dir, dialog_id)
    paths.dialog_dir.mkdir(parents=True, exist_ok=True)
    paths.media_dir.mkdir(parents=True, exist_ok=True)
    return paths
