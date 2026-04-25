"""
M4 — FlyerIngestService

Covers:
  - _safe_float_money / _extract_price_text pure helpers
  - _extract_deals_from_layout against canned Azure Document Intelligence JSON
  - ingest_dealrecords_json end-to-end persistence into flyer_batches + flyer_deals
  - Failure Class #1 guards: OCR-mangled price text must not produce fake numbers
  - Cross-cutting: lines without price anchors filtered out;
    inverted flyer dates are currently accepted (documented finding).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from Grocery_Sense.data import connection as _conn
from Grocery_Sense.data.repositories.flyers_repo import FlyersRepo
from Grocery_Sense.data.repositories.stores_repo import create_store
from Grocery_Sense.services.flyer_ingest_service import (
    FlyerIngestResult,
    FlyerIngestService,
    _safe_float_money,
)


FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "flyer_samples"


@pytest.fixture
def azure_creds(monkeypatch):
    """Dummy creds so FlyerDocIntClient constructs; no network calls are made."""
    monkeypatch.setenv("DOCUMENTINTELLIGENCE_ENDPOINT", "https://fake.example/")
    monkeypatch.setenv("DOCUMENTINTELLIGENCE_API_KEY", "fake-key")


@pytest.fixture
def ingest_svc(azure_creds, isolated_db) -> FlyerIngestService:
    return FlyerIngestService()


# ---------------------------------------------------------------------------
# _safe_float_money — tolerates dirty OCR prefixes but rejects non-numeric
# ---------------------------------------------------------------------------


class TestSafeFloatMoney:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("$2.99", 2.99),
            ("2.99", 2.99),
            (" $ 10.00 ", 10.00),
            ("4.99/kg", 4.99),
            ("$0.99", 0.99),
            ("  $12.50  ", 12.50),
            ("0.50", 0.50),
        ],
    )
    def test_clean_inputs_parse(self, raw, expected):
        assert _safe_float_money(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            None,
            "free",
            "--",
            "O.99",                # OCR letter-O prefix — rejected
            "1,25",                # EU decimal comma — rejected
            "Was $4.99 Now $2.99", # multi-amount — ambiguous, rejected
            "price: $3.49",        # prose prefix — no longer silently extracts
            "-5.00",               # negative sign (prices are non-negative)
            "$",                   # lone dollar sign
            "$ .99",               # missing integer part
        ],
    )
    def test_dirty_or_ambiguous_inputs_return_none(self, raw):
        """
        Post-fix: _safe_float_money rejects strings that aren't unambiguous
        money representations. OCR letter-O, EU decimals, and multi-amount
        strings no longer silently coerce to a wrong number.
        """
        assert _safe_float_money(raw) is None


# ---------------------------------------------------------------------------
# _extract_price_text — pattern detection across common flyer forms
# ---------------------------------------------------------------------------


class TestExtractPriceText:
    def test_dollar_amount(self, ingest_svc):
        assert ingest_svc._extract_price_text("Chicken Thighs $5.99") == "$5.99"

    def test_slash_pattern(self, ingest_svc):
        assert ingest_svc._extract_price_text("Apples 2/$5") == "2/$5"

    def test_for_pattern(self, ingest_svc):
        assert ingest_svc._extract_price_text("Pork 3 for 10") == "3 for 10"

    def test_at_pattern(self, ingest_svc):
        assert ingest_svc._extract_price_text("Yogurt 2 @ 4.00") == "2 @ 4.00"

    def test_fallback_plain_decimal(self, ingest_svc):
        assert ingest_svc._extract_price_text("Milk 3.99") == "3.99"

    @pytest.mark.parametrize(
        "text",
        [
            "Header row",
            "No price here",
            "",
            "   ",
            "Sale!",
            "$O.99",  # OCR letter-O — not a valid decimal pattern
        ],
    )
    def test_no_price_returns_none(self, ingest_svc, text):
        assert ingest_svc._extract_price_text(text) is None


# ---------------------------------------------------------------------------
# _extract_deals_from_layout — golden JSON → extracted deal candidates
# ---------------------------------------------------------------------------


class TestExtractDealsFromLayout:
    def test_extracts_from_canned_azure_json(self, ingest_svc):
        payload = json.loads((FIXTURES / "azure_layout_simple.json").read_text())
        deals = ingest_svc._extract_deals_from_layout(payload)

        # The header row "Header row — no price here" must be skipped entirely.
        titles = [d["title"] for d in deals]
        assert "Header row — no price here" not in titles

        price_texts = [d["price_text"] for d in deals]
        assert "$5.99" in price_texts
        assert "2/$5" in price_texts
        assert "3 for 10" in price_texts
        assert "2 @ 4.00" in price_texts

    def test_every_deal_has_non_empty_price_text(self, ingest_svc):
        payload = json.loads((FIXTURES / "azure_layout_simple.json").read_text())
        deals = ingest_svc._extract_deals_from_layout(payload)
        assert deals, "fixture should yield at least one deal"
        for d in deals:
            assert d["price_text"], "extractor must not emit price-less deals"

    def test_title_uses_prior_line(self, ingest_svc):
        payload = {
            "pages": [
                {
                    "lines": [
                        {"content": "Chicken Thighs"},
                        {"content": "Family Pack"},
                        {"content": "$5.99/kg"},
                    ]
                }
            ]
        }
        deals = ingest_svc._extract_deals_from_layout(payload)
        assert len(deals) == 1
        assert deals[0]["title"] == "Family Pack"
        # description = prev2 + prev1
        assert "Chicken Thighs" in deals[0]["description"]

    def test_empty_pages_returns_empty(self, ingest_svc):
        assert ingest_svc._extract_deals_from_layout({}) == []
        assert ingest_svc._extract_deals_from_layout({"pages": []}) == []
        assert ingest_svc._extract_deals_from_layout({"pages": None}) == []

    def test_malformed_lines_are_skipped(self, ingest_svc):
        payload = {
            "pages": [
                {"lines": [None, {}, {"content": ""}, {"content": "$3.99"}]}
            ]
        }
        deals = ingest_svc._extract_deals_from_layout(payload)
        # Only the real price line survives.
        assert len(deals) == 1
        assert deals[0]["price_text"] == "$3.99"


# ---------------------------------------------------------------------------
# ingest_dealrecords_json — end-to-end persistence without Azure
# ---------------------------------------------------------------------------


class TestIngestDealRecordsJson:
    def test_round_trip_persists_batch_and_deals(self, ingest_svc, tmp_path):
        store = create_store(name="Fixture Mart")
        src = FIXTURES / "dealrecords_simple.json"

        result = ingest_svc.ingest_dealrecords_json(
            store_id=store.id,
            valid_from="2026-04-20",
            valid_to="2026-04-27",
            dealrecords_path=str(src),
            try_item_mapping=False,
        )

        assert isinstance(result, FlyerIngestResult)
        assert result.flyer_id > 0
        assert result.assets_count == 0
        assert result.raw_json_count == 0
        # Three valid entries, one empty record — empties are dropped.
        assert result.deals_count == 3

        # Verify rows landed in the DB via the repo.
        repo = FlyersRepo()
        deals = repo.list_deals_for_flyer(result.flyer_id, apply_preferences=False)
        titles = {d["title"] for d in deals}
        assert "Chicken Thighs Family Pack" in titles
        assert "Fresh Apples" in titles
        assert "Yogurt 750 g" in titles

    def test_missing_file_raises(self, ingest_svc):
        with pytest.raises(FileNotFoundError):
            ingest_svc.ingest_dealrecords_json(
                store_id=None,
                valid_from=None,
                valid_to=None,
                dealrecords_path=str(FIXTURES / "does_not_exist.json"),
            )

    def test_non_list_payload_raises(self, ingest_svc, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"deals": []}), encoding="utf-8")
        with pytest.raises(ValueError):
            ingest_svc.ingest_dealrecords_json(
                store_id=None,
                valid_from=None,
                valid_to=None,
                dealrecords_path=str(bad),
            )

    def test_bundle_promo_resolves_to_unit_price(self, ingest_svc, tmp_path):
        """Ingestion must hand bundle-promo strings to MultiBuyDealService and
        persist the effective per-unit price."""
        src = tmp_path / "records.json"
        src.write_text(
            json.dumps(
                [{"title": "Apples", "description": "Red Delicious", "price_text": "2/$5.00"}]
            ),
            encoding="utf-8",
        )
        store = create_store(name="Promo Mart")

        result = ingest_svc.ingest_dealrecords_json(
            store_id=store.id,
            valid_from="2026-04-20",
            valid_to="2026-04-27",
            dealrecords_path=str(src),
            try_item_mapping=False,
        )

        repo = FlyersRepo()
        deals = repo.list_deals_for_flyer(result.flyer_id, apply_preferences=False)
        assert len(deals) == 1
        # 2/$5 → effective $2.50/unit
        assert deals[0]["unit_price"] == pytest.approx(2.50)


# ---------------------------------------------------------------------------
# Cross-cutting failure: inverted flyer dates currently accepted (FINDING)
# ---------------------------------------------------------------------------


class TestInvertedFlyerDates:
    """
    FINDING: FlyersRepo.create_flyer_batch stores valid_from/valid_to as-is
    with no validation, so an OCR-mangled row where valid_from > valid_to
    slips into the DB. The downstream query in list_active_deals then
    requires valid_from <= day <= valid_to, so inverted-date batches
    quietly disappear from "current deals" rather than alerting — but the
    row still exists. Test locks in current behaviour.
    """

    def test_inverted_dates_are_accepted_at_write_time(self, isolated_db):
        store = create_store(name="Inverted Mart")
        repo = FlyersRepo()

        flyer_id = repo.create_flyer_batch(
            store_id=store.id,
            valid_from="2026-04-27",
            valid_to="2026-04-20",   # before valid_from
            source_type="manual_upload",
        )
        assert flyer_id > 0  # No guard; row is written.

    def test_inverted_dates_never_surface_in_active_deals(self, isolated_db):
        store = create_store(name="Ghost Mart")
        repo = FlyersRepo()

        flyer_id = repo.create_flyer_batch(
            store_id=store.id,
            valid_from="2026-04-27",
            valid_to="2026-04-20",
            source_type="manual_upload",
        )
        repo.add_deal(
            flyer_id=flyer_id,
            store_id=store.id,
            title="Ghost Apples",
            description="unreachable",
            price_text="$1",
            deal_total=1.00,
        )

        # Query for a date that WOULD be inside either endpoint individually.
        # Because valid_from > valid_to, no day satisfies both bounds.
        for day in ("2026-04-20", "2026-04-23", "2026-04-27"):
            deals = repo.list_active_deals(
                store_id=store.id, on_date=day, apply_preferences=False
            )
            titles = [d["title"] for d in deals]
            assert "Ghost Apples" not in titles
