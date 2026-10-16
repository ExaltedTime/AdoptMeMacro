"""The Discord bot's logic (bot/bot_lib.py): the config, who is obeyed, and the messages made from what the macro writes. Nothing here needs
Discord; the macro's own writers (write_status(), record_minigame_result()) make the files the bot reads, so a change in what is written breaks
here and not at the PC."""
import json
import os
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


def test_the_example_config_has_every_key_and_is_what_a_config_looks_like():
    example = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot_config.example.json")
    keys = set(json.load(open(example)))
    assert set(bot_lib.REQUIRED) | set(bot_lib.DEFAULTS) == keys


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
