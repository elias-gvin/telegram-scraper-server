from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional, Sequence

from . import db_helper


ExportFormat = Literal["csv", "json"]


@dataclass(frozen=True)
class ExportResult:
    channel_id: str
    db_file: Path
    output_file: Path
    row_count: int


def _find_channel_db_files(output_dir: Path, channel_ids: Optional[Sequence[str]]) -> list[tuple[str, Path]]:
    """
    Return a list of (channel_id, db_file) pairs.

    This project stores one DB per channel under:
      <output_dir>/<channel_id>/<channel_id>.db

    Also supports passing <output_dir> as a channel dir itself.
    """
    output_dir = Path(output_dir)

    if channel_ids:
        pairs: list[tuple[str, Path]] = []
        for cid in channel_ids:
            paths = db_helper.channel_db_paths(output_dir, str(cid))
            pairs.append((str(cid), paths.db_file))
        return pairs

    # If output_dir looks like a channel dir itself, treat it as a single-channel export.
    candidate_db = output_dir / f"{output_dir.name}.db"
    if candidate_db.exists():
        return [(output_dir.name, candidate_db)]

    pairs = []
    if not output_dir.exists():
        return pairs

    for child in output_dir.iterdir():
        if not child.is_dir():
            continue
        cid = child.name
        db_file = child / f"{cid}.db"
        if db_file.exists():
            pairs.append((cid, db_file))
    return pairs


def export_chat_history(
    *,
    output_dir: Path,
    export_format: ExportFormat,
    channel_ids: Optional[Sequence[str]] = None,
    out_dir: Optional[Path] = None,
    order_by: str = "date",
    json_indent: Optional[int] = 2,
    batch_size: int = 1000,
) -> list[ExportResult]:
    """
    Export chat history from SQLite DB(s) into CSV or JSON.

    - Writes **one file per channel**.
    - If channel_ids is None, exports **all channels found in output_dir**.

    Output file placement:
      - If out_dir is provided: <out_dir>/<channel_id>.<format>
      - Else: next to the DB: <channel_dir>/<channel_id>.<format>
    """
    if export_format not in ("csv", "json"):
        raise ValueError("export_format must be 'csv' or 'json'")

    results: list[ExportResult] = []
    pairs = _find_channel_db_files(Path(output_dir), channel_ids)
    if not pairs:
        return results

    for channel_id, db_file in pairs:
        if not db_file.exists():
            raise FileNotFoundError(f"DB file not found for channel_id={channel_id}: {db_file}")

        channel_dir = db_file.parent
        target_dir = Path(out_dir) if out_dir else channel_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        output_file = target_dir / f"{channel_id}.{export_format}"

        conn = db_helper.open_db_file(db_file, row_factory=True)
        try:
            if export_format == "csv":
                row_count = db_helper.export_messages(
                    conn,
                    output_file=output_file,
                    export_format="csv",
                    order_by=order_by,
                    batch_size=batch_size,
                    validate_only=True,
                )
            else:
                row_count = db_helper.export_messages(
                    conn,
                    output_file=output_file,
                    export_format="json",
                    order_by=order_by,
                    batch_size=batch_size,
                    json_indent=json_indent,
                    validate_only=True,
                )

            results.append(
                ExportResult(
                    channel_id=channel_id,
                    db_file=db_file,
                    output_file=output_file,
                    row_count=row_count,
                )
            )
        finally:
            conn.close()

    return results

if __name__ == "__main__":
    main()

