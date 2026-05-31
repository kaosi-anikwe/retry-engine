import aiosqlite

DATABASE_PATH = "retry_engine.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    method TEXT NOT NULL,
    body TEXT,
    max_retries INTEGER NOT NULL DEFAULT 5,
    backoff_ms INTEGER NOT NULL DEFAULT 1000,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'retrying', 'completed', 'failed')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_retry_at TEXT,
    last_error TEXT,
    result TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL,
    status_code INTEGER,
    response_body TEXT,
    error TEXT,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    duration_ms REAL,
    FOREIGN KEY (request_id) REFERENCES requests(id)
);

CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(status);
CREATE INDEX IF NOT EXISTS idx_requests_next_retry ON requests(next_retry_at);
CREATE INDEX IF NOT EXISTS idx_attempts_request_id ON attempts(request_id);
"""


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DATABASE_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


async def init_db() -> None:
    db = await get_db()
    try:
        await db.executescript(SCHEMA)
        await db.commit()
    finally:
        await db.close()
