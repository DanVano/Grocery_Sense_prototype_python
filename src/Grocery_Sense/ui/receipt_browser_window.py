from __future__ import annotations

# from fileinput import filename
import json
import tkinter as tk
from tkinter import ttk, messagebox
from tkinter.scrolledtext import ScrolledText
from typing import Any, Callable, Dict, Optional

from Grocery_Sense.data.repositories.receipts_repo import (
    list_recent_receipts,
    get_receipt,
    list_receipt_line_items,
    get_receipt_raw_json,
    delete_receipt_with_backup,
    restore_receipt_from_backup,
    list_deleted_backups,
)


def _fmt_money(v: Any) -> str:
    try:
        if v is None:
            return "n/a"
        return f"${float(v):,.2f}"
    except Exception:
        return "n/a"


def _open_text_window(parent: tk.Widget, title: str, text: str) -> None:
    win = tk.Toplevel(parent)
    win.title(title)
    win.geometry("950x700")

    box = ScrolledText(win, state=tk.NORMAL)
    box.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
    box.insert(tk.END, text)
    box.see(tk.END)


class ReceiptBrowserWindow(tk.Toplevel):
    """
    Receipt Browser:
      - browse recent receipts
      - select receipt -> see line items
      - view raw json
      - delete with undo backup
      - undo last delete (restores to a NEW receipt_id)
    """

    def __init__(self, parent: tk.Tk, *, log: Optional[Callable[[str], None]] = None) -> None:
        super().__init__(parent)
        self.title("Receipt Browser")
        self.geometry("1180x700")

        self._log = log or (lambda msg: None)

        self.limit_var = tk.IntVar(value=50)
        self.last_backup_id: Optional[int] = None

        self._build_ui()
        self.refresh_receipts()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        top = ttk.Frame(self, padding=10)
        top.pack(fill=tk.X)

        ttk.Label(top, text="Recent receipts:", font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT)

        ttk.Label(top, text="Show").pack(side=tk.LEFT, padx=(16, 6))
        ttk.Spinbox(top, from_=10, to=500, textvariable=self.limit_var, width=6).pack(side=tk.LEFT)
        ttk.Label(top, text="rows").pack(side=tk.LEFT, padx=(6, 12))

        ttk.Button(top, text="Refresh", command=self.refresh_receipts).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(top, text="Undo Last Delete", command=self.undo_last_delete).pack(side=tk.LEFT)

        # Split layout: top list + bottom details
        main = ttk.PanedWindow(self, orient=tk.VERTICAL)
        main.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        # --- Receipts table
        upper = ttk.Frame(main)
        main.add(upper, weight=2)

        cols = ("id", "date", "store", "total", "items", "file")
        self.receipts_tree = ttk.Treeview(upper, columns=cols, show="headings", height=12)

        self.receipts_tree.heading("id", text="ID")
        self.receipts_tree.heading("date", text="Date")
        self.receipts_tree.heading("store", text="Store")
        self.receipts_tree.heading("total", text="Total")
        self.receipts_tree.heading("items", text="# Items")
        self.receipts_tree.heading("file", text="File")

        self.receipts_tree.column("id", width=70, anchor="center")
        self.receipts_tree.column("date", width=110, anchor="center")
        self.receipts_tree.column("store", width=220, anchor="w")
        self.receipts_tree.column("total", width=100, anchor="e")
        self.receipts_tree.column("items", width=80, anchor="center")
        self.receipts_tree.column("file", width=520, anchor="w")

        rscroll = ttk.Scrollbar(upper, orient="vertical", command=self.receipts_tree.yview)
        self.receipts_tree.configure(yscrollcommand=rscroll.set)

        self.receipts_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        rscroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.receipts_tree.bind("<<TreeviewSelect>>", lambda e: self._on_receipt_selected())

        # --- Detail area
        lower = ttk.Frame(main)
        main.add(lower, weight=3)

        detail_top = ttk.Frame(lower)
        detail_top.pack(fill=tk.X, pady=(0, 8))

        self.detail_var = tk.StringVar(value="Select a receipt to view details.")
        ttk.Label(detail_top, textvariable=self.detail_var).pack(side=tk.LEFT)

        ttk.Button(detail_top, text="View Raw JSON", command=self.view_raw_json).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(detail_top, text="Delete Receipt", command=self.delete_selected_receipt).pack(side=tk.RIGHT)

        # Line items table
        line_cols = ("idx", "canonical", "desc", "qty", "unit_price", "line_total", "discount", "conf")
        self.items_tree = ttk.Treeview(lower, columns=line_cols, show="headings", height=14)

        self.items_tree.heading("idx", text="#")
        self.items_tree.heading("canonical", text="Canonical")
        self.items_tree.heading("desc", text="Receipt Text")
        self.items_tree.heading("qty", text="Qty")
        self.items_tree.heading("unit_price", text="Unit Price")
        self.items_tree.heading("line_total", text="Line Total")
        self.items_tree.heading("discount", text="Discount")
        self.items_tree.heading("conf", text="Conf")

        self.items_tree.column("idx", width=50, anchor="center")
        self.items_tree.column("canonical", width=210, anchor="w")
        self.items_tree.column("desc", width=360, anchor="w")
        self.items_tree.column("qty", width=70, anchor="e")
        self.items_tree.column("unit_price", width=90, anchor="e")
        self.items_tree.column("line_total", width=90, anchor="e")
        self.items_tree.column("discount", width=90, anchor="e")
        self.items_tree.column("conf", width=60, anchor="center")

        iscroll = ttk.Scrollbar(lower, orient="vertical", command=self.items_tree.yview)
        self.items_tree.configure(yscrollcommand=iscroll.set)

        self.items_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        iscroll.pack(side=tk.RIGHT, fill=tk.Y)

        # bottom status
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(self, textvariable=self.status_var, padding=(10, 0, 10, 10)).pack(fill=tk.X)

    # --------------------------------------------------------------- Helpers

    def _selected_receipt_id(self) -> Optional[int]:
        sel = self.receipts_tree.selection()
        if not sel:
            return None
        iid = sel[0]
        try:
            # iid is receipt id
            return int(iid)
        except Exception:
            return None

    # --------------------------------------------------------------- Actions

    def refresh_receipts(self) -> None:
        self.receipts_tree.delete(*self.receipts_tree.get_children())
        self.items_tree.delete(*self.items_tree.get_children())
        self.detail_var.set("Select a receipt to view details.")

        limit = int(self.limit_var.get() or 50)

        try:
            receipts = list_recent_receipts(limit=limit)
        except Exception as e:
            messagebox.showerror("Error", str(e))
            return

        for r in receipts:
            rid = int(r["id"])
            date = r.get("purchase_date") or ""
            store = r.get("store_name") or ""
            total = _fmt_money(r.get("total_amount"))
            items = str(r.get("item_count") or 0)
            file = (r.get("file_path") or "").split("\\")[-1].split("/")[-1]

            # Use receipt id as iid so we can fetch fast
            self.receipts_tree.insert(
                "",
                "end",
                iid=str(rid),
                values=(rid, date, store, total, items, file),
            )

        self.status_var.set(f"Loaded {len(receipts)} receipt(s).")
        self._log(f"[ReceiptBrowser] Loaded {len(receipts)} receipts.")

    def _on_receipt_selected(self) -> None:
        rid = self._selected_receipt_id()
        if rid is None:
            return

        self.items_tree.delete(*self.items_tree.get_children())

        rec = get_receipt(rid)
        if not rec:
            self.detail_var.set(f"Receipt {rid} not found.")
            return

        header = (
            f"Receipt #{rec['id']} | {rec.get('store_name','')} | {rec.get('purchase_date','')} | "
            f"Total {_fmt_money(rec.get('total_amount'))} (Sub {_fmt_money(rec.get('subtotal_amount'))}, Tax {_fmt_money(rec.get('tax_amount'))})"
        )
        self.detail_var.set(header)

        items = list_receipt_line_items(rid)
        for li in items:
            idx = li.get("line_index", 0)
            canonical = li.get("canonical_name") or ""
            desc = li.get("description") or ""
            qty = li.get("quantity")
            up = li.get("unit_price")
            lt = li.get("line_total")
            disc = li.get("discount")
            conf = li.get("confidence")

            self.items_tree.insert(
                "",
                "end",
                values=(
                    idx,
                    canonical,
                    desc,
                    "" if qty is None else f"{float(qty):g}",
                    "" if up is None else f"{float(up):.2f}",
                    "" if lt is None else f"{float(lt):.2f}",
                    "" if disc is None else f"{float(disc):.2f}",
                    "" if conf is None else str(conf),
                ),
            )

        self.status_var.set(f"Loaded receipt {rid} with {len(items)} line item(s).")

    def view_raw_json(self) -> None:
        rid = self._selected_receipt_id()
        if rid is None:
            messagebox.showinfo("No selection", "Select a receipt first.")
            return

        raw_json, json_path = get_receipt_raw_json(rid)
        if not raw_json:
            messagebox.showinfo("No JSON", "No raw JSON stored for this receipt.")
            return

        try:
            obj = json.loads(raw_json)
            pretty = json.dumps(obj, ensure_ascii=False, indent=2)
        except Exception:
            pretty = raw_json

        title = f"Raw JSON for receipt #{rid}"
        if json_path:
            filename = json_path.replace("\\", "/").split("/")[-1]
            title += f" [{filename}]"
        _open_text_window(self, title, pretty)

    def delete_selected_receipt(self) -> None:
        rid = self._selected_receipt_id()
        if rid is None:
            messagebox.showinfo("No selection", "Select a receipt first.")
            return

        if not messagebox.askyesno(
            "Delete Receipt",
            "This will delete the receipt and ALL derived data:\n"
            "- prices\n- line items\n- raw JSON\n- dedupe keys\n\n"
            "An undo backup will be created.\n\nContinue?",
        ):
            return

        try:
            backup_id = delete_receipt_with_backup(rid)
            self.last_backup_id = backup_id
        except Exception as e:
            messagebox.showerror("Delete failed", str(e))
            return

        self._log(f"[ReceiptBrowser] Deleted receipt {rid} (backup_id={self.last_backup_id})")
        self.status_var.set(f"Deleted receipt {rid}. Undo available (backup #{self.last_backup_id}).")

        self.refresh_receipts()

    def undo_last_delete(self) -> None:
        """
        Restores the most recent delete. If we don't have last_backup_id, pick newest backup row.
        Restored receipts come back as NEW receipt IDs.
        """
        backup_id = self.last_backup_id

        if backup_id is None:
            # try find newest backup
            backups = list_deleted_backups(limit=1)
            if backups:
                backup_id = backups[0]["backup_id"]

        if backup_id is None:
            messagebox.showinfo("Nothing to undo", "No delete backups found.")
            return

        if not messagebox.askyesno(
            "Undo Delete",
            f"Restore from backup #{backup_id}?\n\nThis will re-insert the receipt as a NEW receipt ID.",
        ):
            return

        try:
            new_id = restore_receipt_from_backup(int(backup_id))
        except Exception as e:
            messagebox.showerror("Undo failed", str(e))
            return

        self._log(f"[ReceiptBrowser] Undo restore backup {backup_id} -> new receipt_id={new_id}")
        self.status_var.set(f"Restored backup #{backup_id} -> new receipt #{new_id}.")
        self.last_backup_id = None

        self.refresh_receipts()

        # auto-select restored receipt if visible
        try:
            self.receipts_tree.selection_set(str(new_id))
            self.receipts_tree.see(str(new_id))
            self._on_receipt_selected()
        except Exception:
            pass


def open_receipt_browser_window(parent: tk.Tk, log: Optional[Callable[[str], None]] = None) -> None:
    ReceiptBrowserWindow(parent, log=log)
