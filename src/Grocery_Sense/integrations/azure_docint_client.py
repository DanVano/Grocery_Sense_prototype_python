"""
Grocery_Sense.integrations.azure_docint_client

Azure AI Document Intelligence (prebuilt-receipt) -> JSON -> DB ingest

Includes:
- Dedupe layer:
  (1) file hash dedupe (no Azure call needed)
  (2) receipt signature dedupe (merchant+date+total) to catch rescans
- Unit normalization v1:
  - items.default_unit
  - prices.norm_unit_price / prices.norm_unit / prices.norm_note
  - lb <-> kg, g <-> kg
- Multi-buy deal normalization v1:
  - "2/$5", "3 for 10", "2 @ 4.00", "BOGO"
  - compute effective unit price / corrected qty when possible

Default behavior:
- If duplicate found, DO NOT insert a new receipt.
- Returns IngestOutcome with was_duplicate=True, and receipt_id = existing receipt.

Optional:
- replace_existing=True deletes existing receipt + derived rows and ingests new one.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError, ServiceRequestError
from rapidfuzz import fuzz, process

from Grocery_Sense.data.connection import get_connection
from Grocery_Sense.data.repositories import items_repo as items_repo_module
from Grocery_Sense.data.repositories.item_aliases_repo import ItemAliasesRepo
from Grocery_Sense.data.repositories.stores_repo import create_store, list_stores
from Grocery_Sense.services.ingredient_mapping_service import IngredientMappingService
from Grocery_Sense.services.unit_normalization_service import UnitNormalizationService
from Grocery_Sense.services.multibuy_deal_service import MultiBuyDealService


# =============================================================================
# Dedupe schema helpers
# =============================================================================

def _ensure_dedupe_tables() -> None:
    """
    Adds lightweight tables to support dedupe without changing your base schema.
    """
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS receipt_file_hashes (
                file_hash TEXT PRIMARY KEY,
                receipt_id INTEGER NOT NULL,
                file_path TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (receipt_id) REFERENCES receipts(id) ON DELETE CASCADE
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS receipt_signatures (
                signature TEXT PRIMARY KEY,
                receipt_id INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (receipt_id) REFERENCES receipts(id) ON DELETE CASCADE
            );
            """
        )
        conn.commit()


def _compute_file_sha256(file_path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    p = Path(file_path)
    h = hashlib.sha256()
    with p.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _find_receipt_by_file_hash(file_hash: str) -> Optional[int]:
    _ensure_dedupe_tables()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT receipt_id FROM receipt_file_hashes WHERE file_hash = ?",
            (file_hash,),
        ).fetchone()
        return int(row[0]) if row else None


def _find_receipt_by_signature(signature: str) -> Optional[int]:
    _ensure_dedupe_tables()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT receipt_id FROM receipt_signatures WHERE signature = ?",
            (signature,),
        ).fetchone()
        return int(row[0]) if row else None


def _link_hash_to_receipt(file_hash: str, receipt_id: int, file_path: str) -> None:
    _ensure_dedupe_tables()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO receipt_file_hashes (file_hash, receipt_id, file_path, created_at)
            VALUES (?, ?, ?, ?);
            """,
            (file_hash, int(receipt_id), str(file_path), _now_utc_iso()),
        )
        conn.commit()


def _link_signature_to_receipt(signature: str, receipt_id: int) -> None:
    _ensure_dedupe_tables()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO receipt_signatures (signature, receipt_id, created_at)
            VALUES (?, ?, ?);
            """,
            (signature, int(receipt_id), _now_utc_iso()),
        )
        conn.commit()


def _delete_receipt_cascade(receipt_id: int) -> None:
    """
    Deletes a receipt and derived data. Safe if tables exist.
    """
    _ensure_ingest_tables()
    _ensure_dedupe_tables()

    with get_connection() as conn:
        # child -> parent order
        conn.execute("DELETE FROM prices WHERE receipt_id = ?;", (int(receipt_id),))
        conn.execute("DELETE FROM receipt_line_items WHERE receipt_id = ?;", (int(receipt_id),))
        conn.execute("DELETE FROM receipt_raw_json WHERE receipt_id = ?;", (int(receipt_id),))
        conn.execute("DELETE FROM receipt_file_hashes WHERE receipt_id = ?;", (int(receipt_id),))
        conn.execute("DELETE FROM receipt_signatures WHERE receipt_id = ?;", (int(receipt_id),))
        conn.execute("DELETE FROM receipts WHERE id = ?;", (int(receipt_id),))
        conn.commit()


# =============================================================================
# PART 1: Azure upload/analyze + raw JSON saving
# =============================================================================

@dataclass(frozen=True)
class AzureReceiptResult:
    operation_id: str
    analyze_result: Dict[str, Any]
    saved_json_path: Path


@dataclass(frozen=True)
class IngestOutcome:
    receipt_id: int
    was_duplicate: bool
    duplicate_reason: Optional[str] = None  # "file_hash" | "signature"
    replaced_existing: bool = False
    existing_receipt_id: Optional[int] = None  # if duplicate, which one it matched


class AzureReceiptClient:
    def __init__(
        self,
        endpoint: Optional[str] = None,
        api_key: Optional[str] = None,
        locale: str = "en-US",
    ) -> None:
        self.endpoint = endpoint or os.environ.get("DOCUMENTINTELLIGENCE_ENDPOINT", "").strip()
        self.api_key = api_key or os.environ.get("DOCUMENTINTELLIGENCE_API_KEY", "").strip()
        self.locale = locale

        if not self.endpoint or not self.api_key:
            raise RuntimeError(
                "Missing Azure Document Intelligence credentials.\n"
                "Set DOCUMENTINTELLIGENCE_ENDPOINT and DOCUMENTINTELLIGENCE_API_KEY environment variables."
            )

        self.client = DocumentIntelligenceClient(
            endpoint=self.endpoint,
            credential=AzureKeyCredential(self.api_key),
        )

    def analyze_receipt_file(
        self,
        file_path: str | Path,
        *,
        max_attempts: int = 3,
        base_delay: float = 2.0,
    ) -> Tuple[str, Dict[str, Any]]:
        p = Path(file_path)
        if not p.exists():
            raise FileNotFoundError(str(p))

        last_exc: Exception = RuntimeError("No attempts made")
        for attempt in range(max_attempts):
            try:
                with p.open("rb") as f:
                    poller = self.client.begin_analyze_document(
                        "prebuilt-receipt",
                        body=f,
                        locale=self.locale,
                    )
                result = poller.result()

                operation_id = str(poller.details.get("operation_id") or "")
                if not operation_id:
                    operation_id = f"op_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{p.stem}"

                return operation_id, result.as_dict()

            except HttpResponseError as exc:
                status = exc.status_code if exc.status_code is not None else 0
                # Non-retriable: bad request, auth, not found
                if status in (400, 401, 403, 404):
                    raise
                # Retriable: throttle (429) or server errors (5xx)
                last_exc = exc

            except ServiceRequestError as exc:
                # Network-level transient error — always retriable
                last_exc = exc

            if attempt < max_attempts - 1:
                delay = base_delay * (2 ** attempt)
                time.sleep(delay)

        raise last_exc

    def analyze_and_save_json(
        self,
        file_path: str | Path,
        raw_json_dir: str | Path,
    ) -> AzureReceiptResult:
        raw_dir = Path(raw_json_dir)
        raw_dir.mkdir(parents=True, exist_ok=True)

        operation_id, result_dict = self.analyze_receipt_file(file_path)

        src = Path(file_path)
        safe_name = re.sub(r"[^a-zA-Z0-9_\-]+", "_", src.stem)[:80]
        out_path = raw_dir / f"{safe_name}__{operation_id}.json"
        out_path.write_text(json.dumps(result_dict, ensure_ascii=False, indent=2), encoding="utf-8")

        return AzureReceiptResult(operation_id=operation_id, analyze_result=result_dict, saved_json_path=out_path)


# =============================================================================
# PART 2: Parse receipt JSON + store into Grocery Sense DB
# =============================================================================

def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _confidence_to_1_5(conf: Optional[float]) -> Optional[int]:
    if conf is None:
        return None
    try:
        c = float(conf)
    except Exception:
        return None
    if c >= 0.90:
        return 5
    if c >= 0.75:
        return 4
    if c >= 0.60:
        return 3
    if c >= 0.40:
        return 2
    return 1


def _safe_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip()
    s = s.replace(",", "")
    s = re.sub(r"[^\d\.\-]", "", s)
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None


def _pick_field(fields: Dict[str, Any], names) -> Optional[Dict[str, Any]]:
    if not fields:
        return None
    lower = {k.lower(): k for k in fields.keys()}
    for n in names:
        key = lower.get(n.lower())
        if key and isinstance(fields.get(key), dict):
            return fields[key]
    return None


def _field_value(field: Optional[Dict[str, Any]]) -> Tuple[Any, Optional[float]]:
    if not field:
        return None, None
    conf = field.get("confidence")
    for k in (
        "valueString",
        "valueNumber",
        "valueDate",
        "valueTime",
        "valuePhoneNumber",
        "valueCurrency",
        "valueInteger",
        "valueBoolean",
    ):
        if k in field:
            return field.get(k), conf
    if "content" in field:
        return field.get("content"), conf
    return None, conf


def _currency_amount(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, dict):
        return _safe_float(v.get("amount"))
    return _safe_float(v)


def _normalize_merchant_name(s: str) -> str:
    s = (s or "").lower().strip()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^a-z0-9 \-]", "", s)
    return s


def _make_receipt_signature(merchant: str, purchase_date: str, total: Optional[float]) -> Optional[str]:
    """
    Signature to catch duplicates across different photos/scans.
    """
    if not merchant or not purchase_date or total is None:
        return None
    m = _normalize_merchant_name(merchant)
    t = round(float(total), 2)
    return f"{m}|{purchase_date}|{t:.2f}"


def _ensure_ingest_tables() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS receipt_raw_json (
                receipt_id  INTEGER PRIMARY KEY,
                operation_id TEXT,
                json_path    TEXT,
                raw_json     TEXT NOT NULL,
                created_at   TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (receipt_id) REFERENCES receipts(id) ON DELETE CASCADE
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS receipt_line_items (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                receipt_id   INTEGER NOT NULL,
                line_index   INTEGER NOT NULL,
                item_id      INTEGER,
                description  TEXT,
                quantity     REAL,
                unit_price   REAL,
                line_total   REAL,
                discount     REAL,
                confidence   INTEGER,
                created_at   TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (receipt_id) REFERENCES receipts(id) ON DELETE CASCADE,
                FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE SET NULL
            );
            """
        )
        conn.commit()


def _get_or_create_store_id(merchant_name: str, threshold: int = 85) -> int:
    merchant_name = (merchant_name or "").strip() or "Unknown Store"
    stores = list_stores(only_favorites=False, order_by_priority=True)
    if not stores:
        return int(create_store(name=merchant_name).id)

    store_names = [s.name for s in stores]
    match = process.extractOne(merchant_name, store_names, scorer=fuzz.token_set_ratio)

    if match:
        best_name, score, _ = match
        if score >= threshold:
            for s in stores:
                if s.name == best_name:
                    return int(s.id)

    return int(create_store(name=merchant_name).id)


def _insert_receipt_row(
    store_id: int,
    purchase_date: str,
    subtotal: Optional[float],
    tax: Optional[float],
    total: Optional[float],
    source: str,
    file_path: str,
    image_confidence_1_5: Optional[int],
    azure_request_id: str,
) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO receipts (
                store_id, purchase_date, subtotal_amount, tax_amount, total_amount,
                source, file_path, image_overall_confidence, keep_image_until,
                azure_request_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                store_id,
                purchase_date,
                subtotal,
                tax,
                total,
                source,
                file_path,
                image_confidence_1_5,
                None,
                azure_request_id,
                _now_utc_iso(),
            ),
        )
        rid = int(cur.lastrowid)
        conn.commit()
        return rid


def _save_raw_json_row(receipt_id: int, operation_id: str, json_path: Path, raw_json_dict: Dict[str, Any]) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO receipt_raw_json (receipt_id, operation_id, json_path, raw_json, created_at)
            VALUES (?, ?, ?, ?, ?);
            """,
            (
                int(receipt_id),
                operation_id,
                str(json_path),
                json.dumps(raw_json_dict, ensure_ascii=False),
                _now_utc_iso(),
            ),
        )
        conn.commit()


def _upsert_item_from_mapping(raw_desc: str, mapping: Any) -> Tuple[int, Optional[int]]:
    if getattr(mapping, "item_id", None):
        conf = getattr(mapping, "confidence", None)
        return int(mapping.item_id), _confidence_to_1_5(conf)

    cleaned = (raw_desc or "").strip() or "Unknown Item"
    created = items_repo_module.create_item(canonical_name=cleaned)
    item_id = int(created.id)

    try:
        aliases = ItemAliasesRepo()
        aliases.upsert_alias(alias_text=raw_desc, item_id=item_id, confidence=0.60, source="receipt_auto")
    except Exception:
        pass

    return item_id, 2


def _insert_price_point(
    *,
    item_id: int,
    store_id: int,
    receipt_id: int,
    date: str,
    unit_price: float,
    unit: str,
    quantity: Optional[float],
    total_price: Optional[float],
    raw_name: str,
    confidence_1_5: Optional[int],
    norm_unit_price: Optional[float],
    norm_unit: Optional[str],
    norm_note: Optional[str],
) -> None:
    """
    Inserts into prices, including optional normalization fields.
    Unit normalization schema is ensured by UnitNormalizationService.ensure_schema().
    """
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO prices (
                item_id, store_id, receipt_id, flyer_source_id, source, date,
                unit_price, unit, quantity, total_price, raw_name, confidence,
                norm_unit_price, norm_unit, norm_note,
                created_at
            )
            VALUES (?, ?, ?, NULL, 'receipt', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                int(item_id),
                int(store_id),
                int(receipt_id),
                date,
                float(unit_price),
                unit,
                quantity,
                total_price,
                raw_name,
                confidence_1_5,
                norm_unit_price,
                norm_unit,
                norm_note,
                _now_utc_iso(),
            ),
        )
        conn.commit()


def _insert_receipt_line_item(
    receipt_id: int,
    line_index: int,
    item_id: Optional[int],
    description: str,
    quantity: Optional[float],
    unit_price: Optional[float],
    line_total: Optional[float],
    discount: Optional[float],
    confidence_1_5: Optional[int],
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO receipt_line_items (
                receipt_id, line_index, item_id, description, quantity,
                unit_price, line_total, discount, confidence, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                int(receipt_id),
                int(line_index),
                int(item_id) if item_id else None,
                description,
                quantity,
                unit_price,
                line_total,
                discount,
                confidence_1_5,
                _now_utc_iso(),
            ),
        )
        conn.commit()


def _extract_header_for_signature(analyze_result: Dict[str, Any]) -> Tuple[str, str, Optional[float]]:
    """
    Extract merchant, purchase_date (YYYY-MM-DD), total for signature check BEFORE inserting anything.
    """
    docs = analyze_result.get("documents") or []
    if not docs:
        return "", "", None

    receipt_doc = docs[0]
    fields = receipt_doc.get("fields") or {}

    merchant_val, _ = _field_value(_pick_field(fields, ["MerchantName", "Merchant"]))
    merchant = (merchant_val or "").strip() if isinstance(merchant_val, str) else str(merchant_val or "").strip()

    tx_date_val, _ = _field_value(_pick_field(fields, ["TransactionDate", "Date"]))
    if isinstance(tx_date_val, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", tx_date_val.strip()):
        purchase_date = tx_date_val.strip()
    else:
        purchase_date = ""

    total_val, _ = _field_value(_pick_field(fields, ["Total"]))
    total = _currency_amount(total_val)

    return merchant, purchase_date, total


def ingest_analyzed_receipt_into_db(
    *,
    file_path: str | Path,
    operation_id: str,
    analyze_result: Dict[str, Any],
    saved_json_path: Path,
    store_match_threshold: int = 85,
    file_hash: Optional[str] = None,
) -> int:
    """
    Inserts:
      - receipts row
      - receipt_raw_json row
      - receipt_line_items rows
      - prices rows (with norm fields + multi-buy notes)
    Also links file_hash + signature to receipt for dedupe.
    """
    _ensure_ingest_tables()
    _ensure_dedupe_tables()

    docs = analyze_result.get("documents") or []
    if not docs:
        raise ValueError("No documents found in AnalyzeResult JSON.")

    receipt_doc = docs[0]
    fields = receipt_doc.get("fields") or {}

    # Header fields
    merchant_name_val, merchant_conf = _field_value(_pick_field(fields, ["MerchantName", "Merchant"]))
    merchant_name = (merchant_name_val or "").strip() if isinstance(merchant_name_val, str) else str(merchant_name_val or "").strip()
    store_id = _get_or_create_store_id(merchant_name, threshold=store_match_threshold)

    tx_date_val, tx_date_conf = _field_value(_pick_field(fields, ["TransactionDate", "Date"]))
    if isinstance(tx_date_val, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", tx_date_val.strip()):
        purchase_date = tx_date_val.strip()
    else:
        purchase_date = datetime.now().strftime("%Y-%m-%d")

    subtotal_val, subtotal_conf = _field_value(_pick_field(fields, ["Subtotal"]))
    tax_val, tax_conf = _field_value(_pick_field(fields, ["TotalTax", "Tax"]))
    total_val, total_conf = _field_value(_pick_field(fields, ["Total"]))

    subtotal = _currency_amount(subtotal_val)
    tax = _currency_amount(tax_val)
    total = _currency_amount(total_val)

    signature = _make_receipt_signature(merchant_name, purchase_date, total)

    # overall confidence heuristic
    confs = [c for c in [merchant_conf, tx_date_conf, subtotal_conf, tax_conf, total_conf] if isinstance(c, (int, float))]
    overall_conf_float = (sum(float(x) for x in confs) / len(confs)) if confs else None
    overall_conf_1_5 = _confidence_to_1_5(overall_conf_float)

    receipt_id = _insert_receipt_row(
        store_id=store_id,
        purchase_date=purchase_date,
        subtotal=subtotal,
        tax=tax,
        total=total,
        source="receipt",
        file_path=str(file_path),
        image_confidence_1_5=overall_conf_1_5,
        azure_request_id=operation_id,
    )

    _save_raw_json_row(receipt_id, operation_id, saved_json_path, analyze_result)

    # Mapping engine
    mapping_service = IngredientMappingService(
        items_repo=items_repo_module,
        aliases_repo=ItemAliasesRepo(),
        auto_learn=True,
        learn_threshold=0.90,
        accept_threshold=0.75,
    )

    # Unit normalization + deal parsing
    unit_norm = UnitNormalizationService()
    unit_norm.ensure_schema()

    deals = MultiBuyDealService()

    # Line items
    items_field = _pick_field(fields, ["Items", "ItemList", "LineItems"])
    value_array = items_field.get("valueArray") if isinstance(items_field, dict) else None
    if not isinstance(value_array, list):
        value_array = []

    for idx, elem in enumerate(value_array):
        obj = (elem or {}).get("valueObject") if isinstance(elem, dict) else None
        if not isinstance(obj, dict):
            continue

        desc_val, desc_conf = _field_value(_pick_field(obj, ["Description", "Name", "Item"]))
        qty_val, qty_conf = _field_value(_pick_field(obj, ["Quantity", "Qty"]))
        unit_price_val, unit_price_conf = _field_value(_pick_field(obj, ["UnitPrice", "Price"]))
        total_price_val, total_price_conf = _field_value(_pick_field(obj, ["TotalPrice", "LineTotal", "Amount"]))
        discount_val, discount_conf = _field_value(_pick_field(obj, ["Discount", "DiscountAmount"]))

        description = (desc_val or "").strip() if isinstance(desc_val, str) else str(desc_val or "").strip()
        if not description:
            continue

        quantity = _safe_float(qty_val) or 1.0
        unit_price = _currency_amount(unit_price_val)
        line_total = _currency_amount(total_price_val)
        discount = _currency_amount(discount_val)

        # fill missing pieces
        if unit_price is None and line_total is not None and quantity:
            unit_price = float(line_total) / float(quantity)
        if line_total is None and unit_price is not None and quantity:
            line_total = float(unit_price) * float(quantity)

        # Deal normalization (multi-buy, bogo, etc.)
        adj = deals.adjust(
            description=description,
            quantity=quantity,
            unit_price=unit_price,
            line_total=line_total,
            discount=discount,
        )
        quantity = adj.quantity
        unit_price = adj.unit_price
        line_total = adj.line_total
        deal_note = adj.deal_note

        # Confidence heuristic
        conf_candidates = [c for c in [desc_conf, qty_conf, unit_price_conf, total_price_conf, discount_conf] if isinstance(c, (int, float))]
        line_conf_float = (sum(float(x) for x in conf_candidates) / len(conf_candidates)) if conf_candidates else None
        line_conf_1_5 = _confidence_to_1_5(line_conf_float)

        mapping = mapping_service.map_to_item(description)
        item_id, map_conf_1_5 = _upsert_item_from_mapping(description, mapping)

        # Determine observed unit (best effort from text)
        observed_unit = "each"
        guessed = unit_norm.guess_unit_from_text(description)
        if guessed != "unknown":
            observed_unit = guessed

        # Write line item row (store the adjusted values)
        _insert_receipt_line_item(
            receipt_id=receipt_id,
            line_index=idx,
            item_id=item_id,
            description=description,
            quantity=quantity,
            unit_price=unit_price,
            line_total=line_total,
            discount=discount,
            confidence=line_conf_1_5 or map_conf_1_5,
        )

        # Price point (only if we have an effective unit price)
        if unit_price is not None:
            norm = unit_norm.normalize(
                item_id=item_id,
                unit_price=float(unit_price),
                observed_unit=observed_unit,
                description=description,
            )
            combined_note = f"{norm.note};{deal_note}" if deal_note else norm.note

            _insert_price_point(
                item_id=item_id,
                store_id=store_id,
                receipt_id=receipt_id,
                date=purchase_date,
                unit_price=float(unit_price),
                unit=observed_unit,
                quantity=quantity,
                total_price=line_total,
                raw_name=description,
                confidence_1_5=(line_conf_1_5 or map_conf_1_5),
                norm_unit_price=float(norm.norm_unit_price) if norm.norm_unit_price is not None else None,
                norm_unit=norm.norm_unit,
                norm_note=combined_note,
            )

    # Link dedupe keys
    if file_hash:
        _link_hash_to_receipt(file_hash, receipt_id, str(file_path))
    if signature:
        _link_signature_to_receipt(signature, receipt_id)

    return receipt_id


# =============================================================================
# Public entrypoints
# =============================================================================

def ingest_receipt_file_outcome(
    file_path: str | Path,
    *,
    raw_json_dir: str | Path = "azure_raw_json",
    locale: str = "en-US",
    store_match_threshold: int = 85,
    replace_existing: bool = False,
) -> IngestOutcome:
    """
    Sequential ingest for ONE receipt file with dedupe logic.

    Behavior:
      1) file-hash dedupe before Azure call
      2) analyze with Azure + save JSON
      3) signature dedupe (merchant+date+total) before DB insert
      4) ingest into DB
    """
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(str(p))

    _ensure_ingest_tables()
    _ensure_dedupe_tables()

    # ---- 1) FILE HASH DEDUPE (no Azure call) ----
    file_hash = _compute_file_sha256(p)
    existing = _find_receipt_by_file_hash(file_hash)
    if existing is not None:
        if replace_existing:
            _delete_receipt_cascade(existing)
            replaced = True
        else:
            return IngestOutcome(
                receipt_id=int(existing),
                was_duplicate=True,
                duplicate_reason="file_hash",
                replaced_existing=False,
                existing_receipt_id=int(existing),
            )
    else:
        replaced = False

    # ---- 2) AZURE ANALYZE ----
    client = AzureReceiptClient(locale=locale)
    az = client.analyze_and_save_json(file_path=p, raw_json_dir=raw_json_dir)

    # ---- 3) SIGNATURE DEDUPE (catches rescans) ----
    merchant, purchase_date, total = _extract_header_for_signature(az.analyze_result)
    signature = _make_receipt_signature(merchant, purchase_date, total)
    if signature:
        existing_sig = _find_receipt_by_signature(signature)
        if existing_sig is not None:
            if replace_existing:
                _delete_receipt_cascade(existing_sig)
                replaced = True
            else:
                # discard the new attempt (don’t add to DB)
                try:
                    az.saved_json_path.unlink(missing_ok=True)
                except Exception:
                    pass
                return IngestOutcome(
                    receipt_id=int(existing_sig),
                    was_duplicate=True,
                    duplicate_reason="signature",
                    replaced_existing=False,
                    existing_receipt_id=int(existing_sig),
                )

    # ---- 4) INGEST INTO DB ----
    new_receipt_id = ingest_analyzed_receipt_into_db(
        file_path=p,
        operation_id=az.operation_id,
        analyze_result=az.analyze_result,
        saved_json_path=az.saved_json_path,
        store_match_threshold=store_match_threshold,
        file_hash=file_hash,
    )

    return IngestOutcome(
        receipt_id=int(new_receipt_id),
        was_duplicate=False,
        duplicate_reason=None,
        replaced_existing=replaced,
        existing_receipt_id=None,
    )


def ingest_receipt_file(
    file_path: str | Path,
    raw_json_dir: str | Path = "azure_raw_json",
    locale: str = "en-US",
    store_match_threshold: int = 85,
) -> int:
    """
    Backwards compatible: returns receipt_id.
    Uses dedupe default behavior (skip duplicates).
    """
    outcome = ingest_receipt_file_outcome(
        file_path=file_path,
        raw_json_dir=raw_json_dir,
        locale=locale,
        store_match_threshold=store_match_threshold,
        replace_existing=False,
    )
    return outcome.receipt_id
