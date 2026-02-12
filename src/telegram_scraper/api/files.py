"""Media file serving API endpoints."""

from fastapi import APIRouter, Path, Query, HTTPException, Depends
from fastapi.responses import FileResponse, JSONResponse
from typing import Annotated
from pathlib import Path as FilePath

from .auth_utils import get_authenticated_user
from ..config import ServerConfig
from ..database import operations
from ..database import get_session, channel_db_paths


router = APIRouter(tags=["files"])


# Global config (will be set by server.py)
_config: ServerConfig = None


def set_config(config: ServerConfig):
    """Set global config for files module."""
    global _config
    _config = config


def find_media_by_uuid(media_uuid: str) -> dict:
    """
    Search for media file by UUID across all channel databases.

    Returns:
        Dict with media info or raises HTTPException if not found
    """
    if _config is None:
        raise HTTPException(
            status_code=500, detail="Server configuration not initialized"
        )

    # Search through all channel databases
    channels_dir = _config.channels_dir

    if not channels_dir.exists():
        raise HTTPException(status_code=404, detail="Media not found")

    # Iterate through channel directories
    for channel_dir in channels_dir.iterdir():
        if not channel_dir.is_dir():
            continue

        # Use canonical path helper to get database file
        paths = channel_db_paths(channels_dir, channel_dir.name)
        if not paths.db_file.exists():
            continue

        try:
            with get_session(paths.db_file, check_same_thread=True) as session:
                media_info = operations.get_media_info_by_uuid(session, media_uuid)

                if media_info:
                    return media_info
        except Exception:
            # Skip databases with errors
            continue

    raise HTTPException(status_code=404, detail="Media not found")


@router.get(
    "/files/{file_uuid}",
    summary="Download media file by UUID",
    description="""
    Download a media file by its UUID, or retrieve its local path.
    
    The UUID is provided in message responses (media_uuid field).
    
    Query parameters:
    - `metadata_only=true` — instead of downloading the file, returns a JSON
      object with file metadata (path, filename, size). Useful when client
      and server run on the same machine.
    
    Example:
    - `/api/v1/files/a1b2c3d4-e5f6-7890-abcd-ef1234567890`
    - `/api/v1/files/a1b2c3d4-e5f6-7890-abcd-ef1234567890?metadata_only=true`
    """,
)
async def get_file(
    file_uuid: Annotated[str, Path(description="Media file UUID")],
    metadata_only: Annotated[
        bool,
        Query(
            description="If true, return file metadata (path, filename, size) as JSON instead of the file content"
        ),
    ] = False,
    username: str = Depends(get_authenticated_user),
):
    """
    Download media file by UUID.

    Requires X-Telegram-Username header for authentication.
    Pass ?metadata_only=true to get file metadata instead of the file content.
    """
    # Find media in databases
    media_info = find_media_by_uuid(file_uuid)

    raw_path = media_info.get("file_path")
    if not raw_path:
        raise HTTPException(
            status_code=404,
            detail="Media file was not downloaded (skipped by server settings)",
        )

    file_path = FilePath(raw_path)

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Media file not found on disk")

    # Return just metadata if requested (for same-machine clients)
    if metadata_only:
        return JSONResponse(
            content={
                "file_path": str(file_path.resolve()),
                "original_filename": media_info.get("original_filename"),
                "size": file_path.stat().st_size,
            }
        )

    # Determine media type for response (use mimetypes to guess from file extension)
    import mimetypes

    mime_type, _ = mimetypes.guess_type(str(file_path))
    media_type = mime_type or "application/octet-stream"

    return FileResponse(
        path=str(file_path),
        filename=file_path.name,
        media_type=media_type,
    )
