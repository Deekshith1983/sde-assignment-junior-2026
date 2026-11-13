from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ledger.leads import get_file_sha256
from ledger.normalize import parse_event_ts, parse_phone
from ledger.pii import hash_phone_token


def ingest_event_file(
    conn: sqlite3.Connection,
    file_path: Path,
    drop_id: str,
    hmac_key: str,
) -> bool:
    """Ingests a single events JSONL file inside an atomic transaction.
    
    Returns True if file was ingested, or False if skipped due to idempotency.
    """
    rel_path = file_path.name

    # 1. Idempotency check
    row = conn.execute(
        "SELECT id FROM ingest_files WHERE drop_id = ? AND rel_path = ?",
        (drop_id, str(file_path.as_posix())),
    ).fetchone()
    if row:
        return False

    file_hash = get_file_sha256(file_path)

    events_total = 0
    malformed_events = 0
    duplicate_events = 0

    records_to_insert: list[tuple[str, str, str, str, str | None, str, str, str, str, int]] = []
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    with conn:
        seq_row = conn.execute("SELECT COALESCE(MAX(ingest_seq), 0) + 1 AS next_seq FROM ingest_files").fetchone()
        next_seq = seq_row["next_seq"]

        cur = conn.execute(
            """
            INSERT INTO ingest_files (drop_id, rel_path, file_hash, file_type, ingest_seq, ingested_at)
            VALUES (?, ?, ?, 'events', ?, ?);
            """,
            (drop_id, str(file_path.as_posix()), file_hash, next_seq, now_utc),
        )
        file_id = cur.lastrowid

        # In-memory set of event IDs seen in this file to catch duplicate lines in the same file
        seen_in_batch: set[str] = set()

        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line_str = line.strip()
                if not line_str:
                    continue  # Blank lines not counted

                # Rule: events_total counts every non-empty line read, including malformed and duplicate lines
                events_total += 1

                # Rule 1: Malformed: not valid JSON, or has no event_id
                try:
                    data = json.loads(line_str)
                    if not isinstance(data, dict):
                        malformed_events += 1
                        continue
                except Exception:
                    malformed_events += 1
                    continue

                event_id = data.get("event_id")
                if not event_id or not isinstance(event_id, str) or not event_id.strip():
                    malformed_events += 1
                    continue

                event_id = event_id.strip()

                # Rule 2: Duplicate: event_id already seen in this file or any prior drop
                if event_id in seen_in_batch:
                    duplicate_events += 1
                    continue

                existing = conn.execute("SELECT 1 FROM events WHERE event_id = ?", (event_id,)).fetchone()
                if existing:
                    duplicate_events += 1
                    continue

                seen_in_batch.add(event_id)

                event_type = data.get("event_type", "").strip()
                workflow_id = data.get("workflow_id", "").strip()
                call_id = data.get("call_id", "").strip()
                occurred_at_raw = data.get("occurred_at", "").strip()

                if not event_type or not workflow_id or not call_id or not occurred_at_raw:
                    malformed_events += 1
                    continue

                try:
                    _, occurred_at_utc, occurred_at_ist, date_ist = parse_event_ts(occurred_at_raw)
                except Exception:
                    malformed_events += 1
                    continue

                # Phone token resolution (overrides lack phone)
                raw_phone = data.get("phone")
                phone_token = None
                if raw_phone:
                    canonical_phone = parse_phone(str(raw_phone))
                    if canonical_phone:
                        phone_token = hash_phone_token(canonical_phone, hmac_key)

                payload_json = json.dumps(data.get("payload", {}), ensure_ascii=False)

                records_to_insert.append((
                    event_id,
                    event_type,
                    workflow_id,
                    call_id,
                    phone_token,
                    occurred_at_utc,
                    occurred_at_ist,
                    date_ist,
                    payload_json,
                    file_id,
                ))

        if records_to_insert:
            conn.executemany(
                """
                INSERT INTO events (
                    event_id, event_type, workflow_id, call_id, phone_token,
                    occurred_at_utc, occurred_at_ist, date_ist, payload_json, file_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                records_to_insert,
            )

        conn.execute(
            """
            INSERT INTO ingest_counters (
                file_id, events_total, malformed_events, duplicate_events
            ) VALUES (?, ?, ?, ?);
            """,
            (file_id, events_total, malformed_events, duplicate_events),
        )

    return True

