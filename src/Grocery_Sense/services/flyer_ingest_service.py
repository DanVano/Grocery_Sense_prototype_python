from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from Grocery_Sense.data.repositories.flyers_repo import FlyersRepo, compute_sha256
from Grocery_Sense.integrations.flyer_docint_client import FlyerDocIntClient

from Grocery_Sense.services.multibuy_deal_service import MultiBuyDealService
from Grocery_Sense.services.unit_normalization_service import UnitNormalizationService


_PRICE_ANY = re.compile(r"(\$?\s*\d+(?:\.\d{2})?)")
_MONEY = re.compile(r"\$?\s*(\d+(?:\.\d{2})?)")


def _guess_asset_type(path: Path) -> str:
    ext = path.suffix.lower().lstrip(".")
    if ext in ("pdf",):
        return "pdf"
    return "image"


def _safe_float_money(s: str) -> Optional[float]:
    if not s:
        return None
    m = _MONEY.search(s)
    if not m:
        return None
    try:
        return float(m.group(1))
    except Exception:
        return None


@dataclass(frozen=True)
class FlyerIngestResult:
    flyer_id: int
    assets_count: int
    deals_count: int
    raw_json_count: int


class FlyerIngestService:
    """
    Manual flyer ingestion pipeline (Option 3 now; future-proof for Option 2).

    Inputs:
      - PDF/images -> Azure prebuilt-layout -> raw json -> best-effort deal extraction
      - OR extracted DealRecords JSON -> stored directly
    """

    def __init__(self) -> None:
        self.repo = FlyersRepo()
        self.azure = FlyerDocIntClient()
        self.deals = MultiBuyDealService()
        self.unit_norm = UnitNormalizationService()

    # -----------------------
    # Public entrypoints
    # -----------------------

    def ingest_assets(
        self,
        *,
        store_id: Optional[int],
        valid_from: Optional[str],
        valid_to: Optional[str],
        file_paths: List[str],
        raw_json_dir: str,
        source_type: str = "manual_upload",
        source_ref: Optional[str] = None,
        note: Optional[str] = None,
        try_item_mapping: bool = True,
    ) -> FlyerIngestResult:
        """
        Ingest PDFs/images: store assets + azure raw json + extracted deals.
        """
        self.repo.ensure_schema()
        self.unit_norm.ensure_schema()

        flyer_id = self.repo.create_flyer_batch(
            store_id=store_id,
            valid_from=valid_from,
            valid_to=valid_to,
            source_type=source_type,
            source_ref=source_ref,
            note=note,
        )

        raw_dir = Path(raw_json_dir)
        raw_dir.mkdir(parents=True, exist_ok=True)

        assets_count = 0
        raw_count = 0
        deals_count = 0

        mapper = self._get_mapper_if_available() if try_item_mapping else None

        for fp in file_paths:
            p = Path(fp)
            if not p.exists():
                continue

            asset_type = _guess_asset_type(p)
            sha = compute_sha256(p)
            asset_id = self.repo.add_asset(
                flyer_id=flyer_id,
                asset_type=asset_type,
                path=str(p),
                sha256=sha,
            )
            assets_count += 1

            # Azure layout
            az = self.azure.analyze_layout_file(p)

            # Save JSON file (so you can reprocess without re-paying Azure)
            safe_stem = re.sub(r"[^a-zA-Z0-9_\-]+", "_", p.stem)[:80]
            json_path = raw_dir / f"{safe_stem}__{az.operation_id}.json"
            json_path.write_text(json.dumps(az.analyze_result, ensure_ascii=False, indent=2), encoding="utf-8")

            self.repo.add_raw_json(
                flyer_id=flyer_id,
                asset_id=asset_id,
                operation_id=az.operation_id,
                json_path=str(json_path),
                raw_json_dict=az.analyze_result,
                model_id="prebuilt-layout",
            )
            raw_count += 1

            # Extract deal candidates
            extracted = self._extract_deals_from_layout(az.analyze_result)

            # Normalize + persist
            for d in extracted:
                title = d.get("title") or ""
                description = d.get("description") or title
                price_text = d.get("price_text")

                # Parse deal text into unit price / qty
                # We don't always have qty/total in flyers; start conservative.
                qty_guess = d.get("deal_qty")  # may be None
                unit_price_guess = d.get("unit_price")
                line_total_guess = d.get("deal_total")
                discount_guess = None

                adj = self.deals.adjust(
                    description=f"{title} {price_text or ''}".strip(),
                    quantity=qty_guess or 1.0,
                    unit_price=unit_price_guess,
                    line_total=line_total_guess,
                    discount=discount_guess,
                )

                # Observed unit guessed from text
                observed_unit = self.unit_norm.guess_unit_from_text(f"{title} {description}")
                if observed_unit == "unknown":
                    observed_unit = "each"

                # Optional item mapping -> then normalize into item's default unit
                item_id = None
                map_conf = None

                norm_unit_price = adj.unit_price
                norm_unit = observed_unit
                norm_note = f"flyer;{adj.deal_note}"

                if mapper is not None:
                    item_id, map_conf = self._map_to_item(mapper, f"{title} {description}".strip())
                    if item_id is not None and adj.unit_price is not None:
                        norm = self.unit_norm.normalize(
                            item_id=item_id,
                            unit_price=float(adj.unit_price),
                            observed_unit=observed_unit,
                            description=f"{title} {description}".strip(),
                        )
                        norm_unit_price = float(norm.norm_unit_price)
                        norm_unit = norm.norm_unit
                        norm_note = f"{norm.note};{adj.deal_note};flyer"

                self.repo.add_deal(
                    flyer_id=flyer_id,
                    store_id=store_id,
                    asset_id=asset_id,
                    page_index=d.get("page_index"),

                    title=title,
                    description=description,
                    price_text=price_text,

                    deal_qty=float(adj.quantity) if adj.quantity is not None else None,
                    deal_total=float(adj.line_total) if adj.line_total is not None else None,
                    unit_price=float(adj.unit_price) if adj.unit_price is not None else None,
                    unit=observed_unit,

                    norm_unit_price=float(norm_unit_price) if norm_unit_price is not None else None,
                    norm_unit=norm_unit,
                    norm_note=norm_note,

                    item_id=item_id,
                    mapping_confidence=float(map_conf) if map_conf is not None else None,
                    confidence=d.get("confidence"),
                )
                deals_count += 1

        return FlyerIngestResult(
            flyer_id=flyer_id,
            assets_count=assets_count,
            deals_count=deals_count,
            raw_json_count=raw_count,
        )

    def ingest_dealrecords_json(
        self,
        *,
        store_id: Optional[int],
        valid_from: Optional[str],
        valid_to: Optional[str],
        dealrecords_path: str,
        source_type: str = "manual_upload",
        source_ref: Optional[str] = None,
        note: Optional[str] = None,
        try_item_mapping: bool = True,
    ) -> FlyerIngestResult:
        """
        Ingest already-extracted DealRecords from a JSON file containing a list[dict].
        Each record should ideally have: title/description/price_text.
        """
        self.repo.ensure_schema()
        self.unit_norm.ensure_schema()

        p = Path(dealrecords_path)
        if not p.exists():
            raise FileNotFoundError(str(p))

        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("DealRecords JSON must be a list of objects.")

        flyer_id = self.repo.create_flyer_batch(
            store_id=store_id,
            valid_from=valid_from,
            valid_to=valid_to,
            source_type=source_type,
            source_ref=source_ref or str(p),
            note=note,
        )

        mapper = self._get_mapper_if_available() if try_item_mapping else None

        deals_count = 0
        for rec in data:
            if not isinstance(rec, dict):
                continue

            title = (rec.get("title") or rec.get("name") or "").strip()
            description = (rec.get("description") or "").strip()
            price_text = (rec.get("price_text") or rec.get("price") or "").strip() or None

            if not title and not description:
                continue

            # try parse an obvious unit price from price_text like "$2.99"
            unit_price_guess = _safe_float_money(price_text or "")
            qty_guess = rec.get("deal_qty") or rec.get("quantity") or 1.0
            deal_total_guess = rec.get("deal_total") or rec.get("total_price")

            adj = self.deals.adjust(
                description=f"{title} {price_text or ''}".strip(),
                quantity=float(qty_guess) if qty_guess else 1.0,
                unit_price=float(unit_price_guess) if unit_price_guess is not None else None,
                line_total=float(deal_total_guess) if deal_total_guess is not None else None,
                discount=None,
            )

            observed_unit = self.unit_norm.guess_unit_from_text(f"{title} {description}")
            if observed_unit == "unknown":
                observed_unit = "each"

            item_id = None
            map_conf = None

            norm_unit_price = adj.unit_price
            norm_unit = observed_unit
            norm_note = f"dealrecords;{adj.deal_note}"

            if mapper is not None and adj.unit_price is not None:
                item_id, map_conf = self._map_to_item(mapper, f"{title} {description}".strip())
                if item_id is not None:
                    norm = self.unit_norm.normalize(
                        item_id=item_id,
                        unit_price=float(adj.unit_price),
                        observed_unit=observed_unit,
                        description=f"{title} {description}".strip(),
                    )
                    norm_unit_price = float(norm.norm_unit_price)
                    norm_unit = norm.norm_unit
                    norm_note = f"{norm.note};{adj.deal_note};dealrecords"

            self.repo.add_deal(
                flyer_id=flyer_id,
                store_id=store_id,
                asset_id=None,
                page_index=rec.get("page_index"),

                title=title,
                description=description,
                price_text=price_text,

                deal_qty=float(adj.quantity) if adj.quantity is not None else None,
                deal_total=float(adj.line_total) if adj.line_total is not None else None,
                unit_price=float(adj.unit_price) if adj.unit_price is not None else None,
                unit=observed_unit,

                norm_unit_price=float(norm_unit_price) if norm_unit_price is not None else None,
                norm_unit=norm_unit,
                norm_note=norm_note,

                item_id=item_id,
                mapping_confidence=float(map_conf) if map_conf is not None else None,
                confidence=rec.get("confidence"),
            )
            deals_count += 1

        return FlyerIngestResult(
            flyer_id=flyer_id,
            assets_count=0,
            deals_count=deals_count,
            raw_json_count=0,
        )

    # -----------------------
    # Extractor v1 (heuristic)
    # -----------------------

    def _extract_deals_from_layout(self, analyze_result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Best-effort v1:
          - walk line text on each page
          - when a line contains a price-like token, treat it as a deal anchor
          - use 1-2 prior lines as the description
        """
        out: List[Dict[str, Any]] = []

        pages = analyze_result.get("pages") or []
        if not isinstance(pages, list):
            return out

        for pi, page in enumerate(pages):
            lines = page.get("lines") or []
            if not isinstance(lines, list):
                continue

            # Flatten to texts
            texts: List[str] = []
            confs: List[Optional[float]] = []
            for ln in lines:
                t = (ln or {}).get("content")
                if not t:
                    continue
                texts.append(str(t).strip())
                confs.append((ln or {}).get("confidence"))

            for i, t in enumerate(texts):
                price_text = self._extract_price_text(t)
                if not price_text:
                    continue

                prev1 = texts[i - 1].strip() if i - 1 >= 0 else ""
                prev2 = texts[i - 2].strip() if i - 2 >= 0 else ""

                title = prev1 if prev1 else t
                description = " ".join([x for x in (prev2, prev1) if x]).strip()
                if not description:
                    description = title

                # crude confidence: max of nearby line confidences if present
                c = None
                for j in (i, i - 1, i - 2):
                    if 0 <= j < len(confs) and isinstance(confs[j], (int, float)):
                        c = max(c or 0.0, float(confs[j]))
                out.append(
                    {
                        "page_index": pi,
                        "title": title[:180],
                        "description": description[:400],
                        "price_text": price_text[:50],
                        "confidence": c,
                    }
                )

        return out

    def _extract_price_text(self, text: str) -> Optional[str]:
        """
        Finds patterns like:
          - $2.99
          - 2/$5
          - 3 for 10
          - 2 @ 4.00
        Returns the substring that looks like the price anchor.
        """
        t = (text or "").strip()
        if not t:
            return None

        # Common flyer patterns
        if re.search(r"\b\d+\s*/\s*\$?\s*\d+(?:\.\d+)?\b", t):
            return re.search(r"\b\d+\s*/\s*\$?\s*\d+(?:\.\d+)?\b", t).group(0)
        if re.search(r"\b\d+\s*for\s*\$?\s*\d+(?:\.\d+)?\b", t, flags=re.IGNORECASE):
            return re.search(r"\b\d+\s*for\s*\$?\s*\d+(?:\.\d+)?\b", t, flags=re.IGNORECASE).group(0)
        if re.search(r"\b\d+\s*@\s*\$?\s*\d+(?:\.\d+)?\b", t):
            return re.search(r"\b\d+\s*@\s*\$?\s*\d+(?:\.\d+)?\b", t).group(0)

        # Basic $ amount
        m = re.search(r"\$\s*\d+(?:\.\d{2})", t)
        if m:
            return m.group(0)

        # Fallback: number with .dd (no $)
        m = re.search(r"\b\d+\.\d{2}\b", t)
        if m:
            return m.group(0)

        return None

    # -----------------------
    # Optional item mapping
    # -----------------------

    def _get_mapper_if_available(self):
        """
        Uses your Ingredient Mapping Engine if present.
        """
        try:
            from Grocery_Sense.services.ingredient_mapping_service import IngredientMappingService
            from Grocery_Sense.data.repositories.item_aliases_repo import ItemAliasesRepo
            from Grocery_Sense.data.repositories import items_repo as items_repo_module

            return IngredientMappingService(
                items_repo=items_repo_module,
                aliases_repo=ItemAliasesRepo(),
                auto_learn=True,
                learn_threshold=0.90,
                accept_threshold=0.75,
            )
        except Exception:
            return None

    def _map_to_item(self, mapper, text: str) -> Tuple[Optional[int], Optional[float]]:
        """
        Returns (item_id, confidence_float).
        """
        try:
            m = mapper.map_to_item(text)
            item_id = getattr(m, "item_id", None)
            conf = getattr(m, "confidence", None)
            if item_id is None:
                return None, None
            return int(item_id), float(conf) if conf is not None else None
        except Exception:
            return None, None
