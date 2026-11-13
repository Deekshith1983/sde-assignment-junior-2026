from __future__ import annotations

import os
import sys
from datetime import timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc

def get_repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "assignment-a-data").exists():
            return parent
    return current.parents[3]

def get_default_db_path() -> Path:
    repo_root = get_repo_root()
    return repo_root / "solution" / "ledger.db"

def get_schema_dir() -> Path:
    repo_root = get_repo_root()
    return repo_root / "assignment-a-data" / "schemas"

def get_pii_hmac_key() -> str:
    key = os.environ.get("PII_HMAC_KEY", "").strip()
    if not key:
        print(
            "ERROR: Missing mandatory environment variable 'PII_HMAC_KEY'.\n"
            "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\"\n"
            "and export it (e.g. export PII_HMAC_KEY=<key> or set PII_HMAC_KEY=<key>).",
            file=sys.stderr,
        )
        sys.exit(1)
    return key
