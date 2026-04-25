"""
M3 — MultiBuyDealService

Failure Class #2 coverage: every real-world promo phrasing we've seen must either
decode to a correct effective unit price OR refuse to guess. The golden table at
tests/fixtures/multibuy_phrases.json is the source of truth; entries marked
supported=false document current gaps and lock in reject-don't-guess behavior.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from Grocery_Sense.services.multibuy_deal_service import DealAdjusted, MultiBuyDealService


FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent / "fixtures" / "multibuy_phrases.json"
)


def _load_phrases():
    with FIXTURE_PATH.open(encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def svc() -> MultiBuyDealService:
    return MultiBuyDealService()


# ---------------------------------------------------------------------------
# Golden table: parametrized over every row of multibuy_phrases.json
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entry", _load_phrases(), ids=lambda e: e["case"])
def test_golden_phrase(svc: MultiBuyDealService, entry: dict) -> None:
    result = svc.adjust(
        description=entry["desc"],
        quantity=entry.get("quantity"),
        unit_price=entry.get("unit_price"),
        line_total=entry.get("line_total"),
        discount=entry.get("discount"),
    )

    assert isinstance(result, DealAdjusted)
    assert entry["deal_note_contains"] in result.deal_note, (
        f"case={entry['case']!r}: deal_note={result.deal_note!r}"
    )

    expected = entry["expected_unit_price"]
    if expected is None:
        # Reject-don't-guess contract: must NOT fabricate a unit price.
        assert result.unit_price is None, (
            f"case={entry['case']!r} must not invent a unit_price "
            f"(got {result.unit_price!r})"
        )
    else:
        assert result.unit_price is not None, f"case={entry['case']!r} returned None"
        assert math.isclose(result.unit_price, expected, rel_tol=1e-6, abs_tol=1e-4), (
            f"case={entry['case']!r}: got {result.unit_price}, expected {expected}"
        )


# ---------------------------------------------------------------------------
# Focused class-level tests
# ---------------------------------------------------------------------------


class TestBundleParsing:
    svc = MultiBuyDealService()

    def test_slash_pattern_with_dollar(self):
        r = self.svc.adjust(
            description="2/$5.00",
            quantity=1,
            unit_price=None,
            line_total=None,
            discount=None,
        )
        assert math.isclose(r.unit_price, 2.50)

    def test_slash_pattern_without_dollar(self):
        r = self.svc.adjust(
            description="2/5",
            quantity=1,
            unit_price=None,
            line_total=None,
            discount=None,
        )
        assert math.isclose(r.unit_price, 2.50)

    def test_for_pattern(self):
        r = self.svc.adjust(
            description="3 for $10",
            quantity=1,
            unit_price=None,
            line_total=None,
            discount=None,
        )
        assert math.isclose(r.unit_price, 10.0 / 3.0, rel_tol=1e-6)

    def test_receipt_reconciliation_fixes_quantity(self):
        """Receipt shows qty=1 but line_total matches the bundle price → fix qty."""
        r = self.svc.adjust(
            description="2/$5.00",
            quantity=1,
            unit_price=None,
            line_total=5.00,
            discount=None,
        )
        assert r.quantity == 2.0
        assert math.isclose(r.unit_price, 2.50)
        assert "qty_fix" in r.deal_note


class TestBogoParsing:
    svc = MultiBuyDealService()

    def test_bogo_with_totals(self):
        r = self.svc.adjust(
            description="Buy 1 Get 1 Free",
            quantity=2,
            unit_price=5.00,
            line_total=5.00,
            discount=None,
        )
        assert math.isclose(r.unit_price, 2.50)
        assert "bogo" in r.deal_note

    def test_bogo_acronym(self):
        r = self.svc.adjust(
            description="BOGO",
            quantity=2,
            unit_price=None,
            line_total=3.99,
            discount=None,
        )
        assert math.isclose(r.unit_price, 1.995)

    def test_bogo_without_qty_falls_through(self):
        """BOGO with qty=1 cannot be resolved — should return the detected note, no adjust."""
        r = self.svc.adjust(
            description="Buy One Get One",
            quantity=1,
            unit_price=4.99,
            line_total=4.99,
            discount=None,
        )
        assert "bogo" in r.deal_note
        # No adjustment: effective price equals the input unit_price
        assert math.isclose(r.unit_price, 4.99)


class TestAtPriceParsing:
    svc = MultiBuyDealService()

    def test_basic_at_price(self):
        r = self.svc.adjust(
            description="2 @ 4.00",
            quantity=1,
            unit_price=None,
            line_total=None,
            discount=None,
        )
        assert math.isclose(r.unit_price, 4.00)
        assert "at(" in r.deal_note


class TestUnitFromTotalFallback:
    """When no promo is present but totals exist, derive unit price from total/qty."""

    svc = MultiBuyDealService()

    def test_derives_unit_from_total(self):
        r = self.svc.adjust(
            description="chicken thighs",
            quantity=2,
            unit_price=None,
            line_total=10.0,
            discount=None,
        )
        assert math.isclose(r.unit_price, 5.0)
        assert r.deal_note == "unit_from_total"

    def test_net_total_subtracts_discount(self):
        r = self.svc.adjust(
            description="apples",
            quantity=2,
            unit_price=None,
            line_total=10.0,
            discount=2.0,
        )
        assert math.isclose(r.unit_price, 4.0)

    def test_no_signal_returns_no_deal_without_fabricating(self):
        """Nothing to work with — must return no_deal with unit_price=None."""
        r = self.svc.adjust(
            description="mystery item",
            quantity=1,
            unit_price=None,
            line_total=None,
            discount=None,
        )
        assert r.unit_price is None
        assert r.deal_note == "no_deal"


class TestRejectDontGuess:
    """
    Mandatory per Agent 2's challenge: if a promo phrase is ambiguous or
    unrecognized, the parser must NOT fabricate a unit price.
    """

    svc = MultiBuyDealService()

    def test_dollar_off_without_anchor_price(self):
        r = self.svc.adjust(
            description="$1 off",
            quantity=1,
            unit_price=None,
            line_total=None,
            discount=None,
        )
        assert r.unit_price is None

    def test_was_now_without_support(self):
        r = self.svc.adjust(
            description="Was $4.99 Now $2.99",
            quantity=1,
            unit_price=None,
            line_total=None,
            discount=None,
        )
        assert r.unit_price is None
