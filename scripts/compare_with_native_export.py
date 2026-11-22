"""Compare an archived dialog with a Telegram Desktop JSON export of the same chat.

    python scripts/compare_with_native_export.py DATA_DIR DIALOG_ID EXPORT_DIR

Prints mismatch COUNTS per field (never message content). Only native messages
inside the archived date range are compared. Native fields with no counterpart
here (e.g. stickers' emoji) are not checked.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from telegram_scraper.archive import Archive
from telegram_scraper.serialize import serialize_row

ENTITY_TYPES = {  # native text_entities type -> ours
    "bold": "bold",
    "italic": "italic",
    "strikethrough": "strike",
    "underline": "underline",
    "code": "code",
    "pre": "pre",
    "blockquote": "blockquote",
    "custom_emoji": "custom_emoji",
    "spoiler": "spoiler",
    "text_link": "text_url",
    "mention_name": "mention_name",
}
MEDIA_TYPES = {  # native media_type -> ours
    "voice_message": "voice_messages",
    "video_message": "video_messages",
    "video_file": "videos",
    "audio_file": "audio",
    "sticker": "stickers",
    "animation": "gifs",
}


def plain(text) -> str:
    return (
        text
        if isinstance(text, str)
        else "".join(p if isinstance(p, str) else p["text"] for p in text)
    )


def native_peer(value: str | None) -> int | None:
    """'user123' -> 123, 'channel123' -> -100123, 'chat123' -> -123."""
    if not value:
        return None
    for prefix, convert in (
        ("user", int),
        ("channel", lambda n: -(10**12 + int(n))),
        ("chat", lambda n: -int(n)),
    ):
        if value.startswith(prefix):
            return convert(value[len(prefix) :])
    return None


def compare(native: dict, ours: dict, c: Counter):
    if native["type"] == "service":
        c["service_messages"] += 1
        c["service_ok"] += ours.get("type") == "service" and bool(ours.get("action"))
        c["service_with_reply_to (bad)"] += ours["reply_to"] is not None
        return
    c["messages"] += 1
    c["text_mismatch"] += plain(native.get("text", "")) != ours["message"]
    c["sender_mismatch"] += native_peer(native.get("from_id")) != ours["sender_id"]
    if "edited" in native or ours["edit_date"]:
        c["edited"] += 1
        c["edited_mismatch"] += ("edited" in native) != bool(ours["edit_date"])
    c["reply_mismatch"] += native.get("reply_to_message_id") != ours["reply_to"]
    if "forwarded_from" in native or ours["is_forwarded"]:
        c["forwarded"] += 1
        c["forward_flag_mismatch"] += ("forwarded_from" in native) != bool(
            ours["is_forwarded"]
        )
        c["forward_name_mismatch"] += (
            native.get("forwarded_from") != ours["forwarded_from_name"]
        )
        ours_src = ours["forwarded_from_user_id"] or ours["forwarded_from_channel_id"]
        c["forward_source_mismatch"] += (
            native_peer(native.get("forwarded_from_id")) != ours_src
        )
    native_entities = Counter(
        ENTITY_TYPES[e["type"]]
        for e in native.get("text_entities", [])
        if e["type"] in ENTITY_TYPES
    )
    our_entities = Counter(e["type"] for e in ours.get("entities", []))
    if native_entities:
        c["formatted"] += 1
        c["formatting_lost"] += any(
            our_entities[k] < n for k, n in native_entities.items()
        )
    urls = [e["href"] for e in native.get("text_entities", []) if e["type"] == "text_link"]
    if urls:
        c["text_link"] += 1
        c["text_link_url_lost"] += any(
            u not in [e.get("url") for e in ours.get("entities", [])] for u in urls
        )
    if native.get("reactions"):
        c["reactions"] += 1
        c["reactions_count_mismatch"] += sum(
            r["count"] for r in native["reactions"]
        ) != sum(r["count"] for r in ours.get("reactions", []))
    if "poll" in native:
        c["polls"] += 1
        c["poll_mismatch"] += native["poll"]["question"] != (
            ours.get("poll") or {}
        ).get("question")
    if "rich_message" in native:
        c["rich_messages"] += 1
        c["rich_flagged_incomplete"] += bool(ours.get("incomplete"))
    native_media = MEDIA_TYPES.get(native.get("media_type")) or (
        "photos" if "photo" in native else None
    )
    if native_media is None and "file" in native:
        native_media = "files"
    if native_media or ours["media_type"]:
        c["media"] += 1
        c["media_type_mismatch"] += native_media != ours["media_type"]


def main(data_dir: str, dialog_id: str, export_dir: str):
    archive = Archive(Path(data_dir) / "archive.db")
    ours = {
        r["message_id"]: (r["date"], serialize_row(r, archive.peer))
        for batch in archive.read(int(dialog_id), 0, 2**40)
        for r in batch
    }
    if not ours:
        sys.exit("dialog not archived")
    lo, hi = min(d for d, _ in ours.values()), max(d for d, _ in ours.values())
    native = {
        m["id"]: m
        for m in json.loads((Path(export_dir) / "result.json").read_text())["messages"]
        if lo <= int(m["date_unixtime"]) <= hi
    }
    c = Counter(native=len(native), archived=len(ours))
    c["missing_in_archive"] = len(native.keys() - ours.keys())
    c["only_in_archive"] = len(ours.keys() - native.keys())
    for mid in native.keys() & ours.keys():
        compare(native[mid], ours[mid][1], c)
    for key in sorted(c):
        print(f"{key:32} {c[key]}")


if __name__ == "__main__":
    main(*sys.argv[1:])
