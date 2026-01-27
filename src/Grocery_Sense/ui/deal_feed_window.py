from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox
from typing import Any, Callable, Dict, List, Optional

from Grocery_Sense.data.repositories.flyers_repo import FlyersRepo


class DealFeedWindow(tk.Toplevel):
    """Preference-aware Deal Feed (active flyer deals only)."""

    def __init__(self, master: Optional[tk.Misc] = None, *, log: Optional[Callable[[str], None]] = None) -> None:
        super().__init__(master)
        self.title("Deal Feed — Active Flyers")
        self.geometry("1040x720")
        self.minsize(960, 620)

        self._log = log
        self._repo = FlyersRepo()

        self._store_label_to_id: Dict[str, Optional[int]] = {"All stores": None}
        self._store_var = tk.StringVar(value="All stores")
        self._search_var = tk.StringVar(value="")

        self._show_soft_var = tk.BooleanVar(value=True)
        self._include_disallowed_oils_var = tk.BooleanVar(value=False)

        self._all_deals: List[Dict[str, Any]] = []
        self._filtered_deals: List[Dict[str, Any]] = []

        # tooltip state
        self._tip: Optional[tk.Toplevel] = None
        self._tip_label: Optional[ttk.Label] = None
        self._tip_iid: Optional[str] = None

        self._build_ui()
        self._load_stores()
        self._refresh()

    # ---------------- UI ----------------

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        header = ttk.Frame(root)
        header.pack(fill="x")

        ttk.Label(header, text="Deal Feed", font=("Segoe UI", 12, "bold")).pack(side="left")
        ttk.Label(
            header,
            text="Active flyer deals only • hard excludes removed • soft excludes marked with * (hover * for why)",
            foreground="#888",
        ).pack(side="left", padx=(12, 0))

        filters = ttk.LabelFrame(root, text="Filters", padding=10)
        filters.pack(fill="x", pady=(10, 10))

        ttk.Label(filters, text="Store:").grid(row=0, column=0, sticky="w")
        self._store_combo = ttk.Combobox(filters, state="readonly", width=28, textvariable=self._store_var)
        self._store_combo.grid(row=0, column=1, sticky="w", padx=(8, 18))
        self._store_combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh())

        ttk.Label(filters, text="Search:").grid(row=0, column=2, sticky="w")
        self._search_entry = ttk.Entry(filters, textvariable=self._search_var, width=40)
        self._search_entry.grid(row=0, column=3, sticky="w", padx=(8, 18))
        self._search_entry.bind("<KeyRelease>", lambda _e: self._apply_local_filters())

        ttk.Checkbutton(
            filters,
            text="Show soft-excluded deals (*)",
            variable=self._show_soft_var,
            command=self._apply_local_filters,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))

        ttk.Checkbutton(
            filters,
            text="Include disallowed oils",
            variable=self._include_disallowed_oils_var,
            command=self._refresh,
        ).grid(row=1, column=2, columnspan=2, sticky="w", pady=(8, 0))

        ttk.Button(filters, text="Refresh", command=self._refresh).grid(row=0, column=4, sticky="e")
        filters.columnconfigure(4, weight=1)

        body = ttk.PanedWindow(root, orient="horizontal")
        body.pack(fill="both", expand=True)

        left = ttk.Frame(body, padding=(0, 0, 10, 0))
        right = ttk.Frame(body)

        body.add(left, weight=3)
        body.add(right, weight=2)

        cols = ("store", "item", "price", "unit", "dates", "flags")
        self._tree = ttk.Treeview(left, columns=cols, show="headings", height=20)
        self._tree.heading("store", text="Store")
        self._tree.heading("item", text="Item")
        self._tree.heading("price", text="Price")
        self._tree.heading("unit", text="Unit")
        self._tree.heading("dates", text="Valid")
        self._tree.heading("flags", text="Flags")

        self._tree.column("store", width=120, anchor="w")
        self._tree.column("item", width=360, anchor="w")
        self._tree.column("price", width=90, anchor="w")
        self._tree.column("unit", width=90, anchor="w")
        self._tree.column("dates", width=140, anchor="w")
        self._tree.column("flags", width=70, anchor="center")

        vsb = ttk.Scrollbar(left, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)

        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)

        self._tree.bind("<<TreeviewSelect>>", lambda _e: self._on_selected())

        # tooltip bindings
        self._tree.bind("<Motion>", self._on_tree_motion)
        self._tree.bind("<Leave>", lambda _e: self._hide_tooltip())

        ttk.Label(right, text="Details", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self._detail = tk.Text(right, height=10, wrap="word")
        self._detail.pack(fill="both", expand=True, pady=(6, 0))
        self._detail.configure(state="disabled")

    # ---------------- tooltip ----------------

    def _ensure_tooltip(self) -> None:
        if self._tip is not None:
            return
        self._tip = tk.Toplevel(self)
        self._tip.withdraw()
        self._tip.overrideredirect(True)
        self._tip.attributes("-topmost", True)
        frm = ttk.Frame(self._tip, padding=8)
        frm.pack(fill="both", expand=True)
        self._tip_label = ttk.Label(frm, text="", justify="left")
        self._tip_label.pack()

    def _show_tooltip(self, x_root: int, y_root: int, text: str, *, iid: str) -> None:
        self._ensure_tooltip()
        if not self._tip or not self._tip_label:
            return
        self._tip_iid = iid
        self._tip_label.configure(text=text)
        self._tip.geometry(f"+{x_root}+{y_root}")
        self._tip.deiconify()

    def _hide_tooltip(self) -> None:
        self._tip_iid = None
        if self._tip:
            try:
                self._tip.withdraw()
            except Exception:
                pass

    def _on_tree_motion(self, e) -> None:
        # Identify hovered row
        iid = self._tree.identify_row(e.y)
        if not iid:
            self._hide_tooltip()
            return

        # Avoid re-rendering tooltip if still on same row
        if self._tip_iid == iid and self._tip and self._tip.state() == "normal":
            return

        # Map iid -> deal
        try:
            idx = int(iid)
        except Exception:
            self._hide_tooltip()
            return
        if idx < 0 or idx >= len(self._filtered_deals):
            self._hide_tooltip()
            return

        d = self._filtered_deals[idx]
        soft_by = d.get("pref_soft_excluded_by", [])
        hits = d.get("pref_soft_excluded_hits", [])

        if not (isinstance(soft_by, list) and len(soft_by) > 0):
            self._hide_tooltip()
            return

        hit_txt = ", ".join(str(x) for x in hits if str(x).strip()) or "(match unknown)"
        by_txt = ", ".join(str(x) for x in soft_by if str(x).strip()) or "(unknown)"
        tip_text = (
            "Why is this soft-excluded? (*)\n"
            f"Matched: {hit_txt}\n"
            f"By: {by_txt}"
        )

        # Position tooltip near cursor
        x_root = self._tree.winfo_rootx() + e.x + 18
        y_root = self._tree.winfo_rooty() + e.y + 18
        self._show_tooltip(x_root, y_root, tip_text, iid=iid)

    # ---------------- data ----------------

    def _load_stores(self) -> None:
        try:
            stores = self._repo.list_stores()
        except Exception as e:
            self._log_msg(f"DealFeed: failed to load stores: {e}")
            stores = []

        labels = ["All stores"]
        self._store_label_to_id = {"All stores": None}

        for s in stores:
            sid: Optional[int] = None
            name: str = ""

            if isinstance(s, dict):
                if s.get("id") is not None:
                    try:
                        sid = int(s.get("id"))
                    except Exception:
                        sid = None
                name = str(s.get("name") or "")
            else:
                try:
                    sid = int(getattr(s, "id", None))
                except Exception:
                    sid = None
                name = str(getattr(s, "name", "") or "")

            if sid is None or not name:
                continue

            labels.append(name)
            self._store_label_to_id[name] = sid

        self._store_combo["values"] = labels
        if self._store_var.get() not in labels:
            self._store_var.set("All stores")

    def _refresh(self) -> None:
        self._hide_tooltip()

        store_id = self._store_label_to_id.get(self._store_var.get(), None)
        include_disallowed_oils = bool(self._include_disallowed_oils_var.get())

        try:
            self._all_deals = self._repo.list_active_deals(
                store_id=store_id,
                apply_preferences=True,
                include_soft_excluded=True,
                include_disallowed_oils=include_disallowed_oils,
            )
        except AttributeError:
            messagebox.showerror(
                "Missing method",
                "FlyersRepo.list_active_deals() was not found. Paste the updated flyers_repo.py first.",
                parent=self,
            )
            return
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load active deals: {e}", parent=self)
            self._log_msg(f"DealFeed: failed to load deals: {e}")
            return

        self._apply_local_filters()
        self._log_msg(f"DealFeed: loaded {len(self._all_deals)} deals (active only)")

    def _apply_local_filters(self) -> None:
        q = (self._search_var.get() or "").strip().lower()
        show_soft = bool(self._show_soft_var.get())

        def is_soft(deal: Dict[str, Any]) -> bool:
            v = deal.get("pref_soft_excluded_by", [])
            return isinstance(v, list) and len(v) > 0

        out: List[Dict[str, Any]] = []
        for d in self._all_deals:
            if not show_soft and is_soft(d):
                continue
            if q:
                hay = f"{d.get('title','')} {d.get('description','')}".lower()
                if q not in hay:
                    continue
            out.append(d)

        self._filtered_deals = out
        self._render_tree()

    def _render_tree(self) -> None:
        for iid in self._tree.get_children():
            self._tree.delete(iid)

        for idx, d in enumerate(self._filtered_deals):
            store = str(d.get("store_name") or "")
            title = str(d.get("title") or "").strip()
            price = str(d.get("price_text") or "").strip()
            unit = str(d.get("unit") or "").strip()

            valid_from = str(d.get("flyer_valid_from") or "")
            valid_to = str(d.get("flyer_valid_to") or "")
            dates = f"{valid_from} → {valid_to}" if (valid_from and valid_to) else ""

            soft_by = d.get("pref_soft_excluded_by", [])
            soft_flag = "*" if isinstance(soft_by, list) and len(soft_by) > 0 else ""

            oil_allowed = d.get("pref_oil_allowed", True)
            oil_flag = "OIL" if oil_allowed is False else ""

            flags = " ".join([x for x in [soft_flag, oil_flag] if x])

            display_item = f"{title} *" if soft_flag else title

            self._tree.insert(
                "",
                "end",
                iid=str(idx),
                values=(store, display_item, price, unit, dates, flags),
            )

        if self._filtered_deals:
            self._tree.selection_set("0")
            self._tree.focus("0")
            self._on_selected()
        else:
            self._set_detail("No active deals matched your filters.")

    def _on_selected(self) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        try:
            idx = int(sel[0])
        except Exception:
            return
        if idx < 0 or idx >= len(self._filtered_deals):
            return

        d = self._filtered_deals[idx]
        self._set_detail(self._format_detail(d))

    def _format_detail(self, d: Dict[str, Any]) -> str:
        lines: List[str] = []

        lines.append(f"Item: {d.get('title','')}")
        if d.get("description"):
            lines.append(f"Description: {d.get('description','')}")
        if d.get("price_text"):
            lines.append(f"Price: {d.get('price_text','')}")
        if d.get("unit_price") is not None and d.get("unit"):
            lines.append(f"Unit: {d.get('unit_price')} / {d.get('unit')}")
        if d.get("store_name"):
            lines.append(f"Store: {d.get('store_name')}")
        if d.get("flyer_valid_from") and d.get("flyer_valid_to"):
            lines.append(f"Valid: {d.get('flyer_valid_from')} → {d.get('flyer_valid_to')}")
        if d.get("page_index") is not None:
            try:
                lines.append(f"Flyer page: {int(d.get('page_index')) + 1}")
            except Exception:
                pass

        lines.append("")
        lines.append("Preference notes:")
        soft_by = d.get("pref_soft_excluded_by", [])
        hits = d.get("pref_soft_excluded_hits", [])
        if isinstance(soft_by, list) and len(soft_by) > 0:
            lines.append(f"• Soft-excluded by: {', '.join(str(x) for x in soft_by)}")
            if isinstance(hits, list) and len(hits) > 0:
                lines.append(f"• Matched ingredient(s): {', '.join(str(x) for x in hits)}")
            else:
                lines.append("• Matched ingredient(s): (unknown)")
            lines.append("  (Shown with * — later we can also de-rank it.)")
        else:
            lines.append("• No soft-exclude conflicts detected.")

        oil_allowed = d.get("pref_oil_allowed", True)
        if oil_allowed is False:
            lines.append("• This looks like an oil deal, but it's not in your allowed oils list.")
        elif "pref_oil_hit" in d and d.get("pref_oil_hit"):
            lines.append("• Oil check: allowed.")

        return "\n".join(lines)

    def _set_detail(self, text: str) -> None:
        self._detail.configure(state="normal")
        self._detail.delete("1.0", tk.END)
        self._detail.insert(tk.END, text)
        self._detail.configure(state="disabled")

    def _log_msg(self, msg: str) -> None:
        if self._log:
            try:
                self._log(msg)
            except Exception:
                pass


def open_deal_feed_window(
    master: Optional[tk.Misc] = None, *, log: Optional[Callable[[str], None]] = None
) -> DealFeedWindow:
    return DealFeedWindow(master, log=log)
