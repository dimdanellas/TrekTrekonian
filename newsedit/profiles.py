"""Export profiles with explicit codec details.

MOV and MP4 are containers.  Each profile names the real FFmpeg encoder and
its parameters.  None of these defaults is claimed to satisfy a specific
station's control-room specification; every field is overridable per project.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

# DNxHD (VC-3) 1080p25 combinations accepted by FFmpeg's dnxhd encoder.
DNXHD_1080P25 = {
    # bitrate (Mb/s): pixel formats
    36: ("yuv422p",),
    120: ("yuv422p",),
    185: ("yuv422p", "yuv422p10le"),
}


DEFAULT_PROFILES: dict[str, dict] = {
    "tv_prores": {
        "label": "Television — ProRes",
        "container": "mov",
        "width": 1920, "height": 1080, "fps": 25, "scan": "progressive",
        "vcodec": "prores_ks", "vprofile": "2",  # 2 = ProRes 422 (standard)
        "pix_fmt": "yuv422p10le",
        "acodec": "pcm_s24le", "sample_rate": 48000, "channels": 2,
        "vertical_framing": "blur",
        "notes": "MOV / Apple ProRes 422 (prores_ks profile 2), 10-bit 4:2:2, PCM 24-bit stereo 48 kHz.",
    },
    "tv_dnxhd": {
        "label": "Television — DNxHD",
        "container": "mov",
        "width": 1920, "height": 1080, "fps": 25, "scan": "progressive",
        "vcodec": "dnxhd", "vbitrate_mbps": 120,
        "pix_fmt": "yuv422p",
        "acodec": "pcm_s24le", "sample_rate": 48000, "channels": 2,
        "vertical_framing": "blur",
        "notes": "MOV / Avid DNxHD 120 Mb/s 8-bit 4:2:2 1080p25, PCM 24-bit stereo 48 kHz.",
    },
    "web_horizontal": {
        "label": "Web/Social — Horizontal",
        "container": "mp4",
        "width": 1920, "height": 1080, "fps": 25, "scan": "progressive",
        "vcodec": "libx264", "crf": 18, "preset": "medium",
        "pix_fmt": "yuv420p",
        "acodec": "aac", "abitrate_kbps": 192, "sample_rate": 48000, "channels": 2,
        "vertical_framing": "blur",
        "notes": "MP4 / H.264 High (libx264 CRF 18) 4:2:0, AAC 192 kb/s stereo 48 kHz.",
    },
    "vertical_short": {
        "label": "Vertical Short",
        "container": "mp4",
        "width": 1080, "height": 1920, "fps": 25, "scan": "progressive",
        "vcodec": "libx264", "crf": 20, "preset": "medium",
        "pix_fmt": "yuv420p",
        "acodec": "aac", "abitrate_kbps": 192, "sample_rate": 48000, "channels": 2,
        "vertical_framing": "blur",
        "notes": "MP4 / H.264 1080×1920 with blur padding, AAC 192 kb/s stereo 48 kHz.",
    },
}

PREVIEW_PROFILE = {
    "label": "Preview proxy",
    "container": "mp4",
    "vcodec": "libx264", "crf": 28, "preset": "veryfast",
    "pix_fmt": "yuv420p",
    "acodec": "aac", "abitrate_kbps": 128, "sample_rate": 48000, "channels": 2,
    "scan": "progressive",
}


def get_profile(key: str, overrides: dict | None = None, custom: dict | None = None) -> dict:
    base = (custom or {}).get(key) or DEFAULT_PROFILES.get(key)
    if base is None:
        raise KeyError(f"Unknown export profile {key!r}")
    prof = copy.deepcopy(base)
    prof.update(overrides or {})
    prof["key"] = key
    return prof


def proxy_profile(main: dict, max_side: int = 640) -> dict:
    w, h = main["width"], main["height"]
    scale = max_side / max(w, h)
    pw, ph = int(round(w * scale / 2) * 2), int(round(h * scale / 2) * 2)
    prof = copy.deepcopy(PREVIEW_PROFILE)
    prof.update(width=pw, height=ph, fps=main["fps"], key="preview")
    return prof


def validate_profile_static(prof: dict) -> list[str]:
    """Fast checks that need no encoder run. Returns list of error strings."""
    errs = []
    w, h, fps = prof.get("width"), prof.get("height"), prof.get("fps")
    if not (isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0):
        errs.append("Width/height must be positive integers.")
    elif w % 2 or h % 2:
        errs.append("Width and height must be even numbers.")
    if not isinstance(fps, int) or fps <= 0:
        errs.append("Frame rate must be a positive integer.")
    if prof.get("container") not in ("mov", "mp4"):
        errs.append("Container must be MOV or MP4.")
    vc = prof.get("vcodec")
    if vc == "dnxhd":
        if (w, h, fps) != (1920, 1080, 25):
            errs.append(f"DNxHD preset table covers 1920×1080 at 25 fps only; got {w}×{h} at {fps} fps. "
                        "Use ProRes or H.264 for other sizes.")
        br = prof.get("vbitrate_mbps")
        allowed = DNXHD_1080P25.get(br)
        if allowed is None:
            errs.append(f"DNxHD bitrate {br} Mb/s is not a 1080p25 DNxHD preset (use 36, 120 or 185).")
        elif prof.get("pix_fmt") not in allowed:
            errs.append(f"DNxHD {br} Mb/s does not support pixel format {prof.get('pix_fmt')} "
                        f"(allowed: {', '.join(allowed)}).")
        if prof.get("container") != "mov":
            errs.append("DNxHD profile must use the MOV container.")
    elif vc == "prores_ks":
        if str(prof.get("vprofile")) not in ("0", "1", "2", "3", "4", "5"):
            errs.append("ProRes profile must be 0–5 (2 = 422).")
        if prof.get("container") != "mov":
            errs.append("ProRes profile must use the MOV container.")
    elif vc == "libx264":
        if prof.get("pix_fmt") not in ("yuv420p", "yuv422p", "yuv444p"):
            errs.append("H.264 pixel format must be yuv420p, yuv422p or yuv444p.")
    else:
        errs.append(f"Unsupported video encoder {vc!r}.")
    if prof.get("acodec") not in ("aac", "pcm_s16le", "pcm_s24le"):
        errs.append(f"Unsupported audio encoder {prof.get('acodec')!r}.")
    if prof.get("acodec", "").startswith("pcm") and prof.get("container") == "mp4":
        errs.append("PCM audio is not supported in the MP4 container; use AAC or MOV.")
    if prof.get("scan") not in ("progressive", "interlaced_tff"):
        errs.append("Scan must be progressive or interlaced_tff.")
    return errs


def ffmpeg_video_args(prof: dict) -> list[str]:
    vc = prof["vcodec"]
    args = ["-c:v", vc, "-pix_fmt", prof["pix_fmt"]]
    if vc == "prores_ks":
        args += ["-profile:v", str(prof.get("vprofile", "2")), "-vendor", "apl0"]
    elif vc == "dnxhd":
        args += ["-b:v", f"{int(prof['vbitrate_mbps'])}M"]
    elif vc == "libx264":
        args += ["-preset", prof.get("preset", "medium"), "-crf", str(prof.get("crf", 18))]
        if prof.get("container") == "mp4":
            args += ["-movflags", "+faststart"]
    args += ["-r", str(prof["fps"]), "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]
    if prof.get("scan") == "interlaced_tff":
        args += ["-flags", "+ildct+ilme", "-field_order", "tt"]
        if vc == "libx264":
            args += ["-x264opts", "tff=1"]
    else:
        args += ["-field_order", "progressive"]
    return args


def ffmpeg_audio_args(prof: dict) -> list[str]:
    args = ["-c:a", prof["acodec"], "-ar", str(prof.get("sample_rate", 48000)), "-ac", str(prof.get("channels", 2))]
    if prof["acodec"] == "aac":
        args += ["-b:a", f"{int(prof.get('abitrate_kbps', 192))}k"]
    return args
