from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
import pytest

from ledger.events import ingest_event_file
from ledger.leads import ingest_lead_file
from ledger.reports import generate_reports


def create_csv(path: Path, header: list[str], rows: list[list[str]]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)


def create_jsonl(path: Path, lines: list[dict | str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for item in lines:
            if isinstance(item, str):
                f.write(item + "\n")
            else:
                f.write(json.dumps(item) + "\n")


class TestLeadRules:
    def test_rejection_order_missing_name_before_invalid_phone(self, temp_db: sqlite3.Connection, tmp_path: Path, hmac_key: str):
        # Schema order for motor_renewal: customer_name, phone, policy_number, policy_expiry, premium_amount, preferred_language
        csv_file = tmp_path / "batch_001.csv"
        # Name is missing, phone is also invalid
        header = ["customer_name", "phone", "policy_number", "policy_expiry", "premium_amount", "preferred_language"]
        rows = [
            ["", "12345", "POL-001", "2026-10-10", "1000", "en"],
        ]
        create_csv(csv_file, header, rows)
        ingest_lead_file(temp_db, csv_file, "drop_1", "motor_renewal", hmac_key)

        rej = temp_db.execute("SELECT row_number, reason FROM rejections").fetchall()
        assert len(rej) == 1
        assert rej[0]["reason"] == "missing_required:customer_name"
        assert rej[0]["row_number"] == 1

    def test_rejection_invalid_phone_when_name_present(self, temp_db: sqlite3.Connection, tmp_path: Path, hmac_key: str):
        csv_file = tmp_path / "batch_001.csv"
        header = ["customer_name", "phone", "policy_number", "policy_expiry", "premium_amount", "preferred_language"]
        rows = [
            ["Aarav Sharma", "5938472019", "POL-001", "2026-10-10", "1000", "en"],
        ]
        create_csv(csv_file, header, rows)
        ingest_lead_file(temp_db, csv_file, "drop_1", "motor_renewal", hmac_key)

        rej = temp_db.execute("SELECT row_number, reason FROM rejections").fetchall()
        assert len(rej) == 1
        assert rej[0]["reason"] == "invalid_phone"

    def test_rejection_missing_ref(self, temp_db: sqlite3.Connection, tmp_path: Path, hmac_key: str):
        csv_file = tmp_path / "batch_001.csv"
        header = ["customer_name", "phone", "policy_number"]
        rows = [
            ["Aarav Sharma", "9876543210", ""],
        ]
        create_csv(csv_file, header, rows)
        ingest_lead_file(temp_db, csv_file, "drop_1", "motor_renewal", hmac_key)

        rej = temp_db.execute("SELECT row_number, reason FROM rejections").fetchall()
        assert len(rej) == 1
        assert rej[0]["reason"] == "missing_required:policy_number"

    def test_blank_rows_skipped_not_counted(self, temp_db: sqlite3.Connection, tmp_path: Path, hmac_key: str):
        csv_file = tmp_path / "batch_001.csv"
        header = ["customer_name", "phone", "policy_number"]
        rows = [
            ["", "", ""],  # Blank row 1
            ["Aarav Sharma", "9876543210", "POL-001"],  # Data row 1 -> row_number 1
            ["   ", "  ", ""],  # Blank row 2
            ["", "9876543211", "POL-002"],  # Data row 2 -> rejected -> row_number 2
        ]
        create_csv(csv_file, header, rows)
        ingest_lead_file(temp_db, csv_file, "drop_1", "motor_renewal", hmac_key)

        counters = temp_db.execute("SELECT lead_rows_total, lead_rows_rejected FROM ingest_counters").fetchone()
        assert counters["lead_rows_total"] == 2
        assert counters["lead_rows_rejected"] == 1

        rej = temp_db.execute("SELECT row_number, reason FROM rejections").fetchall()
        assert len(rej) == 1
        assert rej[0]["row_number"] == 2  # The 2nd non-blank row

    def test_optional_field_warning_and_null_storage(self, temp_db: sqlite3.Connection, tmp_path: Path, hmac_key: str):
        csv_file = tmp_path / "batch_001.csv"
        header = ["customer_name", "phone", "policy_number", "policy_expiry", "premium_amount", "preferred_language"]
        rows = [
            # Invalid date "31/02/2026" and invalid premium "N/A", but invalid lang "english"
            ["Aarav Sharma", "9876543210", "POL-001", "31/02/2026", "N/A", "english"],
        ]
        create_csv(csv_file, header, rows)
        ingest_lead_file(temp_db, csv_file, "drop_1", "motor_renewal", hmac_key)

        counters = temp_db.execute("SELECT lead_rows_total, lead_rows_rejected, field_warnings FROM ingest_counters").fetchone()
        assert counters["lead_rows_total"] == 1
        assert counters["lead_rows_rejected"] == 0
        assert counters["field_warnings"] == 3  # date, decimal, enum

        lead = temp_db.execute("SELECT policy_expiry, premium_amount, preferred_language FROM leads").fetchone()
        assert lead["policy_expiry"] is None
        assert lead["premium_amount"] is None
        # Preferred language receives default "en" after merge
        assert lead["preferred_language"] == "en"

    def test_batch_id_permanence_and_duplicate_merge(self, temp_db: sqlite3.Connection, tmp_path: Path, hmac_key: str):
        # File 1 in drop 1: batch_001
        csv1 = tmp_path / "batch_001.csv"
        header1 = ["customer_name", "phone", "policy_number", "premium_amount", "preferred_language"]
        rows1 = [
            ["Aarav Sharma", "9876543210", "POL-001", "1000", "hi"],
        ]
        create_csv(csv1, header1, rows1)
        ingest_lead_file(temp_db, csv1, "drop_1", "motor_renewal", hmac_key)

        # File 2 in drop 2: batch_003 with aliased headers and update to premium, but empty language
        csv2 = tmp_path / "batch_003.csv"
        header2 = ["Insured Name", "Mobile No", "Policy No", "Premium", "Language"]
        rows2 = [
            ["Aarav Sharma", "9876543210", "POL-001", "2500", ""],  # empty language should NOT overwrite "hi"
        ]
        create_csv(csv2, header2, rows2)
        ingest_lead_file(temp_db, csv2, "drop_2", "motor_renewal", hmac_key)

        lead = temp_db.execute("SELECT batch_id, premium_amount, preferred_language, merge_count FROM leads").fetchone()
        assert lead["batch_id"] == "batch_001"  # Permanent!
        assert lead["premium_amount"] == 2500.0  # Overwritten
        assert lead["preferred_language"] == "hi"  # Preserved
        assert lead["merge_count"] == 1

        counters = temp_db.execute("SELECT duplicates_merged FROM ingest_counters WHERE file_id = 2").fetchone()
        assert counters["duplicates_merged"] == 1


class TestEventRules:
    def test_event_malformed_and_duplicate_handling(self, temp_db: sqlite3.Connection, tmp_path: Path, hmac_key: str):
        jsonl = tmp_path / "events_0001.jsonl"
        lines = [
            "invalid json truncated...",  # malformed
            {"no_id": True},  # malformed (no event_id)
            {"event_id": "evt_01", "event_type": "call.initiated", "workflow_id": "motor_renewal", "call_id": "c1", "occurred_at": "2026-10-01 10:00:00", "phone": "9876543210"},
            {"event_id": "evt_01", "event_type": "call.initiated", "workflow_id": "motor_renewal", "call_id": "c1", "occurred_at": "2026-10-01 10:00:00", "phone": "9876543210"},  # duplicate in same file
            {"event_id": "evt_02", "event_type": "call.connected", "workflow_id": "motor_renewal", "call_id": "c1", "occurred_at": "2026-10-01 10:01:00", "phone": "9876543210"},
        ]
        create_jsonl(jsonl, lines)
        ingest_event_file(temp_db, jsonl, "drop_1", hmac_key)

        counters = temp_db.execute("SELECT events_total, malformed_events, duplicate_events FROM ingest_counters").fetchone()
        assert counters["events_total"] == 5
        assert counters["malformed_events"] == 2
        assert counters["duplicate_events"] == 1

        events = temp_db.execute("SELECT COUNT(*) AS c FROM events").fetchone()
        assert events["c"] == 2

    def test_effective_disposition_override_beats_set(self, temp_db: sqlite3.Connection, tmp_path: Path, hmac_key: str):
        # 1. Ingest lead
        csv_f = tmp_path / "batch_001.csv"
        create_csv(csv_f, ["customer_name", "phone", "policy_number"], [["Aarav", "9876543210", "POL-001"]])
        ingest_lead_file(temp_db, csv_f, "drop_1", "motor_renewal", hmac_key)

        # 2. Ingest call with disposition.set="renewed" (terminal, success) then supervisor override="not_interested" (terminal, not success)
        jsonl = tmp_path / "events_0001.jsonl"
        lines = [
            {"event_id": "e1", "event_type": "call.initiated", "workflow_id": "motor_renewal", "call_id": "call_1", "occurred_at": "2026-10-01 10:00:00", "phone": "9876543210"},
            {"event_id": "e2", "event_type": "call.connected", "workflow_id": "motor_renewal", "call_id": "call_1", "occurred_at": "2026-10-01 10:00:05", "phone": "9876543210"},
            {"event_id": "e3", "event_type": "disposition.set", "workflow_id": "motor_renewal", "call_id": "call_1", "occurred_at": "2026-10-01 10:05:00", "phone": "9876543210", "payload": {"disposition_code": "renewed"}},
            # Override has NO phone, occurred later
            {"event_id": "e4", "event_type": "disposition.override", "workflow_id": "motor_renewal", "call_id": "call_1", "occurred_at": "2026-10-01 12:00:00", "payload": {"disposition_code": "not_interested"}},
        ]
        create_jsonl(jsonl, lines)
        ingest_event_file(temp_db, jsonl, "drop_1", hmac_key)

        out_dir = tmp_path / "reports"
        generate_reports(temp_db, out_dir)

        # Check lead_report.csv
        with open(out_dir / "lead_report.csv", "r", encoding="utf-8") as f:
            reader = list(csv.DictReader(f))
            assert len(reader) == 1
            assert reader[0]["current_disposition"] == "not_interested"
            assert reader[0]["ever_success"] == "false"
            assert reader[0]["bucket"] == "completed"

    def test_bucket_hierarchy(self, temp_db: sqlite3.Connection, tmp_path: Path, hmac_key: str):
        csv_f = tmp_path / "batch_001.csv"
        create_csv(csv_f, ["customer_name", "phone", "policy_number"], [
            ["L1", "9876543211", "POL-1"],  # 0 calls -> yet_to_reach_out
            ["L2", "9876543212", "POL-2"],  # 1 telephony failed call -> in_progress_telephony_failed
            ["L3", "9876543213", "POL-3"],  # 1 non-terminal connected call -> in_progress_dialed
            ["L4", "9876543214", "POL-4"],  # 1 telephony failed + 1 terminal call -> completed
        ])
        ingest_lead_file(temp_db, csv_f, "drop_1", "motor_renewal", hmac_key)

        events_f = tmp_path / "events_0001.jsonl"
        lines = [
            # L2
            {"event_id": "e21", "event_type": "call.initiated", "workflow_id": "motor_renewal", "call_id": "c21", "occurred_at": "2026-10-01 10:00:00", "phone": "9876543212"},
            {"event_id": "e22", "event_type": "call.telephony_failed", "workflow_id": "motor_renewal", "call_id": "c21", "occurred_at": "2026-10-01 10:00:05", "phone": "9876543212", "payload": {"reason": "sip_503"}},

            # L3
            {"event_id": "e31", "event_type": "call.initiated", "workflow_id": "motor_renewal", "call_id": "c31", "occurred_at": "2026-10-01 10:00:00", "phone": "9876543213"},
            {"event_id": "e32", "event_type": "call.connected", "workflow_id": "motor_renewal", "call_id": "c31", "occurred_at": "2026-10-01 10:00:05", "phone": "9876543213"},
            {"event_id": "e33", "event_type": "disposition.set", "workflow_id": "motor_renewal", "call_id": "c31", "occurred_at": "2026-10-01 10:02:00", "phone": "9876543213", "payload": {"disposition_code": "callback_requested"}},

            # L4
            {"event_id": "e41", "event_type": "call.initiated", "workflow_id": "motor_renewal", "call_id": "c41", "occurred_at": "2026-10-01 09:00:00", "phone": "9876543214"},
            {"event_id": "e42", "event_type": "call.telephony_failed", "workflow_id": "motor_renewal", "call_id": "c41", "occurred_at": "2026-10-01 09:00:05", "phone": "9876543214", "payload": {"reason": "carrier_rejected"}},
            {"event_id": "e43", "event_type": "call.initiated", "workflow_id": "motor_renewal", "call_id": "c42", "occurred_at": "2026-10-01 11:00:00", "phone": "9876543214"},
            {"event_id": "e44", "event_type": "call.connected", "workflow_id": "motor_renewal", "call_id": "c42", "occurred_at": "2026-10-01 11:00:05", "phone": "9876543214"},
            {"event_id": "e45", "event_type": "disposition.set", "workflow_id": "motor_renewal", "call_id": "c42", "occurred_at": "2026-10-01 11:02:00", "phone": "9876543214", "payload": {"disposition_code": "renewed"}},
        ]
        create_jsonl(events_f, lines)
        ingest_event_file(temp_db, events_f, "drop_1", hmac_key)

        out_dir = tmp_path / "reports"
        generate_reports(temp_db, out_dir)

        with open(out_dir / "lead_report.csv", "r", encoding="utf-8") as f:
            leads = {r["external_ref"]: r["bucket"] for r in csv.DictReader(f)}

        assert leads["POL-1"] == "yet_to_reach_out"
        assert leads["POL-2"] == "in_progress_telephony_failed"
        assert leads["POL-3"] == "in_progress_dialed"
        assert leads["POL-4"] == "completed"

