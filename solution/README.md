# Solution: Interaction Ledger & Reporting Pipeline (Assignment A)

A high-performance, deterministic data ingestion and reporting pipeline built for Agentic Universe using Python 3.11+ standard library and SQLite.

---

## 1. Quick Start & Execution

### 1.1 Prerequisites & Virtual Environment
From the repository root:

```bash
# 1. Create and activate virtual environment
python -m venv .venv
# On Linux/macOS:
source .venv/bin/activate
# On Windows (PowerShell):
.\.venv\Scripts\Activate.ps1

# 2. Install dependencies (standard requirements including pytest)
pip install -r requirements.txt

# 3. Configure the mandatory PII secret pepper key
export PII_HMAC_KEY="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
# On Windows (PowerShell):
$env:PII_HMAC_KEY="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
```

### 1.2 Ingesting Drops & Generating Reports
Execute commands using Python module execution (no editable installation required):

```bash
# Ingest drop 1
python -m solution.src.ledger.cli ingest data/drop_1

# Ingest drop 2
python -m solution.src.ledger.cli ingest data/drop_2

# Generate all 5 client reports
python -m solution.src.ledger.cli report --out reports/
```

### 1.3 Running Tests
Run all 50 unit, rule, and integration tests with a single command:

```bash
pytest solution/tests
```

---

## 2. Directory Structure

```
sde-assignment-junior-2026/
├── assignment-a-data/          
│   ├── schemas/
│   │   ├── dispositions.csv
│   │   ├── motor_renewal.json
│   │   └── policy_feedback.json
│   └── tools/
│       └── generate_data.py
├── solution/
│   ├── src/
│   │   └── ledger/
│   │       ├── __init__.py
│   │       ├── __main__.py     # Module execution entrypoint
│   │       ├── cli.py          # CLI subcommands: ingest, report
│   │       ├── config.py       # Configuration, paths, IST/UTC timezones, PII key
│   │       ├── db.py           # SQLite connection, DDL, WAL pragma, triggers
│   │       ├── schema_loader.py# Dynamic schema and disposition catalogue loader
│   │       ├── normalize.py    # Pure functional parsers (phone, date, decimal, enum, ts)
│   │       ├── pii.py          # HMAC-SHA256 blind indexing and last-4 masking
│   │       ├── leads.py        # Lead CSV validator, rejection sink, upsert merger
│   │       ├── events.py       # Append-only JSONL event parser and deduplicator
│   │       └── reports.py      # SQL aggregation CTEs producing the 5 target reports
│   └── tests/
│       ├── conftest.py         # Pytest fixtures and test environment setup
│       ├── test_normalize.py   # Unit tests for pure normalizers (37 tests)
│       ├── test_rules.py       # Rule tests for rejections, merges, buckets, eff_disp (9 tests)
│       └── test_integration.py # Multi-seed idempotency and batch equivalence tests (4 tests)
├── .env.example                # Example environment key setup
├── SUBMISSION.md               # Completed submission document
└── AI_USAGE.md                 # Completed AI usage disclosure
```

---

## 3. Extensibility & Maintenance Guide

| Requirement to Change | Target File | What to Touch |
|---|---|---|
| **Add or Modify a Bucket** | `solution/src/ledger/reports.py` | Update the `CASE` statement in `v_lead_metrics` (lines 140–147). |
| **Change Effective Disposition Rules** | `solution/src/ledger/reports.py` | Modify window ranking CTEs in `v_call_effective_disposition` (lines 53–75). |
| **Add a New Event Type** (e.g. `call.voicemail`) | `solution/src/ledger/reports.py` | Add boolean flag subquery in `v_counted_calls` or filter in `v_linked_events`. |
| **Add/Modify Header Alias or Field Rule** | `solution/src/ledger/normalize.py` | Update regexes or parser functions (`parse_phone`, `parse_decimal`, etc.). |
| **Change PII Masking Scheme** | `solution/src/ledger/pii.py` | Update `mask_phone` or `mask_phone_from_last4`. |
| **Add Lead Validation Precedence** | `solution/src/ledger/leads.py` | Reorder checks inside the schema field loop (lines 135–185). |

