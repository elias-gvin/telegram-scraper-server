"""Shared FastAPI dependencies for all API modules."""

from fastapi import HTTPException, Request

from ..config import ServerConfig


def get_config(request: Request) -> ServerConfig:
    """Extract ServerConfig from app state.

    The config is set once during startup in ``create_app`` via
    ``app.state.config = config``.  Every endpoint that needs the
    config should declare ``config: ServerConfig = Depends(get_config)``.
    """
    config: ServerConfig | None = getattr(request.app.state, "config", None)
    if config is None:
        raise HTTPException(
            status_code=500, detail="Server configuration not initialized"
        )
    return config

