"""Asset discovery, import, probing and relinking with stable IDs.

Asset IDs are generated once when a file enters the project and stored in the
project file.  Rescanning a folder matches existing files by their resolved
path, so IDs never depend on directory listing order.  Source media are never
modified.
"""
from __future__ import annotations

import os
import random
from pathlib import Path
from typing import Iterable, Optional

from .media import AUDIO_EXT, IMAGE_EXT, VIDEO_EXT, MediaError, file_kind, probe
from .model import Asset, Project, new_asset_id

KIND_EXT = {
    "soundbite": VIDEO_EXT | AUDIO_EXT,
    "standup": VIDEO_EXT,
    "broll": VIDEO_EXT | IMAGE_EXT,
    "image": IMAGE_EXT,
    "logo": IMAGE_EXT,
    "music": AUDIO_EXT | VIDEO_EXT,
    "sfx": AUDIO_EXT,
    "narration": AUDIO_EXT,
}

LIBRARY_FOR_KIND = {"sfx": "sfx", "music": "music", "broll": "broll", "image": "images", "logo": "images",
                    "soundbite": "soundbites", "standup": "soundbites"}


def _norm(path: str | Path) -> str:
    try:
        return os.path.normcase(str(Path(path).resolve()))
    except OSError:
        return os.path.normcase(os.path.abspath(str(path)))


def find_by_path(project: Project, path: str | Path, kind: Optional[str] = None) -> Optional[Asset]:
    n = _norm(path)
    for a in project.assets:
        if _norm(a.path) == n and (kind is None or a.kind == kind):
            return a
    return None


def import_file(project: Project, path: str | Path, kind: str) -> Asset:
    """Add a file (or return the existing asset for the same path and kind)."""
    path = Path(path)
    existing = find_by_path(project, path, kind)
    if existing is not None:
        return existing
    ext = path.suffix.lower()
    if kind in KIND_EXT and ext not in KIND_EXT[kind]:
        raise MediaError(f"{path.name}: {ext or 'no extension'} is not a supported {kind} format.")
    meta = probe(path)
    if kind in ("soundbite", "standup") and not meta.get("has_audio"):
        meta.setdefault("warnings", []).append("No audio stream: statement speech will be missing "
                                               "unless the binding is marked deliberately silent.")
    if kind in ("music", "sfx", "narration") and not meta.get("has_audio"):
        raise MediaError(f"{path.name} has no audio stream.")
    if kind in ("broll", "standup") and not meta.get("has_video"):
        raise MediaError(f"{path.name} has no video stream.")
    a = Asset(id=new_asset_id(kind), kind=kind, path=str(path), name=path.name, meta=meta)
    project.assets.append(a)
    return a


def list_library(folder: str | Path, kind: str) -> list[Path]:
    """Sorted list of supported files in a library folder (recursive)."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    exts = KIND_EXT.get(kind, set())
    out = []
    for root, _dirs, files in os.walk(folder):
        for f in files:
            if Path(f).suffix.lower() in exts and not f.startswith("."):
                out.append(Path(root) / f)
    return sorted(out, key=lambda p: str(p).lower())


def rescan_folder(project: Project, folder: str | Path, kind: str) -> tuple[list[Asset], list[str]]:
    """Import all supported files of a folder. Existing assets keep their IDs."""
    added, errors = [], []
    for p in list_library(folder, kind):
        if find_by_path(project, p, kind):
            continue
        try:
            added.append(import_file(project, p, kind))
        except MediaError as exc:
            errors.append(str(exc))
    return added, errors


def refresh_meta(asset: Asset) -> None:
    asset.meta = probe(asset.path)


def missing_assets(project: Project, used_only: Optional[Iterable[str]] = None) -> list[Asset]:
    ids = set(used_only) if used_only is not None else None
    return [a for a in project.assets if (ids is None or a.id in ids) and not os.path.exists(a.path)]


def relink(asset: Asset, new_path: str | Path) -> None:
    """Point an asset at a new file, keeping its ID and all bindings."""
    new_path = Path(new_path)
    meta = probe(new_path)
    old = asset.meta or {}
    if old.get("duration") and meta.get("duration") and abs(old["duration"] - meta["duration"]) > 0.5:
        meta.setdefault("warnings", []).append(
            f"Relinked file duration {meta['duration']:.2f}s differs from the original {old['duration']:.2f}s; "
            "check trims.")
    asset.path = str(new_path)
    asset.name = new_path.name
    asset.meta = meta


def auto_relink(project: Project, folders: Iterable[str]) -> list[tuple[Asset, Path]]:
    """Relink missing assets to same-named files found in the given folders."""
    found = []
    index: dict[str, Path] = {}
    for folder in folders:
        if folder and Path(folder).is_dir():
            for root, _d, files in os.walk(folder):
                for f in files:
                    index.setdefault(f, Path(root) / f)
    for a in missing_assets(project):
        cand = index.get(Path(a.path).name)
        if cand is not None:
            try:
                relink(a, cand)
                found.append((a, cand))
            except MediaError:
                pass
    return found


def seeded_choice(candidates: list[Path], seed: int) -> Optional[Path]:
    """Repeatable automatic selection (seed is saved in the project)."""
    if not candidates:
        return None
    rng = random.Random(seed)
    return sorted(candidates, key=lambda p: str(p).lower())[rng.randrange(len(candidates))]


def kind_for_label(label: str) -> tuple[str, ...]:
    """Asset kinds allowed for a script marker label."""
    kind = label.split("_")[0]
    return {
        "SOUNDBITE": ("soundbite",),
        "STANDUP": ("standup", "soundbite"),
        "BROLL": ("broll", "image"),
        "IMAGE": ("image", "broll"),
        "CARD": ("image",),
    }.get(kind, ())


def guess_kind(path: str | Path) -> str:
    k = file_kind(path)
    return {"image": "image", "audio": "music", "video": "broll"}.get(k, "broll")
