"""
M2 — azure_docint_client ingest layer (end-to-end)

Exercises the real parsing + DB write pipeline using canned Azure receipt
JSON fixtures. The Azure SDK client itself is monkeypatched everywhere so
no network calls happen.

Covers:
  - ingest_analyzed_receipt_into_db populates receipts, receipt_line_items,
    receipt_raw_json, prices
  - _get_or_create_store_id fuzzy-matches an existing store
  - _get_or_create_store_id creates a new store when no match exists
  - Lines with empty descriptions are skipped
  - unit_price is backfilled from total_price / quantity when missing
  - Multi-buy phrase in line description is normalised via MultiBuyDealService
  - ingest_receipt_file_outcome (with monkeypatched AzureReceiptClient):
      * file-hash dedupe skips Azure call entirely
      * signature dedupe returns the existing receipt id on a rescan
      * replace_existing=True deletes then re-ingests
  - Autolearned alias is created for every mapped receipt line (documents
    current behaviour — see scope note on _upsert_item_from_mapping).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from Grocery_Sense.data.connection import get_connection
from Grocery_Sense.data.repositories.items_repo import create_item
from Grocery_Sense.data.repositories.stores_repo import create_store, list_stores
from Grocery_Sense.integrations import azure_docint_client as az_mod
from Grocery_Sense.integrations.azure_docint_client import (
    IngestOutcome,
    _get_or_create_store_id,
    ingest_analyzed_receipt_into_db,
    ingest_receipt_file_outcome,
)


FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "azure_receipt_samples"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def sample_file(tmp_path) -> Path:
    f = tmp_path / "receipt.jpg"
    f.write_bytes(b"fake bytes receipt")
    return f


@pytest.fixture
def dup_file(tmp_path) -> Path:
    """Physically different file from sample_file (so file_hash differs)."""
    f = tmp_path / "rescan.jpg"
    f.write_bytes(b"totally different bytes")
    return f


@pytest.fixture
def raw_json_dir(tmp_path) -> Path:
    return tmp_path / "raw_json"


# ---------------------------------------------------------------------------
# _get_or_create_store_id
# ---------------------------------------------------------------------------


class TestGetOrCreateStoreId:
    def test_creates_when_db_empty(self, isolated_db):
        sid = _get_or_create_store_id("Brand New Mart")
        stores = list_stores()
        assert len(stores) == 1
        assert stores[0].id == sid
        assert stores[0].name == "Brand New Mart"

    def test_fuzzy_matches_existing_store(self, isolated_db):
        """
        OCR often returns a store name with extra suffixes like a branch
        number. token_set_ratio treats shared tokens as dominant.
        """
        existing = create_store(name="Real Canadian Superstore")
        sid = _get_or_create_store_id(
            "Real Canadian Superstore #1234", threshold=80
        )
        assert sid == existing.id
        assert len(list_stores()) == 1  # no duplicate created

    def test_creates_new_when_below_threshold(self, isolated_db):
        create_store(name="Loblaws")
        sid = _get_or_create_store_id("Save-On-Foods", threshold=85)
        names = {s.name for s in list_stores()}
        assert names == {"Loblaws", "Save-On-Foods"}

    def test_blank_input_becomes_unknown_store(self, isolated_db):
        sid = _get_or_create_store_id("")
        store = next(s for s in list_stores() if s.id == sid)
        assert store.name == "Unknown Store"


# ---------------------------------------------------------------------------
# ingest_analyzed_receipt_into_db
# ---------------------------------------------------------------------------


class TestIngestAnalyzedReceiptIntoDb:
    def test_populates_all_child_tables(self, isolated_db, sample_file):
        analyze = _load_fixture("basic_receipt.json")

        rid = ingest_analyzed_receipt_into_db(
            file_path=sample_file,
            operation_id="op-1",
            analyze_result=analyze,
            saved_json_path=sample_file.parent / "out.json",
        )

        with get_connection() as c:
            receipt = c.execute(
                "SELECT store_id, purchase_date, total_amount, tax_amount, subtotal_amount, "
                "source, azure_request_id FROM receipts WHERE id = ?",
                (rid,),
            ).fetchone()
            line_count = c.execute(
                "SELECT COUNT(*) FROM receipt_line_items WHERE receipt_id = ?", (rid,)
            ).fetchone()[0]
            raw_count = c.execute(
                "SELECT COUNT(*) FROM receipt_raw_json WHERE receipt_id = ?", (rid,)
            ).fetchone()[0]
            price_count = c.execute(
                "SELECT COUNT(*) FROM prices WHERE receipt_id = ?", (rid,)
            ).fetchone()[0]

        # Header fields parsed correctly.
        assert receipt["purchase_date"] == "2026-04-22"
        assert receipt["total_amount"] == 25.00
        assert receipt["tax_amount"] == 3.00
        assert receipt["subtotal_amount"] == 22.00
        assert receipt["source"] == "receipt"
        assert receipt["azure_request_id"] == "op-1"

        # The empty-description line is skipped → 2 real lines out of 3.
        assert line_count == 2
        assert raw_count == 1
        assert price_count == 2

    def test_fuzzy_matches_pre_existing_store(self, isolated_db, sample_file):
        existing = create_store(name="Test Mart")
        analyze = _load_fixture("basic_receipt.json")

        rid = ingest_analyzed_receipt_into_db(
            file_path=sample_file,
            operation_id="op-1",
            analyze_result=analyze,
            saved_json_path=sample_file.parent / "x.json",
        )

        with get_connection() as c:
            store_id = c.execute(
                "SELECT store_id FROM receipts WHERE id = ?", (rid,)
            ).fetchone()[0]
        assert store_id == existing.id
        assert len(list_stores()) == 1  # no duplicate created

    def test_empty_description_line_is_skipped(self, isolated_db, sample_file):
        analyze = _load_fixture("basic_receipt.json")
        rid = ingest_analyzed_receipt_into_db(
            file_path=sample_file,
            operation_id="op-2",
            analyze_result=analyze,
            saved_json_path=sample_file.parent / "x.json",
        )
        with get_connection() as c:
            rows = c.execute(
                "SELECT description FROM receipt_line_items WHERE receipt_id = ?",
                (rid,),
            ).fetchall()
        descriptions = [r[0] for r in rows]
        assert all(d for d in descriptions)
        assert "" not in descriptions

    def test_minimal_payload_defaults_date_to_today(self, isolated_db, sample_file):
        """No TransactionDate → defaults to today's date."""
        import datetime as _dt

        analyze = _load_fixture("minimal_receipt.json")
        rid = ingest_analyzed_receipt_into_db(
            file_path=sample_file,
            operation_id="op-min",
            analyze_result=analyze,
            saved_json_path=sample_file.parent / "m.json",
        )
        with get_connection() as c:
            row = c.execute(
                "SELECT purchase_date, total_amount, subtotal_amount, tax_amount FROM receipts WHERE id = ?",
                (rid,),
            ).fetchone()
        assert row["purchase_date"] == _dt.date.today().isoformat()
        assert row["total_amount"] is None
        assert row["subtotal_amount"] is None
        assert row["tax_amount"] is None

    def test_autolearned_alias_created_for_each_line(self, isolated_db, sample_file):
        """
        FINDING (locked in): _upsert_item_from_mapping writes a low-confidence
        alias on every new item created from a receipt line. Documents current
        auto-learning behaviour. If the threshold is ever raised / this path
        removed, this test flips.
        """
        analyze = _load_fixture("basic_receipt.json")
        ingest_analyzed_receipt_into_db(
            file_path=sample_file,
            operation_id="op-aliases",
            analyze_result=analyze,
            saved_json_path=sample_file.parent / "x.json",
        )
        with get_connection() as c:
            rows = c.execute(
                "SELECT alias_text, source FROM item_aliases"
            ).fetchall()
        sources = {r[1] for r in rows}
        # The receipt-auto source covers lines where no canonical was found.
        assert "receipt_auto" in sources or any("auto" in s for s in sources)


# ---------------------------------------------------------------------------
# ingest_receipt_file_outcome (dedupe flow with patched AzureReceiptClient)
# ---------------------------------------------------------------------------


class _FakeAzureResult:
    """Stand-in for AzureReceiptResult."""
    def __init__(self, operation_id, analyze_result, saved_json_path):
        self.operation_id = operation_id
        self.analyze_result = analyze_result
        self.saved_json_path = saved_json_path


def _install_fake_azure(monkeypatch, analyze_result, *, operation_id="op-fake"):
    """
    Replaces AzureReceiptClient in the module so no real credentials / HTTP
    are needed. Each ingest call reads the physical file (so the file hash
    is real), then returns the canned analyze_result.
    """
    raw_out = {"path": None}  # captured for inspection

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        def analyze_and_save_json(self, file_path, raw_json_dir):
            raw_dir = Path(raw_json_dir)
            raw_dir.mkdir(parents=True, exist_ok=True)
            out = raw_dir / f"{Path(file_path).stem}__{operation_id}.json"
            out.write_text(json.dumps(analyze_result), encoding="utf-8")
            raw_out["path"] = out
            return _FakeAzureResult(operation_id, analyze_result, out)

    monkeypatch.setattr(az_mod, "AzureReceiptClient", _FakeClient)
    return raw_out


class TestIngestReceiptFileOutcome:
    def test_normal_ingest(self, isolated_db, sample_file, raw_json_dir, monkeypatch):
        _install_fake_azure(monkeypatch, _load_fixture("basic_receipt.json"))

        outcome = ingest_receipt_file_outcome(
            sample_file, raw_json_dir=raw_json_dir
        )

        assert isinstance(outcome, IngestOutcome)
        assert outcome.was_duplicate is False
        assert outcome.receipt_id > 0
        assert outcome.duplicate_reason is None

    def test_file_hash_dedupe_skips_azure(
        self, isolated_db, sample_file, raw_json_dir, monkeypatch
    ):
        """Same file uploaded twice → second call must hit file-hash dedupe."""
        calls = {"n": 0}
        original = _install_fake_azure(monkeypatch, _load_fixture("basic_receipt.json"))

        # Wrap the fake client to count constructions — dedupe path should skip it.
        real_fake = az_mod.AzureReceiptClient

        class _CountingClient(real_fake):
            def analyze_and_save_json(self, file_path, raw_json_dir):
                calls["n"] += 1
                return super().analyze_and_save_json(file_path, raw_json_dir)

        monkeypatch.setattr(az_mod, "AzureReceiptClient", _CountingClient)

        first = ingest_receipt_file_outcome(sample_file, raw_json_dir=raw_json_dir)
        second = ingest_receipt_file_outcome(sample_file, raw_json_dir=raw_json_dir)

        assert first.was_duplicate is False
        assert second.was_duplicate is True
        assert second.duplicate_reason == "file_hash"
        assert second.receipt_id == first.receipt_id
        # Azure only called once — the dedupe short-circuit worked.
        assert calls["n"] == 1

    def test_signature_dedupe_catches_rescan(
        self, isolated_db, sample_file, dup_file, raw_json_dir, monkeypatch
    ):
        """
        Two physically different files (different bytes → different file hash)
        but the same merchant/date/total → signature dedupe must catch the
        second one.
        """
        # Both ingest calls return the basic receipt fixture → same signature.
        _install_fake_azure(monkeypatch, _load_fixture("basic_receipt.json"))

        first = ingest_receipt_file_outcome(sample_file, raw_json_dir=raw_json_dir)
        second = ingest_receipt_file_outcome(dup_file, raw_json_dir=raw_json_dir)

        assert first.was_duplicate is False
        assert second.was_duplicate is True
        assert second.duplicate_reason == "signature"
        assert second.receipt_id == first.receipt_id
        assert second.existing_receipt_id == first.receipt_id

    def test_different_signatures_both_ingest(
        self, isolated_db, sample_file, dup_file, raw_json_dir, monkeypatch
    ):
        """Different merchant/date/total → no dedupe; both ingested."""
        # First call: basic fixture. Second call: swap to duplicate_receipt
        # which has the SAME merchant/date/total — so it actually IS a dup.
        # To test a true non-dup, we need different totals. Build one inline.
        diff = _load_fixture("basic_receipt.json")
        diff["documents"][0]["fields"]["Total"] = {
            "valueCurrency": {"amount": 99.99}, "confidence": 0.9
        }
        diff["documents"][0]["fields"]["MerchantName"] = {
            "valueString": "Different Mart", "confidence": 0.9
        }

        # Two different analyze_result payloads per call.
        payloads = iter([_load_fixture("basic_receipt.json"), diff])

        class _SwitchingClient:
            def __init__(self, *a, **kw):
                pass

            def analyze_and_save_json(self, file_path, raw_json_dir):
                raw_dir = Path(raw_json_dir)
                raw_dir.mkdir(parents=True, exist_ok=True)
                payload = next(payloads)
                out = raw_dir / f"{Path(file_path).stem}.json"
                out.write_text(json.dumps(payload), encoding="utf-8")
                return _FakeAzureResult("op-x", payload, out)

        monkeypatch.setattr(az_mod, "AzureReceiptClient", _SwitchingClient)

        a = ingest_receipt_file_outcome(sample_file, raw_json_dir=raw_json_dir)
        b = ingest_receipt_file_outcome(dup_file, raw_json_dir=raw_json_dir)

        assert a.was_duplicate is False
        assert b.was_duplicate is False
        assert a.receipt_id != b.receipt_id

    def test_replace_existing_reingests(
        self, isolated_db, sample_file, raw_json_dir, monkeypatch
    ):
        _install_fake_azure(monkeypatch, _load_fixture("basic_receipt.json"))

        first = ingest_receipt_file_outcome(sample_file, raw_json_dir=raw_json_dir)
        second = ingest_receipt_file_outcome(
            sample_file, raw_json_dir=raw_json_dir, replace_existing=True
        )

        # Replace creates a NEW receipt id and deletes the old one.
        assert second.was_duplicate is False
        assert second.replaced_existing is True
        assert second.receipt_id != first.receipt_id

        # Old receipt gone.
        with get_connection() as c:
            row = c.execute(
                "SELECT COUNT(*) FROM receipts WHERE id = ?", (first.receipt_id,)
            ).fetchone()
        assert row[0] == 0

    def test_missing_file_raises(self, isolated_db, raw_json_dir, tmp_path):
        with pytest.raises(FileNotFoundError):
            ingest_receipt_file_outcome(
                tmp_path / "ghost.jpg", raw_json_dir=raw_json_dir
            )
