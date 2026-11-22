"""Every kind of message must survive sync -> archive -> API without losing details.

One "catalog" chat holds every message kind we know of; each is synced and then
read back exactly as the history API returns it.
"""

from __future__ import annotations

import pytest
from telethon.tl import types as t

from telegram_scraper.archive import Archive
from telegram_scraper.config import RuntimeSettings
from telegram_scraper.serialize import serialize_row
from telegram_scraper.sync import sync_dialog

from .fake_telegram import (
    ALICE,
    BOB,
    CHANNEL,
    GROUP,
    T0,
    FakeTelegram,
    document,
    peer,
    photo,
    service_msg,
    text_msg,
)


def twe(text):
    return t.TextWithEntities(text=text, entities=[])


CATALOG = [
    text_msg(GROUP, 1, "plain text"),
    text_msg(
        GROUP,
        2,
        "see docs, bold",
        entities=[
            t.MessageEntityItalic(offset=0, length=3),
            t.MessageEntityTextUrl(offset=4, length=4, url="https://example.com/docs"),
            t.MessageEntityBold(offset=10, length=4),
        ],
    ),
    text_msg(
        GROUP,
        3,
        "a reply",
        reply_to=t.MessageReplyHeader(
            reply_to_msg_id=1, quote_text="plain", quote_offset=0
        ),
    ),
    text_msg(GROUP, 4, "edited", edit_date=T0.replace(year=2026)),
    text_msg(
        GROUP, 5, "from bob", fwd_from=t.MessageFwdHeader(date=T0, from_id=peer(BOB))
    ),
    text_msg(
        GROUP,
        6,
        "from channel",
        fwd_from=t.MessageFwdHeader(date=T0, from_id=peer(CHANNEL), channel_post=9),
    ),
    text_msg(
        GROUP,
        7,
        "from hidden",
        fwd_from=t.MessageFwdHeader(date=T0, from_name="Hidden Person"),
    ),
    service_msg(
        GROUP,
        8,
        t.MessageActionPinMessage(),
        reply_to=t.MessageReplyHeader(reply_to_msg_id=1),
    ),
    service_msg(GROUP, 9, t.MessageActionChatEditTitle(title="New title")),
    service_msg(
        GROUP,
        10,
        t.MessageActionPhoneCall(
            call_id=1, duration=60, reason=t.PhoneCallDiscardReasonHangup()
        ),
    ),
    text_msg(
        GROUP,
        11,
        "",
        media=t.MessageMediaPoll(
            poll=t.Poll(
                id=1,
                question=twe("Lunch?"),
                answers=[t.PollAnswer(twe("Yes"), b"0"), t.PollAnswer(twe("No"), b"1")],
            ),
            results=t.PollResults(
                results=[t.PollAnswerVoters(option=b"0", voters=3)], total_voters=3
            ),
        ),
    ),
    text_msg(
        GROUP,
        12,
        "nice",
        reactions=t.MessageReactions(
            results=[t.ReactionCount(reaction=t.ReactionEmoji("👍"), count=2)],
            recent_reactions=[
                t.MessagePeerReaction(
                    peer_id=peer(BOB), date=T0, reaction=t.ReactionEmoji("👍")
                )
            ],
        ),
    ),
    text_msg(GROUP, 13, "a photo", media=photo(size=1234)),
    text_msg(
        GROUP,
        14,
        "",
        media=document(
            t.DocumentAttributeAudio(duration=5, voice=True), mime="audio/ogg"
        ),
    ),
    text_msg(
        GROUP,
        15,
        "",
        media=document(
            t.DocumentAttributeAudio(duration=200),
            t.DocumentAttributeFilename("song.mp3"),
            mime="audio/mpeg",
        ),
    ),
    text_msg(
        GROUP,
        16,
        "",
        media=t.MessageMediaGeo(geo=t.GeoPoint(long=30.5, lat=50.4, access_hash=0)),
    ),
    text_msg(GROUP, 17, "", media=t.MessageMediaUnsupported()),
    text_msg(
        GROUP, 18, "", media=t.MessageMediaPaidMedia(stars_amount=5, extended_media=[])
    ),
    text_msg(
        GROUP,
        19,
        "",
        media=t.MessageMediaContact(
            phone_number="+100", first_name="Carol", last_name="", vcard="", user_id=0
        ),
    ),
    text_msg(
        GROUP, 21, "", media=t.MessageMediaPhoto(photo=None, ttl_seconds=10)
    ),  # expired self-destructing
    text_msg(CHANNEL, 20, "channel post", sender=None, post_author="Editor"),
]

# message_id -> fields that must appear with exactly these values
EXPECTED = {
    1: {
        "type": "message",
        "message": "plain text",
        "sender_id": ALICE.id,
        "first_name": "Alice",
        "reply_to": None,
    },
    2: {
        "entities": [
            {"type": "italic", "offset": 0, "length": 3},
            {
                "type": "text_url",
                "offset": 4,
                "length": 4,
                "url": "https://example.com/docs",
            },
            {"type": "bold", "offset": 10, "length": 4},
        ]
    },
    3: {"reply_to": 1, "reply_quote_text": "plain", "reply_quote_offset": 0},
    4: {"edit_date": "2026-01-01T00:00:00Z"},
    5: {
        "is_forwarded": 1,
        "forwarded_from_user_id": BOB.id,
        "forwarded_from_name": "Bob",
        "forwarded_from_date": "2025-01-01T00:00:00Z",
    },
    6: {
        "is_forwarded": 1,
        "forwarded_from_channel_id": -1000000005000,
        "forwarded_from_name": "Test Channel",
    },
    7: {
        "is_forwarded": 1,
        "forwarded_from_name": "Hidden Person",
        "forwarded_from_user_id": None,
    },
    8: {
        "type": "service",
        "action": "pin_message",
        "action_data": {"message_id": 1},
        "reply_to": None,
    },
    9: {
        "type": "service",
        "action": "chat_edit_title",
        "action_data": {"title": "New title"},
    },
    10: {"type": "service", "action": "phone_call"},
    11: {
        "poll": {
            "question": "Lunch?",
            "answers": [{"text": "Yes", "voters": 3}, {"text": "No", "voters": None}],
            "total_voters": 3,
            "closed": False,
            "quiz": False,
            "multiple_choice": False,
        }
    },
    12: {
        "reactions": [
            {
                "emoji": "👍",
                "count": 2,
                "recent": [{"from_id": BOB.id, "date": "2025-01-01T00:00:00Z"}],
            }
        ]
    },
    13: {"media_type": "photos", "media_size": 1234, "message": "a photo"},
    14: {
        "media_type": "voice_messages",
        "media_duration": 5,
        "media_mime_type": "audio/ogg",
    },
    15: {"media_type": "audio", "media_original_filename": "song.mp3"},
    16: {"attachment": {"type": "geo", "geo": {"long": 30.5, "lat": 50.4}}},
    17: {"incomplete": "unsupported_by_telegram_library"},
    18: {"incomplete": "unknown_media:MessageMediaPaidMedia"},
    19: {
        "attachment": {"type": "contact", "phone_number": "+100", "first_name": "Carol"}
    },
    21: {"incomplete": "media_unavailable"},
    20: {
        "sender_id": -1000000005000,
        "sender_name": "Test Channel",
        "post_author": "Editor",
    },
}


def contains(got, expected) -> bool:
    """Equal, except that dicts in `got` may carry extra keys."""
    if isinstance(expected, dict):
        return isinstance(got, dict) and all(
            contains(got.get(k), v) for k, v in expected.items()
        )
    return got == expected


@pytest.fixture
async def synced(tmp_path):
    fake = FakeTelegram()
    fake.add(GROUP, *[m for m in CATALOG if m.id != 20])
    fake.add(CHANNEL, *[m for m in CATALOG if m.id == 20])
    archive = Archive(tmp_path / "archive.db")
    settings = RuntimeSettings(download_media=False)
    reports = [
        await sync_dialog(fake, archive, chat, settings, tmp_path / "media")
        for chat in (GROUP, CHANNEL)
    ]
    messages = {}
    for chat in (-6000, -1000000005000):
        for rows in archive.read(chat, 0, 2**40):
            messages.update(
                {r["message_id"]: serialize_row(r, archive.peer) for r in rows}
            )
    return messages, reports


@pytest.mark.parametrize("message_id", sorted(EXPECTED))
async def test_message_kind_survives(synced, message_id):
    messages, _ = synced
    got = messages[message_id]
    for field, value in EXPECTED[message_id].items():
        assert contains(got.get(field), value), (
            f"message {message_id}: {field} = {got.get(field)!r}"
        )


async def test_nothing_looks_like_an_empty_plain_message(synced):
    messages, _ = synced
    content = ("media_type", "poll", "attachment", "incomplete", "action")
    empty = [
        m["message_id"]
        for m in messages.values()
        if not m["message"] and not any(m.get(k) for k in content)
    ]
    assert empty == []


async def test_unreadable_messages_are_reported(synced):
    _, reports = synced
    problems = {(p["message_id"], p["problem"]) for r in reports for p in r.problems}
    assert problems == {
        (17, "unsupported_by_telegram_library"),
        (18, "unknown_media:MessageMediaPaidMedia"),
        (21, "media_unavailable"),
    }


async def test_raw_message_is_kept(tmp_path):
    """The full Telegram message is stored, so fields we don't expose yet can be re-parsed later."""
    fake = FakeTelegram()
    fake.add(GROUP, text_msg(GROUP, 1, "x", grouped_id=777, via_bot_id=42))
    archive = Archive(tmp_path / "a.db")
    await sync_dialog(
        fake, archive, GROUP, RuntimeSettings(download_media=False), tmp_path
    )
    raw = next(archive.read(-6000, 0, 2**40))[0]["raw"]
    assert '"grouped_id": 777' in raw and '"via_bot_id": 42' in raw
