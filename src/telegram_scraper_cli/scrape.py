import sqlite3
import asyncio
import warnings
import logging
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path
from datetime import datetime, timezone
from telethon import TelegramClient
from telethon.tl.types import (
    MessageMediaPhoto,
    MessageMediaDocument,
    MessageMediaWebPage,
    User,
    PeerChannel,
    PeerChat,
    Channel,
    Chat,
    Message,
)
from telethon.errors import FloodWaitError, SessionPasswordNeededError
from tqdm.asyncio import tqdm as atqdm
from tqdm import tqdm
from dotenv import load_dotenv
import os
from .auth import authorize_telegram_client
from . import db_helper


warnings.filterwarnings(
    "ignore", message="Using async sessions support is an experimental feature"
)

logger = logging.getLogger(__name__)


@dataclass
class MessageData:
    message_id: int
    date: str
    sender_id: int
    first_name: Optional[str]
    last_name: Optional[str]
    username: Optional[str]
    message: str
    media_type: Optional[str]
    media_path: Optional[str]
    reply_to: Optional[int]
    post_author: Optional[str]
    is_forwarded: int
    forwarded_from_channel_id: Optional[int]


@dataclass
class ScrapeParams:
    start_date: Optional[str]
    end_date: Optional[str]
    channel: Tuple[str, str]
    scrape_media: bool
    output_dir: Path
    replace_existing: bool = True
    # If set, skip downloading media larger than this many megabytes.
    # None means "no limit".
    max_media_size_mb: Optional[float] = None


MAX_CONCURRENT_DOWNLOADS = 5
BATCH_SIZE = 100
STATE_SAVE_INTERVAL = 50
MEDIA_DOWNLOAD_BATCH_SIZE = 10


class OptimizedTelegramScraper:
    def __init__(
        self,
        client: TelegramClient,
        db_connection: sqlite3.Connection,
        scrape_params: ScrapeParams,
    ) -> None:
        self.client = client
        self.db_connection = db_connection
        self.scrape_params = scrape_params

        self.max_concurrent_downloads = MAX_CONCURRENT_DOWNLOADS
        self.batch_size = BATCH_SIZE
        self.state_save_interval = STATE_SAVE_INTERVAL
        self.media_download_batch_size = MEDIA_DOWNLOAD_BATCH_SIZE

    async def _download_media(self, message: Message) -> Optional[str]:
        if not message.media or not self.scrape_params.scrape_media:
            return None

        if isinstance(message.media, MessageMediaWebPage):
            return None

        try:
            output_dir = Path(self.scrape_params.output_dir)
            channel_id = self.scrape_params.channel[1]

            # Put media next to the SQLite DB folder.
            # In this project, the DB is stored under `<output_dir>/<channel_id>/<channel_id>.db`.
            # Support both cases:
            # - output_dir == parent folder (e.g. ./output)
            # - output_dir == channel folder (e.g. ./output/-100123...)
            db_dir = (
                output_dir
                if output_dir.name == str(channel_id)
                else (output_dir / str(channel_id))
            )

            media_folder = db_dir / "media"
            media_folder.mkdir(parents=True, exist_ok=True)

            # Optional media size limit (best-effort; not all media has a known size up front).
            if self.scrape_params.max_media_size_mb is not None:
                try:
                    max_bytes = int(
                        float(self.scrape_params.max_media_size_mb) * 1024 * 1024
                    )
                except (TypeError, ValueError):
                    max_bytes = None

                if max_bytes is not None and max_bytes >= 0:
                    msg_file = getattr(message, "file", None)
                    size_bytes = getattr(msg_file, "size", None)
                    if size_bytes is None:
                        # Fallbacks for some media types.
                        doc = getattr(getattr(message, "media", None), "document", None)
                        size_bytes = getattr(doc, "size", None)

                    if isinstance(size_bytes, int) and size_bytes > max_bytes:
                        return None

            if isinstance(message.media, MessageMediaPhoto):
                original_name = getattr(message.file, "name", None) or "photo.jpg"
                ext = "jpg"
            elif isinstance(message.media, MessageMediaDocument):
                ext = getattr(message.file, "ext", "bin") if message.file else "bin"
                original_name = getattr(message.file, "name", None) or f"document.{ext}"
            else:
                return None

            base_name = Path(original_name).stem
            extension = Path(original_name).suffix or f".{ext}"
            unique_filename = f"{message.id}-{base_name}{extension}"
            media_path = media_folder / unique_filename

            existing_files = list(media_folder.glob(f"{message.id}-*"))
            if existing_files:
                return str(existing_files[0])

            for attempt in range(3):
                try:
                    downloaded_path = await message.download_media(file=str(media_path))
                    if downloaded_path and Path(downloaded_path).exists():
                        return downloaded_path
                    else:
                        return None
                except FloodWaitError as e:
                    if attempt < 2:
                        await asyncio.sleep(e.seconds)
                    else:
                        return None
                except Exception:
                    if attempt < 2:
                        await asyncio.sleep(2**attempt)
                    else:
                        return None

            return None
        except Exception:
            return None

    async def scrape_channel(self) -> None:
        is_connected_and_authorized = self.client.is_connected() and await self.client.is_user_authorized()
        if not is_connected_and_authorized:
            raise ConnectionError(
                "Telegram client is not connected or not authorized. Please reconnect."
            )

        if not db_helper.check_db_connection(self.db_connection):
            raise ConnectionError("Database connection is not alive. Please reconnect.")

        # Initialize database schema | populates schema if it doesn't exist
        db_helper.ensure_messages_schema(self.db_connection)

        try:
            channel = self.scrape_params.channel[1]
            logger.error(f"!!! Scraping channel {channel}")
            entity = await self.client.get_entity(
                int(channel)
            )  # TODO: remove hardcoded channel id

            # Telethon expects datetime (or None) for offset_date, not a string.
            start_date_dt = None
            if self.scrape_params.start_date:
                try:
                    try:
                        start_date_dt = datetime.strptime(
                            self.scrape_params.start_date, "%Y-%m-%d %H:%M:%S"
                        )
                    except ValueError:
                        start_date_dt = datetime.strptime(
                            self.scrape_params.start_date, "%Y-%m-%d"
                        )
                    # Telethon message dates are timezone-aware (UTC). Make filters UTC-aware too.
                    if start_date_dt.tzinfo is None:
                        start_date_dt = start_date_dt.replace(tzinfo=timezone.utc)
                    logger.info(f"Filtering messages from start_date: {start_date_dt}")
                except ValueError:
                    logger.warning(
                        f"Invalid start_date format '{self.scrape_params.start_date}'. Expected 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS'. Ignoring start_date filter."
                    )
                    start_date_dt = None

            result = await self.client.get_messages(
                entity, offset_date=start_date_dt, reverse=True, limit=0
            )
            total_messages = result.total

            if total_messages == 0:
                logger.warning(f"No messages found in channel {channel}")
                return

            logger.info(f"Found {total_messages} messages in channel {channel}")

            # Parse end_date if provided
            end_date_dt = None
            if self.scrape_params.end_date:
                try:
                    # Try parsing with time first, then date only
                    try:
                        end_date_dt = datetime.strptime(
                            self.scrape_params.end_date, "%Y-%m-%d %H:%M:%S"
                        )
                    except ValueError:
                        end_date_dt = datetime.strptime(
                            self.scrape_params.end_date, "%Y-%m-%d"
                        )
                    # Telethon message dates are timezone-aware (UTC). Make filters UTC-aware too.
                    if end_date_dt.tzinfo is None:
                        end_date_dt = end_date_dt.replace(tzinfo=timezone.utc)
                    logger.info(f"Filtering messages up to end_date: {end_date_dt}")
                except ValueError as e:
                    logger.warning(
                        f"Invalid end_date format '{self.scrape_params.end_date}'. Expected 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS'. Ignoring end_date filter."
                    )
                    end_date_dt = None

            message_batch = []
            media_tasks = []

            # Wrap async iterator with tqdm for progress tracking
            messages_iter = self.client.iter_messages(
                entity, offset_date=start_date_dt, reverse=True
            )
            async for message in atqdm(
                messages_iter, total=total_messages, desc="📄 Messages", unit="msg"
            ):
                try:
                    # Filter out messages after end_date
                    if end_date_dt and message.date > end_date_dt:
                        logger.info(
                            f"Reached end_date ({end_date_dt}). Stopping message collection."
                        )
                        break

                    sender = await message.get_sender()

                    fwd_from = getattr(message, "fwd_from", None)
                    is_forwarded = 1 if fwd_from else 0
                    forwarded_from_channel_id = None
                    if fwd_from:
                        # Prefer `from_id`, fallback to `saved_from_peer` (some forwards hide origin).
                        peer = getattr(fwd_from, "from_id", None) or getattr(
                            fwd_from, "saved_from_peer", None
                        )
                        if isinstance(peer, PeerChannel):
                            forwarded_from_channel_id = peer.channel_id
                        else:
                            forwarded_from_channel_id = getattr(
                                peer, "channel_id", None
                            )

                    msg_data = MessageData(
                        message_id=message.id,
                        date=message.date.strftime("%Y-%m-%d %H:%M:%S"),
                        sender_id=message.sender_id,
                        first_name=getattr(sender, "first_name", None)
                        if isinstance(sender, User)
                        else None,
                        last_name=getattr(sender, "last_name", None)
                        if isinstance(sender, User)
                        else None,
                        username=getattr(sender, "username", None)
                        if isinstance(sender, User)
                        else None,
                        message=message.message or "",
                        media_type=message.media.__class__.__name__
                        if message.media
                        else None,
                        media_path=None,
                        reply_to=message.reply_to_msg_id if message.reply_to else None,
                        post_author=message.post_author,
                        is_forwarded=is_forwarded,
                        forwarded_from_channel_id=forwarded_from_channel_id,
                    )

                    message_batch.append(msg_data)

                    if (
                        self.scrape_params.scrape_media
                        and message.media
                        and not isinstance(message.media, MessageMediaWebPage)
                    ):
                        media_tasks.append(message)

                    if len(message_batch) >= self.batch_size:
                        db_helper.batch_upsert_messages(
                            self.db_connection,
                            messages,
                            replace_existing=self.scrape_params.replace_existing,
                        )
                        message_batch.clear()

                except Exception as e:
                    logger.error(
                        f"Error processing message {message.id}: {e}", exc_info=True
                    )

            if message_batch:
                db_helper.batch_upsert_messages(
                    self.db_connection,
                    messages,
                    replace_existing=self.scrape_params.replace_existing,
                )

            if media_tasks:
                total_media = len(media_tasks)
                successful_downloads = 0
                logger.info(f"Downloading {total_media} media files...")

                semaphore = asyncio.Semaphore(self.max_concurrent_downloads)

                async def download_single_media(message):
                    async with semaphore:
                        return await self._download_media(message)

                with tqdm(total=total_media, desc="📥 Media", unit="file") as pbar:
                    for i in range(0, len(media_tasks), self.media_download_batch_size):
                        batch = media_tasks[i : i + self.media_download_batch_size]
                        tasks = [
                            asyncio.create_task(download_single_media(msg))
                            for msg in batch
                        ]

                        for j, task in enumerate(tasks):
                            try:
                                media_path = await task
                                if media_path:
                                    db_helper.set_message_media_path(
                                        self.db_connection,
                                        message_id=message_id,
                                        media_path=media_path,
                                    )
                                    successful_downloads += 1
                            except Exception:
                                pass

                            pbar.update(1)

                logger.info(
                    f"Media download complete! ({successful_downloads}/{total_media} successful)"
                )

            logger.info(f"Completed scraping channel {channel}")

        except Exception as e:
            logger.error(f"Error with channel {channel}: {e}", exc_info=True)



async def main() -> None:
    load_dotenv()
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    session_name = os.getenv("TELEGRAM_SESSION_NAME", "session")
    # TODO: add proper checks for read values.
    client = await authorize_telegram_client(api_id, api_hash, session_name)

    output_dir = Path("./output")
    channel_id = "-5263097314"
    channel_name = "NotesScraperTest"
    start_date = "2024-01-01"
    end_date = "2026-12-31"
    scrape_media = True
    max_media_size_mb = 8  # 8MB
    replace_existing = True

    db_connection = None

    try:
        # Create database connection
        logger.info(f"Creating database connection for channel {channel_id}...")
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        db_connection = db_helper.open_channel_db(
            output_dir=output_dir, channel_id=channel_id, check_same_thread=False
        )
        logger.info("✅ Database connection created")

        # # Get channel entity to extract channel name (optional, for logging)
        # try:
        #     entity = await client.get_entity(PeerChannel(int(channel_id)) if channel_id.startswith('-') else channel_id)
        #     channel_name = getattr(entity, 'title', channel_id) or channel_id
        # except Exception:
        #     channel_name = channel_id

        # Create scrape parameters
        scrape_params = ScrapeParams(
            start_date=start_date,
            end_date=end_date,
            channel=(
                channel_name,
                channel_id,
            ),  # Tuple[str, int] - int is not used by scraper
            scrape_media=scrape_media,
            output_dir=Path(output_dir),
            replace_existing=replace_existing,
            max_media_size_mb=max_media_size_mb,
        )

        # Create scraper instance
        scraper = OptimizedTelegramScraper(
            client=client, db_connection=db_connection, scrape_params=scrape_params
        )

        # Run the scraper
        logger.info(f"Starting dump for channel {channel_id} ({channel_name})...")
        await scraper.scrape_channel()
        logger.info("✅ Dump completed successfully")

    except Exception as e:
        logger.error(f"Error during dump: {e}", exc_info=True)
        raise
    finally:
        # Cleanup
        if db_connection:
            db_connection.close()
            logger.info("Database connection closed")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(0)
