from __future__ import annotations

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Callable, Optional, List

from Grocery_Sense.data.repositories.flyers_repo import FlyersRepo
from Grocery_Sense.services.flyer_ingest_service import FlyerIngestService


class FlyerImportWindow(tk.Toplevel):
    """
    Manual Flyer Import UI:
      - select store
      - select valid_from / valid_to
      - select PDF/images
      - ingest -> stores assets + raw json + extracted DealRecords
    """

    def __init__(self, parent: tk.Tk, *, log: Optional[Callable[[str], None]] = None) -> None:
        super().__init__(parent)
        self.title("Flyer Import (Manual)")
        self.geometry("900x560")

        self._log = log or (lambda msg: None)

        self.repo = FlyersRepo()
        self.svc = FlyerIngestService()

        self.store_var = tk.StringVar(value="")
        self.valid_from_var = tk.StringVar(value="")
        self.valid_to_var = tk.StringVar(value="")
        self.raw_json_dir_var = tk.StringVar(value="flyer_raw_json")
        self.try_mapping_var = tk.BooleanVar(value=True)

        self.files: List[str] = []

        self._build_ui()
        self._load_stores()

    def _build_ui(self) -> None:
        pad = ttk.Frame(self, padding=10)
        pad.pack(fill=tk.BOTH, expand=True)

        ttk.Label(pad, text="Flyer Import (Manual)", font=("Segoe UI", 12, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 10)
        )

        # Store dropdown
        ttk.Label(pad, text="Store:").grid(row=1, column=0, sticky="w")
        self.store_combo = ttk.Combobox(pad, textvariable=self.store_var, width=45, state="readonly")
        self.store_combo.grid(row=1, column=1, sticky="w", padx=(6, 0))

        # Dates
        ttk.Label(pad, text="Valid From (YYYY-MM-DD):").grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(pad, textvariable=self.valid_from_var, width=20).grid(row=2, column=1, sticky="w", padx=(6, 0), pady=(10, 0))

        ttk.Label(pad, text="Valid To (YYYY-MM-DD):").grid(row=3, column=0, sticky="w")
        ttk.Entry(pad, textvariable=self.valid_to_var, width=20).grid(row=3, column=1, sticky="w", padx=(6, 0))

        # Raw JSON folder
        ttk.Label(pad, text="Raw JSON folder:").grid(row=4, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(pad, textvariable=self.raw_json_dir_var, width=35).grid(row=4, column=1, sticky="w", padx=(6, 0), pady=(10, 0))

        # Mapping toggle
        ttk.Checkbutton(pad, text="Try map deals to items (recommended)", variable=self.try_mapping_var).grid(
            row=5, column=1, sticky="w", pady=(8, 0)
        )

        # File list
        ttk.Label(pad, text="Selected files:").grid(row=6, column=0, sticky="nw", pady=(14, 0))
        self.files_list = tk.Listbox(pad, height=12, width=90)
        self.files_list.grid(row=6, column=1, columnspan=2, sticky="w", pady=(14, 0), padx=(6, 0))

        btn_row = ttk.Frame(pad)
        btn_row.grid(row=7, column=1, sticky="w", pady=(10, 0), padx=(6, 0))

        ttk.Button(btn_row, text="Add PDFs/Images", command=self._pick_files, width=18).pack(side=tk.LEFT)
        ttk.Button(btn_row, text="Clear", command=self._clear_files, width=10).pack(side=tk.LEFT, padx=(8, 0))

        # Progress + action
        self.progress = ttk.Progressbar(pad, mode="indeterminate")
        self.progress.grid(row=8, column=1, sticky="we", padx=(6, 0), pady=(14, 0))

        ttk.Button(pad, text="Import Flyers", command=self._run_import, width=18).grid(
            row=9, column=1, sticky="w", padx=(6, 0), pady=(10, 0)
        )

        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(pad, textvariable=self.status_var).grid(row=10, column=0, columnspan=3, sticky="w", pady=(12, 0))

        pad.columnconfigure(2, weight=1)

    def _load_stores(self) -> None:
        try:
            stores = self.repo.list_stores()
        except Exception as e:
            messagebox.showerror("Stores load failed", str(e))
            return

        # Combobox values: "id - name"
        values = [f"{s.id} - {s.name}" for s in stores]
        self.store_combo["values"] = values
        if values:
            self.store_combo.current(0)

    def _pick_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Select flyer PDFs/images",
            filetypes=[
                ("Flyers", "*.pdf *.png *.jpg *.jpeg"),
                ("PDF", "*.pdf"),
                ("Images", "*.png *.jpg *.jpeg"),
                ("All files", "*.*"),
            ],
        )
        if not paths:
            return
        for p in paths:
            if p not in self.files:
                self.files.append(p)
                self.files_list.insert(tk.END, p)
        self.status_var.set(f"Selected {len(self.files)} file(s).")

    def _clear_files(self) -> None:
        self.files.clear()
        self.files_list.delete(0, tk.END)
        self.status_var.set("Cleared.")

    def _selected_store_id(self) -> Optional[int]:
        v = (self.store_var.get() or "").strip()
        if not v:
            return None
        # expects "id - name"
        try:
            return int(v.split("-", 1)[0].strip())
        except Exception:
            return None

    def _run_import(self) -> None:
        if not self.files:
            messagebox.showinfo("No files", "Add at least one flyer PDF/image.")
            return

        store_id = self._selected_store_id()
        if store_id is None:
            messagebox.showerror(
                "Select a store",
                "Choose the store this flyer is for before importing.",
            )
            return

        valid_from = (self.valid_from_var.get() or "").strip() or None
        valid_to = (self.valid_to_var.get() or "").strip() or None
        raw_json_dir = (self.raw_json_dir_var.get() or "flyer_raw_json").strip()

        self.progress.start(10)
        self.status_var.set("Importing... (Azure + extraction)")
        self.update_idletasks()

        try:
            res = self.svc.ingest_assets(
                store_id=store_id,
                valid_from=valid_from,
                valid_to=valid_to,
                file_paths=list(self.files),
                raw_json_dir=raw_json_dir,
                source_type="manual_upload",
                source_ref=None,
                note=None,
                try_item_mapping=bool(self.try_mapping_var.get()),
            )
        except Exception as e:
            self.progress.stop()
            messagebox.showerror("Import failed", str(e))
            self.status_var.set("Import failed.")
            return

        self.progress.stop()
        self._log(f"[FlyerImport] flyer_id={res.flyer_id}, assets={res.assets_count}, raw_json={res.raw_json_count}, deals={res.deals_count}")
        self.status_var.set(f"Done. Flyer batch {res.flyer_id}: {res.assets_count} assets, {res.deals_count} deals extracted.")

        messagebox.showinfo(
            "Import complete",
            f"Flyer batch created:\n\n"
            f"Flyer ID: {res.flyer_id}\n"
            f"Assets: {res.assets_count}\n"
            f"Raw JSON: {res.raw_json_count}\n"
            f"Deals extracted: {res.deals_count}\n\n"
            f"Next: build a Flyer Deals Browser screen to review & clean.",
        )


def open_flyer_import_window(parent: tk.Tk, log: Optional[Callable[[str], None]] = None) -> None:
    FlyerImportWindow(parent, log=log)
