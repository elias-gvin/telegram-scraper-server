"""Media file serving API endpoints."""

from fastapi import APIRouter, Path, HTTPException, Depends
from fastapi.responses import FileResponse
from typing import Annotated
from pathlib import Path as FilePath

from .auth import get_authenticated_user
from ..config import ServerConfig
from .. import db_helper


router = APIRouter(prefix="/api/v1", tags=["files"])


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
        raise HTTPException(status_code=500, detail="Server configuration not initialized")
    
    # Search through all channel databases
    output_path = _config.output_path
    
    if not output_path.exists():
        raise HTTPException(status_code=404, detail="Media not found")
    
    # Iterate through channel directories
    for channel_dir in output_path.iterdir():
        if not channel_dir.is_dir():
            continue
        
        # Check for database file
        db_file = channel_dir / f"{channel_dir.name}.db"
        if not db_file.exists():
            continue
        
        try:
            conn = db_helper.open_db_file(db_file, row_factory=False)
            media_info = db_helper.get_media_info_by_uuid(conn, media_uuid)
            conn.close()
            
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
    Download a media file by its UUID.
    
    The UUID is provided in message responses (media_uuid field).
    
    Example:
    - `/api/v1/files/a1b2c3d4-e5f6-7890-abcd-ef1234567890`
    """
)
async def get_file(
    file_uuid: Annotated[str, Path(description="Media file UUID")],
    username: str = Depends(get_authenticated_user),
):
    """
    Download media file by UUID.
    
    Requires X-Telegram-Username header for authentication.
    """
    # Find media in databases
    media_info = find_media_by_uuid(file_uuid)
    
    file_path = FilePath(media_info["file_path"])
    
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Media file not found on disk")
    
    # Determine media type for response
    media_type = media_info.get("mime_type") or "application/octet-stream"
    
    return FileResponse(
        path=str(file_path),
        filename=file_path.name,
        media_type=media_type,
    )

