"""
M3 — PriceDropAlertService

Covers:
  - Guard rails: empty DB / no stores / no staples produce zero alerts
  - Staple detection + usual-price computation + below-usual alert firing
  - Near-6-month-low stock-up signal folds into combined 'both' alert
  - refresh_engine_alerts persists rows; dismissed keys suppress re-emission
  - scan_recent_receipts fires on receipts paid far below usual
  - PriceDropAlert._from_dict mapping (UI-facing shape)
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from Grocery_Sense.data import connection as _conn
from Grocery_Sense.data.repositories.stores_repo import create_store
from Grocery_Sense.services.price_drop_alert_service import (
    AlertKey,
    PriceDropAlert,
    PriceDropAlertService,
)
from Grocery_Sense.services.price_history_service import PriceHistoryService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso_days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


@pytest.fixture
def svc(isolated_db) -> PriceDropAlertService:
    # PriceDropAlertService doesn't honour _TEST_DB_PATH automatically; pass it in.
    return PriceDropAlertService(db_path=_conn._TEST_DB_PATH)


@pytest.fixture
def history() -> PriceHistoryService:
    return PriceHistoryService()


def _make_staple_with_drop(
    history: PriceHistoryService,
    store_id: int,
    *,
    item_name: str,
    usual_price: float,
    current_price: float,
    usual_samples: int = 5,
) -> int:
    """
    Seed enough receipt history for `item_name` to count as a staple and
    establish `usual_price` as the receipt median, then insert a single
    most-recent receipt at `current_price`. Returns the item id.
    """
    for _ in range(usual_samples):
        history.record_price_from_receipt(
            item_name=item_name,
            store_id=store_id,
            unit_price=usual_price,
            unit="each",
            date_str=_iso_days_ago(30),
        )
    history.record_price_from_receipt(
        item_name=item_name,
        store_id=store_id,
        unit_price=current_price,
        unit="each",
        date_str=_iso_days_ago(0),
    )
    item = history.get_or_create_item(item_name)
    return item.id


# ---------------------------------------------------------------------------
# Guard rails
# ---------------------------------------------------------------------------


class TestGuardRails:
    def test_empty_db_returns_no_alerts(self, svc):
        assert svc.compute_engine_alerts() == []

    def test_no_stores_returns_no_alerts(self, svc, isolated_db):
        # No stores registered → compute_engine_alerts short-circuits.
        assert svc.compute_engine_alerts() == []

    def test_no_staples_returns_no_alerts_when_staples_only(self, svc, history, isolated_db):
        store = create_store(name="Only Mart")
        # Single receipt price for one item — far short of staple thresholds.
        history.record_price_from_receipt(
            item_name="avocado", store_id=store.id, unit_price=2.00, unit="each"
        )
        assert svc.compute_engine_alerts(staples_only=True) == []


# ---------------------------------------------------------------------------
# Alert firing
# ---------------------------------------------------------------------------


class TestAlertFiring:
    def test_below_usual_fires_on_staple(self, svc, history, isolated_db):
        store = create_store(name="Test Mart")
        _make_staple_with_drop(
            history,
            store.id,
            item_name="eggs",
            usual_price=6.00,
            current_price=4.50,  # 25% below usual — well over 15% threshold
        )

        alerts = svc.compute_engine_alerts()
        assert len(alerts) == 1
        a = alerts[0]
        assert a["item_name"] == "eggs"
        assert a["store_name"] == "Test Mart"
        assert a["current_price"] == pytest.approx(4.50)
        assert a["usual_price"] == pytest.approx(6.00)
        assert a["pct_below_usual"] >= 15.0
        assert a["alert_kind"] in {"below_usual", "both"}
        assert a["is_staple"] == 1

    def test_typical_price_does_not_fire(self, svc, history, isolated_db):
        store = create_store(name="Regular Mart")
        _make_staple_with_drop(
            history,
            store.id,
            item_name="bread",
            usual_price=5.00,
            current_price=4.90,  # 2% below — below the 15% trigger
        )
        assert svc.compute_engine_alerts() == []

    def test_near_low_is_suppressed_when_current_price_hits_the_low(
        self, svc, history, isolated_db
    ):
        """
        FINDING (documented, not fixed): when the current latest price equals the
        6-month low, the stock_up cooldown check sees that same row as
        "last_seen_at_or_below the ceiling" (days=0) and suppresses stock_up.
        The alert therefore fires as 'below_usual' only — 'both' is effectively
        unreachable under the current design. If the cooldown is later changed to
        exclude the current row, flip this assertion to 'both'.
        """
        store = create_store(name="Combo Mart")
        _make_staple_with_drop(
            history, store.id, item_name="milk", usual_price=6.00, current_price=4.00
        )
        alerts = svc.compute_engine_alerts()
        assert len(alerts) == 1
        assert alerts[0]["alert_kind"] == "below_usual"
        assert alerts[0]["pct_above_low"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Persistence & dismissal suppression
# ---------------------------------------------------------------------------


class TestPersistenceAndDismissal:
    def test_refresh_engine_alerts_writes_open_rows(self, svc, history, isolated_db):
        store = create_store(name="Persist Mart")
        _make_staple_with_drop(
            history, store.id, item_name="eggs", usual_price=6.0, current_price=4.0
        )

        inserted = svc.refresh_engine_alerts()
        assert inserted == 1

        open_alerts = svc.get_open_alerts()
        assert len(open_alerts) == 1
        assert open_alerts[0]["status"] == "open"
        assert open_alerts[0]["source"] == "engine"

    def test_dismiss_suppresses_reemission_within_cooldown(
        self, svc, history, isolated_db
    ):
        store = create_store(name="Cooldown Mart")
        _make_staple_with_drop(
            history, store.id, item_name="eggs", usual_price=6.0, current_price=4.0
        )

        svc.refresh_engine_alerts()
        open_alerts = svc.get_open_alerts()
        assert len(open_alerts) == 1
        svc.dismiss_alert(open_alerts[0]["id"])

        # Refreshing again must NOT re-emit the dismissed alert.
        svc.refresh_engine_alerts()
        assert svc.get_open_alerts() == []

    def test_get_alerts_returns_dataclasses(self, svc, history, isolated_db):
        store = create_store(name="DC Mart")
        _make_staple_with_drop(
            history, store.id, item_name="rice", usual_price=10.0, current_price=6.0
        )
        svc.refresh_engine_alerts()
        alerts = svc.get_alerts()
        assert len(alerts) == 1
        assert isinstance(alerts[0], PriceDropAlert)
        assert alerts[0].item_name == "rice"
        assert alerts[0].alert_type in {"DROP_BELOW_USUAL", "BOTH"}


# ---------------------------------------------------------------------------
# scan_recent_receipts path
# ---------------------------------------------------------------------------


class TestScanRecentReceipts:
    def test_scan_fires_on_recent_receipt_below_usual(
        self, svc, history, isolated_db
    ):
        store = create_store(name="Scanner Mart")
        _make_staple_with_drop(
            history, store.id, item_name="pasta", usual_price=5.00, current_price=3.00
        )

        inserted = svc.scan_recent_receipts(days=21)
        assert inserted >= 1

        open_alerts = svc.get_open_alerts()
        assert any(
            a["item_name"] == "pasta" and a["source"] == "receipt"
            for a in open_alerts
        )

    def test_scan_ignores_receipts_within_threshold(self, svc, history, isolated_db):
        store = create_store(name="Quiet Mart")
        _make_staple_with_drop(
            history, store.id, item_name="yogurt", usual_price=5.00, current_price=4.90
        )
        inserted = svc.scan_recent_receipts(days=21)
        assert inserted == 0


# ---------------------------------------------------------------------------
# Dataclass mapping
# ---------------------------------------------------------------------------


class TestPriceDropAlertFromDict:
    def test_from_dict_maps_core_fields(self):
        row = {
            "alert_kind": "below_usual",
            "item_name": "eggs",
            "store_name": "Test Mart",
            "current_price": 4.0,
            "usual_price": 6.0,
            "pct_below_usual": 33.3,
            "six_month_low": 3.5,
            "pct_above_low": 14.2,
            "is_staple": 1,
            "receipt_samples": 5,
            "basis": "receipt_median",
            "notes": "",
        }
        alert = PriceDropAlert._from_dict(row)
        assert alert.alert_type == "DROP_BELOW_USUAL"
        assert alert.is_staple is True
        assert alert.usual_source == "receipt"
        # Percentages are stored as fractions on the dataclass per the mapping
        assert alert.pct_below_usual == pytest.approx(0.333)

    def test_from_dict_handles_missing_fields(self):
        alert = PriceDropAlert._from_dict({})
        assert alert.alert_type == "DROP_BELOW_USUAL"
        assert alert.current_unit_price is None
        assert alert.usual_source == "unknown"


# ---------------------------------------------------------------------------
# AlertKey sanity (hashing for dismissed-set lookups)
# ---------------------------------------------------------------------------


def test_alert_key_is_hashable_and_equal_on_tuple():
    a = AlertKey(item_id=1, store_id=2, alert_kind="below_usual")
    b = AlertKey(item_id=1, store_id=2, alert_kind="below_usual")
    assert a == b
    assert hash(a) == hash(b)
    assert {a, b} == {a}
