"""
Grocery_Sense.ui.tk_main

Tkinter prototype UI for Grocery Sense (Dark Mode ONLY).

Main menu:
- Initialize DB
- Shopping List (Add / Check / Delete)
- Meal Suggestions
- Weekly Plan
- Receipt Import (Azure)
- Receipt Browser (Delete/Undo)
- Stores Management
- Store Plan (with savings)
- Price History Viewer
- Item Manager
- Flyer Import (Manual)
- Seed Demo Data

This file also contains:
- Dark theme configuration (ttk + tk palette)
- Styled log output area (same look as your previous app)
- Thread-safe logger (root.after)
- Progressbar styling + helper utilities
"""

from __future__ import annotations

import re
import threading
import traceback
import tkinter as tk
from tkinter import ttk, messagebox
from tkinter.scrolledtext import ScrolledText
from typing import Callable, Optional

from Grocery_Sense.data.schema import initialize_database

from Grocery_Sense.services.shopping_list_service import ShoppingListService
from Grocery_Sense.services.meal_suggestion_service import MealSuggestionService
from Grocery_Sense.services.weekly_planner_service import WeeklyPlannerService, summarize_weekly_plan
from Grocery_Sense.services.planning_service import PlanningService
from Grocery_Sense.services.demo_seed_service import seed_demo_data

from Grocery_Sense.ui.flyer_import_window import open_flyer_import_window
from Grocery_Sense.ui.item_manager_window import open_item_manager_window
from Grocery_Sense.ui.receipt_import_window import open_receipt_import_window
from Grocery_Sense.ui.receipt_browser_window import open_receipt_browser_window
from Grocery_Sense.ui.store_plan_window import open_store_plan_window
from Grocery_Sense.ui.price_history_window import open_price_history_window


# =============================================================================
# Dark Theme + UI Helpers (embedded)
# =============================================================================

# Detect your final summary lines so labels can be bolded with colons.
# Example: "[INFO] Imported 12 | Duplicates 3 | Errors 0"
_SUMMARY_RE = re.compile(r"^\[INFO\]\s*.+\d(?:\s\|\s.+\d)+$")

# Palette (matches your snippet)
_BG = "#242424"
_PANEL_BG = "#242424"
_FG = "#F8F8F8"
_MUTED_FG = "#AAAAAA"

_BTN_BG = "#333333"
_BTN_HOVER = "#555555"

_ENTRY_BG = "#2B2B2B"
_BORDER = "#333333"

_TROUGH = "#444444"
_PROG = "#D6D6D6"


def apply_dark_theme(root: tk.Misc) -> None:
    """
    Dark mode is the ONLY mode.
    Apply to ttk + base tk palette.
    """
    # Classic tk palette (affects tk.Frame, tk.Label, tk.Listbox, tk.Text, etc.)
    try:
        root.tk_setPalette(
            background=_BG,
            foreground=_FG,
            activeBackground=_BTN_HOVER,
            activeForeground=_FG,
            highlightColor="#444444",
        )
    except Exception:
        pass

    # ttk styling
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass

    try:
        root.option_add("*Font", ("Segoe UI", 10))
    except Exception:
        pass

    # General containers
    style.configure(".", background=_BG, foreground=_FG)
    style.configure("TFrame", background=_BG)
    style.configure("TLabel", background=_BG, foreground=_FG)
    style.configure("TLabelframe", background=_BG, foreground=_FG, bordercolor=_BORDER)
    style.configure("TLabelframe.Label", background=_BG, foreground=_FG)

    # Buttons
    style.configure(
        "TButton",
        background=_BTN_BG,
        foreground=_FG,
        bordercolor=_BORDER,
        focusthickness=2,
        focuscolor=_BORDER,
        padding=(10, 6),
    )
    style.map(
        "TButton",
        background=[("active", _BTN_HOVER), ("pressed", "#2A2A2A"), ("disabled", "#2A2A2A")],
        foreground=[("disabled", "#888888")],
    )

    # Checkbutton / Radiobutton
    style.configure("TCheckbutton", background=_BG, foreground=_FG)
    style.map("TCheckbutton", foreground=[("disabled", "#888888")])

    style.configure("TRadiobutton", background=_BG, foreground=_FG)
    style.map("TRadiobutton", foreground=[("disabled", "#888888")])

    # Entry / Combobox
    style.configure(
        "TEntry",
        fieldbackground=_ENTRY_BG,
        background=_ENTRY_BG,
        foreground=_FG,
        insertcolor=_FG,
        bordercolor=_BORDER,
    )
    style.configure(
        "TCombobox",
        fieldbackground=_ENTRY_BG,
        background=_ENTRY_BG,
        foreground=_FG,
        arrowcolor=_FG,
        bordercolor=_BORDER,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", _ENTRY_BG), ("disabled", _ENTRY_BG)],
        foreground=[("readonly", _FG), ("disabled", "#888888")],
        background=[("active", _BTN_HOVER)],
    )

    # Notebook
    style.configure("TNotebook", background=_BG, bordercolor=_BORDER)
    style.configure("TNotebook.Tab", background=_BTN_BG, foreground=_FG, padding=(10, 6))
    style.map("TNotebook.Tab", background=[("selected", "#2F2F2F"), ("active", _BTN_HOVER)])

    # Treeview
    style.configure(
        "Treeview",
        background=_ENTRY_BG,
        fieldbackground=_ENTRY_BG,
        foreground=_FG,
        rowheight=24,
        bordercolor=_BORDER,
    )
    style.configure(
        "Treeview.Heading",
        background=_BTN_BG,
        foreground=_FG,
        bordercolor=_BORDER,
        padding=(8, 6),
    )
    style.map(
        "Treeview",
        background=[("selected", "#3A3A3A")],
        foreground=[("selected", _FG)],
    )
    style.map("Treeview.Heading", background=[("active", _BTN_HOVER)])

    # Scrollbar
    style.configure("TScrollbar", background=_BG, troughcolor=_BG, bordercolor=_BORDER, arrowcolor=_FG)

    # Progressbar (your style)
    style.configure(
        "Custom.Horizontal.TProgressbar",
        troughcolor=_TROUGH,
        background=_PROG,
        bordercolor="#555555",
        thickness=18,
    )
    style.configure(
        "Horizontal.TProgressbar",
        troughcolor=_TROUGH,
        background=_PROG,
        bordercolor="#555555",
        thickness=18,
    )

    try:
        root.configure(bg=_BG)
    except Exception:
        pass


def style_text_widget(text: tk.Text) -> None:
    """Match your previous Text styling."""
    try:
        text.configure(
            bg=_PANEL_BG,
            fg=_FG,
            insertbackground=_FG,
            highlightbackground=_BORDER,
            highlightcolor="#444444",
            padx=16,
            pady=10,
            font=("Segoe UI", 12),
            wrap="word",
        )
    except Exception:
        pass

    try:
        text.tag_configure("bold", font=("Segoe UI", 12, "bold"))
        text.tag_configure("dotted", foreground=_MUTED_FG, spacing1=7, spacing3=2)
        text.tag_configure("logtitle", font=("Segoe UI", 12, "bold"), spacing1=2)
    except Exception:
        pass


def style_listbox(lb: tk.Listbox) -> None:
    """Dark style for listboxes."""
    try:
        lb.configure(
            bg=_PANEL_BG,
            fg=_FG,
            selectbackground="#3A3A3A",
            selectforeground=_FG,
            highlightbackground=_BORDER,
            highlightcolor="#444444",
            activestyle="none",
        )
    except Exception:
        pass


def build_output_area(parent: tk.Misc, *, height: int = 12) -> tuple[tk.Text, tk.Frame]:
    """Create the output Text area (exact look/feel from your snippet)."""
    out_frame = tk.Frame(parent, bg=_BG)

    text_output = tk.Text(
        out_frame,
        height=height,
        width=110,
        bg=_PANEL_BG,
        fg=_FG,
        insertbackground=_FG,
        highlightbackground=_BORDER,
        highlightcolor="#444444",
        padx=16,
        pady=10,
        font=("Segoe UI", 12),
        wrap="word",
    )
    text_output.pack(side="left", fill="both", expand=True)

    y_scroll = tk.Scrollbar(out_frame, orient="vertical", relief="sunken", command=text_output.yview)
    y_scroll.pack(side="right", fill="y")
    text_output.configure(yscrollcommand=y_scroll.set)

    style_text_widget(text_output)
    text_output.configure(state="disabled")
    return text_output, out_frame


def make_threadsafe_logger(root: tk.Misc, text_output: tk.Text) -> Callable[[str], None]:
    """Thread-safe logger(msg) preserving your summary-line bolding behavior."""
    def log(msg: str) -> None:
        def _append() -> None:
            try:
                text_output.configure(state="normal")

                leading_newlines = len(msg) - len(msg.lstrip("\n"))
                if leading_newlines:
                    text_output.insert(tk.END, "\n" * leading_newlines)

                stripped = msg.lstrip()

                if _SUMMARY_RE.match(stripped):
                    text_output.insert(tk.END, "[INFO] ", "bold")
                    body = stripped.split("] ", 1)[1] if "] " in stripped else stripped[7:]
                    parts = [p.strip() for p in body.split("|")]
                    for i, part in enumerate(parts):
                        label, value = (part.rsplit(" ", 1) if " " in part else (part, ""))
                        text_output.insert(tk.END, f"{label}: ", "bold")
                        text_output.insert(tk.END, value)
                        if i < len(parts) - 1:
                            text_output.insert(tk.END, " | ")
                    text_output.insert(tk.END, "\n")
                else:
                    text_output.insert(tk.END, msg + "\n")

                text_output.see(tk.END)
                text_output.configure(state="disabled")
            except Exception:
                pass

        try:
            root.after(0, _append)
        except Exception:
            pass

    return log


def build_progressbar(parent: tk.Misc, *, length: int = 400, determinate: bool = True) -> ttk.Progressbar:
    """Create your custom-styled progressbar."""
    mode = "determinate" if determinate else "indeterminate"
    return ttk.Progressbar(
        parent,
        orient="horizontal",
        length=length,
        mode=mode,
        style="Custom.Horizontal.TProgressbar",
    )


def center_window(win: tk.Misc) -> None:
    """Center the window on screen."""
    try:
        win.update_idletasks()
        w = win.winfo_width()
        h = win.winfo_height()
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        x = (sw // 2) - (w // 2)
        y = (sh // 2) - (h // 2)
        win.geometry(f"{w}x{h}+{x}+{y}")
    except Exception:
        pass


def run_in_thread(fn, *args, **kwargs) -> None:
    """Start a daemon thread for long-running tasks (keeps UI responsive)."""
    threading.Thread(target=lambda: fn(*args, **kwargs), daemon=True).start()


# =============================================================================
# App
# =============================================================================

class GrocerySenseApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Grocery Sense - Prototype")
        self.geometry("980x700")
        self.configure(bg=_BG)

        # Dark mode is always on
        apply_dark_theme(self)

        initialize_database()

        self.shopping_list_service = ShoppingListService()
        self.meal_suggestion_service = MealSuggestionService(price_history_service=None)
        self.weekly_planner_service = WeeklyPlannerService(
            meal_suggestion_service=self.meal_suggestion_service,
            shopping_list_service=self.shopping_list_service,
        )

        # Your PlanningService currently expects no args (based on your runtime error earlier)
        self.planning_service = PlanningService()

        self._log_box: Optional[tk.Text] = None
        self._log_fn: Callable[[str], None] = lambda _msg: None

        self._build_main_menu()
        self._build_log_panel()
        self._log("App started.")

    # ------------------------------------------------------------------
    # Logging / safety
    # ------------------------------------------------------------------

    def _build_log_panel(self) -> None:
        text, frame = build_output_area(self, height=12)
        frame.pack(side=tk.BOTTOM, fill=tk.BOTH, expand=False, padx=16, pady=6)
        self._log_box = text
        self._log_fn = make_threadsafe_logger(self, text)
        self._log("Log initialized.")

    def _log(self, message: str) -> None:
        try:
            self._log_fn(str(message))
        except Exception:
            pass

    def _log_exception(self, prefix: str) -> None:
        self._log(prefix)
        self._log(traceback.format_exc())

    def _safe_call(self, func):
        def wrapper():
            try:
                func()
            except Exception:
                self._log_exception("ERROR:")
                messagebox.showerror("Error", traceback.format_exc())
        return wrapper

    # ------------------------------------------------------------------
    # Main Menu
    # ------------------------------------------------------------------

    def _build_main_menu(self) -> None:
        frame = ttk.Frame(self)
        frame.pack(side=tk.TOP, fill=tk.X, padx=16, pady=14)

        ttk.Label(frame, text="Grocery Sense - Main Menu", font=("Segoe UI", 14, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 10)
        )

        row = 1

        ttk.Button(
            frame,
            text="1) Initialize / Verify Database",
            command=self._safe_call(self._handle_init_db),
            width=35,
        ).grid(row=row, column=0, sticky="w", pady=3)
        row += 1

        ttk.Button(
            frame,
            text="2) Shopping List",
            command=self._safe_call(self._open_shopping_list_window),
            width=35,
        ).grid(row=row, column=0, sticky="w", pady=3)
        row += 1

        ttk.Button(
            frame,
            text="3) Meal Suggestions",
            command=self._safe_call(self._open_meal_suggestions_window),
            width=35,
        ).grid(row=row, column=0, sticky="w", pady=3)
        row += 1

        ttk.Button(
            frame,
            text="4) Build Weekly Plan",
            command=self._safe_call(self._open_weekly_plan_window),
            width=35,
        ).grid(row=row, column=0, sticky="w", pady=3)
        row += 1

        ttk.Button(
            frame,
            text="5) Receipt Import (Azure)",
            command=self._safe_call(lambda: open_receipt_import_window(self, log=self._log)),
            width=35,
        ).grid(row=row, column=0, sticky="w", pady=3)
        row += 1

        ttk.Button(
            frame,
            text="6) Receipt Browser + Delete/Undo",
            command=self._safe_call(lambda: open_receipt_browser_window(self, log=self._log)),
            width=35,
        ).grid(row=row, column=0, sticky="w", pady=3)
        row += 1

        ttk.Button(
            frame,
            text="7) Stores Management",
            command=self._safe_call(self._open_stores_management_window),
            width=35,
        ).grid(row=row, column=0, sticky="w", pady=3)
        row += 1

        ttk.Button(
            frame,
            text="8) Store Plan (with savings)",
            command=self._safe_call(lambda: open_store_plan_window(self, log=self._log)),
            width=35,
        ).grid(row=row, column=0, sticky="w", pady=3)
        row += 1

        ttk.Button(
            frame,
            text="9) Price History Viewer",
            command=self._safe_call(lambda: open_price_history_window(self)),
            width=35,
        ).grid(row=row, column=0, sticky="w", pady=3)
        row += 1

        ttk.Button(
            frame,
            text="10) Item Manager",
            command=self._safe_call(lambda: open_item_manager_window(self, log=self._log)),
            width=35,
        ).grid(row=row, column=0, sticky="w", pady=3)
        row += 1

        ttk.Button(
            frame,
            text="11) Flyer Import (Manual)",
            command=self._safe_call(lambda: open_flyer_import_window(self, log=self._log)),
            width=35,
        ).grid(row=row, column=0, sticky="w", pady=3)
        row += 1

        ttk.Button(
            frame,
            text="12) Seed Demo Data",
            command=self._safe_call(self._seed_demo_data),
            width=35,
        ).grid(row=row, column=0, sticky="w", pady=3)
        row += 1

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _handle_init_db(self) -> None:
        initialize_database()
        self._log("[INFO] Database initialized 1 | Verified 1 | Errors 0")

    def _open_stores_management_window(self) -> None:
        messagebox.showinfo(
            "Stores Management",
            "Stores Management screen is not wired here yet.\n\n"
            "If you have a stores window module, tell me its import path\n"
            "and I’ll hook it into this menu.",
        )

    def _seed_demo_data(self) -> None:
        result = seed_demo_data(reset_first=True, n_price_points=200, days_back=90, seed=42)
        self._log(
            f"[INFO] Demo seed complete stores {result['stores']} | items {result['items']} | prices {result['price_points']}"
        )

    # ------------------------------------------------------------------
    # Shopping List window
    # ------------------------------------------------------------------

    def _open_shopping_list_window(self) -> None:
        win = tk.Toplevel(self)
        win.title("Shopping List")
        win.geometry("820x560")
        win.configure(bg=_BG)

        root = ttk.Frame(win)
        root.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        ttk.Label(root, text="Shopping List", font=("Segoe UI", 12, "bold")).grid(
            row=0, column=0, sticky="w"
        )

        add_frame = ttk.LabelFrame(root, text="Add Item")
        add_frame.grid(row=1, column=0, sticky="ew", pady=(10, 10))
        add_frame.columnconfigure(1, weight=1)

        ttk.Label(add_frame, text="Name").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        name_var = tk.StringVar()
        name_entry = ttk.Entry(add_frame, textvariable=name_var)
        name_entry.grid(row=0, column=1, sticky="ew", padx=8, pady=6)

        ttk.Label(add_frame, text="Qty").grid(row=0, column=2, sticky="w", padx=8, pady=6)
        qty_var = tk.StringVar()
        qty_entry = ttk.Entry(add_frame, textvariable=qty_var, width=10)
        qty_entry.grid(row=0, column=3, sticky="w", padx=8, pady=6)

        ttk.Label(add_frame, text="Unit").grid(row=0, column=4, sticky="w", padx=8, pady=6)
        unit_var = tk.StringVar(value="each")
        unit_entry = ttk.Entry(add_frame, textvariable=unit_var, width=10)
        unit_entry.grid(row=0, column=5, sticky="w", padx=8, pady=6)

        list_frame = ttk.Frame(root)
        list_frame.grid(row=2, column=0, sticky="nsew")
        root.rowconfigure(2, weight=1)
        root.columnconfigure(0, weight=1)

        listbox = tk.Listbox(list_frame, height=14)
        style_listbox(listbox)

        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=listbox.yview)
        listbox.configure(yscrollcommand=scrollbar.set)

        listbox.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)

        current_items = []

        def refresh() -> None:
            nonlocal current_items
            listbox.delete(0, tk.END)
            current_items = self.shopping_list_service.get_active_items(include_checked_off=True)

            if not current_items:
                listbox.insert(tk.END, "(no items)")
                return

            for it in current_items:
                status = "✓" if it.is_checked_off else " "
                qty = "" if it.quantity is None else str(it.quantity)
                unit = "" if it.unit is None else str(it.unit)
                mapped = "" if it.item_id is None else f" item_id={it.item_id}"
                line = f"[{status}] id={it.id}  {it.display_name}  {qty} {unit}{mapped}"
                listbox.insert(tk.END, line)

        def get_selected_item():
            if not current_items:
                return None
            sel = listbox.curselection()
            if not sel:
                self._log("Shopping List: select an item first.")
                return None
            idx = int(sel[0])
            if idx < 0 or idx >= len(current_items):
                self._log("Shopping List: invalid selection.")
                return None
            return current_items[idx]

        def on_add_item() -> None:
            name = (name_var.get() or "").strip()
            if not name:
                self._log("Add Item: name is required.")
                return

            qty_raw = (qty_var.get() or "").strip()
            unit = (unit_var.get() or "").strip() or "each"

            quantity = None
            if qty_raw:
                try:
                    quantity = float(qty_raw)
                except ValueError:
                    self._log("Add Item: qty must be a number (or blank).")
                    return

            self.shopping_list_service.add_single_item(
                name=name,
                quantity=quantity,
                unit=unit,
                planned_store_id=None,
                notes=None,
                added_by="tk_ui",
                item_id=None,
                auto_map=True,
            )
            self._log(f"Added: {name} ({quantity or ''} {unit})")
            name_var.set("")
            qty_var.set("")
            name_entry.focus_set()
            refresh()

        def on_toggle_checked() -> None:
            it = get_selected_item()
            if not it:
                return
            new_state = not bool(it.is_checked_off)
            self.shopping_list_service.check_off_item(it.id, checked=new_state)
            self._log(f"{'Checked off' if new_state else 'Unchecked'}: {it.display_name} (id={it.id})")
            refresh()

        def on_delete_item() -> None:
            it = get_selected_item()
            if not it:
                return
            self.shopping_list_service.soft_delete_item(it.id)
            self._log(f"Deleted: {it.display_name} (id={it.id})")
            refresh()

        btn_frame = ttk.Frame(root)
        btn_frame.grid(row=3, column=0, sticky="ew", pady=(10, 0))

        ttk.Button(add_frame, text="Add", command=self._safe_call(on_add_item), width=10).grid(
            row=0, column=6, padx=8, pady=6
        )
        ttk.Button(btn_frame, text="Refresh", command=self._safe_call(refresh)).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn_frame, text="Check off / Uncheck", command=self._safe_call(on_toggle_checked)).pack(
            side=tk.LEFT, padx=(0, 8)
        )
        ttk.Button(btn_frame, text="Delete", command=self._safe_call(on_delete_item)).pack(side=tk.LEFT)

        win.bind("<Return>", lambda _e: on_add_item())

        refresh()
        name_entry.focus_set()

    # ------------------------------------------------------------------
    # Meal Suggestions
    # ------------------------------------------------------------------

    def _format_meal_suggestion(self, s) -> str:
        recipe = getattr(s, "recipe", None) or {}
        name = recipe.get("name") or recipe.get("title") or "Recipe"
        total_score = getattr(s, "total_score", None)
        reasons = getattr(s, "reasons", None) or []
        lines = [f"{name}"]
        if total_score is not None:
            lines.append(f"\nScore: {total_score}")
        if reasons:
            lines.append("\nReasons:")
            for r in reasons:
                lines.append(f" - {r}")
        return "\n".join(lines) + "\n"

    def _open_meal_suggestions_window(self) -> None:
        win = tk.Toplevel(self)
        win.title("Meal Suggestions")
        win.geometry("860x540")
        win.configure(bg=_BG)

        top_frame = ttk.Frame(win)
        top_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=12, pady=12)

        ttk.Label(top_frame, text="Meal Suggestions", font=("Segoe UI", 11, "bold")).grid(
            row=0, column=0, sticky="w"
        )

        listbox = tk.Listbox(top_frame, width=35)
        style_listbox(listbox)
        listbox.grid(row=1, column=0, sticky="nsw", pady=10)

        details = ScrolledText(top_frame, state=tk.NORMAL)
        style_text_widget(details)
        details.grid(row=1, column=1, sticky="nsew", padx=(10, 0), pady=10)

        top_frame.grid_columnconfigure(1, weight=1)
        top_frame.grid_rowconfigure(1, weight=1)

        suggestions = self.meal_suggestion_service.suggest_meals_for_week(max_recipes=10)

        for s in suggestions:
            recipe = getattr(s, "recipe", None) or {}
            name = recipe.get("name") or recipe.get("title") or "Recipe"
            listbox.insert(tk.END, name)

        def on_select(_evt):
            idxs = listbox.curselection()
            if not idxs:
                return
            s = suggestions[int(idxs[0])]
            details.delete("1.0", tk.END)
            details.insert(tk.END, self._format_meal_suggestion(s))

        listbox.bind("<<ListboxSelect>>", on_select)

    # ------------------------------------------------------------------
    # Weekly Plan
    # ------------------------------------------------------------------

    def _open_weekly_plan_window(self) -> None:
        win = tk.Toplevel(self)
        win.title("Weekly Plan")
        win.geometry("860x580")
        win.configure(bg=_BG)

        ttk.Label(win, text="Weekly Plan", font=("Segoe UI", 11, "bold")).pack(
            side=tk.TOP, anchor="w", padx=12, pady=12
        )

        summary_box = ScrolledText(win, state=tk.NORMAL)
        style_text_widget(summary_box)
        summary_box.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=12, pady=(0, 10))

        def build_plan():
            summary_box.delete("1.0", tk.END)
            self._log("Building weekly plan (6 recipes, added to shopping list)...")

            plan = self.weekly_planner_service.build_weekly_plan(
                num_recipes=6,
                persist_to_shopping_list=True,
                planned_store_id=None,
                added_by="weekly_planner_ui",
            )

            for line in summarize_weekly_plan(plan):
                summary_box.insert(tk.END, line + "\n")

            summary_box.insert(tk.END, "\nIngredients:\n")
            for ing in plan.planned_ingredients:
                mapped = "" if ing.item_id is None else f" item_id={ing.item_id} ({ing.match_confidence or 0:.2f})"
                summary_box.insert(
                    tk.END,
                    f" - {ing.name} (in {ing.approximate_count} recipes){mapped}\n",
                )

        ttk.Button(win, text="Build Weekly Plan", command=self._safe_call(build_plan)).pack(
            side=tk.BOTTOM, pady=10
        )

        build_plan()

def main() -> None:
    app = GrocerySenseApp()
    app.mainloop()


if __name__ == "__main__":
    main()
