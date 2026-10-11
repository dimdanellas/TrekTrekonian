"""Persistent application settings (separate from per-project settings)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .model import ProjectLoadError, from_dict, to_dict
from .util import atomic_write_json, user_cache_dir, user_config_dir

DEFAULT_PROMPTS = [
    {"name": "Δομή ρεπορτάζ 2 λεπτών",
     "text": "Γράψε σενάριο τηλεοπτικού ρεπορτάζ περίπου 120 δευτερολέπτων για το θέμα: {θέμα}.\n"
             "Χρησιμοποίησε τους δείκτες [BROLL_n], [IMAGE_n], [SOUNDBITE_n].\n"
             "Αφήνω χρόνο για δηλώσεις διάρκειας {διάρκεια_δηλώσεων} δευτερολέπτων συνολικά."},
    {"name": "Σύντομη εισαγωγή παρουσιαστή",
     "text": "Γράψε εισαγωγή παρουσιαστή 2–3 προτάσεων για το ρεπορτάζ: {τίτλος}."},
    {"name": "Έλεγχος ουδετερότητας",
     "text": "Έλεγξε το παρακάτω κείμενο για ισορροπία και ουδέτερη διατύπωση:\n{κείμενο}"},
]


@dataclass
class LibraryPaths:
    sfx: str = ""
    music: str = ""
    broll: str = ""
    images: str = ""
    soundbites: str = ""
    exports: str = ""


@dataclass
class AppConfig:
    version: int = 1
    paths: LibraryPaths = field(default_factory=LibraryPaths)
    default_wpm: float = 140.0
    default_target_seconds: float = 120.0
    default_profile: str = "web_horizontal"
    default_resize_mode: str = "blur"
    autosave_seconds: int = 60
    recent_projects: list[str] = field(default_factory=list)
    prompts: list[dict] = field(default_factory=lambda: [dict(p) for p in DEFAULT_PROMPTS])
    custom_profiles: dict = field(default_factory=dict)
    last_project: str = ""
    ui_geometry: str = "1100x700"
    external_player: str = ""  # optional override; else ffplay / OS default

    @staticmethod
    def path() -> Path:
        return user_config_dir() / "settings.json"

    @classmethod
    def load(cls) -> tuple["AppConfig", Optional[str]]:
        """Return (config, warning). Malformed files are preserved and defaults used."""
        p = cls.path()
        if not p.exists():
            return cls(), None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            cfg = from_dict(cls, data, "settings")
            return cfg, None
        except (OSError, json.JSONDecodeError, ProjectLoadError) as exc:
            backup = p.with_suffix(".broken.json")
            try:
                p.replace(backup)
                note = f" The unreadable file was kept as {backup.name}."
            except OSError:
                note = ""
            return cls(), f"Settings file could not be read ({exc}). Defaults are in use.{note}"

    def save(self) -> None:
        atomic_write_json(self.path(), to_dict(self))

    def add_recent(self, path: str) -> None:
        if path in self.recent_projects:
            self.recent_projects.remove(path)
        self.recent_projects.insert(0, path)
        del self.recent_projects[10:]

    def validate_paths(self) -> list[str]:
        problems = []
        for name, value in to_dict(self.paths).items():
            if value and not Path(value).is_dir():
                problems.append(f"Library folder for {name} does not exist: {value}")
        return problems


def autosave_dir() -> Path:
    return user_config_dir() / "autosave"


def cache_root() -> Path:
    return user_cache_dir()
