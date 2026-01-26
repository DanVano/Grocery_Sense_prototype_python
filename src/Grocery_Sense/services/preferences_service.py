"""
Grocery_Sense.services.preferences_service

Household Preferences (Master vs Secondary) — source of truth for:
- Effective household filtering/ranking
- Soft exclude '*' / strong soft exclude '**' annotations
- Secondary reset-to-household-baseline behavior
- UI helper validation (prevent redundant soft-excludes when already hard-excluded)

Key Rules (v1):
- Household baseline == master profile
- Allergies (any member) => hard exclude household-wide
- Master hard_excludes => hard exclude household-wide
- Secondary excludes => soft exclude (keep item, later star it)
- Master protein exclusions => hard; secondary protein exclusions => soft
- Strong soft exclude if many members soft-exclude something (3/5 example)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from Grocery_Sense import config_store

# ---------------------------------------------------------------------------
# Canonical option lists (driven by config_store defaults)
# ---------------------------------------------------------------------------

PROTEINS: List[str] = list(getattr(config_store, "DEFAULT_PROTEINS", []))
OILS: List[str] = list(getattr(config_store, "DEFAULT_OILS", []))
CUISINES: List[str] = list(getattr(config_store, "DEFAULT_CUISINES", []))

# For the UI (wizard/preferences screen). Key = stored token, Value = label.
STYLE_TAGS: List[Tuple[str, str]] = [
    ("soups_stews", "Soups & stews"),
    ("saucy", "Saucy meals"),
    ("leafy_greens", "Leafy greens"),
    ("quick_weeknight", "Quick weeknight"),
    ("meal_prep", "Meal prep-friendly"),
]

# Protein groups for branching (eat meat? fish/seafood?)
MEAT_PROTEINS: Set[str] = {"chicken", "beef", "pork", "lamb", "turkey"}
SEAFOOD_PROTEINS: Set[str] = {"fish", "shellfish"}
PLANT_PROTEINS: Set[str] = {"plant proteins", "plant_proteins"}

# Keys we consider "preference overrides" for secondaries.
# Reset-to-baseline should clear these but keep allergies.
_SECONDARY_OVERRIDE_KEYS: Set[str] = {
    "diet_flags",
    "hard_excludes",  # may exist in older configs; secondary hard treated as soft
    "soft_excludes",
    "excluded_proteins",
    "preferred_protein_weights",
    "favorite_cuisines",
    "oils_allowed",
    "spice_level",
    "styles",  # canonical key used by config_store + preferences_window
    "meal_styles",  # legacy key (older code); cleared too if present
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class EffectivePreferences:
    """
    Effective household preferences used for filtering/ranking.

    hard_excludes:
      - allergies (any member)
      - master hard_excludes

    soft_excludes:
      - master soft_excludes (tracked, but NOT starred)
      - secondary soft_excludes + any secondary hard_excludes (treated as soft)

    strong_soft_excludes:
      - subset of soft_excludes where many SECONDARY members agree

    excluded_proteins_hard:
      - master excluded_proteins

    excluded_proteins_soft:
      - secondary excluded_proteins (tracked & starred)

    strong_soft_proteins:
      - subset where many SECONDARY members agree

    protein_weights/cuisines/oils:
      - baseline (master)
    """
    hard_excludes: Set[str] = field(default_factory=set)

    soft_excludes: Dict[str, List[str]] = field(default_factory=dict)  # ingredient -> member names
    soft_exclude_counts: Dict[str, int] = field(default_factory=dict)  # ingredient -> secondary count
    strong_soft_excludes: Set[str] = field(default_factory=set)

    excluded_proteins_hard: Set[str] = field(default_factory=set)
    excluded_proteins_soft: Dict[str, List[str]] = field(default_factory=dict)  # protein -> member names
    soft_protein_exclude_counts: Dict[str, int] = field(default_factory=dict)  # protein -> secondary count
    strong_soft_proteins: Set[str] = field(default_factory=set)

    protein_weights: Dict[str, float] = field(default_factory=dict)
    cuisines_preferred: Set[str] = field(default_factory=set)
    oils_allowed: Set[str] = field(default_factory=set)  # empty means "unrestricted"

    def is_hard_excluded(self, ingredient: str) -> bool:
        key = _norm_token(ingredient)
        return bool(key) and key in self.hard_excludes

    def soft_excluders(self, ingredient: str) -> List[str]:
        key = _norm_token(ingredient)
        return list(self.soft_excludes.get(key, []))

    def secondary_soft_excluder_count(self, ingredient: str) -> int:
        key = _norm_token(ingredient)
        return int(self.soft_exclude_counts.get(key, 0))

    def is_strong_soft_excluded(self, ingredient: str) -> bool:
        key = _norm_token(ingredient)
        return bool(key) and key in self.strong_soft_excludes

    def soft_protein_excluders(self, protein: str) -> List[str]:
        key = _norm_token(protein)
        return list(self.excluded_proteins_soft.get(key, []))

    def secondary_soft_protein_excluder_count(self, protein: str) -> int:
        key = _norm_token(protein)
        return int(self.soft_protein_exclude_counts.get(key, 0))

    def is_strong_soft_protein_excluded(self, protein: str) -> bool:
        key = _norm_token(protein)
        return bool(key) and key in self.strong_soft_proteins

    def protein_weight(self, protein: str) -> float:
        key = _norm_token(protein)
        try:
            return float(self.protein_weights.get(key, 1.0))
        except Exception:
            return 1.0

    def is_oil_allowed(self, oil: str) -> bool:
        key = _norm_token(oil)
        if not key:
            return True
        # empty oils_allowed => unrestricted
        if not self.oils_allowed:
            return True
        return key in self.oils_allowed


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def _norm_token(value: Any) -> str:
    return str(value or "").strip().lower()


def _norm_list(values: Any) -> List[str]:
    if isinstance(values, list):
        out: List[str] = []
        for v in values:
            s = _norm_token(v)
            if s:
                out.append(s)
        return out
    if isinstance(values, str):
        return [v.strip().lower() for v in values.split(",") if v.strip()]
    return []


def _norm_dict(values: Any) -> Dict[str, Any]:
    if isinstance(values, dict):
        return values
    return {}


def _add_soft(m: EffectivePreferences, key: str, member_name: str) -> None:
    k = _norm_token(key)
    if not k:
        return
    m.soft_excludes.setdefault(k, [])
    if member_name not in m.soft_excludes[k]:
        m.soft_excludes[k].append(member_name)


def _add_soft_protein(m: EffectivePreferences, key: str, member_name: str) -> None:
    k = _norm_token(key)
    if not k:
        return
    m.excluded_proteins_soft.setdefault(k, [])
    if member_name not in m.excluded_proteins_soft[k]:
        m.excluded_proteins_soft[k].append(member_name)


def _get_master_member():
    return config_store.get_master_member()


def _get_members(cfg) -> List[Any]:
    try:
        return list(cfg.household.members or [])
    except Exception:
        return []


def _find_member(cfg, member_id: int) -> Optional[Any]:
    for mem in _get_members(cfg):
        try:
            if int(mem.id) == int(member_id):
                return mem
        except Exception:
            continue
    return None


def _profile(mem) -> Dict[str, Any]:
    try:
        p = mem.profile or {}
        return p if isinstance(p, dict) else {}
    except Exception:
        return {}


def _strong_soft_threshold(n_members: int, s_count: int) -> bool:
    """
    Strong soft exclude rule:
    - N <= 3: strong if S >= 2
    - N == 4: strong if S >= 3
    - N >= 5: strong if S >= 3 and S/N >= 0.60
    """
    n = max(int(n_members), 0)
    s = max(int(s_count), 0)
    if n <= 0:
        return False
    if n <= 3:
        return s >= 2
    if n == 4:
        return s >= 3
    return (s >= 3) and ((s / float(n)) >= 0.60)


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
    - Strong soft excludes computed from SECONDARY consensus
    """
    cfg = config_store.load_config()
    members = _get_members(cfg)
    master = _get_master_member()
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

    # 3) Baseline protein exclusions (master) => hard excluded proteins
    for p in _norm_list(mprof.get("excluded_proteins", [])):
        eff.excluded_proteins_hard.add(p)

    # 4) Baseline protein weights
    weights = _norm_dict(mprof.get("preferred_protein_weights", {}) or {})
    for k, v in weights.items():
        key = _norm_token(k)
        if not key:
            continue
        try:
            eff.protein_weights[key] = float(v)
        except Exception:
            eff.protein_weights[key] = 1.0

    # 5) Baseline cuisines
    for c in _norm_list(mprof.get("favorite_cuisines", [])):
        eff.cuisines_preferred.add(c)

    # 6) Baseline oils allowed (empty => unrestricted)
    for o in _norm_list(mprof.get("oils_allowed", [])):
        eff.oils_allowed.add(o)

    # 7) Soft excludes:
    # - master soft_excludes are tracked but NOT starred by default
    for x in _norm_list(mprof.get("soft_excludes", [])):
        _add_soft(eff, x, master_name)

    # - secondary soft excludes, plus secondary "hard_excludes" treated as soft (redundant)
    for mem in members:
        try:
            if int(mem.id) == int(master.id):
                continue
        except Exception:
            if getattr(mem, "name", "") == master_name:
                continue

        prof = _profile(mem)
        mem_name = getattr(mem, "name", "Member")

        for x in _norm_list(prof.get("soft_excludes", [])) + _norm_list(prof.get("hard_excludes", [])):
            _add_soft(eff, x, mem_name)

        for p in _norm_list(prof.get("excluded_proteins", [])):
            _add_soft_protein(eff, p, mem_name)

    # 8) Strong soft excludes (SECONDARY consensus only)
    n_members = len(members) if members else 0

    for ing, names in eff.soft_excludes.items():
        secondary_names = [n for n in names if n != master_name]
        s_count = len(secondary_names)
        eff.soft_exclude_counts[ing] = s_count
        if _strong_soft_threshold(n_members, s_count):
            eff.strong_soft_excludes.add(ing)

    for prot, names in eff.excluded_proteins_soft.items():
        secondary_names = [n for n in names if n != master_name]
        s_count = len(secondary_names)
        eff.soft_protein_exclude_counts[prot] = s_count
        if _strong_soft_threshold(n_members, s_count):
            eff.strong_soft_proteins.add(prot)

    return eff


# ---------------------------------------------------------------------------
# Annotation helpers (for list + recommendation UI)
# ---------------------------------------------------------------------------

def get_star_excluders(name: str, eff: Optional[EffectivePreferences] = None) -> List[str]:
    """
    Returns SECONDARY members who caused the marker for this ingredient.
    """
    if eff is None:
        eff = compute_effective_preferences()

    key = _norm_token(name)
    if not key or key in eff.hard_excludes:
        return []

    excluders = eff.soft_excluders(key)
    master_name = getattr(_get_master_member(), "name", "Master")
    return [n for n in excluders if n != master_name]


def get_soft_exclude_marker(name: str, eff: Optional[EffectivePreferences] = None) -> str:
    """
    Marker scheme:
      - ""  => no soft exclude
      - "*" => soft-excluded by >=1 secondary
      - "**" => strong soft exclude (many secondaries)
    """
    if eff is None:
        eff = compute_effective_preferences()

    key = _norm_token(name)
    if not key or key in eff.hard_excludes:
        return ""

    if eff.is_strong_soft_excluded(key) and eff.secondary_soft_excluder_count(key) > 0:
        return "**"

    if get_star_excluders(key, eff=eff):
        return "*"

    return ""


def annotate_name_with_star(name: str, eff: Optional[EffectivePreferences] = None) -> str:
    """
    Backward-compatible name (may now append '*' or '**' depending on consensus).
    """
    marker = get_soft_exclude_marker(name, eff=eff)
    return f"{name}{marker}" if marker else name


def annotate_protein_with_star(protein: str, eff: Optional[EffectivePreferences] = None) -> str:
    """
    Backward-compatible name (may now append '*' or '**' depending on consensus).
    """
    if eff is None:
        eff = compute_effective_preferences()

    key = _norm_token(protein)
    if not key or key in eff.excluded_proteins_hard:
        return protein

    master_name = getattr(_get_master_member(), "name", "Master")
    excluders = [n for n in eff.soft_protein_excluders(key) if n != master_name]
    if not excluders:
        return protein

    if eff.is_strong_soft_protein_excluded(key):
        return f"{protein}**"
    return f"{protein}*"


# ---------------------------------------------------------------------------
# Wizard/Screen helpers: baseline + member effective edit state
# ---------------------------------------------------------------------------

def get_household_hard_excludes(eff: Optional[EffectivePreferences] = None) -> List[str]:
    """
    Household hard excludes = allergies(any) + master hard_excludes.
    """
    if eff is None:
        eff = compute_effective_preferences()
    return sorted(set([_norm_token(x) for x in eff.hard_excludes if _norm_token(x)]))


def get_household_baseline_profile() -> Dict[str, Any]:
    """
    Returns the master profile dict (baseline) with safe defaults applied where useful.
    Intended for pre-filling UI.

    NOTE: We keep oils_allowed UI-friendly:
      - empty baseline oils_allowed => show all checked in UI (unrestricted)
    """
    master = _get_master_member()
    prof = dict(_profile(master))

    # Ensure preferred weights exist as dict
    if not isinstance(prof.get("preferred_protein_weights", {}), dict):
        prof["preferred_protein_weights"] = {}

    # Ensure list keys exist
    for k in ("hard_excludes", "soft_excludes", "excluded_proteins", "favorite_cuisines", "styles", "allergies"):
        if k not in prof:
            prof[k] = []
        if not isinstance(prof.get(k), list):
            prof[k] = _norm_list(prof.get(k, []))

    # Legacy: meal_styles -> styles
    if "meal_styles" in prof and not prof.get("styles"):
        prof["styles"] = _norm_list(prof.get("meal_styles", []))

    # UI convenience: if oils not set, show all as checked
    if not _norm_list(prof.get("oils_allowed", [])):
        prof["oils_allowed"] = [o.strip().lower() for o in OILS if str(o).strip()]
    else:
        prof["oils_allowed"] = _norm_list(prof.get("oils_allowed", []))

    # spice_level is optional; leave as-is
    return prof


def get_member_profile(member_id: int) -> Dict[str, Any]:
    """
    Returns raw member profile (stored overrides) as a dict.
    """
    cfg = config_store.load_config()
    mem = _find_member(cfg, member_id)
    if not mem:
        return {}

    prof = dict(_profile(mem))

    # Legacy: meal_styles -> styles
    if "meal_styles" in prof and "styles" not in prof:
        prof["styles"] = prof.get("meal_styles", [])

    # normalize expected list keys
    for k in ("allergies", "hard_excludes", "soft_excludes", "excluded_proteins", "favorite_cuisines", "styles"):
        if k in prof:
            prof[k] = _norm_list(prof.get(k, []))

    if "preferred_protein_weights" in prof and not isinstance(prof.get("preferred_protein_weights"), dict):
        prof["preferred_protein_weights"] = {}

    if "oils_allowed" in prof:
        prof["oils_allowed"] = _norm_list(prof.get("oils_allowed", []))

    return prof


def get_effective_edit_state_for_member(member_id: int) -> Dict[str, Any]:
    """
    Returns a dict used to pre-fill wizard/preferences UI for a member:
    - baseline selections (from household baseline)
    - with member overrides applied for display

    IMPORTANT:
    - This is for UI convenience, not for filtering logic.
    """
    baseline = get_household_baseline_profile()
    member = get_member_profile(member_id)

    effective: Dict[str, Any] = {}

    # Proteins: baseline allowed = PROTEINS minus baseline excluded_proteins
    baseline_excl = set(_norm_list(baseline.get("excluded_proteins", [])))
    allowed_proteins = [p for p in _norm_list(PROTEINS) if p and p not in baseline_excl]

    # Apply member excluded_proteins for UI (unchecked)
    member_excl = set(_norm_list(member.get("excluded_proteins", [])))
    effective["proteins_selected"] = [p for p in allowed_proteins if p not in member_excl]

    # Protein weights: baseline weights; member may have their own (optional override for their view)
    weights = dict(_norm_dict(baseline.get("preferred_protein_weights", {}) or {}))
    member_weights = _norm_dict(member.get("preferred_protein_weights", {}) or {})
    weights.update(member_weights)
    effective["preferred_protein_weights"] = weights

    # Oils: member override else baseline UI view
    oils = _norm_list(member.get("oils_allowed", [])) or _norm_list(baseline.get("oils_allowed", []))
    effective["oils_allowed"] = oils

    # Cuisines: baseline + member favorites (additive for UI)
    cuisines = set(_norm_list(baseline.get("favorite_cuisines", [])))
    cuisines.update(_norm_list(member.get("favorite_cuisines", [])))
    effective["favorite_cuisines"] = sorted(cuisines)

    # Spice level: member override else baseline
    effective["spice_level"] = (member.get("spice_level") or baseline.get("spice_level") or "medium")

    # Styles: baseline + member (additive for UI)
    styles = set(_norm_list(baseline.get("styles", [])))
    styles.update(_norm_list(member.get("styles", [])))
    effective["styles"] = sorted(styles)

    # Allergies (member-specific list, but effective prefs uses all members)
    effective["allergies"] = _norm_list(member.get("allergies", []))

    # Excludes for UI:
    effective["household_hard_excludes"] = get_household_hard_excludes()
    effective["member_soft_excludes"] = sorted(
        set(_norm_list(member.get("soft_excludes", [])) + _norm_list(member.get("hard_excludes", [])))
    )

    # diet_flags optional
    effective["diet_flags"] = member.get("diet_flags", baseline.get("diet_flags", {}))

    return effective


# ---------------------------------------------------------------------------
# UI validation helpers (duplicate / redundancy prevention)
# ---------------------------------------------------------------------------

def validate_add_exclude(
    *,
    member_id: int,
    value: str,
    exclude_kind: str,
) -> Tuple[bool, str]:
    """
    Generic validator for UI list add actions.

    exclude_kind:
      - "allergy"
      - "hard_exclude"
      - "soft_exclude"

    Rules:
      - If adding soft exclude as a SECONDARY and value is already household hard-excluded, block (redundant).
      - Always block exact duplicates inside the target list.
      - Normalize tokens (lower/strip).
    """
    cfg = config_store.load_config()
    mem = _find_member(cfg, member_id)
    if not mem:
        return False, "Member not found."

    token = _norm_token(value)
    if not token:
        return False, "Enter a non-empty value."

    role = getattr(mem, "role", config_store.ROLE_SECONDARY)

    prof = _profile(mem)
    allergies = set(_norm_list(prof.get("allergies", [])))
    hard_ex = set(_norm_list(prof.get("hard_excludes", [])))
    soft_ex = set(_norm_list(prof.get("soft_excludes", [])))

    eff = compute_effective_preferences()
    household_hard = set(get_household_hard_excludes(eff))

    kind = (exclude_kind or "").strip().lower()

    if kind == "allergy":
        if token in allergies:
            return False, "Already in allergies."
        return True, ""

    if kind == "hard_exclude":
        # For master, prevent redundancy with allergies (still fine, but noisy)
        if token in allergies:
            return False, "Already hard-excluded via an allergy."
        if token in hard_ex:
            return False, "Already in hard excludes."
        # If it's already household hard (from master hard or allergies), it is redundant
        if token in household_hard:
            return False, "Already hard-excluded at the household level."
        return True, ""

    if kind == "soft_exclude":
        # For secondaries: can't add if already household hard-excluded
        if role != config_store.ROLE_MASTER and token in household_hard:
            return False, "That ingredient is already hard-excluded for the household (master/allergy)."

        if token in soft_ex or token in hard_ex:
            return False, "Already in your excludes."

        # Optional: prevent redundancy if master already soft-excludes it (still allowed, but could be noisy)
        # We'll allow it (since secondary dislike can be useful for '*' attribution).
        return True, ""

    return False, "Unknown exclude type."


# ---------------------------------------------------------------------------
# Reset helper (for "Reset to household baseline" button)
# ---------------------------------------------------------------------------

def reset_secondary_member_to_household_baseline(member_id: int) -> None:
    """
    Clears SECONDARY member overrides back to baseline, while preserving allergies.

    Meant to back the UI button:
      "Reset to household baseline"
    """
    cfg = config_store.load_config()
    master = _get_master_member()

    # Don't allow resetting the master via this function
    try:
        if int(member_id) == int(master.id):
            return
    except Exception:
        pass

    mem = _find_member(cfg, member_id)
    if not mem:
        return

    prof = _profile(mem)
    allergies = _norm_list(prof.get("allergies", []))

    # Remove override keys
    for k in list(_SECONDARY_OVERRIDE_KEYS):
        prof.pop(k, None)

    # Restore allergies
    if allergies:
        prof["allergies"] = allergies
    else:
        prof.pop("allergies", None)

    try:
        mem.profile = prof
    except Exception:
        pass

    config_store.save_config(cfg)


# ---------------------------------------------------------------------------
# Convenience: protein lists for UI grouping
# ---------------------------------------------------------------------------

def protein_groups() -> Dict[str, List[str]]:
    """
    For wizard UI grouping.
    """
    canon = set([_norm_token(p) for p in PROTEINS if _norm_token(p)])
    meat = [p for p in MEAT_PROTEINS if _norm_token(p) in canon]
    seafood = [p for p in SEAFOOD_PROTEINS if _norm_token(p) in canon]
    plant = [p for p in PLANT_PROTEINS if _norm_token(p) in canon]

    return {
        "meat": sorted({p for p in meat}),
        "seafood": sorted({p for p in seafood}),
        "plant": sorted({p for p in plant}),
    }
