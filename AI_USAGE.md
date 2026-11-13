# AI Usage Disclosure

## Tools used
Antigravity IDE (Claude Sonnet 4.6, Gemini 3.8 Flash).

## What I used them for
- Scaffolding the database DDL tables, indexes, and triggers in `solution/src/ledger/db.py`.
- Drafting pure functional normalizers in `solution/src/ledger/normalize.py` and expanding edge-case fixtures across phone formats, date representations, and currency strings.
- Drafting initial SQL Common Table Expression (CTE) queries for effective disposition ranking and lead bucketing.
- Generating boilerplates for unit, rule, and integration pytest files.

## What I wrote or decided myself
- **Data Modeling & Architecture:** Decided to resolve event-to-lead linking dynamically at report generation time rather than storing a mutable `lead_id` at ingest. This was critical to handle out-of-order deliveries where telephony events in `drop_1` precede their lead upload in `drop_2`.
- **PII Security Threat Modeling:** Identified that unsalted SHA-256 is fundamentally broken for 10-digit Indian mobile numbers (`[6-9]\d{9}` has only 4 billion combinations, reversible in <0.5s on GPU). Specified the HMAC-SHA256 blind index scheme (`phone_token`) with a secret pepper and chose zero-retention for `customer_name` (since no report requires names).
- **Ambiguity Resolutions:** Made key engineering calls including breaking timestamp ties on `event_id DESC`, enforcing the orphan check before the unknown disposition check, and keeping `batch_id` permanently bound to the first batch.
- **Transaction & Idempotency Strategy:** Formulated the per-file transaction boundary with SHA-256 hash tracking in `ingest_files` to ensure atomic rollbacks upon mid-file failure.

## Where the AI was wrong or unhelpful
- **Windows Timezone Support:** The AI initially used `zoneinfo.ZoneInfo("Asia/Kolkata")` in `config.py`. On standard Windows environments without the external `tzdata` package installed, this raised `ZoneInfoNotFoundError: 'No time zone found with key Asia/Kolkata'`. I identified the failure during test execution and refactored the timezone definition to `timezone(timedelta(hours=5, minutes=30))`, which is 100% portable across Windows, Linux, and macOS without extra dependencies.
- **SQL Floating-Point Precision:** An early draft of `daily_overview.csv` computed the connectivity rate string directly inside SQLite SQL, which caused intermittent floating-point rounding variations. I refactored the pipeline to fetch exact integer counts from SQL and format the decimal rate deterministically in Python via `f"{(connected / total):.4f}"`.

## How I verified AI-generated code
- **Automated Pytest Suite:** Wrote and executed 50 test cases covering unit normalizers, schema business rules, and integration flows (`pytest solution/tests`).
- **Multi-Seed Byte-Differential Testing:** Built an automated verification script that generated data across seeds 42, 101, and 202, ingested drops sequentially vs in batch, and diffed output files byte-for-byte to confirm 100% idempotency and batch equivalence.
- **Binary Security Audit:** Implemented an automated test that scanned the raw database bytes (`.db` file) to prove that not a single valid 10-digit phone number was persisted in plaintext.
