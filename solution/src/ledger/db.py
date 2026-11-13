from __future__ import annotations

import sqlite3
from pathlib import Path

from ledger.config import get_default_db_path, get_schema_dir
from ledger.schema_loader import load_dispositions

DDL_STATEMENTS = """
-- Tracks every file processed; primary idempotency guard
CREATE TABLE IF NOT EXISTS ingest_files (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    drop_id         TEXT    NOT NULL,
    rel_path        TEXT    NOT NULL,
    file_hash       TEXT    NOT NULL,
    file_type       TEXT    NOT NULL CHECK (file_type IN ('leads', 'events')),
    ingest_seq      INTEGER NOT NULL,
    ingested_at     TEXT    NOT NULL,
    UNIQUE (drop_id, rel_path)
);

-- Disposition catalogue
CREATE TABLE IF NOT EXISTS dispositions (
    workflow_id TEXT NOT NULL,
    code        TEXT NOT NULL,
    label       TEXT NOT NULL,
    is_terminal INTEGER NOT NULL CHECK (is_terminal IN (0, 1)),
    is_success  INTEGER NOT NULL CHECK (is_success  IN (0, 1)),
    PRIMARY KEY (workflow_id, code)
);

-- One row per unique (workflow_id, phone_token) identity
CREATE TABLE IF NOT EXISTS leads (
    workflow_id         TEXT    NOT NULL,
    phone_token         TEXT    NOT NULL,
    phone_last4         TEXT    NOT NULL,
    external_ref        TEXT    NOT NULL,
    batch_id            TEXT    NOT NULL,
    preferred_language  TEXT,
    policy_expiry       TEXT,
    premium_amount      REAL,
    product             TEXT,
    order_date          TEXT,
    merge_count         INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT    NOT NULL,
    PRIMARY KEY (workflow_id, phone_token)
);

CREATE INDEX IF NOT EXISTS idx_leads_ref ON leads (workflow_id, external_ref);

-- Rejected rows log (strictly no PII)
CREATE TABLE IF NOT EXISTS rejections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id TEXT    NOT NULL,
    batch_id    TEXT    NOT NULL,
    row_number  INTEGER NOT NULL,
    reason      TEXT    NOT NULL,
    file_id     INTEGER NOT NULL REFERENCES ingest_files(id)
);

CREATE INDEX IF NOT EXISTS idx_rej_sort ON rejections (workflow_id, batch_id, row_number);

-- Per-file aggregate counters persisted at ingest time
CREATE TABLE IF NOT EXISTS ingest_counters (
    file_id             INTEGER PRIMARY KEY REFERENCES ingest_files(id),
    lead_rows_total     INTEGER NOT NULL DEFAULT 0,
    lead_rows_rejected  INTEGER NOT NULL DEFAULT 0,
    duplicates_merged   INTEGER NOT NULL DEFAULT 0,
    field_warnings      INTEGER NOT NULL DEFAULT 0,
    events_total        INTEGER NOT NULL DEFAULT 0,
    malformed_events    INTEGER NOT NULL DEFAULT 0,
    duplicate_events    INTEGER NOT NULL DEFAULT 0
);

-- Append-only interaction ledger
CREATE TABLE IF NOT EXISTS events (
    event_id        TEXT PRIMARY KEY,
    event_type      TEXT NOT NULL,
    workflow_id     TEXT NOT NULL,
    call_id         TEXT NOT NULL,
    phone_token     TEXT,
    occurred_at_utc TEXT NOT NULL,
    occurred_at_ist TEXT NOT NULL,
    date_ist        TEXT NOT NULL,
    payload_json    TEXT NOT NULL,
    file_id         INTEGER NOT NULL REFERENCES ingest_files(id)
);

CREATE INDEX IF NOT EXISTS idx_ev_call      ON events (call_id);
CREATE INDEX IF NOT EXISTS idx_ev_lead      ON events (workflow_id, phone_token);
CREATE INDEX IF NOT EXISTS idx_ev_date      ON events (workflow_id, date_ist);
CREATE INDEX IF NOT EXISTS idx_ev_type_call ON events (event_type, call_id);

-- Enforce append-only semantics via database triggers
CREATE TRIGGER IF NOT EXISTS trg_events_no_update
BEFORE UPDATE ON events BEGIN
    SELECT RAISE(ABORT, 'events table is append-only: UPDATE forbidden');
END;

CREATE TRIGGER IF NOT EXISTS trg_events_no_delete
BEFORE DELETE ON events BEGIN
    SELECT RAISE(ABORT, 'events table is append-only: DELETE forbidden');
END;
"""


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Creates a sqlite3 connection with WAL mode and foreign keys enabled."""
    target_path = Path(db_path) if db_path else get_default_db_path()
    target_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(target_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


def init_db(db_path: Path | str | None = None, schema_dir: Path | None = None) -> sqlite3.Connection:
    """Initializes tables, triggers, indexes, and seeds the disposition catalogue."""
    conn = get_connection(db_path)
    with conn:
        conn.executescript(DDL_STATEMENTS)

        # Populate dispositions catalogue
        disp_records = load_dispositions(schema_dir or get_schema_dir())
        conn.executemany(
            """
            INSERT OR REPLACE INTO dispositions (workflow_id, code, label, is_terminal, is_success)
            VALUES (:workflow_id, :code, :label, :is_terminal, :is_success);
            """,
            disp_records,
        )
    return conn

