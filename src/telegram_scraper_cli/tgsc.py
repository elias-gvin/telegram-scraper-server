from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional, Sequence

import click
from dotenv import load_dotenv

from . import export as export_mod
from . import scrape as scrape_mod
from . import search as search_mod
from .auth import authorize_telegram_client
from . import db_helper


class _OrderedGroup(click.Group):
    """
    Click lists commands alphabetically by default.
    Override ordering so `tgsc --help` follows the typical workflow.
    """

    _preferred_order = ("search", "scrape", "export")

    def list_commands(self, ctx: click.Context) -> list[str]:
        commands = list(self.commands)
        order_index = {name: i for i, name in enumerate(self._preferred_order)}
        return sorted(commands, key=lambda n: (order_index.get(n, 999), n))


def _configure_logging(log_level: str) -> None:
    level = getattr(logging, (log_level or "INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )


def _load_telegram_creds(
    *,
    api_id: Optional[str],
    api_hash: Optional[str],
    session_name: Optional[str],
    env_file: Optional[str],
) -> tuple[int, str, str]:
    if env_file:
        load_dotenv(env_file)
    else:
        load_dotenv()

    api_id_val = api_id or os.getenv("TELEGRAM_API_ID")
    api_hash_val = api_hash or os.getenv("TELEGRAM_API_HASH")
    session_val = session_name or os.getenv("TELEGRAM_SESSION_NAME", "session")

    if not api_id_val or not api_hash_val:
        raise click.ClickException(
            "Missing TELEGRAM_API_ID / TELEGRAM_API_HASH. Provide --api-id/--api-hash or set them in .env."
        )
    try:
        api_id_int = int(api_id_val)
    except ValueError as e:
        raise click.ClickException("TELEGRAM_API_ID must be an integer.") from e

    return api_id_int, api_hash_val, session_val


@click.group(
    cls=_OrderedGroup,
    help="Telegram Scraper CLI (tgsc). Usually, sequence of commands is: search (to get channel id), scrape (to scrape messages), export (to export messages to CSV/JSON).",
)
@click.option("--log-level", default="INFO", show_default=True)
def main(log_level: str) -> None:
    _configure_logging(log_level)


@main.command("search", help="Search your dialogs (channels + groups).")
@click.argument("query", type=str)
@click.option(
    "--env-file", type=click.Path(path_type=Path, dir_okay=False), default=None
)
@click.option("--api-id", default=None, help="Overrides TELEGRAM_API_ID env var.")
@click.option("--api-hash", default=None, help="Overrides TELEGRAM_API_HASH env var.")
@click.option(
    "--session-name", default=None, help="Overrides TELEGRAM_SESSION_NAME env var."
)
@click.option("--by-id/--no-by-id", default=True, show_default=True)
@click.option("--by-username/--no-by-username", default=True, show_default=True)
@click.option("--by-title/--no-by-title", default=True, show_default=True)
@click.option(
    "--title-threshold",
    default=80,
    show_default=True,
    type=click.IntRange(0, 100),
    help="0-100 RapidFuzz score threshold for title matches.",
)
def search_cmd(
    query: str,
    env_file: Optional[Path],
    api_id: Optional[str],
    api_hash: Optional[str],
    session_name: Optional[str],
    by_id: bool,
    by_username: bool,
    by_title: bool,
    title_threshold: int,
) -> None:
    async def _run() -> None:
        api_id_int, api_hash_val, session_val = _load_telegram_creds(
            api_id=api_id,
            api_hash=api_hash,
            session_name=session_name,
            env_file=str(env_file) if env_file else None,
        )
        client = await authorize_telegram_client(api_id_int, api_hash_val, session_val)
        try:
            params = search_mod.SearchParams(
                search_by_username=by_username,
                search_by_channel_id=by_id,
                search_by_title=by_title,
                title_similarity_threshold=title_threshold,
            )
            results = await search_mod.search_channels(client, query, params=params)
            if not results:
                click.echo("No matches.")
                return
            for r in results:
                c = r.channel
                uname = f"@{c.username}" if c.username else "N/A"
                participants = (
                    c.participants_count if c.participants_count is not None else "N/A"
                )
                click.echo(
                    f"{c.title} | id={c.id} | {c.type} | username={uname} | participants={participants} | matched_on={r.matched_on} | score={r.score:.1f}"
                )
        finally:
            await client.disconnect()

    asyncio.run(_run())


@main.command(
    "scrape",
    help="Scrape messages (and optional media) from a channel/group into SQLite.",
)
@click.option(
    "--env-file", type=click.Path(path_type=Path, dir_okay=False), default=None
)
@click.option("--api-id", default=None, help="Overrides TELEGRAM_API_ID env var.")
@click.option("--api-hash", default=None, help="Overrides TELEGRAM_API_HASH env var.")
@click.option(
    "--session-name", default=None, help="Overrides TELEGRAM_SESSION_NAME env var."
)
@click.option(
    "--channel-id", required=True, help="Numeric channel/chat id (e.g. -5263097314)."
)
@click.option(
    "--channel-name",
    default=None,
    help="Optional display name; will be auto-resolved if omitted.",
)
@click.option(
    "--output-dir",
    type=click.Path(path_type=Path, file_okay=False, dir_okay=True),
    default=Path("./output"),
    show_default=True,
)
@click.option("--start-date", default=None, help="YYYY-MM-DD or YYYY-MM-DD HH:MM:SS")
@click.option("--end-date", default=None, help="YYYY-MM-DD or YYYY-MM-DD HH:MM:SS")
@click.option("--media/--no-media", default=True, show_default=True)
@click.option(
    "--max-media-size-mb",
    default=None,
    type=float,
    help="Skip downloading media larger than this size.",
)
@click.option(
    "--replace-existing/--no-replace-existing", default=True, show_default=True
)
def scrape_cmd(
    env_file: Optional[Path],
    api_id: Optional[str],
    api_hash: Optional[str],
    session_name: Optional[str],
    channel_id: str,
    channel_name: Optional[str],
    output_dir: Path,
    start_date: Optional[str],
    end_date: Optional[str],
    media: bool,
    max_media_size_mb: Optional[float],
    replace_existing: bool,
) -> None:
    async def _run() -> None:
        api_id_int, api_hash_val, session_val = _load_telegram_creds(
            api_id=api_id,
            api_hash=api_hash,
            session_name=session_name,
            env_file=str(env_file) if env_file else None,
        )
        client = await authorize_telegram_client(api_id_int, api_hash_val, session_val)

        db_connection = None
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            db_connection = db_helper.open_channel_db(
                output_dir=output_dir, channel_id=channel_id, check_same_thread=False
            )

            resolved_name = channel_name
            if not resolved_name:
                try:
                    entity = await client.get_entity(int(channel_id))
                    resolved_name = getattr(entity, "title", None) or channel_id
                except Exception:
                    resolved_name = channel_id

            scrape_params = scrape_mod.ScrapeParams(
                start_date=start_date,
                end_date=end_date,
                channel=(resolved_name, channel_id),
                scrape_media=media,
                output_dir=output_dir,
                replace_existing=replace_existing,
                max_media_size_mb=max_media_size_mb,
            )
            scraper = scrape_mod.OptimizedTelegramScraper(
                client=client, db_connection=db_connection, scrape_params=scrape_params
            )
            await scraper.scrape_channel()
            click.echo("Scrape completed.")
        finally:
            if db_connection is not None:
                db_connection.close()
            await client.disconnect()

    asyncio.run(_run())


@main.command(
    "export", help="Export chat history from per-channel SQLite DB(s) to CSV/JSON."
)
@click.option(
    "--output-dir",
    type=click.Path(path_type=Path, file_okay=False, dir_okay=True),
    default=Path("./output"),
    show_default=True,
    help="Base output directory containing per-channel DB folders, or a specific channel folder.",
)
@click.option(
    "--format",
    "export_format",
    type=click.Choice(["csv", "json"], case_sensitive=False),
    default="json",
    show_default=True,
)
@click.option(
    "--channel",
    "channel_ids",
    multiple=True,
    help="Channel id to export. May be specified multiple times. If omitted, exports all channels found in output-dir.",
)
@click.option(
    "--out-dir",
    type=click.Path(path_type=Path, file_okay=False, dir_okay=True),
    default=None,
    help="Where to write exported files (default: next to each channel DB).",
)
@click.option("--order-by", default="date", show_default=True)
@click.option("--batch-size", default=1000, show_default=True, type=int)
@click.option("--json-indent", default=2, show_default=True, type=int)
@click.option(
    "--no-json-indent",
    is_flag=True,
    default=False,
    help="Write compact JSON (indent=None).",
)
def export_cmd(
    output_dir: Path,
    export_format: str,
    channel_ids: Sequence[str],
    out_dir: Optional[Path],
    order_by: str,
    batch_size: int,
    json_indent: int,
    no_json_indent: bool,
) -> None:
    indent = None if no_json_indent else json_indent
    results = export_mod.export_chat_history(
        output_dir=output_dir,
        export_format=export_format.lower(),  # type: ignore[arg-type]
        channel_ids=list(channel_ids) if channel_ids else None,
        out_dir=out_dir,
        order_by=order_by,
        json_indent=indent,
        batch_size=batch_size,
    )

    if not results:
        click.echo("No DBs found to export.")
        return

    for r in results:
        click.echo(f"{r.channel_id}: rows={r.row_count} -> {r.output_file}")


if __name__ == "__main__":
    main()
