"""
M6 — config_store

Covers:
  - load_config on empty filesystem bootstraps a valid UserConfig with a master
  - save_config round-trips to disk
  - Corrupted JSON → graceful default
  - Version bump on load
  - ensure_member_profile_defaults: secondary hard→soft downgrade
  - Household helpers: list, get (by id / master / primary / active)
  - add_member / rename_member / delete_member (last-member guard, master reassignment)
  - set_active_member_id / set_primary_member_id
  - get_member_profile / save_member_profile
  - get_household_allergies unions across members
  - get_user_profile flattens master profile with legacy keys
  - get_postal_code / get_store_priority
  - cache_get / cache_set behaviour + expiry
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from Grocery_Sense.config import config_store
from Grocery_Sense.config.config_store import (
    PROFILE_VERSION,
    ROLE_MASTER,
    ROLE_SECONDARY,
    Household,
    HouseholdMember,
    UserConfig,
    add_member,
    cache_get,
    cache_set,
    default_member_profile,
    delete_member,
    ensure_member_profile_defaults,
    get_active_member,
    get_household_allergies,
    get_master_member,
    get_member,
    get_member_profile,
    get_postal_code,
    get_primary_member,
    get_store_priority,
    get_user_profile,
    is_master,
    list_members,
    load_config,
    rename_member,
    save_config,
    save_member_profile,
    set_active_member_id,
    set_primary_member_id,
)


# ---------------------------------------------------------------------------
# load / save round-trip
# ---------------------------------------------------------------------------


class TestLoadSaveRoundTrip:
    def test_empty_filesystem_bootstraps_defaults(self, tmp_config_file):
        cfg = load_config()
        assert isinstance(cfg, UserConfig)
        assert cfg.profile_version == PROFILE_VERSION
        assert cfg.country == "CA"
        assert len(cfg.household.members) == 1
        assert cfg.household.members[0].role == ROLE_MASTER
        assert cfg.household.members[0].id == cfg.household.primary_member_id

    def test_save_and_reload(self, tmp_config_file):
        cfg = load_config()
        cfg.postal_code = "V3J 0P6"
        cfg.city = "Coquitlam"
        cfg.store_priority = {"Walmart": 10, "Save-On": 5}
        save_config(cfg)

        # Force cache invalidation by clearing module-level singleton.
        config_store._config_cache = None
        config_store._config_mtime = None

        reloaded = load_config()
        assert reloaded.postal_code == "V3J 0P6"
        assert reloaded.city == "Coquitlam"
        assert reloaded.store_priority == {"Walmart": 10, "Save-On": 5}

    def test_corrupted_json_falls_back_to_defaults(self, tmp_config_file):
        tmp_config_file.write_text("{ not json", encoding="utf-8")
        cfg = load_config()
        assert isinstance(cfg, UserConfig)
        assert len(cfg.household.members) >= 1

    def test_version_bumped_on_load(self, tmp_config_file):
        tmp_config_file.write_text(
            json.dumps({"profile_version": 1, "postal_code": "X"}), encoding="utf-8"
        )
        cfg = load_config()
        assert cfg.profile_version == PROFILE_VERSION


# ---------------------------------------------------------------------------
# default_member_profile + ensure_member_profile_defaults
# ---------------------------------------------------------------------------


class TestProfileDefaults:
    def test_default_profile_shape(self):
        prof = default_member_profile()
        assert prof["eats_meat"] is True
        assert prof["allergies"] == []
        assert prof["hard_excludes"] == []
        assert prof["spice_level"] == "medium"

    def test_master_keeps_hard_excludes(self):
        prof = ensure_member_profile_defaults(
            {"hard_excludes": ["Peanuts"]}, role=ROLE_MASTER
        )
        assert prof["hard_excludes"] == ["peanuts"]
        assert prof["soft_excludes"] == []

    def test_secondary_hard_excludes_downgrade_to_soft(self):
        """
        FINAL RULE: secondaries cannot create household-hard excludes; their
        hard_excludes are merged into soft_excludes on save.
        """
        prof = ensure_member_profile_defaults(
            {"hard_excludes": ["Olives"]}, role=ROLE_SECONDARY
        )
        assert prof["hard_excludes"] == []
        assert "olives" in prof["soft_excludes"]

    def test_weights_clamped(self):
        prof = ensure_member_profile_defaults(
            {"preferred_protein_weights": {"chicken": 10.0, "beef": 0.01}},
            role=ROLE_MASTER,
        )
        assert prof["preferred_protein_weights"]["chicken"] == 3.0
        assert prof["preferred_protein_weights"]["beef"] == 0.25

    def test_legacy_styles_migration_is_dead_code(self):
        """
        FINDING: the 'styles' → 'meal_styles' migration branch at
        config_store.py:228-231 uses the condition
        `"styles" in merged and "meal_styles" not in merged`, but
        `base.copy()` always seeds `meal_styles=[]` first, so the branch
        never fires. Legacy configs that used 'styles' lose the data.
        'styles' is also always popped afterward. Locks in current
        (broken) behaviour.
        """
        prof = ensure_member_profile_defaults(
            {"styles": ["saucy"]}, role=ROLE_MASTER
        )
        # Data should have migrated — but it didn't.
        assert prof["meal_styles"] == []
        assert "styles" not in prof

    def test_sanitize_spice_level(self):
        for raw, expected in [
            ("mild", "low"),
            ("hot", "high"),
            ("MEDIUM", "medium"),
            ("banana", "medium"),  # unknown defaults
        ]:
            prof = ensure_member_profile_defaults(
                {"spice_level": raw}, role=ROLE_MASTER
            )
            assert prof["spice_level"] == expected

    def test_invalid_diet_defaults(self):
        prof = ensure_member_profile_defaults({"diet": "paleo"}, role=ROLE_MASTER)
        assert prof["diet"] == "meat eater"

    def test_comma_separated_list_sanitized(self):
        prof = ensure_member_profile_defaults(
            {"allergies": "peanuts, shellfish,, eggs"}, role=ROLE_MASTER
        )
        assert prof["allergies"] == ["peanuts", "shellfish", "eggs"]


# ---------------------------------------------------------------------------
# Household helpers
# ---------------------------------------------------------------------------


class TestHouseholdHelpers:
    def test_default_bootstrap_has_master(self, tmp_config_file):
        master = get_master_member()
        assert master.role == ROLE_MASTER

    def test_list_members(self, tmp_config_file):
        add_member("Alice")
        members = list_members()
        assert len(members) == 2
        assert {m.name for m in members} == {"Primary", "Alice"}

    def test_get_member_by_id(self, tmp_config_file):
        alice = add_member("Alice")
        found = get_member(alice.id)
        assert found is not None
        assert found.name == "Alice"

    def test_get_member_missing_returns_none(self, tmp_config_file):
        assert get_member(9999) is None

    def test_get_primary_falls_back_to_first(self, tmp_config_file):
        """
        Intentionally set an invalid primary_member_id — _ensure_household_defaults
        must recover to a valid existing id.
        """
        cfg = load_config()
        cfg.household.primary_member_id = 9999
        save_config(cfg)
        config_store._config_cache = None
        config_store._config_mtime = None

        assert get_primary_member().id in {m.id for m in list_members()}

    def test_get_active_member_default(self, tmp_config_file):
        assert get_active_member().id == get_primary_member().id

    def test_is_master(self, tmp_config_file):
        master = get_master_member()
        other = add_member("Alice")
        assert is_master(master.id) is True
        assert is_master(other.id) is False


# ---------------------------------------------------------------------------
# add / rename / delete
# ---------------------------------------------------------------------------


class TestMemberCrud:
    def test_add_member_returns_new_id(self, tmp_config_file):
        alice = add_member("Alice")
        bob = add_member("Bob")
        assert alice.id != bob.id
        assert bob.id > alice.id
        assert bob.role == ROLE_SECONDARY

    def test_add_member_trims_name(self, tmp_config_file):
        m = add_member("   Claire   ")
        assert m.name == "Claire"

    def test_add_member_empty_name_defaults(self, tmp_config_file):
        m = add_member("")
        assert m.name.startswith("Member ")

    def test_rename_member(self, tmp_config_file):
        m = add_member("Bob")
        rename_member(m.id, "Robert")
        assert get_member(m.id).name == "Robert"

    def test_rename_missing_is_silent(self, tmp_config_file):
        rename_member(9999, "Ghost")  # does not raise

    def test_delete_member(self, tmp_config_file):
        m = add_member("Bob")
        assert delete_member(m.id) is True
        assert get_member(m.id) is None

    def test_delete_last_member_blocked(self, tmp_config_file):
        master = get_master_member()
        assert delete_member(master.id) is False
        # Household still has its one member.
        assert len(list_members()) == 1

    def test_delete_master_reassigns(self, tmp_config_file):
        master = get_master_member()
        other = add_member("Alice")
        assert delete_member(master.id) is True

        # The remaining member is promoted to master.
        assert get_master_member().id == other.id

    def test_delete_missing_returns_false(self, tmp_config_file):
        assert delete_member(9999) is False


# ---------------------------------------------------------------------------
# set_active / set_primary
# ---------------------------------------------------------------------------


class TestActivePrimary:
    def test_set_active_member_id(self, tmp_config_file):
        other = add_member("Alice")
        set_active_member_id(other.id)
        assert get_active_member().id == other.id

    def test_set_active_ignores_unknown_id(self, tmp_config_file):
        original = get_active_member().id
        set_active_member_id(9999)
        assert get_active_member().id == original

    def test_set_primary_member_id(self, tmp_config_file):
        other = add_member("Alice")
        set_primary_member_id(other.id)
        assert get_primary_member().id == other.id


# ---------------------------------------------------------------------------
# Member profile getters / setters
# ---------------------------------------------------------------------------


class TestMemberProfiles:
    def test_get_empty_profile_returns_dict(self, tmp_config_file):
        master = get_master_member()
        prof = get_member_profile(master.id)
        assert isinstance(prof, dict)

    def test_save_profile_round_trips(self, tmp_config_file):
        master = get_master_member()
        save_member_profile(master.id, {"allergies": ["peanuts"], "diet": "vegan"})

        prof = get_member_profile(master.id)
        assert "peanuts" in prof["allergies"]
        assert prof["diet"] == "vegan"

    def test_save_profile_unknown_member_is_silent(self, tmp_config_file):
        save_member_profile(9999, {"allergies": ["peanuts"]})
        assert get_member_profile(9999) == {}


# ---------------------------------------------------------------------------
# get_household_allergies (union across members)
# ---------------------------------------------------------------------------


class TestHouseholdAllergies:
    def test_unions_across_members(self, tmp_config_file):
        master = get_master_member()
        save_member_profile(master.id, {"allergies": ["peanuts"]})

        alice = add_member("Alice")
        save_member_profile(alice.id, {"allergies": ["shellfish", "eggs"]})

        allergies = get_household_allergies()
        assert allergies == {"peanuts", "shellfish", "eggs"}

    def test_empty_when_none_set(self, tmp_config_file):
        assert get_household_allergies() == set()


# ---------------------------------------------------------------------------
# get_user_profile (flat master view used by MealSuggestionService)
# ---------------------------------------------------------------------------


class TestGetUserProfile:
    def test_flattens_master_profile(self, tmp_config_file):
        master = get_master_member()
        save_member_profile(
            master.id,
            {
                "allergies": ["peanuts"],
                "hard_excludes": ["liver"],
                "soft_excludes": ["tofu"],
                "excluded_proteins": ["pork"],
                "preferred_protein_weights": {"chicken": 2.0, "beef": 0.5},
            },
        )

        prof = get_user_profile()
        assert "peanuts" in prof["allergies"]
        # avoid_ingredients = hard + soft
        assert "liver" in prof["avoid_ingredients"]
        assert "tofu" in prof["avoid_ingredients"]
        # prefer_meats = weights > 1.0
        assert "chicken" in prof["prefer_meats"]
        assert "beef" not in prof["prefer_meats"]
        # avoid_meats = excluded_proteins
        assert "pork" in prof["avoid_meats"]

    def test_restrictions_populated_from_diet_toggles(self, tmp_config_file):
        master = get_master_member()
        save_member_profile(master.id, {"eats_meat": False, "eats_fish": False})

        prof = get_user_profile()
        assert "no_meat" in prof["restrictions"]
        assert "no_fish" in prof["restrictions"]


# ---------------------------------------------------------------------------
# Postal code + store priority
# ---------------------------------------------------------------------------


class TestPostalAndStorePriority:
    def test_default_postal_is_empty(self, tmp_config_file):
        assert get_postal_code() == ""

    def test_postal_round_trip(self, tmp_config_file):
        cfg = load_config()
        cfg.postal_code = "V3J 0P6"
        save_config(cfg)
        config_store._config_cache = None
        config_store._config_mtime = None
        assert get_postal_code() == "V3J 0P6"

    def test_store_priority_sorted_by_value_desc(self, tmp_config_file):
        cfg = load_config()
        cfg.store_priority = {"A": 1, "B": 10, "C": 5}
        save_config(cfg)
        config_store._config_cache = None
        config_store._config_mtime = None
        assert get_store_priority() == ["B", "C", "A"]

    def test_empty_store_priority(self, tmp_config_file):
        assert get_store_priority() == []


# ---------------------------------------------------------------------------
# cache_get / cache_set
# ---------------------------------------------------------------------------


class TestDealsCache:
    def test_set_then_get(self, tmp_config_file):
        cache_set("k", {"a": 1})
        assert cache_get("k") == {"a": 1}

    def test_miss_returns_none(self, tmp_config_file):
        assert cache_get("missing") is None

    def test_expiry_returns_none(self, tmp_config_file, monkeypatch):
        cache_set("k", "v")

        # Force the stored entry to look ancient.
        cache_path = config_store._CACHE_FILE
        import json as _json
        data = _json.loads(cache_path.read_text(encoding="utf-8"))
        data["k"]["stored_at"] = time.time() - (8 * 86400)  # 8 days old
        cache_path.write_text(_json.dumps(data), encoding="utf-8")

        assert cache_get("k", max_age_days=7) is None

    def test_malformed_cache_file_returns_none(self, tmp_config_file):
        config_store._CACHE_FILE.write_text("not json", encoding="utf-8")
        assert cache_get("k") is None


# ---------------------------------------------------------------------------
# FINDING: reset_secondary_member_to_household_baseline is defined in BOTH
# config_store.py AND preferences_service.py
# ---------------------------------------------------------------------------


class TestDuplicateResetDefinitionFinding:
    """
    FINDING: the function `reset_secondary_member_to_household_baseline`
    exists in two modules with slightly different signatures/semantics:
      - config_store.reset_secondary_member_to_household_baseline(id) -> bool
      - preferences_service.reset_secondary_member_to_household_baseline(id) -> None
    Callers get different behaviour depending on which import wins. Tests
    lock in both until the duplication is resolved.
    """

    def test_config_store_version_returns_bool(self, tmp_config_file):
        alice = add_member("Alice")
        save_member_profile(alice.id, {"allergies": ["peanuts"], "soft_excludes": ["tofu"]})

        result = config_store.reset_secondary_member_to_household_baseline(alice.id)
        assert result is True

        prof = get_member_profile(alice.id)
        assert prof["soft_excludes"] == []
        assert "peanuts" in prof["allergies"]  # allergies preserved

    def test_config_store_version_refuses_master(self, tmp_config_file):
        master = get_master_member()
        result = config_store.reset_secondary_member_to_household_baseline(master.id)
        assert result is False

    def test_preferences_service_version_returns_none(self, tmp_config_file):
        """The preferences_service version returns None (implicit)."""
        from Grocery_Sense.services import preferences_service

        alice = add_member("Alice")
        save_member_profile(alice.id, {"soft_excludes": ["tofu"]})
        result = preferences_service.reset_secondary_member_to_household_baseline(alice.id)
        assert result is None
