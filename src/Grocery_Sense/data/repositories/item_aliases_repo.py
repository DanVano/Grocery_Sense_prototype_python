from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, List
from datetime import datetime, timezone

from Grocery_Sense.data.connection import get_connection



@dataclass
class ItemAlias:
    id: int
    alias_text: str
    item_id: int
    confidence: float
    source: str
    created_at: str
    last_seen_at: Optional[str]
    times_seen: int


class ItemAliasesRepo:
    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path

    def get_by_alias(self, alias_text: str) -> Optional[ItemAlias]:
        alias_text = alias_text.strip().lower()
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT id, alias_text, item_id, confidence, source, created_at, last_seen_at, times_seen
                FROM item_aliases
                WHERE alias_text = ?
                """,
                (alias_text,),
            ).fetchone()

        if not row:
            return None
        return ItemAlias(*row)

    def upsert_alias(
        self,
        alias_text: str,
        item_id: int,
        confidence: float = 1.0,
        source: str = "manual",
    ) -> None:
        alias_text = alias_text.strip().lower()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO item_aliases (alias_text, item_id, confidence, source, created_at, last_seen_at, times_seen)
                VALUES (?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(alias_text) DO UPDATE SET
                    item_id = excluded.item_id,
                    confidence = excluded.confidence,
                    source = excluded.source,
                    last_seen_at = excluded.last_seen_at,
                    times_seen = item_aliases.times_seen + 1
                """,
                (alias_text, item_id, confidence, source, now, now),
            )
            conn.commit()

    def mark_seen(self, alias_text: str) -> None:
        alias_text = alias_text.strip().lower()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with get_connection() as conn:
            conn.execute(
                """
                UPDATE item_aliases
                SET last_seen_at = ?, times_seen = times_seen + 1
                WHERE alias_text = ?
                """,
                (now, alias_text),
            )
            conn.commit()

    def list_all(self) -> List[ItemAlias]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, alias_text, item_id, confidence, source, created_at, last_seen_at, times_seen
                FROM item_aliases
                ORDER BY times_seen DESC, alias_text ASC
                """
            ).fetchall()
        return [ItemAlias(*r) for r in rows]
