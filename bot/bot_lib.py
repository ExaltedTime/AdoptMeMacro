"""The logic of the Discord bot (discord_bot.py) that needs neither Discord nor Windows: reading the config, deciding who may give
orders, and turning the files the macro writes (status.json, run_log.txt, results.jsonl, the run charts) into what is posted.
Plain functions, so tests/test_bot.py can run them anywhere. See docs/ARCHITECTURE.md#discord-bot."""
import contextlib
import glob
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

import magic_numbers as cfg

DISCORD_MESSAGE_LIMIT = 2000
MAX_LOG_LINE = 100                    # a line of the update plan's git log is cut to this
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
    heartbeat_hours: float       # 0: no heartbeat
    repo_dir: str                # "": the folder this repository is in
    python: str                  # "": the Python that runs the bot
    stop_timeout_seconds: float
    auto_restart: bool
    auto_restart_max_per_hour: int
    update_remote: str           # what /update pulls from: this remote's branch (the default: origin's main)
    update_branch: str


GIT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")      # a remote or branch name: nothing git could take for an option, no spaces
REQUIRED = ("token", "owner_id", "channel_id")
DEFAULTS: dict[str, Any] = {"guild_id": None, "stale_status_minutes": 5, "log_lines_max": 40, "onedrive_dir": "", "heartbeat_hours": 0,
            "repo_dir": "", "python": "", "stop_timeout_seconds": 30, "auto_restart": False, "auto_restart_max_per_hour": 3,
            "update_remote": "origin", "update_branch": "main"}


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
    for key in ("heartbeat_hours",):
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
    for key in ("update_remote", "update_branch"):
        if not isinstance(values[key], str) or not GIT_NAME.match(values[key]) or ".." in values[key]:
            raise ConfigError(f"{key} must be a name of letters, digits and . _ / - (not starting with a dash), not {values[key]!r}")
    return Config(values["token"], values["owner_id"], values["channel_id"], values["guild_id"],
                  float(values["stale_status_minutes"]), values["log_lines_max"], values["onedrive_dir"].strip(),
                  float(values["heartbeat_hours"]),
                  values["repo_dir"].strip(), values["python"].strip(), float(values["stop_timeout_seconds"]),
                  values["auto_restart"], values["auto_restart_max_per_hour"], values["update_remote"], values["update_branch"])


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
    if status.get("stopped"):
        lines.append(f"Macro: stopped at {status['stopped']} (on purpose - nothing is running)")
    elif age is None:
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


class Said(Exception):
    """An error whose message is for the channel as it is (error_text() does not dress it): "the processes cannot be listed", and the like."""


def followup_needs_the_channel(status: Optional[int], expired: bool) -> bool:
    """Whether an answer to a command has to go to the channel itself: the 15 minutes an interaction can be followed up for are over (`expired`), or Discord
    said the token is no good (HTTP 401) or the interaction is unknown (404). Any other failure is a real one."""
    return expired or status in (401, 404)


def error_text(error: BaseException) -> str:
    """What the channel is told when a command raised: the kind of error and its message, cut short (the details are in the bot's log); a `Said` as it is."""
    if isinstance(error, Said):
        return clip_message(str(error), 300)
    message = str(error).strip().replace("\n", " ")
    return clip_message(f"The command failed: {type(error).__name__}" + (f": {message}" if message else "") + " (see the bot's log)", 300)


def clip_message(text: str, limit: int = DISCORD_MESSAGE_LIMIT) -> str:
    """`text` cut to fit one Discord message (a longer one is refused, and the whole command with it): whole lines from the start, then "... (N more lines)";
    a code block stays closed."""
    if len(text) <= limit:
        return text
    fenced = text.startswith("```") and text.rstrip().endswith("```")
    body = text[text.index("\n") + 1:text.rstrip().rindex("```")].rstrip("\n") if fenced else text
    lines = body.split("\n")
    room = limit - (8 if fenced else 0) - 30                                 # (30: the note about what was cut)
    kept, used = [], 0
    for line in lines:
        line = line if len(line) <= room else line[:room - 1] + "…"
        if used + len(line) + 1 > room:
            break
        kept.append(line)
        used += len(line) + 1
    cut = f"... ({len(lines) - len(kept)} more lines)"
    body = "\n".join([*kept, cut])
    return f"```\n{body}\n```" if fenced else body


# --- /screenshot ---

def looks_blank(rgb: bytes, sample: int = 20000) -> bool:
    """Whether a screen grab (raw RGB bytes) is black or one flat colour - a locked or sleeping PC gives that - judged from `sample` evenly spread bytes."""
    if not rgb:
        return True
    step = max(1, len(rgb) // sample)
    values = rgb[::step]
    return max(values) - min(values) < 8


# --- is the macro running ---

MAIN_PY = re.compile(r"""(?:^|[\\/\s"'])main\.py(?=$|[\s"'])""")      # `main.py` as a whole file name: not test_main.py, main.pyc or xmain.py


def main_py_in(command_lines: list[str]) -> bool:
    """Whether one of the process command lines is the macro (`... main.py ...`, the file by that exact name), not the bot itself."""
    return any(MAIN_PY.search(line) and "discord_bot" not in line for line in command_lines)


# --- alerts: what the macro writes that the bot watches ---

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
    try:
        inside = os.path.commonpath([path, os.path.normpath(cfg.DEBUG_DIR)]) == os.path.normpath(cfg.DEBUG_DIR)
    except ValueError:                                  # (a path on another drive: Windows has no common path with it)
        return None
    return path if inside and os.path.isfile(path) else None


def read_new_lines(path: str, offset: int) -> tuple[list[str], int]:
    """The whole lines added to the text file since byte `offset`, and the offset after them. A file that is shorter than `offset` was
    deleted or started again (Refresh debug does that): it is read from its start. A last line without its newline is left for next time. A file that
    is not there is no lines; one that cannot be read (a lock by OneDrive or a virus scanner) raises OSError, so that the offset is kept and not started over."""
    try:
        size = os.path.getsize(path)
        if size < offset:
            offset = 0
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
    except FileNotFoundError:
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
    say "and N more"; the held ones are only counted. `flush(now)` is for when no further failure comes: the count of those held back, once the gap has
    passed, so that they are not left unsaid."""

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

    def flush(self, now: float) -> int:
        """The failures held back, if there are any and a post is allowed now (then the count starts again); else 0."""
        if self.held and self.last is not None and now - self.last >= self.gap:
            held, self.held, self.last = self.held, 0, now
            return held
        return 0


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
    """The new "is stale" state and what to post for the change: once when the macro stops updating, once when it is updating again. A macro that
    was stopped on purpose (its status says `stopped`) is not stale, and nothing is posted for it."""
    if status is not None and status.get("stopped"):
        return False, None
    stale = is_stale(status, now, stale_minutes)
    if stale is None or stale == was_stale:
        return was_stale, None
    if stale:
        assert status is not None
        return True, f"**The macro is not updating** - its last update was {status.get('updated')} (stuck, stopped, or the PC is asleep or locked)."
    return False, "The macro is updating again."


def loop_idle(status: Optional[dict[str, Any]]) -> bool:
    """True if status.json says the macro is running nothing (stopped on purpose, or open with no loop): see main.mark_stopped()."""
    return status is not None and bool(status.get("stopped"))


def macro_busy(status: Optional[dict[str, Any]]) -> Optional[bool]:
    """Whether the macro's window is running something (the loop or an action), from status.json; None if it does not say (no status, or one from before it did)."""
    busy = status.get("busy") if status else None
    return busy if isinstance(busy, bool) else None


def rview_finished(elapsed: float, limit: float, until_done: bool, busy: Optional[bool], seen_busy: bool, grace: float) -> bool:
    """Whether the rolling view is over: its time (`limit` seconds) is up or - with `until_done` - the macro has stopped being busy after it was seen busy, or was not busy at all for
    `grace` seconds from the start (what was asked for was over already, or never began). A status that does not say (`busy` None) never ends it early."""
    if elapsed >= limit:
        return True
    return until_done and busy is False and (seen_busy or elapsed >= grace)


def heartbeat_due(last: Optional[float], hours: float, now: float) -> bool:
    """Whether a heartbeat post is due: `hours` is not 0 and that long has passed since `last` (None: never, so it is due at once)."""
    return hours > 0 and (last is None or now - last >= hours * 3600)


def run_charts(runs_dir: str = "") -> list[str]:
    """The run charts (RUNS_DIR/run<N>.png) there are now, oldest first."""
    return sorted(glob.glob(os.path.join(runs_dir or cfg.RUNS_DIR, "run*.png")), key=os.path.getmtime)


# --- the bot's memory between starts (bot_state.json): where it got to ---

def state_path() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot_state.json")


@dataclass
class BotState:
    log_unreadable: bool = False                             # the run log could not be read and that was posted
    log_offset: Optional[int] = None                         # None: never started - begin at the end of the run log, not at its start
    charts: list[str] = field(default_factory=list)          # run charts already posted (or there when the bot first started)
    stale: bool = False
    last_heartbeat: Optional[float] = None
    desired_state: Optional[str] = None                      # after the bot's /start ("open": the window, no loop), /loop ("running": the loop too), /stop ("open") and /exit ("stopped"); None before: a macro that is gone is a crash only if it should be there
    crash_reported: bool = False                             # the macro's process is gone and that was posted
    restarts: list[float] = field(default_factory=list)      # when the bot restarted the macro by itself (the last hour counts)
    previous_commit: Optional[str] = None                    # the commit the PC was on before the last /update: what /rollback goes back to


def load_state(path: str) -> BotState:
    """The saved state, with the default for anything missing or of the wrong kind (no file: all defaults)."""
    data = read_json(path) or {}
    state = BotState()
    for key in ("log_offset", "last_heartbeat"):
        if isinstance(data.get(key), (int, float)) and not isinstance(data.get(key), bool):
            setattr(state, key, data[key])
    for key in ("charts",):
        if isinstance(data.get(key), list):
            setattr(state, key, [item for item in data[key] if isinstance(item, str)])
    if isinstance(data.get("previous_commit"), str) and re.fullmatch(r"[0-9a-f]{7,64}", data["previous_commit"]):
        state.previous_commit = data["previous_commit"]
    if isinstance(data.get("restarts"), list):
        state.restarts = [item for item in data["restarts"] if isinstance(item, (int, float)) and not isinstance(item, bool)]
    for key in ("stale", "crash_reported", "log_unreadable"):
        if isinstance(data.get(key), bool):
            setattr(state, key, data[key])
    if data.get("desired_state") in ("running", "open", "stopped"):
        state.desired_state = data["desired_state"]
    return state


def save_state(path: str, state: BotState) -> None:
    """Write the state to `path` all at once (a half-written file would read as no state: everything posted again)."""
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(state.__dict__, f)
    os.replace(temp, path)


# --- pulling things out of the debug folder into OneDrive (/pull) ---

PULL_ALL = "*"           # the item that means everything in the folder


def pull_entries(source: str, limit: int = 24) -> list[str]:
    """The names of what is in the debug folder `source` (a key of magic_numbers.PULL_SOURCES) that /pull can take, newest first, at most `limit`."""
    folder = cfg.PULL_SOURCES.get(source)
    if folder is None or not os.path.isdir(folder):
        return []
    def modified(name: str) -> float:
        try:
            return os.path.getmtime(os.path.join(folder, name))
        except OSError:                                  # (gone since the listing: the macro empties these folders)
            return 0.0
    try:
        names = [name for name in os.listdir(folder) if not name.startswith(".") and not name.endswith(".tmp")]
    except OSError:
        return []
    return sorted(names, key=modified, reverse=True)[:limit]


def resolve_pull(source: str, item: Optional[str]) -> tuple[str, str]:
    """(path, name) of what /pull `item` of `source` means: the newest entry when `item` is None, the whole folder for PULL_ALL, else the entry of
    that exact name - which must be in the folder (a name is checked against what is there, so no path - `..`, a slash, a drive - gets anywhere).
    ValueError if the folder is unknown or empty or there is no such entry."""
    folder = cfg.PULL_SOURCES.get(source)
    if folder is None:
        raise ValueError(f"there is no folder called {source!r} to pull from")
    if item == PULL_ALL:
        return folder, source
    names = pull_entries(source, limit=10 ** 6)
    if not names:
        raise ValueError(f"{source} has nothing in it")
    if item is None:
        item = names[0]
    if item not in names:
        raise ValueError(f"{source} has no {item!r}")
    return os.path.join(folder, item), item


def pull_files(path: str, name: str) -> list[tuple[str, str]]:
    """(file on this PC, path inside the destination folder) for everything `path` is: a file; a folder's files, all of them - except a minigame
    capture (a folder with a zips/ folder, where the frames are the same again as the zips, and the first zip holds info.json and events.jsonl): its zips
    alone, wherever in the folder it is."""
    if os.path.isfile(path):
        return [(path, name)]
    found = []
    unzipped = []
    for root, folders, files in os.walk(path):
        if "zips" in folders or any(file.startswith("frame_") for file in files):          # (a capture)
            zips = os.path.join(root, "zips")
            if not os.path.isdir(zips):
                unzipped.append(os.path.basename(root))                                    # (its zips are not there yet - zip_capture() puts them there once all are made -: not its frames, which are the same again as the zips)
                folders[:] = []
                continue
            folders[:] = ["zips"]
            files = []
        for file in sorted(files):
            full = os.path.join(root, file)
            found.append((full, os.path.join(name, os.path.relpath(full, path))))
    if unzipped and not found:
        raise ValueError(f"{', '.join(unzipped)} has no zips yet - it is still being zipped, or was never zipped: try again in a moment")
    return found


def files_size_mb(files: list[tuple[str, str]]) -> float:
    """The megabytes of the files /pull would copy."""
    return sum(os.path.getsize(src) for src, _ in files) / 1048576


def size_text(megabytes: float) -> str:
    """A size for a message: megabytes, or gigabytes from 1024 MB."""
    return f"{megabytes / 1024:.1f} GB" if megabytes >= 1024 else f"{megabytes:.0f} MB"


def copy_pull(files: list[tuple[str, str]], onedrive_dir: str, source: str, max_mb: float) -> float:
    """Copy `files` into onedrive_dir/<source>/ (the OneDrive app uploads them from there); the megabytes copied. ValueError, and nothing copied, if
    there are none or they come to more than `max_mb`."""
    if not files:
        raise ValueError("there are no files to copy")
    total = files_size_mb(files)
    if total > max_mb:
        raise ValueError(f"that is {size_text(total)} - more than the {size_text(max_mb)} one /pull will copy; pull a smaller one")
    made: list[str] = []                                  # what this copy made, to take away again if it fails half-way
    try:
        for src, relative in files:
            target = long_path(os.path.join(onedrive_dir, source, relative))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            existed = os.path.exists(target)
            shutil.copy2(long_path(src), target)
            if not existed:
                made.append(target)
            if os.path.getsize(target) != os.path.getsize(long_path(src)):                    # (a copy that is not the file: said, not reported as done)
                raise OSError(f"{relative} was copied as {os.path.getsize(target)} bytes, not {os.path.getsize(long_path(src))}")
    except OSError:
        for path in made:
            with contextlib.suppress(OSError):
                os.remove(path)
        raise
    return total


# --- /onedrive: why a copy into onedrive_dir is not uploaded, told from the PC without anyone at it ---

ATTRIBUTE_FLAGS = ((0x1, "read-only"), (0x2, "hidden"), (0x4, "system"), (0x400, "reparse point"), (0x1000, "offline"), (0x40000, "cloud placeholder (recall on open)"),
                   (0x400000, "cloud placeholder (recall on data access)"), (0x80000, "pinned: always keep"), (0x100000, "unpinned: online-only"))


def attribute_text(attributes: int) -> str:
    """The Windows file attributes of `attributes` (st_file_attributes) that say something about a synced file, in words."""
    return ", ".join(name for flag, name in ATTRIBUTE_FLAGS if attributes & flag) or "plain"


def onedrive_accounts() -> list[tuple[str, str]]:
    """(account, its synced folder) of each OneDrive account in the registry (Windows; none elsewhere or when it cannot be read)."""
    try:
        import winreg
    except ImportError:
        return []
    found = []
    try:                                                        # (the attributes are Windows's: mypy on another system does not know them)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\OneDrive\Accounts") as accounts:  # type: ignore[attr-defined]
            for index in range(winreg.QueryInfoKey(accounts)[0]):  # type: ignore[attr-defined]
                name = winreg.EnumKey(accounts, index)  # type: ignore[attr-defined]
                with winreg.OpenKey(accounts, name) as account:  # type: ignore[attr-defined]
                    with contextlib.suppress(OSError):
                        found.append((name, str(winreg.QueryValueEx(account, "UserFolder")[0])))  # type: ignore[attr-defined]
    except OSError:
        pass
    return found


def same_place(path: str, root: str) -> bool:
    """Whether `path` is `root` or inside it (the way Windows compares: whatever the case, whichever slashes)."""
    path, root = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(root))
    return path == root or path.startswith(root.rstrip("\\/") + os.sep)


def onedrive_report(folder: str, processes: Optional[list[tuple[int, str]]], env: dict[str, str], accounts: list[tuple[str, str]], now: float) -> str:
    """What can be told of why files copied into `folder` (onedrive_dir) are not uploaded: that it is a folder that can be written to and has room, whether it is inside a folder
    OneDrive syncs (the OneDrive folders of the environment and of the registry), whether the OneDrive app runs and when it last wrote its logs, and what the newest files in it are
    - their size, age and Windows attributes (a cloud placeholder or "online-only" is a file OneDrive has handled)."""
    lines = [f"onedrive_dir: {folder or '(not set)'}"]
    if not folder or not os.path.isdir(folder):
        return "\n".join(lines + ["It is not a folder on this PC: nothing can be copied there."])
    lines.append(f"  full path: {os.path.abspath(folder)}" + (" (a link to another place: OneDrive does not sync through one)" if os.path.islink(folder) else ""))
    probe = os.path.join(folder, f"_onedrive_probe_{int(now)}.txt")
    try:
        with open(probe, "w", encoding="utf-8") as f:
            f.write("probe")
        with open(probe, encoding="utf-8") as f:
            read = f.read()
        os.remove(probe)
        lines.append("  write test: a small file was written, read back and removed - " + ("ok" if read == "probe" else f"it read back as {read!r}"))
    except OSError as error:
        lines.append(f"  write test: FAILED ({error})")
    try:
        free = shutil.disk_usage(folder).free
        lines.append(f"  free space on its drive: {size_text(free / 1048576)}")
    except OSError as error:
        lines.append(f"  free space: unknown ({error})")
    roots = [(key, env[key]) for key in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial") if env.get(key)] + [(f"registry {name}", root) for name, root in accounts]
    inside = [f"{key} ({root})" for key, root in roots if same_place(folder, root)]
    lines.append("OneDrive folders of this PC: " + (", ".join(f"{key} = {root}" for key, root in roots) or "none found (no environment variable, no account in the registry)"))
    lines.append("  onedrive_dir is inside: " + (", ".join(inside) if inside else "NONE of them - OneDrive does not sync this folder"))
    if processes is None:
        lines.append("OneDrive app: processes of this PC cannot be listed")
    else:
        running = [line for _, line in processes if "onedrive.exe" in line.lower()]
        lines.append("OneDrive app: " + (f"running ({len(running)} process{'es' if len(running) != 1 else ''})" if running else "NOT running"))
    logs = os.path.join(env.get("LOCALAPPDATA", ""), "Microsoft", "OneDrive", "logs")
    if env.get("LOCALAPPDATA") and os.path.isdir(logs):
        for account in sorted(os.listdir(logs)):
            path = os.path.join(logs, account)
            try:
                newest = max((os.path.getmtime(os.path.join(path, name)) for name in os.listdir(path)), default=None) if os.path.isdir(path) else None
            except OSError:
                newest = None
            lines.append(f"  its log for {account}: " + (f"last written {age_text(now - newest)} ago" if newest else "nothing in it"))
    files = []
    for root, _, names in os.walk(folder):
        for name in names:
            path = os.path.join(root, name)
            try:
                info = os.stat(path)
            except OSError:
                continue
            files.append((info.st_mtime, os.path.relpath(path, folder), info.st_size, getattr(info, "st_file_attributes", 0)))
    lines.append(f"in the folder: {len(files)} file(s), {size_text(sum(size for _, _, size, _ in files) / 1048576)}; the newest:")
    for modified, relative, size, attributes in sorted(files, reverse=True)[:10]:
        lines.append(f"  {relative} - {size_text(size / 1048576)}, {age_text(now - modified)} old, {attribute_text(attributes)}")
    return "\n".join(lines)


def long_path(path: str) -> str:
    """On Windows the path in the form that is not limited to MAX_PATH characters (`\\\\?\\` and the full path); anywhere else the path as it is."""
    if os.name != "nt" or path.startswith("\\\\?\\"):
        return path
    return "\\\\?\\" + os.path.abspath(path)


# --- control: start, stop, restart the macro ---

def write_command(folder: str, command: str, now: float, **details: Any) -> str:
    """Leave `command` (one of magic_numbers.COMMANDS) for the macro to find in `folder`, as a file of its own named <time>-<id> (so two commands sent close
    together are both kept and done in order): {"command": ..., "time": ..., plus `details`}, written all at once. Returns the name to ask about it
    (take_ack(), withdraw_command()). The macro removes the file once it has read it, answers in <name>.ack, and ignores a command that is old
    (COMMAND_MAX_AGE) or a `set` for a switch it doesn't have."""
    if command not in cfg.COMMANDS:
        raise ValueError(f"not a command the macro knows: {command!r}")
    os.makedirs(folder, exist_ok=True)
    for old in glob.glob(os.path.join(folder, "*.ack")) + glob.glob(os.path.join(folder, "*.done")):                  # (answers nobody came for)
        if now - os.path.getmtime(old) > cfg.BOT_ACK_KEEP:
            with contextlib.suppress(OSError):
                os.remove(old)
    name = f"{now:015.3f}-{uuid.uuid4().hex[:6]}"
    temp = os.path.join(folder, name + ".tmp")
    with open(temp, "w", encoding="utf-8") as f:
        json.dump({"command": command, "time": now, **details}, f)
    os.replace(temp, os.path.join(folder, name + ".json"))
    return name


def take_ack(folder: str, name: str, suffix: str = "ack") -> Optional[str]:
    """What the macro answered about the command `name` ("taken" or "dropped: why"; with suffix "done", how its action ended), removing the answer; None if
    it has not answered."""
    path = os.path.join(folder, f"{name}.{suffix}")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read().strip()
    except OSError:
        return None
    with contextlib.suppress(OSError):
        os.remove(path)
    return text or None


def withdraw_command(folder: str, name: str) -> bool:
    """Take the command `name` back if the macro has not read it; False if it is gone (the macro took it)."""
    try:
        os.remove(os.path.join(folder, name + ".json"))
        return True
    except OSError:
        return False


def inventory_pictures(folder: str) -> list[str]:
    """The pictures of an inventory tour in `folder` (001.png, 002.png ...), in order."""
    return sorted(glob.glob(os.path.join(folder, "[0-9][0-9][0-9].png")))


def clear_inventory(folder: str) -> None:
    """Remove the pictures and the end marker of an earlier tour."""
    for path in [*inventory_pictures(folder), os.path.join(folder, "done.json")]:
        with contextlib.suppress(OSError):
            os.remove(path)


def trade_pictures(folder: str) -> list[str]:
    """The pictures of a /trades tour in `folder` (L1_01.png, L1_02.png ... L2_01.png ...), in order; not the pictures of the rows it keeps for /decline."""
    return sorted(glob.glob(os.path.join(folder, "L[0-9]_[0-9][0-9].png")))


def trades_text(done: dict[str, Any]) -> str:
    """What the /trades message says: what the tour found on each listing, or what went wrong."""
    if done.get("problem"):
        return f"The trade listings were not looked at: {done['problem']}."
    lines = []
    for listing, found in sorted(done.get("listings", {}).items()):
        offers, pictures = found.get("offers", 0), found.get("pictures", 0)
        lines.append(f"Listing {listing}: {offers} offer{'s' if offers != 1 else ''}" + (f", {pictures} picture{'s' if pictures != 1 else ''}" if pictures else "") + f" ({found.get('ended', 'ended')}).")
    return "\n".join(lines or ["No listings were looked at."]) + ("\n/decline <listing> <offer> declines an offer by the number on the pictures." if any(f.get("offers") for f in done.get("listings", {}).values()) else "")


def parse_offer(text: str) -> Optional[int | str]:
    """/decline's offer: a number from 1 to TRADES_MAX_OFFERS, or TRADES_ALL_OFFERS as it is; None if it is neither."""
    text = text.strip()
    if text == cfg.TRADES_ALL_OFFERS:
        return text
    return int(text) if text.isdigit() and 1 <= int(text) <= cfg.TRADES_MAX_OFFERS else None


def decline_text(done: dict[str, Any]) -> str:
    """What the /decline message says."""
    if done.get("offer") == cfg.TRADES_ALL_OFFERS:
        declined = f"{done.get('count', 0)} offer(s) of listing {done.get('listing')}"
        if done.get("declined"):
            return f"Declined {declined}."
        return f"Declined {declined}; it stopped: {done.get('why') or 'it did not end'}."
    where = f"offer {done.get('offer')} of listing {done.get('listing')}"
    if done.get("declined"):
        return f"Declined {where} (the offer as it was is in the picture)."
    return f"Did not decline {where}: {done.get('why') or 'it did not end'}."


def clear_trades(folder: str) -> None:
    """Remove the pictures and the end markers of an earlier /trades tour or /decline (not the pictures of the rows, which /decline needs until the next tour)."""
    for path in [*trade_pictures(folder), *(os.path.join(folder, name) for name in ("done.json", "decline.json", "declined.png"))]:
        with contextlib.suppress(OSError):
            os.remove(path)


def in_batches(items: list[str], size: int) -> list[list[str]]:
    """`items` in lists of at most `size`, in order."""
    return [items[i:i + size] for i in range(0, len(items), size)]


def clear_commands(folder: str) -> None:
    """Remove the commands nobody read (after the macro was killed, say) and their answers."""
    for path in glob.glob(os.path.join(folder, "*.json")) + glob.glob(os.path.join(folder, "*.ack")) + glob.glob(os.path.join(folder, "*.done")):
        with contextlib.suppress(OSError):
            os.remove(path)


def switch_command(kind: str, name: str, value: bool) -> dict[str, Any]:
    """The details of a `set` command for the switch `name` (`kind` "need" or "option"); ValueError if there is no such switch."""
    valid = {"need": cfg.NEED_NAMES, "option": (*cfg.OPTION_SWITCHES, cfg.SHOW_DEBUG_LINES_OPTION)}.get(kind, ())
    if name not in valid:
        raise ValueError(f"no {kind} switch called {name!r}")
    return {"kind": kind, "name": name, "value": bool(value)}


# The game config's defaults for the switches (main.py's _read_game_config(): side quest, the private server and the blackout key on, the rest off), for a config that doesn't have them yet.
OPTION_DEFAULTS = {"side_quest_enabled": True, "use_private_server": True, "blackout_hotkey_enabled": True}


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
    """Whether the macro's process is gone when the bot's /start or /loop (and not /exit) says it should be there: the new "reported" state and "alert" or
    "recovered" when that changes. Nothing is known (None) when the process check can't tell, or `/start` was never used."""
    if desired not in ("running", "open"):
        return False, None                                   # (stopped on purpose, or the bot never started it: no crash to report)
    if running is None:
        return reported, None
    if not running and not reported:
        return True, "alert"
    if running and reported:
        return False, "recovered"
    return reported, None


def crash_check_due(desired: Optional[str], updating: bool) -> bool:
    """Whether the macro's process is looked for now: when the bot has it that it should be running, and not while /update or /rollback has stopped it on purpose."""
    return desired in ("running", "open") and not updating


def loop_started(status: Optional[dict[str, Any]], since: datetime) -> bool:
    """Whether the loop is going and began at or after `since`: status.json written then, and not saying that nothing runs (a window with its loop stopped writes
    it too). A loop that was going all along also writes it, so this is asked only of a loop that was seen to be idle (loop_idle())."""
    return started_since(status, since) and not loop_idle(status)


def started_since(status: Optional[dict[str, Any]], since: datetime) -> bool:
    """Whether status.json was written at or after `since` - the macro that was just started is running its loop (it writes the status when it starts)."""
    try:
        return status is not None and datetime.strptime(str(status.get("updated")), TIME_FORMAT) >= since.replace(microsecond=0)
    except ValueError:
        return False


# --- update and rollback ---

@dataclass(frozen=True)
class UpdatePlan:
    """What /update or /rollback will do: `kind` is "update" (or "rollback") to go ahead, anything else to stop with `text` as the reason."""
    kind: str
    text: str


def python_changes(numstat: str) -> tuple[int, int, int]:
    """(files, lines added, lines removed) of the Python files in `git diff --numstat` output (`added<TAB>removed<TAB>path`; a binary file has dashes)."""
    files = added = removed = 0
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[2].endswith(".py"):
            files += 1
            added += int(parts[0]) if parts[0].isdigit() else 0
            removed += int(parts[1]) if parts[1].isdigit() else 0
    return files, added, removed


def python_changes_text(changes: tuple[int, int, int]) -> str:
    """"N lines changed in M Python files (+A / -R)" - or that no Python file changed."""
    files, added, removed = changes
    if not files:
        return "No Python file changed."
    lines = added + removed
    return f"{lines} line{'s' if lines != 1 else ''} changed in {files} Python file{'s' if files != 1 else ''} (+{added} / -{removed})."


def short(commit: str) -> str:
    return commit[:7]


def dirty_files(porcelain: str) -> list[str]:
    """The paths in `git status --porcelain --untracked-files=no` output (a line is two status characters, a space, the path; a rename's new path)."""
    return [line[3:].split(" -> ")[-1].strip() for line in porcelain.splitlines() if len(line) > 3]


def files_text(files: list[str], limit: int = 8) -> str:
    return ", ".join(files[:limit]) + (f" and {len(files) - limit} more" if len(files) > limit else "")


def log_text(lines: list[str], limit: int = 15) -> str:
    """The `git log --oneline` lines of what is pulled, the newest first, at most `limit` of them."""
    shown = [line if len(line) <= MAX_LOG_LINE else line[:MAX_LOG_LINE - 1] + "…" for line in lines[:limit]]
    return "\n".join(shown) + (f"\n... and {len(lines) - limit} more" if len(lines) > limit else "")


def update_plan(head: str, target: str, dirty: list[str], head_in_target: bool, target_in_head: bool, log_lines: list[str], name: str, changes: str = "") -> UpdatePlan:
    """Whether /update may move the PC from commit `head` to `target` (`name`, say origin/main) and what to say: nothing to do when they are the same,
    never over local changes to tracked files, and only forward - `head_in_target` (head is an ancestor of target: a fast-forward). If target is
    behind head (`target_in_head`) the PC is ahead; otherwise the histories have diverged."""
    if head == target:
        return UpdatePlan("up_to_date", f"Already up to date at {short(head)} ({name}).")
    if dirty:
        return UpdatePlan("dirty", f"The PC's copy has local changes to tracked files ({files_text(dirty)}) - not updating. Nothing was changed.")
    if not head_in_target:
        if target_in_head:
            return UpdatePlan("ahead", f"The PC is ahead of {name} ({short(head)} is past {short(target)}) - nothing to pull.")
        return UpdatePlan("diverged", f"The PC's history and {name} have diverged ({short(head)} vs {short(target)}) - not updating. Nothing was changed.")
    return UpdatePlan("update", f"Updating {short(head)} -> {short(target)}:\n{log_text(log_lines)}" + (f"\n{changes}" if changes else ""))


def rollback_plan(head: str, previous: Optional[str], dirty: list[str], previous_exists: bool) -> UpdatePlan:
    """Whether /rollback may go back to commit `previous` (the one before the last /update, None if there was none): not over local changes."""
    if previous is None:
        return UpdatePlan("none", "There is no earlier commit to go back to - /rollback only undoes a /update.")
    if not previous_exists:
        return UpdatePlan("missing", f"The commit before the last update ({short(previous)}) is not in this copy any more - not rolling back.")
    if previous == head:
        return UpdatePlan("same", f"Already at {short(head)}, the commit before the last update.")
    if dirty:
        return UpdatePlan("dirty", f"The PC's copy has local changes to tracked files ({files_text(dirty)}) - not rolling back. Nothing was changed.")
    return UpdatePlan("rollback", f"Going back {short(head)} -> {short(previous)}.")


def needs_pip(changed: list[str]) -> bool:
    """Whether the changed files include requirements.txt (the packages the macro and the bot need)."""
    return "requirements.txt" in changed


def needs_bot_restart(changed: list[str]) -> bool:
    """Whether the bot itself runs different code after this change: its own files, the constants it reads (magic_numbers.py) or the packages."""
    return any(path.startswith("bot/") or path in ("magic_numbers.py", "requirements.txt") for path in changed)


GitRunner = Callable[[list[str]], tuple[int, str]]       # runs git with these arguments in the repository: (exit code, output)


def git_ok(run: GitRunner, *args: str) -> str:
    """The output of a git command that has to work; RuntimeError with git's own message if it doesn't."""
    code, output = run(list(args))
    if code != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {output[-500:]}")
    return output


def make_plan(run: GitRunner, remote: str, branch: str, previous: Optional[str], rollback: bool) -> tuple[UpdatePlan, list[str], str]:
    """Ask git what /update (or, with `rollback`, /rollback to commit `previous`) would do, changing nothing but the remote-tracking branch (a fetch):
    the plan, the git arguments that carry it out (`merge --ff-only` to the fetched commit, or `reset --hard` to the earlier one) and the commit
    the PC is on now. RuntimeError if git fails."""
    head = git_ok(run, "rev-parse", "HEAD").strip()
    dirty = dirty_files(git_ok(run, "status", "--porcelain", "--untracked-files=no"))
    if rollback:
        exists = previous is not None and run(["cat-file", "-e", f"{previous}^{{commit}}"])[0] == 0
        plan = rollback_plan(head, previous, dirty, exists)
        return plan, ["reset", "--hard", previous or ""], head
    name = f"{remote}/{branch}"
    git_ok(run, "fetch", remote, branch)
    target = git_ok(run, "rev-parse", "FETCH_HEAD^{commit}").strip()           # (what was just fetched, whatever the remote's fetch refspec makes of the branch's name)
    head_in_target = run(["merge-base", "--is-ancestor", head, target])[0] == 0
    target_in_head = run(["merge-base", "--is-ancestor", target, head])[0] == 0
    log = git_ok(run, "log", "--oneline", f"{head}..{target}").splitlines() if head_in_target else []
    changes = python_changes_text(python_changes(git_ok(run, "diff", "--numstat", head, target))) if head_in_target and head != target else ""
    return update_plan(head, target, dirty, head_in_target, target_in_head, log, name, changes), ["merge", "--ff-only", target], head
