"""The Discord bot's glue (bot/discord_bot.py) run against fakes: a channel that records what is sent, and the macro's files in a temp folder. What the real
Discord does with a message (rate limits, an expired interaction) is not here; what the bot decides to send, and when, is."""
import asyncio
import json
import time
import types

import pytest

pytest.importorskip("discord")

from bot import bot_lib, discord_bot  # noqa: E402


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
    return types.SimpleNamespace(once=once, channel=channel, posted=posted, state=state, folder=tmp_path, config=config)


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
