"""
M6 — preferences_service

Covers the rules engine that drives basket_optimizer, flyers_repo, and the
preferences UI.

Key rules exercised:
  - Allergies (any member) → household HARD (user-safety critical)
  - Master hard_excludes → household HARD
  - Secondary hard_excludes → household SOFT (degraded)
  - Master excluded_proteins → HARD
  - Secondary excluded_proteins → SOFT
  - Strong soft excludes via _strong_soft_threshold:
      N<=3 → S>=2; N=4 → S>=3; N>=5 → S>=3 AND S/N>=0.60
  - Protein weights / cuisines / oils inherit baseline (master)
  - Annotation helpers (*/**) for UI
  - validate_add_exclude prevents redundant excludes
  - reset_secondary_member_to_household_baseline preserves allergies
  - is_oil_allowed with empty allowlist → unrestricted
  - Effective edit state for a member blends baseline + member overrides
"""

from __future__ import annotations

import pytest

from Grocery_Sense.config import config_store
from Grocery_Sense.config.config_store import (
    ROLE_MASTER,
    ROLE_SECONDARY,
    add_member,
    get_master_member,
    save_member_profile,
)
from Grocery_Sense.services import preferences_service as ps
from Grocery_Sense.services.preferences_service import (
    EffectivePreferences,
    _strong_soft_threshold,
    annotate_name_with_star,
    annotate_protein_with_star,
    compute_effective_preferences,
    get_effective_edit_state_for_member,
    get_household_baseline_profile,
    get_household_hard_excludes,
    get_soft_exclude_marker,
    get_star_excluders,
    protein_groups,
    reset_secondary_member_to_household_baseline,
    validate_add_exclude,
)


# ---------------------------------------------------------------------------
# Scenario helpers
# ---------------------------------------------------------------------------


def _seed_master(**profile_overrides):
    master = get_master_member()
    save_member_profile(master.id, profile_overrides)
    return master


def _add_secondary(name: str, **profile_overrides):
    mem = add_member(name, role=ROLE_SECONDARY)
    save_member_profile(mem.id, profile_overrides)
    return mem


# ---------------------------------------------------------------------------
# _strong_soft_threshold (pure)
# ---------------------------------------------------------------------------


class TestStrongSoftThreshold:
    @pytest.mark.parametrize(
        "n, s, expected",
        [
            (0, 0, False),
            (1, 1, False),
            (2, 1, False),
            (2, 2, True),
            (3, 2, True),
            (3, 1, False),
            (4, 2, False),
            (4, 3, True),
            (5, 2, False),
            (5, 3, True),     # 3/5 = 0.60, meets threshold
            (5, 4, True),
            (6, 3, False),    # 3/6 = 0.50, below 0.60
            (6, 4, True),     # 4/6 ≈ 0.67
            (10, 6, True),
            (10, 5, False),   # 5/10 = 0.50
        ],
    )
    def test_thresholds(self, n, s, expected):
        assert _strong_soft_threshold(n, s) is expected


# ---------------------------------------------------------------------------
# compute_effective_preferences — rule-by-rule
# ---------------------------------------------------------------------------


class TestAllergyRule:
    def test_any_member_allergy_becomes_household_hard(self, tmp_config_file):
        _seed_master(allergies=["peanuts"])
        _add_secondary("Alice", allergies=["shellfish"])

        eff = compute_effective_preferences()
        assert "peanuts" in eff.hard_excludes
        assert "shellfish" in eff.hard_excludes
        assert eff.is_hard_excluded("peanuts")
        assert eff.is_hard_excluded("shellfish")

    def test_secondary_only_allergy_still_household_hard(self, tmp_config_file):
        """
        User-safety contract (cross-ref M5 meal-suggestion test): a secondary's
        allergy must protect the whole household.
        """
        _seed_master()
        _add_secondary("Alice", allergies=["peanuts"])

        eff = compute_effective_preferences()
        assert eff.is_hard_excluded("peanuts")


class TestHardExcludeRule:
    def test_master_hard_exclude_is_household_hard(self, tmp_config_file):
        _seed_master(hard_excludes=["liver"])
        eff = compute_effective_preferences()
        assert eff.is_hard_excluded("liver")

    def test_secondary_hard_exclude_downgrades_to_soft(self, tmp_config_file):
        """
        Secondary's stored hard_excludes were already downgraded by
        ensure_member_profile_defaults. compute_effective_preferences treats
        them as soft as well. Net result: never hard at the household level.
        """
        _seed_master()
        alice = _add_secondary("Alice", hard_excludes=["liver"])

        eff = compute_effective_preferences()
        assert not eff.is_hard_excluded("liver")
        assert "Alice" in eff.soft_excluders("liver")


class TestSoftExcludeRule:
    def test_master_soft_exclude_tracked_not_starred(self, tmp_config_file):
        _seed_master(soft_excludes=["tofu"])
        eff = compute_effective_preferences()
        # Tracked in the map.
        assert eff.soft_excluders("tofu") == ["Primary"]
        # But master alone doesn't produce a star marker.
        assert get_soft_exclude_marker("tofu", eff=eff) == ""

    def test_secondary_soft_exclude_earns_star(self, tmp_config_file):
        _seed_master()
        _add_secondary("Alice", soft_excludes=["tofu"])

        eff = compute_effective_preferences()
        assert eff.soft_excluders("tofu") == ["Alice"]
        assert get_soft_exclude_marker("tofu", eff=eff) == "*"

    def test_multiple_secondaries_with_consensus_earn_double_star(self, tmp_config_file):
        """
        N=3 household, 2 secondaries agree → strong soft exclude (S>=2 rule).
        """
        _seed_master()
        _add_secondary("Alice", soft_excludes=["tofu"])
        _add_secondary("Bob", soft_excludes=["tofu"])

        eff = compute_effective_preferences()
        assert eff.is_strong_soft_excluded("tofu")
        assert get_soft_exclude_marker("tofu", eff=eff) == "**"

    def test_hard_excluded_ingredient_does_not_get_star(self, tmp_config_file):
        """A hard-excluded item should never show as soft-marked."""
        _seed_master(hard_excludes=["liver"])
        _add_secondary("Alice", soft_excludes=["liver"])

        eff = compute_effective_preferences()
        assert get_soft_exclude_marker("liver", eff=eff) == ""
        assert get_star_excluders("liver", eff=eff) == []


class TestProteinExclusionRule:
    def test_master_excluded_proteins_are_hard(self, tmp_config_file):
        _seed_master(excluded_proteins=["pork"])
        eff = compute_effective_preferences()
        assert "pork" in eff.excluded_proteins_hard

    def test_secondary_excluded_proteins_are_soft(self, tmp_config_file):
        _seed_master()
        _add_secondary("Alice", excluded_proteins=["beef"])

        eff = compute_effective_preferences()
        assert "beef" not in eff.excluded_proteins_hard
        assert "Alice" in eff.soft_protein_excluders("beef")

    def test_strong_soft_protein_exclude(self, tmp_config_file):
        _seed_master()
        _add_secondary("Alice", excluded_proteins=["beef"])
        _add_secondary("Bob", excluded_proteins=["beef"])

        eff = compute_effective_preferences()
        # N=3, S=2 → strong
        assert eff.is_strong_soft_protein_excluded("beef")

    def test_annotate_protein_with_star(self, tmp_config_file):
        _seed_master()
        _add_secondary("Alice", excluded_proteins=["beef"])

        eff = compute_effective_preferences()
        assert annotate_protein_with_star("beef", eff=eff) == "beef*"

    def test_annotate_protein_double_star_on_consensus(self, tmp_config_file):
        _seed_master()
        _add_secondary("Alice", excluded_proteins=["beef"])
        _add_secondary("Bob", excluded_proteins=["beef"])

        eff = compute_effective_preferences()
        assert annotate_protein_with_star("beef", eff=eff) == "beef**"


class TestBaselineInheritance:
    def test_protein_weights_come_from_master(self, tmp_config_file):
        _seed_master(preferred_protein_weights={"chicken": 2.0, "beef": 0.5})
        eff = compute_effective_preferences()
        assert eff.protein_weight("chicken") == pytest.approx(2.0)
        assert eff.protein_weight("beef") == pytest.approx(0.5)
        # Unknown protein defaults to 1.0
        assert eff.protein_weight("unicorn") == 1.0

    def test_secondary_weights_do_not_override(self, tmp_config_file):
        _seed_master(preferred_protein_weights={"chicken": 2.0})
        _add_secondary("Alice", preferred_protein_weights={"chicken": 0.1})

        eff = compute_effective_preferences()
        # Master's weight wins — secondaries do not shift baseline.
        assert eff.protein_weight("chicken") == pytest.approx(2.0)

    def test_cuisines_come_from_master(self, tmp_config_file):
        _seed_master(favorite_cuisines=["japanese", "mexican"])
        eff = compute_effective_preferences()
        assert eff.cuisines_preferred == {"japanese", "mexican"}

    def test_oils_come_from_master_only(self, tmp_config_file):
        _seed_master(oils_allowed=["olive oil", "avocado oil"])
        _add_secondary("Alice", oils_allowed=["coconut oil"])  # ignored

        eff = compute_effective_preferences()
        assert eff.oils_allowed == {"olive oil", "avocado oil"}


class TestIsOilAllowed:
    def test_empty_allowlist_is_unrestricted(self, tmp_config_file):
        _seed_master()  # no oils_allowed
        eff = compute_effective_preferences()
        assert eff.is_oil_allowed("olive oil")
        assert eff.is_oil_allowed("vegetable oil")
        assert eff.is_oil_allowed("anything")

    def test_allowlist_gates(self, tmp_config_file):
        _seed_master(oils_allowed=["olive oil"])
        eff = compute_effective_preferences()
        assert eff.is_oil_allowed("olive oil")
        assert not eff.is_oil_allowed("vegetable oil")

    def test_empty_string_returns_true(self, tmp_config_file):
        eff = compute_effective_preferences()
        assert eff.is_oil_allowed("") is True


# ---------------------------------------------------------------------------
# Annotation helpers
# ---------------------------------------------------------------------------


class TestAnnotateNameWithStar:
    def test_no_marker_returns_name_unchanged(self, tmp_config_file):
        _seed_master()
        assert annotate_name_with_star("apples") == "apples"

    def test_single_star_for_soft_exclude(self, tmp_config_file):
        _seed_master()
        _add_secondary("Alice", soft_excludes=["tofu"])
        assert annotate_name_with_star("tofu") == "tofu*"

    def test_double_star_for_strong_soft(self, tmp_config_file):
        _seed_master()
        _add_secondary("Alice", soft_excludes=["tofu"])
        _add_secondary("Bob", soft_excludes=["tofu"])
        assert annotate_name_with_star("tofu") == "tofu**"

    def test_hard_excluded_ingredient_not_starred(self, tmp_config_file):
        _seed_master(hard_excludes=["liver"])
        assert annotate_name_with_star("liver") == "liver"


class TestStarExcluders:
    def test_filters_out_master(self, tmp_config_file):
        master = _seed_master(soft_excludes=["tofu"])
        _add_secondary("Alice", soft_excludes=["tofu"])

        excluders = get_star_excluders("tofu")
        assert master.name not in excluders
        assert "Alice" in excluders

    def test_empty_list_when_only_master(self, tmp_config_file):
        _seed_master(soft_excludes=["tofu"])
        assert get_star_excluders("tofu") == []


# ---------------------------------------------------------------------------
# get_household_hard_excludes / baseline profile / edit state
# ---------------------------------------------------------------------------


class TestHouseholdHelpers:
    def test_household_hard_excludes_union(self, tmp_config_file):
        _seed_master(hard_excludes=["liver"], allergies=["peanuts"])
        _add_secondary("Alice", allergies=["shellfish"])

        excludes = get_household_hard_excludes()
        assert {"liver", "peanuts", "shellfish"} <= set(excludes)

    def test_baseline_profile_unrestricted_oils_shown_as_all(self, tmp_config_file):
        """
        UI convenience: when no oils are explicitly allowed, the baseline
        profile returns the full canonical list so the UI renders everything
        checked.
        """
        _seed_master()  # no oils_allowed
        baseline = get_household_baseline_profile()
        assert len(baseline["oils_allowed"]) == len(ps.OILS)

    def test_baseline_profile_respects_explicit_oils(self, tmp_config_file):
        _seed_master(oils_allowed=["olive oil"])
        baseline = get_household_baseline_profile()
        assert baseline["oils_allowed"] == ["olive oil"]


class TestEffectiveEditState:
    def test_member_excluded_proteins_affect_selection(self, tmp_config_file):
        _seed_master()
        alice = _add_secondary("Alice", excluded_proteins=["beef"])

        state = get_effective_edit_state_for_member(alice.id)
        assert "beef" not in state["proteins_selected"]
        assert "chicken" in state["proteins_selected"]

    def test_missing_member_returns_empty_shape(self, tmp_config_file):
        state = get_effective_edit_state_for_member(9999)
        # The function still returns a dict with the same keys; they reference
        # baseline-only values since there's no member override.
        assert "proteins_selected" in state
        assert "oils_allowed" in state


# ---------------------------------------------------------------------------
# validate_add_exclude
# ---------------------------------------------------------------------------


class TestValidateAddExclude:
    def test_allergy_ok_when_new(self, tmp_config_file):
        master = get_master_member()
        ok, msg = validate_add_exclude(
            member_id=master.id, value="peanuts", exclude_kind="allergy"
        )
        assert ok is True
        assert msg == ""

    def test_allergy_duplicate_blocked(self, tmp_config_file):
        master = _seed_master(allergies=["peanuts"])
        ok, _ = validate_add_exclude(
            member_id=master.id, value="peanuts", exclude_kind="allergy"
        )
        assert ok is False

    def test_hard_exclude_blocks_if_already_in_allergies(self, tmp_config_file):
        master = _seed_master(allergies=["peanuts"])
        ok, msg = validate_add_exclude(
            member_id=master.id, value="peanuts", exclude_kind="hard_exclude"
        )
        assert ok is False
        assert "allergy" in msg.lower()

    def test_secondary_soft_blocked_when_household_hard(self, tmp_config_file):
        _seed_master(hard_excludes=["liver"])
        alice = _add_secondary("Alice")

        ok, msg = validate_add_exclude(
            member_id=alice.id, value="liver", exclude_kind="soft_exclude"
        )
        assert ok is False
        assert "household" in msg.lower() or "hard" in msg.lower()

    def test_unknown_kind_blocked(self, tmp_config_file):
        master = get_master_member()
        ok, msg = validate_add_exclude(
            member_id=master.id, value="x", exclude_kind="banana"
        )
        assert ok is False

    def test_unknown_member_blocked(self, tmp_config_file):
        ok, _ = validate_add_exclude(
            member_id=9999, value="x", exclude_kind="allergy"
        )
        assert ok is False

    def test_empty_value_blocked(self, tmp_config_file):
        master = get_master_member()
        ok, _ = validate_add_exclude(
            member_id=master.id, value="   ", exclude_kind="allergy"
        )
        assert ok is False


# ---------------------------------------------------------------------------
# reset_secondary_member_to_household_baseline (preferences_service version)
# ---------------------------------------------------------------------------


class TestResetSecondaryBaseline:
    def test_clears_overrides_preserves_allergies(self, tmp_config_file):
        """
        The preferences_service version pops override keys entirely rather
        than setting them to []. Allergies are restored. After reset the
        effective household view no longer shows Alice soft-excluding tofu.
        """
        _seed_master()
        alice = _add_secondary(
            "Alice",
            allergies=["peanuts"],
            soft_excludes=["tofu"],
            excluded_proteins=["beef"],
        )

        # Confirm override is effective BEFORE reset.
        eff_before = compute_effective_preferences()
        assert "Alice" in eff_before.soft_excluders("tofu")

        reset_secondary_member_to_household_baseline(alice.id)

        # Allergy survives.
        prof = config_store.get_member_profile(alice.id)
        assert "peanuts" in prof["allergies"]
        # Override keys popped, so missing. Missing-or-empty means effective
        # preferences no longer picks them up.
        assert prof.get("soft_excludes", []) == []
        assert prof.get("excluded_proteins", []) == []

        # Observable contract: Alice no longer contributes the soft exclude.
        eff_after = compute_effective_preferences()
        assert "Alice" not in eff_after.soft_excluders("tofu")

    def test_refuses_master(self, tmp_config_file):
        master = _seed_master(soft_excludes=["tofu"])
        reset_secondary_member_to_household_baseline(master.id)

        prof = config_store.get_member_profile(master.id)
        # Master's preferences untouched.
        assert "tofu" in prof["soft_excludes"]


# ---------------------------------------------------------------------------
# protein_groups
# ---------------------------------------------------------------------------


class TestProteinGroups:
    def test_groups_populated(self, tmp_config_file):
        groups = protein_groups()
        assert "meat" in groups
        assert "seafood" in groups
        assert "plant" in groups

    def test_chicken_in_meat(self, tmp_config_file):
        groups = protein_groups()
        assert "chicken" in groups["meat"]
