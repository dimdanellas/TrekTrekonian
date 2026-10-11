# Project and settings schema

## Project file

A project is saved as `*.newsproj.json` (UTF-8 JSON) with `schema_version` 1.

- Saves are atomic: the file is written to a temporary file, fsynced, then swapped in. If the write fails, the previous file stays untouched.
- On load, types are checked field by field. A malformed file produces a message naming the bad field.
- Paths under the project folder are stored relative to it; all other paths are absolute.

**Units used in every field:** seconds (float) for any time, decibels for `*_db`, milliseconds for `*_ms`, a fraction of the frame for `*_ratio`, and 0–1 for opacity.

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | int | 1 |
| `id` | str | Stable project UUID. Used for the cache and autosave. |
| `title` | str | Package title. Used in output names and the rundown. |
| `target_seconds` | float | Target duration (default 120). |
| `wpm` | float | Words per minute for estimates, 120–160 (default 140). |
| `script` | str | Script text with markers (see README). |
| `assets[]` | list | `{id, kind, path, name, meta}`. `kind` is one of `soundbite`, `standup`, `broll`, `image`, `music`, `sfx`, `logo`, `narration`. `meta` comes from ffprobe or Pillow: `duration`, `width`, `height`, `fps`, `has_audio`, `has_video`, `rotation`, `is_image`, `warnings[]`. |
| `bindings{LABEL}` | object | One entry per marker label (for example `SOUNDBITE_1`); fields below. |
| `lower_thirds[]` | list | `{binding_label, name, role, start_offset, end_offset (null = statement end), fade_seconds}`. Offsets are relative to the statement's start. |
| `narration.mode` | str | `manual` or `tts`. |
| `narration.provider` | str | `gtts` or `elevenlabs`. Also stored: `language`, `voice_id`, `voice_name`, `model_id`, `stability`, `similarity_boost`, `gtts_tld`, `gtts_slow`. The API key is **never** stored. |
| `narration.takes[]` | list | Continuous takes: `{id, path, duration}`. |
| `narration.blocks[]` | list | Narration audio per block; fields below. |
| `subtitle_tracks{LABEL}` | object | Cues per statement; fields below. |
| `subtitles` | object | `burn_in`, `narration_captions`, `max_chars` (37–42), `max_lines` (2), `min_duration`, `max_duration`, `max_cps`, `style` (`box`/`outline`), `box_opacity`, `outline_px_1080`, `font_size_1080`, `font_path`, `whisper_model`, `whisper_device`, `write_srt`, `write_ass`. |
| `music` | object | `asset_id`, `base_db`, `duck_db`, `attack_ms`, `release_ms`, `soundbite_policy` (`duck`/`mute`), `mute_preroll`, `fade_in`, `fade_out`, `loop`, `loop_crossfade`, and `auto_seed` (set when the track was chosen by seeded automatic selection). |
| `sfx[]` | list | `{asset_id, at_seconds (timeline), gain_db}`. |
| `watermark` | object | `{asset_id, width_ratio, opacity}`. Placed top-right inside the safe area. |
| `opening_title` | object | `{enabled, text, subtitle, start, duration}`. |
| `visual` | object | `resize_mode` (`blur`/`crop`/`stretch`), `transition` (`cut`/`fade`), `fade_frames`, `broll_shot_pref_seconds`, `safe_inset`, `lower_third_color`. |
| `audio` | object | `peak_target_db`, `loudness_target_lufs` (null = off), `auto_fix_peaks`, `narration_gain_db`. |
| `output` | object | `{profile, width, height, fps, overrides{}}`. `profile` is one of `tv_prores`, `tv_dnxhd`, `web_horizontal`, `vertical_short`. |
| `export` | object | `{voice_over_wav, segment_wavs, segment_rundown}`. |
| `export_directory` | str | Where renders are written. |
| `duration_override` | bool | Allows export over the target. Saved with `duration_override_reason`. |
| `acknowledged_warnings[]` | list | Warnings shown at the last render. |
| `last_render` | object | `{frames, fps, filename, timeline_hash, profile}`. |

### Binding fields

Each `bindings{LABEL}` entry holds:

- `label` and `asset_id`
- `in_seconds` and `out_seconds` (`out_seconds` null means the end of the source). These are source time.
- `fill_policy`: `none`, `loop` or `hold`
- `resize_mode` (null means the project default), plus `crop_x` and `crop_y` (−1 to 1)
- `ambience_db` (null means muted)
- `silent`, `language` and `gain_db`
- for cards: `card_title`, `card_body`, `card_seconds`, `card_background_asset_id` and `card_color`

### Narration block fields

Each `narration.blocks[]` entry holds:

- `block_index`
- `text_hash`: a hash of the block text, used for staleness and re-matching
- `text`
- `source`: `recorded`, `imported`, `tts` or `take`
- `wav_path`
- for a take range: `take_id`, `take_in` and `take_out`
- `provider` and `voice`
- `trim_head` and `trim_tail`: reversible silence trims
- `duration`

### Subtitle track fields

Each `subtitle_tracks{LABEL}` entry holds:

- `label`, `language`, `source` (`whisper` or `manual`) and `model`
- `clip_in` and `clip_out`: the trim when the clip was transcribed
- `reviewed`
- `cues[]`: `{start, end, text, words[[s, e, w]]}`, with times relative to the trimmed clip

## Compiled timeline (not stored)

`newsedit.timeline.compile_timeline()` turns the script and bindings into sequential segments, each with:

- integer `start_frame` and `duration_frames`
- the exact `seconds`
- the source range `src_in`/`src_out`
- the visual's asset, range and fill policy
- a reference to its narration audio
- `estimated` and `stale` flags

Compilation is deterministic. The preview, the audio mix, captions, the rundown and the manifest all use the same compiled timeline.

## Application settings (`settings.json`)

| Field | Meaning |
|---|---|
| `paths` | `{sfx, music, broll, images, soundbites, exports}` |
| `default_wpm`, `default_target_seconds`, `default_profile`, `default_resize_mode` | Defaults for new projects. |
| `autosave_seconds` | Autosave interval. |
| `recent_projects[]` | Recently opened projects. |
| `prompts[]` | AI Prompts templates, as `{name, text}`. |
| `custom_profiles` | Custom export profiles. |
| `ui_geometry` | Window size. |
| `external_player` | Player used for External player. |

If `settings.json` cannot be read, it is renamed to `settings.broken.json` and the defaults are used.
