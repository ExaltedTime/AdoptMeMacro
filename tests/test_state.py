"""State, stats and the logic that runs on them: stuck needs, failures, the quiet and idle checks, stopping."""
import json
import os
import threading
import time

import pytest


def test_record_detection_disables_a_need_attempted_and_still_there(macro):
    macro.cfg.ENABLED_NEEDS.add("hungry")
    for _ in range(macro.cfg.NEED_STUCK_CHECKS):
        macro.state.attempted_last = {"hungry"}
        macro.record_detection(["hungry"])
    assert "hungry" in macro.state.disabled_needs


def test_record_detection_one_clear_check_breaks_the_streak(macro):
    for _ in range(macro.cfg.NEED_STUCK_CHECKS - 1):
        macro.state.attempted_last = {"hungry"}
        macro.record_detection(["hungry"])
    macro.state.attempted_last = {"hungry"}
    macro.record_detection([])                       # gone this time
    macro.state.attempted_last = {"hungry"}
    macro.record_detection(["hungry"])
    assert "hungry" not in macro.state.disabled_needs
    assert macro.state.attempt_streaks["hungry"] == 1


def test_record_detection_a_need_waiting_its_turn_is_not_an_attempt(macro):
    for _ in range(10):
        macro.record_detection(["hungry"])           # detected, never attempted
    assert not macro.state.disabled_needs


def test_run_stats_reset(macro):
    macro.state.run_number = 12
    macro.run_stats.cycles = 5
    macro.run_stats.resolved["hungry"] = 2
    macro.run_stats.reset()
    assert macro.run_stats.cycles == 0 and macro.run_stats.resolved == {}
    assert macro.run_stats.run == 12 and macro.run_stats.started


def test_write_status_has_the_documented_keys_and_is_valid_json(macro):
    macro.state.disabled_needs.add("pizza")
    macro.write_status()
    status = json.load(open(macro.cfg.STATUS_PATH))
    for key in ("run", "started", "cycles", "last_detected", "resolved", "failed", "failures", "quiet_minutes",
                "cpu_percent", "memory_mb", "recoveries", "disconnects", "scheduled_rejoins", "focus_resumes",
                "last_resolved", "last_failure", "updated", "disabled_needs"):
        assert key in status
    assert status["disabled_needs"] == ["pizza"]
    assert not os.path.exists(macro.cfg.STATUS_PATH + ".tmp")     # replaced atomically, no leftover


def test_state_snapshot_is_json_friendly(macro):
    macro.state.disabled_needs.add("pizza")
    macro.state.macro_window = object()
    snapshot = macro.state.snapshot()
    assert snapshot["disabled_needs"] == ["pizza"] and snapshot["macro_window"] == "set"
    json.dumps(snapshot)


def test_log_failure_makes_a_folder_with_screen_info_and_state(macro, monkeypatch):
    import numpy as np
    monkeypatch.setattr(macro, "grab_full_screen", lambda: np.zeros((20, 30, 3), np.uint8))
    monkeypatch.setattr(macro, "grab_screen", lambda: np.zeros((1080, 1920, 3), np.uint8))
    macro.state.run_number = 4
    macro.log_failure("pizza still showing after 60.0s")
    (folder,) = os.listdir(macro.cfg.FAILURE_DIR)
    assert folder.endswith("_run4_pizza_still_showing_after_60_0s")
    assert sorted(os.listdir(os.path.join(macro.cfg.FAILURE_DIR, folder))) == ["info.txt", "need_icons.png", "screen.png", "state.json"]
    saved = json.load(open(os.path.join(macro.cfg.FAILURE_DIR, folder, "state.json")))
    assert saved["state"]["run_number"] == 4 and "failures" in saved["run_stats"]
    assert macro.run_stats.failures == 1 and macro.run_stats.last_failure["reason"].startswith("pizza")
    assert "FAILURE: pizza still showing" in open(macro.cfg.RUN_LOG_PATH).read()


def test_failure_report_without_roblox_still_saves_the_screen(macro, monkeypatch):
    import numpy as np
    monkeypatch.setattr(macro, "grab_full_screen", lambda: np.zeros((20, 30, 3), np.uint8))
    def no_roblox():
        raise RuntimeError("no window")
    monkeypatch.setattr(macro, "grab_screen", no_roblox)
    shot = macro.save_failure_report("roblox isn't running")
    folder = os.path.join(macro.cfg.FAILURE_DIR, shot.split("/")[0])
    assert "screen.png" in os.listdir(folder) and "need_icons.png" not in os.listdir(folder)


def test_failure_folders_keep_only_the_newest(macro, monkeypatch):
    import numpy as np
    monkeypatch.setattr(macro, "grab_full_screen", lambda: np.zeros((4, 4, 3), np.uint8))
    monkeypatch.setattr(macro.cfg, "MAX_FAILURE_FOLDERS", 3)
    os.makedirs(macro.cfg.FAILURE_DIR)
    for i in range(5):
        os.makedirs(os.path.join(macro.cfg.FAILURE_DIR, f"2020010100000{i}_old"))
    macro.save_failure_report("new one")
    names = sorted(os.listdir(macro.cfg.FAILURE_DIR))
    assert len(names) == 3 and names[-1].endswith("_new_one") and not any(n.endswith("0_old") for n in names)


def test_check_quiet_logs_once_per_interval_and_reports_minutes(macro):
    macro.state.last_progress = time.time() - 11 * 60
    macro.check_quiet()
    macro.check_quiet()                                # the same stretch: not logged again
    assert macro.run_stats.quiet_minutes == 11
    lines = [l for l in open(macro.cfg.RUN_LOG_PATH) if "quiet:" in l]
    assert len(lines) == 1 and "no need resolved for 10 min" in lines[0]
    macro.state.last_progress = time.time()           # progress made
    macro.check_quiet()
    assert macro.run_stats.quiet_minutes == 0


@pytest.fixture
def respawns(macro, monkeypatch):
    calls = []
    monkeypatch.setattr(macro, "respawn_character", lambda: calls.append(time.time()))
    return calls


def test_no_respawn_before_the_interval(macro, respawns):
    macro.state.last_detection = time.time() - (macro.cfg.NO_DETECTION_RESPAWN_INTERVAL - 30)
    assert macro.respawn_if_no_detection() is False and not respawns


def test_respawns_after_the_interval_then_waits_a_full_interval_again(macro, respawns):
    macro.state.last_detection = time.time() - macro.cfg.NO_DETECTION_RESPAWN_INTERVAL - 1
    assert macro.respawn_if_no_detection() is True and len(respawns) == 1
    assert macro.respawn_if_no_detection() is False and len(respawns) == 1
    assert "no need detected for 4 min - respawning" in open(macro.cfg.RUN_LOG_PATH).read()


def test_the_idle_respawn_does_not_touch_the_progress_clock(macro, respawns, monkeypatch):
    """The 20 minute no-progress rejoin counts from last_progress, which a respawn must leave alone."""
    import numpy as np
    monkeypatch.setattr(macro, "roblox_windows", lambda: [object()])          # Roblox is running...
    monkeypatch.setattr(macro, "grab_screen", lambda: np.zeros((1080, 1920, 3), np.uint8))   # ...and not disconnected
    stalled = time.time() - macro.cfg.NO_PROGRESS_REJOIN_INTERVAL - 5
    macro.state.last_progress = stalled
    macro.state.last_rejoin = time.time()
    macro.state.last_detection = time.time() - macro.cfg.NO_DETECTION_RESPAWN_INTERVAL - 1
    assert macro.respawn_if_no_detection() is True
    assert macro.state.last_progress == stalled
    assert macro.rejoin_reason()[0] == "stalled"       # the rejoin still comes


def test_the_respawn_interval_is_shorter_than_the_rejoin_one(macro):
    assert macro.cfg.NO_DETECTION_RESPAWN_INTERVAL < macro.cfg.NO_PROGRESS_REJOIN_INTERVAL


def test_no_respawn_before_the_loop_has_started(macro, respawns):
    assert macro.state.last_detection is None
    assert macro.respawn_if_no_detection() is False


def test_stop_wakes_a_wait_at_once(macro, monkeypatch):
    monkeypatch.setattr(macro, "check_focus", lambda: None)
    threading.Timer(0.2, macro.STOP_EVENT.set).start()
    started = time.time()
    with pytest.raises(macro.StopRequested):
        macro.wait_interruptible(10)
    assert time.time() - started < 1.0


def test_a_wait_runs_its_full_length_when_not_stopped(macro, monkeypatch):
    monkeypatch.setattr(macro, "check_focus", lambda: None)
    started = time.time()
    macro.wait_interruptible(0.3)
    assert 0.25 < time.time() - started < 1.0


def test_stop_pressed_soon(macro, monkeypatch):
    monkeypatch.setattr(macro.cfg, "STOP_CLICK_GRACE", 0.3)
    assert macro.stop_pressed_soon() is False
    threading.Timer(0.05, macro.STOP_EVENT.set).start()
    assert macro.stop_pressed_soon() is True


def test_output_log_stays_open_rotates_and_reopens(macro, monkeypatch):
    monkeypatch.setattr(macro.cfg, "OUTPUT_LOG_MAX_BYTES", 300)
    for i in range(30):
        macro.log_output(f"line {i}\n")
    assert sorted(os.listdir(os.path.dirname(macro.cfg.OUTPUT_LOG_PATH))) >= ["output.log", "output.log.old"]
    text = open(macro.cfg.OUTPUT_LOG_PATH).read()
    assert text.rstrip().endswith("line 29") and text.startswith("[20")
    macro.close_output_log()
    os.remove(macro.cfg.OUTPUT_LOG_PATH)               # what Refresh debug does - must not be blocked
    macro.log_output("after\n")
    assert "after" in open(macro.cfg.OUTPUT_LOG_PATH).read()


def test_log_run_event_tags_the_run(macro):
    macro.state.run_number = 328
    macro.log_run_event("resolved: hungry (28s)")
    line = open(macro.cfg.RUN_LOG_PATH).read()
    assert "[RUN 328] resolved: hungry (28s)" in line
