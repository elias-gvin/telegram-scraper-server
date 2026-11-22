# Independent Technical Review

> **Historical document. Not instructions.** This reviews the code at commit `4cbade3`, before the archive rewrite (branch `rewrite-archive`). Most of the code it describes (`scraper.py`, `database/`, `X-Telegram-Username` auth, the date-range cache) no longer exists. Do not act on it; for the current design read `README.md` and the code. See `reviews/README.md`.

## Verdict

This is trying to be a local Telegram account mirror with a REST wrapper, cached history, media download, search, and QR login.

The useful core is small:

1. connect an authenticated Telethon session;
2. fetch messages for a dialog;
3. upsert them into SQLite;
4. optionally download media;
5. expose the result over HTTP.

That core is reasonable. The implementation around it is not.

The project currently advertises “authentication,” “smart caching,” “atomic commits,” streaming, multi-user sessions, and Docker support. Several of those claims are false in important ways. The code works on the happy path covered by mocks, but its security boundary is nonexistent, its cache coverage model is mathematically wrong, its supposedly atomic writes commit in the middle of a batch, and the default Docker invocation cannot reach the server.

This is not production-ready. It is a useful prototype buried under accidental complexity and misleading names.

## 1. What this is actually trying to do

Ignore the API versioning, settings machinery, fuzzy search, per-dialog databases, client pools, and QR state machine. The real goal is:

> Keep a local copy of selected Telegram dialog history and media, then let a local client query it.

The design has not decided whether this is:

- a single-user localhost tool; or
- a multi-user network service.

It currently takes the risks of the second while implementing the security model of the first.

## 2. Critical failures

### 2.1 `X-Telegram-Username` is not authentication

`src/telegram_scraper/api/auth_utils.py:17-44` accepts a caller-provided username and checks only whether a file with that name exists. There is no password, API token, signed session, identity provider, or proof that the caller owns the Telegram session.

Calling this “header-based authentication” is bullshit. It is a filename selector.

Consequences:

- anyone who knows or guesses a session name can use that Telegram account;
- file and settings endpoints do not even validate that Telethon considers the session authorized;
- every authenticated-looking endpoint trusts caller-controlled identity;
- one user can read media discovered from another user's cache;
- one user can mutate global runtime settings for everyone.

Binding to localhost reduces exposure only until somebody uses Docker, a reverse proxy, port forwarding, an SSRF primitive, or another local process. It does not turn a user-controlled string into authentication.

### 2.2 Caller-controlled usernames become filesystem paths

The project repeatedly builds paths directly from `username`:

- `src/telegram_scraper/api/auth_utils.py:37-38`
- `src/telegram_scraper/api/auth_utils.py:91-95`
- `src/telegram_scraper/api/auth.py:257-269`
- `src/telegram_scraper/api/auth.py:272-274`
- `src/telegram_scraper/authenticate.py:273-276`

There is no allowlist, canonicalization, containment check, or opaque internal ID. Absolute paths and `..` components are accepted.

The unauthenticated QR endpoint makes this worse. `POST /auth/qr` with `force=true` deletes the chosen `.session` file at `src/telegram_scraper/api/auth.py:257-269`. A remote caller can therefore target session files outside the sessions directory when the process has permission.

This is an arbitrary session-file deletion and creation primitive. Fixing it requires more than stripping slashes. User-visible names must never be storage keys.

### 2.3 The “smart gap detection” does not detect gaps

`src/telegram_scraper/database/operations.py:346-368` computes only `MIN(date)` and `MAX(date)` from cached messages. `src/telegram_scraper/scraper.py:410-443` then treats every timestamp between those two values as fully cached.

That assumption is false.

Internal holes arise from:

- interrupted or failed syncs;
- messages deleted from the local database;
- filtered or inaccessible messages;
- an empty Telegram result;
- concurrent writes;
- historical holes that existed before the first or after the last cached message;
- schema or media failures after part of a batch was committed.

Once there is one old row and one new row, the code declares the entire interval covered and may never fetch the missing middle. An empty interval cannot be recorded at all, so it is fetched forever.

This is not moving complexity into a clever cache. It is replacing a real coverage model with two aggregate values and hoping message timestamps form a proof.

### 2.4 “Atomic batch operations” commit repeatedly inside the batch

`download_from_telegram_batched()` claims to save each batch atomically at `src/telegram_scraper/scraper.py:330-352`. It calls `store_media_with_uuid()` once per media message.

`store_media_with_uuid()` commits at:

- `src/telegram_scraper/database/operations.py:273`
- `src/telegram_scraper/database/operations.py:292`

The final `session.commit()` in the scraper is therefore theater. If media item 7 fails, items 1-6 and the message upserts may already be durable. The subsequent rollback cannot undo them.

This directly feeds the broken cache-range logic: a partially committed batch can make the cache claim a range is complete.

Transaction ownership must have one layer. Helpers used inside a transaction do not commit.

### 2.5 The documented Docker command cannot reach the service

The server defaults to `127.0.0.1` at `src/telegram_scraper/server.py:130-134`. The Docker image does not override `--host` in `Dockerfile:38-40`. Inside a container, binding to loopback means publishing `-p 8000:8000` does not expose the process through the container interface.

README examples claim the Docker command works and later claim the default is `0.0.0.0` (`README.md:30-38`, `README.md:225-228`). The code says otherwise.

Keep localhost as the host-machine default if desired, but the container entrypoint must explicitly pass `--host 0.0.0.0`.

## 3. High-severity design problems

### 3.1 Cached data is not scoped by Telegram account

Database and media paths are keyed only by `dialog_id`:

- `src/telegram_scraper/database/paths.py:17-37`
- `src/telegram_scraper/database/models.py:59-67`
- `src/telegram_scraper/database/models.py:60-63`

The username/account is absent from the path and database keys. Two Telegram accounts using the same server share and overwrite the same dialog cache. Numeric IDs are not a tenant boundary. Private chat and account-specific visibility make cross-account reuse especially wrong.

Even with real HTTP authentication, the storage model would still leak or corrupt data across accounts.

### 3.2 The client “pool” serializes every request for one user

`get_telegram_client()` acquires a per-user lock and yields the client while still inside `async with lock` (`src/telegram_scraper/api/auth_utils.py:69-116`).

Dependency cleanup happens after the response finishes. For SSE history, that means the lock is held for the entire stream, including Telegram fetches, media downloads, and client backpressure. One slow history request blocks search, folders, sync, and every other Telegram-backed request for that account.

That is not connection pooling. It is a single global lane hidden inside a dependency.

If Telethon access must be serialized, use an explicit per-account command queue or lock only the operations that require it. Do not hold a lock across an HTTP stream.

### 3.3 Synchronous SQLite and filesystem work runs in the async event loop

History and sync handlers execute SQLModel queries, commits, schema creation, directory scans, stats, YAML writes, and file operations directly inside async functions.

Examples:

- `src/telegram_scraper/api/history.py:160-204`
- `src/telegram_scraper/api/history.py:298-331`
- `src/telegram_scraper/scraper.py:330-381`
- `src/telegram_scraper/api/files.py:29-48`
- `src/telegram_scraper/api/settings.py:140-171`

Under load, this blocks the event loop. Combined with media downloads and a per-user lock, concurrency collapses quickly.

For a small local tool, the simplest answer is often to make sync an explicit background job and keep reads short. For a network service, move blocking work to a worker/thread boundary or use a deliberately synchronous service architecture.

### 3.4 A new SQLAlchemy engine is created over and over

`get_session()` creates a fresh engine every call (`src/telegram_scraper/database/session.py:51-71`). Handlers also create an engine for `create_all()` and then immediately create another one for the session (`src/telegram_scraper/api/history.py:160-173`, `src/telegram_scraper/api/history.py:298-309`).

There is no engine registry, disposal strategy, or application-scoped lifecycle. File lookup creates engines while scanning every dialog database.

The code built a “pool” for Telethon and then forgot to manage the actual database pools.

### 3.5 One database per dialog is the wrong unit

Per-dialog SQLite files cause:

- duplicated `users` and `dialogs` tables;
- no cheap account-wide query;
- O(number of dialogs) media lookup;
- repeated schema initialization;
- repeated engines and WAL files;
- awkward migrations;
- no account ownership boundary.

`find_media_by_uuid()` literally walks every dialog directory and opens databases until it finds a UUID (`src/telegram_scraper/api/files.py:17-49`), swallowing every database error on the way.

Use one SQLite database per Telegram account, or one database total with an `account_id` column. Both are simpler than this filesystem database fan-out.

### 3.6 Force-redownloading media destroys the good copy first

`src/telegram_scraper/media_downloader.py:139-146` deletes existing files before attempting the replacement download. A network failure then leaves no file.

Download to a temporary file, validate success, `fsync` if durability matters, and atomically replace the old path. Destructive refresh before successful replacement is amateur-hour data handling.

### 3.7 QR login is an unauthenticated in-memory resource-exhaustion API

Every `POST /auth/qr` creates a Telegram client, a task, a mutable state object, and potentially a session file:

- `src/telegram_scraper/api/auth.py:250-296`
- `src/telegram_scraper/api/auth.py:112-204`

There is:

- no rate limit;
- no cap per IP or username;
- no cap on total pending sessions;
- no protection against concurrent login attempts for the same username;
- no persistence or multi-process coordination;
- no cleanup until timeout, polling, cancellation, or shutdown.

The duplicate `_qr_sessions` declarations at `src/telegram_scraper/api/auth.py:37-38` and `src/telegram_scraper/api/auth.py:104-106` are a small but accurate summary of the state management quality.

### 3.8 Runtime settings are global, mutable, racy, and lie on write failure

Any caller with any existing session filename can mutate the one global `config.settings`. Concurrent PATCH requests modify the same Pydantic object and rewrite the same YAML file without a lock or atomic replace.

Worse, persistence errors are logged and the endpoint still returns success (`src/telegram_scraper/api/settings.py:167-175`).

Either persistence is part of the operation and failure is returned, or settings are explicitly ephemeral. Pretending both succeeded is not an API contract.

### 3.9 Schema creation is not schema migration

`SQLModel.metadata.create_all()` on requests (`src/telegram_scraper/database/session.py:41-48`) only creates missing tables. It does not add, remove, or transform existing columns and constraints.

This project has already changed its data model repeatedly. Without explicit migrations and a schema version, existing caches will eventually fail or silently retain incompatible data.

### 3.10 History streaming has no reliable error contract

The outer `try/except` in `get_history()` does not catch failures that occur later inside `event_stream()` after the `StreamingResponse` has started (`src/telegram_scraper/api/history.py:184-217`).

Clients can receive HTTP 200 and then a truncated stream with no structured error or completion event. There are no heartbeats, event IDs, retry hints, disconnect handling policy, or explicit terminal event.

If the endpoint is SSE, define an SSE protocol. If it merely returns chunks, newline-delimited JSON or a normal paginated JSON endpoint is simpler.

### 3.11 Cached file paths are trusted without confinement

`src/telegram_scraper/api/files.py:98-121` passes the `file_path` stored in SQLite directly to `FileResponse`. It verifies existence, but never verifies that the resolved path remains under the expected account/dialog media directory.

A corrupted, migrated, or otherwise modified database row can therefore turn the file endpoint into an arbitrary local-file reader. Database content is not a security boundary. Resolve the path and enforce containment beneath a known media root before serving it.

### 3.12 Work and resource limits are mostly absent

Sync defaults to downloading from 2013 through the present and blocks until completion (`src/telegram_scraper/api/history.py:240-249`, `src/telegram_scraper/api/history.py:278-329`). `telegram_batch_size` has no upper bound in the PATCH model (`src/telegram_scraper/api/settings.py:76-81`) and has no bounds at all in startup settings.

Uvicorn is started without explicit concurrency, request-timeout, or graceful-shutdown limits (`src/telegram_scraper/server.py:219-222`). Combined with unbounded QR sessions, media downloads, and up to 500 count RPCs, accidental or hostile resource exhaustion is easy.

Limits belong at every expensive boundary: concurrent sync jobs, pending QR flows, Telegram RPC fan-out, batch sizes, media bytes, and per-account disk usage.

### 3.13 The container runs as root while storing account credentials

The runtime image has no `USER` directive (`Dockerfile:21-40`). The process therefore runs as root while reading and writing durable Telethon sessions, SQLite databases, settings, and downloaded media.

A service compromise gets the strongest filesystem privileges available in the container and direct access to every persisted Telegram session. Create an unprivileged runtime user, own `/app/data` appropriately, and document volume permissions.

## 4. Broken or misleading behavior

### 4.1 Dialog message filtering and sorting use fake counts

Before pagination, `search_dialogs()` uses the latest message ID as an approximate message count (`src/telegram_scraper/api/dialogs.py:425-435`). It fetches actual counts only after filtering, sorting, and pagination (`src/telegram_scraper/api/dialogs.py:497-516`).

Therefore:

- `min_messages` and `max_messages` filter by message ID, not message count;
- sorting by `messages` sorts every item as zero because `message_count` is still `None`;
- returned counts may contradict the filter that selected them.

The comment saying the real count “will be filled later” does not make the earlier decisions correct.

### 4.2 Fuzzy sort ignores part of the API contract

When fuzzy search is active, results are always primarily score-descending and ties use the selected field without honoring `order` (`src/telegram_scraper/api/dialogs.py:483-495`).

The endpoint exposes more combinations than it correctly implements.

### 4.3 Dialog search is an expensive full-account scan

Every request iterates every dialog, computes Python-side filters, sorts the full result, then can launch up to 500 concurrent count requests (`src/telegram_scraper/api/dialogs.py:397-516`).

This is overbuilt for a local convenience endpoint and underbuilt for a scalable one. Keep a small filter surface or cache dialog metadata. Do not expose a database-like query API backed by an O(n) remote scan plus N RPCs.

### 4.4 Dates are strings with second precision

Message dates are stored as `"YYYY-MM-DD HH:MM:SS"` strings (`src/telegram_scraper/database/models.py:72-74`) and compared lexically. Timezone information and sub-second precision are discarded.

Pagination orders only by date (`src/telegram_scraper/database/operations.py:387-399`). Messages sharing a second have no deterministic tie-breaker, and OFFSET pagination can skip or duplicate rows while data changes.

Store UTC timestamps consistently and order by `(date, message_id)`. Prefer keyset pagination for stable reads.

### 4.5 Cache refresh does not model edits or deletions correctly

Upserts update rows that Telegram returns, but absence is never interpreted as deletion. A force refresh does not remove stale cached messages. Messages that lose media keep their old `media_uuid`, because media is only updated when the new message has media.

The cache is an append/update snapshot with no deletion semantics, not a mirror.

### 4.6 Media metadata lookup is N+1

Cached history loads message rows in batches, then calls `get_media_info_by_uuid()` separately for every media message (`src/telegram_scraper/scraper.py:499-510`). Repair can add Telegram calls and more commits per row.

Join `MediaFile` in the batch query. The current schema already has the foreign key.

### 4.7 File metadata leaks server filesystem paths

`metadata_only=true` returns an absolute resolved path (`src/telegram_scraper/api/files.py:103-110`). That is unnecessary information disclosure for a network API and only useful for a tightly controlled local tool.

Again, choose which product this is.

### 4.8 Error responses expose internal exceptions

Multiple endpoints return `str(e)` to clients:

- `src/telegram_scraper/api/auth.py:283`
- `src/telegram_scraper/api/history.py:217`
- `src/telegram_scraper/api/history.py:350`
- `src/telegram_scraper/api/search.py:275`
- `src/telegram_scraper/api/search.py:380`

This leaks implementation and upstream details while giving clients unstable error contracts. Log internal errors; return stable codes and safe messages.

### 4.9 The health endpoint proves nothing

`src/telegram_scraper/server.py:115-118` always returns healthy. It does not check writable storage, settings load, database access, client manager state, or Telegram connectivity.

Call it a liveness endpoint or make it test dependencies. “Healthy” currently means only “this coroutine ran.”

### 4.10 Documentation and versions disagree with the code

Examples and endpoint descriptions still refer to `/api/v1` while routers expose `/api/v3`. Package version is `0.2.0`, FastAPI advertises `1.0.0`, and the Docker host documentation contradicts the CLI default.

Version numbers are cheap until consumers depend on them. Then this sloppiness becomes breakage.

### 4.11 Search reverses the meaning of start and end dates

History uses the conventional rule `start_date < end_date`. Message search maps `start_date` to Telethon's upper-bound `offset_date`, maps `end_date` to the lower bound, and rejects unless `end_date < start_date`:

- `src/telegram_scraper/api/search.py:190-226`
- `src/telegram_scraper/api/search.py:304-336`

That leaks an upstream API quirk into the public contract and gives identical parameter names opposite meanings across endpoints. Expose consistent lower/upper bounds and translate to Telethon internally.

Search also reports `total` as the number collected before reaching `limit`, not the total number of Telegram matches (`src/telegram_scraper/api/search.py:240-257`, `src/telegram_scraper/api/search.py:348-364`). Calling a page length “total” is simply wrong.

### 4.12 Sender metadata degrades as new messages arrive

`upsert_user()` blindly replaces existing names and usernames with incoming values, including `None` (`src/telegram_scraper/database/operations.py:80-98`). Failed forward-source resolution deliberately produces empty metadata, which can wipe previously known values.

The scraper also turns missing senders into `sender_id=0` (`src/telegram_scraper/scraper.py:292`) and then upserts that fabricated user. Preserve known fields when new values are absent, and represent an unknown sender as null or a distinct typed source rather than inventing user zero.

### 4.13 Startup settings bypass endpoint validation

`RuntimeSettings` declares plain numeric fields without bounds (`src/telegram_scraper/config.py:51-58`). The API update model validates non-negative media size and positive batch size, but YAML loading directly validates against the weaker runtime model (`src/telegram_scraper/config.py:134-143`).

As a result, values rejected by PATCH can be accepted at startup. Put the constraints on the canonical settings model; request models should not be the only place invariants exist.

### 4.14 OpenAPI does not describe the actual stream payload

The scraper emits forward-source fields such as `forwarded_from_user_id`, `forwarded_from_name`, and `forwarded_from_date` (`src/telegram_scraper/scraper.py:619-623`), but `MessageResponse` omits them (`src/telegram_scraper/api/history.py:31-51`).

`StreamingResponse` bypasses response-model validation, so this drift survives tests and makes generated clients incomplete. Use one canonical response model to serialize both cached and Telegram-backed messages, and derive the documentation from it.

## 5. What competent engineering would have added

The missing work is not another response model or another settings toggle. It is the boring foundation:

- a declared trust model: local single-user or authenticated multi-user;
- real request authentication and authorization;
- account ownership on every cached row and media object;
- strict opaque storage identifiers instead of username-derived paths;
- coverage intervals or sync cursors updated only after successful completion;
- explicit deletion/edit synchronization semantics;
- one transaction owner per batch;
- schema migrations and version checks;
- bounded queues, rate limits, and backpressure;
- deterministic ordering and pagination;
- atomic file and YAML replacement;
- application-scoped database/client lifecycle;
- tests for interruption, partial commit, empty ranges, internal holes, concurrency, cancellation, path traversal, cross-account access, and real container startup;
- CI that runs tests and the documented lint command.

## 6. The simpler design

### Option A: admit it is a local single-user tool

This is the shortest path to something honest and robust.

1. Configure exactly one Telegram session per server process.
2. Remove `X-Telegram-Username`, multi-user pooling, and cross-user claims.
3. Bind to localhost by default.
4. Put one SQLite database in the data directory.
5. Store dialogs, messages, media, and coverage intervals in that database.
6. Make `POST /sync` perform synchronization.
7. Make `GET /history` read only from SQLite.
8. Keep Telegram-native search as a clearly separate online endpoint.
9. Serve files by a database-owned opaque ID.
10. Use transactions and atomic file replacement.

That design is nearly trivial and matches how the software is likely to be used.

### Option B: build an actual multi-user service

Then pay the full cost:

1. authenticate API callers with real credentials;
2. map caller identity to an internal account ID;
3. authorize every session, dialog, media, and settings operation;
4. isolate storage by account ID;
5. protect QR enrollment as a privileged account-linking flow;
6. require TLS and configure CORS narrowly;
7. use persistent job state and bounded workers;
8. support multiple server processes or explicitly prohibit them;
9. add migrations, quotas, audit logs, and operational metrics.

Anything halfway between these options is how private Telegram data gets exposed.

## 7. Cleaner implementation shape

A smaller codebase could have these components:

- `AccountClientManager`: owns one Telethon client per internal account and its lifecycle.
- `Repository`: owns one engine and all database transactions.
- `SyncService`: fetches a requested segment and commits messages plus coverage atomically.
- `MediaStore`: writes temporary files and atomically promotes successful downloads.
- `api/`: validates requests, calls services, and serializes stable responses.

The synchronization rule should be obvious:

1. read uncovered intervals from a `coverage` table;
2. fetch one interval;
3. write messages, media metadata, and the completed coverage interval in one transaction;
4. merge adjacent coverage intervals;
5. never infer coverage from message presence.

History should be even more obvious:

1. query cached rows;
2. order by `(date, message_id)`;
3. return a page or stream;
4. do not contact Telegram or mutate the database during a read.

## 8. Tests and tooling

The current automated tests passed locally: **87 passed**.

That sounds better than it is. The shared test fixture overrides both authentication dependencies (`tests/conftest.py:86-103`), so most endpoint tests never exercise the security boundary. The Telethon mock is a happy-path list iterator and does not model disconnections, concurrent use, flood waits, partial batches, empty covered intervals, message deletion, or session-file locking.

The tests prove that mocked requests produce expected JSON. They do not prove the system is safe or that its caching claims are correct.

The documented lint command also fails. With the installed current Ruff, `ruff check src/` reports **244 errors**. Many are cleanup issues, but the list also catches broad exception handling, swallowed failures, and dead or misleading constructs. A lint command in the README that the repository does not pass is noise, not quality control.

## 9. What is actually good

Some pieces are worth keeping:

- FastAPI dependency injection makes the transport layer testable.
- Telethon calls are isolated enough that mocks can exercise endpoint flows.
- Pydantic models provide useful request validation.
- WAL mode and foreign-key enforcement are sensible SQLite defaults.
- Media metadata is collected even when file download is disabled, which is the right prerequisite for later repair.
- Batch-oriented message handling is the correct general direction.
- The test suite is fast and covers basic endpoint contracts.
- The latest localhost default is safer for direct host execution.

Those are solid ingredients. They do not rescue the architecture, but they mean a rewrite can preserve useful behavior instead of starting from nothing.

## 10. Priority order

1. Decide local single-user versus real multi-user service.
2. Remove the fake authentication claim and close the username path traversal/deletion hole.
3. Add account ownership to storage or remove multi-account support.
4. Replace min/max cache inference with explicit coverage records.
5. Restore real batch atomicity by removing helper commits.
6. Fix Docker binding and documentation.
7. Separate synchronization writes from history reads.
8. Replace per-dialog databases with one database per account or one tenant-aware database.
9. Fix client locking, engine lifecycle, and blocking work.
10. Add migrations and failure-oriented tests.

Do not spend time polishing fuzzy search, adding settings toggles, or expanding response schemas before these are fixed. That would be decorating a house with no locks and a cracked foundation.
