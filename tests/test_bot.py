"""The Discord bot's logic (bot/bot_lib.py): the config, who is obeyed, and the messages made from what the macro writes. Nothing here needs
Discord; the macro's own writers (write_status(), record_minigame_result()) make the files the bot reads, so a change in what is written breaks
here and not at the PC."""
import json
import os
import pathlib
import subprocess
import types
from datetime import datetime, timedelta

import pytest

from bot import bot_lib

NOW = datetime(2026, 10, 15, 12, 0, 0)


def write_config(tmp_path, **values):
    data = {"token": "t", "owner_id": 111, "channel_id": 222, **values}
    path = tmp_path / "bot_config.json"
    path.write_text(json.dumps({key: value for key, value in data.items() if value is not None}))
    return str(path)


# --- the config ---

def test_a_config_is_read_with_defaults_for_what_is_left_out(tmp_path):
    config = bot_lib.load_config(write_config(tmp_path))
    assert (config.token, config.owner_id, config.channel_id, config.guild_id) == ("t", 111, 222, None)
    assert config.stale_status_minutes > 0 and config.log_lines_max >= 1
    assert bot_lib.load_config(write_config(tmp_path, guild_id=333, stale_status_minutes=2.5, log_lines_max=10)).guild_id == 333


def test_the_docs_list_every_config_key():
    """There is no example file: docs/ARCHITECTURE.md is where the keys of bot_config.json are written down."""
    docs = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "ARCHITECTURE.md"), encoding="utf-8").read()
    section = docs[docs.index("## Discord bot"):]
    assert [key for key in (*bot_lib.REQUIRED, *bot_lib.DEFAULTS) if f"`{key}`" not in section] == []


@pytest.mark.parametrize("change, message", [
    ({"token": None}, "token"), ({"owner_id": None}, "owner_id"), ({"channel_id": ""}, "channel_id"),
    ({"owner_id": "111"}, "owner_id must be a number"), ({"channel_id": True}, "channel_id must be a number"),
    ({"guild_id": "1"}, "guild_id"), ({"stale_status_minutes": 0}, "stale_status_minutes"), ({"log_lines_max": 0}, "log_lines_max"),
])
def test_a_config_that_cannot_be_used_says_what_is_wrong(tmp_path, change, message):
    with pytest.raises(bot_lib.ConfigError, match=message):
        bot_lib.load_config(write_config(tmp_path, **change))


def test_a_missing_or_broken_config_file_says_so(tmp_path):
    with pytest.raises(bot_lib.ConfigError, match="not found"):
        bot_lib.load_config(str(tmp_path / "nothing.json"))
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    with pytest.raises(bot_lib.ConfigError, match="can't be read"):
        bot_lib.load_config(str(broken))
    broken.write_text("[1, 2]")
    with pytest.raises(bot_lib.ConfigError, match="JSON object"):
        bot_lib.load_config(str(broken))


def test_only_the_owner_in_the_one_channel_is_obeyed(tmp_path):
    config = bot_lib.load_config(write_config(tmp_path))
    assert bot_lib.is_allowed(config, 111, 222)
    assert not bot_lib.is_allowed(config, 111, 999) and not bot_lib.is_allowed(config, 999, 222) and not bot_lib.is_allowed(config, 0, 0)


# --- /status ---

def status_of(macro, **changes):
    macro.run_stats.run, macro.run_stats.started, macro.run_stats.cycles = 4, "2026-10-15 06:52:20", 120
    macro.run_stats.resolved = {"hungry": 20, "sick": 3}
    macro.run_stats.failed = {"school": 1}
    macro.run_stats.failures = 5
    macro.run_stats.last_failure = {"time": "2026-10-15 07:03:53", "reason": "Hauntlet 2: no doors found in room 4", "screenshot": None}
    macro.run_stats.last_resolved = {"need": "sleepy", "time": "2026-10-15 07:07:33"}
    macro.state.disabled_needs = {"pizza"}
    macro.write_status()
    status = bot_lib.read_json(bot_lib.status_path())
    assert status is not None
    return {**status, **changes}


def test_the_status_message_is_made_from_what_the_macro_writes(macro):
    status = status_of(macro, updated=(NOW - timedelta(seconds=12)).strftime(bot_lib.TIME_FORMAT))
    text = bot_lib.status_text(status, NOW, 5, True)
    assert "running - updated 12 s ago" in text and "main.py is running" in text
    assert "Run 4, started 2026-10-15 06:52:20, 120 cycles" in text
    assert "Resolved: 23 (hungry 20, sick 3)" in text and "Failed: 1 (school 1), 5 failures" in text
    assert "Disabled as stuck: pizza" in text and "Last resolved: sleepy at 2026-10-15 07:07:33" in text
    assert "Last failure: 2026-10-15 07:03:53 - Hauntlet 2: no doors found in room 4" in text


def test_a_status_that_stopped_updating_is_flagged_and_the_process_is_reported_when_known(macro):
    status = status_of(macro, updated=(NOW - timedelta(minutes=14)).strftime(bot_lib.TIME_FORMAT))
    stale = bot_lib.status_text(status, NOW, 5, False)
    assert "NOT UPDATING - last update 14 min ago" in stale and "main.py is NOT running" in stale
    assert "Process:" not in bot_lib.status_text(status, NOW, 5, None)                        # not known: not said
    fresh = {**status, "updated": (NOW - timedelta(minutes=4)).strftime(bot_lib.TIME_FORMAT)}
    assert "NOT UPDATING" not in bot_lib.status_text(fresh, NOW, 5)
    assert "NOT UPDATING" in bot_lib.status_text(fresh, NOW, 3)                               # (the limit is the config's)


def test_no_status_or_a_status_without_a_time_is_said_not_crashed_on(macro):
    assert "No status yet" in bot_lib.status_text(None, NOW, 5)
    assert bot_lib.read_json(bot_lib.status_path()) is None                                   # (nothing written yet)
    assert "no update time" in bot_lib.status_text({"resolved": {}}, NOW, 5)
    assert bot_lib.status_text({"updated": "garbage"}, NOW, 5)


def test_counts_and_ages_are_written_short():
    assert bot_lib.counts_text({}) == "0" and bot_lib.counts_text({"b": 2, "a": 2, "c": 5}) == "9 (c 5, a 2, b 2)"
    assert bot_lib.age_text(3) == "3 s" and bot_lib.age_text(120) == "2 min" and bot_lib.age_text(7500) == "2 h 05 min" and bot_lib.age_text(-5) == "0 s"


# --- /results ---

def test_results_are_read_skipping_what_is_not_a_result(macro, tmp_path):
    path = tmp_path / "results.jsonl"
    path.write_text('{"minigame": "ghost_gallery", "earned": 3000}\nnot json\n[1]\n{"no": "minigame"}\n{"minigame": "hauntlet_2", "earned": null}\n')
    assert [entry["minigame"] for entry in bot_lib.read_results(str(path))] == ["ghost_gallery", "hauntlet_2"]
    assert bot_lib.read_results(str(tmp_path / "missing.jsonl")) == []


def test_the_results_message_has_best_worst_average_unread_room_and_latest_per_minigame(macro):
    runs = [{"minigame": "ghost_gallery", "earned": 3000, "time": "t1"}, {"minigame": "ghost_gallery", "earned": 4000, "time": "t2"},
            {"minigame": "ghost_gallery", "earned": None, "time": "t3"},
            {"minigame": "hauntlet_2", "earned": 2520, "room": 6, "time": "t4"}, {"minigame": "hauntlet_2", "earned": 5460, "room": 13, "time": "t5"}]
    lines = bot_lib.results_text(runs).splitlines()
    assert lines[0] == "Ghost Gallery: 3 runs, best 4000, worst 3000, average 3500 candy corn (1 not read); latest not read at t3"
    assert lines[1] == "Hauntlet 2: 2 runs, best 5460, worst 2520, average 3990 candy corn; deepest room 13; latest 5460 at t5"
    assert bot_lib.results_text([]) == "No minigame results yet."
    assert "not read in any" in bot_lib.results_text([{"minigame": "hauntlet_2", "earned": None, "time": "t"}])


def test_what_the_macro_records_is_what_the_bot_reads(macro):
    macro.state.run_number = 5
    macro.record_minigame_result({"minigame": "ghost_gallery", "earned": 3102, "time": "2026-10-15 16:35:50"})
    macro.record_minigame_result({"minigame": "hauntlet_2", "earned": 3360, "room": 8, "time": "2026-10-15 16:25:10"})
    text = bot_lib.results_text(bot_lib.read_results(bot_lib.results_path()))
    assert "Ghost Gallery: 1 runs, best 3102" in text and "Hauntlet 2: 1 runs, best 3360, worst 3360, average 3360 candy corn; deepest room 8" in text


# --- /log, /report, /screenshot ---

def test_the_last_lines_of_a_log_are_read_from_its_end(tmp_path):
    path = tmp_path / "log.txt"
    path.write_text("".join(f"line {i}\n" for i in range(5)))
    assert bot_lib.tail_lines(str(path), 2) == "line 3\nline 4" and bot_lib.tail_lines(str(path), 50) == "\n".join(f"line {i}" for i in range(5))
    path.write_text("".join(f"line number {i}\n" for i in range(5000)))
    text = bot_lib.tail_lines(str(path), 3)
    assert text == "line number 4997\nline number 4998\nline number 4999"
    assert bot_lib.tail_lines(str(path), 10 ** 6, limit=200).startswith("line number 49")          # a window of 200 bytes: the line it cuts in half is left out
    assert bot_lib.tail_lines(str(tmp_path / "missing.txt"), 3) is None


def test_a_log_tail_that_is_too_long_for_a_message_is_not_put_in_one():
    assert bot_lib.fits_a_message("x" * 1900) and not bot_lib.fits_a_message("x" * (bot_lib.DISCORD_MESSAGE_LIMIT - 7))


def test_the_newest_run_chart_is_the_one_last_written(macro, tmp_path):
    assert bot_lib.newest_report(str(tmp_path)) is None
    for name, at in (("run3.png", 1000), ("run10.png", 3000), ("run4.png", 2000)):
        (tmp_path / name).write_bytes(b"x")
        os.utime(tmp_path / name, (at, at))
    (tmp_path / "run11.txt").write_text("a log, not a chart")
    assert os.path.basename(bot_lib.newest_report(str(tmp_path)) or "") == "run10.png"


def test_a_blank_grab_is_told_from_a_real_one():
    assert bot_lib.looks_blank(b"") and bot_lib.looks_blank(bytes(3 * 1000)) and bot_lib.looks_blank(bytes([200]) * 3000)
    assert not bot_lib.looks_blank(bytes(range(256)) * 100)


def test_the_macro_is_told_from_the_bot_in_a_process_list():
    assert bot_lib.main_py_in(["C:\\Python\\python.exe C:\\AdoptMeMacro\\main.py"]) and bot_lib.main_py_in(["python", "python main.py --loop"])
    assert not bot_lib.main_py_in(["python bot/discord_bot.py main.py-config"]) and not bot_lib.main_py_in([])


# --- alerts ---

REAL_FAILURE = "[2026-10-16 12:37:30] [RUN 6] FAILURE: sleepy handler could not resolve it (screenshot: failures/20261016_123729_run6_sleepy_handler_could_not_resolve_it/screen.png)"


def test_a_failure_line_is_read_and_other_lines_are_not():
    failure = bot_lib.parse_failure(REAL_FAILURE)
    assert (failure.time, failure.run, failure.reason) == ("2026-10-16 12:37:30", 6, "sleepy handler could not resolve it")
    assert failure.screenshot == "failures/20261016_123729_run6_sleepy_handler_could_not_resolve_it/screen.png"
    bare = bot_lib.parse_failure("[2026-10-16 11:25:15] [RUN 6] FAILURE: disconnected from the game - rejoining")
    assert bare.reason == "disconnected from the game - rejoining" and bare.screenshot is None
    assert bot_lib.parse_failure("[2026-10-16 11:25:15] [RUN None] FAILURE: x").run is None
    assert bot_lib.parse_failure("[2026-10-16 12:37:27] [RUN 6] detected: sleepy") is None
    assert bot_lib.parse_failure("[2026-10-16 12:37:27] [RUN 6] note: a FAILURE: in the middle") is None


def test_a_failures_screenshot_is_only_taken_from_inside_the_debug_folder(macro_paths, tmp_path):
    shot = tmp_path / "debug" / "failures" / "f1" / "screen.png"
    shot.parent.mkdir(parents=True)
    shot.write_bytes(b"png")
    assert bot_lib.failure_screenshot_path(bot_lib.Failure("t", 1, "r", "failures/f1/screen.png")) == str(shot)
    (tmp_path / "secret.txt").write_text("x")
    assert bot_lib.failure_screenshot_path(bot_lib.Failure("t", 1, "r", "../secret.txt")) is None          # a line is only text
    assert bot_lib.failure_screenshot_path(bot_lib.Failure("t", 1, "r", "failures/gone/screen.png")) is None
    assert bot_lib.failure_screenshot_path(bot_lib.Failure("t", 1, "r", None)) is None


@pytest.fixture
def macro_paths(monkeypatch, tmp_path):
    (tmp_path / "debug").mkdir()
    monkeypatch.setattr(bot_lib.cfg, "DEBUG_DIR", str(tmp_path / "debug"))


def test_new_lines_are_read_from_where_the_last_look_ended(tmp_path):
    log = tmp_path / "run_log.txt"
    log.write_text("one\ntwo\n")
    lines, offset = bot_lib.read_new_lines(str(log), 0)
    assert lines == ["one", "two"]
    with open(log, "a") as f:
        f.write("three\npart")
    lines, offset = bot_lib.read_new_lines(str(log), offset)
    assert lines == ["three"]                                                  # (a line still being written waits)
    with open(log, "a") as f:
        f.write("ial\n")
    assert bot_lib.read_new_lines(str(log), offset)[0] == ["partial"]
    assert bot_lib.read_new_lines(str(log), bot_lib.file_end(str(log))) == ([], bot_lib.file_end(str(log)))


def test_a_log_that_was_deleted_or_started_again_is_read_from_its_start(tmp_path):
    log = tmp_path / "run_log.txt"
    log.write_text("a long first line of the old log\n")
    offset = bot_lib.file_end(str(log))
    log.write_text("new\n")                                                   # (Refresh debug, then the next run)
    assert bot_lib.read_new_lines(str(log), offset)[0] == ["new"]
    log.unlink()
    assert bot_lib.read_new_lines(str(log), offset) == ([], 0)
    assert bot_lib.file_end(str(log)) == 0


def test_failures_are_posted_one_a_minute_and_the_rest_counted():
    limiter = bot_lib.RateLimiter(60)
    assert limiter.check(1000) == (True, 0)
    assert [limiter.check(1000 + i) for i in (5, 20)] == [(False, 0), (False, 0)]
    assert limiter.check(1061) == (True, 2)                                    # "and 2 more"
    assert limiter.check(1062) == (False, 0) and limiter.check(1130) == (True, 1)
    failure = bot_lib.Failure("2026-10-16 12:37:30", 6, "sleepy handler could not resolve it", None)
    assert "run 6" in bot_lib.failure_text(failure) and "more" not in bot_lib.failure_text(failure)
    assert "1 more failure since" in bot_lib.failure_text(failure, 1) and "3 more failures since" in bot_lib.failure_text(failure, 3)


def test_the_loop_is_idle_only_when_the_status_says_so():
    assert bot_lib.loop_idle({"stopped": "2026-10-17 12:00:00"}) and not bot_lib.loop_idle({"stopped": None}) and not bot_lib.loop_idle(None)


def test_the_process_is_not_looked_for_while_an_update_has_it_stopped():
    assert bot_lib.crash_check_due("running", updating=False)
    assert not bot_lib.crash_check_due("running", updating=True)                 # (/update stops the macro, moves the code and starts it again)
    assert not bot_lib.crash_check_due("stopped", updating=False) and not bot_lib.crash_check_due(None, updating=False)


def test_a_macro_stopped_on_purpose_is_not_reported_as_not_updating():
    old = {"updated": (NOW - timedelta(minutes=30)).strftime(bot_lib.TIME_FORMAT), "stopped": "2026-10-17 12:00:00"}
    assert bot_lib.stale_alert(False, old, NOW, 5) == (False, None)
    assert bot_lib.stale_alert(True, old, NOW, 5) == (False, None)             # (stopped while it was flagged: no "updating again" either)
    text = bot_lib.status_text(old, NOW, 5, True)
    assert "stopped at 2026-10-17 12:00:00" in text and "NOT UPDATING" not in text


def test_a_stale_status_is_reported_once_and_so_is_its_recovery():
    old = {"updated": (NOW - timedelta(minutes=9)).strftime(bot_lib.TIME_FORMAT)}
    fresh = {"updated": (NOW - timedelta(seconds=20)).strftime(bot_lib.TIME_FORMAT)}
    stale, text = bot_lib.stale_alert(False, old, NOW, 5)
    assert stale and "not updating" in text and old["updated"] in text
    assert bot_lib.stale_alert(True, old, NOW, 5) == (True, None)             # (said once)
    stale, text = bot_lib.stale_alert(True, fresh, NOW, 5)
    assert not stale and "updating again" in text
    assert bot_lib.stale_alert(False, fresh, NOW, 5) == (False, None)
    for nothing in (None, {}, {"updated": "yesterday"}):                      # (no status, or no time in it: nothing to say, the state stays)
        assert bot_lib.stale_alert(True, nothing, NOW, 5) == (True, None)


def test_a_heartbeat_is_due_when_switched_on_and_the_time_has_passed():
    assert not bot_lib.heartbeat_due(None, 0, 1000)
    assert bot_lib.heartbeat_due(None, 2, 1000)
    assert not bot_lib.heartbeat_due(1000, 2, 1000 + 7199) and bot_lib.heartbeat_due(1000, 2, 1000 + 7200)


def test_what_the_bot_has_dealt_with_is_kept_between_starts_and_a_bad_file_means_defaults(tmp_path):
    path = str(tmp_path / "bot_state.json")
    assert bot_lib.load_state(path) == bot_lib.BotState() and bot_lib.BotState().log_offset is None
    state = bot_lib.BotState(log_offset=1234, charts=["run1.png"], stale=True, last_heartbeat=55.5)
    bot_lib.save_state(path, state)
    assert bot_lib.load_state(path) == state and not os.path.exists(path + ".tmp")
    (tmp_path / "bot_state.json").write_text(json.dumps({"log_offset": "x", "charts": "run1.png", "stale": 1}))
    assert bot_lib.load_state(path) == bot_lib.BotState()
    (tmp_path / "bot_state.json").write_text("{broken")
    assert bot_lib.load_state(path) == bot_lib.BotState()


# --- captures, and /pull into OneDrive ---

def make_capture(root, name, outcome="finished (the game over screen)", zips=("capture_part01.zip",), size=1048576):
    folder = root / name
    (folder / "zips").mkdir(parents=True)
    (folder / "frame_000001_0.5.png").write_bytes(b"f" * size)                         # (the frames: the same again as the zips)
    if outcome is not None:
        (folder / "info.json").write_text(json.dumps({"outcome": outcome}))
    (folder / "events.jsonl").write_text("{}\n")
    for z in zips:
        (folder / "zips" / z).write_bytes(b"x" * size)
    return folder


def test_the_captures_message_lists_the_newest_first_by_the_time_in_the_name(tmp_path):
    make_capture(tmp_path, "hauntlet_2_20261016_100000_9330cc_room23", zips=("a_part01.zip", "a_part02.zip"))
    make_capture(tmp_path, "ghost_gallery_20261016_110000", outcome=None)
    make_capture(tmp_path, "notes_20261016_150000")                                             # (not a capture's name)
    (tmp_path / "hauntlet_2_20261016_090000").write_text("a file, not a folder")
    first, second = bot_lib.captures_text(str(tmp_path)).splitlines()
    assert first == "ghost_gallery_20261016_110000: not finished, 1 zips, 1 MB"
    assert second == "hauntlet_2_20261016_100000_9330cc_room23: finished (the game over screen), 2 zips, 2 MB"
    assert bot_lib.captures_text(str(tmp_path / "none")) == "No minigame captures on this PC."


@pytest.fixture
def debug_folders(tmp_path, monkeypatch):
    """The folders /pull takes from, in tmp_path, with a capture, failures, logs and a game config (which must never be reachable)."""
    root = tmp_path / "debug"
    for name in ("failures", "logs", "runs", "minigame_captures", "captures", "recordings"):
        (root / name).mkdir(parents=True)
    monkeypatch.setattr(bot_lib.cfg, "PULL_SOURCES", {name: str(root / name) for name in ("failures", "logs", "runs", "minigame_captures", "captures", "recordings")})
    (root / ".config").write_text('{"private_server_link": "secret"}')
    return root


def touch(path, age, text="x"):
    import time
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    os.utime(path, (time.time() - age, time.time() - age))


def test_what_can_be_pulled_is_what_is_in_the_folder_newest_first_and_nothing_else(debug_folders):
    touch(debug_folders / "failures" / "f_old" / "screen.png", 300)
    touch(debug_folders / "failures" / "f_new" / "screen.png", 10)
    os.utime(debug_folders / "failures" / "f_old", (1000, 1000)), os.utime(debug_folders / "failures" / "f_new", (2000, 2000))
    touch(debug_folders / "failures" / ".hidden", 5)
    assert bot_lib.pull_entries("failures") == ["f_new", "f_old"]
    assert bot_lib.pull_entries("nothing") == [] and bot_lib.pull_entries("logs") == []
    assert bot_lib.resolve_pull("failures", None) == (str(debug_folders / "failures" / "f_new"), "f_new")             # no item: the newest
    assert bot_lib.resolve_pull("failures", "f_old")[1] == "f_old"
    assert bot_lib.resolve_pull("failures", bot_lib.PULL_ALL) == (str(debug_folders / "failures"), "failures")
    for source, item in (("failures", "../.config"), ("failures", ".."), ("failures", "f_new/screen.png"), ("failures", "/etc/passwd"), ("failures", "C:\\x"),
                         ("failures", ".hidden"), ("failures", "nope"), ("logs", None), ("debug", None), (".config", None), ("../x", None)):
        with pytest.raises(ValueError):
            bot_lib.resolve_pull(source, item)


def test_a_capture_is_pulled_as_its_zips_alone_not_its_frames_and_other_things_as_all_their_files(debug_folders):
    make_capture(debug_folders / "minigame_captures", "hauntlet_2_20261016_100000_9330cc_room23", zips=("p1.zip", "p2.zip"))
    path, name = bot_lib.resolve_pull("minigame_captures", None)
    assert sorted(rel for _, rel in bot_lib.pull_files(path, name)) == sorted(os.path.join(name, r) for r in (os.path.join("zips", "p1.zip"), os.path.join("zips", "p2.zip")))
    make_capture(debug_folders / "minigame_captures", "ghost_gallery_20261016_110000")
    path, name = bot_lib.resolve_pull("minigame_captures", bot_lib.PULL_ALL)                  # (everything: still only the zips, of every capture)
    assert {os.path.splitext(rel)[1] for _, rel in bot_lib.pull_files(path, name)} == {".zip"}
    touch(debug_folders / "failures" / "f1" / "screen.png", 1)
    touch(debug_folders / "failures" / "f1" / "sub" / "info.txt", 1)
    path, name = bot_lib.resolve_pull("failures", "f1")
    assert sorted(rel for _, rel in bot_lib.pull_files(path, name)) == sorted([os.path.join("f1", "screen.png"), os.path.join("f1", "sub", "info.txt")])
    touch(debug_folders / "logs" / "run_log.txt", 1)
    path, name = bot_lib.resolve_pull("logs", "run_log.txt")
    assert bot_lib.pull_files(path, name) == [(path, "run_log.txt")]


def test_pulled_files_are_copied_into_a_folder_of_the_source_and_a_too_big_pull_copies_nothing(debug_folders, tmp_path):
    onedrive = tmp_path / "onedrive"
    onedrive.mkdir()
    make_capture(debug_folders / "minigame_captures", "hauntlet_2_20261016_100000", zips=("p1.zip", "p2.zip"))
    path, name = bot_lib.resolve_pull("minigame_captures", None)
    files = bot_lib.pull_files(path, name)
    assert bot_lib.copy_pull(files, str(onedrive), "minigame_captures", 100) == pytest.approx(2, abs=0.01)
    assert sorted(os.listdir(onedrive / "minigame_captures" / name / "zips")) == ["p1.zip", "p2.zip"] and (debug_folders / "minigame_captures" / name / "zips" / "p1.zip").exists()
    bot_lib.copy_pull(files, str(onedrive), "minigame_captures", 100)                            # (again: replaces, no error)
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(ValueError, match="more than the 1 MB"):
        bot_lib.copy_pull(files, str(other), "minigame_captures", 1)
    assert os.listdir(other) == []
    with pytest.raises(ValueError, match="no files"):
        bot_lib.copy_pull([], str(other), "logs", 100)


def test_the_onedrive_folder_and_heartbeat_keys_are_read_and_checked(tmp_path):
    config = bot_lib.load_config(write_config(tmp_path, onedrive_dir=" C:/OneDrive/AdoptMeMacro/captures ", heartbeat_hours=6))
    assert (config.onedrive_dir, config.heartbeat_hours) == ("C:/OneDrive/AdoptMeMacro/captures", 6.0)
    config = bot_lib.load_config(write_config(tmp_path))
    assert config.onedrive_dir == "" and config.heartbeat_hours == 0
    for change in ({"onedrive_dir": 5}, {"heartbeat_hours": -1}, {"heartbeat_hours": "2"}, {"heartbeat_hours": True}):
        with pytest.raises(bot_lib.ConfigError):
            bot_lib.load_config(write_config(tmp_path, **change))


# --- control: start, stop, restart the macro ---

def test_a_command_is_left_for_the_macro_all_at_once_and_only_a_known_one(tmp_path):
    path = str(tmp_path / "commands.json")
    bot_lib.write_command(path, "exit", 1234.5)
    assert json.load(open(path)) == {"command": "exit", "time": 1234.5} and not os.path.exists(path + ".tmp")
    for command in ("start", "stop"):
        bot_lib.write_command(path, command, 1.0)
    with pytest.raises(ValueError):
        bot_lib.write_command(path, "git pull", 1.0)
    assert json.load(open(path))["command"] == "stop"                         # (the refused one wrote nothing)


def test_the_macro_is_found_among_the_processes_but_not_the_bot_or_other_scripts():
    processes = [(10, "python bot/discord_bot.py bot_config.json"), (11, "python C:/repo/main.py --loop"), (12, "python other.py"),
                 (13, "C:/Python/python.exe main.py"), (14, "")]
    assert bot_lib.macro_pids(processes) == [11, 13]
    assert bot_lib.parse_process_lines("11 python main.py --loop\n\nnot a pid line\n12 \n 13 python x.py") == [(11, "python main.py --loop"), (12, ""), (13, "python x.py")]


def test_the_bot_restarts_the_macro_by_itself_only_so_often():
    allowed, history = bot_lib.restart_allowed([], 10000, 2)
    assert allowed and history == [10000]
    allowed, history = bot_lib.restart_allowed(history, 10100, 2)
    assert allowed and history == [10000, 10100]
    assert bot_lib.restart_allowed(history, 10200, 2) == (False, [10000, 10100])
    assert bot_lib.restart_allowed(history, 10000 + 3601, 2) == (True, [10100, 13601])      # (the first is more than an hour old)


@pytest.mark.parametrize("desired, running, reported, expected", [
    ("running", False, False, (True, "alert")),          # it should be there and is gone
    ("running", False, True, (True, None)),              # ...said once
    ("running", True, True, (False, "recovered")),
    ("running", True, False, (False, None)),
    ("running", None, False, (False, None)),             # (the process check can't tell)
    ("running", None, True, (True, None)),
    ("stopped", False, False, (False, None)),            # stopped on purpose is not a crash
    (None, False, False, (False, None)),                 # (never started by the bot: not its business)
    ("stopped", False, True, (False, None)),
])
def test_a_macro_that_is_gone_is_a_crash_only_if_the_bot_started_it_and_is_said_once(desired, running, reported, expected):
    assert bot_lib.crash_check(desired, running, reported) == expected


def test_a_started_macro_shows_when_status_is_written_at_or_after_the_start():
    since = datetime(2026, 10, 16, 12, 0, 5, 700000)
    assert bot_lib.started_since({"updated": "2026-10-16 12:00:05"}, since) and bot_lib.started_since({"updated": "2026-10-16 12:01:00"}, since)
    assert not bot_lib.started_since({"updated": "2026-10-16 12:00:04"}, since)       # (what the last run left)
    assert not bot_lib.started_since(None, since) and not bot_lib.started_since({"updated": "soon"}, since) and not bot_lib.started_since({}, since)


def test_the_control_config_keys_and_state_are_read_and_checked(tmp_path):
    config = bot_lib.load_config(write_config(tmp_path))
    assert (config.repo_dir, config.python, config.auto_restart) == ("", "", False) and config.stop_timeout_seconds > 0 and config.auto_restart_max_per_hour >= 1
    config = bot_lib.load_config(write_config(tmp_path, repo_dir=" C:/repo ", python="C:/py/python.exe", stop_timeout_seconds=10, auto_restart=True, auto_restart_max_per_hour=2))
    assert (config.repo_dir, config.python, config.stop_timeout_seconds, config.auto_restart, config.auto_restart_max_per_hour) == ("C:/repo", "C:/py/python.exe", 10.0, True, 2)
    for change in ({"repo_dir": 1}, {"python": []}, {"stop_timeout_seconds": 0}, {"auto_restart": "yes"}, {"auto_restart_max_per_hour": 0}):
        with pytest.raises(bot_lib.ConfigError):
            bot_lib.load_config(write_config(tmp_path, **change))
    path = str(tmp_path / "bot_state.json")
    state = bot_lib.BotState(desired_state="running", crash_reported=True, restarts=[1.5, 2.0])
    bot_lib.save_state(path, state)
    assert bot_lib.load_state(path) == state
    (tmp_path / "bot_state.json").write_text(json.dumps({"desired_state": "paused", "restarts": [1, "x", True], "crash_reported": "no"}))
    assert bot_lib.load_state(path) == bot_lib.BotState(restarts=[1])


# --- changing the macro's switches (/need, /option, /options) ---

def test_a_set_command_carries_its_switch_and_only_for_one_that_exists(tmp_path):
    path = str(tmp_path / "commands.json")
    bot_lib.write_command(path, "set", 5.0, **bot_lib.switch_command("need", "sick", False))
    assert json.load(open(path)) == {"command": "set", "time": 5.0, "kind": "need", "name": "sick", "value": False}
    assert bot_lib.switch_command("option", "play_stitches_spotlight", True)["name"] == "play_stitches_spotlight"
    assert bot_lib.switch_command("option", "show_debug_lines", True)["value"] is True
    for kind, name in (("need", "play_ghost_gallery"), ("option", "sick"), ("secret", "sick"), ("option", "private_server_link")):
        with pytest.raises(ValueError):
            bot_lib.switch_command(kind, name, True)


def test_the_switches_are_read_from_the_game_config_with_the_defaults_for_what_is_missing():
    states = bot_lib.switch_states(None)
    assert states["option:side_quest"] is True and states["option:play_hauntlet_2"] is False and states["need:hungry"] is True
    assert set(states) == {f"need:{n}" for n in bot_lib.cfg.NEED_NAMES} | {f"option:{n}" for n in bot_lib.cfg.OPTION_SWITCHES}
    states = bot_lib.switch_states({"needs_enabled": {"hungry": False, "bored": True}, "side_quest_enabled": False, "ghost_gallery_enabled": True})
    assert states["need:hungry"] is False and states["need:bored"] is True and states["need:sick"] is True   
    assert states["option:side_quest"] is False and states["option:play_ghost_gallery"] is True
    assert bot_lib.switch_states({"needs_enabled": "x"})["need:hungry"] is True                                   # (a bad config is the defaults)
    text = bot_lib.options_text(bot_lib.switch_states({"needs_enabled": {"hungry": False}}))
    assert "side_quest on" in text and "play_stitches_spotlight off" in text and text.splitlines()[1] == "Needs off: hungry"


# --- update and rollback ---

def git_in(folder):
    """A bot_lib.GitRunner running the real git in `folder` (the tests make their own repositories)."""
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}

    def run(args):
        done = subprocess.run(["git", *args], cwd=str(folder), capture_output=True, text=True, env=env)
        return done.returncode, (done.stdout + done.stderr).strip()
    return run


def commit(run, folder, name, text, message="change"):
    (folder / name).parent.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text)
    assert run(["add", name])[0] == 0 and run(["commit", "-q", "-m", message])[0] == 0


@pytest.fixture
def repos(tmp_path):
    """An upstream repository on `main` with two commits and the PC's clone of it."""
    upstream, pc = tmp_path / "upstream", tmp_path / "pc"
    upstream.mkdir()
    up = git_in(upstream)
    assert up(["init", "-q", "-b", "main"])[0] == 0
    commit(up, upstream, "main.py", "one", "first")
    commit(up, upstream, "bot/discord_bot.py", "bot one", "second")
    assert subprocess.run(["git", "clone", "-q", str(upstream), str(pc)], capture_output=True).returncode == 0
    return types.SimpleNamespace(up=up, upstream=upstream, pc=git_in(pc), pc_dir=pc)


def test_the_update_plan_goes_ahead_only_over_a_clean_copy_and_only_forward():
    plan = bot_lib.update_plan("a" * 40, "b" * 40, [], True, False, ["bbbbbbb fix", "ccccccc more"], "origin/main")
    assert plan.kind == "update" and "aaaaaaa -> bbbbbbb" in plan.text and "bbbbbbb fix" in plan.text
    assert bot_lib.update_plan("a" * 40, "a" * 40, [], True, True, [], "origin/main").kind == "up_to_date"
    dirty = bot_lib.update_plan("a" * 40, "b" * 40, ["main.py", "x.py"], True, False, [], "origin/main")
    assert dirty.kind == "dirty" and "main.py, x.py" in dirty.text and "Nothing was changed" in dirty.text
    assert bot_lib.update_plan("a" * 40, "b" * 40, [], False, True, [], "origin/main").kind == "ahead"
    assert bot_lib.update_plan("a" * 40, "b" * 40, [], False, False, [], "origin/main").kind == "diverged"
    long_log = [f"{i:07d} commit" for i in range(20)]
    assert bot_lib.update_plan("a" * 40, "b" * 40, [], True, False, long_log, "origin/main").text.endswith("... and 5 more")
    assert bot_lib.files_text([f"f{i}" for i in range(10)]) == "f0, f1, f2, f3, f4, f5, f6, f7 and 2 more"


def test_the_rollback_plan_goes_back_only_to_a_known_commit_over_a_clean_copy():
    head, previous = "b" * 40, "a" * 40
    assert bot_lib.rollback_plan(head, previous, [], True).kind == "rollback"
    assert bot_lib.rollback_plan(head, None, [], False).kind == "none"
    assert bot_lib.rollback_plan(head, previous, [], False).kind == "missing"
    assert bot_lib.rollback_plan(head, head, [], True).kind == "same"
    assert bot_lib.rollback_plan(head, previous, ["main.py"], True).kind == "dirty"


def test_what_changed_says_whether_the_packages_and_the_bot_need_attention():
    assert bot_lib.needs_pip(["main.py", "requirements.txt"]) and not bot_lib.needs_pip(["main.py"])
    for restarts in (["bot/bot_lib.py"], ["magic_numbers.py"], ["requirements.txt"], ["main.py", "bot/discord_bot.py"]):
        assert bot_lib.needs_bot_restart(restarts)
    assert not bot_lib.needs_bot_restart(["main.py", "docs/ARCHITECTURE.md", "tests/test_bot.py", "robot/x.py"])
    assert bot_lib.dirty_files(" M main.py\nM  bot/a.py\nR  old.py -> new.py\n") == ["main.py", "bot/a.py", "new.py"]


def test_with_real_git_an_update_is_planned_and_done_only_forward_over_a_clean_copy(repos):
    plan, move, head = bot_lib.make_plan(repos.pc, "origin", "main", None, False)
    assert plan.kind == "up_to_date"
    commit(repos.up, repos.upstream, "main.py", "two", "third: the macro")
    commit(repos.up, repos.upstream, "requirements.txt", "numpy", "fourth: packages")
    plan, move, head = bot_lib.make_plan(repos.pc, "origin", "main", None, False)
    assert plan.kind == "update" and "fourth: packages" in plan.text and "third: the macro" in plan.text and move[:2] == ["merge", "--ff-only"]
    (repos.pc_dir / "main.py").write_text("edited on the PC")
    assert bot_lib.make_plan(repos.pc, "origin", "main", None, False)[0].kind == "dirty"                          # a tracked file changed here: not touched
    assert repos.pc(["checkout", "main.py"])[0] == 0
    (repos.pc_dir / "notes.txt").write_text("untracked")
    assert bot_lib.make_plan(repos.pc, "origin", "main", None, False)[0].kind == "update"                         # (an untracked file is no reason)
    assert bot_lib.git_ok(repos.pc, *move) is not None
    assert (repos.pc_dir / "main.py").read_text() == "two" and (repos.pc_dir / "notes.txt").exists()
    changed = bot_lib.git_ok(repos.pc, "diff", "--name-only", head, bot_lib.git_ok(repos.pc, "rev-parse", "HEAD").strip()).splitlines()
    assert sorted(changed) == ["main.py", "requirements.txt"] and bot_lib.needs_pip(changed) and bot_lib.needs_bot_restart(changed)     # (new packages: the bot too)
    plan, move, now = bot_lib.make_plan(repos.pc, "origin", "main", head, True)                                    # and back
    assert plan.kind == "rollback" and move == ["reset", "--hard", head]
    bot_lib.git_ok(repos.pc, *move)
    assert (repos.pc_dir / "main.py").read_text() == "one" and not (repos.pc_dir / "requirements.txt").exists() and (repos.pc_dir / "notes.txt").exists()
    assert bot_lib.make_plan(repos.pc, "origin", "main", head, True)[0].kind == "same"


def test_with_real_git_a_copy_that_is_ahead_or_has_diverged_is_not_updated(repos):
    commit(repos.pc, repos.pc_dir, "local.py", "only here", "a commit made on the PC")
    assert bot_lib.make_plan(repos.pc, "origin", "main", None, False)[0].kind == "ahead"
    commit(repos.up, repos.upstream, "main.py", "two", "upstream moved on")
    plan, move, head = bot_lib.make_plan(repos.pc, "origin", "main", None, False)
    assert plan.kind == "diverged" and "Nothing was changed" in plan.text
    assert bot_lib.make_plan(repos.pc, "origin", "main", "0" * 40, True)[0].kind == "missing"
    with pytest.raises(RuntimeError, match="fetch"):
        bot_lib.make_plan(repos.pc, "origin", "no-such-branch", None, False)


def test_the_update_source_is_a_name_git_cannot_take_for_an_option(tmp_path):
    config = bot_lib.load_config(write_config(tmp_path))
    assert (config.update_remote, config.update_branch) == ("origin", "main")
    assert bot_lib.load_config(write_config(tmp_path, update_remote="upstream", update_branch="release/1.2")).update_branch == "release/1.2"
    for change in ({"update_branch": "--upload-pack=x"}, {"update_branch": "a b"}, {"update_remote": ""}, {"update_branch": "a..b"}, {"update_branch": 5}, {"update_remote": "-x"}):
        with pytest.raises(bot_lib.ConfigError):
            bot_lib.load_config(write_config(tmp_path, **change))
    path = str(tmp_path / "bot_state.json")
    bot_lib.save_state(path, bot_lib.BotState(previous_commit="abc1234def"))
    assert bot_lib.load_state(path).previous_commit == "abc1234def"
    (tmp_path / "bot_state.json").write_text(json.dumps({"previous_commit": "--hard; rm -rf"}))
    assert bot_lib.load_state(path).previous_commit is None                                                       # (a state file is not trusted with a git argument)


def test_sizes_are_said_in_megabytes_or_from_a_gigabyte_in_gigabytes_and_the_total_is_of_the_files(tmp_path):
    assert bot_lib.size_text(5) == "5 MB" and bot_lib.size_text(1023) == "1023 MB" and bot_lib.size_text(1024) == "1.0 GB" and bot_lib.size_text(10240) == "10.0 GB"
    (tmp_path / "a").write_bytes(b"x" * 1048576)
    (tmp_path / "b").write_bytes(b"x" * 3145728)
    assert bot_lib.files_size_mb([(str(tmp_path / "a"), "a"), (str(tmp_path / "b"), "b")]) == 4


def test_every_slash_command_has_a_name_and_descriptions_discord_accepts():
    """Discord refuses the whole upload (and the bot never gets its commands) for a description over 100 characters or a bad name."""
    import re
    source = (pathlib.Path(__file__).parent.parent / "bot" / "discord_bot.py").read_text(encoding="utf-8")
    commands = re.findall(r'@command\("([^"]+)", "((?:[^"\\]|\\.)*)"\)', source)
    names = {name for name, _ in commands}
    assert {"status", "screenshot", "rview", "log", "report", "results", "captures", "pull", "start", "loop", "stop", "exit", "restart", "rejoin", "setup", "refresh_debug",
            "trade_hub", "leave_trade_hub", "update", "rollback", "need", "option", "options"} <= names
    for name, description in commands:
        assert re.fullmatch(r"[a-z0-9_-]{1,32}", name) and 1 <= len(description) <= 100, name
    for described in re.findall(r'@app_commands\.describe\((.*)\)', source):
        for value in re.findall(r'="((?:[^"\\]|\\.)*)"', described):
            assert 1 <= len(value) <= 100, value
