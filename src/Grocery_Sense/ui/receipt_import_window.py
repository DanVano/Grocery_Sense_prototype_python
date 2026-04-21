from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from queue import Queue, Empty
from typing import Any, Callable, Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

from Grocery_Sense.data.repositories import receipts_repo

# Updated import: use outcome-based ingest (dedupe + optional replace)
from Grocery_Sense.integrations.azure_docint_client import (
    ingest_receipt_file_outcome,
    ingest_analyzed_receipt_into_db,
)

SUPPORTED_EXTS = (".jpg", ".jpeg", ".png", ".pdf", ".tif", ".tiff")


@dataclass
class _RowState:
    path: Path
    status: str = "Pending"
    receipt_id: Optional[int] = None
    error: Optional[str] = None


def _fetch_receipt_summary(receipt_id: int) -> Dict[str, Any]:
    """
    Small summary for UI, delegated to receipts_repo so the UI never
    touches the database directly.
    """
    rec = receipts_repo.get_receipt(int(receipt_id))
    if not rec:
        return {"receipt_id": receipt_id}

    line_items = receipts_repo.list_receipt_line_items(int(receipt_id))
    return {
        "receipt_id": rec["id"],
        "purchase_date": rec["purchase_date"],
        "total": rec["total_amount"],
        "subtotal": rec["subtotal_amount"],
        "tax": rec["tax_amount"],
        "store_id": rec["store_id"],
        "store_name": rec["store_name"],
        "item_count": len(line_items),
    }


def _fetch_raw_json_and_path(receipt_id: int) -> Tuple[Optional[str], Optional[str]]:
    return receipts_repo.get_receipt_raw_json(int(receipt_id))


def _open_text_window(parent: tk.Widget, title: str, text: str) -> None:
    win = tk.Toplevel(parent)
    win.title(title)
    win.geometry("900x650")

    box = ScrolledText(win, state=tk.NORMAL)
    box.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
    box.insert(tk.END, text)
    box.see(tk.END)


class ReceiptImportWindow(tk.Toplevel):
    """
    Receipt Import window:
    - Select folder, scan files
    - Import sequentially (one at a time) with progress
    - Dedupe-aware import (skips duplicates)
    - Optional replace behavior toggle
    - Review: summary / raw JSON / reprocess from JSON
    """

    def __init__(
        self,
        parent: tk.Tk,
        *,
        log: Optional[Callable[[str], None]] = None,
        raw_json_dir: str | Path = "azure_raw_json",
    ) -> None:
        super().__init__(parent)
        self.title("Receipt Import")
        self.geometry("1100x680")
        self.parent = parent

        self._log = log or (lambda msg: None)
        self.raw_json_dir = Path(raw_json_dir)

        self._rows: List[_RowState] = []
        self._queue: "Queue[Tuple[str, Dict[str, Any]]]" = Queue()
        self._worker: Optional[threading.Thread] = None
        self._stop_flag = False

        # replace toggle
        self.replace_existing_var = tk.BooleanVar(value=False)

        self._build_ui()
        self._poll_queue()

    # ---------------------------------------------------------------------
    # UI
    # ---------------------------------------------------------------------

    def _build_ui(self) -> None:
        top = ttk.Frame(self)
        top.pack(fill=tk.X, padx=10, pady=10)

        ttk.Label(top, text="Receipt Folder:").pack(side=tk.LEFT)

        self.folder_var = tk.StringVar(value="")
        self.folder_entry = ttk.Entry(top, textvariable=self.folder_var, width=70)
        self.folder_entry.pack(side=tk.LEFT, padx=(8, 8))

        ttk.Button(top, text="Browse...", command=self._browse_folder).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(top, text="Scan", command=self._scan_folder).pack(side=tk.LEFT, padx=(0, 12))

        ttk.Checkbutton(
            top,
            text="Replace existing duplicates",
            variable=self.replace_existing_var,
        ).pack(side=tk.LEFT)

        mid = ttk.Frame(self)
        mid.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        cols = ("file", "status", "receipt_id")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings")
        self.tree.heading("file", text="File")
        self.tree.heading("status", text="Status")
        self.tree.heading("receipt_id", text="Receipt ID")

        self.tree.column("file", width=680, anchor="w")
        self.tree.column("status", width=190, anchor="center")
        self.tree.column("receipt_id", width=120, anchor="center")

        yscroll = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)

        bottom = ttk.Frame(self)
        bottom.pack(fill=tk.X, padx=10, pady=(0, 10))

        self.progress = ttk.Progressbar(bottom, mode="determinate")
        self.progress.pack(side=tk.TOP, fill=tk.X)

        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(bottom, textvariable=self.status_var).pack(side=tk.TOP, anchor="w", pady=(6, 8))

        btns = ttk.Frame(bottom)
        btns.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(btns, text="Import All", command=self._import_all).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btns, text="Import Selected", command=self._import_selected).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btns, text="Stop", command=self._stop_import).pack(side=tk.LEFT, padx=(0, 20))

        ttk.Button(btns, text="View Summary", command=self._view_summary).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btns, text="View Raw JSON", command=self._view_raw_json).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btns, text="Reprocess from JSON", command=self._reprocess_from_json).pack(
            side=tk.LEFT, padx=(0, 8)
        )
        ttk.Button(btns, text="Refresh List", command=self._refresh_tree).pack(side=tk.RIGHT)

    # ---------------------------------------------------------------------
    # Folder / scanning
    # ---------------------------------------------------------------------

    def _browse_folder(self) -> None:
        path = filedialog.askdirectory(title="Select receipt folder")
        if path:
            self.folder_var.set(path)
            self._scan_folder()

    def _scan_folder(self) -> None:
        folder = Path(self.folder_var.get().strip())
        if not folder.exists() or not folder.is_dir():
            messagebox.showerror("Invalid folder", "Select a valid folder first.")
            return

        files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS]
        files.sort(key=lambda p: p.name.lower())

        self._rows = [_RowState(path=p) for p in files]
        self._refresh_tree()

        self.status_var.set(f"Found {len(self._rows)} receipt file(s).")
        self._log(f"[ReceiptImport] Scanned folder: {folder} ({len(self._rows)} files)")

        self.progress["value"] = 0
        self.progress["maximum"] = max(1, len(self._rows))

    def _refresh_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for idx, r in enumerate(self._rows):
            rid = "" if r.receipt_id is None else str(r.receipt_id)
            self.tree.insert("", tk.END, iid=str(idx), values=(r.path.name, r.status, rid))

    # ---------------------------------------------------------------------
    # Import workflow (threaded, sequential)
    # ---------------------------------------------------------------------

    def _import_all(self) -> None:
        if not self._rows:
            messagebox.showinfo("Nothing to import", "Scan a folder first.")
            return
        self._start_worker([i for i in range(len(self._rows))])

    def _import_selected(self) -> None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("No selection", "Select one or more rows first.")
            return
        idxs = [int(iid) for iid in sel]
        self._start_worker(idxs)

    def _stop_import(self) -> None:
        self._stop_flag = True
        self.status_var.set("Stopping after current receipt…")
        self._log("[ReceiptImport] Stop requested.")

    def _start_worker(self, indexes: List[int]) -> None:
        if self._worker and self._worker.is_alive():
            messagebox.showinfo("Import running", "An import is already running.")
            return

        self._stop_flag = False

        # reset statuses for selected to queued (unless already imported)
        for i in indexes:
            if 0 <= i < len(self._rows):
                if self._rows[i].receipt_id is None:
                    self._rows[i].status = "Queued"
                    self._rows[i].error = None

        self._refresh_tree()

        self.progress["value"] = 0
        self.progress["maximum"] = max(1, len(indexes))
        self.status_var.set(f"Import queued: {len(indexes)} receipt(s).")

        replace_existing = bool(self.replace_existing_var.get())

        self._worker = threading.Thread(
            target=self._worker_run,
            args=(indexes, replace_existing),
            daemon=True,
        )
        self._worker.start()

    def _worker_run(self, indexes: List[int], replace_existing: bool) -> None:
        done = 0
        for i in indexes:
            if self._stop_flag:
                self._queue.put(("stopped", {"message": "Stopped by user."}))
                break

            if not (0 <= i < len(self._rows)):
                continue

            row = self._rows[i]

            # If already imported, just count it as done
            if row.receipt_id is not None:
                done += 1
                self._queue.put(("progress", {"done": done}))
                continue

            row.status = "Processing..."
            self._queue.put(("row_update", {"index": i, "status": row.status}))

            try:
                outcome = ingest_receipt_file_outcome(
                    file_path=row.path,
                    raw_json_dir=self.raw_json_dir,
                    locale="en-US",
                    store_match_threshold=85,
                    replace_existing=replace_existing,
                )

                row.receipt_id = int(outcome.receipt_id)

                if outcome.was_duplicate and not outcome.replaced_existing:
                    row.status = "Duplicate (skipped)"
                    self._queue.put(
                        (
                            "log",
                            {
                                "message": f"Duplicate skipped {row.path.name} -> existing receipt_id={outcome.receipt_id} ({outcome.duplicate_reason})"
                            },
                        )
                    )
                elif outcome.replaced_existing:
                    row.status = "Replaced + Imported"
                    self._queue.put(
                        (
                            "log",
                            {
                                "message": f"Replaced existing duplicate and imported {row.path.name} -> receipt_id={outcome.receipt_id}"
                            },
                        )
                    )
                else:
                    row.status = "Imported"
                    self._queue.put(("log", {"message": f"Imported {row.path.name} -> receipt_id={row.receipt_id}"}))

                self._queue.put(
                    ("row_update", {"index": i, "status": row.status, "receipt_id": row.receipt_id})
                )

            except Exception as e:
                row.status = "Error"
                row.error = str(e)
                self._queue.put(("row_update", {"index": i, "status": row.status}))
                self._queue.put(("log", {"message": f"ERROR importing {row.path.name}: {row.error}"}))

            done += 1
            self._queue.put(("progress", {"done": done}))

        self._queue.put(("done", {"message": "Import complete."}))

    def _poll_queue(self) -> None:
        try:
            while True:
                msg_type, payload = self._queue.get_nowait()

                if msg_type == "row_update":
                    idx = int(payload["index"])
                    status = payload.get("status", "")
                    rid = payload.get("receipt_id")

                    if 0 <= idx < len(self._rows):
                        self._rows[idx].status = status
                        if rid is not None:
                            self._rows[idx].receipt_id = int(rid)

                        # update tree row
                        row = self._rows[idx]
                        self.tree.set(str(idx), "status", row.status)
                        self.tree.set(str(idx), "receipt_id", "" if row.receipt_id is None else str(row.receipt_id))

                elif msg_type == "progress":
                    done = int(payload.get("done", 0))
                    self.progress["value"] = done
                    self.status_var.set(f"Importing… {done}/{int(self.progress['maximum'])}")

                elif msg_type == "log":
                    self._log(f"[ReceiptImport] {payload.get('message','')}")

                elif msg_type == "stopped":
                    self.status_var.set(payload.get("message", "Stopped."))

                elif msg_type == "done":
                    self.status_var.set(payload.get("message", "Done."))

        except Empty:
            pass

        self.after(200, self._poll_queue)

    # ---------------------------------------------------------------------
    # Review actions
    # ---------------------------------------------------------------------

    def _selected_row(self) -> Optional[_RowState]:
        sel = self.tree.selection()
        if not sel:
            return None
        idx = int(sel[0])
        if not (0 <= idx < len(self._rows)):
            return None
        return self._rows[idx]

    def _view_summary(self) -> None:
        row = self._selected_row()
        if not row or not row.receipt_id:
            messagebox.showinfo("No receipt", "Select an imported receipt first.")
            return

        summary = _fetch_receipt_summary(row.receipt_id)
        pretty = json.dumps(summary, indent=2, ensure_ascii=False)
        _open_text_window(self, f"Receipt Summary (id={row.receipt_id})", pretty)

    def _view_raw_json(self) -> None:
        row = self._selected_row()
        if not row or not row.receipt_id:
            messagebox.showinfo("No receipt", "Select an imported receipt first.")
            return

        raw_json, json_path = _fetch_raw_json_and_path(row.receipt_id)
        if not raw_json:
            messagebox.showinfo("Not found", "No raw JSON found for this receipt.")
            return

        try:
            obj = json.loads(raw_json)
            raw_json = json.dumps(obj, indent=2, ensure_ascii=False)
        except Exception:
            pass

        title = f"Raw JSON (receipt_id={row.receipt_id})"
        if json_path:
            title += f" [{Path(json_path).name}]"
        _open_text_window(self, title, raw_json)

    def _reprocess_from_json(self) -> None:
        row = self._selected_row()
        if not row or not row.receipt_id:
            messagebox.showinfo("No receipt", "Select an imported receipt first.")
            return

        raw_json, json_path = _fetch_raw_json_and_path(row.receipt_id)
        if not json_path:
            messagebox.showinfo("No JSON path", "This receipt does not have a stored JSON path.")
            return

        p = Path(json_path)
        if not p.exists():
            messagebox.showerror("Missing file", f"JSON file not found:\n{p}")
            return

        if not messagebox.askyesno(
            "Reprocess from JSON",
            "This will create a NEW receipt record by re-ingesting the stored JSON.\n\nContinue?",
        ):
            return

        try:
            analyze_result = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            messagebox.showerror("Invalid JSON", f"Could not read/parse JSON:\n{e}")
            return

        # NOTE: this creates a new receipt row (no Azure call) - does NOT dedupe.
        try:
            new_receipt_id = ingest_analyzed_receipt_into_db(
                file_path=row.path,
                operation_id=f"reprocess_{row.receipt_id}",
                analyze_result=analyze_result,
                saved_json_path=p,
                store_match_threshold=85,
            )
        except Exception as e:
            messagebox.showerror("Reprocess failed", str(e))
            return

        messagebox.showinfo("Reprocessed", f"Created new receipt_id={new_receipt_id}")
        self._log(f"[ReceiptImport] Reprocessed JSON for receipt_id={row.receipt_id} -> new receipt_id={new_receipt_id}")


def open_receipt_import_window(parent: tk.Tk, log: Optional[Callable[[str], None]] = None) -> None:
    ReceiptImportWindow(parent, log=log)
