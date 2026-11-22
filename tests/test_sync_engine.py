"""Sync invariants: no holes, no duplicates, nothing silently lost, refresh works."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from telethon.tl import types as t

from telegram_scraper.archive import AccountMismatch, Archive
from telegram_scraper.config import RuntimeSettings
from telegram_scraper.serialize import serialize_row
from telegram_scraper.sync import sync_dialog

from .fake_telegram import CHANNEL, GROUP, SUPERGROUP, FakeTelegram, photo, text_msg

CHAT_ID = -1000000005000  # CHANNEL, marked
NOW = datetime.now(UTC).replace(microsecond=0)


def messages(ids, chat=CHANNEL):
    """Messages ending one hour ago, one per hour, oldest first."""
    ids = list(ids)
    return [
        text_msg(chat, i, f"m{i}", date=NOW - timedelta(hours=len(ids) - n))
        for n, i in enumerate(ids)
    ]


@pytest.fixture
def env(tmp_path):
    fake = FakeTelegram()
    archive = Archive(tmp_path / "archive.db")
    settings = RuntimeSettings(download_media=False, telegram_batch_size=10)

    async def sync(chat=CHANNEL, **kw):
        return await sync_dialog(
            fake, archive, chat, settings, tmp_path / "media", **kw
        )

    def history(chat_id=CHAT_ID):
        rows = [r for batch in archive.read(chat_id, 0, 2**40) for r in batch]
        return [serialize_row(r, archive.peer) for r in rows]

    return fake, archive, settings, sync, history


def ids(history):
    return [m["message_id"] for m in history]


def date_of(fake, id, chat=CHANNEL):
    return fake.chats[-1000000005000 if chat is CHANNEL else -6000][id].date


async def test_full_sync_archives_everything_once(env):
    fake, _, _, sync, history = env
    fake.add(CHANNEL, *messages(range(1, 51)))
    report = await sync()
    assert report.new_messages == 50 and report.reached_start
    await sync()
    await sync()
    assert ids(history()) == list(range(1, 51))  # no duplicates after repeated syncs


async def test_since_then_older_extends_without_holes(env):
    fake, _, _, sync, history = env
    fake.add(CHANNEL, *messages(range(1, 51)))
    report = await sync(since=date_of(fake, 30))
    assert ids(history()) == list(range(30, 51)) and not report.reached_start
    await sync(since=date_of(fake, 10))
    assert ids(history()) == list(range(10, 51))
    await sync()
    assert ids(history()) == list(range(1, 51))


async def test_new_messages_are_picked_up(env):
    fake, _, _, sync, history = env
    fake.add(CHANNEL, *messages(range(1, 11)))
    await sync()
    fake.add(
        CHANNEL,
        text_msg(CHANNEL, 11, "new", date=NOW),
        text_msg(CHANNEL, 12, "newer", date=NOW),
    )
    report = await sync()
    assert report.new_messages == 2
    assert ids(history()) == list(range(1, 13))


async def test_interrupted_sync_leaves_no_hole_and_resumes(env):
    fake, _, _, sync, history = env
    fake.add(CHANNEL, *messages(range(1, 51)))
    fake.fail_after = 25
    with pytest.raises(ConnectionError):
        await sync()
    partial = ids(history())
    assert partial == list(range(partial[0], 51))  # contiguous, newest end
    fake.fail_after = None
    await sync()
    assert ids(history()) == list(range(1, 51))


async def test_refresh_picks_up_edits_reactions_and_deletions(env):
    fake, _, _, sync, history = env
    fake.add(CHANNEL, *messages(range(1, 21)))
    await sync()
    fake.edit(CHANNEL, 20, message="edited", edit_date=NOW)
    fake.edit(
        CHANNEL,
        19,
        reactions=t.MessageReactions(
            results=[t.ReactionCount(t.ReactionEmoji("🔥"), 4)]
        ),
    )
    fake.delete(CHANNEL, 18)
    report = await sync()
    got = {m["message_id"]: m for m in history()}
    assert got[20]["message"] == "edited" and got[20]["edit_date"]
    assert got[19]["reactions"] == [{"emoji": "🔥", "count": 4}]
    assert got[18]["deleted"] is True and got[18]["message"] == "m18"  # kept, marked
    assert report.deleted_messages == 1


async def test_old_edits_need_full_refresh(env):
    fake, _, _, sync, history = env
    old = [
        text_msg(CHANNEL, i, f"m{i}", date=NOW - timedelta(days=100 - i))
        for i in range(1, 6)
    ]
    fake.add(CHANNEL, *old)
    await sync()
    fake.edit(CHANNEL, 1, message="edited long ago")
    await sync()
    assert history()[0]["message"] == "m1"  # outside the refresh window
    await sync(full=True)
    assert history()[0]["message"] == "edited long ago"


async def test_sparse_ids_are_not_mistaken_for_deletions(env):
    """Private chats and basic groups share one id counter: a chat's ids have gaps."""
    fake, _, _, sync, history = env
    fake.add(GROUP, *messages(range(1, 60, 7), chat=GROUP))
    await sync(GROUP)
    report = await sync(GROUP)
    assert report.deleted_messages == 0
    assert not any(m.get("deleted") for m in history(-6000))


async def test_failed_media_is_reported_kept_and_retried(env, tmp_path):
    fake, archive, settings, sync, history = env
    settings.download_media = True
    fake.add(CHANNEL, *messages(range(1, 3)))
    fake.edit(CHANNEL, 2, media=photo())
    fake.fail_download = {2}
    report = await sync()
    assert report.media_failed == 1
    assert report.problems[0]["message_id"] == 2 and report.problems[0][
        "problem"
    ].startswith("media_failed")
    msg = history()[1]
    assert (
        msg["incomplete"].startswith("media_failed") and msg["media_status"] == "failed"
    )

    fake.fail_download = set()
    report = await sync()
    assert report.media_downloaded == 1
    msg = history()[1]
    assert msg["media_status"] == "downloaded" and "incomplete" not in msg
    assert (
        tmp_path / "media" / archive.media(CHAT_ID, 2)["path"]
    ).read_bytes() == b"media-bytes-2"


async def test_media_skipped_by_settings_downloads_once_allowed(env):
    fake, _, settings, sync, history = env
    settings.download_media = True
    settings.download_file_types.photos = False
    fake.add(CHANNEL, *messages(range(1, 2)))
    fake.edit(CHANNEL, 1, media=photo())
    report = await sync()
    assert report.media_skipped == 1 and history()[0]["media_status"] == "skipped"
    settings.download_file_types.photos = True
    report = await sync()
    assert report.media_downloaded == 1 and history()[0]["media_status"] == "downloaded"


async def test_upgraded_group_also_archives_its_old_history(env):
    fake, _, _, sync, history = env
    fake.add(GROUP, *messages(range(1, 6), chat=GROUP))
    fake.add(SUPERGROUP, *messages(range(1, 4), chat=SUPERGROUP))
    fake.migrated_from[-1000000007000] = -6000
    report = await sync(SUPERGROUP)
    assert report.migrated_from == -6000
    assert ids(history(-1000000007000)) == [1, 2, 3]
    assert ids(history(-6000)) == [1, 2, 3, 4, 5]


def test_archive_refuses_a_different_account(tmp_path):
    archive = Archive(tmp_path / "a.db")
    archive.bind_account(1)
    archive.bind_account(1)
    with pytest.raises(AccountMismatch):
        archive.bind_account(2)


async def test_refresh_window_edge_does_not_invent_deletions(env, monkeypatch):
    """A message dated exactly on the refresh cutoff second is read, so never marked deleted."""
    from telegram_scraper import sync as sync_module

    fixed_now = NOW.replace(microsecond=700_000)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    fake, _, settings, sync, history = env
    cutoff = NOW - timedelta(days=settings.refresh_days)
    fake.add(
        CHANNEL,
        text_msg(CHANNEL, 1, "old", date=cutoff - timedelta(days=1)),
        text_msg(CHANNEL, 2, "on the edge", date=cutoff),
        text_msg(CHANNEL, 3, "new", date=NOW),
    )
    monkeypatch.setattr(sync_module, "datetime", FixedDatetime)
    await sync()
    report = await sync()
    assert report.deleted_messages == 0
    assert not any(m.get("deleted") for m in history())


async def test_problems_persist_across_syncs(env):
    fake, _, _, sync, _ = env
    fake.add(CHANNEL, *messages(range(1, 4)))
    fake.edit(CHANNEL, 2, media=t.MessageMediaUnsupported())
    first = await sync()
    second = await sync()  # message 2 is not re-read here, yet still unarchivable
    expected = [
        {
            "dialog_id": CHAT_ID,
            "message_id": 2,
            "problem": "unsupported_by_telegram_library",
        }
    ]
    assert first.problems == expected and second.problems == expected


async def test_missing_messages_fail_the_count_check(env):
    fake, _, _, sync, _ = env
    fake.add(CHANNEL, *messages(range(1, 4)))
    fake.extra_total = 2  # Telegram says 5, but only returns 3
    report = await sync()
    assert report.problems == [
        {
            "dialog_id": CHAT_ID,
            "message_id": 0,
            "problem": "count_mismatch: Telegram has 5 messages, archive 3",
        }
    ]
    fake.extra_total = 0
    assert (await sync()).problems == []


async def test_media_that_disappears_does_not_break_sync(env):
    fake, archive, settings, sync, history = env
    fake.add(CHANNEL, *messages(range(1, 2)))
    fake.edit(CHANNEL, 1, media=photo())
    await sync()  # downloads are off: skipped
    fake.edit(
        CHANNEL, 1, media=t.MessageMediaPhoto(photo=None, ttl_seconds=5)
    )  # expired
    settings.download_media = True
    report = await sync()
    assert archive.media(CHAT_ID, 1)["status"] == "unavailable"
    assert report.problems[0]["problem"] == "media_unavailable"
    assert history()[0]["incomplete"] == "media_unavailable"


async def test_media_step_can_be_skipped(env):
    fake, archive, settings, sync, _ = env
    settings.download_media = True
    fake.add(CHANNEL, *messages(range(1, 2)))
    fake.edit(CHANNEL, 1, media=photo())
    await sync(media=False)
    assert archive.media(CHAT_ID, 1)["status"] == "pending"
    await sync()
    assert archive.media(CHAT_ID, 1)["status"] == "downloaded"
