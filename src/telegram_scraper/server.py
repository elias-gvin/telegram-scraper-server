"""FastAPI server for Telegram Scraper."""

import logging
from pathlib import Path
from typing import Optional

import click
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import (
    ServerConfig,
    load_credentials_from_env,
    load_settings,
    resolve_settings_file,
)
from fastapi import APIRouter
from .api import dialogs_router, history_router, files_router, auth_router, settings_router, API_PREFIX
from .api import auth_utils as api_auth
from .api import auth as api_qr_auth
from .api import history as api_history
from .api import files as api_files
from .api import settings as api_settings

from contextlib import asynccontextmanager


# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for FastAPI application.
    """
    yield
    logger.info("Shutting down server, cleaning up Telegram clients...")
    await api_qr_auth.cleanup_qr_sessions()
    await api_auth.cleanup_clients()
    logger.info("Cleanup complete")


def create_app(config: ServerConfig) -> FastAPI:
    """
    Create and configure FastAPI application.

    Args:
        config: Server configuration

    Returns:
        Configured FastAPI app
    """
    app = FastAPI(
        title="Telegram Scraper API",
        description="API for scraping and caching Telegram messages with smart cache management",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # CORS middleware (adjust origins as needed)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # In production, specify actual origins
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Set config in API modules
    api_auth.set_config(config)
    api_qr_auth.set_config(config)
    api_history.set_config(config)
    api_files.set_config(config)
    api_settings.set_config(config)

    # Include routers under versioned prefix
    api_router = APIRouter(prefix=API_PREFIX)
    api_router.include_router(dialogs_router)
    api_router.include_router(history_router)
    api_router.include_router(files_router)
    api_router.include_router(auth_router)
    api_router.include_router(settings_router)
    app.include_router(api_router)

    @app.get("/", tags=["root"])
    async def root():
        """Root endpoint with API information."""
        return {
            "name": "Telegram Scraper API",
            "version": "1.0.0",
            "docs": "/docs",
            "endpoints": {
                "auth_qr_start": f"{API_PREFIX}/auth/qr",
                "auth_qr_status": f"{API_PREFIX}/auth/qr/{{token}}",
                "search_dialogs": f"{API_PREFIX}/search/dialogs",
                "folders": f"{API_PREFIX}/folders",
                "history": f"{API_PREFIX}/history/{{channel_id}}",
                "files": f"{API_PREFIX}/files/{{file_uuid}}",
                "settings": f"{API_PREFIX}/settings",
            },
        }

    @app.get("/health", tags=["health"])
    async def health():
        """Health check endpoint."""
        return {"status": "healthy"}

    return app


@click.command()
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default="./data",
    help="Data directory for sessions, channels, and settings (default: ./data)",
)
@click.option(
    "--host",
    type=str,
    default="0.0.0.0",
    help="Server host (default: 0.0.0.0)",
)
@click.option(
    "--port",
    type=int,
    default=8000,
    help="Server port (default: 8000)",
)
@click.option(
    "--settings",
    "settings_file",
    type=click.Path(path_type=Path),
    default=None,
    help="Path to a settings.yaml to import into the data directory (overrides existing)",
)
def main(
    data_dir: Path,
    host: str,
    port: int,
    settings_file: Optional[Path],
):
    """Telegram Scraper API Server.

    Start the FastAPI server for the Telegram Scraper.

    \b
    Configuration:
    - Telegram credentials: set TELEGRAM_API_ID and TELEGRAM_API_HASH
      as environment variables or in a .env file.
    - Runtime settings (download_media, max_media_size_mb, telegram_batch_size):
      stored in {data-dir}/settings.yaml, editable via the /settings API.
    - Use --settings to import a settings.yaml template on first run
      or to reset settings.
    """
    # 1. Load credentials from env
    try:
        creds = load_credentials_from_env()
    except ValueError as e:
        raise click.ClickException(str(e))

    # 2. Resolve data directory and settings file
    data_dir = Path(data_dir)
    try:
        settings_path = resolve_settings_file(data_dir, settings_file)
    except ValueError as e:
        raise click.ClickException(str(e))

    # 3. Load runtime settings from the resolved settings.yaml
    settings = load_settings(settings_path)

    # 4. Build config
    server_config = ServerConfig(
        api_id=creds["api_id"],
        api_hash=creds["api_hash"],
        data_dir=data_dir,
        host=host,
        port=port,
        settings_path=settings_path,
        **settings,
    )

    # Log configuration
    logger.info("=" * 60)
    logger.info("Telegram Scraper API Server")
    logger.info("=" * 60)
    logger.info(f"API ID: {server_config.api_id}")
    logger.info(f"Data directory: {server_config.data_dir.resolve()}")
    logger.info(f"  Channels: {server_config.channels_dir.resolve()}")
    logger.info(f"  Sessions: {server_config.sessions_dir.resolve()}")
    logger.info(f"Settings file: {server_config.settings_path}")
    logger.info(f"Download media: {server_config.download_media}")
    if server_config.download_media:
        if server_config.max_media_size_mb:
            logger.info(f"Max media size: {server_config.max_media_size_mb} MB")
        else:
            logger.info("Max media size: unlimited")
    logger.info(f"Telegram batch size: {server_config.telegram_batch_size}")
    logger.info(f"Server: {server_config.host}:{server_config.port}")
    logger.info("=" * 60)

    # Create app
    app_instance = create_app(server_config)

    # Run server
    uvicorn.run(
        app_instance, host=server_config.host, port=server_config.port, log_level="info"
    )
