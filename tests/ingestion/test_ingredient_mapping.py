"""
M4 — IngredientMappingService

Failure Class #3 (mandatory): aliasing collisions & orphaned aliases.
  - Weak matches MUST return method='none' (no silent "cream"→"ice cream").
  - Auto-learn contract: high-confidence fuzzy hits cache; below-threshold do not.
  - Abbreviation expansion + stopword stripping drive normalization.
  - Orphaned-alias finding: item_aliases has no ON DELETE CASCADE
    (schema.py:199) — deleting an item leaves aliases stranded. Test documents.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

import pytest

import Grocery_Sense.data.repositories.items_repo as items_repo_module
from Grocery_Sense.data.connection import get_connection
from Grocery_Sense.data.repositories.item_aliases_repo import ItemAliasesRepo
from Grocery_Sense.data.repositories.items_repo import create_item
from Grocery_Sense.services.ingredient_mapping_service import (
    IngredientMappingService,
    MappingResult,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent / "fixtures" / "alias_ambiguity.json"
)


def _load_ambiguity_table() -> List[dict]:
    with FIXTURE_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def _make_mapper(**overrides) -> IngredientMappingService:
    kwargs = dict(
        items_repo=items_repo_module,
        aliases_repo=ItemAliasesRepo(),
        auto_learn=True,
        learn_threshold=0.90,
        accept_threshold=0.78,
    )
    kwargs.update(overrides)
    return IngredientMappingService(**kwargs)


# ---------------------------------------------------------------------------
# Normalization pipeline (pure)
# ---------------------------------------------------------------------------


class TestNormalizationPipeline:
    mapper = _make_mapper.__wrapped__ if False else None  # noqa
    svc = IngredientMappingService(items_repo=items_repo_module)

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("Chicken Breast", "chicken breast"),
            ("CHICKEN-BREAST!!!", "chicken breast"),
            ("   multi    space   ", "multi space"),
            ("chk brst", "chicken breast"),
            ("GRND BF", "ground beef"),
            ("Skls Bnls THG", "skinless boneless thigh"),
            ("Chicken Breast Value Pack", "chicken breast"),
            ("Chicken Breast FAMILY pack", "chicken breast"),
            ("", ""),
            ("   ", ""),
        ],
    )
    def test_pipeline_expands_and_strips(self, raw, expected):
        assert self.svc._normalize_pipeline(raw) == expected


# ---------------------------------------------------------------------------
# Empty-DB behaviour — no items means method='none'
# ---------------------------------------------------------------------------


class TestEmptyDatabase:
    def test_empty_returns_none(self, isolated_db):
        mapper = _make_mapper()
        result = mapper.map_to_item("chicken breast")
        assert isinstance(result, MappingResult)
        assert result.method == "none"
        assert result.item_id is None
        assert result.confidence == 0.0


# ---------------------------------------------------------------------------
# Threshold contracts
# ---------------------------------------------------------------------------


class TestAcceptThreshold:
    def test_above_threshold_returns_fuzzy(self, isolated_db):
        item = create_item(canonical_name="chicken breast")
        mapper = _make_mapper()
        result = mapper.map_to_item("chicken breast")
        assert result.method == "fuzzy"
        assert result.item_id == item.id
        assert result.confidence >= 0.78

    def test_below_threshold_returns_none(self, isolated_db):
        create_item(canonical_name="olive oil")
        mapper = _make_mapper()
        # "oil" vs "olive oil" is too weak a token_sort match to meet the 0.78 gate.
        result = mapper.map_to_item("oil")
        assert result.method == "none"
        assert result.item_id is None
        # Confidence is reported even when method=none, to help callers tune.
        assert 0.0 <= result.confidence < 0.78

    def test_raising_threshold_rejects_borderline(self, isolated_db):
        create_item(canonical_name="chicken breast")
        strict = _make_mapper(accept_threshold=0.99)
        # Missing a word → fuzzy score dips below 99%.
        result = strict.map_to_item("chicken")
        assert result.method == "none"


# ---------------------------------------------------------------------------
# Auto-learn contract
# ---------------------------------------------------------------------------


class TestAutoLearn:
    def test_high_confidence_caches_alias(self, isolated_db):
        item = create_item(canonical_name="chicken breast")
        mapper = _make_mapper()

        # First call: fuzzy at score 1.0 → above learn_threshold=0.90 → alias cached.
        first = mapper.map_to_item("chicken breast")
        assert first.method == "fuzzy"
        assert first.confidence >= 0.90

        # Second call: alias cache hit short-circuits fuzzy.
        second = mapper.map_to_item("chicken breast")
        assert second.method == "alias"
        assert second.item_id == item.id

    def test_below_learn_threshold_does_not_cache(self, isolated_db):
        create_item(canonical_name="chicken breast")
        # Accept lower so fuzzy path succeeds, but learn stays at 0.90.
        mapper = _make_mapper(accept_threshold=0.50, learn_threshold=0.90)

        first = mapper.map_to_item("chicken")  # partial match, ~<0.90
        assert first.method == "fuzzy"
        assert first.confidence < 0.90

        # No alias written — next call still takes the fuzzy path.
        second = mapper.map_to_item("chicken")
        assert second.method == "fuzzy"

    def test_auto_learn_disabled_never_caches(self, isolated_db):
        create_item(canonical_name="chicken breast")
        mapper = _make_mapper(auto_learn=False)

        mapper.map_to_item("chicken breast")
        mapper.map_to_item("chicken breast")
        aliases = ItemAliasesRepo().list_all()
        assert aliases == []


# ---------------------------------------------------------------------------
# Alias cache hit short-circuits fuzzy
# ---------------------------------------------------------------------------


class TestAliasCache:
    def test_manual_alias_hit(self, isolated_db):
        item = create_item(canonical_name="chicken breast")
        aliases_repo = ItemAliasesRepo()
        aliases_repo.upsert_alias(
            alias_text="bp chk brst", item_id=item.id, confidence=1.0, source="manual"
        )

        mapper = _make_mapper(aliases_repo=aliases_repo)
        # Normalizer turns "BP CHK BRST" into "boneless chicken breast"
        # but the alias was stored under "bp chk brst" — so a direct hit requires
        # supplying the same raw form. That's expected behaviour: aliases are
        # keyed on the normalized pipeline output.
        # Store an alias under the normalized form instead:
        aliases_repo.upsert_alias(
            alias_text=mapper._normalize_pipeline("BP CHK BRST"),
            item_id=item.id,
            confidence=1.0,
            source="manual",
        )

        result = mapper.map_to_item("BP CHK BRST")
        assert result.method == "alias"
        assert result.item_id == item.id

    def test_alias_hit_increments_times_seen(self, isolated_db):
        item = create_item(canonical_name="rice")
        aliases_repo = ItemAliasesRepo()
        aliases_repo.upsert_alias(
            alias_text="rice", item_id=item.id, confidence=1.0, source="manual"
        )

        mapper = _make_mapper(aliases_repo=aliases_repo)
        mapper.map_to_item("rice")
        mapper.map_to_item("rice")

        aliases = aliases_repo.list_all()
        assert len(aliases) == 1
        # upsert_alias starts times_seen=1, mark_seen increments twice more.
        assert aliases[0].times_seen >= 3


# ---------------------------------------------------------------------------
# Failure Class #3 — golden ambiguity table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "entry", _load_ambiguity_table(), ids=lambda e: e["case"]
)
def test_ambiguity_golden(entry: dict, isolated_db) -> None:
    for canonical in entry["canonicals"]:
        create_item(canonical_name=canonical)

    mapper = _make_mapper()
    result = mapper.map_to_item(entry["raw"])

    assert result.method == entry["expected_method"], (
        f"case={entry['case']!r}: got method={result.method!r}, "
        f"confidence={result.confidence:.2f}, debug={result.debug}"
    )

    if entry["expected_canonical"] is None:
        assert result.item_id is None
    else:
        assert result.canonical_name == entry["expected_canonical"] or (
            result.matched_text == entry["expected_canonical"]
        )


# ---------------------------------------------------------------------------
# Orphaned aliases — FINDING: schema has no CASCADE
# ---------------------------------------------------------------------------


class TestOrphanedAliases:
    """
    FINDING (open): item_aliases.item_id still has no ON DELETE clause in
    schema.py. With FK pragma now enforced, deleting an item that has
    aliases raises IntegrityError rather than cascading. Callers must
    delete aliases first, or the schema should add ON DELETE CASCADE.
    """

    def test_delete_item_with_alias_raises_integrity_error(self, isolated_db):
        import sqlite3

        item = create_item(canonical_name="doomed item")
        aliases = ItemAliasesRepo()
        aliases.upsert_alias(
            alias_text="doomed alias", item_id=item.id, confidence=1.0, source="manual"
        )

        with pytest.raises(sqlite3.IntegrityError):
            with get_connection() as conn:
                conn.execute("DELETE FROM items WHERE id = ?", (item.id,))
                conn.commit()

        # The alias is still there because the DELETE was blocked.
        orphan = aliases.get_by_alias("doomed alias")
        assert orphan is not None
        assert orphan.item_id == item.id

    def test_cleaning_aliases_first_allows_delete(self, isolated_db):
        """Post-FK-fix workflow: drop aliases, then delete the parent item."""
        item = create_item(canonical_name="temp")
        aliases = ItemAliasesRepo()
        aliases.upsert_alias(
            alias_text="temp",
            item_id=item.id,
            confidence=1.0,
            source="manual",
        )

        with get_connection() as conn:
            conn.execute("DELETE FROM item_aliases WHERE item_id = ?", (item.id,))
            conn.execute("DELETE FROM items WHERE id = ?", (item.id,))
            conn.commit()

        from Grocery_Sense.data.repositories.items_repo import get_item_by_id
        assert get_item_by_id(item.id) is None
        assert aliases.get_by_alias("temp") is None


# ---------------------------------------------------------------------------
# Whitespace canonical_name — UNIQUE constraint is exact-string, not normalized
# ---------------------------------------------------------------------------


class TestCanonicalWhitespaceCollision:
    """
    FINDING (schema): items.canonical_name UNIQUE applies to the literal stored
    string after strip(). Variants that differ only in internal whitespace (e.g.
    'Milk 2L' vs 'Milk 2 L') can both slip in. create_item() uses strip() but
    not internal collapse. Documents the gap — tests lock in current behaviour.
    """

    def test_internal_whitespace_creates_duplicates(self, isolated_db):
        a = create_item(canonical_name="Milk 2L")
        b = create_item(canonical_name="Milk 2 L")
        assert a.id != b.id

    def test_trailing_whitespace_does_not_duplicate(self, isolated_db):
        a = create_item(canonical_name="eggs")
        b = create_item(canonical_name="eggs   ")
        # create_item() strips, so these resolve to the same row.
        assert a.id == b.id
