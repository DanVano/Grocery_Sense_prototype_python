from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from typing import Any, Callable, Dict, List, Optional

from Grocery_Sense import config_store
from Grocery_Sense.services import preferences_service


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


class _SimpleList(ttk.Frame):
    def __init__(self, master: tk.Misc, *, title: str, hint: str = "", height: int = 7) -> None:
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

        btns = ttk.Frame(self)
        btns.pack(fill="x", pady=(6, 0))
        ttk.Button(btns, text="Add", command=self._add).pack(side="left")
        ttk.Button(btns, text="Remove", command=self._remove).pack(side="left", padx=(8, 0))

        self._values: List[str] = []

    def set_values(self, values: List[str]) -> None:
        self._values = self._norm(values)
        self._render()

    def get_values(self) -> List[str]:
        return list(self._values)

    def _norm(self, values: Any) -> List[str]:
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

    def _render(self) -> None:
        self.listbox.delete(0, tk.END)
        for v in self._values:
            self.listbox.insert(tk.END, v)

    def _add(self) -> None:
        raw = simpledialog.askstring("Add", "Enter a value (e.g., 'olives'):", parent=self)
        if not raw:
            return
        s = raw.strip().lower()
        if not s:
            return
        if s not in self._values:
            self._values.append(s)
            self._values.sort()
            self._render()

    def _remove(self) -> None:
        sel = self.listbox.curselection()
        if not sel:
            return
        idx = int(sel[0])
        if 0 <= idx < len(self._values):
            self._values.pop(idx)
            self._render()


class PreferencesWizardWindow(tk.Toplevel):
    def __init__(
        self,
        master: Optional[tk.Misc] = None,
        *,
        member_id: Optional[int] = None,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        super().__init__(master)
        self.title("Preferences Wizard")
        self.geometry("820x660")
        self.minsize(760, 600)

        self._log = log
        self._cfg = config_store.load_config()
        self._member_id: Optional[int] = member_id

        self._member_var = tk.StringVar(value="")
        self._member_label_to_id: Dict[str, int] = {}

        self._role: str = config_store.ROLE_SECONDARY

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
            tag: tk.BooleanVar(value=False) for tag, _ in preferences_service.STYLE_TAGS
        }
        self._cuisine_vars: Dict[str, tk.BooleanVar] = {
            c: tk.BooleanVar(value=False) for c in preferences_service.CUISINES
        }
        self._oil_vars: Dict[str, tk.BooleanVar] = {
            o: tk.BooleanVar(value=False) for o in preferences_service.OILS
        }

        self._allergies_list: Optional[_SimpleList] = None
        self._hard_list: Optional[_SimpleList] = None
        self._soft_list: Optional[_SimpleList] = None

        self._step = 0
        self._steps: List[ttk.Frame] = []

        self._build_ui()
        self._load_member_into_state()
        self._show_step(0)

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
            self._step_diet(),
            self._step_proteins(),
            self._step_weights(),
            self._step_excludes(),
            self._step_cuisines_styles(),
            self._step_oils(),
        ]

    def _step_member(self) -> ttk.Frame:
        f = ttk.Frame(self._content, padding=10)

        ttk.Label(f, text="Who are we setting up?", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(f, text="You can re-run this wizard any time.", foreground="#888").pack(anchor="w", pady=(0, 12))

        members = config_store.list_members()
        labels: List[str] = []
        self._member_label_to_id.clear()

        for m in members:
            tag = " (Master)" if m.role == config_store.ROLE_MASTER else ""
            label = f"{m.name}{tag}"
            labels.append(label)
            self._member_label_to_id[label] = m.id

        self._member_combo = ttk.Combobox(
            f, state="readonly", width=30, values=labels, textvariable=self._member_var
        )
        self._member_combo.pack(anchor="w")

        if self._member_id:
            m = config_store.get_member(self._member_id)
            if m:
                label = f"{m.name}{' (Master)' if m.role == config_store.ROLE_MASTER else ''}"
                self._member_var.set(label)

        ttk.Button(f, text="Add a new secondary member", command=self._quick_add_secondary).pack(
            anchor="w", pady=(14, 0)
        )
        return f

    def _step_diet(self) -> ttk.Frame:
        f = ttk.Frame(self._content, padding=10)

        ttk.Label(f, text="Diet basics", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(f, text="A few quick yes/no toggles. We’ll refine next.", foreground="#888").pack(
            anchor="w", pady=(0, 12)
        )

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

    def _step_proteins(self) -> ttk.Frame:
        f = ttk.Frame(self._content, padding=10)

        ttk.Label(f, text="Proteins", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(f, text="Uncheck anything you want to exclude.", foreground="#888").pack(
            anchor="w", pady=(0, 12)
        )

        box = ttk.LabelFrame(f, text="Include proteins", padding=10)
        box.pack(fill="both", expand=True)

        sf = _ScrollableFrame(box, height=320)
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
        return f

    def _step_weights(self) -> ttk.Frame:
        f = ttk.Frame(self._content, padding=10)

        ttk.Label(f, text="Master preferences (weights)", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(f, text="Only used for the Master profile (priority).", foreground="#888").pack(
            anchor="w", pady=(0, 12)
        )

        box = ttk.LabelFrame(f, text="Protein preference weights", padding=10)
        box.pack(fill="both", expand=True)

        grid = ttk.Frame(box)
        grid.pack(fill="both", expand=True)

        for i, p in enumerate(preferences_service.PROTEINS):
            ttk.Label(grid, text=p.title(), width=16).grid(row=i, column=0, sticky="w", pady=3)
            sc = ttk.Scale(grid, from_=0.5, to=2.0, variable=self._protein_weight_vars[p])
            sc.grid(row=i, column=1, sticky="ew", padx=(8, 8), pady=3)
            grid.columnconfigure(1, weight=1)

        ttk.Button(box, text="Quick: Prefer chicken", command=lambda: self._protein_weight_vars["chicken"].set(1.5)).pack(
            anchor="w", pady=(10, 0)
        )
        return f

    def _step_excludes(self) -> ttk.Frame:
        f = ttk.Frame(self._content, padding=10)

        ttk.Label(f, text="Allergies & excludes", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(
            f,
            text="Allergies are always hard excludes for the whole household. Secondary member excludes are soft.",
            foreground="#888",
        ).pack(anchor="w", pady=(0, 12))

        row = ttk.Frame(f)
        row.pack(fill="both", expand=True)
        row.columnconfigure(0, weight=1)
        row.columnconfigure(1, weight=1)

        self._allergies_list = _SimpleList(row, title="Allergies", hint="Always hard excludes for the whole household.", height=6)
        self._hard_list = _SimpleList(row, title="Hard excludes (master only)", hint="Never recommend these ingredients.", height=6)
        self._soft_list = _SimpleList(f, title="Soft excludes", hint="Allowed, but deprioritized. Secondary excludes will show with '*'.", height=7)

        self._allergies_list.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self._hard_list.grid(row=0, column=1, sticky="nsew")
        self._soft_list.pack(fill="both", expand=True, pady=(12, 0))

        m = config_store.get_member(self._member_id or 0)
        if m:
            prof = m.profile or {}
            self._allergies_list.set_values(list(prof.get("allergies", []) or []))
            self._hard_list.set_values(list(prof.get("hard_excludes", []) or []))
            self._soft_list.set_values(list(prof.get("soft_excludes", []) or []))

        if self._role != config_store.ROLE_MASTER:
            ttk.Label(
                f,
                text="Note: Secondary user 'hard excludes' will be treated as soft excludes.",
                foreground="#888",
            ).pack(anchor="w", pady=(8, 0))

        return f

    def _step_cuisines_styles(self) -> ttk.Frame:
        f = ttk.Frame(self._content, padding=10)

        ttk.Label(f, text="Cuisines & style", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(f, text="Helps keep suggestions aligned with what you actually cook.", foreground="#888").pack(
            anchor="w", pady=(0, 12)
        )

        top = ttk.Frame(f)
        top.pack(fill="x")

        ttk.Label(top, text="Spice preference:").pack(side="left")
        ttk.Combobox(top, state="readonly", width=12, values=["low", "medium", "high"], textvariable=self._spice_var).pack(
            side="left", padx=(8, 0)
        )

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

        return f

    def _step_oils(self) -> ttk.Frame:
        f = ttk.Frame(self._content, padding=10)

        ttk.Label(f, text="Oils used at home", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(f, text="Select oils you use. If none are selected, oils are unrestricted.", foreground="#888").pack(
            anchor="w", pady=(0, 12)
        )

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

        return f

    # ---------------- navigation ----------------

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
            ("Diet", "Quick diet toggles."),
            ("Proteins", "Exclude proteins you don’t eat."),
            ("Weights", "Master-only protein preferences."),
            ("Excludes", "Allergies and dislikes."),
            ("Cuisines", "What you like to cook."),
            ("Oils", "Which oils you use."),
        ]
        self._header.configure(text=f"Preferences Wizard — Step {idx + 1} of {len(self._steps)}: {titles[idx][0]}")
        self._sub.configure(text=titles[idx][1])

        self._btn_back.configure(state="normal" if idx > 0 else "disabled")
        self._btn_next.configure(state="normal" if idx < len(self._steps) - 1 else "disabled")
        self._btn_finish.configure(state="normal" if idx == len(self._steps) - 1 else "disabled")

        if idx == 0 and self._member_id:
            self._show_step(1)
            return

        if idx == 3 and self._role != config_store.ROLE_MASTER:
            self._show_step(4)
            return

    def _back(self) -> None:
        self._show_step(self._step - 1)

    def _next(self) -> None:
        if self._step == 0 and not self._member_id:
            label = (self._member_var.get() or "").strip()
            if not label or label not in self._member_label_to_id:
                messagebox.showwarning("Select a member", "Please select a member to configure.", parent=self)
                return
            self._member_id = self._member_label_to_id[label]
            self._load_member_into_state()

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

    # ---------------- state ----------------

    def _quick_add_secondary(self) -> None:
        active = config_store.get_active_member()
        if active.role != config_store.ROLE_MASTER:
            messagebox.showinfo("Restricted", "Only the Master user can add members.", parent=self)
            return

        name = simpledialog.askstring("Add member", "Member name:", parent=self)
        if not name:
            return

        m = config_store.add_member(name=name, role=config_store.ROLE_SECONDARY)
        messagebox.showinfo("Added", f"Added {m.name}. Select them from the dropdown.", parent=self)

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

    def _load_member_into_state(self) -> None:
        if not self._member_id:
            return
        m = config_store.get_member(self._member_id)
        if not m:
            return

        self._role = m.role
        prof = m.profile or {}

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
                self._protein_weight_vars[p].set(float(weights.get(p, 1.0)))
            except Exception:
                self._protein_weight_vars[p].set(1.0)

        self._spice_var.set(str(prof.get("spice_level", "medium")).strip().lower() or "medium")

        styles = set(str(s).strip().lower() for s in (prof.get("styles", []) or []))
        for tag in self._style_vars:
            self._style_vars[tag].set(tag in styles)

        cuisines = set(str(c).strip().lower() for c in (prof.get("favorite_cuisines", []) or []))
        for c in self._cuisine_vars:
            self._cuisine_vars[c].set(c in cuisines)

        oils_allowed = set(str(o).strip().lower() for o in (prof.get("oils_allowed", []) or []))
        for o in self._oil_vars:
            self._oil_vars[o].set(o in oils_allowed)

        self._sync_proteins_from_diet()

    def _sync_proteins_from_diet(self) -> None:
        eats_meat = bool(self._eats_meat.get())
        eats_fish = bool(self._eats_fish.get())

        for p in ["chicken", "beef", "pork", "lamb", "turkey"]:
            if p in self._protein_allowed_vars and not eats_meat:
                self._protein_allowed_vars[p].set(False)

        for p in ["fish", "shellfish"]:
            if p in self._protein_allowed_vars and not eats_fish:
                self._protein_allowed_vars[p].set(False)

    def _collect_profile(self) -> Dict[str, Any]:
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

        member = config_store.get_member(self._member_id or 0)
        current_profile = (member.profile or {}) if member else {}

        allergies = self._allergies_list.get_values() if self._allergies_list else list(current_profile.get("allergies", []) or [])
        hard = self._hard_list.get_values() if self._hard_list else list(current_profile.get("hard_excludes", []) or [])
        soft = self._soft_list.get_values() if self._soft_list else list(current_profile.get("soft_excludes", []) or [])

        prof["allergies"] = allergies

        if self._role == config_store.ROLE_MASTER:
            prof["hard_excludes"] = hard
            prof["soft_excludes"] = soft
        else:
            prof["hard_excludes"] = []
            prof["soft_excludes"] = sorted(set(soft + hard))

        prof["spice_level"] = (self._spice_var.get() or "medium").strip().lower()
        prof["styles"] = [tag for tag, var in self._style_vars.items() if bool(var.get())]
        prof["favorite_cuisines"] = [c for c, var in self._cuisine_vars.items() if bool(var.get())]
        prof["oils_allowed"] = [o for o, var in self._oil_vars.items() if bool(var.get())]

        return prof


def open_preferences_wizard_window(
    master: Optional[tk.Misc] = None,
    *,
    member_id: Optional[int] = None,
    log: Optional[Callable[[str], None]] = None,
) -> PreferencesWizardWindow:
    return PreferencesWizardWindow(master, member_id=member_id, log=log)
