"""State, stats and the logic that runs on them: stuck needs, failures, the quiet and idle checks, stopping."""
import json
import os
import threading
import types
import time

import numpy as np
import pytest


def test_record_detection_disables_a_need_attempted_and_still_there(macro, monkeypatch):
    monkeypatch.setattr(macro.cfg, "ENABLED_NEEDS", {"hungry"})
    for _ in range(macro.cfg.NEED_STUCK_CHECKS):
        macro.state.attempted_last = {"hungry"}
        macro.record_detection(["hungry"])
    assert "hungry" in macro.state.disabled_needs


def test_record_detection_one_clear_check_breaks_the_streak(macro, monkeypatch):
    monkeypatch.setattr(macro.cfg, "NEED_STUCK_CHECKS", 3)
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
    macro.state.attempted_last = {"sick"}                                          # sets must not end up in the JSON
    snapshot = macro.state.snapshot()
    assert snapshot["disabled_needs"] == ["pizza"] and snapshot["macro_window"] == "set"
    assert snapshot["attempted_last"] == ["sick"]
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
    interval = macro.cfg.QUIET_LOG_INTERVAL
    quiet = 2.5 * interval                             # two whole intervals and a bit
    macro.state.last_progress = time.time() - quiet
    macro.check_quiet()
    macro.check_quiet()                                # the same stretch: not logged again
    assert macro.run_stats.quiet_minutes == int(quiet // 60)
    lines = [l for l in open(macro.cfg.RUN_LOG_PATH) if "quiet:" in l]
    assert len(lines) == 1 and f"no need resolved for {2 * interval / 60:g} min" in lines[0]
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
    minutes = (macro.cfg.NO_DETECTION_RESPAWN_INTERVAL + 1) / 60
    assert f"no need detected for {minutes:.0f} min - respawning" in open(macro.cfg.RUN_LOG_PATH).read()


def test_the_idle_respawn_does_not_touch_the_progress_clock(macro, respawns, monkeypatch):
    """The 20 minute no-progress rejoin counts from last_progress, which a respawn must leave alone."""
    import numpy as np
    window = type("Window", (), {"title": "Roblox"})()
    monkeypatch.setattr(macro.gw, "getAllWindows", lambda: [window])           # Roblox is running...
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
        macro.wait_interruptible(60)
    assert time.time() - started < 30                  # a wait that ignored the stop would run the whole 60s


@pytest.fixture
def fake_clock(macro, monkeypatch):
    """time.monotonic and STOP_EVENT.wait on a clock the test controls: waits take no real time, and what they add
    up to can be asserted exactly (a real-time assertion fails on a slow machine)."""
    clock = {"now": 1000.0, "slept": []}
    monkeypatch.setattr(macro.time, "monotonic", lambda: clock["now"])

    def wait(timeout=None):
        clock["slept"].append(timeout)
        clock["now"] += timeout
        return False

    monkeypatch.setattr(macro.STOP_EVENT, "wait", wait)
    monkeypatch.setattr(macro, "check_focus", lambda: None)
    return clock


def test_a_wait_runs_its_full_length_when_not_stopped(macro, fake_clock):
    macro.wait_interruptible(0.3)
    assert sum(fake_clock["slept"]) == pytest.approx(0.3)
    assert max(fake_clock["slept"]) <= macro.cfg.STOP_CHECK_INTERVAL + 1e-9       # in chunks, so a stop is noticed within one


def test_stop_pressed_soon(macro, monkeypatch):
    monkeypatch.setattr(macro.cfg, "STOP_CLICK_GRACE", 0.05)
    assert macro.stop_pressed_soon() is False
    monkeypatch.setattr(macro.cfg, "STOP_CLICK_GRACE", 30)       # long: it returns the moment the stop is set
    threading.Timer(0.05, macro.STOP_EVENT.set).start()
    assert macro.stop_pressed_soon() is True


def test_output_log_has_every_line_with_its_level_rotates_and_reopens(macro, monkeypatch):
    monkeypatch.setattr(macro.cfg, "OUTPUT_LOG_MAX_BYTES", 300)
    for i in range(30):
        macro.logger.debug(f"line {i}")
    macro.logger.warning("a warning")
    assert sorted(os.listdir(os.path.dirname(macro.cfg.OUTPUT_LOG_PATH))) >= ["output.log", "output.log.old"]
    text = open(macro.cfg.OUTPUT_LOG_PATH).read()
    assert text.rstrip().endswith("WARNING: a warning") and text.startswith("[20") and "DEBUG: line 29" in text
    macro.close_output_log()
    os.remove(macro.cfg.OUTPUT_LOG_PATH)               # what Refresh debug does - must not be blocked
    macro.logger.info("after")
    assert "INFO: after" in open(macro.cfg.OUTPUT_LOG_PATH).read()


def test_a_status_line_is_written_to_output_log_once_in_a_row(macro):
    for _ in range(3):
        macro.logger.info("Waiting for a need... 0:05", extra={"status": True})
    macro.logger.info("something else")
    macro.logger.info("Waiting for a need... 0:10", extra={"status": True})
    text = open(macro.cfg.OUTPUT_LOG_PATH).read()
    assert text.count("Waiting for a need") == 2 and "something else" in text


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
    monkeypatch.setattr(macro.cfg, "SAVE_NEW_NEEDS", False)                  # (True would ask for a name for an unknown icon)
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


def test_no_icons_leaves_the_idle_clock_running(macro, one_need, monkeypatch):
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
    own = os.path.join(macro.cfg.RUNS_DIR)
    assert sorted(os.listdir(own)) == ["run7.txt", "run8.txt"]
    assert open(os.path.join(own, "run7.txt")).read().count("\n") == 1
    eight = open(os.path.join(own, "run8.txt")).read().splitlines()
    assert len(eight) == 2 and all("[RUN 8]" in line for line in eight)
    combined = open(macro.cfg.RUN_LOG_PATH).read().splitlines()
    assert len(combined) == 4 and "[RUN None] before any run" in combined[0]
    assert macro.parse_run(macro.cfg.RUN_LOG_PATH, 8)["resolved"][0][1:] == ("pizza", 80)    # the report still reads the combined log


def test_a_run_log_that_cannot_be_written_does_not_lose_the_combined_line(macro, monkeypatch):
    macro.state.run_number = 3
    monkeypatch.setattr(macro.cfg, "RUNS_DIR", os.path.join(macro.cfg.RUN_LOG_PATH, "not-a-folder"))   # a path under a file
    macro.log_run_event("resolved: hungry (28s)")
    assert "resolved: hungry" in open(macro.cfg.RUN_LOG_PATH).read()


@pytest.fixture
def walker(macro, monkeypatch):
    """A made-up teleport-walk need with an entry of its own, so these tests don't depend on how the real ones are
    tuned (or on HALLOWEEN, which decides which of them exist). Returns make(**extra_keys) -> handler, and the
    list of max_wait values wait_until_need_gone() was called with."""
    waited = []
    monkeypatch.setattr(macro, "focus_roblox", lambda: True)
    monkeypatch.setattr(macro, "teleport_to", lambda pos: None)
    monkeypatch.setattr(macro, "hold_key", lambda key, duration: None)
    monkeypatch.setattr(macro, "wait_interruptible", lambda duration: None)
    monkeypatch.setattr(macro, "wait_until_need_gone", lambda name, max_wait=60.0, **kw: waited.append(max_wait))

    def make(**extra):
        entry = dict(teleport_pos=(1, 2), steps=(("w", 1.0), ("a", 2.0)), helicopter=False, **extra)
        monkeypatch.setitem(macro.cfg.TELEPORT_WALK_NEEDS, "test_need", entry)
        return macro.TeleportWalkNeedHandler("test_need")

    make.waited = waited
    return make


def test_a_teleport_need_waits_for_the_need_to_clear_up_to_the_default_time(macro, walker):
    assert walker().handle() is True
    assert walker.waited == [macro.cfg.NEED_GONE_MAX_WAIT]


def test_a_need_can_set_its_own_wait(walker):
    walker(max_wait=150.0).handle()
    assert walker.waited == [150.0]


def test_rejoin_reason_lists_the_windows_once(macro, monkeypatch):
    import numpy as np
    calls = []
    window = type("Window", (), {"title": "Roblox"})()
    monkeypatch.setattr(macro.gw, "getAllWindows", lambda: calls.append(1) or [window])
    monkeypatch.setattr(macro, "grab_screen", lambda: np.zeros((1080, 1920, 3), np.uint8))
    assert macro.rejoin_reason() is None
    assert calls == [1]


def test_rejoin_reason_knows_a_crashed_or_missing_roblox(macro, monkeypatch):
    crash = type("Window", (), {"title": macro.cfg.ROBLOX_CRASH_WINDOW_TITLE})()
    other = type("Window", (), {"title": "Notepad"})()
    monkeypatch.setattr(macro.gw, "getAllWindows", lambda: [other, crash])
    assert macro.rejoin_reason() == ("disconnect", "Roblox crashed")
    monkeypatch.setattr(macro.gw, "getAllWindows", lambda: [other])
    assert macro.rejoin_reason() == ("disconnect", "Roblox isn't running")


POLL = 3.5          # the poll interval these tests use (passed in: the default is the tuned one, fixed when main.py is imported)


@pytest.fixture
def need_gone(macro, monkeypatch):
    """wait_until_need_gone() with scripted scan results: scans[i] is what the i-th look at the screen sees."""
    log = {"waits": [], "scans": []}
    monkeypatch.setattr(macro.cfg, "NEED_GONE_FLICKER_RECHECK_DELAY", 0.3)
    monkeypatch.setattr(macro, "wait_interruptible", lambda duration: log["waits"].append(round(duration, 2)))
    monkeypatch.setattr(macro, "check_focus", lambda: None)
    monkeypatch.setattr(macro, "log_failure", lambda reason, screenshot=True: log.setdefault("failures", []).append(reason))

    def script(*results):
        queue = list(results)

        def scan_needs(fresh=False):
            log["scans"].append(fresh)
            return queue.pop(0) if queue else ["hungry"]
        monkeypatch.setattr(macro, "scan_needs", scan_needs)
    log["script"] = script
    return log


def test_the_wait_ends_at_the_first_check_that_finds_the_need_gone(need_gone, macro):
    need_gone["script"](["hungry"], [], [])      # check 1: still there; check 2: gone on both looks
    macro.wait_until_need_gone("hungry", max_wait=60, poll_interval=POLL)
    assert need_gone["scans"] == [False, False, True]                      # the second look of the last check was a fresh search
    assert [w for w in need_gone["waits"] if w > 1] == [POLL] * 2   # polled every poll_interval, no extra confirmations
    assert "failures" not in need_gone


def test_a_flicker_on_the_first_look_does_not_end_the_wait(need_gone, macro):
    need_gone["script"]((([]), True), ["hungry"], ["hungry"], [], [])
    macro.wait_until_need_gone("hungry", max_wait=60)
    assert len(need_gone["scans"]) == 5                                    # check 1 missed it once but saw it on the second look


def test_a_need_that_never_clears_is_a_failure_after_the_wait(need_gone, macro, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(macro.time, "time", lambda: clock[0])
    monkeypatch.setattr(macro, "wait_interruptible", lambda duration: clock.__setitem__(0, clock[0] + duration))
    need_gone["script"]()                                                  # always still there
    macro.wait_until_need_gone("hungry", max_wait=10)
    assert need_gone["failures"] == ["hungry still showing after 10s"]


def test_config_reads_never_see_a_half_written_file(macro, monkeypatch, tmp_path):
    """The GUI thread and the worker both read and write the config: a reader must never get the defaults
    because it looked while the file was being rewritten (the private server link would read as empty)."""
    monkeypatch.setattr(macro.cfg, "GAME_CONFIG_PATH", str(tmp_path / ".config"))
    macro.update_game_config(lambda c: c.update(private_server_link="https://link", filler="x" * 200000))
    stop = threading.Event()

    def write():
        i = 0
        while not stop.is_set():
            i += 1
            macro.update_game_config(lambda c: c.update(money_collected=i))

    writer = threading.Thread(target=write)
    writer.start()
    try:
        end = time.time() + 0.7
        while time.time() < end:
            assert macro.load_game_config()["private_server_link"] == "https://link"
    finally:
        stop.set()
        writer.join()
    assert not (tmp_path / ".config.tmp").exists()


def test_a_button_action_cut_short_by_a_rejoin_is_done_not_a_crash(macro):
    """The Setup button runs setup_game() under the in-task checks; a disconnect rejoins (and sets up) mid-way and
    raises TaskInterrupted, which the worker must not report as a crash."""
    gui = object.__new__(macro.AdoptMeGUI)
    gui.is_running = False
    gui.pending, gui.pending_stems, gui.running_stem, gui.resume_loop = [], [], "", False
    statuses = []
    gui._set_running_ui = lambda running, status_text=None: statuses.append((running, status_text))
    gui.root = type("Root", (), {"after": lambda self, ms, fn: fn()})()

    def interrupted():
        raise macro.TaskInterrupted("a rejoin")

    macro.AdoptMeGUI.run_async(gui, interrupted)
    deadline = time.time() + 5
    while gui.is_running and time.time() < deadline:
        time.sleep(0.01)
    assert statuses[-1] == (False, "[DONE]")
    assert "interrupted by a rejoin" in open(macro.cfg.RUN_LOG_PATH).read()
    assert not any("crashed" in line for line in open(macro.cfg.RUN_LOG_PATH))


@pytest.mark.parametrize("failure, status", [
    (None, "[STOPPED]"),
    (lambda: (_ for _ in ()).throw(RuntimeError("boom")), "[ERROR]"),
])
def test_a_log_that_cannot_be_written_cannot_leave_the_ui_disabled(macro, monkeypatch, failure, status):
    """However the action ends, a locked log file must not stop the worker re-enabling the buttons (final_status bound
    first, the logging guarded) - and must not leave an exception behind in the thread either."""
    gui = object.__new__(macro.AdoptMeGUI)
    gui.is_running = False
    gui.pending, gui.pending_stems, gui.running_stem, gui.resume_loop = [], [], "", False
    statuses = []
    gui._set_running_ui = lambda running, status_text=None: statuses.append((running, status_text))
    gui.root = type("Root", (), {"after": lambda self, ms, fn: fn()})()

    def broken_log(message, *args, **kwargs):
        raise PermissionError("run_log.txt is locked")

    monkeypatch.setattr(macro, "log_run_event", broken_log)
    monkeypatch.setattr(macro, "log_failure", broken_log)

    def action():
        if status == "[STOPPED]":
            raise macro.StopRequested()
        failure()

    macro.AdoptMeGUI.run_async(gui, action)
    deadline = time.time() + 30
    while not (statuses and statuses[-1][0] is False) and time.time() < deadline:
        time.sleep(0.01)
    assert statuses[-1] == (False, status) and gui.is_running is False


def test_refresh_debug_carries_on_past_a_locked_entry(macro, monkeypatch, tmp_path):
    debug = tmp_path / "debug"
    for folder in ("failures", "logs", "recordings"):
        (debug / folder).mkdir(parents=True)
        (debug / folder / "x.txt").write_text("x")
    (debug / ".config").write_text("{}")
    for name, value in (("DEBUG_DIR", str(debug)), ("FAILURE_DIR", str(debug / "failures")), ("LOGS_DIR", str(debug / "logs")),
                        ("GAME_CONFIG_PATH", str(debug / ".config"))):
        monkeypatch.setattr(macro.cfg, name, value)
    real_rmtree = macro.shutil.rmtree

    def rmtree(path, *args, **kwargs):
        if path.endswith("recordings"):
            raise PermissionError("a video is still open")
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(macro.shutil, "rmtree", rmtree)
    assert macro.refresh_debug() == 2                                  # failures and logs went, recordings stayed
    assert (debug / "failures").is_dir() and (debug / "logs").is_dir() and (debug / ".config").exists()


def test_refresh_debug_empties_the_error_output_of_a_macro_the_bot_started_instead_of_deleting_it(macro, monkeypatch, tmp_path):
    debug = tmp_path / "debug"
    (debug / "logs" / "old_run").mkdir(parents=True)
    (debug / "logs" / "old_run" / "a.txt").write_text("a")
    (debug / "logs" / "output.log").write_text("lines")
    stderr = debug / "logs" / "macro_stderr.log"
    stderr.write_text("Traceback: from an earlier run\n")
    for name, value in (("DEBUG_DIR", str(debug)), ("LOGS_DIR", str(debug / "logs")), ("MACRO_STDERR_PATH", str(stderr)), ("GAME_CONFIG_PATH", str(debug / ".config")),
                        ("FAILURE_DIR", str(debug / "failures")), ("RUNS_DIR", str(debug / "runs"))):
        monkeypatch.setattr(macro.cfg, name, value)
    real_remove = macro.os.remove

    def remove(path, *args, **kwargs):
        if str(path) == str(stderr):
            raise PermissionError("the process has it open")             # Windows: a file that is open cannot be deleted
        real_remove(path, *args, **kwargs)
    monkeypatch.setattr(macro.os, "remove", remove)
    assert macro.refresh_debug() == 1
    assert sorted(os.listdir(debug / "logs")) == ["macro_stderr.log"] and stderr.read_text() == ""          # emptied, still there for the process
    macro.refresh_debug()
    assert stderr.exists() and stderr.read_text() == ""                                                    # (and again, with nothing in the folder but it)


def test_the_stop_hotkey_stops_even_if_the_log_cannot_be_written(macro, monkeypatch):
    gui = object.__new__(macro.AdoptMeGUI)
    gui.is_running = True
    gui.status = type("Label", (), {"config": lambda self, **kw: None})()
    monkeypatch.setattr(macro, "log_run_event", lambda message: (_ for _ in ()).throw(PermissionError("locked")))
    macro.STOP_EVENT.clear()
    gui.stop_by_hotkey()
    assert macro.STOP_EVENT.is_set()


def test_a_run_whose_clock_stepped_back_still_gets_a_report(macro, tmp_path):
    """Local-time stamps go backwards at the end of daylight saving: negative offsets must not index the bars from the end."""
    log = tmp_path / "log.txt"
    log.write_text("\n".join([
        "[2026-10-25 02:30:00] [RUN 9] LOOP started",
        "[2026-10-25 02:40:00] [RUN 9] resolved: hungry (20s)",
        "[2026-10-25 02:10:00] [RUN 9] resolved: hungry (20s)",     # an hour back
        "[2026-10-25 03:30:00] [RUN 9] LOOP stopped"]) + "\n")
    assert macro.generate_run_report(str(log), 9, str(tmp_path)) is not None


class _FakeText:
    """Just enough of a Tk Text for DebugCapture.trim(): a list of lines."""
    def __init__(self, lines):
        self.lines = list(lines)

    def index(self, where):
        assert where == "end-1c"
        return f"{len(self.lines) + 1}.0"        # a Text always ends in one more empty line

    def delete(self, start, end):
        assert start == "1.0"
        del self.lines[:int(end.split(".")[0]) - 1]


def test_the_console_keeps_only_its_last_lines(macro, monkeypatch):
    monkeypatch.setattr(macro.cfg, "CONSOLE_MAX_LINES", 5)
    capture = object.__new__(macro.DebugCapture)
    capture.text = _FakeText(f"line {i}" for i in range(12))
    capture.trim()
    assert capture.text.lines == [f"line {i}" for i in range(7, 12)]
    capture.trim()                                   # already short enough: untouched
    assert len(capture.text.lines) == 5


class _TypingText:
    """A Tk Text that keeps what was put on it: `content`, with (text, tag) pieces; delete() takes the last line (what the status line replacement does)."""
    def __init__(self):
        self.pieces, self.tags = [], {}

    def tag_config(self, tag, **options):
        self.tags[tag] = options

    def config(self, **options):
        pass

    def see(self, where):
        pass

    def insert(self, where, text, tag=None):
        self.pieces.append((text, tag))

    def delete(self, start, end):
        assert (start, end) == ("end-1c linestart", "end-1c")
        while self.pieces and not self.pieces[-1][0].endswith("\n"):
            self.pieces.pop()

    def index(self, where):
        return "1.0"

    @property
    def content(self):
        return "".join(text for text, _ in self.pieces)


def test_the_console_shows_each_level_in_its_colour_a_status_line_replaced_and_debug_lines_can_be_hidden(macro):
    import logging
    text = _TypingText()
    root = type("Root", (), {"after": lambda self, ms, fn: None})()
    capture = macro.DebugCapture(root, text)
    macro.logger.addHandler(capture)
    try:
        macro.logger.debug("how it got there")
        macro.logger.warning("could not do it")
        macro.logger.info("Waiting 1", extra={"status": True})
        macro.logger.info("Waiting 2", extra={"status": True})
        capture.setLevel(logging.INFO)                                    # the checkbox: no debug lines
        macro.logger.debug("hidden")
        macro.logger.info("done")
        capture.drain()
    finally:
        macro.logger.removeHandler(capture)
    lines = [line.split("] ", 1)[1] for line in text.content.splitlines()]
    assert lines == ["how it got there", "could not do it", "Waiting 2", "done"]
    assert {tag for _, tag in text.pieces if tag} == {"DEBUG", "WARNING", "INFO"} and set(text.tags) == set(macro.cfg.GUI_CONSOLE_LEVEL_COLORS)


def test_only_the_newest_run_reports_are_kept(macro, tmp_path):
    for i in range(6):
        path = tmp_path / f"run{i}.png"
        path.write_text("x")
        os.utime(path, (1000 + i, 1000 + i))
    for i in range(6):                                              # (the runs' own logs are in the same folder, and are kept)
        (tmp_path / f"run{i}.txt").write_text("x")
    macro.keep_newest_files(str(tmp_path), 3, ".png")
    assert sorted(p.name for p in tmp_path.iterdir() if p.suffix == ".png") == ["run3.png", "run4.png", "run5.png"]
    assert len([p for p in tmp_path.iterdir() if p.suffix == ".txt"]) == 6
    macro.keep_newest_files(str(tmp_path / "missing"), 3)       # never raises


def test_every_check_reaches_the_log_even_when_it_finds_nothing(macro, one_need, monkeypatch):
    """The run report charts needs per check, so checks that find no need must be counted - as one summary line,
    not a line every few seconds."""
    import numpy as np
    monkeypatch.setattr(macro, "detect_need_icons", lambda save_debug=False: ([], np.zeros((1080, 1920, 3), np.uint8)))
    macro.state.run_number = 4
    for _ in range(3):
        assert macro.process_needs() is False
    assert not os.path.exists(macro.cfg.RUN_LOG_PATH) or "idle checks" not in open(macro.cfg.RUN_LOG_PATH).read()   # not yet
    macro.state.idle_since -= 90                                         # the three checks were 90s ago
    monkeypatch.setattr(macro, "detect_need_icons", lambda save_debug=False: ([(100, 50, 30)], np.zeros((1080, 1920, 3), np.uint8)))
    assert macro.process_needs() is True                                 # a need turns up (one_need's handler resolves it)
    lines = open(macro.cfg.RUN_LOG_PATH).read().splitlines()
    idle = [i for i, line in enumerate(lines) if "idle checks: 3 over 9" in line]
    detected = [i for i, line in enumerate(lines) if "detected: hungry" in line]
    assert idle and detected and idle[0] < detected[0]                   # written before the detection that ended the streak
    assert macro.state.idle_checks == 0
    data = macro.parse_run(macro.cfg.RUN_LOG_PATH, 4)
    assert [matched for _, matched, _ in data["detected"]] == [0, 0, 0, 1]    # four checks, three of them empty


def test_idle_checks_that_found_icons_nothing_matched_are_counted_too(macro, one_need, monkeypatch):
    monkeypatch.setattr(macro, "identify_icons", lambda found, img: iter([(0, img[:10, :10], None, 0.5)]))
    macro.state.run_number = 4
    for _ in range(4):
        macro.process_needs()
    log = open(macro.cfg.RUN_LOG_PATH).read()
    assert log.count("detected: none matched") == 1 and macro.state.idle_checks == 3   # logged once, the other three counted
    macro.flush_idle_checks()
    assert [matched for _, matched, _ in macro.parse_run(macro.cfg.RUN_LOG_PATH, 4)["detected"]] == [0, 0, 0, 0]


def test_idle_checks_are_flushed_after_a_quiet_interval(macro):
    macro.state.run_number = 4
    macro.note_idle_check()
    macro.flush_idle_checks_if_due()
    assert macro.state.idle_checks == 1                                  # too soon
    macro.state.idle_since -= macro.cfg.QUIET_LOG_INTERVAL
    macro.flush_idle_checks_if_due()
    assert macro.state.idle_checks == 0 and "idle checks: 1 over" in open(macro.cfg.RUN_LOG_PATH).read()


def test_roblox_not_running_at_the_start_is_not_a_failure(macro, monkeypatch):
    monkeypatch.setattr(macro, "rejoin_reason", lambda: ("disconnect", macro.ROBLOX_NOT_RUNNING))
    monkeypatch.setattr(macro, "leave_and_rejoin", lambda clean_leave=True: True)
    monkeypatch.setattr(macro, "setup_game", lambda: True)
    macro.state.run_start_pending = True
    assert macro.rejoin_game() is True
    log = open(macro.cfg.RUN_LOG_PATH).read()
    assert "Roblox isn't running at the start - starting it" in log and "FAILURE" not in log
    assert macro.run_stats.failures == 0 and macro.run_stats.recoveries == 0 and macro.state.run_start_pending is False
    assert macro.rejoin_game() is True                                          # the same later on is a failure
    assert "FAILURE: Roblox isn't running - rejoining" in open(macro.cfg.RUN_LOG_PATH).read()


def test_only_the_first_look_of_a_run_counts_as_the_start(macro, monkeypatch):
    monkeypatch.setattr(macro, "rejoin_reason", lambda: None)
    macro.state.run_start_pending = True
    macro.rejoin_game(kinds=("disconnect",))                                     # an in-task look is not the start of the run
    assert macro.state.run_start_pending is True
    macro.rejoin_game()
    assert macro.state.run_start_pending is False


def test_the_zoom_keys_and_the_mouse_buttons_that_are_down_are_released_with_the_rest(macro, monkeypatch):
    released, buttons, held = [], [], [{"mouse_left", "mouse_right"}]
    monkeypatch.setattr(macro.pydirectinput, "keyUp", lambda key: released.append(key), raising=False)
    monkeypatch.setattr(macro.pydirectinput, "mouseUp", lambda button="left": buttons.append(button), raising=False)
    monkeypatch.setattr(macro, "pressed_inputs", lambda: held[0])
    macro.release_all_inputs()
    assert {macro.cfg.KEYS["zoom_in"], macro.cfg.KEYS["zoom_out"], macro.cfg.KEYS["helicopter"]} <= set(released)
    assert sorted(buttons) == ["left", "right"]                                      # (the camera is turned with the right one)
    buttons.clear()
    held[0] = {"mouse_left"}
    macro.release_all_inputs()
    assert buttons == ["left"]                                                       # a release with the button up is a click on whatever is under the mouse
    buttons.clear()
    held[0] = set()
    macro.release_all_inputs()
    assert buttons == []                                                             # (over the window's border or title bar, a right one opens its menu)


def test_the_rejoin_is_booked_before_the_respawn_that_can_lose_focus(macro, monkeypatch):
    """A FocusLost out of the post-join respawn must not leave the old rejoin/progress times behind (the next cycle
    would rejoin the fresh game again)."""
    import numpy as np
    monkeypatch.setattr(macro, "close_roblox", lambda clean_leave=True: None)
    monkeypatch.setattr(macro, "run_deeplink", lambda server=None: None)
    monkeypatch.setattr(macro, "roblox_windows", lambda: [object()])
    monkeypatch.setattr(macro, "focus_roblox", lambda: True)
    monkeypatch.setattr(macro, "grab_screen", lambda: np.zeros((1080, 1920, 3), np.uint8))
    monkeypatch.setattr(macro, "detect_play_button", lambda img: True)
    monkeypatch.setattr(macro, "wait_stoppable", lambda duration: None)

    def lose_focus():
        raise macro.FocusLost()

    monkeypatch.setattr(macro, "respawn_character", lose_focus)
    macro.state.last_rejoin = macro.state.last_progress = macro.state.last_detection = time.time() - 3600
    with pytest.raises(macro.FocusLost):
        macro.leave_and_rejoin()
    assert time.time() - macro.state.last_rejoin < 5 and time.time() - macro.state.last_progress < 5


def test_a_dismissed_minigame_popup_does_not_lose_the_abort(macro, monkeypatch):
    monkeypatch.setattr(macro, "rejoin_game", lambda kinds=None: False)
    monkeypatch.setattr(macro, "close_backpack_if_stuck", lambda in_task: False)
    monkeypatch.setattr(macro, "dismiss_stray_windows", lambda: "cant_spawn_vehicle")     # flagged abort_task
    monkeypatch.setattr(macro, "close_minigame_finish_screen", lambda abandon=True: False)
    monkeypatch.setattr(macro, "minigame_popup", lambda play=True: "dismissed")
    monkeypatch.setattr(macro, "detect_paycheck", lambda: False)
    monkeypatch.setattr(macro.cfg, "HALLOWEEN", True)
    assert macro.unscrew(in_task=True) == "aborted"


def test_setup_keeps_its_backpack_open_without_the_in_task_check_closing_it(macro, monkeypatch):
    """Setup holds the backpack open ~8s on purpose; two sightings 4s apart count as stuck elsewhere."""
    def stuck_check(in_task):
        raise AssertionError("the in-task backpack check must not run during setup")

    monkeypatch.setattr(macro, "rejoin_game", lambda kinds=None: False)
    monkeypatch.setattr(macro, "close_backpack_if_stuck", stuck_check)
    monkeypatch.setattr(macro, "dismiss_stray_windows", lambda: None)
    monkeypatch.setattr(macro, "detect_paycheck", lambda: False)
    monkeypatch.setattr(macro.cfg, "HALLOWEEN", False)
    assert macro.unscrew(in_task=True, play_minigames=False) is None


def test_the_in_task_check_closes_a_backpack_seen_open_twice_but_not_one_a_handler_has_open_on_purpose(macro, monkeypatch):
    import numpy as np
    closed = []
    monkeypatch.setattr(macro, "is_roblox_focused", lambda: True)
    monkeypatch.setattr(macro, "grab_screen", lambda: np.zeros((20, 20, 3), np.uint8))
    monkeypatch.setattr(macro, "detect_backpack_open", lambda img: True)
    monkeypatch.setattr(macro, "close_backpack_if_open", lambda step="": closed.append(step))
    stuck = macro.close_backpack_if_stuck
    confirmations = macro.cfg.BACKPACK_CLOSE_CONFIRMATIONS
    before = [True] * (confirmations - 1)                                                  # the sightings that are not enough yet
    assert [stuck(True) for _ in before] == before and closed == []
    assert stuck(True) is False and len(closed) == 1                                       # the last one closes it
    closed.clear()
    with macro.backpack_on_purpose():
        assert [stuck(True) for _ in range(confirmations + 3)] == [True] * (confirmations + 3) and closed == []     # a handler has it open: never
    assert [stuck(True) for _ in before] == before and closed == []                        # (and what it saw before doesn't count: first sightings again)
    macro.state.backpack_strikes = confirmations - 1
    with macro.backpack_on_purpose():
        pass
    assert [stuck(True) for _ in before] == before and closed == []                        # sightings from before the block do not count after it either


def test_an_equip_sequence_is_not_closed_in_the_middle_by_the_in_task_check(macro, monkeypatch):
    """With a sighting already counted (a false one), the next check falls in the sequence, which keeps the backpack open longer than
    the interval between checks: it used to close the backpack there, and the key that ends the sequence then opened it again."""
    import numpy as np
    now = [1000.0]
    monkeypatch.setattr(macro.time, "time", lambda: now[0])
    monkeypatch.setattr(macro.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(macro.time, "sleep", lambda duration: now.__setitem__(0, now[0] + duration))
    monkeypatch.setattr(macro.STOP_EVENT, "wait", lambda timeout=None: now.__setitem__(0, now[0] + (timeout or 0)) or False)
    monkeypatch.setattr(macro, "check_focus", lambda: None)
    opened, presses, logged = [False], [], []

    def press(key, **kwargs):
        if key == macro.cfg.KEYS["backpack"]:
            opened[0] = not opened[0]
            presses.append(opened[0])
    monkeypatch.setattr(macro.pydirectinput, "press", press, raising=False)
    monkeypatch.setattr(macro, "hover_click", lambda x, y, duration=0: now.__setitem__(0, now[0] + 1.2))
    monkeypatch.setattr(macro, "is_backpack_tab_selected", lambda pos: False)
    monkeypatch.setattr(macro, "is_roblox_focused", lambda: True)
    monkeypatch.setattr(macro, "grab_screen", lambda: np.zeros((20, 20, 3), np.uint8))
    monkeypatch.setattr(macro, "detect_backpack_open", lambda img: opened[0])
    monkeypatch.setattr(macro, "detect_backpack_expanded", lambda img: False)
    monkeypatch.setattr(macro.cv2, "imwrite", lambda *a, **k: True)
    monkeypatch.setattr(macro, "rejoin_game", lambda kinds=None: False)
    monkeypatch.setattr(macro, "dismiss_stray_windows", lambda: now.__setitem__(0, now[0] + 1.0))
    monkeypatch.setattr(macro, "detect_paycheck", lambda: False)
    monkeypatch.setattr(macro, "log_run_event", logged.append)
    monkeypatch.setattr(macro.cfg, "HALLOWEEN", False)
    with macro.task_unscrew():
        macro.state.last_task_unscrew = now[0] - 100                                       # a check is due at once
        macro.state.backpack_strikes = 1                                                   # and one sighting is already counted
        macro.equip_favorite_vehicle()
    assert presses == [True, False] and logged == []                                       # opened once, closed once, nothing closed it in between


def test_the_teleport_and_the_toy_sequences_are_exempt_only_while_their_backpack_is_open(macro, monkeypatch):
    wanted = []
    note = lambda what: wanted.append((what, macro.state.backpack_wanted))
    monkeypatch.setattr(macro, "focus_roblox", lambda: True)
    monkeypatch.setattr(macro, "select_backpack_tab", lambda pos: note("tab"))
    monkeypatch.setattr(macro, "hover_click", lambda x, y, duration=0: note("click"))
    monkeypatch.setattr(macro, "close_backpack", lambda step: note("close"))
    monkeypatch.setattr(macro, "hold_key", lambda key, duration: note("hold"))
    monkeypatch.setattr(macro, "wait_interruptible", lambda duration: note("wait"))
    monkeypatch.setattr(macro, "grab_screen", lambda: None)
    monkeypatch.setattr(macro, "minigame_teleported", lambda before: True)
    macro.teleport_to((100, 100))
    assert [n for n, w in wanted if w] == ["wait", "click", "wait", "click", "wait", "click"]           # opening it, the category, the teleport: exempt
    assert [w for n, w in wanted[6:]] == [0] * len(wanted[6:])                                          # the long wait after the teleport and the walk back: not
    wanted.clear()
    assert macro.CatchNeedHandler().handle() is True
    assert [w for n, w in wanted if n in ("tab", "close")] == [1, 1] and wanted[-1][1] == 0 and macro.state.backpack_wanted == 0


def test_the_teleport_dialogs_yes_is_clicked_again_when_nothing_teleported(macro, monkeypatch):
    clicks, logged = [], []
    monkeypatch.setattr(macro, "hover_click", lambda x, y, duration=0: clicks.append((x, y)))
    monkeypatch.setattr(macro, "wait_interruptible", lambda duration: None)
    monkeypatch.setattr(macro, "grab_screen", lambda: None)
    monkeypatch.setattr(macro, "hold_key", lambda key, duration: None)
    monkeypatch.setattr(macro, "log_run_event", logged.append)
    took = iter([False, True])
    monkeypatch.setattr(macro, "minigame_teleported", lambda before: next(took))
    macro.teleport_to((100, 100))
    assert clicks.count(macro.cfg.GENERAL_TELEPORT_POS_3) == 2 and logged == []             # the first click was lost, the second took
    clicks.clear()
    monkeypatch.setattr(macro, "minigame_teleported", lambda before: False)
    macro.teleport_to((100, 100))
    assert clicks.count(macro.cfg.GENERAL_TELEPORT_POS_3) == macro.cfg.TELEPORT_YES_TRIES and len(logged) == 1      # never: tried as often as allowed, said so, went on


def test_the_recorder_does_not_spin_while_no_frame_can_be_grabbed(macro, monkeypatch):
    recorder = macro.RunRecorder("unused")
    recorder.writer = type("Writer", (), {"write": lambda self, frame: None})()
    monkeypatch.setattr(recorder, "_frame", lambda: (_ for _ in ()).throw(RuntimeError("no window")))
    waits = []
    real_set = recorder.stop_event.set

    def wait(timeout=None):
        waits.append(timeout)
        if len(waits) >= 3:
            real_set()                                       # enough: let _record() return
        return False

    monkeypatch.setattr(recorder.stop_event, "wait", wait)
    recorder._record()
    assert len(waits) == 3 and all(w >= 1.0 / macro.cfg.RECORD_FPS for w in waits)    # each try a frame's time apart, never 0


def test_time_spent_in_the_in_task_checks_counts_towards_a_wait(macro, fake_clock, monkeypatch):
    def slow_check():
        fake_clock["now"] += 0.1                                              # an in-task check that takes 0.1s every pass

    monkeypatch.setattr(macro, "task_unscrew_tick", slow_check)
    started = fake_clock["now"]
    macro.wait_interruptible(0.5)
    assert fake_clock["now"] - started == pytest.approx(0.5, abs=0.11)         # it used to add every check's time on top (1.0)


def _window(title):
    return type("Window", (), {"title": title})()


def test_a_browser_tab_about_roblox_is_not_the_roblox_window(macro, monkeypatch):
    game = _window("Roblox")
    monkeypatch.setattr(macro, "roblox_windows", lambda: [_window("Roblox - Google Chrome"), game])
    for title, expected in (("Roblox", True), ("Roblox - Google Chrome", False), ("Notepad", False)):
        monkeypatch.setattr(macro.gw, "getActiveWindow", lambda title=title: _window(title))
        assert macro.is_roblox_focused() is expected, title
    monkeypatch.setattr(macro.gw, "getActiveWindow", lambda: None)
    assert macro.is_roblox_focused() is False


def test_without_a_window_titled_exactly_roblox_any_roblox_title_still_counts(macro, monkeypatch):
    monkeypatch.setattr(macro, "roblox_windows", lambda: [_window("Roblox Player")])
    monkeypatch.setattr(macro.gw, "getActiveWindow", lambda: _window("Roblox Player"))
    assert macro.is_roblox_focused() is True


def sick_screen(name):
    """A real screenshot from tests/sick/: at_the_doctor (the hand button over the player's head), dialog ("Ask Doctor Heart to heal
    your family?") or pet_view (what pressing 'e' there brought up: the pet's menu)."""
    import cv2
    return cv2.imread(os.path.join(os.path.dirname(__file__), "sick", f"{name}.png"))


@pytest.fixture
def sick_run(macro, world, monkeypatch):
    """The sick handler up to its Yes, on real screenshots (the detectors are the real ones): run["screen"] is what is on screen
    now; the prompt's click changes it to run["after_prompt"], backing out of the pet view to run["after_back_out"].
    The need has an entry of its own here (positions made up), so none of this depends on how the real one is tuned
    or on HALLOWEEN, which decides whether the real one exists."""
    from functools import partial
    final = (610, 620)
    monkeypatch.setitem(macro.cfg.TELEPORT_WALK_NEEDS, "sick", dict(teleport_pos=(1, 2), steps=(("w", 1.0),), helicopter=False,
                                                                    final_click=final))
    monkeypatch.setitem(macro.NEED_HANDLER_CLASSES, "sick", partial(macro.TeleportWalkNeedHandler, "sick"))
    run = {"screen": sick_screen("at_the_doctor"), "after_prompt": sick_screen("dialog"), "after_back_out": sick_screen("at_the_doctor"),
           "events": [], "waited": [], "respawns": 0, "final": final, "world": world}

    def click(x, y, duration=0):
        run["events"].append(("click", (x, y)))
        if (x, y) != final:
            run["screen"] = run["after_prompt"]

    def back_out():
        run["events"].append("back out")
        run["screen"] = run["after_back_out"]

    monkeypatch.setattr(macro, "focus_roblox", lambda: True)
    monkeypatch.setattr(macro, "teleport_to", lambda pos: None)
    monkeypatch.setattr(macro, "hold_key", lambda key, duration: None)
    monkeypatch.setattr(macro, "grab_screen", lambda: run["screen"])
    monkeypatch.setattr(macro, "dismiss_stray_windows", back_out)
    monkeypatch.setattr(macro, "hover_click", click)
    monkeypatch.setattr(macro, "wait_until_need_gone", lambda name, max_wait=60.0, **kw: run["waited"].append(name))
    monkeypatch.setattr(macro, "respawn_character", lambda: run.update(respawns=run["respawns"] + 1))
    return run


def prompt_click(run):
    """The one click that is not the Yes."""
    (_, pos), = [event for event in run["events"] if event != "back out" and event[1] != run["final"]]
    return pos


def test_sick_clicks_the_hand_button_where_it_is_and_then_yes(macro, sick_run):
    assert macro.TeleportWalkNeedHandler("sick").handle() is True
    assert [event[0] for event in sick_run["events"]] == ["click", "click"] and sick_run["events"][-1] == ("click", sick_run["final"])
    x, y = prompt_click(sick_run)
    assert abs(x - 965) <= 3 and abs(y - 498) <= 3                       # the hand button of tests/sick/at_the_doctor.png
    assert sick_run["waited"] == ["sick"] and sick_run["respawns"] == 0
    assert not [key for key in sick_run["world"].keys if key[:2] == ("press", "e")]       # 'e' focuses the pet


def test_sick_backs_out_of_the_pet_view_before_it_looks_for_the_prompt(macro, sick_run):
    sick_run["screen"] = sick_screen("pet_view")
    assert macro.TeleportWalkNeedHandler("sick").handle() is True
    assert sick_run["events"][0] == "back out" and sick_run["events"][-1] == ("click", sick_run["final"])
    assert len(sick_run["events"]) == 3 and sick_run["waited"] == ["sick"]


def test_sick_gives_up_at_once_when_the_prompt_is_not_on_screen(macro, sick_run):
    sick_run["screen"] = sick_screen("dialog")                          # not at the doctor
    assert macro.TeleportWalkNeedHandler("sick").handle() is False
    assert sick_run["events"] == [] and sick_run["waited"] == [] and sick_run["respawns"] == 1
    assert "sick: the doctor's prompt is not on screen" in open(macro.cfg.RUN_LOG_PATH).read()


def test_sick_gives_up_at_once_when_the_dialog_does_not_come_up(macro, sick_run):
    sick_run["after_prompt"] = sick_screen("at_the_doctor")             # the click did nothing
    assert macro.TeleportWalkNeedHandler("sick").handle() is False
    assert len(sick_run["events"]) == 1 and ("click", sick_run["final"]) not in sick_run["events"]     # never the Yes
    assert sick_run["waited"] == [] and sick_run["respawns"] == 1                                    # no waiting for the icon
    assert "sick: no dialog after the prompt was clicked" in open(macro.cfg.RUN_LOG_PATH).read()


def test_sick_looks_for_the_dialog_for_a_while_and_not_longer(macro, sick_run):
    clock = sick_run["world"].clock
    sick_run["after_prompt"] = sick_screen("at_the_doctor")
    start = clock.now
    macro.TeleportWalkNeedHandler("sick").handle()
    spent = clock.now - start
    assert macro.cfg.SICK_CONFIRM_WAIT <= spent <= macro.cfg.SICK_PRE_CLICK_WAIT + macro.cfg.SICK_CONFIRM_WAIT + 2 * macro.cfg.SICK_LOOK_INTERVAL


def test_a_failed_sick_is_logged_as_a_failure_by_process_needs(macro, sick_run, monkeypatch):
    import numpy as np
    sick_run["screen"] = sick_screen("dialog")                          # not at the doctor
    screen = np.zeros((1080, 1920, 3), np.uint8)
    monkeypatch.setattr(macro, "focus_roblox_click", lambda: True)
    monkeypatch.setattr(macro, "detect_need_icons", lambda save_debug=False: ([(100, 50, 30)], screen))
    monkeypatch.setattr(macro, "identify_icons", lambda found, img: iter([(0, screen[:10, :10], "sick", 0.9)]))
    monkeypatch.setattr(macro, "check_focus", lambda: None)
    monkeypatch.setattr(macro, "save_failure_report", lambda reason: None)
    assert macro.process_needs() is False
    assert macro.run_stats.failed == {"sick": 1} and "sick handler could not resolve it" in open(macro.cfg.RUN_LOG_PATH).read()


# --- a wait that ran out is not a resolved need ---

def test_a_wait_that_runs_out_marks_the_need_as_not_cleared(need_gone, macro, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(macro.time, "time", lambda: clock[0])
    monkeypatch.setattr(macro, "wait_interruptible", lambda duration: clock.__setitem__(0, clock[0] + duration))
    need_gone["script"]()
    macro.wait_until_need_gone("hungry", max_wait=10, poll_interval=POLL)
    assert macro.state.need_cleared is False
    need_gone["script"]([], [])
    macro.wait_until_need_gone("hungry", max_wait=10, poll_interval=POLL)
    assert macro.state.need_cleared is True                                  # every wait starts afresh


def walk_with_watcher_answers(macro, monkeypatch, answers, cleared=False):
    """walk_alternating("hungry") on a made-up clock, its watcher thread replaced by one that reports `answers`."""
    clock = [1000.0]
    failures = []
    monkeypatch.setattr(macro.time, "time", lambda: clock[0])
    monkeypatch.setattr(macro, "focus_roblox", lambda: True)
    monkeypatch.setattr(macro, "hold_key", lambda key, duration: clock.__setitem__(0, clock[0] + duration))
    monkeypatch.setattr(macro, "wait_interruptible", lambda duration: clock.__setitem__(0, clock[0] + duration))
    monkeypatch.setattr(macro, "log_failure", lambda reason, screenshot=True: failures.append(reason))

    def watcher(need, cleared_event, stop_event, seen=None):
        seen.extend(answers)
        if cleared:
            cleared_event.set()
    monkeypatch.setattr(macro, "_watch_need_gone", watcher)
    macro.walk_alternating(("a", "d"), 10, step_duration=1, need_name="hungry")
    return failures


def test_a_walk_that_ends_with_the_need_still_showing_is_not_cleared(macro, monkeypatch):
    failures = walk_with_watcher_answers(macro, monkeypatch, [False, False])
    assert macro.state.need_cleared is False and failures == ["hungry still showing after 10s"]


def test_a_walk_that_clears_the_need_is_cleared(macro, monkeypatch):
    failures = walk_with_watcher_answers(macro, monkeypatch, [False, True], cleared=True)
    assert macro.state.need_cleared is True and failures == []


def test_a_need_that_was_not_cleared_is_failed_not_resolved(macro, one_need, monkeypatch):
    respawns = []
    monkeypatch.setattr(macro, "respawn_character", lambda: respawns.append(1))

    class Handler:
        def handle(self):
            macro.state.need_cleared = False          # what the waits do when they run out
            return True
    monkeypatch.setattr(macro, "get_need_handler", lambda name: Handler())
    macro.state.run_number = 4
    stalled = macro.state.last_progress = time.time() - 100
    assert macro.process_needs() is True              # the character was moved: a fresh check follows
    assert respawns == [1]
    assert macro.run_stats.resolved == {} and macro.run_stats.failed == {"hungry": 1}
    assert macro.state.last_progress == stalled
    log = open(macro.cfg.RUN_LOG_PATH).read()
    assert "not cleared: hungry (" in log and "resolved: hungry" not in log
    assert macro.state.attempted_last == {"hungry"}   # an attempt, so a need that never clears is disabled in the end


def test_a_handler_that_forgets_the_flag_still_resolves(macro, one_need):
    macro.state.need_cleared = False                  # left over from an earlier need
    assert macro.process_needs() is True
    assert macro.run_stats.resolved == {"hungry": 1}


# --- the same needs on screen, check after check ---

def test_the_same_needs_on_every_check_are_logged_once_and_counted(macro, one_need, monkeypatch):
    monkeypatch.setattr(macro, "get_need_handler", lambda name: None)       # disabled: nothing is handled
    macro.state.run_number = 4
    for _ in range(5):
        macro.process_needs()
    log = open(macro.cfg.RUN_LOG_PATH).read()
    assert log.count("detected: hungry") == 1 and macro.state.repeat_checks == 4
    macro.flush_idle_checks()
    assert macro.state.repeat_checks == 0 and "same checks: 4 over" in open(macro.cfg.RUN_LOG_PATH).read()
    data = macro.parse_run(macro.cfg.RUN_LOG_PATH, 4)
    assert [matched for _, matched, _ in data["detected"]] == [1] * 5       # the per-check chart still counts all five
    assert "same checks" not in data["events"]


def test_a_change_in_what_is_on_screen_is_logged_again(macro, one_need, monkeypatch):
    monkeypatch.setattr(macro, "get_need_handler", lambda name: None)
    macro.state.run_number = 4
    macro.process_needs()
    macro.process_needs()
    monkeypatch.setattr(macro, "identify_icons", lambda found, img: iter([(0, one_need[:10, :10], "thirsty", 0.9)]))
    macro.process_needs()
    log = open(macro.cfg.RUN_LOG_PATH).read()
    assert log.index("detected: hungry") < log.index("same checks: 1 over") < log.index("detected: thirsty")


def test_a_need_seen_again_after_a_handler_ran_is_logged_again(macro, one_need):
    macro.state.run_number = 4
    macro.process_needs()                                                    # handled (and resolved)
    macro.process_needs()
    assert open(macro.cfg.RUN_LOG_PATH).read().count("detected: hungry") == 2


def test_an_empty_stretch_between_two_identical_detections_logs_the_second(macro, one_need, monkeypatch):
    monkeypatch.setattr(macro, "get_need_handler", lambda name: None)
    macro.state.run_number = 4
    macro.process_needs()
    macro.note_idle_check()
    macro.process_needs()
    assert open(macro.cfg.RUN_LOG_PATH).read().count("detected: hungry") == 2


def test_repeated_checks_are_flushed_after_a_quiet_interval(macro):
    macro.state.run_number = 4
    macro.note_repeat_check()
    macro.flush_idle_checks_if_due()
    assert macro.state.repeat_checks == 1
    macro.state.repeat_since -= macro.cfg.QUIET_LOG_INTERVAL
    macro.flush_idle_checks_if_due()
    assert macro.state.repeat_checks == 0 and "same checks: 1 over" in open(macro.cfg.RUN_LOG_PATH).read()


def test_a_need_disabled_for_being_stuck_is_logged_by_name(macro, monkeypatch):
    monkeypatch.setattr(macro.cfg, "ENABLED_NEEDS", {"hungry"})
    monkeypatch.setattr(macro, "log_failure", lambda reason, screenshot=True: None)
    macro.state.run_number = 4
    for _ in range(macro.cfg.NEED_STUCK_CHECKS):
        macro.state.attempted_last = {"hungry"}
        macro.record_detection(["hungry"])
    assert "hungry" in macro.state.disabled_needs
    assert "[RUN 4] disabled: hungry\n" in open(macro.cfg.RUN_LOG_PATH).read()


# --- the white screen ---

def white_screen_run(macro, monkeypatch, frames):
    """rejoin_reason() with the game running and `frames` (a list, popped one per call) as what the screen shows."""
    import numpy as np
    clock = [1000.0]
    window = type("Window", (), {"title": "Roblox"})()
    monkeypatch.setattr(macro.time, "time", lambda: clock[0])
    monkeypatch.setattr(macro.gw, "getAllWindows", lambda: [window])
    white = np.full((1080, 1920, 3), 255, np.uint8)
    dark = np.zeros((1080, 1920, 3), np.uint8)
    monkeypatch.setattr(macro, "grab_screen", lambda: white if frames.pop(0) else dark)
    return clock


def test_a_screen_that_stays_white_is_rejoined_after_the_wait(macro, monkeypatch):
    frames = [True] * 5
    clock = white_screen_run(macro, monkeypatch, frames)
    limit = macro.cfg.WHITE_SCREEN_REJOIN_AFTER
    assert macro.rejoin_reason() is None                      # first white look: the clock starts
    clock[0] += limit - 1
    assert macro.rejoin_reason() is None                      # not long enough
    clock[0] += 2
    kind, reason = macro.rejoin_reason()
    assert kind == "white_screen" and f"{limit:g}s" in reason


def test_a_white_screen_that_clears_starts_the_wait_afresh(macro, monkeypatch):
    frames = [True, False, True, True]
    clock = white_screen_run(macro, monkeypatch, frames)
    limit = macro.cfg.WHITE_SCREEN_REJOIN_AFTER
    macro.rejoin_reason()
    clock[0] += limit - 1
    assert macro.rejoin_reason() is None and macro.state.white_since is None     # it cleared a moment before the limit
    macro.rejoin_reason()
    clock[0] += limit - 1
    assert macro.rejoin_reason() is None                                          # the second white spell is a new one


def test_a_white_screen_is_acted_on_mid_task_too(macro, monkeypatch):
    seen = []
    monkeypatch.setattr(macro, "rejoin_reason", lambda: ("white_screen", "white"))
    monkeypatch.setattr(macro, "leave_and_rejoin", lambda clean_leave=True: seen.append(clean_leave) or True)
    monkeypatch.setattr(macro, "setup_game", lambda: True)
    monkeypatch.setattr(macro, "log_failure", lambda reason, screenshot=True: None)
    assert macro.unscrew(in_task=True) == "rejoined"
    assert seen == [True]


def test_a_white_screen_rejoin_is_an_event_not_a_failure(macro, monkeypatch):
    """The game's own doing: the rejoin that works is all there is to it (the run log says `white screen: ...`, the report counts them)."""
    monkeypatch.setattr(macro, "rejoin_reason", lambda: ("white_screen", "the screen has been white for 30s"))
    monkeypatch.setattr(macro, "leave_and_rejoin", lambda clean_leave=True: True)
    monkeypatch.setattr(macro, "setup_game", lambda: True)
    failures = []
    monkeypatch.setattr(macro, "log_failure", lambda reason, screenshot=True: failures.append(reason))
    assert macro.rejoin_game() is True
    assert failures == [] and macro.run_stats.recoveries == 1 and macro.run_stats.failures == 0
    assert "white screen: the screen has been white for 30s - rejoining" in open(macro.cfg.RUN_LOG_PATH).read()


def test_detect_white_screen_wants_most_of_the_screen_completely_white(macro):
    import numpy as np
    screen = np.zeros((1080, 1920, 3), np.uint8)
    assert not macro.detect_white_screen(screen)
    screen[:, :int(1920 * (macro.cfg.WHITE_SCREEN_MIN_FRACTION + 0.05))] = 255
    assert macro.detect_white_screen(screen)
    bright = np.full((1080, 1920, 3), macro.cfg.WHITE_SCREEN_LEVEL - 20, np.uint8)       # bright is not white
    assert not macro.detect_white_screen(bright)


def test_the_real_white_transition_is_white_and_no_other_real_screenshot_is(macro):
    import glob
    import os
    import cv2
    folder = os.path.join(os.path.dirname(__file__), "icon_detection", "halloween1")
    white = [os.path.basename(f) for f in sorted(glob.glob(os.path.join(folder, "*.png")))
             if macro.detect_white_screen(cv2.imread(f))]
    assert [name.split("__")[0] for name in white] == ["white_screen"]


def test_a_failed_minigame_does_not_count_as_progress_but_a_won_one_does(macro, monkeypatch):
    stalled = time.time() - 100
    monkeypatch.setattr(macro, "release_all_inputs", lambda: None)
    monkeypatch.setattr(macro, "accept_minigame_popup", lambda: True)
    monkeypatch.setattr(macro, "hover_click", lambda *a, **k: None)
    monkeypatch.setattr(macro, "respawn_character", lambda: None)
    monkeypatch.setattr(macro, "click_nice_until_gone", lambda: True)
    monkeypatch.setattr(macro, "log_failure", lambda reason, screenshot=True: None)
    monkeypatch.setattr(macro, "read_candy_corn_settled", lambda tries=4: None)                  # (no screen to read the total off)
    for finished in (False, True):
        macro.state.last_progress = stalled
        monkeypatch.setitem(macro.MINIGAME_PLAYERS, "ghost_gallery", lambda deadline, watch, finished=finished: finished)
        assert macro.play_minigame("ghost_gallery") is finished
        assert (macro.state.last_progress == stalled) is (not finished)
        assert time.time() - macro.state.last_detection < 5                            # needs couldn't be seen meanwhile either way


@pytest.fixture
def popup_world(macro, world, monkeypatch):
    """A screen that follows the clicks on the popup's Yes: `popup_world.after(n_clicks)` -> what the screen shows after that many
    ('dark' = the same as before, 'white', 'other' = a different view); `.popup_up` says whether the popup is still on screen."""
    import numpy as np
    shown = {"dark": np.full((108, 192, 3), 30, np.uint8), "white": np.full((108, 192, 3), 255, np.uint8),     # (small: it is looked at hundreds of times)
             "other": np.full((108, 192, 3), 200, np.uint8)}
    scene = types.SimpleNamespace(after=lambda clicks: "dark", popup_up=lambda clicks: True)
    monkeypatch.setattr(macro, "grab_screen", lambda: shown[scene.after(len(macro.clicks))])
    monkeypatch.setattr(macro, "detect_minigame_popup", lambda img: scene.popup_up(len(macro.clicks)))
    return scene


def test_a_yes_that_teleports_is_clicked_once(macro, popup_world, world):
    popup_world.after = lambda clicks: "white" if clicks else "dark"
    assert macro.accept_minigame_popup() is True and len(macro.clicks) == 1
    del macro.clicks[:]
    popup_world.after = lambda clicks: "other" if clicks else "dark"                     # a teleport whose white screen was missed
    assert macro.accept_minigame_popup() is True and len(macro.clicks) == 1


def test_a_yes_that_did_not_take_is_clicked_again_while_the_popup_is_up(macro, popup_world, world):
    popup_world.after = lambda clicks: "white" if clicks >= 2 else "dark"
    assert macro.accept_minigame_popup() is True and len(macro.clicks) == 2
    del macro.clicks[:]
    popup_world.after = lambda clicks: "dark"                                              # never does
    assert macro.accept_minigame_popup() is False and len(macro.clicks) == macro.cfg.MINIGAME_YES_TRIES


def test_a_popup_that_is_gone_with_no_teleport_is_a_failed_start_at_once(macro, popup_world, world):
    popup_world.popup_up = lambda clicks: False
    start = world.clock.now
    assert macro.accept_minigame_popup() is False and len(macro.clicks) == 1
    assert world.clock.now - start < macro.cfg.MINIGAME_TELEPORT_WAIT + 2                  # one wait, not a minigame's start wait


def test_a_minigame_whose_popup_did_not_teleport_is_not_played_but_logged(macro, monkeypatch):
    events = []
    monkeypatch.setattr(macro, "accept_minigame_popup", lambda: False)
    monkeypatch.setattr(macro, "release_all_inputs", lambda: None)
    monkeypatch.setattr(macro, "respawn_character", lambda: events.append("respawn"))
    monkeypatch.setattr(macro, "log_failure", lambda reason, screenshot=True: events.append(reason))
    monkeypatch.setattr(macro, "start_run_capture", lambda label: events.append("capture"))
    monkeypatch.setitem(macro.MINIGAME_PLAYERS, "ghost_gallery", lambda deadline, watch: events.append("played"))
    assert macro.play_minigame("ghost_gallery") is False
    assert len(events) == 2 and "could not be started" in events[0] and events[1] == "respawn"       # no capture, no play


def test_a_hotkey_acts_on_a_new_press_only_when_its_conditions_hold(macro, monkeypatch):
    focused = [True]
    monkeypatch.setattr(macro, "is_roblox_focused", lambda: focused[0])
    fires = macro.hotkey_fires
    assert fires(True, False, False, False, False)
    assert not fires(True, True, False, False, False) and not fires(False, False, False, False, False)      # held, or not pressed
    assert not fires(True, False, True, False, False) and fires(True, False, True, True, False)               # a stop key needs a run
    assert fires(True, False, False, False, True)
    focused[0] = False
    assert not fires(True, False, False, False, True) and fires(True, False, False, False, False)             # M needs Roblox, P doesn't


def test_a_button_the_game_refuses_is_clicked_again_after_a_wait_and_refused_again_it_is_given_up(macro, world, monkeypatch):
    cfg = macro.cfg
    waited, refusals = [], [1]
    monkeypatch.setattr(macro, "focus_roblox", lambda: True)
    monkeypatch.setattr(macro, "grab_screen", lambda: None)
    monkeypatch.setattr(macro, "only_pets_message_visible", lambda img: refusals[0] > 0 and (refusals.__setitem__(0, refusals[0] - 1) or True))
    monkeypatch.setattr(macro, "wait_until_need_gone", lambda name, max_wait=None, **kw: waited.append((name, max_wait)))
    macro.state.button_positions["potty"] = (100, 200)
    assert macro.click_need_button("potty") is True                                    # refused once, then taken: the icon gets the shorter wait
    assert macro.clicks.count((100, 200)) == 2 and waited == [("potty", cfg.BUTTON_RETRY_CLEAR_WAIT)]
    assert world.waits.count(cfg.BUTTON_RETRY_WAIT) == 1
    del macro.clicks[:]
    refusals[0] = 10 ** 6                                                              # refused again: given up at once, the icon not waited for
    waited.clear()
    world.waits.clear()
    assert macro.click_need_button("potty") is False
    assert macro.clicks.count((100, 200)) == cfg.BUTTON_CLICK_TRIES and waited == []
    assert world.waits.count(cfg.BUTTON_RETRY_WAIT) == cfg.BUTTON_CLICK_TRIES - 1 and world.waits[-1] == cfg.BUTTON_RETRY_WAIT
    del macro.clicks[:]
    refusals[0] = 0                                                                    # not refused: clicked once
    waited.clear()
    assert macro.click_need_button("potty") is True and macro.clicks.count((100, 200)) == 1 and waited == [("potty", None)]       # (the usual wait)


def test_a_minigame_popup_waits_for_a_need_in_its_end_wait_but_not_for_ever(macro, world, monkeypatch):
    played = []
    monkeypatch.setattr(macro, "grab_screen", lambda: None)
    monkeypatch.setattr(macro, "detect_minigame_popup", lambda img: True)
    monkeypatch.setattr(macro, "identify_minigame", lambda img: "ghost_gallery")
    monkeypatch.setattr(macro, "load_game_config", lambda: {"ghost_gallery_enabled": True})
    monkeypatch.setattr(macro, "play_minigame", lambda name: played.append(name))
    assert macro.minigame_popup() == "played" and played == ["ghost_gallery"]            # no need waiting: at once
    played.clear()
    with macro.end_wait("sick"):
        assert macro.minigame_popup() == "waiting" and not played                        # held off while it waits...
        world.clock.now += macro.cfg.MINIGAME_POPUP_END_WAIT - 1
        assert macro.minigame_popup() == "waiting" and not played
        world.clock.now += 2                                                             # ...but only so long
        assert macro.minigame_popup() == "played" and played == ["ghost_gallery"]
    assert macro.state.end_wait_need is None and macro.state.popup_held_since is None


def test_the_end_wait_is_marked_while_a_need_is_waited_for_and_a_held_popup_is_not_taken_for_a_paycheck(macro, monkeypatch):
    seen = []
    monkeypatch.setattr(macro, "wait_interruptible", lambda duration: seen.append(macro.state.end_wait_need))
    monkeypatch.setattr(macro, "_need_cleared", lambda name: True)
    macro.wait_until_need_gone("potty")
    assert seen == ["potty"] and macro.state.end_wait_need is None
    checked = []
    for name in ("rejoin_game", "close_backpack_if_stuck", "dismiss_stray_windows", "close_minigame_finish_screen"):
        monkeypatch.setattr(macro, name, lambda *a, **k: False)
    monkeypatch.setattr(macro, "minigame_popup", lambda play=True: "waiting")
    monkeypatch.setattr(macro, "detect_paycheck", lambda: checked.append(1) or True)
    monkeypatch.setattr(macro.cfg, "HALLOWEEN", True)
    assert macro.unscrew(in_task=True) is None and checked == []                         # the paycheck check, which clicks, is skipped


def test_the_run_logs_and_charts_of_an_older_layout_are_moved_into_the_runs_folder(macro, tmp_path, monkeypatch):
    cfg = macro.cfg
    for name, folder in (("run1.txt", "logs/runs"), ("run1.png", "images/reports"), ("run2.png", "reports")):
        path = tmp_path / "old" / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    old = tmp_path / "old"
    for name, folder in (("debug_needs.png", "images"), ("debug_buttons.png", ""), ("1/screen.png", "images/failures")):
        path = tmp_path / "old" / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    for name, value in (("DEBUG_DIR", old), ("LOGS_DIR", old / "logs"), ("FAILURE_DIR", old / "failures"), ("RUNS_DIR", old / "runs")):
        monkeypatch.setattr(cfg, name, str(value))
    macro.migrate_debug_layout()
    assert (old / "failures" / "1" / "screen.png").exists() and not (old / "images").exists()          # the failures moved up; the debug images are gone
    assert not (old / "debug_buttons.png").exists()
    assert sorted(os.listdir(old / "runs")) == ["run1.png", "run1.txt", "run2.png"]
    assert not (old / "logs" / "runs").exists() and not (old / "images" / "reports").exists() and not (old / "reports").exists()


# --- commands from outside (the Discord bot): read_command_with_id(), poll_commands() ---

def write_raw_command(macro, text):
    """A command file with `text` in it, named after the time so that the oldest is read first (the bot's names do that too)."""
    os.makedirs(macro.cfg.COMMANDS_DIR, exist_ok=True)
    name = f"{time.time():015.3f}-{len(os.listdir(macro.cfg.COMMANDS_DIR)):03d}"
    with open(os.path.join(macro.cfg.COMMANDS_DIR, name + ".json"), "w", encoding="utf-8") as f:
        f.write(text)
    return name


def write_command(macro, command, age=0, **extra):
    return write_raw_command(macro, json.dumps({"command": command, "time": time.time() - age, **extra}))


def commands_waiting(macro):
    return [name for name in os.listdir(macro.cfg.COMMANDS_DIR) if name.endswith(".json")] if os.path.isdir(macro.cfg.COMMANDS_DIR) else []


def answer(macro, name):
    with open(os.path.join(macro.cfg.COMMANDS_DIR, name + ".ack"), encoding="utf-8") as f:
        return f.read()


def test_a_fresh_command_is_read_once_and_the_file_is_removed(macro, monkeypatch, tmp_path):
    monkeypatch.setattr(macro.cfg, "COMMANDS_DIR", str(tmp_path / "commands"))
    assert macro.read_command_with_id()[1] is None                                       # (no file)
    for command in ("start", "stop", "exit", "rejoin", "setup", "refresh_debug", "trade_hub", "leave_trade_hub", "inventory", "sloop", "focus"):
        name = write_command(macro, command)
        assert macro.read_command_with_id()[1]["command"] == command and commands_waiting(macro) == []
        assert answer(macro, name) == "taken"


def test_a_command_that_is_old_unknown_or_unreadable_is_dropped_with_a_warning_but_its_file_still_goes(macro, monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(macro.cfg, "COMMANDS_DIR", str(tmp_path / "commands"))
    for write in (lambda: write_command(macro, "exit", age=macro.cfg.COMMAND_MAX_AGE + 5),      # left over from before the macro started
                  lambda: write_command(macro, "format-c"),                                       # not in the fixed list
                  lambda: write_command(macro, "stop", time="soon"),
                  lambda: write_raw_command(macro, "{not json"),
                  lambda: write_raw_command(macro, "[\"exit\"]")):
        name = write()
        caplog.clear()
        assert macro.read_command_with_id()[1] is None and commands_waiting(macro) == []
        assert answer(macro, name).startswith("dropped: ")                      # (the bot is told, not left waiting)
        assert "Command from outside dropped" in caplog.text                  # (said, not silently ignored: a host with the wrong time gets every command dropped)


def test_commands_sent_close_together_are_read_one_at_a_time_oldest_first(macro, monkeypatch, tmp_path):
    monkeypatch.setattr(macro.cfg, "COMMANDS_DIR", str(tmp_path / "commands"))
    for command in ("rejoin", "start", "stop"):
        write_command(macro, command)
    assert [macro.read_command_with_id()[1]["command"] for _ in range(3)] == ["rejoin", "start", "stop"]
    assert macro.read_command_with_id()[1] is None


def test_commands_left_from_before_the_macro_started_are_all_dropped_and_answered(macro, monkeypatch, tmp_path):
    monkeypatch.setattr(macro.cfg, "COMMANDS_DIR", str(tmp_path / "commands"))
    names = [write_command(macro, command) for command in ("exit", "start", "rejoin")]
    macro.discard_commands()
    assert commands_waiting(macro) == [] and all(answer(macro, name).startswith("dropped: ") for name in names)


class FakeRoot:
    def __init__(self):
        self.scheduled = []
    def after(self, ms, func):
        self.scheduled.append((ms, func.__name__))


def remote_gui(macro, monkeypatch, tmp_path, running=False):
    monkeypatch.setattr(macro.cfg, "COMMANDS_DIR", str(tmp_path / "commands"))
    gui = object.__new__(macro.AdoptMeGUI)
    gui.root, gui.is_running, gui.calls = FakeRoot(), running, []
    gui.pending, gui.pending_chain, gui.pending_waits, gui.running_loop = [], False, 0, running
    gui.pending_stems, gui.running_stem, gui.resume_loop = [], "", False
    gui.run_async = lambda func: gui.calls.append(("run_async", func.__name__))
    gui.stop = lambda: gui.calls.append(("stop",))
    gui.close = lambda: gui.calls.append(("close",))
    monkeypatch.setattr(macro, "log_run_event", lambda message: None)
    return gui


def test_the_commands_do_what_the_buttons_do_and_polling_goes_on_except_after_exit(macro, monkeypatch, tmp_path):
    gui = remote_gui(macro, monkeypatch, tmp_path)
    for command, expected in (("start", [("run_async", "run_workflow_loop")]), ("stop", [("stop",)]), ("exit", [("close",)])):
        gui.calls.clear(), gui.root.scheduled.clear()
        write_command(macro, command)
        gui.poll_commands()
        assert gui.calls == expected
        assert gui.root.scheduled == ([] if command == "exit" else [(macro.cfg.COMMAND_POLL_MS, "poll_commands")])
    gui.calls.clear()
    gui.poll_commands()                                                       # nothing waiting: nothing done, polling goes on
    assert gui.calls == [] and gui.root.scheduled[-1][1] == "poll_commands"


def test_outside_actions_run_what_their_buttons_run_one_after_the_other_in_the_order_asked(macro, monkeypatch, tmp_path):
    names = {"rejoin": "leave_and_rejoin", "setup": "setup_game", "trade_hub": "travel_to_trade_hub", "leave_trade_hub": "leave_trade_hub"}
    for command, action in names.items():
        gui = remote_gui(macro, monkeypatch, tmp_path)
        write_command(macro, command)
        gui.poll_commands()
        assert gui.calls == [("run_async", action)]
    gui = remote_gui(macro, monkeypatch, tmp_path, running=False)             # two asked for while something else runs, then it ends
    gui.is_running, gui.running_loop = True, False                            # (an action, not the loop: it is waited for, not stopped)
    gui.act_from_outside("rejoin")
    gui.act_from_outside("setup")
    gui.drain_pending()
    assert gui.calls == [] and gui.pending == ["rejoin", "setup"]
    gui.is_running = False
    gui.drain_pending()
    assert gui.calls == [("run_async", "leave_and_rejoin")] and gui.pending == ["setup"]
    gui.drain_pending()
    assert gui.calls[-1] == ("run_async", "setup_game") and gui.pending == []
    gui.root.scheduled.clear()
    gui.drain_pending()
    assert gui.root.scheduled == [] and not gui.pending_chain                  # the queue is empty: the checking ends


def test_a_running_loop_is_told_to_stop_once_and_given_up_on_if_it_never_ends_and_stop_leaves_the_queue_alone(macro, monkeypatch, tmp_path):
    gui = remote_gui(macro, monkeypatch, tmp_path, running=True)               # the loop is running
    gui.act_from_outside("rejoin")
    gui.act_from_outside("setup")
    gui.drain_pending()
    gui.drain_pending()
    assert gui.calls == [("stop",)] and gui.pending == ["rejoin", "setup"]      # (stopped once, not at every look)
    write_command(macro, "stop")                                                # a stop command stops what runs and clears nothing
    gui.poll_commands()
    assert gui.pending == ["rejoin", "setup"]
    gui.pending_waits = macro.cfg.REJOIN_STOP_WAIT_MS // macro.cfg.REJOIN_STOP_POLL_MS - 1
    gui.drain_pending()                                                         # a loop that never ends: the first action is given up, the next waits its turn
    assert gui.pending == ["setup"] and not any(call[0] == "run_async" for call in gui.calls)
    gui.is_running = False
    gui.drain_pending()
    assert gui.calls[-1] == ("run_async", "setup_game")


def test_the_trade_hub_trips_click_the_recorded_positions_in_order(macro, monkeypatch):
    cfg, clicks, keys = macro.cfg, [], []
    monkeypatch.setattr(macro, "focus_roblox", lambda: True)
    monkeypatch.setattr(macro, "hover_click", lambda x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(macro, "wait_interruptible", lambda duration: None)
    monkeypatch.setattr(macro, "log_run_event", lambda message: None)
    monkeypatch.setattr(macro, "close_backpack", lambda step: keys.append("closed"))
    monkeypatch.setattr(macro.pydirectinput, "press", lambda key: keys.append(key))
    assert macro.travel_to_trade_hub() and keys == [cfg.KEYS["backpack"], "closed"]
    assert clicks == [cfg.TRADE_HUB_TOYS_TAB_POS, cfg.TRADE_HUB_PHONE_POS, cfg.TRADE_HUB_EQUIP_POS, cfg.TRADE_HUB_USE_POS, cfg.TRADE_HUB_TRAVEL_POS, cfg.TRADE_HUB_YES_POS]
    clicks.clear()
    assert macro.leave_trade_hub() and clicks == [cfg.TRADE_HUB_HOME_POS, cfg.TRADE_HUB_REGULAR_SERVER_POS]
    clicks.clear(), keys.clear()
    monkeypatch.setattr(macro, "focus_roblox", lambda: False)                  # (Roblox cannot be focused: nothing is clicked)
    assert not macro.travel_to_trade_hub() and not macro.leave_trade_hub() and clicks == [] and keys == []


def test_a_command_that_raises_is_logged_and_the_polling_goes_on(macro, monkeypatch, tmp_path, caplog):
    gui = remote_gui(macro, monkeypatch, tmp_path)
    gui.set_switch = lambda kind, name, value: (_ for _ in ()).throw(PermissionError("game_config.json is locked"))
    write_command(macro, "set", kind="need", name="hungry", value=False)
    gui.poll_commands()
    assert gui.root.scheduled == [(macro.cfg.COMMAND_POLL_MS, "poll_commands")]       # (scheduled before the command ran)
    assert "failed" in caplog.text and "locked" in caplog.text
    gui.root.scheduled.clear()
    write_command(macro, "start")                                                     # and the next command is still heard
    gui.poll_commands()
    assert gui.calls == [("run_async", "run_workflow_loop")] and len(gui.root.scheduled) == 1


def test_refresh_debug_is_done_when_nothing_runs_and_the_status_is_written_again(macro, monkeypatch, tmp_path):
    done = []
    monkeypatch.setattr(macro, "refresh_debug", lambda: done.append("refreshed") or 3)
    monkeypatch.setattr(macro, "mark_stopped", lambda: done.append("status"))
    for running, expected in ((True, []), (False, ["refreshed", "status"])):
        done.clear()
        gui = remote_gui(macro, monkeypatch, tmp_path, running=running)
        write_command(macro, "refresh_debug")
        gui.poll_commands()
        assert done == expected


def test_start_does_nothing_to_a_loop_that_is_running_and_the_loop_argument_starts_it_once_the_window_is_up(macro, monkeypatch, tmp_path):
    gui = remote_gui(macro, monkeypatch, tmp_path, running=True)
    write_command(macro, "start")
    gui.poll_commands()
    assert gui.calls == []
    gui = remote_gui(macro, monkeypatch, tmp_path)
    write_command(macro, "exit")                                              # (a command left from before is dropped, not done)
    gui.start_remote_control(loop=True)
    assert [name for _, name in gui.root.scheduled] == ["poll_commands", "start_loop_from_outside"] and commands_waiting(macro) == []
    assert gui.calls == []
    gui.root.scheduled.clear()
    marked = []
    monkeypatch.setattr(macro, "mark_stopped", lambda: marked.append(True))
    gui.start_remote_control(loop=True)
    assert marked == []
    gui.root.scheduled.clear()
    gui.start_remote_control(loop=False)
    assert [name for _, name in gui.root.scheduled] == ["poll_commands"] and marked == [True]       # (no loop: the status says nothing is running)


def test_stopping_on_purpose_is_written_to_the_status_and_a_new_run_clears_it(macro, monkeypatch, tmp_path):
    monkeypatch.setattr(macro.cfg, "STATUS_PATH", str(tmp_path / "status.json"))
    macro.mark_stopped()
    assert json.load(open(macro.cfg.STATUS_PATH))["stopped"]
    macro.reset_run_stats()
    assert json.load(open(macro.cfg.STATUS_PATH))["stopped"] is None


def test_a_set_command_is_only_read_for_a_switch_that_exists_with_a_true_or_false_value(macro, monkeypatch, tmp_path):
    monkeypatch.setattr(macro.cfg, "COMMANDS_DIR", str(tmp_path / "commands"))
    good = [dict(kind="need", name="hungry", value=False), dict(kind="option", name="play_stitches_spotlight", value=True), dict(kind="option", name="show_debug_lines", value=False)]
    for details in good:
        write_command(macro, "set", **details)
        assert macro.read_command_with_id()[1] == {"command": "set", "time": pytest.approx(time.time(), abs=5), **details}
    for details in (dict(kind="need", name="play_ghost_gallery", value=True), dict(kind="option", name="hungry", value=True), dict(kind="need", name="hungry", value="off"),
                    dict(kind="need", name="hungry"), dict(kind="other", name="hungry", value=True), dict(kind="option", name="private_server_link", value=True), {}):
        write_command(macro, "set", **details)
        assert macro.read_command_with_id()[1] is None, details


def test_a_set_command_changes_the_switch_and_its_checkbox(macro, monkeypatch, tmp_path):
    gui = remote_gui(macro, monkeypatch, tmp_path)
    class Var:
        def __init__(self): self.value = None
        def set(self, value): self.value = value
    gui.need_vars, gui.config_flag_vars, gui.show_debug_var = {"hungry": Var()}, {"side_quest_enabled": Var(), "ghost_gallery_enabled": Var()}, Var()
    changes = []
    gui.set_need_enabled = lambda name, value: changes.append(("need", name, value))
    gui.set_config_flag = lambda key, value: changes.append((key, value))
    gui.set_debug_lines_shown = lambda: changes.append(("debug lines", gui.show_debug_var.value))
    for kind, name, value in (("need", "hungry", False), ("option", "side_quest", False), ("option", "play_ghost_gallery", True), ("option", "show_debug_lines", False),
                              ("option", "play_stitches_spotlight", True)):
        write_command(macro, "set", kind=kind, name=name, value=value)
        gui.poll_commands()
    assert changes == [("need", "hungry", False), ("side_quest_enabled", False), ("ghost_gallery_enabled", True), ("debug lines", False), ("stitches_spotlight_enabled", True)]
    assert gui.need_vars["hungry"].value is False and gui.config_flag_vars["side_quest_enabled"].value is False and gui.config_flag_vars["ghost_gallery_enabled"].value is True
    assert gui.show_debug_var.value is False                                              # (a switch with no checkbox of its own, like the stitches one, is only persisted)


def test_the_need_names_a_command_may_switch_are_the_handlers_there_are(macro):
    assert set(macro.cfg.NEED_NAMES) == set(macro.NEED_HANDLER_CLASSES) and len(macro.cfg.NEED_NAMES) == len(set(macro.cfg.NEED_NAMES))


def test_a_minigame_that_has_no_player_yet_is_declined_even_when_switched_on(macro, monkeypatch):
    import cv2
    monkeypatch.delitem(macro.MINIGAME_PLAYERS, "stitches_spotlight")                         # (one that is recognised but cannot be played)
    clicks, played = [], []
    monkeypatch.setattr(macro, "grab_screen", lambda: cv2.imread(os.path.join(os.path.dirname(__file__), "popups", "stitches_spotlight.png")))
    monkeypatch.setattr(macro, "load_game_config", lambda: {"stitches_spotlight_enabled": True})
    monkeypatch.setattr(macro, "play_minigame", lambda name: played.append(name))
    monkeypatch.setattr(macro, "hover_click", lambda x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(macro, "log_run_event", lambda message: None)
    assert macro.identify_minigame(macro.grab_screen()) == "stitches_spotlight" and "stitches_spotlight" not in macro.MINIGAME_PLAYERS
    assert macro.minigame_popup() == "dismissed" and played == [] and clicks[-1] == macro.cfg.MINIGAME_POPUP_NO_POS


def test_the_start_wait_ends_when_the_overlay_shows_or_the_game_is_lost_not_only_when_it_runs_out(macro, monkeypatch):
    looks = []
    monkeypatch.setattr(macro, "minigame_wait", lambda duration, watch: looks.append(duration))
    watch = macro.MinigameWatch("ghost_gallery")
    watch.last_seen = time.time()                                                  # the overlay is up: no waiting at all
    macro.wait_for_minigame_start(watch, "ghost_gallery")
    assert looks == [] and watch.start_grace_from is not None
    watch = macro.MinigameWatch("ghost_gallery")
    watch.disconnected = True                                                      # a disconnect in the lobby: the rejoin should not wait for the whole start wait
    macro.wait_for_minigame_start(watch, "ghost_gallery")
    assert looks == []
    watch = macro.MinigameWatch("ghost_gallery")                                   # nothing yet: it looks, at the interval, until the overlay shows
    monkeypatch.setattr(macro, "minigame_wait", lambda duration, w: (looks.append(duration), setattr(w, "last_seen", time.time())))
    macro.wait_for_minigame_start(watch, "ghost_gallery")
    assert looks == [macro.cfg.MINIGAME_START_LOOK_INTERVAL]


def test_release_all_inputs_lets_go_of_every_key_that_is_down_including_the_camera_arrows(macro, monkeypatch):
    ups = []
    monkeypatch.setattr(macro.pydirectinput, "keyDown", lambda key: None)
    monkeypatch.setattr(macro.pydirectinput, "keyUp", lambda key: ups.append(key))
    monkeypatch.setattr(macro, "pressed_inputs", lambda: set())
    macro.key_down("right")                                            # (the Stitches' Spotlight camera turn, cut short by an exit)
    macro.key_down("some_future_key")                                  # (a key no list has heard of)
    macro.release_all_inputs()
    assert "right" in ups and "some_future_key" in ups
    assert set(macro.cfg.HELD_KEYS) <= set(ups) and ups.count("right") == 1       # (the keys of the fixed list too, each once)
    assert macro.HELD_KEYS == set()
    ups.clear()
    macro.key_down("w")
    macro.key_up("w")
    assert macro.HELD_KEYS == set() and ups == ["w"]


def test_a_key_is_let_go_of_whatever_happens_during_the_hold_and_the_safety_net_survives_a_failure(macro, monkeypatch):
    ups = []
    monkeypatch.setattr(macro.pydirectinput, "keyDown", lambda key: None)
    monkeypatch.setattr(macro.pydirectinput, "keyUp", lambda key: ups.append(key))
    monkeypatch.setattr(macro, "wait_interruptible", lambda duration: (_ for _ in ()).throw(macro.StopRequested()))
    with pytest.raises(macro.StopRequested):
        macro.hold_key("left", 0.2)
    assert ups == ["left"] and macro.HELD_KEYS == set()
    monkeypatch.setattr(macro, "pressed_inputs", lambda: (_ for _ in ()).throw(OSError("no state")))
    macro.release_all_inputs()                                          # (does not raise: what follows in the caller still happens)


def test_held_keys_and_buttons_are_released_without_the_fail_safe_check_and_without_moving_the_cursor(macro, monkeypatch):
    """With the cursor in a corner PyDirectInput's checked calls raise; its unchecked ones (the wrapped function) do not, so everything is let go of."""
    ups, mouse_ups, moves = [], [], []
    def checked_up(key):
        raise RuntimeError("fail-safe: the cursor is in a corner")
    def checked_mouse_up(button="left"):
        raise RuntimeError("fail-safe: the cursor is in a corner")
    checked_up.__wrapped__ = lambda key: ups.append(key)                          # (what functools.wraps keeps)
    checked_mouse_up.__wrapped__ = lambda button="left": mouse_ups.append(button)
    monkeypatch.setattr(macro.pydirectinput, "keyDown", lambda key: None)
    monkeypatch.setattr(macro.pydirectinput, "keyUp", checked_up)
    monkeypatch.setattr(macro.pydirectinput, "mouseUp", checked_mouse_up, raising=False)
    monkeypatch.setattr(macro.pydirectinput, "moveTo", lambda *a, **k: moves.append(a), raising=False)
    monkeypatch.setattr(macro, "pressed_inputs", lambda: {"mouse_right"})
    macro.key_down("a")
    macro.release_all_inputs()
    assert "a" in ups and set(macro.cfg.HELD_KEYS) <= set(ups) and mouse_ups == ["right"] and moves == []
    assert macro.pydirectinput.keyUp is checked_up                                  # (the checked call itself is left as it was: the fail-safe stays on)


def test_a_key_that_could_not_be_let_go_of_stays_known_so_that_the_safety_net_tries_it_again(macro, monkeypatch):
    monkeypatch.setattr(macro.pydirectinput, "keyDown", lambda key: None)
    monkeypatch.setattr(macro.pydirectinput, "keyUp", lambda key: (_ for _ in ()).throw(RuntimeError("fail-safe")))
    macro.key_down("some_key")
    with pytest.raises(RuntimeError):
        macro.key_up("some_key")
    assert "some_key" in macro.HELD_KEYS
    macro.HELD_KEYS.clear()


# --- blanking the screen (the H key) ---

def test_the_blackout_test_tells_whether_the_screenshots_see_its_white_window(macro):
    dark = np.full((100, 100, 3), 60, np.uint8)
    white = np.full((100, 100, 3), 255, np.uint8)
    assert macro.blackout_seen_by_capture(dark, white) is True                       # the white window shows in the screenshot: it is not left out of it
    assert macro.blackout_seen_by_capture(dark, dark.copy()) is False                # the screenshot still shows the game: it is left out
    noisy = np.clip(dark.astype(int) + 5, 0, 255).astype(np.uint8)                    # (the game moving a little)
    assert macro.blackout_seen_by_capture(dark, noisy) is False
    assert macro.blackout_seen_by_capture(white, white.copy()) is None                # a white screen cannot be told from the white window


class FakeWindow:
    def __init__(self):
        self.destroyed, self.colors = False, []
    def configure(self, **kw):
        self.colors.append(kw.get("bg"))
    def destroy(self):
        self.destroyed = True


def blackout_with(macro, monkeypatch, screens, window="ok", supported=True):
    fake = FakeWindow()
    root = types.SimpleNamespace(update=lambda: None, after=lambda ms, func: "job", after_cancel=lambda job: None)
    shown = macro.Blackout(root)
    monkeypatch.setattr(shown, "supported", lambda: supported)
    monkeypatch.setattr(shown, "make_window", lambda: fake if window == "ok" else None)
    seen = iter(screens)
    monkeypatch.setattr(macro, "grab_screen", lambda: next(seen))
    monkeypatch.setattr(macro.time, "sleep", lambda s: None)
    return shown, fake


def test_the_screen_is_blanked_only_when_the_screenshots_do_not_see_the_window_and_the_key_toggles_it(macro, monkeypatch):
    dark = np.full((100, 100, 3), 60, np.uint8)
    shown, fake = blackout_with(macro, monkeypatch, [dark, dark.copy()])
    assert shown.toggle() is True and shown.shown and fake.colors == ["black"]       # (the white test window made black once it is known to be left out)
    assert shown.toggle() is False and not shown.shown and fake.destroyed


def test_the_screen_is_not_blanked_when_the_screenshots_would_see_the_window_or_it_cannot_be_told(macro, monkeypatch, caplog):
    dark, white = np.full((100, 100, 3), 60, np.uint8), np.full((100, 100, 3), 255, np.uint8)
    shown, fake = blackout_with(macro, monkeypatch, [dark, white])
    assert shown.toggle() is False and not shown.shown and fake.destroyed and "would see the black window" in caplog.text
    shown, fake = blackout_with(macro, monkeypatch, [white, white])
    assert shown.toggle() is False and fake.destroyed and "too bright" in caplog.text
    shown, fake = blackout_with(macro, monkeypatch, [dark, dark], window=None)         # Windows refuses to leave a window out of screenshots
    assert shown.toggle() is False and not shown.shown
    shown, fake = blackout_with(macro, monkeypatch, [], supported=False)               # not Windows
    assert shown.toggle() is False and not fake.destroyed


def test_closing_asks_for_the_stop_before_it_closes_the_capture_and_waits_for_the_worker_before_it_lets_go(macro, monkeypatch):
    order = []
    gui = object.__new__(macro.AdoptMeGUI)
    gui.root = types.SimpleNamespace(destroy=lambda: order.append("destroyed"))
    gui.blackout = types.SimpleNamespace(hide=lambda: order.append("blackout away"))
    gui.worker = types.SimpleNamespace(is_alive=lambda: True, join=lambda timeout: order.append(f"worker joined ({timeout == macro.cfg.CLOSE_WORKER_WAIT})"))
    macro.STOP_EVENT.clear()
    class Capture:
        def stop(self):
            order.append(f"capture stopped (stop already asked: {macro.STOP_EVENT.is_set()})")
    macro.state.capture = Capture()
    monkeypatch.setattr(macro, "mark_stopped", lambda: order.append("status"))
    monkeypatch.setattr(macro, "release_all_inputs", lambda: order.append("released"))
    gui.close()
    macro.STOP_EVENT.clear()
    assert order == ["capture stopped (stop already asked: True)", "status", "worker joined (True)", "released", "blackout away", "destroyed"]


# --- the end of an outside action, and the loop that goes on after Setup ---

def ending_gui(macro, monkeypatch, tmp_path, was_loop=False):
    gui = remote_gui(macro, monkeypatch, tmp_path, running=True)
    gui.running_loop = was_loop
    gui._set_running_ui = lambda running, status=None: None
    gui.start_loop_from_outside = lambda: gui.calls.append(("start_loop",))
    return gui


def done_text(macro, stem):
    path = os.path.join(macro.cfg.COMMANDS_DIR, stem + ".done")
    return open(path, encoding="utf-8").read() if os.path.exists(path) else None


def test_a_command_is_read_with_its_name_and_the_end_of_its_action_is_written_for_the_bot(macro, monkeypatch, tmp_path):
    monkeypatch.setattr(macro.cfg, "COMMANDS_DIR", str(tmp_path / "commands"))
    name = write_command(macro, "setup")
    stem, data = macro.read_command_with_id()
    assert stem == name and data["command"] == "setup"
    macro.finish_command(stem, "done")
    assert done_text(macro, stem) == "done"
    macro.finish_command("", "done")                                                      # (a command with no name: nothing is written)
    assert sorted(os.listdir(macro.cfg.COMMANDS_DIR)) == [stem + ".ack", stem + ".done"]
    assert macro.read_command_with_id() == ("", None)


def test_the_end_of_an_action_asked_for_from_outside_is_written_with_the_status_the_buttons_show(macro, monkeypatch, tmp_path):
    expected = {"[DONE]": "done", "[STOPPED]": "stopped", "[STOPPED: Roblox not focused]": "stopped: roblox not focused", "[ERROR]": "error"}
    for i, (status, text) in enumerate(expected.items()):
        gui = ending_gui(macro, monkeypatch, tmp_path)
        gui.running_stem = f"command-{i}"
        gui._finish_running(status)
        assert done_text(macro, f"command-{i}") == text and gui.running_stem == ""
    written = sorted(os.listdir(macro.cfg.COMMANDS_DIR))
    ending_gui(macro, monkeypatch, tmp_path)._finish_running("[DONE]")                    # (an action of the window's own buttons: nothing is written)
    assert sorted(os.listdir(macro.cfg.COMMANDS_DIR)) == written


def test_an_action_that_is_given_up_because_the_loop_would_not_stop_says_so(macro, monkeypatch, tmp_path):
    gui = remote_gui(macro, monkeypatch, tmp_path, running=True)
    gui.act_from_outside("rejoin", "stuck")
    for _ in range(macro.cfg.REJOIN_STOP_WAIT_MS // macro.cfg.REJOIN_STOP_POLL_MS):
        gui.drain_pending()
    assert gui.pending == [] and done_text(macro, "stuck").startswith("given up") and gui.resume_loop is False


def test_a_loop_that_was_running_starts_again_when_the_setup_that_stopped_it_is_done(macro, monkeypatch, tmp_path):
    gui = remote_gui(macro, monkeypatch, tmp_path, running=True)                           # the loop runs
    gui._set_running_ui = lambda running, status=None: None
    gui.start_loop_from_outside = lambda: gui.calls.append(("start_loop",))
    gui.act_from_outside("setup", "s1")
    assert gui.calls == [("stop",)] and gui.resume_loop is True
    gui._finish_running("[STOPPED]")                                                       # the loop ends: nothing is started yet
    assert ("start_loop",) not in gui.calls
    gui.is_running, gui.running_loop = False, False
    gui.drain_pending()
    assert gui.calls[-1] == ("run_async", "setup_game")
    gui.running_stem, gui.running_loop = "s1", False                                       # (the action runs)
    gui._finish_running("[DONE]")
    assert gui.calls[-1] == ("start_loop",) and gui.resume_loop is False and done_text(macro, "s1") == "done"


def test_the_loop_does_not_start_again_after_a_setup_that_was_stopped_failed_or_is_not_the_last_asked_for(macro, monkeypatch, tmp_path):
    for status in ("[STOPPED]", "[ERROR]", "[STOPPED: Roblox not focused]"):
        gui = ending_gui(macro, monkeypatch, tmp_path)
        gui.resume_loop = True
        gui._finish_running(status)
        assert ("start_loop",) not in gui.calls and gui.resume_loop is False               # (a person who pressed STOP wants it stopped)
    gui = ending_gui(macro, monkeypatch, tmp_path)
    gui.resume_loop, gui.pending = True, ["rejoin"]                                        # another action waits: the loop goes on after the last
    gui._finish_running("[DONE]")
    assert ("start_loop",) not in gui.calls and gui.resume_loop is True
    gui.pending = []
    gui._finish_running("[DONE]")
    assert gui.calls[-1] == ("start_loop",)


def test_sloop_runs_setup_and_then_the_loop_whether_or_not_one_was_running(macro, monkeypatch, tmp_path):
    idle = remote_gui(macro, monkeypatch, tmp_path)                                         # nothing runs
    idle._set_running_ui = lambda running, status=None: None
    idle.start_loop_from_outside = lambda: idle.calls.append(("start_loop",))
    idle.do_command("sloop", {"command": "sloop"}, "s1")
    assert idle.calls == [("run_async", "exit_minigame_and_setup")] and idle.resume_loop is True
    idle.running_stem, idle.running_loop, idle.is_running = "s1", False, True
    idle._finish_running("[DONE]")
    assert idle.calls[-1] == ("start_loop",) and done_text(macro, "s1") == "done"             # Setup done: the loop
    running = remote_gui(macro, monkeypatch, tmp_path, running=True)                        # a loop runs: stopped first, as for Setup
    running.do_command("sloop", {"command": "sloop"}, "s2")
    assert running.calls == [("stop",)] and running.resume_loop is True and running.pending == ["sloop"]
    stopped = ending_gui(macro, monkeypatch, tmp_path)                                      # (and a Setup that was stopped does not go on to the loop)
    stopped.resume_loop = True
    stopped._finish_running("[STOPPED]")
    assert ("start_loop",) not in stopped.calls


def test_the_focus_command_brings_the_roblox_window_to_the_front_without_stopping_a_loop(macro, monkeypatch, tmp_path):
    focused = []
    monkeypatch.setattr(macro, "focus_roblox", lambda: focused.append(1) or True)
    gui = remote_gui(macro, monkeypatch, tmp_path, running=True)                           # a loop runs
    gui.do_command("focus", {"command": "focus"}, "f1")
    assert focused == [1] and gui.calls == [] and gui.pending == []                       # (nothing stopped, nothing queued)


def test_only_the_actions_in_resume_loop_after_bring_the_loop_back(macro, monkeypatch, tmp_path):
    assert macro.cfg.RESUME_LOOP_AFTER == ("setup",)
    for command, resumes in (("setup", True), ("rejoin", False), ("trade_hub", False)):
        gui = remote_gui(macro, monkeypatch, tmp_path, running=True)
        gui.act_from_outside(command)
        assert gui.resume_loop is resumes, command


class FakeBlackout:
    def __init__(self, shown=False):
        self.shown, self.toggles, self.hides, self.raised = shown, 0, 0, 0

    def toggle(self):
        self.toggles += 1
        self.shown = not self.shown

    def hide(self):
        self.hides += 1
        self.shown = False

    def raise_over(self):
        self.raised += 1


def blackout_gui(macro, monkeypatch, tmp_path, hotkey_on=True, shown=False, loop=True):
    monkeypatch.setattr(macro.cfg, "GAME_CONFIG_PATH", str(tmp_path / ".config"))
    gui = object.__new__(macro.AdoptMeGUI)
    gui.is_running = gui.running_loop = loop
    gui.root = types.SimpleNamespace()
    gui.blackout = FakeBlackout(shown)
    gui.config_flag_vars = {"blackout_hotkey_enabled": types.SimpleNamespace(get=lambda: hotkey_on)}
    return gui


def test_the_blackout_key_blanks_the_screen_only_while_the_loop_runs_but_always_shows_it_again(macro, monkeypatch, tmp_path):
    idle = blackout_gui(macro, monkeypatch, tmp_path, loop=False)                          # nothing runs
    idle.blackout_by_hotkey()
    assert idle.blackout.toggles == 0 and not idle.blackout.shown
    other = blackout_gui(macro, monkeypatch, tmp_path)                                     # an action runs (Setup, say), not the loop
    other.running_loop = False
    other.blackout_by_hotkey()
    assert other.blackout.toggles == 0
    running = blackout_gui(macro, monkeypatch, tmp_path)
    running.blackout_by_hotkey()
    assert running.blackout.shown and running.blackout.toggles == 1                         # the loop runs: blank
    running.is_running = running.running_loop = False                                       # the loop ends with the screen blank
    running.blackout_by_hotkey()
    assert not running.blackout.shown and running.blackout.toggles == 2                     # (it can be shown again)


def test_the_blackout_key_blanks_the_screen_only_while_its_checkbox_is_on(macro, monkeypatch, tmp_path):
    off = blackout_gui(macro, monkeypatch, tmp_path, hotkey_on=False)
    off.blackout_by_hotkey()
    assert off.blackout.toggles == 0 and not off.blackout.shown        # (the key is typed in the game)
    on = blackout_gui(macro, monkeypatch, tmp_path)
    on.blackout_by_hotkey()
    assert on.blackout.toggles == 1 and on.blackout.shown
    on.blackout_by_hotkey()
    assert on.blackout.toggles == 2 and not on.blackout.shown
    assert macro.load_game_config()["blackout_hotkey_enabled"] is True                       # on unless it was turned off


def test_the_black_window_is_put_back_above_everything_again_and_again_while_the_screen_is_blank(macro, monkeypatch):
    dark = np.full((100, 100, 3), 60, np.uint8)
    jobs, cancelled, raised = [], [], []
    root = types.SimpleNamespace(update=lambda: None, after=lambda ms, func: jobs.append((ms, func)) or f"job{len(jobs)}", after_cancel=cancelled.append)
    shown = macro.Blackout(root)
    monkeypatch.setattr(shown, "supported", lambda: True)
    monkeypatch.setattr(shown, "make_window", lambda: FakeWindow())
    monkeypatch.setattr(shown, "raise_over", lambda: raised.append(1))
    seen = iter([dark, dark.copy()])
    monkeypatch.setattr(macro, "grab_screen", lambda: next(seen))
    monkeypatch.setattr(macro.time, "sleep", lambda s: None)
    assert shown.toggle() is True
    assert len(raised) == 1 and [ms for ms, _ in jobs] == [macro.cfg.BLACKOUT_RAISE_INTERVAL_MS]       # raised as it goes up, and scheduled again
    jobs[0][1]()                                                                                      # the timer fires
    assert len(raised) == 2 and len(jobs) == 2
    shown.toggle()                                                                                    # away: the timer is cancelled
    assert cancelled == ["job2"] and not shown.shown
    jobs[1][1]()                                                                                      # (a timer that fires anyway does nothing)
    assert len(raised) == 2 and len(jobs) == 2


def test_the_black_window_is_put_on_top_with_a_handle_as_wide_as_a_pointer(macro, monkeypatch):
    import ctypes
    calls = []
    user32 = types.SimpleNamespace(GetParent=lambda hwnd: 0, SetWindowPos=lambda *args: calls.append(args) or 1)
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(user32=user32), raising=False)
    shown = macro.Blackout(types.SimpleNamespace())
    monkeypatch.setattr(shown, "supported", lambda: True)
    shown.raise_over()
    assert calls == []                                                                           # (no window: nothing)
    shown.window = types.SimpleNamespace(winfo_id=lambda: 4242)
    shown.raise_over()
    (hwnd, after, x, y, width, height, flags), = calls
    assert hwnd.value == 4242 and after.value == ctypes.c_void_p(-1).value and (x, y, width, height) == (0, 0, 0, 0)     # HWND_TOPMOST as a pointer-sized -1, not a 32-bit one
    assert flags == 0x0001 | 0x0002 | 0x0010                                                      # no size, no move, no activation


def test_bringing_the_window_back_after_a_minigame_puts_the_black_window_over_it_again(macro):
    order = []
    gui = object.__new__(macro.AdoptMeGUI)
    gui.root = types.SimpleNamespace(attributes=lambda *a: order.append("topmost"), lift=lambda: order.append("lift"))
    gui.blackout = types.SimpleNamespace(raise_over=lambda: order.append("black window raised"))
    gui.bring_to_front()
    assert order == ["topmost", "lift", "black window raised"]


def test_turning_the_blackout_key_off_takes_a_blank_screen_away(macro, monkeypatch, tmp_path):
    gui = blackout_gui(macro, monkeypatch, tmp_path, shown=True)
    gui.set_config_flag("blackout_hotkey_enabled", True)
    assert gui.blackout.shown and gui.blackout.hides == 0
    gui.set_config_flag("blackout_hotkey_enabled", False)
    assert not gui.blackout.shown and gui.blackout.hides == 1
    assert macro.load_game_config()["blackout_hotkey_enabled"] is False


def test_two_threads_writing_the_status_at_once_leave_one_whole_file(macro, monkeypatch, tmp_path):
    monkeypatch.setattr(macro.cfg, "STATUS_PATH", str(tmp_path / "status.json"))
    errors = []
    def writer():
        for _ in range(150):
            macro.write_status()
            try:
                json.load(open(macro.cfg.STATUS_PATH))
            except Exception as error:                                  # a half-written or missing file would show here
                errors.append(error)
    threads = [threading.Thread(target=writer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


def test_a_stop_lets_go_of_everything_at_once_and_nothing_is_pressed_after_it(macro, monkeypatch):
    events = []
    monkeypatch.setattr(macro.pydirectinput, "keyDown", lambda key: events.append(("down", key)))
    monkeypatch.setattr(macro.pydirectinput, "keyUp", lambda key: events.append(("up", key)))
    monkeypatch.setattr(macro.pydirectinput, "mouseDown", lambda button="left": events.append(("mouse down", button)), raising=False)
    monkeypatch.setattr(macro, "pressed_inputs", lambda: set())
    gui = object.__new__(macro.AdoptMeGUI)
    gui.is_running, gui.status = True, types.SimpleNamespace(config=lambda **k: None)
    macro.STOP_EVENT.clear()
    macro.key_down("w")
    gui.stop()                                                                        # (from the Tk thread, while the worker is still in its wait)
    assert ("up", "w") in events and macro.STOP_EVENT.is_set()
    events.clear()
    with pytest.raises(macro.StopRequested):
        macro.key_down("d")
    with pytest.raises(macro.StopRequested):
        macro.mouse_down("right")
    assert events == []                                                               # the worker's next press is refused
    macro.STOP_EVENT.clear()


def test_the_hotkey_watcher_survives_a_failed_look_and_ends_only_when_the_window_is_gone(macro, monkeypatch, caplog):
    monkeypatch.setattr(macro.cfg, "STOP_HOTKEY_POLL_INTERVAL", 0.001)
    calls = []
    def hotkey_down(key):
        calls.append(key)
        if len(calls) <= 6:
            raise ValueError("the focused window could not be read")           # (pygetwindow on an odd window)
        if len(calls) > 40:
            raise RuntimeError("the window is gone")
        return False
    monkeypatch.setattr(macro, "hotkey_down", hotkey_down)
    gui = object.__new__(macro.AdoptMeGUI)
    gui.root, gui.is_running = types.SimpleNamespace(after=lambda ms, f: None), False
    before = threading.active_count()
    gui.watch_stop_hotkey()
    deadline = time.time() + 10
    while len(calls) <= 40 and time.time() < deadline:
        time.sleep(0.01)
    assert len(calls) > 40                                                       # it went on after the failures, and every key was watched
    assert "failed (the watching goes on)" in caplog.text and caplog.text.count("failed (the watching goes on)") <= len(macro.cfg.HOTKEYS)       # (said once per key, not at every poll)
    time.sleep(0.1)
    assert threading.active_count() <= before                                    # and the thread ended when the window was gone


def test_the_need_watcher_goes_on_after_a_look_that_failed_and_still_reports_the_need_gone(macro, monkeypatch, caplog):
    monkeypatch.setattr(macro.cfg, "NEED_GONE_POLL_INTERVAL", 0.001)
    answers_given = iter([RuntimeError("a screenshot failed"), RuntimeError("again"), False, True])
    def need_cleared(name):
        answer = next(answers_given)
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(macro, "_need_cleared", need_cleared)
    cleared, stop, answers = threading.Event(), threading.Event(), []
    macro._watch_need_gone("hungry", cleared, stop, answers)
    assert cleared.is_set() and answers == [False, True]
    assert caplog.text.count("a look failed") == 1                               # (said once)


def test_the_flag_that_nothing_runs_goes_down_on_tks_thread_together_with_the_buttons(macro):
    queued, buttons = [], []
    gui = object.__new__(macro.AdoptMeGUI)
    gui.is_running, gui.root = False, types.SimpleNamespace(after=lambda ms, f: queued.append(f))
    gui.pending, gui.pending_chain, gui.pending_waits, gui.running_loop = [], False, 0, False
    gui.pending_stems, gui.running_stem, gui.resume_loop = [], "", False
    gui._set_running_ui = lambda running, status=None: buttons.append((running, status))
    gui.run_async(lambda: None)
    gui.worker.join(10)
    assert gui.is_running is True and buttons == [(True, None)]                    # (the worker is done, but Tk's thread has not been told yet: nothing can start in between)
    queued[-1]()
    assert gui.is_running is False and buttons[-1] == (False, "[DONE]")


def test_refresh_debug_from_outside_is_refused_while_a_capture_is_recording(macro, monkeypatch, tmp_path):
    done = []
    monkeypatch.setattr(macro, "refresh_debug", lambda: done.append("refreshed") or 0)
    monkeypatch.setattr(macro, "mark_stopped", lambda: None)
    gui = remote_gui(macro, monkeypatch, tmp_path)
    monkeypatch.setattr(macro.state, "capture", object())
    gui.refresh_debug_from_outside()
    assert done == []
    monkeypatch.setattr(macro.state, "capture", None)
    gui.refresh_debug_from_outside()
    assert done == ["refreshed"]


# --- the screenshot hotkey ---

def fake_layout(macro, monkeypatch, scans, pressed=()):
    """ctypes.windll with a keyboard layout: VkKeyScanW answers from `scans` (character -> its answer), and the keys in `pressed` are down."""
    import ctypes
    user32 = types.SimpleNamespace(VkKeyScanW=lambda char: scans.get(chr(char), -1), GetAsyncKeyState=lambda vk: 0x8000 if vk in pressed else 0)
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(user32=user32), raising=False)


def test_a_hotkey_is_its_letter_or_digit_or_the_key_the_layout_has_for_the_character(macro, monkeypatch):
    fake_layout(macro, monkeypatch, {"\u00f4": 0x00DE, "\u00e9": 0x0132, "x": 0x0058})        # an ô on a key of its own (VK 0xDE), an é that needs Shift (high byte)
    assert macro.hotkey_vk("p") == ord("P") and macro.hotkey_vk("7") == ord("7")            # (letters and digits are their own: the layout is not asked)
    assert macro.hotkey_vk("\u00f4") == 0xDE
    assert macro.hotkey_vk("\u00e9") is None and macro.hotkey_vk("\u00fc") is None         # needs Shift; not on the layout at all (-1)


def test_a_hotkey_is_down_while_its_key_is_and_one_the_layout_lacks_is_never_down_and_said_once(macro, monkeypatch, caplog):
    fake_layout(macro, monkeypatch, {"\u00f4": 0x00DE}, pressed={0xDE, ord("M")})
    assert macro.hotkey_down("\u00f4") is True and macro.hotkey_down("m") is True and macro.hotkey_down("h") is False
    fake_layout(macro, monkeypatch, {}, pressed={0xDE})                                        # another layout: no such key
    macro._WARNED_HOTKEYS.discard("\u00f4")
    assert macro.hotkey_down("\u00f4") is False and macro.hotkey_down("\u00f4") is False
    assert caplog.text.count("is not a key of its own on this keyboard layout") == 1


def test_a_screenshot_is_saved_in_the_debug_folder_under_its_time_and_a_second_in_the_same_second_gets_a_number(macro, monkeypatch, tmp_path):
    import numpy as np
    import cv2
    monkeypatch.setattr(macro.cfg, "SCREENSHOTS_DIR", str(tmp_path / "debug" / "screenshots"))
    monkeypatch.setattr(macro, "grab_full_screen", lambda: np.full((20, 30, 3), 90, np.uint8))
    monkeypatch.setattr(macro, "grab_screen", lambda: (_ for _ in ()).throw(AssertionError("the Roblox window's picture is not what is saved")))
    monkeypatch.setattr(macro.time, "strftime", lambda fmt: "screenshot_20261025_120000")
    first, second = macro.save_screenshot(), macro.save_screenshot()
    assert os.path.basename(first) == "screenshot_20261025_120000.png" and os.path.basename(second) == "screenshot_20261025_120000_2.png"
    assert cv2.imread(first).shape == (20, 30, 3)
    monkeypatch.setattr(macro.cv2, "imwrite", lambda path, img: False)
    with pytest.raises(OSError):
        macro.save_screenshot()


def test_the_screenshot_key_saves_a_picture_and_a_failure_to_save_is_logged_not_raised(macro, monkeypatch, caplog):
    saved = []
    gui = object.__new__(macro.AdoptMeGUI)
    monkeypatch.setattr(macro, "save_screenshot", lambda: saved.append(1) or "/x/screenshot.png")
    gui.screenshot_by_hotkey()
    assert saved == [1] and "Screenshot saved: /x/screenshot.png" in caplog.text

    def broken():
        raise OSError("disk full")
    monkeypatch.setattr(macro, "save_screenshot", broken)
    gui.screenshot_by_hotkey()
    assert "Saving a screenshot failed" in caplog.text


# --- the screen blanked by a command ---

def blackout_gui_for_command(macro, shown=False, show_works=True):
    gui = object.__new__(macro.AdoptMeGUI)

    class Blackout:
        def __init__(self):
            self.shown, self.calls = shown, []

        def show(self):
            self.calls.append("show")
            self.shown = show_works
            return show_works

        def hide(self):
            self.calls.append("hide")
            self.shown = False
    gui.blackout = Blackout()
    return gui


def test_the_blackout_command_blanks_shows_or_switches_and_says_what_the_screen_is(macro):
    gui = blackout_gui_for_command(macro)
    assert gui.blackout_command(True) == "blanked" and gui.blackout.calls == ["show"]
    assert gui.blackout_command(True) == "blanked" and gui.blackout.calls == ["show"]            # (already blank: nothing again)
    assert gui.blackout_command(False) == "shown" and gui.blackout.calls == ["show", "hide"]
    assert gui.blackout_command(None) == "blanked"                                                # switched: it was shown
    assert gui.blackout_command(None) == "shown"                                                  # and back
    refused = blackout_gui_for_command(macro, show_works=False)                                   # the test flash saw the window in a screenshot, say
    assert refused.blackout_command(True) == "not blanked"


def test_the_blackout_command_is_obeyed_whatever_the_hotkeys_checkbox_says_and_the_answer_is_left_for_the_bot(macro, monkeypatch, tmp_path):
    gui = remote_gui(macro, monkeypatch, tmp_path)
    gui.blackout_command = lambda value: gui.calls.append(("blackout", value)) or "blanked"
    gui.config_flag_vars = {"blackout_hotkey_enabled": types.SimpleNamespace(get=lambda: False)}      # (the key is off: the command works anyway)
    gui.do_command("blackout", {"command": "blackout", "value": True}, "b1")
    gui.do_command("blackout", {"command": "blackout"}, "b2")
    gui.do_command("blackout", {"command": "blackout", "value": "yes"}, "b3")                     # (not a switch: it switches)
    assert gui.calls == [("blackout", True), ("blackout", None), ("blackout", None)]
    assert [done_text(macro, name) for name in ("b1", "b2", "b3")] == ["blanked"] * 3
