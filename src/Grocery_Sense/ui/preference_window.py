from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from typing import Any, Callable, Dict, List, Optional, Set

from Grocery_Sense import config_store
from Grocery_Sense.services import preferences_service


# ----------------------------
# Small reusable UI helpers
# ----------------------------

class _ScrollableFrame(ttk.Frame):
    def __init__(self, master: tk.Misc, *, height: int = 220) -> None:
        super().__init__(master)

        self._canvas = tk.Canvas(self, highlightthickness=0, height=height)
        self._vsb = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self._inner = ttk.Frame(self._canvas)

        self._inner.bind("<Configure>", lambda _e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._window_id = self._canvas.create_window((0, 0), window=self._inner, anchor="nw")
        self._canvas.configure(yscrollcommand=self._vsb.set)

        self._canvas.pack(side="left", fill="both", expand=True)
        self._vsb.pack(side="right", fill="y")

        self._canvas.bind("<Configure>", self._on_canvas_configure)

    def _on_canvas_configure(self, evt) -> None:
        try:
            self._canvas.itemconfigure(self._window_id, width=evt.width)
        except Exception:
            pass

    @property
    def inner(self) -> ttk.Frame:
        return self._inner


class _ListEditor(ttk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        *,
        title: str,
        on_changed: Callable[[], None],
        height: int = 7,
        hint: str = "",
        validate_add: Optional[Callable[[str], Optional[str]]] = None,
    ) -> None:
        super().__init__(master)
        self._on_changed = on_changed
        self._validate_add = validate_add

        ttk.Label(self, text=title, font=("Segoe UI", 10, "bold")).pack(anchor="w")
        if hint:
            ttk.Label(self, text=hint, foreground="#888").pack(anchor="w", pady=(0, 6))

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)

        self.listbox = tk.Listbox(body, height=height)
        vsb = ttk.Scrollbar(body, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=vsb.set)

        self.listbox.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        btns = ttk.Frame(self)
        btns.pack(fill="x", pady=(6, 0))

        self._btn_add = ttk.Button(btns, text="Add", command=self._add)
        self._btn_remove = ttk.Button(btns, text="Remove", command=self._remove)
        self._btn_add.pack(side="left")
        self._btn_remove.pack(side="left", padx=(8, 0))

        self._values: List[str] = []

    def set_values(self, values: List[str]) -> None:
        self._values = self._normalize(values)
        self._render()

    def get_values(self) -> List[str]:
        return list(self._values)

    def set_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.listbox.configure(state=state)
        for btn in (self._btn_add, self._btn_remove):
            try:
                btn.configure(state=state)
            except Exception:
                pass

    def _render(self) -> None:
        self.listbox.delete(0, tk.END)
        for v in self._values:
            self.listbox.insert(tk.END, v)

    def _normalize(self, values: Any) -> List[str]:
        if not values:
            return []
        out: List[str] = []
        if isinstance(values, str):
            values = [values]
        for v in list(values):
            s = str(v).strip().lower()
            if s and s not in out:
                out.append(s)
        return out

    def _add(self) -> None:
        raw = simpledialog.askstring("Add", "Enter a value (e.g., 'olives'):", parent=self)
        if not raw:
            return
        s = raw.strip().lower()
        if not s:
            return

        # NEW: validate hook
        if self._validate_add:
            err = self._validate_add(s)
            if err:
                messagebox.showwarning("Not allowed", err, parent=self)
                return

        if s not in self._values:
            self._values.append(s)
            self._values.sort()
            self._render()
            self._on_changed()

    def _remove(self) -> None:
        sel = self.listbox.curselection()
        if not sel:
            return
        idx = int(sel[0])
        if 0 <= idx < len(self._values):
            self._values.pop(idx)
            self._render()
            self._on_changed()



def _profile_get_styles(profile: Dict[str, Any]) -> List[str]:
    """
    Backwards/forwards compatible: supports 'styles' (current) and 'meal_styles' (older/newer).
    """
    v = profile.get("styles", None)
    if isinstance(v, list):
        return [str(x).strip().lower() for x in v if str(x).strip()]
    v2 = profile.get("meal_styles", None)
    if isinstance(v2, list):
        return [str(x).strip().lower() for x in v2 if str(x).strip()]
    return []


# ----------------------------
# 8-step guided wizard
# ----------------------------

class PreferencesWizardWindow(tk.Toplevel):
    """
    8 steps total:
      1) Who are you editing?
      2) Diet basics (meat/fish/dairy/eggs)
      3) Proteins (exclude list) + (master only) weights
      4) Allergies (always hard, household-wide)
      5) Ingredient excludes (master hard+soft; secondary soft only + show household hard)
      6) Oils used
      7) Favorite cuisines
      8) Meal style + spice + Review & Save
    """

    def __init__(
        self,
        master: Optional[tk.Misc] = None,
        *,
        initial_member_id: Optional[int] = None,
        editor_member_id: Optional[int] = None,
        log: Optional[Callable[[str], None]] = None,
        on_saved: Optional[Callable[[int], None]] = None,
    ) -> None:
        super().__init__(master)
        self.title("Preferences Wizard")
        self.geometry("980x700")
        self.minsize(920, 640)

        self._log = log
        self._on_saved = on_saved

        self._cfg = config_store.load_config()

        self._editor_member = config_store.get_member(editor_member_id or config_store.get_active_member().id) or config_store.get_active_member()
        self._editor_member_id = int(self._editor_member.id)

        # Editable targets: master can edit anyone; secondary only themselves
        self._editable_member_ids = self._compute_editable_member_ids(self._editor_member_id)
        self._editable_members = [config_store.get_member(mid) for mid in self._editable_member_ids]
        self._editable_members = [m for m in self._editable_members if m is not None]

        # Choose initial target (must be allowed)
        target_id = initial_member_id if initial_member_id in self._editable_member_ids else (
            self._editable_member_ids[0] if self._editable_member_ids else None
        )
        if target_id is None and config_store.list_members():
            target_id = config_store.get_active_member().id

        self._target_member_id: int = int(target_id or config_store.get_master_member().id)

        # Vars
        self._dirty = False

        self._eats_meat = tk.BooleanVar(value=True)
        self._eats_fish = tk.BooleanVar(value=True)
        self._eats_dairy = tk.BooleanVar(value=True)
        self._eats_eggs = tk.BooleanVar(value=True)

        self._protein_allowed_vars: Dict[str, tk.BooleanVar] = {
            p: tk.BooleanVar(value=True) for p in preferences_service.PROTEINS
        }
        self._protein_weight_vars: Dict[str, tk.DoubleVar] = {
            p: tk.DoubleVar(value=1.0) for p in preferences_service.PROTEINS
        }

        self._spice_var = tk.StringVar(value="medium")
        self._style_vars: Dict[str, tk.BooleanVar] = {
            tag: tk.BooleanVar(value=False) for tag, _label in preferences_service.STYLE_TAGS
        }
        self._cuisine_vars: Dict[str, tk.BooleanVar] = {
            c: tk.BooleanVar(value=False) for c in preferences_service.CUISINES
        }
        self._oil_vars: Dict[str, tk.BooleanVar] = {
            o: tk.BooleanVar(value=False) for o in preferences_service.OILS
        }

        # Editors (created per step)
        self._allergies_editor: Optional[_ListEditor] = None
        self._hard_editor: Optional[_ListEditor] = None
        self._soft_editor: Optional[_ListEditor] = None

        # For step 5 secondary: show baseline hard excludes read-only
        self._household_hard_excludes_lb: Optional[tk.Listbox] = None

        # Step machinery
        self._step_index = 0
        self._steps: List[ttk.Frame] = []
        self._step_titles: List[str] = []

        self._build_ui()
        self._build_steps()
        self._load_target_into_vars(self._target_member_id)
        self._show_step(0)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- Permissions (NO dependencies on preferences_service) ----------

    def _compute_editable_member_ids(self, editor_member_id: int) -> List[int]:
        editor = config_store.get_member(editor_member_id) or config_store.get_active_member()
        if editor.role == config_store.ROLE_MASTER:
            return [m.id for m in config_store.list_members()]
        return [editor.id]

    def _can_edit_target(self, target_member_id: int) -> bool:
        editor = config_store.get_member(self._editor_member_id) or config_store.get_active_member()
        if editor.role == config_store.ROLE_MASTER:
            return True
        return int(editor.id) == int(target_member_id)

    # ---------- Baseline hard excludes set (used for Step 5 UI + validation) ----------

    def _baseline_hard_excludes_set(self) -> Set[str]:
        baseline = preferences_service.get_household_baseline_profile()
        raw = baseline.get("hard_excludes", []) or []
        out: Set[str] = set()
        for x in raw:
            s = str(x).strip().lower()
            if s:
                out.add(s)
        return out

    def _validate_soft_exclude_add(self, value: str) -> Optional[str]:
        """
        NEW: When editing a secondary member, block adding items already in household hard excludes.
        """
        tgt = config_store.get_member(self._target_member_id)
        if not tgt:
            return None
        if tgt.role == config_store.ROLE_MASTER:
            return None

        baseline_hard = self._baseline_hard_excludes_set()
        if value in baseline_hard:
            return f"'{value}' is already a household hard exclude (baseline). It’s redundant to add it as a soft exclude."
        return None

    # ---------- UI shell ----------

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        # Header
        self._hdr = ttk.Frame(root)
        self._hdr.pack(fill="x")

        self._title_lbl = ttk.Label(self._hdr, text="Preferences Wizard", font=("Segoe UI", 14, "bold"))
        self._title_lbl.pack(side="left")

        self._step_lbl = ttk.Label(self._hdr, text="", foreground="#666")
        self._step_lbl.pack(side="right")

        # Body container
        self._body = ttk.Frame(root)
        self._body.pack(fill="both", expand=True, pady=(10, 10))
        self._body.columnconfigure(0, weight=1)
        self._body.rowconfigure(0, weight=1)

        # Footer nav
        nav = ttk.Frame(root)
        nav.pack(fill="x")

        self._btn_back = ttk.Button(nav, text="Back", command=self._back)
        self._btn_next = ttk.Button(nav, text="Next", command=self._next)
        self._btn_cancel = ttk.Button(nav, text="Cancel", command=self._on_close)

        self._btn_back.pack(side="left")
        self._btn_cancel.pack(side="right")
        self._btn_next.pack(side="right", padx=(0, 8))

    def _build_steps(self) -> None:
        self._steps.clear()
        self._step_titles.clear()

        self._steps.append(self._step_choose_member(self._body))
        self._step_titles.append("1/8  Who are you editing?")

        self._steps.append(self._step_diet_basics(self._body))
        self._step_titles.append("2/8  Diet basics")

        self._steps.append(self._step_proteins(self._body))
        self._step_titles.append("3/8  Proteins")

        self._steps.append(self._step_allergies(self._body))
        self._step_titles.append("4/8  Allergies")

        self._steps.append(self._step_excludes(self._body))
        self._step_titles.append("5/8  Ingredient excludes")

        self._steps.append(self._step_oils(self._body))
        self._step_titles.append("6/8  Oils used")

        self._steps.append(self._step_cuisines(self._body))
        self._step_titles.append("7/8  Favorite cuisines")

        self._steps.append(self._step_styles_review(self._body))
        self._step_titles.append("8/8  Style + Review")

        for f in self._steps:
            f.grid_forget()

    def _show_step(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._steps):
            return

        for f in self._steps:
            f.grid_forget()

        self._step_index = idx
        self._steps[idx].grid(row=0, column=0, sticky="nsew")

        self._step_lbl.config(text=self._step_titles[idx])

        self._btn_back.configure(state="disabled" if idx == 0 else "normal")
        self._btn_next.configure(text="Save" if idx == len(self._steps) - 1 else "Next")

        if idx == 0:
            self._refresh_choose_member_step()
        if idx == 4:
            self._refresh_excludes_step_readonly_list()
        if idx == len(self._steps) - 1:
            self._render_review_text()

    # ---------- Steps ----------

    def _step_choose_member(self, master: tk.Misc) -> ttk.Frame:
        f = ttk.Frame(master, padding=10)
        f.columnconfigure(0, weight=1)

        editor = self._editor_member
        editor_role = getattr(editor, "role", config_store.ROLE_SECONDARY)
        editor_name = getattr(editor, "name", "User")

        info = ttk.LabelFrame(f, text="Step 1: Select person to edit", padding=10)
        info.grid(row=0, column=0, sticky="ew")
        info.columnconfigure(1, weight=1)

        ttk.Label(info, text="Signed in as:", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(info, text=f"{editor_name} ({editor_role})").grid(row=0, column=1, sticky="w", padx=(8, 0))

        ttk.Label(info, text="Editing:", font=("Segoe UI", 10, "bold")).grid(row=1, column=0, sticky="w", pady=(10, 0))

        self._target_var = tk.StringVar(value="")
        self._target_combo = ttk.Combobox(info, state="readonly", width=34, textvariable=self._target_var)
        self._target_combo.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(10, 0))
        self._target_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_target_changed_from_combo())

        ttk.Label(
            f,
            text="Household baseline = Master profile.\n"
                 "• Secondary preferences become soft excludes (still allowed, flagged with '*').\n"
                 "• Allergies are always hard excludes for everyone.",
            foreground="#666",
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(12, 0))

        btn_row = ttk.Frame(f)
        btn_row.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        btn_row.columnconfigure(0, weight=1)

        self._btn_reset_secondary = ttk.Button(
            btn_row,
            text="Reset this secondary member to household baseline",
            command=self._reset_target_to_baseline,
        )
        self._btn_reset_secondary.pack(side="left")

        return f

    def _step_diet_basics(self, master: tk.Misc) -> ttk.Frame:
        f = ttk.Frame(master, padding=10)
        f.columnconfigure(0, weight=1)

        box = ttk.LabelFrame(f, text="Step 2: Diet basics", padding=10)
        box.grid(row=0, column=0, sticky="ew")

        ttk.Label(
            box,
            text="These toggles help the wizard pre-uncheck related proteins. You can still fine-tune on the next step.",
            foreground="#666",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        ttk.Checkbutton(
            box,
            text="Eats meat",
            variable=self._eats_meat,
            command=self._mark_dirty_and_sync_proteins,
        ).grid(row=1, column=0, sticky="w", padx=(0, 20), pady=2)

        ttk.Checkbutton(
            box,
            text="Eats fish/seafood",
            variable=self._eats_fish,
            command=self._mark_dirty_and_sync_proteins,
        ).grid(row=1, column=1, sticky="w", padx=(0, 20), pady=2)

        ttk.Checkbutton(
            box,
            text="Eats dairy",
            variable=self._eats_dairy,
            command=self._mark_dirty,
        ).grid(row=2, column=0, sticky="w", padx=(0, 20), pady=2)

        ttk.Checkbutton(
            box,
            text="Eats eggs",
            variable=self._eats_eggs,
            command=self._mark_dirty,
        ).grid(row=2, column=1, sticky="w", padx=(0, 20), pady=2)

        return f

    def _step_proteins(self, master: tk.Misc) -> ttk.Frame:
        f = ttk.Frame(master, padding=10)
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)

        proteins = ttk.LabelFrame(f, text="Step 3: Proteins (uncheck to exclude)", padding=10)
        proteins.grid(row=0, column=0, sticky="nsew")
        proteins.rowconfigure(0, weight=1)
        proteins.columnconfigure(0, weight=1)

        sf = _ScrollableFrame(proteins, height=240)
        sf.pack(fill="both", expand=True)

        for i, p in enumerate(preferences_service.PROTEINS):
            r = i // 2
            c = i % 2
            ttk.Checkbutton(
                sf.inner,
                text=p.title(),
                variable=self._protein_allowed_vars[p],
                command=self._mark_dirty,
            ).grid(row=r, column=c, sticky="w", padx=(0, 20), pady=2)

        self._weights_frame = ttk.LabelFrame(f, text="Master-only: protein preference weights", padding=10)
        self._weights_frame.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self._weights_frame.columnconfigure(0, weight=1)

        grid = ttk.Frame(self._weights_frame)
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)

        for i, p in enumerate(preferences_service.PROTEINS):
            ttk.Label(grid, text=p.title(), width=16).grid(row=i, column=0, sticky="w", pady=2)
            sc = ttk.Scale(
                grid,
                from_=0.5,
                to=2.0,
                variable=self._protein_weight_vars[p],
                command=lambda _v, _p=p: self._mark_dirty(),
            )
            sc.grid(row=i, column=1, sticky="ew", padx=(8, 8), pady=2)

        self._btn_prefer_chicken = ttk.Button(self._weights_frame, text="Quick: Prefer chicken", command=self._prefer_chicken)
        self._btn_prefer_chicken.pack(anchor="w", pady=(8, 0))

        return f

    def _step_allergies(self, master: tk.Misc) -> ttk.Frame:
        f = ttk.Frame(master, padding=10)
        f.columnconfigure(0, weight=1)

        self._allergies_editor = _ListEditor(
            f,
            title="Step 4: Allergies (ALWAYS hard exclude for the whole household)",
            hint="Example: peanut, shellfish. Any member allergy will block suggestions/deals.",
            on_changed=self._mark_dirty,
            height=10,
        )
        self._allergies_editor.grid(row=0, column=0, sticky="nsew")

        return f

    def _step_excludes(self, master: tk.Misc) -> ttk.Frame:
        f = ttk.Frame(master, padding=10)
        f.columnconfigure(0, weight=1)

        ttk.Label(
            f,
            text="Step 5: Ingredient excludes\n"
                 "• Master hard excludes = never show at all.\n"
                 "• Secondary excludes = soft excludes (still allowed, but flagged with '*').\n"
                 "• NEW: Secondary members cannot add items already hard-excluded by the household baseline (redundant).",
            foreground="#666",
            justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(0, 10))

        ro = ttk.LabelFrame(f, text="Household hard excludes (baseline)", padding=10)
        ro.grid(row=1, column=0, sticky="ew")
        ro.columnconfigure(0, weight=1)

        self._household_hard_excludes_lb = tk.Listbox(ro, height=5)
        self._household_hard_excludes_lb.grid(row=0, column=0, sticky="ew")
        self._household_hard_excludes_lb.configure(state="disabled")

        editors = ttk.Frame(f)
        editors.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
        editors.columnconfigure(0, weight=1)
        editors.columnconfigure(1, weight=1)

        self._hard_editor = _ListEditor(
            editors,
            title="Hard excludes (Master only)",
            hint="Never recommend these ingredients (e.g., real olives).",
            on_changed=self._mark_dirty,
            height=7,
        )
        self._hard_editor.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        # NEW: validate_add blocks redundant soft excludes for secondary targets
        self._soft_editor = _ListEditor(
            editors,
            title="Soft excludes (still allowed, but deprioritized)",
            hint="Example: tomatoes (if one child dislikes it).",
            on_changed=self._mark_dirty,
            height=7,
            validate_add=self._validate_soft_exclude_add,
        )
        self._soft_editor.grid(row=0, column=1, sticky="nsew")

        return f

    def _step_oils(self, master: tk.Misc) -> ttk.Frame:
        f = ttk.Frame(master, padding=10)
        f.columnconfigure(0, weight=1)

        ttk.Label(
            f,
            text="Step 6: Oils used at home.\nIf none are selected, the app treats oils as unrestricted.",
            foreground="#666",
            justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(0, 10))

        oils = ttk.LabelFrame(f, text="Allowed oils", padding=10)
        oils.grid(row=1, column=0, sticky="nsew")
        oils.rowconfigure(0, weight=1)
        oils.columnconfigure(0, weight=1)

        sf = _ScrollableFrame(oils, height=340)
        sf.pack(fill="both", expand=True)

        for i, o in enumerate(preferences_service.OILS):
            r = i // 2
            c = i % 2
            ttk.Checkbutton(sf.inner, text=o.title(), variable=self._oil_vars[o], command=self._mark_dirty).grid(
                row=r, column=c, sticky="w", padx=(0, 20), pady=2
            )

        return f

    def _step_cuisines(self, master: tk.Misc) -> ttk.Frame:
        f = ttk.Frame(master, padding=10)
        f.columnconfigure(0, weight=1)

        ttk.Label(
            f,
            text="Step 7: Favorite cuisines (used to prioritize deals/recipes you’ll actually eat).",
            foreground="#666",
        ).grid(row=0, column=0, sticky="w", pady=(0, 10))

        cuisines = ttk.LabelFrame(f, text="Favorite cuisines", padding=10)
        cuisines.grid(row=1, column=0, sticky="nsew")
        cuisines.rowconfigure(0, weight=1)
        cuisines.columnconfigure(0, weight=1)

        sf = _ScrollableFrame(cuisines, height=380)
        sf.pack(fill="both", expand=True)

        for i, c in enumerate(preferences_service.CUISINES):
            r = i // 2
            col = i % 2
            ttk.Checkbutton(sf.inner, text=c.title(), variable=self._cuisine_vars[c], command=self._mark_dirty).grid(
                row=r, column=col, sticky="w", padx=(0, 20), pady=2
            )

        return f

    def _step_styles_review(self, master: tk.Misc) -> ttk.Frame:
        f = ttk.Frame(master, padding=10)
        f.columnconfigure(0, weight=1)
        f.rowconfigure(3, weight=1)

        top = ttk.LabelFrame(f, text="Step 8: Meal style + spice", padding=10)
        top.grid(row=0, column=0, sticky="ew")

        row = ttk.Frame(top)
        row.pack(fill="x")

        ttk.Label(row, text="Spice preference:").pack(side="left")
        self._spice_combo = ttk.Combobox(row, state="readonly", width=12, values=["low", "medium", "high"], textvariable=self._spice_var)
        self._spice_combo.pack(side="left", padx=(8, 14))
        self._spice_combo.bind("<<ComboboxSelected>>", lambda _e: self._mark_dirty())

        styles = ttk.LabelFrame(f, text="Cooking styles", padding=10)
        styles.grid(row=1, column=0, sticky="ew", pady=(10, 0))

        for i, (tag, label) in enumerate(preferences_service.STYLE_TAGS):
            ttk.Checkbutton(styles, text=label, variable=self._style_vars[tag], command=self._mark_dirty).grid(
                row=i // 2, column=i % 2, sticky="w", padx=(0, 20), pady=2
            )

        rev = ttk.LabelFrame(f, text="Review", padding=10)
        rev.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
        rev.columnconfigure(0, weight=1)
        rev.rowconfigure(0, weight=1)

        self._review = tk.Text(rev, height=10, wrap="word")
        self._review.grid(row=0, column=0, sticky="nsew")
        self._review.configure(state="disabled")

        ttk.Label(
            f,
            text="Click Save to store these preferences for the selected member.",
            foreground="#666",
        ).grid(row=3, column=0, sticky="w", pady=(10, 0))

        return f

    # ---------- Step 1 helpers ----------

    def _refresh_choose_member_step(self) -> None:
        labels: List[str] = []
        self._label_to_id: Dict[str, int] = {}
        for m in self._editable_members:
            tag = " (Master)" if m.role == config_store.ROLE_MASTER else ""
            label = f"{m.name}{tag}"
            labels.append(label)
            self._label_to_id[label] = int(m.id)

        self._target_combo["values"] = labels

        current_member = config_store.get_member(self._target_member_id)
        if current_member:
            current_label = next((lab for lab, mid in self._label_to_id.items() if mid == current_member.id), "")
            if current_label:
                self._target_var.set(current_label)

        is_editor_master = self._editor_member.role == config_store.ROLE_MASTER
        self._target_combo.configure(state="readonly" if is_editor_master else "disabled")

        tgt = config_store.get_member(self._target_member_id)
        is_target_secondary = bool(tgt) and tgt.role != config_store.ROLE_MASTER
        self._btn_reset_secondary.configure(state="normal" if is_target_secondary else "disabled")

    def _on_target_changed_from_combo(self) -> None:
        label = self._target_var.get().strip()
        mid = self._label_to_id.get(label)
        if not mid:
            return

        if self._dirty and not messagebox.askyesno(
            "Unsaved changes",
            "You have unsaved changes in the wizard. Switch the editing target anyway?",
            parent=self,
        ):
            self._refresh_choose_member_step()
            return

        if not self._can_edit_target(mid):
            messagebox.showinfo("Restricted", "You do not have permission to edit that member.", parent=self)
            self._refresh_choose_member_step()
            return

        self._target_member_id = int(mid)
        self._load_target_into_vars(self._target_member_id)
        self._refresh_choose_member_step()
        self._log_msg(f"[Wizard] Target member set to id={mid}")

    def _reset_target_to_baseline(self) -> None:
        tgt = config_store.get_member(self._target_member_id)
        if not tgt or tgt.role == config_store.ROLE_MASTER:
            return

        if not messagebox.askyesno(
            "Reset to baseline",
            f"Reset {tgt.name} to household baseline?\n\nThis clears their overrides but keeps their allergies.",
            parent=self,
        ):
            return

        # Your service returns None; we just call it then reload.
        preferences_service.reset_secondary_member_to_household_baseline(self._target_member_id)

        self._dirty = False
        self._load_target_into_vars(self._target_member_id)
        self._refresh_choose_member_step()
        messagebox.showinfo("Reset complete", f"{tgt.name} was reset to baseline (allergies preserved).", parent=self)
        self._log_msg(f"[Wizard] Reset secondary member id={tgt.id} to baseline")

    # ---------- Data load/save ----------

    def _load_target_into_vars(self, member_id: int) -> None:
        member = config_store.get_member(member_id)
        if not member:
            return

        prof = member.profile or {}
        self._dirty = False

        self._eats_meat.set(bool(prof.get("eats_meat", True)))
        self._eats_fish.set(bool(prof.get("eats_fish", True)))
        self._eats_dairy.set(bool(prof.get("eats_dairy", True)))
        self._eats_eggs.set(bool(prof.get("eats_eggs", True)))

        excluded = set(str(x).strip().lower() for x in (prof.get("excluded_proteins", []) or []))
        for p in preferences_service.PROTEINS:
            self._protein_allowed_vars[p].set(p.lower() not in excluded)

        weights = prof.get("preferred_protein_weights", {}) or {}
        for p in preferences_service.PROTEINS:
            try:
                self._protein_weight_vars[p].set(float(weights.get(p.lower(), weights.get(p, 1.0))))
            except Exception:
                self._protein_weight_vars[p].set(1.0)

        # Allergies
        if self._allergies_editor:
            self._allergies_editor.set_values(list(prof.get("allergies", []) or []))

        # Excludes
        if self._hard_editor:
            self._hard_editor.set_values(list(prof.get("hard_excludes", []) or []))
        if self._soft_editor:
            self._soft_editor.set_values(list(prof.get("soft_excludes", []) or []))

            # NEW: if target is secondary, remove any redundant entries (baseline hard excludes) from display
            if member.role != config_store.ROLE_MASTER:
                baseline_hard = self._baseline_hard_excludes_set()
                cleaned = [x for x in self._soft_editor.get_values() if x not in baseline_hard]
                if cleaned != self._soft_editor.get_values():
                    self._soft_editor.set_values(cleaned)

        # Spice + styles
        self._spice_var.set(str(prof.get("spice_level", "medium")).strip().lower() or "medium")

        styles = set(_profile_get_styles(prof))
        for tag in self._style_vars:
            self._style_vars[tag].set(tag in styles)

        # Cuisines
        cuisines = set(str(c).strip().lower() for c in (prof.get("favorite_cuisines", []) or []))
        for c in self._cuisine_vars:
            self._cuisine_vars[c].set(c in cuisines)

        # Oils
        oils_allowed = set(str(o).strip().lower() for o in (prof.get("oils_allowed", []) or []))
        for o in self._oil_vars:
            self._oil_vars[o].set(o in oils_allowed)

        # Role gating for master-only
        is_target_master = member.role == config_store.ROLE_MASTER
        self._set_weights_enabled(is_target_master)
        if self._hard_editor:
            self._hard_editor.set_enabled(is_target_master)

        self._refresh_excludes_step_readonly_list()

    def _collect_profile_from_vars(self) -> Dict[str, Any]:
        prof: Dict[str, Any] = {}

        prof["eats_meat"] = bool(self._eats_meat.get())
        prof["eats_fish"] = bool(self._eats_fish.get())
        prof["eats_dairy"] = bool(self._eats_dairy.get())
        prof["eats_eggs"] = bool(self._eats_eggs.get())

        excluded: List[str] = []
        for p in preferences_service.PROTEINS:
            if not bool(self._protein_allowed_vars[p].get()):
                excluded.append(p.lower())
        prof["excluded_proteins"] = excluded

        weights: Dict[str, float] = {}
        for p in preferences_service.PROTEINS:
            try:
                weights[p.lower()] = float(self._protein_weight_vars[p].get())
            except Exception:
                weights[p.lower()] = 1.0
        prof["preferred_protein_weights"] = weights

        prof["allergies"] = self._allergies_editor.get_values() if self._allergies_editor else []

        prof["hard_excludes"] = self._hard_editor.get_values() if self._hard_editor else []
        prof["soft_excludes"] = self._soft_editor.get_values() if self._soft_editor else []

        prof["spice_level"] = (self._spice_var.get() or "medium").strip().lower()
        prof["styles"] = [tag for tag, var in self._style_vars.items() if bool(var.get())]
        prof["favorite_cuisines"] = [c for c, var in self._cuisine_vars.items() if bool(var.get())]
        prof["oils_allowed"] = [o for o, var in self._oil_vars.items() if bool(var.get())]

        # NEW: if saving a secondary, strip redundant soft excludes that are already baseline hard excludes
        tgt = config_store.get_member(self._target_member_id)
        if tgt and tgt.role != config_store.ROLE_MASTER:
            baseline_hard = self._baseline_hard_excludes_set()
            prof["soft_excludes"] = [x for x in prof["soft_excludes"] if x not in baseline_hard]
            # also: secondary can't set household hard excludes anyway (config_store will sanitize)
            # but leaving it here is fine; config_store moves secondary hard->soft.

        return prof

    # ---------- Excludes step helpers ----------

    def _refresh_excludes_step_readonly_list(self) -> None:
        if not self._household_hard_excludes_lb:
            return

        baseline_hard = sorted(self._baseline_hard_excludes_set())

        self._household_hard_excludes_lb.configure(state="normal")
        self._household_hard_excludes_lb.delete(0, tk.END)
        if not baseline_hard:
            self._household_hard_excludes_lb.insert(tk.END, "(none)")
        else:
            for x in baseline_hard:
                self._household_hard_excludes_lb.insert(tk.END, x)
        self._household_hard_excludes_lb.configure(state="disabled")

        tgt = config_store.get_member(self._target_member_id)
        if self._hard_editor and tgt and tgt.role != config_store.ROLE_MASTER:
            self._hard_editor.set_enabled(False)

    # ---------- Review ----------

    def _render_review_text(self) -> None:
        tgt = config_store.get_member(self._target_member_id)
        name = tgt.name if tgt else f"member #{self._target_member_id}"
        role = tgt.role if tgt else "unknown"

        prof = self._collect_profile_from_vars()

        allergies = prof.get("allergies", [])
        hard = prof.get("hard_excludes", [])
        soft = prof.get("soft_excludes", [])
        cuisines = prof.get("favorite_cuisines", [])
        oils = prof.get("oils_allowed", [])
        styles = prof.get("styles", [])
        spice = prof.get("spice_level", "medium")
        excluded_proteins = set(prof.get("excluded_proteins", []))

        allowed_proteins = [p for p in preferences_service.PROTEINS if p.lower() not in excluded_proteins]

        lines: List[str] = []
        lines.append(f"Editing: {name} ({role})")
        lines.append("")
        lines.append("Diet basics:")
        lines.append(f"  - Eats meat: {bool(prof.get('eats_meat'))}")
        lines.append(f"  - Eats fish/seafood: {bool(prof.get('eats_fish'))}")
        lines.append(f"  - Eats dairy: {bool(prof.get('eats_dairy'))}")
        lines.append(f"  - Eats eggs: {bool(prof.get('eats_eggs'))}")
        lines.append("")
        lines.append("Proteins allowed:")
        lines.append("  - " + (", ".join([p for p in allowed_proteins]) if allowed_proteins else "(none)"))
        lines.append("")
        lines.append("Allergies (household hard excludes):")
        lines.append("  - " + (", ".join(allergies) if allergies else "(none)"))
        lines.append("")
        if role == config_store.ROLE_MASTER:
            lines.append("Hard excludes (never show):")
            lines.append("  - " + (", ".join(hard) if hard else "(none)"))
            lines.append("")
        else:
            lines.append("Hard excludes:")
            lines.append("  - (secondary members do not set household hard excludes)")
            lines.append("")
        lines.append("Soft excludes (still allowed, but flagged/deprioritized):")
        lines.append("  - " + (", ".join(soft) if soft else "(none)"))
        lines.append("")
        lines.append("Oils used:")
        lines.append("  - " + (", ".join(oils) if oils else "(unrestricted)"))
        lines.append("")
        lines.append("Favorite cuisines:")
        lines.append("  - " + (", ".join(cuisines) if cuisines else "(none)"))
        lines.append("")
        lines.append("Meal style + spice:")
        lines.append(f"  - Spice: {spice}")
        lines.append("  - Styles: " + (", ".join(styles) if styles else "(none)"))

        text = "\n".join(lines)

        self._review.configure(state="normal")
        self._review.delete("1.0", tk.END)
        self._review.insert(tk.END, text)
        self._review.configure(state="disabled")

    # ---------- Navigation ----------

    def _back(self) -> None:
        self._show_step(self._step_index - 1)

    def _next(self) -> None:
        if self._step_index < len(self._steps) - 1:
            self._show_step(self._step_index + 1)
            return

        tgt = config_store.get_member(self._target_member_id)
        if not tgt:
            messagebox.showerror("Error", "Missing target member.", parent=self)
            return

        prof = self._collect_profile_from_vars()
        config_store.save_member_profile(tgt.id, prof)
        self._dirty = False

        self._log_msg(f"[Wizard] Saved preferences for {tgt.name} (id={tgt.id})")
        try:
            if self._on_saved:
                self._on_saved(int(tgt.id))
        except Exception:
            pass

        messagebox.showinfo("Saved", f"Preferences saved for {tgt.name}.", parent=self)
        self.destroy()

    def _on_close(self) -> None:
        if self._dirty and not messagebox.askyesno(
            "Unsaved changes",
            "You have unsaved changes in the wizard. Close anyway?",
            parent=self,
        ):
            return
        self.destroy()

    # ---------- Misc ----------

    def _mark_dirty(self) -> None:
        self._dirty = True

    def _mark_dirty_and_sync_proteins(self) -> None:
        self._dirty = True
        self._sync_proteins_from_diet()

    def _prefer_chicken(self) -> None:
        if "chicken" in self._protein_weight_vars:
            self._protein_weight_vars["chicken"].set(1.5)
            self._dirty = True

    def _sync_proteins_from_diet(self) -> None:
        eats_meat = bool(self._eats_meat.get())
        eats_fish = bool(self._eats_fish.get())

        for p in ["chicken", "beef", "pork", "lamb", "turkey"]:
            if p in self._protein_allowed_vars and not eats_meat:
                self._protein_allowed_vars[p].set(False)

        for p in ["fish", "shellfish"]:
            if p in self._protein_allowed_vars and not eats_fish:
                self._protein_allowed_vars[p].set(False)

    def _set_weights_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"

        def walk(w: tk.Widget) -> List[tk.Widget]:
            out = [w]
            for c in w.winfo_children():
                out.extend(walk(c))
            return out

        try:
            for w in walk(self._weights_frame):
                try:
                    if isinstance(w, (ttk.Scale, ttk.Button, ttk.Entry, ttk.Combobox, ttk.Checkbutton)):
                        w.configure(state=state)
                except Exception:
                    pass
        except Exception:
            pass

        try:
            self._btn_prefer_chicken.configure(state=state)
        except Exception:
            pass

    def _log_msg(self, msg: str) -> None:
        if self._log:
            try:
                self._log(msg)
            except Exception:
                pass


def open_preferences_wizard_window(
    master: Optional[tk.Misc] = None,
    *,
    member_id: Optional[int] = None,
    editor_member_id: Optional[int] = None,
    log: Optional[Callable[[str], None]] = None,
    on_saved: Optional[Callable[[int], None]] = None,
) -> PreferencesWizardWindow:
    return PreferencesWizardWindow(
        master,
        initial_member_id=member_id,
        editor_member_id=editor_member_id,
        log=log,
        on_saved=on_saved,
    )


# ----------------------------
# Main Preferences Screen
# ----------------------------

class PreferencesWindow(tk.Toplevel):
    def __init__(self, master: Optional[tk.Misc] = None, *, log: Optional[Callable[[str], None]] = None) -> None:
        super().__init__(master)
        self.title("Household Preferences")
        self.geometry("1040x720")
        self.minsize(960, 640)

        self._log = log
        self._cfg = config_store.load_config()

        self._editing_member_id: Optional[int] = None
        self._dirty = False

        # Vars
        self._eats_meat = tk.BooleanVar(value=True)
        self._eats_fish = tk.BooleanVar(value=True)
        self._eats_dairy = tk.BooleanVar(value=True)
        self._eats_eggs = tk.BooleanVar(value=True)

        self._protein_allowed_vars: Dict[str, tk.BooleanVar] = {
            p: tk.BooleanVar(value=True) for p in preferences_service.PROTEINS
        }
        self._protein_weight_vars: Dict[str, tk.DoubleVar] = {
            p: tk.DoubleVar(value=1.0) for p in preferences_service.PROTEINS
        }

        self._spice_var = tk.StringVar(value="medium")

        self._style_vars: Dict[str, tk.BooleanVar] = {
            tag: tk.BooleanVar(value=False) for tag, _label in preferences_service.STYLE_TAGS
        }
        self._cuisine_vars: Dict[str, tk.BooleanVar] = {
            c: tk.BooleanVar(value=False) for c in preferences_service.CUISINES
        }
        self._oil_vars: Dict[str, tk.BooleanVar] = {
            o: tk.BooleanVar(value=False) for o in preferences_service.OILS
        }

        self._build_ui()
        self._reload_members()
        self._apply_active_member_rules()
        self._select_initial_member()

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        # Top bar
        top = ttk.Frame(root)
        top.pack(fill="x")

        ttk.Label(top, text="Active user:", font=("Segoe UI", 10, "bold")).pack(side="left")
        self.active_combo = ttk.Combobox(top, state="readonly", width=26)
        self.active_combo.pack(side="left", padx=(8, 10))
        self.active_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_active_user_changed())

        ttk.Button(top, text="Run 8-Step Wizard", command=self._run_wizard_for_selected).pack(side="left", padx=(10, 0))

        self.btn_reset_to_baseline = ttk.Button(top, text="Reset to baseline", command=self._reset_selected_secondary_to_baseline)
        self.btn_reset_to_baseline.pack(side="left", padx=(10, 0))

        ttk.Button(top, text="Save Changes", command=self._save_current).pack(side="right")
        ttk.Button(top, text="Close", command=self._on_close).pack(side="right", padx=(0, 10))

        # Split layout
        body = ttk.Frame(root)
        body.pack(fill="both", expand=True, pady=(10, 0))
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        left = ttk.Frame(body)
        left.grid(row=0, column=0, sticky="ns", padx=(0, 12))
        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="nsew")

        ttk.Label(left, text="Household Members", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.members_listbox = tk.Listbox(left, height=16, width=28)
        self.members_listbox.pack(fill="y", expand=False, pady=(6, 0))
        self.members_listbox.bind("<<ListboxSelect>>", lambda _e: self._on_member_selected())

        member_btns = ttk.Frame(left)
        member_btns.pack(fill="x", pady=(8, 0))

        self.btn_add_member = ttk.Button(member_btns, text="Add", command=self._add_member)
        self.btn_rename_member = ttk.Button(member_btns, text="Rename", command=self._rename_member)
        self.btn_delete_member = ttk.Button(member_btns, text="Delete", command=self._delete_member)
        self.btn_set_primary = ttk.Button(member_btns, text="Set Primary", command=self._set_primary)

        self.btn_add_member.pack(side="left")
        self.btn_rename_member.pack(side="left", padx=(6, 0))
        self.btn_delete_member.pack(side="left", padx=(6, 0))
        self.btn_set_primary.pack(side="left", padx=(6, 0))

        self.tabs = ttk.Notebook(right)
        self.tabs.pack(fill="both", expand=True)

        self._build_tab_diet_proteins()
        self._build_tab_excludes()
        self._build_tab_cuisines_styles()
        self._build_tab_oils()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_tab_diet_proteins(self) -> None:
        tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(tab, text="Diet & Proteins")

        diet = ttk.LabelFrame(tab, text="Diet baseline", padding=10)
        diet.pack(fill="x")

        ttk.Checkbutton(diet, text="Eats meat", variable=self._eats_meat, command=self._mark_dirty_and_sync_proteins).grid(row=0, column=0, sticky="w", padx=(0, 14))
        ttk.Checkbutton(diet, text="Eats fish/seafood", variable=self._eats_fish, command=self._mark_dirty_and_sync_proteins).grid(row=0, column=1, sticky="w", padx=(0, 14))
        ttk.Checkbutton(diet, text="Eats dairy", variable=self._eats_dairy, command=self._mark_dirty).grid(row=1, column=0, sticky="w", padx=(0, 14), pady=(6, 0))
        ttk.Checkbutton(diet, text="Eats eggs", variable=self._eats_eggs, command=self._mark_dirty).grid(row=1, column=1, sticky="w", padx=(0, 14), pady=(6, 0))

        proteins = ttk.LabelFrame(tab, text="Proteins (uncheck to exclude)", padding=10)
        proteins.pack(fill="both", expand=True, pady=(10, 0))

        sf = _ScrollableFrame(proteins, height=240)
        sf.pack(fill="both", expand=True)

        for i, p in enumerate(preferences_service.PROTEINS):
            r = i // 2
            c = i % 2
            ttk.Checkbutton(
                sf.inner,
                text=p.title(),
                variable=self._protein_allowed_vars[p],
                command=self._mark_dirty,
            ).grid(row=r, column=c, sticky="w", padx=(0, 20), pady=2)

        self._weights_frame = ttk.LabelFrame(tab, text="Master protein preferences (weights)", padding=10)
        self._weights_frame.pack(fill="x", pady=(10, 0))

        grid = ttk.Frame(self._weights_frame)
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)

        for i, p in enumerate(preferences_service.PROTEINS):
            ttk.Label(grid, text=p.title(), width=14).grid(row=i, column=0, sticky="w", pady=2)
            sc = ttk.Scale(
                grid,
                from_=0.5,
                to=2.0,
                variable=self._protein_weight_vars[p],
                command=lambda _v, _p=p: self._mark_dirty(),
            )
            sc.grid(row=i, column=1, sticky="ew", padx=(8, 8), pady=2)

        ttk.Button(self._weights_frame, text="Quick: Prefer chicken", command=self._prefer_chicken).pack(anchor="w", pady=(8, 0))

    def _build_tab_excludes(self) -> None:
        tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(tab, text="Allergies & Excludes")

        row = ttk.Frame(tab)
        row.pack(fill="both", expand=True)
        row.columnconfigure(0, weight=1)
        row.columnconfigure(1, weight=1)

        self.allergies_editor = _ListEditor(
            row,
            title="Allergies (ALWAYS hard exclude for the whole household)",
            hint="Example: peanut, shellfish. Any member allergy will block suggestions/deals.",
            on_changed=self._mark_dirty,
            height=7,
        )
        self.allergies_editor.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        self.hard_editor = _ListEditor(
            row,
            title="Hard excludes (Master only)",
            hint="Never recommend these ingredients (e.g., real olives).",
            on_changed=self._mark_dirty,
            height=7,
        )
        self.hard_editor.grid(row=0, column=1, sticky="nsew")

        self.soft_editor = _ListEditor(
    	    tab,
    	    title="Soft excludes (still allowed, but deprioritized; secondary excludes will show with '*')",
    	    hint="Example: tomatoes (if one child dislikes it).",
    	    on_changed=self._mark_dirty,
    	    height=8,
    	    validate_add=self._validate_secondary_soft_exclude_add,  # NEW
	)

        self.soft_editor.pack(fill="both", expand=True, pady=(12, 0))

    def _build_tab_cuisines_styles(self) -> None:
        tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(tab, text="Cuisines & Style")

        top = ttk.Frame(tab)
        top.pack(fill="x")

        ttk.Label(top, text="Spice preference:").pack(side="left")
        self.spice_combo = ttk.Combobox(top, state="readonly", width=12, values=["low", "medium", "high"], textvariable=self._spice_var)
        self.spice_combo.pack(side="left", padx=(8, 14))
        self.spice_combo.bind("<<ComboboxSelected>>", lambda _e: self._mark_dirty())

        styles = ttk.LabelFrame(tab, text="Cooking styles", padding=10)
        styles.pack(fill="x", pady=(10, 0))

        for i, (tag, label) in enumerate(preferences_service.STYLE_TAGS):
            ttk.Checkbutton(styles, text=label, variable=self._style_vars[tag], command=self._mark_dirty).grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 20), pady=2)

        cuisines = ttk.LabelFrame(tab, text="Favorite cuisines", padding=10)
        cuisines.pack(fill="both", expand=True, pady=(10, 0))

        sf = _ScrollableFrame(cuisines, height=260)
        sf.pack(fill="both", expand=True)

        for i, c in enumerate(preferences_service.CUISINES):
            r = i // 2
            col = i % 2
            ttk.Checkbutton(sf.inner, text=c.title(), variable=self._cuisine_vars[c], command=self._mark_dirty).grid(row=r, column=col, sticky="w", padx=(0, 20), pady=2)

    def _build_tab_oils(self) -> None:
        tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(tab, text="Oils")

        ttk.Label(
            tab,
            text="Select oils you use at home. If none are selected, the app treats oils as unrestricted.",
            foreground="#888",
        ).pack(anchor="w", pady=(0, 8))

        oils = ttk.LabelFrame(tab, text="Allowed oils", padding=10)
        oils.pack(fill="both", expand=True)

        sf = _ScrollableFrame(oils, height=340)
        sf.pack(fill="both", expand=True)

        for i, o in enumerate(preferences_service.OILS):
            r = i // 2
            c = i % 2
            ttk.Checkbutton(sf.inner, text=o.title(), variable=self._oil_vars[o], command=self._mark_dirty).grid(row=r, column=c, sticky="w", padx=(0, 20), pady=2)

    # ---------------- helpers ----------------

    def _mark_dirty(self) -> None:
        self._dirty = True

    def _mark_dirty_and_sync_proteins(self) -> None:
        self._dirty = True
        self._sync_proteins_from_diet()

    def _prefer_chicken(self) -> None:
        if "chicken" in self._protein_weight_vars:
            self._protein_weight_vars["chicken"].set(1.5)
            self._mark_dirty()

    def _sync_proteins_from_diet(self) -> None:
        eats_meat = bool(self._eats_meat.get())
        eats_fish = bool(self._eats_fish.get())

        for p in ["chicken", "beef", "pork", "lamb", "turkey"]:
            if p in self._protein_allowed_vars and not eats_meat:
                self._protein_allowed_vars[p].set(False)

        for p in ["fish", "shellfish"]:
            if p in self._protein_allowed_vars and not eats_fish:
                self._protein_allowed_vars[p].set(False)

    def _reload_members(self) -> None:
        self._cfg = config_store.load_config()
        self._members = self._cfg.household.members

        self._id_by_label: Dict[str, int] = {}
        labels: List[str] = []
        primary_id = self._cfg.household.primary_member_id

        for m in self._members:
            tag = " (Master)" if m.role == config_store.ROLE_MASTER else ""
            pri = " ★" if m.id == primary_id else ""
            label = f"{m.name}{tag}{pri}"
            labels.append(label)
            self._id_by_label[label] = m.id

        self.active_combo["values"] = labels
        active_id = self._cfg.household.active_member_id
        active_label = next((lab for lab, mid in self._id_by_label.items() if mid == active_id), labels[0] if labels else "")
        self.active_combo.set(active_label)

        self.members_listbox.delete(0, tk.END)
        for m in self._members:
            tag = "👑 " if m.role == config_store.ROLE_MASTER else "   "
            pri = "★ " if m.id == primary_id else "  "
            self.members_listbox.insert(tk.END, f"{tag}{pri}{m.name}")

        self._update_reset_button_state()

    def _apply_active_member_rules(self) -> None:
        active = config_store.get_active_member()
        is_master = active.role == config_store.ROLE_MASTER
        for btn in (self.btn_add_member, self.btn_rename_member, self.btn_delete_member, self.btn_set_primary):
            btn.configure(state="normal" if is_master else "disabled")

    def _select_initial_member(self) -> None:
        active = config_store.get_active_member()
        target_id = self._cfg.household.primary_member_id if active.role == config_store.ROLE_MASTER else active.id

        idx = 0
        for i, m in enumerate(self._members):
            if m.id == target_id:
                idx = i
                break
        self.members_listbox.selection_clear(0, tk.END)
        self.members_listbox.selection_set(idx)
        self.members_listbox.activate(idx)
        self._load_member_into_form(self._members[idx].id)

    def _current_member_index(self) -> int:
        if self._editing_member_id is None:
            return 0
        for i, m in enumerate(self._members):
            if m.id == self._editing_member_id:
                return i
        return 0

    def _on_active_user_changed(self) -> None:
        label = self.active_combo.get()
        mid = self._id_by_label.get(label)
        if not mid:
            return

        if self._dirty and not messagebox.askyesno("Unsaved changes", "You have unsaved changes. Switch active user anyway?", parent=self):
            self._reload_members()
            return

        config_store.set_active_member_id(mid)
        self._reload_members()
        self._apply_active_member_rules()
        self._select_initial_member()
        self._log_msg(f"Active user set to member_id={mid}")

    def _on_member_selected(self) -> None:
        sel = self.members_listbox.curselection()
        if not sel:
            return
        idx = int(sel[0])

        active = config_store.get_active_member()
        is_master = active.role == config_store.ROLE_MASTER
        target = self._members[idx]

        if not is_master and target.id != active.id:
            messagebox.showinfo("Restricted", "Secondary users can only edit their own preferences.", parent=self)
            self._select_initial_member()
            return

        if self._dirty and not messagebox.askyesno("Unsaved changes", "You have unsaved changes. Switch member anyway?", parent=self):
            self.members_listbox.selection_clear(0, tk.END)
            cur_idx = self._current_member_index()
            self.members_listbox.selection_set(cur_idx)
            self.members_listbox.activate(cur_idx)
            return

        self._load_member_into_form(target.id)

    def _load_member_into_form(self, member_id: int) -> None:
        member = config_store.get_member(member_id)
        if not member:
            return

        self._editing_member_id = member_id
        prof = member.profile or {}
        self._dirty = False

        self._eats_meat.set(bool(prof.get("eats_meat", True)))
        self._eats_fish.set(bool(prof.get("eats_fish", True)))
        self._eats_dairy.set(bool(prof.get("eats_dairy", True)))
        self._eats_eggs.set(bool(prof.get("eats_eggs", True)))

        excluded = set(str(x).strip().lower() for x in (prof.get("excluded_proteins", []) or []))
        for p in preferences_service.PROTEINS:
            self._protein_allowed_vars[p].set(p.lower() not in excluded)

        weights = prof.get("preferred_protein_weights", {}) or {}
        for p in preferences_service.PROTEINS:
            try:
                self._protein_weight_vars[p].set(float(weights.get(p.lower(), weights.get(p, 1.0))))
            except Exception:
                self._protein_weight_vars[p].set(1.0)

        self.allergies_editor.set_values(list(prof.get("allergies", []) or []))
        self.hard_editor.set_values(list(prof.get("hard_excludes", []) or []))

        self.soft_editor.set_values(list(prof.get("soft_excludes", []) or []))
	# NEW: If editing a secondary, strip redundant soft excludes that are already household hard excludes
	if member.role != config_store.ROLE_MASTER:
    	    baseline_hard = self._baseline_hard_excludes_set()
    	    cleaned = [x for x in self.soft_editor.get_values() if x not in baseline_hard]
    	    if cleaned != self.soft_editor.get_values():
        	self.soft_editor.set_values(cleaned)

        self._spice_var.set(str(prof.get("spice_level", "medium")).strip().lower() or "medium")

        styles = set(_profile_get_styles(prof))
        for tag in self._style_vars:
            self._style_vars[tag].set(tag in styles)

        cuisines = set(str(c).strip().lower() for c in (prof.get("favorite_cuisines", []) or []))
        for c in self._cuisine_vars:
            self._cuisine_vars[c].set(c in cuisines)

        oils_allowed = set(str(o).strip().lower() for o in (prof.get("oils_allowed", []) or []))
        for o in self._oil_vars:
            self._oil_vars[o].set(o in oils_allowed)

        is_member_master = member.role == config_store.ROLE_MASTER
        self.hard_editor.set_enabled(is_member_master)
        self._set_weights_enabled(is_member_master)
        self._update_reset_button_state()

    def _set_weights_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"

        def walk(w: tk.Widget) -> List[tk.Widget]:
            out = [w]
            for c in w.winfo_children():
                out.extend(walk(c))
            return out

        for w in walk(self._weights_frame):
            try:
                if isinstance(w, (ttk.Scale, ttk.Button, ttk.Entry, ttk.Combobox, ttk.Checkbutton)):
                    w.configure(state=state)
            except Exception:
                pass

    def _update_reset_button_state(self) -> None:
        if self._editing_member_id is None:
            self.btn_reset_to_baseline.configure(state="disabled")
            return
        m = config_store.get_member(self._editing_member_id)
        if not m:
            self.btn_reset_to_baseline.configure(state="disabled")
            return
        self.btn_reset_to_baseline.configure(state="normal" if m.role != config_store.ROLE_MASTER else "disabled")

    def _reset_selected_secondary_to_baseline(self) -> None:
        if self._editing_member_id is None:
            return
        m = config_store.get_member(self._editing_member_id)
        if not m or m.role == config_store.ROLE_MASTER:
            return

        if not messagebox.askyesno(
            "Reset to baseline",
            f"Reset {m.name} to household baseline?\n\nThis clears their overrides but keeps their allergies.",
            parent=self,
        ):
            return

        preferences_service.reset_secondary_member_to_household_baseline(m.id)

        self._dirty = False
        self._reload_members()
        self._apply_active_member_rules()
        self._load_member_into_form(m.id)
        self._log_msg(f"Reset member {m.name} (id={m.id}) to household baseline")

    def _collect_form_profile(self) -> Dict[str, Any]:
        prof: Dict[str, Any] = {}

        prof["eats_meat"] = bool(self._eats_meat.get())
        prof["eats_fish"] = bool(self._eats_fish.get())
        prof["eats_dairy"] = bool(self._eats_dairy.get())
        prof["eats_eggs"] = bool(self._eats_eggs.get())

        excluded: List[str] = []
        for p in preferences_service.PROTEINS:
            if not bool(self._protein_allowed_vars[p].get()):
                excluded.append(p.lower())
        prof["excluded_proteins"] = excluded

        weights: Dict[str, float] = {}
        for p in preferences_service.PROTEINS:
            try:
                weights[p.lower()] = float(self._protein_weight_vars[p].get())
            except Exception:
                weights[p.lower()] = 1.0
        prof["preferred_protein_weights"] = weights

        prof["allergies"] = self.allergies_editor.get_values()
        prof["hard_excludes"] = self.hard_editor.get_values()
        prof["soft_excludes"] = self.soft_editor.get_values()

        prof["spice_level"] = (self._spice_var.get() or "medium").strip().lower()
        prof["styles"] = [tag for tag, var in self._style_vars.items() if bool(var.get())]
        prof["favorite_cuisines"] = [c for c, var in self._cuisine_vars.items() if bool(var.get())]
        prof["oils_allowed"] = [o for o, var in self._oil_vars.items() if bool(var.get())]

        return prof

    def _save_current(self) -> None:
        if self._editing_member_id is None:
            return
        member = config_store.get_member(self._editing_member_id)
        if not member:
            return

        prof = self._collect_form_profile()
	# NEW: Enforce redundancy rule on save too
	if member.role != config_store.ROLE_MASTER:
    	    baseline_hard = self._baseline_hard_excludes_set()
    	    prof["soft_excludes"] = [x for x in (prof.get("soft_excludes", []) or []) if x not in baseline_hard]
        config_store.save_member_profile(member.id, prof)

        self._dirty = False
        self._reload_members()
        self._apply_active_member_rules()
        self._log_msg(f"Saved preferences for {member.name} (id={member.id})")

    def _run_wizard_for_selected(self) -> None:
        active = config_store.get_active_member()
        target_id = self._editing_member_id or self._cfg.household.primary_member_id

        def _after_save(saved_member_id: int) -> None:
            self._reload_members()
            self._apply_active_member_rules()
            self._load_member_into_form(saved_member_id)

        open_preferences_wizard_window(
            self,
            member_id=target_id,
            editor_member_id=active.id,
            log=self._log,
            on_saved=_after_save,
        )

    def _add_member(self) -> None:
        name = simpledialog.askstring("Add member", "Member name:", parent=self)
        if not name:
            return
        config_store.add_member(name=name, role=config_store.ROLE_SECONDARY)
        self._reload_members()
        self._log_msg(f"Added member: {name}")

    def _rename_member(self) -> None:
        if self._editing_member_id is None:
            return
        m = config_store.get_member(self._editing_member_id)
        if not m:
            return
        name = simpledialog.askstring("Rename member", "New name:", initialvalue=m.name, parent=self)
        if not name:
            return
        config_store.rename_member(m.id, name)
        self._reload_members()
        self._log_msg(f"Renamed member id={m.id} to {name}")

    def _delete_member(self) -> None:
        if self._editing_member_id is None:
            return
        m = config_store.get_member(self._editing_member_id)
        if not m:
            return
        if not messagebox.askyesno("Delete member", f"Delete {m.name}? This cannot be undone.", parent=self):
            return
        ok = config_store.delete_member(m.id)
        if not ok:
            messagebox.showwarning("Not allowed", "Cannot delete the last remaining member.", parent=self)
            return
        self._reload_members()
        self._apply_active_member_rules()
        self._select_initial_member()
        self._log_msg(f"Deleted member: {m.name}")

    def _set_primary(self) -> None:
        if self._editing_member_id is None:
            return
        config_store.set_primary_member_id(self._editing_member_id)
        self._reload_members()
        self._log_msg(f"Set primary member_id={self._editing_member_id}")

    def _on_close(self) -> None:
        if self._dirty and not messagebox.askyesno("Unsaved changes", "You have unsaved changes. Close anyway?", parent=self):
            return
        self.destroy()

    def _log_msg(self, msg: str) -> None:
        if self._log:
            try:
                self._log(msg)
            except Exception:
                pass
    def _baseline_hard_excludes_set(self) -> Set[str]:
        """
        Household baseline hard excludes = master hard_excludes.
        Used to block redundant secondary soft excludes.
        """
        baseline = preferences_service.get_household_baseline_profile()
        raw = baseline.get("hard_excludes", []) or []
        out: Set[str] = set()
        for x in raw:
            s = str(x).strip().lower()
            if s:
                out.add(s)
        return out


    def _validate_secondary_soft_exclude_add(self, value: str) -> Optional[str]:
        """
        Main screen: when editing a secondary member, prevent adding a soft exclude
        that is already a household hard exclude (baseline).
        """
        if self._editing_member_id is None:
            return None

        member = config_store.get_member(self._editing_member_id)
        if not member:
            return None

        # Only enforce for SECONDARY targets
        if member.role == config_store.ROLE_MASTER:
            return None

        baseline_hard = self._baseline_hard_excludes_set()
        if (value or "").strip().lower() in baseline_hard:
            return f"'{value}' is already a household hard exclude (baseline). Adding it here is redundant."
        return None



def open_preferences_window(master: Optional[tk.Misc] = None, *, log: Optional[Callable[[str], None]] = None) -> PreferencesWindow:
    return PreferencesWindow(master, log=log)
