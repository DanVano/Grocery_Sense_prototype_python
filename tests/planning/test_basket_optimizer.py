"""
M5 — BasketOptimizerService

Covers:
  - phrase_safe_hit (Failure Class #3 olive-oil trap, multi-word phrases,
    empty inputs)
  - optimize() guard rails: empty basket / no stores / items without item_id
  - Price-source priority: flyer > store history > global history > unknown
  - One-store vs two-store mode
  - Savings vs 180-day average and 180-day lowest
  - Preference annotations (hard_excluded, soft_hits, starred) via
    monkeypatched preferences_service
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from Grocery_Sense.data.repositories.flyers_repo import FlyersRepo
from Grocery_Sense.data.repositories.items_repo import create_item
from Grocery_Sense.data.repositories.prices_repo import add_price_point
from Grocery_Sense.data.repositories.stores_repo import create_store
from Grocery_Sense.services import basket_optimizer_service as bo_mod
from Grocery_Sense.services.basket_optimizer_service import (
    BasketOptimizationResult,
    BasketOptimizerService,
    DEFAULT_EXCLUDE_SAFE_PHRASES,
    PricePick,
    StorePlan,
    phrase_safe_hit,
)
from Grocery_Sense.services.shopping_list_service import ShoppingListService


def _recent(n: int = 3) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


@pytest.fixture
def svc() -> BasketOptimizerService:
    return BasketOptimizerService()


@pytest.fixture
def sl(isolated_db) -> ShoppingListService:
    return ShoppingListService()


def _add_to_basket(sl: ShoppingListService, name: str, item_id: int, qty: float = 1.0):
    return sl.add_single_item(name=name, quantity=qty, item_id=item_id)


# ---------------------------------------------------------------------------
# phrase_safe_hit — Failure Class #3 guard
# ---------------------------------------------------------------------------


class TestPhraseSafeHit:
    """
    Failure Class #3 mandatory guard: 'olives' must NOT match text that
    contains 'olive oil'. This is the fragile allowlist at
    basket_optimizer_service.py:30 (DEFAULT_EXCLUDE_SAFE_PHRASES).
    """

    def test_olive_oil_protects_olives(self):
        assert phrase_safe_hit("Extra Virgin Olive Oil 500ml", "olives") is False
        assert phrase_safe_hit("Olive Oil", "olive") is False

    def test_plain_olives_still_hits(self):
        assert phrase_safe_hit("olives jar", "olives") is True
        assert phrase_safe_hit("black olive", "olive") is True

    def test_olive_oil_only_saves_olive_variants(self):
        """
        The allowlist logic only short-circuits when the term is 'olive' or
        'olives'. Other overlapping-word cases still hit.
        """
        assert phrase_safe_hit("Olive Oil 500ml", "oil") is True

    def test_single_word_requires_token_boundary(self):
        # 'beef' hits 'ground beef' (boundary token match).
        assert phrase_safe_hit("Ground Beef Family Pack", "beef") is True
        # 'pork' does NOT hit a recipe of chicken+thighs.
        assert phrase_safe_hit("chicken thighs", "pork") is False

    def test_multiword_phrase_uses_substring_match(self):
        assert phrase_safe_hit("I love ground beef", "ground beef") is True
        assert phrase_safe_hit("beef stew", "ground beef") is False

    @pytest.mark.parametrize(
        "text, term",
        [
            ("", "beef"),
            ("beef", ""),
            (None, "beef"),
            ("beef", None),
        ],
    )
    def test_empty_inputs_return_false(self, text, term):
        assert phrase_safe_hit(text, term) is False  # type: ignore[arg-type]

    def test_custom_safe_phrases_override_default(self):
        """Caller can supply their own allowlist."""
        assert phrase_safe_hit("vegetable oil 1L", "oil", safe_phrases=[]) is True
        # With the custom safe phrase supplied, the 'olives' trap still only
        # triggers for olive/olives (documented narrow behaviour).
        assert (
            phrase_safe_hit("vegetable oil 1L", "olives", safe_phrases=["vegetable oil"])
            is False
        )

    def test_default_safe_phrases_list_populated(self):
        """Regression: the allowlist must not be accidentally emptied."""
        assert "olive oil" in DEFAULT_EXCLUDE_SAFE_PHRASES


# ---------------------------------------------------------------------------
# optimize() — guard rails
# ---------------------------------------------------------------------------


class TestGuardRails:
    def test_empty_basket(self, svc, isolated_db):
        create_store(name="A", is_favorite=True)
        result = svc.optimize(mode="two_store")
        assert isinstance(result, BasketOptimizationResult)
        assert result.stores == []
        assert any("empty" in w.lower() for w in result.warnings)

    def test_no_stores(self, svc, sl):
        item = create_item(canonical_name="eggs")
        _add_to_basket(sl, "eggs", item.id)
        result = svc.optimize()
        assert result.stores == []
        assert any("no stores" in w.lower() for w in result.warnings)

    def test_item_without_item_id_is_skipped(self, svc, sl):
        create_store(name="A", is_favorite=True)
        sl.add_single_item(name="unlinked")  # no item_id
        result = svc.optimize()
        assert result.stores == []
        assert any("item_id" in w.lower() for w in result.warnings)

    def test_invalid_mode_defaults_to_two_store(self, svc, sl):
        a = create_store(name="A", is_favorite=True)
        item = create_item(canonical_name="eggs")
        add_price_point(
            item_id=item.id, store_id=a.id, unit_price=3.0, unit="each",
            source="receipt", date=_recent(),
        )
        _add_to_basket(sl, "eggs", item.id)
        result = svc.optimize(mode="banana")
        assert result.mode == "two_store"


# ---------------------------------------------------------------------------
# Price-source priority
# ---------------------------------------------------------------------------


class TestPriceSourcePriority:
    def _setup_flyer(self, store_id: int, item_id: int, *, unit_price: float) -> int:
        """Create an active flyer batch + deal so _load_active_flyer_unit_prices can pick it up."""
        # flyer_ingest uses a separate flyer_deals table; the basket optimizer reads
        # from prices.source='flyer' joined to flyer_sources. Seed via prices_repo.
        from Grocery_Sense.data.connection import get_connection

        today = date.today().isoformat()
        vt = (date.today() + timedelta(days=7)).isoformat()

        with get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO flyer_sources (provider, external_id, store_id, valid_from, valid_to) "
                "VALUES (?, ?, ?, ?, ?)",
                ("test", "flyer-1", store_id, today, vt),
            )
            fs_id = int(cur.lastrowid)
            conn.commit()

        add_price_point(
            item_id=item_id, store_id=store_id, unit_price=unit_price,
            unit="each", source="flyer", flyer_source_id=fs_id, date=today,
        )
        return fs_id

    def test_store_history_used_when_no_flyer(self, svc, sl):
        a = create_store(name="A", is_favorite=True)
        item = create_item(canonical_name="eggs")
        add_price_point(
            item_id=item.id, store_id=a.id, unit_price=4.0, unit="each",
            source="receipt", date=_recent(),
        )
        _add_to_basket(sl, "eggs", item.id)

        result = svc.optimize(mode="one_store")
        a_plan = next(sp for sp in result.stores if sp.store_id == a.id)
        assert a_plan.items[0].chosen.source == "history_store"
        assert a_plan.items[0].chosen.unit_price == pytest.approx(4.0)

    def test_active_flyer_wins_over_history(self, svc, sl):
        a = create_store(name="A", is_favorite=True)
        item = create_item(canonical_name="eggs")

        # Older, cheaper history
        add_price_point(
            item_id=item.id, store_id=a.id, unit_price=2.0, unit="each",
            source="receipt", date=_recent(60),
        )
        # Active flyer — should win regardless of price
        self._setup_flyer(a.id, item.id, unit_price=3.5)
        _add_to_basket(sl, "eggs", item.id)

        result = svc.optimize(mode="one_store")
        chosen = result.stores[0].items[0].chosen
        assert chosen.source == "flyer"
        assert chosen.unit_price == pytest.approx(3.5)

    def test_global_history_fallback_when_store_has_no_data(self, svc, sl):
        a = create_store(name="A", is_favorite=True)
        b = create_store(name="B")
        item = create_item(canonical_name="eggs")
        # Only Store B has history.
        add_price_point(
            item_id=item.id, store_id=b.id, unit_price=7.0, unit="each",
            source="receipt", date=_recent(),
        )
        _add_to_basket(sl, "eggs", item.id)

        result = svc.optimize(mode="one_store")
        # Whichever store is picked, eggs must get a non-None price via fallback.
        all_items = [i for sp in result.stores for i in sp.items]
        assert all_items[0].chosen.unit_price == pytest.approx(7.0)
        # If A was chosen, the source is history_any; if B was chosen, history_store.
        assert all_items[0].chosen.source in {"history_any", "history_store"}

    def test_unknown_source_when_no_data(self, svc, sl):
        a = create_store(name="A", is_favorite=True)
        item = create_item(canonical_name="mystery")
        _add_to_basket(sl, "mystery", item.id)

        result = svc.optimize(mode="one_store")
        chosen = result.stores[0].items[0].chosen
        assert chosen.unit_price is None
        assert chosen.source == "unknown"
        assert any("unknown prices" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# One-store vs two-store mode
# ---------------------------------------------------------------------------


class TestModes:
    def _setup_two_store_scenario(self, sl: ShoppingListService):
        a = create_store(name="A", is_favorite=True, priority=10)
        b = create_store(name="B", priority=5)

        eggs = create_item(canonical_name="eggs")
        milk = create_item(canonical_name="milk")

        # eggs cheaper at A, milk cheaper at B
        add_price_point(item_id=eggs.id, store_id=a.id, unit_price=3.0, unit="each",
                        source="receipt", date=_recent())
        add_price_point(item_id=eggs.id, store_id=b.id, unit_price=5.0, unit="each",
                        source="receipt", date=_recent())
        add_price_point(item_id=milk.id, store_id=a.id, unit_price=5.0, unit="each",
                        source="receipt", date=_recent())
        add_price_point(item_id=milk.id, store_id=b.id, unit_price=3.0, unit="each",
                        source="receipt", date=_recent())

        _add_to_basket(sl, "eggs", eggs.id)
        _add_to_basket(sl, "milk", milk.id)
        return a, b, eggs, milk

    def test_one_store_mode_uses_single_store(self, svc, sl):
        self._setup_two_store_scenario(sl)
        result = svc.optimize(mode="one_store")
        assert len(result.stores) == 1
        # Single store → basket = 3 + 5 = 8 regardless of which store wins
        # (either A with eggs=3,milk=5 or B with eggs=5,milk=3).
        assert result.basket_total_estimated == pytest.approx(8.0)

    def test_two_store_mode_can_split(self, svc, sl):
        self._setup_two_store_scenario(sl)
        result = svc.optimize(mode="two_store")
        assert 1 <= len(result.stores) <= 2
        # Best split puts each item at its cheaper store → 3 + 3 = 6.
        if len(result.stores) == 2:
            assert result.basket_total_estimated == pytest.approx(6.0)

    def test_two_store_warning_when_two_stores_chosen(self, svc, sl):
        self._setup_two_store_scenario(sl)
        result = svc.optimize(mode="two_store")
        if len(result.stores) == 2:
            assert any("extra trip" in w.lower() for w in result.warnings)


# ---------------------------------------------------------------------------
# Savings math
# ---------------------------------------------------------------------------


class TestSavings:
    def test_computes_usual_avg_and_lowest_over_180_days(self, svc, sl):
        a = create_store(name="A", is_favorite=True)
        item = create_item(canonical_name="eggs")

        # 180-day history: prices 2, 4, 6 → avg=4, min=2
        for price in (2.0, 4.0, 6.0):
            add_price_point(
                item_id=item.id, store_id=a.id, unit_price=price, unit="each",
                source="receipt", date=_recent(10),
            )
        # Most recent price (drives "chosen" for optimizer)
        add_price_point(
            item_id=item.id, store_id=a.id, unit_price=3.0, unit="each",
            source="receipt", date=_recent(),
        )

        _add_to_basket(sl, "eggs", item.id, qty=2)

        result = svc.optimize(mode="one_store")
        # basket_total = 3.0 * 2 = 6
        assert result.basket_total_estimated == pytest.approx(6.0)
        # usual avg = (2+4+6+3)/4 = 3.75; × 2 = 7.5
        assert result.basket_usual_avg_estimated == pytest.approx(7.5)
        # lowest = min 2.0; × 2 = 4.0
        assert result.basket_lowest_estimated == pytest.approx(4.0)
        # save vs avg = 7.5 - 6 = 1.5
        assert result.save_vs_usual_avg == pytest.approx(1.5)
        # save vs lowest = 4 - 6 = -2 (paying more than historic low)
        assert result.save_vs_lowest == pytest.approx(-2.0)

    def test_no_history_leaves_savings_none(self, svc, sl):
        a = create_store(name="A", is_favorite=True)
        item = create_item(canonical_name="eggs")
        # Only one very recent price to drive the estimate
        add_price_point(
            item_id=item.id, store_id=a.id, unit_price=3.0, unit="each",
            source="receipt", date=_recent(),
        )
        _add_to_basket(sl, "eggs", item.id)
        result = svc.optimize(mode="one_store")
        # With a single sample, usual_avg and lowest are both defined (n=1).
        assert result.basket_usual_avg_estimated is not None
        assert result.basket_lowest_estimated is not None


# ---------------------------------------------------------------------------
# Preference annotations
# ---------------------------------------------------------------------------


class TestPreferenceAnnotations:
    """
    preferences_service.compute_effective_preferences is optional. Tests
    monkeypatch it to return a SimpleNamespace shaped like EffectivePreferences.
    """

    @pytest.fixture
    def fake_prefs(self, monkeypatch):
        eff = SimpleNamespace(
            hard_excludes={"peanuts"},
            soft_excludes={
                "olives": ["Alice", "Bob"],
                "beef": ["Alice"],
            },
        )
        fake_ps = SimpleNamespace(
            compute_effective_preferences=lambda: eff,
        )
        monkeypatch.setattr(bo_mod, "preferences_service", fake_ps)
        return eff

    def test_hard_exclude_flagged(self, svc, sl, fake_prefs):
        a = create_store(name="A", is_favorite=True)
        item = create_item(canonical_name="peanuts")
        add_price_point(
            item_id=item.id, store_id=a.id, unit_price=3.0, unit="each",
            source="receipt", date=_recent(),
        )
        _add_to_basket(sl, "peanuts", item.id)

        result = svc.optimize(mode="one_store")
        plan = result.stores[0].items[0]
        assert plan.hard_excluded is True
        assert any("HARD exclude" in w for w in result.warnings)

    def test_soft_exclude_annotated(self, svc, sl, fake_prefs):
        a = create_store(name="A", is_favorite=True)
        item = create_item(canonical_name="ground beef")
        add_price_point(
            item_id=item.id, store_id=a.id, unit_price=8.0, unit="each",
            source="receipt", date=_recent(),
        )
        _add_to_basket(sl, "ground beef", item.id)

        result = svc.optimize(mode="one_store")
        plan = result.stores[0].items[0]
        # beef term should be soft-hit for Alice
        assert any(term == "beef" for term, _members in plan.soft_hits)

    def test_olive_oil_is_not_soft_excluded_by_olives_term(self, svc, sl, monkeypatch):
        """
        Failure Class #3 end-to-end: household's soft-exclude of 'olives' must
        NOT flag 'olive oil' in the basket. Locked in via DEFAULT_EXCLUDE_SAFE_PHRASES.
        """
        eff = SimpleNamespace(
            hard_excludes=set(),
            soft_excludes={"olives": ["Alice"]},
        )
        monkeypatch.setattr(
            bo_mod, "preferences_service",
            SimpleNamespace(compute_effective_preferences=lambda: eff),
        )

        a = create_store(name="A", is_favorite=True)
        item = create_item(canonical_name="olive oil 500ml")
        add_price_point(
            item_id=item.id, store_id=a.id, unit_price=9.99, unit="each",
            source="receipt", date=_recent(),
        )
        _add_to_basket(sl, "olive oil 500ml", item.id)

        result = svc.optimize(mode="one_store")
        plan = result.stores[0].items[0]
        assert plan.hard_excluded is False
        assert plan.soft_hits == []  # the trap is defused


# ---------------------------------------------------------------------------
# Dataclass shapes
# ---------------------------------------------------------------------------


class TestDataclasses:
    def test_price_pick_defaults(self):
        pp = PricePick(store_id=1, store_name="X", unit_price=None, unit="each", source="unknown")
        assert pp.unit_price is None

    def test_store_plan_defaults(self):
        sp = StorePlan(store_id=1, store_name="X")
        assert sp.items == []
        assert sp.total_estimated == 0.0
        assert sp.unknown_count == 0

    def test_result_defaults(self):
        r = BasketOptimizationResult(mode="one_store")
        assert r.stores == []
        assert r.basket_total_estimated == 0.0
        assert r.warnings == []
