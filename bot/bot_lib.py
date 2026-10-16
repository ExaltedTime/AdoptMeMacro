"""The logic of the Discord bot (discord_bot.py) that needs neither Discord nor Windows: reading the config, deciding who may give
orders, and turning the files the macro writes (status.json, run_log.txt, results.jsonl, the run charts) into what is posted.
Plain functions, so tests/test_bot.py can run them anywhere. See docs/ARCHITECTURE.md#discord-bot."""
import glob
import json
import os
from dataclasses import dataclass
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


REQUIRED = ("token", "owner_id", "channel_id")
DEFAULTS: dict[str, Any] = {"guild_id": None, "stale_status_minutes": 5, "log_lines_max": 40}


def load_config(path: str) -> Config:
    """The bot's settings from the JSON file at `path` (see bot_config.example.json); ConfigError with what is wrong if it can't be used."""
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        raise ConfigError(f"{path} not found - copy bot_config.example.json to it and fill it in") from None
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
    return Config(values["token"], values["owner_id"], values["channel_id"], values["guild_id"],
                  float(values["stale_status_minutes"]), values["log_lines_max"])


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
