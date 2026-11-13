from __future__ import annotations

import csv
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
import pytest

from ledger.cli import main
from ledger.db import init_db


def run_pipeline(args_list: list[str], hmac_key: str):
    import os
    env = os.environ.copy()
    env["PII_HMAC_KEY"] = hmac_key
    # Run through Python module or main directly
    main(args_list)


class TestIntegration:
    @pytest.fixture
    def test_drops(self, tmp_path_factory):
        data_dir = tmp_path_factory.mktemp("data_gen")
        # Generate small dataset with seed 42 into temp directory
        script = Path(__file__).resolve().parents[2] / "assignment-a-data" / "tools" / "generate_data.py"
        subprocess.run(
            [sys.executable, str(script), "--seed", "42", "--scale", "small", "--out", str(data_dir)],
            check=True,
            capture_output=True,
        )
        return data_dir

    def test_idempotency_and_report_reproducibility(self, test_drops: Path, tmp_path: Path, hmac_key: str):
        db_path = tmp_path / "idempotent.db"
        reports_1 = tmp_path / "reports_run1"
        reports_2 = tmp_path / "reports_run2"

        # 1. Ingest drop_1 and drop_2
        main(["ingest", str(test_drops / "drop_1"), "--db", str(db_path)])
        main(["ingest", str(test_drops / "drop_2"), "--db", str(db_path)])

        conn = init_db(db_path)
        leads_count_1 = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        events_count_1 = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        rejections_count_1 = conn.execute("SELECT COUNT(*) FROM rejections").fetchone()[0]
        conn.close()

        main(["report", "--out", str(reports_1), "--db", str(db_path)])

        # 2. Re-ingest drop_1 and drop_2 (must change nothing)
        main(["ingest", str(test_drops / "drop_1"), "--db", str(db_path)])
        main(["ingest", str(test_drops / "drop_2"), "--db", str(db_path)])

        conn = init_db(db_path)
        leads_count_2 = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        events_count_2 = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        rejections_count_2 = conn.execute("SELECT COUNT(*) FROM rejections").fetchone()[0]
        conn.close()

        assert leads_count_1 == leads_count_2
        assert events_count_1 == events_count_2
        assert rejections_count_1 == rejections_count_2

        main(["report", "--out", str(reports_2), "--db", str(db_path)])

        # 3. Compare reports byte-for-byte
        for fname in ["batch_summary.csv", "daily_overview.csv", "lead_report.csv", "rejections.csv", "data_quality.json"]:
            b1 = (reports_1 / fname).read_bytes()
            b2 = (reports_2 / fname).read_bytes()
            assert b1 == b2, f"File {fname} differed after re-ingest!"

    def test_incremental_equals_batch(self, test_drops: Path, tmp_path: Path, hmac_key: str):
        db_inc = tmp_path / "incremental.db"
        rep_inc = tmp_path / "rep_inc"

        db_batch = tmp_path / "batch.db"
        rep_batch = tmp_path / "rep_batch"

        # Incremental: drop_1 then drop_2
        main(["ingest", str(test_drops / "drop_1"), "--db", str(db_inc)])
        main(["ingest", str(test_drops / "drop_2"), "--db", str(db_inc)])
        main(["report", "--out", str(rep_inc), "--db", str(db_inc)])

        # Batch: drop_1 then drop_2 in single setup
        main(["ingest", str(test_drops / "drop_1"), "--db", str(db_batch)])
        main(["ingest", str(test_drops / "drop_2"), "--db", str(db_batch)])
        main(["report", "--out", str(rep_batch), "--db", str(db_batch)])

        for fname in ["batch_summary.csv", "daily_overview.csv", "lead_report.csv", "rejections.csv", "data_quality.json"]:
            b_inc = (rep_inc / fname).read_bytes()
            b_bat = (rep_batch / fname).read_bytes()
            assert b_inc == b_bat, f"{fname} differs between incremental and batch!"

    def test_no_plaintext_phone_in_database(self, test_drops: Path, tmp_path: Path, hmac_key: str):
        db_path = tmp_path / "secure.db"
        main(["ingest", str(test_drops / "drop_1"), "--db", str(db_path)])

        # Collect raw phone strings from raw drop_1 csv
        raw_phones = set()
        for f in (test_drops / "drop_1" / "leads").glob("**/*.csv"):
            with open(f, "r", encoding="utf-8") as fp:
                for row in csv.reader(fp):
                    for cell in row:
                        digits = re.sub(r"\D", "", cell)
                        if len(digits) == 10 and digits[0] in "6789":
                            raw_phones.add(digits)
                            raw_phones.add("+91" + digits)

        db_bytes = db_path.read_bytes()
        for phone in raw_phones:
            # Full 10-digit phone must not be present anywhere in DB file
            assert phone.encode("utf-8") not in db_bytes, f"Plaintext phone {phone} leaked in DB bytes!"

    def test_report_contract_formats(self, test_drops: Path, tmp_path: Path, hmac_key: str):
        db_path = tmp_path / "contract.db"
        reports_dir = tmp_path / "contract_reports"
        main(["ingest", str(test_drops / "drop_1"), "--db", str(db_path)])
        main(["ingest", str(test_drops / "drop_2"), "--db", str(db_path)])
        main(["report", "--out", str(reports_dir), "--db", str(db_path)])

        # 1. batch_summary.csv
        with open(reports_dir / "batch_summary.csv", "r", encoding="utf-8") as f:
            reader = list(csv.DictReader(f))
            assert reader[0].keys() == {"workflow_id", "batch_id", "total_leads", "yet_to_reach_out", "in_progress_telephony_failed", "in_progress_dialed", "completed"}
            # Check sum of buckets == total_leads
            for r in reader:
                tot = int(r["total_leads"])
                b_sum = int(r["yet_to_reach_out"]) + int(r["in_progress_telephony_failed"]) + int(r["in_progress_dialed"]) + int(r["completed"])
                assert tot == b_sum
            # Check sorting
            sort_keys = [(r["workflow_id"], r["batch_id"]) for r in reader]
            assert sort_keys == sorted(sort_keys)

        # 2. daily_overview.csv
        with open(reports_dir / "daily_overview.csv", "r", encoding="utf-8") as f:
            reader = list(csv.DictReader(f))
            assert reader[0].keys() == {"workflow_id", "date", "total_calls", "connected_calls", "connectivity_rate", "success_leads"}
            for r in reader:
                assert int(r["total_calls"]) > 0
                assert int(r["connected_calls"]) <= int(r["total_calls"])
                # Exact 4 decimals
                assert re.match(r"^\d+\.\d{4}$", r["connectivity_rate"])
            sort_keys = [(r["workflow_id"], r["date"]) for r in reader]
            assert sort_keys == sorted(sort_keys)

        # 3. lead_report.csv
        with open(reports_dir / "lead_report.csv", "r", encoding="utf-8") as f:
            reader = list(csv.DictReader(f))
            assert reader[0].keys() == {"workflow_id", "external_ref", "batch_id", "phone_masked", "bucket", "total_calls", "connected_calls", "last_call_at", "current_disposition", "ever_success"}
            for r in reader:
                assert re.match(r"^\+91\*{6}\d{4}$", r["phone_masked"])
                assert r["bucket"] in {"yet_to_reach_out", "in_progress_telephony_failed", "in_progress_dialed", "completed"}
                assert r["ever_success"] in {"true", "false"}
                if r["last_call_at"]:
                    assert "+05:30" in r["last_call_at"]
            sort_keys = [(r["workflow_id"], r["external_ref"]) for r in reader]
            assert sort_keys == sorted(sort_keys)

        # 4. rejections.csv
        with open(reports_dir / "rejections.csv", "r", encoding="utf-8") as f:
            reader = list(csv.DictReader(f))
            assert reader[0].keys() == {"workflow_id", "batch_id", "row_number", "reason"}
            sort_keys = [(r["workflow_id"], r["batch_id"], int(r["row_number"])) for r in reader]
            assert sort_keys == sorted(sort_keys)

        # 5. data_quality.json
        with open(reports_dir / "data_quality.json", "r", encoding="utf-8") as f:
            dq = json.load(f)
            expected_keys = [
                "lead_rows_total",
                "lead_rows_rejected",
                "rejections_by_reason",
                "duplicate_lead_rows_merged",
                "field_warnings",
                "events_total",
                "malformed_events",
                "duplicate_events_dropped",
                "orphan_events",
                "unknown_disposition_events",
            ]
            assert list(dq.keys()) == expected_keys
            assert isinstance(dq["lead_rows_total"], int)
            assert isinstance(dq["rejections_by_reason"], dict)

