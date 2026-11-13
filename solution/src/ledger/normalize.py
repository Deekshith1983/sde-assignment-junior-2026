from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from ledger.config import IST, UTC


def norm_header(h: str) -> str:
    """Normalises a header: lower-case, trim, replace runs of _ or whitespace with single space."""
    return re.sub(r"[_\s]+", " ", (h or "").strip().lower())


def parse_phone(raw: str | None) -> str | None:
    """Parses Indian mobile number to canonical +91XXXXXXXXXX or None if invalid.
    
    Strip every non-digit. If 12 digits starting with 91, drop 91.
    If 11 digits starting with 0, drop 0.
    Valid only if exactly 10 digits starting with 6, 7, 8, or 9.
    """
    if not raw:
        return None
    d = re.sub(r"\D", "", raw)
    if len(d) == 12 and d.startswith("91"):
        d = d[2:]
    elif len(d) == 11 and d.startswith("0"):
        d = d[1:]
    if len(d) == 10 and d[0] in "6789":
        return "+91" + d
    return None


def parse_date(raw: str | None) -> str | None:
    """Parses date to YYYY-MM-DD string or None if invalid.
    
    Accepts YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY, DD-Mon-YYYY (case-insensitive).
    Dates are day-first. Impossible dates return None.
    """
    if not raw:
        return None
    s = raw.strip()
    if not s:
        return None

    # Check DD-Mon-YYYY (e.g. 01-Dec-2026, 21-oct-2026)
    formats = [
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d-%b-%Y",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def parse_decimal(raw: str | None) -> float | None:
    """Parses currency/decimal string to float rounded to 2 decimal places or None.
    
    Optionally prefixed by ₹, Rs, Rs. or INR (case-insensitive, optional space).
    Commas are thousands separators. Must be >= 0.
    """
    if not raw:
        return None
    s = raw.strip()
    if not s:
        return None

    # Strip currency prefix
    s = re.sub(r"^(₹|rs\.?|inr)\s*", "", s, flags=re.IGNORECASE)
    # Strip thousands separators
    s = s.replace(",", "").strip()

    try:
        val = Decimal(s)
        if val < 0:
            return None
        rounded = val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return float(rounded)
    except (InvalidOperation, ValueError):
        return None


def parse_enum(raw: str | None, values: list[str]) -> str | None:
    """Compared case-insensitively against values and stored lower-case. Returns None if invalid."""
    if not raw:
        return None
    s = raw.strip().lower()
    allowed = {v.lower() for v in values}
    if s in allowed:
        return s
    return None


def parse_string(raw: str | None) -> str | None:
    """Returns trimmed string or None if empty."""
    if raw is None:
        return None
    s = raw.strip()
    return s if s else None


def parse_event_ts(raw: str) -> tuple[datetime, str, str, str]:
    """Parses event occurred_at string in UTC, offset, or naive format (naive = IST).
    
    Returns:
        (utc_dt, occurred_at_utc, occurred_at_ist, date_ist)
        - utc_dt: aware datetime in UTC
        - occurred_at_utc: ISO string ending with 'Z'
        - occurred_at_ist: ISO string with offset '+05:30'
        - date_ist: YYYY-MM-DD calendar date in IST
    """
    s = raw.strip()
    if s.endswith("Z"):
        dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    elif "T" in s and ("+" in s or "-" in s[-6:]):
        dt = datetime.fromisoformat(s)
    else:
        # Naive format (e.g. "2026-10-03 10:01:00") -> naive means IST
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)

    utc_dt = dt.astimezone(UTC)
    ist_dt = dt.astimezone(IST)

    occurred_at_utc = utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    occurred_at_ist = ist_dt.strftime("%Y-%m-%dT%H:%M:%S+05:30")
    date_ist = ist_dt.strftime("%Y-%m-%d")

    return utc_dt, occurred_at_utc, occurred_at_ist, date_ist
