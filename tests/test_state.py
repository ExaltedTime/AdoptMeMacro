"""State, stats and the logic that runs on them: stuck needs, failures, the quiet and idle checks, stopping."""
import json
import os
import threading
import time

import pytest


def test_record_detection_disables_a_need_attempted_and_still_there(macro, monkeypatch):
    monkeypatch.setattr(macro.cfg, "ENABLED_NEEDS", {"hungry"})
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


@pytest.fixture
def one_need(macro, monkeypatch):
    """process_needs() with a single 'hungry' icon on a dark screen and a handler that succeeds."""
    import numpy as np
    screen = np.zeros((1080, 1920, 3), np.uint8)
    monkeypatch.setattr(macro, "focus_roblox_click", lambda: True)
    monkeypatch.setattr(macro, "detect_need_icons", lambda save_debug=False: ([(100, 50, 30)], screen))
    monkeypatch.setattr(macro, "identify_icons", lambda found, img: iter([(0, screen[:10, :10], "hungry", 0.9)]))
    monkeypatch.setattr(macro, "respawn_character", lambda: None)
    monkeypatch.setattr(macro, "check_focus", lambda: None)

    class Handler:
        def handle(self):
            return True
    monkeypatch.setattr(macro, "get_need_handler", lambda name: Handler())
    return screen


def test_process_needs_resolves_a_need_and_counts_it(macro, one_need):
    macro.state.last_detection = time.time() - 100
    macro.state.last_progress = time.time() - 100
    assert macro.process_needs() is True
    assert macro.run_stats.resolved == {"hungry": 1} and macro.run_stats.last_resolved["need"] == "hungry"
    assert time.time() - macro.state.last_progress < 1 and time.time() - macro.state.last_detection < 1
    assert "detected: hungry" in open(macro.cfg.RUN_LOG_PATH).read()
    assert "resolved: hungry (" in open(macro.cfg.RUN_LOG_PATH).read()


def test_a_detected_need_that_cannot_be_resolved_still_counts_as_detected(macro, one_need, monkeypatch):
    class Failing:
        def handle(self):
            return False
    monkeypatch.setattr(macro, "get_need_handler", lambda name: Failing())
    macro.state.last_detection = time.time() - 100
    stalled = macro.state.last_progress = time.time() - 100
    assert macro.process_needs() is False
    assert time.time() - macro.state.last_detection < 1                 # seen - so no idle respawn for it
    assert macro.state.last_progress == stalled                          # but not progress
    assert macro.run_stats.failed == {"hungry": 1}


def test_an_unreadable_need_bar_is_not_a_reason_to_respawn(macro, one_need, monkeypatch):
    import numpy as np
    monkeypatch.setattr(macro, "detect_need_icons", lambda save_debug=False: ([], np.full((1080, 1920, 3), 255, np.uint8)))
    macro.state.last_detection = time.time() - 1000
    assert macro.process_needs() is False
    assert time.time() - macro.state.last_detection < 1


def test_no_icons_on_a_readable_bar_leaves_the_idle_clock_running(macro, one_need, monkeypatch):
    import numpy as np
    monkeypatch.setattr(macro, "detect_need_icons", lambda save_debug=False: ([], np.zeros((1080, 1920, 3), np.uint8)))
    before = macro.state.last_detection = time.time() - 1000
    assert macro.process_needs() is False
    assert macro.state.last_detection == before


def test_a_popup_abort_counts_as_an_attempt_but_a_minigame_does_not(macro, one_need, monkeypatch):
    def interrupted(reason):
        class Handler:
            def handle(self):
                raise macro.TaskInterrupted(reason)
        monkeypatch.setattr(macro, "get_need_handler", lambda name: Handler())
        assert macro.process_needs() is True

    interrupted("a popup")
    assert macro.state.attempted_last == {"hungry"}         # so a need the popup keeps spoiling gets disabled
    interrupted("a minigame")
    assert macro.state.attempted_last == set()


def test_a_need_the_popup_keeps_spoiling_is_eventually_disabled(macro, one_need, monkeypatch):
    monkeypatch.setattr(macro.cfg, "ENABLED_NEEDS", {"hungry"})

    class Handler:
        def handle(self):
            raise macro.TaskInterrupted("a popup")
    monkeypatch.setattr(macro, "get_need_handler", lambda name: Handler())
    for _ in range(macro.cfg.NEED_STUCK_CHECKS + 1):
        macro.process_needs()
    assert "hungry" in macro.state.disabled_needs


def test_a_second_disconnect_during_setup_does_not_end_the_run(macro, monkeypatch):
    """rejoin_game() -> setup_game() -> (disconnect) -> nested recovery -> TaskInterrupted must not escape."""
    import numpy as np
    monkeypatch.setattr(macro, "grab_full_screen", lambda: np.zeros((4, 4, 3), np.uint8))
    monkeypatch.setattr(macro, "grab_screen", lambda: (_ for _ in ()).throw(RuntimeError("no window")))
    monkeypatch.setattr(macro, "rejoin_reason", lambda: ("disconnect", "disconnected from the game"))
    monkeypatch.setattr(macro, "leave_and_rejoin", lambda clean_leave=True: True)

    def setup_that_is_interrupted():
        raise macro.TaskInterrupted("a rejoin")
    monkeypatch.setattr(macro, "setup_game", setup_that_is_interrupted)
    macro.state.disabled_needs.add("pizza")
    assert macro.rejoin_game() is True
    assert not macro.state.disabled_needs
    log = open(macro.cfg.RUN_LOG_PATH).read()
    assert "setup interrupted by a rejoin" in log and "recovery complete" in log


def test_every_run_gets_its_own_log_file_and_the_combined_log_keeps_everything(macro):
    macro.log_run_event("before any run")                     # no run number yet: only in the combined log
    macro.state.run_number = 7
    macro.log_run_event("resolved: hungry (28s)")
    macro.state.run_number = 8
    macro.log_run_event("detected: pizza")
    macro.log_run_event("resolved: pizza (80s)")
    own = os.path.join(macro.cfg.RUN_LOGS_DIR)
    assert sorted(os.listdir(own)) == ["run7.txt", "run8.txt"]
    assert open(os.path.join(own, "run7.txt")).read().count("\n") == 1
    eight = open(os.path.join(own, "run8.txt")).read().splitlines()
    assert len(eight) == 2 and all("[RUN 8]" in line for line in eight)
    combined = open(macro.cfg.RUN_LOG_PATH).read().splitlines()
    assert len(combined) == 4 and "[RUN None] before any run" in combined[0]
    assert macro.parse_run(macro.cfg.RUN_LOG_PATH, 8)["resolved"][0][1:] == ("pizza", 80)    # the report still reads the combined log


def test_a_run_log_that_cannot_be_written_does_not_lose_the_combined_line(macro, monkeypatch):
    macro.state.run_number = 3
    monkeypatch.setattr(macro.cfg, "RUN_LOGS_DIR", os.path.join(macro.cfg.RUN_LOG_PATH, "not-a-folder"))   # a path under a file
    macro.log_run_event("resolved: hungry (28s)")
    assert "resolved: hungry" in open(macro.cfg.RUN_LOG_PATH).read()


@pytest.mark.parametrize("need, expected", [("camping", 90.0), ("pizza", 90.0)])
def test_teleport_needs_wait_longer_for_the_need_to_clear(macro, monkeypatch, need, expected):
    waited = []
    monkeypatch.setattr(macro, "focus_roblox", lambda: True)
    monkeypatch.setattr(macro, "teleport_to", lambda pos: None)
    monkeypatch.setattr(macro, "equip_favorite_vehicle", lambda: None)       # camping flies while Halloween is on
    monkeypatch.setattr(macro, "hold_key", lambda key, duration: None)
    monkeypatch.setattr(macro, "wait_interruptible", lambda duration: None)
    monkeypatch.setattr(macro, "wait_until_need_gone", lambda name, max_wait=60.0, **kw: waited.append(max_wait))
    assert macro.TeleportWalkNeedHandler(need).handle() is True
    assert waited == [expected] and macro.cfg.TELEPORT_NEED_GONE_MAX_WAIT > macro.cfg.NEED_GONE_MAX_WAIT


def test_a_need_can_set_its_own_wait(macro, monkeypatch):
    waited = []
    monkeypatch.setitem(macro.cfg.TELEPORT_WALK_NEEDS, "camping", {**macro.cfg.TELEPORT_WALK_NEEDS["camping"], "max_wait": 150.0})
    monkeypatch.setattr(macro, "focus_roblox", lambda: True)
    monkeypatch.setattr(macro, "teleport_to", lambda pos: None)
    monkeypatch.setattr(macro, "equip_favorite_vehicle", lambda: None)       # camping flies while Halloween is on
    monkeypatch.setattr(macro, "hold_key", lambda key, duration: None)
    monkeypatch.setattr(macro, "wait_interruptible", lambda duration: None)
    monkeypatch.setattr(macro, "wait_until_need_gone", lambda name, max_wait=60.0, **kw: waited.append(max_wait))
    macro.TeleportWalkNeedHandler("camping").handle()
    assert waited == [150.0]
