from __future__ import annotations

import os
import sys
from pathlib import Path
import pytest
import sqlite3

# Ensure solution/src is in sys.path
SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Ensure test PII HMAC key is present
TEST_KEY = "test_hmac_secret_key_0123456789abcdef0123456789abcdef"
os.environ.setdefault("PII_HMAC_KEY", TEST_KEY)

from ledger.config import get_schema_dir
from ledger.db import init_db


@pytest.fixture
def hmac_key() -> str:
    return os.environ.get("PII_HMAC_KEY", TEST_KEY)


@pytest.fixture
def schema_dir() -> Path:
    return get_schema_dir()


@pytest.fixture
def temp_db(tmp_path: Path, schema_dir: Path) -> sqlite3.Connection:
    db_file = tmp_path / "test_ledger.db"
    conn = init_db(db_file, schema_dir)
    yield conn
    conn.close()

