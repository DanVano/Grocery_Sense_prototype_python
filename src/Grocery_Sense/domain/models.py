"""
Grocery_Sense.domain.models

Dataclasses representing the core domain objects of the Grocery Sense app.
These are the types that repositories return and services operate on.
"""

from dataclasses import dataclass
from typing import Optional


# ---------- Stores & Items ----------

@dataclass
class Store:
    id: int
    name: str
    address: Optional[str] = None
    city: Optional[str] = None
    postal_code: Optional[str] = None
    flipp_store_id: Optional[str] = None
    is_favorite: bool = False
    priority: int = 0
    notes: Optional[str] = None


@dataclass
class Item:
    id: int
    canonical_name: str
    category: Optional[str] = None
    default_unit: Optional[str] = None
    typical_package_size: Optional[float] = None
    typical_package_unit: Optional[str] = None
    is_tracked: bool = True
    notes: Optional[str] = None


# ---------- Receipts & Flyers ----------

@dataclass
class Receipt:
    id: int
    store_id: int
    purchase_date: str           # 'YYYY-MM-DD'
    subtotal_amount: Optional[float] = None
    tax_amount: Optional[float] = None
    total_amount: Optional[float] = None
    source: str = "receipt"      # 'receipt' | 'manual'
    file_path: Optional[str] = None
    image_overall_confidence: Optional[int] = None  # 1–5
    keep_image_until: Optional[str] = None          # 'YYYY-MM-DD'
    azure_request_id: Optional[str] = None


@dataclass
class FlyerSource:
    id: int
    provider: str                # e.g. 'flipp'
    external_id: Optional[str]
    store_id: int
    valid_from: str              # 'YYYY-MM-DD'
    valid_to: str                # 'YYYY-MM-DD'


# ---------- Price history ----------

@dataclass
class PricePoint:
    id: int
    item_id: int
    store_id: int
    source: str                  # 'receipt' | 'flyer' | 'manual'
    date: str                    # 'YYYY-MM-DD'
    unit_price: float            # pre-tax, normalized (e.g. per kg)
    unit: str                    # 'kg', 'lb', 'each', etc.
    quantity: Optional[float] = None
    total_price: Optional[float] = None
    receipt_id: Optional[int] = None
    flyer_source_id: Optional[int] = None
    raw_name: Optional[str] = None
    confidence: Optional[int] = None  # 1–5


# ---------- Price stats ----------

@dataclass
class PriceStats:
    item_id: int
    store_id: Optional[int]
    min_price: Optional[float]
    max_price: Optional[float]
    avg_price: Optional[float]
    count: int


# ---------- Shopping list ----------

@dataclass
class ShoppingListItem:
    id: int
    display_name: str
    quantity: Optional[float] = None
    unit: Optional[str] = None
    item_id: Optional[int] = None           # link to canonical Item if known
    planned_store_id: Optional[int] = None  # which store to buy at
    added_by: Optional[str] = None
    added_at: Optional[str] = None          # ISO datetime string
    is_checked_off: bool = False
    is_active: bool = True
    notes: Optional[str] = None


# ---------- User profile & sync ----------

@dataclass
class UserProfile:
    id: int
    household_name: Optional[str] = None
    postal_code: Optional[str] = None
    currency: str = "CAD"
    preferred_store_ids: Optional[str] = None  # e.g. "1,2,5" (simple for now)
    eats_chicken: bool = True
    eats_beef: bool = True
    eats_pork: bool = True
    eats_fish: bool = True
    is_vegetarian: bool = False
    is_gluten_free: bool = False
    has_nut_allergy: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class SyncMeta:
    id: int
    device_role: Optional[str] = None       # 'primary' | 'secondary'
    instance_id: Optional[str] = None       # random UUID per install
    last_sync_from_primary_at: Optional[str] = None
    last_sync_to_primary_at: Optional[str] = None
    created_at: Optional[str] = None
