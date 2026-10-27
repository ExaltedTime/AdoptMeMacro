"""The Discord bot's glue (bot/discord_bot.py) run against fakes: a channel that records what is sent, and the macro's files in a temp folder. What the real
Discord does with a message (rate limits, an expired interaction) is not here; what the bot decides to send, and when, is."""
import asyncio
import json
import pathlib
import tempfile
import time
import types
from datetime import datetime

import pytest

pytest.importorskip("discord")

from bot import bot_lib, discord_bot  # noqa: E402

REAL_POST = discord_bot.post                      # (the `watch` fixture replaces it with a recorder)


class FakeChannel:
    def __init__(self):
        self.sent = []

    async def send(self, text, **kwargs):
        self.sent.append(text)


@pytest.fixture
def watch(tmp_path, monkeypatch):
    """(run watch_once once, the channel, the state, the files' folder): the macro's paths in tmp_path, nothing to post by default."""
    cfg = bot_lib.cfg
    monkeypatch.setattr(cfg, "RUN_LOG_PATH", str(tmp_path / "run_log.txt"))
    monkeypatch.setattr(cfg, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(cfg, "STATUS_PATH", str(tmp_path / "status.json"))
    (tmp_path / "runs").mkdir()
    (tmp_path / "run_log.txt").write_text("")
    channel, posted = FakeChannel(), []

    async def post(channel, text, path=None):
        posted.append((text, path))
    monkeypatch.setattr(discord_bot, "post", post)
    monkeypatch.setattr(discord_bot, "macro_process_running", lambda: True)
    config = bot_lib.load_config(_config(tmp_path))
    state = bot_lib.BotState(log_offset=0, last_heartbeat=time.time())
    limiter = bot_lib.RateLimiter(60)

    def once():
        asyncio.run(discord_bot.watch_once(channel, config, state, limiter))
    return types.SimpleNamespace(once=once, channel=channel, posted=posted, state=state, folder=tmp_path, config=config, limiter=limiter)


def _config(tmp_path, **values):
    data = {"token": "t", "owner_id": 1, "channel_id": 2, "heartbeat_hours": 1000, **values}
    path = tmp_path / "bot_config.json"
    path.write_text(json.dumps(data))
    return str(path)


def test_a_run_log_that_cannot_be_read_is_said_once_and_again_when_it_can(watch, monkeypatch):
    real = bot_lib.read_new_lines

    def locked(path, offset):
        raise PermissionError("locked")
    monkeypatch.setattr(bot_lib, "read_new_lines", locked)
    watch.once()
    watch.once()
    assert len(watch.channel.sent) == 1 and "could not be read" in watch.channel.sent[0]
    monkeypatch.setattr(bot_lib, "read_new_lines", real)
    watch.once()
    watch.once()
    assert len(watch.channel.sent) == 2 and "can be read again" in watch.channel.sent[1]


def test_a_chart_that_is_gone_is_forgotten_so_a_new_run_with_its_number_is_posted(watch, monkeypatch):
    monkeypatch.setattr(discord_bot.cfg, "BOT_FILE_SETTLE", 0)
    chart = watch.folder / "runs" / "run1.png"
    chart.write_bytes(b"png")
    watch.once()
    assert [text for text, _ in watch.posted] == ["Run chart run1.png"] and watch.state.charts == ["run1.png"]
    watch.once()
    assert len(watch.posted) == 1                                          # (not twice)
    chart.unlink()                                                         # Refresh debug
    watch.once()
    assert watch.state.charts == []
    chart.write_bytes(b"png")
    watch.once()
    assert len(watch.posted) == 2


@pytest.mark.parametrize("desired, with_loop", [("running", True), ("open", False)])
def test_a_crashed_macro_comes_back_with_its_loop_only_if_the_loop_was_wanted(watch, monkeypatch, desired, with_loop):
    launched = []
    monkeypatch.setattr(discord_bot, "macro_process_running", lambda: False)
    monkeypatch.setattr(discord_bot, "launch_macro", lambda config, loop=True: launched.append(loop))
    watch.config = bot_lib.load_config(_config(watch.folder, auto_restart=True))
    watch.state.desired_state = desired
    asyncio.run(discord_bot.watch_once(watch.channel, watch.config, watch.state, bot_lib.RateLimiter(60)))
    assert launched == [with_loop] and "restarting" in watch.channel.sent[0]


def _http_error(cls):
    return cls(types.SimpleNamespace(status=500, reason="x"), "boom")


class FakeClient:
    def __init__(self, outcomes, cached=None):
        self.outcomes, self.cached, self.fetches = list(outcomes), cached, 0

    def get_channel(self, channel_id):
        return self.cached

    async def fetch_channel(self, channel_id):
        self.fetches += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def slept(monkeypatch):
    waits = []

    async def sleep(seconds):
        waits.append(seconds)
    monkeypatch.setattr(discord_bot.asyncio, "sleep", sleep)
    return waits


def test_the_channel_is_fetched_again_after_a_hiccup_with_a_growing_wait(slept):
    import discord
    client = FakeClient([_http_error(discord.HTTPException), OSError("net"), "the channel"])
    assert asyncio.run(discord_bot.find_channel(client, 2)) == "the channel"
    assert client.fetches == 3 and slept == [discord_bot.cfg.BOT_CHANNEL_RETRY_WAIT, discord_bot.cfg.BOT_CHANNEL_RETRY_WAIT * 2]
    assert asyncio.run(discord_bot.find_channel(FakeClient([], cached="cached"), 2)) == "cached"      # (no fetch when it is known)


def test_a_channel_that_is_forbidden_is_not_retried_and_one_that_never_comes_is_given_up(slept):
    import discord
    forbidden = FakeClient([_http_error(discord.Forbidden)])
    assert asyncio.run(discord_bot.find_channel(forbidden, 2)) is None and forbidden.fetches == 1 and slept == []
    failing = FakeClient([_http_error(discord.HTTPException)] * discord_bot.cfg.BOT_CHANNEL_TRIES)
    assert asyncio.run(discord_bot.find_channel(failing, 2)) is None
    assert failing.fetches == discord_bot.cfg.BOT_CHANNEL_TRIES and len(slept) == failing.fetches - 1


def test_a_watcher_that_ends_by_an_exception_is_logged(caplog):
    async def boom():
        raise ValueError("watcher died")

    async def run():
        task = asyncio.ensure_future(boom())
        task.add_done_callback(discord_bot.task_ended)
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
    asyncio.run(run())
    assert any("the watcher ended" in record.message for record in caplog.records)


@pytest.fixture
def commands(tmp_path, monkeypatch):
    monkeypatch.setattr(discord_bot.cfg, "COMMANDS_DIR", str(tmp_path / "commands"))
    monkeypatch.setattr(discord_bot.cfg, "BOT_COMMAND_ACK_WAIT", 0.3)
    monkeypatch.setattr(discord_bot.cfg, "BOT_COMMAND_ACK_POLL", 0.02)
    return tmp_path / "commands"


def _answer_with(folder, text):
    """Plays the macro: answers the command that appears in `folder` the way read_command() does."""
    async def play():
        for _ in range(100):
            await asyncio.sleep(0.01)
            files = list(folder.glob("*.json")) if folder.exists() else []
            if files:
                (folder / (files[0].stem + ".ack")).write_text(text)
                files[0].unlink()
                return
    return play


def test_a_command_the_macro_takes_is_quiet_and_one_it_drops_says_why(commands):
    async def both():
        results = []
        for text in ("taken", "dropped: stop is 40 s old"):
            task = asyncio.ensure_future(_answer_with(commands, text)())
            results.append(await discord_bot.send_command("stop"))
            await task
        return results
    taken, dropped = asyncio.run(both())
    assert taken == "" and "stop is 40 s old" in dropped and "nothing was done" in dropped


def test_a_command_the_macro_never_reads_is_withdrawn_so_it_does_not_run_later(commands):
    text = asyncio.run(discord_bot.send_command("rejoin"))
    assert "withdrawn" in text and list(commands.glob("*.json")) == []


def test_a_picture_that_is_gone_is_left_out_and_the_text_still_goes(tmp_path):
    channel = FakeChannel()
    asyncio.run(REAL_POST(channel, "A failure", str(tmp_path / "deleted_by_refresh.png")))
    assert len(channel.sent) == 1 and channel.sent[0].startswith("A failure") and "picture could not be sent" in channel.sent[0]


def test_a_post_that_fails_is_tried_again_at_the_next_look_and_does_not_stop_the_rest(watch, monkeypatch):
    monkeypatch.setattr(discord_bot.cfg, "BOT_FILE_SETTLE", 0)
    (watch.folder / "runs" / "run1.png").write_bytes(b"png")
    broken = {"on": True}

    async def post(channel, text, path=None):
        if broken["on"]:
            raise RuntimeError("Discord is having a bad day")
        watch.posted.append((text, path))
    monkeypatch.setattr(discord_bot, "post", post)
    (watch.folder / "status.json").write_text(json.dumps({"updated": "2000-01-01 00:00:00", "running": True}))
    watch.state.last_heartbeat = None
    watch.config = bot_lib.load_config(_config(watch.folder, heartbeat_hours=1))
    watch.once()
    assert watch.state.charts == []                                           # (not recorded as posted)
    assert any(text.startswith("Heartbeat") for text in watch.channel.sent) and watch.state.last_heartbeat is not None     # (the rest of the look went on)
    broken["on"] = False
    watch.once()
    assert watch.state.charts == ["run1.png"] and [text for text, _ in watch.posted] == ["Run chart run1.png"]


def test_a_restart_that_fails_is_not_counted_against_the_hourly_limit_and_is_said(watch, monkeypatch):
    def refused(config, loop=True):
        raise OSError("python.exe is gone")
    monkeypatch.setattr(discord_bot, "macro_process_running", lambda: False)
    monkeypatch.setattr(discord_bot, "launch_macro", refused)
    watch.config = bot_lib.load_config(_config(watch.folder, auto_restart=True))
    watch.state.desired_state = "running"
    asyncio.run(discord_bot.watch_once(watch.channel, watch.config, watch.state, bot_lib.RateLimiter(60)))
    assert watch.state.restarts == [] and any("could not be started again" in text and "python.exe is gone" in text for text in watch.channel.sent)
    launched = []
    monkeypatch.setattr(discord_bot, "launch_macro", lambda config, loop=True: launched.append(loop))
    watch.state.crash_reported = False
    asyncio.run(discord_bot.watch_once(watch.channel, watch.config, watch.state, bot_lib.RateLimiter(60)))
    assert launched == [True] and len(watch.state.restarts) == 1                         # a restart that was made is counted


def test_a_macro_that_is_being_started_is_not_taken_for_a_crash(watch, monkeypatch):
    monkeypatch.setattr(discord_bot, "macro_process_running", lambda: False)
    watch.state.desired_state = "running"
    with discord_bot.launching():
        watch.once()
    assert not any("process is gone" in text for text in watch.channel.sent)
    watch.once()                                                                          # (once it is no longer being started it is looked for)
    assert any("process is gone" in text for text in watch.channel.sent)


def test_failures_held_back_by_the_rate_limit_are_counted_in_a_post_of_their_own_when_no_later_one_comes(watch):
    watch.limiter.check(time.time())                                                      # a post just made...
    watch.limiter.check(time.time())
    watch.limiter.check(time.time())                                                      # ...and two held back
    watch.once()
    assert not any("more failure" in text for text in watch.channel.sent)                  # (the gap has not passed)
    watch.limiter.last -= watch.limiter.gap + 1
    watch.once()
    assert any(text.startswith("2 more failures") for text in watch.channel.sent)
    watch.once()
    assert sum("more failure" in text for text in watch.channel.sent) == 1                 # said once


def test_buttons_nobody_pressed_are_taken_off_the_message_at_the_timeout():
    edits = []

    async def run():
        view = discord_bot.ConfirmView(bot_lib.load_config(_config(pathlib.Path(tempfile.mkdtemp()))), 1.0)
        view.message = types.SimpleNamespace(edit=lambda **kw: _record(edits, kw))
        await view.on_timeout()
    asyncio.run(run())
    assert edits == [{"view": None}]


async def _record(edits, kw):
    edits.append(kw)


def test_a_loop_already_going_is_said_so_and_one_that_was_idle_is_asked_to_start_and_seen_to_begin(monkeypatch, tmp_path):
    monkeypatch.setattr(bot_lib.cfg, "STATUS_PATH", str(tmp_path / "status.json"))
    sent = []

    async def pids():
        return [123]

    async def send(command, **details):
        sent.append(command)
        return ""
    monkeypatch.setattr(discord_bot, "running_pids", pids)
    monkeypatch.setattr(discord_bot, "send_command", send)
    monkeypatch.setattr(discord_bot.cfg, "BOT_START_CONFIRM", 3)
    config = bot_lib.load_config(_config(tmp_path))
    status = tmp_path / "status.json"
    status.write_text(json.dumps({"updated": datetime.now().strftime(bot_lib.TIME_FORMAT)}))              # a loop that is going
    assert "running already" in asyncio.run(discord_bot.start_macro(config)) and sent == []                # nothing was asked of it
    status.write_text(json.dumps({"updated": "2000-01-01 00:00:00", "stopped": True}))                      # a window with no loop
    monkeypatch.setattr(discord_bot.asyncio, "sleep", lambda s: _later(status))

    text = asyncio.run(discord_bot.start_macro(config))
    assert sent == ["start"] and text.endswith("Its loop is running.")


async def _later(status):
    status.write_text(json.dumps({"updated": datetime.now().strftime(bot_lib.TIME_FORMAT)}))              # the loop writes the status, and no longer says it is stopped


def test_the_macro_is_launched_in_a_process_group_of_its_own_and_killed_with_its_children(tmp_path, monkeypatch):
    launched, killed = [], []
    monkeypatch.setattr(discord_bot.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, raising=False)
    monkeypatch.setattr(discord_bot.subprocess, "Popen", lambda *a, **kw: launched.append(kw) or types.SimpleNamespace(pid=7))
    monkeypatch.setattr(discord_bot.cfg, "LOGS_DIR", str(tmp_path / "logs"))
    assert discord_bot.launch_macro(bot_lib.load_config(_config(tmp_path)), False) == 7
    assert launched[0]["creationflags"] == 512                                         # (Ctrl+C in the bot's console is not sent to it)
    monkeypatch.setattr(discord_bot.os, "name", "nt")
    monkeypatch.setattr(discord_bot.subprocess, "run", lambda command, **kw: killed.append(command))
    monkeypatch.setattr(discord_bot, "release_inputs", lambda: None)
    discord_bot.kill_macro([11])
    assert killed == [["taskkill", "/F", "/T", "/PID", "11"]]


def test_the_rollback_target_is_kept_until_the_repository_has_moved(tmp_path, monkeypatch):
    moved, said = [], []
    config = bot_lib.load_config(_config(tmp_path))

    async def nobody():
        return []

    async def say(text):
        said.append(text)

    async def fail_on_merge(config, *args):
        if args[0] == "merge":
            raise RuntimeError("git merge failed")
        return "abc1234\n" if args[0] == "rev-parse" else ""
    monkeypatch.setattr(discord_bot, "running_pids", nobody)
    monkeypatch.setattr(discord_bot, "git", fail_on_merge)
    with pytest.raises(RuntimeError, match="merge failed"):
        asyncio.run(discord_bot.apply_commit(config, ["merge", "--ff-only", "x"], "0" * 40, say, True, lambda: moved.append(1)))
    assert moved == []                                                                  # the move failed: what /rollback goes back to is as it was

    async def move_works(config, *args):
        return "abc1234\n" if args[0] == "rev-parse" else ""
    monkeypatch.setattr(discord_bot, "git", move_works)
    asyncio.run(discord_bot.apply_commit(config, ["merge", "--ff-only", "x"], "0" * 40, say, True, lambda: moved.append(1)))
    assert moved == [1]


@pytest.fixture
def macro_calls(monkeypatch, tmp_path):
    """Everything restart_all() asks of the macro, recorded: what is stopped and started, and what is said."""
    calls, said = [], []

    async def stop(config):
        calls.append("stop")
        return "The macro stopped."

    async def start(config):
        calls.append("loop")
        return "Started the macro. Its loop is running."

    async def open_(config):
        calls.append("window")
        return "Started the macro (its window is up, no loop going - /loop starts the loop)."
    monkeypatch.setattr(discord_bot, "stop_macro", stop)
    monkeypatch.setattr(discord_bot, "start_macro", start)
    monkeypatch.setattr(discord_bot, "open_macro", open_)
    monkeypatch.setattr(discord_bot, "RESTART_BOT", False)

    async def say(text):
        said.append(text)
    return types.SimpleNamespace(calls=calls, said=said, say=say, config=bot_lib.load_config(_config(tmp_path)))


def run_restart(macro_calls, monkeypatch, running, loop_was_running):
    async def pids():
        return [5] if running else []
    monkeypatch.setattr(discord_bot, "running_pids", pids)
    asyncio.run(discord_bot.restart_all(macro_calls.config, macro_calls.say, loop_was_running))


def test_a_restart_stops_the_macro_starts_it_as_it_was_and_has_the_bot_start_again(macro_calls, monkeypatch):
    run_restart(macro_calls, monkeypatch, running=True, loop_was_running=True)
    assert macro_calls.calls == ["stop", "loop"] and discord_bot.RESTART_BOT is True
    assert macro_calls.said[-1] == "The bot restarts now."
    macro_calls.calls.clear()
    run_restart(macro_calls, monkeypatch, running=True, loop_was_running=False)           # a window with no loop comes back with no loop
    assert macro_calls.calls == ["stop", "window"]


def test_a_restart_with_no_macro_running_starts_none_and_still_restarts_the_bot(macro_calls, monkeypatch):
    run_restart(macro_calls, monkeypatch, running=False, loop_was_running=True)
    assert macro_calls.calls == [] and discord_bot.RESTART_BOT is True
    assert any("was not running" in text for text in macro_calls.said)


def test_a_macro_that_does_not_look_started_is_said_with_its_error_output(macro_calls, monkeypatch, tmp_path):
    async def start(config):
        return "Started the macro. No sign of its loop yet - see /status and /log."
    monkeypatch.setattr(discord_bot, "start_macro", start)
    monkeypatch.setattr(discord_bot.cfg, "MACRO_STDERR_PATH", str(tmp_path / "macro_stderr.log"))
    (tmp_path / "macro_stderr.log").write_text("Traceback: boom\n")
    run_restart(macro_calls, monkeypatch, running=True, loop_was_running=True)
    assert any("does not look started" in text and "boom" in text for text in macro_calls.said) and discord_bot.RESTART_BOT is True        # (the bot restarts anyway)


@pytest.fixture
def tour_folder(tmp_path, monkeypatch):
    folder = tmp_path / "inventory"
    folder.mkdir()
    monkeypatch.setattr(discord_bot.cfg, "INVENTORY_DIR", str(folder))
    monkeypatch.setattr(discord_bot.cfg, "INVENTORY_DONE_PATH", str(folder / "done.json"))
    monkeypatch.setattr(discord_bot.cfg, "BOT_INVENTORY_POLL", 0.01)
    monkeypatch.setattr(discord_bot.cfg, "BOT_INVENTORY_WAIT", 0.3)
    return folder


def test_an_inventory_tour_is_asked_for_waited_for_and_its_pictures_are_listed_in_order(tour_folder, monkeypatch):
    sent = []

    async def send(command, **details):
        sent.append(command)
        return ""
    monkeypatch.setattr(discord_bot, "send_command", send)
    (tour_folder / "007.png").write_text("from an earlier tour")                       # cleared before the macro is asked

    async def macro_tour():
        await asyncio.sleep(0.05)
        for name in ("002.png", "001.png"):
            (tour_folder / name).write_text("picture")
        (tour_folder / "done.json").write_text(json.dumps({"pictures": 2, "ended": "the end of the list"}))

    async def both():
        task = asyncio.ensure_future(macro_tour())
        result = await discord_bot.collect_inventory()
        await task
        return result
    text, pictures = asyncio.run(both())
    assert sent == ["inventory"] and [pathlib.Path(p).name for p in pictures] == ["001.png", "002.png"]
    assert text.startswith("2 pictures of the pets tab (the end of the list)") and "/loop" in text


def test_an_inventory_tour_that_never_ends_or_is_refused_is_said(tour_folder, monkeypatch):
    async def send_ok(command, **details):
        return ""
    monkeypatch.setattr(discord_bot, "send_command", send_ok)
    text, pictures = asyncio.run(discord_bot.collect_inventory())
    assert "has not ended its tour" in text and pictures == []

    async def send_refused(command, **details):
        return "The macro dropped the command (too old) - nothing was done."
    monkeypatch.setattr(discord_bot, "send_command", send_refused)
    assert asyncio.run(discord_bot.collect_inventory()) == ("The macro dropped the command (too old) - nothing was done.", [])


@pytest.fixture
def trades_folder(tmp_path, monkeypatch):
    folder = tmp_path / "trades"
    folder.mkdir()
    for name, path in (("TRADES_DIR", folder), ("TRADES_DONE_PATH", folder / "done.json"), ("TRADES_DECLINE_DONE_PATH", folder / "decline.json"), ("TRADES_DECLINED_PATH", folder / "declined.png")):
        monkeypatch.setattr(discord_bot.cfg, name, str(path))
    monkeypatch.setattr(discord_bot.cfg, "BOT_TRADES_POLL", 0.01)
    monkeypatch.setattr(discord_bot.cfg, "BOT_TRADES_WAIT", 0.3)
    return folder


def test_a_look_at_the_trade_listings_is_asked_for_waited_for_and_its_pictures_listed(trades_folder, monkeypatch):
    sent = []

    async def send(command, **details):
        sent.append((command, details))
        return ""
    monkeypatch.setattr(discord_bot, "send_command", send)
    (trades_folder / "L1_09.png").write_text("from an earlier tour")                    # cleared before the macro is asked

    async def macro_tour():
        await asyncio.sleep(0.05)
        for name in ("L2_01.png", "L1_01.png"):
            (trades_folder / name).write_text("picture")
        (trades_folder / "done.json").write_text(json.dumps({"listings": {"1": {"offers": 2, "pictures": 1, "ended": "the end of the list"}}, "problem": ""}))

    async def both():
        task = asyncio.ensure_future(macro_tour())
        result = await discord_bot.collect_trades()
        await task
        return result
    text, pictures = asyncio.run(both())
    assert sent == [("trades", {})] and [pathlib.Path(p).name for p in pictures] == ["L1_01.png", "L2_01.png"]
    assert text.startswith("Listing 1: 2 offers, 1 picture") and "/loop" in text
    text, pictures = asyncio.run(discord_bot.collect_trades())                          # (the marker was removed with the pictures: this one never ends)
    assert "has not ended its look at the trade listings" in text and pictures == []


def test_a_decline_is_asked_for_with_its_listing_and_offer_and_the_picture_of_what_was_declined_comes_back(trades_folder, monkeypatch):
    sent = []

    async def send(command, **details):
        sent.append((command, details))
        return ""
    monkeypatch.setattr(discord_bot, "send_command", send)

    async def macro_decline():
        await asyncio.sleep(0.05)
        (trades_folder / "declined.png").write_text("picture")
        (trades_folder / "decline.json").write_text(json.dumps({"listing": 2, "offer": 3, "declined": True, "why": ""}))

    async def both():
        task = asyncio.ensure_future(macro_decline())
        result = await discord_bot.decline_offer(2, 3)
        await task
        return result
    text, pictures = asyncio.run(both())
    assert sent == [("decline", {"listing": 2, "offer": 3})] and text.startswith("Declined offer 3 of listing 2") and [pathlib.Path(p).name for p in pictures] == ["declined.png"]
    (trades_folder / "decline.json").write_text(json.dumps({"listing": 2, "offer": 3, "declined": False, "why": "the offer did not open"}))   # (cleared before the macro is asked: gone)
    text, pictures = asyncio.run(discord_bot.decline_offer(2, 3))
    assert "has not ended the decline" in text and pictures == []


class ChannelClient:
    def __init__(self):
        self.channel = FakeChannel()

    def get_channel(self, channel_id):
        return self.channel


def test_what_is_said_when_an_action_ends_follows_how_it_ended():
    assert discord_bot.action_end_text("Setup", "done", "Its loop starts now.") == "Setup finished (done). Its loop starts now."
    assert discord_bot.action_end_text("Setup", "stopped", "Its loop starts now.") == "Setup finished (stopped)."                  # (no loop after a Setup that did not end)
    assert discord_bot.action_end_text("The rejoin", "error") == "The rejoin finished (error). See /log."
    assert discord_bot.action_end_text("The rejoin", "given up: the running loop did not stop").startswith("The rejoin finished (given up:")


def test_the_channel_is_told_when_an_action_ends_and_when_it_never_says(tmp_path, monkeypatch):
    monkeypatch.setattr(discord_bot.cfg, "COMMANDS_DIR", str(tmp_path))
    monkeypatch.setattr(discord_bot.cfg, "BOT_ACTION_POLL", 0.01)
    monkeypatch.setattr(discord_bot.cfg, "BOT_ACTION_WAIT", 0.2)
    config = bot_lib.load_config(_config(tmp_path))
    client = ChannelClient()

    async def macro_ends():
        await asyncio.sleep(0.05)
        (tmp_path / "setup-1.done").write_text("done")

    async def both():
        task = asyncio.ensure_future(macro_ends())
        await discord_bot.announce_when_done(client, config, "setup-1", "Setup", "Its loop starts again.")
        await task
    asyncio.run(both())
    assert client.channel.sent == ["Setup finished (done). Its loop starts again."] and not (tmp_path / "setup-1.done").exists()          # (read once)
    asyncio.run(discord_bot.announce_when_done(client, config, "never", "The rejoin"))
    assert "has not reported that it finished" in client.channel.sent[-1]


def test_a_command_submitted_says_its_name_so_that_its_end_can_be_waited_for(tmp_path, monkeypatch):
    monkeypatch.setattr(discord_bot.cfg, "COMMANDS_DIR", str(tmp_path / "commands"))
    monkeypatch.setattr(discord_bot.cfg, "BOT_COMMAND_ACK_WAIT", 0.3)
    monkeypatch.setattr(discord_bot.cfg, "BOT_COMMAND_ACK_POLL", 0.02)
    folder = tmp_path / "commands"

    async def both():
        task = asyncio.ensure_future(_answer_with(folder, "taken")())
        result = await discord_bot.submit_command("setup")
        await task
        return result
    problem, name = asyncio.run(both())
    assert problem == "" and name and not (folder / (name + ".json")).exists()
    problem, name = asyncio.run(discord_bot.submit_command("setup"))                                  # nobody takes it
    assert "withdrawn" in problem and name == ""


def test_the_bot_says_what_it_runs_and_what_the_macro_is_when_it_is_online(tmp_path, monkeypatch):
    config = bot_lib.load_config(_config(tmp_path))
    monkeypatch.setattr(discord_bot, "run_command", lambda command, folder, timeout: (0, "abc1234"))
    monkeypatch.setattr(bot_lib.cfg, "STATUS_PATH", str(tmp_path / "status.json"))
    for pids, status, expected in (([], None, "not running"), ([5], {"stopped": "x"}, "open with no loop"), ([5], {"updated": "x"}, "running its loop"), (None, None, "not known")):
        monkeypatch.setattr(discord_bot, "current_macro_pids", lambda pids=pids: pids)
        (tmp_path / "status.json").write_text(json.dumps(status))
        text = asyncio.run(discord_bot.online_text(config))
        assert text.startswith("**The bot is online** (at commit abc1234)") and expected in text
    monkeypatch.setattr(discord_bot, "run_command", lambda command, folder, timeout: (1, "not a git repository"))
    assert asyncio.run(discord_bot.online_text(config)).startswith("**The bot is online**. ")          # (no commit to say)


def test_an_update_that_stops_part_way_does_not_say_it_finished(tmp_path, monkeypatch):
    said = []
    config = bot_lib.load_config(_config(tmp_path))

    async def nobody():
        return []

    async def say(text):
        said.append(text)

    async def git_changes(config, *args):
        return "requirements.txt\n" if args[0] == "diff" else "abc1234\n" if args[0] == "rev-parse" else ""
    monkeypatch.setattr(discord_bot, "running_pids", nobody)
    monkeypatch.setattr(discord_bot, "git", git_changes)
    monkeypatch.setattr(discord_bot, "run_command", lambda command, folder, timeout: (1, "pip is broken"))              # the packages cannot be installed
    assert asyncio.run(discord_bot.apply_commit(config, ["merge", "--ff-only", "x"], "0" * 40, say, True)) is False
    assert any("pip failed" in text for text in said)
    monkeypatch.setattr(discord_bot, "git", lambda config, *args: _git_plain(args))
    monkeypatch.setattr(discord_bot, "run_command", lambda command, folder, timeout: (0, ""))
    assert asyncio.run(discord_bot.apply_commit(config, ["merge", "--ff-only", "x"], "0" * 40, say, True)) is True


async def _git_plain(args):
    return "abc1234\n" if args[0] == "rev-parse" else ""


def test_after_an_update_that_started_the_macro_again_the_roblox_window_is_brought_to_the_front(macro_calls, monkeypatch):
    sent = []

    async def send(command, **details):
        sent.append(command)
        return ""

    async def git_plain(config, *args):
        return "abc1234\n" if args[0] == "rev-parse" else ""

    async def running():
        return [5]
    monkeypatch.setattr(discord_bot, "send_command", send)
    monkeypatch.setattr(discord_bot, "git", git_plain)
    monkeypatch.setattr(discord_bot, "running_pids", running)
    window_waits = []

    async def window_up(since):
        window_waits.append(since)
        return ""
    monkeypatch.setattr(discord_bot, "wait_for_window", window_up)
    assert asyncio.run(discord_bot.apply_commit(macro_calls.config, ["merge"], "0" * 40, macro_calls.say, True)) is True
    assert len(window_waits) == 1                                                          # (focus is sent only once the window is up: before, the macro drops it)
    assert macro_calls.calls == ["stop", "loop"] and sent == ["focus"]                    # (after the macro was started again)

    async def not_started(config):
        return "Started the macro. No sign of its loop yet - see /status and /log."
    monkeypatch.setattr(discord_bot, "start_macro", not_started)
    sent.clear()
    asyncio.run(discord_bot.apply_commit(macro_calls.config, ["merge"], "0" * 40, macro_calls.say, True))
    assert sent == []                                                                     # a macro that does not look started is not focused on

    async def nobody():
        return []
    monkeypatch.setattr(discord_bot, "running_pids", nobody)
    asyncio.run(discord_bot.apply_commit(macro_calls.config, ["merge"], "0" * 40, macro_calls.say, True))
    assert sent == []                                                                     # nor is one that was not running

    async def refused(command, **details):
        return "The macro dropped the command (too old) - nothing was done."
    monkeypatch.setattr(discord_bot, "send_command", refused)
    monkeypatch.setattr(discord_bot, "running_pids", running)
    monkeypatch.setattr(discord_bot, "start_macro", lambda config: _started(macro_calls))
    macro_calls.said.clear()
    asyncio.run(discord_bot.apply_commit(macro_calls.config, ["merge"], "0" * 40, macro_calls.say, True))
    assert any("could not be brought to the front" in text for text in macro_calls.said)


async def _started(macro_calls):
    macro_calls.calls.append("loop")
    return "Started the macro. Its loop is running."


def window_macro(monkeypatch, tmp_path, opened="Started the macro (its window is up, no loop going - /loop starts the loop).", running=False):
    """start_window()/ensure_macro() with the launching replaced: `status` is written by the test when the window is to be up."""
    monkeypatch.setattr(bot_lib.cfg, "STATUS_PATH", str(tmp_path / "status.json"))
    calls = []

    async def open_macro(config):
        calls.append("open")
        return opened

    async def pids():
        return [3] if running else []
    monkeypatch.setattr(discord_bot, "open_macro", open_macro)
    monkeypatch.setattr(discord_bot, "running_pids", pids)
    monkeypatch.setattr(discord_bot.cfg, "BOT_COMMAND_ACK_POLL", 0.01)
    monkeypatch.setattr(discord_bot.cfg, "BOT_START_CONFIRM", 0.3)
    return calls, bot_lib.load_config(_config(tmp_path))


def test_a_command_that_needs_the_macro_starts_it_instead_of_saying_it_is_not_running(tmp_path, monkeypatch):
    calls, config = window_macro(monkeypatch, tmp_path)
    status = tmp_path / "status.json"

    async def window_comes_up():
        await asyncio.sleep(0.05)
        status.write_text(json.dumps({"updated": datetime.now().strftime(bot_lib.TIME_FORMAT), "stopped": "x"}))      # the window writes it when it is up

    async def both():
        task = asyncio.ensure_future(window_comes_up())
        result = await discord_bot.ensure_macro(config)
        await task
        return result
    assert asyncio.run(both()) == ("", True) and calls == ["open"]
    assert discord_bot.LAUNCHING == 0                                                      # (the crash check is looking again)


def test_a_macro_that_is_running_is_left_alone_and_one_that_does_not_come_up_is_said(tmp_path, monkeypatch):
    calls, config = window_macro(monkeypatch, tmp_path, running=True)
    assert asyncio.run(discord_bot.ensure_macro(config)) == ("", False) and calls == []
    calls, config = window_macro(monkeypatch, tmp_path)
    problem, started = asyncio.run(discord_bot.ensure_macro(config))                       # no status is ever written
    assert "did not come up in time" in problem and started is False and calls == ["open"]
    calls, config = window_macro(monkeypatch, tmp_path, opened="Started the macro, but its process is not there - see /log and macro_stderr.log.")
    assert asyncio.run(discord_bot.ensure_macro(config)) == ("Started the macro, but its process is not there - see /log and macro_stderr.log.", False)


class FakeMessage:
    def __init__(self):
        self.edits = []

    async def edit(self, **kwargs):
        self.edits.append(kwargs)


def debug_fakes(monkeypatch, answers=None):
    """The debug session with the macro and Discord replaced: `sent` is every command asked for, `said` what the channel was told."""
    sent, said, message = [], [], FakeMessage()

    async def submit(command, **details):
        sent.append((command, details))
        return (answers or {}).get((command, details.get("value")), ""), "name"

    async def say(interaction, text=None, **kwargs):
        said.append(text)
        return message

    async def ended(name, seconds):
        return "stopped"
    monkeypatch.setattr(discord_bot, "submit_command", submit)
    monkeypatch.setattr(discord_bot, "say", say)
    monkeypatch.setattr(discord_bot, "wait_for_end", ended)
    monkeypatch.setattr(discord_bot, "grab_roblox_jpeg", lambda: (b"jpeg", False))
    monkeypatch.setattr(discord_bot.cfg, "BOT_RVIEW_INTERVAL", 0.01)
    return sent, said, message


def test_a_debug_session_starts_a_capture_and_a_rolling_view_and_ends_both_once_when_the_command_is_over(monkeypatch):
    sent, said, message = debug_fakes(monkeypatch)

    async def run():
        session = discord_bot.DebugSession(object())
        await session.start()
        await asyncio.sleep(0.1)
        await session.end()
        await session.end()                                                           # (once)
    asyncio.run(run())
    assert sent == [("capture", {"value": True}), ("capture", {"value": False})]       # (a start, not a toggle: a capture that runs already is left alone, and the end only ends)
    assert said[0].startswith("Capture running. Rolling view") and "stopped and being zipped" in said[-1] and len(said) == 2
    assert len(message.edits) >= 3 and message.edits[-1]["content"] == "Capture running. Rolling view over."          # (pictures while it went, then over)
    assert all("attachments" in e for e in message.edits[:-1])


def test_a_debug_session_without_a_capture_still_shows_the_view_and_says_why_and_a_command_that_ends_it_does_it_through_announce(monkeypatch, tmp_path):
    sent, said, message = debug_fakes(monkeypatch, answers={("capture", True): "The macro dropped the command (too old) - nothing was done."})
    monkeypatch.setattr(discord_bot.cfg, "COMMANDS_DIR", str(tmp_path))
    monkeypatch.setattr(discord_bot.cfg, "BOT_ACTION_POLL", 0.01)
    monkeypatch.setattr(discord_bot.cfg, "BOT_ACTION_WAIT", 0.3)
    told = []

    async def tell(client, config, text):
        told.append(text)
    monkeypatch.setattr(discord_bot, "tell_channel", tell)

    async def run():
        session = discord_bot.DebugSession(object())
        await session.start()
        (tmp_path / "name.done").write_text("done")                                       # the macro's action ends: announce says so, and then the session ends
        await discord_bot.announce_when_done(None, None, "name", "The test", "", session.end)
        return session
    session = asyncio.run(run())
    assert sent == [("capture", {"value": True})] and said[0].startswith("No capture (The macro dropped the command")
    assert told == ["The test finished (done)."] and session.ended is True and message.edits[-1]["content"].endswith("Rolling view over.")


def test_the_session_ends_even_when_the_macro_never_reports(monkeypatch, tmp_path):
    sent, said, message = debug_fakes(monkeypatch)
    monkeypatch.setattr(discord_bot.cfg, "COMMANDS_DIR", str(tmp_path))
    monkeypatch.setattr(discord_bot.cfg, "BOT_ACTION_POLL", 0.01)
    monkeypatch.setattr(discord_bot.cfg, "BOT_ACTION_WAIT", 0.05)

    async def tell(client, config, text):
        pass
    monkeypatch.setattr(discord_bot, "tell_channel", tell)

    async def run():
        session = discord_bot.DebugSession(object())
        await session.start()
        await discord_bot.announce_when_done(None, None, "name", "The test", "", session.end)
        return session
    assert asyncio.run(run()).ended is True and sent[-1] == ("capture", {"value": False})


def test_the_macros_answer_to_capture_is_said_in_words():
    assert "/capture again to end it" in discord_bot.capture_text("started")
    assert "stopped" in discord_bot.capture_text("stopped") and "/captures" in discord_bot.capture_text("stopped")
    assert discord_bot.capture_text("failed: no space left") == "The capture did not work (no space left) - see /log."
    assert "has not said" in discord_bot.capture_text(None)


def test_the_macros_answer_to_blank_is_said_in_words(tmp_path, monkeypatch):
    assert discord_bot.blank_text("blanked").startswith("The screen is blank") and discord_bot.blank_text("shown") == "The screen is shown."
    assert "was not blanked" in discord_bot.blank_text("not blanked") and "has not said" in discord_bot.blank_text(None)
    monkeypatch.setattr(discord_bot.cfg, "COMMANDS_DIR", str(tmp_path))
    monkeypatch.setattr(discord_bot.cfg, "BOT_COMMAND_ACK_POLL", 0.01)

    async def macro_answers():
        await asyncio.sleep(0.05)
        (tmp_path / "b1.done").write_text("blanked")

    async def both():
        task = asyncio.ensure_future(macro_answers())
        result = await discord_bot.wait_for_end("b1", 1.0)
        await task
        return result
    assert asyncio.run(both()) == "blanked" and not (tmp_path / "b1.done").exists()
    assert asyncio.run(discord_bot.wait_for_end("never", 0.1)) is None
