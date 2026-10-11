"""Media & properties: assets, marker bindings, compiled timeline, overlays/music/SFX."""
from __future__ import annotations

import os
import random
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk
from typing import TYPE_CHECKING, Callable, Optional

from PIL import Image, ImageTk

from .. import assets as A
from ..media import MediaError
from ..model import FILL_LABELS, RESIZE_LABELS, Binding, LowerThird, SfxPlacement
from ..util import fmt_seconds, parse_time, seconds_to_tc
from .widgets import BG2, FG_DIM, RED, ScrollableFrame

if TYPE_CHECKING:
    from .app import App

KIND_LABELS = {"soundbite": "Soundbite", "standup": "Stand-up", "broll": "B-roll", "image": "Image",
               "music": "Music", "sfx": "SFX", "logo": "Logo/Watermark", "narration": "Narration"}


def asset_choices(project, kinds) -> tuple[list[str], dict[str, Optional[str]]]:
    names = ["— none —"]
    mapping: dict[str, Optional[str]] = {"— none —": None}
    for a in project.assets:
        if not kinds or a.kind in kinds:
            label = f"{a.name}  [{a.id}]"
            names.append(label)
            mapping[label] = a.id
    return names, mapping


def time_text(seconds: float, fps: int) -> str:
    """Timecode when frame-aligned, otherwise exact seconds (never silently re-quantised)."""
    if abs(seconds * fps - round(seconds * fps)) < 1e-6:
        return seconds_to_tc(seconds, fps)
    return f"{seconds:.3f}"


def label_for(project, asset_id) -> str:
    a = project.asset(asset_id)
    return f"{a.name}  [{a.id}]" if a else "— none —"


class MediaPanel(ttk.Frame):
    def __init__(self, master, app: "App"):
        super().__init__(master)
        self.app = app
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True)
        self.assets_tab = AssetsTab(self.nb, app)
        self.markers_tab = MarkersTab(self.nb, app)
        self.timeline_tab = TimelineTab(self.nb, app)
        self.overlays_tab = OverlaysTab(self.nb, app)
        self.nb.add(self.markers_tab, text="Markers")
        self.nb.add(self.assets_tab, text="Assets")
        self.nb.add(self.timeline_tab, text="Timeline")
        self.nb.add(self.overlays_tab, text="Overlays & Music")

    def refresh(self, full: bool = True) -> None:
        """full=True after structural changes (assets/project); False for live timing updates."""
        if full:
            self.assets_tab.refresh()
            self.overlays_tab.refresh()
        self.markers_tab.refresh(rebuild=full)
        self.timeline_tab.refresh()

    def select_label(self, label: str) -> None:
        self.nb.select(self.markers_tab)
        self.markers_tab.select(label)


# ---------------------------------------------------------------------------
class ThumbView(ttk.Frame):
    """Source preview: shows a frame of a video/image at a time."""

    def __init__(self, master, width=320, height=180):
        super().__init__(master, style="Card.TFrame")
        self.W, self.H = width, height
        self.canvas = tk.Canvas(self, width=width, height=height, bg="#000", highlightthickness=0)
        self.canvas.pack()
        self._img = None

    def show(self, asset, t: float = 0.0) -> None:
        self.canvas.delete("all")
        if asset is None or not os.path.exists(asset.path):
            self.canvas.create_text(self.W // 2, self.H // 2, text="(no preview)", fill=FG_DIM)
            return
        try:
            if asset.meta.get("is_image"):
                from PIL import ImageOps

                with Image.open(asset.path) as im:
                    im = ImageOps.exif_transpose(im).convert("RGB")
                    im.thumbnail((self.W, self.H))
                    img = im.copy()
            elif asset.meta.get("has_video"):
                from ..preview import grab_frame

                w, h = asset.meta.get("width") or 16, asset.meta.get("height") or 9
                s = min(self.W / w, self.H / h)
                tw, th = max(2, int(w * s) // 2 * 2), max(2, int(h * s) // 2 * 2)
                img = Image.fromarray(grab_frame(asset.path, t, tw, th))
            else:
                self.canvas.create_text(self.W // 2, self.H // 2, text=f"♪ audio  {seconds_to_tc(t)}", fill=FG_DIM)
                return
        except Exception as exc:
            self.canvas.create_text(self.W // 2, self.H // 2, text=f"preview failed: {exc}"[:60], fill=RED)
            return
        self._img = ImageTk.PhotoImage(img)
        self.canvas.create_image(self.W // 2, self.H // 2, image=self._img)
        self.canvas.create_text(6, self.H - 6, anchor="sw", text=seconds_to_tc(t), fill="#fff")


# ---------------------------------------------------------------------------
class AssetsTab(ttk.Frame):
    def __init__(self, master, app: "App"):
        super().__init__(master)
        self.app = app
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=2)
        imp = ttk.Menubutton(bar, text="Import file…")
        m = tk.Menu(imp, tearoff=0)
        for k in ("soundbite", "standup", "broll", "image", "music", "sfx", "logo"):
            m.add_command(label=KIND_LABELS[k], command=lambda k=k: self.import_files(k))
        imp["menu"] = m
        imp.pack(side="left", padx=2)
        lib = ttk.Menubutton(bar, text="Add from library…")
        m2 = tk.Menu(lib, tearoff=0)
        for k in ("soundbite", "broll", "image", "music", "sfx", "logo"):
            m2.add_command(label=KIND_LABELS[k], command=lambda k=k: self.add_from_library(k))
        lib["menu"] = m2
        lib.pack(side="left", padx=2)
        ttk.Button(bar, text="Relink…", command=self.relink).pack(side="left", padx=2)
        ttk.Button(bar, text="Auto-relink", command=self.auto_relink).pack(side="left", padx=2)
        ttk.Button(bar, text="Remove", command=self.remove).pack(side="left", padx=2)

        pw = ttk.PanedWindow(self, orient="vertical")
        pw.pack(fill="both", expand=True)
        top = ttk.Frame(pw)
        cols = ("kind", "duration", "size", "audio", "status")
        self.tree = ttk.Treeview(top, columns=cols, show="tree headings", selectmode="browse", height=8)
        self.tree.heading("#0", text="File")
        self.tree.column("#0", width=200)
        for c, w in zip(cols, (80, 70, 90, 50, 80)):
            self.tree.heading(c, text=c.title())
            self.tree.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(top, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.tag_configure("missing", foreground=RED)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_detail())
        pw.add(top, weight=3)
        bottom = ttk.Frame(pw)
        self.thumb = ThumbView(bottom, 320, 180)
        self.thumb.pack(side="left", padx=4, pady=4)
        right = ttk.Frame(bottom)
        right.pack(side="left", fill="both", expand=True)
        self.meta_lbl = ttk.Label(right, text="", justify="left", wraplength=280)
        self.meta_lbl.pack(anchor="w")
        self.scrub = ttk.Scale(right, from_=0, to=1, orient="horizontal", command=self._scrub)
        self.scrub.pack(fill="x", pady=4)
        ttk.Button(right, text="Play audio", command=self.play_audio).pack(anchor="w")
        pw.add(bottom, weight=2)
        self._scrub_job = None

    def selected(self):
        sel = self.tree.selection()
        return self.app.project.asset(sel[0]) if sel else None

    def refresh(self) -> None:
        p = self.app.project
        sel = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        for a in p.assets:
            exists = os.path.exists(a.path)
            m = a.meta
            size = f"{m.get('width')}×{m.get('height')}" if m.get("width") else ""
            dur = fmt_seconds(m.get("duration"), 2) if m.get("duration") else ("still" if m.get("is_image") else "")
            self.tree.insert("", "end", iid=a.id, text=a.name,
                             values=(KIND_LABELS.get(a.kind, a.kind), dur, size, "yes" if m.get("has_audio") else "no",
                                     "OK" if exists else "MISSING"),
                             tags=() if exists else ("missing",))
        if sel and self.tree.exists(sel[0]):
            self.tree.selection_set(sel[0])

    def show_detail(self) -> None:
        a = self.selected()
        if a is None:
            return
        m = a.meta
        lines = [f"{a.name}", f"ID: {a.id}   Kind: {KIND_LABELS.get(a.kind, a.kind)}", f"Path: {a.path}"]
        if m.get("duration"):
            lines.append(f"Duration: {m['duration']:.3f} s ({seconds_to_tc(m['duration'])})")
        if m.get("width"):
            lines.append(f"Size: {m['width']}×{m['height']}" + (f"  @ {m.get('fps')} fps" if m.get("fps") else ""))
        if m.get("rotation"):
            lines.append(f"Rotation metadata: {m['rotation']}° (applied on decode)")
        if m.get("exif_rotated"):
            lines.append("EXIF orientation applied")
        if m.get("acodec"):
            lines.append(f"Audio: {m['acodec']} {m.get('sample_rate')} Hz, {m.get('channels')} ch")
        for w in m.get("warnings", []):
            lines.append(f"⚠ {w}")
        self.meta_lbl.configure(text="\n".join(lines))
        self.scrub.configure(to=max(0.01, m.get("duration") or 0.01))
        self.scrub.set(0)
        self.thumb.show(a, 0.0)

    def _scrub(self, _v=None):
        if self._scrub_job:
            self.after_cancel(self._scrub_job)
        self._scrub_job = self.after(200, lambda: self.thumb.show(self.selected(), float(self.scrub.get())))

    def play_audio(self):
        a = self.selected()
        if a is None:
            return
        self.app.play_audio_file(a.path, float(self.scrub.get()), 15.0)

    def import_files(self, kind: str) -> None:
        exts = sorted(A.KIND_EXT.get(kind, set()))
        start = self.app.config.paths.__dict__.get(A.LIBRARY_FOR_KIND.get(kind, ""), "") or str(Path.home())
        files = filedialog.askopenfilenames(parent=self, title=f"Import {KIND_LABELS[kind]}", initialdir=start,
                                            filetypes=[(KIND_LABELS[kind], " ".join("*" + e for e in exts)),
                                                       ("All files", "*")])
        self._import(list(files), kind)

    def _import(self, files, kind) -> list:
        added, errors = [], []
        for f in files:
            try:
                added.append(A.import_file(self.app.project, f, kind))
            except MediaError as exc:
                errors.append(str(exc))
        if added:
            self.app.mark_dirty()
            self.app.refresh_all()
            self.tree.selection_set(added[-1].id)
            self.tree.see(added[-1].id)
        if errors:
            messagebox.showerror("Import", "\n".join(errors), parent=self)
        return added

    def add_from_library(self, kind: str) -> None:
        folder = getattr(self.app.config.paths, A.LIBRARY_FOR_KIND[kind], "")
        if not folder or not os.path.isdir(folder):
            messagebox.showinfo("Library", f"No {KIND_LABELS[kind]} library folder is configured. Set it in "
                                           "Settings → Library paths.", parent=self)
            return
        files = A.list_library(folder, kind)
        if not files:
            messagebox.showinfo("Library", f"No supported files in {folder}.", parent=self)
            return
        LibraryPicker(self, folder, files, lambda chosen: self._import(chosen, kind))

    def relink(self) -> None:
        a = self.selected()
        if a is None:
            messagebox.showinfo("Relink", "Select an asset first.", parent=self)
            return
        f = filedialog.askopenfilename(parent=self, title=f"Relink {a.name}",
                                       initialdir=str(Path(a.path).parent) if Path(a.path).parent.exists() else None)
        if not f:
            return
        try:
            A.relink(a, f)
        except MediaError as exc:
            messagebox.showerror("Relink", str(exc), parent=self)
            return
        self.app.mark_dirty()
        self.app.refresh_all()
        warn = a.meta.get("warnings", [])
        if warn:
            messagebox.showwarning("Relink", "\n".join(warn), parent=self)

    def auto_relink(self) -> None:
        folders = list(self.app.config.paths.__dict__.values())
        if self.app.project_path:
            folders.append(str(self.app.project_path.parent))
        found = A.auto_relink(self.app.project, folders)
        missing = A.missing_assets(self.app.project)
        if found:
            self.app.mark_dirty()
            self.app.refresh_all()
        messagebox.showinfo("Auto-relink", f"Relinked {len(found)} file(s). Still missing: {len(missing)}.",
                            parent=self)

    def remove(self) -> None:
        a = self.selected()
        if a is None:
            return
        p = self.app.project
        used = [l for l, b in p.bindings.items() if b.asset_id == a.id or b.card_background_asset_id == a.id]
        if p.music.asset_id == a.id:
            used.append("music")
        if p.watermark.asset_id == a.id:
            used.append("watermark")
        used += [f"SFX @ {s.at_seconds:g}s" for s in p.sfx if s.asset_id == a.id]
        if used:
            messagebox.showwarning("Remove", f"{a.name} is used by: {', '.join(used)}. Unbind it first.", parent=self)
            return
        p.assets.remove(a)
        self.app.mark_dirty()
        self.app.refresh_all()


class LibraryPicker(tk.Toplevel):
    def __init__(self, master, folder, files, on_ok: Callable[[list], None]):
        super().__init__(master)
        self.title(f"Library — {folder}")
        self.files = files
        self.lb = tk.Listbox(self, selectmode="extended", width=70, height=20)
        for f in files:
            self.lb.insert("end", os.path.relpath(f, folder))
        self.lb.pack(fill="both", expand=True, padx=6, pady=6)
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=4)
        ttk.Button(bar, text="Add selected", style="Accent.TButton",
                   command=lambda: (on_ok([self.files[i] for i in self.lb.curselection()]), self.destroy())
                   ).pack(side="right", padx=6)
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="right")
        self.transient(master)


# ---------------------------------------------------------------------------
class MarkersTab(ttk.Frame):
    """Per-marker binding editor: asset, trims, policies, lower thirds, cards."""

    def __init__(self, master, app: "App"):
        super().__init__(master)
        self.app = app
        pw = ttk.PanedWindow(self, orient="horizontal")
        pw.pack(fill="both", expand=True)
        left = ttk.Frame(pw)
        self.tree = ttk.Treeview(left, columns=("status",), show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Marker")
        self.tree.heading("status", text="Binding")
        self.tree.column("#0", width=120)
        self.tree.column("status", width=90)
        self.tree.pack(fill="both", expand=True)
        self.tree.tag_configure("bad", foreground=RED)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        pw.add(left, weight=1)
        self.form_host = ScrollableFrame(pw)
        pw.add(self.form_host, weight=3)
        self.current: Optional[str] = None
        self._building = False

    def _on_select(self, _e=None):
        sel = self.tree.selection()
        if sel and sel[0] != self.current:
            self.build_form()

    def refresh(self, rebuild: bool = False) -> None:
        p = self.app.project
        labels = list(self.app.parse.labels) if self.app.parse else []
        # include bound labels no longer in the script (so they can be cleaned up)
        for l in p.bindings:
            if l not in labels:
                labels.append(l)
        sel = self.current
        self.tree.delete(*self.tree.get_children())
        for l in labels:
            b = p.bindings.get(l)
            status, bad = self._status(l, b)
            in_script = self.app.parse and l in self.app.parse.labels
            self.tree.insert("", "end", iid=l, text=l if in_script else f"{l} (unused)", values=(status,),
                             tags=("bad",) if bad and in_script else ())
        if sel and self.tree.exists(sel):
            self.tree.selection_set(sel)
            if rebuild:
                self.current = None
                self.build_form()
            else:
                self._refresh_values()
        elif sel and not self.tree.exists(sel):
            self.current = None
            self._clear_form()

    def _status(self, label, b) -> tuple[str, bool]:
        p = self.app.project
        if label.startswith("CARD_"):
            return ("card: " + (b.card_title[:14] if b and b.card_title else "(empty)")), not (b and b.card_title)
        if b is None or p.asset(b.asset_id) is None:
            return "unbound", True
        a = p.asset(b.asset_id)
        if a.meta.get("is_image"):
            return "still", False
        from ..timeline import binding_trim, trim_error

        tin, tout, src = binding_trim(p, b)
        err = trim_error(tin, tout, src)
        return ((f"{tout - tin:.2f} s" if not err else "bad trim"), bool(err))

    def select(self, label: str) -> None:
        if self.tree.exists(label):
            self.tree.selection_set(label)
            self.tree.see(label)

    def _clear_form(self):
        for w in self.form_host.body.winfo_children():
            w.destroy()

    # -- form -------------------------------------------------------------
    def build_form(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        label = sel[0]
        self.current = label
        self._clear_form()
        self._building = True
        p = self.app.project
        b = p.bindings.get(label) or Binding(label)
        kind = label.split("_")[0]
        f = self.form_host.body
        f.columnconfigure(1, weight=1, minsize=180)
        r = 0
        ttk.Label(f, text=label, style="Big.TLabel").grid(row=r, column=0, columnspan=3, sticky="w", padx=6, pady=4)
        r += 1
        self.vars: dict[str, tk.Variable] = {}
        self.err_lbl = ttk.Label(f, text="", foreground=RED, wraplength=380)

        def add(lab, widget, hint=""):
            nonlocal r
            ttk.Label(f, text=lab).grid(row=r, column=0, sticky="w", padx=6, pady=2)
            widget.grid(row=r, column=1, sticky="ew", padx=4, pady=2)
            if hint:
                ttk.Label(f, text=hint, style="Dim.TLabel", wraplength=110).grid(row=r, column=2, sticky="w")
            r += 1
            return widget

        if kind == "CARD":
            self.vars["card_title"] = tk.StringVar(value=b.card_title)
            add("Title", ttk.Entry(f, textvariable=self.vars["card_title"]))
            ttk.Label(f, text="Body / data (manually supplied)").grid(row=r, column=0, sticky="nw", padx=6)
            self.card_body = tk.Text(f, height=5, width=30, bg=BG2, fg="#eee", insertbackground="#eee", wrap="word")
            self.card_body.insert("1.0", b.card_body)
            self.card_body.grid(row=r, column=1, sticky="ew", padx=4, pady=2)
            self.card_body.bind("<KeyRelease>", lambda e: self.commit())
            r += 1
            self.vars["card_seconds"] = tk.StringVar(value=f"{b.card_seconds:g}")
            add("Seconds (standalone)", ttk.Entry(f, textvariable=self.vars["card_seconds"], width=8),
                "used when no narration follows")
            self.vars["card_color"] = tk.StringVar(value=b.card_color)
            cf = ttk.Frame(f)
            ttk.Entry(cf, textvariable=self.vars["card_color"], width=10).pack(side="left")
            ttk.Button(cf, text="Pick…", command=self._pick_color).pack(side="left", padx=4)
            add("Background colour", cf)
            names, self.bg_map = asset_choices(p, ("image",))
            self.vars["card_bg"] = tk.StringVar(value=label_for(p, b.card_background_asset_id))
            add("Background image", ttk.Combobox(f, values=names, textvariable=self.vars["card_bg"], state="readonly"))
        else:
            kinds = A.kind_for_label(label)
            names, self.asset_map = asset_choices(p, kinds)
            self.vars["asset"] = tk.StringVar(value=label_for(p, b.asset_id))
            add("Media", ttk.Combobox(f, values=names, textvariable=self.vars["asset"], state="readonly"),
                "/".join(KIND_LABELS[k] for k in kinds))
            a = p.asset(b.asset_id)
            src = a.meta.get("duration") if a else None
            self.src_lbl = ttk.Label(f, text="", style="Dim.TLabel")
            add("Source", self.src_lbl)
            self.vars["in"] = tk.StringVar(value=time_text(b.in_seconds, p.output.fps))
            self.vars["out"] = tk.StringVar(value="" if b.out_seconds is None else time_text(b.out_seconds, p.output.fps))
            tf = ttk.Frame(f)
            ttk.Entry(tf, textvariable=self.vars["in"], width=13).pack(side="left")
            ttk.Button(tf, text="Show", width=5, command=lambda: self._show("in")).pack(side="left", padx=2)
            add("In (source)", tf, "HH:MM:SS:FF or seconds")
            tf2 = ttk.Frame(f)
            ttk.Entry(tf2, textvariable=self.vars["out"], width=13).pack(side="left")
            ttk.Button(tf2, text="Show", width=5, command=lambda: self._show("out")).pack(side="left", padx=2)
            add("Out (source)", tf2, "blank = end of source")
            self.dur_lbl = ttk.Label(f, text="", style="Head.TLabel")
            add("Selected duration", self.dur_lbl)
            self.thumb = ThumbView(f, 288, 162)
            self.thumb.grid(row=r, column=0, columnspan=3, pady=4)
            r += 1
            if kind in ("SOUNDBITE", "STANDUP"):
                ttk.Button(f, text="▶ Play selected range", command=self._play_range).grid(row=r, column=1, sticky="w")
                r += 1
            self.vars["resize"] = tk.StringVar(value=RESIZE_LABELS.get(b.resize_mode, "Project default")
                                               if b.resize_mode else "Project default")
            add("Resize", ttk.Combobox(f, values=["Project default"] + list(RESIZE_LABELS.values()),
                                       textvariable=self.vars["resize"], state="readonly"))
            self.vars["crop_x"] = tk.DoubleVar(value=b.crop_x)
            self.vars["crop_y"] = tk.DoubleVar(value=b.crop_y)
            add("Crop framing X", ttk.Scale(f, from_=-1, to=1, variable=self.vars["crop_x"],
                                            command=lambda v: self.commit()), "left ← → right")
            add("Crop framing Y", ttk.Scale(f, from_=-1, to=1, variable=self.vars["crop_y"],
                                            command=lambda v: self.commit()), "top ← → bottom")
            if kind in ("BROLL", "IMAGE"):
                self.vars["fill"] = tk.StringVar(value=FILL_LABELS[b.fill_policy])
                add("If narration outlasts it", ttk.Combobox(f, values=list(FILL_LABELS.values()),
                                                             textvariable=self.vars["fill"], state="readonly"))
                self.vars["amb_on"] = tk.BooleanVar(value=b.ambience_db is not None)
                self.vars["amb_db"] = tk.StringVar(value=f"{b.ambience_db if b.ambience_db is not None else -18:g}")
                af = ttk.Frame(f)
                ttk.Checkbutton(af, text="Use source ambience at", variable=self.vars["amb_on"],
                                command=self.commit).pack(side="left")
                ttk.Entry(af, textvariable=self.vars["amb_db"], width=6).pack(side="left", padx=2)
                ttk.Label(af, text="dB").pack(side="left")
                add("B-roll audio", af, "muted by default")
            if kind in ("SOUNDBITE", "STANDUP"):
                self.vars["gain"] = tk.StringVar(value=f"{b.gain_db:g}")
                add("Speech gain (dB)", ttk.Entry(f, textvariable=self.vars["gain"], width=6))
                self.vars["silent"] = tk.BooleanVar(value=b.silent)
                add("", ttk.Checkbutton(f, text="Deliberately silent statement", variable=self.vars["silent"],
                                        command=self.commit))
                self.vars["lang"] = tk.StringVar(value=b.language)
                add("Speech language", ttk.Combobox(f, values=["el", "en", "fr", "de", "it", "es", "tr"],
                                                    textvariable=self.vars["lang"], width=6))
                names, self.bg_map = asset_choices(p, ("image",))
                self.vars["card_bg"] = tk.StringVar(value=label_for(p, b.card_background_asset_id))
                add("Still (audio-only source)", ttk.Combobox(f, values=names, textvariable=self.vars["card_bg"],
                                                              state="readonly"))
                lt = p.lower_third_for(label) or LowerThird(label)
                ttk.Separator(f).grid(row=r, column=0, columnspan=3, sticky="ew", pady=6)
                r += 1
                ttk.Label(f, text="Lower third", style="Head.TLabel").grid(row=r, column=0, sticky="w", padx=6)
                r += 1
                self.vars["lt_name"] = tk.StringVar(value=lt.name)
                self.vars["lt_role"] = tk.StringVar(value=lt.role)
                self.vars["lt_start"] = tk.StringVar(value=f"{lt.start_offset:g}")
                self.vars["lt_end"] = tk.StringVar(value="" if lt.end_offset is None else f"{lt.end_offset:g}")
                self.vars["lt_fade"] = tk.StringVar(value=f"{lt.fade_seconds:g}")
                add("Name", ttk.Entry(f, textvariable=self.vars["lt_name"]))
                add("Role", ttk.Entry(f, textvariable=self.vars["lt_role"]))
                add("Show from (s)", ttk.Entry(f, textvariable=self.vars["lt_start"], width=6), "after statement start")
                add("Hide at (s)", ttk.Entry(f, textvariable=self.vars["lt_end"], width=6), "blank = statement end")
                add("Fade (s)", ttk.Entry(f, textvariable=self.vars["lt_fade"], width=6))
                ttk.Separator(f).grid(row=r, column=0, columnspan=3, sticky="ew", pady=6)
                r += 1
                sf = ttk.Frame(f)
                ttk.Button(sf, text="Transcribe (local Whisper)",
                           command=lambda: self.app.transcribe([label])).pack(side="left")
                ttk.Button(sf, text="Edit subtitles…", command=lambda: self.app.open_cue_editor(label)).pack(
                    side="left", padx=4)
                add("Subtitles", sf)
        self.err_lbl.grid(row=r, column=0, columnspan=3, sticky="w", padx=6, pady=4)
        r += 1
        ttk.Button(f, text="Unbind / reset", command=self.unbind).grid(row=r, column=1, sticky="w", padx=4, pady=6)
        for k, v in self.vars.items():
            if k not in ("crop_x", "crop_y", "silent", "amb_on"):
                v.trace_add("write", lambda *_: self.commit())
        self._building = False
        self._refresh_values()
        if "asset" in self.vars:
            self._show("in")

    def _pick_color(self):
        c = colorchooser.askcolor(self.vars["card_color"].get(), parent=self)
        if c and c[1]:
            self.vars["card_color"].set(c[1])

    def _refresh_values(self):
        """Update read-only labels (source duration, selected duration)."""
        if not self.current or "asset" not in getattr(self, "vars", {}):
            return
        p = self.app.project
        b = p.bindings.get(self.current)
        a = p.asset(b.asset_id) if b else None
        if a is None:
            self.src_lbl.configure(text="(no media)")
            self.dur_lbl.configure(text="—")
            return
        src = a.meta.get("duration")
        self.src_lbl.configure(text=("still image" if a.meta.get("is_image") else
                                     f"{src:.3f} s  ({seconds_to_tc(src, p.output.fps)})" if src else "unknown duration"))
        from ..timeline import binding_trim, trim_error

        tin, tout, srcd = binding_trim(p, b)
        if a.meta.get("is_image"):
            self.dur_lbl.configure(text="still (follows narration)")
            return
        err = trim_error(tin, tout, srcd)
        self.dur_lbl.configure(text=err if err else f"{tout - tin:.3f} s  ({seconds_to_tc(tout - tin, p.output.fps)})",
                               foreground=RED if err else "")

    def _show(self, which: str):
        p = self.app.project
        b = p.bindings.get(self.current or "")
        a = p.asset(b.asset_id) if b else None
        if a is None or not hasattr(self, "thumb"):
            return
        t = b.in_seconds if which == "in" else (b.out_seconds if b.out_seconds is not None else
                                                (a.meta.get("duration") or 0) - 0.04)
        self.thumb.show(a, max(0.0, t))

    def _play_range(self):
        p = self.app.project
        b = p.bindings.get(self.current or "")
        a = p.asset(b.asset_id) if b else None
        if a is None:
            return
        from ..timeline import binding_trim

        tin, tout, _ = binding_trim(p, b)
        if tin is not None and tout is not None and tout > tin:
            self.app.play_audio_file(a.path, tin, tout - tin)

    def commit(self) -> None:
        if self._building or not self.current:
            return
        p = self.app.project
        label = self.current
        b = p.binding(label)
        v = self.vars
        errs = []
        fps = p.output.fps
        try:
            if "card_title" in v:
                b.card_title = v["card_title"].get()
                b.card_body = self.card_body.get("1.0", "end-1c")
                try:
                    s = float(v["card_seconds"].get().replace(",", "."))
                    if s <= 0:
                        raise ValueError
                    b.card_seconds = s
                except ValueError:
                    errs.append("Card seconds must be a positive number.")
                b.card_color = v["card_color"].get()
                b.card_background_asset_id = self.bg_map.get(v["card_bg"].get())
            if "asset" in v:
                new_id = self.asset_map.get(v["asset"].get())
                if new_id != b.asset_id:
                    b.asset_id = new_id
                    a = p.asset(new_id)
                    if a is not None and b.out_seconds is not None and a.meta.get("duration") \
                            and b.out_seconds > a.meta["duration"]:
                        errs.append("Out-point is beyond the new source; it was kept for you to correct.")
                try:
                    b.in_seconds = parse_time(v["in"].get() or "0", fps)
                except ValueError as exc:
                    errs.append(f"In: {exc}")
                out_txt = v["out"].get().strip()
                try:
                    b.out_seconds = parse_time(out_txt, fps) if out_txt else None
                except ValueError as exc:
                    errs.append(f"Out: {exc}")
                rz = v["resize"].get()
                inv = {lab: k for k, lab in RESIZE_LABELS.items()}
                b.resize_mode = inv.get(rz)
                b.crop_x = round(float(v["crop_x"].get()), 3)
                b.crop_y = round(float(v["crop_y"].get()), 3)
            if "fill" in v:
                inv = {lab: k for k, lab in FILL_LABELS.items()}
                b.fill_policy = inv.get(v["fill"].get(), "none")
                if v["amb_on"].get():
                    try:
                        b.ambience_db = float(v["amb_db"].get())
                    except ValueError:
                        errs.append("Ambience level must be a number in dB.")
                else:
                    b.ambience_db = None
            if "gain" in v:
                try:
                    b.gain_db = float(v["gain"].get())
                except ValueError:
                    errs.append("Speech gain must be a number in dB.")
                b.silent = bool(v["silent"].get())
                b.language = v["lang"].get().strip() or "el"
                b.card_background_asset_id = self.bg_map.get(v["card_bg"].get())
                lt = p.lower_third_for(label)
                name, role = v["lt_name"].get(), v["lt_role"].get()
                if lt is None and (name or role):
                    lt = LowerThird(label)
                    p.lower_thirds.append(lt)
                if lt is not None:
                    lt.name, lt.role = name, role
                    try:
                        lt.start_offset = float(v["lt_start"].get() or 0)
                        e = v["lt_end"].get().strip()
                        lt.end_offset = float(e) if e else None
                        lt.fade_seconds = float(v["lt_fade"].get() or 0)
                    except ValueError:
                        errs.append("Lower-third times must be numbers in seconds.")
        except tk.TclError:
            return
        self.err_lbl.configure(text="\n".join(errs))
        self.app.mark_dirty()
        self.app.schedule_refresh()
        self._refresh_values()
        status, bad = self._status(label, b)
        if self.tree.exists(label):
            self.tree.item(label, values=(status,), tags=("bad",) if bad else ())

    def unbind(self):
        if not self.current:
            return
        p = self.app.project
        p.bindings.pop(self.current, None)
        p.lower_thirds = [lt for lt in p.lower_thirds if lt.binding_label != self.current]
        self.app.mark_dirty()
        self.current = None
        self.app.refresh_all()


# ---------------------------------------------------------------------------
class TimelineTab(ttk.Frame):
    def __init__(self, master, app: "App"):
        super().__init__(master)
        self.app = app
        cols = ("start", "dur", "visual", "audio", "status")
        self.tree = ttk.Treeview(self, columns=cols, show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Segment")
        self.tree.column("#0", width=120)
        for c, t, w in zip(cols, ("Start", "Duration", "Visual", "Audio", "Timing"), (90, 70, 140, 120, 90)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w)
        sb = ttk.Scrollbar(self, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.tag_configure("stmt", foreground="#ffcf70")
        self.tree.tag_configure("est", foreground=FG_DIM)
        self.tree.bind("<Double-1>", self._goto)

    def refresh(self) -> None:
        tl = self.app.timeline
        p = self.app.project
        self.tree.delete(*self.tree.get_children())
        if tl is None:
            return
        for s in tl.segments:
            a = p.asset(s.visual_asset_id)
            vis = a.name if a else (s.label if s.label.startswith("CARD_") else "—")
            if s.kind == "narration":
                aud = "narration" if s.narration and not s.stale else ("STALE audio" if s.stale else "no audio yet")
            elif s.kind in ("soundbite", "standup"):
                aud = "original speech"
            else:
                aud = "music/ambience"
            timing = "estimated" if s.estimated else "measured"
            self.tree.insert("", "end", iid=str(s.index), text=s.name(),
                             values=(seconds_to_tc(s.start_frame / tl.fps, tl.fps),
                                     f"{s.duration_frames / tl.fps:.2f}s", vis, aud, timing),
                             tags=("stmt",) if s.kind in ("soundbite", "standup") else (("est",) if s.estimated else ()))
        self.tree.insert("", "end", iid="total", text="TOTAL",
                         values=(seconds_to_tc(tl.total_frames / tl.fps, tl.fps), f"{tl.total_frames / tl.fps:.2f}s",
                                 "", "", "estimated" if not tl.all_measured else "measured"))

    def _goto(self, _e=None):
        sel = self.tree.selection()
        if not sel or sel[0] == "total" or self.app.timeline is None:
            return
        s = self.app.timeline.segments[int(sel[0])]
        self.app.goto_script(s.char_start, s.char_end)
        self.app.preview.seek_seconds(s.start_frame / self.app.timeline.fps)


# ---------------------------------------------------------------------------
class OverlaysTab(ScrollableFrame):
    def __init__(self, master, app: "App"):
        super().__init__(master)
        self.app = app
        self._building = False
        self.vars: dict[str, tk.Variable] = {}
        self.build()

    def build(self):
        f = self.body
        for w in f.winfo_children():
            w.destroy()
        f.columnconfigure(1, weight=1, minsize=180)
        p = self.app.project
        self._building = True
        r = 0

        def head(t):
            nonlocal r
            ttk.Label(f, text=t, style="Head.TLabel").grid(row=r, column=0, columnspan=3, sticky="w", padx=6,
                                                           pady=(10, 2))
            r += 1

        def add(lab, widget, hint=""):
            nonlocal r
            ttk.Label(f, text=lab).grid(row=r, column=0, sticky="w", padx=6, pady=2)
            widget.grid(row=r, column=1, sticky="ew", padx=4, pady=2)
            if hint:
                ttk.Label(f, text=hint, style="Dim.TLabel", wraplength=110).grid(row=r, column=2, sticky="w")
            r += 1
            return widget

        V = self.vars
        head("Programme watermark")
        names, self.logo_map = asset_choices(p, ("logo", "image"))
        V["wm"] = tk.StringVar(value=label_for(p, p.watermark.asset_id))
        self.wm_combo = add("Logo", ttk.Combobox(f, values=names, textvariable=V["wm"], state="readonly"),
                            "top-right, inside safe area")
        V["wm_w"] = tk.DoubleVar(value=p.watermark.width_ratio)
        add("Width (fraction)", ttk.Spinbox(f, from_=0.03, to=0.4, increment=0.01, textvariable=V["wm_w"], width=6))
        V["wm_o"] = tk.DoubleVar(value=p.watermark.opacity)
        add("Opacity", ttk.Spinbox(f, from_=0.1, to=1.0, increment=0.05, textvariable=V["wm_o"], width=6))
        head("Opening title (manually supplied)")
        V["ot_on"] = tk.BooleanVar(value=p.opening_title.enabled)
        add("", ttk.Checkbutton(f, text="Show opening title", variable=V["ot_on"]))
        V["ot_text"] = tk.StringVar(value=p.opening_title.text)
        add("Title", ttk.Entry(f, textvariable=V["ot_text"]))
        V["ot_sub"] = tk.StringVar(value=p.opening_title.subtitle)
        add("Subtitle", ttk.Entry(f, textvariable=V["ot_sub"]))
        V["ot_start"] = tk.StringVar(value=f"{p.opening_title.start:g}")
        add("Start (s)", ttk.Entry(f, textvariable=V["ot_start"], width=6))
        V["ot_dur"] = tk.StringVar(value=f"{p.opening_title.duration:g}")
        add("Duration (s)", ttk.Entry(f, textvariable=V["ot_dur"], width=6))
        head("Background music")
        names, self.music_map = asset_choices(p, ("music",))
        V["music"] = tk.StringVar(value=label_for(p, p.music.asset_id))
        mf = ttk.Frame(f)
        ttk.Combobox(mf, values=names, textvariable=V["music"], state="readonly").pack(side="left", fill="x",
                                                                                      expand=True)
        ttk.Button(mf, text="Auto (seeded)", command=self.auto_music).pack(side="left", padx=4)
        add("Track", mf)
        self.seed_lbl = ttk.Label(f, text=(f"Automatic choice, seed {p.music.auto_seed}" if p.music.auto_seed is not None
                                           else "Explicit selection"), style="Dim.TLabel")
        add("", self.seed_lbl)
        V["music_db"] = tk.DoubleVar(value=p.music.base_db)
        self.music_db_lbl = ttk.Label(f, text=f"{p.music.base_db:.0f} dBFS")
        sc = ttk.Scale(f, from_=-40, to=-6, variable=V["music_db"],
                       command=lambda v: self.music_db_lbl.configure(text=f"{float(v):.0f} dBFS"))
        add("Base level (in pauses)", sc)
        add("", self.music_db_lbl)
        V["music_loop"] = tk.BooleanVar(value=p.music.loop)
        add("", ttk.Checkbutton(f, text="Loop with crossfade if shorter than the package", variable=V["music_loop"]))
        head("Sound effects (explicit placements)")
        self.sfx_tree = ttk.Treeview(f, columns=("at", "gain"), show="tree headings", height=4)
        self.sfx_tree.heading("#0", text="SFX")
        self.sfx_tree.heading("at", text="At (s)")
        self.sfx_tree.heading("gain", text="Gain dB")
        self.sfx_tree.column("at", width=60)
        self.sfx_tree.column("gain", width=60)
        self.sfx_tree.grid(row=r, column=0, columnspan=2, sticky="ew", padx=6)
        r += 1
        sf = ttk.Frame(f)
        names, self.sfx_map = asset_choices(p, ("sfx",))
        V["sfx_asset"] = tk.StringVar(value=names[1] if len(names) > 1 else names[0])
        ttk.Combobox(sf, values=names, textvariable=V["sfx_asset"], state="readonly", width=22).pack(side="left")
        V["sfx_at"] = tk.StringVar(value="0")
        V["sfx_gain"] = tk.StringVar(value="-12")
        ttk.Label(sf, text="at").pack(side="left", padx=2)
        ttk.Entry(sf, textvariable=V["sfx_at"], width=6).pack(side="left")
        ttk.Label(sf, text="s, gain").pack(side="left", padx=2)
        ttk.Entry(sf, textvariable=V["sfx_gain"], width=5).pack(side="left")
        ttk.Button(sf, text="Add", command=self.add_sfx).pack(side="left", padx=4)
        ttk.Button(sf, text="Remove", command=self.remove_sfx).pack(side="left")
        sf.grid(row=r, column=0, columnspan=3, sticky="w", padx=6, pady=2)
        r += 1
        head("Output folder")
        V["export_dir"] = tk.StringVar(value=p.export_directory)
        ef = ttk.Frame(f)
        ttk.Entry(ef, textvariable=V["export_dir"]).pack(side="left", fill="x", expand=True)
        ttk.Button(ef, text="Browse…", command=self.pick_export).pack(side="left", padx=4)
        add("Export directory", ef)
        for k, var in V.items():
            if not k.startswith("sfx_"):
                var.trace_add("write", lambda *_: self.commit())
        self._building = False
        self.refresh_sfx()

    def refresh(self):
        # Rebuild choices when assets change (keeps current values)
        self.build()

    def refresh_sfx(self):
        p = self.app.project
        self.sfx_tree.delete(*self.sfx_tree.get_children())
        for i, s in enumerate(p.sfx):
            a = p.asset(s.asset_id)
            self.sfx_tree.insert("", "end", iid=str(i), text=a.name if a else "(missing)",
                                 values=(f"{s.at_seconds:g}", f"{s.gain_db:g}"))

    def add_sfx(self):
        aid = self.sfx_map.get(self.vars["sfx_asset"].get())
        if not aid:
            messagebox.showinfo("SFX", "Import an SFX asset first (Assets → Import file → SFX).", parent=self)
            return
        try:
            at, gain = float(self.vars["sfx_at"].get()), float(self.vars["sfx_gain"].get())
            if at < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("SFX", "Position and gain must be numbers (position ≥ 0).", parent=self)
            return
        self.app.project.sfx.append(SfxPlacement(aid, at, gain))
        self.app.mark_dirty()
        self.refresh_sfx()
        self.app.schedule_refresh()

    def remove_sfx(self):
        sel = self.sfx_tree.selection()
        if sel:
            del self.app.project.sfx[int(sel[0])]
            self.app.mark_dirty()
            self.refresh_sfx()
            self.app.schedule_refresh()

    def auto_music(self):
        folder = self.app.config.paths.music
        files = A.list_library(folder, "music") if folder else []
        if not files:
            messagebox.showinfo("Music", "Configure a music library folder with audio files first "
                                         "(Settings → Library paths).", parent=self)
            return
        seed = random.randint(1, 999999)
        choice = A.seeded_choice(files, seed)
        try:
            a = A.import_file(self.app.project, choice, "music")
        except MediaError as exc:
            messagebox.showerror("Music", str(exc), parent=self)
            return
        self.app.project.music.asset_id = a.id
        self.app.project.music.auto_seed = seed
        self.app.mark_dirty()
        self.app.refresh_all()
        messagebox.showinfo("Music", f"Selected {a.name} (seed {seed}). The choice is saved in the project and is "
                                     "not changed at render time.", parent=self)

    def pick_export(self):
        d = filedialog.askdirectory(parent=self, title="Export folder",
                                    initialdir=self.vars["export_dir"].get() or self.app.config.paths.exports or None)
        if d:
            self.vars["export_dir"].set(d)

    def commit(self):
        if self._building:
            return
        p = self.app.project
        V = self.vars
        try:
            p.watermark.asset_id = self.logo_map.get(V["wm"].get())
            p.watermark.width_ratio = max(0.03, min(0.4, float(V["wm_w"].get())))
            p.watermark.opacity = max(0.1, min(1.0, float(V["wm_o"].get())))
            ot = p.opening_title
            ot.enabled = bool(V["ot_on"].get())
            ot.text, ot.subtitle = V["ot_text"].get(), V["ot_sub"].get()
            ot.start = max(0.0, float(V["ot_start"].get() or 0))
            ot.duration = max(0.5, float(V["ot_dur"].get() or 4))
            new_music = self.music_map.get(V["music"].get())
            if new_music != p.music.asset_id:
                p.music.asset_id = new_music
                p.music.auto_seed = None
                self.seed_lbl.configure(text="Explicit selection")
            p.music.base_db = round(float(V["music_db"].get()), 1)
            p.music.loop = bool(V["music_loop"].get())
            p.export_directory = V["export_dir"].get().strip()
        except (ValueError, tk.TclError):
            return
        self.app.mark_dirty()
        self.app.schedule_refresh()
