"""The Discord bot: ask the macro's PC how it is doing from anywhere. Read-only (Phase 1 of docs/planned/Discord.md): /status, /screenshot, /log,
/report, /results. It runs on the PC the macro runs on, in the same logged-in desktop session (it reads the macro's files and grabs its screen),
and connects out to Discord, so no port needs opening. Only one user, in one channel, is obeyed (bot_config.json).

    python bot/discord_bot.py [path to bot_config.json]

The logic that needs neither Discord nor Windows is in bot_lib.py (tested); this file is the part that talks to Discord and the screen.
See docs/ARCHITECTURE.md#discord-bot."""
import asyncio
import contextlib
import io
import logging
import os
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


def list_processes() -> Optional[list[tuple[int, str]]]:
    """(id, command line) of every process on this PC, or None when that can't be found out (psutil is used when it is installed)."""
    try:
        import psutil
        return [(p.info["pid"], " ".join(p.info["cmdline"] or [])) for p in psutil.process_iter(["pid", "cmdline"])]
    except ImportError:
        pass
    try:
        if os.name == "nt":
            command = ["powershell", "-NoProfile", "-Command",
                       "Get-CimInstance Win32_Process | ForEach-Object { \"$($_.ProcessId) $($_.CommandLine)\" }"]
        else:
            command = ["pgrep", "-af", "."]
        return bot_lib.parse_process_lines(subprocess.run(command, capture_output=True, text=True, timeout=15).stdout)
    except (OSError, subprocess.SubprocessError):
        return None


def macro_process_running() -> Optional[bool]:
    """Whether main.py is running on this PC; None when that can't be found out."""
    processes = list_processes()
    return None if processes is None else bool(bot_lib.macro_pids(processes))


def release_inputs() -> None:
    """Let go of every key the macro holds and of a mouse button that is down - for a macro that was killed (a killed process can leave one pressed in
    the game). Best effort: main.py's release_all_inputs() is the same list (cfg.MOVE_KEYS and the others), and a button is only released if it is
    down, since a right button released with none pressed is a click."""
    try:
        import pydirectinput
    except ImportError:
        return
    for key in (*cfg.MOVE_KEYS, cfg.KEY_HELICOPTER, cfg.KEY_ZOOM_IN, cfg.KEY_ZOOM_OUT):
        try:
            pydirectinput.keyUp(key)
        except Exception:
            pass
    if os.name == "nt":
        import ctypes
        for button, code in (("left", 0x01), ("right", 0x02)):
            try:
                if ctypes.windll.user32.GetAsyncKeyState(code) & 0x8000:  # type: ignore[attr-defined]
                    pydirectinput.mouseUp(button=button)
            except Exception:
                pass


def launch_macro(config: bot_lib.Config, loop: bool = True) -> int:
    """Start `main.py` (the repository's, with the configured Python; `--loop` if `loop`) as a process of its own, in this desktop session; what it prints on its
    error output is appended to debug/logs/macro_stderr.log. Returns its process id."""
    folder = config.repo_dir or ROOT
    with open(os.path.join(cfg.LOGS_DIR, "macro_stderr.log"), "ab") as errors:
        process = subprocess.Popen([config.python or sys.executable, os.path.join(folder, "main.py"), *(["--loop"] if loop else [])], cwd=folder,
                                   stdout=subprocess.DEVNULL, stderr=errors)
    return process.pid


def kill_macro(pids: list[int]) -> None:
    """End the processes, hard (the fallback of a stop that did not end the macro), then release what it may have held."""
    for pid in pids:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=15)
            else:
                import signal
                os.kill(pid, signal.SIGKILL)
        except (OSError, subprocess.SubprocessError):
            pass
    release_inputs()


def current_macro_pids() -> list[int]:
    return bot_lib.macro_pids(list_processes() or [])


async def stop_macro(config: bot_lib.Config) -> str:
    """Ask the macro to exit (its cooperative stop: the loop ends, every key and the mouse are released, the window closes) and wait up to
    stop_timeout_seconds for its process to end; if it hasn't, kill it and release the inputs from here. Returns what happened, as a sentence."""
    if not await asyncio.to_thread(current_macro_pids):
        return "The macro was not running."
    bot_lib.write_command(cfg.COMMANDS_PATH, "exit", time.time())
    deadline = time.time() + config.stop_timeout_seconds
    while time.time() < deadline:
        await asyncio.sleep(1)
        if not await asyncio.to_thread(current_macro_pids):
            return "The macro stopped (it released its keys and closed)."
    pids = await asyncio.to_thread(current_macro_pids)
    await asyncio.to_thread(kill_macro, pids)
    with contextlib.suppress(OSError):
        os.remove(cfg.COMMANDS_PATH)
    return f"The macro did not stop within {config.stop_timeout_seconds:g} s - it was killed and its keys and mouse buttons released."


async def open_macro(config: bot_lib.Config) -> str:
    """Start the macro with its window up and no loop going (`main.py`), unless it is running already."""
    if await asyncio.to_thread(current_macro_pids):
        return "The macro is already running."
    await asyncio.to_thread(launch_macro, config, False)
    deadline = time.time() + cfg.BOT_START_CONFIRM
    while time.time() < deadline:
        await asyncio.sleep(2)
        if await asyncio.to_thread(current_macro_pids):
            return "Started the macro (its window is up, no loop going - /loop starts the loop)."
    return "Started the macro, but its process is not there - see /log and macro_stderr.log."


async def stop_loop(config: bot_lib.Config) -> str:
    """Ask the running macro to stop its loop (the [STOP] button); its window stays open."""
    if not await asyncio.to_thread(current_macro_pids):
        return "The macro is not running."
    bot_lib.write_command(cfg.COMMANDS_PATH, "stop", time.time())
    return "Asked the macro to stop (its keys are released; its window stays open - /exit closes it)."


async def start_macro(config: bot_lib.Config) -> str:
    """Start the macro's loop: launch `main.py --loop` if it is not running; if it is (its window may be open with the loop stopped), ask it to start
    the loop. Then wait up to BOT_START_CONFIRM seconds for status.json to be written, and say what was seen."""
    since = datetime.now()
    if await asyncio.to_thread(current_macro_pids):
        bot_lib.write_command(cfg.COMMANDS_PATH, "start", time.time())
        what = "The macro was already running - asked it to start its loop (nothing happens if it is going already)."
    else:
        await asyncio.to_thread(launch_macro, config)
        what = "Started the macro."
    deadline = time.time() + cfg.BOT_START_CONFIRM
    while time.time() < deadline:
        await asyncio.sleep(2)
        if bot_lib.started_since(bot_lib.read_json(bot_lib.status_path()), since):
            return what + " Its loop is running."
    return what + " No sign of its loop yet - see /status and /log."


RESTART_BOT = False        # set by /update or /rollback when the bot's own code changed: main() starts the bot again once it has closed


def repo_folder(config: bot_lib.Config) -> str:
    return config.repo_dir or ROOT


def run_command(command: list[str], folder: str, timeout: float) -> tuple[int, str]:
    """Run `command` (a list: no shell) in `folder`; (exit code, its output). A program that isn't there or takes too long gives code -1 and why."""
    try:
        done = subprocess.run(command, cwd=folder, capture_output=True, text=True, errors="replace", timeout=timeout)
        return done.returncode, (done.stdout + done.stderr).strip()
    except (OSError, subprocess.SubprocessError) as error:
        return -1, f"{command[0]}: {error}"


async def git(config: bot_lib.Config, *args: str) -> str:
    """The output of a git command of the repository that has to work (RuntimeError with git's message if it doesn't), run on a thread."""
    return await asyncio.to_thread(bot_lib.git_ok, git_runner(config), *args)


def git_runner(config: bot_lib.Config) -> bot_lib.GitRunner:
    return lambda args: run_command(["git", *args], repo_folder(config), cfg.BOT_GIT_TIMEOUT)


def output_tail(path: str, count: int = 12) -> str:
    return bot_lib.tail_lines(path, count) or "(nothing)"


async def apply_commit(config: bot_lib.Config, move: list[str], old: str, say) -> None:
    """The steps both /update and /rollback share, once the plan is made: stop the macro, move the repository (`move`: the git arguments), install
    the packages if requirements.txt changed, start the macro again if it was running, and ask for the bot's own restart if its code changed.
    `say` posts a message. A step that fails stops it there, with what to do."""
    global RESTART_BOT
    was_running = bool(await asyncio.to_thread(current_macro_pids))
    if was_running:
        await say(await stop_macro(config))
    await git(config, *move)
    new = (await git(config, "rev-parse", "HEAD")).strip()
    changed = (await git(config, "diff", "--name-only", old, new)).splitlines()
    await say(f"Now at {bot_lib.short(new)}. Changed: {bot_lib.files_text(changed) or 'nothing'}.")
    if bot_lib.needs_pip(changed):
        await say("requirements.txt changed - installing the packages...")
        code, output = await asyncio.to_thread(run_command, [config.python or sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], repo_folder(config), cfg.BOT_PIP_TIMEOUT)
        if code != 0:
            await say(f"pip failed (exit {code}) - the macro was not started. Use /rollback.\n```\n{output[-1200:]}\n```")
            return
    if was_running:
        result = await start_macro(config)
        await say(result)
        if "Its loop is running." not in result:
            errors = output_tail(os.path.join(cfg.LOGS_DIR, "macro_stderr.log"))
            await say(f"The macro does not look started. The end of its error output:\n```\n{errors}\n```\nUse /rollback to go back to {bot_lib.short(old)}.")
    else:
        await say("The macro was not running, so it was not started (/loop).")
    if bot_lib.needs_bot_restart(changed):
        await say("The bot's own code changed - the bot restarts now.")
        RESTART_BOT = True


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
    client.bot_state = bot_lib.load_state(bot_lib.state_path())  # type: ignore[attr-defined]   # (what the bot has dealt with; the commands and watch() share it)
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

    def remember(desired: str) -> None:
        state = client.bot_state  # type: ignore[attr-defined]
        state.desired_state, state.crash_reported = desired, False
        bot_lib.save_state(bot_lib.state_path(), state)

    @command("start", "Start the macro with its window up and no loop going")
    async def start(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        remember("running")
        await interaction.followup.send(await open_macro(config))

    @command("loop", "Start the macro's loop (launches the macro if it is not running)")
    async def loop(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        remember("running")
        await interaction.followup.send(await start_macro(config))

    @command("stop", "Stop the macro's loop (its window stays open)")
    async def stop(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        await interaction.followup.send(await stop_loop(config))

    @command("exit", "Close the macro: its loop is stopped, its keys are released and its window closed")
    async def exit_macro(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        remember("stopped")
        await interaction.followup.send(await stop_macro(config))

    @command("restart", "Close the macro, then start it with its loop")
    async def restart(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        remember("stopped")
        stopped = await stop_macro(config)
        remember("running")
        await interaction.followup.send(f"{stopped}\n{await start_macro(config)}")

    @command("rejoin", "Have the macro leave the game and rejoin it (stops its loop first)")
    async def rejoin(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        if not await asyncio.to_thread(current_macro_pids):
            await interaction.followup.send("The macro isn't running - /start it first (a rejoin is done by its window).")
            return
        bot_lib.write_command(cfg.COMMANDS_PATH, "rejoin", time.time())
        await interaction.followup.send("Asked the macro to leave and rejoin. Its loop is stopped; /loop it again once it is back in the game.")

    async def change_switch(interaction: discord.Interaction, kind: str, name: str, value: bool) -> None:
        """Ask the running macro to change a switch (the command file) and say what the game config shows a moment later."""
        await interaction.response.defer(thinking=True)
        if not await asyncio.to_thread(current_macro_pids):
            await interaction.followup.send("The macro isn't running - a switch can only be changed while its window is open (or by hand in the window).")
            return
        bot_lib.write_command(cfg.COMMANDS_PATH, "set", time.time(), **bot_lib.switch_command(kind, name, value))
        if name == cfg.SHOW_DEBUG_LINES_OPTION:
            await interaction.followup.send(f"Asked the macro to turn {name} {'on' if value else 'off'} (it is not kept in the config, so it can't be checked).")
            return
        for _ in range(5):
            await asyncio.sleep(1)
            if bot_lib.switch_states(bot_lib.read_json(cfg.GAME_CONFIG_PATH)).get(f"{kind}:{name}") == value:
                await interaction.followup.send(f"{name} is now {'on' if value else 'off'}.")
                return
        await interaction.followup.send(f"Asked the macro to turn {name} {'on' if value else 'off'}, but the config doesn't show it yet.")

    need_choices = [app_commands.Choice(name=name, value=name) for name in cfg.NEED_NAMES]
    option_choices = [app_commands.Choice(name=name, value=name) for name in (*cfg.OPTION_SWITCHES, cfg.SHOW_DEBUG_LINES_OPTION)]

    update_lock = asyncio.Lock()

    async def change_code(interaction: discord.Interaction, rollback: bool) -> None:
        """/update and /rollback: make the plan from what git says (nothing is changed unless it says go), then apply_commit() - one at a time."""
        await interaction.response.defer(thinking=True)
        if update_lock.locked():
            await interaction.followup.send("An update or rollback is already going on.")
            return
        async with update_lock:
            state = client.bot_state  # type: ignore[attr-defined]
            try:
                plan, move, head = await asyncio.to_thread(bot_lib.make_plan, git_runner(config), config.update_remote, config.update_branch, state.previous_commit, rollback)
            except RuntimeError as error:
                await interaction.followup.send(f"Nothing was changed. {error}")
                return
            await interaction.followup.send(f"```\n{plan.text}\n```")
            if plan.kind not in ("update", "rollback"):
                return
            if not rollback:
                state.previous_commit = head
            else:
                state.previous_commit = None
            bot_lib.save_state(bot_lib.state_path(), state)
            try:
                await apply_commit(config, move, head, lambda text: interaction.followup.send(text))
            except RuntimeError as error:
                await interaction.followup.send(f"**Stopped:** {error}\nThe macro may be stopped - see /status.")
                return
        if RESTART_BOT:
            await client.close()

    @command("update", "Pull the latest main, restart the macro (and the bot if its code changed)")
    async def update(interaction: discord.Interaction) -> None:
        await change_code(interaction, rollback=False)

    @command("rollback", "Go back to the commit before the last /update and restart the macro")
    async def rollback(interaction: discord.Interaction) -> None:
        await change_code(interaction, rollback=True)

    @command("need", "Switch a need on or off (the Debug tab's switches)")
    @app_commands.choices(need=need_choices)
    async def need_command(interaction: discord.Interaction, need: app_commands.Choice[str], on: bool) -> None:
        await change_switch(interaction, "need", need.value, on)

    @command("option", "Switch an option on or off (side quest, minigames, recording, debug lines in the output...)")
    @app_commands.choices(option=option_choices)
    async def option_command(interaction: discord.Interaction, option: app_commands.Choice[str], on: bool) -> None:
        await change_switch(interaction, "option", option.value, on)

    @command("options", "Which needs and options are on")
    async def options(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        text = bot_lib.options_text(bot_lib.switch_states(bot_lib.read_json(cfg.GAME_CONFIG_PATH)))
        await interaction.followup.send(f"```\n{text}\n```")

    pull_folders = [app_commands.Choice(name=name, value=name) for name in cfg.PULL_SOURCES]

    @command("pull", "Copy something from the debug folder into OneDrive (the newest of a folder, or one you pick)")
    @app_commands.choices(folder=pull_folders)
    @app_commands.describe(folder="Which folder of debug/", item="Which one - leave out for the newest; * is everything in the folder")
    async def pull(interaction: discord.Interaction, folder: app_commands.Choice[str], item: Optional[str] = None) -> None:
        await interaction.response.defer(thinking=True)
        if not config.onedrive_dir or not os.path.isdir(config.onedrive_dir):
            await interaction.followup.send("onedrive_dir is not set, or is not a folder on this PC - there is nowhere to copy to.")
            return
        try:
            path, name = bot_lib.resolve_pull(folder.value, item)
            files = await asyncio.to_thread(bot_lib.pull_files, path, name)
            megabytes = await asyncio.to_thread(bot_lib.copy_pull, files, config.onedrive_dir, folder.value, cfg.BOT_MAX_PULL_MB)
        except (ValueError, OSError) as error:
            await interaction.followup.send(f"Nothing copied: {error}")
            return
        await interaction.followup.send(f"Copied `{name}` ({len(files)} file{'s' if len(files) != 1 else ''}, {megabytes:.0f} MB) to OneDrive, in `{folder.value}/`. The OneDrive app on the PC uploads it from there.")

    @pull.autocomplete("item")
    async def pull_items(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        source = getattr(interaction.namespace, "folder", None)
        names = [bot_lib.PULL_ALL, *bot_lib.pull_entries(source or "")]
        return [app_commands.Choice(name=("* (everything in the folder)" if n == bot_lib.PULL_ALL else n)[:100], value=n) for n in names if current.lower() in n.lower()][:25]

    @command("captures", "The newest minigame captures on the PC (/pull minigame_captures copies one to OneDrive)")
    async def captures(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        await interaction.followup.send(f"```\n{bot_lib.captures_text(bot_lib.captures_dir())}\n```")

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
    stale (and its recovery), run charts, a heartbeat, a macro that is gone. A failure inside a look is logged and the watching
    goes on. What it has dealt with is kept in bot_state.json, so a restart neither posts it again nor misses what came while it was down."""
    await client.wait_until_ready()
    channel = client.get_channel(config.channel_id) or await client.fetch_channel(config.channel_id)
    path = bot_lib.state_path()
    state = client.bot_state  # type: ignore[attr-defined]
    limiter = bot_lib.RateLimiter(cfg.BOT_ALERT_GAP)
    if state.log_offset is None:                                   # the first start: what is there already is not news
        state.log_offset = bot_lib.file_end(bot_lib.run_log_path())
        state.charts = [os.path.basename(c) for c in bot_lib.run_charts()]
        bot_lib.save_state(path, state)
    while not client.is_closed():
        try:
            await watch_once(channel, config, state, limiter)
            bot_lib.save_state(path, state)
        except Exception:
            log.exception("a look at the macro's files failed")
        await asyncio.sleep(cfg.BOT_WATCH_INTERVAL)


async def watch_once(channel: discord.abc.Messageable, config: bot_lib.Config, state: bot_lib.BotState, limiter: bot_lib.RateLimiter) -> None:
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
    if state.desired_state == "running":
        running = await asyncio.to_thread(macro_process_running)
        state.crash_reported, event = bot_lib.crash_check(state.desired_state, running, state.crash_reported)
        if event == "alert":
            allowed, state.restarts = bot_lib.restart_allowed(state.restarts, now, config.auto_restart_max_per_hour) if config.auto_restart else (False, state.restarts)
            if allowed:
                await channel.send("**The macro's process is gone** while it should be running - restarting it.")
                await asyncio.to_thread(launch_macro, config)
                state.crash_reported = False
            else:
                limit = " (the limit of automatic restarts this hour is used)" if config.auto_restart else ""
                await channel.send(f"**The macro's process is gone** while it should be running{limit}. /start or /loop starts it again.")
        elif event == "recovered":
            await channel.send("The macro's process is running again.")
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
    if RESTART_BOT:                       # (the client has closed: the new bot is the only one logged in)
        log.info("restarting the bot with the updated code")
        if os.name == "nt":
            subprocess.Popen([sys.executable, *sys.argv], creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))   # (os.execv on Windows leaves the terminal confused)
        else:
            os.execv(sys.executable, [sys.executable, *sys.argv])


if __name__ == "__main__":
    main()
