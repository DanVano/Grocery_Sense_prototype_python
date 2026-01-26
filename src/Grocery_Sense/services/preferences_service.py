from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from Grocery_Sense import config_store

# ---------------------------------------------------------------------------
# Canonical option lists (driven by config_store defaults)
# ---------------------------------------------------------------------------

def _canon_list(values: Any) -> List[str]:
    if isinstance(values, list):
        out: List[str] = []
        for v in values:
            s = str(v).strip().lower()
            if s:
                out.append(s)
        return out
    if isinstance(values, str):
        return [v.strip().lower() for v in values.split(",") if v.strip()]
    return []


PROTEINS: List[str] = _canon_list(getattr(config_store, "DEFAULT_PROTEINS", []))
OILS: List[str] = _canon_list(getattr(config_store, "DEFAULT_OILS", []))
CUISINES: List[str] = _canon_list(getattr(config_store, "DEFAULT_CUISINES", []))

# For UI (wizard/preferences screen). Key = stored token, Value = label.
STYLE_TAGS: List[Tuple[str, str]] = [
    ("soups_stews", "Soups & stews"),
    ("saucy", "Saucy meals"),
    ("leafy_greens", "Leafy greens"),
    ("quick_weeknight", "Quick weeknight"),
    ("meal_prep", "Meal prep-friendly"),
]

# Protein group tokens MUST match config_store.DEFAULT_PROTEINS
MEAT_PROTEINS: Set[str] = {"chicken", "beef", "pork", "lamb", "turkey"}
SEAFOOD_PROTEINS: Set[str] = {"fish", "shellfish"}
PLANT_PROTEINS: Set[str] = {"plant proteins"}

# Keys considered “secondary overrides”.
# Reset-to-baseline should clear these but preserve allergies.
_SECONDARY_OVERRIDE_KEYS: Set[str] = {
    "diet_flags",  # optional (future)
    "hard_excludes",  # secondary 'hard' becomes soft downstream, but still safe to clear if present
    "soft_excludes",
    "excluded_proteins",
    "preferred_protein_weights",
    "favorite_cuisines",
    "oils_allowed",
    "spice_level",
    "meal_styles",
    # flags exist in profiles but we treat baseline as master anyway; if a secondary overrides them later,
    # they should be considered overrides too:
    "eats_meat",
    "eats_fish",
    "eats_dairy",
    "eats_eggs",
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class EffectivePreferences:
    """
    Effective household preferences used for filtering/ranking.

    FINAL RULES:
    - Household baseline == master profile (master defaults)
    - Allergies (any member) => household hard exclude
    - Master hard_excludes => household hard exclude
    - Secondary excludes => soft exclude (keep item, later star it)
    - Master protein exclusions => hard; secondary protein exclusions => soft
    - Protein weights/cuisines/oils come from baseline (master)
    - oils_allowed: if empty => treat as "all allowed"
    """
    hard_excludes: Set[str] = field(default_factory=set)

    soft_excludes: Dict[str, List[str]] = field(default_factory=dict)

    excluded_proteins_hard: Set[str] = field(default_factory=set)
    excluded_proteins_soft: Dict[str, List[str]] = field(default_factory=dict)

    protein_weights: Dict[str, float] = field(default_factory=dict)

    cuisines_preferred: Set[str] = field(default_factory=set)
    oils_allowed: Set[str] = field(default_factory=set)

    def is_hard_excluded(self, ingredient: str) -> bool:
        key = (ingredient or "").strip().lower()
        return bool(key) and key in self.hard_excludes

    def soft_excluders(self, ingredient: str) -> List[str]:
        key = (ingredient or "").strip().lower()
        return list(self.soft_excludes.get(key, []))

    def soft_protein_excluders(self, protein: str) -> List[str]:
        key = (protein or "").strip().lower()
        return list(self.excluded_proteins_soft.get(key, []))

    def protein_weight(self, protein: str) -> float:
        key = (protein or "").strip().lower()
        try:
            return float(self.protein_weights.get(key, 1.0))
        except Exception:
            return 1.0

    def is_protein_hard_excluded(self, protein: str) -> bool:
        key = (protein or "").strip().lower()
        return bool(key) and key in self.excluded_proteins_hard

    def is_oil_allowed(self, oil: str) -> bool:
        key = (oil or "").strip().lower()
        if not key:
            return True
        # If baseline oils_allowed is empty, treat as "all allowed"
        if not self.oils_allowed:
            return True
        return key in self.oils_allowed


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def _norm_list(values: Any) -> List[str]:
    return _canon_list(values)


def _norm_dict(values: Any) -> Dict[str, Any]:
    if isinstance(values, dict):
        return values
    return {}


def _add_soft(m: EffectivePreferences, key: str, member_name: str) -> None:
    k = (key or "").strip().lower()
    if not k:
        return
    m.soft_excludes.setdefault(k, [])
    if member_name not in m.soft_excludes[k]:
        m.soft_excludes[k].append(member_name)


def _add_soft_protein(m: EffectivePreferences, key: str, member_name: str) -> None:
    k = (key or "").strip().lower()
    if not k:
        return
    m.excluded_proteins_soft.setdefault(k, [])
    if member_name not in m.excluded_proteins_soft[k]:
        m.excluded_proteins_soft[k].append(member_name)


def _profile(mem) -> Dict[str, Any]:
    try:
        p = mem.profile or {}
        return p if isinstance(p, dict) else {}
    except Exception:
        return {}


def _sanitize_bool(v: Any, default: bool = True) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(int(v))
    if isinstance(v, str):
        t = v.strip().lower()
        if t in {"1", "true", "yes", "y", "on"}:
            return True
        if t in {"0", "false", "no", "n", "off"}:
            return False
    return default


# ---------------------------------------------------------------------------
# Role / baseline helpers (for wizard screen 1)
# ---------------------------------------------------------------------------

def household_baseline_member_id() -> int:
    """
    Household baseline == master profile, so baseline editing = editing master member.
    """
    return config_store.get_master_member().id


def can_edit_member(editor_member_id: int, target_member_id: int) -> bool:
    """
    Wizard rule:
    - Master can edit anyone
    - Secondary can only edit themselves
    """
    editor = config_store.get_member(editor_member_id)
    if not editor:
        return False
    if editor.role == getattr(config_store, "ROLE_MASTER", "master"):
        return True
    return int(editor_member_id) == int(target_member_id)


def list_editable_member_ids(editor_member_id: int) -> List[int]:
    editor = config_store.get_member(editor_member_id)
    if not editor:
        return []
    if editor.role == getattr(config_store, "ROLE_MASTER", "master"):
        return [m.id for m in config_store.list_members()]
    return [editor.id]


# ---------------------------------------------------------------------------
# Baseline + effective merging (household = master defaults)
# ---------------------------------------------------------------------------

def compute_effective_preferences() -> EffectivePreferences:
    """
    Merge household preferences.

    FINAL RULES:
    - Household baseline == master profile (master defaults)
    - Allergies (any member) => household hard exclude
    - Master hard_excludes => household hard exclude
    - Secondary excludes => soft exclude (keep item, later star it)
    - Master protein exclusions => hard; secondary protein exclusions => soft
    - Protein weights/cuisines/oils come from baseline (master)
    - eats_meat/eats_fish flags (if used) are respected
    """
    cfg = config_store.load_config()
    members = list(getattr(cfg.household, "members", []) or [])
    master = config_store.get_master_member()
    master_name = getattr(master, "name", "Master")

    eff = EffectivePreferences()

    # 1) Allergies from any member => household hard exclude
    for mem in members:
        prof = _profile(mem)
        for a in _norm_list(prof.get("allergies", [])):
            eff.hard_excludes.add(a)

    # 2) Baseline hard excludes (master)
    mprof = _profile(master)
    for x in _norm_list(mprof.get("hard_excludes", [])):
        eff.hard_excludes.add(x)

    # 3) Baseline protein exclusions (master) + baseline flags
    for p in _norm_list(mprof.get("excluded_proteins", [])):
        eff.excluded_proteins_hard.add(p)

    eats_meat = _sanitize_bool(mprof.get("eats_meat", True), True)
    eats_fish = _sanitize_bool(mprof.get("eats_fish", True), True)
    if not eats_meat:
        eff.excluded_proteins_hard.update(MEAT_PROTEINS)
        eff.excluded_proteins_hard.discard("plant proteins")
    if not eats_fish:
        eff.excluded_proteins_hard.update(SEAFOOD_PROTEINS)

    # 4) Baseline protein weights
    weights = _norm_dict(mprof.get("preferred_protein_weights", {}) or {})
    for k, v in weights.items():
        key = str(k).strip().lower()
        if not key:
            continue
        try:
            eff.protein_weights[key] = float(v)
        except Exception:
            eff.protein_weights[key] = 1.0

    # 5) Baseline cuisines
    for c in _norm_list(mprof.get("favorite_cuisines", [])):
        eff.cuisines_preferred.add(c)

    # 6) Baseline oils allowed
    oils_allowed = _norm_list(mprof.get("oils_allowed", []))
    if oils_allowed:
        eff.oils_allowed.update(oils_allowed)
    else:
        # empty baseline => all allowed (we keep a filled set for UI friendliness)
        eff.oils_allowed.update([o for o in OILS if o])

    # 7) Soft excludes:
    # - master soft_excludes are tracked (not starred by default)
    for x in _norm_list(mprof.get("soft_excludes", [])):
        _add_soft(eff, x, master_name)

    # - secondary soft excludes (+ any legacy hard_excludes treated as soft)
    # - secondary excluded_proteins treated as soft protein excludes
    for mem in members:
        # skip master
        try:
            if int(mem.id) == int(master.id):
                continue
        except Exception:
            if getattr(mem, "name", "") == master_name:
                continue

        prof = _profile(mem)
        mem_name = getattr(mem, "name", "Member")

        # ingredient excludes
        for x in _norm_list(prof.get("soft_excludes", [])) + _norm_list(prof.get("hard_excludes", [])):
            _add_soft(eff, x, mem_name)

        # protein excludes
        for p in _norm_list(prof.get("excluded_proteins", [])):
            _add_soft_protein(eff, p, mem_name)

        # flags -> protein groups (soft)
        se_meat = _sanitize_bool(prof.get("eats_meat", True), True)
        se_fish = _sanitize_bool(prof.get("eats_fish", True), True)
        if not se_meat:
            for p in MEAT_PROTEINS:
                _add_soft_protein(eff, p, mem_name)
        if not se_fish:
            for p in SEAFOOD_PROTEINS:
                _add_soft_protein(eff, p, mem_name)

    return eff


# ---------------------------------------------------------------------------
# Star/annotation helpers (for list + recommendation UI)
# ---------------------------------------------------------------------------

def get_star_excluders(name: str, eff: Optional[EffectivePreferences] = None) -> List[str]:
    """
    Returns SECONDARY members who caused the '*' marker for this ingredient.
    """
    if eff is None:
        eff = compute_effective_preferences()

    key = (name or "").strip().lower()
    if not key or key in eff.hard_excludes:
        return []

    excluders = eff.soft_excluders(key)
    master_name = getattr(config_store.get_master_member(), "name", "Master")
    return [n for n in excluders if n != master_name]


def annotate_name_with_star(name: str, eff: Optional[EffectivePreferences] = None) -> str:
    """
    Adds '*' if an ingredient is soft-excluded by at least one SECONDARY member.
    """
    excluders = get_star_excluders(name, eff=eff)
    return f"{name}*" if excluders else name


def annotate_protein_with_star(protein: str, eff: Optional[EffectivePreferences] = None) -> str:
    """
    Adds '*' if a protein is soft-excluded by at least one SECONDARY member.
    """
    if eff is None:
        eff = compute_effective_preferences()

    key = (protein or "").strip().lower()
    if not key or key in eff.excluded_proteins_hard:
        return protein

    excluders = eff.soft_protein_excluders(key)
    master_name = getattr(config_store.get_master_member(), "name", "Master")
    if any(n != master_name for n in excluders):
        return f"{protein}*"
    return protein


# ---------------------------------------------------------------------------
# Wizard/Screen helpers: baseline + member effective edit state
# ---------------------------------------------------------------------------

def get_household_baseline_profile() -> Dict[str, Any]:
    """
    Returns the master profile dict (baseline) with safe defaults applied where useful.
    """
    master = config_store.get_master_member()
    prof = dict(_profile(master))

    # Ensure lists exist + normalized
    for k in ("hard_excludes", "soft_excludes", "excluded_proteins", "favorite_cuisines", "meal_styles", "allergies"):
        prof[k] = _norm_list(prof.get(k, []))

    # Ensure weights are dict
    if not isinstance(prof.get("preferred_protein_weights", {}), dict):
        prof["preferred_protein_weights"] = {}

    # Ensure oils_allowed has a default for UI (all oils if unset)
    if not _norm_list(prof.get("oils_allowed", [])):
        prof["oils_allowed"] = [o for o in OILS if o]
    else:
        prof["oils_allowed"] = _norm_list(prof.get("oils_allowed", []))

    # Ensure flags exist (default True)
    prof["eats_meat"] = _sanitize_bool(prof.get("eats_meat", True), True)
    prof["eats_fish"] = _sanitize_bool(prof.get("eats_fish", True), True)
    prof["eats_dairy"] = _sanitize_bool(prof.get("eats_dairy", True), True)
    prof["eats_eggs"] = _sanitize_bool(prof.get("eats_eggs", True), True)

    return prof


def get_member_profile(member_id: int) -> Dict[str, Any]:
    """
    Returns raw member profile (stored overrides) as a dict.
    """
    cfg = config_store.load_config()
    mem = None
    for m in list(getattr(cfg.household, "members", []) or []):
        try:
            if int(m.id) == int(member_id):
                mem = m
                break
        except Exception:
            continue
    if not mem:
        return {}

    prof = dict(_profile(mem))

    for k in ("allergies", "hard_excludes", "soft_excludes", "excluded_proteins", "favorite_cuisines", "meal_styles"):
        if k in prof:
            prof[k] = _norm_list(prof.get(k, []))

    if "preferred_protein_weights" in prof and not isinstance(prof.get("preferred_protein_weights"), dict):
        prof["preferred_protein_weights"] = {}

    # flags (if present)
    if "eats_meat" in prof:
        prof["eats_meat"] = _sanitize_bool(prof.get("eats_meat", True), True)
    if "eats_fish" in prof:
        prof["eats_fish"] = _sanitize_bool(prof.get("eats_fish", True), True)

    return prof


def get_effective_edit_state_for_member(member_id: int) -> Dict[str, Any]:
    """
    Dict used to pre-fill wizard/preferences UI for a member:
    - baseline selections (from household baseline)
    - with member overrides applied for display

    IMPORTANT: This is UI convenience, not filtering logic.
    """
    baseline = get_household_baseline_profile()
    member = get_member_profile(member_id)

    effective: Dict[str, Any] = {}

    # Flags: member override else baseline
    effective["eats_meat"] = _sanitize_bool(member.get("eats_meat", baseline.get("eats_meat", True)), True)
    effective["eats_fish"] = _sanitize_bool(member.get("eats_fish", baseline.get("eats_fish", True)), True)

    # Baseline excluded proteins (hard household baseline)
    baseline_excl = set(_norm_list(baseline.get("excluded_proteins", [])))

    # Baseline allowed proteins = PROTEINS - baseline excluded
    allowed_proteins = [p for p in PROTEINS if p and p not in baseline_excl]

    # Member excluded_proteins for UI (unchecked)
    member_excl = set(_norm_list(member.get("excluded_proteins", [])))
    proteins_selected = [p for p in allowed_proteins if p not in member_excl]

    # Apply member flags for UI only: if member says “no meat/fish”, uncheck those groups
    if not effective["eats_meat"]:
        proteins_selected = [p for p in proteins_selected if p not in MEAT_PROTEINS]
    if not effective["eats_fish"]:
        proteins_selected = [p for p in proteins_selected if p not in SEAFOOD_PROTEINS]

    effective["proteins_selected"] = proteins_selected

    # Protein weights: baseline weights; member may have their own (optional override for their view)
    weights = dict(_norm_dict(baseline.get("preferred_protein_weights", {}) or {}))
    member_weights = _norm_dict(member.get("preferred_protein_weights", {}) or {})
    weights.update(member_weights)
    effective["preferred_protein_weights"] = weights

    # Oils: baseline for household; member may have their own selection for their view
    oils = _norm_list(member.get("oils_allowed", [])) or _norm_list(baseline.get("oils_allowed", []))
    effective["oils_allowed"] = oils

    # Cuisines: baseline + member favorites (additive for UI)
    cuisines = set(_norm_list(baseline.get("favorite_cuisines", [])))
    cuisines.update(_norm_list(member.get("favorite_cuisines", [])))
    effective["favorite_cuisines"] = sorted(cuisines)

    # Spice level: member override else baseline
    effective["spice_level"] = member.get("spice_level", baseline.get("spice_level", "medium"))

    # Meal styles: baseline + member (additive)
    styles = set(_norm_list(baseline.get("meal_styles", [])))
    styles.update(_norm_list(member.get("meal_styles", [])))
    effective["meal_styles"] = sorted(styles)

    # Allergies (member-specific list, but household hard exclude will be built from all members)
    effective["allergies"] = _norm_list(member.get("allergies", []))

    # Ingredient excludes for UI:
    effective["household_hard_excludes"] = sorted(set(_norm_list(baseline.get("hard_excludes", []))))

    # member soft excludes: include legacy hard_excludes too (treated as soft for secondary)
    effective["member_soft_excludes"] = sorted(
        set(_norm_list(member.get("soft_excludes", [])) + _norm_list(member.get("hard_excludes", [])))
    )

    # diet_flags: optional (future)
    effective["diet_flags"] = member.get("diet_flags", baseline.get("diet_flags", {}))

    return effective


# ---------------------------------------------------------------------------
# Reset helper (for "Reset to household baseline" button)
# ---------------------------------------------------------------------------

def reset_secondary_member_to_household_baseline(member_id: int) -> bool:
    """
    Clears SECONDARY member overrides back to baseline, preserving allergies.

    Delegates to config_store.reset_secondary_member_to_household_baseline if present.
    Returns True if reset occurred.
    """
    fn = getattr(config_store, "reset_secondary_member_to_household_baseline", None)
    if callable(fn):
        return bool(fn(member_id))

    # Fallback (older config_store)
    cfg = config_store.load_config()
    master = config_store.get_master_member()

    try:
        if int(member_id) == int(master.id):
            return False
    except Exception:
        return False

    mem = None
    for m in list(getattr(cfg.household, "members", []) or []):
        try:
            if int(m.id) == int(member_id):
                mem = m
                break
        except Exception:
            continue
    if not mem:
        return False

    if getattr(mem, "role", "secondary") != getattr(config_store, "ROLE_SECONDARY", "secondary"):
        return False

    prof = _profile(mem)
    allergies = _norm_list(prof.get("allergies", []))

    for k in list(_SECONDARY_OVERRIDE_KEYS):
        prof.pop(k, None)

    if allergies:
        prof["allergies"] = allergies

    mem.profile = prof
    config_store.save_config(cfg)
    return True


# ---------------------------------------------------------------------------
# Convenience: protein lists for UI grouping
# ---------------------------------------------------------------------------

def protein_groups() -> Dict[str, List[str]]:
    """
    For wizard UI grouping, strictly matching your canonical protein list.
    """
    canon = set([p.strip().lower() for p in PROTEINS if str(p).strip()])

    meat = [p for p in sorted(MEAT_PROTEINS) if p in canon]
    seafood = [p for p in sorted(SEAFOOD_PROTEINS) if p in canon]
    plant = [p for p in sorted(PLANT_PROTEINS) if p in canon]

    return {
        "meat": meat,
        "seafood": seafood,
        "plant": plant,
    }
