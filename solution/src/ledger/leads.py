from __future__ import annotations

import csv
import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ledger.normalize import (
    norm_header,
    parse_phone,
    parse_date,
    parse_decimal,
    parse_enum,
    parse_string,
)
from ledger.pii import hash_phone_token
from ledger.schema_loader import load_workflow_schema


def get_file_sha256(file_path: Path) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def ingest_lead_file(
    conn: sqlite3.Connection,
    file_path: Path,
    drop_id: str,
    workflow_id: str,
    hmac_key: str,
    schema_dir: Path | None = None,
) -> bool:
    """Ingests a single lead CSV file inside an atomic transaction.
    
    Returns True if file was ingested, or False if skipped due to idempotency.
    """
    rel_path = file_path.name
    batch_id = file_path.stem

    # 1. Idempotency check
    row = conn.execute(
        "SELECT id FROM ingest_files WHERE drop_id = ? AND rel_path = ?",
        (drop_id, str(file_path.as_posix())),
    ).fetchone()
    if row:
        return False

    file_hash = get_file_sha256(file_path)

    # 2. Load schema
    schema = load_workflow_schema(workflow_id, schema_dir)
    fields = schema["fields"]

    # Build alias map: norm(alias) -> field_def
    alias_map: dict[str, dict[str, Any]] = {}
    ref_field_name: str | None = None
    default_values: dict[str, Any] = {}

    for f_def in fields:
        name = f_def["name"]
        alias_map[norm_header(name)] = f_def
        for alias in f_def.get("aliases", []):
            alias_map[norm_header(alias)] = f_def
        if f_def.get("reference"):
            ref_field_name = name
        if "default" in f_def:
            default_values[name] = f_def["default"]

    # Read CSV
    with open(file_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        try:
            raw_header = next(reader)
        except StopIteration:
            raw_header = []

        col_to_field: dict[int, dict[str, Any]] = {}
        for idx, col_name in enumerate(raw_header):
            norm_col = norm_header(col_name)
            if norm_col in alias_map:
                col_to_field[idx] = alias_map[norm_col]

        data_rows = list(reader)

    # Ingest counters for this file
    lead_rows_total = 0
    lead_rows_rejected = 0
    duplicates_merged = 0
    field_warnings = 0

    rejection_records: list[tuple[str, str, int, str, int]] = []
    leads_to_insert: list[dict[str, Any]] = []
    leads_to_update: list[dict[str, Any]] = []

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    row_number = 0

    with conn:
        # Monotonic sequence
        seq_row = conn.execute("SELECT COALESCE(MAX(ingest_seq), 0) + 1 AS next_seq FROM ingest_files").fetchone()
        next_seq = seq_row["next_seq"]

        cur = conn.execute(
            """
            INSERT INTO ingest_files (drop_id, rel_path, file_hash, file_type, ingest_seq, ingested_at)
            VALUES (?, ?, ?, 'leads', ?, ?);
            """,
            (drop_id, str(file_path.as_posix()), file_hash, next_seq, now_utc),
        )
        file_id = cur.lastrowid

        for raw_cells in data_rows:
            # Check blank row: every cell empty or whitespace
            if not raw_cells or all(not cell.strip() for cell in raw_cells):
                continue  # Blank row: skipped and not counted

            row_number += 1
            lead_rows_total += 1

            # Build extracted raw values for schema fields
            raw_values: dict[str, str | None] = {}
            for col_idx, f_def in col_to_field.items():
                if col_idx < len(raw_cells):
                    raw_val = raw_cells[col_idx].strip()
                    raw_values[f_def["name"]] = raw_val if raw_val else None

            # Validate fields in schema order
            rejection_reason: str | None = None
            row_warnings = 0
            parsed_values: dict[str, Any] = {}

            for f_def in fields:
                name = f_def["name"]
                ftype = f_def["type"]
                is_req = f_def.get("required", False)
                val = raw_values.get(name)

                # Missing required check
                if is_req and val is None:
                    rejection_reason = f"missing_required:{name}"
                    break

                # Phone check
                if ftype == "phone":
                    if val is not None:
                        canonical = parse_phone(val)
                        if canonical is None:
                            rejection_reason = "invalid_phone"
                            break
                        parsed_values[name] = canonical
                    else:
                        parsed_values[name] = None

                # Date check
                elif ftype == "date":
                    if val is not None:
                        parsed_d = parse_date(val)
                        if parsed_d is None:
                            row_warnings += 1
                            parsed_values[name] = None
                        else:
                            parsed_values[name] = parsed_d
                    else:
                        parsed_values[name] = None

                # Decimal check
                elif ftype == "decimal":
                    if val is not None:
                        parsed_num = parse_decimal(val)
                        if parsed_num is None:
                            row_warnings += 1
                            parsed_values[name] = None
                        else:
                            parsed_values[name] = parsed_num
                    else:
                        parsed_values[name] = None

                # Enum check
                elif ftype == "enum":
                    if val is not None:
                        parsed_e = parse_enum(val, f_def.get("values", []))
                        if parsed_e is None:
                            row_warnings += 1
                            parsed_values[name] = None
                        else:
                            parsed_values[name] = parsed_e
                    else:
                        parsed_values[name] = None

                # String check
                elif ftype == "string":
                    parsed_values[name] = parse_string(val)

            if rejection_reason is not None:
                # Row is rejected
                lead_rows_rejected += 1
                rejection_records.append((workflow_id, batch_id, row_number, rejection_reason, file_id))
                continue

            # Row is valid: accumulate warnings
            field_warnings += row_warnings

            canonical_phone = parsed_values["phone"]
            phone_token = hash_phone_token(canonical_phone, hmac_key)
            phone_last4 = canonical_phone[-4:]
            external_ref = parsed_values.get(ref_field_name or "", "")

            # Check if lead identity exists
            existing = conn.execute(
                "SELECT batch_id FROM leads WHERE workflow_id = ? AND phone_token = ?",
                (workflow_id, phone_token),
            ).fetchone()

            if existing is None:
                # First valid row creates the lead; its batch_id is permanent
                conn.execute(
                    """
                    INSERT INTO leads (
                        workflow_id, phone_token, phone_last4, external_ref, batch_id,
                        preferred_language, policy_expiry, premium_amount, product, order_date,
                        merge_count, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?);
                    """,
                    (
                        workflow_id,
                        phone_token,
                        phone_last4,
                        external_ref,
                        batch_id,
                        parsed_values.get("preferred_language"),
                        parsed_values.get("policy_expiry"),
                        parsed_values.get("premium_amount"),
                        parsed_values.get("product"),
                        parsed_values.get("order_date"),
                        now_utc,
                    ),
                )
            else:
                # Duplicate row: merge non-null fields
                duplicates_merged += 1
                conn.execute(
                    """
                    UPDATE leads SET
                        external_ref = COALESCE(?, external_ref),
                        preferred_language = COALESCE(?, preferred_language),
                        policy_expiry = COALESCE(?, policy_expiry),
                        premium_amount = COALESCE(?, premium_amount),
                        product = COALESCE(?, product),
                        order_date = COALESCE(?, order_date),
                        merge_count = merge_count + 1
                    WHERE workflow_id = ? AND phone_token = ?;
                    """,
                    (
                        external_ref if external_ref else None,
                        parsed_values.get("preferred_language"),
                        parsed_values.get("policy_expiry"),
                        parsed_values.get("premium_amount"),
                        parsed_values.get("product"),
                        parsed_values.get("order_date"),
                        workflow_id,
                        phone_token,
                    ),
                )

            # Apply defaults for this lead to any fields still null
            for def_name, def_val in default_values.items():
                if def_name == "preferred_language":
                    conn.execute(
                        "UPDATE leads SET preferred_language = ? WHERE workflow_id = ? AND phone_token = ? AND preferred_language IS NULL",
                        (def_val, workflow_id, phone_token),
                    )

        # Batch insert rejections
        if rejection_records:
            conn.executemany(
                """
                INSERT INTO rejections (workflow_id, batch_id, row_number, reason, file_id)
                VALUES (?, ?, ?, ?, ?);
                """,
                rejection_records,
            )

        # Record file counters
        conn.execute(
            """
            INSERT INTO ingest_counters (
                file_id, lead_rows_total, lead_rows_rejected, duplicates_merged, field_warnings
            ) VALUES (?, ?, ?, ?, ?);
            """,
            (file_id, lead_rows_total, lead_rows_rejected, duplicates_merged, field_warnings),
        )

    return True

