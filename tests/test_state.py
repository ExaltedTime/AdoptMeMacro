"""State, stats and the logic that runs on them: stuck needs, failures, the quiet and idle checks, stopping."""
import json
import os
import threading
import types
import time

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


def test_a_button_action_cut_short_by_a_rejoin_is_done_not_a_crash(macro, monkeypatch):
    """The Setup button runs setup_game() under the in-task checks; a disconnect rejoins (and sets up) mid-way and
    raises TaskInterrupted, which the worker must not report as a crash."""
    gui = object.__new__(macro.AdoptMeGUI)
    gui.is_running = False
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
    for folder in ("images", "logs", "recordings"):
        (debug / folder).mkdir(parents=True)
        (debug / folder / "x.txt").write_text("x")
    (debug / ".config").write_text("{}")
    for name, value in (("DEBUG_DIR", str(debug)), ("IMAGES_DIR", str(debug / "images")), ("LOGS_DIR", str(debug / "logs")),
                        ("GAME_CONFIG_PATH", str(debug / ".config"))):
        monkeypatch.setattr(macro.cfg, name, value)
    real_rmtree = macro.shutil.rmtree

    def rmtree(path, *args, **kwargs):
        if path.endswith("recordings"):
            raise PermissionError("a video is still open")
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(macro.shutil, "rmtree", rmtree)
    assert macro.refresh_debug() == 2                                  # images and logs went, recordings stayed
    assert (debug / "images").is_dir() and (debug / "logs").is_dir() and (debug / ".config").exists()


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
    assert {macro.cfg.KEY_ZOOM_IN, macro.cfg.KEY_ZOOM_OUT, macro.cfg.KEY_HELICOPTER} <= set(released)
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
    monkeypatch.setattr(macro, "close_minigame_win_screen", lambda abandon=True: False)
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


def test_the_in_task_check_closes_a_backpack_seen_open_twice_but_not_one_a_handler_has_open_on_purpose(macro, monkeypatch, tmp_path):
    import numpy as np
    closed = []
    monkeypatch.setattr(macro.cfg, "IMAGES_DIR", str(tmp_path))
    monkeypatch.setattr(macro, "is_roblox_focused", lambda: True)
    monkeypatch.setattr(macro, "grab_screen", lambda: np.zeros((20, 20, 3), np.uint8))
    monkeypatch.setattr(macro, "detect_backpack_open", lambda img: True)
    monkeypatch.setattr(macro, "close_backpack_if_open", lambda step="": closed.append(step))
    stuck = macro.close_backpack_if_stuck
    assert stuck(True) is True and closed == []                                            # one sighting is not enough...
    assert (tmp_path / "debug_backpack_first_sighting.png").exists()                       # ...but what the screen looked like is kept
    assert stuck(True) is False and len(closed) == 1                                       # the second one closes it
    closed.clear()
    with macro.backpack_on_purpose():
        assert [stuck(True) for _ in range(4)] == [True] * 4 and closed == []              # a handler has it open: never
    assert stuck(True) is True and closed == []                                            # (and what it saw before doesn't count: a first sighting again)
    macro.state.backpack_strikes = 1
    with macro.backpack_on_purpose():
        pass
    assert stuck(True) is True and closed == []                                            # a sighting from before the block does not count after it either


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
        if key == macro.cfg.KEY_BACKPACK:
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
    macro.teleport_to((100, 100))
    assert [n for n, w in wanted if w] == ["wait", "click", "wait", "click", "wait", "click"]           # opening it, the category, the teleport: exempt
    assert [w for n, w in wanted[6:]] == [0] * len(wanted[6:])                                          # the long wait after the teleport and the walk back: not
    wanted.clear()
    assert macro.CatchNeedHandler().handle() is True
    assert [w for n, w in wanted if n in ("tab", "close")] == [1, 1] and wanted[-1][1] == 0 and macro.state.backpack_wanted == 0


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


@pytest.fixture
def sick_run(macro, monkeypatch):
    """The sick handler up to its press of 'e': run["dialog"] is what each look for the doctor's dialog sees, in order.
    The need has an entry of its own here (positions made up), so none of this depends on how the real one is tuned
    or on HALLOWEEN, which decides whether the real one exists."""
    import numpy as np
    from functools import partial
    final, prompt = (610, 620), (630, 640)
    monkeypatch.setitem(macro.cfg.TELEPORT_WALK_NEEDS, "sick", dict(teleport_pos=(1, 2), steps=(("w", 1.0),), helicopter=False,
                                                                    final_click=final, prompt_pos=prompt))
    monkeypatch.setitem(macro.NEED_HANDLER_CLASSES, "sick", partial(macro.TeleportWalkNeedHandler, "sick"))
    run = {"dialog": [True], "waited": [], "respawns": 0, "events": [], "pet_focused": False, "final": final, "prompt": prompt}

    def dialog_up(img):
        run["events"].append("look")
        looks = run["dialog"]
        return looks.pop(0) if len(looks) > 1 else looks[0]

    monkeypatch.setattr(macro, "focus_roblox", lambda: True)
    monkeypatch.setattr(macro, "teleport_to", lambda pos: None)
    monkeypatch.setattr(macro, "hold_key", lambda key, duration: None)
    monkeypatch.setattr(macro, "wait_interruptible", lambda duration: None)
    monkeypatch.setattr(macro, "grab_screen", lambda: np.zeros((1080, 1920, 3), np.uint8))
    monkeypatch.setattr(macro, "detect_sick_dialog", dialog_up)
    monkeypatch.setattr(macro, "detect_pet_focus", lambda img: run["pet_focused"])
    monkeypatch.setattr(macro, "dismiss_stray_windows", lambda: run["events"].append("back out") or None)
    monkeypatch.setattr(macro, "hover_click", lambda x, y, duration=0: run["events"].append(("click", (x, y))))
    monkeypatch.setattr(macro, "wait_until_need_gone", lambda name, max_wait=60.0, **kw: run["waited"].append(name))
    monkeypatch.setattr(macro, "respawn_character", lambda: run.update(respawns=run["respawns"] + 1))
    return run


def test_sick_clicks_yes_once_the_dialog_is_up(macro, sick_run):
    assert macro.TeleportWalkNeedHandler("sick").handle() is True
    assert sick_run["events"] == ["look", ("click", sick_run["final"])]        # no prompt click needed
    assert sick_run["waited"] == ["sick"] and sick_run["respawns"] == 0


def test_sick_backs_out_of_the_pet_view_and_clicks_the_prompt_when_e_brings_up_no_dialog(macro, sick_run):
    sick_run["dialog"] = [False, True]                                   # nothing after e, the dialog after the click
    sick_run["pet_focused"] = True
    assert macro.TeleportWalkNeedHandler("sick").handle() is True
    assert sick_run["events"] == ["look", "back out", ("click", sick_run["prompt"]), "look",
                                  ("click", sick_run["final"])]
    assert sick_run["waited"] == ["sick"] and sick_run["respawns"] == 0
    log = open(macro.cfg.RUN_LOG_PATH).read()
    assert "sick: no dialog after e (pet focused: True) - clicking the prompt" in log and "FAILURE" not in log


def test_sick_goes_on_to_click_yes_when_the_dialog_is_not_recognised_and_the_pet_is_not_focused(macro, sick_run):
    """Run 2: 27 sick attempts in a row found no dialog with the pet NOT focused (the recognition was failing; the
    plain click had cleared sick every time in run 1). Not the pet view: click Yes as before."""
    sick_run["dialog"] = [False]
    assert macro.TeleportWalkNeedHandler("sick").handle() is True
    assert sick_run["events"] == ["look", ("click", sick_run["final"])]            # no back-out, no prompt click
    assert sick_run["waited"] == ["sick"] and sick_run["respawns"] == 0
    assert "dialog not recognised after e (pet focused: False) - clicking Yes anyway" in open(macro.cfg.RUN_LOG_PATH).read()


def test_sick_fails_at_once_when_the_prompt_click_brings_up_no_dialog_either(macro, sick_run):
    sick_run["dialog"] = [False]
    sick_run["pet_focused"] = True
    assert macro.TeleportWalkNeedHandler("sick").handle() is False
    assert sick_run["events"] == ["look", "back out", ("click", sick_run["prompt"]), "look"]   # never the Yes click
    assert sick_run["waited"] == [] and sick_run["respawns"] == 1                                    # no waiting for the icon


def test_a_need_without_a_prompt_to_click_fails_after_the_first_look(macro, sick_run, monkeypatch):
    monkeypatch.setitem(macro.cfg.TELEPORT_WALK_NEEDS["sick"], "prompt_pos", None)
    sick_run["dialog"] = [False]
    sick_run["pet_focused"] = True
    assert macro.TeleportWalkNeedHandler("sick").handle() is False
    assert sick_run["events"] == ["look"] and sick_run["respawns"] == 1


def test_a_failed_sick_is_logged_as_a_failure_by_process_needs(macro, sick_run, monkeypatch):
    import numpy as np
    sick_run["dialog"] = [False]
    sick_run["pet_focused"] = True
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
    assert white == ["Screenshot (303).png"]


def test_a_failed_minigame_does_not_count_as_progress_but_a_won_one_does(macro, monkeypatch):
    stalled = time.time() - 100
    monkeypatch.setattr(macro, "release_all_inputs", lambda: None)
    monkeypatch.setattr(macro, "accept_minigame_popup", lambda: True)
    monkeypatch.setattr(macro, "hover_click", lambda *a, **k: None)
    monkeypatch.setattr(macro, "respawn_character", lambda: None)
    monkeypatch.setattr(macro, "click_nice_until_gone", lambda: True)
    monkeypatch.setattr(macro, "log_failure", lambda reason, screenshot=True: None)
    monkeypatch.setattr(macro, "read_candy_corn_settled", lambda: None)                  # (no screen to read the total off)
    for won in (False, True):
        macro.state.last_progress = stalled
        monkeypatch.setitem(macro.MINIGAME_PLAYERS, "ghost_gallery", lambda deadline, watch, won=won: won)
        assert macro.play_minigame("ghost_gallery") is won
        assert (macro.state.last_progress == stalled) is (not won)
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


def test_a_button_the_game_refuses_is_clicked_again_until_it_takes(macro, world, monkeypatch):
    waited, refusals = [], [2]
    monkeypatch.setattr(macro, "focus_roblox", lambda: True)
    monkeypatch.setattr(macro, "grab_screen", lambda: None)
    monkeypatch.setattr(macro, "only_pets_message_visible", lambda img: refusals[0] > 0 and (refusals.__setitem__(0, refusals[0] - 1) or True))
    monkeypatch.setattr(macro, "wait_until_need_gone", lambda name, **kw: waited.append(name))
    macro.state.button_positions["potty"] = (100, 200)
    assert macro.click_need_button("potty") is True                                    # refused twice, then taken
    assert macro.clicks.count((100, 200)) == 3 and waited == ["potty"]
    del macro.clicks[:]
    refusals[0] = 10 ** 6                                                              # always refused: given up, not waited for
    waited.clear()
    assert macro.click_need_button("potty") is False
    assert macro.clicks.count((100, 200)) == macro.cfg.BUTTON_CLICK_TRIES and waited == []
    del macro.clicks[:]
    refusals[0] = 0                                                                    # not refused: clicked once
    assert macro.click_need_button("potty") is True and macro.clicks.count((100, 200)) == 1


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
    for name in ("rejoin_game", "close_backpack_if_stuck", "dismiss_stray_windows", "close_minigame_win_screen"):
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
    for name, value in (("DEBUG_DIR", old), ("LOGS_DIR", old / "logs"), ("IMAGES_DIR", old / "images"), ("RUNS_DIR", old / "runs")):
        monkeypatch.setattr(cfg, name, str(value))
    macro.migrate_debug_layout()
    assert sorted(os.listdir(old / "runs")) == ["run1.png", "run1.txt", "run2.png"]
    assert not (old / "logs" / "runs").exists() and not (old / "images" / "reports").exists() and not (old / "reports").exists()
