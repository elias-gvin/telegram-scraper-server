# Code Review: telegram-scraper-server

> **Historical document. Not instructions.** This reviews the code at commit `4cbade3`, before the archive rewrite (branch `rewrite-archive`). Most of the code it describes (`scraper.py`, `database/`, `X-Telegram-Username` auth, the date-range cache) no longer exists. Do not act on it; for the current design read `README.md` and the code. See `reviews/README.md`.

Reviewed at commit `4cbade3` ("fix(server): bind API to localhost by default").
Scope: the whole `src/telegram_scraper` tree plus tests, Dockerfile, packaging.
Review style: no diplomacy. If something is wrong, it's called wrong and I say why.

Note: I could not run the test suite (no poetry/venv in this environment), so
"tests pass" is taken from your CI, not verified by me. Everything below is from
reading the code.

---

## 1. What is this actually trying to do?

Strip the README fluff ("Smart caching", "Real-time streaming", emojis) and it's this:

> A single-user HTTP shim over Telethon that (a) lists/searches dialogs,
> (b) downloads message history for a dialog into a per-dialog SQLite file plus a
> media folder, and (c) serves that back over SSE, only hitting Telegram for
> date ranges it hasn't seen yet.

That's it. It's a cache in front of `client.iter_messages`. The auth, settings,
search and folders endpoints are all thin passthroughs. The only piece with any
actual logic — the only thing that justifies this project existing instead of a
30-line Telethon script — is the cache-gap computation in `scraper.py`.

So the review question is: **does the cache work?**

## 2. Does it achieve the goal?

**No. The core cache is broken by design.**

`operations.get_cached_date_range()` returns `(MIN(date), MAX(date))` over all
messages in the dialog. `find_gaps()` then only looks for gaps *before* that min
and *after* that max. The cache is modelled as a **single contiguous interval**.

```python
# scraper.py
def find_gaps(requested, cached_range):
    ...
    # Gap before cached range
    if requested.start < cached_range.start: ...
    # Gap after cached range
    if requested.end > cached_range.end: ...
```

Now do this:

1. `GET /history/123?start_date=2024-01-01&end_date=2024-01-31` → downloads January.
2. `GET /history/123?start_date=2024-06-01&end_date=2024-06-30` → downloads June.
3. `GET /history/123?start_date=2024-03-01&end_date=2024-03-31` → cached range is
   now `[Jan 1, Jun 30]`, March is "covered", the code reads the DB, finds zero
   rows, and **returns nothing**. Silently. Forever. With a 200.

Any two non-adjacent requests permanently poison the cache for the hole between
them. The only escape is `force_refresh=true`, which is the "cache" admitting it
doesn't work. There is a `merge_overlapping_ranges()` function sitting in
`scraper.py` that would be the first building block of a correct implementation
(track the set of fetched intervals) and **it is dead code — never called**.
Somebody knew what the right design was, wrote half of it, and then shipped the
wrong one.

Neither `tests/test_history.py::test_second_request_uses_cache` nor anything in
`test_sync.py` exercises the hole case. The tests test the happy path of the
wrong model.

On top of that, the cache has no invalidation story at all: edited messages,
deleted messages, and messages whose media was later removed are never
reconciled. `edit_date` is stored but nobody compares it.

**Verdict on the core goal: it's a cache that lies.** Everything else in this
review is secondary to fixing that.

## 3. The stupid, over-engineered and half-assed parts (named)

### 3.1 `scraper.py::download_from_telegram_batched` — 260 lines to build one dataclass

Lines 145–386. A single function that resolves forward entities with a
hand-rolled cache, classifies media, downloads media, builds `MessageData`,
batches, writes to DB, commits, yields, and then **duplicates the entire
"save the batch" block** for the tail (lines 331–355 vs 364–386). Copy-paste of
a 20-line transaction block inside one function.

The forwarded-message handling (lines 174–254) is 80 lines of
`getattr(fwd_from, ...)` soup with two nearly identical branches for
`PeerUser`/`PeerChannel`, each with its own try/except, each writing into the
same cache dict with different keys. Extract `resolve_forward(fwd_from) ->
ForwardInfo` and it's 25 lines.

### 3.2 "Atomic" batches that aren't

```python
# scraper.py:332
# Save batch to DB atomically
try:
    _batch_insert_messages(session, batch, dialog_id)   # auto_commit=False
    for msg in batch:
        if msg.media_type:
            msg.media_uuid = operations.store_media_with_uuid(...)  # <- commits internally
    session.commit()
```

`store_media_with_uuid()` calls `session.commit()` twice in its body (lines 272,
292). So the comment is false, the `auto_commit=False` plumbing in
`batch_upsert_messages` / `upsert_user` / `upsert_dialog` is pointless
ceremony, and a crash midway leaves messages committed with media rows for half
the batch. If you want atomicity, nobody inside the batch gets to call `commit()`.
Pick one.

### 3.3 `operations.py` — the ORM used as a dumber ORM

- `batch_upsert_messages(messages: Sequence[object])` then does
  `getattr(msg, "sender_id")`, `getattr(msg, "message")`, ... twenty times.
  It's a `MessageData`. Type it as `MessageData`. Deliberately erasing the type
  so you can use `getattr` is not "flexibility", it's hiding bugs from the linter.
- It does a `SELECT` per message to check existence, then a separate
  `SELECT` per user, per forward user, per forward channel. For a batch of 100
  that's ~400 round trips. SQLite has `INSERT ... ON CONFLICT DO UPDATE`. Use it.
- `iter_messages_in_range()` paginates with `OFFSET`. That's O(n²) over a large
  dialog. It's a single ordered scan; keep a cursor on `(date, id)` or just
  `yield_per`.
- Every cached row then triggers `get_media_info_by_uuid()` — an N+1 query per
  message. The join is one line.
- `get_cached_date_range()` imports `sqlalchemy.func` inside the function body
  with a comment "using raw SQL for efficiency". It's not raw SQL and the import
  placement is just lazy.

### 3.4 `session.py::get_session` creates a new engine every call

```python
@contextmanager
def get_session(db_path, check_same_thread=True):
    engine = get_engine(db_path, check_same_thread)   # <- new engine, new pool, new event listener
    with Session(engine) as session:
        yield session
```

`get_history` alone calls `get_engine()` once directly, then `get_session()`
twice → three engines per request. `find_media_by_uuid()` creates one engine
per dialog directory per file request. Nothing is ever disposed. Cache the
engine per `db_path` (a dict, ten lines) or stop pretending there's a pool.

Also `create_db_and_tables(engine)` runs `CREATE TABLE IF NOT EXISTS` on every
single history/sync request. Do it once when the dialog directory is created.

### 3.5 `files.py::find_media_by_uuid` — linear scan of every database on disk

To serve one file, open every `<dialog>/<dialog>.db` in `data/dialogs/` and
query each for the UUID until you get a hit. With 500 dialogs that's 500 SQLite
opens per image. The fix is trivial: **put the dialog_id in the URL**
(`/files/{dialog_id}/{uuid}`) or make the UUID self-describing, or keep one
index DB. You already have the dialog_id when you emit the `media_uuid` in the
history response; you threw it away.

### 3.6 Per-dialog SQLite databases — the wrong unit of storage

Each dialog gets its own `.db`, yet every `Message` row still carries a
`dialog_id` column, and the `dialogs` table inside dialog 123's database is
being filled with *other* channels (forward sources) via `upsert_dialog`. So you
have the fragmentation cost of one-DB-per-dialog and the schema of a shared DB,
and get the benefits of neither. Cross-dialog queries are impossible (hence 3.5),
and a single `WAL` per dialog means hundreds of `-wal`/`-shm` files.

One SQLite file. `messages(dialog_id, message_id)` composite PK. Done.

### 3.7 `dialogs.py::search_dialogs` — 300 lines, 20 query params, all in one function

- Iterates **every** dialog on every request, applies 12 filters inline, sorts
  in Python, paginates in Python, then fires `asyncio.gather` of
  `get_messages(limit=0)` for the whole page — up to 500 concurrent Telegram
  RPCs. That's how you get a `FloodWaitError`, which you then catch and turn
  into a 429. You built the flood and the umbrella.
- `min_messages`/`max_messages` filter on `dialog.message.id` (a proxy) while
  the response reports the real `total`. So filtering says one thing and the
  result says another.
- `DialogType.me` is an alias for `saved` that the docstring says "output always
  uses saved" — enum aliasing done by hand in the endpoint body. Just don't have
  two values.

### 3.8 `search.py` — inverted date semantics as a documented feature

```
start_date: "Only messages before this date ... Acts as upper bound"
end_date:   "Only messages after this date ... Acts as lower bound"
```

and the validation error is `"end_date must be before start_date"`. This is
Telethon's `offset_date` leaking straight into the public API and then being
*documented* instead of fixed. In `history.py`, `start_date` is the lower bound.
Same server, same parameter names, opposite meaning. Anyone using both
endpoints will get it wrong. Swap the mapping internally; keep the API sane.

### 3.9 Three copies of `_parse_date`, six copies of the FloodWait handler

`dialogs.py`, `history.py`, `search.py` each define their own date parser
(`search.py` even imports `datetime` inside the function). Every endpoint has an
identical

```python
except FloodWaitError as e:
    raise HTTPException(429, ..., headers={"Retry-After": str(e.seconds)})
```

That's what `@app.exception_handler(FloodWaitError)` exists for. One place.

### 3.10 The message shape is defined in four places and they disagree

1. `models.MessageData` (dataclass)
2. `database.models.Message` (SQLModel)
3. `operations.iter_messages_in_range` (hand-built dict, 20 keys)
4. `scraper.stream_messages_with_cache` telegram branch (hand-built dict, 18 keys)
5. `history.MessageResponse` (pydantic, documented shape)

`MessageResponse` is missing `forwarded_from_user_id`, `forwarded_from_name`,
`forwarded_from_date` — which the stream actually emits. The OpenAPI doc is
wrong about the payload and nobody noticed because `StreamingResponse` bypasses
response validation entirely. `transform_message_to_response()` — a function
that pops three keys from a dict — exists to paper over the fact that the DB row
dict and the API dict are supposed to be the same thing and aren't.

### 3.11 `auth.py` — the QR flow

- `_qr_sessions` is declared twice with a `# Fix forward reference` comment.
  That's not fixing anything; the first declaration is just noise.
- `submit_2fa_password` busy-polls: `for _ in range(30): await asyncio.sleep(0.1)`.
  The background task owns a future; await *its* completion or return
  `password_required` immediately and let the client poll like it already does.
- Terminal sessions are only removed from `_qr_sessions` when someone polls
  them. Start a QR flow, walk away → dict entry leaks until process restart.
- `username` from the request body goes straight into
  `config.sessions_dir / f"{username}.session"` and, with `force=true`,
  `session_file.unlink()`. No validation. `"../../whatever"` is accepted.
  Same in `auth_utils.get_authenticated_user` from the header.

### 3.12 `media_downloader.py::download_media`

```python
db_dir = output_dir if output_dir.name == str(dialog_id) else (output_dir / str(dialog_id))
```

Guessing whether the caller passed the parent or the child directory by string
comparison. You have `dialog_db_paths()` for exactly this. Use it, take one
argument, stop guessing.

The size limit is computed here **and again** in `stream_messages_with_cache`
(`_repair_max_bytes`) with the same `int(float(x) * 1024 * 1024)` try/except.
`max_media_size_mb` is already `Optional[float]` in a pydantic model — the
`TypeError/ValueError` guard can never fire.

### 3.13 Config / versioning noise

- `pyproject.toml`: `0.2.0`. `FastAPI(version="1.0.0")`. `API_VERSION = "v3"`.
  Three unrelated version numbers.
- `logging.basicConfig()` called at import time in both `server.py` and
  `authenticate.py`. Library modules must not configure the root logger.
- `save_settings` docstring: "Writes only the 3 tunable params". There are five.
- `ServerConfig.api_id: str` — it's an int. Telethon casts it; you shouldn't
  make it.
- `config.py::load_settings` has a commented-out block of dead code.
  `dialogs.py::list_folders` has another. Delete or do.

## 4. What's missing that a competent developer notices in five minutes

1. **The latest commit breaks Docker.** `4cbade3` changed the default host to
   `127.0.0.1`. The `Dockerfile` `CMD` is `["--data-dir", "/app/data"]` — no
   `--host`. Inside a container, binding to loopback means `-p 8000:8000` in the
   README connects to nothing. The "fix" commit shipped an unreachable image.
   Add `"--host", "0.0.0.0"` to `CMD`.

2. **Authentication is a header with a filename in it.** `X-Telegram-Username:
   alice` is the entire auth. If `data/sessions/alice.session` exists, you *are*
   alice, with full read access to her Telegram account. Combined with
   `CORSMiddleware(allow_origins=["*"], allow_credentials=True)` this is
   "any web page can drive the logged-in user's Telegram" the moment the server
   is exposed. Binding to localhost is a mitigation, not a design. At minimum:
   a per-session bearer token generated at QR success, and CORS off by default.

3. **No error signalling on the SSE stream.** `event_stream()` starts, HTTP 200
   is sent, then `download_from_telegram_batched` raises on message 4,000
   (`raise` on line 361, "fail the whole batch to maintain consistency"). The
   client sees the connection drop. No `event: error`, no way to distinguish
   "done" from "died". Emit a terminal `event: done` / `event: error` frame.

4. **Blocking I/O in the event loop.** All SQLite access is synchronous
   `Session` calls inside `async def`. The `PRAGMA synchronous=NORMAL` commits
   and the OFFSET scans block every other request while they run. Either
   `run_in_executor`, `aiosqlite`, or accept that this server handles one
   request at a time and say so.

5. **Media download is inline and serial with message iteration.** A 20 MB
   video on message 50 stalls the stream for the whole download; the batch of
   100 isn't yielded until every file in it is on disk. Download after the batch
   is written, or in a bounded task group, or in `POST /sync` only.

6. **No migrations.** Schema is `create_all`. The moment you add a column
   (which you did — `reply_quote_text` two commits ago) every existing
   per-dialog `.db` is silently out of date and will explode on the first
   `INSERT`. Alembic or a `PRAGMA user_version` check. Anything.

7. **`sender_id=message.sender_id or 0`** stores a fake user `0` and then
   `upsert_user(user_id=0, first_name=None, ...)` for every channel post. The
   `users` table also gets channel IDs (posts in channels have `sender_id` =
   channel ID). And `upsert_user` **overwrites** `first_name`/`last_name`/
   `username` with `None` when forward resolution fails (`fwd_entity_cache[key] =
   {}`), wiping data you previously had. Upsert must not downgrade non-null
   to null.

8. **`get_authenticated_user` doesn't authenticate; `get_telegram_client` does.**
   The first only checks a file exists. `/settings` and `/files` depend on the
   first only, so a stale/revoked session file still grants access to settings
   and every downloaded media file.

9. **No rate limiting on your own side.** `search_dialogs` can issue 500
   parallel RPCs; `list_folders?include_dialogs=true` does one `get_entity` per
   peer per folder. `flood_sleep_threshold=120` means the request handler will
   silently `sleep(119)` inside an HTTP request. Users will see a hung request
   and retry, making it worse.

10. **No `.dockerignore` for `data/`** — checked, it exists, fine. But
    `.pytest_cache/` and `.ruff_cache/` are in the working tree; `.gitignore`
    covers them, ok. `.env` is present locally and ignored — fine. No complaints
    here, just confirming I looked.

## 5. Where the design can be simplified to almost nothing

### 5.1 Model the cache correctly, and it becomes smaller, not bigger

Replace "min/max of message dates" with a table:

```sql
CREATE TABLE fetched_ranges (
  dialog_id INTEGER, start TEXT, end TEXT
);
```

On every Telegram fetch, insert the range you actually fetched and merge
adjacent/overlapping rows (that's the `merge_overlapping_ranges` you already
wrote). Gap computation becomes: requested range minus the union of stored
ranges. `find_gaps`, `find_covered_range`, `build_timeline`, `compute_segments`,
`TimelineSegment`, `DateRange` all collapse into one function returning
`list[(start, end, is_cached)]`. Fewer concepts, and it's *correct*.

### 5.2 One database

One `data/telegram.db`. Tables: `messages(dialog_id, message_id, ... PK(dialog_id,
message_id))`, `users`, `dialogs`, `media(uuid, dialog_id, message_id, ...)`,
`fetched_ranges`. `files.py` becomes one query. `paths.py` mostly disappears.
The engine is created once at startup and lives in `app.state`.

### 5.3 One message shape

`Message` (SQLModel) *is* the row and *is* the API payload. Add `sender` fields
via a join or a `computed_field`. Delete `MessageData`, delete the two hand-built
dicts, delete `transform_message_to_response`. `MessageResponse` becomes
`Message.model_dump(exclude={"id"})`. Four definitions → one.

### 5.4 Split "fetch" from "serve"

`stream_messages_with_cache` currently reads cache, downloads Telegram, downloads
media, repairs media, and formats output in one 200-line generator. Make it two
steps:

```
async def fill_cache(dialog_id, start, end) -> SyncStats   # only writes
def   read_cache(dialog_id, start, end, reverse) -> Iterator[Message]  # only reads
```

`GET /history` = `await fill_cache(); stream read_cache()`. `POST /sync` =
`await fill_cache()`. `repair_media` becomes a flag to `fill_cache`. The
duplicated tail-flush and the `client_buffer` slicing dance go away — buffering
is `itertools.batched(read_cache(...), n)`.

Yes, this means history isn't "streamed while downloading". It also means the
response is correct, ordered, and can report an error before sending 200. If
progressive streaming during download is a hard requirement, emit a
`event: progress` from `fill_cache` and then stream the cache — still two
functions.

### 5.5 Drop the settings mutation-through-API surface or make it real

`PATCH /settings` mutates a pydantic model in place and rewrites a YAML file,
swallows write errors, and returns 200. It's five booleans and a float. Either
make it a real config store with validation errors surfacing, or just document
"edit `settings.yaml` and restart". Right now it's the worst of both.

## 6. Implementation cleanups (cheap, mechanical)

- `@app.exception_handler(FloodWaitError)` → delete six try/except blocks.
- One `parse_date()` in a `utils.py` → delete two copies.
- `except Exception: pass` around `Path(...).stat()` appears three times.
  `os.path.getsize` and let it raise, or one helper.
- `batch_upsert_messages`: use `sqlite`'s `insert(...).on_conflict_do_update()`
  from `sqlalchemy.dialects.sqlite`. Removes the per-row SELECT and the
  `existing.x = getattr(msg, "x")` block (15 lines of field copying that will
  drift the next time a column is added — it already drifted: `media_uuid` is
  handled "separately").
- `iter_messages_in_range`: `session.exec(stmt).yield_per(batch_size)` instead
  of OFFSET.
- `_wait_for_qr_scan`: `asyncio.timeout()` context instead of manual deadline
  arithmetic.
- `dialogs.py`: the 12 inline filters are all `if param is not None and
  not pred(dialog): continue`. A list of `(param, predicate)` tuples and one
  `all(...)` makes the function readable and the filters unit-testable.
- `MediaCategory` is a `StrEnum` but `MediaMetadata.media_type: str`,
  `MediaFile.media_type: str`, `MessageData.media_type: str`. Use the enum.
- `SortField`/`_sort_key`: a `dict[SortField, Callable]` instead of an if-chain.
- Delete: `merge_overlapping_ranges` (or use it — see 5.1),
  `get_media_uuid_by_message_id`, `check_db_connection`, `generate_media_uuid`
  (it's `str(uuid4())`), `client.py::get_client` (duplicate of
  `auth_utils.get_telegram_client` minus the pool), both commented-out blocks.
- `Dockerfile`: `CMD ["--host", "0.0.0.0", "--data-dir", "/app/data"]`.

## 7. What's actually fine

Credit where it's due, briefly:

- Dependency injection via `Depends(get_config)` with `app.state.config` is the
  right shape; the tests override cleanly because of it.
- The test harness (`MockTelegramClient`, `ASGITransport`) is a reasonable way
  to test this without Telegram. It just tests the wrong things (see §2).
- `MediaFile` row always created even when not downloaded, with `file_path NULL`
  meaning "skipped" — that's a sensible way to make `repair_media` possible
  without re-querying Telegram.
- `classify_media_category` is clear and correct.
- QR auth with token refresh every 25 s is the right approach for Telethon's
  30 s expiry.
- Settings split (env for secrets, CLI for infra, YAML for tunables) is
  reasonable and documented.

## Bottom line

The project is a cache. The cache has a correctness hole you can drive a truck
through (§2), the "atomic" writes aren't (§3.2), the storage layout makes the
simplest lookup O(dialogs) (§3.5–3.6), and the last commit made the Docker image
unreachable (§4.1). Around that core there's a lot of well-intentioned
duplication: three date parsers, five message shapes, six FloodWait handlers,
two "save the batch" blocks, two size-limit calculators.

None of this is unfixable, and the fix is *less* code, not more:
one DB, one message type, a `fetched_ranges` table, fetch/read split, one
exception handler. Do §5.1 first — until the cache tells the truth, nothing
built on it matters.
