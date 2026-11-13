from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure solution/src is in sys.path
_pkg_root = Path(__file__).resolve().parent.parent
if str(_pkg_root) not in sys.path:
    sys.path.insert(0, str(_pkg_root))

from ledger.config import get_default_db_path, get_pii_hmac_key
from ledger.db import init_db
from ledger.events import ingest_event_file
from ledger.leads import ingest_lead_file
from ledger.reports import generate_reports


def cmd_ingest(args: argparse.Namespace) -> None:
    drop_dir = Path(args.drop_dir)
    if not drop_dir.exists() or not drop_dir.is_dir():
        print(f"Error: Drop directory not found at {drop_dir}", file=sys.stderr)
        sys.exit(1)

    drop_id = drop_dir.name
    hmac_key = get_pii_hmac_key()
    conn = init_db(args.db)

    print(f"Ingesting drop: {drop_id} from {drop_dir}")

    # 1. Discover and ingest leads files
    # Order: workflows sorted, files sorted by filename
    leads_dir = drop_dir / "leads"
    leads_count = 0
    if leads_dir.exists() and leads_dir.is_dir():
        workflow_dirs = sorted([d for d in leads_dir.iterdir() if d.is_dir()], key=lambda d: d.name)
        for wf_dir in workflow_dirs:
            wf_id = wf_dir.name
            csv_files = sorted(wf_dir.glob("*.csv"), key=lambda p: p.name)
            for csv_file in csv_files:
                ingested = ingest_lead_file(conn, csv_file, drop_id, wf_id, hmac_key)
                status = "Ingested" if ingested else "Skipped (already ingested)"
                print(f"  [Lead] {wf_id}/{csv_file.name}: {status}")
                leads_count += 1

    # 2. Discover and ingest event files
    events_dir = drop_dir / "events"
    events_count = 0
    if events_dir.exists() and events_dir.is_dir():
        jsonl_files = sorted(events_dir.glob("*.jsonl"), key=lambda p: p.name)
        for jsonl_file in jsonl_files:
            ingested = ingest_event_file(conn, jsonl_file, drop_id, hmac_key)
            status = "Ingested" if ingested else "Skipped (already ingested)"
            print(f"  [Event] {jsonl_file.name}: {status}")
            events_count += 1

    conn.close()
    print(f"Drop {drop_id} ingestion complete. ({leads_count} lead files, {events_count} event files)")


def cmd_report(args: argparse.Namespace) -> None:
    out_dir = Path(args.out)
    conn = init_db(args.db)

    print(f"Generating reports into: {out_dir}")
    report_paths = generate_reports(conn, out_dir)
    conn.close()

    for name, p in report_paths.items():
        print(f"  Written: {p.name}")
    print("Report generation complete.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Interaction Ledger & Reporting Pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Ingest subcommand
    parser_ingest = subparsers.add_parser("ingest", help="Ingest a data drop directory")
    parser_ingest.add_argument("drop_dir", help="Path to drop directory (e.g. data/drop_1)")
    parser_ingest.add_argument("--db", default=None, help="Path to SQLite database")
    parser_ingest.set_defaults(func=cmd_ingest)

    # Report subcommand
    parser_report = subparsers.add_parser("report", help="Generate reports from the ledger")
    parser_report.add_argument("--out", required=True, help="Directory to write output reports")
    parser_report.add_argument("--db", default=None, help="Path to SQLite database")
    parser_report.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()

