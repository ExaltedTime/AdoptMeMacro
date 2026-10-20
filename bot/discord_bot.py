"""The Discord bot: ask the macro's PC how it is doing from anywhere, and control it: /status, /screenshot, /log, /report, /results, /captures, /pull, the
commands that start, stop and change the macro, and /update and /rollback. It runs on the PC the macro runs on, in the same logged-in desktop session (it reads the macro's files and grabs its screen),
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
from typing import Any, Awaitable, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import magic_numbers as cfg  # noqa: E402

import discord  # noqa: E402
from discord import app_commands  # noqa: E402

from bot import bot_lib  # noqa: E402

log = logging.getLogger("adoptme-bot")


async def say(interaction: discord.Interaction, text: Optional[str] = None, **kwargs) -> Any:
    """Follow up on a command with `text`, cut to fit a message first (bot_lib.clip_message()): a message over Discord's limit is refused, and the command would
    end with nothing said. An interaction can only be followed up for 15 minutes (a long /update or /pull outlasts that): after that, or when Discord says the
    token is no good, the answer goes to the channel itself (bot_lib.followup_needs_the_channel()). The message that was sent."""
    text = bot_lib.clip_message(text) if text else text
    if not interaction.is_expired():
        try:
            return await interaction.followup.send(text, **kwargs)  # type: ignore[arg-type]
        except discord.HTTPException as error:
            if not bot_lib.followup_needs_the_channel(error.status, False):
                raise
    channel = interaction.channel or await interaction.client.fetch_channel(interaction.channel_id or 0)
    return await channel.send(text, **kwargs)  # type: ignore[union-attr,arg-type]


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


def grab_roblox_jpeg() -> tuple[bytes, bool]:
    """A small JPEG of the Roblox window (the whole monitor if there is none), BOT_RVIEW_WIDTH wide, and whether it looks blank - for /rview, which sends one every second."""
    import cv2
    import mss
    import numpy as np
    with mss.MSS() as sct:
        shot = sct.grab(roblox_region(sct.monitors[1]))
    picture = np.frombuffer(shot.bgra, np.uint8).reshape(shot.height, shot.width, 4)[:, :, :3]
    if shot.width > cfg.BOT_RVIEW_WIDTH:
        picture = cv2.resize(picture, (cfg.BOT_RVIEW_WIDTH, round(shot.height * cfg.BOT_RVIEW_WIDTH / shot.width)), interpolation=cv2.INTER_AREA)
    done, encoded = cv2.imencode(".jpg", picture, [cv2.IMWRITE_JPEG_QUALITY, cfg.BOT_RVIEW_JPEG_QUALITY])
    if not done:
        raise RuntimeError("the picture could not be encoded")
    return encoded.tobytes(), bot_lib.looks_blank(shot.rgb)


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
    the game). Best effort: main.py's release_all_inputs() lets go of the same keys (cfg.HELD_KEYS, and the ones it knows are down), and a button is only released if it is
    down, since a right button released with none pressed is a click."""
    try:
        import pydirectinput
    except ImportError:
        return
    key_up = getattr(pydirectinput.keyUp, "__wrapped__", pydirectinput.keyUp)       # (without the fail-safe check: see main.unchecked())
    mouse_up = getattr(pydirectinput.mouseUp, "__wrapped__", pydirectinput.mouseUp)
    for key in cfg.HELD_KEYS:
        try:
            key_up(key)
        except Exception:
            pass
    if os.name == "nt":
        import ctypes
        for button, code in (("left", 0x01), ("right", 0x02)):
            try:
                if ctypes.windll.user32.GetAsyncKeyState(code) & 0x8000:  # type: ignore[attr-defined]
                    mouse_up(button=button)
            except Exception:
                pass


def launch_macro(config: bot_lib.Config, loop: bool = True) -> int:
    """Start `main.py` (the repository's, with the configured Python; `--loop` if `loop`) as a process of its own, in this desktop session; what it prints on its
    error output is appended to debug/logs/macro_stderr.log. Returns its process id."""
    folder = config.repo_dir or ROOT
    os.makedirs(cfg.LOGS_DIR, exist_ok=True)                           # (a Refresh debug, or a fresh checkout, leaves no folder to write the error output into)
    with open(os.path.join(cfg.LOGS_DIR, "macro_stderr.log"), "ab") as errors:
        process = subprocess.Popen([config.python or sys.executable, os.path.join(folder, "main.py"), *(["--loop"] if loop else [])], cwd=folder,
                                   stdout=subprocess.DEVNULL, stderr=errors,
                                   creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))     # (Ctrl+C in the bot's console is not sent to the macro too)
    return process.pid


def kill_macro(pids: list[int]) -> None:
    """End the processes, hard (the fallback of a stop that did not end the macro), then release what it may have held."""
    for pid in pids:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=15)       # (/T: its child processes too)
            else:
                import signal
                os.kill(pid, signal.SIGKILL)
        except (OSError, subprocess.SubprocessError):
            pass
    release_inputs()


def current_macro_pids() -> Optional[list[int]]:
    """The ids of the macro's processes, or None when the processes of this PC cannot be listed - which is not the same as "not running"."""
    processes = list_processes()
    return None if processes is None else bot_lib.macro_pids(processes)


async def running_pids() -> list[int]:
    """The ids of the macro's processes. A listing that fails is tried again (BOT_PROCESS_LIST_TRIES times, a second apart); one that keeps failing is an error
    said in the channel (bot_lib.Said), not "the macro is not running": nothing is stopped, started or killed on a guess."""
    for attempt in range(cfg.BOT_PROCESS_LIST_TRIES):
        pids = await asyncio.to_thread(current_macro_pids)
        if pids is not None:
            return pids
        await asyncio.sleep(1)
    raise bot_lib.Said("I can't list this PC's processes (psutil is not installed and PowerShell did not answer in time), so I can't tell whether the macro is running - nothing was done. "
                       "`pip install psutil` in the macro's environment fixes it.")


async def send_command(command: str, **details: Any) -> str:
    """Leave the macro `command` and wait up to BOT_COMMAND_ACK_WAIT seconds for it to answer that it took it. "" when it did; else what to tell the owner:
    the macro dropped it (and why), or did not look in time (its window may be stuck) and the command was withdrawn, so that it does not run later."""
    name = bot_lib.write_command(cfg.COMMANDS_DIR, command, time.time(), **details)
    deadline = time.time() + cfg.BOT_COMMAND_ACK_WAIT
    while True:
        answer = bot_lib.take_ack(cfg.COMMANDS_DIR, name)
        if answer is None and time.time() >= deadline:
            if bot_lib.withdraw_command(cfg.COMMANDS_DIR, name):
                return f"The macro did not take the command within {cfg.BOT_COMMAND_ACK_WAIT:g} s (its window may be stuck) - it was withdrawn and nothing was done."
            answer = bot_lib.take_ack(cfg.COMMANDS_DIR, name) or "taken"            # (taken just now)
        if answer is not None:
            return "" if answer == "taken" else f"The macro dropped the command ({answer.removeprefix('dropped: ')}) - nothing was done."
        await asyncio.sleep(cfg.BOT_COMMAND_ACK_POLL)


async def stop_macro(config: bot_lib.Config) -> str:
    """Ask the macro to exit (its cooperative stop: the loop ends, every key and the mouse are released, the window closes) and wait up to
    stop_timeout_seconds for its process to end; if it hasn't, kill it and release the inputs from here. Returns what happened, as a sentence.
    A listing of the processes that fails in the middle is waited through; if it still fails at the end nothing is claimed and nothing is killed."""
    if not await running_pids():
        return "The macro was not running."
    problem = await send_command("exit")
    deadline = time.time() + (0 if problem else config.stop_timeout_seconds)           # (a macro that does not answer is not waited for: it is killed)
    while time.time() < deadline:
        await asyncio.sleep(1)
        if await asyncio.to_thread(current_macro_pids) == []:
            return "The macro stopped (it released its keys and closed)."
    pids = await asyncio.to_thread(current_macro_pids)
    if pids is None:
        return "I asked the macro to exit, but can't list this PC's processes, so I can't tell whether it ended (or kill it if it did not) - look at the PC."
    if not pids:
        return "The macro stopped (it released its keys and closed)."
    await asyncio.to_thread(kill_macro, pids)
    bot_lib.clear_commands(cfg.COMMANDS_DIR)
    if problem:
        return f"{problem} The macro was killed and its keys and mouse buttons released."
    return f"The macro did not stop within {config.stop_timeout_seconds:g} s - it was killed and its keys and mouse buttons released."


async def open_macro(config: bot_lib.Config) -> str:
    """Start the macro with its window up and no loop going (`main.py`), unless it is running already."""
    if await running_pids():
        return "The macro is already running."
    await asyncio.to_thread(launch_macro, config, False)
    deadline = time.time() + cfg.BOT_START_CONFIRM
    while time.time() < deadline:
        await asyncio.sleep(2)
        if await asyncio.to_thread(current_macro_pids):                  # (a failed listing here is just "not yet")
            return "Started the macro (its window is up, no loop going - /loop starts the loop)."
    return "Started the macro, but its process is not there - see /log and macro_stderr.log."


async def stop_loop() -> tuple[bool, str]:
    """Ask the running macro to stop its loop (the [STOP] button); its window stays open. (Whether the macro was there, what to say.)"""
    if not await running_pids():
        return False, "The macro is not running."
    problem = await send_command("stop")
    if problem:
        return False, problem
    return True, "Asked the macro to stop (its keys are released; its window stays open - /exit closes it)."


async def start_macro(config: bot_lib.Config) -> str:
    """Start the macro's loop: launch `main.py --loop` if it is not running; if it is (its window may be open with the loop stopped), ask it to start
    the loop. Then wait up to BOT_START_CONFIRM seconds for status.json to be written, and say what was seen."""
    since = datetime.now()
    if await running_pids():
        if not bot_lib.loop_idle(bot_lib.read_json(bot_lib.status_path())):
            return "The macro's loop is running already."                       # (its status is fresh whatever a `start` did: it could not be told from a loop that began now)
        problem = await send_command("start")
        if problem:
            return problem
        what = "The macro was already running - asked it to start its loop."
    else:
        await asyncio.to_thread(launch_macro, config)
        what = "Started the macro."
    deadline = time.time() + cfg.BOT_START_CONFIRM
    while time.time() < deadline:
        await asyncio.sleep(2)
        if bot_lib.loop_started(bot_lib.read_json(bot_lib.status_path()), since):
            return what + " Its loop is running."
    return what + " No sign of its loop yet - see /status and /log."


LAUNCHING = 0             # the commands that are starting the macro now (/start, /loop): its process is not there yet, which is not a crash
UPDATING = False          # True while /update or /rollback is stopping, moving and starting the macro: the watcher must not take that for a crash
RESTART_BOT = False        # set by /update or /rollback when the bot's own code changed: main() starts the bot again once it has closed


@contextlib.contextmanager
def launching():
    """While the macro is being started, the watcher does not take the process that is not there yet for a crash."""
    global LAUNCHING
    LAUNCHING += 1
    try:
        yield
    finally:
        LAUNCHING -= 1


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


async def apply_commit(config: bot_lib.Config, move: list[str], old: str, say, loop_was_running: bool = True, moved=None) -> None:
    """The steps both /update and /rollback share, once the plan is made: stop the macro, move the repository (`move`: the git arguments), install
    the packages if requirements.txt changed, start the macro again if it was running (with its loop only if `loop_was_running`: a window that was open with
    no loop comes back the same), and ask for the bot's own restart if its code changed and still imports. `say` posts a message. A step that fails stops it
    there, with what to do. `moved` is called once the repository has moved (not before: a move that fails keeps the rollback target there was)."""
    global RESTART_BOT
    was_running = bool(await running_pids())
    if was_running:
        await say(await stop_macro(config))
    await git(config, *move)
    if moved is not None:
        moved()
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
        result = await start_macro(config) if loop_was_running else await open_macro(config)
        await say(result)
        if ("Its loop is running." not in result) if loop_was_running else (not result.startswith("Started the macro (")):
            errors = output_tail(os.path.join(cfg.LOGS_DIR, "macro_stderr.log"))
            await say(f"The macro does not look started. The end of its error output:\n```\n{errors}\n```\nUse /rollback to go back to {bot_lib.short(old)}.")
    else:
        await say("The macro was not running, so it was not started (/loop).")
    if bot_lib.needs_bot_restart(changed):
        code, output = await asyncio.to_thread(run_command, [config.python or sys.executable, "-c", "import bot.discord_bot"], repo_folder(config), cfg.BOT_IMPORT_TIMEOUT)
        if code != 0:
            await say(f"The bot's own code changed, but it does not import (exit {code}) - the bot is **not** restarted and goes on with the old code in memory, which would not "
                      f"start again. Use /rollback to go back to {bot_lib.short(old)}.\n```\n{output[-1200:]}\n```")
            return
        await say("The bot's own code changed - the bot restarts now.")
        RESTART_BOT = True


class ConfirmView(discord.ui.View):
    """Yes and Cancel buttons that only the owner in the one channel can press; `answer` is True, False, or None if nobody pressed one in time."""

    def __init__(self, config: bot_lib.Config, timeout: float, yes: str = "Copy it") -> None:
        super().__init__(timeout=timeout)
        self.config = config
        self.answer: Optional[bool] = None
        self.message: Any = None                                      # the message the buttons are on, to take them off when nobody pressed one in time
        self.yes.label = yes                                         # (the button the decorator made)

    async def on_timeout(self) -> None:
        if self.message is not None:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return bot_lib.is_allowed(self.config, interaction.user.id, interaction.channel_id or 0)

    async def finish(self, interaction: discord.Interaction, answer: bool) -> None:
        self.answer = answer
        await interaction.response.edit_message(view=None)
        self.stop()

    @discord.ui.button(label="Copy it", style=discord.ButtonStyle.green)
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.finish(interaction, True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.finish(interaction, False)


class OwnerOnlyTree(app_commands.CommandTree):
    """A command tree that obeys the owner in the one channel and nobody else (their commands get no answer at all)."""

    def __init__(self, client: discord.Client, config: bot_lib.Config) -> None:
        super().__init__(client)
        self.config = config

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return bot_lib.is_allowed(self.config, interaction.user.id, interaction.channel_id or 0)

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        """A command that raised: logged, and said in the channel - every command defers ("thinking..."), which would otherwise go on for ever."""
        if isinstance(error, app_commands.CheckFailure):
            return
        log.error("command failed: %s", error, exc_info=error)
        text = bot_lib.error_text(getattr(error, "original", None) or error)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(text)
            else:
                await interaction.response.send_message(text)
        except Exception:
            log.exception("the error could not be said in the channel either")


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
        await say(interaction, f"```\n{text}\n```")

    @command("screenshot", "The Roblox window right now")
    async def screenshot(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        try:
            png, blank = await asyncio.to_thread(grab_roblox_png)
        except Exception as error:
            await say(interaction, f"Couldn't grab the screen: {error}")
            return
        note = "The screen looks blank - the PC may be locked or asleep." if blank else ""
        name = "screenshot.png"
        if len(png) > cfg.BOT_MAX_UPLOAD_MB * 1048576:                 # (a large monitor: too big for Discord as a PNG, a JPEG of it is not)
            try:
                png, _ = await asyncio.to_thread(grab_roblox_jpeg)
            except Exception as error:
                await say(interaction, f"The screenshot was too big to send, and a smaller one could not be made: {error}")
                return
            name, note = "screenshot.jpg", (note + " " if note else "") + "(sent as a smaller JPEG: the PNG was too big)"
        await say(interaction, note, file=discord.File(io.BytesIO(png), name))

    rolling = {"on": False}                                        # one rolling view at a time

    @command("rview", "A rolling view: the Roblox window once a second for 30 seconds, in one message that updates")
    async def rview(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        if rolling["on"]:
            await say(interaction, "A rolling view is already running.")
            return
        rolling["on"] = True
        try:
            started = time.monotonic()
            shown = 0
            while True:
                elapsed = time.monotonic() - started
                left = max(0, round(cfg.BOT_RVIEW_SECONDS - elapsed))
                jpeg, blank = await asyncio.to_thread(grab_roblox_jpeg)
                note = f"Rolling view - {left} s left" + (" - the screen looks blank (the PC may be locked or asleep)" if blank else "")
                if len(jpeg) > cfg.BOT_MAX_UPLOAD_MB * 1048576:
                    note += " - this picture was too big to send"
                    attachments = []
                else:
                    attachments = [discord.File(io.BytesIO(jpeg), f"view{shown % 2}.jpg")]    # (a new name each time, so the picture shown is replaced)
                await interaction.edit_original_response(content=note, attachments=attachments)
                shown += 1
                if elapsed >= cfg.BOT_RVIEW_SECONDS:
                    break
                await asyncio.sleep(max(0.0, started + shown * cfg.BOT_RVIEW_INTERVAL - time.monotonic()))
            await interaction.edit_original_response(content="Rolling view over.")
        except Exception as error:
            log.exception("rolling view failed")
            with contextlib.suppress(Exception):
                await say(interaction, f"The rolling view stopped: {error}")
        finally:
            rolling["on"] = False

    @command("log", "The last lines of the run log")
    @app_commands.describe(lines="How many lines (at most the configured limit)")
    async def log_command(interaction: discord.Interaction, lines: int = 20) -> None:
        await interaction.response.defer(thinking=True)
        text = bot_lib.tail_lines(bot_lib.run_log_path(), max(1, min(lines, config.log_lines_max)))
        if text is None:
            await say(interaction, "There is no run log yet.")
        elif bot_lib.fits_a_message(text):
            await say(interaction, f"```\n{text}\n```")
        else:
            await say(interaction, file=discord.File(io.BytesIO(text.encode("utf-8")), "run_log_tail.txt"))

    @command("report", "The newest run chart")
    async def report(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        path = bot_lib.newest_report()
        if path is None:
            await say(interaction, "There is no run chart yet - one is written when a run stops.")
        else:
            await say(interaction, os.path.basename(path), file=discord.File(path))

    @command("results", "Best, worst and average candy corn of each minigame")
    async def results(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        await say(interaction, f"```\n{bot_lib.results_text(bot_lib.read_results(bot_lib.results_path()))}\n```")

    def remember(desired: str) -> None:
        state = client.bot_state  # type: ignore[attr-defined]
        state.desired_state, state.crash_reported = desired, False
        bot_lib.save_state(bot_lib.state_path(), state)

    @command("start", "Start the macro with its window up and no loop going")
    async def start(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        remember("open")
        with launching():
            text = await open_macro(config)
        await say(interaction, text)

    @command("loop", "Start the macro's loop (launches the macro if it is not running)")
    async def loop(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        remember("running")
        with launching():
            text = await start_macro(config)
        await say(interaction, text)

    @command("stop", "Stop the macro's loop (its window stays open)")
    async def stop(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        was_there, text = await stop_loop()
        if was_there:
            remember("open")                                         # (the window stays, with no loop: a restart of it would not start one)
        await say(interaction, text)

    @command("exit", "Close the macro: its loop is stopped, its keys are released and its window closed")
    async def exit_macro(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        remember("stopped")
        await say(interaction, await stop_macro(config))

    async def ask_macro(interaction: discord.Interaction, command_name: str, said: str, idle_only: bool = False) -> None:
        """Leave the running macro `command_name` (its window does it) and say `said`; with `idle_only` not while its loop runs."""
        await interaction.response.defer(thinking=True)
        if not await running_pids():
            await say(interaction, "The macro isn't running - /start it first (it is done by its window).")
            return
        if idle_only and not bot_lib.loop_idle(bot_lib.read_json(bot_lib.status_path())):
            await say(interaction, "The macro's loop is running - /stop it first.")
            return
        await say(interaction, await send_command(command_name) or said)

    @command("rejoin", "Have the macro leave the game and rejoin it (stops its loop first)")
    async def rejoin(interaction: discord.Interaction) -> None:
        await ask_macro(interaction, "rejoin", "Asked the macro to leave and rejoin. Its loop is stopped; /loop it again once it is back in the game.")

    @command("setup", "Have the macro run Setup (stops its loop first)")
    async def setup(interaction: discord.Interaction) -> None:
        await ask_macro(interaction, "setup", "Asked the macro to run Setup. Its loop is stopped; /loop it again once Setup is done.")

    @command("trade_hub", "Have the macro travel to the Trading Hub (stops its loop first)")
    async def trade_hub(interaction: discord.Interaction) -> None:
        await ask_macro(interaction, "trade_hub", "Asked the macro to travel to the Trading Hub. Its loop is stopped; /leave_trade_hub and /loop when you want it back.")

    @command("leave_trade_hub", "Have the macro leave the Trading Hub for a regular server")
    async def leave_trade_hub(interaction: discord.Interaction) -> None:
        await ask_macro(interaction, "leave_trade_hub", "Asked the macro to leave the Trading Hub for a regular server. Its loop is stopped; /loop it again once it is back.")

    @command("refresh_debug", "Empty the macro's debug folder (the game config stays); not while its loop runs")
    async def refresh_debug(interaction: discord.Interaction) -> None:
        await ask_macro(interaction, "refresh_debug", "Asked the macro to empty its debug folder (everything but the game config).", idle_only=True)

    async def change_switch(interaction: discord.Interaction, kind: str, name: str, value: bool) -> None:
        """Ask the running macro to change a switch (the command file) and say what the game config shows a moment later."""
        await interaction.response.defer(thinking=True)
        if not await running_pids():
            await say(interaction, "The macro isn't running - a switch can only be changed while its window is open (or by hand in the window).")
            return
        problem = await send_command("set", **bot_lib.switch_command(kind, name, value))
        if problem:
            await say(interaction, problem)
            return
        if name == cfg.SHOW_DEBUG_LINES_OPTION:
            await say(interaction, f"Asked the macro to turn {name} {'on' if value else 'off'} (it is not kept in the config, so it can't be checked).")
            return
        for _ in range(5):
            await asyncio.sleep(1)
            if bot_lib.switch_states(bot_lib.read_json(cfg.GAME_CONFIG_PATH)).get(f"{kind}:{name}") == value:
                await say(interaction, f"{name} is now {'on' if value else 'off'}.")
                return
        await say(interaction, f"Asked the macro to turn {name} {'on' if value else 'off'}, but the config doesn't show it yet.")

    need_choices = [app_commands.Choice(name=name, value=name) for name in cfg.NEED_NAMES]
    option_choices = [app_commands.Choice(name=name, value=name) for name in (*cfg.OPTION_SWITCHES, cfg.SHOW_DEBUG_LINES_OPTION)]

    update_lock = asyncio.Lock()

    async def change_code(interaction: discord.Interaction, rollback: bool) -> None:
        """/update and /rollback: make the plan from what git says (nothing is changed unless it says go), then apply_commit() - one at a time."""
        global UPDATING
        await interaction.response.defer(thinking=True)
        if update_lock.locked():
            await say(interaction, "An update or rollback is already going on.")
            return
        async with update_lock:
            try:
                await plan_and_apply(interaction, rollback)
            except Exception:                                      # (a message that could not be sent, git gone wrong: said in the log, and the lock is let go)
                log.exception("/update or /rollback failed")
                with contextlib.suppress(Exception):
                    await say(interaction, "**The update failed** - see the bot's log. The macro may be stopped (/status).")
            finally:
                UPDATING = False
        if RESTART_BOT:
            await client.close()

    async def plan_and_apply(interaction: discord.Interaction, rollback: bool) -> None:
        global UPDATING
        state = client.bot_state  # type: ignore[attr-defined]
        try:
            plan, move, head = await asyncio.to_thread(bot_lib.make_plan, git_runner(config), config.update_remote, config.update_branch, state.previous_commit, rollback)
        except RuntimeError as error:
            await say(interaction, f"Nothing was changed. {error}")
            return
        if plan.kind not in ("update", "rollback"):
            await say(interaction, f"```\n{plan.text}\n```")
            return
        pids = await running_pids()               # (before anything is changed: if the processes cannot be listed, whether the macro runs is not known, and nothing is done)
        loop_running = bool(pids) and not bot_lib.loop_idle(bot_lib.read_json(bot_lib.status_path()))
        if not rollback:                          # an update says what it brings (the commits, the lines changed in the Python files) and waits for a yes; a rollback is the way back, and does not
            view = ConfirmView(config, cfg.BOT_UPDATE_CONFIRM_TIMEOUT, yes="Update")
            view.message = await say(interaction, f"```\n{plan.text}\n```", view=view)
            await view.wait()
            if not view.answer:
                await say(interaction, "Nothing was changed: " + ("cancelled." if view.answer is False else "no answer in time."))
                return
        else:
            await say(interaction, f"```\n{plan.text}\n```")
        if pids:
            state.desired_state = "running" if loop_running else "open"      # (what the macro is, so that what comes back - and a restart of it - is the same)

        def moved() -> None:
            state.previous_commit = None if rollback else head
            bot_lib.save_state(bot_lib.state_path(), state)
        bot_lib.save_state(bot_lib.state_path(), state)
        UPDATING = True
        try:
            await apply_commit(config, move, head, lambda text: say(interaction, text), loop_running, moved)
        except RuntimeError as error:
            await say(interaction, f"**Stopped:** {error}\nThe macro may be stopped - see /status.")
        finally:
            UPDATING = False

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
        await say(interaction, f"```\n{text}\n```")

    pull_folders = [app_commands.Choice(name=name, value=name) for name in cfg.PULL_SOURCES]

    @command("pull", "Copy something from the debug folder into OneDrive (the newest of a folder, or one you pick)")
    @app_commands.choices(folder=pull_folders)
    @app_commands.describe(folder="Which folder of debug/", item="Which one - leave out for the newest; * is everything in the folder")
    async def pull(interaction: discord.Interaction, folder: app_commands.Choice[str], item: Optional[str] = None) -> None:
        await interaction.response.defer(thinking=True)
        if not config.onedrive_dir or not os.path.isdir(config.onedrive_dir):
            await say(interaction, "onedrive_dir is not set, or is not a folder on this PC - there is nowhere to copy to.")
            return
        try:
            path, name = bot_lib.resolve_pull(folder.value, item)
            files = await asyncio.to_thread(bot_lib.pull_files, path, name)
            size = await asyncio.to_thread(bot_lib.files_size_mb, files)
            if cfg.BOT_PULL_CONFIRM_MB < size <= cfg.BOT_MAX_PULL_MB:
                view = ConfirmView(config, cfg.BOT_PULL_CONFIRM_TIMEOUT)
                view.message = await say(interaction, f"`{name}` is {bot_lib.size_text(size)} ({len(files)} file{'s' if len(files) != 1 else ''}). Copy it to OneDrive?", view=view)
                await view.wait()
                if not view.answer:
                    await say(interaction, "Nothing copied: " + ("cancelled." if view.answer is False else "no answer in time."))
                    return
            megabytes = await asyncio.to_thread(bot_lib.copy_pull, files, config.onedrive_dir, folder.value, cfg.BOT_MAX_PULL_MB)
        except (ValueError, OSError) as error:
            await say(interaction, f"Nothing copied: {error}")
            return
        await say(interaction, f"Copied `{name}` ({len(files)} file{'s' if len(files) != 1 else ''}, {bot_lib.size_text(megabytes)}) to OneDrive, in `{folder.value}/`. The OneDrive app on the PC uploads it from there.")

    @pull.autocomplete("item")
    async def pull_items(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        source = getattr(interaction.namespace, "folder", None)
        names = [bot_lib.PULL_ALL, *bot_lib.pull_entries(source or "")]
        return [app_commands.Choice(name=("* (everything in the folder)" if n == bot_lib.PULL_ALL else n)[:100], value=n) for n in names if current.lower() in n.lower()][:25]

    @command("captures", "The newest minigame captures on the PC (/pull minigame_captures copies one to OneDrive)")
    async def captures(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        await say(interaction, f"```\n{bot_lib.captures_text(bot_lib.captures_dir())}\n```")

    background: list[asyncio.Task] = []                                # (kept: a task nobody holds can be collected while it runs)

    async def setup_hook() -> None:
        task = client.loop.create_task(watch(client, config))
        background.append(task)
        task.add_done_callback(task_ended)

    client.setup_hook = setup_hook  # type: ignore[method-assign]
    synced = False

    @client.event
    async def on_ready() -> None:
        nonlocal synced
        log.info("ready as %s", client.user)
        if synced:                                                     # (on_ready comes again after every reconnect; the commands are registered once)
            return
        synced = True
        try:
            await tree.sync(guild=guild)
        except (app_commands.CommandSyncFailure, discord.HTTPException) as error:
            log.exception("syncing the slash commands failed")
            await tell_channel(client, config, f"**The slash commands could not be registered:** {error}\nThe ones Discord already has still work; a new or changed command will not show until this is fixed (see the bot's console).")

    client.tree = tree  # type: ignore[attr-defined]       # (kept, so the commands can be listed)
    return client


async def post(channel: discord.abc.Messageable, text: str, path: Optional[str] = None) -> None:
    """Send `text` to the channel, with the file at `path` if there is one and it is small enough for Discord. A file that cannot be read (a Refresh debug
    deleted it) or that Discord refuses is left out, and the text says so: the alert is worth more than its picture."""
    try:
        if path and os.path.getsize(path) <= cfg.BOT_MAX_UPLOAD_MB * 1048576:
            await channel.send(bot_lib.clip_message(text), file=discord.File(path))
            return
    except (OSError, discord.HTTPException):
        log.exception("posting %s with its file failed - sending the text alone", path)
        text += "\n(its picture could not be sent)"
    await channel.send(bot_lib.clip_message(text))


async def attempt(what: str, sending: Awaitable[Any]) -> bool:
    """Await a post; False (logged) if it fails, so that one post that cannot be made does not stop the rest of a look at the macro's files."""
    try:
        await sending
        return True
    except Exception:
        log.exception("posting %s failed", what)
        return False


def task_ended(task: asyncio.Task) -> None:
    """Done-callback of the watcher: a watcher that ended by an exception is logged (it would otherwise only show when the task is collected)."""
    if not task.cancelled() and task.exception() is not None:
        log.error("the watcher ended", exc_info=task.exception())


async def find_channel(client: discord.Client, channel_id: int) -> Optional[discord.abc.Messageable]:
    """The bot's channel: from the cache, else fetched, trying `BOT_CHANNEL_TRIES` times with a doubling wait for a network or Discord hiccup. A channel that is
    not there or not allowed (NotFound, Forbidden) is not retried - the id or the permissions are wrong. None when it could not be had; said in the log."""
    channel = client.get_channel(channel_id)
    if channel is not None:
        return channel  # type: ignore[return-value]
    for attempt in range(cfg.BOT_CHANNEL_TRIES):
        try:
            return await client.fetch_channel(channel_id)  # type: ignore[return-value]
        except (discord.NotFound, discord.Forbidden) as error:
            log.error("channel %s cannot be used (%s) - check channel_id in bot_config.json and the bot's permissions there", channel_id, error)
            return None
        except (discord.HTTPException, OSError) as error:
            log.warning("fetching channel %s failed (%s), try %d of %d", channel_id, error, attempt + 1, cfg.BOT_CHANNEL_TRIES)
            if attempt + 1 < cfg.BOT_CHANNEL_TRIES:
                await asyncio.sleep(cfg.BOT_CHANNEL_RETRY_WAIT * 2 ** attempt)
    log.error("channel %s could not be fetched in %d tries - the watching (alerts, charts, heartbeat) is off; commands still work", channel_id, cfg.BOT_CHANNEL_TRIES)
    return None


async def tell_channel(client: discord.Client, config: bot_lib.Config, text: str) -> None:
    """Post `text` in the bot's channel if it can be had; a failure is logged, not raised (this is for reporting a problem)."""
    channel = await find_channel(client, config.channel_id)
    if channel is None:
        return
    try:
        await channel.send(bot_lib.clip_message(text))  # type: ignore[attr-defined]
    except (discord.HTTPException, OSError):
        log.exception("could not post to the channel")


async def watch(client: discord.Client, config: bot_lib.Config) -> None:
    """Every BOT_WATCH_INTERVAL seconds look at what the macro wrote and post what is new: failures (with their screenshot), a status that has gone
    stale (and its recovery), run charts, a heartbeat, a macro that is gone. A failure inside a look is logged and the watching
    goes on. What it has dealt with is kept in bot_state.json, so a restart neither posts it again nor misses what came while it was down."""
    await client.wait_until_ready()
    channel = await find_channel(client, config.channel_id)
    if channel is None:
        return
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
    try:
        lines, state.log_offset = bot_lib.read_new_lines(bot_lib.run_log_path(), state.log_offset)
    except OSError as error:                       # (locked by OneDrive or a virus scanner, say: the place in the log is kept, and the owner is told once)
        lines = []
        if not state.log_unreadable and await attempt("the unreadable log", channel.send(bot_lib.clip_message(f"**The run log could not be read** ({error}) - failures are not seen until it can be (the place in it is kept)."))):
            state.log_unreadable = True
    else:
        if state.log_unreadable and await attempt("the readable log", channel.send("The run log can be read again.")):
            state.log_unreadable = False
    for failure in filter(None, (bot_lib.parse_failure(line) for line in lines)):
        post_now, held = limiter.check(now)
        if post_now:
            await attempt("a failure", post(channel, bot_lib.failure_text(failure, held), bot_lib.failure_screenshot_path(failure)))
    unsaid = limiter.flush(now)                    # failures held back for the rate limit, with no later one to carry the count
    if unsaid:
        await attempt("the held failures", channel.send(f"{unsaid} more failure{'s' if unsaid != 1 else ''} since the last post - see /log."))
    status = bot_lib.read_json(bot_lib.status_path())
    stale, text = bot_lib.stale_alert(state.stale, status, datetime.now(), config.stale_status_minutes)
    if not text or await attempt("the stale-status alert", channel.send(bot_lib.clip_message(text))):
        state.stale = stale                        # (kept as it was if the alert could not be sent, so that the next look says it again)
    charts = bot_lib.run_charts()
    there = {os.path.basename(chart) for chart in charts}
    state.charts = [name for name in state.charts if name in there]    # (a chart that is gone - Refresh debug - is forgotten, so that a new run with the same number is posted)
    for chart in charts:
        if os.path.basename(chart) not in state.charts and now - os.path.getmtime(chart) >= cfg.BOT_FILE_SETTLE:
            if await attempt("a run chart", post(channel, f"Run chart {os.path.basename(chart)}", chart)):
                state.charts.append(os.path.basename(chart))
    if bot_lib.crash_check_due(state.desired_state, UPDATING or LAUNCHING > 0):
        running = await asyncio.to_thread(macro_process_running)
        state.crash_reported, event = bot_lib.crash_check(state.desired_state, running, state.crash_reported)
        if event == "alert":
            allowed, history = bot_lib.restart_allowed(state.restarts, now, config.auto_restart_max_per_hour) if config.auto_restart else (False, state.restarts)
            if allowed:
                await attempt("the restart notice", channel.send("**The macro's process is gone** while it should be running - restarting it."))
                try:
                    await asyncio.to_thread(launch_macro, config, state.desired_state == "running")      # (with its loop only if the loop was wanted)
                except OSError as error:                  # (the restart is counted only once it has been made: a launch that fails does not use one up)
                    await attempt("the failed restart", channel.send(bot_lib.clip_message(f"**The macro could not be started again:** {error}")))
                else:
                    state.restarts = history
                    state.crash_reported = False
            else:
                limit = " (the limit of automatic restarts this hour is used)" if config.auto_restart else ""
                await attempt("the crash alert", channel.send(f"**The macro's process is gone** while it should be running{limit}. /start or /loop starts it again."))
        elif event == "recovered":
            await attempt("the recovery notice", channel.send("The macro's process is running again."))
    if bot_lib.heartbeat_due(state.last_heartbeat, config.heartbeat_hours, now):
        running = await asyncio.to_thread(macro_process_running)
        if await attempt("the heartbeat", channel.send(bot_lib.clip_message("Heartbeat\n```\n" + bot_lib.status_text(status, datetime.now(), config.stale_status_minutes, running) + "\n```"))):
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
