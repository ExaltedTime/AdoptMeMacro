"""The Discord bot: ask the macro's PC how it is doing from anywhere. Read-only (Phase 1 of docs/planned/Discord.md): /status, /screenshot, /log,
/report, /results. It runs on the PC the macro runs on, in the same logged-in desktop session (it reads the macro's files and grabs its screen),
and connects out to Discord, so no port needs opening. Only one user, in one channel, is obeyed (bot_config.json).

    python bot/discord_bot.py [path to bot_config.json]

The logic that needs neither Discord nor Windows is in bot_lib.py (tested); this file is the part that talks to Discord and the screen.
See docs/ARCHITECTURE.md#discord-bot."""
import asyncio
import io
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import magic_numbers as cfg  # noqa: E402

import discord  # noqa: E402
from discord import app_commands  # noqa: E402

from bot import bot_lib  # noqa: E402

log = logging.getLogger("adoptme-bot")


def roblox_region(monitor: dict) -> dict:
    """The Roblox window as a region of the primary monitor `monitor` (mss's dict), or the whole monitor if there is no usable window."""
    try:
        import pygetwindow as gw
        windows = [w for w in gw.getAllWindows() if "roblox" in w.title.lower()]
        window = next((w for w in windows if w.title == "Roblox"), windows[0] if windows else None)
        if window is not None and not window.isMinimized:
            left, top = max(window.left, monitor["left"]), max(window.top, monitor["top"])
            right = min(window.left + window.width, monitor["left"] + monitor["width"])
            bottom = min(window.top + window.height, monitor["top"] + monitor["height"])
            if right > left and bottom > top:
                return {"left": left, "top": top, "width": right - left, "height": bottom - top}
    except Exception:
        pass
    return dict(monitor)


def grab_roblox_png() -> tuple[bytes, bool]:
    """A PNG of the Roblox window (the whole monitor if there is none) and whether it looks blank (a locked or sleeping PC)."""
    import mss
    import mss.tools
    with mss.MSS() as sct:
        shot = sct.grab(roblox_region(sct.monitors[1]))
    return mss.tools.to_png(shot.rgb, shot.size), bot_lib.looks_blank(shot.rgb)


def macro_process_running() -> Optional[bool]:
    """Whether main.py is running on this PC; None when that can't be found out."""
    try:
        import psutil  # optional: used when it is installed
        return bot_lib.main_py_in([" ".join(p.info["cmdline"] or []) for p in psutil.process_iter(["cmdline"])])
    except ImportError:
        pass
    try:
        if os.name == "nt":
            command = ["powershell", "-NoProfile", "-Command",
                       "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*main.py*' } | Select-Object -ExpandProperty CommandLine"]
        else:
            command = ["pgrep", "-af", "main.py"]
        output = subprocess.run(command, capture_output=True, text=True, timeout=15).stdout
        return bot_lib.main_py_in(output.splitlines())
    except (OSError, subprocess.SubprocessError):
        return None


class OwnerOnlyTree(app_commands.CommandTree):
    """A command tree that obeys the owner in the one channel and nobody else (their commands get no answer at all)."""

    def __init__(self, client: discord.Client, config: bot_lib.Config) -> None:
        super().__init__(client)
        self.config = config

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return bot_lib.is_allowed(self.config, interaction.user.id, interaction.channel_id or 0)

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if not isinstance(error, app_commands.CheckFailure):
            log.error("command failed: %s", error, exc_info=error)


def build(config: bot_lib.Config) -> discord.Client:
    client = discord.Client(intents=discord.Intents.default())
    tree = OwnerOnlyTree(client, config)
    guild = discord.Object(id=config.guild_id) if config.guild_id else None

    def command(name: str, description: str):
        return tree.command(name=name, description=description, guild=guild)

    @command("status", "How the macro is doing")
    async def status(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        running = await asyncio.to_thread(macro_process_running)
        text = bot_lib.status_text(bot_lib.read_json(bot_lib.status_path()), datetime.now(), config.stale_status_minutes, running)
        await interaction.followup.send(f"```\n{text}\n```")

    @command("screenshot", "The Roblox window right now")
    async def screenshot(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        try:
            png, blank = await asyncio.to_thread(grab_roblox_png)
        except Exception as error:
            await interaction.followup.send(f"Couldn't grab the screen: {error}")
            return
        note = "The screen looks blank - the PC may be locked or asleep." if blank else ""
        await interaction.followup.send(note, file=discord.File(io.BytesIO(png), "screenshot.png"))

    @command("log", "The last lines of the run log")
    @app_commands.describe(lines="How many lines (at most the configured limit)")
    async def log_command(interaction: discord.Interaction, lines: int = 20) -> None:
        await interaction.response.defer(thinking=True)
        text = bot_lib.tail_lines(bot_lib.run_log_path(), max(1, min(lines, config.log_lines_max)))
        if text is None:
            await interaction.followup.send("There is no run log yet.")
        elif bot_lib.fits_a_message(text):
            await interaction.followup.send(f"```\n{text}\n```")
        else:
            await interaction.followup.send(file=discord.File(io.BytesIO(text.encode("utf-8")), "run_log_tail.txt"))

    @command("report", "The newest run chart")
    async def report(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        path = bot_lib.newest_report()
        if path is None:
            await interaction.followup.send("There is no run chart yet - one is written when a run stops.")
        else:
            await interaction.followup.send(os.path.basename(path), file=discord.File(path))

    @command("results", "Best, worst and average candy corn of each minigame")
    async def results(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        await interaction.followup.send(f"```\n{bot_lib.results_text(bot_lib.read_results(bot_lib.results_path()))}\n```")

    @command("captures", "The newest minigame captures and whether they are in OneDrive")
    async def captures(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        done = set(bot_lib.load_state(bot_lib.state_path()).captures)
        await interaction.followup.send(f"```\n{bot_lib.captures_text(bot_lib.captures_dir(), done)}\n```")

    async def setup_hook() -> None:
        client.loop.create_task(watch(client, config))

    client.setup_hook = setup_hook  # type: ignore[method-assign]

    @client.event
    async def on_ready() -> None:
        await tree.sync(guild=guild)
        log.info("ready as %s", client.user)

    client.tree = tree  # type: ignore[attr-defined]       # (kept, so the commands can be listed)
    return client


async def post(channel: discord.abc.Messageable, text: str, path: Optional[str] = None) -> None:
    """Send `text` to the channel, with the file at `path` if there is one and it is small enough for Discord."""
    if path and os.path.getsize(path) <= cfg.BOT_MAX_UPLOAD_MB * 1048576:
        await channel.send(text, file=discord.File(path))
    else:
        await channel.send(text)


async def watch(client: discord.Client, config: bot_lib.Config) -> None:
    """Every BOT_WATCH_INTERVAL seconds look at what the macro wrote and post what is new: failures (with their screenshot), a status that has gone
    stale (and its recovery), run charts, a heartbeat - and copy finished captures' zips to OneDrive. A failure inside a look is logged and the watching
    goes on. What it has dealt with is kept in bot_state.json, so a restart neither posts it again nor misses what came while it was down."""
    await client.wait_until_ready()
    channel = client.get_channel(config.channel_id) or await client.fetch_channel(config.channel_id)
    path = bot_lib.state_path()
    state = bot_lib.load_state(path)
    limiter = bot_lib.RateLimiter(cfg.BOT_ALERT_GAP)
    if state.log_offset is None:                                   # the first start: what is there already is not news
        state.log_offset = bot_lib.file_end(bot_lib.run_log_path())
        state.charts = [os.path.basename(c) for c in bot_lib.run_charts()]
        state.captures = [os.path.basename(f) for f in os.listdir(bot_lib.captures_dir())] if os.path.isdir(bot_lib.captures_dir()) else []
        bot_lib.save_state(path, state)
    copying = bool(config.onedrive_dir)
    if copying and not os.path.isdir(config.onedrive_dir):
        await channel.send(f"onedrive_dir `{config.onedrive_dir}` is not a folder on this PC - the capture zips are not copied.")
        copying = False
    while not client.is_closed():
        try:
            await watch_once(channel, config, state, limiter, copying)
            bot_lib.save_state(path, state)
        except Exception:
            log.exception("a look at the macro's files failed")
        await asyncio.sleep(cfg.BOT_WATCH_INTERVAL)


async def watch_once(channel: discord.abc.Messageable, config: bot_lib.Config, state: bot_lib.BotState, limiter: bot_lib.RateLimiter, copying: bool) -> None:
    now = time.time()
    assert state.log_offset is not None
    lines, state.log_offset = bot_lib.read_new_lines(bot_lib.run_log_path(), state.log_offset)
    for failure in filter(None, (bot_lib.parse_failure(line) for line in lines)):
        post_now, held = limiter.check(now)
        if post_now:
            await post(channel, bot_lib.failure_text(failure, held), bot_lib.failure_screenshot_path(failure))
    status = bot_lib.read_json(bot_lib.status_path())
    state.stale, text = bot_lib.stale_alert(state.stale, status, datetime.now(), config.stale_status_minutes)
    if text:
        await channel.send(text)
    for chart in bot_lib.run_charts():
        if os.path.basename(chart) not in state.charts and now - os.path.getmtime(chart) >= cfg.BOT_FILE_SETTLE:
            state.charts.append(os.path.basename(chart))
            await post(channel, f"Run chart {os.path.basename(chart)}", chart)
    if copying:
        copied = False
        for capture in bot_lib.ready_captures(bot_lib.captures_dir(), set(state.captures), now, cfg.BOT_CAPTURE_SETTLE):
            await asyncio.to_thread(bot_lib.copy_capture, capture, config.onedrive_dir)
            state.captures.append(capture.name)
            copied = True
            await channel.send(bot_lib.capture_text(capture))
        if copied:
            for folder in bot_lib.old_copies(config.onedrive_dir, config.onedrive_keep_days, now):
                await asyncio.to_thread(shutil.rmtree, folder, True)
    if bot_lib.heartbeat_due(state.last_heartbeat, config.heartbeat_hours, now):
        running = await asyncio.to_thread(macro_process_running)
        await channel.send("Heartbeat\n```\n" + bot_lib.status_text(status, datetime.now(), config.stale_status_minutes, running) + "\n```")
        state.last_heartbeat = now


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "bot_config.json")
    try:
        config = bot_lib.load_config(path)
    except bot_lib.ConfigError as error:
        sys.exit(str(error))
    build(config).run(config.token, log_handler=None)


if __name__ == "__main__":
    main()
