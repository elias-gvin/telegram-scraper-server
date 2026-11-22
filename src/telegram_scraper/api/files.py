"""Archived media files."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from ..archive import Archive
from ..config import ServerConfig
from .deps import get_archive, get_config

router = APIRouter(tags=["files"])


@router.get(
    "/files/{dialog_id}/{message_id}", summary="Download the media file of a message"
)
async def get_file(
    dialog_id: int,
    message_id: int,
    metadata_only: Annotated[
        bool, Query(description="Return {file_path, size} instead of the file")
    ] = False,
    archive: Archive = Depends(get_archive),
    config: ServerConfig = Depends(get_config),
):
    row = archive.media(dialog_id, message_id)
    if row is None:
        raise HTTPException(
            status_code=404, detail="No archived media for this message"
        )
    if row["status"] != "downloaded":
        raise HTTPException(
            status_code=404, detail=f"Media not downloaded: {row['status']}"
        )
    path = (config.media_dir / row["path"]).resolve()
    if not path.is_relative_to(config.media_dir.resolve()) or not path.is_file():
        raise HTTPException(status_code=404, detail="Media file missing on disk")
    if metadata_only:
        return {"file_path": str(path), "size": path.stat().st_size}
    return FileResponse(path, filename=path.name)
