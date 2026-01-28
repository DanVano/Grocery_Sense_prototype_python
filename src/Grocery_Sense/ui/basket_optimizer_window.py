from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox
from typing import Callable, Optional

from Grocery_Sense.services.basket_optimizer_service import BasketOptimizerService, BasketOptimizationResult


class BasketOptimizerWindow(tk.Toplevel):
    def __init__(self, master: Optional[tk.Misc] = None, *, log: Optional[Callable[[str], None]] = None) -> None:
        super().__init__(master)
        self.title("Basket Optimizer — Best Store(s) This Week")
        self.geometry("980x720")
        self.minsize(900, 640)

        self._log = log
        self._svc = BasketOptimizerService()

        self._mode = tk.StringVar(value="two_store")
        self._selected_item_reason = tk.StringVar(value="")

        self._build_ui()

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        header = ttk.Frame(root)
        header.pack(fill="x")

        ttk.Label(
            header,
            text="Which store(s) should we shop at this week?",
            font=("Segoe UI", 12, "bold"),
        ).pack(side="left")

        ttk.Button(header, text="Close", command=self.destroy).pack(side="right")

        # Mode + run
        controls = ttk.LabelFrame(root, text="Mode", padding=10)
        controls.pack(fill="x", pady=(10, 0))

        ttk.Radiobutton(
            controls,
            text="Fast trip (1 store)",
            value="one_store",
            variable=self._mode,
        ).grid(row=0, column=0, sticky="w", padx=(0, 20))

        ttk.Radiobutton(
            controls,
            text="Savings mode (up to 2 stores)",
            value="two_store",
            variable=self._mode,
        ).grid(row=0, column=1, sticky="w", padx=(0, 20))

        ttk.Button(
            controls,
            text="Run Optimizer",
            command=self._run,
            width=18,
        ).grid(row=0, column=2, sticky="e")

        controls.columnconfigure(2, weight=1)

        # Summary
        summary = ttk.LabelFrame(root, text="Summary", padding=10)
        summary.pack(fill="x", pady=(10, 0))

        self._summary_text = tk.Text(summary, height=7, wrap="word")
        self._summary_text.pack(fill="x", expand=False)

        # Main split
        body = ttk.Frame(root)
        body.pack(fill="both", expand=True, pady=(10, 0))
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)

        left = ttk.LabelFrame(body, text="Store(s)", padding=10)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        right = ttk.LabelFrame(body, text="Store shopping list", padding=10)
        right.grid(row=0, column=1, sticky="nsew")

        # Store list
        self._stores_list = tk.Listbox(left, height=14)
        self._stores_list.pack(fill="both", expand=True)
        self._stores_list.bind("<<ListboxSelect>>", lambda _e: self._render_items_for_selected_store())

        # Items list
        self._items = ttk.Treeview(
            right,
            columns=("qty", "unit", "price", "src"),
            show="headings",
            height=16,
        )
        self._items.heading("qty", text="Qty")
        self._items.heading("unit", text="Unit")
        self._items.heading("price", text="Unit Price")
        self._items.heading("src", text="Source")

        self._items.column("qty", width=70, anchor="w")
        self._items.column("unit", width=80, anchor="w")
        self._items.column("price", width=120, anchor="w")
        self._items.column("src", width=110, anchor="w")

        self._items.pack(fill="both", expand=True)
        self._items.bind("<<TreeviewSelect>>", lambda _e: self._on_item_selected())

        # Tooltip-ish line
        hint = ttk.LabelFrame(root, text="Why is this item starred / soft-excluded?", padding=10)
        hint.pack(fill="x", pady=(10, 0))
        ttk.Label(hint, textvariable=self._selected_item_reason, foreground="#666").pack(anchor="w")

        self._result: Optional[BasketOptimizationResult] = None

    def _run(self) -> None:
        try:
            self._result = self._svc.optimize(mode=self._mode.get())
        except Exception as e:
            messagebox.showerror("Error", f"Failed to run optimizer:\n\n{e}", parent=self)
            return

        self._render_summary()
        self._render_stores()

        if self._log:
            try:
                self._log("Basket optimizer ran successfully.")
            except Exception:
                pass

    def _render_summary(self) -> None:
        r = self._result
        self._summary_text.delete("1.0", tk.END)
        if not r:
            return

        lines = []
        lines.append(f"Mode: {'Fast trip (1 store)' if r.mode == 'one_store' else 'Savings mode (up to 2 stores)'}")
        lines.append(f"Estimated basket total: ${r.basket_total_estimated:,.2f}")

        if r.basket_usual_avg_estimated is not None and r.save_vs_usual_avg is not None:
            lines.append(f"You save vs usual basket (avg last 6 months): ${r.save_vs_usual_avg:,.2f}")
        else:
            lines.append("You save vs usual basket (avg last 6 months): unknown (insufficient price history)")

        if r.basket_lowest_estimated is not None and r.save_vs_lowest is not None:
            lines.append(f"You save vs lowest price seen (last 6 months): ${r.save_vs_lowest:,.2f}")
        else:
            lines.append("You save vs lowest price seen (last 6 months): unknown (insufficient price history)")

        if r.warnings:
            lines.append("")
            lines.append("Warnings:")
            for w in r.warnings:
                lines.append(f" • {w}")

        self._summary_text.insert(tk.END, "\n".join(lines))

    def _render_stores(self) -> None:
        self._stores_list.delete(0, tk.END)
        self._items.delete(*self._items.get_children())
        self._selected_item_reason.set("")
        if not self._result:
            return

        for sp in self._result.stores:
            label = f"{sp.store_name} — ${sp.total_estimated:,.2f}"
            if sp.unknown_count:
                label += f"  (unknown items: {sp.unknown_count})"
            self._stores_list.insert(tk.END, label)

        if self._result.stores:
            self._stores_list.selection_set(0)
            self._stores_list.activate(0)
            self._render_items_for_selected_store()

    def _render_items_for_selected_store(self) -> None:
        self._items.delete(*self._items.get_children())
        self._selected_item_reason.set("")
        r = self._result
        if not r:
            return
        sel = self._stores_list.curselection()
        if not sel:
            return
        idx = int(sel[0])
        if idx < 0 or idx >= len(r.stores):
            return

        store = r.stores[idx]

        # Display in a stable order (starred first, then alpha)
        items = list(store.items)
        items.sort(key=lambda x: (not x.starred, x.name.lower()))

        for it in items:
            name = it.name + ("*" if it.starred else "")
            price = "unknown"
            src = "-"
            if it.chosen and it.chosen.unit_price is not None:
                price = f"${it.chosen.unit_price:,.2f}"
                src = it.chosen.source

            iid = self._items.insert("", tk.END, values=(it.quantity, it.unit, price, src))
            # store full object reference on the row
            self._items.set(iid, "qty", str(it.quantity))
            self._items.item(iid, text=name)
            # also embed name in first column via headings display workaround:
            # simplest: add it as a prefix in qty column display
            self._items.set(iid, "qty", f"{name}   (x{it.quantity:g})")

        # set a friendly hint
        self._selected_item_reason.set("Select an item to see why it may be soft-excluded / starred.")

    def _on_item_selected(self) -> None:
        r = self._result
        if not r:
            return
        sel_store = self._stores_list.curselection()
        if not sel_store:
            return
        store_idx = int(sel_store[0])
        if store_idx < 0 or store_idx >= len(r.stores):
            return
        store = r.stores[store_idx]

        sel = self._items.selection()
        if not sel:
            return
        row_id = sel[0]
        qty_cell = str(self._items.set(row_id, "qty") or "")

        # Extract the name (we stuffed it into qty column as "Name* (xQ)")
        name_part = qty_cell.split("(x", 1)[0].strip()

        # Find the item in this store
        chosen_item = None
        for it in store.items:
            display = it.name + ("*" if it.starred else "")
            if display == name_part or it.name == name_part or (it.name + "*") == name_part:
                chosen_item = it
                break

        if not chosen_item:
            self._selected_item_reason.set("")
            return

        # Build the tooltip line
        if chosen_item.hard_excluded:
            self._selected_item_reason.set(
                f"⚠ Hard-excluded household-wide (allergy or master hard exclude). Ingredient hit matches: {chosen_item.name}"
            )
            return

        if chosen_item.soft_hits:
            parts = []
            for term, members in chosen_item.soft_hits:
                if members:
                    parts.append(f"{term} → {', '.join(members)}")
                else:
                    parts.append(f"{term}")
            self._selected_item_reason.set(
                f"Soft-excluded hit(s): " + " | ".join(parts)
            )
            return

        self._selected_item_reason.set("No soft-exclude reason detected for this item.")


def open_basket_optimizer_window(
    master: Optional[tk.Misc] = None,
    *,
    log: Optional[Callable[[str], None]] = None,
) -> BasketOptimizerWindow:
    return BasketOptimizerWindow(master, log=log)
