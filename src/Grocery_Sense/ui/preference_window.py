from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from typing import Any, Callable, Dict, List, Optional, Tuple, Set

from Grocery_Sense import config_store
from Grocery_Sense.services import preferences_service


def _norm_token(v: Any) -> str:
    return str(v or "").strip().lower()


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
        validate_add: Optional[Callable[[str], Tuple[bool, str]]] = None,
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
        self._enabled = True

    def set_validate_add(self, validate_add: Optional[Callable[[str], Tuple[bool, str]]]) -> None:
        self._validate_add = validate_add

    def set_values(self, values: List[str]) -> None:
        self._values = self._normalize(values)
        self._render()

    def get_values(self) -> List[str]:
        return list(self._values)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        state = "normal" if self._enabled else "disabled"
        self.listbox.configure(state=state)
        self._btn_add.configure(state=state)
        self._btn_remove.configure(state=state)

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
            s = _norm_token(v)
            if s and s not in out:
                out.append(s)
        return sorted(out)

    def _add(self) -> None:
        if not self._enabled:
            return
        raw = simpledialog.askstring("Add", "Enter a value (e.g., 'olives'):", parent=self)
        if not raw:
            return
        s = _norm_token(raw)
        if not s:
            return
        if s in self._values:
            messagebox.showinfo("Duplicate", f"'{s}' is already in the list.", parent=self)
            return

        if self._validate_add:
            ok, reason = self._validate_add(s)
            if not ok:
                messagebox.showwarning("Not allowed", reason or "That entry is not allowed.", parent=self)
                return

        self._values.append(s)
        self._values.sort()
        self._render()
        self._on_changed()

    def _remove(self) -> None:
        if not self._enabled:
            return
        sel = self.listbox.curselection()
        if not sel:
            return
        idx = int(sel[0])
        if 0 <= idx < len(self._values):
            self._values.pop(idx)
            self._render()
            self._on_changed()


class PreferencesWindow(tk.Toplevel):
    def __init__(self, master: Optional[tk.Misc] = None, *, log: Optional[Callable[[str], None]] = None) -> None:
        super().__init__(master)
        self.title("Household Preferences")
        self.geometry("1040x740")
        self.minsize(960, 660)

        self._log = log
        self._cfg = config_store.load_config()

        self._editing_member_id: Optional[int] = None
        self._dirty = False

        # Cached household hard excludes for duplicate prevention
        self._household_hard_excludes: Set[str] = set()

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

        ttk.Button(top, text="Run Wizard", command=self._run_wizard_for_selected).pack(side="left", padx=(10, 0))
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
            grid.columnconfigure(1, weight=1)

        ttk.Button(self._weights_frame, text="Quick: Prefer chicken", command=self._prefer_chicken).pack(anchor="w", pady=(8, 0))

    def _build_tab_excludes(self) -> None:
        tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(tab, text="Allergies & Excludes")

        # Household hard excludes display (read-only)
        hard_box = ttk.LabelFrame(tab, text="Household hard excludes (Master + any allergies)", padding=10)
        hard_box.pack(fill="x")

        row = ttk.Frame(hard_box)
        row.pack(fill="x")

        self._household_hard_listbox = tk.Listbox(row, height=5)
        vsb = ttk.Scrollbar(row, orient="vertical", command=self._household_hard_listbox.yview)
        self._household_hard_listbox.configure(yscrollcommand=vsb.set)
        self._household_hard_listbox.pack(side="left", fill="x", expand=True)
        vsb.pack(side="right", fill="y")

        self._household_hard_hint = ttk.Label(
            tab,
            text="Secondary members cannot add these to Soft excludes (redundant).",
            foreground="#888",
        )
        self._household_hard_hint.pack(anchor="w", pady=(6, 10))

        row2 = ttk.Frame(tab)
        row2.pack(fill="both", expand=True)
        row2.columnconfigure(0, weight=1)
        row2.columnconfigure(1, weight=1)

        self.allergies_editor = _ListEditor(
            row2,
            title="Allergies (ALWAYS hard exclude for the whole household)",
            hint="Example: peanut, shellfish. Any member allergy will block suggestions/deals.",
            on_changed=self._mark_dirty,
            height=7,
        )
        self.allergies_editor.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        self.hard_editor = _ListEditor(
            row2,
            title="Hard excludes (Master only)",
            hint="Never recommend these ingredients (e.g., real olives).",
            on_changed=self._mark_dirty,
            height=7,
        )
        self.hard_editor.grid(row=0, column=1, sticky="nsew")

        # Soft excludes editor gets validate_add wired dynamically in _load_member_into_form
        self.soft_editor = _ListEditor(
            tab,
            title="Soft excludes (still allowed, but deprioritized; secondary excludes will show with '*')",
            hint="Example: tomatoes (if one child dislikes it).",
            on_changed=self._mark_dirty,
            height=8,
        )
        self.soft_editor.pack(fill="both", expand=True, pady=(12, 0))

        self._secondary_soft_rule_label = ttk.Label(tab, text="", foreground="#888")
        self._secondary_soft_rule_label.pack(anchor="w", pady=(8, 0))

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
            return

        self._load_member_into_form(target.id)

    def _refresh_household_hard_display(self) -> None:
        """
        Uses compute_effective_preferences().hard_excludes as the canonical household hard list.
        """
        self._household_hard_excludes = set()
        try:
            eff = preferences_service.compute_effective_preferences()
            for x in getattr(eff, "hard_excludes", set()) or set():
                s = _norm_token(x)
                if s:
                    self._household_hard_excludes.add(s)
        except Exception:
            # Safe fallback: show empty list if service not available
            self._household_hard_excludes = set()

        self._household_hard_listbox.delete(0, tk.END)
        for v in sorted(self._household_hard_excludes):
            self._household_hard_listbox.insert(tk.END, v)

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

        excluded = set(_norm_token(x) for x in (prof.get("excluded_proteins", []) or []))
        for p in preferences_service.PROTEINS:
            self._protein_allowed_vars[p].set(_norm_token(p) not in excluded)

        weights = prof.get("preferred_protein_weights", {}) or {}
        if not isinstance(weights, dict):
            weights = {}
        for p in preferences_service.PROTEINS:
            key = _norm_token(p)
            try:
                self._protein_weight_vars[p].set(float(weights.get(key, 1.0)))
            except Exception:
                self._protein_weight_vars[p].set(1.0)

        self.allergies_editor.set_values(list(prof.get("allergies", []) or []))
        self.hard_editor.set_values(list(prof.get("hard_excludes", []) or []))
        self.soft_editor.set_values(list(prof.get("soft_excludes", []) or []))

        self._spice_var.set(_norm_token(prof.get("spice_level", "medium")) or "medium")

        styles_raw = prof.get("styles", None)
        if styles_raw is None:
            styles_raw = prof.get("meal_styles", []) or []
        styles = set(_norm_token(s) for s in (styles_raw or []))
        for tag in self._style_vars:
            self._style_vars[tag].set(tag in styles)

        cuisines = set(_norm_token(c) for c in (prof.get("favorite_cuisines", []) or []))
        for c in self._cuisine_vars:
            self._cuisine_vars[c].set(c in cuisines)

        oils_allowed = set(_norm_token(o) for o in (prof.get("oils_allowed", []) or []))
        for o in self._oil_vars:
            self._oil_vars[o].set(o in oils_allowed)

        # Refresh household hard excludes display
        self._refresh_household_hard_display()

        is_member_master = member.role == config_store.ROLE_MASTER
        self.hard_editor.set_enabled(is_member_master)

        # Disable weights if not master
        for w in self._weights_frame.winfo_children():
            for child in w.winfo_children():
                try:
                    child.configure(state="normal" if is_member_master else "disabled")
                except Exception:
                    pass

        # Wire soft-exclude duplicate prevention for secondary:
        # secondary cannot add something already hard-excluded household-wide
        if not is_member_master:
            def validate_add_soft(token: str) -> Tuple[bool, str]:
                if token in self._household_hard_excludes:
                    return False, f"'{token}' is already hard-excluded household-wide (Master/allergies). Adding it to Soft excludes is redundant."
                return True, ""
            self.soft_editor.set_validate_add(validate_add_soft)
            self._secondary_soft_rule_label.configure(
                text="Secondary rule: Soft excludes cannot include household hard excludes."
            )
        else:
            self.soft_editor.set_validate_add(None)
            self._secondary_soft_rule_label.configure(text="")

    def _collect_form_profile(self) -> Dict[str, Any]:
        prof: Dict[str, Any] = {}

        prof["eats_meat"] = bool(self._eats_meat.get())
        prof["eats_fish"] = bool(self._eats_fish.get())
        prof["eats_dairy"] = bool(self._eats_dairy.get())
        prof["eats_eggs"] = bool(self._eats_eggs.get())

        excluded: List[str] = []
        for p in preferences_service.PROTEINS:
            if not bool(self._protein_allowed_vars[p].get()):
                excluded.append(_norm_token(p))
        prof["excluded_proteins"] = excluded

        weights: Dict[str, float] = {}
        for p in preferences_service.PROTEINS:
            key = _norm_token(p)
            try:
                weights[key] = float(self._protein_weight_vars[p].get())
            except Exception:
                weights[key] = 1.0
        prof["preferred_protein_weights"] = weights

        prof["allergies"] = self.allergies_editor.get_values()
        prof["hard_excludes"] = self.hard_editor.get_values()
        prof["soft_excludes"] = self.soft_editor.get_values()

        prof["spice_level"] = (_norm_token(self._spice_var.get()) or "medium")

        styles = [tag for tag, var in self._style_vars.items() if bool(var.get())]
        prof["styles"] = styles
        prof["meal_styles"] = styles  # compat

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
        config_store.save_member_profile(member.id, prof)

        self._dirty = False
        self._reload_members()
        self._apply_active_member_rules()
        self._log_msg(f"Saved preferences for {member.name} (id={member.id})")

    def _run_wizard_for_selected(self) -> None:
        if self._editing_member_id is None:
            return

        from Grocery_Sense.ui.preferences_wizard_window import open_preferences_wizard_window

        open_preferences_wizard_window(self, member_id=self._editing_member_id, log=self._log)

        # reload after wizard saves
        self.after(300, self._reload_members)
        self.after(350, lambda: self._load_member_into_form(self._editing_member_id or self._cfg.household.primary_member_id))

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


def open_preferences_window(master: Optional[tk.Misc] = None, *, log: Optional[Callable[[str], None]] = None) -> PreferencesWindow:
    return PreferencesWindow(master, log=log)
