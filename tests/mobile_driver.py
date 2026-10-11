"""Drives the tablet interface in a separate process (Kivy runs one App per process).

Usage: python tests/mobile_driver.py <project.newsproj.json> <export_dir> <result.json>
Prints nothing; writes a JSON report of what it observed.
"""
import json
import os
import sys
from pathlib import Path

os.environ["KIVY_NO_ARGS"] = "1"
os.environ.setdefault("KIVY_NO_CONSOLELOG", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kivy.config import Config  # noqa: E402

Config.set("graphics", "width", "1280")
Config.set("graphics", "height", "800")

from kivy.clock import Clock  # noqa: E402
from kivy.core.window import Window  # noqa: E402
from kivy.uix.button import Button  # noqa: E402
from kivy.uix.popup import Popup  # noqa: E402

from newsedit.mobile.app import NewsEditorApp  # noqa: E402

project, export_dir, out = sys.argv[1:4]
app = NewsEditorApp(project)
report = {"popups": [], "errors": []}
steps = []


def step(delay):
    def deco(fn):
        steps.append((delay, fn))
        return fn
    return deco


def popups():
    return [w for w in Window.children if isinstance(w, Popup)]


def press(popup, text):
    for w in popup.walk():
        if isinstance(w, Button) and w.text == text:
            w.dispatch("on_release")
            return True
    return False


@step(2.0)
def loaded():
    for p in popups():
        report["popups"].append(p.title)
        p.dismiss()
    tl = app.timeline
    report["segments"] = len(tl.segments)
    report["words"] = tl.narration_words
    report["labels"] = list(app.parse.labels)
    report["assets"] = len(app.project.assets)
    report["load_errors"] = [d.message for d in app.diagnostics if d.is_error]


@step(0.5)
def edit_script():
    before = app.timeline.narration_words
    app.script_tab.text.cursor = app.script_tab.text.get_cursor_from_index(len(app.script_tab.text.text))
    app.script_tab.insert_text(" ένα δύο τρία")
    report["words_before"] = before


@step(0.8)
def counted():
    report["words_after"] = app.timeline.narration_words
    report["dirty_after_edit"] = app.dirty
    app.script_tab.text.do_undo()


@step(0.8)
def overrun():
    report["words_after_undo"] = app.timeline.narration_words
    app.script_tab.v_target.text = "10"


@step(0.8)
def overrun_check():
    report["over_bg"] = list(app.script_tab.text.background_color)
    report["remaining_text"] = app.script_tab.remaining.text
    app.script_tab.v_target.text = "40"


@step(0.8)
def overrun_reset():
    report["normal_bg"] = list(app.script_tab.text.background_color)
    sb = [lab for lab in app.parse.labels if lab.startswith("SOUNDBITE")][0]
    app.show_tab("Markers")
    app.markers_tab.select(sb)
    report["soundbite"] = sb


@step(1.0)
def bad_trim():
    v = app.markers_tab.v
    report["form_in"] = v["in"].text
    v["out"].text = "99"


@step(0.8)
def bad_trim_check():
    report["bad_trim_label"] = app.markers_tab.dur_lbl.text
    report["bad_trim_error"] = any(d.code == "bad_trim" for d in app.diagnostics)
    app.markers_tab.v["out"].text = "5.5"


@step(0.8)
def subtitles():
    mt = app.markers_tab
    mt.sub_text.text = "0.00 - 2.00\nΠρώτη γραμμή\n\n2.00 - 4.50\nΔεύτερη γραμμή"
    mt._apply_cues()
    tr = app.project.subtitle_tracks[mt.current]
    report["cues"] = [(c.start, c.end, c.text) for c in tr.cues]
    mt.sub_reviewed.checkbox.active = True
    report["reviewed"] = tr.reviewed


@step(0.5)
def frame():
    app.show_tab("Render")
    app.render_tab.slider.value = 300
    app.render_tab.inspect_frame()


@step(15.0)
def frame_done():
    tex = app.render_tab.frame_img.texture
    report["frame_size"] = list(tex.size) if tex else None
    app.project.export_directory = export_dir
    app.render_tab.render()


@step(1.0)
def confirm_render():
    for p in popups():
        report["popups"].append(p.title)
        press(p, "Render")


def wait_render(*_):
    if app.jobs.busy():
        Clock.schedule_once(wait_render, 1.0)
        return
    for p in popups():
        report["popups"].append(p.title)
        p.dismiss()
    report["outputs"] = sorted(os.listdir(export_dir)) if os.path.isdir(export_dir) else []
    report["last_render"] = app.project.last_render
    Path(out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    app.stop()


def run_steps(*_):
    t = 0.0
    for delay, fn in steps:
        t += delay

        def call(dt, fn=fn):
            try:
                fn()
            except Exception as exc:  # recorded, the test asserts on it
                import traceback

                report["errors"].append(f"{fn.__name__}: {exc}\n{traceback.format_exc()}")
        Clock.schedule_once(call, t)
    Clock.schedule_once(wait_render, t + 3.0)


Clock.schedule_once(run_steps, 0)
app.run()
