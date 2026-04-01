from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from Grocery_Sense import config_store
from Grocery_Sense.services import preferences_service


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

class _ScrollableFrame(ttk.Frame):
    def __init__(self, master: tk.Misc, *, height: int = 240) -> None:
        super().__init__(master)
        self._canvas = tk.Canvas(self, highlightthickness=0, height=height)
        self._vsb = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self._inner = ttk.Frame(self._canvas)

        self._inner.bind("<Configure>", lambda _e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._window_id = self._canvas.create_window((0, 0), window=self._inner, anchor="nw")
        self._canvas.configure(yscrollcommand=self._vsb.set)

        self._canvas.pack(side="left", fill="both", expand=True)
        self._vsb.pack(side="right", fill="y")
        self._canvas.bind("<Configure>", self._on_canvas)

    def _on_canvas(self, evt) -> None:
        try:
            self._canvas.itemconfigure(self._window_id, width=evt.width)
        except Exception:
            pass

    @property
    def inner(self) -> ttk.Frame:
        return self._inner


def _norm_token(v: Any) -> str:
    return str(v or "").strip().lower()


def _norm_list(values: Any) -> List[str]:
    if not values:
        return []
    if isinstance(values, str):
        values = [values]
    out: List[str] = []
    for v in list(values):
        s = _norm_token(v)
        if s and s not in out:
            out.append(s)
    return out


class _ValidatedList(ttk.Frame):
    """
    Simple list editor for wizard steps with optional validate_add hook.

    validate_add: Callable[[str], Tuple[bool, str]]
      - input: token (already normalized)
      - output: (ok, reason_if_not_ok)
    """
    def __init__(
        self,
        master: tk.Misc,
        *,
        title: str,
        hint: str = "",
        height: int = 7,
        validate_add: Optional[Callable[[str], Tuple[bool, str]]] = None,
    ) -> None:
        super().__init__(master)
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

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        state = "normal" if self._enabled else "disabled"
        self.listbox.configure(state=state)
        self._btn_add.configure(state=state)
        self._btn_remove.configure(state=state)

    def set_values(self, values: List[str]) -> None:
        self._values = _norm_list(values)
        self._values.sort()
        self._render()

    def get_values(self) -> List[str]:
        return list(self._values)

    def _render(self) -> None:
        self.listbox.delete(0, tk.END)
        for v in self._values:
            self.listbox.insert(tk.END, v)

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


class _ReadOnlyList(ttk.Frame):
    def __init__(self, master: tk.Misc, *, title: str, hint: str = "", height: int = 6) -> None:
        super().__init__(master)
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

    def set_values(self, values: List[str]) -> None:
        self.listbox.delete(0, tk.END)
        for v in sorted(_norm_list(values)):
            self.listbox.insert(tk.END, v)


# ---------------------------------------------------------------------------
# Wizard
# ---------------------------------------------------------------------------

class PreferencesWizardWindow(tk.Toplevel):
    """
    Preferences Wizard — 8 steps (guided)

    Step 1: Choose member (role rules enforced)
    Step 2: Household baseline / Reset to baseline (secondary only)
    Step 3: Diet basics (yes/no toggles)
    Step 4: Proteins (+ weights if master, inline)
    Step 5: Allergies (always hard household-level)
    Step 6: Excludes (show household hard excludes; prevent redundant soft excludes for secondary)
    Step 7: Oils
    Step 8: Cuisines + spice + styles
    """

    def __init__(
        self,
        master: Optional[tk.Misc] = None,
        *,
        member_id: Optional[int] = None,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        super().__init__(master)
        self.title("Preferences Wizard")
        self.geometry("860x700")
        self.minsize(780, 620)

        self._log = log
        self._cfg = config_store.load_config()

        self._active_user = config_store.get_active_member()
        self._active_is_master = self._active_user.role == config_store.ROLE_MASTER

        # Enforce role rule: secondary can only edit themselves, even if member_id passed.
        if not self._active_is_master:
            self._member_id: Optional[int] = self._active_user.id
        else:
            self._member_id = member_id

        self._member_var = tk.StringVar(value="")
        self._member_label_to_id: Dict[str, int] = {}

        self._editing_member_role: str = config_store.ROLE_SECONDARY
        self._editing_member_name: str = "Member"

        # Shared state (loaded per member)
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

        # Lists
        self._allergies_list: Optional[_ValidatedList] = None
        self._hard_list: Optional[_ValidatedList] = None
        self._soft_list: Optional[_ValidatedList] = None
        self._household_hard_ro: Optional[_ReadOnlyList] = None

        # Household hard excludes cache (for duplicate prevention)
        self._household_hard_excludes: Set[str] = set()

        # Steps
        self._step = 0
        self._steps: List[ttk.Frame] = []

        # UI
        self._build_ui()

        # Load member state (if preset)
        if self._member_id:
            self._load_member_into_state(self._member_id)

        self._show_step(0)

    # -----------------------------------------------------------------------
    # UI build
    # -----------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=10)
        outer.pack(fill="both", expand=True)

        self._header = ttk.Label(outer, text="Preferences Wizard", font=("Segoe UI", 12, "bold"))
        self._header.pack(anchor="w")

        self._sub = ttk.Label(outer, text="", foreground="#888")
        self._sub.pack(anchor="w", pady=(0, 8))

        self._content = ttk.Frame(outer)
        self._content.pack(fill="both", expand=True)

        nav = ttk.Frame(outer)
        nav.pack(fill="x", pady=(10, 0))

        self._btn_back = ttk.Button(nav, text="Back", command=self._back)
        self._btn_next = ttk.Button(nav, text="Next", command=self._next)
        self._btn_finish = ttk.Button(nav, text="Finish", command=self._finish)

        self._btn_back.pack(side="left")
        self._btn_finish.pack(side="right")
        self._btn_next.pack(side="right", padx=(0, 8))

        self._steps = [
            self._step_member(),
            self._step_baseline(),
            self._step_diet(),
            self._step_proteins_and_weights(),
            self._step_allergies(),
            self._step_excludes(),
            self._step_oils(),
            self._step_cuisines_styles(),
        ]

    # -----------------------------------------------------------------------
    # Step frames
    # -----------------------------------------------------------------------

    def _step_member(self) -> ttk.Frame:
        f = ttk.Frame(self._content, padding=10)

        ttk.Label(f, text="Who are we editing?", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        if self._active_is_master:
            ttk.Label(f, text="Master can edit any household member.", foreground="#888").pack(anchor="w", pady=(0, 12))
        else:
            ttk.Label(
                f,
                text="Secondary accounts can only edit their own preferences.",
                foreground="#888",
            ).pack(anchor="w", pady=(0, 12))

        members = config_store.list_members()
        labels: List[str] = []
        self._member_label_to_id.clear()

        allowed_ids: Set[int] = {m.id for m in members}
        if not self._active_is_master:
            allowed_ids = {self._active_user.id}

        for m in members:
            if m.id not in allowed_ids:
                continue
            tag = " (Master)" if m.role == config_store.ROLE_MASTER else ""
            label = f"{m.name}{tag}"
            labels.append(label)
            self._member_label_to_id[label] = m.id

        self._member_combo = ttk.Combobox(
            f, state="readonly", width=34, values=labels, textvariable=self._member_var
        )
        self._member_combo.pack(anchor="w")
        self._member_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_member_combo_changed())

        if not self._active_is_master:
            # Lock selection for secondary
            self._member_combo.configure(state="disabled")

        # Preselect if we already have a member_id
        if self._member_id:
            m = config_store.get_member(self._member_id)
            if m:
                label = f"{m.name}{' (Master)' if m.role == config_store.ROLE_MASTER else ''}"
                self._member_var.set(label)

        if self._active_is_master:
            ttk.Button(f, text="Add a new secondary member", command=self._quick_add_secondary).pack(
                anchor="w", pady=(14, 0)
            )

        return f

    def _step_baseline(self) -> ttk.Frame:
        f = ttk.Frame(self._content, padding=10)

        ttk.Label(f, text="Household baseline", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(
            f,
            text="The Master profile is the household baseline. Secondary members can add soft dislikes; allergies always hard-exclude.",
            foreground="#888",
        ).pack(anchor="w", pady=(0, 12))

        box = ttk.LabelFrame(f, text="Baseline summary (from Master)", padding=10)
        box.pack(fill="x")

        self._baseline_summary = ttk.Label(box, text="", justify="left")
        self._baseline_summary.pack(anchor="w")

        # Reset button for secondary edits
        self._btn_reset_baseline = ttk.Button(
            f, text="Reset this member to household baseline (keeps allergies)", command=self._reset_to_baseline
        )
        self._reset_note = ttk.Label(
            f,
            text="(Secondary overrides cleared: soft excludes, proteins, cuisines, oils, styles, spice, etc.)",
            foreground="#888",
        )

        return f

    def _step_diet(self) -> ttk.Frame:
        f = ttk.Frame(self._content, padding=10)

        ttk.Label(f, text="Diet basics", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(
            f,
            text="Quick yes/no toggles. These will help auto-toggle proteins next.",
            foreground="#888",
        ).pack(anchor="w", pady=(0, 12))

        box = ttk.LabelFrame(f, text="Do you eat…", padding=10)
        box.pack(fill="x")

        ttk.Checkbutton(box, text="Meat", variable=self._eats_meat, command=self._sync_proteins_from_diet).grid(
            row=0, column=0, sticky="w", padx=(0, 20), pady=4
        )
        ttk.Checkbutton(
            box, text="Fish / seafood", variable=self._eats_fish, command=self._sync_proteins_from_diet
        ).grid(row=0, column=1, sticky="w", padx=(0, 20), pady=4)

        ttk.Checkbutton(box, text="Dairy", variable=self._eats_dairy).grid(
            row=1, column=0, sticky="w", padx=(0, 20), pady=4
        )
        ttk.Checkbutton(box, text="Eggs", variable=self._eats_eggs).grid(
            row=1, column=1, sticky="w", padx=(0, 20), pady=4
        )

        return f

    def _step_proteins_and_weights(self) -> ttk.Frame:
        f = ttk.Frame(self._content, padding=10)

        ttk.Label(f, text="Proteins", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(f, text="Uncheck anything you want to exclude.", foreground="#888").pack(
            anchor="w", pady=(0, 12)
        )

        box = ttk.LabelFrame(f, text="Include proteins", padding=10)
        box.pack(fill="both", expand=True)

        sf = _ScrollableFrame(box, height=260)
        sf.pack(fill="both", expand=True)

        for i, p in enumerate(preferences_service.PROTEINS):
            r = i // 2
            c = i % 2
            ttk.Checkbutton(sf.inner, text=p.title(), variable=self._protein_allowed_vars[p]).grid(
                row=r, column=c, sticky="w", padx=(0, 20), pady=2
            )

        ttk.Button(f, text="Reset from diet toggles", command=self._sync_proteins_from_diet).pack(
            anchor="w", pady=(10, 0)
        )

        # Inline weights section: only enabled for master, but visible for clarity
        weights = ttk.LabelFrame(f, text="Master protein preferences (weights)", padding=10)
        weights.pack(fill="x", pady=(12, 0))

        self._weights_note = ttk.Label(
            weights,
            text="Only the Master’s weights affect household ranking. Secondary weights are stored but not used yet.",
            foreground="#888",
        )
        self._weights_note.pack(anchor="w", pady=(0, 8))

        grid = ttk.Frame(weights)
        grid.pack(fill="x")

        for i, p in enumerate(preferences_service.PROTEINS):
            ttk.Label(grid, text=p.title(), width=16).grid(row=i, column=0, sticky="w", pady=2)
            sc = ttk.Scale(grid, from_=0.5, to=2.0, variable=self._protein_weight_vars[p])
            sc.grid(row=i, column=1, sticky="ew", padx=(8, 8), pady=2)
            grid.columnconfigure(1, weight=1)

        ttk.Button(weights, text="Quick: Prefer chicken", command=self._prefer_chicken).pack(anchor="w", pady=(8, 0))

        self._weights_frame = weights
        return f

    def _step_allergies(self) -> ttk.Frame:
        f = ttk.Frame(self._content, padding=10)

        ttk.Label(f, text="Allergies", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(
            f,
            text="Allergies are ALWAYS hard excludes for the whole household (no matter who sets them).",
            foreground="#888",
        ).pack(anchor="w", pady=(0, 12))

        self._allergies_list = _ValidatedList(
            f,
            title="Allergies",
            hint="Example: peanut, shellfish. These will be excluded from deals, recipes, and suggestions.",
            height=9,
        )
        self._allergies_list.pack(fill="both", expand=True)

        return f

    def _step_excludes(self) -> ttk.Frame:
        f = ttk.Frame(self._content, padding=10)

        ttk.Label(f, text="Dislikes & excludes", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(
            f,
            text="Household hard excludes are set by the Master + any allergies. Secondary dislikes become soft excludes (starred *).",
            foreground="#888",
        ).pack(anchor="w", pady=(0, 12))

        row = ttk.Frame(f)
        row.pack(fill="both", expand=True)
        row.columnconfigure(0, weight=1)
        row.columnconfigure(1, weight=1)

        self._household_hard_ro = _ReadOnlyList(
            row,
            title="Household hard excludes (read-only)",
            hint="These are already excluded household-wide. Secondary members cannot add these to soft excludes (redundant).",
            height=7,
        )
        self._household_hard_ro.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        # Master hard excludes editor (enabled only for master)
        self._hard_list = _ValidatedList(
            row,
            title="Hard excludes (Master only)",
            hint="Never recommend these ingredients (e.g., real olives).",
            height=7,
        )
        self._hard_list.grid(row=0, column=1, sticky="nsew")

        self._soft_list = _ValidatedList(
            f,
            title="Soft excludes (member dislikes)",
            hint="Allowed but deprioritized. If any secondary excludes an ingredient, it may appear with a '*' marker.",
            height=8,
        )
        self._soft_list.pack(fill="both", expand=True, pady=(12, 0))

        self._soft_block_note = ttk.Label(
            f,
            text="",
            foreground="#888",
        )
        self._soft_block_note.pack(anchor="w", pady=(8, 0))

        return f

    def _step_oils(self) -> ttk.Frame:
        f = ttk.Frame(self._content, padding=10)

        ttk.Label(f, text="Oils used at home", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(
            f,
            text="Select oils you use. If none are selected, oils are treated as unrestricted.",
            foreground="#888",
        ).pack(anchor="w", pady=(0, 12))

        oils = ttk.LabelFrame(f, text="Allowed oils", padding=10)
        oils.pack(fill="both", expand=True)

        sf = _ScrollableFrame(oils, height=360)
        sf.pack(fill="both", expand=True)

        for i, o in enumerate(preferences_service.OILS):
            r = i // 2
            c = i % 2
            ttk.Checkbutton(sf.inner, text=o.title(), variable=self._oil_vars[o]).grid(
                row=r, column=c, sticky="w", padx=(0, 20), pady=2
            )

        self._oils_hint = ttk.Label(f, text="", foreground="#888")
        self._oils_hint.pack(anchor="w", pady=(10, 0))

        return f

    def _step_cuisines_styles(self) -> ttk.Frame:
        f = ttk.Frame(self._content, padding=10)

        ttk.Label(f, text="Cuisines, spice & style", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(
            f,
            text="Helps suggestions stay aligned with what you actually cook.",
            foreground="#888",
        ).pack(anchor="w", pady=(0, 12))

        top = ttk.Frame(f)
        top.pack(fill="x")

        ttk.Label(top, text="Spice preference:").pack(side="left")
        self._spice_combo = ttk.Combobox(
            top, state="readonly", width=12, values=["low", "medium", "high"], textvariable=self._spice_var
        )
        self._spice_combo.pack(side="left", padx=(8, 0))

        styles = ttk.LabelFrame(f, text="Cooking styles", padding=10)
        styles.pack(fill="x", pady=(12, 0))

        for i, (tag, label) in enumerate(preferences_service.STYLE_TAGS):
            ttk.Checkbutton(styles, text=label, variable=self._style_vars[tag]).grid(
                row=i // 2, column=i % 2, sticky="w", padx=(0, 20), pady=2
            )

        cuisines = ttk.LabelFrame(f, text="Favorite cuisines", padding=10)
        cuisines.pack(fill="both", expand=True, pady=(12, 0))

        sf = _ScrollableFrame(cuisines, height=260)
        sf.pack(fill="both", expand=True)

        for i, c in enumerate(preferences_service.CUISINES):
            r = i // 2
            col = i % 2
            ttk.Checkbutton(sf.inner, text=c.title(), variable=self._cuisine_vars[c]).grid(
                row=r, column=col, sticky="w", padx=(0, 20), pady=2
            )

        self._cuisines_hint = ttk.Label(f, text="", foreground="#888")
        self._cuisines_hint.pack(anchor="w", pady=(10, 0))

        return f

    # -----------------------------------------------------------------------
    # Navigation
    # -----------------------------------------------------------------------

    def _show_step(self, idx: int) -> None:
        idx = max(0, min(idx, len(self._steps) - 1))
        self._step = idx

        for child in self._content.winfo_children():
            child.pack_forget()
            child.grid_forget()

        frame = self._steps[idx]
        frame.pack(fill="both", expand=True)

        titles = [
            ("Member", "Pick which household member to configure."),
            ("Baseline", "Household baseline rules + reset for secondary members."),
            ("Diet", "Quick yes/no diet toggles."),
            ("Proteins", "Exclude proteins you don’t eat (+ weights if Master)."),
            ("Allergies", "Allergies always hard-exclude household-wide."),
            ("Excludes", "Hard excludes (Master) + soft dislikes (Secondary)."),
            ("Oils", "Select oils used at home."),
            ("Cuisines", "Favorite cuisines + spice + meal styles."),
        ]
        self._header.configure(text=f"Preferences Wizard — Step {idx + 1} of {len(self._steps)}: {titles[idx][0]}")
        self._sub.configure(text=titles[idx][1])

        self._btn_back.configure(state="normal" if idx > 0 else "disabled")
        self._btn_next.configure(state="normal" if idx < len(self._steps) - 1 else "disabled")
        self._btn_finish.configure(state="normal" if idx == len(self._steps) - 1 else "disabled")

        # Step-specific refresh
        if idx == 1:
            self._refresh_baseline_step_ui()
        if idx == 4:
            self._refresh_allergies_step_ui()
        if idx == 5:
            self._refresh_excludes_step_ui()
        if idx == 6:
            self._refresh_oils_step_ui()
        if idx == 7:
            self._refresh_cuisines_step_ui()
        if idx == 3:
            self._refresh_protein_weights_ui()

        # If wizard was launched with a member_id, skip step 1 automatically
        if idx == 0 and self._member_id:
            self.after(0, lambda: self._show_step(1))

    def _back(self) -> None:
        self._show_step(self._step - 1)

    def _next(self) -> None:
        if self._step == 0:
            if not self._member_id:
                label = (self._member_var.get() or "").strip()
                if not label or label not in self._member_label_to_id:
                    messagebox.showwarning("Select a member", "Please select a member to configure.", parent=self)
                    return
                chosen_id = self._member_label_to_id[label]
                self._load_member_into_state(chosen_id)

        self._show_step(self._step + 1)

    def _finish(self) -> None:
        if not self._member_id:
            messagebox.showwarning("Missing member", "No member selected.", parent=self)
            return

        member = config_store.get_member(self._member_id)
        if not member:
            messagebox.showerror("Error", "Member not found in config.", parent=self)
            return

        profile = self._collect_profile()
        config_store.save_member_profile(member.id, profile)

        if self._log:
            try:
                self._log(f"Preferences wizard saved for {member.name} (id={member.id})")
            except Exception:
                pass

        messagebox.showinfo("Saved", f"Saved preferences for {member.name}.", parent=self)
        self.destroy()

    # -----------------------------------------------------------------------
    # Member selection / role rules
    # -----------------------------------------------------------------------

    def _on_member_combo_changed(self) -> None:
        if not self._active_is_master:
            return
        label = (self._member_var.get() or "").strip()
        mid = self._member_label_to_id.get(label)
        if not mid:
            return
        self._load_member_into_state(mid)

    def _quick_add_secondary(self) -> None:
        if not self._active_is_master:
            messagebox.showinfo("Restricted", "Only the Master user can add members.", parent=self)
            return

        name = simpledialog.askstring("Add member", "Member name:", parent=self)
        if not name:
            return

        m = config_store.add_member(name=name, role=config_store.ROLE_SECONDARY)
        messagebox.showinfo("Added", f"Added {m.name}. Select them from the dropdown.", parent=self)

        # Refresh dropdown
        members = config_store.list_members()
        labels: List[str] = []
        self._member_label_to_id.clear()
        for mem in members:
            tag = " (Master)" if mem.role == config_store.ROLE_MASTER else ""
            label = f"{mem.name}{tag}"
            labels.append(label)
            self._member_label_to_id[label] = mem.id

        self._member_combo.configure(values=labels)
        self._member_var.set(m.name)
        self._load_member_into_state(m.id)

    def _load_member_into_state(self, member_id: int) -> None:
        # Enforce security: secondary can only load themselves
        if not self._active_is_master and member_id != self._active_user.id:
            member_id = self._active_user.id

        m = config_store.get_member(member_id)
        if not m:
            return

        self._member_id = m.id
        self._editing_member_role = m.role
        self._editing_member_name = m.name

        prof = m.profile or {}

        # Diet toggles
        self._eats_meat.set(bool(prof.get("eats_meat", True)))
        self._eats_fish.set(bool(prof.get("eats_fish", True)))
        self._eats_dairy.set(bool(prof.get("eats_dairy", True)))
        self._eats_eggs.set(bool(prof.get("eats_eggs", True)))

        # Proteins
        excluded = set(_norm_list(prof.get("excluded_proteins", []) or []))
        for p in preferences_service.PROTEINS:
            self._protein_allowed_vars[p].set(_norm_token(p) not in excluded)

        # Weights (support both dict keys)
        weights = prof.get("preferred_protein_weights", {}) or {}
        if not isinstance(weights, dict):
            weights = {}
        for p in preferences_service.PROTEINS:
            key = _norm_token(p)
            try:
                self._protein_weight_vars[p].set(float(weights.get(key, 1.0)))
            except Exception:
                self._protein_weight_vars[p].set(1.0)

        # Spice
        self._spice_var.set(_norm_token(prof.get("spice_level", "medium")) or "medium")

        # Styles (support both "styles" and "meal_styles")
        styles_raw = prof.get("styles", None)
        if styles_raw is None:
            styles_raw = prof.get("meal_styles", []) or []
        styles = set(_norm_list(styles_raw))
        for tag in self._style_vars:
            self._style_vars[tag].set(tag in styles)

        # Cuisines
        cuisines = set(_norm_list(prof.get("favorite_cuisines", []) or []))
        for c in self._cuisine_vars:
            self._cuisine_vars[c].set(_norm_token(c) in cuisines)

        # Oils
        oils_allowed = set(_norm_list(prof.get("oils_allowed", []) or []))
        for o in self._oil_vars:
            self._oil_vars[o].set(_norm_token(o) in oils_allowed)

        # Prime sync
        self._sync_proteins_from_diet()

        # Update member combobox label (if master)
        if self._active_is_master:
            label = f"{m.name}{' (Master)' if m.role == config_store.ROLE_MASTER else ''}"
            self._member_var.set(label)

    # -----------------------------------------------------------------------
    # Step refresh hooks
    # -----------------------------------------------------------------------

    def _refresh_baseline_step_ui(self) -> None:
        # Baseline is master profile
        baseline = {}
        try:
            baseline = preferences_service.get_household_baseline_profile()
        except Exception:
            master = config_store.get_master_member()
            baseline = dict((master.profile or {}) if master else {})

        hard = _norm_list(baseline.get("hard_excludes", []) or [])
        excl_proteins = _norm_list(baseline.get("excluded_proteins", []) or [])
        oils = _norm_list(baseline.get("oils_allowed", []) or [])
        cuisines = _norm_list(baseline.get("favorite_cuisines", []) or [])
        spice = _norm_token(baseline.get("spice_level", "medium"))

        # Oils: if empty baseline, treat as unrestricted (display that)
        oils_txt = "unrestricted (none selected)" if not oils else ", ".join(sorted(set(oils))[:8]) + ("…" if len(set(oils)) > 8 else "")

        lines = [
            f"Baseline Master: {config_store.get_master_member().name}",
            f"Hard excludes: {('none' if not hard else ', '.join(sorted(set(hard))[:8]) + ('…' if len(set(hard)) > 8 else ''))}",
            f"Excluded proteins: {('none' if not excl_proteins else ', '.join(sorted(set(excl_proteins))[:8]) + ('…' if len(set(excl_proteins)) > 8 else ''))}",
            f"Oils allowed: {oils_txt}",
            f"Favorite cuisines: {('none' if not cuisines else ', '.join(sorted(set(cuisines))[:8]) + ('…' if len(set(cuisines)) > 8 else ''))}",
            f"Spice: {spice or 'medium'}",
        ]
        self._baseline_summary.configure(text="\n".join(lines))

        # Reset controls (secondary only)
        for w in (self._btn_reset_baseline, self._reset_note):
            try:
                w.pack_forget()
            except Exception:
                pass

        if self._member_id and self._editing_member_role != config_store.ROLE_MASTER:
            self._btn_reset_baseline.pack(anchor="w", pady=(14, 0))
            self._reset_note.pack(anchor="w", pady=(6, 0))

    def _refresh_protein_weights_ui(self) -> None:
        is_master = self._editing_member_role == config_store.ROLE_MASTER
        # Enable/disable weight controls depending on member role
        try:
            for child in self._weights_frame.winfo_children():
                # note label should remain visible
                if isinstance(child, ttk.Frame):
                    for w in child.winfo_children():
                        try:
                            w.configure(state="normal" if is_master else "disabled")
                        except Exception:
                            pass
                else:
                    # buttons etc
                    try:
                        # keep label visible
                        if isinstance(child, ttk.Button):
                            child.configure(state="normal" if is_master else "disabled")
                    except Exception:
                        pass
        except Exception:
            pass

    def _refresh_allergies_step_ui(self) -> None:
        if not self._member_id or not self._allergies_list:
            return
        m = config_store.get_member(self._member_id)
        prof = (m.profile or {}) if m else {}
        self._allergies_list.set_values(_norm_list(prof.get("allergies", []) or []))

    def _compute_household_hard_excludes(self) -> Set[str]:
        """
        Household hard excludes = any allergies (any member) + master hard_excludes.
        """
        hard: Set[str] = set()
        try:
            eff = preferences_service.compute_effective_preferences()
            for x in getattr(eff, "hard_excludes", set()) or set():
                hard.add(_norm_token(x))
            return set([x for x in hard if x])
        except Exception:
            # Fallback: compute manually
            cfg = config_store.load_config()
            # allergies from any member
            try:
                for mem in cfg.household.members:
                    prof = mem.profile or {}
                    for a in _norm_list(prof.get("allergies", []) or []):
                        hard.add(a)
            except Exception:
                pass
            master = config_store.get_master_member()
            mprof = (master.profile or {}) if master else {}
            for x in _norm_list(mprof.get("hard_excludes", []) or []):
                hard.add(x)
            return set([x for x in hard if x])

    def _refresh_excludes_step_ui(self) -> None:
        if not self._member_id:
            return

        self._household_hard_excludes = self._compute_household_hard_excludes()

        # Populate household hard read-only list
        if self._household_hard_ro:
            self._household_hard_ro.set_values(sorted(self._household_hard_excludes))

        m = config_store.get_member(self._member_id)
        prof = (m.profile or {}) if m else {}

        # Ensure list widgets are populated if present
        if self._hard_list:
            self._hard_list.set_values(_norm_list(prof.get("hard_excludes", []) or []))
        if self._soft_list:
            self._soft_list.set_values(_norm_list(prof.get("soft_excludes", []) or []))

        # Secondary: hard excludes editor disabled (and also "soft add" blocks household hard items)
        is_master = self._editing_member_role == config_store.ROLE_MASTER
        if self._hard_list:
            self._hard_list.set_enabled(is_master)

        if self._soft_list:
            def validate_soft_add(token: str) -> Tuple[bool, str]:
                if not token:
                    return False, "Please enter a value."
                if token in self._household_hard_excludes:
                    return False, f"'{token}' is already hard-excluded household-wide (Master/allergies). Adding it as a soft exclude is redundant."
                return True, ""
            self._soft_list.set_validate_add(validate_soft_add if not is_master else None)

        # Master: optional validation for hard excludes to prevent redundant allergy duplicates
        if is_master and self._hard_list:
            def validate_hard_add(token: str) -> Tuple[bool, str]:
                if not token:
                    return False, "Please enter a value."
                # If token exists due to allergies already, it's redundant but not harmful—block for cleanliness.
                # We only block if it's an allergy from any member.
                allergy_set: Set[str] = set()
                cfg = config_store.load_config()
                for mem in getattr(cfg.household, "members", []) or []:
                    for a in _norm_list((mem.profile or {}).get("allergies", []) or []):
                        allergy_set.add(a)
                if token in allergy_set:
                    return False, f"'{token}' is already listed as an allergy for someone. Allergies are always hard-excludes; no need to add it again."
                return True, ""
            self._hard_list.set_validate_add(validate_hard_add)

        # Soft-block note
        if self._soft_block_note:
            if not is_master:
                self._soft_block_note.configure(
                    text="Secondary rule: You cannot add household hard-excludes to soft excludes. (They’re already blocked.)"
                )
            else:
                self._soft_block_note.configure(text="")

    def _refresh_oils_step_ui(self) -> None:
        is_master = self._editing_member_role == config_store.ROLE_MASTER
        # For now, keep oils editable for everyone but clarify baseline effect.
        # If you want oils to be master-only, set enabled False for secondary.
        self._oils_hint.configure(
            text=("Baseline: Master oils affect household filtering. Your selection is saved for your profile."
                  if not is_master
                  else "Master oils affect household filtering.")
        )

    def _refresh_cuisines_step_ui(self) -> None:
        is_master = self._editing_member_role == config_store.ROLE_MASTER
        self._cuisines_hint.configure(
            text=("Baseline: Master cuisines/styles affect household suggestions. Your selection is saved for your profile."
                  if not is_master
                  else "Master cuisines/styles affect household suggestions.")
        )

    # -----------------------------------------------------------------------
    # Actions
    # -----------------------------------------------------------------------

    def _reset_to_baseline(self) -> None:
        if not self._member_id:
            return
        if self._editing_member_role == config_store.ROLE_MASTER:
            messagebox.showinfo("Not applicable", "The Master is the household baseline.", parent=self)
            return

        if not messagebox.askyesno(
            "Reset to baseline",
            f"Reset {self._editing_member_name} to the household baseline?\n\n"
            f"This clears overrides (soft excludes, proteins, cuisines, oils, styles, spice, etc.) but keeps allergies.",
            parent=self,
        ):
            return

        try:
            preferences_service.reset_secondary_member_to_household_baseline(self._member_id)
        except Exception as e:
            messagebox.showerror("Reset failed", f"Could not reset to baseline.\n\n{e}", parent=self)
            return

        # Reload state
        self._cfg = config_store.load_config()
        self._load_member_into_state(self._member_id)

        messagebox.showinfo("Reset", "Member was reset to household baseline (allergies preserved).", parent=self)

        # If we're on excludes-related steps, refresh displays
        self._refresh_baseline_step_ui()
        self._refresh_allergies_step_ui()
        self._refresh_excludes_step_ui()

    def _sync_proteins_from_diet(self) -> None:
        eats_meat = bool(self._eats_meat.get())
        eats_fish = bool(self._eats_fish.get())

        for p in ["chicken", "beef", "pork", "lamb", "turkey"]:
            if p in self._protein_allowed_vars and not eats_meat:
                self._protein_allowed_vars[p].set(False)

        for p in ["fish", "shellfish"]:
            if p in self._protein_allowed_vars and not eats_fish:
                self._protein_allowed_vars[p].set(False)

    def _prefer_chicken(self) -> None:
        if "chicken" in self._protein_weight_vars:
            self._protein_weight_vars["chicken"].set(1.5)

    # -----------------------------------------------------------------------
    # Collect & save
    # -----------------------------------------------------------------------

    def _collect_profile(self) -> Dict[str, Any]:
        prof: Dict[str, Any] = {}

        # Diet flags
        prof["eats_meat"] = bool(self._eats_meat.get())
        prof["eats_fish"] = bool(self._eats_fish.get())
        prof["eats_dairy"] = bool(self._eats_dairy.get())
        prof["eats_eggs"] = bool(self._eats_eggs.get())

        # Proteins
        excluded: List[str] = []
        for p in preferences_service.PROTEINS:
            if not bool(self._protein_allowed_vars[p].get()):
                excluded.append(_norm_token(p))
        prof["excluded_proteins"] = excluded

        # Weights
        weights: Dict[str, float] = {}
        for p in preferences_service.PROTEINS:
            key = _norm_token(p)
            try:
                weights[key] = float(self._protein_weight_vars[p].get())
            except Exception:
                weights[key] = 1.0
        prof["preferred_protein_weights"] = weights

        # Allergies
        member = config_store.get_member(self._member_id or 0)
        current_profile = (member.profile or {}) if member else {}

        allergies = []
        if self._allergies_list:
            allergies = self._allergies_list.get_values()
        else:
            allergies = _norm_list(current_profile.get("allergies", []) or [])
        prof["allergies"] = allergies

        # Excludes
        hard = _norm_list(current_profile.get("hard_excludes", []) or [])
        soft = _norm_list(current_profile.get("soft_excludes", []) or [])

        if self._hard_list:
            hard = self._hard_list.get_values()
        if self._soft_list:
            soft = self._soft_list.get_values()

        if self._editing_member_role == config_store.ROLE_MASTER:
            prof["hard_excludes"] = hard
            prof["soft_excludes"] = soft
        else:
            # Secondary: hard excludes become soft excludes; also prevent redundant additions
            # (We already block via validator, but keep safe here too.)
            merged_soft = sorted(set(soft + hard))
            merged_soft = [x for x in merged_soft if x and x not in self._household_hard_excludes]
            prof["hard_excludes"] = []
            prof["soft_excludes"] = merged_soft

        # Spice, styles, cuisines, oils
        prof["spice_level"] = (_norm_token(self._spice_var.get()) or "medium")

        styles = [tag for tag, var in self._style_vars.items() if bool(var.get())]
        prof["styles"] = styles
        prof["meal_styles"] = styles  # compat with newer service keys

        prof["favorite_cuisines"] = [c for c, var in self._cuisine_vars.items() if bool(var.get())]

        oils = [o for o, var in self._oil_vars.items() if bool(var.get())]
        prof["oils_allowed"] = oils

        return prof


def open_preferences_wizard_window(
    master: Optional[tk.Misc] = None,
    *,
    member_id: Optional[int] = None,
    log: Optional[Callable[[str], None]] = None,
) -> PreferencesWizardWindow:
    return PreferencesWizardWindow(master, member_id=member_id, log=log)
