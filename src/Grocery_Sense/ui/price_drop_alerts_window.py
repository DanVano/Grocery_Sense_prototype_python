from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, List, Optional

from Grocery_Sense.services.price_drop_alert_service import PriceDropAlert, PriceDropAlertService


class PriceDropAlertsWindow(tk.Toplevel):
    def __init__(self, master: Optional[tk.Misc] = None, *, log: Optional[Callable[[str], None]] = None) -> None:
        super().__init__(master)
        self.title("Price Drop Alerts")
        self.geometry("1100x680")
        self.minsize(980, 600)

        self._log = log
        self._svc = PriceDropAlertService()

        self._alerts: List[PriceDropAlert] = []

        self._build_ui()
        self._refresh()

        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        top = ttk.Frame(root)
        top.pack(fill="x")

        ttk.Label(top, text="Receipt-based alerts (usual price + 6-month lows)", font=("Segoe UI", 11, "bold")).pack(
            side="left"
        )
        ttk.Button(top, text="Refresh", command=self._refresh).pack(side="right")

        note = ttk.Label(
            root,
            text="Note: 'Usual' is learned from receipts. If receipts are sparse, we estimate from other history. Unknown = no history yet.",
            foreground="#666",
        )
        note.pack(anchor="w", pady=(6, 10))

        body = ttk.Frame(root)
        body.pack(fill="both", expand=True)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        cols = ("type", "item", "store", "flyer", "usual", "drop", "low6", "near6", "staple")
        self.tree = ttk.Treeview(body, columns=cols, show="headings", height=16)
        self.tree.grid(row=0, column=0, sticky="nsew")

        vsb = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        vsb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=vsb.set)

        self.tree.heading("type", text="Alert")
        self.tree.heading("item", text="Item")
        self.tree.heading("store", text="Store")
        self.tree.heading("flyer", text="Flyer price")
        self.tree.heading("usual", text="Usual")
        self.tree.heading("drop", text="% below usual")
        self.tree.heading("low6", text="6-mo low")
        self.tree.heading("near6", text="Δ vs low")
        self.tree.heading("staple", text="Staple (90d)")

        self.tree.column("type", width=110, anchor="w")
        self.tree.column("item", width=320, anchor="w")
        self.tree.column("store", width=160, anchor="w")
        self.tree.column("flyer", width=110, anchor="e")
        self.tree.column("usual", width=110, anchor="e")
        self.tree.column("drop", width=110, anchor="e")
        self.tree.column("low6", width=110, anchor="e")
        self.tree.column("near6", width=90, anchor="e")
        self.tree.column("staple", width=110, anchor="center")

        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._on_select())

        # Details panel (this is your “tooltip line” replacement: always visible + clear)
        details = ttk.LabelFrame(root, text="Details", padding=10)
        details.pack(fill="x", pady=(10, 0))

        self._details_var = tk.StringVar(value="Select an alert to see details.")
        ttk.Label(details, textvariable=self._details_var, wraplength=1040, justify="left").pack(anchor="w")

        self._soft_var = tk.StringVar(value="")
        self._soft_label = ttk.Label(details, textvariable=self._soft_var, foreground="#444", wraplength=1040, justify="left")
        self._soft_label.pack(anchor="w", pady=(6, 0))

    def _refresh(self) -> None:
        self._alerts = self._svc.get_alerts(limit=250)

        self.tree.delete(*self.tree.get_children())
        for idx, a in enumerate(self._alerts):
            item_label = a.item_name
            if a.soft_excluded_by:
                # Star if soft excluded (more members -> stronger signal)
                stars = "*" * min(2, max(1, len(a.soft_excluded_by)))
                item_label = f"{item_label} {stars}"

            flyer = self._fmt_money(a.current_unit_price, a.unit)
            usual = self._fmt_money(a.usual_unit_price, a.unit) if a.usual_unit_price is not None else "—"
            drop = self._fmt_pct(a.pct_below_usual) if a.pct_below_usual is not None else "—"

            low6 = a.low_6mo_global if a.low_6mo_global is not None else a.low_6mo_store
            low6_s = self._fmt_money(low6, a.unit) if low6 is not None else "—"
            near6 = self._fmt_pct(a.pct_above_low_6mo) if a.pct_above_low_6mo is not None else "—"

            staple = f"{a.staple_purchases_90d}" + (" ✓" if a.is_staple else "")

            alert_txt = {
                "DROP_BELOW_USUAL": "Drop",
                "STOCK_UP": "Stock-up",
                "BOTH": "Drop + Stock-up",
            }.get(a.alert_type, a.alert_type)

            self.tree.insert(
                "",
                "end",
                iid=str(idx),
                values=(alert_txt, item_label, a.store_name, flyer, usual, drop, low6_s, near6, staple),
            )

        self._details_var.set("Select an alert to see details.")
        self._soft_var.set("")

        if self._log:
            try:
                self._log(f"Loaded {len(self._alerts)} price alerts")
            except Exception:
                pass

    def _on_select(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        i = int(sel[0])
        if i < 0 or i >= len(self._alerts):
            return

        a = self._alerts[i]
        bits: List[str] = []

        bits.append(f"{a.item_name} @ {a.store_name}")
        bits.append(f"Flyer: {self._fmt_money(a.current_unit_price, a.unit)} (valid to: {a.valid_to or 'unknown'})")

        if a.usual_unit_price is not None:
            src = "receipts" if a.usual_source == "receipt" else "estimated"
            bits.append(
                f"Usual: {self._fmt_money(a.usual_unit_price, a.unit)} ({src}; receipt samples: {a.receipt_samples})"
            )
        else:
            bits.append("Usual: unknown (no history yet)")

        if a.low_6mo_global is not None:
            bits.append(f"6-mo low (global): {self._fmt_money(a.low_6mo_global, a.unit)}")
        elif a.low_6mo_store is not None:
            bits.append(f"6-mo low (store): {self._fmt_money(a.low_6mo_store, a.unit)}")
        else:
            bits.append("6-mo low: unknown")

        if a.alert_type in ("STOCK_UP", "BOTH"):
            bits.append("Stock-up suggestion: near 6-month low and this is a staple.")
        if a.alert_type in ("DROP_BELOW_USUAL", "BOTH") and a.pct_below_usual is not None:
            bits.append(f"Dropped {self._fmt_pct(a.pct_below_usual)} below your usual price.")

        if a.warnings:
            bits.append("Warnings: " + " | ".join(a.warnings))

        self._details_var.set(" • ".join(bits))

        # “Why is this soft-excluded?” line (ingredient hit + members)
        if a.soft_excluded_by:
            hit = a.soft_exclude_hit or a.item_name
            members = ", ".join(a.soft_excluded_by)
            self._soft_var.set(f"Why soft-excluded? Hit: “{hit}” — by: {members}")
        else:
            self._soft_var.set("")

    @staticmethod
    def _fmt_money(v: Optional[float], unit: str = "") -> str:
        if v is None:
            return "—"
        try:
            s = f"${float(v):.2f}"
        except Exception:
            return "—"
        u = (unit or "").strip()
        return f"{s}/{u}" if u else s

    @staticmethod
    def _fmt_pct(v: Optional[float]) -> str:
        if v is None:
            return "—"
        try:
            return f"{float(v) * 100.0:.0f}%"
        except Exception:
            return "—"


def open_price_drop_alerts_window(
    master: Optional[tk.Misc] = None, *, log: Optional[Callable[[str], None]] = None
) -> PriceDropAlertsWindow:
    return PriceDropAlertsWindow(master, log=log)
