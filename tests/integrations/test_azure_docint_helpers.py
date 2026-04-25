"""
M2 — azure_docint_client pure helpers

Covers the stateless parsers (no Azure, no DB required):
  - _safe_float: tolerant numeric extraction
  - _confidence_to_1_5: bucketing
  - _currency_amount: dict-or-scalar normalization
  - _normalize_merchant_name: lowercase + punctuation scrub
  - _make_receipt_signature: None-safe and deterministic
  - _pick_field: case-insensitive field lookup + alias list
  - _field_value: key-priority order, content fallback
  - _compute_file_sha256: streaming hash stability
  - _extract_header_for_signature: all three pieces + missing-doc edge
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from Grocery_Sense.integrations.azure_docint_client import (
    _compute_file_sha256,
    _confidence_to_1_5,
    _currency_amount,
    _extract_header_for_signature,
    _field_value,
    _make_receipt_signature,
    _normalize_merchant_name,
    _pick_field,
    _safe_float,
)


# ---------------------------------------------------------------------------
# _safe_float
# ---------------------------------------------------------------------------


class TestSafeFloat:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            (1.5, 1.5),
            (2, 2.0),
            ("3.14", 3.14),
            ("$ 4.99", 4.99),
            ("1,234.56", 1234.56),
            ("  10.00  ", 10.0),
            ("-2.50", -2.5),
            ("abc", None),
            ("", None),
            (None, None),
        ],
    )
    def test_parses(self, raw, expected):
        assert _safe_float(raw) == expected


# ---------------------------------------------------------------------------
# _confidence_to_1_5
# ---------------------------------------------------------------------------


class TestConfidenceTo15:
    @pytest.mark.parametrize(
        "conf, expected",
        [
            (0.95, 5),
            (0.90, 5),
            (0.89, 4),
            (0.75, 4),
            (0.74, 3),
            (0.60, 3),
            (0.59, 2),
            (0.40, 2),
            (0.39, 1),
            (0.0, 1),
            (None, None),
            ("bad", None),
        ],
    )
    def test_bucketing(self, conf, expected):
        assert _confidence_to_1_5(conf) == expected


# ---------------------------------------------------------------------------
# _currency_amount
# ---------------------------------------------------------------------------


class TestCurrencyAmount:
    def test_dict_with_amount(self):
        assert _currency_amount({"amount": 4.99}) == 4.99

    def test_dict_without_amount_returns_none(self):
        assert _currency_amount({"currency": "USD"}) is None

    def test_scalar_falls_back_to_safe_float(self):
        assert _currency_amount("4.99") == 4.99
        assert _currency_amount(5) == 5.0

    def test_none(self):
        assert _currency_amount(None) is None


# ---------------------------------------------------------------------------
# _normalize_merchant_name
# ---------------------------------------------------------------------------


class TestNormalizeMerchantName:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("Real Canadian Superstore", "real canadian superstore"),
            ("  LOBLAWS   #1234  ", "loblaws 1234"),
            ("Save-On-Foods", "save-on-foods"),
            ("T&T Supermarket!", "tt supermarket"),
            ("", ""),
            (None, ""),
        ],
    )
    def test_normalize(self, raw, expected):
        assert _normalize_merchant_name(raw) == expected  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _make_receipt_signature
# ---------------------------------------------------------------------------


class TestMakeReceiptSignature:
    def test_deterministic(self):
        sig1 = _make_receipt_signature("Test Mart", "2026-04-22", 25.00)
        sig2 = _make_receipt_signature("Test Mart", "2026-04-22", 25.00)
        assert sig1 == sig2

    def test_normalizes_merchant_name(self):
        sig = _make_receipt_signature("TEST MART", "2026-04-22", 25.00)
        assert sig.startswith("test mart|")

    def test_rounds_total_to_cents(self):
        # 25.001 and 25.005 both round to 25.00/25.01 respectively via round-half-to-even.
        sig_a = _make_receipt_signature("x", "2026-04-22", 25.001)
        sig_b = _make_receipt_signature("x", "2026-04-22", 25.00)
        assert sig_a == sig_b  # both round to 25.00

    def test_returns_none_when_any_piece_missing(self):
        assert _make_receipt_signature("", "2026-04-22", 25.00) is None
        assert _make_receipt_signature("x", "", 25.00) is None
        assert _make_receipt_signature("x", "2026-04-22", None) is None

    def test_format_matches_pipe_delimited(self):
        sig = _make_receipt_signature("Test Mart", "2026-04-22", 25.00)
        assert sig == "test mart|2026-04-22|25.00"


# ---------------------------------------------------------------------------
# _pick_field
# ---------------------------------------------------------------------------


class TestPickField:
    def test_exact_match(self):
        fields = {"MerchantName": {"valueString": "X"}}
        assert _pick_field(fields, ["MerchantName"]) == {"valueString": "X"}

    def test_case_insensitive(self):
        fields = {"merchantname": {"valueString": "X"}}
        assert _pick_field(fields, ["MerchantName"]) == {"valueString": "X"}

    def test_alias_order_preserved(self):
        fields = {"Merchant": {"valueString": "A"}, "MerchantName": {"valueString": "B"}}
        # First alias in list that matches wins.
        assert _pick_field(fields, ["MerchantName", "Merchant"])["valueString"] == "B"
        assert _pick_field(fields, ["Merchant", "MerchantName"])["valueString"] == "A"

    def test_missing_returns_none(self):
        assert _pick_field({"a": {}}, ["b", "c"]) is None

    def test_empty_fields_returns_none(self):
        assert _pick_field({}, ["anything"]) is None

    def test_non_dict_field_value_returns_none(self):
        """If a field exists but isn't a dict, _pick_field must NOT return it."""
        assert _pick_field({"MerchantName": "raw string"}, ["MerchantName"]) is None


# ---------------------------------------------------------------------------
# _field_value
# ---------------------------------------------------------------------------


class TestFieldValue:
    def test_value_string_priority(self):
        field = {"valueString": "X", "valueNumber": 1, "confidence": 0.9}
        assert _field_value(field) == ("X", 0.9)

    def test_value_number_when_no_string(self):
        field = {"valueNumber": 3.14, "confidence": 0.8}
        assert _field_value(field) == (3.14, 0.8)

    def test_value_currency(self):
        field = {"valueCurrency": {"amount": 4.99}, "confidence": 0.85}
        val, conf = _field_value(field)
        assert val == {"amount": 4.99}
        assert conf == 0.85

    def test_content_fallback(self):
        field = {"content": "raw", "confidence": 0.7}
        assert _field_value(field) == ("raw", 0.7)

    def test_none_field(self):
        assert _field_value(None) == (None, None)

    def test_empty_field(self):
        assert _field_value({}) == (None, None)


# ---------------------------------------------------------------------------
# _compute_file_sha256
# ---------------------------------------------------------------------------


class TestComputeFileSha256:
    def test_matches_hashlib(self, tmp_path):
        f = tmp_path / "x.bin"
        data = b"hello world\n" * 100
        f.write_bytes(data)

        expected = hashlib.sha256(data).hexdigest()
        assert _compute_file_sha256(f) == expected

    def test_streaming_handles_large_files(self, tmp_path):
        f = tmp_path / "big.bin"
        data = b"A" * (3 * 1024 * 1024 + 7)  # span multiple chunks
        f.write_bytes(data)

        assert _compute_file_sha256(f, chunk_size=1024 * 1024) == hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# _extract_header_for_signature
# ---------------------------------------------------------------------------


class TestExtractHeaderForSignature:
    def test_pulls_three_pieces(self):
        result = {
            "documents": [
                {
                    "fields": {
                        "MerchantName": {"valueString": "Test Mart"},
                        "TransactionDate": {"valueString": "2026-04-22"},
                        "Total": {"valueCurrency": {"amount": 25.00}},
                    }
                }
            ]
        }
        merchant, date, total = _extract_header_for_signature(result)
        assert merchant == "Test Mart"
        assert date == "2026-04-22"
        assert total == 25.00

    def test_missing_document_returns_blanks(self):
        merchant, date, total = _extract_header_for_signature({})
        assert merchant == ""
        assert date == ""
        assert total is None

    def test_malformed_date_left_blank(self):
        """A non-ISO date string is dropped — signatures must not include junk."""
        result = {
            "documents": [
                {
                    "fields": {
                        "MerchantName": {"valueString": "x"},
                        "TransactionDate": {"valueString": "04/22/2026"},  # not ISO
                        "Total": {"valueCurrency": {"amount": 1.0}},
                    }
                }
            ]
        }
        _, date, _ = _extract_header_for_signature(result)
        assert date == ""
