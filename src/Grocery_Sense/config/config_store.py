from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

# Expected location:
#   .../src/Grocery_Sense/config_store.py
# parents[1] => .../src
_BASE_DIR = Path(__file__).resolve().parents[1]

_CONFIG_DIR = _BASE_DIR / "config"
_CONFIG_FILE = _CONFIG_DIR / "user_config.json"
_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

PROFILE_VERSION = 3  # bumped because we're formalizing household baseline + reset semantics

ROLE_MASTER = "master"
ROLE_SECONDARY = "secondary"
VALID_ROLES = {ROLE_MASTER, ROLE_SECONDARY}

_VALID_DIETS = {
    "vegan",
    "vegetarian",
    "meat eater",
    "pescatarian",
    "keto",
    "omnivore",
}

# These are used by preferences_service + UI.
DEFAULT_PROTEINS: List[str] = [
    "chicken",
    "beef",
    "pork",
    "lamb",
    "turkey",
    "fish",
    "shellfish",
    "plant proteins",
]

DEFAULT_OILS: List[str] = [
    "olive oil",
    "avocado oil",
    "butter/ghee",
    "coconut oil",
    "vegetable oil",
    "canola oil",
    "soybean oil",
    "sunflower oil",
    "corn oil",
    "grapeseed oil",
    "sesame oil",
    "peanut oil",
]

DEFAULT_CUISINES: List[str] = [
    "european",
    "east asian",
    "southeast asian",
    "south asian",
    "mexican",
    "jamaican",
    "middle eastern",
    "mediterranean",
    "indian",
    "korean",
    "japanese",
    "chinese",
    "thai",
    "vietnamese",
]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class HouseholdMember:
    id: int
    name: str
    role: str = ROLE_SECONDARY
    profile: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Household:
    primary_member_id: int = 1
    active_member_id: int = 1
    members: List[HouseholdMember] = field(default_factory=list)


@dataclass
class UserConfig:
    """
    Top-level config structure stored as JSON at: src/config/user_config.json
    """

    profile_version: int = PROFILE_VERSION

    postal_code: str = ""
    city: str = ""
    country: str = "CA"

    store_priority: Dict[str, int] = field(default_factory=dict)
    favorite_store_ids: List[int] = field(default_factory=list)

    household: Household = field(default_factory=Household)


# ---------------------------------------------------------------------------
# Defaults / validation
# ---------------------------------------------------------------------------

def default_member_profile() -> Dict[str, Any]:
    """
    Canonical profile shape for a household member.

    IMPORTANT: Household baseline == MASTER profile.
    Secondary member profiles store ONLY their overrides + allergies/soft dislikes.
    """
    return {
        # Baseline dietary toggles
        "eats_meat": True,
        "eats_fish": True,   # fish/seafood umbrella
        "eats_dairy": True,
        "eats_eggs": True,

        # Proteins (use excludes + weights)
        "excluded_proteins": [],                 # list[str]
        "preferred_protein_weights": {},         # dict[str,float], default=1.0 if missing

        # Safety
        "allergies": [],                         # ALWAYS hard exclude household-wide when merging

        # Preference filtering
        "hard_excludes": [],                     # master only; secondaries are converted to soft
        "soft_excludes": [],

        # Taste / discovery
        "favorite_cuisines": [],
        "spice_level": "medium",                 # low|medium|high
        "meal_styles": [],                       # e.g. ["soups_stews", "saucy"]

        # Oils used (empty => no restriction)
        "oils_allowed": [],                      # list[str]

        # Legacy compatibility fields
        "diet": "meat eater",
        "favorite_tags": [],
        "price_sensitivity": "medium",           # low|medium|high
    }


def _sanitize_list(values: Any) -> List[str]:
    if isinstance(values, str):
        return [v.strip().lower() for v in values.split(",") if v.strip()]
    if isinstance(values, list):
        out: List[str] = []
        for v in values:
            s = str(v).strip().lower()
            if s:
                out.append(s)
        return out
    return []


def _sanitize_bool(v: Any, default: bool = False) -> bool:
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


def _sanitize_spice_level(v: Any) -> str:
    t = str(v).strip().lower()
    if t in {"low", "mild"}:
        return "low"
    if t in {"high", "hot"}:
        return "high"
    return "medium"


def _sanitize_price_sensitivity(v: Any) -> str:
    t = str(v).strip().lower()
    if t in {"low", "medium", "high"}:
        return t
    return "medium"


def _sanitize_role(role: Any) -> str:
    r = str(role or ROLE_SECONDARY).strip().lower()
    if r not in VALID_ROLES:
        return ROLE_SECONDARY
    return r


def ensure_member_profile_defaults(profile: Dict[str, Any], *, role: str) -> Dict[str, Any]:
    """
    Ensures expected keys exist and are normalized.

    Rule:
    - Secondary "hard_excludes" are downgraded to soft_excludes automatically.
      (Allergies remain as-is and are treated as hard household-wide at merge time.)
    """
    base = default_member_profile()
    merged = base.copy()
    for k, v in (profile or {}).items():
        merged[k] = v

    # Migration / alias handling
    # Older code used "styles" instead of "meal_styles"
    if "styles" in merged and "meal_styles" not in merged:
        merged["meal_styles"] = merged.get("styles")
    # Normalize both
    merged.pop("styles", None)

    merged["eats_meat"] = _sanitize_bool(merged.get("eats_meat", True), True)
    merged["eats_fish"] = _sanitize_bool(merged.get("eats_fish", True), True)
    merged["eats_dairy"] = _sanitize_bool(merged.get("eats_dairy", True), True)
    merged["eats_eggs"] = _sanitize_bool(merged.get("eats_eggs", True), True)

    merged["excluded_proteins"] = _sanitize_list(merged.get("excluded_proteins", []))

    # Normalize weights
    weights_raw = merged.get("preferred_protein_weights", {}) or {}
    weights: Dict[str, float] = {}
    if isinstance(weights_raw, dict):
        for k, v in weights_raw.items():
            key = str(k).strip().lower()
            try:
                w = float(v)
            except Exception:
                w = 1.0
            if w < 0.25:
                w = 0.25
            if w > 3.0:
                w = 3.0
            if key:
                weights[key] = w
    merged["preferred_protein_weights"] = weights

    merged["allergies"] = _sanitize_list(merged.get("allergies", []))
    merged["hard_excludes"] = _sanitize_list(merged.get("hard_excludes", []))
    merged["soft_excludes"] = _sanitize_list(merged.get("soft_excludes", []))

    merged["favorite_cuisines"] = _sanitize_list(merged.get("favorite_cuisines", []))
    merged["spice_level"] = _sanitize_spice_level(merged.get("spice_level", "medium"))
    merged["meal_styles"] = _sanitize_list(merged.get("meal_styles", []))

    merged["oils_allowed"] = _sanitize_list(merged.get("oils_allowed", []))

    # Legacy fields
    diet = str(merged.get("diet", "meat eater")).strip().lower()
    merged["diet"] = diet if diet in _VALID_DIETS else "meat eater"
    merged["favorite_tags"] = _sanitize_list(merged.get("favorite_tags", []))
    merged["price_sensitivity"] = _sanitize_price_sensitivity(merged.get("price_sensitivity", "medium"))

    # Role rule: secondary "hard excludes" behave as soft excludes
    role = _sanitize_role(role)
    if role != ROLE_MASTER:
        if merged["hard_excludes"]:
            merged["soft_excludes"] = sorted(set(merged["soft_excludes"] + merged["hard_excludes"]))
            merged["hard_excludes"] = []

    return merged


def _ensure_household_defaults(h: Household) -> Household:
    """
    Ensures:
    - at least one member exists
    - there is exactly one master (or at least one), tied to primary if possible
    - active/primary ids are valid
    - profiles are normalized
    """
    if not h.members:
        h.members = [
            HouseholdMember(
                id=1,
                name="Primary",
                role=ROLE_MASTER,
                profile=default_member_profile(),
            )
        ]

    # Ensure there is at least one master
    masters = [m for m in h.members if _sanitize_role(m.role) == ROLE_MASTER]
    if not masters:
        # Promote primary if present, else first member
        for m in h.members:
            if m.id == h.primary_member_id:
                m.role = ROLE_MASTER
                break
        else:
            h.members[0].role = ROLE_MASTER

    # Validate primary/active
    ids = {m.id for m in h.members}
    if h.primary_member_id not in ids:
        h.primary_member_id = next(iter(sorted(ids)))
    if h.active_member_id not in ids:
        h.active_member_id = h.primary_member_id

    # Normalize members
    for m in h.members:
        m.role = _sanitize_role(m.role)
        m.profile = ensure_member_profile_defaults(m.profile or {}, role=m.role)

    return h


# ---------------------------------------------------------------------------
# Internal helpers for JSON I/O
# ---------------------------------------------------------------------------

def _read_raw_config() -> Dict[str, Any]:
    if not _CONFIG_FILE.exists():
        return {}
    try:
        with _CONFIG_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_raw_config(data: Dict[str, Any]) -> None:
    with _CONFIG_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)


def _member_from_raw(raw: Dict[str, Any]) -> HouseholdMember:
    try:
        mid = int(raw.get("id", 0) or 0)
    except Exception:
        mid = 0
    name = str(raw.get("name", "") or "Member").strip() or "Member"
    role = _sanitize_role(raw.get("role", ROLE_SECONDARY))
    profile = raw.get("profile", {}) if isinstance(raw.get("profile", {}), dict) else {}
    return HouseholdMember(id=mid, name=name, role=role, profile=profile)


def _household_from_raw(raw: Dict[str, Any]) -> Household:
    try:
        primary = int(raw.get("primary_member_id", 1) or 1)
    except Exception:
        primary = 1
    try:
        active = int(raw.get("active_member_id", primary) or primary)
    except Exception:
        active = primary

    members_raw = raw.get("members", [])
    members: List[HouseholdMember] = []
    if isinstance(members_raw, list):
        for m in members_raw:
            if isinstance(m, dict):
                members.append(_member_from_raw(m))

    return Household(primary_member_id=primary, active_member_id=active, members=members)


def _from_raw_config(raw: Dict[str, Any]) -> UserConfig:
    cfg = UserConfig(
        profile_version=int(raw.get("profile_version", raw.get("version", PROFILE_VERSION)) or PROFILE_VERSION),
        postal_code=str(raw.get("postal_code", "") or ""),
        city=str(raw.get("city", "") or ""),
        country=str(raw.get("country", "") or "CA"),
        store_priority=raw.get("store_priority", {}) or {},
        favorite_store_ids=raw.get("favorite_store_ids", []) or [],
        household=_household_from_raw(raw.get("household", {}) or {}),
    )

    # Migration: older configs may only have a top-level "profile"
    legacy_profile = raw.get("profile")
    if legacy_profile and isinstance(legacy_profile, dict):
        if not cfg.household.members:
            cfg.household.members = [
                HouseholdMember(id=1, name="Primary", role=ROLE_MASTER, profile=legacy_profile)
            ]
            cfg.household.primary_member_id = 1
            cfg.household.active_member_id = 1

    cfg.household = _ensure_household_defaults(cfg.household)

    # Always bump to latest version on load (safe)
    cfg.profile_version = PROFILE_VERSION
    return cfg


def _to_raw_config(cfg: UserConfig) -> Dict[str, Any]:
    return asdict(cfg)


# ---------------------------------------------------------------------------
# Public config API
# ---------------------------------------------------------------------------

def load_config() -> UserConfig:
    raw = _read_raw_config()
    cfg = _from_raw_config(raw)
    return cfg


def save_config(cfg: UserConfig) -> None:
    _write_raw_config(_to_raw_config(cfg))


# ---------------------------------------------------------------------------
# Household / members API
# ---------------------------------------------------------------------------

def list_members() -> List[HouseholdMember]:
    return load_config().household.members


def get_member(member_id: int) -> Optional[HouseholdMember]:
    cfg = load_config()
    for m in cfg.household.members:
        if m.id == member_id:
            return m
    return None


def get_primary_member() -> HouseholdMember:
    cfg = load_config()
    for m in cfg.household.members:
        if m.id == cfg.household.primary_member_id:
            return m
    return cfg.household.members[0]


def get_master_member() -> HouseholdMember:
    cfg = load_config()
    for m in cfg.household.members:
        if m.role == ROLE_MASTER:
            return m
    return get_primary_member()


def get_active_member() -> HouseholdMember:
    cfg = load_config()
    for m in cfg.household.members:
        if m.id == cfg.household.active_member_id:
            return m
    return get_primary_member()


def set_active_member_id(member_id: int) -> None:
    cfg = load_config()
    ids = {m.id for m in cfg.household.members}
    if member_id in ids:
        cfg.household.active_member_id = int(member_id)
        save_config(cfg)


def set_primary_member_id(member_id: int) -> None:
    cfg = load_config()
    ids = {m.id for m in cfg.household.members}
    if member_id in ids:
        cfg.household.primary_member_id = int(member_id)
        save_config(cfg)


def add_member(name: str, role: str = ROLE_SECONDARY) -> HouseholdMember:
    cfg = load_config()
    role = _sanitize_role(role)

    next_id = max((m.id for m in cfg.household.members), default=0) + 1
    member = HouseholdMember(
        id=next_id,
        name=(name or "").strip() or f"Member {next_id}",
        role=role,
        profile=ensure_member_profile_defaults({}, role=role),
    )
    cfg.household.members.append(member)
    save_config(cfg)
    return member


def rename_member(member_id: int, new_name: str) -> None:
    cfg = load_config()
    for m in cfg.household.members:
        if m.id == member_id:
            m.name = (new_name or "").strip() or m.name
            save_config(cfg)
            return


def delete_member(member_id: int) -> bool:
    """
    Returns True if deleted. Will not delete the last remaining member.
    If deleting the primary/active, reassigns.
    """
    cfg = load_config()
    if len(cfg.household.members) <= 1:
        return False

    remaining = [m for m in cfg.household.members if m.id != member_id]
    if len(remaining) == len(cfg.household.members):
        return False

    cfg.household.members = remaining
    ids = {m.id for m in remaining}
    if cfg.household.primary_member_id not in ids:
        cfg.household.primary_member_id = remaining[0].id
    if cfg.household.active_member_id not in ids:
        cfg.household.active_member_id = cfg.household.primary_member_id

    # Ensure we still have a master
    masters = [m for m in cfg.household.members if m.role == ROLE_MASTER]
    if not masters:
        cfg.household.members[0].role = ROLE_MASTER

    save_config(cfg)
    return True


def get_member_profile(member_id: int) -> Dict[str, Any]:
    cfg = load_config()
    for m in cfg.household.members:
        if m.id == member_id:
            return dict(m.profile or {})
    return {}


def save_member_profile(member_id: int, profile: Dict[str, Any]) -> None:
    cfg = load_config()
    for m in cfg.household.members:
        if m.id == member_id:
            m.profile = ensure_member_profile_defaults(profile or {}, role=m.role)
            save_config(cfg)
            return


# ---------------------------------------------------------------------------
# Convenience / safety helpers
# ---------------------------------------------------------------------------

def is_master(member_id: int) -> bool:
    m = get_member(member_id)
    return bool(m and m.role == ROLE_MASTER)


def get_household_allergies() -> Set[str]:
    """
    Returns union of all member allergies (canonical lowercase tokens).
    """
    cfg = load_config()
    out: Set[str] = set()
    for m in cfg.household.members:
        prof = m.profile or {}
        vals = prof.get("allergies", [])
        if isinstance(vals, list):
            for v in vals:
                s = str(v).strip().lower()
                if s:
                    out.add(s)
        elif isinstance(vals, str):
            for v in vals.split(","):
                s = v.strip().lower()
                if s:
                    out.add(s)
    return out


def reset_secondary_member_to_household_baseline(member_id: int) -> bool:
    """
    Clears SECONDARY member overrides back to baseline while preserving allergies.
    Returns True if reset occurred.

    - Does nothing for master member.
    """
    cfg = load_config()
    master = get_master_member()
    if member_id == master.id:
        return False

    target: Optional[HouseholdMember] = None
    for m in cfg.household.members:
        if m.id == member_id:
            target = m
            break
    if not target:
        return False

    if target.role != ROLE_SECONDARY:
        return False

    prof = target.profile or {}
    allergies = _sanitize_list(prof.get("allergies", []))

    # Keep only allergies after reset (and keep legacy keys minimal)
    new_prof = default_member_profile()
    # Secondary baseline should not carry master-only hard_excludes
    new_prof["hard_excludes"] = []
    new_prof["soft_excludes"] = []
    new_prof["excluded_proteins"] = []
    new_prof["preferred_protein_weights"] = {}
    new_prof["favorite_cuisines"] = []
    new_prof["meal_styles"] = []
    new_prof["oils_allowed"] = []
    new_prof["spice_level"] = "medium"

    # Keep dietary toggles inherited implicitly through baseline logic,
    # but for member profile we can leave them at defaults.
    new_prof["eats_meat"] = True
    new_prof["eats_fish"] = True
    new_prof["eats_dairy"] = True
    new_prof["eats_eggs"] = True

    # Restore allergies
    new_prof["allergies"] = allergies

    # Normalize per role rules
    target.profile = ensure_member_profile_defaults(new_prof, role=target.role)
    save_config(cfg)
    return True
