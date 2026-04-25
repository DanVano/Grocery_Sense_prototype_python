"""
M3 — UnitNormalizationService

Covers:
  - Pure-math conversions (weight, volume, count) via _convert
  - Alias folding in _normalize_unit (kgs, lbs, #, pounds, grams, fl oz variants, etc.)
  - guess_unit_from_text pattern matching
  - normalize() end-to-end including DB-backed default-unit persistence
  - Failure Class #1: OCR-mangled unit strings resolve safely (or fall back to 'each'),
    never silently apply a wrong conversion
"""

from __future__ import annotations

import math

import pytest

from Grocery_Sense.data.repositories.items_repo import create_item
from Grocery_Sense.services.unit_normalization_service import (
    G_PER_OZ,
    KG_TO_LB,
    LB_TO_KG,
    NormalizedPrice,
    UnitNormalizationService,
)


# ---------------------------------------------------------------------------
# _convert: pure math, no DB required
# ---------------------------------------------------------------------------


class TestWeightConversions:
    svc = UnitNormalizationService()

    @pytest.mark.parametrize(
        "price_from, from_unit, to_unit, expected",
        [
            (1.0, "kg", "kg", 1.0),
            (1.0, "lb", "kg", 1.0 / LB_TO_KG),
            (1.0, "kg", "lb", LB_TO_KG),
            (2.0, "lb", "kg", 2.0 * KG_TO_LB),
            (1.0, "g", "kg", 1000.0),
            (1.0, "kg", "g", 0.001),
            (1.0, "oz", "kg", 1.0 / (G_PER_OZ / 1000.0)),
        ],
    )
    def test_weight_factor(self, price_from, from_unit, to_unit, expected):
        result = self.svc._convert(
            unit_price=price_from, from_unit=from_unit, to_unit=to_unit
        )
        assert result is not None
        assert math.isclose(result, expected, rel_tol=1e-6)

    def test_kg_lb_round_trip_is_lossless(self):
        start = 4.99
        to_lb = self.svc._convert(unit_price=start, from_unit="kg", to_unit="lb")
        back = self.svc._convert(unit_price=to_lb, from_unit="lb", to_unit="kg")
        assert math.isclose(back, start, rel_tol=1e-9)


class TestVolumeConversions:
    svc = UnitNormalizationService()

    @pytest.mark.parametrize(
        "from_unit, to_unit",
        [
            ("L", "ml"),
            ("ml", "L"),
            ("L", "cup"),
            ("L", "fl_oz"),
            ("L", "tbsp"),
            ("L", "tsp"),
            ("L", "gal"),
            ("L", "pint"),
        ],
    )
    def test_volume_round_trip(self, from_unit, to_unit):
        start = 3.25
        mid = self.svc._convert(unit_price=start, from_unit=from_unit, to_unit=to_unit)
        assert mid is not None
        back = self.svc._convert(unit_price=mid, from_unit=to_unit, to_unit=from_unit)
        assert math.isclose(back, start, rel_tol=1e-6)


class TestCountConversions:
    svc = UnitNormalizationService()

    def test_each_to_dozen(self):
        assert self.svc._convert(unit_price=1.0, from_unit="each", to_unit="dozen") == 12.0

    def test_dozen_to_each(self):
        assert self.svc._convert(unit_price=12.0, from_unit="dozen", to_unit="each") == 1.0


class TestCrossTypeConversionsRejected:
    """Weight<->volume<->count must never silently convert."""

    svc = UnitNormalizationService()

    @pytest.mark.parametrize(
        "from_unit, to_unit",
        [
            ("kg", "L"),
            ("L", "kg"),
            ("each", "kg"),
            ("kg", "each"),
            ("pack", "kg"),
            ("ml", "g"),
        ],
    )
    def test_returns_none(self, from_unit, to_unit):
        assert (
            self.svc._convert(unit_price=1.0, from_unit=from_unit, to_unit=to_unit)
            is None
        )


# ---------------------------------------------------------------------------
# _normalize_unit — alias folding
# ---------------------------------------------------------------------------


class TestUnitAliasFolding:
    svc = UnitNormalizationService()

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("kg", "kg"),
            ("KG", "kg"),
            ("kgs", "kg"),
            ("Kilograms", "kg"),
            ("lb", "lb"),
            ("LBS", "lb"),
            ("#", "lb"),
            ("pound", "lb"),
            ("Pounds", "lb"),
            ("g", "g"),
            ("gram", "g"),
            ("GRAMS", "g"),
            ("oz", "oz"),
            ("Ounces", "oz"),
            ("l", "L"),
            ("L", "L"),
            ("litre", "L"),
            ("liters", "L"),
            ("ml", "ml"),
            ("mL", "ml"),
            ("fl oz", "fl_oz"),
            ("fl_oz", "fl_oz"),
            ("floz", "fl_oz"),
            ("fluid ounces", "fl_oz"),
            ("cup", "cup"),
            ("cups", "cup"),
            ("tbsp", "tbsp"),
            ("tsp", "tsp"),
            ("gal", "gal"),
            ("pt", "pint"),
            ("pint", "pint"),
            ("dozen", "dozen"),
            ("doz", "dozen"),
            ("bunch", "bunch"),
            ("case", "case"),
            ("pack", "pack"),
            ("pkg", "pack"),
            ("ea", "each"),
            ("each", "each"),
            ("COUNT", "each"),
        ],
    )
    def test_known_aliases(self, raw, expected):
        assert self.svc._normalize_unit(raw) == expected

    @pytest.mark.parametrize("raw", ["", None, "   ", "bogus", "xyz", "karat"])
    def test_unknown_returns_unknown(self, raw):
        assert self.svc._normalize_unit(raw) == "unknown"


# ---------------------------------------------------------------------------
# Failure Class #1 — OCR-mangled unit strings must fail safe
# ---------------------------------------------------------------------------


class TestOcrMangledUnits:
    """
    Dirty inputs we've seen out of Azure DocInt + flyer OCR.
    Contract: either resolve to a real unit, or fall through to 'unknown' → 'each'.
    NEVER silently produce a converted price for a wrong unit.
    """

    svc = UnitNormalizationService()

    @pytest.mark.parametrize(
        "raw, expected_category",
        [
            ("l", "L"),
            ("L", "L"),
            ("1L", "unknown"),
            ("lb.", "unknown"),
            ("lb ", "lb"),
            ("Lb", "lb"),
            (" KG ", "kg"),
            ("g ", "g"),
            ("0.453", "unknown"),
            ("O.453", "unknown"),
            ("1,25 kg", "unknown"),
            ("1/2 lb", "unknown"),
            ("kilo", "unknown"),
        ],
    )
    def test_mangled_unit_resolution(self, raw, expected_category):
        """Mangled text either normalizes cleanly or returns 'unknown' — never a wrong unit."""
        assert self.svc._normalize_unit(raw) == expected_category

    def test_mangled_falls_through_to_each_in_normalize(self, isolated_db):
        """normalize() must default unknown units to 'each', not silently convert."""
        item = create_item(canonical_name="mystery jar")
        result = self.svc.normalize(
            item_id=item.id,
            unit_price=4.99,
            observed_unit="O.453",
            description="",
        )
        assert result.norm_unit == "each"
        assert result.norm_unit_price == 4.99


# ---------------------------------------------------------------------------
# guess_unit_from_text
# ---------------------------------------------------------------------------


class TestGuessUnitFromText:
    svc = UnitNormalizationService()

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("Chicken breast $10/kg", "kg"),
            ("Apples 500g bag", "g"),
            ("Ground beef per lb", "lb"),
            ("Ham 12 oz", "oz"),
            ("Milk 2 L carton", "L"),
            ("Olive oil 500 ml", "ml"),
            ("Vanilla 1 fl oz", "fl_oz"),
            ("Butter 1 cup", "cup"),
            ("Sugar 2 tbsp", "tbsp"),
            ("Salt tsp", "tsp"),
            ("Paint 1 gallon", "gal"),
            ("Ice cream pint", "pint"),
            ("Eggs dozen", "dozen"),
            ("Bananas bunch", "bunch"),
            ("24-pack cola", "pack"),
            ("Case of water", "case"),
            ("Apples each", "each"),
            ("unlabelled garbage", "unknown"),
        ],
    )
    def test_patterns(self, text, expected):
        assert self.svc.guess_unit_from_text(text) == expected

    def test_fl_oz_beats_oz(self):
        """'fl oz' must be detected before plain 'oz' to avoid unit collision."""
        assert self.svc.guess_unit_from_text("Soda 12 fl oz can") == "fl_oz"


# ---------------------------------------------------------------------------
# normalize() end-to-end (DB-backed via isolated_db autouse fixture)
# ---------------------------------------------------------------------------


class TestNormalizeEndToEnd:
    svc = UnitNormalizationService()

    def test_first_observation_sets_item_default_unit(self, isolated_db):
        item = create_item(canonical_name="chicken thighs", category="meat")
        # No default_unit on the item yet → first observation should set it.
        result = self.svc.normalize(
            item_id=item.id, unit_price=8.80, observed_unit="kg", description=""
        )
        assert isinstance(result, NormalizedPrice)
        assert result.norm_unit == "kg"
        assert result.norm_unit_price == 8.80
        assert result.note == "no_conversion"
        assert self.svc.get_item_default_unit(item.id) == "kg"

    def test_converts_lb_to_kg_when_item_default_is_kg(self, isolated_db):
        item = create_item(canonical_name="pork loin")
        self.svc.set_item_default_unit_if_missing(item.id, "kg")
        result = self.svc.normalize(
            item_id=item.id, unit_price=2.00, observed_unit="lb", description=""
        )
        assert result.norm_unit == "kg"
        assert math.isclose(result.norm_unit_price, 2.00 / LB_TO_KG, rel_tol=1e-6)
        assert "lb->kg" in result.note

    def test_unconvertible_pair_keeps_observed_unit(self, isolated_db):
        """If default=kg but observed=each, no conversion is possible — keep observed."""
        item = create_item(canonical_name="weird jar")
        self.svc.set_item_default_unit_if_missing(item.id, "kg")
        result = self.svc.normalize(
            item_id=item.id, unit_price=3.99, observed_unit="each", description=""
        )
        assert result.norm_unit == "each"
        assert result.norm_unit_price == 3.99
        assert "no_conversion_possible" in result.note

    def test_unknown_unit_infers_from_description(self, isolated_db):
        item = create_item(canonical_name="rice")
        result = self.svc.normalize(
            item_id=item.id,
            unit_price=5.00,
            observed_unit="",
            description="Basmati rice 1 kg bag",
        )
        assert result.norm_unit == "kg"

    def test_set_default_unit_skips_unknown(self, isolated_db):
        """Unknown/garbage observations must NOT pollute items.default_unit."""
        item = create_item(canonical_name="mystery")
        self.svc.set_item_default_unit_if_missing(item.id, "O.453")
        assert self.svc.get_item_default_unit(item.id) is None

    def test_ensure_schema_adds_norm_columns(self, isolated_db):
        from Grocery_Sense.data.connection import get_connection

        self.svc.ensure_schema()
        with get_connection() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(prices);").fetchall()}
        assert {"norm_unit_price", "norm_unit", "norm_note"} <= cols
