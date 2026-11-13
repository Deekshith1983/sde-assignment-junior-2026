from __future__ import annotations

import pytest
from datetime import datetime, timezone

from ledger.config import IST, UTC
from ledger.normalize import (
    norm_header,
    parse_phone,
    parse_date,
    parse_decimal,
    parse_enum,
    parse_string,
    parse_event_ts,
)


class TestPhoneNormalizer:
    def test_phone_plain_10(self):
        assert parse_phone("9876543210") == "+919876543210"

    def test_phone_11_zero(self):
        assert parse_phone("09876543210") == "+919876543210"

    def test_phone_12_91(self):
        assert parse_phone("919876543210") == "+919876543210"

    def test_phone_spaced(self):
        assert parse_phone("+91 98765 43210") == "+919876543210"

    def test_phone_hyphen(self):
        assert parse_phone("91-98765-43210") == "+919876543210"

    def test_phone_paren(self):
        assert parse_phone("(+91) 9876543210") == "+919876543210"

    def test_phone_prefixes_6789(self):
        assert parse_phone("6123456789") == "+916123456789"
        assert parse_phone("7123456789") == "+917123456789"
        assert parse_phone("8123456789") == "+918123456789"
        assert parse_phone("9123456789") == "+919123456789"

    def test_phone_bad_prefix(self):
        assert parse_phone("5876543210") is None
        assert parse_phone("1876543210") is None

    def test_phone_landline(self):
        assert parse_phone("022-2345-6789") is None

    def test_phone_9digits(self):
        assert parse_phone("987654321") is None

    def test_phone_us(self):
        assert parse_phone("+1 415 555 0134") is None

    def test_phone_letters_or_empty(self):
        assert parse_phone("N/A") is None
        assert parse_phone("") is None
        assert parse_phone(None) is None


class TestDateNormalizer:
    def test_date_iso(self):
        assert parse_date("2026-11-26") == "2026-11-26"

    def test_date_dd_mm_yyyy_slash(self):
        # Day-first
        assert parse_date("04/12/2026") == "2026-12-04"

    def test_date_dd_mm_yyyy_dash(self):
        assert parse_date("08-10-2026") == "2026-10-08"

    def test_date_dd_mon_yyyy(self):
        assert parse_date("21-Oct-2026") == "2026-10-21"

    def test_date_mon_case_insensitive(self):
        assert parse_date("01-DEC-2026") == "2026-12-01"
        assert parse_date("15-sep-2026") == "2026-09-15"

    def test_date_impossible(self):
        assert parse_date("31/02/2026") is None
        assert parse_date("31-02-2026") is None

    def test_date_invalid_month(self):
        assert parse_date("2026-13-01") is None

    def test_date_bad_strings(self):
        assert parse_date("soon") is None
        assert parse_date("TBD") is None
        assert parse_date("") is None
        assert parse_date(None) is None


class TestDecimalNormalizer:
    def test_decimal_plain(self):
        assert parse_decimal("9594.13") == 9594.13
        assert parse_decimal("20641") == 20641.0

    def test_decimal_rupee_symbol(self):
        assert parse_decimal("₹10,376") == 10376.0
        assert parse_decimal("₹ 10,376.50") == 10376.50

    def test_decimal_rs_dot(self):
        assert parse_decimal("Rs. 27158") == 27158.0
        assert parse_decimal("rs 27158.45") == 27158.45

    def test_decimal_inr(self):
        assert parse_decimal("INR 35320.6") == 35320.60
        assert parse_decimal("inr 100") == 100.0

    def test_decimal_negative(self):
        assert parse_decimal("-100") is None
        assert parse_decimal("₹-50") is None

    def test_decimal_rounding(self):
        assert parse_decimal("1234.567") == 1234.57
        assert parse_decimal("1234.564") == 1234.56

    def test_decimal_bad_strings(self):
        assert parse_decimal("twelve thousand") is None
        assert parse_decimal("N/A") is None
        assert parse_decimal("-") is None
        assert parse_decimal("") is None
        assert parse_decimal(None) is None


class TestEnumNormalizer:
    def test_enum_exact(self):
        assert parse_enum("en", ["en", "hi", "ta"]) == "en"

    def test_enum_upper(self):
        assert parse_enum("EN", ["en", "hi", "ta"]) == "en"
        assert parse_enum("KN", ["en", "kn"]) == "kn"

    def test_enum_padded(self):
        assert parse_enum(" mr ", ["en", "mr"]) == "mr"

    def test_enum_invalid(self):
        assert parse_enum("Hindi", ["en", "hi", "ta"]) is None
        assert parse_enum("english", ["en", "hi"]) is None
        assert parse_enum("", ["en", "hi"]) is None
        assert parse_enum(None, ["en", "hi"]) is None


class TestHeaderNormalizer:
    def test_header_norm_spaces_underscores(self):
        assert norm_header("Insured Name") == "insured name"
        assert norm_header("policy_number") == "policy number"
        assert norm_header("  Mobile__No   ") == "mobile no"
        assert norm_header("preferred_language") == "preferred language"


class TestTimestampNormalizer:
    def test_ts_utc(self):
        dt, utc_s, ist_s, d_ist = parse_event_ts("2026-10-05T13:20:21Z")
        assert utc_s == "2026-10-05T13:20:21Z"
        assert ist_s == "2026-10-05T18:50:21+05:30"
        assert d_ist == "2026-10-05"

    def test_ts_offset(self):
        dt, utc_s, ist_s, d_ist = parse_event_ts("2026-10-05T18:50:21+05:30")
        assert utc_s == "2026-10-05T13:20:21Z"
        assert ist_s == "2026-10-05T18:50:21+05:30"
        assert d_ist == "2026-10-05"

    def test_ts_naive_means_ist(self):
        dt, utc_s, ist_s, d_ist = parse_event_ts("2026-10-05 18:50:21")
        assert utc_s == "2026-10-05T13:20:21Z"
        assert ist_s == "2026-10-05T18:50:21+05:30"
        assert d_ist == "2026-10-05"

    def test_ts_midnight_boundary_utc_to_ist(self):
        # 19:30 UTC on Oct 5 is 01:00 IST on Oct 6
        dt, utc_s, ist_s, d_ist = parse_event_ts("2026-10-05T19:30:00Z")
        assert d_ist == "2026-10-06"
        assert ist_s == "2026-10-06T01:00:00+05:30"

    def test_ts_midnight_boundary_naive_ist(self):
        dt, utc_s, ist_s, d_ist = parse_event_ts("2026-10-05 00:00:00")
        assert d_ist == "2026-10-05"
        assert ist_s == "2026-10-05T00:00:00+05:30"
