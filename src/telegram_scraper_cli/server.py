"""FastAPI server for Telegram Scraper."""

import argparse
import logging
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import load_config, ServerConfig
from .api import channels_router, history_router, files_router
from .api import auth as api_auth
from .api import history as api_history
from .api import files as api_files


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


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
    app.include_router(channels_router)
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
                "find_channels": "/api/v1/find-channels",
                "history": "/api/v1/history/{channel_id}",
                "files": "/api/v1/files/{file_uuid}"
            }
        }
    
    @app.get("/health", tags=["health"])
    async def health():
        """Health check endpoint."""
        return {"status": "healthy"}
    
    return app


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Telegram Scraper API Server",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    
    # Config file
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to YAML configuration file"
    )
    
    # Telegram credentials
    parser.add_argument(
        "--api-id",
        type=str,
        help="Telegram API ID (overrides config file)"
    )
    parser.add_argument(
        "--api-hash",
        type=str,
        help="Telegram API hash (overrides config file)"
    )
    
    # Download settings
    parser.add_argument(
        "--download-media",
        action="store_true",
        default=None,
        help="Enable media download"
    )
    parser.add_argument(
        "--no-download-media",
        action="store_true",
        help="Disable media download"
    )
    parser.add_argument(
        "--max-media-size-mb",
        type=float,
        help="Maximum media file size in MB (0 for no limit)"
    )
    parser.add_argument(
        "--telegram-batch-size",
        type=int,
        help="Batch size for downloading from Telegram"
    )
    
    # Storage paths
    parser.add_argument(
        "--output-path",
        type=Path,
        help="Output directory for cache and media"
    )
    parser.add_argument(
        "--sessions-path",
        type=Path,
        help="Directory for Telegram session files"
    )
    
    # Server settings
    parser.add_argument(
        "--host",
        type=str,
        help="Server host"
    )
    parser.add_argument(
        "--port",
        type=int,
        help="Server port"
    )
    
    return parser.parse_args()


def main():
    """Main entry point for the server."""
    args = parse_args()
    
    # Build CLI overrides dict
    cli_overrides = {}
    
    if args.api_id:
        cli_overrides["api_id"] = args.api_id
    if args.api_hash:
        cli_overrides["api_hash"] = args.api_hash
    
    # Handle download_media boolean
    if args.download_media:
        cli_overrides["download_media"] = True
    elif args.no_download_media:
        cli_overrides["download_media"] = False
    
    if args.max_media_size_mb is not None:
        cli_overrides["max_media_size_mb"] = args.max_media_size_mb if args.max_media_size_mb > 0 else None
    if args.telegram_batch_size:
        cli_overrides["telegram_batch_size"] = args.telegram_batch_size
    
    if args.output_path:
        cli_overrides["output_path"] = args.output_path
    if args.sessions_path:
        cli_overrides["sessions_path"] = args.sessions_path
    
    if args.host:
        cli_overrides["host"] = args.host
    if args.port:
        cli_overrides["port"] = args.port
    
    # Load configuration
    try:
        config = load_config(config_path=args.config, cli_overrides=cli_overrides)
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        return 1
    
    # Log configuration
    logger.info("=" * 60)
    logger.info("Telegram Scraper API Server")
    logger.info("=" * 60)
    logger.info(f"API ID: {config.api_id}")
    logger.info(f"Download media: {config.download_media}")
    if config.download_media:
        if config.max_media_size_mb:
            logger.info(f"Max media size: {config.max_media_size_mb} MB")
        else:
            logger.info(f"Max media size: unlimited")
    logger.info(f"Telegram batch size: {config.telegram_batch_size}")
    logger.info(f"Output path: {config.output_path}")
    logger.info(f"Sessions path: {config.sessions_path}")
    logger.info(f"Server: {config.host}:{config.port}")
    logger.info("=" * 60)
    
    # Create app
    app = create_app(config)
    
    # Run server
    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level="info"
    )
    
    return 0


if __name__ == "__main__":
    exit(main())

