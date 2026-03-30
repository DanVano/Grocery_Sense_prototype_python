"""
Grocery_Sense.services.price_history_service

Service layer for price history and "is this a good deal?" logic.

This wraps:
  - items_repo (canonical items)
  - prices_repo (price history)

and provides higher-level methods suitable for UI and future integrations
(receipts, flyers, Flipp, Azure Document Intelligence, etc.).
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, date
from typing import Optional, Dict, Any, Tuple, List

from Grocery_Sense.data.repositories.items_repo import (
    get_item_by_name,
    get_item_by_id,
    create_item,
)
from Grocery_Sense.data.repositories.prices_repo import (
    add_price_point,
    get_most_recent_price,
    get_price_stats_for_item,
)
from Grocery_Sense.domain.models import Item, PricePoint


class PriceHistoryService:
    """
    High-level price history operations.

    Responsibilities:
      - Ensure canonical Item records exist.
      - Record price points from different sources (receipt/flyer/manual).
      - Compute per-item statistics (avg/min/max over a window).
      - Classify a candidate price as "great / good / typical / expensive".
    """

    # ---------- Item helpers ----------

    def get_or_create_item(
        self,
        canonical_name: str,
        category: Optional[str] = None,
        default_unit: Optional[str] = None,
    ) -> Item:
        """
        Look up an Item by canonical_name (case-insensitive).
        If not found, create it.

        This is the typical entry point when parsing receipts/flyers.
        """
        name_clean = canonical_name.strip()
        existing = get_item_by_name(name_clean)
        if existing:
            return existing

        return create_item(
            canonical_name=name_clean,
            category=category,
            default_unit=default_unit,
        )

    def ensure_item_exists(self, canonical_name: str) -> Item:
        """
        Convenience wrapper around get_or_create_item when you don't care
        about category/unit yet.
        """
        return self.get_or_create_item(canonical_name=canonical_name)

    # ---------- Recording prices ----------

    def record_price_from_receipt(
        self,
        item_name: str,
        store_id: int,
        unit_price: float,
        unit: str,
        *,
        date_str: Optional[str] = None,
        quantity: Optional[float] = None,
        total_price: Optional[float] = None,
        receipt_id: Optional[int] = None,
        raw_name: Optional[str] = None,
        confidence: Optional[int] = None,
    ) -> PricePoint:
        """
        Record a price coming from a supermarket receipt.

        - `item_name` is your canonical / normalized name, not the messy receipt text.
        - `unit_price` should be normalized (e.g. per kg or per each) before calling,
          but you can also store it "as-is" for now and normalize later.

        Returns the created PricePoint.
        """
        item = self.ensure_item_exists(item_name)
        used_date = date_str or date.today().isoformat()

        return add_price_point(
            item_id=item.id,
            store_id=store_id,
            source="receipt",
            date=used_date,
            unit_price=unit_price,
            unit=unit,
            quantity=quantity,
            total_price=total_price,
            receipt_id=receipt_id,
            flyer_source_id=None,
            raw_name=raw_name,
            confidence=confidence,
        )

    def record_price_from_flyer(
        self,
        item_name: str,
        store_id: int,
        unit_price: float,
        unit: str,
        *,
        date_str: Optional[str] = None,
        flyer_source_id: Optional[int] = None,
        raw_name: Optional[str] = None,
        confidence: Optional[int] = None,
    ) -> PricePoint:
        """
        Record a price coming from a flyer / Flipp.

        The `date_str` here should typically be the "valid_from" or current day
        of the flyer; you can decide that when integrating flipp.
        """
        item = self.ensure_item_exists(item_name)
        used_date = date_str or date.today().isoformat()

        return add_price_point(
            item_id=item.id,
            store_id=store_id,
            source="flyer",
            date=used_date,
            unit_price=unit_price,
            unit=unit,
            quantity=None,
            total_price=None,
            receipt_id=None,
            flyer_source_id=flyer_source_id,
            raw_name=raw_name,
            confidence=confidence,
        )

    def record_manual_price(
        self,
        item_name: str,
        store_id: int,
        unit_price: float,
        unit: str,
        *,
        date_str: Optional[str] = None,
        quantity: Optional[float] = None,
        total_price: Optional[float] = None,
        raw_name: Optional[str] = None,
    ) -> PricePoint:
        """
        Record a price manually entered by the user (e.g. quick comparison at store).
        """
        item = self.ensure_item_exists(item_name)
        used_date = date_str or date.today().isoformat()

        return add_price_point(
            item_id=item.id,
            store_id=store_id,
            source="manual",
            date=used_date,
            unit_price=unit_price,
            unit=unit,
            quantity=quantity,
            total_price=total_price,
            receipt_id=None,
            flyer_source_id=None,
            raw_name=raw_name,
            confidence=None,
        )

    # ---------- Stats & comparison ----------

    def get_item_stats(
        self,
        item_name: str,
        window_days: int = 180,
    ) -> Optional[Dict[str, Any]]:
        """
        Return basic statistics for the given item over the last `window_days`.

        Returns dict:
            {
              "item": Item,
              "avg_unit_price": float,
              "min_unit_price": float,
              "max_unit_price": float,
              "sample_count": int,
            }

        or None if no data.
        """
        item = get_item_by_name(item_name.strip())
        if not item:
            return None

        stats = get_price_stats_for_item(item_id=item.id, since_days=window_days)
        if stats.count == 0:
            return None

        avg_price = stats.avg_price
        min_price = stats.min_price
        max_price = stats.max_price
        count = stats.count
        return {
            "item": item,
            "avg_unit_price": avg_price,
            "min_unit_price": min_price,
            "max_unit_price": max_price,
            "sample_count": count,
        }

    def classify_deal(
        self,
        item_name: str,
        candidate_unit_price: float,
        *,
        window_days: int = 180,
    ) -> Dict[str, Any]:
        """
        Given a candidate unit price (e.g. today's flyer), classify it relative
        to your historical average over the last `window_days`.

        Returns dict with keys:
            {
              "item": Item or None,
              "has_history": bool,
              "classification": str,     # 'great', 'good', 'typical', 'expensive', 'no_data'
              "percent_vs_avg": float or None,  # positive = cheaper, negative = more expensive
              "avg_unit_price": float or None,
              "min_unit_price": float or None,
              "max_unit_price": float or None,
              "sample_count": int,
              "message": str,
            }
        """
        item = get_item_by_name(item_name.strip())
        if not item:
            return {
                "item": None,
                "has_history": False,
                "classification": "no_data",
                "percent_vs_avg": None,
                "avg_unit_price": None,
                "min_unit_price": None,
                "max_unit_price": None,
                "sample_count": 0,
                "message": (
                    f"No price history for '{item_name}'. "
                    "You can start building history by scanning receipts or entering prices."
                ),
            }

        stats = get_price_stats_for_item(item_id=item.id, since_days=window_days)
        if stats.count == 0:
            return {
                "item": item,
                "has_history": False,
                "classification": "no_data",
                "percent_vs_avg": None,
                "avg_unit_price": None,
                "min_unit_price": None,
                "max_unit_price": None,
                "sample_count": 0,
                "message": (
                    f"No price history for '{item.canonical_name}' in the last {window_days} days."
                ),
            }

        avg_price = stats.avg_price
        min_price = stats.min_price
        max_price = stats.max_price
        count = stats.count
        if avg_price <= 0 or count == 0:
            return {
                "item": item,
                "has_history": False,
                "classification": "no_data",
                "percent_vs_avg": None,
                "avg_unit_price": avg_price,
                "min_unit_price": min_price,
                "max_unit_price": max_price,
                "sample_count": count,
                "message": "Price data is invalid or incomplete.",
            }

        # Positive = cheaper than avg; negative = more expensive than avg
        percent_vs_avg = (avg_price - candidate_unit_price) / avg_price * 100.0

        # Simple threshold logic; we can tune this later
        if count < 3:
            # Not enough data for strong judgement
            classification = "weak_data"
            message = (
                f"Limited data for '{item.canonical_name}' "
                f"(n={count}). Current price {candidate_unit_price:.2f} vs "
                f"avg {avg_price:.2f} ({percent_vs_avg:+.1f}% vs avg)."
            )
        else:
            if percent_vs_avg >= 20.0:
                classification = "great"
            elif percent_vs_avg >= 10.0:
                classification = "good"
            elif percent_vs_avg > -10.0:
                classification = "typical"
            else:
                classification = "expensive"

            # Human-readable explanation
            if classification == "great":
                msg_prefix = "🔥 Great deal"
            elif classification == "good":
                msg_prefix = "✅ Good deal"
            elif classification == "typical":
                msg_prefix = "➖ Typical price"
            else:
                msg_prefix = "⚠️ More expensive than usual"

            message = (
                f"{msg_prefix} for '{item.canonical_name}': "
                f"{candidate_unit_price:.2f} vs your average {avg_price:.2f} "
                f"({percent_vs_avg:+.1f}% vs avg). "
                f"Historical range: {min_price:.2f}–{max_price:.2f} from {count} data points."
            )

        return {
            "item": item,
            "has_history": True,
            "classification": classification,
            "percent_vs_avg": percent_vs_avg,
            "avg_unit_price": avg_price,
            "min_unit_price": min_price,
            "max_unit_price": max_price,
            "sample_count": count,
            "message": message,
        }

    # ---------- Debug / utility ----------

    def describe_item_history(
        self,
        item_name: str,
        window_days: int = 365,
    ) -> str:
        """
        Return a human-readable description of this item's price history
        over the last `window_days`.
        """
        stats = self.get_item_stats(item_name, window_days=window_days)
        if not stats:
            return f"No price history found for '{item_name}' in the last {window_days} days."

        item: Item = stats["item"]
        avg_p = stats["avg_unit_price"]
        min_p = stats["min_unit_price"]
        max_p = stats["max_unit_price"]
        n = stats["sample_count"]

        return (
            f"Price history for '{item.canonical_name}' (last {window_days} days):\n"
            f"  • Average: {avg_p:.2f} per unit\n"
            f"  • Range:   {min_p:.2f} – {max_p:.2f}\n"
            f"  • Samples: {n} data points"
        )
