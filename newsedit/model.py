"""Versioned project data model.

Units (also documented in docs/PROJECT_SCHEMA.md):
* every ``*_seconds``/``start``/``end``/``in_seconds``/``out_seconds`` field is
  in seconds (float).  Source trims are *source* time; timeline positions are
  computed by :mod:`newsedit.timeline` and stored as integer frames only in
  compiled output, never in the project.
* every ``*_db`` field is decibels; ``duck_db`` is a positive attenuation.
* ``*_ms`` fields are milliseconds.
* ``*_ratio`` fields are fractions of the output frame width/height.
* ``opacity`` fields are 0..1.
"""
from __future__ import annotations

import copy
import dataclasses
import json
import os
import typing
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .util import atomic_write_json

SCHEMA_VERSION = 1

ASSET_KINDS = ("soundbite", "standup", "broll", "image", "music", "sfx", "logo", "narration")
RESIZE_MODES = ("blur", "crop", "stretch")
RESIZE_LABELS = {"crop": "Center Crop", "blur": "Fit with Blur Background", "stretch": "Stretch"}
FILL_POLICIES = ("none", "loop", "hold")
FILL_LABELS = {"none": "No fill (warn)", "loop": "Approved loop", "hold": "Still hold (freeze last frame)"}


class ProjectLoadError(Exception):
    """Raised for malformed or unsupported project files."""


@dataclass
class Asset:
    id: str
    kind: str
    path: str
    name: str = ""
    meta: dict = field(default_factory=dict)  # duration, width, height, fps, has_audio, has_video, rotation...


@dataclass
class Binding:
    """Association of a script marker label (e.g. ``SOUNDBITE_1``) with media."""
    label: str
    asset_id: Optional[str] = None
    in_seconds: float = 0.0
    out_seconds: Optional[float] = None  # None = end of source
    fill_policy: str = "none"  # B-roll/still coverage policy when narration outlasts the visual
    resize_mode: Optional[str] = None  # None = project default
    crop_x: float = 0.0  # framing offset for Center Crop, -1 (left) .. +1 (right)
    crop_y: float = 0.0  # -1 (top) .. +1 (bottom)
    ambience_db: Optional[float] = None  # B-roll source audio; None = muted (default)
    silent: bool = False  # statement deliberately without speech audio
    language: str = "el"  # transcription language for statements
    gain_db: float = 0.0  # statement/stand-up speech gain
    # CARD_n fields (manually supplied title/data cards; nothing is invented)
    card_title: str = ""
    card_body: str = ""
    card_seconds: float = 4.0  # used when the card stands alone (independent time)
    card_background_asset_id: Optional[str] = None
    card_color: str = "#102040"


@dataclass
class LowerThird:
    binding_label: str
    name: str = ""
    role: str = ""
    start_offset: float = 0.5  # seconds after statement start
    end_offset: Optional[float] = 5.0  # seconds after statement start; None = until statement end
    fade_seconds: float = 0.3


@dataclass
class NarrationAudio:
    """Audio bound to a narration block, keyed by a hash of the block text."""
    block_index: int
    text_hash: str
    text: str = ""
    source: str = "recorded"  # recorded | imported | tts | take
    wav_path: Optional[str] = None  # PCM WAV (recorded/imported/tts)
    take_id: Optional[str] = None  # for ranges of a continuous take
    take_in: float = 0.0
    take_out: float = 0.0
    provider: str = ""
    voice: str = ""
    trim_head: float = 0.0  # reversible silence trim applied at use time
    trim_tail: float = 0.0
    duration: float = 0.0  # untrimmed duration of the referenced audio range


@dataclass
class NarrationTake:
    id: str
    path: str
    duration: float = 0.0


@dataclass
class NarrationSettings:
    mode: str = "manual"  # manual | tts
    provider: str = "gtts"  # gtts | elevenlabs
    language: str = "el"
    voice_id: str = ""
    voice_name: str = ""
    model_id: str = "eleven_multilingual_v2"
    stability: float = 0.5
    similarity_boost: float = 0.75
    gtts_tld: str = "com"
    gtts_slow: bool = False
    fallback_provider: str = "gtts"  # offered (never silent) when ElevenLabs fails
    input_device: Optional[str] = None
    takes: list[NarrationTake] = field(default_factory=list)
    blocks: list[NarrationAudio] = field(default_factory=list)


@dataclass
class Cue:
    start: float  # seconds relative to the trimmed clip start
    end: float
    text: str
    words: list = field(default_factory=list)  # optional [[start, end, word], ...] clip-relative


@dataclass
class SubtitleTrack:
    label: str
    language: str = "el"
    source: str = "whisper"  # whisper | manual
    model: str = ""
    clip_in: float = 0.0  # source trim used when transcribed
    clip_out: float = 0.0
    reviewed: bool = False
    cues: list[Cue] = field(default_factory=list)


@dataclass
class SubtitleSettings:
    burn_in: bool = True
    narration_captions: bool = False
    max_chars: int = 42
    max_lines: int = 2
    min_duration: float = 1.0
    max_duration: float = 6.0
    max_cps: float = 17.0
    style: str = "box"  # box | outline
    box_opacity: float = 0.55
    outline_px_1080: float = 2.0
    font_size_1080: int = 44
    font_path: Optional[str] = None
    whisper_model: str = "small"
    whisper_device: str = "cpu"  # cpu | cuda (never assumed)
    write_srt: bool = True
    write_ass: bool = True


@dataclass
class MusicSettings:
    asset_id: Optional[str] = None
    base_db: float = -18.0  # level in pauses (the "100%" of the source texts)
    duck_db: float = 20.0
    attack_ms: float = 100.0
    release_ms: float = 300.0
    soundbite_policy: str = "duck"  # duck | mute
    mute_preroll: float = 0.5
    fade_in: float = 0.5
    fade_out: float = 2.0
    loop: bool = True
    loop_crossfade: float = 1.0
    auto_seed: Optional[int] = None  # set when the user used seeded automatic selection


@dataclass
class SfxPlacement:
    asset_id: str
    at_seconds: float = 0.0  # timeline position
    gain_db: float = -6.0


@dataclass
class Watermark:
    asset_id: Optional[str] = None
    width_ratio: float = 0.12
    opacity: float = 0.85


@dataclass
class OpeningTitle:
    enabled: bool = False
    text: str = ""
    subtitle: str = ""
    start: float = 0.0
    duration: float = 4.0


@dataclass
class VisualSettings:
    resize_mode: str = "blur"
    transition: str = "cut"  # cut | fade (fade through black, no timing overlap)
    fade_frames: int = 6
    broll_shot_pref_seconds: float = 3.0  # editorial preference, warning only
    safe_inset: float = 0.05
    lower_third_color: str = "#0b3d91"


@dataclass
class AudioSettings:
    peak_target_db: float = -1.0
    loudness_target_lufs: Optional[float] = None  # None = no loudness normalisation
    auto_fix_peaks: bool = True
    narration_gain_db: float = 0.0


@dataclass
class OutputSettings:
    profile: str = "web_horizontal"
    width: int = 1920
    height: int = 1080
    fps: int = 25
    overrides: dict = field(default_factory=dict)  # per-project codec overrides


@dataclass
class ExportOptions:
    voice_over_wav: bool = True
    segment_wavs: bool = False
    segment_rundown: bool = True


@dataclass
class Project:
    schema_version: int = SCHEMA_VERSION
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    title: str = "Νέο ρεπορτάζ"
    target_seconds: float = 120.0
    wpm: float = 140.0
    script: str = ""
    assets: list[Asset] = field(default_factory=list)
    bindings: dict[str, Binding] = field(default_factory=dict)
    narration: NarrationSettings = field(default_factory=NarrationSettings)
    lower_thirds: list[LowerThird] = field(default_factory=list)
    subtitle_tracks: dict[str, SubtitleTrack] = field(default_factory=dict)
    subtitles: SubtitleSettings = field(default_factory=SubtitleSettings)
    music: MusicSettings = field(default_factory=MusicSettings)
    sfx: list[SfxPlacement] = field(default_factory=list)
    watermark: Watermark = field(default_factory=Watermark)
    opening_title: OpeningTitle = field(default_factory=OpeningTitle)
    visual: VisualSettings = field(default_factory=VisualSettings)
    audio: AudioSettings = field(default_factory=AudioSettings)
    output: OutputSettings = field(default_factory=OutputSettings)
    export: ExportOptions = field(default_factory=ExportOptions)
    export_directory: str = ""
    duration_override: bool = False
    duration_override_reason: str = ""
    acknowledged_warnings: list[str] = field(default_factory=list)
    last_render: dict = field(default_factory=dict)

    # ---- convenience -------------------------------------------------
    def asset(self, asset_id: Optional[str]) -> Optional[Asset]:
        if not asset_id:
            return None
        for a in self.assets:
            if a.id == asset_id:
                return a
        return None

    def lower_third_for(self, label: str) -> Optional[LowerThird]:
        for lt in self.lower_thirds:
            if lt.binding_label == label:
                return lt
        return None

    def binding(self, label: str) -> Binding:
        b = self.bindings.get(label)
        if b is None:
            b = Binding(label=label)
            self.bindings[label] = b
        return b

    def snapshot(self) -> "Project":
        """Immutable-by-convention deep copy for background jobs."""
        return copy.deepcopy(self)


# ---------------------------------------------------------------------------
# Serialisation with explicit type checks
# ---------------------------------------------------------------------------

def to_dict(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj):
        return {f.name: to_dict(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_dict(v) for v in obj]
    return obj


_HINT_CACHE: dict[type, dict[str, Any]] = {}


def _hints(cls: type) -> dict[str, Any]:
    if cls not in _HINT_CACHE:
        _HINT_CACHE[cls] = typing.get_type_hints(cls)
    return _HINT_CACHE[cls]


def _convert(tp: Any, value: Any, where: str) -> Any:
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if origin is typing.Union:
        if value is None and type(None) in args:
            return None
        non_none = [a for a in args if a is not type(None)]
        return _convert(non_none[0], value, where)
    if dataclasses.is_dataclass(tp):
        if not isinstance(value, dict):
            raise ProjectLoadError(f"{where}: expected object, got {type(value).__name__}")
        return from_dict(tp, value, where)
    if origin in (list, typing.List):
        if not isinstance(value, list):
            raise ProjectLoadError(f"{where}: expected list")
        return [_convert(args[0], v, f"{where}[{i}]") for i, v in enumerate(value)] if args else list(value)
    if origin in (dict, typing.Dict):
        if not isinstance(value, dict):
            raise ProjectLoadError(f"{where}: expected object")
        if args:
            return {str(k): _convert(args[1], v, f"{where}.{k}") for k, v in value.items()}
        return dict(value)
    if tp is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ProjectLoadError(f"{where}: expected number")
        return float(value)
    if tp is int:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
            raise ProjectLoadError(f"{where}: expected integer")
        return int(value)
    if tp is bool:
        if not isinstance(value, bool):
            raise ProjectLoadError(f"{where}: expected true/false")
        return value
    if tp is str:
        if not isinstance(value, str):
            raise ProjectLoadError(f"{where}: expected text")
        return value
    if tp is dict:
        if not isinstance(value, dict):
            raise ProjectLoadError(f"{where}: expected object")
        return value
    if tp is list:
        if not isinstance(value, list):
            raise ProjectLoadError(f"{where}: expected list")
        return value
    return value


def from_dict(cls: type, data: dict, where: str = "project") -> Any:
    hints = _hints(cls)
    kwargs = {}
    for f in dataclasses.fields(cls):
        if f.name in data:
            kwargs[f.name] = _convert(hints[f.name], data[f.name], f"{where}.{f.name}")
    try:
        return cls(**kwargs)
    except TypeError as exc:
        raise ProjectLoadError(f"{where}: {exc}") from exc


def validate_project(p: Project) -> None:
    """Semantic checks beyond types. Raises ProjectLoadError."""
    if p.schema_version > SCHEMA_VERSION:
        raise ProjectLoadError(
            f"Project schema {p.schema_version} is newer than this application supports ({SCHEMA_VERSION})."
        )
    ids = [a.id for a in p.assets]
    if len(ids) != len(set(ids)):
        raise ProjectLoadError("Duplicate asset IDs in project.")
    for a in p.assets:
        if a.kind not in ASSET_KINDS:
            raise ProjectLoadError(f"Asset {a.id}: unknown kind {a.kind!r}")
    for label, b in p.bindings.items():
        if b.label != label:
            b.label = label
        if b.fill_policy not in FILL_POLICIES:
            raise ProjectLoadError(f"Binding {label}: invalid fill policy {b.fill_policy!r}")
        if b.resize_mode is not None and b.resize_mode not in RESIZE_MODES:
            raise ProjectLoadError(f"Binding {label}: invalid resize mode {b.resize_mode!r}")
    if not (120 <= p.wpm <= 160):
        raise ProjectLoadError(f"WPM {p.wpm} outside the supported 120–160 range")
    if p.target_seconds <= 0:
        raise ProjectLoadError("Target duration must be positive")
    if p.visual.resize_mode not in RESIZE_MODES:
        raise ProjectLoadError(f"Invalid resize mode {p.visual.resize_mode!r}")
    if p.music.soundbite_policy not in ("duck", "mute"):
        raise ProjectLoadError("Music soundbite policy must be 'duck' or 'mute'")


# ---------------------------------------------------------------------------
# Paths: project-relative where possible
# ---------------------------------------------------------------------------

def relativize(path: str, project_dir: Optional[Path]) -> str:
    if not project_dir or not path:
        return path
    try:
        p = Path(path).resolve()
        rel = p.relative_to(project_dir.resolve())
        return rel.as_posix()
    except (ValueError, OSError):
        return path


def resolve_path(path: str, project_dir: Optional[Path]) -> Path:
    p = Path(path)
    if not p.is_absolute() and project_dir is not None:
        p = project_dir / p
    return p


def project_to_json_dict(p: Project, project_dir: Optional[Path]) -> dict:
    d = to_dict(p)
    for a in d["assets"]:
        a["path"] = relativize(a["path"], project_dir)
    for t in d["narration"]["takes"]:
        t["path"] = relativize(t["path"], project_dir)
    for b in d["narration"]["blocks"]:
        if b.get("wav_path"):
            b["wav_path"] = relativize(b["wav_path"], project_dir)
    if d.get("export_directory"):
        d["export_directory"] = relativize(d["export_directory"], project_dir)
    return d


def project_from_json_dict(d: dict, project_dir: Optional[Path]) -> Project:
    if not isinstance(d, dict):
        raise ProjectLoadError("Project file must contain a JSON object.")
    if "schema_version" not in d:
        raise ProjectLoadError("Missing schema_version: this is not a News Package Editor project.")
    p = from_dict(Project, d)
    validate_project(p)
    for a in p.assets:
        a.path = str(resolve_path(a.path, project_dir))
    for t in p.narration.takes:
        t.path = str(resolve_path(t.path, project_dir))
    for b in p.narration.blocks:
        if b.wav_path:
            b.wav_path = str(resolve_path(b.wav_path, project_dir))
    if p.export_directory:
        p.export_directory = str(resolve_path(p.export_directory, project_dir))
    return p


def save_project(p: Project, path: Path | str) -> None:
    """Atomic save; the previous file survives a failed write."""
    path = Path(path)
    atomic_write_json(path, project_to_json_dict(p, path.parent))


def load_project(path: Path | str) -> Project:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProjectLoadError(f"Cannot read {path}: {exc.strerror or exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProjectLoadError(f"{path.name} is not valid JSON (line {exc.lineno}, column {exc.colno}).") from exc
    return project_from_json_dict(data, path.parent)


def new_asset_id(kind: str) -> str:
    prefix = {"soundbite": "sb", "standup": "su", "broll": "br", "image": "im", "music": "mu",
              "sfx": "fx", "logo": "lg", "narration": "nr"}.get(kind, "as")
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def asset_exists(a: Asset) -> bool:
    return bool(a.path) and os.path.exists(a.path)
