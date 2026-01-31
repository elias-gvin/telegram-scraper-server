# Database Schema Changes: Atomic Commits

## Overview

Simplified the database schema by removing the `scrape_runs` table. With atomic commit guarantees, we no longer need to track which scrapes succeeded or failed - the cache itself is the source of truth.

## Key Principle

**If data exists in the cache for a date range, it is complete and valid (no gaps).**

This is guaranteed by:
1. **Atomic batch commits** - Each 100-message batch is committed atomically
2. **Rollback on error** - Any error during download causes immediate rollback
3. **No partial commits** - Either the entire batch succeeds or nothing is saved

## Changes Made

### 1. Removed `scrape_runs` Table

**Before:**
```sql
CREATE TABLE scrape_runs (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT,
  launched_at TEXT NOT NULL,
  triggered_by_user TEXT,
  params_json TEXT NOT NULL,
  successful INTEGER NOT NULL DEFAULT 0,
  -- ... more fields
);
```

**After:**
- Table completely removed
- No tracking of scrape runs needed

### 2. Removed `run_id` from `messages` Table

**Before:**
```sql
CREATE TABLE messages (
  id INTEGER PRIMARY KEY,
  channel_id TEXT NOT NULL,
  run_id INTEGER NOT NULL,  -- ← Removed
  message_id INTEGER UNIQUE,
  date TEXT,
  -- ...
  FOREIGN KEY(run_id) REFERENCES scrape_runs(run_id)
);
```

**After:**
```sql
CREATE TABLE messages (
  id INTEGER PRIMARY KEY,
  channel_id TEXT NOT NULL,
  message_id INTEGER UNIQUE,
  date TEXT,
  -- ...
  FOREIGN KEY(channel_id) REFERENCES channels(channel_id)
);
```

### 3. Simplified Functions

#### Removed Functions:
- `create_scrape_run()` - No longer needed
- `finalize_scrape_run()` - No longer needed

#### Updated Functions:
- `batch_upsert_messages()` - Removed `run_id` parameter

**Before:**
```python
db_helper.batch_upsert_messages(
    conn, messages,
    channel_id=channel_id,
    run_id=run_id,  # ← No longer needed
    replace_existing=True
)
```

**After:**
```python
db_helper.batch_upsert_messages(
    conn, messages,
    channel_id=channel_id,
    replace_existing=True
)
```

### 4. Improved Error Handling in Streaming Scraper

**Atomic Batch Processing:**

```python
async def download_from_telegram_batched(...):
    batch = []
    
    async for message in client.iter_messages(...):
        # Process message
        batch.append(msg_data)
        
        # When batch is full, commit atomically
        if len(batch) >= batch_size:
            try:
                conn.execute("BEGIN IMMEDIATE")
                _batch_insert_messages(conn, batch, channel_id)
                conn.commit()  # ✓ Success: batch saved
            except Exception as e:
                conn.rollback()  # ✗ Error: nothing saved
                raise  # Propagate error to caller
            
            yield batch
            batch = []
```

**Key Points:**
- `BEGIN IMMEDIATE` ensures exclusive lock during write
- Rollback on any error ensures no partial data
- Exception propagates to caller (returned to API client)

## Database Tables (Final Schema)

### Core Tables:

1. **`messages`** - Message content (no run_id)
   ```sql
   CREATE TABLE messages (
     id INTEGER PRIMARY KEY,
     channel_id TEXT NOT NULL,
     message_id INTEGER UNIQUE,
     date TEXT,
     sender_id INTEGER,
     first_name TEXT,
     last_name TEXT,
     username TEXT,
     message TEXT,
     media_type TEXT,
     media_path TEXT,
     reply_to INTEGER,
     post_author TEXT,
     is_forwarded INTEGER,
     forwarded_from_channel_id INTEGER,
     FOREIGN KEY(channel_id) REFERENCES channels(channel_id)
   );
   ```

2. **`channels`** - Channel metadata
   ```sql
   CREATE TABLE channels (
     channel_id TEXT PRIMARY KEY,
     channel_name TEXT,
     user TEXT
   );
   ```

3. **`media_files`** - Media UUID → path mapping
   ```sql
   CREATE TABLE media_files (
     uuid TEXT PRIMARY KEY,
     message_id INTEGER UNIQUE NOT NULL,
     file_path TEXT NOT NULL,
     file_size INTEGER,
     mime_type TEXT,
     created_at TEXT NOT NULL,
     FOREIGN KEY(message_id) REFERENCES messages(message_id)
   );
   ```

## Migration Guide

### For Existing Databases

If you have existing databases with the old schema:

**Option 1: Keep using old data (recommended)**
- Old databases will continue to work
- `run_id` column will simply be ignored
- New data won't populate `run_id` (will be NULL)
- No migration needed!

**Option 2: Clean migration (optional)**
```sql
-- Remove scrape_runs table
DROP TABLE IF EXISTS scrape_runs;

-- Remove run_id column from messages (SQLite doesn't support DROP COLUMN directly)
-- Create new table without run_id
CREATE TABLE messages_new (
  id INTEGER PRIMARY KEY,
  channel_id TEXT NOT NULL,
  message_id INTEGER UNIQUE,
  date TEXT,
  sender_id INTEGER,
  first_name TEXT,
  last_name TEXT,
  username TEXT,
  message TEXT,
  media_type TEXT,
  media_path TEXT,
  reply_to INTEGER,
  post_author TEXT,
  is_forwarded INTEGER,
  forwarded_from_channel_id INTEGER,
  FOREIGN KEY(channel_id) REFERENCES channels(channel_id)
);

-- Copy data
INSERT INTO messages_new 
SELECT id, channel_id, message_id, date, sender_id, first_name, last_name, 
       username, message, media_type, media_path, reply_to, post_author, 
       is_forwarded, forwarded_from_channel_id
FROM messages;

-- Replace old table
DROP TABLE messages;
ALTER TABLE messages_new RENAME TO messages;

-- Recreate indexes
CREATE INDEX idx_message_id ON messages(message_id);
CREATE INDEX idx_messages_channel_id ON messages(channel_id);
CREATE INDEX idx_date ON messages(date);

-- Vacuum to reclaim space
VACUUM;
```

**Option 3: Start fresh**
- Delete old database files
- Let the system create new schema automatically

## Benefits

### 1. Simpler Schema
- 2 fewer tables (scrape_runs removed)
- 1 fewer column (run_id removed)
- No foreign key constraints to scrape_runs
- Easier to understand and maintain

### 2. Stronger Guarantees
- Cache is always consistent (no partial data)
- No need to check if scrape was "successful"
- Date range in cache = complete data for that range

### 3. Better Error Handling
- Errors propagate to API client immediately
- No "successful=0" records lingering in database
- Clear failure modes (rollback + error response)

### 4. Performance
- Fewer writes per batch (no scrape_run updates)
- Simpler queries (no joins to scrape_runs)
- Smaller database size

## Testing Checklist

Test atomic commit behavior:

- [ ] **Success case**: Download 250 messages, verify all 250 in DB
- [ ] **Network error**: Simulate network failure mid-batch, verify:
  - Partial batch NOT in DB
  - API returns error to client
  - Previous batches still intact
- [ ] **Disk full**: Simulate disk full, verify rollback works
- [ ] **Duplicate messages**: Download same range twice, verify no errors
- [ ] **Gap filling**: 
  - Download 2024-01-01 to 2024-01-10 (success)
  - Download 2024-01-20 to 2024-01-31 (success)
  - Verify gap: 2024-01-10 to 2024-01-20
  - Download 2024-01-01 to 2024-01-31, verify:
    - Serves 2024-01-01 to 2024-01-10 from cache
    - Downloads 2024-01-10 to 2024-01-20 only
    - Serves 2024-01-20 to 2024-01-31 from cache

## API Behavior

### On Success
```http
GET /api/v1/history/123?start_date=2024-01-01&end_date=2024-01-31

Response: 200 OK
data: {"messages": [...]}  # Batch 1
data: {"messages": [...]}  # Batch 2
...
```

### On Error (e.g., network timeout)
```http
GET /api/v1/history/123?start_date=2024-01-01&end_date=2024-01-31

Response: 500 Internal Server Error
{
  "detail": "Error downloading messages: Connection timeout"
}
```

**Important**: No partial data will be in cache. Client can retry the request.

## Conclusion

By ensuring atomic commits and removing the `scrape_runs` table, we've:
- ✅ Simplified the schema
- ✅ Guaranteed cache consistency
- ✅ Improved error handling
- ✅ Made the system easier to reason about

The cache is now **authoritative**: if data exists for a range, it's complete and valid. No need to check run status or success flags.

