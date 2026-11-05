"""The tests of capture that are about the event (Halloween 2026): scripts/Halloween2026.py, which the macro does not import."""
import json
import os
import time
import numpy as np
import pytest
from scripts import Halloween2026 as hw  # noqa: E402


@pytest.fixture
def capture(macro, monkeypatch, tmp_path):
    """A capture folder in tmp_path with the keyboard, mouse and screen replaced by what the test sets: held (the keys
    and buttons down), position, and a small made-up screen."""
    world = {"held": set(), "position": (100, 200)}
    monkeypatch.setattr(macro, "pressed_inputs", lambda: set(world["held"]))
    monkeypatch.setattr(macro, "cursor_position", lambda: world["position"])
    monkeypatch.setattr(macro, "roblox_rect", lambda: (0, 0, 1920, 1080))
    monkeypatch.setattr(macro, "grab_screen", lambda: np.full((90, 160, 3), 77, np.uint8))
    monkeypatch.setattr(macro.cfg, "CAPTURES_DIR", str(tmp_path / "captures"))
    monkeypatch.setattr(macro.cfg, "CAPTURE_POLL_INTERVAL", 0.005)
    monkeypatch.setattr(macro.cfg, "CAPTURE_MOVE_INTERVAL", 0.0)
    monkeypatch.setattr(macro.cfg, "CAPTURE_FRAME_INTERVAL", 0.05)
    world["folder"] = str(tmp_path / "capture")
    return world


def wait_for(condition, seconds=5.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def screen(macro, capture, monkeypatch):
    """The screen the capture sees, as a mutable array; looked at every 20 ms; a long wait before a frame is forced."""
    picture = {"img": np.full((1080, 1920, 3), 90, np.uint8)}
    monkeypatch.setattr(macro, "grab_screen", lambda: picture["img"].copy())
    monkeypatch.setattr(macro.cfg, "CAPTURE_FRAME_INTERVAL", 0.02)
    monkeypatch.setattr(macro.cfg, "CAPTURE_MAX_SKIP", 3600.0)
    return picture


def run_capture(macro, capture, until):
    recorder = macro.InputCapture(capture["folder"])
    recorder.start()
    assert wait_for(lambda: until(recorder))
    recorder.stop()
    return recorder


@pytest.fixture
def run_folder(macro, capture, monkeypatch, tmp_path):
    monkeypatch.setattr(hw, "RUN_CAPTURES_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(hw, "MINIGAME_CAPTURE", True)
    return capture


def test_a_minigame_run_is_recorded_with_a_budget_and_finished_with_its_outcome_and_a_zip(macro, run_folder):
    recorder = hw.start_run_capture("hauntlet_2")
    assert recorder is not None and recorder.max_zips == hw.RUN_CAPTURE_ZIPS and recorder.running
    assert os.path.dirname(recorder.folder) == hw.RUN_CAPTURES_DIR and os.path.basename(recorder.folder).startswith("hauntlet_2_")
    assert wait_for(lambda: recorder.frames >= 1)
    hw.finish_run_capture(recorder, "finished (the game over screen)", {"earned": 2500, "room": 7})
    assert not recorder.running and hw.event.run_capture is None
    info = json.load(open(os.path.join(recorder.folder, "info.json")))
    assert info["outcome"].startswith("finished") and info["stopped_because"] == "the minigame ended"
    assert info["earned"] == 2500 and info["room"] == 7                       # what the run came to is in its info.json
    assert os.path.basename(recorder.folder).endswith("_2500cc_room7")        # and in the folder's name, to tell it from the others (the zips are named after it)
    assert wait_for(lambda: any(n.startswith(os.path.basename(recorder.folder)) for n in os.listdir(os.path.join(recorder.folder, "zips"))) if os.path.isdir(os.path.join(recorder.folder, "zips")) else False)
    assert wait_for(lambda: os.path.isdir(os.path.join(recorder.folder, "zips")) and os.listdir(os.path.join(recorder.folder, "zips")))


def test_no_run_is_recorded_when_it_is_switched_off_or_a_capture_is_already_running(macro, run_folder, monkeypatch):
    monkeypatch.setattr(hw, "MINIGAME_CAPTURE", False)
    assert hw.start_run_capture("hauntlet_2") is None
    monkeypatch.setattr(hw, "MINIGAME_CAPTURE", True)
    manual = macro.toggle_input_capture()
    assert manual is not None
    assert hw.start_run_capture("hauntlet_2") is None                      # one capture at a time
    macro.toggle_input_capture()
    hw.finish_run_capture(None, "finished")                                    # nothing to finish is no error


def test_a_recording_that_cannot_start_does_not_stop_the_minigame(macro, run_folder, monkeypatch, caplog):
    def broken(self):
        raise OSError("disk says no")
    monkeypatch.setattr(macro.InputCapture, "start", broken)
    assert hw.start_run_capture("hauntlet_2") is None
    assert "Couldn't start recording" in caplog.text


@pytest.mark.parametrize("result, outcome", [(True, "finished (the game over screen)"), (False, "did not reach the game over screen in time"), ("raises", "interrupted")])
def test_play_minigame_records_the_run_and_says_how_it_ended(macro, monkeypatch, result, outcome):
    started, finished, order = [], [], []
    monkeypatch.setattr(hw, "start_run_capture", lambda label: started.append(label) or "the capture")
    monkeypatch.setattr(hw, "finish_run_capture", lambda capture, how, result=None: finished.append((capture, how)) or order.append("finished"))
    monkeypatch.setattr(hw, "read_candy_corn_settled", lambda tries=4: None)
    monkeypatch.setattr(hw, "record_minigame_result", lambda result: None)
    monkeypatch.setattr(macro, "send_macro_window_to_back", lambda: order.append("window back"))
    monkeypatch.setattr(macro, "bring_macro_window_to_front", lambda: order.append("window front"))
    for name in ("release_all_inputs", "respawn_character"):
        monkeypatch.setattr(macro, name, lambda *a, **k: None)
    monkeypatch.setattr(hw, "accept_minigame_popup", lambda: True)
    monkeypatch.setattr(macro, "hover_click", lambda *a, **k: None)
    monkeypatch.setattr(hw, "click_nice_until_gone", lambda: True)
    monkeypatch.setattr(macro, "log_failure", lambda reason, screenshot=True: None)

    def player(deadline, watch):
        if result == "raises":
            raise macro.StopRequested()
        return result
    monkeypatch.setitem(hw.MINIGAME_PLAYERS, "ghost_gallery", player)
    if result == "raises":
        with pytest.raises(macro.StopRequested):
            hw.play_minigame("ghost_gallery")
    else:
        assert hw.play_minigame("ghost_gallery") is result
    assert started == ["ghost_gallery"] and finished == [("the capture", outcome)]
    assert order == ["window back", "window front", "finished"]                                # the macro's window is out of the way while it is played, even when it is stopped


def test_a_run_that_went_wrong_keeps_the_frames_of_its_last_seconds(macro, capture, monkeypatch):
    monkeypatch.setattr(hw, "RUN_CAPTURE_TAIL", 15.0)                  # (the frames below are placed for this)
    recorder = macro.InputCapture(capture["folder"], max_zips=2)
    recorder.started = time.time() - 100
    recorder._kept = [(f"frame_{i + 1:06d}_{t:.3f}.png", 1) for i, t in enumerate((10.0, 50.0, 80.0, 90.0, 99.0))]
    recorder.protect_tail(15.0)
    assert recorder.protected == {"frame_000004_90.000.png", "frame_000005_99.000.png"}


def test_a_mark_with_no_recording_is_nothing(macro):
    hw.event.run_capture = None
    hw.mark_run_capture("anything")                                         # (no error)
    macro.log_failure("something", screenshot=False)




@pytest.fixture(autouse=True)
def capture_limits(macro, monkeypatch):
    """How many run captures are kept is tuned (magic_numbers.py); the tests of which are kept use these counts, whatever is set there."""
    monkeypatch.setattr(hw, "RUN_CAPTURES_BEST", 2)
    monkeypatch.setattr(hw, "RUN_CAPTURES_WORST", 2)
    monkeypatch.setattr(hw, "RUN_CAPTURES_FAILED", 3)


def runs_dir_with(macro, label, earned_by_name, failed=()):
    os.makedirs(hw.RUN_CAPTURES_DIR, exist_ok=True)
    for i, (name, earned) in enumerate(earned_by_name.items()):
        folder = os.path.join(hw.RUN_CAPTURES_DIR, f"{label}_{name}")
        os.makedirs(folder)
        with open(os.path.join(folder, "info.json"), "w") as f:
            json.dump(({} if earned == "unknown" else {"earned": earned}) | ({"finished": False} if name in failed else {}), f)
        os.utime(folder, (1000 + i, 1000 + i))                                 # in the order given: the last is the latest


def test_of_each_minigame_the_best_the_worst_and_the_latest_recordings_are_kept(macro, run_folder):
    runs_dir_with(macro, "hauntlet_2", {"a": 500, "b": 9000, "c": 100, "d": 4000, "e": 7000, "f": 300, "g": 2000, "latest": 2500})
    runs_dir_with(macro, "ghost_gallery", {"other": 1})
    hw.prune_run_captures("hauntlet_2")
    kept = {n.split("_", 2)[2] for n in os.listdir(hw.RUN_CAPTURES_DIR) if n.startswith("hauntlet_2_")}
    assert kept == {"b", "e", "c", "f", "latest"}                              # the two best, the two worst and the latest
    assert any(n.startswith("ghost_gallery_") for n in os.listdir(hw.RUN_CAPTURES_DIR))     # another minigame's are not touched


def test_the_latest_runs_that_failed_are_kept_whatever_they_earned(macro, run_folder, monkeypatch):
    monkeypatch.setattr(hw, "RUN_CAPTURES_FAILED", 2)
    runs_dir_with(macro, "hauntlet_2", {"f1": 5000, "f2": 5100, "f3": 5200, "ok": 4000, "best": 9000, "b2": 8000, "w1": 10, "w2": 20, "latest": 4500}, failed={"f1", "f2", "f3"})
    hw.prune_run_captures("hauntlet_2")
    kept = {n.split("_", 2)[2] for n in os.listdir(hw.RUN_CAPTURES_DIR)}
    assert kept == {"f2", "f3", "best", "b2", "w1", "w2", "latest"}             # (f1 is the third failure back; "ok" is nothing special)


def test_a_run_with_failures_logged_counts_as_failed_too(macro, run_folder):
    os.makedirs(hw.RUN_CAPTURES_DIR)
    for name, info in (("old", {"earned": 5000, "failures": 3}), ("older", {"earned": 5000, "failures": 0}), ("best", {"earned": 9000}), ("b2", {"earned": 8000}),
                       ("w1", {"earned": 1}), ("w2", {"earned": 2}), ("latest", {"earned": 3000})):
        os.makedirs(os.path.join(hw.RUN_CAPTURES_DIR, f"hauntlet_2_{name}"))
        with open(os.path.join(hw.RUN_CAPTURES_DIR, f"hauntlet_2_{name}", "info.json"), "w") as f:
            json.dump(info, f)
        os.utime(os.path.join(hw.RUN_CAPTURES_DIR, f"hauntlet_2_{name}"), (len(name), len(name)))
    hw.prune_run_captures("hauntlet_2")
    assert {n.split("_", 2)[2] for n in os.listdir(hw.RUN_CAPTURES_DIR)} == {"old", "best", "b2", "w1", "w2", "latest"}


def test_tidying_with_no_recordings_folder_is_nothing_and_the_latest_is_the_one_whose_name_is_latest(macro, run_folder, caplog):
    hw.prune_run_captures("hauntlet_2")                                      # (recording was never on: no folder, no message)
    assert caplog.text == ""
    runs_dir_with(macro, "hauntlet_2", {"20261014_100000_10cc_room3": 10, "20261014_110000": 9, "20261014_120000_8cc": 8, "20261014_090000": 7, "20261014_080000": 6, "20261014_070000": 5})
    for i, name in enumerate(sorted(os.listdir(hw.RUN_CAPTURES_DIR), reverse=True)):
        os.utime(os.path.join(hw.RUN_CAPTURES_DIR, name), (1000 + i, 1000 + i))      # (the folders' own times say the opposite of their names)
    hw.prune_run_captures("hauntlet_2")
    kept = {n[len("hauntlet_2_"):] for n in os.listdir(hw.RUN_CAPTURES_DIR)}
    assert kept == {"20261014_070000", "20261014_080000", "20261014_100000_10cc_room3", "20261014_110000", "20261014_120000_8cc"}      # the one named latest is kept though it is neither among the two best nor the two worst


def test_a_recording_whose_candy_corn_was_not_read_counts_as_one_of_the_worst_but_a_few_are_all_kept(macro, run_folder):
    runs_dir_with(macro, "ghost_gallery", {"a": 800, "b": "unknown", "c": 1200})
    hw.prune_run_captures("ghost_gallery")
    assert len(os.listdir(hw.RUN_CAPTURES_DIR)) == 3                    # fewer than five: all of them
    runs_dir_with(macro, "hauntlet_2", {"a": 800, "b": "unknown", "c": 1200, "d": 5, "e": 6, "f": 7, "g": 8, "h": 9})
    hw.prune_run_captures("hauntlet_2")
    kept = {n.split("_", 2)[2] for n in os.listdir(hw.RUN_CAPTURES_DIR) if n.startswith("hauntlet_2_")}
    assert kept == {"b", "d", "c", "a", "h"}                                     # the unread and the 5 (worst two), the 1200 and the 800 (best two), the latest


@pytest.mark.parametrize("outcome, keeps_its_tail", [("left the minigame (its overlay was gone for 10s)", True), ("finished (the game over screen)", False)])
def test_only_a_run_that_did_not_finish_keeps_the_frames_of_its_last_seconds(macro, run_folder, outcome, keeps_its_tail):
    recorder = hw.start_run_capture("hauntlet_2")
    assert wait_for(lambda: recorder.frames >= 1 and recorder._kept)                 # (the frame counts before it is on the list of what is kept)
    hw.finish_run_capture(recorder, outcome)
    assert bool(recorder.protected) is keeps_its_tail


def test_the_name_added_to_a_recording_says_what_the_run_earned_and_how_it_ended(macro):
    assert hw.run_capture_suffix("finished (the game over screen)", {"earned": 2105, "room": 10}) == "_2105cc_room10"
    assert hw.run_capture_suffix("finished (the game over screen)", {"earned": 2540}) == "_2540cc"
    assert hw.run_capture_suffix("left the minigame (its overlay was gone for 10s)", {"earned": None, "room": 1}) == "_cc-unread_room1_failed"
    assert hw.run_capture_suffix("interrupted", {}) == "_cc-unread_failed"
    assert hw.run_capture_suffix("finished (the game over screen)", {"earned": 2105, "room": 10, "failures": 2}) == "_2105cc_room10_failures"
