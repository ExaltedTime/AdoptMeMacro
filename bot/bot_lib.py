"""The logic of the Discord bot (discord_bot.py) that needs neither Discord nor Windows: reading the config, deciding who may give
orders, and turning the files the macro writes (status.json, run_log.txt, results.jsonl, the run charts) into what is posted.
Plain functions, so tests/test_bot.py can run them anywhere. See docs/ARCHITECTURE.md#discord-bot."""
import glob
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import magic_numbers as cfg

DISCORD_MESSAGE_LIMIT = 2000
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


class ConfigError(Exception):
    """bot_config.json is missing, unreadable or wrong; the message says what to fix."""


@dataclass(frozen=True)
class Config:
    token: str
    owner_id: int
    channel_id: int
    guild_id: Optional[int]
    stale_status_minutes: float
    log_lines_max: int
    onedrive_dir: str            # "": the capture zips are not copied anywhere
    onedrive_keep_days: float
    heartbeat_hours: float       # 0: no heartbeat
    repo_dir: str                # "": the folder this repository is in
    python: str                  # "": the Python that runs the bot
    stop_timeout_seconds: float
    auto_restart: bool
    auto_restart_max_per_hour: int


REQUIRED = ("token", "owner_id", "channel_id")
DEFAULTS: dict[str, Any] = {"guild_id": None, "stale_status_minutes": 5, "log_lines_max": 40, "onedrive_dir": "", "onedrive_keep_days": 14, "heartbeat_hours": 0,
            "repo_dir": "", "python": "", "stop_timeout_seconds": 30, "auto_restart": False, "auto_restart_max_per_hour": 3}


def load_config(path: str) -> Config:
    """The bot's settings from the JSON file at `path` (its keys are listed in docs/ARCHITECTURE.md#discord-bot); ConfigError with what is wrong if it can't be used."""
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        raise ConfigError(f"{path} not found - create it with the bot's settings (docs/ARCHITECTURE.md#discord-bot lists the keys)") from None
    except (OSError, ValueError) as error:
        raise ConfigError(f"{path} can't be read: {error}") from None
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must hold a JSON object")
    missing = [key for key in REQUIRED if raw.get(key) in (None, "")]
    if missing:
        raise ConfigError(f"{path} lacks: {', '.join(missing)}")
    values = {**DEFAULTS, **raw}
    for key in ("owner_id", "channel_id"):
        if not isinstance(values[key], int) or isinstance(values[key], bool):
            raise ConfigError(f"{key} must be a number (a Discord ID), not {values[key]!r}")
    if values["guild_id"] is not None and (not isinstance(values["guild_id"], int) or isinstance(values["guild_id"], bool)):
        raise ConfigError("guild_id must be a number (a Discord ID) or left out")
    if not isinstance(values["token"], str):
        raise ConfigError("token must be a string")
    if not isinstance(values["stale_status_minutes"], (int, float)) or values["stale_status_minutes"] <= 0:
        raise ConfigError("stale_status_minutes must be a positive number")
    if not isinstance(values["log_lines_max"], int) or values["log_lines_max"] < 1:
        raise ConfigError("log_lines_max must be a whole number of at least 1")
    if not isinstance(values["onedrive_dir"], str):
        raise ConfigError("onedrive_dir must be a folder path (a string), or left out")
    for key in ("onedrive_keep_days", "heartbeat_hours"):
        if not isinstance(values[key], (int, float)) or isinstance(values[key], bool) or values[key] < 0:
            raise ConfigError(f"{key} must be a number, 0 or more")
    for key in ("repo_dir", "python"):
        if not isinstance(values[key], str):
            raise ConfigError(f"{key} must be a path (a string), or left out")
    if not isinstance(values["stop_timeout_seconds"], (int, float)) or isinstance(values["stop_timeout_seconds"], bool) or values["stop_timeout_seconds"] <= 0:
        raise ConfigError("stop_timeout_seconds must be a positive number")
    if not isinstance(values["auto_restart"], bool):
        raise ConfigError("auto_restart must be true or false")
    if not isinstance(values["auto_restart_max_per_hour"], int) or isinstance(values["auto_restart_max_per_hour"], bool) or values["auto_restart_max_per_hour"] < 1:
        raise ConfigError("auto_restart_max_per_hour must be a whole number of at least 1")
    return Config(values["token"], values["owner_id"], values["channel_id"], values["guild_id"],
                  float(values["stale_status_minutes"]), values["log_lines_max"], values["onedrive_dir"].strip(),
                  float(values["onedrive_keep_days"]), float(values["heartbeat_hours"]),
                  values["repo_dir"].strip(), values["python"].strip(), float(values["stop_timeout_seconds"]),
                  values["auto_restart"], values["auto_restart_max_per_hour"])


def is_allowed(config: Config, user_id: int, channel_id: int) -> bool:
    """Whether a command from `user_id` in `channel_id` is obeyed: only the owner, only in the one channel. Everyone else is ignored."""
    return user_id == config.owner_id and channel_id == config.channel_id


# --- where the macro writes things (the paths of magic_numbers.py) ---

def status_path() -> str:
    return cfg.STATUS_PATH


def run_log_path() -> str:
    return cfg.RUN_LOG_PATH


def results_path() -> str:
    return os.path.join(cfg.RUNS_DIR, "results", "results.jsonl")


def newest_report(runs_dir: str = "") -> Optional[str]:
    """The most recently written run chart (RUNS_DIR/run<N>.png), or None if there is none."""
    charts = glob.glob(os.path.join(runs_dir or cfg.RUNS_DIR, "run*.png"))
    return max(charts, key=os.path.getmtime) if charts else None


# --- /status ---

def read_json(path: str) -> Optional[dict[str, Any]]:
    """The JSON object in the file, or None if it isn't there or isn't readable (the macro replaces it atomically, so a half-written file is rare)."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def age_text(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"{seconds} s"
    if seconds < 90 * 60:
        return f"{round(seconds / 60)} min"
    return f"{seconds // 3600} h {seconds % 3600 // 60:02d} min"


def counts_text(counts: dict[str, int]) -> str:
    """`{"hungry": 20, "sick": 3}` as `23 (sick 3, hungry 20)`: the total, then each need, most first."""
    if not counts:
        return "0"
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return f"{sum(counts.values())} (" + ", ".join(f"{name} {count}" for name, count in ordered) + ")"


def status_text(status: Optional[dict[str, Any]], now: datetime, stale_minutes: float, process_running: Optional[bool] = None) -> str:
    """The /status message from the parsed status.json (None: there is none yet). `process_running` is whether main.py was found running
    (None: not known); a status that has not been updated for `stale_minutes` is flagged, whatever else is known."""
    if status is None:
        return "No status yet - status.json is missing or unreadable. Has the macro run on this PC?"
    lines = []
    try:
        age = (now - datetime.strptime(str(status.get("updated")), TIME_FORMAT)).total_seconds()
    except ValueError:
        age = None
    if age is None:
        lines.append("Macro: status has no update time")
    elif age > stale_minutes * 60:
        lines.append(f"Macro: NOT UPDATING - last update {age_text(age)} ago (stuck, stopped or the PC is asleep)")
    else:
        lines.append(f"Macro: running - updated {age_text(age)} ago")
    if process_running is not None:
        lines.append("Process: main.py is running" if process_running else "Process: main.py is NOT running")
    if status.get("run") is not None:
        lines.append(f"Run {status['run']}, started {status.get('started')}, {status.get('cycles', 0)} cycles")
    lines.append(f"Resolved: {counts_text(status.get('resolved') or {})}")
    lines.append(f"Failed: {counts_text(status.get('failed') or {})}, {status.get('failures', 0)} failures in all")
    if status.get("disabled_needs"):
        lines.append("Disabled as stuck: " + ", ".join(status["disabled_needs"]))
    lines.append(f"Quiet: {status.get('quiet_minutes', 0)} min since a need was resolved")
    if status.get("last_resolved"):
        lines.append(f"Last resolved: {status['last_resolved'].get('need')} at {status['last_resolved'].get('time')}")
    if status.get("last_failure"):
        failure = status["last_failure"]
        lines.append(f"Last failure: {failure.get('time')} - {failure.get('reason')}")
    lines.append(f"Rejoins: {status.get('recoveries', 0)} (scheduled {status.get('scheduled_rejoins', 0)}, disconnects {status.get('disconnects', 0)}), "
                 f"focus resumes {status.get('focus_resumes', 0)}")
    if status.get("cpu_percent") is not None or status.get("memory_mb") is not None:
        lines.append(f"Script: CPU {status.get('cpu_percent')}%, memory {status.get('memory_mb')} MB")
    return "\n".join(lines)


# --- /results ---

def read_results(path: str) -> list[dict[str, Any]]:
    """The finished minigame runs in results.jsonl, oldest first; lines that aren't a JSON object are skipped."""
    found = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict) and entry.get("minigame"):
                    found.append(entry)
    except OSError:
        pass
    return found


def results_text(results: list[dict[str, Any]]) -> str:
    """The /results message: for each minigame its runs, the best, the worst and the average candy corn of those that were read (the number
    not read is said), the latest, and for Hauntlet 2 the deepest room."""
    if not results:
        return "No minigame results yet."
    lines = []
    for name in sorted({entry["minigame"] for entry in results}):
        runs = [entry for entry in results if entry["minigame"] == name]
        earned = [entry["earned"] for entry in runs if isinstance(entry.get("earned"), int)]
        label = cfg.MINIGAME_LABELS.get(name, name)
        if earned:
            line = f"{label}: {len(runs)} runs, best {max(earned)}, worst {min(earned)}, average {sum(earned) / len(earned):.0f} candy corn"
        else:
            line = f"{label}: {len(runs)} runs, candy corn not read in any"
        if len(earned) < len(runs):
            line += f" ({len(runs) - len(earned)} not read)"
        rooms = [entry["room"] for entry in runs if isinstance(entry.get("room"), int)]
        if rooms:
            line += f"; deepest room {max(rooms)}"
        latest = runs[-1]
        line += f"; latest {latest['earned'] if isinstance(latest.get('earned'), int) else 'not read'} at {latest.get('time')}"
        lines.append(line)
    return "\n".join(lines)


# --- /log ---

def tail_lines(path: str, count: int, limit: int = 0) -> Optional[str]:
    """The last `count` lines of the text file (read from its end, so a big log is cheap), or None if there is no file. At most
    `limit` bytes are read when given; the default is enough for `count` ordinary lines."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            window = limit or max(4096, count * 400)
            f.seek(max(0, size - window))
            data = f.read()
    except OSError:
        return None
    lines = data.decode("utf-8", errors="replace").splitlines()
    if size > len(data) and lines:
        lines = lines[1:]                                  # (the first line is cut in the middle)
    return "\n".join(lines[-count:])


def fits_a_message(text: str) -> bool:
    """Whether `text` goes in one Discord message (in a code block, which takes 8 characters more)."""
    return len(text) + 8 <= DISCORD_MESSAGE_LIMIT


# --- /screenshot ---

def looks_blank(rgb: bytes, sample: int = 20000) -> bool:
    """Whether a screen grab (raw RGB bytes) is black or one flat colour - a locked or sleeping PC gives that - judged from `sample` evenly spread bytes."""
    if not rgb:
        return True
    step = max(1, len(rgb) // sample)
    values = rgb[::step]
    return max(values) - min(values) < 8


# --- is the macro running ---

def main_py_in(command_lines: list[str]) -> bool:
    """Whether one of the process command lines is the macro (`... main.py ...`), not the bot itself."""
    return any("main.py" in line and "discord_bot" not in line for line in command_lines)


# --- alerts: what the macro writes that the bot watches (Phase 2) ---

@dataclass(frozen=True)
class Failure:
    """A `FAILURE:` line of run_log.txt: when, in which run, why, and the screenshot's path relative to debug/ (None if it has none)."""
    time: str
    run: Optional[int]
    reason: str
    screenshot: Optional[str]


FAILURE_LINE = re.compile(r"^\[(?P<time>[^\]]+)\] \[RUN (?P<run>\w+)\] FAILURE: (?P<detail>.*?)(?: \(screenshot: (?P<shot>[^)]+)\))?$")


def parse_failure(line: str) -> Optional[Failure]:
    """The failure a run-log line records, or None if it is another kind of line."""
    found = FAILURE_LINE.match(line.strip())
    if found is None:
        return None
    run = int(found["run"]) if found["run"].isdigit() else None
    return Failure(found["time"], run, found["detail"], found["shot"])


def failure_screenshot_path(failure: Failure) -> Optional[str]:
    """The screenshot file of `failure` if it is one, still there, and inside the debug folder (a line is only text: it is not trusted with a path)."""
    if not failure.screenshot:
        return None
    path = os.path.normpath(os.path.join(cfg.DEBUG_DIR, failure.screenshot))
    inside = os.path.commonpath([path, os.path.normpath(cfg.DEBUG_DIR)]) == os.path.normpath(cfg.DEBUG_DIR)
    return path if inside and os.path.isfile(path) else None


def read_new_lines(path: str, offset: int) -> tuple[list[str], int]:
    """The whole lines added to the text file since byte `offset`, and the offset after them. A file that is shorter than `offset` was
    deleted or started again (Refresh debug does that): it is read from its start. A last line without its newline is left for next time."""
    try:
        size = os.path.getsize(path)
        if size < offset:
            offset = 0
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        return [], 0
    end = data.rfind(b"\n") + 1
    return data[:end].decode("utf-8", errors="replace").splitlines(), offset + end


def file_end(path: str) -> int:
    """The size of the file in bytes (0 if there is none): where to start reading its new lines from, to skip what it already holds."""
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


class RateLimiter:
    """At most one post per `gap` seconds. `check(now)` says whether to post now and how many were held back since the last post, so the post can
    say "and N more"; the held ones are not kept."""

    def __init__(self, gap: float) -> None:
        self.gap = gap
        self.last: Optional[float] = None
        self.held = 0

    def check(self, now: float) -> tuple[bool, int]:
        if self.last is None or now - self.last >= self.gap:
            held, self.held, self.last = self.held, 0, now
            return True, held
        self.held += 1
        return False, 0


def failure_text(failure: Failure, held: int = 0) -> str:
    text = f"**Failure** (run {failure.run if failure.run is not None else '?'}, {failure.time}): {failure.reason}"
    return text + (f"\n({held} more failure{'s' if held != 1 else ''} since the last post)" if held else "")


def is_stale(status: Optional[dict[str, Any]], now: datetime, stale_minutes: float) -> Optional[bool]:
    """Whether status.json has gone `stale_minutes` without an update; None when there is no status or no readable update time (nothing to say)."""
    if status is None:
        return None
    try:
        age = (now - datetime.strptime(str(status.get("updated")), TIME_FORMAT)).total_seconds()
    except ValueError:
        return None
    return age > stale_minutes * 60


def stale_alert(was_stale: bool, status: Optional[dict[str, Any]], now: datetime, stale_minutes: float) -> tuple[bool, Optional[str]]:
    """The new "is stale" state and what to post for the change: once when the macro stops updating, once when it is updating again."""
    stale = is_stale(status, now, stale_minutes)
    if stale is None or stale == was_stale:
        return was_stale, None
    if stale:
        assert status is not None
        return True, f"**The macro is not updating** - its last update was {status.get('updated')} (stuck, stopped, or the PC is asleep or locked)."
    return False, "The macro is updating again."


def heartbeat_due(last: Optional[float], hours: float, now: float) -> bool:
    """Whether a heartbeat post is due: `hours` is not 0 and that long has passed since `last` (None: never, so it is due at once)."""
    return hours > 0 and (last is None or now - last >= hours * 3600)


def captures_dir() -> str:
    return cfg.RUN_CAPTURES_DIR


def run_charts(runs_dir: str = "") -> list[str]:
    """The run charts (RUNS_DIR/run<N>.png) there are now, oldest first."""
    return sorted(glob.glob(os.path.join(runs_dir or cfg.RUNS_DIR, "run*.png")), key=os.path.getmtime)


# --- the bot's memory between starts (bot_state.json): where it got to ---

def state_path() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot_state.json")


@dataclass
class BotState:
    log_offset: Optional[int] = None                         # None: never started - begin at the end of the run log, not at its start
    charts: list[str] = field(default_factory=list)          # run charts already posted (or there when the bot first started)
    captures: list[str] = field(default_factory=list)        # capture folders already copied (or there when the bot first started)
    stale: bool = False
    last_heartbeat: Optional[float] = None
    desired_state: Optional[str] = None                      # "running" or "stopped" after the bot's /start and /stop, None before: a macro that is gone is a crash only if it should run
    crash_reported: bool = False                             # the macro's process is gone and that was posted
    restarts: list[float] = field(default_factory=list)      # when the bot restarted the macro by itself (the last hour counts)


def load_state(path: str) -> BotState:
    """The saved state, with the default for anything missing or of the wrong kind (no file: all defaults)."""
    data = read_json(path) or {}
    state = BotState()
    for key in ("log_offset", "last_heartbeat"):
        if isinstance(data.get(key), (int, float)) and not isinstance(data.get(key), bool):
            setattr(state, key, data[key])
    for key in ("charts", "captures"):
        if isinstance(data.get(key), list):
            setattr(state, key, [item for item in data[key] if isinstance(item, str)])
    if isinstance(data.get("restarts"), list):
        state.restarts = [item for item in data["restarts"] if isinstance(item, (int, float)) and not isinstance(item, bool)]
    for key in ("stale", "crash_reported"):
        if isinstance(data.get(key), bool):
            setattr(state, key, data[key])
    if data.get("desired_state") in ("running", "stopped"):
        state.desired_state = data["desired_state"]
    return state


def save_state(path: str, state: BotState) -> None:
    """Write the state to `path` all at once (a half-written file would read as no state: everything posted again)."""
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(state.__dict__, f)
    os.replace(temp, path)


# --- capture zips -> OneDrive (Phase 3) ---

CAPTURE_NAME = re.compile(r"^(hauntlet_2|ghost_gallery)_(\d{8}_\d{6})")


def capture_folders(captures_dir: str) -> list[str]:
    """The capture folders in `captures_dir` (named like a capture's), oldest first - by the time in the name, not the minigame's."""
    named = [(match[2], folder) for folder in glob.glob(os.path.join(captures_dir, "*"))
             if (match := CAPTURE_NAME.match(os.path.basename(folder))) and os.path.isdir(folder)]
    return [folder for _, folder in sorted(named)]


@dataclass(frozen=True)
class Capture:
    name: str            # the capture's folder name
    outcome: str
    zips: tuple[str, ...]
    megabytes: float


def ready_captures(captures_dir: str, done: set[str], now: float, settle: float) -> list[Capture]:
    """The captures in `captures_dir` that are ready to copy and not in `done`: `info.json` has an `outcome` (the recording is over) and there are
    zips, none changed in the last `settle` seconds (they are written on a thread, so they can be there before they are complete). Oldest first."""
    found = []
    for folder in capture_folders(captures_dir):
        name = os.path.basename(folder)
        if name in done:
            continue
        info = read_json(os.path.join(folder, "info.json"))
        zips = sorted(glob.glob(os.path.join(folder, "zips", "*.zip")))
        if not info or not info.get("outcome") or not zips:
            continue
        if any(now - os.path.getmtime(z) < settle for z in zips):
            continue
        found.append(Capture(name, str(info["outcome"]), tuple(zips), sum(os.path.getsize(z) for z in zips) / 1048576))
    return found


def copy_capture(capture: Capture, onedrive_dir: str) -> list[str]:
    """Copy the capture's zips into onedrive_dir/<capture name>/ (the OneDrive app uploads them from there). The names of the files copied."""
    target = os.path.join(onedrive_dir, capture.name)
    os.makedirs(target, exist_ok=True)
    for zipfile_path in capture.zips:
        shutil.copy2(zipfile_path, os.path.join(target, os.path.basename(zipfile_path)))
    return [os.path.basename(z) for z in capture.zips]


def capture_text(capture: Capture) -> str:
    label = cfg.MINIGAME_LABELS.get(next((n for n in cfg.MINIGAME_LABELS if capture.name.startswith(n)), ""), capture.name)
    return f"{label} capture `{capture.name}`: {capture.outcome} - {len(capture.zips)} zip{'s' if len(capture.zips) != 1 else ''}, {capture.megabytes:.0f} MB. Zips posted to OneDrive."


def old_copies(onedrive_dir: str, keep_days: float, now: float) -> list[str]:
    """The copied captures in `onedrive_dir` last changed more than `keep_days` ago - only folders named like a capture's (nothing else in the folder
    is ever listed), and none when `keep_days` is 0 (keep everything)."""
    if keep_days <= 0:
        return []
    old = []
    for folder in glob.glob(os.path.join(onedrive_dir, "*")):
        if CAPTURE_NAME.match(os.path.basename(folder)) and os.path.isdir(folder) and now - os.path.getmtime(folder) > keep_days * 86400:
            old.append(folder)
    return sorted(old)


def captures_text(captures_dir: str, done: set[str], limit: int = 10) -> str:
    """The /captures message: the newest `limit` captures on this PC, each with its outcome, zip count and size, and whether it is in OneDrive."""
    rows = []
    for folder in reversed(capture_folders(captures_dir)):
        name = os.path.basename(folder)
        info = read_json(os.path.join(folder, "info.json")) or {}
        zips = glob.glob(os.path.join(folder, "zips", "*.zip"))
        size = sum(os.path.getsize(z) for z in zips) / 1048576
        rows.append(f"{name}: {info.get('outcome', 'not finished')}, {len(zips)} zips, {size:.0f} MB, " + ("in OneDrive" if name in done else "not in OneDrive"))
        if len(rows) == limit:
            break
    return "\n".join(rows) if rows else "No minigame captures on this PC."


# --- control: start, stop, restart the macro (Phase 4) ---

def write_command(path: str, command: str, now: float, **details: Any) -> None:
    """Leave `command` (one of magic_numbers.COMMANDS) for the macro to find at `path`: {"command": ..., "time": ..., plus `details`}, written all at
    once. The macro removes the file once it has read it, and ignores a command that is old (COMMAND_MAX_AGE) or a `set` for a switch it doesn't have."""
    if command not in cfg.COMMANDS:
        raise ValueError(f"not a command the macro knows: {command!r}")
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump({"command": command, "time": now, **details}, f)
    os.replace(temp, path)


def switch_command(kind: str, name: str, value: bool) -> dict[str, Any]:
    """The details of a `set` command for the switch `name` (`kind` "need" or "option"); ValueError if there is no such switch."""
    valid = {"need": cfg.NEED_NAMES, "option": (*cfg.OPTION_SWITCHES, cfg.SHOW_DEBUG_LINES_OPTION)}.get(kind, ())
    if name not in valid:
        raise ValueError(f"no {kind} switch called {name!r}")
    return {"kind": kind, "name": name, "value": bool(value)}


# The game config's defaults for the switches (main.py's _read_game_config(): side quest on, the rest off), for a config that doesn't have them yet.
OPTION_DEFAULTS = {"side_quest_enabled": True}


def switch_states(config: Optional[dict[str, Any]]) -> dict[str, bool]:
    """Every switch the bot can change, as {"need:hungry": True, "option:side_quest": True, ...}, from the game config (None: not there - the
    defaults). `show_debug_lines` is not kept in the config and isn't listed."""
    config = config or {}
    flags = config["needs_enabled"] if isinstance(config.get("needs_enabled"), dict) else {}
    states = {f"need:{name}": bool(flags.get(name, name in cfg.ENABLED_NEEDS)) for name in cfg.NEED_NAMES}
    for name, key in cfg.OPTION_SWITCHES.items():
        states[f"option:{name}"] = bool(config.get(key, OPTION_DEFAULTS.get(key, False)))
    return states


def options_text(states: dict[str, bool]) -> str:
    """The /options message: the options, then the needs that are off and those that are on."""
    options = ", ".join(f"{key[7:]} {'on' if on else 'off'}" for key, on in states.items() if key.startswith("option:"))
    needs_off = [key[5:] for key, on in states.items() if key.startswith("need:") and not on]
    needs_on = [key[5:] for key, on in states.items() if key.startswith("need:") and on]
    return f"Options: {options}\nNeeds off: {', '.join(needs_off) or 'none'}\nNeeds on: {', '.join(needs_on) or 'none'}"


def macro_pids(processes: list[tuple[int, str]]) -> list[int]:
    """The ids of the processes of `processes` = (id, command line) that are the macro (`... main.py ...`, not the bot)."""
    return [pid for pid, line in processes if main_py_in([line])]


def parse_process_lines(output: str) -> list[tuple[int, str]]:
    """(id, command line) of each line `<id> <command line>` of `pgrep -af`'s or the PowerShell listing's output; other lines are skipped."""
    found = []
    for line in output.splitlines():
        head, _, rest = line.strip().partition(" ")
        if head.isdigit():
            found.append((int(head), rest))
    return found


def restart_allowed(history: list[float], now: float, per_hour: int) -> tuple[bool, list[float]]:
    """Whether the bot may restart the macro by itself once more: fewer than `per_hour` restarts in the last hour. Returns that and the history
    to keep (the last hour's, with this restart added when allowed)."""
    recent = [t for t in history if now - t < 3600]
    if len(recent) >= per_hour:
        return False, recent
    return True, recent + [now]


def crash_check(desired: Optional[str], running: Optional[bool], reported: bool) -> tuple[bool, Optional[str]]:
    """Whether the macro's process is gone when the bot's /start (and not /stop) says it should be there: the new "reported" state and "alert" or
    "recovered" when that changes. Nothing is known (None) when the process check can't tell, or `/start` was never used."""
    if desired != "running":
        return False, None                                   # (stopped on purpose, or the bot never started it: no crash to report)
    if running is None:
        return reported, None
    if not running and not reported:
        return True, "alert"
    if running and reported:
        return False, "recovered"
    return reported, None


def started_since(status: Optional[dict[str, Any]], since: datetime) -> bool:
    """Whether status.json was written at or after `since` - the macro that was just started is running its loop (it writes the status when it starts)."""
    try:
        return status is not None and datetime.strptime(str(status.get("updated")), TIME_FORMAT) >= since.replace(microsecond=0)
    except ValueError:
        return False
