from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional

from Grocery_Sense.data.repositories import prices_repo

# Preferences are optional. If unavailable (during early prototyping), alerts still work.
try:
    from Grocery_Sense.services import preferences_service
except Exception:  # pragma: no cover
    preferences_service = None


@dataclass(frozen=True)
class PriceDropAlert:
    # Identity
    alert_type: str  # "DROP_BELOW_USUAL" | "STOCK_UP" | "BOTH"
    item_id: int
    item_name: str
    store_id: int
    store_name: str

    # Current quote (from active flyer deal)
    current_unit_price: float
    unit: str
    valid_to: Optional[str] = None

    # Usual price (receipt-first)
    usual_unit_price: Optional[float] = None
    usual_source: str = "unknown"  # "receipt" | "estimated" | "unknown"
    receipt_samples: int = 0

    # 6-month stats
    low_6mo_store: Optional[float] = None
    low_6mo_global: Optional[float] = None
    month_low_store: Optional[float] = None

    # Derived deltas
    pct_below_usual: Optional[float] = None          # e.g. 0.25 = 25% below usual
    pct_above_low_6mo: Optional[float] = None        # e.g. 0.03 = 3% above 6mo low (near-low)
    pct_above_month_low: Optional[float] = None

    # Staple learning
    staple_purchases_90d: int = 0
    is_staple: bool = False

    # Preference annotation (soft excludes)
    soft_excluded_by: Optional[List[str]] = None
    soft_exclude_hit: Optional[str] = None

    # UI helpers
    warnings: Optional[List[str]] = None


class PriceDropAlertService:
    """
    Milestone 2 engine:
      - "Usual price" learned from receipts (store-specific).
      - Falls back to other sources if receipt history is sparse.
      - Generates alerts for active flyer deals only (valid date).
      - "Stock-up" when near 6-month low AND the item is a staple.
      - Preference aware (optional): blocks hard-excludes, stars soft-excludes.
    """

    # Thresholds (tune later)
    DEFAULT_DROP_THRESHOLD = 0.20          # 20% below usual
    DEFAULT_NEAR_LOW_MARGIN = 0.05         # within 5% of 6mo low
    DEFAULT_NEAR_MONTH_LOW_MARGIN = 0.02   # within 2% of month low

    DEFAULT_USUAL_WINDOW_DAYS = 180
    DEFAULT_STAPLE_WINDOW_DAYS = 90
    DEFAULT_STAPLE_MIN_PURCHASES = 4

    def __init__(self) -> None:
        pass

    def get_alerts(
        self,
        *,
        limit: int = 250,
        as_of: Optional[date] = None,
        drop_threshold: float = DEFAULT_DROP_THRESHOLD,
        near_low_margin: float = DEFAULT_NEAR_LOW_MARGIN,
        near_month_low_margin: float = DEFAULT_NEAR_MONTH_LOW_MARGIN,
        usual_window_days: int = DEFAULT_USUAL_WINDOW_DAYS,
        staple_window_days: int = DEFAULT_STAPLE_WINDOW_DAYS,
        staple_min_purchases: int = DEFAULT_STAPLE_MIN_PURCHASES,
        min_receipt_samples: int = 3,
    ) -> List[PriceDropAlert]:
        """
        Build alerts based on ACTIVE flyer deals (mapped item_id only).

        Notes:
          - 'usual' is receipt-first; if receipt samples < min_receipt_samples, we estimate.
          - 'stock-up' requires staple AND near 6mo low (global) or near store low.
        """
        as_of = as_of or date.today()

        # Active flyer deals (mapped items, valid date)
        deals = prices_repo.list_active_flyer_deal_quotes(as_of=as_of)

        # Optional: preference engine
        effective = None
        if preferences_service is not None:
            try:
                effective = preferences_service.compute_effective_preferences()
            except Exception:
                effective = None

        alerts: List[PriceDropAlert] = []

        for d in deals:
            item_id = d.get("item_id")
            store_id = d.get("store_id")
            if item_id is None or store_id is None:
                continue

            try:
                item_id_i = int(item_id)
                store_id_i = int(store_id)
            except Exception:
                continue

            item_name = str(d.get("item_name") or "").strip() or "Unknown item"
            store_name = str(d.get("store_name") or "").strip() or "Unknown store"

            # Preference: hard-exclude blocks the alert entirely
            if effective is not None:
                try:
                    if preferences_service.is_hard_excluded(item_name, effective):
                        continue
                except Exception:
                    pass

            try:
                current = float(d.get("unit_price"))
            except Exception:
                continue

            unit = str(d.get("unit") or "").strip().lower()
            valid_to = d.get("valid_to")

            # Usual (receipt-first, store-specific)
            usual_info = prices_repo.get_usual_unit_price_for_store(
                item_id_i,
                store_id_i,
                days=usual_window_days,
                as_of=as_of,
                min_receipt_samples=min_receipt_samples,
            )
            usual = usual_info.get("usual_price")
            usual_source = str(usual_info.get("usual_source") or "unknown")
            receipt_samples = int(usual_info.get("receipt_samples") or 0)

            # 6-month lows
            low_store = prices_repo.get_six_month_low_for_store(
                item_id_i, store_id_i, days=usual_window_days, as_of=as_of
            )
            low_global = prices_repo.get_six_month_low_global(
                item_id_i, days=usual_window_days, as_of=as_of
            )
            month_low = prices_repo.get_month_low_for_store(
                item_id_i, store_id_i, days=30, as_of=as_of
            )

            # Staple learning (receipt frequency)
            staple_count = prices_repo.get_receipt_purchase_count(
                item_id_i, days=staple_window_days, as_of=as_of
            )
            is_staple = staple_count >= int(staple_min_purchases)

            # Trigger logic
            below_usual = False
            near_6mo_low = False
            near_month_low = False

            pct_below_usual: Optional[float] = None
            pct_above_low_6mo: Optional[float] = None
            pct_above_month_low: Optional[float] = None

            if usual is not None and usual > 0:
                pct_below_usual = max(0.0, (usual - current) / usual)
                below_usual = current <= (usual * (1.0 - float(drop_threshold)))

            # Near-low checks (prefer global, but still compute store)
            ref_low = None
            if low_global is not None and low_global > 0:
                ref_low = low_global
            elif low_store is not None and low_store > 0:
                ref_low = low_store

            if ref_low is not None and ref_low > 0:
                pct_above_low_6mo = max(0.0, (current - ref_low) / ref_low)
                near_6mo_low = current <= (ref_low * (1.0 + float(near_low_margin)))

            if month_low is not None and month_low > 0:
                pct_above_month_low = max(0.0, (current - month_low) / month_low)
                near_month_low = current <= (month_low * (1.0 + float(near_month_low_margin)))

            # Decide alert type
            # - Drop alerts can fire even if not staple (still useful)
            # - Stock-up requires staple AND near 6mo low (and optionally near month-low)
            want_stock_up = is_staple and near_6mo_low
            want_drop = below_usual

            if not want_drop and not want_stock_up:
                continue

            if want_drop and want_stock_up:
                alert_type = "BOTH"
            elif want_stock_up:
                alert_type = "STOCK_UP"
            else:
                alert_type = "DROP_BELOW_USUAL"

            # Soft-exclude annotation (optional)
            soft_by: Optional[List[str]] = None
            soft_hit: Optional[str] = None
            if effective is not None:
                try:
                    soft_by = preferences_service.soft_excluded_by_members(item_name, effective)
                    soft_hit = preferences_service.explain_soft_exclude_hit(item_name, effective)
                    if soft_by and not isinstance(soft_by, list):
                        soft_by = list(soft_by)
                except Exception:
                    soft_by = None
                    soft_hit = None

            warnings: List[str] = []
            if usual is None:
                warnings.append("No usual price history for this item/store.")
            elif usual_source != "receipt":
                warnings.append("Usual price estimated (receipt history is sparse).")

            if low_global is None and low_store is None:
                warnings.append("No 6-month low history for this item.")

            # Score/sort key: prefer bigger savings, then staples
            # (we keep score internal; window sorts by fields too)
            alert = PriceDropAlert(
                alert_type=alert_type,
                item_id=item_id_i,
                item_name=item_name,
                store_id=store_id_i,
                store_name=store_name,
                current_unit_price=current,
                unit=unit,
                valid_to=valid_to,
                usual_unit_price=float(usual) if usual is not None else None,
                usual_source=usual_source,
                receipt_samples=receipt_samples,
                low_6mo_store=low_store,
                low_6mo_global=low_global,
                month_low_store=month_low,
                pct_below_usual=pct_below_usual,
                pct_above_low_6mo=pct_above_low_6mo,
                pct_above_month_low=pct_above_month_low,
                staple_purchases_90d=int(staple_count),
                is_staple=is_staple,
                soft_excluded_by=soft_by,
                soft_exclude_hit=soft_hit,
                warnings=warnings or None,
            )
            alerts.append(alert)

        # Sort: biggest % below usual first, then stock-up, then staples, then price
        def _sort_key(a: PriceDropAlert) -> tuple:
            pct = a.pct_below_usual if a.pct_below_usual is not None else 0.0
            stock = 1 if a.alert_type in ("STOCK_UP", "BOTH") else 0
            staple = 1 if a.is_staple else 0
            return (-pct, -stock, -staple, a.current_unit_price)

        alerts.sort(key=_sort_key)
        return alerts[: int(limit)]
