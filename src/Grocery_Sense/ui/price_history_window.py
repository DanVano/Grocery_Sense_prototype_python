"""
Grocery_Sense.ui.price_history_window

Price History Viewer (Prototype UI)

Features:
- Choose an item (tracked items by default)
- Show avg/min/max by store for last 30/60/90 days
- Show "best store recently" (lowest avg in the selected window)
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox
from dataclasses import dataclass
from typing import Optional, Dict, List, Tuple
from Grocery_Sense.data.repositories.items_repo import list_items
from Grocery_Sense.data.repositories.stores_repo import list_stores
from Grocery_Sense.services.price_history_service import PriceHistoryService


# ----------------------------
# Helpers
# ----------------------------

def _fmt_money(v: Optional[float]) -> str:
    if v is None:
        return "-"
    return f"${v:,.2f}"


@dataclass
class StoreStats:
    store_id: int
    store_name: str
    window_days: int
    avg_price: Optional[float]
    min_price: Optional[float]
    max_price: Optional[float]
    sample_count: int
    unit_hint: str
    most_recent_date: str  # ISO date string or ""


# ----------------------------
# Main Window
# ----------------------------

class PriceHistoryWindow(tk.Toplevel):
    def __init__(self, master: Optional[tk.Misc] = None) -> None:
        super().__init__(master)
        self.title("Price History Viewer")
        self.geometry("980x620")
        self.minsize(900, 560)

        # UI state
        self.only_tracked_var = tk.BooleanVar(value=True)
        self.search_var = tk.StringVar(value="")
        self.item_var = tk.StringVar(value="")
        self.selected_item_id: Optional[int] = None

        self._item_name_to_id: Dict[str, int] = {}
        self._item_names: List[str] = []

        self._stores = list_stores(only_favorites=False, order_by_priority=True)
        self._price_svc = PriceHistoryService()

        # Build UI
        self._build_header()
        self._build_tabs()
        self._build_footer()

        # Load initial items + initial render
        self._refresh_item_list()
        self._select_first_item_if_any()

    # ----------------------------
    # UI Construction
    # ----------------------------

    def _build_header(self) -> None:
        header = ttk.Frame(self, padding=10)
        header.pack(fill="x")

        # Row 1: search + tracked toggle
        row1 = ttk.Frame(header)
        row1.pack(fill="x")

        ttk.Label(row1, text="Item search:").pack(side="left")

        search_entry = ttk.Entry(row1, textvariable=self.search_var, width=40)
        search_entry.pack(side="left", padx=(6, 10))
        search_entry.bind("<Return>", lambda e: self._refresh_item_list())

        tracked_cb = ttk.Checkbutton(
            row1,
            text="Tracked items only",
            variable=self.only_tracked_var,
            command=self._refresh_item_list,
        )
        tracked_cb.pack(side="left")

        ttk.Button(row1, text="Search", command=self._refresh_item_list).pack(side="left", padx=(10, 0))

        # Row 2: combobox
        row2 = ttk.Frame(header)
        row2.pack(fill="x", pady=(10, 0))

        ttk.Label(row2, text="Choose item:").pack(side="left")

        self.item_combo = ttk.Combobox(
            row2,
            textvariable=self.item_var,
            values=[],
            width=60,
            state="readonly",
        )
        self.item_combo.pack(side="left", padx=(6, 10))
        self.item_combo.bind("<<ComboboxSelected>>", lambda e: self._on_item_changed())

        ttk.Button(row2, text="Refresh Stats", command=self._refresh_stats).pack(side="left")

    def _build_tabs(self) -> None:
        body = ttk.Frame(self, padding=(10, 0, 10, 10))
        body.pack(fill="both", expand=True)

        self.tabs = ttk.Notebook(body)
        self.tabs.pack(fill="both", expand=True)

        self._trees: Dict[int, ttk.Treeview] = {}

        for days in (30, 60, 90):
            tab = ttk.Frame(self.tabs, padding=8)
            self.tabs.add(tab, text=f"Last {days} days")

            tree = ttk.Treeview(
                tab,
                columns=("store", "avg", "min", "max", "count", "unit", "recent"),
                show="headings",
                height=16,
            )
            tree.heading("store", text="Store")
            tree.heading("avg", text="Avg")
            tree.heading("min", text="Min")
            tree.heading("max", text="Max")
            tree.heading("count", text="# Samples")
            tree.heading("unit", text="Unit")
            tree.heading("recent", text="Most Recent")

            tree.column("store", width=260, anchor="w")
            tree.column("avg", width=90, anchor="e")
            tree.column("min", width=90, anchor="e")
            tree.column("max", width=90, anchor="e")
            tree.column("count", width=90, anchor="e")
            tree.column("unit", width=100, anchor="w")
            tree.column("recent", width=140, anchor="w")

            vsb = ttk.Scrollbar(tab, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=vsb.set)

            tree.pack(side="left", fill="both", expand=True)
            vsb.pack(side="right", fill="y")

            self._trees[days] = tree

        self.tabs.bind("<<NotebookTabChanged>>", lambda e: self._update_best_store_label())

    def _build_footer(self) -> None:
        footer = ttk.Frame(self, padding=10)
        footer.pack(fill="x")

        self.best_store_label = ttk.Label(footer, text="Best store recently: -")
        self.best_store_label.pack(side="left")

        self.summary_label = ttk.Label(footer, text="", foreground="#444")
        self.summary_label.pack(side="right")

    # ----------------------------
    # Item Loading
    # ----------------------------

    def _refresh_item_list(self) -> None:
        """
        Refresh combobox items based on search + tracked toggle.

        Repo API:
        list_items(include_untracked: bool = False)
        """
        try:
            include_untracked = not bool(self.only_tracked_var.get())
            items = list_items(include_untracked=include_untracked)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load items.\n\n{e}")
            return

        query = (self.search_var.get() or "").strip().lower()

        self._item_name_to_id.clear()
        self._item_names.clear()

        for it in items:
            name = (getattr(it, "canonical_name", "") or "").strip()
            if not name:
                continue
            if query and query not in name.lower():
                continue
            self._item_name_to_id[name] = int(it.id)
            self._item_names.append(name)

        self._item_names.sort(key=lambda s: s.lower())
        self.item_combo["values"] = self._item_names

        current = self.item_var.get().strip()
        if current and current in self._item_name_to_id:
            self.selected_item_id = self._item_name_to_id[current]
        else:
            self.item_var.set("")
            self.selected_item_id = None
            self._clear_tables()
            self._update_best_store_label()


    def _select_first_item_if_any(self) -> None:
        if self._item_names:
            self.item_var.set(self._item_names[0])
            self.selected_item_id = self._item_name_to_id[self._item_names[0]]
            self._refresh_stats()

    def _on_item_changed(self) -> None:
        name = self.item_var.get().strip()
        self.selected_item_id = self._item_name_to_id.get(name)
        self._refresh_stats()

    # ----------------------------
    # Stats Loading
    # ----------------------------

    def _clear_tables(self) -> None:
        for tree in self._trees.values():
            tree.delete(*tree.get_children())
        self.summary_label.config(text="")

    def _compute_stats_for_store(self, item_id: int, store_id: int, store_name: str, window_days: int) -> StoreStats:
        stats = self._price_svc.stats_for_item_by_store(
            item_id=item_id,
            store_id=store_id,
            window_days=window_days,
        )
        return StoreStats(
            store_id=store_id,
            store_name=store_name,
            window_days=window_days,
            avg_price=stats["avg_price"],
            min_price=stats["min_price"],
            max_price=stats["max_price"],
            sample_count=stats["sample_count"],
            unit_hint=stats["unit_hint"],
            most_recent_date=stats["most_recent_date"],
        )

    def _refresh_stats(self) -> None:
        if not self.selected_item_id:
            self._clear_tables()
            self.best_store_label.config(text="Best store recently: -")
            return

        item_name = self.item_var.get().strip()
        if not item_name:
            self._clear_tables()
            self.best_store_label.config(text="Best store recently: -")
            return

        # Clear and rebuild for each tab/window
        total_samples_by_window: Dict[int, int] = {}

        for days, tree in self._trees.items():
            tree.delete(*tree.get_children())

            window_total = 0
            rows: List[StoreStats] = []
            for s in self._stores:
                stats = self._compute_stats_for_store(
                    item_id=self.selected_item_id,
                    store_id=s.id,
                    store_name=s.name,
                    window_days=days,
                )
                rows.append(stats)
                window_total += stats.sample_count

            total_samples_by_window[days] = window_total

            # Sort stores: favorites first, then priority desc, then name
            # (stores_repo.list_stores already orders, but keep safe)
            rows.sort(key=lambda r: r.store_name.lower())

            for st in rows:
                tree.insert(
                    "",
                    "end",
                    values=(
                        st.store_name,
                        _fmt_money(st.avg_price),
                        _fmt_money(st.min_price),
                        _fmt_money(st.max_price),
                        st.sample_count,
                        st.unit_hint or "",
                        st.most_recent_date or "",
                    ),
                )

        self.summary_label.config(
            text=f"Samples: 30d={total_samples_by_window.get(30,0)} | 60d={total_samples_by_window.get(60,0)} | 90d={total_samples_by_window.get(90,0)}"
        )

        self._update_best_store_label()

    def _update_best_store_label(self) -> None:
        """
        Determine best store (lowest avg) for the selected tab window.
        """
        if not self.selected_item_id:
            self.best_store_label.config(text="Best store recently: -")
            return

        # Which window is selected?
        tab_index = self.tabs.index(self.tabs.select())
        window_days = (30, 60, 90)[tab_index]

        best: Optional[Tuple[str, float, int]] = None  # (store_name, avg_price, sample_count)

        for s in self._stores:
            stats = self._compute_stats_for_store(self.selected_item_id, s.id, s.name, window_days)
            if stats.avg_price is None or stats.sample_count <= 0:
                continue
            if best is None or stats.avg_price < best[1]:
                best = (stats.store_name, stats.avg_price, stats.sample_count)

        if best is None:
            self.best_store_label.config(text=f"Best store recently ({window_days}d): No data")
        else:
            self.best_store_label.config(
                text=f"Best store recently ({window_days}d): {best[0]} (avg {_fmt_money(best[1])}, n={best[2]})"
            )


# ----------------------------
# Convenience launcher
# ----------------------------

def open_price_history_window(master: Optional[tk.Misc] = None) -> PriceHistoryWindow:
    """
    Call this from your main Tkinter app.
    """
    return PriceHistoryWindow(master)
