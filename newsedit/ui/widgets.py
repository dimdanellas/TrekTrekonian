"""Dark theme and reusable Tkinter widgets."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

BG = "#1e1f22"
BG2 = "#26282c"
BG3 = "#2f3237"
FG = "#e6e6e6"
FG_DIM = "#9aa0a6"
ACCENT = "#3d7eff"
RED = "#ff5c5c"
YELLOW = "#ffd24d"
GREEN = "#5fd38d"
OVERRUN_BG = "#4a2323"
SCRIPT_BG = "#17181a"
MONO = ("DejaVu Sans Mono", 11)


def apply_dark_theme(root: tk.Tk) -> ttk.Style:
    style = ttk.Style(root)
    style.theme_use("clam")
    root.configure(bg=BG)
    style.configure(".", background=BG, foreground=FG, fieldbackground=BG3, bordercolor=BG3, darkcolor=BG2,
                    lightcolor=BG3, troughcolor=BG2, selectbackground=ACCENT, selectforeground="#ffffff",
                    insertcolor=FG)
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=BG2)
    style.configure("TLabel", background=BG, foreground=FG)
    style.configure("Dim.TLabel", foreground=FG_DIM)
    style.configure("Card.TLabel", background=BG2)
    style.configure("Head.TLabel", font=("TkDefaultFont", 10, "bold"))
    style.configure("Big.TLabel", font=("TkDefaultFont", 13, "bold"))
    style.configure("Red.TLabel", foreground=RED, font=("TkDefaultFont", 13, "bold"))
    style.configure("Green.TLabel", foreground=GREEN, font=("TkDefaultFont", 13, "bold"))
    style.configure("Warn.TLabel", foreground=YELLOW, font=("TkDefaultFont", 10, "bold"))
    style.configure("TLabelframe", background=BG, bordercolor=BG3)
    style.configure("TLabelframe.Label", background=BG, foreground=FG_DIM)
    style.configure("TButton", background=BG3, foreground=FG, padding=(8, 3))
    style.map("TButton", background=[("active", "#3a3e44"), ("disabled", BG2)], foreground=[("disabled", "#666")])
    style.configure("Small.TButton", padding=(3, 1))
    style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff")
    style.map("Accent.TButton", background=[("active", "#5b93ff"), ("disabled", BG2)])
    style.configure("Danger.TButton", background="#7a2a2a")
    style.configure("TCheckbutton", background=BG, foreground=FG)
    style.map("TCheckbutton", background=[("active", BG)])
    style.configure("TRadiobutton", background=BG, foreground=FG)
    style.map("TRadiobutton", background=[("active", BG)])
    style.configure("TEntry", fieldbackground=BG3, foreground=FG, insertcolor=FG)
    style.configure("TSpinbox", fieldbackground=BG3, foreground=FG, arrowcolor=FG)
    style.configure("TCombobox", fieldbackground=BG3, foreground=FG, arrowcolor=FG)
    style.map("TCombobox", fieldbackground=[("readonly", BG3)], foreground=[("readonly", FG)])
    style.configure("TNotebook", background=BG, bordercolor=BG3)
    style.configure("TNotebook.Tab", background=BG2, foreground=FG_DIM, padding=(10, 4))
    style.map("TNotebook.Tab", background=[("selected", BG3)], foreground=[("selected", FG)])
    style.configure("Treeview", background=BG2, fieldbackground=BG2, foreground=FG, rowheight=22, bordercolor=BG3)
    style.configure("Treeview.Heading", background=BG3, foreground=FG)
    style.map("Treeview", background=[("selected", ACCENT)])
    style.configure("TPanedwindow", background=BG)
    style.configure("Sash", sashthickness=6, background=BG3)
    style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=BG2)
    style.configure("Level.Horizontal.TProgressbar", background=GREEN, troughcolor=BG2)
    style.configure("Horizontal.TScale", background=BG)
    root.option_add("*TCombobox*Listbox.background", BG3)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*Menu.background", BG2)
    root.option_add("*Menu.foreground", FG)
    root.option_add("*Menu.activeBackground", ACCENT)
    root.option_add("*Listbox.background", BG2)
    root.option_add("*Listbox.foreground", FG)
    return style


class ScrollableFrame(ttk.Frame):
    """Vertically scrollable container; put widgets into ``.body``."""

    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0, bd=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.body = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vsb.pack(side="right", fill="y")
        self.body.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        for w in (self.canvas, self.body):
            w.bind("<Enter>", self._bind_wheel)
            w.bind("<Leave>", self._unbind_wheel)

    def _bind_wheel(self, _e=None):
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        self.canvas.bind_all("<Button-4>", lambda e: self.canvas.yview_scroll(-3, "units"))
        self.canvas.bind_all("<Button-5>", lambda e: self.canvas.yview_scroll(3, "units"))

    def _unbind_wheel(self, _e=None):
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")

    def _on_wheel(self, e):
        self.canvas.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units")


def row(parent, label: str, widget_factory: Callable[[tk.Widget], tk.Widget], r: int, col: int = 0,
        hint: str = "") -> tk.Widget:
    ttk.Label(parent, text=label).grid(row=r, column=col, sticky="w", padx=(6, 4), pady=2)
    w = widget_factory(parent)
    w.grid(row=r, column=col + 1, sticky="ew", padx=(0, 6), pady=2)
    if hint:
        ttk.Label(parent, text=hint, style="Dim.TLabel").grid(row=r, column=col + 2, sticky="w", padx=(0, 6))
    return w


class Tooltip:
    def __init__(self, widget: tk.Widget, text: str):
        self.widget, self.text, self.tip = widget, text, None
        widget.bind("<Enter>", self.show)
        widget.bind("<Leave>", self.hide)

    def show(self, _e=None):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 16
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=self.text, bg="#33363b", fg=FG, justify="left", wraplength=360, padx=6,
                 pady=4).pack()

    def hide(self, _e=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


def text_dialog(parent, title: str, text: str, width: int = 90, height: int = 30) -> tk.Toplevel:
    top = tk.Toplevel(parent)
    top.title(title)
    top.configure(bg=BG)
    t = tk.Text(top, wrap="word", width=width, height=height, bg=SCRIPT_BG, fg=FG, insertbackground=FG,
                relief="flat", padx=10, pady=10)
    sb = ttk.Scrollbar(top, command=t.yview)
    t.configure(yscrollcommand=sb.set)
    t.insert("1.0", text)
    t.configure(state="disabled")
    t.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    top.transient(parent)
    return top


def set_state(widgets, enabled: bool) -> None:
    for w in widgets:
        try:
            w.configure(state=("normal" if enabled else "disabled"))
        except tk.TclError:
            try:
                w.state(["!disabled"] if enabled else ["disabled"])
            except Exception:
                pass
