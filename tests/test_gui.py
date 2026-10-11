"""GUI behaviour tests (need a display; run under xvfb-run on headless Linux)."""
import os
import time

import pytest

tk = pytest.importorskip("tkinter")

if not os.environ.get("DISPLAY") and os.name != "nt":
    pytest.skip("no display (run with xvfb-run)", allow_module_level=True)


@pytest.fixture
def gui(monkeypatch, routing_project, tmp_path):
    from newsedit.model import save_project
    from newsedit.ui import app as appmod

    calls = []

    class MB:
        @staticmethod
        def showinfo(title, msg, **kw):
            calls.append(("info", title, msg))

        @staticmethod
        def showwarning(title, msg, **kw):
            calls.append(("warning", title, msg))

        @staticmethod
        def showerror(title, msg, **kw):
            calls.append(("error", title, msg))

        @staticmethod
        def askyesno(*a, **kw):
            return False

        @staticmethod
        def askokcancel(*a, **kw):
            return True

        @staticmethod
        def askyesnocancel(*a, **kw):
            return False

    monkeypatch.setattr(appmod, "messagebox", MB)
    path = tmp_path / "δοκιμή.newsproj.json"
    save_project(routing_project, path)
    root = tk.Tk()
    app = appmod.App(root, None)
    app.open_project(str(path))
    pump(root, 0.5)
    yield app, root, calls
    app.dirty = False
    if app.jobs.busy():
        app.jobs.cancel()
        app.jobs.wait_all(30)
    root.destroy()


def pump(root, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        root.update()
        time.sleep(0.01)


def wait_job(app, root, timeout=180):
    ticks = 0
    end = time.monotonic() + timeout
    while app.jobs.busy() and time.monotonic() < end:
        root.update()
        ticks += 1
        time.sleep(0.01)
    pump(root, 0.5)
    return ticks


def test_paste_undo_and_load_update_counters(gui):
    app, root, _ = gui
    words0 = int(app.script.v_words.get())
    root.clipboard_clear()
    root.clipboard_append(" ένα δύο τρία τέσσερα")
    app.script.text.mark_set("insert", "3.end")
    app.script.text.focus_force()
    app.script.text.event_generate("<<Paste>>")
    pump(root, 0.6)
    assert int(app.script.v_words.get()) == words0 + 4
    app.script.undo()
    pump(root, 0.6)
    assert int(app.script.v_words.get()) == words0
    app.script.redo()
    pump(root, 0.6)
    assert int(app.script.v_words.get()) == words0 + 4
    assert app.dirty


def test_overrun_colour_sets_and_resets(gui):
    from newsedit.ui.widgets import OVERRUN_BG, SCRIPT_BG

    app, root, _ = gui
    app.script.v_target.set(3)
    app.script._target_changed()
    pump(root, 0.5)
    assert app.script.text.cget("bg") == OVERRUN_BG
    assert "OVER" in app.script.v_remaining.get()
    app.script.v_target.set(60)
    app.script._target_changed()
    pump(root, 0.5)
    assert app.script.text.cget("bg") == SCRIPT_BG


def test_preview_job_keeps_ui_responsive(gui):
    app, root, calls = gui
    app.build_preview()
    assert app.jobs.busy("preview")
    ticks = wait_job(app, root)
    assert ticks > 20  # the event loop kept running while the proxy rendered
    assert app.preview.player is not None, calls
    assert app.preview.player.duration > 5
    app.preview.step(10)
    pump(root, 0.3)
    assert app.preview.tc.cget("text") != "00:00:00:00"


def test_render_success_and_cancel(gui):
    app, root, calls = gui
    app.render()
    wait_job(app, root, 300)
    assert any(c[1] == "Render complete" for c in calls), calls
    assert app.project.last_render.get("filename")
    calls.clear()
    app.render()
    pump(root, 1.5)
    app.cancel_jobs()
    wait_job(app, root, 120)
    assert not any(c[1] == "Render complete" for c in calls), calls
    assert any("cancelled" in c[2] for c in calls)
    exports = os.listdir(app.project.export_directory)
    assert not [f for f in exports if ".partial." in f]


def test_cue_editor_edit_survives_save_reopen(gui, tmp_path):
    from newsedit.model import load_project
    from newsedit.ui.dialogs import CueEditor

    app, root, _ = gui
    ed = CueEditor(app, "SOUNDBITE_1")
    ed.add()
    ed.tree.selection_set("0")
    pump(root, 0.2)
    ed.select()
    ed.text.delete("1.0", "end")
    ed.text.insert("1.0", "Διορθωμένο κείμενο.")
    ed.apply()
    ed.destroy()
    out = tmp_path / "saved.newsproj.json"
    app._write(out)
    q = load_project(out)
    assert q.subtitle_tracks["SOUNDBITE_1"].cues[0].text == "Διορθωμένο κείμενο."


def test_markers_form_validates_trim(gui):
    app, root, _ = gui
    app.media.select_label("SOUNDBITE_1")
    pump(root, 0.5)
    tab = app.media.markers_tab
    tab.vars["out"].set("99")
    pump(root, 0.5)
    assert "beyond the source" in tab.dur_lbl.cget("text")
    assert any(d.code == "bad_trim" for d in app.diagnostics)
    tab.vars["out"].set("2.0")
    pump(root, 0.5)
    assert "1.500" in tab.dur_lbl.cget("text")
