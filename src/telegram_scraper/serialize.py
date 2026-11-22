"""Raw Telegram message (Telethon `to_dict()` after a JSON round trip) -> API dict.

This is the ONLY module that knows Telegram's message shapes. History (from the
archive) and search (live) both go through `serialize()`.

Old flat fields are always present (null when absent). New fields are only
present when they carry something.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timezone

from .media import media_info

PeerLookup = Callable[[int | None], dict | None]

# Non-file media we know how to represent. Anything else is flagged `incomplete`
# so a new Telegram feature never silently looks like an empty message.
KNOWN_ATTACHMENTS = {
    "MessageMediaGeo",
    "MessageMediaGeoLive",
    "MessageMediaVenue",
    "MessageMediaContact",
    "MessageMediaDice",
    "MessageMediaGame",
    "MessageMediaInvoice",
    "MessageMediaStory",
    "MessageMediaGiveaway",
    "MessageMediaGiveawayResults",
}
_OTHER_KNOWN_MEDIA = {
    "MessageMediaPhoto",
    "MessageMediaDocument",
    "MessageMediaPoll",
    "MessageMediaWebPage",
    "MessageMediaEmpty",
}


def iso(ts: int | None) -> str | None:
    return (
        datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if ts
        else None
    )


def snake(type_name: str, prefix: str) -> str:
    """'MessageActionPinMessage', 'MessageAction' -> 'pin_message'."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", type_name.removeprefix(prefix)).lower()


def peer_id(peer: dict | None) -> int | None:
    """Marked id of a Peer dict, same numbers as telethon.utils.get_peer_id."""
    if not peer:
        return None
    if peer["_"] == "PeerUser":
        return peer["user_id"]
    if peer["_"] == "PeerChat":
        return -peer["chat_id"]
    return -(10**12 + peer["channel_id"])


def display_name(p: dict | None) -> str | None:
    if not p:
        return None
    return (
        p.get("title")
        or " ".join(x for x in (p.get("first_name"), p.get("last_name")) if x)
        or None
    )


def _clean(d: dict) -> dict:
    """Drop the TL type tag and empty values (but keep 0 and 0.0)."""
    return {
        k: v
        for k, v in d.items()
        if k != "_" and v is not None and v is not False and v != [] and v != ""
    }


def _text(value) -> str:
    """Layer 178+ wraps poll texts in TextWithEntities."""
    return value["text"] if isinstance(value, dict) else value


def incomplete(raw: dict, media_status: str | None = None) -> str | None:
    """Why this message is not fully archived, or None. Also used by sync to report problems."""
    media = raw.get("media")
    kind = media and media["_"]
    if kind == "MessageMediaUnsupported":
        return "unsupported_by_telegram_library"
    if (
        kind in ("MessageMediaPhoto", "MessageMediaDocument")
        and media_info(media) is None
    ):
        return "media_unavailable"
    if kind and kind not in KNOWN_ATTACHMENTS | _OTHER_KNOWN_MEDIA:
        return f"unknown_media:{kind}"
    if media_status and media_status.startswith("failed"):
        return f"media_{media_status}"
    return None


def serialize(
    raw: dict,
    sender_id: int | None,
    peer: PeerLookup,
    media_status: str | None = None,
    deleted: bool = False,
) -> dict:
    sender = peer(sender_id)
    out = {
        "message_id": raw["id"],
        "date": iso(raw["date"]),
        "edit_date": iso(raw.get("edit_date")),
        "sender_id": sender_id,
        "first_name": sender and sender.get("first_name"),
        "last_name": sender and sender.get("last_name"),
        "username": sender and sender.get("username"),
        "message": raw.get("message") or "",
        "post_author": raw.get("post_author"),
        **_forward(raw.get("fwd_from"), peer),
        **_media(raw.get("media"), media_status),
    }
    if raw["_"] == "MessageService":
        out.update(_reply(None), type="service", **_action(raw))
    else:
        out.update(_reply(raw.get("reply_to")), type="message")
    extras = {
        "sender_name": display_name(sender),
        "entities": [
            {"type": snake(e["_"], "MessageEntity"), **_clean(e)}
            for e in raw.get("entities") or []
        ],
        "reactions": _reactions(raw.get("reactions")),
        "incomplete": incomplete(raw, media_status),
        "deleted": deleted,
    }
    out.update({k: v for k, v in extras.items() if v})
    return out


def serialize_row(row, peer: PeerLookup) -> dict:
    """An `Archive.read()` row -> API dict."""
    return serialize(
        json.loads(row["raw"]),
        row["sender_id"],
        peer,
        row["media_status"],
        bool(row["deleted"]),
    )


def _forward(fwd: dict | None, peer: PeerLookup) -> dict:
    out = {
        "is_forwarded": 0,
        "forwarded_from_channel_id": None,
        "forwarded_from_user_id": None,
        "forwarded_from_name": None,
        "forwarded_from_date": None,
    }
    if not fwd:
        return out
    source = fwd.get("from_id") or fwd.get("saved_from_peer")
    pid = peer_id(source)
    out.update(
        is_forwarded=1,
        forwarded_from_name=display_name(peer(pid)) or fwd.get("from_name"),
        forwarded_from_date=iso(fwd.get("date")),
    )
    if source:
        key = (
            "forwarded_from_user_id"
            if source["_"] == "PeerUser"
            else "forwarded_from_channel_id"
        )
        out[key] = pid
    return out


def _reply(reply: dict | None) -> dict:
    out = {"reply_to": None, "reply_quote_text": None, "reply_quote_offset": None}
    if not reply:
        return out
    if reply["_"] == "MessageReplyStoryHeader":
        out["reply_to_story"] = {
            "peer_id": peer_id(reply["peer"]),
            "story_id": reply["story_id"],
        }
        return out
    reply_to = reply.get("reply_to_msg_id")
    if reply.get("forum_topic"):
        # In forums a plain message in a topic points at the topic root; that is not a reply.
        out["topic_id"] = reply.get("reply_to_top_id") or reply_to
        if not reply.get("reply_to_top_id"):
            reply_to = None
    out.update(
        reply_to=reply_to,
        reply_quote_text=reply.get("quote_text"),
        reply_quote_offset=reply.get("quote_offset"),
    )
    if reply.get("reply_to_peer_id"):
        out["reply_to_peer"] = peer_id(
            reply["reply_to_peer_id"]
        )  # reply into another chat
    return out


def _action(raw: dict) -> dict:
    action = raw["action"]
    data = _clean(action)
    if (raw.get("reply_to") or {}).get("reply_to_msg_id"):
        data["message_id"] = raw["reply_to"][
            "reply_to_msg_id"
        ]  # e.g. the pinned message
    return {"action": snake(action["_"], "MessageAction"), "action_data": data}


def _media(media: dict | None, status: str | None) -> dict:
    out = {"media_type": None, "media_size": None, "media_original_filename": None}
    info = media_info(media)
    kind = media["_"] if media else None
    if info:
        out.update(
            media_type=info.category.value,
            media_size=info.size,
            media_original_filename=info.filename,
        )
        extra = {
            "media_mime_type": info.mime_type,
            "media_duration": info.duration,
            "media_status": status and status.split(":")[0],
        }
        out.update({k: v for k, v in extra.items() if v is not None})
    elif kind == "MessageMediaPoll":
        out["poll"] = _poll(media)
    elif kind == "MessageMediaWebPage":
        page = media["webpage"]
        out["attachment"] = _clean(
            {
                "type": "webpage",
                "url": page.get("url"),
                "title": page.get("title"),
                "description": page.get("description"),
            }
        )
    elif kind in KNOWN_ATTACHMENTS:
        out["attachment"] = {"type": snake(kind, "MessageMedia"), **_clean(media)}
    return out


def _poll(media: dict) -> dict:
    poll, results = media["poll"], media.get("results") or {}
    voters = {r["option"]: r["voters"] for r in results.get("results") or []}
    return {
        "question": _text(poll["question"]),
        "answers": [
            {"text": _text(a["text"]), "voters": voters.get(a["option"])}
            for a in poll["answers"]
        ],
        "total_voters": results.get("total_voters"),
        "closed": bool(poll.get("closed")),
        "quiz": bool(poll.get("quiz")),
        "multiple_choice": bool(poll.get("multiple_choice")),
    }


def _reaction(reaction: dict) -> dict:
    if reaction["_"] == "ReactionEmoji":
        return {"emoji": reaction["emoticon"]}
    if reaction["_"] == "ReactionCustomEmoji":
        return {"custom_emoji_id": reaction["document_id"]}
    if reaction["_"] == "ReactionPaid":
        return {"paid": True}
    return {"reaction": reaction["_"]}


def _reactions(reactions: dict | None) -> list | None:
    if not reactions:
        return None
    recent = defaultdict(list)
    for r in reactions.get("recent_reactions") or []:
        key = json.dumps(_reaction(r["reaction"]), sort_keys=True)
        recent[key].append({"from_id": peer_id(r["peer_id"]), "date": iso(r["date"])})
    out = []
    for r in reactions.get("results") or []:
        item = {**_reaction(r["reaction"]), "count": r["count"]}
        if who := recent.get(json.dumps(_reaction(r["reaction"]), sort_keys=True)):
            item["recent"] = who
        out.append(item)
    return out or None
