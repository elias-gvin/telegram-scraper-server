import sqlite3
import asyncio
import warnings
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path
from io import StringIO
from telethon import TelegramClient
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument, MessageMediaWebPage, User, PeerChannel, Channel, Chat, Message
from telethon.errors import FloodWaitError, SessionPasswordNeededError
import qrcode
from datetime import datetime
from tqdm.asyncio import tqdm as atqdm
from tqdm import tqdm


warnings.filterwarnings("ignore", message="Using async sessions support is an experimental feature")

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
    views: Optional[int]
    forwards: Optional[int]
    reactions: Optional[str]

@dataclass
class ScrapeParams:
    start_date: Optional[str]
    end_date: Optional[str]
    channel: Tuple[str, int]
    scrape_media: bool
    output_dir: Path

MAX_CONCURRENT_DOWNLOADS = 5
BATCH_SIZE = 100
STATE_SAVE_INTERVAL = 50
MEDIA_DOWNLOAD_BATCH_SIZE = 10

class OptimizedTelegramScraper:
    def __init__(self, api_id: int, api_hash: str, scrape_params: ScrapeParams) -> None:
        self.api_id = api_id
        self.api_hash = api_hash
        self.scrape_params = scrape_params

        self.client = None
        self.db_connection = None
        
        self.max_concurrent_downloads = MAX_CONCURRENT_DOWNLOADS
        self.batch_size = BATCH_SIZE
        self.state_save_interval = STATE_SAVE_INTERVAL
        self.media_download_batch_size = MEDIA_DOWNLOAD_BATCH_SIZE

    async def download_media(self, message: Message) -> Optional[str]:
        channel = self.scrape_params.channel[0]

        if not message.media or not self.scrape_params.scrape_media:
            return None

        if isinstance(message.media, MessageMediaWebPage):
            return None

        try:
            channel_dir = Path(channel)
            media_folder = channel_dir / 'media'
            media_folder.mkdir(exist_ok=True)
            
            if isinstance(message.media, MessageMediaPhoto):
                original_name = getattr(message.file, 'name', None) or "photo.jpg"
                ext = "jpg"
            elif isinstance(message.media, MessageMediaDocument):
                ext = getattr(message.file, 'ext', 'bin') if message.file else 'bin'
                original_name = getattr(message.file, 'name', None) or f"document.{ext}"
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
                        await asyncio.sleep(2 ** attempt)
                    else:
                        return None
            
            return None
        except Exception:
            return None

    async def scrape_channel(self) -> List[MessageData]:
        try:
            channel = self.scrape_params.channel[0]
            entity = await self.client.get_entity(PeerChannel(int(channel)) if channel.startswith('-') else channel)
            result = await self.client.get_messages(entity, offset_date=self.scrape_params.start_date, reverse=True, limit=0)
            total_messages = result.total

            if total_messages == 0:
                logger.warning(f"No messages found in channel {channel}")
                return

            logger.info(f"Found {total_messages} messages in channel {channel}")

            message_batch = []
            media_tasks = []
            semaphore = asyncio.Semaphore(self.max_concurrent_downloads)

            # Wrap async iterator with tqdm for progress tracking
            messages_iter = self.client.iter_messages(entity, offset_date=self.scrape_params.start_date, reverse=True)
            async for message in atqdm(messages_iter, total=total_messages, desc="📄 Messages", unit="msg"):
                try:
                    sender = await message.get_sender()

                    reactions_str = None
                    if message.reactions and message.reactions.results:
                        reactions_parts = []
                        for reaction in message.reactions.results:
                            emoji = getattr(reaction.reaction, 'emoticon', '')
                            count = reaction.count
                            if emoji:
                                reactions_parts.append(f"{emoji} {count}")
                        if reactions_parts:
                            reactions_str = ' '.join(reactions_parts)

                    msg_data = MessageData(
                        message_id=message.id,
                        date=message.date.strftime('%Y-%m-%d %H:%M:%S'),
                        sender_id=message.sender_id,
                        first_name=getattr(sender, 'first_name', None) if isinstance(sender, User) else None,
                        last_name=getattr(sender, 'last_name', None) if isinstance(sender, User) else None,
                        username=getattr(sender, 'username', None) if isinstance(sender, User) else None,
                        message=message.message or '',
                        media_type=message.media.__class__.__name__ if message.media else None,
                        media_path=None,
                        reply_to=message.reply_to_msg_id if message.reply_to else None,
                        post_author=message.post_author,
                        views=message.views,
                        forwards=message.forwards,
                        reactions=reactions_str
                    )

                    message_batch.append(msg_data)

                    if self.scrape_params.scrape_media and message.media and not isinstance(message.media, MessageMediaWebPage):
                        media_tasks.append(message)

                    if len(message_batch) >= self.batch_size:
                        self.batch_insert_messages(channel, message_batch)
                        message_batch.clear()

                except Exception as e:
                    logger.error(f"Error processing message {message.id}: {e}", exc_info=True)

            if message_batch:
                self.batch_insert_messages(channel, message_batch)

            if media_tasks:
                total_media = len(media_tasks)
                successful_downloads = 0
                logger.info(f"Downloading {total_media} media files...")
                
                semaphore = asyncio.Semaphore(self.max_concurrent_downloads)
                
                async def download_single_media(message):
                    async with semaphore:
                        return await self.download_media(channel, message)
                
                with tqdm(total=total_media, desc="📥 Media", unit="file") as pbar:
                    for i in range(0, len(media_tasks), self.media_download_batch_size):
                        batch = media_tasks[i:i + self.media_download_batch_size]
                        tasks = [asyncio.create_task(download_single_media(msg)) for msg in batch]
                        
                        for j, task in enumerate(tasks):
                            try:
                                media_path = await task
                                if media_path:
                                    await self.update_media_path(channel, batch[j].id, media_path)
                                    successful_downloads += 1
                            except Exception:
                                pass
                            
                            pbar.update(1)
                
                logger.info(f"Media download complete! ({successful_downloads}/{total_media} successful)")

            logger.info(f"Completed scraping channel {channel}")

        except Exception as e:
            logger.error(f"Error with channel {channel}: {e}", exc_info=True)

    def batch_insert_messages(self, messages: List[MessageData]):
        if not messages:
            return

        conn = self.get_db_connection()
        data = [(msg.message_id, msg.date, msg.sender_id, msg.first_name,
                msg.last_name, msg.username, msg.message, msg.media_type,
                msg.media_path, msg.reply_to, msg.post_author, msg.views,
                msg.forwards, msg.reactions) for msg in messages]

        conn.executemany('''INSERT OR IGNORE INTO messages
                           (message_id, date, sender_id, first_name, last_name, username,
                            message, media_type, media_path, reply_to, post_author, views,
                            forwards, reactions)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', data)
        conn.commit()

    def get_db_connection(self) -> sqlite3.Connection:
        channel = self.scrape_params.channel[0]
        if not self.db_connection:
            output_dir = Path(self.scrape_params.output_dir)
            output_dir.mkdir(exist_ok=True)
            channel_dir = Path(output_dir / channel)
            channel_dir.mkdir(exist_ok=True)

            db_file = channel_dir / f'{channel}.db'
            conn = sqlite3.connect(str(db_file), check_same_thread=False)
            conn.execute('''CREATE TABLE IF NOT EXISTS messages
                          (id INTEGER PRIMARY KEY, message_id INTEGER UNIQUE, date TEXT,
                           sender_id INTEGER, first_name TEXT, last_name TEXT, username TEXT,
                           message TEXT, media_type TEXT, media_path TEXT, reply_to INTEGER,
                           post_author TEXT, views INTEGER, forwards INTEGER, reactions TEXT)''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_message_id ON messages(message_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_date ON messages(date)')
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA synchronous=NORMAL')
            conn.commit()

            self.db_connection = conn

        return self.db_connection

    def close_db_connections(self):
        conn = self.db_connection
        if conn:
            conn.close()
            self.db_connection = None
        
async def main():
    scraper = OptimizedTelegramScraper()
    await scraper.run()
