"""FastAPI server for Telegram Scraper."""

import logging
from pathlib import Path

import click
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import load_config, ServerConfig
from .api import dialogs_router, history_router, files_router
from .api import auth_utils as api_auth
from .api import history as api_history
from .api import files as api_files

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
    api_history.set_config(config)
    api_files.set_config(config)

    # Include routers
    app.include_router(dialogs_router)
    app.include_router(history_router)
    app.include_router(files_router)

    @app.get("/", tags=["root"])
    async def root():
        """Root endpoint with API information."""
        return {
            "name": "Telegram Scraper API",
            "version": "1.0.0",
            "docs": "/docs",
            "endpoints": {
                "search_dialogs": "/api/v1/search/dialogs",
                "folders": "/api/v1/folders",
                "history": "/api/v1/history/{channel_id}",
                "files": "/api/v1/files/{file_uuid}",
            },
        }

    @app.get("/health", tags=["health"])
    async def health():
        """Health check endpoint."""
        return {"status": "healthy"}

    return app


@click.command()
@click.option(
    "--config",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Path to YAML configuration file",
)
@click.option(
    "--api-id",
    type=str,
    default=None,
    help="Telegram API ID (overrides config file)",
)
@click.option(
    "--api-hash",
    type=str,
    default=None,
    help="Telegram API hash (overrides config file)",
)
@click.option(
    "--download-media/--no-download-media",
    default=True,
    help="Enable or disable media download (default: enabled)",
)
@click.option(
    "--max-media-size-mb",
    type=float,
    default=20.0,
    help="Maximum media file size in MB (0 for no limit, default: 20)",
)
@click.option(
    "--telegram-batch-size",
    type=int,
    default=100,
    help="Batch size for downloading from Telegram (default: 100)",
)
@click.option(
    "--output-path",
    type=click.Path(path_type=Path),
    default="./output",
    help="Output directory for cache and media (default: ./output)",
)
@click.option(
    "--sessions-path",
    type=click.Path(path_type=Path),
    default="./sessions",
    help="Directory for Telegram session files (default: ./sessions)",
)
@click.option(
    "--host",
    type=str,
    default=None,
    help="Server host",
)
@click.option(
    "--port",
    type=int,
    default=None,
    help="Server port",
)
@click.pass_context
def main(
    ctx,
    config,
    api_id,
    api_hash,
    download_media,
    max_media_size_mb,
    telegram_batch_size,
    output_path,
    sessions_path,
    host,
    port,
):
    """Telegram Scraper API Server.

    Start the FastAPI server for the Telegram Scraper with smart cache management.

    \b
    Configuration Priority (highest to lowest):
    1. CLI arguments (if explicitly provided)
    2. Environment variables
    3. Config file values (if --config specified)
    4. Defaults
    """
    # Build CLI overrides dict
    # Only include parameters that were explicitly provided or use defaults if no config file
    cli_overrides = {}

    # Helper to check if parameter was explicitly provided
    def is_provided(param_name):
        return (
            ctx.get_parameter_source(param_name)
            == click.core.ParameterSource.COMMANDLINE
        )

    if api_id:
        cli_overrides["api_id"] = api_id
    if api_hash:
        cli_overrides["api_hash"] = api_hash

    # For parameters with defaults, only override if explicitly provided OR no config file
    if is_provided("download_media") or config is None:
        cli_overrides["download_media"] = download_media

    if is_provided("max_media_size_mb") or config is None:
        cli_overrides["max_media_size_mb"] = (
            max_media_size_mb if max_media_size_mb > 0 else None
        )

    if is_provided("telegram_batch_size") or config is None:
        cli_overrides["telegram_batch_size"] = telegram_batch_size

    if is_provided("output_path") or config is None:
        cli_overrides["output_path"] = output_path

    if is_provided("sessions_path") or config is None:
        cli_overrides["sessions_path"] = sessions_path

    if host:
        cli_overrides["host"] = host
    if port:
        cli_overrides["port"] = port

    # Load configuration
    try:
        server_config = load_config(config_path=config, cli_overrides=cli_overrides)
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        raise click.ClickException(str(e))

    # Log configuration
    logger.info("=" * 60)
    logger.info("Telegram Scraper API Server")
    logger.info("=" * 60)
    logger.info(f"API ID: {server_config.api_id}")
    logger.info(f"Download media: {server_config.download_media}")
    if server_config.download_media:
        if server_config.max_media_size_mb:
            logger.info(f"Max media size: {server_config.max_media_size_mb} MB")
        else:
            logger.info("Max media size: unlimited")
    logger.info(f"Telegram batch size: {server_config.telegram_batch_size}")
    logger.info(f"Output path: {server_config.output_path}")
    logger.info(f"Sessions path: {server_config.sessions_path}")
    logger.info(f"Server: {server_config.host}:{server_config.port}")
    logger.info("=" * 60)

    # Create app
    app_instance = create_app(server_config)

    # Run server
    uvicorn.run(
        app_instance, host=server_config.host, port=server_config.port, log_level="info"
    )


# TODO: figure out how to run server allow running server in 2 ways:
# 1. From script: tgsc-server
# 2. From uvicorn CLI: uvicorn telegram_scraper.server:app --reload
# Right now, those 2 ways are not compatible, since you have to create app instance in both cases.
# And creation / configuration process is different in both cases.
# MB I should create 2 different entry points for server?

# For development with uvicorn CLI (e.g., uvicorn telegram_scraper.server:app --reload)
# Configure via environment variables. Use tgsc-server for config file support.
# if __name__ != "__main__":
# app = create_app(load_config())
