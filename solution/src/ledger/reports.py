from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from typing import Any


def generate_reports(conn: sqlite3.Connection, out_dir: Path) -> dict[str, Path]:
    """Generates the five report files matching the exact specification."""
    out_dir.mkdir(parents=True, exist_ok=True)

    # Output file paths
    batch_summary_path = out_dir / "batch_summary.csv"
    daily_overview_path = out_dir / "daily_overview.csv"
    lead_report_path = out_dir / "lead_report.csv"
    rejections_path = out_dir / "rejections.csv"
    data_quality_path = out_dir / "data_quality.json"

    # 1. Base query: Determine all linked events and call properties
    # An event is linked if:
    # - It has a phone_token that exists in leads for that workflow_id
    # - Or it is disposition.override whose call_id has another event linked to a lead
    conn.execute("DROP VIEW IF EXISTS v_call_lead_map;")
    conn.execute("DROP VIEW IF EXISTS v_linked_events;")

    conn.execute("""
    CREATE TEMP VIEW v_call_lead_map AS
    SELECT DISTINCT e.workflow_id, e.call_id, e.phone_token
    FROM events e
    JOIN leads l ON l.workflow_id = e.workflow_id AND l.phone_token = e.phone_token
    WHERE e.phone_token IS NOT NULL;
    """)

    conn.execute("""
    CREATE TEMP VIEW v_linked_events AS
    SELECT e.event_id, e.event_type, e.workflow_id, e.call_id,
           COALESCE(e.phone_token, clm.phone_token) AS phone_token,
           e.occurred_at_utc, e.occurred_at_ist, e.date_ist, e.payload_json
    FROM events e
    LEFT JOIN v_call_lead_map clm ON clm.workflow_id = e.workflow_id AND clm.call_id = e.call_id
    WHERE (e.phone_token IS NOT NULL AND EXISTS (
        SELECT 1 FROM leads l WHERE l.workflow_id = e.workflow_id AND l.phone_token = e.phone_token
    )) OR (
        e.phone_token IS NULL AND clm.phone_token IS NOT NULL
    );
    """)

    # 2. Extract valid disposition codes per call
    # Disposition events: disposition.override or disposition.set
    # Ignored if code is not in dispositions catalogue for that workflow
    conn.execute("DROP VIEW IF EXISTS v_call_dispositions;")
    conn.execute("""
    CREATE TEMP VIEW v_call_dispositions AS
    SELECT le.call_id, le.workflow_id, le.event_type,
           json_extract(le.payload_json, '$.disposition_code') AS disp_code,
           d.is_terminal, d.is_success,
           le.occurred_at_utc, le.event_id
    FROM v_linked_events le
    JOIN dispositions d ON d.workflow_id = le.workflow_id
                       AND d.code = json_extract(le.payload_json, '$.disposition_code')
    WHERE le.event_type IN ('disposition.set', 'disposition.override');
    """)

    # Effective disposition per call:
    # Latest override by occurred_at_utc DESC, event_id DESC.
    # If no override, latest disposition.set.
    conn.execute("DROP VIEW IF EXISTS v_call_effective_disposition;")
    conn.execute("""
    CREATE TEMP VIEW v_call_effective_disposition AS
    WITH ranked_overrides AS (
        SELECT call_id, disp_code, is_terminal, is_success,
               ROW_NUMBER() OVER (PARTITION BY call_id ORDER BY occurred_at_utc DESC, event_id DESC) AS rn
        FROM v_call_dispositions
        WHERE event_type = 'disposition.override'
    ),
    ranked_sets AS (
        SELECT call_id, disp_code, is_terminal, is_success,
               ROW_NUMBER() OVER (PARTITION BY call_id ORDER BY occurred_at_utc DESC, event_id DESC) AS rn
        FROM v_call_dispositions
        WHERE event_type = 'disposition.set'
    )
    SELECT c.call_id,
           COALESCE(ro.disp_code, rs.disp_code) AS effective_code,
           COALESCE(ro.is_terminal, rs.is_terminal) AS is_terminal,
           COALESCE(ro.is_success, rs.is_success) AS is_success
    FROM (SELECT DISTINCT call_id FROM v_linked_events) c
    LEFT JOIN ranked_overrides ro ON ro.call_id = c.call_id AND ro.rn = 1
    LEFT JOIN ranked_sets rs ON rs.call_id = c.call_id AND rs.rn = 1
    WHERE ro.disp_code IS NOT NULL OR rs.disp_code IS NOT NULL;
    """)

    # Calls table: Counted ONLY if call.initiated is present and linked
    conn.execute("DROP VIEW IF EXISTS v_counted_calls;")
    conn.execute("""
    CREATE TEMP VIEW v_counted_calls AS
    SELECT le.call_id, le.workflow_id, le.phone_token,
           le.occurred_at_utc AS initiated_at_utc,
           le.occurred_at_ist AS initiated_at_ist,
           le.date_ist AS initiated_date_ist,
           CASE WHEN EXISTS (
               SELECT 1 FROM v_linked_events c_conn
               WHERE c_conn.call_id = le.call_id AND c_conn.event_type = 'call.connected'
           ) THEN 1 ELSE 0 END AS is_connected,
           CASE WHEN EXISTS (
               SELECT 1 FROM v_linked_events c_fail
               WHERE c_fail.call_id = le.call_id AND c_fail.event_type = 'call.telephony_failed'
           ) THEN 1 ELSE 0 END AS is_telephony_failed,
           ed.effective_code,
           COALESCE(ed.is_terminal, 0) AS is_terminal,
           COALESCE(ed.is_success, 0) AS is_success
    FROM v_linked_events le
    LEFT JOIN v_call_effective_disposition ed ON ed.call_id = le.call_id
    WHERE le.event_type = 'call.initiated';
    """)

    # Lead summary metrics:
    # - total_calls
    # - connected_calls
    # - last_call_at
    # - bucket
    # - current_disposition
    # - ever_success
    conn.execute("DROP VIEW IF EXISTS v_lead_metrics;")
    conn.execute("""
    CREATE TEMP VIEW v_lead_metrics AS
    WITH lead_call_stats AS (
        SELECT cc.workflow_id, cc.phone_token,
               COUNT(cc.call_id) AS total_calls,
               SUM(cc.is_connected) AS connected_calls,
               SUM(cc.is_telephony_failed) AS failed_calls,
               MAX(cc.initiated_at_utc) AS max_init_utc,
               MAX(cc.is_terminal) AS has_terminal,
               MAX(cc.is_success) AS ever_success
        FROM v_counted_calls cc
        GROUP BY cc.workflow_id, cc.phone_token
    ),
    latest_init AS (
        SELECT cc.workflow_id, cc.phone_token, cc.initiated_at_ist AS last_call_at,
               ROW_NUMBER() OVER (PARTITION BY cc.workflow_id, cc.phone_token ORDER BY cc.initiated_at_utc DESC, cc.call_id DESC) AS rn
        FROM v_counted_calls cc
    ),
    current_disp AS (
        SELECT cc.workflow_id, cc.phone_token, cc.effective_code AS current_disposition,
               ROW_NUMBER() OVER (PARTITION BY cc.workflow_id, cc.phone_token ORDER BY cc.initiated_at_utc DESC, cc.call_id DESC) AS rn
        FROM v_counted_calls cc
        WHERE cc.effective_code IS NOT NULL
    )
    SELECT l.workflow_id, l.phone_token, l.external_ref, l.batch_id, l.phone_last4,
           COALESCE(lcs.total_calls, 0) AS total_calls,
           COALESCE(lcs.connected_calls, 0) AS connected_calls,
           li.last_call_at,
           cd.current_disposition,
           CASE WHEN COALESCE(lcs.ever_success, 0) = 1 THEN 'true' ELSE 'false' END AS ever_success,
           CASE
               WHEN COALESCE(lcs.total_calls, 0) = 0 THEN 'yet_to_reach_out'
               WHEN COALESCE(lcs.has_terminal, 0) = 1 THEN 'completed'
               WHEN lcs.total_calls = lcs.failed_calls THEN 'in_progress_telephony_failed'
               ELSE 'in_progress_dialed'
           END AS bucket
    FROM leads l
    LEFT JOIN lead_call_stats lcs ON lcs.workflow_id = l.workflow_id AND lcs.phone_token = l.phone_token
    LEFT JOIN latest_init li ON li.workflow_id = l.workflow_id AND li.phone_token = l.phone_token AND li.rn = 1
    LEFT JOIN current_disp cd ON cd.workflow_id = l.workflow_id AND cd.phone_token = l.phone_token AND cd.rn = 1;
    """)

    # -------------------------------------------------------------
    # 1. Write batch_summary.csv
    # workflow_id,batch_id,total_leads,yet_to_reach_out,in_progress_telephony_failed,in_progress_dialed,completed
    # Sorted by: workflow_id, batch_id
    # -------------------------------------------------------------
    batch_rows = conn.execute("""
    SELECT workflow_id, batch_id,
           COUNT(*) AS total_leads,
           SUM(CASE WHEN bucket = 'yet_to_reach_out' THEN 1 ELSE 0 END) AS yet_to_reach_out,
           SUM(CASE WHEN bucket = 'in_progress_telephony_failed' THEN 1 ELSE 0 END) AS in_progress_telephony_failed,
           SUM(CASE WHEN bucket = 'in_progress_dialed' THEN 1 ELSE 0 END) AS in_progress_dialed,
           SUM(CASE WHEN bucket = 'completed' THEN 1 ELSE 0 END) AS completed
    FROM v_lead_metrics
    GROUP BY workflow_id, batch_id
    ORDER BY workflow_id, batch_id;
    """).fetchall()

    with open(batch_summary_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["workflow_id", "batch_id", "total_leads", "yet_to_reach_out", "in_progress_telephony_failed", "in_progress_dialed", "completed"])
        for r in batch_rows:
            writer.writerow([
                r["workflow_id"],
                r["batch_id"],
                r["total_leads"],
                r["yet_to_reach_out"],
                r["in_progress_telephony_failed"],
                r["in_progress_dialed"],
                r["completed"],
            ])

    # -------------------------------------------------------------
    # 2. Write daily_overview.csv
    # workflow_id,date,total_calls,connected_calls,connectivity_rate,success_leads
    # Sorted by: workflow_id, date
    # One row per workflow per IST date that has at least one call
    # -------------------------------------------------------------
    daily_rows = conn.execute("""
    SELECT cc.workflow_id, cc.initiated_date_ist AS call_date,
           COUNT(cc.call_id) AS total_calls,
           SUM(cc.is_connected) AS connected_calls,
           COUNT(DISTINCT CASE WHEN cc.is_success = 1 THEN cc.phone_token END) AS success_leads
    FROM v_counted_calls cc
    GROUP BY cc.workflow_id, cc.initiated_date_ist
    HAVING COUNT(cc.call_id) > 0
    ORDER BY cc.workflow_id, cc.initiated_date_ist;
    """).fetchall()

    with open(daily_overview_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["workflow_id", "date", "total_calls", "connected_calls", "connectivity_rate", "success_leads"])
        for r in daily_rows:
            t_calls = r["total_calls"]
            c_calls = r["connected_calls"]
            conn_rate = (c_calls / t_calls) if t_calls > 0 else 0.0
            writer.writerow([
                r["workflow_id"],
                r["call_date"],
                t_calls,
                c_calls,
                f"{conn_rate:.4f}",
                r["success_leads"],
            ])

    # -------------------------------------------------------------
    # 3. Write lead_report.csv
    # workflow_id,external_ref,batch_id,phone_masked,bucket,total_calls,connected_calls,last_call_at,current_disposition,ever_success
    # Sorted by: workflow_id, external_ref
    # -------------------------------------------------------------
    lead_rows = conn.execute("""
    SELECT workflow_id, external_ref, batch_id, phone_last4, bucket,
           total_calls, connected_calls, last_call_at, current_disposition, ever_success
    FROM v_lead_metrics
    ORDER BY workflow_id, external_ref;
    """).fetchall()

    with open(lead_report_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["workflow_id", "external_ref", "batch_id", "phone_masked", "bucket", "total_calls", "connected_calls", "last_call_at", "current_disposition", "ever_success"])
        for r in lead_rows:
            masked = f"+91******{r['phone_last4']}"
            writer.writerow([
                r["workflow_id"],
                r["external_ref"],
                r["batch_id"],
                masked,
                r["bucket"],
                r["total_calls"],
                r["connected_calls"],
                r["last_call_at"] or "",
                r["current_disposition"] or "",
                r["ever_success"],
            ])

    # -------------------------------------------------------------
    # 4. Write rejections.csv
    # workflow_id,batch_id,row_number,reason
    # Sorted by: workflow_id, batch_id, row_number
    # -------------------------------------------------------------
    rej_rows = conn.execute("""
    SELECT workflow_id, batch_id, row_number, reason
    FROM rejections
    ORDER BY workflow_id, batch_id, row_number;
    """).fetchall()

    with open(rejections_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["workflow_id", "batch_id", "row_number", "reason"])
        for r in rej_rows:
            writer.writerow([r["workflow_id"], r["batch_id"], r["row_number"], r["reason"]])

    # -------------------------------------------------------------
    # 5. Write data_quality.json
    # Exact structure:
    # {
    #   "lead_rows_total": 0,
    #   "lead_rows_rejected": 0,
    #   "rejections_by_reason": {"invalid_phone": 0, ...},
    #   "duplicate_lead_rows_merged": 0,
    #   "field_warnings": 0,
    #   "events_total": 0,
    #   "malformed_events": 0,
    #   "duplicate_events_dropped": 0,
    #   "orphan_events": 0,
    #   "unknown_disposition_events": 0
    # }
    # -------------------------------------------------------------
    counters_row = conn.execute("""
    SELECT COALESCE(SUM(lead_rows_total), 0) AS lead_rows_total,
           COALESCE(SUM(lead_rows_rejected), 0) AS lead_rows_rejected,
           COALESCE(SUM(duplicates_merged), 0) AS duplicate_lead_rows_merged,
           COALESCE(SUM(field_warnings), 0) AS field_warnings,
           COALESCE(SUM(events_total), 0) AS events_total,
           COALESCE(SUM(malformed_events), 0) AS malformed_events,
           COALESCE(SUM(duplicate_events), 0) AS duplicate_events_dropped
    FROM ingest_counters;
    """).fetchone()

    # Rejections breakdown
    rej_by_reason_rows = conn.execute("""
    SELECT reason, COUNT(*) AS count
    FROM rejections
    GROUP BY reason
    ORDER BY reason;
    """).fetchall()
    rejections_by_reason = {r["reason"]: r["count"] for r in rej_by_reason_rows}

    # Orphan events:
    # Any event in events that is not in v_linked_events
    orphan_row = conn.execute("""
    SELECT COUNT(*) AS count
    FROM events e
    WHERE e.event_id NOT IN (SELECT event_id FROM v_linked_events);
    """).fetchone()
    orphan_events = orphan_row["count"]

    # Unknown disposition events:
    # Linked disposition.* events whose code is not in dispositions catalogue
    unknown_disp_row = conn.execute("""
    SELECT COUNT(*) AS count
    FROM v_linked_events le
    WHERE le.event_type IN ('disposition.set', 'disposition.override')
      AND NOT EXISTS (
          SELECT 1 FROM dispositions d
          WHERE d.workflow_id = le.workflow_id
            AND d.code = json_extract(le.payload_json, '$.disposition_code')
      );
    """).fetchone()
    unknown_disposition_events = unknown_disp_row["count"]

    dq_data = {
        "lead_rows_total": counters_row["lead_rows_total"],
        "lead_rows_rejected": counters_row["lead_rows_rejected"],
        "rejections_by_reason": rejections_by_reason,
        "duplicate_lead_rows_merged": counters_row["duplicate_lead_rows_merged"],
        "field_warnings": counters_row["field_warnings"],
        "events_total": counters_row["events_total"],
        "malformed_events": counters_row["malformed_events"],
        "duplicate_events_dropped": counters_row["duplicate_events_dropped"],
        "orphan_events": orphan_events,
        "unknown_disposition_events": unknown_disposition_events,
    }

    with open(data_quality_path, "w", encoding="utf-8") as f:
        json.dump(dq_data, f, indent=2)

    return {
        "batch_summary": batch_summary_path,
        "daily_overview": daily_overview_path,
        "lead_report": lead_report_path,
        "rejections": rejections_path,
        "data_quality": data_quality_path,
    }

