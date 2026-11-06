"""The capture of a run's moments (start_moments(), note_moment(), finish_moments()), the run charts drawn on request (the `report` command), and the capture's own parameters."""
import json
import os
import time
import zipfile

import cv2
import numpy as np
import pytest

import test_capture
from test_state import write_command
from test_report import write_log


capture = test_capture.capture                                                  # (the fixture of that file: a folder, and a keyboard, mouse and screen the test sets)


class FakeCapture:
    """Stands in for InputCapture: notes what it is told."""
    made = []

    def __init__(self, folder, **options):
        self.folder, self.options, self.calls = folder, options, []
        self.started, self.running, self.stopped_because = time.time(), True, None
        self.frames, self.dropped = 12, 3
        FakeCapture.made.append(self)

    def start(self):
        os.makedirs(self.folder, exist_ok=True)
        with open(os.path.join(self.folder, "info.json"), "w") as f:
            json.dump({"started": "now"}, f)

    def note(self, text):
        self.calls.append(("note", text))

    def mark(self, reason):
        self.calls.append(("mark", reason))

    def protect_tail(self, seconds):
        self.calls.append(("tail", seconds))

    def stop(self):
        self.calls.append(("stop", None))
        return "summary"


@pytest.fixture
def moments(macro, monkeypatch, tmp_path):
    FakeCapture.made = []
    monkeypatch.setattr(macro, "InputCapture", FakeCapture)
    monkeypatch.setattr(macro.cfg, "MOMENTS_DIR", str(tmp_path / "moments"))
    monkeypatch.setattr(macro.cfg, "GAME_CONFIG_PATH", str(tmp_path / ".config"))
    zipped = []
    monkeypatch.setattr(macro, "zip_capture_in_background", zipped.append)
    macro.state.run_number = 7
    macro.state.moments = None
    macro.state.capture = None
    yield zipped
    macro.finish_moments()
    macro.state.capture = None


def test_the_moments_are_captured_with_a_budget_and_no_inputs_unless_switched_off_or_a_capture_runs(macro, moments):
    macro.update_game_config(lambda c: c.update(keep_moments=False))
    macro.start_moments()
    assert macro.state.moments is None
    macro.update_game_config(lambda c: c.update(keep_moments=True))
    macro.state.capture = FakeCapture("x")
    macro.start_moments()
    assert macro.state.moments is None                                           # (the hotkey's capture is running)
    macro.state.capture = None
    macro.start_moments()
    capture = macro.state.moments
    assert capture is FakeCapture.made[-1] and os.path.basename(capture.folder).startswith("run7_")
    assert capture.options["max_zips"] == macro.cfg.MOMENTS_ZIPS and capture.options["inputs"] is False and capture.options["interval"] == macro.cfg.MOMENTS_FRAME_INTERVAL


def test_log_lines_go_to_the_capture_and_the_bad_ones_mark_a_moment_at_most_once_in_a_while(macro, moments):
    macro.update_game_config(lambda c: c.update(keep_moments=True))
    macro.start_moments()
    capture = macro.state.moments
    macro.note_moment("resolved: hungry (30s)")
    macro.note_moment("FAILURE: hungry still showing after 75s")
    macro.note_moment("FAILURE: again, a moment later")
    assert [call for call in capture.calls if call[0] == "mark"] == [("mark", "FAILURE: hungry still showing after 75s")]
    assert len([call for call in capture.calls if call[0] == "note"]) == 3
    macro.state.moments_marked -= macro.cfg.MOMENTS_MARK_GAP
    macro.note_moment("scheduled rejoin: 3 hours")
    assert len([call for call in capture.calls if call[0] == "mark"]) == 2
    macro.state.moments = None
    macro.note_moment("FAILURE: nobody is listening")                           # (no capture: nothing happens)


def test_a_short_run_leaves_nothing_and_a_long_one_keeps_its_tail_and_is_zipped(macro, moments):
    macro.update_game_config(lambda c: c.update(keep_moments=True))
    macro.start_moments()
    short = macro.state.moments
    macro.finish_moments()
    assert macro.state.moments is None and not os.path.exists(short.folder) and moments == [] and ("tail", macro.cfg.MOMENTS_TAIL) not in short.calls
    macro.start_moments()
    long = macro.state.moments
    long.started -= macro.cfg.MOMENTS_MIN_RUN
    macro.finish_moments()
    assert os.path.exists(long.folder) and moments == [long.folder] and ("tail", macro.cfg.MOMENTS_TAIL) in long.calls and long.stopped_because == "the run ended"
    macro.finish_moments()                                                      # (none left: nothing happens)


def test_the_loop_starts_and_ends_the_capture_of_its_moments(macro, moments, monkeypatch, tmp_path):
    macro.update_game_config(lambda c: c.update(keep_moments=True))
    monkeypatch.setattr(macro.cfg, "RUN_COUNTER_PATH", str(tmp_path / "run_counter.txt"))
    monkeypatch.setattr(macro, "respawn_character", lambda: None)
    monkeypatch.setattr(macro, "check_focus", lambda: None)

    def stop_at_once():
        raise macro.StopRequested()
    monkeypatch.setattr(macro, "run_full_cycle", stop_at_once)
    with pytest.raises(macro.StopRequested):
        macro.run_workflow_loop()
    assert len(FakeCapture.made) == 1 and macro.state.moments is None
    assert any(call[0] == "stop" for call in FakeCapture.made[0].calls) and any(call == ("note", "LOOP stopped") for call in FakeCapture.made[0].calls)


def test_the_hotkey_capture_ends_the_capture_of_the_moments(macro, moments, monkeypatch, tmp_path):
    macro.update_game_config(lambda c: c.update(keep_moments=True))
    monkeypatch.setattr(macro.cfg, "CAPTURES_DIR", str(tmp_path / "captures"))
    macro.start_moments()
    mine = macro.state.moments
    started = macro.toggle_input_capture()
    assert macro.state.moments is None and any(call[0] == "stop" for call in mine.calls) and started is FakeCapture.made[-1] and started is not mine
    macro.state.capture = None


def test_a_capture_without_inputs_looks_at_its_own_pace_and_keeps_to_its_own_limits(macro, capture, monkeypatch):  
    monkeypatch.setattr(macro.cfg, "CAPTURE_FRAME_INTERVAL", 0.02)
    recorder = macro.InputCapture(capture["folder"], max_zips=3, interval=0.1, protected_max=2, max_minutes=1.0 / 60, inputs=False)
    assert recorder.interval == 0.1 and recorder.protected_max == 2 and recorder._gap == 0.1
    recorder.start()
    capture["held"] = {"w"}
    time.sleep(0.2)
    assert not test_capture.seen(capture["folder"], type="key_down")              # (no keys written: no poll thread)
    recorder.note("a line of the run log")
    assert test_capture.seen(capture["folder"], type="log", text="a line of the run log")
    assert test_capture.wait_for(lambda: recorder.stopped_because is not None, 5)                    # (its own time limit: a second, not the 15 minutes of a capture)
    recorder.stop()


def test_the_newest_run_of_the_log_is_the_one_a_report_is_drawn_of(macro, tmp_path, monkeypatch):
    monkeypatch.setattr(macro.cfg, "RUN_LOG_PATH", write_log(tmp_path))
    monkeypatch.setattr(macro.cfg, "RUNS_DIR", str(tmp_path / "runs"))
    assert macro.newest_run_in_log() == 328
    assert macro.report_from_outside(None) == "done" and os.path.exists(tmp_path / "runs" / "run328.png")
    assert macro.report_from_outside(327) == "done" and os.path.exists(tmp_path / "runs" / "run327.png")
    assert macro.report_from_outside(9999) == "given up: run 9999 is not in the log"
    monkeypatch.setattr(macro.cfg, "RUN_LOG_PATH", str(tmp_path / "missing.txt"))
    assert macro.newest_run_in_log() is None and macro.report_from_outside(None) == "given up: there is no run in the log"


def test_a_report_command_names_a_run_by_its_number_or_none(macro, tmp_path, monkeypatch):
    monkeypatch.setattr(macro.cfg, "COMMANDS_DIR", str(tmp_path / "commands"))
    for extra, taken in (({}, True), ({"run": 5}, True), ({"run": 0}, False), ({"run": "5"}, False), ({"run": True}, False)):
        write_command(macro, "report", **extra)
        assert (macro.read_command_with_id()[1] is not None) is taken, extra


def test_every_frame_says_what_the_macro_is_doing_and_a_task_names_its_activity(macro):
    macro.run_stats.cycles, macro.run_stats.last_detected = 9, ["hungry"]
    macro.run_stats.resolved = {"hungry": 2, "dirty": 1}
    macro.state.disabled_needs = {"camping"}
    assert macro.moments_context() == {"activity": "waiting for needs", "detected": ["hungry"], "cycles": 9, "resolved": 3, "failures": 0, "disabled": ["camping"], "task": False}
    with macro.task_unscrew(what="need handler: hungry"):
        assert macro.moments_context()["activity"] == "need handler: hungry" and macro.moments_context()["task"] is True
        with macro.task_unscrew(what="side action: pet_pen"):
            assert macro.state.activity == "side action: pet_pen"
        assert macro.state.activity == "need handler: hungry"
    assert macro.state.activity == ""


def test_everything_the_macro_prints_goes_to_output_log_while_the_capture_runs_and_no_longer(macro, moments):
    macro.update_game_config(lambda c: c.update(keep_moments=True))
    macro.start_moments()
    folder = macro.state.moments.folder
    macro.logger.debug("a debug line")
    macro.logger.info("an info line")
    text = open(os.path.join(folder, "output.log"), encoding="utf-8").read()
    assert "DEBUG a debug line" in text and "INFO an info line" in text
    macro.finish_moments()
    macro.logger.info("after the end")
    assert "after the end" not in open(os.path.join(folder, "output.log"), encoding="utf-8").read() if os.path.exists(os.path.join(folder, "output.log")) else True
    assert macro.state.moments_log is None and not [h for h in macro.logger.handlers if isinstance(h, macro.MomentsLog)]


def test_a_long_runs_folder_is_self_explaining_with_its_log_status_config_failures_and_moments(macro, moments, tmp_path, monkeypatch):
    cfg = macro.cfg
    monkeypatch.setattr(cfg, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(cfg, "STATUS_PATH", str(tmp_path / "status.json"))
    monkeypatch.setattr(cfg, "FAILURE_DIR", str(tmp_path / "failures"))
    os.makedirs(cfg.RUNS_DIR)
    open(os.path.join(cfg.RUNS_DIR, "run7.txt"), "w").write("[2026-11-06 10:00:00] [RUN 7] LOOP started\n")
    open(cfg.STATUS_PATH, "w").write('{"run": 7}')
    for name, run in (("20261106_100500_run7_hungry_still", 7), ("20261106_100600_run8_other", 8)):
        os.makedirs(os.path.join(cfg.FAILURE_DIR, name))
        open(os.path.join(cfg.FAILURE_DIR, name, "info.txt"), "w").write(f"reason: failure of run {run}\n")
        cv2.imwrite(os.path.join(cfg.FAILURE_DIR, name, "screen.png"), np.full((40, 60, 3), 80, np.uint8))
    macro.update_game_config(lambda c: c.update(keep_moments=True, private_server_link="https://www.roblox.com/share?code=SECRET"))
    macro.start_moments()
    capture = macro.state.moments
    macro.note_moment("FAILURE: hungry still showing after 75s (screenshot: x)")
    capture.started -= macro.cfg.MOMENTS_MIN_RUN + 100
    macro.finish_moments()
    folder = capture.folder
    assert sorted(f for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f))) == ["README.txt", "config.json", "failure_20261106_100500_run7_hungry_still.jpg", "failures.txt", "info.json", "output.log", "run_log.txt", "status.json"]
    readme = open(os.path.join(folder, "README.txt"), encoding="utf-8").read()
    assert "RUN 7" in readme and "FAILURE: hungry still showing" in readme and "events.jsonl" in readme and "private server link" in readme
    assert "failure of run 7" in open(os.path.join(folder, "failures.txt")).read() and "failure of run 8" not in open(os.path.join(folder, "failures.txt")).read()
    assert "SECRET" not in open(os.path.join(folder, "config.json")).read() and "game_config" in open(os.path.join(folder, "config.json")).read()
    info = json.load(open(os.path.join(folder, "info.json")))
    assert info["run"] == 7 and info["macro_version"] and info["moments"][0]["what"].startswith("FAILURE: hungry")


def test_the_zips_of_a_capture_hold_its_text_files_in_the_first_part_deflated(macro, tmp_path):
    folder = test_capture.fake_capture(str(tmp_path / "capture_y"), 3, 2000)
    for name in ("README.txt", "output.log", "run_log.txt"):
        open(os.path.join(folder, name), "w").write(name * 1000)
    paths = macro.zip_capture(folder)
    with zipfile.ZipFile(paths[0]) as z:
        assert z.namelist()[:3] == ["README.txt", "info.json", "events.jsonl"] and {"output.log", "run_log.txt"} <= set(z.namelist())
        assert z.getinfo("output.log").compress_type == zipfile.ZIP_DEFLATED and z.getinfo("output.log").compress_size < 1000
        assert next(z.getinfo(n) for n in z.namelist() if n.startswith("frame_")).compress_type == zipfile.ZIP_STORED
    assert sorted(n for p in paths for n in zipfile.ZipFile(p).namelist()) == sorted(f for f in os.listdir(folder) if f != "zips")
