"""FastAPI server: one Telegram account, one archive, one bearer token."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import click
import uvicorn
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from telethon.errors import FloodWaitError

from .api import (
    API_PREFIX,
    auth_router,
    dialogs_router,
    files_router,
    history_router,
    search_router,
    settings_router,
)
from .api import auth as api_qr_auth
from .api.deps import require_token
from .archive import AccountMismatch, Archive
from .config import (
    ServerConfig,
    load_credentials_from_env,
    load_settings,
    resolve_settings_file,
)
from .telegram import (
    SESSION_LOST,
    DialogNotFound,
    NotAuthorized,
    TelegramHolder,
    check_account_name,
)

logger = logging.getLogger(__name__)


def create_app(config: ServerConfig, telegram: TelegramHolder | None = None) -> FastAPI:
    archive = Archive(config.archive_path)

    async def bind_account(client):
        archive.bind_account((await client.get_me()).id)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await api_qr_auth.cleanup_qr_sessions(config)
        await app.state.telegram.reset()
        archive.close()

    app = FastAPI(title="Telegram Scraper API", version="0.3.0", lifespan=lifespan)
    app.state.config = config
    app.state.archive = archive
    app.state.telegram = telegram or TelegramHolder(
        config.session_path, config.api_id, config.api_hash, on_connect=bind_account
    )

    api = APIRouter(prefix=API_PREFIX, dependencies=[Depends(require_token)])
    for router in (
        dialogs_router,
        history_router,
        files_router,
        auth_router,
        settings_router,
        search_router,
    ):
        api.include_router(router)
    app.include_router(api)

    def error(status: int, detail: str, **headers) -> JSONResponse:
        return JSONResponse(
            {"detail": detail}, status_code=status, headers=headers or None
        )

    @app.exception_handler(NotAuthorized)
    async def not_authorized(_: Request, e: NotAuthorized):
        return error(401, str(e))

    @app.exception_handler(DialogNotFound)
    async def dialog_not_found(_: Request, e: DialogNotFound):
        return error(404, str(e))

    @app.exception_handler(AccountMismatch)
    async def account_mismatch(_: Request, e: AccountMismatch):
        return error(409, str(e))

    async def session_lost(_: Request, e: Exception):
        await (
            app.state.telegram.reset()
        )  # drop the dead client; the next request reconnects
        return error(
            401,
            f"Telegram session is no longer authorized ({type(e).__name__}): log in again",
        )

    for exc in SESSION_LOST:
        app.add_exception_handler(exc, session_lost)

    @app.exception_handler(Exception)
    async def unexpected(_: Request, e: Exception):
        logger.exception("Unhandled error")
        return error(500, f"{type(e).__name__}: {e}")

    @app.exception_handler(FloodWaitError)
    async def flood_wait(_: Request, e: FloodWaitError):
        return error(
            429,
            f"Telegram rate limit: retry after {e.seconds} s",
            **{"Retry-After": str(e.seconds)},
        )

    @app.get("/health", tags=["health"])
    async def health():
        return {"status": "healthy"}

    return app


@click.command()
@click.option(
    "--account",
    envvar="TGSC_ACCOUNT",
    required=True,
    help="Telegram session name (from tgsc-auth / POST /auth/qr). Env: TGSC_ACCOUNT",
)
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default="./data",
    show_default=True,
    help="Sessions, archive, media, settings and the API token",
)
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", type=int, default=8000, show_default=True)
@click.option(
    "--settings",
    "settings_file",
    type=click.Path(path_type=Path),
    default=None,
    help="Import this settings.yaml into the data directory (overwrites the existing one)",
)
def main(
    account: str, data_dir: Path, host: str, port: int, settings_file: Path | None
):
    """Telegram Scraper API server for ONE Telegram account.

    \b
    Every request needs 'Authorization: Bearer <token>'; the token is
    created on first start in {data-dir}/token.
    Credentials: TELEGRAM_API_ID and TELEGRAM_API_HASH (env or .env).
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    try:
        check_account_name(account)
        creds = load_credentials_from_env()
        settings_path = resolve_settings_file(data_dir, settings_file)
    except ValueError as e:
        raise click.ClickException(str(e)) from e

    config = ServerConfig(
        api_id=creds["api_id"],
        api_hash=creds["api_hash"],
        data_dir=data_dir,
        account=account,
        host=host,
        port=port,
        settings_path=settings_path,
        settings=load_settings(settings_path),
    )
    logger.info("Account %s, data directory %s", account, config.data_dir.resolve())
    logger.info("API token file: %s", (config.data_dir / "token").resolve())
    logger.info("Settings: %s", config.settings.model_dump())
    uvicorn.run(create_app(config), host=host, port=port, log_level="info")
