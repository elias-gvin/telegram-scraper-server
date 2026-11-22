"""HTTP contract: history stream, sync, files, search, access control."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from telegram_scraper.config import MediaCategory
from telegram_scraper.media import MediaInfo, download
from telegram_scraper.server import create_app

from .conftest import API, TOKEN, FakeHolder
from .fake_telegram import ALICE, BOB, CHANNEL, photo, text_msg

CHAT = -1000000005000  # CHANNEL, marked id


def day(d: int) -> datetime:
    return datetime(2025, 1, d, 12, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def chat(tg):
    tg.add(
        CHANNEL,
        *[text_msg(CHANNEL, i, f"message {i}", date=day(i)) for i in range(1, 11)],
    )


def events(body: str) -> list[tuple[str, dict]]:
    """Parse an SSE body into (event, data) pairs; plain data events are 'data'."""
    out = []
    for block in body.strip().split("\n\n"):
        event, data = "data", None
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        out.append((event, data))
    return out


async def history(client, **params):
    resp = await client.get(f"{API}/history/{CHAT}", params=params)
    assert resp.status_code == 200
    return events(resp.text)


def ids_of(evts) -> list[int]:
    return [m["message_id"] for e, d in evts if e == "data" for m in d["messages"]]


# --- history ----------------------------------------------------------------


async def test_history_streams_everything_and_ends_with_done(client):
    evts = await history(client, chunk_size=3)
    assert ids_of(evts) == list(range(1, 11))
    assert [e for e, _ in evts].count("data") == 4  # 3+3+3+1
    event, done = evts[-1]
    assert (
        event == "done" and done["count"] == 10 and done["sync"]["new_messages"] == 10
    )


async def test_history_date_bounds_are_inclusive(client):
    evts = await history(
        client, start_date="2025-01-03", end_date="2025-01-05 12:00:00"
    )
    assert ids_of(evts) == [3, 4, 5]


async def test_history_newest_first(client):
    assert ids_of(await history(client, reverse="false")) == list(range(10, 0, -1))


async def test_second_request_is_served_from_archive(client, tg):
    await history(client)
    tg.chats[CHAT].clear()  # Telegram no longer has them; the archive does
    assert ids_of(await history(client)) == list(range(1, 11))


async def test_failure_ends_the_stream_with_an_error_event(client, tg):
    tg.fail_after = 2
    evts = await history(client)
    assert evts[-1][0] == "error" and "ConnectionError" in evts[-1][1]["error"]
    assert all(e != "done" for e, _ in evts)


async def test_unknown_dialog_is_404(client):
    resp = await client.get(f"{API}/history/-1009999")
    assert resp.status_code == 404


async def test_bad_date_is_400(client):
    resp = await client.get(
        f"{API}/history/{CHAT}", params={"start_date": "01/02/2025"}
    )
    assert resp.status_code == 400


# --- sync + files -------------------------------------------------------------


async def test_sync_reports_counts_and_files_are_served(client, tg, server_config):
    server_config.settings.download_media = True
    tg.edit(CHANNEL, 5, media=photo())
    report = (await client.post(f"{API}/sync/{CHAT}")).json()
    assert (
        report["new_messages"] == 10
        and report["media_downloaded"] == 1
        and report["problems"] == []
    )

    resp = await client.get(f"{API}/files/{CHAT}/5")
    assert resp.status_code == 200 and resp.content == b"media-bytes-5"
    assert (await client.get(f"{API}/files/{CHAT}/6")).status_code == 404  # no media


async def test_skipped_media_is_404_with_reason(client, tg):
    tg.edit(CHANNEL, 5, media=photo())  # download_media is off in the test config
    await client.post(f"{API}/sync/{CHAT}")
    resp = await client.get(f"{API}/files/{CHAT}/5")
    assert resp.status_code == 404 and "skipped" in resp.json()["detail"]


async def test_failed_redownload_keeps_the_old_file(tmp_path):
    info = MediaInfo(
        key=1, category=MediaCategory.PHOTOS, size=1, mime_type="image/jpeg"
    )

    class Msg:
        id = 7

        async def download_media(self, file):
            raise ConnectionError("network down")

    target = tmp_path / "7-photo.jpg"
    target.write_bytes(b"good copy")
    with pytest.raises(ConnectionError):
        await download(Msg(), info, tmp_path)
    assert target.read_bytes() == b"good copy"
    assert list(tmp_path.iterdir()) == [target]  # no leftover partial file


# --- search -----------------------------------------------------------------


async def test_search_uses_history_shape_and_sane_date_bounds(client, tg):
    tg.add(
        CHANNEL,
        text_msg(CHANNEL, 11, "hello world", sender=ALICE, date=day(11)),
        text_msg(CHANNEL, 12, "hello again", sender=BOB, date=day(12)),
    )
    resp = await client.get(
        f"{API}/search/messages/{CHAT}",
        params={"q": "hello", "start_date": "2025-01-12", "end_date": "2025-01-13"},
    )
    data = resp.json()
    assert data["count"] == 1
    hit = data["results"][0]
    assert (
        hit["message_id"] == 12
        and hit["first_name"] == "Bob"
        and hit["type"] == "message"
    )
    assert hit["dialog_id"] == CHAT and hit["dialog_name"] == "Test Channel"


# --- access control -----------------------------------------------------------


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}]
)
@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", f"/history/{CHAT}"),
        ("POST", f"/sync/{CHAT}"),
        ("GET", f"/files/{CHAT}/1"),
        ("GET", "/search/dialogs"),
        ("GET", "/search/messages?q=x"),
        ("GET", "/folders"),
        ("GET", "/settings"),
        ("PATCH", "/settings"),
        ("POST", "/auth/qr"),
        ("GET", "/auth/qr/abc"),
    ],
)
async def test_every_api_route_needs_the_token(app, method, path, headers):
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp = await c.request(method, API + path, headers=headers)
    assert resp.status_code == 401


async def test_cross_origin_requests_are_not_allowed(client):
    resp = await client.options(
        f"{API}/settings",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert "access-control-allow-origin" not in resp.headers


def test_token_file_is_created_once_and_private(tmp_path):
    from telegram_scraper.config import ServerConfig

    first = ServerConfig(api_id="1", api_hash="x", data_dir=tmp_path)
    second = ServerConfig(api_id="1", api_hash="x", data_dir=tmp_path)
    assert first.token == second.token and len(first.token) > 30
    assert (tmp_path / "token").stat().st_mode & 0o077 == 0


def test_bad_account_name_is_rejected():
    from telegram_scraper.telegram import check_account_name

    for bad in ("../x", "a/b", "", "x" * 65):
        with pytest.raises(ValueError):
            check_account_name(bad)
    assert check_account_name("odetilie") == "odetilie"


async def test_archive_of_another_account_is_refused(server_config, tg, monkeypatch):
    """The real client holder binds the archive to the account on connect."""
    from httpx import ASGITransport, AsyncClient

    from telegram_scraper import telegram
    from telegram_scraper.archive import Archive

    async def fake_connect(*_):
        return tg  # logged in as fake_telegram.ME (id 1)

    monkeypatch.setattr(telegram, "connect", fake_connect)
    Archive(server_config.archive_path).bind_account(
        424242
    )  # archive made by another account
    app = create_app(server_config)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as c:
        resp = await c.post(f"{API}/sync/{CHAT}")
    assert resp.status_code == 409 and "424242" in resp.json()["detail"]


# --- failures ------------------------------------------------------------------


async def test_one_broken_message_does_not_break_sync_or_history(
    client, tg, monkeypatch
):
    """A message our code cannot handle is flagged and reported; everything else flows."""
    import sys

    serialize_module = sys.modules["telegram_scraper.serialize"]
    sync_module = sys.modules["telegram_scraper.sync"]

    real_dump, real_forward = sync_module.dump_raw, serialize_module._forward

    def dump(msg):  # storing message 3 fails
        if msg.id == 3:
            raise TypeError("cannot store this")
        return real_dump(msg)

    def forward(fwd, peer):  # rendering message 7 fails (it is forwarded)
        if fwd:
            raise KeyError("surprise")
        return real_forward(fwd, peer)

    from telethon.tl import types as t

    tg.edit(CHANNEL, 7, fwd_from=t.MessageFwdHeader(date=day(1), from_name="X"))
    monkeypatch.setattr(sync_module, "dump_raw", dump)
    monkeypatch.setattr(serialize_module, "_forward", forward)

    report = (await client.post(f"{API}/sync/{CHAT}")).json()
    problems = {p["message_id"]: p["problem"] for p in report["problems"]}
    assert problems[3] == "raw_error: TypeError: cannot store this"
    assert problems[7].startswith("serializer_error: KeyError")

    evts = await history(client)
    assert evts[-1][0] == "done" and evts[-1][1]["count"] == 10
    got = {m["message_id"]: m for e, d in evts if e == "data" for m in d["messages"]}
    assert got[3]["incomplete"].startswith("raw_error") and got[7][
        "incomplete"
    ].startswith("serializer_error")
    assert got[8]["message"] == "message 8"


async def test_unexpected_error_returns_its_type_and_message(app, tg, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    async def boom(*a, **kw):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(tg, "get_entity", boom)
    # The server sends the 500 response, then re-raises for its log; don't re-raise here.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as c:
        resp = await c.post(f"{API}/sync/{CHAT}")
    assert (
        resp.status_code == 500
        and resp.json()["detail"] == "RuntimeError: disk on fire"
    )


async def test_revoked_session_is_401_and_drops_the_client(server_config, tg):
    from httpx import ASGITransport, AsyncClient
    from telethon.errors import AuthKeyUnregisteredError

    class Holder(FakeHolder):
        resets = 0

        async def reset(self):
            Holder.resets += 1

    async def revoked(*a, **kw):
        raise AuthKeyUnregisteredError(request=None)
        yield  # pragma: no cover

    tg.iter_messages = revoked
    app = create_app(server_config, telegram=Holder(tg))
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as c:
        resp = await c.post(f"{API}/sync/{CHAT}")
        assert resp.status_code == 401 and "log in again" in resp.json()["detail"]
        evts = events((await c.get(f"{API}/history/{CHAT}")).text)
    assert evts[-1][0] == "error" and "AuthKeyUnregistered" in evts[-1][1]["error"]
    assert Holder.resets == 2


async def test_unknown_ids_refresh_the_dialog_list_at_most_once(client, tg):
    calls = []

    async def get_dialogs(*a, **kw):
        calls.append(1)
        return []

    tg.get_dialogs = get_dialogs
    for bad in (-1009999, -1008888, 123456789):
        assert (await client.get(f"{API}/history/{bad}")).status_code == 404
    assert len(calls) == 1


async def test_id_telegram_rejects_is_404(client, tg, monkeypatch):
    from telethon.errors import ChatIdInvalidError

    async def invalid(*a, **kw):
        raise ChatIdInvalidError(request=None)

    monkeypatch.setattr(tg, "get_entity", invalid)
    resp = await client.get(f"{API}/history/-1009999")
    assert resp.status_code == 404 and "not found" in resp.json()["detail"]


async def test_bare_id_is_rejected_with_a_hint(client, tg, monkeypatch):
    async def guess(x):  # real Telethon may map a bare number to a cached channel
        return CHANNEL

    monkeypatch.setattr(tg, "get_entity", guess)
    resp = await client.get(f"{API}/history/5000")
    assert resp.status_code == 404 and str(CHAT) in resp.json()["detail"]
