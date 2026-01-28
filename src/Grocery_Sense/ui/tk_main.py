"""
Grocery_Sense.ui.tk_main

Tkinter prototype UI for Grocery Sense.

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
"""

from __future__ import annotations

import traceback
import tkinter as tk
from tkinter import ttk, messagebox
from tkinter.scrolledtext import ScrolledText

from Grocery_Sense.data.schema import initialize_database

from Grocery_Sense.services.shopping_list_service import ShoppingListService
from Grocery_Sense.services.meal_suggestion_service import MealSuggestionService

from Grocery_Sense.services.weekly_planner_service import (
    WeeklyPlannerService,
    summarize_weekly_plan,
)
from Grocery_Sense.services.planning_service import PlanningService
from Grocery_Sense.services.demo_seed_service import seed_demo_data

from Grocery_Sense.ui.deal_feed_window import open_deal_feed_window
from Grocery_Sense.ui.flyer_import_window import open_flyer_import_window
from Grocery_Sense.ui.item_manager_window import open_item_manager_window
from Grocery_Sense.ui.preference_window import open_preferences_window
from Grocery_Sense.ui.price_history_window import open_price_history_window
from Grocery_Sense.ui.receipt_import_window import open_receipt_import_window
from Grocery_Sense.ui.receipt_browser_window import open_receipt_browser_window
from Grocery_Sense.ui.store_plan_window import open_store_plan_window



class GrocerySenseApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Grocery Sense - Prototype")
        self.geometry("980x700")

        initialize_database()

        self.shopping_list_service = ShoppingListService()
        self.meal_suggestion_service = MealSuggestionService(price_history_service=None)
        self.weekly_planner_service = WeeklyPlannerService(
            meal_suggestion_service=self.meal_suggestion_service,
            shopping_list_service=self.shopping_list_service,
        )
        self.planning_service = PlanningService()


        self._build_main_menu()
        self._build_log_panel()
        self._log("App started.")

    # ------------------------------------------------------------------
    # Base UI helpers
    # ------------------------------------------------------------------

    def _build_main_menu(self) -> None:
        frame = ttk.Frame(self)
        frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=10)

        ttk.Label(frame, text="Grocery Sense - Main Menu", font=("Segoe UI", 14, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 10)
        )

        row = 1

        ttk.Button(
            frame,
            text="1) Initialize / Verify Database",
            command=self._safe_call(self._handle_init_db),
            width=35,
        ).grid(row=row, column=0, sticky="w", pady=2)
        row += 1

        ttk.Button(
            frame,
            text="2) Shopping List",
            command=self._safe_call(self._open_shopping_list_window),
            width=35,
        ).grid(row=row, column=0, sticky="w", pady=2)
        row += 1

        ttk.Button(
            frame,
            text="3) Meal Suggestions",
            command=self._safe_call(self._open_meal_suggestions_window),
            width=35,
        ).grid(row=row, column=0, sticky="w", pady=2)
        row += 1

        ttk.Button(
            frame,
            text="4) Build Weekly Plan",
            command=self._safe_call(self._open_weekly_plan_window),
            width=35,
        ).grid(row=row, column=0, sticky="w", pady=2)
        row += 1

        ttk.Button(
            frame,
            text="5) Receipt Import (Azure)",
            command=self._safe_call(lambda: open_receipt_import_window(self, log=self._log)),
            width=35,
        ).grid(row=row, column=0, sticky="w", pady=2)
        row += 1

        ttk.Button(
            frame,
            text="6) Receipt Browser + Delete/Undo",
            command=self._safe_call(lambda: open_receipt_browser_window(self, log=self._log)),
            width=35,
        ).grid(row=row, column=0, sticky="w", pady=2)
        row += 1

        ttk.Button(
            frame,
            text="7) Stores Management",
            command=self._safe_call(self._open_stores_management_window),
            width=35,
        ).grid(row=row, column=0, sticky="w", pady=2)
        row += 1

        ttk.Button(
            frame,
            text="8) Store Plan (with savings)",
            command=self._safe_call(lambda: open_store_plan_window(self, log=self._log)),
            width=35,
        ).grid(row=row, column=0, sticky="w", pady=2)
        row += 1

        ttk.Button(
            frame,
            text="9) Price History Viewer",
            command=self._safe_call(lambda: open_price_history_window(self)),
            width=35,
        ).grid(row=row, column=0, sticky="w", pady=2)
        row += 1

        ttk.Button(
            frame,
            text="10) Item Manager",
            command=self._safe_call(lambda: open_item_manager_window(self, log=self._log)),
            width=35,
        ).grid(row=row, column=0, sticky="w", pady=2)
        row += 1
	
        ttk.Button(
    	    frame,
    	    text="11) Preferences",
    	    command=self._safe_call(lambda: open_preferences_window(self, log=self._log)),
    	    width=35,
	    ).grid(row=row, column=0, sticky="w", pady=2)
        row += 1

        ttk.Button(
    	    frame,
    	    text="12) Deal Feed (Active)",
    	    command=self._safe_call(lambda: open_deal_feed_window(self, log=self._log)),
    	    width=35,
	    ).grid(row=row, column=0, sticky="w", pady=2)
        row += 1

        ttk.Button(
            frame,
            text="13) Flyer Import (Manual)",
            command=self._safe_call(lambda: open_flyer_import_window(self, log=self._log)),
            width=35,
        ).grid(row=row, column=0, sticky="w", pady=2)
        row += 1

        ttk.Button(
            frame,
            text="14) Seed Demo Data",
            command=self._safe_call(self._seed_demo_data),
            width=35,
        ).grid(row=row, column=0, sticky="w", pady=2)
        row += 1

    def _build_log_panel(self) -> None:
        self.log_box = ScrolledText(self, state=tk.NORMAL, height=12)
        self.log_box.pack(side=tk.BOTTOM, fill=tk.BOTH, expand=False, padx=10, pady=10)
        self._log("Log initialized.")

    def _log(self, message: str) -> None:
        try:
            self.log_box.insert(tk.END, message + "\n")
            self.log_box.see(tk.END)
        except Exception:
            # If log box isn't built yet
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
    # Handlers / windows
    # ------------------------------------------------------------------

    def _handle_init_db(self) -> None:
        initialize_database()
        self._log("Database schema initialized / verified.")

    def _open_stores_management_window(self) -> None:
        """
        You referenced this in the main menu.
        If you already have a stores management window module, import and call it here.

        For now: safe placeholder so the UI runs cleanly.
        """
        messagebox.showinfo(
            "Stores Management",
            "Stores Management screen is not wired in this tk_main.py yet.\n\n"
            "If you have it already, tell me the module path (e.g. Grocery_Sense.ui.stores_management_window)\n"
            "and I’ll hook it up.",
        )

    def _seed_demo_data(self) -> None:
        result = seed_demo_data(reset_first=True, n_price_points=200, days_back=90, seed=42)
        self._log(
            f"Demo seed complete: stores={result['stores']}, "
            f"items={result['items']}, prices={result['price_points']}"
        )

    # ------------------------------------------------------------------
    # Shopping List window
    # ------------------------------------------------------------------

    def _open_shopping_list_window(self) -> None:
        win = tk.Toplevel(self)
        win.title("Shopping List")
        win.geometry("820x560")

        root = ttk.Frame(win)
        root.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        ttk.Label(root, text="Shopping List", font=("Segoe UI", 12, "bold")).grid(
            row=0, column=0, sticky="w"
        )

        # --- Add item panel
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

        # --- List panel
        list_frame = ttk.Frame(root)
        list_frame.grid(row=2, column=0, sticky="nsew")
        root.rowconfigure(2, weight=1)
        root.columnconfigure(0, weight=1)

        listbox = tk.Listbox(list_frame, height=14)
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
                auto_map=True,  # mapping
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

    def _open_meal_suggestions_window(self) -> None:
        win = tk.Toplevel(self)
        win.title("Meal Suggestions")
        win.geometry("860x540")

        top_frame = ttk.Frame(win)
        top_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=10)

        ttk.Label(top_frame, text="Meal Suggestions", font=("Segoe UI", 11, "bold")).grid(
            row=0, column=0, sticky="w"
        )

        listbox = tk.Listbox(top_frame, width=35)
        listbox.grid(row=1, column=0, sticky="nsw", pady=10)

        details = ScrolledText(top_frame, state=tk.NORMAL)
        details.grid(row=1, column=1, sticky="nsew", padx=(10, 0), pady=10)

        top_frame.grid_columnconfigure(1, weight=1)
        top_frame.grid_rowconfigure(1, weight=1)

        suggestions = self.meal_suggestion_service.suggest_meals_for_week(max_recipes=10)

        for s in suggestions:
            name = s.recipe.get("name") or s.recipe.get("title") or "Recipe"
            listbox.insert(tk.END, name)

        def on_select(_evt):
            idxs = listbox.curselection()
            if not idxs:
                return
            s = suggestions[int(idxs[0])]
            details.delete("1.0", tk.END)
            details.insert(tk.END, explain_suggested_meal(s))

        listbox.bind("<<ListboxSelect>>", on_select)

    # ------------------------------------------------------------------
    # Weekly Plan
    # ------------------------------------------------------------------

    def _open_weekly_plan_window(self) -> None:
        win = tk.Toplevel(self)
        win.title("Weekly Plan")
        win.geometry("860x580")

        ttk.Label(win, text="Weekly Plan", font=("Segoe UI", 11, "bold")).pack(
            side=tk.TOP, anchor="w", padx=10, pady=10
        )

        summary_box = ScrolledText(win, state=tk.NORMAL)
        summary_box.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

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
            side=tk.BOTTOM, pady=8
        )

        build_plan()

    # ------------------------------------------------------------------
    # Store Plan (simple renderer)
    # ------------------------------------------------------------------

    def _open_store_plan_window(self) -> None:
        win = tk.Toplevel(self)
        win.title("Store Plan")
        win.geometry("900x600")

        root = ttk.Frame(win)
        root.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        header = ttk.Frame(root)
        header.pack(fill=tk.X)

        ttk.Label(header, text="Store Plan", font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT)

        ttk.Label(header, text="Max stores:").pack(side=tk.LEFT, padx=(20, 6))
        max_var = tk.StringVar(value="3")
        max_entry = ttk.Entry(header, textvariable=max_var, width=6)
        max_entry.pack(side=tk.LEFT)

        output = ScrolledText(root, state=tk.NORMAL)
        output.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        def render_plan() -> None:
            output.delete("1.0", tk.END)

            try:
                max_stores = int((max_var.get() or "3").strip())
                if max_stores < 1:
                    max_stores = 1
            except ValueError:
                max_stores = 3

            plan = self.planning_service.build_plan_for_active_list(max_stores=max_stores)

            summary = str(plan.get("summary") or "")
            output.insert(tk.END, summary + "\n\n")

            stores_struct = plan.get("stores") or {}
            if not stores_struct:
                output.insert(tk.END, "(No stores selected)\n")
            else:
                store_rows = []
                for sid, payload in stores_struct.items():
                    items = payload.get("items") or []
                    store_rows.append((sid, payload, len(items)))
                store_rows.sort(key=lambda x: x[2], reverse=True)

                for _sid, payload, _count in store_rows:
                    st = payload.get("store")
                    items = payload.get("items") or []
                    if not st:
                        continue

                    fav = " ★" if getattr(st, "is_favorite", False) else ""
                    pri = getattr(st, "priority", 0) or 0
                    output.insert(tk.END, f"{st.name}{fav} (priority={pri})\n")
                    for it in items:
                        qty = "" if it.quantity is None else str(it.quantity)
                        unit = "" if it.unit is None else str(it.unit)
                        mapped = "" if it.item_id is None else f" [item_id={it.item_id}]"
                        output.insert(tk.END, f"  - {it.display_name} {qty} {unit}{mapped}\n")
                    output.insert(tk.END, "\n")

            unassigned = plan.get("unassigned") or []
            if unassigned:
                output.insert(tk.END, "Unassigned:\n")
                for it in unassigned:
                    qty = "" if it.quantity is None else str(it.quantity)
                    unit = "" if it.unit is None else str(it.unit)
                    mapped = "" if it.item_id is None else f" [item_id={it.item_id}]"
                    output.insert(tk.END, f"  - {it.display_name} {qty} {unit}{mapped}\n")
                output.insert(tk.END, "\n")

        ttk.Button(header, text="Refresh", command=self._safe_call(render_plan)).pack(side=tk.RIGHT)

        render_plan()


def main() -> None:
    app = GrocerySenseApp()
    app.mainloop()


if __name__ == "__main__":
    main()
