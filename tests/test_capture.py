"""The input capture: keys, mouse buttons, mouse movement and screenshots, written to a folder of their own."""
import json
import os
import time

import numpy as np
import pytest


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


def events_of(folder):
    with open(os.path.join(folder, "events.jsonl"), encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def wait_for(condition, seconds=5.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def seen(folder, **match):
    try:
        return any(all(e.get(k) == v for k, v in match.items()) for e in events_of(folder))
    except (OSError, ValueError):
        return False


def test_keys_buttons_and_the_mouse_are_written_with_both_coordinate_systems(macro, capture):
    folder = capture["folder"]
    recorder = macro.InputCapture(folder)
    recorder.start()
    capture["held"] = {"w"}
    assert wait_for(lambda: seen(folder, type="key_down", key="w"))
    capture["position"] = (960, 540)
    capture["held"] = {"w", "mouse_left"}
    assert wait_for(lambda: seen(folder, type="mouse_down", button="left"))
    capture["held"] = set()
    assert wait_for(lambda: seen(folder, type="mouse_up", button="left") and seen(folder, type="key_up", key="w"))
    recorder.stop()
    events = events_of(folder)
    click = next(e for e in events if e["type"] == "mouse_down")
    assert (click["x"], click["y"], click["rx"], click["ry"]) == (960, 540, 960, 540)
    assert [e["t"] for e in events] == sorted(e["t"] for e in events)
    assert any(e["type"] == "move" and (e["x"], e["y"]) == (960, 540) for e in events)


def test_a_window_that_is_not_at_the_origin_gives_reference_coordinates(macro, capture, monkeypatch):
    monkeypatch.setattr(macro, "roblox_rect", lambda: (100, 50, 960, 540))       # half size, offset
    assert macro.to_reference(100 + 480, 50 + 270) == (960, 540)
    assert macro.to_screen(*macro.to_reference(300, 200)) == (300, 200)


def test_the_hotkey_is_not_recorded_and_nothing_held_means_no_key_events(macro, capture):
    folder = capture["folder"]
    recorder = macro.InputCapture(folder)
    recorder.start()
    capture["held"] = {macro.cfg.CAPTURE_HOTKEY}
    time.sleep(0.15)
    capture["held"] = set()
    time.sleep(0.05)
    recorder.stop()
    assert not [e for e in events_of(folder) if e["type"] in ("key_down", "key_up")]


def test_a_click_gets_a_screenshot_straight_away_and_frames_are_lossless_pngs_on_the_same_clock(macro, capture, monkeypatch):
    import cv2
    monkeypatch.setattr(macro.cfg, "CAPTURE_FRAME_INTERVAL", 30.0)         # only the first frame and click frames
    folder = capture["folder"]
    recorder = macro.InputCapture(folder)
    recorder.start()
    assert wait_for(lambda: seen(folder, type="frame"))
    first = len([e for e in events_of(folder) if e["type"] == "frame"])
    capture["held"] = {"mouse_right"}
    assert wait_for(lambda: len([e for e in events_of(folder) if e["type"] == "frame"]) > first)
    recorder.stop()
    frames = [e for e in events_of(folder) if e["type"] == "frame"]
    click = next(e for e in events_of(folder) if e["type"] == "mouse_down")
    assert frames[-1]["t"] >= click["t"]                                    # the extra frame is taken after the click
    image = cv2.imread(os.path.join(folder, frames[0]["file"]))
    assert image.shape == (90, 160, 3) and (image == 77).all()              # exactly what was captured
    assert frames[0]["file"].startswith("frame_000001_")
    assert sorted(f for f in os.listdir(folder) if f.endswith(".png")) == sorted(e["file"] for e in frames)


def test_the_summary_is_written_and_a_capture_that_runs_out_of_time_stops_itself(macro, capture, monkeypatch):
    monkeypatch.setattr(macro.cfg, "CAPTURE_MAX_MINUTES", 0.05 / 60)      # 50 ms
    folder = capture["folder"]
    recorder = macro.InputCapture(folder)
    recorder.start()
    assert wait_for(lambda: not recorder.running)
    assert recorder.stopped_because and "minute" in recorder.stopped_because
    summary = recorder.stop()
    info = json.load(open(os.path.join(folder, "info.json")))
    assert info["frames"] == recorder.frames and info["events"] == recorder.events and "frames" in summary
    assert info["stopped_because"] == recorder.stopped_because and info["reference"] == [macro.cfg.REFERENCE_WIDTH, macro.cfg.REFERENCE_HEIGHT]


def test_a_nearly_full_disk_stops_the_capture(macro, capture, monkeypatch):
    monkeypatch.setattr(macro.shutil, "disk_usage", lambda path: type("Usage", (), {"free": 1024})())
    recorder = macro.InputCapture(capture["folder"])
    recorder.start()
    assert wait_for(lambda: not recorder.running)
    recorder.stop()
    assert recorder.stopped_because == "the disk is nearly full" and recorder.frames == 0


def test_the_hotkey_starts_then_stops_and_the_oldest_captures_are_deleted(macro, capture, monkeypatch):
    monkeypatch.setattr(macro.cfg, "MAX_CAPTURES", 2)
    os.makedirs(macro.cfg.CAPTURES_DIR)
    for i in range(3):
        old = os.path.join(macro.cfg.CAPTURES_DIR, f"old{i}")
        os.makedirs(old)
        os.utime(old, (1000 + i, 1000 + i))
    first = macro.toggle_input_capture()
    assert first is not None and macro.state.capture is first and first.running
    kept = os.listdir(macro.cfg.CAPTURES_DIR)
    assert len(kept) == 2 and "old2" in kept and os.path.basename(first.folder) in kept          # room made for the new one
    assert macro.toggle_input_capture() is None and macro.state.capture is None and not first.running


def test_the_default_capture_folder_is_in_debug(macro):
    assert os.path.realpath(macro.cfg.CAPTURES_DIR) == os.path.realpath(os.path.join(macro.cfg.DEBUG_DIR, "captures"))


# --- unchanged frames are skipped ---

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


def test_a_screen_that_does_not_change_is_saved_once(macro, capture, screen):
    recorder = run_capture(macro, capture, lambda r: r.skipped >= 5)
    assert recorder.frames == 1 and len([f for f in os.listdir(capture["folder"]) if f.endswith(".png")]) == 1


def test_a_small_change_like_a_ticking_timer_is_skipped_but_something_moving_is_saved(macro, capture, screen):
    recorder = macro.InputCapture(capture["folder"])
    recorder.start()
    assert wait_for(lambda: recorder.frames == 1)
    screen["img"][40:70, 780:860] = 255                               # a timer's digits: about 0.1% of the picture
    skipped = recorder.skipped
    assert wait_for(lambda: recorder.skipped >= skipped + 4) and recorder.frames == 1
    screen["img"][300:500, 700:900] = 20                              # a character: about 2%
    assert wait_for(lambda: recorder.frames == 2)
    recorder.stop()


def test_a_click_always_gets_a_frame_even_when_nothing_changed(macro, capture, screen):
    recorder = macro.InputCapture(capture["folder"])
    recorder.start()
    assert wait_for(lambda: recorder.frames == 1 and recorder.skipped >= 2)
    capture["held"] = {"mouse_left"}
    assert wait_for(lambda: recorder.frames == 2)
    recorder.stop()


def test_a_long_stretch_without_a_frame_forces_one(macro, capture, screen, monkeypatch):
    monkeypatch.setattr(macro.cfg, "CAPTURE_MAX_SKIP", 0.15)
    recorder = run_capture(macro, capture, lambda r: r.frames >= 3)
    assert recorder.skipped >= 1                                       # some looks were skipped in between


def test_the_summary_counts_the_skipped_frames(macro, capture, screen):
    recorder = run_capture(macro, capture, lambda r: r.skipped >= 3)
    info = json.load(open(os.path.join(capture["folder"], "info.json")))
    assert info["frames_skipped"] == recorder.skipped and info["frames"] == 1


def test_a_capture_that_stopped_by_itself_is_tidied_up_and_the_next_press_starts_a_new_one(macro, capture, monkeypatch):
    monkeypatch.setattr(macro.cfg, "CAPTURE_MAX_MINUTES", 0.02 / 60)
    first = macro.toggle_input_capture()
    assert wait_for(lambda: not first.running)
    monkeypatch.setattr(macro.cfg, "CAPTURE_MAX_MINUTES", 15)
    time.sleep(1.1)                                                                 # (folder names carry the second)
    second = macro.toggle_input_capture()
    assert second is not None and second is not first and second.running
    macro.toggle_input_capture()


def test_the_state_snapshot_shows_a_running_capture_as_its_folder(macro, capture):
    import json as _json
    recorder = macro.toggle_input_capture()
    try:
        assert macro.state.snapshot()["capture"] == recorder.folder
        _json.dumps(macro.state.snapshot())
    finally:
        macro.toggle_input_capture()


def test_the_key_names_cover_what_the_game_uses(macro):
    names = set(macro.VK_NAMES.values())
    assert {"w", "a", "s", "d", "space", "shift", "e", "r", "mouse_left", "mouse_right", "esc", "enter", "1", "f1"} <= names
    assert len(names) == len(macro.VK_NAMES)                                         # no name used twice


# --- zips for sending ---

def fake_capture(folder, frames, size):
    os.makedirs(folder)
    rng = np.random.RandomState(0)
    for n in range(1, frames + 1):
        with open(os.path.join(folder, f"frame_{n:06d}_{n * 0.5:.3f}.png"), "wb") as f:
            f.write(rng.bytes(size))
    with open(os.path.join(folder, "events.jsonl"), "w") as f:
        f.write('{"t": 0.1, "type": "move"}\n')
    with open(os.path.join(folder, "info.json"), "w") as f:
        f.write("{}")
    return folder


def test_a_capture_is_split_into_zips_under_the_limit_with_every_file_once(macro, tmp_path, monkeypatch):
    import zipfile
    monkeypatch.setattr(macro.cfg, "CAPTURE_ZIP_MB", 0.01)                          # ~10 KB: three 4 KB frames to a zip
    folder = fake_capture(str(tmp_path / "capture_x"), 10, 4000)
    paths = macro.zip_capture(folder)
    assert len(paths) >= 4 and [os.path.basename(p) for p in paths][:2] == ["capture_x_part01.zip", "capture_x_part02.zip"]
    assert all(os.path.getsize(p) <= macro.cfg.CAPTURE_ZIP_MB * 1024 * 1024 + 500 for p in paths)       # (a zip's own headers)
    members = [zipfile.ZipFile(p).namelist() for p in paths]
    flat = [name for names in members for name in names]
    assert sorted(flat) == sorted([f for f in os.listdir(folder) if f != "zips"])           # everything, none twice
    assert {"events.jsonl", "info.json"} <= set(members[0])                                    # the small files are in the first part
    frames = [n for names in members for n in names if n.startswith("frame_")]
    assert frames == sorted(frames)                                                            # in time order across the parts
    with zipfile.ZipFile(paths[1]) as z:
        name = next(n for n in z.namelist() if n.startswith("frame_"))
        assert z.read(name) == open(os.path.join(folder, name), "rb").read()                  # byte for byte


def test_a_small_capture_is_one_zip(macro, tmp_path):
    folder = fake_capture(str(tmp_path / "capture_y"), 3, 1000)
    assert len(macro.zip_capture(folder)) == 1


def test_stopping_a_capture_with_the_hotkey_zips_it(macro, capture):
    first = macro.toggle_input_capture()
    wait_for(lambda: first.frames >= 1)
    macro.toggle_input_capture()
    assert wait_for(lambda: os.path.isdir(os.path.join(first.folder, "zips")) and os.listdir(os.path.join(first.folder, "zips")))


def test_split_zip_cuts_an_existing_zip_into_parts_without_changing_a_file(tmp_path, monkeypatch):
    import zipfile
    monkeypatch.syspath_prepend(os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools"))
    import split_zip
    source = str(tmp_path / "big.zip")
    rng = np.random.RandomState(1)
    originals = {f"capture_x/frame_{n:06d}.png": rng.bytes(3000) for n in range(1, 9)}
    originals["capture_x/events.jsonl"] = b'{"t": 1}\n'
    with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in originals.items():
            z.writestr(name, data)
    paths = split_zip.split_zip(source, 0.007)                                       # ~7 KB: two frames to a part
    assert len(paths) >= 4 and paths[0].endswith("big_part01.zip")
    got = {}
    for p in paths:
        assert os.path.getsize(p) < 0.007 * 1024 * 1024 + 800
        with zipfile.ZipFile(p) as z:
            for name in z.namelist():
                assert name not in got                                              # none twice
                got[name] = z.read(name)
    assert got == originals                                                         # none lost, none changed, paths kept
    assert list(got) == list(originals)                                             # in the original order
    assert len(split_zip.split_zip(source, 25)) == 1


# --- the automatic capture of minigame runs ---

def test_a_capture_with_a_budget_thins_its_frames_out_evenly_and_keeps_the_first_and_the_newest(macro, capture, monkeypatch):
    rng = np.random.default_rng(1)
    monkeypatch.setattr(macro, "grab_screen", lambda: rng.integers(0, 256, (90, 160, 3), dtype=np.uint8))   # always different, never compressible
    monkeypatch.setattr(macro.cfg, "CAPTURE_FRAME_INTERVAL", 0.01)
    folder = capture["folder"]
    budget_mb = 0.25                                                          # about six frames
    recorder = macro.InputCapture(folder, budget_mb=budget_mb)
    recorder.start()
    assert wait_for(lambda: recorder.frames >= 12 and recorder.dropped >= 3, seconds=20)
    recorder.stop()
    on_disk = sorted(f for f in os.listdir(folder) if f.endswith(".png"))
    assert sum(os.path.getsize(os.path.join(folder, f)) for f in on_disk) <= budget_mb * 1024 * 1024
    assert on_disk[0].startswith("frame_000001_")                              # the start of the run is still there
    assert on_disk[-1].startswith(f"frame_{recorder.frames:06d}_")             # and the newest frame
    events = events_of(folder)
    dropped = {e["file"] for e in events if e["type"] == "frame_dropped"}
    assert len(dropped) == recorder.dropped and dropped.isdisjoint(on_disk)
    assert {e["file"] for e in events if e["type"] == "frame"} == set(on_disk) | dropped
    info = json.load(open(os.path.join(folder, "info.json")))
    assert info["frames_dropped"] == recorder.dropped and info["budget_mb"] == budget_mb


def test_frames_are_spread_over_the_run_not_all_at_its_start_or_end(macro, capture, monkeypatch):
    rng = np.random.default_rng(2)
    monkeypatch.setattr(macro, "grab_screen", lambda: rng.integers(0, 256, (90, 160, 3), dtype=np.uint8))
    monkeypatch.setattr(macro.cfg, "CAPTURE_FRAME_INTERVAL", 0.01)
    recorder = macro.InputCapture(capture["folder"], budget_mb=0.25)
    recorder.start()
    assert wait_for(lambda: recorder.frames >= 12 and recorder.dropped >= 3, seconds=20)
    recorder.stop()
    times = [float(f.split("_")[2][:-4]) for f in sorted(os.listdir(capture["folder"])) if f.endswith(".png")]
    gaps = np.diff(times)
    assert len(times) >= 3 and gaps.max() <= 4 * gaps.min()                    # no big hole anywhere in the run
    saved = np.diff([e["t"] for e in events_of(capture["folder"]) if e["type"] == "frame"])
    assert saved[-1] >= 2 * saved[0]                                           # frames were taken further apart as the run went on


def test_a_capture_without_a_budget_keeps_every_frame(macro, capture, monkeypatch):
    rng = np.random.default_rng(3)
    monkeypatch.setattr(macro, "grab_screen", lambda: rng.integers(0, 256, (90, 160, 3), dtype=np.uint8))
    recorder = macro.InputCapture(capture["folder"])
    recorder.start()
    assert wait_for(lambda: recorder.frames >= 8)
    recorder.stop()
    assert recorder.dropped == 0 and len([f for f in os.listdir(capture["folder"]) if f.endswith(".png")]) == recorder.frames


@pytest.fixture
def run_folder(macro, capture, monkeypatch, tmp_path):
    monkeypatch.setattr(macro.cfg, "RUN_CAPTURES_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(macro.cfg, "MINIGAME_CAPTURE", True)
    return capture


def test_a_minigame_run_is_recorded_with_a_budget_and_finished_with_its_outcome_and_a_zip(macro, run_folder):
    recorder = macro.start_run_capture("hauntlet_2")
    assert recorder is not None and recorder.budget_mb == macro.cfg.RUN_CAPTURE_MB and recorder.running
    assert os.path.dirname(recorder.folder) == macro.cfg.RUN_CAPTURES_DIR and os.path.basename(recorder.folder).startswith("hauntlet_2_")
    assert wait_for(lambda: recorder.frames >= 1)
    macro.finish_run_capture(recorder, "won")
    assert not recorder.running
    assert json.load(open(os.path.join(recorder.folder, "info.json")))["outcome"] == "won"
    assert wait_for(lambda: os.path.isdir(os.path.join(recorder.folder, "zips")) and os.listdir(os.path.join(recorder.folder, "zips")))


def test_no_run_is_recorded_when_it_is_switched_off_or_a_capture_is_already_running(macro, run_folder, monkeypatch):
    monkeypatch.setattr(macro.cfg, "MINIGAME_CAPTURE", False)
    assert macro.start_run_capture("hauntlet_2") is None
    monkeypatch.setattr(macro.cfg, "MINIGAME_CAPTURE", True)
    manual = macro.toggle_input_capture()
    assert manual is not None
    assert macro.start_run_capture("hauntlet_2") is None                      # one capture at a time
    macro.toggle_input_capture()
    macro.finish_run_capture(None, "won")                                    # nothing to finish is no error


def test_the_oldest_run_captures_are_deleted(macro, run_folder, monkeypatch):
    monkeypatch.setattr(macro.cfg, "MAX_RUN_CAPTURES", 2)
    os.makedirs(macro.cfg.RUN_CAPTURES_DIR)
    for i in range(3):
        old = os.path.join(macro.cfg.RUN_CAPTURES_DIR, f"old{i}")
        os.makedirs(old)
        os.utime(old, (1000 + i, 1000 + i))
    recorder = macro.start_run_capture("ghost_gallery")
    kept = os.listdir(macro.cfg.RUN_CAPTURES_DIR)
    macro.finish_run_capture(recorder, "won")
    assert len(kept) == 2 and "old2" in kept and os.path.basename(recorder.folder) in kept


def test_a_recording_that_cannot_start_does_not_stop_the_minigame(macro, run_folder, monkeypatch, capsys):
    def broken(self):
        raise OSError("disk says no")
    monkeypatch.setattr(macro.InputCapture, "start", broken)
    assert macro.start_run_capture("hauntlet_2") is None
    assert "Couldn't start recording" in capsys.readouterr().out


def test_the_default_run_capture_folder_is_in_debug_and_the_budget_leaves_room_for_the_zip_limit(macro):
    assert os.path.realpath(macro.cfg.RUN_CAPTURES_DIR) == os.path.realpath(os.path.join(macro.cfg.DEBUG_DIR, "minigame_captures"))
    assert macro.cfg.RUN_CAPTURE_MB <= macro.cfg.CAPTURE_ZIP_MB                # a run fits one zip


@pytest.mark.parametrize("result, outcome", [(True, "won"), (False, "did not reach the victory screen in time"), ("raises", "interrupted")])
def test_play_minigame_records_the_run_and_says_how_it_ended(macro, monkeypatch, result, outcome):
    started, finished = [], []
    monkeypatch.setattr(macro, "start_run_capture", lambda label: started.append(label) or "the capture")
    monkeypatch.setattr(macro, "finish_run_capture", lambda capture, how: finished.append((capture, how)))
    for name in ("release_all_inputs", "respawn_character"):
        monkeypatch.setattr(macro, name, lambda *a, **k: None)
    monkeypatch.setattr(macro, "hover_click", lambda *a, **k: None)
    monkeypatch.setattr(macro, "click_nice_until_gone", lambda: True)
    monkeypatch.setattr(macro, "log_failure", lambda reason, screenshot=True: None)

    def player(deadline, watch):
        if result == "raises":
            raise macro.StopRequested()
        return result
    monkeypatch.setitem(macro.MINIGAME_PLAYERS, "ghost_gallery", player)
    if result == "raises":
        with pytest.raises(macro.StopRequested):
            macro.play_minigame("ghost_gallery")
    else:
        assert macro.play_minigame("ghost_gallery") is result
    assert started == ["ghost_gallery"] and finished == [("the capture", outcome)]
