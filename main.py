#!/usr/bin/env python3
"""Adopt Me Macro - Automated pet care with GUI control panel."""

from __future__ import annotations

# ============================================================================
# EXTERNAL DEPENDENCIES (install via pip)
# ============================================================================
# numpy       - numerical array operations
# opencv-python (cv2) - computer vision and image processing
# mss         - fast screenshot capture
# pyautogui   - cross-platform mouse/keyboard automation
# pydirectinput - direct input for game compatibility
# pygetwindow - window management
#
# See README.md for an explanation of how the code is organized and how it behaves.

import math, os, re, sys, time, threading, json, subprocess, traceback, ctypes, queue, random, contextlib, shutil, zipfile
import datetime
import logging
import logging.handlers
import itertools
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field, fields
from typing import Collection, Any, Callable, Iterable, Iterator, Optional, Sequence, Union
from abc import ABC, abstractmethod
from functools import lru_cache, partial
import tkinter as tk
from tkinter import messagebox, scrolledtext, simpledialog, ttk

import numpy as np
import cv2
import mss
import pyautogui
import pydirectinput
import pygetwindow as gw

Image = np.ndarray                  # a BGR screenshot or crop (what grab_screen() returns)
Point = tuple[int, int]             # (x, y) in the reference space
Box = tuple[int, int, int, int]     # (left, top, right, bottom) - or (left, top, width, height) for roblox_rect()
Color = tuple[int, int, int]        # (R, G, B)

import magic_numbers as cfg

class TaskInterrupted(Exception):
    """A minigame or a rejoin took over the character: the handler is abandoned and
    process_needs() catches this. `reason` is for the log. See docs/ARCHITECTURE.md#stopping--focus-safety.
    """
    def __init__(self, reason: str = "something") -> None:
        super().__init__(reason)
        self.reason = reason

class StopRequested(Exception):
    """Raised to unwind a running workflow when [STOP] is pressed - cooperative
    cancellation, so held keys are released by the finally blocks on the way up.
    See docs/ARCHITECTURE.md#stopping--focus-safety for why it isn't thread-killing.
    """
    pass

class FocusLost(Exception):
    """Raised when Roblox is no longer the focused window mid-workflow; unwinds like
    StopRequested so no input goes to another window. See docs/ARCHITECTURE.md#stopping--focus-safety.
    """
    pass

def hotkey_down(key: str) -> bool:
    """True while `key` (a single letter or digit) is held down, whichever
    window has the focus (Windows)."""
    return bool(ctypes.windll.user32.GetAsyncKeyState(ord(key.upper())) & 0x8000)  # type: ignore[attr-defined]  # Windows only

def hotkey_fires(down: bool, was_down: bool, only_while_running: bool, running: bool, only_in_roblox: bool) -> bool:
    """Whether a hotkey seen `down` (and not `was_down` at the last look) is to act: not while it is just being held, not when it is
    one for a run that isn't going (`only_while_running`), and not when it is for Roblox's window only (`only_in_roblox`) and another is focused."""
    return down and not was_down and (running or not only_while_running) and (not only_in_roblox or is_roblox_focused())

def check_stop() -> None:
    """Raise StopRequested if [STOP] was pressed - the one checkpoint that doesn't
    require Roblox focus (see run_full_cycle()).
    """
    if STOP_EVENT.is_set():
        raise StopRequested()

def check_focus() -> None:
    """Raise FocusLost if Roblox is running but is no longer the focused
    window. Prefer check_running() at most checkpoints."""
    if not is_roblox_focused():
        logger.warning("Roblox is no longer focused")
        raise FocusLost()

def stop_pressed_soon() -> bool:
    """True if [STOP] is pressed within STOP_CLICK_GRACE. Clicking the button
    takes the focus from Roblox on mouse-down, a moment before the click sets
    STOP_EVENT, so a FocusLost seen right then is really the user stopping."""
    return STOP_EVENT.wait(cfg.STOP_CLICK_GRACE)

def check_running() -> None:
    """Raise StopRequested or FocusLost if the workflow shouldn't continue - the single
    checkpoint used everywhere (see wait_interruptible()).
    """
    check_stop()
    check_focus()

# ============================================================================
# WINDOW FOCUS & SCREEN CAPTURE
# ============================================================================

def roblox_windows() -> list[Any]:
    """Every open window with 'roblox' in its title."""
    return [w for w in gw.getAllWindows() if "roblox" in w.title.lower()]

def roblox_window() -> Any:
    """The Roblox game window: the one titled exactly "Roblox" if there is
    one (so a browser tab that merely mentions Roblox isn't picked), else the
    first window with 'roblox' in its title, else None."""
    wins = roblox_windows()
    return next((w for w in wins if w.title == "Roblox"), wins[0] if wins else None)

def focus_roblox() -> bool:
    """Bring the Roblox window to the front (no click)."""
    global _ROBLOX_RECT
    w = roblox_window()
    if w is None:
        logger.warning("Roblox not found")
        return False
    if w.isMinimized:
        w.restore()
    w.activate()
    _ROBLOX_RECT = None  # it may have just moved or changed size
    time.sleep(cfg.FOCUS_DELAY)
    return True

def is_roblox_focused() -> bool:
    """Return True if a window with 'roblox' in its title is currently the
    active (focused) window."""
    active = gw.getActiveWindow()
    title = (active.title or "") if active is not None else ""
    if title == "Roblox":
        return True
    if "roblox" not in title.lower():
        return False
    # An ambiguous title (a browser tab, the crash window): the game only if no window is titled exactly "Roblox" -
    # the same choice roblox_window() makes.
    game = roblox_window()
    return game is None or game.title != "Roblox"

def focus_roblox_click() -> bool:
    """Focus Roblox and click near the top edge to make sure input registers."""
    if not cfg.FOCUS_WINDOW_ON_ACTION:
        return True
    if not focus_roblox():
        return False
    pyautogui.moveTo(*to_screen(cfg.REFERENCE_WIDTH * cfg.FOCUS_CLICK_X_PERCENT, cfg.FOCUS_CLICK_Y))
    pydirectinput.click()
    time.sleep(cfg.FOCUS_CLICK_SETTLE_DELAY)
    return True

# (time looked up, rect) of the last roblox_rect() answer - see ROBLOX_RECT_TTL.
_ROBLOX_RECT = None

def roblox_rect() -> Box:
    """(left, top, width, height) in screen pixels of the Roblox window clipped to the
    primary monitor, or the whole monitor if there is no usable window. Cached for
    ROBLOX_RECT_TTL. See docs/ARCHITECTURE.md#window--coordinates.
    """
    global _ROBLOX_RECT
    now = time.time()
    cached = _ROBLOX_RECT                          # (a local: focus_roblox() can set the global to None on another thread between the test and the use)
    if cached and now - cached[0] < cfg.ROBLOX_RECT_TTL:
        return cached[1]
    screen_w, screen_h = pyautogui.size()
    rect = (0, 0, screen_w, screen_h)
    try:
        w = roblox_window()
        if w is not None and not w.isMinimized:
            left, top = max(w.left, 0), max(w.top, 0)
            right, bottom = min(w.left + w.width, screen_w), min(w.top + w.height, screen_h)
            if right > left and bottom > top:
                rect = (left, top, right - left, bottom - top)
    except Exception:
        pass  # the window closed while it was being measured - use the whole monitor
    _ROBLOX_RECT = (now, rect)
    return rect

def to_screen(x: float, y: float) -> Point:
    """Turn a position in the reference space every constant and every
    detection uses (REFERENCE_WIDTH x REFERENCE_HEIGHT) into the screen
    pixel it is on in the Roblox window right now."""
    left, top, width, height = roblox_rect()
    return int(left + x * width / cfg.REFERENCE_WIDTH), int(top + y * height / cfg.REFERENCE_HEIGHT)

def grab_full_screen() -> Image:
    """Screenshot the whole primary monitor as a BGR numpy array."""
    with mss.MSS() as sct:
        shot = np.array(sct.grab(sct.monitors[1]))
    return cv2.cvtColor(shot, cv2.COLOR_BGRA2BGR)

def grab_screen() -> Image:
    """Screenshot of the Roblox window as a BGR array scaled to the reference size, so
    every detection works on the same pixels whatever the window.
    See docs/ARCHITECTURE.md#window--coordinates.
    """
    img = grab_full_screen()
    left, top, width, height = roblox_rect()
    if (left, top, width, height) != (0, 0, img.shape[1], img.shape[0]):
        img = img[top:top + height, left:left + width]
    if img.shape[1] != cfg.REFERENCE_WIDTH or img.shape[0] != cfg.REFERENCE_HEIGHT:
        img = cv2.resize(img, (cfg.REFERENCE_WIDTH, cfg.REFERENCE_HEIGHT), interpolation=cv2.INTER_AREA)
    return img

def grab_screen_region(x: int, y: int, width: int, height: int) -> Image:
    """The same pixels as grab_screen()[y:y + height, x:x + width] (reference space), captured
    directly when the window is exactly the reference size. See docs/ARCHITECTURE.md#need-icon-detection.
    """
    left, top, window_w, window_h = roblox_rect()
    if (window_w, window_h) == (cfg.REFERENCE_WIDTH, cfg.REFERENCE_HEIGHT) and x >= 0 and y >= 0:
        with mss.MSS() as sct:
            monitor = sct.monitors[1]
            if left + x + width <= monitor["width"] and top + y + height <= monitor["height"]:
                shot = np.array(sct.grab({"left": monitor["left"] + left + x, "top": monitor["top"] + top + y,
                                          "width": width, "height": height}))
                return cv2.cvtColor(shot, cv2.COLOR_BGRA2BGR)
    return grab_screen()[y:y + height, x:x + width]

def exact_color_mask(img: Image, rgb: Color, tolerance: int = 0) -> Image:
    """Mask of every pixel in `img` (BGR, as grab_screen() returns) that
    matches `rgb` (an (R, G, B) tuple) exactly - or, with `tolerance`, to
    within that much on each channel."""
    r, g, b = rgb
    target_bgr = np.array([b, g, r])
    return cv2.inRange(img, target_bgr - tolerance, target_bgr + tolerance)

def find_exact_color(img: Image, rgb: Color) -> Optional[Point]:
    """Find the centroid of every pixel in `img` that matches `rgb` (an
    (R, G, B) tuple) exactly. Returns (x, y), or None if nothing matches.
    `img` is expected in BGR (as grab_screen() returns)."""
    ys, xs = np.where(exact_color_mask(img, rgb) > 0)
    if len(xs) == 0:
        return None
    return int(xs.mean()), int(ys.mean())

# ============================================================================
# STATE
# ============================================================================
# This module's own mutable state, changed while the macro runs - everything
# else (fixed config/tuning values) lives in magic_numbers.py instead. It is
# all in one `State` object, `state`, so it can be saved with a failure
# (State.snapshot()) and reset or replaced in one place.

# Set when the user presses [STOP] (or the stop hotkey); cleared when a run starts and ends. An Event, not a bool, so
# the waits can sleep on it and wake the moment it is set (see wait_interruptible()).
STOP_EVENT = threading.Event()

@dataclass
class State:
    # The GUI object (set by AdoptMeGUI), so macro code running on the worker
    # thread can send the macro window to the back and bring it up again - see
    # send_macro_window_to_back(). None when running without the GUI.
    macro_window: Any = None
    # When the game was last (re)joined: set when the loop starts and by every
    # successful leave_and_rejoin(); rejoin_reason() schedules the next
    # refresh REJOIN_INTERVAL after it. None until then.
    last_rejoin: Optional[float] = None
    # When a need was last resolved (or the loop started, or the game was last
    # rejoined): rejoin_reason() rejoins after NO_PROGRESS_REJOIN_INTERVAL
    # without any. None until the loop starts.
    last_progress: Optional[float] = None
    # When a need was last detected on screen (or the loop started, the game was
    # rejoined or a minigame was played, or the need bar couldn't be read):
    # respawn_if_no_detection() respawns after NO_DETECTION_RESPAWN_INTERVAL
    # without any. Separate from last_progress, so a respawn never delays the
    # NO_PROGRESS_REJOIN_INTERVAL rejoin. None until the loop starts.
    last_detection: Optional[float] = None
    # Detected action-button screen positions, keyed by need name (e.g. "hungry").
    # Populated by refresh_button_mapping() each time the macro walks to the
    # buttons, so this is a same-run cache, not persisted state - there's
    # nothing gained from saving it to disk since it's never trusted across a
    # run anyway (screen layout can change between sessions).
    button_positions: dict = field(default_factory=dict)
    # The run number of the currently executing workflow (run_workflow_loop()),
    # set by next_run_number() at the start of each. Every log_run_event() call
    # tags its line with this, so entries from different runs can be told apart
    # in the shared RUN_LOG_PATH file.
    run_number: Optional[int] = None
    # Stuck-need tracking (see record_detection()): how many times in a row each
    # need was attempted and still showed on the next check, and which needs the
    # last pass attempted. Reset by reset_need_tracking() at the start of every
    # workflow run and after a rejoin, so a streak never spans a stop and restart.
    attempt_streaks: dict = field(default_factory=dict)
    attempted_last: set = field(default_factory=set)
    # True while the last "detected:" line in the run log was "none matched", so
    # an unrecognised icon sitting on screen is logged once, not on every check.
    none_matched_logged: bool = False
    # Checks in a row that found no need, not yet written to the run log (see note_idle_check()): how many, and
    # when the first one was.
    idle_checks: int = 0
    idle_since: float = 0.0
    # Since when the screen has been completely white, without a break (see detect_white_screen()); None when it isn't.
    white_since: Optional[float] = None
    # The input capture that is running, if any (see toggle_input_capture()). A State field only so snapshot() sees it as text.
    capture: Optional[Any] = None
    run_capture: Optional[Any] = None                    # the recording of the minigame being played, see start_run_capture()
    minigame_detail: dict = field(default_factory=dict)  # what the player of the minigame being played says about it ("room")
    # The needs named by the last "detected:" line, and how many checks since then found exactly the same with no
    # handler run in between (not yet written to the run log - see note_repeat_check()).
    last_detected_body: Optional[str] = None
    repeat_checks: int = 0
    repeat_since: float = 0.0
    # False when the last wait for a need to clear (wait_until_need_gone(), walk_alternating()) ran out with the
    # need still showing - process_needs() then doesn't count the need as resolved.
    need_cleared: bool = True
    # True from the start of a run until rejoin_game() has looked once: the game not running then is just where the
    # run begins (it is started), not a failure.
    run_start_pending: bool = False
    # Needs switched off for the rest of this launch of the script because they
    # kept showing up without ever clearing. Deliberately not persisted (unlike
    # the game config): relaunching the script gives every enabled need a fresh
    # chance.
    disabled_needs: set = field(default_factory=set)
    # The in-task checks (see task_unscrew()): whether a need handler or setup
    # is running and its thread, whether it may play a minigame, when the checks
    # last ran, and whether they are running now.
    task_active: bool = False
    task_play_minigames: bool = True
    task_thread: Optional[int] = None
    last_task_unscrew: float = 0.0
    in_unscrew: bool = False
    # How many in-task checks in a row have seen the backpack open (see
    # close_backpack_if_stuck()).
    backpack_strikes: int = 0
    # Above 0 while a handler has the backpack open on purpose and closes it itself (see backpack_on_purpose()).
    backpack_wanted: int = 0
    # True while a handler that wants the pet focused runs (see wanting_pet_focus()).
    pet_focus_wanted: bool = False
    # When the minigame popup checks last ran, and how many "quiet:" lines have
    # been logged for the current stretch with no need resolved.
    last_minigame_popup_check: float = 0.0
    quiet_reported: int = 0
    # The need whose end wait (wait_until_need_gone(), walk_alternating() with a need) is running, and when a minigame popup
    # that is being held off for it was first seen (see minigame_popup()).
    end_wait_need: Optional[str] = None
    popup_held_since: Optional[float] = None

    def snapshot(self) -> dict[str, Any]:
        """The state as a JSON-friendly dict (the GUI object left out), for
        saving with a failure."""
        out = {}
        for item in fields(self):
            value = getattr(self, item.name)
            if item.name == "macro_window":
                value = None if value is None else "set"
            elif item.name == "capture":
                value = None if value is None else value.folder
            elif isinstance(value, set):
                value = sorted(value)
            out[item.name] = value
        return out

state = State()

# ============================================================================
# LOGGING
# ============================================================================

def migrate_debug_layout() -> None:
    """Move what an older version left in debug/ into the logs/ and failures/ subfolders (see LOGS_DIR / FAILURE_DIR), if it isn't
    there already, and drop the images/ folder it had (the debug_*.png views are not made any more). Never raises."""
    moves = [(os.path.join(cfg.DEBUG_DIR, name), os.path.join(cfg.LOGS_DIR, name))
             for name in ("run_log.txt", "run_counter.txt", "output.log", "output.log.old", "status.json")]
    old_images = os.path.join(cfg.DEBUG_DIR, "images")
    moves += [(os.path.join(old_images, "failures"), cfg.FAILURE_DIR)]
    try:
        for source, target in moves:
            if os.path.exists(source) and not os.path.exists(target):
                os.replace(source, target)
        for old_folder in (os.path.join(cfg.LOGS_DIR, "runs"), os.path.join(old_images, "reports"), os.path.join(cfg.DEBUG_DIR, "reports")):       # run logs and run charts: now together in RUNS_DIR
            if os.path.isdir(old_folder):
                os.makedirs(cfg.RUNS_DIR, exist_ok=True)
                for name in os.listdir(old_folder):
                    if not os.path.exists(os.path.join(cfg.RUNS_DIR, name)):
                        os.replace(os.path.join(old_folder, name), os.path.join(cfg.RUNS_DIR, name))
                if not os.listdir(old_folder):
                    os.rmdir(old_folder)
        for folder in (cfg.DEBUG_DIR, old_images):
            if os.path.isdir(folder):
                for name in os.listdir(folder):
                    if name.startswith("debug_") and name.endswith(".png"):
                        os.remove(os.path.join(folder, name))
        if os.path.isdir(old_images) and not os.listdir(old_images):
            os.rmdir(old_images)
    except OSError as error:
        logger.warning(f"Couldn't move the old debug files: {error}")

migrate_debug_layout()

def refresh_debug() -> int:
    """Empty the debug folder except the game config and recreate its subfolders (run
    numbers start from 1 again). Returns the number of entries removed.
    See docs/ARCHITECTURE.md#debugging-an-unattended-run.
    """
    removed = 0
    close_output_log()
    for name in os.listdir(cfg.DEBUG_DIR):
        path = os.path.join(cfg.DEBUG_DIR, name)
        if path == cfg.GAME_CONFIG_PATH:
            continue
        try:
            shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
            removed += 1
        except OSError as error:   # a locked file (a video still open, say): leave it, carry on with the rest
            logger.warning(f"Couldn't delete {name}: {error}")
    for directory in (cfg.LOGS_DIR, cfg.RUNS_DIR, cfg.FAILURE_DIR):
        os.makedirs(directory, exist_ok=True)
    return removed

def acknowledge_command(stem: str, text: str) -> None:
    """Tell the bot what became of the command in <stem>.json: "taken", or "dropped: <why>" (the bot waits for this before it says it asked)."""
    try:
        with open(os.path.join(cfg.COMMANDS_DIR, stem + ".ack"), "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        logger.warning(f"Could not write the acknowledgement of the command {stem}")


def discard_commands() -> None:
    """Remove every command waiting (left from before the macro started), answering each as dropped."""
    try:
        names = sorted(name for name in os.listdir(cfg.COMMANDS_DIR) if name.endswith(".json"))
    except OSError:
        return
    for name in names:
        with contextlib.suppress(OSError):
            os.remove(os.path.join(cfg.COMMANDS_DIR, name))
            acknowledge_command(name[:-len(".json")], "dropped: it was left from before the macro started")


def read_command() -> Optional[dict[str, Any]]:
    """The oldest command waiting in COMMANDS_DIR (a file written by the Discord bot: {"command": ..., "time": ...}), or None. The file is always removed
    once read, and the bot is told in an acknowledgement file whether the command was taken; a command that isn't one of COMMANDS, or is older than
    COMMAND_MAX_AGE seconds (left over from before the macro started, say), is none - and a warning says why, so that a bot host whose clock is off does
    not have every command ignored without a trace. A `set` command must also name a switch that exists (`kind` "need" and a NEED_NAMES name, or "option"
    and an OPTION_SWITCHES name or SHOW_DEBUG_LINES_OPTION) and give it a true or false `value`. One command is read per call, the oldest first, so two sent
    close together are both done, in order. Never raises. See docs/ARCHITECTURE.md#remote-control."""
    try:
        names = sorted(name for name in os.listdir(cfg.COMMANDS_DIR) if name.endswith(".json"))
    except OSError:
        return None
    if not names:
        return None
    path = os.path.join(cfg.COMMANDS_DIR, names[0])
    stem = names[0][:-len(".json")]

    def dropped(why: str) -> None:
        logger.warning(f"Command from outside dropped: {why}")
        acknowledge_command(stem, "dropped: " + why)

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        data = None
        dropped(f"the command file could not be read ({error})")
    try:
        os.remove(path)
    except OSError:
        pass
    if data is None:
        return None
    if not isinstance(data, dict) or data.get("command") not in cfg.COMMANDS:
        dropped(f"not a command the macro knows ({str(data)[:80]})")
        return None
    sent = data.get("time")
    if not isinstance(sent, (int, float)):
        dropped(f"{data['command']} has no time")
        return None
    age = time.time() - sent
    if abs(age) > cfg.COMMAND_MAX_AGE:
        dropped(f"{data['command']} is {age:.0f} s old (more than COMMAND_MAX_AGE; if the PCs' clocks differ, set them right)")
        return None
    if data["command"] == "set":
        switches = {"need": cfg.NEED_NAMES, "option": (*cfg.OPTION_SWITCHES, cfg.SHOW_DEBUG_LINES_OPTION)}.get(str(data.get("kind")), ())
        if data.get("name") not in switches or not isinstance(data.get("value"), bool):
            dropped(f"set {data.get('kind')} {data.get('name')} is not a switch the macro has")
            return None
    acknowledge_command(stem, "taken")
    return data

def next_run_number() -> int:
    """Return a fresh run number, persisted in RUN_COUNTER_PATH so numbers
    stay unique across separate launches of the script, not just within one
    session."""
    try:
        with open(cfg.RUN_COUNTER_PATH, "r", encoding="utf-8") as f:
            n = int(f.read().strip())
    except (FileNotFoundError, ValueError):
        n = 0
    n += 1
    with open(cfg.RUN_COUNTER_PATH, "w", encoding="utf-8") as f:
        f.write(str(n))
    return n

def reset_need_tracking() -> None:
    """Forget the stuck-need streaks (see record_detection())."""
    state.attempt_streaks.clear()
    state.attempted_last = set()
    state.none_matched_logged = False
    state.last_detected_body = None

def note_idle_check() -> None:
    """Count one check that found no need. They are logged together, as one "idle checks: N over Ss" line
    (flush_idle_checks()), so a long wait doesn't write a line every few seconds - and the run report can still
    count every check, not just the ones that found something."""
    state.last_detected_body = None      # whatever shows up next is news again
    if state.idle_checks == 0:
        state.idle_since = time.time()
    state.idle_checks += 1

def note_repeat_check() -> None:
    """Count one check that found the same needs as the "detected:" line above it, with nothing handled since
    (e.g. disabled needs sitting on screen). Logged together as "same checks: N over Ss" by flush_idle_checks()."""
    if state.repeat_checks == 0:
        state.repeat_since = time.time()
    state.repeat_checks += 1

def flush_idle_checks() -> None:
    """Write the counted idle checks (note_idle_check()) and repeated checks (note_repeat_check()) to the run log
    and start counting again."""
    if state.idle_checks:
        log_run_event(f"idle checks: {state.idle_checks} over {time.time() - state.idle_since:.0f}s")
        state.idle_checks = 0
    if state.repeat_checks:
        log_run_event(f"same checks: {state.repeat_checks} over {time.time() - state.repeat_since:.0f}s")
        state.repeat_checks = 0

def flush_idle_checks_if_due() -> None:
    """flush_idle_checks() once QUIET_LOG_INTERVAL has passed since the first of them, so a long idle stretch
    reaches the log (and a crash loses at most that much)."""
    now = time.time()
    if (state.idle_checks and now - state.idle_since >= cfg.QUIET_LOG_INTERVAL) or \
            (state.repeat_checks and now - state.repeat_since >= cfg.QUIET_LOG_INTERVAL):
        flush_idle_checks()

def log_run_event(message: str) -> None:
    """Append one timestamped, run-tagged line to RUN_LOG_PATH and to that run's own file.
    See docs/ARCHITECTURE.md#debugging-an-unattended-run.
    """
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [RUN {state.run_number}] {message}\n"
    with open(cfg.RUN_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line)
    if state.run_number is not None:
        try:
            os.makedirs(cfg.RUNS_DIR, exist_ok=True)
            with open(os.path.join(cfg.RUNS_DIR, f"run{state.run_number}.txt"), "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass   # the combined log above has it

# What the macro knows about how this run is going, written to STATUS_PATH by
# write_status() so it can be checked without opening the GUI.
@dataclass
class RunStats:
    """What the macro knows about how this run is going, written to STATUS_PATH
    by write_status() so it can be checked without opening the GUI. Field
    names are the keys of status.json."""
    run: Optional[int] = None
    started: Optional[str] = None
    cycles: int = 0
    last_detected: list[str] = field(default_factory=list)
    resolved: dict[str, int] = field(default_factory=dict)           # per need
    failed: dict[str, int] = field(default_factory=dict)             # per need
    failures: int = 0
    quiet_minutes: int = 0                                           # whole minutes since a need was last resolved
    cpu_percent: Optional[float] = None                              # the script's own, from ResourceMonitor
    memory_mb: Optional[int] = None
    recoveries: int = 0
    disconnects: int = 0
    scheduled_rejoins: int = 0
    focus_resumes: int = 0
    last_resolved: Optional[dict[str, str]] = None                   # {"need", "time"}
    last_failure: Optional[dict[str, Optional[str]]] = None          # {"time", "reason", "screenshot"}
    stopped: Optional[str] = None                                    # when what was running was ended by the user ([STOP], the window's X, the Discord bot) or finished: no more updates are due

    def reset(self) -> None:
        """Start afresh (a workflow was just started)."""
        fresh = RunStats()
        for item in fields(self):
            setattr(self, item.name, getattr(fresh, item.name))
        self.run, self.started = state.run_number, time.strftime("%Y-%m-%d %H:%M:%S")

run_stats = RunStats()

# Logging. Everything the macro says goes through `logger` (debug: how it got there; info: what it is doing; warning: something it
# couldn't do; error: a crash). Where the lines go is decided by handlers: OUTPUT_LOG below (a file) and the GUI's DebugCapture (the
# console, whose level can be raised to hide the debug lines). The run log (log_run_event()) is the record the report is drawn from and
# is not part of this. See docs/ARCHITECTURE.md#debugging-an-unattended-run.
logger = logging.getLogger("adoptme")
logger.setLevel(logging.DEBUG)

class OutputLog(logging.Handler):
    """output.log (OUTPUT_LOG_PATH): every line logged, with its time and level, appended to across runs and kept to OUTPUT_LOG_MAX_BYTES
    (past it the file moves to output.log.old, replacing the last one). The file is opened with the first line and again after
    close_output_log(), which anything that deletes or moves it needs first (on Windows an open file can't be). A status line (a
    record with `status`, "Waiting for a need...") is written the first time only. Never raises."""

    def __init__(self) -> None:
        super().__init__()
        self.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s", "%Y-%m-%d %H:%M:%S"))
        self.file: Optional[logging.handlers.RotatingFileHandler] = None
        self.status_written = False

    def emit(self, record: logging.LogRecord) -> None:
        status = bool(getattr(record, "status", False))
        if status and self.status_written:
            return
        self.status_written = status
        try:
            if self.file is None:
                self.file = logging.handlers.RotatingFileHandler(cfg.OUTPUT_LOG_PATH, maxBytes=cfg.OUTPUT_LOG_MAX_BYTES, backupCount=1, encoding="utf-8")
                self.file.namer = lambda name: name[:-2] + ".old"          # output.log.1 -> output.log.old
                self.file.setFormatter(self.formatter)
            self.file.emit(record)
        except (OSError, ValueError):
            pass

    def close_file(self) -> None:
        if self.file is not None:
            self.file.close()
            self.file = None

OUTPUT_LOG = OutputLog()
logger.addHandler(OUTPUT_LOG)

def close_output_log() -> None:
    """Close output.log if it's open (it reopens with the next line). Needed before anything deletes or moves the file - on Windows an
    open file can't be - e.g. refresh_debug()."""
    OUTPUT_LOG.close_file()

def save_failure_report(reason: str) -> Optional[str]:
    """Save what was going on when `reason` went wrong as a folder in FAILURE_DIR
    (screen.png, need_icons.png, info.txt, state.json). Returns the screen.png path or
    None; never raises. See docs/ARCHITECTURE.md#debugging-an-unattended-run.
    """
    try:
        slug = re.sub(r"[^a-z0-9]+", "_", reason.lower()).strip("_")[:40]
        name = f"{time.strftime('%Y%m%d_%H%M%S')}_run{state.run_number}_{slug}"
        folder = os.path.join(cfg.FAILURE_DIR, name)
        os.makedirs(folder, exist_ok=True)
        cv2.imwrite(os.path.join(folder, "screen.png"), grab_full_screen())
        try:
            cv2.imwrite(os.path.join(folder, "need_icons.png"), need_icon_strip(grab_screen()))
        except Exception:
            pass   # Roblox isn't there to capture - the full screenshot shows why
        since = f"{time.time() - state.last_progress:.0f}s ago" if state.last_progress else "never"
        with open(os.path.join(folder, "info.txt"), "w", encoding="utf-8") as f:
            f.write(f"reason: {reason}\ntime: {time.strftime('%Y-%m-%d %H:%M:%S')}\nrun: {state.run_number}\n"
                    f"last detected: {', '.join(run_stats.last_detected) or 'nothing'}\n"
                    f"disabled needs: {', '.join(sorted(state.disabled_needs)) or 'none'}\n"
                    f"last need resolved: {since}\n")
        with open(os.path.join(folder, "state.json"), "w", encoding="utf-8") as f:
            json.dump({"state": state.snapshot(), "run_stats": asdict(run_stats)}, f, indent=2, default=str)
        for old in sorted(os.listdir(cfg.FAILURE_DIR))[:-cfg.MAX_FAILURE_FOLDERS]:
            path = os.path.join(cfg.FAILURE_DIR, old)
            shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
        return f"{name}/screen.png"
    except Exception as e:
        logger.warning(f"Couldn't save a failure report: {e}")
        return None

STATUS_LOCK = threading.Lock()

def write_status() -> None:
    """Write run_stats, the currently disabled needs and the time to
    STATUS_PATH (replacing it atomically). Never raises. The worker thread writes it as the run goes on and Tk's thread when the window opens with no loop
    and when it closes (mark_stopped(), while the worker is still unwinding): one writer at a time, or the two would share the temporary file."""
    try:
        with STATUS_LOCK:
            status = {**asdict(run_stats), "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                      "disabled_needs": sorted(state.disabled_needs)}
            tmp = cfg.STATUS_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(status, f, indent=2)
            os.replace(tmp, cfg.STATUS_PATH)
    except (OSError, TypeError):
        pass

def mark_stopped() -> None:
    """Note in the status file that the macro is no longer running anything on purpose, so that the status not being updated any more is not
    taken for a stuck macro (the Discord bot's alert)."""
    run_stats.stopped = time.strftime("%Y-%m-%d %H:%M:%S")
    write_status()

def log_failure(reason: str, screenshot: bool = True) -> None:
    """Record that something went wrong: print it, add it to the run log,
    save a screenshot of what was on screen, and note it in the status
    file. Doesn't change what the macro does next."""
    mark_run_capture(f"failure: {reason}")
    shot = save_failure_report(reason) if screenshot else None
    detail = f"{reason}" + (f" (screenshot: failures/{shot})" if shot else "")
    logger.error(detail)
    log_run_event(f"FAILURE: {detail}")
    run_stats.failures += 1
    run_stats.last_failure = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "reason": reason, "screenshot": shot}
    write_status()

def reset_run_stats() -> None:
    """Start the status counters afresh (a workflow was just started)."""
    run_stats.reset()
    write_status()

# ============================================================================
# GAME CONFIG
# ============================================================================
# Game state that needs to persist across separate launches of the script
# (unlike BUTTON_POSITIONS/CURRENT_RUN_NUMBER above) - see side_quest().

# The GUI (Tk main thread) and the workflow (run_async's worker thread) both
# read and write this file, so every access goes through the functions below,
# each holding this one lock - a reader can never see a half-written file (it
# would read as "no config" and silently give the defaults: the private server
# link gone, the minigames switched off).
GAME_CONFIG_LOCK = threading.Lock()

def _read_game_config() -> dict[str, Any]:
    """load_game_config() without taking the lock (for callers that hold it)."""
    defaults: dict[str, Any] = {
        "money_collected": 0,
        "lure_timer": time.time(),  # due immediately on a fresh config
        "tree_timer": time.time(),  # likewise
        "money_reset_boundary": None,   # the last daily MONEY_RESET_HOUR reset side_quest() has dealt with (None: not yet)
        "side_quest_enabled": True, # whether side_quest() is allowed to run at all
        "resume_on_focus_loss": False,  # whether the loop takes Roblox's focus back instead of stopping
        "record_next_run": False,   # record the next run as a low quality video; cleared when that run starts
        "blackout_hotkey_enabled": True,   # whether HOTKEYS["blackout"] blanks the screen (off: that key can be typed in Roblox)
        "needs_enabled": {},        # {need: whether it is acted on}, the Debug tab's switches; a need not in it follows ENABLED_NEEDS (see need_is_enabled())
        **{f"{name}_enabled": False for name in cfg.MINIGAME_LABELS},  # which Halloween minigames get played (see minigame_popup())
        "private_server_link": "",  # used by leave_and_rejoin(); kept here, not in the source, since it's a join key
        "use_private_server": True,   # whether a rejoin goes to that private server (off: a public one, with the link kept)
    }
    try:
        with open(cfg.GAME_CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
    except (FileNotFoundError, ValueError):
        return defaults
    if "hauntlet_enabled" in config:   # before it was called Hauntlet 2
        config.setdefault("hauntlet_2_enabled", config.pop("hauntlet_enabled"))
    return {**defaults, **config}

def load_game_config() -> dict[str, Any]:
    """Load GAME_CONFIG_PATH, or these defaults if it doesn't exist yet
    (first run, or reset via reset_game_config())."""
    with GAME_CONFIG_LOCK:
        return _read_game_config()

def save_game_config(config: dict[str, Any]) -> None:
    """Persist `config` to GAME_CONFIG_PATH atomically; callers hold GAME_CONFIG_LOCK
    (use update_game_config()). See docs/ARCHITECTURE.md#persisted-game-config.
    """
    tmp = cfg.GAME_CONFIG_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(config, f)
        os.replace(tmp, cfg.GAME_CONFIG_PATH)
    except OSError:
        with open(cfg.GAME_CONFIG_PATH, "w", encoding="utf-8") as f:   # the move wasn't allowed (the file is open elsewhere): write it in place
            json.dump(config, f)

def update_game_config(mutate: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
    """Load the config, call mutate(config) to change it in place, save it,
    and return it - all atomically with respect to other callers."""
    with GAME_CONFIG_LOCK:
        config = _read_game_config()
        mutate(config)
        save_game_config(config)
        return config

def reset_game_config() -> None:
    """Delete GAME_CONFIG_PATH so the next load starts from the defaults -
    except the private server link, which isn't progress and is kept."""
    with GAME_CONFIG_LOCK:
        link = _read_game_config()["private_server_link"]
        try:
            os.remove(cfg.GAME_CONFIG_PATH)
        except FileNotFoundError:
            pass
        if link:
            save_game_config({**_read_game_config(), "private_server_link": link})

# ============================================================================
# ICON PROCESSING
# ============================================================================

def preprocess_icon(icon_img: Image) -> Image:
    """High-contrast black & white version of an icon (color-independent)."""
    gray = cv2.cvtColor(icon_img, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=cfg.ICON_CLAHE_CLIP, tileGridSize=cfg.ICON_CLAHE_TILE)
    enhanced = clahe.apply(gray)
    _, binary = cv2.threshold(enhanced, cfg.ICON_BW_THRESHOLD, cfg.PIXEL_MAX, cv2.THRESH_BINARY)
    return binary

def icon_disc_mask(width: int, height: int, radius_fraction: float) -> Image:
    """Boolean mask, True inside the disc (centred, `radius_fraction` of half the width across) of a width x height image."""
    yy, xx = np.mgrid[:height, :width]
    cx, cy = (width - 1) / 2, (height - 1) / 2
    return (xx - cx) ** 2 + (yy - cy) ** 2 <= (radius_fraction * width / 2) ** 2

@lru_cache(maxsize=None)
def icon_match_mask(size: tuple[int, int], radius_fraction: float, left_half: bool) -> Image:
    """The part of a signature (see icon_signature()) that is compared: the disc inside the icon's ring, and only
    its left half when `left_half`."""
    mask = icon_disc_mask(size[0], size[1], radius_fraction)
    return mask[:, :mask.shape[1] // 2] if left_half else mask

def icon_signature(icon_img: Image) -> Image:
    """Comparable form of an icon: outside-the-ring pixels blacked out, preprocessed,
    resized, left half only. See docs/ARCHITECTURE.md#need-icon-matching.
    """
    height, width = icon_img.shape[:2]
    icon_img = np.where(icon_disc_mask(width, height, cfg.ICON_MATCH_RADIUS_FRACTION)[:, :, None], icon_img, 0).astype(icon_img.dtype)
    proc = cv2.resize(preprocess_icon(icon_img), cfg.ICON_COMPARE_SIZE).astype(np.float32) / cfg.PIXEL_MAX
    if cfg.MATCH_ONLY_LEFT_HALF:
        proc = proc[:, :proc.shape[1] // 2]
    return proc

def compare_signatures(sig1: Image, sig2: Image) -> float:
    """Return a 0-1 similarity score (1 = identical) between two signatures: one minus the mean squared difference
    over the part inside the icon's ring (icon_match_mask())."""
    mask = icon_match_mask(cfg.ICON_COMPARE_SIZE, cfg.ICON_MATCH_RADIUS_FRACTION, cfg.MATCH_ONLY_LEFT_HALF)
    return 1.0 - float(np.sum(((sig1 - sig2) ** 2) * mask) / np.count_nonzero(mask))

# ============================================================================
# NEED ICON DETECTION
# ============================================================================

def extract_icon(img: Image, cx: int, cy: int, radius: int, padding: int = cfg.ICON_EXTRACT_PADDING) -> Image:
    r = radius + padding
    x0, x1 = max(0, cx - r), min(img.shape[1], cx + r)
    y0, y1 = max(0, cy - r), min(img.shape[0], cy + r)
    return img[y0:y1, x0:x1].copy()

def need_icon_strip(img: Image) -> Image:
    """The part of `img` (the top-left of the Roblox window, in the reference
    space) the need icons are looked for in."""
    return img[0:int(cfg.REFERENCE_HEIGHT * cfg.NEED_ICON_TOP_PERCENT), 0:int(cfg.REFERENCE_WIDTH * cfg.NEED_ICON_WIDTH_PERCENT)]

# The strip the icons were last searched for in, and what was found: while the strip looks the same, the circle
# search is skipped (see detect_need_icons()).
_NEED_STRIP_LOCK = threading.Lock()
_NEED_STRIP_CACHE: dict[str, Any] = {"strip": None, "found": [], "reuses": 0}

def strip_changed_fraction(before: Image, after: Image) -> float:
    """The fraction of pixels that differ between two same-size strips by more than NEED_STRIP_CHANGED_LEVEL."""
    changed = cv2.absdiff(before, after).max(axis=2) > cfg.NEED_STRIP_CHANGED_LEVEL
    return np.count_nonzero(changed) / changed.size

def detect_need_icons(fresh: bool = False) -> tuple[list[tuple[int, int, int]], Image]:
    """Find the round need icons in the top-left strip by Hough circles: ((cx, cy, radius)
    left to right, the captured image). The search is skipped while the strip is
    practically unchanged; `fresh` always searches. See docs/ARCHITECTURE.md#need-icon-detection.
    """
    margin = cfg.ICON_CROP_RADIUS + cfg.ICON_EXTRACT_PADDING + cfg.ICON_SHIFT_TOLERANCE + 2
    strip_w, strip_h = int(cfg.REFERENCE_WIDTH * cfg.NEED_ICON_WIDTH_PERCENT), int(cfg.REFERENCE_HEIGHT * cfg.NEED_ICON_TOP_PERCENT)
    img = grab_screen_region(0, 0, min(cfg.REFERENCE_WIDTH, strip_w + margin), min(cfg.REFERENCE_HEIGHT, strip_h + margin))

    top_strip = need_icon_strip(img).copy()
    top_strip[0:int(cfg.REFERENCE_HEIGHT * cfg.NEED_ICON_BLANK_HEIGHT), 0:int(cfg.REFERENCE_WIDTH * cfg.NEED_ICON_BLANK_WIDTH)] = 0

    cache = _NEED_STRIP_CACHE
    with _NEED_STRIP_LOCK:   # the need-gone watcher thread scans too
        reusable = (not fresh and cache["strip"] is not None and cache["strip"].shape == top_strip.shape
                    and cache["reuses"] < (cfg.NEED_STRIP_MAX_REUSES if cache["found"] else cfg.NEED_STRIP_MAX_EMPTY_REUSES)
                    and strip_changed_fraction(cache["strip"], top_strip) < cfg.NEED_STRIP_MAX_CHANGED_FRACTION)
        if reusable:
            cache["reuses"] += 1
            found = list(cache["found"])
        else:
            gray = cv2.medianBlur(cv2.cvtColor(top_strip, cv2.COLOR_BGR2GRAY), cfg.NEED_ICON_BLUR_KERNEL)
            circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, dp=1, minDist=cfg.NEED_ICON_MIN_DISTANCE,
                                       param1=cfg.NEED_ICON_HOUGH_EDGE, param2=cfg.NEED_ICON_HOUGH_VOTES,
                                       minRadius=cfg.NEED_ICON_MIN_RADIUS, maxRadius=cfg.NEED_ICON_MAX_RADIUS)
            found = [] if circles is None else sorted((int(x), int(y), int(r)) for x, y, r in circles[0])
            cache.update(strip=top_strip, found=found, reuses=0)

    return found, img

def icon_variants(img: Image, cx: int, cy: int) -> list[Image]:
    """Crops of the icon centered within +/- ICON_SHIFT_TOLERANCE px of
    (cx, cy), so a detection that's a pixel or two off-center still lines up
    with the saved reference."""
    shifts = range(-cfg.ICON_SHIFT_TOLERANCE, cfg.ICON_SHIFT_TOLERANCE + 1)
    return [extract_icon(img, cx + dx, cy + dy, cfg.ICON_CROP_RADIUS) for dx in shifts for dy in shifts]

# (need file paths and modification times, [(name, signature)]) - see need_signatures().
_NEED_SIGNATURES: tuple[Any, list[tuple[str, Image]]] = (None, [])

def need_signatures() -> list[tuple[str, Image]]:
    """[(need name, signature)] for every saved icon under NEEDS_DIR, cached and reloaded when
    a file changes. See docs/ARCHITECTURE.md#need-icon-matching.
    """
    global _NEED_SIGNATURES
    files = sorted(os.path.join(root, f) for root, _, names in os.walk(cfg.NEEDS_DIR) for f in names if f.endswith('.png'))
    key = tuple((f, os.path.getmtime(f)) for f in files)
    if _NEED_SIGNATURES[0] != key:
        loaded = []
        for f in files:
            icon = cv2.imread(f)
            if icon is not None:
                loaded.append((os.path.splitext(os.path.basename(f))[0], icon_signature(icon)))
        _NEED_SIGNATURES = (key, loaded)
    return _NEED_SIGNATURES[1]

def find_matching_need(icon_variants: list[Image]) -> tuple[Optional[str], float]:
    """Compare a detected icon (its icon_variants() crops) against the saved
    needs (need_signatures()), scoring each by its best-aligned variant.
    Returns (name, score)."""
    live_signatures = [icon_signature(v) for v in icon_variants]
    best_match, best_score = None, 0.0
    for name, saved_signature in need_signatures():
        score = max(compare_signatures(sig, saved_signature) for sig in live_signatures)
        if score > best_score:
            best_match, best_score = name, score

    if best_score >= cfg.ICON_MATCH_THRESHOLD:
        return best_match, best_score
    return None, best_score

def prompt_rename_need(icon_img: Image, idx: int) -> str:
    """Save a new need icon and ask for its name (dialog or console); a cancelled dialog saves
    it as unnamed_<idx>. See docs/ARCHITECTURE.md#process_needs-step-by-step.
    """
    logger.info("New need detected")
    temp_path = os.path.join(cfg.NEEDS_DIR, f"temp_{idx}.png")
    cv2.imwrite(temp_path, preprocess_icon(icon_img))

    prompt = "Need name (hungry/thirsty/dirty/potty/sleepy/catch/walk/other):"
    if state.macro_window is not None:
        answer = state.macro_window.run_on_ui_thread(
            lambda: simpledialog.askstring("New need", prompt, parent=state.macro_window.root), timeout=None)
        focus_roblox_click()   # the dialog took the focus
    else:
        print(f"{prompt} ", end="", flush=True)                           # (a question to the person at the terminal, not a log line)
        answer = input()
    need_name = (answer or "").strip().lower() or f"unnamed_{idx}"

    os.rename(temp_path, os.path.join(cfg.NEEDS_DIR, f"{need_name}.png"))
    logger.info(f"Saved: {need_name}")
    return need_name

def identify_icons(found_icons: list[tuple[int, int, int]], full_img: Image) -> Iterator[tuple[int, Image, Optional[str], float]]:
    """Match each detected icon against the saved needs. Yields
    (index, icon_image, need_name_or_None, score) per icon, checking
    check_running() between icons."""
    for idx, (cx, cy, _) in enumerate(found_icons):
        check_running()
        icon_img = extract_icon(full_img, cx, cy, cfg.ICON_CROP_RADIUS)
        need_name, score = find_matching_need(icon_variants(full_img, cx, cy))
        yield idx, icon_img, need_name, score

def scan_needs(fresh: bool = False) -> list[Optional[str]]:
    """One-shot snapshot: every need name currently detected on screen. `fresh`
    makes the circle search run even if the strip looks unchanged (see
    detect_need_icons())."""
    found_icons, full_img = detect_need_icons(fresh=fresh)
    return [name for _, _, name, _ in identify_icons(found_icons, full_img)]

def _need_cleared(need_name: str) -> bool:
    """True if `need_name` is missing on two checks in a row (the second a fresh search) - the
    flicker guard shared by wait_until_need_gone() and _watch_need_gone().
    See docs/ARCHITECTURE.md#waiting-for-a-need-to-clear.
    """
    for attempt in range(2):
        if need_name in scan_needs(fresh=attempt > 0):   # the second look must not just repeat the first
            return False
        if attempt == 0:
            wait_interruptible(cfg.NEED_GONE_FLICKER_RECHECK_DELAY)
    return True

@contextlib.contextmanager
def end_wait(need_name: Optional[str]) -> Iterator[None]:
    """Mark `need_name`'s end wait as running (state.end_wait_need), so that a minigame popup waits for it - see minigame_popup()."""
    state.end_wait_need = need_name
    try:
        yield
    finally:
        state.end_wait_need = None

def wait_until_need_gone(need_name: str, max_wait: float = cfg.NEED_GONE_MAX_WAIT, poll_interval: float = cfg.NEED_GONE_POLL_INTERVAL) -> None:
    """Wait up to `max_wait` for `need_name` to clear, polling every `poll_interval`. A
    timeout is logged as a failure and sets state.need_cleared False.
    See docs/ARCHITECTURE.md#waiting-for-a-need-to-clear.
    """
    logger.debug(f"Waiting up to {max_wait}s for {need_name} to clear...")
    state.need_cleared = True
    deadline = time.time() + max_wait
    with end_wait(need_name):
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                logger.debug(f"{need_name} still showing after {max_wait}s, moving on")
                log_failure(f"{need_name} still showing after {max_wait}s")
                state.need_cleared = False
                return
            wait_interruptible(min(poll_interval, remaining))
            if _need_cleared(need_name):
                logger.debug(f"{need_name} cleared")
                return

def _watch_need_gone(need_name: str, cleared_event: threading.Event, stop_event: threading.Event, answers: Optional[list] = None) -> None:
    """Watcher-thread body for walk_alternating(): sets `cleared_event` when the need is
    gone, exits quietly on stop and never lets StopRequested/FocusLost escape.
    See docs/ARCHITECTURE.md#checking-a-need-in-parallel-with-movement.
    """
    reported = False
    try:
        while not stop_event.is_set():
            if stop_event.wait(cfg.NEED_GONE_POLL_INTERVAL):
                return
            try:
                cleared = _need_cleared(need_name)
            except (StopRequested, FocusLost):
                raise
            except Exception:                 # (one look that failed - a screenshot, a detector - is not the end of the watching: the next one is tried)
                if not reported:
                    reported = True
                    logger.exception(f"Watching for {need_name} to clear: a look failed (the watching goes on)")
                continue
            if answers is not None:
                answers.append(cleared)       # the last answer tells walk_alternating() whether the need was still showing
            if cleared:
                cleared_event.set()
                return
    except (StopRequested, FocusLost):
        return

# ============================================================================
# CLICKING
# ============================================================================

def hover_move(x: float, y: float, duration: float = cfg.CLICK_MOVE_DURATION) -> None:
    """Move the mouse to a reference-space position with SendInput, nudging so Roblox sees
    hover. See docs/ARCHITECTURE.md#click--input-primitives.
    """
    x, y = to_screen(x, y)
    pydirectinput.moveTo(x, y, duration=duration)
    time.sleep(cfg.CLICK_SETTLE_DELAY)
    pydirectinput.moveTo(min(pyautogui.size()[0] - 1, x + cfg.HOVER_NUDGE_PIXELS), y)
    time.sleep(cfg.CLICK_SETTLE_DELAY)
    pydirectinput.moveTo(x, y)
    time.sleep(cfg.CLICK_SETTLE_DELAY)

def hover_click(x: float, y: float, duration: float = cfg.CLICK_MOVE_DURATION) -> None:
    """hover_move() to (x, y), then click."""
    hover_move(x, y, duration)
    pydirectinput.click()
    time.sleep(cfg.POST_CLICK_DELAY)

# While a need handler (or setup) runs - see task_unscrew() - every
# wait_interruptible() also runs the per-cycle checks (unscrew()) now and
# then, so a popup, a stray window or a minigame doesn't wait for the handler
# to finish. Only on the thread that started the task, and never from inside
# unscrew() itself.

@contextlib.contextmanager
def task_unscrew(play_minigames: bool = True) -> Iterator[None]:
    """Within this block wait_interruptible() runs unscrew(in_task=True) every
    UNSCREW_TASK_INTERVAL; `play_minigames` False (setup) only declines minigames.
    See docs/ARCHITECTURE.md#need-handlers.
    """
    previous = (state.task_active, state.task_play_minigames, state.task_thread)
    state.task_active, state.task_play_minigames, state.task_thread = True, play_minigames, threading.get_ident()
    state.last_task_unscrew = time.time()
    try:
        yield
    finally:
        state.task_active, state.task_play_minigames, state.task_thread = previous

def task_unscrew_tick() -> None:
    """The hook in wait_interruptible(): run the in-task checks if one is due.
    Raises TaskInterrupted if a minigame was played, the game was rejoined or a popup spoiled the task."""
    if (not state.task_active or state.in_unscrew or threading.get_ident() != state.task_thread
            or time.time() - state.last_task_unscrew < cfg.UNSCREW_TASK_INTERVAL):
        return
    state.in_unscrew = True
    try:
        played = unscrew(in_task=True, play_minigames=state.task_play_minigames)
    finally:
        state.in_unscrew = False
        state.last_task_unscrew = time.time()
    if played == "played":
        release_all_inputs()
        raise TaskInterrupted("a minigame")
    if played == "rejoined":
        release_all_inputs()
        raise TaskInterrupted("a rejoin")
    if played == "aborted":
        release_all_inputs()
        respawn_character()   # unlike a minigame or a rejoin this doesn't respawn by itself: start the retry from a known spot
        raise TaskInterrupted("a popup")

def wait_interruptible(duration: float) -> None:
    """Sleep `duration` in STOP_CHECK_INTERVAL chunks, raising StopRequested/FocusLost as
    soon as either is noticed. See docs/ARCHITECTURE.md#stopping--focus-safety.
    """
    end = time.monotonic() + duration   # a deadline, so the time the in-task checks take counts too
    while time.monotonic() < end:
        check_running()
        task_unscrew_tick()
        remaining = end - time.monotonic()
        if remaining > 0:
            STOP_EVENT.wait(min(cfg.STOP_CHECK_INTERVAL, remaining))   # returns early if [STOP] is pressed; check_running() then raises
    check_running()

def wait_stoppable(duration: float) -> None:
    """Like wait_interruptible() but only [STOP] interrupts it, not Roblox
    losing focus. Used while the game is being closed and relaunched, when
    there is no focused Roblox window to lose."""
    end = time.time() + duration
    while time.time() < end:
        check_stop()
        STOP_EVENT.wait(min(cfg.STOP_CHECK_INTERVAL, max(0.0, end - time.time())))
    check_stop()

HELD_KEYS: set[str] = set()                    # the keys key_down() has pressed and key_up() has not let go of
HELD_KEYS_LOCK = threading.Lock()

def key_down(key: str) -> None:
    """Press `key` and keep it, until key_up(), in HELD_KEYS - so that release_all_inputs() lets go of exactly the keys that are down, whichever they are.
    Nothing is pressed once a stop has been asked for (StopRequested): a stop must not be followed by one more press."""
    check_stop()
    with HELD_KEYS_LOCK:
        HELD_KEYS.add(key)                         # (before the press: one that failed half-way is let go of too)
    pydirectinput.keyDown(key)

def key_up(key: str) -> None:
    """Let go of a key key_down() pressed (it stays in HELD_KEYS if that raises)."""
    pydirectinput.keyUp(key)                       # (one that raises - the fail-safe - stays in HELD_KEYS, for release_all_inputs() to try again)
    with HELD_KEYS_LOCK:
        HELD_KEYS.discard(key)

def mouse_down(button: str = "left") -> None:
    """Press a mouse button (let go of with pydirectinput.mouseUp, and by release_all_inputs() if that is missed); not once a stop has been asked for."""
    check_stop()
    pydirectinput.mouseDown(button=button)

def unchecked(function: Callable[..., Any]) -> Callable[..., Any]:
    """`function` (a PyDirectInput key or mouse call) without its fail-safe check and its pause: the call itself, which `functools.wraps` keeps as __wrapped__.
    Only for letting go of what is held. With the cursor in a corner - the fail-safe's sign - every checked call raises, and the keys and buttons the macro
    holds would stay held after the very stop that corner asked for. The fail-safe stays on for everything else, and the cursor is not moved."""
    return getattr(function, "__wrapped__", function)

def release_all_inputs() -> None:
    """Best-effort safety net: release every key the macro holds (those in HELD_KEYS, and the keys of cfg.HELD_KEYS) and the mouse buttons that are down,
    called when a background task ends and at once when a stop is asked for. The releases skip the fail-safe check (unchecked()): see there.
    See docs/ARCHITECTURE.md#stopping--focus-safety.
    """
    with HELD_KEYS_LOCK:
        keys = [*dict.fromkeys((*HELD_KEYS, *cfg.HELD_KEYS))]
        HELD_KEYS.clear()
    for key in keys:
        try:
            unchecked(pydirectinput.keyUp)(key)
        except Exception:
            pass
    # Only a button that is down: a right button released with none pressed is a click as far as the window under the mouse is
    # concerned, and over its border or title bar that opens the window's menu and takes the focus from the game.
    try:
        held = pressed_inputs()
    except Exception:                              # (it must not stop the caller from reaching what it does next, such as letting the window be used again)
        held = set()                               # (not known: a button released with none pressed is a click, so none is)
    for button in ("left", "right"):
        if f"mouse_{button}" not in held:
            continue
        try:
            unchecked(pydirectinput.mouseUp)(button=button)
        except Exception:
            pass

# ============================================================================
# BUTTON DETECTION
# ============================================================================

def detect_buttons() -> list[Point]:
    """Detect purple, white-outlined action buttons in the middle of the screen.
    Assumes the character is already positioned where the buttons are visible."""
    if not focus_roblox_click():
        return []

    img = grab_screen()
    h, w = img.shape[:2]
    y0, y1 = int(h * cfg.BUTTON_BAND_Y[0]), int(h * cfg.BUTTON_BAND_Y[1])
    x0, x1 = int(w * cfg.BUTTON_BAND_X[0]), int(w * cfg.BUTTON_BAND_X[1])
    band = img[y0:y1, x0:x1]
    band_h, band_w = band.shape[:2]
    hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)

    purple_mask = cv2.inRange(hsv, *cfg.PURPLE_RANGE)
    white_mask = cv2.inRange(hsv, *cfg.WHITE_RANGE)

    purple_dilated = cv2.dilate(purple_mask, None, iterations=cfg.BUTTON_PURPLE_DILATE_ITERATIONS)  # type: ignore[call-overload]
    outline_zone = cv2.subtract(purple_dilated, cv2.erode(purple_mask, None, iterations=cfg.BUTTON_PURPLE_ERODE_ITERATIONS))  # type: ignore[call-overload]
    has_white = cv2.bitwise_and(outline_zone, white_mask)

    contours, _ = cv2.findContours(purple_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    logger.debug(f"Button scan: {len(contours)} raw purple contour(s) in search band")

    candidates = []
    rejected_area, rejected_radius, rejected_circularity = 0, 0, 0
    for c in contours:
        area = cv2.contourArea(c)
        if area < cfg.BUTTON_MIN_AREA * cfg.BUTTON_LOOSE_AREA_FACTOR:
            rejected_area += 1
            continue
        if area > band_w * band_h * cfg.BUTTON_MAX_AREA_FRACTION:  # reject blobs covering most of the band (not a button)
            rejected_area += 1
            continue

        _, radius = cv2.minEnclosingCircle(c)
        if radius < cfg.BUTTON_MIN_RADIUS:
            rejected_radius += 1
            continue

        circularity = area / (np.pi * radius * radius + cfg.CIRCULARITY_EPSILON)
        if circularity < cfg.BUTTON_MIN_CIRCULARITY * cfg.BUTTON_LOOSE_CIRCULARITY_FACTOR:
            rejected_circularity += 1
            continue

        x, y, cw, ch = cv2.boundingRect(c)
        cx, cy = x + cw // 2, y + ch // 2
        pad = cfg.BUTTON_OUTLINE_PADDING
        roi = has_white[max(0, y - pad):y + ch + pad, max(0, x - pad):x + cw + pad]
        white_score = cv2.countNonZero(roi)
        candidates.append((cx + x0, cy + y0, area, white_score))

    logger.debug(f"Button scan: {len(candidates)} passed filters "
          f"(rejected: {rejected_area} area, {rejected_radius} radius, {rejected_circularity} circularity)")

    # Prefer candidates with a white outline, then by size; final order is left-to-right
    candidates.sort(key=lambda t: (t[3] > 0, t[2]), reverse=True)
    candidates = sorted(candidates[:cfg.BUTTON_MAX_COUNT], key=lambda t: t[0])
    positions = [(cx, cy) for cx, cy, _, _ in candidates]

    if not positions:
        logger.warning("No buttons detected. Check whether PURPLE_RANGE / BUTTON_BAND_X / BUTTON_BAND_Y need adjusting.")

    return positions

def click_button(button_x: float, button_y: float, need_name: str) -> bool:
    """Focus the game, then click a mapped need button with a hover click."""
    if not focus_roblox():
        return False
    time.sleep(cfg.FOCUS_DELAY)
    logger.debug(f"Clicking {need_name} at ({button_x}, {button_y})")
    hover_click(button_x, button_y)
    return True

def name_buttons_by_position(positions: list[Point]) -> dict[str, Point]:
    """The buttons of `positions` named by the BUTTON_SLOTS_X each is within BUTTON_SLOT_TOLERANCE of - {} if one is near none of them, or
    two are near the same one (they are then not the buttons, or not all of them)."""
    named: dict[str, Point] = {}
    for x, y in positions:
        near = [name for name, slot in zip(cfg.BUTTON_NAMES, cfg.BUTTON_SLOTS_X) if abs(x - slot) <= cfg.BUTTON_SLOT_TOLERANCE and name not in named]
        if len(near) != 1:
            return {}
        named[near[0]] = (x, y)
    return named

def refresh_button_mapping() -> bool:
    """Detect the action buttons and cache them in state.button_positions, clearing any
    previous mapping first so a failed detection never leaves a stale one. Anything but all five buttons is a failed
    detection - tried once more after BUTTON_SCAN_RETRY_WAIT seconds, then False with nothing mapped.
    See docs/ARCHITECTURE.md#button-detection.
    """
    state.button_positions.clear()

    positions = detect_buttons()
    if len(positions) != len(cfg.BUTTON_NAMES):
        log_run_event(f"button scan found {len(positions)} of {len(cfg.BUTTON_NAMES)}: "
                      + ", ".join(f"({x}, {y})" for x, y in positions) + f" - looking again in {cfg.BUTTON_SCAN_RETRY_WAIT:g}s")
        wait_interruptible(cfg.BUTTON_SCAN_RETRY_WAIT)
        positions = detect_buttons()
    if not positions:
        logger.error("No buttons detected")
        respawn_character()
        return False

    logger.info(f"Detected {len(positions)} button(s)")
    if len(positions) == len(cfg.BUTTON_NAMES):
        named = dict(zip(cfg.BUTTON_NAMES, positions))
    else:
        # The buttons are named by their order left to right, so a missed one (hidden by the character or a beam of light) would shift
        # every name after it: they are named by where they are instead, if each is where one of the buttons is, else none is made.
        named = name_buttons_by_position(positions)
        log_run_event(f"button scan found {len(positions)} of {len(cfg.BUTTON_NAMES)} again: "
                      + ", ".join(f"({x}, {y})" for x, y in positions)
                      + (f" - named by position, no {', '.join(n for n in cfg.BUTTON_NAMES if n not in named)}" if named else " - not where the buttons are, none mapped"))
        if not named:
            return False
    for name, (cx, cy) in named.items():
        state.button_positions[name] = (cx, cy)
        logger.info(f"Button '{name}' @ ({cx}, {cy})")

    logger.info("Button mapping refreshed")
    return True

def only_pets_message_visible(img: Image) -> bool:
    """True if the game's "Only Pets, Babies, and Dolls can use this." is on screen in `img`: the white of its letters matches the same
    of the crop (ONLY_PETS_*), which doesn't depend on what the world behind it looks like."""
    crop = load_templates(cfg.STRAY_WINDOW_DIR).get("only_pets")
    left, top, right, bottom = cfg.ONLY_PETS_BOX
    if crop is None:
        return False
    band = (img[top:bottom, left:right].min(axis=2) >= cfg.ONLY_PETS_WHITE).astype(np.float32)
    letters = (crop.min(axis=2) >= cfg.ONLY_PETS_WHITE).astype(np.float32)
    if letters.shape[0] > band.shape[0] or letters.shape[1] > band.shape[1] or not letters.any() or not band.any():
        return False
    return float(cv2.minMaxLoc(cv2.matchTemplate(band, letters, cv2.TM_CCOEFF_NORMED))[1]) >= cfg.ONLY_PETS_THRESHOLD

def button_click_refused() -> bool:
    """Look for the "Only Pets, Babies, and Dolls can use this." message for BUTTON_REJECT_LOOK seconds after a need button was clicked."""
    end = time.time() + cfg.BUTTON_REJECT_LOOK
    while time.time() < end:
        if only_pets_message_visible(grab_screen()):
            return True
        wait_interruptible(cfg.WATCH_POLL_INTERVAL)
    return False

def click_need_button(need_name: str) -> bool:
    """Click the button mapped to `need_name` - again if the game answers "Only Pets, Babies, and Dolls can use this.", up to
    BUTTON_CLICK_TRIES clicks, BUTTON_RETRY_WAIT seconds apart; a click that is refused again ends it - then wait for its icon to clear
    (BUTTON_RETRY_CLEAR_WAIT seconds at most after a retry). Returns False if no button was mapped or clicked (the need must
    not count as resolved). See docs/ARCHITECTURE.md#need-handlers.
    """
    if need_name not in state.button_positions:
        logger.warning(f"No button mapped for '{need_name}'")
        return False
    button_x, button_y = state.button_positions[need_name]
    for attempt in range(cfg.BUTTON_CLICK_TRIES):
        logger.info(f"Clicking the {need_name} button")
        if not click_button(button_x, button_y, need_name):
            return False
        if not button_click_refused():
            if attempt:
                wait_until_need_gone(need_name, max_wait=cfg.BUTTON_RETRY_CLEAR_WAIT)      # the retry that took: the icon gets this long
            else:
                wait_until_need_gone(need_name)
            return True
        log_run_event(f"{need_name}: the game said only pets can use it (click {attempt + 1})")
        if attempt + 1 < cfg.BUTTON_CLICK_TRIES:
            wait_interruptible(cfg.BUTTON_RETRY_WAIT)                                       # the pet may be on its way
    return False

# ============================================================================
# MOVEMENT
# ============================================================================

def respawn_character() -> None:
    """Respawn the character (ESC, R, ENTER), make sure the respawn dialog
    isn't left open (dismiss_stray_windows()), and wait for it to settle."""
    logger.debug("Respawning...")
    if not focus_roblox_click():
        return
    for key in cfg.KEY_SEQUENCES["respawn"]:
        key_down(key)
        try:
            time.sleep(cfg.RESPAWN_KEY_DURATION)
        finally:
            key_up(key)
        time.sleep(cfg.RESPAWN_KEY_DURATION)
    # The keys go in within a fraction of a second; if the Enter came before the
    # "Are you sure you want to respawn your character?" dialog did, it's still
    # open and blocks everything - click its Respawn (see STRAY_WINDOWS).
    time.sleep(cfg.RESPAWN_CONFIRM_LOOK_DELAY)
    for _ in range(2):
        if dismiss_stray_windows() != "respawn_confirm":
            break
    wait_interruptible(cfg.RESPAWN_WAIT)
    logger.debug("Respawn complete")

def hold_key(key: str, duration: float) -> None:
    """Hold `key` down for `duration` seconds (interruptibly). Always
    releases the key, even if StopRequested/FocusLost fires mid-hold -
    otherwise it would stay stuck held down in the game."""
    try:
        key_down(key)
        wait_interruptible(duration)
    finally:
        key_up(key)

def walk_to_buttons() -> None:
    """Walk forward from the respawn spot to where the action buttons are."""
    if not focus_roblox():
        return
    logger.debug("Walking to buttons...")
    hold_key(cfg.KEYS["forward"], cfg.WALK_TO_BUTTONS_DURATION)
    wait_interruptible(cfg.UI_SETTLE)
    logger.debug("Arrived at buttons")

def walk_alternating(direction_pair: tuple[str, str], total_duration: float, step_duration: float = cfg.WALK_ALTERNATING_STEP, need_name: Optional[str] = None) -> None:
    """Alternate the two keys, each held `step_duration`, for `total_duration` - or until
    `need_name` is confirmed gone by a watcher thread. A timeout with the need still
    showing is logged and sets state.need_cleared False.
    See docs/ARCHITECTURE.md#checking-a-need-in-parallel-with-movement.
    """
    logger.debug(f"Walking alternating {direction_pair} pattern for {total_duration}s...")
    if not focus_roblox():
        return

    state.need_cleared = True
    cleared_event = threading.Event()
    stop_event = threading.Event()
    answers: list = []
    watcher = None
    if need_name is not None:
        watcher = threading.Thread(target=_watch_need_gone, args=(need_name, cleared_event, stop_event, answers), daemon=True)
        watcher.start()

    try:
        direction_idx = 0
        elapsed = 0.0
        start_time = time.time()
        state.end_wait_need = need_name

        while elapsed < total_duration and not cleared_event.is_set():
            direction = direction_pair[direction_idx % 2]
            logger.debug(f"Step {direction_idx + 1}: {direction} for {step_duration}s...")
            hold_key(direction, step_duration)
            wait_interruptible(cfg.KEY_STEP_GAP)

            direction_idx += 1
            elapsed = time.time() - start_time
    finally:
        state.end_wait_need = None
        stop_event.set()
        if watcher is not None:
            watcher.join(timeout=cfg.NEED_WATCH_JOIN_TIMEOUT)

    if cleared_event.is_set():
        logger.debug(f"{need_name} cleared, stopping early")
    elif need_name is not None and answers and not answers[-1]:
        logger.debug(f"{need_name} still showing after {total_duration}s")
        log_failure(f"{need_name} still showing after {total_duration}s")
        state.need_cleared = False
    logger.debug("Alternating walk complete")

# ============================================================================
# SIDE ACTIONS
# ============================================================================
# Plain callable actions, not tied to any detected need/icon - not in
# ENABLED_NEEDS or NEED_HANDLER_CLASSES. side_quest() runs lure_collect() and
# tree_collect() on a timer; setup_game() and leave_and_rejoin() run from the
# GUI's Functions section and from rejoin_game()'s recovery.

def lure_collect() -> bool:
    """Walk to the lure, collect its rewards, then set a new one
    (set_new_lure())."""
    if not focus_roblox():
        return False
    logger.debug(f"Walking to the lure for {cfg.LURE_COLLECT_WALK_DURATION}s...")
    hold_key(cfg.KEYS["left"], cfg.LURE_COLLECT_WALK_DURATION)
    pydirectinput.press(cfg.KEYS["interact"])
    wait_interruptible(cfg.LURE_COLLECT_SETTLE_DELAY)
    pydirectinput.press(cfg.KEYS["interact"])
    wait_interruptible(cfg.UI_SETTLE)
    if not set_new_lure():
        return False
    respawn_character()
    logger.info("Lure collect complete")
    return True

def set_new_lure() -> bool:
    """Click the two backpack positions that place a fresh lure (split out of
    lure_collect() so it can be tuned alone). See docs/ARCHITECTURE.md#side-actions.
    """
    if not focus_roblox():
        return False
    hover_click(*cfg.LURE_NEW_POS_1)
    wait_interruptible(cfg.UI_SETTLE)
    hover_click(*cfg.LURE_NEW_POS_2)
    wait_interruptible(cfg.UI_SETTLE)
    logger.info("New lure set")
    return True

def tree_collect() -> bool:
    """Walk to the money tree, step back to line up with it, then harvest it."""
    if not focus_roblox():
        return False
    logger.debug(f"Walking to the money tree for {cfg.TREE_COLLECT_WALK_DURATION}s...")
    hold_key(cfg.KEYS["right"], cfg.TREE_COLLECT_WALK_DURATION)
    logger.debug(f"Stepping back for {cfg.TREE_COLLECT_BACKWARD_DURATION}s...")
    hold_key(cfg.KEYS["back"], cfg.TREE_COLLECT_BACKWARD_DURATION)
    pydirectinput.press(cfg.KEYS["interact"])
    respawn_character()
    logger.info("Tree collect complete")
    return True

def setup_game() -> bool:
    """Lock the house, filter the backpack to favorites and disable trades. Run on demand
    and after a rejoin, not in the cycle. See docs/ARCHITECTURE.md#side-actions.
    """
    with task_unscrew(play_minigames=False):
        return _setup_steps()

def _setup_steps() -> bool:
    for _ in range(cfg.SETUP_STRAY_TRIES):
        if not dismiss_stray_windows():
            break
    respawn_character()
    logger.debug("Locking house...")
    hover_click(*cfg.SETUP_LOCK_HOUSE_POS)

    logger.debug("Opening backpack...")
    pydirectinput.press(cfg.KEYS["backpack"])
    wait_interruptible(cfg.UI_SETTLE)
    logger.debug("Opening settings...")
    hover_click(*cfg.SETUP_BACKPACK_SETTINGS_POS)
    wait_interruptible(cfg.UI_SETTLE)
    logger.debug("Opening sort menu...")
    hover_click(*cfg.SETUP_SORT_MENU_POS)
    wait_interruptible(cfg.UI_SETTLE)
    logger.debug("Selecting favorites...")
    hover_click(*cfg.SETUP_FAVORITES_POS)
    wait_interruptible(cfg.UI_SETTLE)
    logger.debug("Confirming...")
    hover_click(*cfg.SETUP_CONFIRM_POS)
    wait_interruptible(cfg.UI_SETTLE)
    logger.debug("Closing backpack...")
    close_backpack("setup")

    disable_trades()

    logger.info("Setup complete")
    return True

def send_macro_window_to_back() -> None:
    """Drop the macro's own window behind everything (it's normally
    always-on-top and covers part of the Roblox UI). Safe to call from the
    worker thread: the change runs on Tk's thread and this waits for it."""
    if state.macro_window is not None:
        state.macro_window.run_on_ui_thread(state.macro_window.send_to_back)

def bring_macro_window_to_front() -> None:
    """Undo send_macro_window_to_back()."""
    if state.macro_window is not None:
        state.macro_window.run_on_ui_thread(state.macro_window.bring_to_front)

def disable_trades() -> bool:
    """Set the game's trade setting to "no one". The macro window covers the
    settings gear, so it's sent to the back for the duration and always
    brought back, even if the run is stopped partway."""
    logger.debug("Sending the macro window to the back...")
    send_macro_window_to_back()
    try:
        wait_interruptible(cfg.UI_SETTLE)
        if not focus_roblox():
            return False
        for label, pos in (("settings", cfg.SETUP_TRADES_SETTINGS_POS),
                           ("settings menu", cfg.SETUP_TRADES_MENU_POS),
                           ("interaction tab", cfg.SETUP_TRADES_INTERACTION_TAB_POS),
                           ("trading setting", cfg.SETUP_TRADES_SETTING_POS),
                           ("no one", cfg.SETUP_TRADES_NO_ONE_POS),
                           ("closing settings", cfg.SETUP_TRADES_CLOSE_POS)):
            logger.debug(f"{label}...")
            hover_click(*pos)
            wait_interruptible(cfg.UI_SETTLE)
    finally:
        logger.debug("Bringing the macro window back...")
        bring_macro_window_to_front()
    logger.info("Trades disabled")
    return True

def click_steps(steps: Sequence[tuple[str, tuple[int, int]]]) -> bool:
    """Click each (what, position) in turn with UI_SETTLE after it, logging it; False (nothing more clicked) if Roblox can't be focused first."""
    if not focus_roblox():
        return False
    for label, pos in steps:
        logger.debug(f"{label}...")
        hover_click(*pos)
        wait_interruptible(cfg.UI_SETTLE)
    return True

def travel_to_trade_hub() -> bool:
    """From the house (or anywhere the backpack opens): equip the Trading Hub Phone from the backpack's Toys, use it (a click in the world, as the game
    does with a held toy), TRAVEL TO HUB and Yes. The clicks are the positions of a human's run; the game then loads the Trading Hub."""
    if not focus_roblox():
        return False
    logger.debug("Opening backpack...")
    pydirectinput.press(cfg.KEYS["backpack"])
    wait_interruptible(cfg.UI_SETTLE)
    steps = [("Toys tab", cfg.TRADE_HUB_TOYS_TAB_POS), ("Trading Hub Phone", cfg.TRADE_HUB_PHONE_POS), ("Equip", cfg.TRADE_HUB_EQUIP_POS)]
    if not click_steps(steps):
        return False
    close_backpack("the Trading Hub Phone")
    if not click_steps([("Using the phone", cfg.TRADE_HUB_USE_POS), ("Travel to hub", cfg.TRADE_HUB_TRAVEL_POS), ("Yes", cfg.TRADE_HUB_YES_POS)]):
        return False
    log_run_event("travelling to the Trading Hub")
    return True

def leave_trade_hub() -> bool:
    """In the Trading Hub: the GO HOME button at the top and REGULAR SERVER in what opens; the game then loads a regular server."""
    if not click_steps([("Go home button", cfg.TRADE_HUB_HOME_POS), ("Regular server", cfg.TRADE_HUB_REGULAR_SERVER_POS)]):
        return False
    log_run_event("leaving the Trading Hub")
    return True

def parse_server_link(link: str) -> Optional[tuple[str, str]]:
    """Turn a private server link (share or older form) into (type, code), or None.
    See docs/ARCHITECTURE.md#side-actions.
    """
    match = re.search(r"privateServerLinkCode=(\w{32})", link, re.I)
    if match:
        return "LinkCode", match.group(1)
    match = re.search(r"share\?code=(\w{32})&type=Server", link, re.I)
    if match:
        return "ShareCode", match.group(1)
    return None

def run_deeplink(server: Optional[tuple[str, str]] = None) -> None:
    """Start Roblox on the game through a roblox:// deeplink: onto the given
    (type, code) private server, or a public server if `server` is None."""
    if server is None:
        uri = f"roblox://placeID={cfg.ROBLOX_PLACE_ID}"
    elif server[0] == "LinkCode":
        uri = f"roblox://placeID={cfg.ROBLOX_PLACE_ID}&linkcode={server[1]}"
    else:
        uri = f"roblox://navigation/share_links?code={server[1]}&type=Server"
    os.startfile(uri)  # type: ignore[attr-defined]  # Windows only

def close_roblox(clean_leave: bool = True) -> None:
    """Leave the game cleanly (only if `clean_leave`), kill any Roblox process left and
    wait REJOIN_CLOSE_WAIT. See docs/ARCHITECTURE.md#side-actions.
    """
    window = roblox_window()
    if clean_leave and window is not None and window.height >= cfg.REJOIN_MIN_LEAVE_HEIGHT and focus_roblox():
        logger.debug("Leaving the game...")
        for key in cfg.KEY_SEQUENCES["leave_game"]:
            pydirectinput.press(key)
            wait_stoppable(cfg.REJOIN_LEAVE_KEY_GAP)
    logger.debug("Closing Roblox...")
    for name in cfg.ROBLOX_PROCESS_NAMES:
        subprocess.run(["taskkill", "/F", "/IM", name], capture_output=True)
    wait_stoppable(cfg.REJOIN_CLOSE_WAIT)

def detect_play_button(img: Image) -> bool:
    """True if the game's green Play button is on screen around
    REJOIN_JOIN_POS in `img` - the sign that the game has loaded."""
    x, y = cfg.REJOIN_JOIN_POS
    half_w, half_h = cfg.REJOIN_PLAY_BOX
    box = img[max(0, y - half_h):y + half_h, max(0, x - half_w):x + half_w]
    mask = exact_color_mask(box, cfg.GREEN_BUTTON_COLOR, cfg.GREEN_BUTTON_TOLERANCE)
    return int(np.count_nonzero(mask)) >= cfg.REJOIN_PLAY_MIN_PIXELS

def rejoin_server(config: dict[str, Any]) -> tuple[Optional[tuple[str, str]], Optional[str]]:
    """The server a rejoin goes to, as (type, code), and what is wrong if it can't: None for a public server - when the private server is switched off
    (`use_private_server`) or no link is saved. A saved link that isn't recognised is a problem (and no rejoin), not a public server by accident."""
    link = str(config["private_server_link"]).strip()
    if not config["use_private_server"]:
        logger.info("The private server is switched off - rejoining a public server")
        return None, None
    if not link:
        logger.info("No private server link saved - rejoining a public server")
        return None, None
    server = parse_server_link(link)
    if server is None:
        return None, "the saved private server link isn't recognised"
    return server, None

def leave_and_rejoin(clean_leave: bool = True) -> bool:
    """Close Roblox, relaunch through a deeplink, wait for the game, click Play and respawn;
    up to REJOIN_MAX_ATTEMPTS times. Returns True once back in the game.
    See docs/ARCHITECTURE.md#side-actions.
    """
    server, problem = rejoin_server(load_game_config())
    if problem:
        logger.warning(f"Rejoin failed: {problem}")
        return False

    for attempt in range(1, cfg.REJOIN_MAX_ATTEMPTS + 1):
        logger.debug(f"Rejoin attempt {attempt}/{cfg.REJOIN_MAX_ATTEMPTS}")
        close_roblox(clean_leave)
        run_deeplink(server)

        logger.debug("Waiting for the Roblox window...")
        deadline = time.time() + cfg.REJOIN_WINDOW_TIMEOUT
        while not roblox_windows() and time.time() < deadline:
            wait_stoppable(cfg.REJOIN_POLL_INTERVAL)
        if not roblox_windows():
            logger.warning("No Roblox window appeared")
            continue

        logger.debug("Waiting for the game to load...")
        loaded = False
        deadline = time.time() + cfg.REJOIN_LOAD_TIMEOUT
        while time.time() < deadline:
            if roblox_windows():
                focus_roblox()
                if detect_play_button(grab_screen()):
                    loaded = True
                    break
            wait_stoppable(cfg.REJOIN_POLL_INTERVAL)
        if not loaded:
            logger.warning("The game didn't finish loading")
            continue

        logger.debug("Game loaded, clicking play...")
        wait_stoppable(cfg.REJOIN_PLAY_SETTLE)
        focus_roblox()
        hover_click(*cfg.REJOIN_JOIN_POS)
        logger.debug(f"Waiting {cfg.REJOIN_AFTER_JOIN_WAIT}s, then respawning...")
        wait_stoppable(cfg.REJOIN_AFTER_JOIN_WAIT)
        state.last_rejoin = state.last_progress = state.last_detection = time.time()   # first: the respawn can raise FocusLost
        state.white_since = None
        respawn_character()
        logger.info("Rejoin complete")
        return True

    logger.warning(f"Rejoin failed after {cfg.REJOIN_MAX_ATTEMPTS} attempts")
    return False

# ============================================================================
# NEED HANDLERS
# ============================================================================
# Every need is handled the same way: look its name up in
# NEED_HANDLER_CLASSES and call handle() on an instance of whatever's
# registered there. Most needs (catch, pet, choose, ride, walk) get their
# own dedicated class below; ButtonNeedHandler and TeleportWalkNeedHandler
# instead cover several need names each via partial() (see
# NEED_HANDLER_CLASSES), since those only differ by a name/config, not by
# behavior.

def macro_window_reference_rect() -> Optional[Box]:
    """The macro's own window as a reference-space box (plus FOCUS_PET_IGNORE_MARGIN) or
    None: its scrolling console looks like movement to focus_pet(). See docs/ARCHITECTURE.md#need-handlers.
    """
    if state.macro_window is None:
        return None
    measure = lambda: (state.macro_window.root.winfo_x(), state.macro_window.root.winfo_y(),
                       state.macro_window.root.winfo_rootx() + state.macro_window.root.winfo_width(),
                       state.macro_window.root.winfo_rooty() + state.macro_window.root.winfo_height())
    box = state.macro_window.run_on_ui_thread(measure, timeout=1.0)
    if box is None:
        return None
    left, top, width, height = roblox_rect()
    scale_x, scale_y = cfg.REFERENCE_WIDTH / width, cfg.REFERENCE_HEIGHT / height
    m = cfg.FOCUS_PET_IGNORE_MARGIN
    return (int((box[0] - left) * scale_x) - m, int((box[1] - top) * scale_y) - m,
            int((box[2] - left) * scale_x) + m, int((box[3] - top) * scale_y) + m)

def moving_blobs(img_a: Image, img_b: Image, top_percent: float = cfg.FOCUS_PET_REGION_TOP_PERCENT, ignore: Sequence[Box] = ()) -> tuple[list[tuple[int, int, int, int, int]], Image, int]:
    """Diff two screenshots below the top FOCUS_PET_REGION_TOP_PERCENT, minus the `ignore`
    boxes: ([(x, y, w, h, area)] largest first, the change mask, the region's top row).
    Pure image logic. See docs/ARCHITECTURE.md#need-handlers.
    """
    top = int(img_a.shape[0] * top_percent)
    diff = cv2.absdiff(cv2.cvtColor(img_a[top:], cv2.COLOR_BGR2GRAY),
                       cv2.cvtColor(img_b[top:], cv2.COLOR_BGR2GRAY))
    mask: Image = (diff > cfg.FOCUS_PET_DIFF_THRESHOLD).astype(np.uint8) * cfg.PIXEL_MAX
    for left, box_top, right, bottom in ignore:
        mask[max(0, box_top - top):max(0, bottom - top), max(0, left):max(0, right)] = 0
    # An up/down bob only changes the pet's top and bottom edges, so close
    # the gap between them to get one blob per moving thing.
    kernel = np.ones((cfg.FOCUS_PET_MERGE_KERNEL, cfg.FOCUS_PET_MERGE_KERNEL), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    blobs = []
    for contour in sorted(contours, key=cv2.contourArea, reverse=True):
        area = cv2.contourArea(contour)
        if area < cfg.FOCUS_PET_MIN_AREA:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        blobs.append((x, top + y, w, h, int(area)))
    return blobs, mask, top

def detect_pet_focus(img: Image) -> bool:
    """True if the pet focus view (its BACK button) is showing in `img`."""
    return popup_visible(img, "pet_focus")

def focus_pet(click_duration: float = cfg.CLICK_MOVE_DURATION) -> bool:
    """Click the pet to open its menu: found by movement between two frames, clicked at the
    centre of each moving blob until detect_pet_focus() sees the focus view. Returns
    True if anything was clicked (or the pet was already focused). See docs/ARCHITECTURE.md#need-handlers.
    """
    if detect_pet_focus(grab_screen()):
        logger.debug("Focus_pet: the pet is already focused")
        return True
    img_a = grab_screen()
    wait_interruptible(cfg.FOCUS_PET_FRAME_GAP)
    img_b = grab_screen()
    ignore = [box for box in [macro_window_reference_rect()] if box is not None]
    blobs, mask, top = moving_blobs(img_a, img_b, ignore=ignore)
    changed = int(np.count_nonzero(mask))
    logger.debug(f"Focus_pet: {len(blobs)} moving blob(s), {changed} changed px below y={top}")
    for i, (x, y, w, h, area) in enumerate(blobs, 1):
        logger.debug(f"Focus_pet:   #{i} center ({x + w // 2}, {y + h // 2}), {w}x{h}, area {area}")
    if not blobs:
        logger.debug("Focus_pet: nothing moved, pet not found")
        return False
    for i, (x, y, w, h, _) in enumerate(blobs, 1):
        logger.debug(f"Focus_pet: clicking #{i} at ({x + w // 2}, {y + h // 2})...")
        hover_click(x + w // 2, y + h // 2, duration=click_duration)
        wait_interruptible(cfg.FOCUS_PET_CLICK_SETTLE)
        if detect_pet_focus(grab_screen()):
            logger.debug(f"Focus_pet: focused after click #{i}, no more clicks needed")
            break
    else:
        wait_interruptible(cfg.FOCUS_PET_MENU_WAIT)
    return True

def is_backpack_tab_selected(pos: Point) -> bool:
    """True if the backpack category tab at `pos` is already selected (orange)."""
    x, y = pos
    hx, hy = cfg.TAB_HALF_SIZE
    tile = grab_screen()[y - hy:y + hy, x - hx:x + hx]
    mask = exact_color_mask(tile, cfg.TAB_SELECTED_COLOR, cfg.TAB_SELECTED_TOLERANCE)
    return bool(np.count_nonzero(mask) >= cfg.TAB_SELECTED_MIN_PIXELS)

def select_backpack_tab(pos: Point) -> None:
    """Click the backpack category tab at `pos` unless it's already selected:
    clicking a selected tab expands the backpack."""
    if is_backpack_tab_selected(pos):
        logger.debug("Tab already selected, not clicking it")
        return
    hover_click(*pos)

@contextlib.contextmanager
def backpack_on_purpose() -> Iterator[None]:
    """Within this block a handler has the backpack open on purpose and closes it itself: the in-task check (close_backpack_if_stuck())
    leaves it alone and forgets any sighting from before. (Two sightings UNSCREW_TASK_INTERVAL apart used to count as stuck open, and an
    equip sequence keeps it open for longer than that: the check closed it in the middle, and the final key press opened it again.)"""
    state.backpack_wanted += 1
    state.backpack_strikes = 0
    try:
        yield
    finally:
        state.backpack_wanted -= 1

def equip_favorite_vehicle() -> None:
    """Open the backpack, equip the first vehicle (the favorite) and close it. Shared by
    ride and the helicopter needs. See docs/ARCHITECTURE.md#need-handlers.
    """
    with backpack_on_purpose():
        logger.debug("Opening backpack...")
        pydirectinput.press(cfg.KEYS["backpack"])
        wait_interruptible(cfg.UI_SETTLE)

        logger.debug("Opening vehicles...")
        select_backpack_tab(cfg.RIDE_VEHICLES_POS)
        wait_interruptible(cfg.UI_SETTLE)

        logger.debug("Selecting first vehicle...")
        hover_click(*cfg.RIDE_FIRST_VEHICLE_POS)
        wait_interruptible(cfg.UI_SETTLE)

        logger.debug("Equipping vehicle...")
        hover_click(*cfg.RIDE_EQUIP_POS)
        wait_interruptible(cfg.UI_SETTLE)

        logger.debug("Closing backpack...")
        close_backpack("equipping the vehicle")

class NeedHandler(ABC):
    """Reacts to one detected need. Subclass this to add a new need."""

    @abstractmethod
    def handle(self) -> bool:
        """Perform the action for this need. Return True if it was handled."""
        raise NotImplementedError

class ButtonNeedHandler(NeedHandler):
    """Walks to the action buttons and clicks the one matching `name`
    (hungry/thirsty/dirty/potty/sleepy)."""

    def __init__(self, name: str) -> None:
        self.name = name

    def handle(self) -> bool:
        walk_to_buttons()
        if not refresh_button_mapping():
            return False
        return click_need_button(self.name)

class WalkNeedHandler(NeedHandler):
    """The 'walk' need is satisfied by walking left-right for a while -
    or less, the moment its icon is confirmed gone (see walk_alternating())."""

    def handle(self) -> bool:
        logger.info("Walk need")
        walk_alternating((cfg.KEYS["left"], cfg.KEYS["right"]), cfg.NEED_GONE_MAX_WAIT, need_name="walk")
        return True

class CatchNeedHandler(NeedHandler):
    """The 'catch' need requires opening backpack, equipping a toy, throwing it
    (clicking) CATCH_THROW_COUNT times, then unequipping it."""

    def handle(self) -> bool:
        logger.info("Catch need")
        if not focus_roblox():
            return False

        with backpack_on_purpose():
            # Open backpack with 'b' key
            logger.debug("Opening backpack...")
            pydirectinput.press(cfg.KEYS["backpack"])
            wait_interruptible(cfg.UI_SETTLE)

            # Navigate to toys
            logger.debug("Opening toys...")
            select_backpack_tab(cfg.CATCH_TOYS_POS)
            wait_interruptible(cfg.UI_SETTLE)

            # Click squeaky toy
            logger.debug("Selecting squeaky toy...")
            hover_click(*cfg.CATCH_SQUEAKY_TOY_POS)
            wait_interruptible(cfg.UI_SETTLE)

            # Equip the toy
            logger.debug("Equipping toy...")
            hover_click(*cfg.CATCH_EQUIP_POS)
            wait_interruptible(cfg.UI_SETTLE)

            # Close backpack with 'b' key
            logger.debug("Closing backpack...")
            close_backpack("equipping the toy")

        # Wait before throwing
        logger.debug(f"Waiting {cfg.CATCH_WAIT_AFTER_EQUIP}s before throwing...")
        wait_interruptible(cfg.CATCH_WAIT_AFTER_EQUIP)

        # Zoom in, to avoid focusing the pet on toy throw
        hold_key(cfg.KEYS["zoom_in"], cfg.CATCH_ZOOM_DURATION)
        
        # Click empty space to throw, with a delay between throws
        for i in range(cfg.CATCH_THROW_COUNT):
            logger.debug(f"Throw {i + 1}/{cfg.CATCH_THROW_COUNT}...")
            hover_click(*cfg.EMPTY_POS)
            wait_interruptible(cfg.CATCH_EMOTE_DELAY)

        # Unequip the toy
        logger.debug("Unequipping toy...")
        hover_click(*cfg.CATCH_UNEQUIP_POS)

        logger.info("Catch complete")
        return True

class PetNeedHandler(NeedHandler):
    """The 'pet' need: click to focus the pet, then hold the mouse button
    down and swipe it once down from just above the center of the screen to
    just below it, over PET_SWIPE_DURATION."""

    def handle(self) -> bool:
        logger.info("Pet need")
        if not focus_roblox():
            return False

        logger.debug("Focusing pet...")
        with wanting_pet_focus():
            return self._pet()

    def _pet(self) -> bool:
        if not focus_pet(cfg.PET_FOCUS_CLICK_DURATION):
            return False
        wait_interruptible(cfg.UI_SETTLE)
        logger.debug(f"Petting: one swipe down over {cfg.PET_SWIPE_DURATION}s...")
        # Click the center to focus
        hover_click(cfg.REFERENCE_CENTER_X, cfg.REFERENCE_CENTER_Y, duration=cfg.PET_FOCUS_CLICK_DURATION)
        time.sleep(cfg.PET_SETTLE_DELAY)
        start_y = cfg.REFERENCE_CENTER_Y + cfg.PET_SWIPE_START_OFFSET
        end_y = cfg.REFERENCE_CENTER_Y + cfg.PET_SWIPE_END_OFFSET
        pydirectinput.moveTo(*to_screen(cfg.REFERENCE_CENTER_X, start_y))
        time.sleep(cfg.PET_SETTLE_DELAY)
        # Hold down and move with incremental steps (much more reliable for games)
        mouse_down()
        try:
            start_time = time.time()
            while True:
                check_running()
                elapsed = time.time() - start_time
                if elapsed >= cfg.PET_SWIPE_DURATION:
                    break
                y = int(start_y + (end_y - start_y) * elapsed / cfg.PET_SWIPE_DURATION)
                pydirectinput.moveTo(*to_screen(cfg.REFERENCE_CENTER_X, y))
                time.sleep(cfg.PET_SWIPE_STEP_DURATION)
        finally:
            # Always release, even if interrupted
            pydirectinput.mouseUp()
        logger.info("Pet complete")
        return True

class ChooseNeedHandler(NeedHandler):
    """The 'choose' need: focus the pet, move slowly to the button of one exact colour,
    click it, then click mid-screen. Doesn't wait for the icon. See docs/ARCHITECTURE.md#need-handlers.
    """

    def handle(self) -> bool:
        logger.info("Choose need")
        if not focus_roblox():
            return False

        logger.debug("Focusing pet...")
        with wanting_pet_focus():
            return self._choose()

    def _choose(self) -> bool:
        if not focus_pet():
            return False
        wait_interruptible(cfg.UI_SETTLE)

        logger.debug(f"Searching screen for color {cfg.CHOOSE_BUTTON_COLOR}...")
        img = grab_screen()
        match = find_exact_color(img, cfg.CHOOSE_BUTTON_COLOR)
        if match is None:
            logger.warning(f"No pixel matching {cfg.CHOOSE_BUTTON_COLOR} found on screen")
            return False
        logger.debug(f"Found at {match}, moving there slowly...")
        hover_click(*match, duration=cfg.CHOOSE_SLOW_MOVE_DURATION)
        wait_interruptible(cfg.UI_SETTLE)

        logger.debug("Clicking middle of screen...")
        hover_click(cfg.REFERENCE_CENTER_X, cfg.REFERENCE_CENTER_Y, duration=cfg.CHOOSE_SLOW_MOVE_DURATION)

        logger.info("Choose complete")
        return True

class RideNeedHandler(NeedHandler):
    """The 'ride' need: step back, mount a vehicle from the backpack, then
    walk back and forth for a while astride it - or less, the moment its
    icon is confirmed gone (see walk_alternating())."""

    def handle(self) -> bool:
        logger.info("Ride need")
        if not focus_roblox():
            return False

        # Step back before mounting
        logger.debug(f"Stepping back for {cfg.RIDE_BACKWARD_DURATION}s...")
        hold_key(cfg.KEYS["back"], cfg.RIDE_BACKWARD_DURATION)

        logger.debug("Pressing e...")
        pydirectinput.press(cfg.KEYS["mount"])
        wait_interruptible(cfg.RIDE_WAIT_AFTER_E)

        # Walk forward briefly after mounting, before opening the backpack
        logger.debug(f"Walking forward for {cfg.RIDE_FORWARD_DURATION}s...")
        hold_key(cfg.KEYS["forward"], cfg.RIDE_FORWARD_DURATION)

        equip_favorite_vehicle()

        # Hold r briefly before starting to move
        logger.debug(f"Holding {cfg.KEYS['helicopter']} for {cfg.RIDE_R_HOLD_DURATION}s...")
        hold_key(cfg.KEYS["helicopter"], cfg.RIDE_R_HOLD_DURATION)

        # Walk back and forth (forward/backward, not left/right) while riding
        walk_alternating((cfg.KEYS["forward"], cfg.KEYS["back"]), cfg.NEED_GONE_MAX_WAIT, need_name="ride")

        logger.info("Ride complete")
        return True

def teleport_to(category_pos: Point) -> None:
    """Open the backpack, teleport via the category tab, walk back to clear the landing
    spot. Shared by the teleport-walk needs. See docs/ARCHITECTURE.md#need-handlers.
    """
    with backpack_on_purpose():
        logger.debug("Opening backpack...")
        pydirectinput.press(cfg.KEYS["backpack"])
        wait_interruptible(cfg.UI_SETTLE)

        logger.debug("Selecting category...")
        hover_click(*category_pos)
        wait_interruptible(cfg.UI_SETTLE)
        hover_click(*cfg.GENERAL_TELEPORT_POS_2)
        wait_interruptible(cfg.UI_SETTLE)
        for attempt in range(cfg.TELEPORT_YES_TRIES):            # the dialog's Yes: clicked again if nothing teleported (the click can be lost)
            before = grab_screen()
            hover_click(*cfg.GENERAL_TELEPORT_POS_3)
            if minigame_teleported(before):
                break
            logger.debug(f"The teleport did not take after click {attempt + 1}")
        else:
            log_run_event("teleport: nothing happened after the dialog's Yes")
    wait_interruptible(cfg.TELEPORT_WAIT)
    logger.debug("Walking backward...")
    hold_key(cfg.KEYS["back"], cfg.TELEPORT_BACK_DURATION)
    wait_interruptible(cfg.TELEPORT_SETTLE_WAIT)

class TeleportWalkNeedHandler(NeedHandler):
    """Teleport, hold a sequence of keys, then wait for the need to clear; configured per
    need in TELEPORT_WALK_NEEDS (`helicopter` flies instead). process_needs() respawns
    afterwards. See docs/ARCHITECTURE.md#need-handlers.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.config = cfg.TELEPORT_WALK_NEEDS[name]

    def handle(self) -> bool:
        logger.info(f"{self.name.capitalize()} need")
        if not focus_roblox():
            return False

        teleport_to(self.config["teleport_pos"])

        helicopter = self.config.get("helicopter", cfg.HELICOPTER_REQUIRED)
        if helicopter:
            logger.debug(f"Stepping forward for {cfg.HELICOPTER_FORWARD_DURATION}s...")
            hold_key(cfg.KEYS["forward"], cfg.HELICOPTER_FORWARD_DURATION)
            equip_favorite_vehicle()
            logger.debug(f"Holding {cfg.KEYS['helicopter']} for {cfg.HELICOPTER_HOLD_DURATION}s...")
            hold_key(cfg.KEYS["helicopter"], cfg.HELICOPTER_HOLD_DURATION)

        for i, (key, duration) in enumerate(self.config["steps"]):
            if i:
                wait_interruptible(cfg.TELEPORT_WALK_STEP_GAP)
            logger.debug(f"Holding {key} for {duration}s...")
            hold_key(key, duration)

        if helicopter:
            logger.debug("Arrived, pressing space...")
            pydirectinput.press(cfg.KEYS["jump"])

        final_click = self.config.get("final_click")
        if final_click:
            if not self._open_dialog():
                # Nothing more can be done from here: the need has failed - no waiting for it to clear.
                logger.debug(f"The {self.name} dialog never came up - the need failed")
                respawn_character()
                return False
            logger.debug(f"Clicking {final_click}...")
            hover_click(*final_click)

        wait_until_need_gone(self.name, max_wait=self.config.get("max_wait", cfg.NEED_GONE_MAX_WAIT))

        logger.info(f"{self.name.capitalize()} complete")
        return True

    def _open_dialog(self) -> bool:
        """Click the doctor's prompt - the hand button over the player's head, found by its reference crop, not pressed with
        'e' (that focuses the pet) - and wait for the dialog. Returns whether it came up (the Yes is next); False means
        the need is given up at once. See docs/ARCHITECTURE.md#need-handlers.
        """
        wait_interruptible(cfg.SICK_PRE_CLICK_WAIT)
        if detect_pet_focus(grab_screen()):                      # (it hides the prompt)
            log_run_event(f"{self.name}: the pet view is open - backing out of it")
            dismiss_stray_windows()
        prompt = look_until(find_doctor_prompt, cfg.SICK_PROMPT_LOOK)
        if prompt is None:
            log_run_event(f"{self.name}: the doctor's prompt is not on screen")
            return False
        logger.debug(f"Clicking the {self.name} prompt at {prompt}...")
        hover_click(*prompt)
        if look_until(detect_sick_dialog, cfg.SICK_CONFIRM_WAIT) is None:
            log_run_event(f"{self.name}: no dialog after the prompt was clicked")
            return False
        return True

# Handler factory (class, or partial of one) for each need.
# get_need_handler() uses this plus the enabled needs (need_is_enabled()) to
# decide what to do with a detected need.
NEED_HANDLER_CLASSES: dict[str, Callable[[], NeedHandler]] = {
    "catch": CatchNeedHandler,
    "pet": PetNeedHandler,
    "choose": ChooseNeedHandler,
    "ride": RideNeedHandler,
    "walk": WalkNeedHandler,
    **{name: partial(ButtonNeedHandler, name) for name in cfg.BUTTON_NAMES},
    **{name: partial(TeleportWalkNeedHandler, name) for name in cfg.TELEPORT_WALK_NEEDS},
}

# Every handler the GUI's Debug tab can run on its own, and the teleport ones it can run all of in turn.
DEBUG_HANDLERS = NEED_HANDLER_CLASSES
TELEPORT_HANDLERS = {name: handler for name, handler in NEED_HANDLER_CLASSES.items() if name in cfg.TELEPORT_WALK_NEEDS}

def need_is_enabled(need_name: str) -> bool:
    """Whether the macro acts on `need_name`: the Debug tab's switch for it (the game config's needs_enabled), which is
    on unless the need is missing from ENABLED_NEEDS (magic_numbers.py) - that is only the starting position."""
    return bool(load_game_config()["needs_enabled"].get(need_name, need_name in cfg.ENABLED_NEEDS))

def enabled_needs() -> set[str]:
    """Every need the macro acts on at the moment (need_is_enabled()), for the needs it knows and any ENABLED_NEEDS names."""
    flags = load_game_config()["needs_enabled"]
    return {name for name in cfg.ENABLED_NEEDS if flags.get(name, True)} | {name for name, on in flags.items() if on}

def get_need_handler(need_name: str) -> Optional[NeedHandler]:
    """Return the handler for a detected need, or None if it's not
    currently enabled (see need_is_enabled()), was disabled for this run (see
    record_detection()), or the macro doesn't know it."""
    if not need_is_enabled(need_name) or need_name in state.disabled_needs:
        return None
    handler_cls = NEED_HANDLER_CLASSES.get(need_name)
    return handler_cls() if handler_cls else None

def record_detection(detected: list[str]) -> None:
    """Track stuck needs: one attempted last pass and still on screen counts a strike, and
    NEED_STUCK_CHECKS in a row disables it for the run. Call for every check.
    See docs/ARCHITECTURE.md#stuck-needs.
    """
    for need_name in state.attempted_last:
        if need_name in detected:
            state.attempt_streaks[need_name] = state.attempt_streaks.get(need_name, 0) + 1
        else:
            state.attempt_streaks.pop(need_name, None)
    state.attempted_last = set()
    stuck = {n for n, count in state.attempt_streaks.items() if count >= cfg.NEED_STUCK_CHECKS} & (enabled_needs() - state.disabled_needs)
    for need_name in sorted(stuck):
        state.disabled_needs.add(need_name)
        logger.info(f"{need_name} still there after {cfg.NEED_STUCK_CHECKS} attempts in a row - disabling it for this run")
        log_run_event(f"disabled: {need_name}")
        log_failure(f"{need_name} stuck for {cfg.NEED_STUCK_CHECKS} attempts - disabled for this run "
                    f"({len(state.disabled_needs)} disabled now: {', '.join(sorted(state.disabled_needs))})")

def process_needs() -> bool:
    """Detect needs and resolve the first one that can be: respawn and return True on
    success (so the next need is picked from a fresh look), skip failures without respawn,
    False if nothing was resolved. See docs/ARCHITECTURE.md#process_needs-step-by-step.
    """
    if not focus_roblox_click():
        return False

    found_icons, full_img = detect_need_icons()
    if not found_icons:
        record_detection([])
        note_idle_check()
        return False

    matched_needs = []
    for idx, icon_img, need_name, score in identify_icons(found_icons, full_img):
        if not need_name:
            if cfg.SAVE_NEW_NEEDS:
                logger.info(f"New need (best score: {score:.4f})")
                # prompt_rename_need() already saves the reference icon into
                # NEEDS_DIR - that .png file is the only record needed for this
                # need to be recognized next time, so there's nothing further to
                # persist here.
                prompt_rename_need(icon_img, idx)
            continue
        matched_needs.append((need_name, score))

    names = [name for name, _ in matched_needs]
    if names:
        body = ", ".join(names)
        if body == state.last_detected_body:
            note_repeat_check()      # the same needs as the line above, nothing handled since: counted, not repeated
        else:
            flush_idle_checks()
            logger.info(f"Found {len(found_icons)} icon(s): " + ", ".join(f"{n} ({s:.4f})" for n, s in matched_needs))
            log_run_event(f"detected: {body}")
            state.last_detected_body = body
        state.none_matched_logged = False
    elif not state.none_matched_logged:
        flush_idle_checks()
        log_run_event(f"detected: none matched ({len(found_icons)} icon(s) on screen)")
        state.none_matched_logged = True
        state.last_detected_body = None
    else:
        note_idle_check()   # more icons nothing matched: logged once already, counted with the idle checks
    run_stats.last_detected = names
    if names:
        state.last_detection = time.time()
    record_detection(names)

    attempted = set()
    try:
        for need_name in names:
            check_running()

            handler = get_need_handler(need_name)
            if handler is None:
                logger.debug(f"{need_name} is disabled, skipping")
                continue
            attempted.add(need_name)
            flush_idle_checks()
            state.last_detected_body = None     # whatever is detected after this is news again
            state.need_cleared = True
            started = time.time()
            try:
                with task_unscrew():
                    handled = handler.handle()
            except TaskInterrupted as interruption:
                # A minigame or a rejoin took over (both respawn): not
                # resolved, and not a failed attempt either - the next check
                # sees what's still on screen. A popup that spoiled it (the
                # character was respawned by task_unscrew_tick()) does count
                # as an attempt, so a need it keeps spoiling is disabled like
                # any other stuck need instead of being retried forever.
                if interruption.reason != "a popup":
                    attempted.discard(need_name)
                logger.debug(f"{need_name} interrupted by {interruption.reason}")
                log_run_event(f"{need_name} interrupted by {interruption.reason}")
                return True
            if not handled:
                logger.debug(f"Could not resolve '{need_name}' this pass, skipping")
                run_stats.failed[need_name] = run_stats.failed.get(need_name, 0) + 1
                log_failure(f"{need_name} handler could not resolve it")
                continue

            respawn_character()
            seconds = time.time() - started
            if not state.need_cleared:
                # The handler ran its course but the need was still showing when its wait ran out (already logged
                # as a failure): not resolved. The character is respawned all the same, since it was moved.
                logger.debug(f"{need_name} was not cleared, counting it as failed")
                run_stats.failed[need_name] = run_stats.failed.get(need_name, 0) + 1
                log_run_event(f"not cleared: {need_name} ({seconds:.0f}s)")
                write_status()
                return True
            run_stats.resolved[need_name] = run_stats.resolved.get(need_name, 0) + 1
            run_stats.last_resolved = {"need": need_name, "time": time.strftime("%Y-%m-%d %H:%M:%S")}
            state.last_progress = time.time()
            log_run_event(f"resolved: {need_name} ({seconds:.0f}s)")
            write_status()
            return True
        return False
    finally:
        state.attempted_last = attempted

# ============================================================================
# PER-CYCLE CHECKS
# ============================================================================
# Run at the top of every cycle by unscrew() and side_quest(): popups to
# dismiss, recovering the game when it's gone wrong, and the timed chores.

def detect_paycheck() -> bool:
    """Detect the paycheck popup by the crop of its bank-card header and dismiss it. Returns
    True if it was there. See docs/ARCHITECTURE.md#need-handlers.
    """
    if not popup_visible(grab_screen(), "paycheck"):
        return False

    logger.debug("Paycheck popup detected, dismissing...")
    log_run_event("paycheck popup dismissed")
    hover_click(*cfg.PAYCHECK_DISMISS_POS_1)
    hover_click(*cfg.PAYCHECK_DISMISS_POS_2)
    return True

def color_pixels(img: Image, box: Box, color: Color, tolerance: int) -> int:
    """How many pixels of `box` (left, top, right, bottom) of `img` are `color` within `tolerance`."""
    left, top, right, bottom = box
    return int(np.count_nonzero(exact_color_mask(img[top:bottom, left:right], color, tolerance)))

def color_fraction(img: Image, box: Box, color: Color, tolerance: int) -> float:
    """The share (0-1) of `box` of `img` that is `color` within `tolerance`."""
    left, top, right, bottom = box
    return color_pixels(img, box, color, tolerance) / ((right - left) * (bottom - top))

def detect_backpack_expanded(img: Image) -> bool:
    """True if the EXPANDED backpack is open in `img`: most of
    BACKPACK_HEADER_BOX is the purple of its header bar."""
    return color_fraction(img, cfg.BACKPACK_HEADER_BOX, cfg.BACKPACK_HEADER_COLOR, cfg.BACKPACK_HEADER_TOLERANCE) >= cfg.BACKPACK_HEADER_MIN_FRACTION

def detect_backpack_normal(img: Image) -> bool:
    """True if the normal backpack is open: its purple frame plus the white item grid, or its
    title crop. See docs/ARCHITECTURE.md#need-handlers.
    """
    if popup_visible(img, "backpack_normal"):
        return True
    if color_pixels(img, cfg.BACKPACK_NORMAL_PANEL_BOX, cfg.BACKPACK_NORMAL_COLOR, cfg.BACKPACK_NORMAL_TOLERANCE) < cfg.BACKPACK_NORMAL_MIN_PIXELS:
        return False
    left, top, right, bottom = cfg.BACKPACK_NORMAL_WHITE_BOX
    grid = img[top:bottom, left:right]
    white = np.count_nonzero(grid.min(axis=2) >= cfg.BACKPACK_NORMAL_WHITE_LEVEL)
    return bool(white / (grid.shape[0] * grid.shape[1]) >= cfg.BACKPACK_NORMAL_MIN_WHITE_FRACTION)

def detect_backpack_open(img: Image) -> bool:
    """True if the backpack is open in `img`, in either form."""
    return detect_backpack_expanded(img) or detect_backpack_normal(img)

# How many in-task checks in a row have seen the backpack open (see
# close_backpack_if_stuck()).

def close_backpack_if_open(step: str = "") -> bool:
    """Close the backpack in either form (key, look, key once more); logs a failure and stops
    if it is still open. Returns True if it was open. See docs/ARCHITECTURE.md#click--input-primitives.
    """
    if not is_roblox_focused() or not detect_backpack_open(grab_screen()):
        return False
    img = grab_screen()
    logger.debug(f"Backpack left open, closing it... (expanded: {detect_backpack_expanded(img)}, "
          f"normal: {detect_backpack_normal(img)})")
    if detect_backpack_expanded(img):
        # Its BACK button closes it entirely; the key would only shrink it first.
        back = popup_found(img, "backpack_back")
        if back is not None:
            hover_click(*back)
            wait_interruptible(cfg.UI_SETTLE)
            if not detect_backpack_open(grab_screen()):
                log_run_event("backpack was left open - closed it (BACK)" + (f" after {step}" if step else ""))
                return True
    for _ in range(2):
        pydirectinput.press(cfg.KEYS["backpack"])
        wait_interruptible(cfg.UI_SETTLE)
        if not detect_backpack_open(grab_screen()):
            log_run_event("backpack was left open - closed it" + (f" after {step}" if step else ""))
            return True
    log_failure("backpack still open after pressing the backpack key twice")
    return True

def close_backpack(step: str) -> None:
    """End of a click sequence: press the backpack key and check it really closed.
    See docs/ARCHITECTURE.md#click--input-primitives.
    """
    pydirectinput.press(cfg.KEYS["backpack"])
    wait_interruptible(cfg.UI_SETTLE)
    close_backpack_if_open(step)

def close_backpack_if_stuck(in_task: bool) -> bool:
    """The backpack part of unscrew(): closed at once at the top of a cycle, mid-task only
    after BACKPACK_CLOSE_CONFIRMATIONS sightings in a row. Returns True if still open.
    See docs/ARCHITECTURE.md#need-handlers.
    """
    if not in_task:
        close_backpack_if_open()
        return False
    img = grab_screen() if is_roblox_focused() else None
    seen = img is not None and detect_backpack_open(img)
    if state.backpack_wanted:                                   # a handler has it open on purpose (backpack_on_purpose()) and closes it itself
        state.backpack_strikes = 0
        return seen
    if img is None or not seen:
        state.backpack_strikes = 0
        return False
    state.backpack_strikes += 1
    if state.backpack_strikes == 1:
        logger.debug("The backpack was seen open (the first of the sightings that count as stuck)")
    if state.backpack_strikes < cfg.BACKPACK_CLOSE_CONFIRMATIONS:
        return True
    state.backpack_strikes = 0
    close_backpack_if_open()
    return False

def detect_disconnect(img: Image) -> bool:
    """True if the Roblox "Disconnected" dialog is on screen in `img`: most
    of DISCONNECT_PANEL_BOX is the dialog's grey (the game behind it is
    blurred, so nothing else on screen fills a box like that)."""
    return color_fraction(img, cfg.DISCONNECT_PANEL_BOX, cfg.DISCONNECT_PANEL_COLOR, cfg.DISCONNECT_PANEL_TOLERANCE) >= cfg.DISCONNECT_PANEL_MIN_FRACTION

def detect_white_screen(img: Image) -> bool:
    """True if most of `img` is completely white - the transition shown on a respawn, teleport or change of
    location (WHITE_SCREEN_LEVEL / WHITE_SCREEN_MIN_FRACTION). See docs/ARCHITECTURE.md#stuck-needs."""
    return bool(np.count_nonzero(img.min(axis=2) >= cfg.WHITE_SCREEN_LEVEL) / (img.shape[0] * img.shape[1]) >= cfg.WHITE_SCREEN_MIN_FRACTION)

ROBLOX_NOT_RUNNING = "Roblox isn't running"

def rejoin_reason() -> Optional[tuple[str, str]]:
    """Why the game needs rejoining now, as (kind, reason), or None - in priority order
    "disconnect", "white_screen", "stuck", "stalled", "scheduled". See docs/ARCHITECTURE.md#stuck-needs.
    """
    windows = gw.getAllWindows()   # listed once (a Windows call that reads every window's title) for both questions
    if any(w.title == cfg.ROBLOX_CRASH_WINDOW_TITLE for w in windows):
        return "disconnect", "Roblox crashed"
    if not any("roblox" in w.title.lower() for w in windows):
        return "disconnect", ROBLOX_NOT_RUNNING
    screen = grab_screen()
    if detect_disconnect(screen):
        return "disconnect", "disconnected from the game"
    if not detect_white_screen(screen):
        state.white_since = None
    elif state.white_since is None:
        state.white_since = time.time()
    elif time.time() - state.white_since >= cfg.WHITE_SCREEN_REJOIN_AFTER:
        return "white_screen", f"the screen has been white for {cfg.WHITE_SCREEN_REJOIN_AFTER:g}s"
    if len(state.disabled_needs) >= cfg.DISABLED_NEEDS_REJOIN_THRESHOLD:
        return "stuck", f"{len(state.disabled_needs)} needs disabled ({', '.join(sorted(state.disabled_needs))})"
    if state.last_progress is not None and time.time() - state.last_progress >= cfg.NO_PROGRESS_REJOIN_INTERVAL:
        return "stalled", f"no need resolved for {cfg.NO_PROGRESS_REJOIN_INTERVAL / 60:g} minutes"
    if state.last_rejoin is not None and time.time() - state.last_rejoin >= cfg.REJOIN_INTERVAL:
        return "scheduled", f"{cfg.REJOIN_INTERVAL / 3600:g} hour(s) since the last rejoin"
    return None

def rejoin_game(kinds: Optional[Sequence[str]] = None) -> bool:
    """Recover when rejoin_reason() says so (or only for `kinds`): rejoin, run setup_game(),
    clear disabled needs. Returns True if it recovered. All but the scheduled rejoin and the white screen's
    (logged as events: `white screen: ...`) are logged failures. See docs/ARCHITECTURE.md#stuck-needs.
    """
    at_start = state.run_start_pending and kinds is None
    if kinds is None:
        state.run_start_pending = False   # (an in-task look is never the start of a run)
    found = rejoin_reason()
    if found is None or (kinds is not None and found[0] not in kinds):
        return False
    kind, reason = found
    if kind == "scheduled":
        logger.info(f"{reason} - rejoining")
        log_run_event(f"scheduled rejoin: {reason}")
        run_stats.scheduled_rejoins += 1
    elif at_start and reason == ROBLOX_NOT_RUNNING:
        logger.info(f"{reason} - starting it")
        log_run_event(f"{reason} at the start - starting it")
    elif kind == "white_screen":
        logger.info(f"{reason} - rejoining")                            # the game's own doing, nothing the macro can help: a rejoin that works is all there is to it
        log_run_event(f"white screen: {reason} - rejoining")
        run_stats.recoveries += 1
    else:
        log_failure(f"{reason} - rejoining")
        run_stats.recoveries += 1
        if kind == "disconnect":
            run_stats.disconnects += 1
    if not leave_and_rejoin(clean_leave=(kind != "disconnect")):
        log_failure("recovery: rejoin failed, will try again next cycle")
        return False
    try:
        setup_game()
    except TaskInterrupted as interruption:
        # The game was rejoined again while setup ran (its in-task checks do that on a disconnect), and that
        # nested recovery already ran setup itself - so this one carries on instead of ending the run.
        log_run_event(f"setup interrupted by {interruption.reason} - the nested recovery redid it")
    state.disabled_needs.clear()
    reset_need_tracking()
    log_run_event("recovery complete: rejoined, setup run, disabled needs cleared")
    logger.info("Recovery complete - carrying on")
    write_status()
    return True

def detect_sick_dialog(img: Image) -> bool:
    """True if the sick need's "Ask Doctor Heart to heal your family?" dialog is on screen in `img`."""
    return popup_visible(img, "sick_dialog")

def find_doctor_prompt(img: Image) -> Optional[Point]:
    """Where the hand button over the player's head is (its centre) when the player stands in front of Doctor Heart, else None."""
    return popup_found(img, "doctor_prompt")

def look_until(find: Callable[[Image], Any], seconds: float) -> Any:
    """What `find` returns for a screenshot - one is taken every SICK_LOOK_INTERVAL - as soon as that is something (not None or False),
    else None once `seconds` have gone by (there is always at least one look)."""
    deadline = time.time() + seconds
    while True:
        found = find(grab_screen())
        if found or time.time() >= deadline:
            return found or None
        wait_interruptible(cfg.SICK_LOOK_INTERVAL)

def detect_minigame_popup(img: Image) -> bool:
    """True if a Halloween minigame popup is on screen: a button of the Yes green (a piece of it of at least
    MINIGAME_POPUP_MIN_BUTTON_PIXELS px) with the No red close to its left (neither colour is unique alone), under the popup's own "Teleport there now?" line (a crop,
    POPUP_TEMPLATES["minigame_popup"]). Each green piece
    is looked at on its own: a popup over the open backpack, whose buttons are green too, was missed when all the green on the
    screen was taken together. See docs/ARCHITECTURE.md#need-handlers.
    """
    if detect_sick_dialog(img) or not popup_visible(img, "minigame_popup"):
        return False                                                  # (another dialog with a green and a red button is not the popup: it has its "Teleport there now?")
    green = (exact_color_mask(img, cfg.GREEN_BUTTON_COLOR) > 0).astype(np.uint8)
    count, _, stats, centres = cv2.connectedComponentsWithStats(green)
    for i in range(1, count):
        if stats[i, cv2.CC_STAT_AREA] < cfg.MINIGAME_POPUP_MIN_BUTTON_PIXELS:
            continue
        yes_x, yes_y = int(centres[i][0]), int(centres[i][1])
        near_no = img[max(0, yes_y - cfg.MINIGAME_POPUP_NO_MAX_DY):yes_y + cfg.MINIGAME_POPUP_NO_MAX_DY,
                      max(0, yes_x - cfg.MINIGAME_POPUP_NO_MAX_DX):yes_x]
        if int(np.count_nonzero(exact_color_mask(near_no, cfg.MINIGAME_POPUP_NO_COLOR))) >= cfg.MINIGAME_POPUP_MIN_BUTTON_PIXELS:
            return True
    return False

# directory -> (the files' names and modification times, {name: image}) - see load_templates().
_TEMPLATE_CACHE: dict[str, tuple[Any, dict[str, Image]]] = {}

def load_templates(directory: str) -> dict[str, Image]:
    """{name: image} for every .png in `directory`, cached and re-read when a file changes.
    The images are shared: don't modify them. See docs/ARCHITECTURE.md#need-handlers.
    """
    if not os.path.isdir(directory):
        return {}
    files = sorted(f for f in os.listdir(directory) if f.lower().endswith(".png"))
    key = tuple((f, os.stat(os.path.join(directory, f)).st_mtime_ns) for f in files)
    cached = _TEMPLATE_CACHE.get(directory)
    if cached is None or cached[0] != key:
        templates = {}
        for f in files:
            crop = cv2.imread(os.path.join(directory, f))
            if crop is not None:
                templates[os.path.splitext(f)[0].lower()] = crop
        cached = _TEMPLATE_CACHE[directory] = (key, templates)
    return dict(cached[1])

def template_score(img: Image, crop: Image, box: Box) -> float:
    """How well `crop` matches anywhere within `box` (left, top, right,
    bottom) of `img`: 0-1, 1 = identical (cv2.matchTemplate)."""
    left, top, right, bottom = box
    band = img[top:bottom, left:right]
    if crop.shape[0] > band.shape[0] or crop.shape[1] > band.shape[1]:
        return 0.0
    return float(cv2.minMaxLoc(cv2.matchTemplate(band, crop, cv2.TM_CCOEFF_NORMED))[1])

def find_template(img: Image, crop: Image, box: Box) -> tuple[float, Optional[Point]]:
    """Like template_score(), but also where: (score, (x, y) of the match's
    center in `img`)."""
    left, top, right, bottom = box
    band = img[top:bottom, left:right]
    if crop.shape[0] > band.shape[0] or crop.shape[1] > band.shape[1]:
        return 0.0, None
    _, score, _, (x, y) = cv2.minMaxLoc(cv2.matchTemplate(band, crop, cv2.TM_CCOEFF_NORMED))
    return float(score), (left + x + crop.shape[1] // 2, top + y + crop.shape[0] // 2)

def popup_found(img: Image, name: str) -> Optional[Point]:
    """Where the STRAY_WINDOW_DIR crop `name` is in `img` - its centre - if it matches within its box well enough
    (POPUP_TEMPLATES, or STRAY_WINDOWS for a stray window), else None. See docs/ARCHITECTURE.md#popup-templates."""
    crop = load_templates(cfg.STRAY_WINDOW_DIR).get(name)
    entry = cfg.POPUP_TEMPLATES.get(name) or cfg.STRAY_WINDOWS[name]
    if crop is None:
        return None
    score, centre = find_template(img, crop, entry["box"])
    return centre if score >= entry.get("threshold", cfg.STRAY_WINDOW_MATCH_THRESHOLD) else None

def popup_visible(img: Image, name: str) -> bool:
    """True if the crop `name` is on screen in `img` (see popup_found())."""
    return popup_found(img, name) is not None

def identify_minigame(img: Image) -> Optional[str]:
    """Which minigame the popup in `img` is for: the name whose title crop
    (MINIGAME_TEMPLATE_DIR) matches best within MINIGAME_TITLE_BOX, if that
    match reaches MINIGAME_TITLE_MATCH_THRESHOLD - otherwise None."""
    best_name, best_score = None, cfg.MINIGAME_TITLE_MATCH_THRESHOLD
    for name, crop in load_templates(cfg.MINIGAME_TEMPLATE_DIR).items():
        score = template_score(img, crop, cfg.MINIGAME_TITLE_BOX)
        if score >= best_score:
            best_name, best_score = name, score
    return best_name

# True while a handler that needs the pet focused (choose, pet) is running;
# the "pet_focus" stray window is left alone then.

@contextlib.contextmanager
def wanting_pet_focus() -> Iterator[None]:
    """Within this block dismiss_stray_windows() doesn't back out of the pet
    focus view."""
    state.pet_focus_wanted = True
    try:
        yield
    finally:
        state.pet_focus_wanted = False

def dismiss_stray_windows() -> Optional[str]:
    """Close any window from STRAY_WINDOWS that is open (found by its title crop); returns
    its name or None. See docs/ARCHITECTURE.md#need-handlers.
    """
    img = grab_screen()
    for name, config in cfg.STRAY_WINDOWS.items():
        if not popup_visible(img, name):
            continue
        if config.get("only_when_unwanted") and state.pet_focus_wanted:
            continue
        logger.debug(f"Stray window open: {name}, closing it...")
        log_run_event(f"stray window closed: {name}")
        if name == "trading_hub":
            if color_pixels(img, cfg.STRAY_OKAY_BOX, cfg.GREEN_BUTTON_COLOR, cfg.GREEN_BUTTON_TOLERANCE) >= cfg.STRAY_OKAY_MIN_PIXELS:
                hover_click(*cfg.STRAY_OKAY_POS)
        hover_click(*config["close_pos"])
        time.sleep(cfg.STRAY_CLOSE_SETTLE)
        return name
    return None

def detect_minigame_finished(img: Image) -> bool:
    """True if the minigame finish screen is on screen in `img`: the red
    GAME OVER! banner and the green NICE! button, both by exact color (the
    same green is the Yes button's, so the banner is what rules that out)."""
    for color, tolerance, box, minimum in (
            (cfg.MINIGAME_FINISH_BANNER_COLOR, cfg.MINIGAME_FINISH_BANNER_TOLERANCE,
             cfg.MINIGAME_FINISH_BANNER_BOX, cfg.MINIGAME_FINISH_BANNER_MIN_PIXELS),
            (cfg.GREEN_BUTTON_COLOR, cfg.GREEN_BUTTON_TOLERANCE,
             cfg.MINIGAME_FINISH_BUTTON_BOX, cfg.MINIGAME_FINISH_BUTTON_MIN_PIXELS)):
        if color_pixels(img, box, color, tolerance) < minimum:
            return False
    return True

def minigame_overlay_visible(img: Image, name: str) -> bool:
    """True if minigame `name`'s overlay (its crop in MINIGAME_OVERLAY_DIR, within its box in MINIGAME_OVERLAYS) is on screen in `img`, at least
    MINIGAME_OVERLAY_THRESHOLD: one template match, not one per minigame, since this is asked at every look."""
    crop = load_templates(cfg.MINIGAME_OVERLAY_DIR).get(name)
    return crop is not None and name in cfg.MINIGAME_OVERLAYS and template_score(img, crop, cfg.MINIGAME_OVERLAYS[name]) >= cfg.MINIGAME_OVERLAY_THRESHOLD

class MinigameWatch:
    """Keeps track of whether the minigame being played is on screen (its
    overlay - minigame_overlay_visible()) and whether the game disconnected
    (detect_disconnect()), fed one screenshot at a time by minigame_look() and minigame_wait().
    `problem` says why to stop, or None."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.last_seen: Optional[float] = None
        self.start_grace_from: Optional[float] = None   # set once the start wait is over
        self.disconnected = False                       # the "Disconnected" dialog has been seen

    def update(self, img: Image) -> None:
        if detect_disconnect(img):
            self.disconnected = True
        elif minigame_overlay_visible(img, self.name):
            self.last_seen = time.time()

    def start_wait_over(self) -> None:
        self.start_grace_from = time.time()

    @property
    def problem(self) -> Optional[str]:
        now = time.time()
        if self.disconnected:
            return "disconnected from the game"
        if self.last_seen is not None and now - self.last_seen > cfg.MINIGAME_OVERLAY_LOST_TIMEOUT:
            return f"left the minigame (its overlay was gone for {cfg.MINIGAME_OVERLAY_LOST_TIMEOUT:g}s)"
        if (self.last_seen is None and self.start_grace_from is not None
                and now - self.start_grace_from > cfg.MINIGAME_OVERLAY_START_GRACE):
            return f"never started (no overlay within {cfg.MINIGAME_OVERLAY_START_GRACE:g}s of the start wait)"
        return None


def minigame_popup_checks() -> None:
    """The popups that can turn up mid-minigame - stray windows and the
    paycheck - checked at most every MINIGAME_POPUP_CHECK_INTERVAL. (The
    in-task unscrew() can't run here: the minigame is itself played from it.)"""
    if time.time() - state.last_minigame_popup_check < cfg.MINIGAME_POPUP_CHECK_INTERVAL:
        return
    state.last_minigame_popup_check = time.time()
    if dismiss_stray_windows():
        mark_run_capture("a stray window was closed")
    elif detect_paycheck():
        mark_run_capture("the paycheck popup was closed")

def minigame_look(watch: MinigameWatch) -> Image:
    """Run the popup checks if they're due, then take a fresh screenshot, which `watch` is told about."""
    minigame_popup_checks()
    img = grab_screen()
    watch.update(img)
    return img

def zoom_out() -> None:
    """Zoom the camera out (KEYS["zoom_out"] held MINIGAME_ZOOM_OUT_DURATION seconds) to see the whole room: close up, the TVs
    over a Hauntlet 2 room's doors are cut off by the top of the screen."""
    logger.debug("Zooming the camera out")
    hold_key(cfg.KEYS["zoom_out"], cfg.MINIGAME_ZOOM_OUT_DURATION)

def minigame_wait(duration: float, watch: MinigameWatch) -> None:
    """wait_interruptible(), with the popup checks every
    MINIGAME_POPUP_CHECK_INTERVAL and `watch` kept up to date - for the long
    waits of a minigame. Ends early if the minigame is plainly over."""
    end = time.time() + duration
    while time.time() < end:
        wait_interruptible(min(cfg.MINIGAME_POPUP_CHECK_INTERVAL, end - time.time()))
        minigame_popup_checks()
        watch.update(grab_screen())
        if watch.problem:
            return

def wait_for_minigame_start(watch: MinigameWatch, name: str) -> None:
    """Wait, looking every MINIGAME_START_LOOK_INTERVAL, until the minigame's overlay shows (the game has begun: the lobby's countdown to a round that starts
    at a fixed time), for at most MINIGAME_START_WAIT, or until `watch` (the minigame `name`'s) has a problem (a disconnect); then tell `watch` the start wait is over."""
    logger.debug(f"{cfg.MINIGAME_LABELS[name]}: waiting for it to start (at most {cfg.MINIGAME_START_WAIT:g}s)...")
    waited_until = time.time() + cfg.MINIGAME_START_WAIT
    while time.time() < waited_until and watch.last_seen is None and not watch.problem:
        minigame_wait(cfg.MINIGAME_START_LOOK_INTERVAL, watch)
    watch.start_wait_over()

class HauntletRun:
    """What play_hauntlet_2() knows about the room it is in: its number, when that changed, and the TVs of the last
    look. look() says when the doors can be chosen from. See docs/ARCHITECTURE.md#playing-hauntlet-2."""

    def __init__(self) -> None:
        self.room: Optional[int] = None
        self.since = 0.0                              # when the number changed
        self.walked_at: Optional[float] = None        # when the last walk in this room ended
        self.stuck = 0                                # walks in a row in this room that didn't change the room number
        self.last_index: Optional[int] = None         # the door of the last of them
        self.picked: Optional[int] = None             # the door whose circle the player has stepped into: that is the choice made, for good
        self.turns = 0                                # times the camera was turned to find the doors, in this room
        self.rooms = 0                                # numbers seen
        self.tvs: Optional[list[tuple[int, int]]] = None   # of the last look: None if the timer was hidden
        self.empty_looks = 0                          # looks in a row at a room without TVs
        self.doors_missing_reported = False           # the failure for a room with no doors found has been logged
        self.white = False                            # the last look was a white screen

    @property
    def fork(self) -> bool:
        """Whether this room's number is one of the forks (HAUNTLET_FORK_EVERY)."""
        return self.room is not None and self.room > 0 and self.room % cfg.HAUNTLET_FORK_EVERY == 0

    def look(self, img: Image, now: float) -> Optional[list[HauntletDoor]]:
        """Take in a screenshot. The doors to choose from - three, or a fork's two - if it is time to, else None."""
        room = read_hauntlet_room(img)
        if room is not None and room != self.room:
            self.room, self.since, self.walked_at, self.rooms = room, now, None, self.rooms + 1
            self.stuck, self.last_index, self.turns, self.picked = 0, None, 0, None
            self.tvs, self.empty_looks = None, 0
            logger.debug(f"Hauntlet 2: room {room}")
        if self.room is None:
            return None
        if detect_white_screen(img):                  # a heart lost to a ghost, a death: the game whites the screen out and brings the player back to the room's start,
            if not self.white:                        # so the room is looked at afresh from now - not taken for one with no doors because it was entered long ago
                logger.debug("Hauntlet 2: the screen went white (a ghost, or a fall): the room starts again")
            self.white, self.since, self.walked_at, self.tvs, self.empty_looks = True, now, None, None, 0
            return None
        self.white = False
        age = now - self.since
        before, self.tvs = self.tvs, (find_door_tvs(img) if hauntlet_timer_visible(img) else None)
        if self.tvs is None or age < cfg.HAUNTLET_ROOM_SETTLE or (self.walked_at is not None and now < self.walked_at + cfg.HAUNTLET_RETRY_WAIT):
            self.empty_looks = 0
            return None
        if self.tvs:
            self.empty_looks = 0
            steady = before is not None and len(before) == len(self.tvs) and all(
                abs(a - b) <= cfg.HAUNTLET_TV_STABLE for old, new in zip(before, self.tvs) for a, b in zip(old, new))
            return find_hauntlet_doors(img) if steady or age >= cfg.HAUNTLET_DOORS_TIMEOUT else None
        self.empty_looks += 1
        if self.fork and self.empty_looks >= 2:
            return [HauntletDoor(0, 0, None), HauntletDoor(0, 0, None)]       # two doorways, not located
        if not self.fork and (age >= cfg.HAUNTLET_DOORS_TIMEOUT or (self.room == 1 and self.empty_looks >= 2)):       # room 1 is the entrance hall (a fork waits for its second empty look: after a walk the looks start again, and a fork past the timeout was taken for one doorway and the camera was turned)
            return [HauntletDoor(0, 0, None)]                                  # one doorway, straight ahead
        return None

def choose_hauntlet_door(doors: list[HauntletDoor], items: list[HauntletItem], avoid: Collection[int] = ()) -> int:
    """The index in `doors` (left to right) of the door to go through: a random one, but none of `avoid` (the one the phone's
    arrow is over, where a ghost is, and the one that just got the player stuck) and a locked one only when the grey key is
    held - unless that leaves nothing, then whatever is left."""
    has_key = any(item.name == cfg.HAUNTLET_ITEM_KEY for item in items)
    allowed = [i for i in range(len(doors)) if i not in avoid]
    options = [i for i in allowed if doors[i].kind != "locked" or has_key]
    return random.choice(options or allowed or list(range(len(doors))))

def hauntlet_items_for(door: HauntletDoor, hearts: Optional[HauntletHearts], items: list[HauntletItem]) -> list[str]:
    """The items (names) to use before going through `door`, in the order to use them: the grey key for a locked door, a cyan potion
    (a heart that lasts the room) for a boarded one, the red potion when the hearts are down to HAUNTLET_LOW_HEARTS or fewer, and a
    heart potion whenever there is one. Only those held."""
    held = {item.name for item in items}
    wanted = []
    if door.kind == "locked":
        wanted.append(cfg.HAUNTLET_ITEM_KEY)
    if door.kind == "boarded":
        wanted.append(cfg.HAUNTLET_ITEM_BOARDED)
    if hearts is not None and hearts.red <= cfg.HAUNTLET_LOW_HEARTS:
        wanted.append(cfg.HAUNTLET_ITEM_REFILL)
    wanted.append(cfg.HAUNTLET_ITEM_HEART)
    return [name for name in wanted if name in held]

def find_door_circles(img: Image) -> list[tuple[int, int, int, int]]:
    """The purple circles on the floor in front of a room's doors - stepping into one picks that door - as (x, y, width, height)
    of each one's middle, left to right: the ring-shaped pieces of HAUNTLET_CIRCLE_HSV of about the right size (HAUNTLET_CIRCLE_*), which
    leaves out the purple badges and labels. One the player stands on is mostly hidden, so it can be missing."""
    top, bottom = cfg.HAUNTLET_CIRCLE_BAND
    mask = cv2.inRange(cv2.cvtColor(img[top:bottom], cv2.COLOR_BGR2HSV), cfg.HAUNTLET_CIRCLE_HSV[0], cfg.HAUNTLET_CIRCLE_HSV[1])
    count, _, stats, _ = cv2.connectedComponentsWithStats(cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)))
    found = []
    for i in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[i])
        y += top
        if (cfg.HAUNTLET_CIRCLE_WIDTH[0] <= w <= cfg.HAUNTLET_CIRCLE_WIDTH[1] and cfg.HAUNTLET_CIRCLE_HEIGHT[0] <= h <= cfg.HAUNTLET_CIRCLE_HEIGHT[1]
                and cfg.HAUNTLET_CIRCLE_MIN_AREA <= area < cfg.HAUNTLET_CIRCLE_FILL * w * h):
            found.append((x + w // 2, y + h // 2, w, h))
    return sorted(found)

def find_fork_doorways(img: Image) -> Optional[tuple[int, int]]:
    """The x of the middle of a fork's two doorways, left and right, or None unless exactly two openings of the right size and
    distance are seen (HAUNTLET_OPENING_*): each opening is dark inside and starts right under the wall's pale stripes, where the wall
    elsewhere has the gold wainscot below it. Only seen with the camera near the wall - from the room's entrance one is, or none."""
    top, bottom = cfg.HAUNTLET_FORK_BAND
    hsv = cv2.cvtColor(img[top:bottom], cv2.COLOR_BGR2HSV)
    wall = cv2.inRange(hsv, cfg.HAUNTLET_WALL_HSV[0], cfg.HAUNTLET_WALL_HSV[1]) > 0
    dark = cv2.inRange(hsv, cfg.HAUNTLET_OPENING_HSV[0], cfg.HAUNTLET_OPENING_HSV[1]) > 0
    run = dark.copy()
    for rows in range(1, cfg.HAUNTLET_OPENING_RUN):                       # a row that starts a run of HAUNTLET_OPENING_RUN dark ones
        run[:-rows] &= dark[rows:]
    first = run.argmax(axis=0)                                            # the first such row of each column (0 if none)
    near, far = cfg.HAUNTLET_OPENING_ABOVE
    wall_above = np.vstack([np.zeros((1, wall.shape[1]), int), np.cumsum(wall, axis=0)])
    columns = np.arange(wall.shape[1])
    walled = wall_above[np.maximum(first - near, 0), columns] - wall_above[np.maximum(first - far, 0), columns]
    openings = run.any(axis=0) & (walled >= cfg.HAUNTLET_OPENING_WALL)
    spans: list[list[int]] = []
    for x in np.flatnonzero(openings):
        if spans and x - spans[-1][1] <= cfg.HAUNTLET_OPENING_JOIN:
            spans[-1][1] = int(x)
        else:
            spans.append([int(x), int(x)])
    whole = [span for span in spans if cfg.HAUNTLET_OPENING_WIDTH[0] <= span[1] - span[0] + 1 <= cfg.HAUNTLET_OPENING_WIDTH[1]]
    if len(whole) == 2:
        left, right = ((a + b) // 2 for a, b in whole)
        return (left, right) if cfg.HAUNTLET_OPENING_GAP[0] <= right - left <= cfg.HAUNTLET_OPENING_GAP[1] else None
    # Seen at an angle the second doorway shows only as a narrow piece (its far side is lit, not dark): one whole opening that isn't
    # too wide, with exactly one narrow piece the distance away that the doorways are of each other, is the pair.
    pairs = []
    for opening in whole:
        if opening[1] - opening[0] + 1 > cfg.HAUNTLET_OPENING_MAIN_MAX:
            continue
        for piece in spans:
            if cfg.HAUNTLET_OPENING_PIECE[0] <= piece[1] - piece[0] + 1 <= cfg.HAUNTLET_OPENING_PIECE[1] and \
                    cfg.HAUNTLET_OPENING_PIECE_GAP[0] <= abs((piece[0] + piece[1]) // 2 - (opening[0] + opening[1]) // 2) <= cfg.HAUNTLET_OPENING_PIECE_GAP[1]:
                pairs.append(sorted(((opening[0] + opening[1]) // 2, (piece[0] + piece[1]) // 2)))
    return (pairs[0][0], pairs[0][1]) if len(pairs) == 1 else None

def player_in_circle(img: Image, circles: list[tuple[int, int, int, int]]) -> bool:
    """Whether the player stands in one of the three circles - and so has picked that door, and is locked in place for a few moments:
    two circles are seen, the third being hidden under the player, whose place (where the hearts are, else mid-screen) is where
    the third one would be (the same distance from its neighbour as the other two are from each other, HAUNTLET_IN_CIRCLE of it)."""
    if len(circles) != 2:
        return False
    hearts = read_hauntlet_hearts(img)
    x = hearts.center_x if hearts else cfg.REFERENCE_CENTER_X
    left, right = circles[0][0], circles[1][0]
    gap = right - left
    if left < x < right:                                   # between them: the middle one (they are two spacings apart)
        return abs(x - (left + right) / 2) <= cfg.HAUNTLET_IN_CIRCLE * gap / 2
    return abs(x - (left - gap if x < left else right + gap)) <= cfg.HAUNTLET_IN_CIRCLE * gap

def hauntlet_strafe(img: Image, doors: list[HauntletDoor], index: int) -> tuple[Optional[str], float]:
    """The key to strafe with, and for how long, to reach door `index` from where the player stands in `img`: the
    distance to the door in door spacings (the TVs' spacing) times HAUNTLET_STRAFE_DURATION, or no strafe when that
    is under HAUNTLET_STRAFE_MIN spacings. Where the player starts a room can depend on the door it came through, so
    it is measured (the hearts above the head); not seen, it is taken to be in the middle of the screen (the camera follows the player; the
    middle door is not always in front of it - in a captured room 3 the player stood 350 px right of the middle door, hearts unread, and walked into the wall). Doors that
    aren't located have no distance to go: a fork's two doorways are the left key or the right one for one spacing, a
    single doorway ahead (an entrance hall) none. A fork's doorways that were found (find_fork_doorways(): the two x are
    in `doors`) are two door spacings apart, like a room's outer doors, and are walked to like a room's."""
    spacing = (doors[-1].x - doors[0].x) / 2 if len(doors) in (2, 3) else 0
    if spacing <= 0:
        if len(doors) == 1 or (len(doors) == 3 and index == 1):
            return None, 0.0
        return (cfg.KEYS["left"] if index == 0 else cfg.KEYS["right"]), cfg.HAUNTLET_STRAFE_DURATION
    hearts = read_hauntlet_hearts(img)
    circles = find_door_circles(img)
    target = circles[index][0] if len(circles) == len(doors) == 3 else doors[index].x       # the circle in front of the door, where it can be seen: it is what is to be stepped in
    distance = (target - (hearts.center_x if hearts else cfg.REFERENCE_CENTER_X)) / spacing
    if abs(distance) < cfg.HAUNTLET_STRAFE_MIN:
        return None, 0.0
    return (cfg.KEYS["right"] if distance > 0 else cfg.KEYS["left"]), min(abs(distance), cfg.HAUNTLET_STRAFE_MAX) * cfg.HAUNTLET_STRAFE_DURATION

def hauntlet_use_item(name: str, watch: MinigameWatch) -> Optional[Image]:
    """Click the slot of the item called `name` and give the game HAUNTLET_ITEM_WAIT seconds to use it. The slots move together
    when an item is used up, so the slot is looked for on a fresh screenshot each time. The screenshot taken after the click,
    or None if the item isn't there."""
    item = next((i for i in read_hauntlet_items(minigame_look(watch)) if i.name == name), None)
    if item is None:
        return None
    hover_click(*item.pos)
    wait_interruptible(cfg.HAUNTLET_ITEM_WAIT)
    return minigame_look(watch)

def hauntlet_choose(img: Image, doors: list[HauntletDoor], run: HauntletRun, watch: MinigameWatch) -> tuple[int, Image]:
    """Choose the door and use the items it calls for, logging the lot. With a chance of HAUNTLET_PHONE_CHANCE the phone is used first, and
    the door its red arrow shows is not gone through. Returns the door's index and the latest screenshot."""
    if run.picked is not None and run.picked < len(doors):
        logger.debug(f"Hauntlet 2: room {run.room}, the door is picked already (its circle was stepped in): going to the same one")
        return run.picked, img
    hearts = read_hauntlet_hearts(img)
    items = read_hauntlet_items(img)
    names = {1: ("ahead",), 2: ("left", "right"), 3: ("left", "middle", "right")}[len(doors)]
    ghost: Optional[int] = None
    used: list[str] = []
    low = hearts is not None and hearts.red <= cfg.HAUNTLET_LOW_HEARTS
    chance = cfg.HAUNTLET_PHONE_CHANCE_LOW if low else cfg.HAUNTLET_PHONE_CHANCE                 # the phone shows where the ghost is: more use when hearts are few
    if any(item.name == cfg.HAUNTLET_ITEM_PHONE for item in items) and random.random() < chance:
        shown = hauntlet_use_item(cfg.HAUNTLET_ITEM_PHONE, watch)
        if shown is not None:
            used.append(cfg.HAUNTLET_ITEM_PHONE)
            img = shown
            for _ in range(cfg.HAUNTLET_ARROW_LOOKS):                           # the arrow takes a moment to show
                ghost = find_ghost_door(img, doors)
                if ghost is not None:
                    break
                wait_interruptible(cfg.HAUNTLET_LOOK_INTERVAL)
                img = minigame_look(watch)
            items = read_hauntlet_items(img)
    avoid = {door for door in (ghost, run.last_index if run.stuck else None) if door is not None}      # the ghost's, and the one that got us stuck
    index = choose_hauntlet_door(doors, items, avoid=avoid)
    run.last_index = index
    for name in hauntlet_items_for(doors[index], hearts, items):
        shown = hauntlet_use_item(name, watch)
        if shown is not None:
            used.append(name)
            img = shown
    logger.debug(f"Hauntlet 2: room {run.room}, "
          + (f"{hearts.red} hearts ({hearts.black} lost)" if hearts else "hearts not seen")
          + f", items: {', '.join(f'{item.name} x{item.count or 1}' for item in items) or 'none'}"
          + f", doors: {', '.join(door.kind or '-' for door in doors)}"
          + (f", ghost behind the {names[ghost]} one" if ghost is not None else "")
          + f", going {names[index]}" + (f", used {', '.join(used)}" if used else ""))
    return index, img

def hauntlet_view(img: Image) -> np.ndarray:
    """`img` shrunk to a grey HAUNTLET_VIEW_SIZE, without the room panel at the top and the item bar at the bottom (HAUNTLET_VIEW_ROWS): what the walk looks at to
    tell whether anything is moving (the camera follows the player, so a player who moves makes the whole view move)."""
    small = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cfg.HAUNTLET_VIEW_SIZE, interpolation=cv2.INTER_AREA).astype(np.int16)
    small[:cfg.HAUNTLET_VIEW_ROWS[0]] = 0
    small[cfg.HAUNTLET_VIEW_ROWS[1]:] = 0
    return small

def hauntlet_unstick(img: Image, target_index: Optional[int], tries: int, last_key: Optional[str]) -> str:
    """Get off whatever the player is caught on while holding forward: a step sideways (the forward key stays held, so it slides
    along) and a jump. The way is towards the door being walked to (its TV against where the player stands, HAUNTLET_UNSTICK_MARGIN
    px or more off), the other way each time after that, and when the way isn't known a random one first; the step is
    HAUNTLET_UNSTICK_STEP seconds longer for each try, up to HAUNTLET_UNSTICK_MAX. The key used. In a fork the door's place is the doorway
    (find_fork_doorways()), when both are in sight."""
    tvs = find_door_tvs(img)
    where = [x for x, _ in tvs] if len(tvs) == 3 else list(find_fork_doorways(img) or ())
    key: Optional[str] = None
    if last_key is not None:
        key = cfg.KEYS["right"] if last_key == cfg.KEYS["left"] else cfg.KEYS["left"]
    elif target_index is not None and target_index < len(where):
        hearts = read_hauntlet_hearts(img)
        dx = where[target_index] - (hearts.center_x if hearts else cfg.REFERENCE_CENTER_X)
        if abs(dx) >= cfg.HAUNTLET_UNSTICK_MARGIN:
            key = cfg.KEYS["right"] if dx > 0 else cfg.KEYS["left"]
    if key is None:
        key = random.choice((cfg.KEYS["left"], cfg.KEYS["right"]))
    logger.debug(f"Hauntlet 2: not moving - a step {key}")
    mark_run_capture("Hauntlet 2: not moving")
    pydirectinput.press(cfg.KEYS["jump"])
    hold_key(key, min(cfg.HAUNTLET_UNSTICK_STEP * (tries + 1), cfg.HAUNTLET_UNSTICK_MAX))
    return key

def hauntlet_steer(img: Image, target_index: int) -> Optional[tuple[str, float]]:
    """While walking to door `target_index`: the key to strafe with and for how long, if the player has drifted off the door (as hauntlet_strafe()
    measures it: the three TVs, the circle in front of the door, the hearts) by HAUNTLET_STEER_MIN door spacings or more, else None - also None when the
    three TVs aren't all on screen (close to the doors they are cut off). HAUNTLET_STEER_GAIN of the strafe, at most HAUNTLET_STEER_MAX seconds."""
    doors = find_hauntlet_doors(img)
    if len(doors) != 3:
        return None
    key, seconds = hauntlet_strafe(img, doors, target_index)
    if key is None or seconds < cfg.HAUNTLET_STEER_MIN * cfg.HAUNTLET_STRAFE_DURATION:
        return None
    return key, min(seconds * cfg.HAUNTLET_STEER_GAIN, cfg.HAUNTLET_STEER_MAX)

def hauntlet_walk(direction: Optional[str], duration: float, run: HauntletRun, watch: MinigameWatch, until_doors: bool = False,
                  target_index: Optional[int] = None, until_doorways: bool = False) -> bool:
    """Walk to a door: first strafe, `direction` (a strafe key, or None) held for `duration` seconds on its own, then hold forward
    until the room number changes (at most HAUNTLET_WALK_MAX seconds) and then HAUNTLET_WALK_PAST_DURATION longer, as
    the number changes with the player still in the doorway - or, with `until_doors`, until a row of TVs is
    on screen (a hall's doorway leads into a room whose number is the same), or, with `until_doorways`, until both of a
    fork's doorways can be seen (find_fork_doorways()). The view is looked at every HAUNTLET_LOOK_INTERVAL
    seconds: if it hasn't moved (hauntlet_view(), HAUNTLET_STILL_LEVEL) for HAUNTLET_STILL_AFTER seconds the player is caught
    on something and hauntlet_unstick() steps sideways, towards door `target_index` of the three (or the fork's two). A walk that doesn't get
    anywhere counts in `run.stuck`; the next one steps back first (HAUNTLET_BACKUP_DURATION). True if the minigame finished."""
    run.walked_at = time.time()
    if run.stuck > 0:
        hold_key(cfg.KEYS["back"], cfg.HAUNTLET_BACKUP_DURATION)
    aimed = False
    if target_index is not None and not run.fork:                               # aimed in small steps, looking at the doors again before each (HAUNTLET_AIM_STEPS)
        for _ in range(cfg.HAUNTLET_AIM_STEPS):
            img = minigame_look(watch)
            if detect_minigame_finished(img):
                return True
            steer = hauntlet_steer(img, target_index)
            if steer is None:
                break
            hold_key(*steer)
            aimed = True
    if not aimed and direction and duration > 0:
        hold_key(direction, duration)                                           # one key at a time: the strafe, then the walk (what couldn't be aimed is a strafe worked out in one go)
    arrived = False
    started = time.time()
    key_down(cfg.KEYS["forward"])
    try:
        last_view: Optional[np.ndarray] = None
        still_since: Optional[float] = None
        tries, last_key = 0, None
        locked_until, was_inside = 0.0, False
        steer_at = started + cfg.HAUNTLET_STEER_EVERY
        while time.time() < started + cfg.HAUNTLET_WALK_MAX:
            img = minigame_look(watch)
            if detect_minigame_finished(img):
                return True
            room = read_hauntlet_room(img)
            if room is not None and room != run.room:
                arrived = True
                wait_interruptible(cfg.HAUNTLET_WALK_PAST_DURATION)             # the number changes in the doorway: on into the room
                break
            if watch.problem:
                break
            if (until_doors and find_door_tvs(img)) or (until_doorways and find_fork_doorways(img)):
                arrived = True
                break
            now = time.time()
            inside = player_in_circle(img, find_door_circles(img))
            if inside and not was_inside:                                       # just stepped in: the door is picked and the player is held for a moment
                locked_until = now + cfg.HAUNTLET_LOCK_WAIT
                if run.picked is None and target_index is not None:
                    run.picked = target_index
                    logger.debug(f"Hauntlet 2: in the circle of door {target_index + 1} - picked")
            was_inside = inside
            if now < locked_until:
                still_since, last_view = None, None
                wait_interruptible(cfg.HAUNTLET_LOOK_INTERVAL)
                continue
            if target_index is not None and not run.fork and now >= steer_at:      # the way forward isn't always towards the door (a room seen at an angle): steer
                steer_at = now + cfg.HAUNTLET_STEER_EVERY
                steer = hauntlet_steer(img, target_index)
                if steer is not None:
                    logger.debug(f"Hauntlet 2: off the door - a step {steer[0]} for {steer[1]:.2f}s")
                    key_up(cfg.KEYS["forward"])                # one key at a time
                    hold_key(*steer)
                    key_down(cfg.KEYS["forward"])
                    still_since, last_view = None, None
                    continue
            view = hauntlet_view(img)
            if last_view is not None and float(np.abs(view - last_view).mean()) < cfg.HAUNTLET_STILL_LEVEL:
                still_since = now if still_since is None else still_since
            else:
                still_since = None
            last_view = view
            if still_since is not None and now - still_since >= cfg.HAUNTLET_STILL_AFTER:
                last_key = hauntlet_unstick(img, target_index, tries, last_key)
                tries += 1
                still_since, last_view = None, None
                continue
            wait_interruptible(cfg.HAUNTLET_LOOK_INTERVAL)
    finally:
        key_up(cfg.KEYS["forward"])
        run.walked_at = time.time()
        run.stuck = 0 if arrived else run.stuck + 1
    if not arrived:
        mark_run_capture(f"Hauntlet 2: the walk in room {run.room} got nowhere")
    return False

def play_hauntlet_2(deadline: float, watch: MinigameWatch) -> bool:
    """Hauntlet 2: wait for it to start, then room after room choose a door and walk to it (HauntletRun,
    hauntlet_choose(), hauntlet_walk()) until the minigame finishes or the overlay says it's over. True if it
    finished before `deadline`. See docs/ARCHITECTURE.md#playing-hauntlet-2.
    """
    wait_for_minigame_start(watch, "hauntlet_2")
    zoom_out()
    if cfg.HAUNTLET_START_FORWARD > 0:
        wait_interruptible(cfg.HAUNTLET_START_DELAY)                   # the doors of the entrance hall open after a moment...
        hold_key(cfg.KEYS["forward"], cfg.HAUNTLET_START_FORWARD)    # ...and the way out is a step or two ahead
    run = HauntletRun()
    try:
        while time.time() < deadline and not watch.problem:
            img = minigame_look(watch)
            if detect_minigame_finished(img):
                return True
            doors = run.look(img, time.time())
            if doors is None:
                wait_interruptible(cfg.HAUNTLET_LOOK_INTERVAL)
                continue
            if len(doors) == 1 and run.room and run.room > 1 and not run.doors_missing_reported:   # not the entrance hall (room 1): keep the screenshot
                run.doors_missing_reported = True
                log_failure(f"Hauntlet 2: no doors found in room {run.room}")
            if len(doors) == 1 and run.room and run.room > 1 and run.turns < cfg.HAUNTLET_SEARCH_TURNS:
                if run.turns == 0:
                    zoom_out()                                                  # perhaps the camera is too close to see the TVs...
                rotate_camera(cfg.HAUNTLET_CAMERA_DRAG, 0)                      # ...or turned away from them: look around, then look again
                run.turns += 1
                continue
            index, img = hauntlet_choose(img, doors, run, watch)
            if len(doors) == 2:
                doorways = find_fork_doorways(img)
                if doorways is None:                                            # the doorways are too far to be seen from the entrance: walk on until both are
                    if hauntlet_walk(None, 0.0, run, watch, until_doorways=True):
                        return True
                    img = minigame_look(watch)
                    if read_hauntlet_room(img) not in (None, run.room):         # (the walk went through one)
                        continue
                    doorways = find_fork_doorways(img)
                if doorways is not None:
                    doors = [HauntletDoor(x, 0, None) for x in doorways]
                else:
                    mark_run_capture(f"Hauntlet 2: the fork's doorways of room {run.room} were not seen")
            direction, duration = hauntlet_strafe(img, doors, index)
            if hauntlet_walk(direction, duration, run, watch, until_doors=len(doors) == 1, target_index=index if len(doors) >= 2 else None):
                return True
            if len(doors) == 1:
                run.walked_at = None                                            # whatever is past the doorway is looked at at once
    finally:
        if run.room is not None:
            log_run_event(f"Hauntlet 2: got as far as room {run.room}")
            state.minigame_detail["room"] = run.room
    return False

def ghost_character_box(hsv: Image) -> Optional[tuple[int, int, int, int]]:
    """Where the character (and its pet, hoodie and beam) is when the camera is close to it - pulled in by a wall, as it is in the
    dark of the boss fight - and its cyan clothes are blobs as big as ghosts: the glowing pink emblem of its backpack
    (GHOST_EMBLEM_HSV, at least GHOST_EMBLEM_MIN px, in GHOST_EMBLEM_BOX) gives the place and, by its size, how close the camera is;
    the box is GHOST_SELF_SCALE times the emblem's size (its side's square root, at least GHOST_SELF_MIN_UNIT) to the sides, up and down.
    None when the emblem isn't seen: the camera is far, and GHOST_IGNORE_BOXES' small box does."""
    left, top, right, bottom = cfg.GHOST_EMBLEM_BOX
    mask = cv2.inRange(hsv[top:bottom, left:right], cfg.GHOST_EMBLEM_HSV[0], cfg.GHOST_EMBLEM_HSV[1])
    count, _, stats, centres = cv2.connectedComponentsWithStats(mask)
    best = max(range(1, count), key=lambda i: stats[i, cv2.CC_STAT_AREA], default=None)
    if best is None or stats[best, cv2.CC_STAT_AREA] < cfg.GHOST_EMBLEM_MIN:
        return None
    x, y = left + int(centres[best][0]), top + int(centres[best][1])
    unit = max(float(np.sqrt(stats[best, cv2.CC_STAT_AREA])), cfg.GHOST_SELF_MIN_UNIT)
    side, up, down = cfg.GHOST_SELF_SCALE
    return int(x - side * unit), int(y - up * unit), int(x + side * unit), int(y + down * unit)

def find_ghost_blobs(img: Image, boss: bool = False) -> list[tuple[int, int, int]]:
    """(x, y, area) of every ghost in `img`, biggest first: the connected pieces of the translucent cyan the ghosts and the
    boss are drawn in (GHOST_COLOR), at least GHOST_MIN_AREA px, not in the HUD (GHOST_IGNORE_BOXES) or on the character -
    and, in the boss phase (`boss`), not on the boss's health bar, whose crest and icons are cyan too."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.bitwise_or(cv2.inRange(hsv, *cfg.GHOST_COLOR), cv2.inRange(hsv, *cfg.GHOST_COLOR_FAR))
    closeup = ghost_character_box(hsv)
    for left, top, right, bottom in cfg.GHOST_IGNORE_BOXES + ((cfg.GHOST_BOSS_BAR_IGNORE,) if boss else ()) + ((closeup,) if closeup else ()):
        mask[max(0, top):bottom, max(0, left):right] = 0
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((cfg.GHOST_CLOSE, cfg.GHOST_CLOSE), np.uint8))
    count, _, stats, centres = cv2.connectedComponentsWithStats(mask, connectivity=8)
    blobs = [(int(centres[i][0]), int(centres[i][1]), int(stats[i, cv2.CC_STAT_AREA])) for i in range(1, count)
             if stats[i, cv2.CC_STAT_AREA] >= cfg.GHOST_MIN_AREA]
    return sorted(blobs, key=lambda blob: -blob[2])

def find_ghost_alerts(img: Image) -> list[tuple[int, int, int]]:
    """(x, y, height) of every white "!" in `img` - the mark over furniture a ghost hides in: a white bar with a white dot under it
    (GHOST_ALERT_*), whatever the camera's distance, left to right."""
    top, bottom = cfg.GHOST_ALERT_BAND
    level = cfg.GHOST_ALERT_WHITE
    white = cv2.inRange(img[top:bottom], (level, level, level), (255, 255, 255))
    count, _, stats, _ = cv2.connectedComponentsWithStats(white, connectivity=8)
    pieces = [tuple(int(v) for v in stats[i]) for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] >= cfg.GHOST_ALERT_MIN_PIECE]
    found = []
    for x, y, w, h, area in pieces:
        if h < cfg.GHOST_ALERT_ASPECT * w or h < cfg.GHOST_ALERT_MIN_HEIGHT or area < cfg.GHOST_ALERT_BAR_FILL * w * h:
            continue                                                                   # not a bar
        for x2, y2, w2, h2, area2 in pieces:
            if (y + h < y2 <= y + cfg.GHOST_ALERT_DOT_GAP * h and abs((x2 + w2 / 2) - (x + w / 2)) <= cfg.GHOST_ALERT_DOT_SHIFT * w + cfg.GHOST_ALERT_DOT_SLACK
                    and cfg.GHOST_ALERT_DOT_WIDTH[0] * w <= w2 <= cfg.GHOST_ALERT_DOT_WIDTH[1] * w
                    and h2 <= cfg.GHOST_ALERT_DOT_ASPECT * w2 + 2 and area2 >= cfg.GHOST_ALERT_DOT_FILL * w2 * h2):                    # a dot under it
                found.append((x + w // 2, top + y + h // 2, h))
                break
    return sorted(found)

def ghost_boss_bar_visible(img: Image) -> bool:
    """Whether the boss's health bar (a gold frame along the top, GHOST_BOSS_BAR_*) is on screen: the boss phase."""
    left, top, right, bottom = cfg.GHOST_BOSS_BAR_BOX
    low, high = cfg.GHOST_BOSS_BAR_GOLD
    gold = cv2.inRange(cv2.cvtColor(img[top:bottom, left:right], cv2.COLOR_BGR2HSV), low, high)
    return int(np.count_nonzero(gold)) >= cfg.GHOST_BOSS_BAR_MIN

def ghost_boss_shielded(img: Image) -> bool:
    """Whether the boss, its bar being up, can't be hurt now: the bar shows two or three ghost icons instead of its
    cyan health (no GHOST_BOSS_FILL pixels), the boss itself is dim and small ghosts fly in - they are what is
    to be caught then. (The bar's health is down to a sliver at the very end, but not nothing.)"""
    left, top, right, bottom = cfg.GHOST_BOSS_BAR_BOX
    low, high = cfg.GHOST_BOSS_FILL
    fill = cv2.inRange(cv2.cvtColor(img[top:bottom, left:right], cv2.COLOR_BGR2HSV), low, high)
    return int(np.count_nonzero(fill)) < cfg.GHOST_BOSS_FILL_MIN

def choose_ghost_target(blobs: list[tuple[int, int, int]], boss: bool, previous: Optional[Point]) -> Optional[Point]:
    """Where to aim, from `blobs` (find_ghost_blobs()): when the boss can be hurt (`boss`) the biggest one (the boss), else the
    one that is still the one aimed at (`previous`, if a ghost is within GHOST_STICK px of it - they move, and aiming at
    another each look would lose them all), else the one nearest the character (GHOST_CHARACTER). None if there are none."""
    if not blobs:
        return None
    if boss:
        return blobs[0][:2]
    if previous is not None:
        near = min(blobs, key=lambda b: (b[0] - previous[0]) ** 2 + (b[1] - previous[1]) ** 2)
        if (near[0] - previous[0]) ** 2 + (near[1] - previous[1]) ** 2 <= cfg.GHOST_STICK ** 2:
            return near[:2]
    cx, cy = cfg.GHOST_CHARACTER
    return min(blobs, key=lambda b: (b[0] - cx) ** 2 + (b[1] - cy) ** 2)[:2]

def start_camera_drag() -> None:
    """Move the mouse to the middle of the screen and press the right button there. The drag that follows moves the cursor by
    hundreds of px, and a right button released over the window's border or title bar opens its menu and takes the focus from
    the game - which is where the cursor was left by whatever had been aimed at before."""
    pydirectinput.moveTo(*to_screen(cfg.REFERENCE_CENTER_X, cfg.REFERENCE_CENTER_Y))
    mouse_down("right")

def tilt_camera_down() -> None:
    """Drag the mouse down with the right button held, GHOST_START_TILT px: the camera rises behind the character, for a view
    from above, over GHOST_CAMERA_DRAG_DURATION seconds in GHOST_CAMERA_DRAG_STEPS moves."""
    start_camera_drag()
    try:
        for _ in range(cfg.GHOST_CAMERA_DRAG_STEPS):
            pydirectinput.moveRel(0, cfg.GHOST_START_TILT // cfg.GHOST_CAMERA_DRAG_STEPS, relative=True)
            wait_interruptible(cfg.GHOST_CAMERA_DRAG_DURATION / cfg.GHOST_CAMERA_DRAG_STEPS)
    finally:
        pydirectinput.mouseUp(button="right")

def far_from_character(box: tuple[int, int, int, int], minimum: float) -> Point:
    """A random point in `box` at least `minimum` px from the character (GHOST_CHARACTER); the middle of the farthest corner if the box has none."""
    left, top, right, bottom = box
    cx, cy = cfg.GHOST_CHARACTER
    for _ in range(50):
        x, y = random.randint(left, right), random.randint(top, bottom)
        if (x - cx) ** 2 + (y - cy) ** 2 >= minimum ** 2:
            return x, y
    return max(((left, top), (right, top), (left, bottom), (right, bottom)), key=lambda c: (c[0] - cx) ** 2 + (c[1] - cy) ** 2)

def ghost_chase_key(target: Point) -> Optional[str]:
    """The key to walk towards `target` with: W if it is above the character (GHOST_CHARACTER), S below, A/D left/right - along the
    axis it is farther off on - or None when it is no more than GHOST_TOWARD_MARGIN px off on either: near enough for the beam."""
    dx, dy = target[0] - cfg.GHOST_CHARACTER[0], target[1] - cfg.GHOST_CHARACTER[1]
    if max(abs(dx), abs(dy)) <= cfg.GHOST_TOWARD_MARGIN:
        return None
    if abs(dx) > abs(dy):
        return cfg.KEYS["right"] if dx > 0 else cfg.KEYS["left"]
    return cfg.KEYS["back"] if dy > 0 else cfg.KEYS["forward"]

def rotate_camera(drag: tuple[int, int] = cfg.GHOST_CAMERA_DRAG, tilt: int = cfg.GHOST_CAMERA_TILT, direction: Optional[int] = None) -> None:
    """Turn the camera: hold the right mouse button and drag the mouse sideways (`direction` -1 or 1, else a random way; `drag` = least and most px, and
    up to `tilt` px up or down too) over GHOST_CAMERA_DRAG_DURATION seconds, in GHOST_CAMERA_DRAG_STEPS moves, as Roblox wants a drag."""
    dx = (direction or random.choice((-1, 1))) * random.randint(*drag)
    dy = random.randint(-tilt, tilt)
    start_camera_drag()
    try:
        for _ in range(cfg.GHOST_CAMERA_DRAG_STEPS):
            pydirectinput.moveRel(dx // cfg.GHOST_CAMERA_DRAG_STEPS, dy // cfg.GHOST_CAMERA_DRAG_STEPS, relative=True)
            wait_interruptible(cfg.GHOST_CAMERA_DRAG_DURATION / cfg.GHOST_CAMERA_DRAG_STEPS)
    finally:
        pydirectinput.mouseUp(button="right")

def furniture_outlined(before: Image, after: Image, point: Point) -> bool:
    """Whether the mouse, moved to `point` between the screenshots `before` and `after`, is over a piece of furniture the beam can
    be held on, which is highlighted (a pale outline) near the mouse: at least GHOST_OUTLINE_MIN pixels within GHOST_OUTLINE_RADIUS px of `point`
    have turned pale (every channel at least GHOST_OUTLINE_WHITE) that weren't, or weren't beside a pale pixel, before."""
    x, y = point
    r = cfg.GHOST_OUTLINE_RADIUS
    top, left = max(0, y - r), max(0, x - r)
    was = (before[top:y + r, left:x + r].min(axis=2) >= cfg.GHOST_OUTLINE_WHITE).astype(np.uint8)
    now = after[top:y + r, left:x + r].min(axis=2) >= cfg.GHOST_OUTLINE_WHITE
    new = now & (cv2.dilate(was, np.ones((5, 5), np.uint8)) == 0)
    return int(np.count_nonzero(new)) >= cfg.GHOST_OUTLINE_MIN

def ring_point(centre: Point, box: tuple[int, int, int, int], radii: tuple[int, int]) -> Point:
    """A random point GHOST_RING-ish px (`radii`, nearest and farthest) from `centre`, pulled into `box`."""
    angle, radius = random.uniform(0, 2 * np.pi), random.uniform(*radii)
    left, top, right, bottom = box
    return (min(max(int(centre[0] + radius * np.cos(angle)), left), right), min(max(int(centre[1] + radius * np.sin(angle)), top), bottom))

def find_furniture(watch: MinigameWatch, around: Optional[Point] = None, avoid: Sequence[Point] = ()) -> Point:
    """Somewhere the beam can be held on furniture: the mouse is moved to up to GHOST_FURNITURE_PROBES places - random ones in
    GHOST_FURNITURE_BOX far from the character (far_from_character()), or, with `around`, ones in a ring of GHOST_RING px around that point
    (a ghost that is hiding in furniture) - none within GHOST_BAD_SPOT_RADIUS px of any of `avoid`, a look at each (furniture_outlined()),
    and the first one that gets the white outline is it; none did, the last place tried."""
    def place() -> Point:
        point = (0, 0)
        for _ in range(50):
            point = (ring_point(around, cfg.GHOST_FURNITURE_BOX, cfg.GHOST_RING) if around is not None
                     else far_from_character(cfg.GHOST_FURNITURE_BOX, cfg.GHOST_FURNITURE_MIN_DISTANCE))
            if all((point[0] - x) ** 2 + (point[1] - y) ** 2 >= cfg.GHOST_BAD_SPOT_RADIUS ** 2 for x, y in avoid):
                break
        return point
    point = place()
    before = minigame_look(watch)
    for probe in range(cfg.GHOST_FURNITURE_PROBES):
        if probe:
            point = place()
        pydirectinput.moveTo(*to_screen(*point))
        wait_interruptible(cfg.GHOST_OUTLINE_WAIT)
        after = minigame_look(watch)
        if furniture_outlined(before, after, point):
            return point
        before = after
    return point

class GhostFeedback:
    """What the score says about where the beam is held, fed one screenshot at a time (look()): the digits of the score pill, as a mask of
    their white pixels, differ from the last look's when the score changed - which means the beam is on a ghost. Nothing is concluded
    while the banner isn't on screen (`visible`). See docs/ARCHITECTURE.md#playing-ghost-gallery."""

    def __init__(self, now: float) -> None:
        self.digits: Optional[np.ndarray] = None            # the white pixels of the score at the last look; None if the banner wasn't there
        self.visible = False
        self.scored_at = now                                # the last time the score changed (or the start)
        self.spot_since = now                               # since when the beam has been on what it is on
        self.bad: list[tuple[Point, float]] = []            # places that scored nothing, and until when to keep away from them
        self.skipped: list[list[float]] = []                # [x, y, until]: blobs the score says are no ghosts, where they were at the last look
        self.hide_tries = 0                                 # furniture tried around the blob the beam is on, without a score

    def look(self, img: Image, now: float) -> bool:
        """Take in a screenshot. True if the score changed since the look before."""
        left, top, right, bottom = cfg.GHOST_SCORE_BOX
        box = img[top:bottom, left:right]
        pill = cv2.inRange(cv2.cvtColor(box, cv2.COLOR_BGR2HSV), cfg.GHOST_SCORE_PILL[0], cfg.GHOST_SCORE_PILL[1])
        self.visible = int(np.count_nonzero(pill)) >= cfg.GHOST_SCORE_PILL_MIN
        if not self.visible:
            self.digits = None
            return False
        digits = box.min(axis=2) >= cfg.GHOST_SCORE_WHITE
        changed = self.digits is not None and int(np.count_nonzero(digits ^ self.digits)) >= cfg.GHOST_SCORE_CHANGE
        self.digits = digits
        if changed:
            self.scored_at = self.spot_since = now
            self.hide_tries = 0
        return changed

    def new_spot(self, now: float, new_target: bool = False) -> None:
        """The beam goes somewhere else: it has a trial of its own (and with another blob - `new_target` - the furniture tried is counted afresh)."""
        self.spot_since = now
        if new_target:
            self.hide_tries = 0

    def skip(self, point: Point, now: float) -> None:
        """What is at `point` is no ghost: left alone for GHOST_SKIP_TIME seconds, wherever it goes."""
        self.skipped.append([float(point[0]), float(point[1]), now + cfg.GHOST_SKIP_TIME])
        self.hide_tries = 0

    def follow_skipped(self, blobs: list[tuple[int, int, int]], now: float) -> list[tuple[int, int, int]]:
        """`blobs` without the skipped ones; each skipped one moves to the blob it has become (the nearest within GHOST_STICK px), so that a pet that
        flies about stays skipped, and is dropped when its time is up."""
        self.skipped = [entry for entry in self.skipped if entry[2] > now]
        kept = []
        for blob in blobs:
            for entry in self.skipped:
                reach = max(cfg.GHOST_SKIP_RADIUS, float(blob[2]) ** 0.5)
                if (blob[0] - entry[0]) ** 2 + (blob[1] - entry[1]) ** 2 <= reach ** 2:
                    entry[0], entry[1] = float(blob[0]), float(blob[1])
                    break
            else:
                kept.append(blob)
        return kept

    def trial_over(self, now: float, trial: Optional[float] = None) -> bool:
        """The beam has been where it is for GHOST_SPOT_TRIAL seconds (or `trial`) without a score."""
        return self.visible and now - max(self.scored_at, self.spot_since) >= (cfg.GHOST_SPOT_TRIAL if trial is None else trial)

    def stalled(self, now: float) -> bool:
        """No score for GHOST_STALL seconds."""
        return self.visible and now - self.scored_at >= cfg.GHOST_STALL

    def mark_bad(self, point: Point, now: float) -> None:
        self.bad.append((point, now + cfg.GHOST_BAD_SPOT_TIME))

    def avoid(self, now: float) -> list[Point]:
        """The places to keep away from for now."""
        self.bad = [(point, until) for point, until in self.bad if until > now]
        return [point for point, _ in self.bad]

def play_ghost_gallery(deadline: float, watch: MinigameWatch) -> bool:
    """Ghost Gallery: once its overlay shows (or the start wait is over), look at the screen every GHOST_LOOK_INTERVAL,
    aim the mouse at a ghost (choose_ghost_target()) and hold the button down on it, until the minigame finishes or the
    overlay says it's over. It begins by zooming the camera out and tilting it down for a better view. In the boss phase it is the boss, but while the boss's bar shows ghost icons it can't be hurt
    (ghost_boss_shielded()) and the small ghosts are aimed at instead. With no ghost in sight, before the boss phase the beam
    is held on random furniture (ghosts hide there), and the camera is turned every GHOST_CAMERA_PERIOD to find some;
    it jumps now and then, and walks about a step at a time. The score is watched (GhostFeedback): a ghost the beam scores nothing on is
    taken to hide, and the furniture around it is tried; no score for GHOST_STALL seconds moves the player and turns the camera.
    True if the minigame finished before `deadline`. See docs/ARCHITECTURE.md#playing-ghost-gallery.
    """
    wait_for_minigame_start(watch, "ghost_gallery")
    minigame_wait(cfg.GHOST_INTRO_WAIT, watch)
    zoom_out()
    tilt_camera_down()
    hold_key(cfg.KEYS["forward"], cfg.GHOST_START_FORWARD)           # into the middle of the hall, from where the game puts the player
    target: Optional[Point] = None
    held = False
    moving: Optional[str] = None                     # the key being held to walk, and until when
    regrab_at = time.time() + cfg.GHOST_REGRAB_PERIOD   # when the beam is let go of and pressed again
    chasing: Optional[str] = None                    # the key held to get to what is aimed at (a `moving` that has no end)
    turn: Optional[int] = None                       # the way the camera is being turned to find the boss (-1 or 1)
    move_until = 0.0
    next_move = time.time() + cfg.GHOST_MOVE_PERIOD
    phase = "ghosts"
    furniture: Optional[Point] = None
    furniture_until = 0.0
    feedback = GhostFeedback(time.time())            # what the score says about where the beam is
    hide_spot: Optional[Point] = None                # furniture near a ghost the score says is hiding, which the beam is held on until `hide_until`
    hide_until = 0.0
    last_target: Optional[Point] = None
    last_turn = last_seen = time.time()
    next_jump = time.time() + random.uniform(cfg.GHOST_JUMP_MIN, cfg.GHOST_JUMP_MAX)

    def aim(point: Point) -> None:
        nonlocal held
        pydirectinput.moveTo(*to_screen(*point))
        if not held:
            mouse_down()
            held = True

    def let_go() -> None:
        nonlocal held
        if held:
            pydirectinput.mouseUp()
            held = False

    def stop_walking() -> None:
        nonlocal moving
        if moving is not None:
            key_up(moving)
            moving = None

    try:
        while time.time() < deadline and not watch.problem:
            img = minigame_look(watch)
            if detect_minigame_finished(img):
                return True
            if feedback.look(img, time.time()):
                hide_spot, hide_until = None, 0.0                           # it scored: whatever the beam is on is right
            bar = ghost_boss_bar_visible(img)
            if held and time.time() >= regrab_at:                           # knocked down, the player lets go of the beam and has to press again: do both now and then
                let_go()
                regrab_at = time.time() + cfg.GHOST_REGRAB_PERIOD
            now_phase = ("shielded" if ghost_boss_shielded(img) else "boss") if bar else "ghosts"
            if now_phase != phase:
                logger.debug(f"Ghost Gallery: {dict(ghosts='the boss phase is over', boss='the boss can be hurt', shielded='the boss is shielded: after the small ghosts')[now_phase]}")
                phase = now_phase
            blobs = find_ghost_blobs(img, bar)
            if phase == "shielded":                                         # only the small ghosts count: what is bigger is the beam's glow or the boss's fog
                blobs = [blob for blob in blobs if blob[2] <= cfg.GHOST_SMALL_MAX]
            if phase == "ghosts":
                alerts = find_ghost_alerts(img)                             # a "!" is a ghost hiding in the furniture below it: that comes first
                if alerts:
                    blobs = [(x, y + int(cfg.GHOST_ALERT_BELOW * height), cfg.GHOST_MIN_AREA) for x, y, height in alerts]
            blobs = feedback.follow_skipped(blobs, time.time())
            target = choose_ghost_target(blobs, phase == "boss" and bool(blobs) and blobs[0][2] >= cfg.GHOST_BOSS_MIN, target)   # (the boss is a big one, else it's the ghosts that are there)
            now = time.time()
            if target is not None and (last_target is None or (target[0] - last_target[0]) ** 2 + (target[1] - last_target[1]) ** 2 > cfg.GHOST_STICK ** 2):
                feedback.new_spot(now, True)                                # another ghost: it has a trial of its own
            last_target = target
            if phase == "ghosts" and feedback.stalled(now):                 # nothing has scored for a long time (a view blocked by a pillar, a beam on the floor): somewhere else, looking another way
                logger.debug(f"Ghost Gallery: no score for {cfg.GHOST_STALL:g}s - moving and turning the camera")
                mark_run_capture(f"Ghost Gallery: no score for {cfg.GHOST_STALL:g}s")
                let_go()
                stop_walking()
                chasing = None
                hold_key(random.choice(cfg.MOVE_KEYS), cfg.GHOST_STALL_WALK)
                pydirectinput.press(cfg.KEYS["jump"])
                rotate_camera(cfg.GHOST_STALL_TURN, 0, random.choice((-1, 1)))
                feedback.scored_at = last_turn = time.time()
                feedback.new_spot(last_turn)
                furniture, hide_spot, hide_until = None, None, 0.0
                now = time.time()
            elif phase == "ghosts" and target is not None and feedback.trial_over(now):   # a ghost the beam on it doesn't score: it is hiding in furniture, which is somewhere around it
                mark_run_capture("Ghost Gallery: the beam on a ghost scores nothing")
                if hide_spot is not None:
                    feedback.mark_bad(hide_spot, now)                       # (the furniture tried last scored nothing either)
                    feedback.hide_tries += 1
                if feedback.hide_tries >= cfg.GHOST_HIDE_TRIES:             # the blob and the furniture around it score nothing: no ghost (the pet)
                    feedback.skip(target, now)
                    hide_spot, hide_until, target, last_target = None, 0.0, None, None
                    logger.debug("Ghost Gallery: what the beam is on scores nothing, furniture or not - left alone")
                else:
                    hide_spot = find_furniture(watch, around=target, avoid=feedback.avoid(now))
                    hide_until = time.time() + cfg.GHOST_SPOT_TRIAL
                    feedback.new_spot(time.time())
                now = time.time()
            elif phase == "boss" and target is not None and feedback.trial_over(now, cfg.GHOST_BOSS_TRIAL):
                mark_run_capture("Ghost Gallery: the beam on what is taken for the boss scores nothing")
                feedback.skip(target, now)                                  # fog, a light shaft: not the boss
                logger.debug("Ghost Gallery: the biggest blob scores nothing - not the boss, left alone")
                target, last_target = None, None
                now = time.time()
            if target is not None:
                last_seen = now
                aim(hide_spot if hide_spot is not None and now < hide_until else target)
            elif phase == "ghosts":                                         # nothing to be seen before the boss: try the furniture
                if furniture is None or now >= furniture_until:
                    furniture = find_furniture(watch)                       # (the mouse is moved around with the beam down)
                    furniture_until = time.time() + cfg.GHOST_FURNITURE_PERIOD
                aim(furniture)
            if target is not None:
                turn = None                                                 # the next search for the boss turns a way of its own
            elif phase != "ghosts":                                         # the boss, or the ghosts that shield it, out of sight: turn the camera until they are
                turn = turn or random.choice((-1, 1))
                rotate_camera(cfg.GHOST_BOSS_TURN_DRAG, 0, turn)
                last_turn = time.time()
            elif now - max(last_turn, last_seen) >= cfg.GHOST_CAMERA_PERIOD:
                turn = turn or random.choice((-1, 1))                       # one way round, not back and forth: a sweep sees the whole hall
                rotate_camera(direction=turn)
                last_turn = time.time()
            chase = ghost_chase_key(target) if target is not None else None   # always towards the ghost or the boss, until it is near
            if chase != chasing:
                stop_walking()
                chasing = chase
                if chase is not None:
                    moving = chase
                    key_down(chase)
                move_until = now
            if chasing is None and target is None and phase == "ghosts":     # nothing in sight: walking about, a step at a time, without stopping the aiming
                if moving is not None and now >= move_until:
                    stop_walking()
                    next_move = now + cfg.GHOST_MOVE_PERIOD
                elif moving is None and now >= next_move:
                    moving = random.choice(cfg.MOVE_KEYS)
                    key_down(moving)
                    move_until = time.time() + random.uniform(cfg.GHOST_MOVE_STEP_MIN, cfg.GHOST_MOVE_STEP_MAX)      # (`now` is from before a search for furniture, which can take longer than a step)
            elif chasing is None:
                stop_walking()
            if now >= next_jump:
                pydirectinput.press(cfg.KEYS["jump"])
                next_jump = now + random.uniform(cfg.GHOST_JUMP_MIN, cfg.GHOST_JUMP_MAX)
            wait_interruptible(cfg.GHOST_LOOK_INTERVAL)
    finally:
        let_go()
        stop_walking()
    return False

def stitches_ring_angle(img: Image) -> Optional[float]:
    """The slope, in degrees, of the gold line along the ring walkway's inner edge in `img` (Stitches' Spotlight): 0 when it runs straight across the screen,
    which it does when the camera looks at the middle of the ring; positive when it falls to the right (the middle is to the right), negative when it
    rises. None when too little of it shows (STITCHES_LINE_MIN_PIXELS: it is not seen in the red of a searchlight, or with the camera turned from the ring)."""
    left, top, right, bottom = cfg.STITCHES_LINE_BOX
    hsv = cv2.cvtColor(img[top:bottom, left:right], cv2.COLOR_BGR2HSV)
    hue, saturation, value = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    mask = ((hue >= cfg.STITCHES_LINE_HUE[0]) & (hue <= cfg.STITCHES_LINE_HUE[1]) & (saturation >= cfg.STITCHES_LINE_MIN_SATURATION)
            & (value >= cfg.STITCHES_LINE_VALUE[0]) & (value <= cfg.STITCHES_LINE_VALUE[1]))
    ys, xs = np.nonzero(mask)
    if len(xs) < cfg.STITCHES_LINE_MIN_PIXELS:
        return None
    dx, dy = cv2.fitLine(np.column_stack([xs, ys]).astype(np.float32), cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()[:2]
    angle = math.degrees(math.atan2(dy, dx))
    return angle - 180 if angle > 90 else angle + 180 if angle < -90 else angle

class RingCamera:
    """What turns the camera towards the middle of Stitches' Spotlight's ring, from the angle of the ring's edge line (stitches_ring_angle()). Walking
    round the ring needs the camera turned all the time, at a steady rate, so besides the turn the angle asks for (proportional: up to STITCHES_TURN_HOLD_MAX
    seconds of key for a line 90 degrees off) it learns that rate (`bias`: the share of the time the turn key is held, grown by STITCHES_TURN_BIAS_GAIN
    per degree per second while the line is off by more than STITCHES_LINE_DEADBAND) and keeps turning at it while the line is out of sight, and after
    STITCHES_LOST_AFTER seconds without it sweeps the camera round to find it. `keys` are the (left, right) arrow keys; swap_keys() when a test turn
    shows they work the other way."""

    def __init__(self) -> None:
        self.keys = (cfg.KEYS["camera_left"], cfg.KEYS["camera_right"])
        self.bias = 0.0
        self.lost_since: Optional[float] = None

    def swap_keys(self) -> None:
        self.keys = (self.keys[1], self.keys[0])
        self.bias = -self.bias

    def decide(self, angle: Optional[float], now: float, interval: float) -> Optional[tuple[str, float]]:
        """(key, seconds to hold it) for this look, or None. `angle` is None when the line is not in sight."""
        if angle is None:
            if self.lost_since is None:
                self.lost_since = now
            seconds = cfg.STITCHES_TURN_HOLD_MAX if now - self.lost_since > cfg.STITCHES_LOST_AFTER else self.bias * interval
        else:
            self.lost_since = None
            error = angle if abs(angle) > cfg.STITCHES_LINE_DEADBAND else 0.0
            self.bias = max(-cfg.STITCHES_TURN_BIAS_MAX, min(cfg.STITCHES_TURN_BIAS_MAX, self.bias + cfg.STITCHES_TURN_BIAS_GAIN * error * interval))
            seconds = cfg.STITCHES_TURN_HOLD_MAX * max(-90.0, min(90.0, error)) / 90.0 + self.bias * interval
        seconds = max(-cfg.STITCHES_TURN_HOLD_MAX, min(cfg.STITCHES_TURN_HOLD_MAX, seconds))
        if abs(seconds) < cfg.STITCHES_TURN_MIN_HOLD:
            return None
        return self.keys[seconds > 0], abs(seconds)

def calibrate_ring_camera(camera: RingCamera, watch: MinigameWatch) -> None:
    """Find out which arrow key turns the camera which way, by turning with the right one for STITCHES_CALIBRATE_HOLD seconds and seeing the ring's line
    move: turning right must make a line that falls to the right (positive angle) level. If it moved the other way the camera's keys are swapped. Too small a
    move, or no line in sight before or after, is logged and the keys are left as they are."""
    before = stitches_ring_angle(minigame_look(watch))
    if before is None:
        logger.debug("Stitches' Spotlight: no ring line in sight to test the camera keys against")
        return
    hold_key(camera.keys[1], cfg.STITCHES_CALIBRATE_HOLD)
    minigame_wait(cfg.STITCHES_CALIBRATE_HOLD, watch)
    after = stitches_ring_angle(minigame_look(watch))
    if after is None or abs(after - before) < cfg.STITCHES_CALIBRATE_MIN:
        logger.debug(f"Stitches' Spotlight: the camera keys test showed nothing clear (line {before:.0f} -> {'not seen' if after is None else f'{after:.0f}'} degrees)")
        mark_run_capture("Stitches' Spotlight: the camera keys test was unclear")
        return
    if after > before:
        camera.swap_keys()
    logger.debug(f"Stitches' Spotlight: line {before:.0f} -> {after:.0f} degrees, the camera keys are {camera.keys}")

def play_stitches_spotlight(deadline: float, watch: MinigameWatch) -> bool:
    """Stitches' Spotlight: once its overlay shows, wait (at most STITCHES_INTRO_MAX seconds) for the ring's edge line - the game's intro camera looks at
    the sky first - test which way the arrow keys turn the camera (calibrate_ring_camera()), then walk round the ring by holding KEYS["right"] and, every
    STITCHES_CENTER_INTERVAL, turn the camera (RingCamera) until the line runs straight across the screen, i.e. the camera looks at the middle. KEYS["speed_boost"]
    is pressed every STITCHES_BOOST_INTERVAL. Nothing is read or avoided: the candy corn on the way is picked up by walking over it.
    True if the minigame finished before `deadline`. See docs/ARCHITECTURE.md#playing-stitches-spotlight."""
    wait_for_minigame_start(watch, "stitches_spotlight")
    intro_until = time.time() + cfg.STITCHES_INTRO_MAX
    while time.time() < intro_until and not watch.problem and stitches_ring_angle(minigame_look(watch)) is None:
        wait_interruptible(cfg.STITCHES_CENTER_INTERVAL)
    camera = RingCamera()
    calibrate_ring_camera(camera, watch)
    next_boost = time.time()
    key_down(cfg.KEYS["right"])
    try:
        while time.time() < deadline and not watch.problem:
            img = minigame_look(watch)
            if detect_minigame_finished(img):
                return True
            angle = stitches_ring_angle(img)
            turn = camera.decide(angle, time.time(), cfg.STITCHES_CENTER_INTERVAL)
            logger.debug(f"Stitches' Spotlight: ring line {'not seen' if angle is None else f'{angle:.0f} degrees'}, turn {turn if turn else 'none'}, bias {camera.bias:+.2f}")
            if turn is not None:
                hold_key(*turn)
            if time.time() >= next_boost:
                pydirectinput.press(cfg.KEYS["speed_boost"])
                next_boost = time.time() + cfg.STITCHES_BOOST_INTERVAL
            wait_interruptible(cfg.STITCHES_CENTER_INTERVAL)
    finally:
        key_up(cfg.KEYS["right"])
    return False

MINIGAME_PLAYERS = {"hauntlet_2": play_hauntlet_2, "ghost_gallery": play_ghost_gallery, "stitches_spotlight": play_stitches_spotlight}

def click_nice_until_gone() -> bool:
    """Click the finish screen's NICE! until it is gone (up to MINIGAME_FINISH_CLICK_TRIES).
    Returns True if it is. See docs/ARCHITECTURE.md#need-handlers.
    """
    for attempt in range(cfg.MINIGAME_FINISH_CLICK_TRIES):
        hover_click(*cfg.MINIGAME_FINISH_BUTTON_POS)
        wait_interruptible(cfg.MINIGAME_FINISH_WAIT)
        if not detect_minigame_finished(grab_screen()):
            return True
        logger.debug(f"Finish screen still up after click {attempt + 1}")
    log_failure("minigame finish screen would not close")
    return False

def screen_changed(before: Image, after: Image) -> bool:
    """Whether the view is a different one: the mean grey-level difference of the two (shrunk) screens is over MINIGAME_TELEPORT_CHANGE."""
    small = [cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cfg.SCREEN_CHANGE_SIZE, interpolation=cv2.INTER_AREA).astype(np.int16) for img in (before, after)]
    return bool(np.abs(small[0] - small[1]).mean() > cfg.MINIGAME_TELEPORT_CHANGE)

def minigame_teleported(before: Image) -> bool:
    """Wait up to MINIGAME_TELEPORT_WAIT seconds for the character to be teleported after the popup's Yes: the white screen of a
    teleport (detect_white_screen()) or a view changed from `before` (the screen as it was at the click; screen_changed())."""
    end = time.time() + cfg.MINIGAME_TELEPORT_WAIT
    while time.time() < end:
        wait_interruptible(cfg.MINIGAME_TELEPORT_POLL)
        img = grab_screen()
        if detect_white_screen(img) or screen_changed(before, img):
            return True
    return False

def accept_minigame_popup() -> bool:
    """Click the popup's Yes and check that it teleported the character (minigame_teleported()). If not and the popup is still
    up, the click didn't take: click again, up to MINIGAME_YES_TRIES clicks in all. False if it never did, or the popup is gone
    without a teleport (it was gone before the click, or the click was lost). See docs/ARCHITECTURE.md#need-handlers."""
    for attempt in range(cfg.MINIGAME_YES_TRIES):
        before = grab_screen()
        hover_click(*cfg.MINIGAME_POPUP_YES_POS)
        if minigame_teleported(before):
            return True
        if not detect_minigame_popup(grab_screen()):
            logger.debug("The minigame popup is gone and nothing teleported")
            return False
        logger.debug(f"The popup is still up after click {attempt + 1}")
    return False

def candy_corn_digit_templates() -> dict[int, Image]:
    """{digit: picture} for the saved HUD digits (CANDY_CORN_DIGIT_DIR), re-read when the folder changes."""
    directory = cfg.CANDY_CORN_DIGIT_DIR
    return _digit_templates(directory, os.path.getmtime(directory)) if os.path.isdir(directory) else {}

def read_candy_corn(img: Image) -> Optional[int]:
    """The candy corn total in the HUD of `img`, or None when it isn't readable: no counter on screen (at home, in a minigame), or a
    digit that matches none of the saved ones. See docs/ARCHITECTURE.md#candy-corn."""
    left, top, right, bottom = cfg.CANDY_CORN_BOX
    mask = cv2.inRange(img[top:bottom, left:right], cfg.CANDY_CORN_RED[0], cfg.CANDY_CORN_RED[1])
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    tall = sorted((int(stats[i, 0]), int(stats[i, 1]), int(stats[i, 2]), int(stats[i, 3])) for i in range(1, count)
                  if cfg.CANDY_CORN_DIGIT_HEIGHT[0] <= stats[i, 3] <= cfg.CANDY_CORN_DIGIT_HEIGHT[1])
    boxes = [box for box in tall if box[2] <= cfg.CANDY_CORN_DIGIT_MAX_WIDTH]
    templates = candy_corn_digit_templates()
    if not 1 <= len(boxes) <= cfg.CANDY_CORN_MAX_DIGITS or len(boxes) != len(tall) or not templates:
        return None
    if any(not cfg.CANDY_CORN_PITCH[0] <= b[0] - a[0] <= cfg.CANDY_CORN_PITCH[1] for a, b in zip(boxes, boxes[1:])):
        return None                                                          # a digit is missing, or something is between them
    total = 0
    for x, y, w, h in boxes:
        picture = cv2.resize(mask[y:y + h, x:x + w], cfg.CANDY_CORN_DIGIT_SIZE, interpolation=cv2.INTER_AREA)
        diffs = {digit: float(np.abs(picture.astype(int) - template.astype(int)).mean()) for digit, template in templates.items()}
        digit = min(diffs, key=lambda d: diffs[d])
        if diffs[digit] > cfg.CANDY_CORN_MAX_DIFF:
            return None
        total = total * 10 + digit
    return total

def save_minigame_result(name: str) -> Optional[str]:
    """Save the GAME OVER! screen of the minigame `name` (the card with what the run earned, in candy corn, and its score) as
    RUNS_DIR/results/run<N>_<name>_<time>.png, the newest MAX_RESULT_SHOTS kept. The path, or None. Never raises."""
    try:
        folder = os.path.join(cfg.RUNS_DIR, "results")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"run{state.run_number}_{name}_{time.strftime('%H%M%S')}.png")
        left, top, right, bottom = cfg.MINIGAME_RESULT_BOX
        if not cv2.imwrite(path, grab_screen()[top:bottom, left:right]):
            return None
        keep_newest_files(folder, cfg.MAX_RESULT_SHOTS, ".png")
        return path
    except Exception as error:
        logger.warning(f"Couldn't save the result screen: {error}")
        return None

def play_minigame(name: str) -> bool:
    """Take the popup's Yes, play `name` until its GAME OVER! screen (which every run ends on: what a run earned is the candy corn
    on that screen, which is saved, see save_minigame_result()), click NICE!, respawn. Gives up (a logged failure) if the overlay shows it over or never
    started, or after MINIGAME_MAX_DURATION. Counts as progress only if it ran to the end. See docs/ARCHITECTURE.md#need-handlers.
    """
    label = cfg.MINIGAME_LABELS.get(name, name)
    logger.info(f"Playing {label}")
    log_run_event(f"minigame started: {label}")
    # The handler this interrupted may be holding a key or the mouse button
    # down (hold_key(), the pet swipe); that must not carry into the minigame.
    release_all_inputs()
    if not accept_minigame_popup():
        log_failure(f"{label} could not be started (the popup's Yes did not teleport the character)")
        state.last_detection = time.time()
        respawn_character()
        return False
    started = time.time()
    failures_before = run_stats.failures
    watch = MinigameWatch(name)
    state.minigame_detail = {}
    before = read_candy_corn_settled(cfg.CANDY_CORN_BEFORE_TRIES)      # in the lobby the total is on screen (after the white of the teleport): what the run earned is the difference
    capture = start_run_capture(name)
    outcome = "interrupted"
    finished = False
    after: Optional[int] = None
    send_macro_window_to_back()                 # it covers part of the game's screen, and would be in the recording and the readers' way
    try:
        finished = MINIGAME_PLAYERS[name](started + cfg.MINIGAME_MAX_DURATION, watch)
        outcome = "finished (the game over screen)" if finished else (watch.problem or "did not reach the game over screen in time")
        after = read_candy_corn_settled()        # (the game over screen has the total too, counting up for a moment)
    finally:
        release_all_inputs()
        bring_macro_window_to_front()
        result = minigame_result(name, outcome, finished, time.time() - started, before, after, run_stats.failures - failures_before)
        finish_run_capture(capture, outcome, result)
    record_minigame_result(result)
    if finished:
        log_run_event(f"minigame finished: {label} ({time.time() - started:.0f}s)")
        save_minigame_result(name)
        click_nice_until_gone()
    elif watch.disconnected:
        log_run_event(f"{label} ended by a disconnect")      # (the failure is the rejoin's to log)
        state.last_detection = time.time()
        return False
    else:
        reason = watch.problem or f"did not finish within {cfg.MINIGAME_MAX_DURATION:g}s"
        log_failure(f"{label} {reason}")
    state.last_detection = time.time()      # no needs could be seen while it ran
    if finished:
        state.last_progress = time.time()   # a failed one (never started, left early) must not reset the no-progress rejoin
    respawn_character()
    return finished

def close_minigame_finish_screen(abandon: bool = True) -> bool:
    """If the finish screen is up, click NICE!; with `abandon` also count it as progress and
    respawn. Returns True if it was there. See docs/ARCHITECTURE.md#need-handlers.
    """
    if not detect_minigame_finished(grab_screen()):
        return False
    logger.debug("Minigame finish screen is up, clicking NICE!...")
    log_run_event("minigame finish screen closed")
    click_nice_until_gone()
    if abandon:
        state.last_progress = state.last_detection = time.time()
        respawn_character()
    return True

def minigame_popup(play: bool = True) -> Optional[str]:
    """Halloween only: play a popup's minigame if it is switched on, else dismiss it.
    Returns "played", "dismissed", "waiting" (a playable popup is held off while a need is in its end wait, at most MINIGAME_POPUP_END_WAIT
    seconds) or None. See docs/ARCHITECTURE.md#need-handlers.
    """
    img = grab_screen()
    if not detect_minigame_popup(img):
        state.popup_held_since = None
        return None
    name = identify_minigame(img)
    if name is not None and name in MINIGAME_PLAYERS and load_game_config().get(f"{name}_enabled", False):    # (one with no player yet is declined, switched on or not)
        if play and state.end_wait_need is not None:
            # A need is in its end wait: give it the time to clear (and count as resolved) before the minigame abandons it.
            state.popup_held_since = state.popup_held_since or time.time()
            if time.time() - state.popup_held_since < cfg.MINIGAME_POPUP_END_WAIT:
                return "waiting"
        state.popup_held_since = None
        if play:
            logger.debug(f"Minigame popup detected: playing {name}")
            play_minigame(name)
            return "played"
        logger.debug(f"Minigame popup detected ({name}), declining it for now...")
        hover_click(*cfg.MINIGAME_POPUP_NO_POS)
        return "dismissed"
    logger.debug(f"Minigame popup detected ({name or 'unknown'}), dismissing...")
    log_run_event(f"minigame popup dismissed ({name or 'unknown'})")
    hover_click(*cfg.MINIGAME_POPUP_DONT_SHOW_POS)   # "Do not show again this session"
    hover_click(*cfg.MINIGAME_POPUP_NO_POS)
    return "dismissed"

def unscrew(in_task: bool = False, play_minigames: bool = True) -> Optional[str]:
    """The per-cycle checks (at the top of every cycle; with `in_task` also during a task):
    rejoin, backpack, stray windows, Halloween minigames, then the paycheck popup - in
    that order, since the green buttons of the earlier ones look like CASH OUT. Returns
    "played", "rejoined" or "aborted" (the callers abandon the task) or None. See docs/ARCHITECTURE.md#need-handlers.
    """
    if in_task:
        if rejoin_game(kinds=("disconnect", "white_screen")):
            return "rejoined"
    else:
        rejoin_game()
    # Setup (play_minigames False) keeps the backpack open for ~8s on purpose - longer than the two checks that
    # count as stuck - and closes it itself at the end, so the check leaves it alone there.
    backpack_open = False if (in_task and not play_minigames) else close_backpack_if_stuck(in_task)
    closed = dismiss_stray_windows()
    if cfg.HALLOWEEN and close_minigame_finish_screen(abandon=play_minigames):
        if play_minigames:
            return "played"    # back in the world somewhere unknown: the task is abandoned like after a minigame
    played = minigame_popup(play=play_minigames) if cfg.HALLOWEEN else None
    if played == "waiting":
        return None                    # the popup is up for a few seconds more (its green Yes would be taken for a paycheck's button)
    paycheck = detect_paycheck() if not backpack_open else False
    if in_task and play_minigames and played != "played" and (paycheck or (closed and cfg.STRAY_WINDOWS[closed].get("abort_task"))):
        return "aborted"   # (not setup, which can't be abandoned halfway - that is `play_minigames` False)
    return played

def run_side_action(action: Callable[[], bool]) -> bool:
    """Run lure_collect() / tree_collect() with the in-task checks on. False if a minigame
    or rejoin took over. See docs/ARCHITECTURE.md#side-actions.
    """
    try:
        with task_unscrew():
            return action()
    except TaskInterrupted as interruption:
        logger.debug(f"{action.__name__} interrupted by {interruption.reason}")
        log_run_event(f"{action.__name__} interrupted by {interruption.reason}")
        return False

def last_money_reset_time(now: float) -> float:
    """The most recent daily reset moment (MONEY_RESET_HOUR o'clock, local
    time) at or before `now`, as a timestamp."""
    moment = datetime.datetime.fromtimestamp(now)
    boundary = moment.replace(hour=cfg.MONEY_RESET_HOUR, minute=0, second=0, microsecond=0)
    if boundary > moment:
        boundary -= datetime.timedelta(days=1)
    return boundary.timestamp()

def reset_money_if_due(config: dict[str, Any]) -> dict[str, Any]:
    """Reset the tree's money total at each MONEY_RESET_HOUR (first sighting only recorded).
    Returns the config. See docs/ARCHITECTURE.md#persisted-game-config.
    """
    boundary = last_money_reset_time(time.time())
    recorded = config["money_reset_boundary"]
    if recorded is not None and recorded >= boundary:
        return config
    if recorded is None:
        return update_game_config(lambda c: c.update(money_reset_boundary=boundary))
    logger.info(f"Money total reset (daily at {cfg.MONEY_RESET_HOUR}:00): {config['money_collected']} -> 0")
    log_run_event(f"money total reset: {config['money_collected']} -> 0")
    return update_game_config(lambda c: c.update(money_collected=0, money_reset_boundary=boundary))

def side_quest() -> None:
    """Once per cycle after unscrew(), unless side_quest_enabled is off: tend the money tree and
    the lure from the persisted state, saving each success at once.
    See docs/ARCHITECTURE.md#side-actions.
    """
    config = reset_money_if_due(load_game_config())
    if not config["side_quest_enabled"]:
        return
    now = time.time()

    if config["money_collected"] < cfg.MONEY_COLLECTED_TARGET and now >= config["tree_timer"]:
        if run_side_action(tree_collect):
            def harvested(c):
                c["money_collected"] += cfg.TREE_HARVEST_YIELD
                c["tree_timer"] = time.time() + cfg.TREE_CHECK_INTERVAL
            update_game_config(harvested)

    if now >= config["lure_timer"]:
        if run_side_action(lure_collect):
            update_game_config(lambda c: c.update(lure_timer=time.time() + cfg.LURE_RECOLLECT_INTERVAL))

# ============================================================================
# RUN RECORDING
# ============================================================================

class RunRecorder:
    """Records the Roblox window to a low-quality, time-stamped video from a background thread
    (stops after RECORD_MAX_MINUTES). See docs/ARCHITECTURE.md#recording-a-run.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self.writer: Any = None
        self.thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()

    def start(self) -> bool:
        """Open the file and start recording. Returns False if no video file
        could be opened (nothing is recorded then)."""
        for extension, codec in ((".mp4", "mp4v"), (".avi", "MJPG")):
            path = os.path.splitext(self.path)[0] + extension
            writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*codec), cfg.RECORD_FPS, (cfg.RECORD_WIDTH, cfg.RECORD_HEIGHT))  # type: ignore[attr-defined]
            if writer.isOpened():
                self.path, self.writer = path, writer
                self.thread = threading.Thread(target=self._record, daemon=True)
                self.thread.start()
                return True
            writer.release()
        return False

    def _frame(self) -> Image:
        frame = cv2.resize(grab_screen(), (cfg.RECORD_WIDTH, cfg.RECORD_HEIGHT), interpolation=cv2.INTER_AREA)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        (text_w, text_h), baseline = cv2.getTextSize(stamp, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
        # White text on a solid black box: it stays readable over any scene (an outline smeared in the video
        # compression) - the corner of the screen it covers is given up.
        cv2.rectangle(frame, (0, 0), (text_w + 12, text_h + baseline + 12), (0, 0, 0), -1)
        cv2.putText(frame, stamp, (6, text_h + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
        return frame

    def _record(self) -> None:
        """Thread body: one frame per 1/RECORD_FPS seconds of real time (a
        slow grab is made up for by repeating the last frame, so the video
        runs at real speed)."""
        started = time.time()
        written = 0
        frame = None
        while not self.stop_event.is_set() and time.time() - started < cfg.RECORD_MAX_MINUTES * 60:
            try:
                frame = self._frame()
            except Exception:
                pass   # a failed grab repeats the last frame
            due = int((time.time() - started) * cfg.RECORD_FPS) + 1
            if frame is not None:
                for _ in range(max(1, due - written)):
                    self.writer.write(frame)
                    written += 1
            self.stop_event.wait(max(0.0, (written / cfg.RECORD_FPS) - (time.time() - started)) if frame is not None
                                 else 1.0 / cfg.RECORD_FPS)   # nothing to write yet: try again in a frame's time

    def stop(self) -> None:
        """Stop recording and close the file."""
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=5.0)
        if self.writer is not None:
            self.writer.release()
        size_mb = os.path.getsize(self.path) / (1024 * 1024) if os.path.exists(self.path) else 0
        log_run_event(f"recording stopped: {os.path.basename(self.path)} ({size_mb:.0f} MB)")
        logger.info(f"Recording saved: {self.path} ({size_mb:.0f} MB)")

def start_recording_if_requested() -> Optional[RunRecorder]:
    """If "Record the next run" is ticked, clear it, start a RunRecorder and return it
    (None if not ticked or no file could be opened). See docs/ARCHITECTURE.md#recording-a-run.
    """
    if not load_game_config()["record_next_run"]:
        return None
    update_game_config(lambda c: c.update(record_next_run=False))
    if state.macro_window is not None:
        state.macro_window.run_on_ui_thread(lambda: state.macro_window.config_flag_vars["record_next_run"].set(False), timeout=1.0)
    os.makedirs(cfg.RECORDINGS_DIR, exist_ok=True)
    videos = sorted((os.path.join(cfg.RECORDINGS_DIR, f) for f in os.listdir(cfg.RECORDINGS_DIR)), key=os.path.getmtime)
    for old in videos[:max(0, len(videos) - (cfg.MAX_RECORDINGS - 1))]:
        try:
            os.remove(old)
        except OSError:
            pass
    recorder = RunRecorder(os.path.join(cfg.RECORDINGS_DIR, f"run{state.run_number}_{time.strftime('%Y%m%d_%H%M%S')}"))
    if not recorder.start():
        logger.warning("Couldn't open a video file - not recording")
        log_run_event("recording: couldn't open a video file")
        return None
    log_run_event(f"recording started: {os.path.basename(recorder.path)}")
    logger.info(f"Recording this run to {recorder.path}")
    return recorder

# ============================================================================
# INPUT CAPTURE
# ============================================================================
# Press HOTKEYS["capture"] (any window) to start recording what is on screen and every key, mouse button and mouse
# movement; press it again to stop. For working out how something is played by hand (the Hauntlet 2 rooms, say):
# the frames are lossless PNGs and the events carry the same clock. See docs/ARCHITECTURE.md#input-capture.

# Windows virtual-key codes the capture looks at, by the name written in the events.
VK_NAMES: dict[int, str] = {
    0x01: "mouse_left", 0x02: "mouse_right", 0x04: "mouse_middle", 0x05: "mouse_x1", 0x06: "mouse_x2",
    0x08: "backspace", 0x09: "tab", 0x0D: "enter", 0x10: "shift", 0x11: "ctrl", 0x12: "alt", 0x14: "caps_lock",
    0x1B: "esc", 0x20: "space", 0x21: "page_up", 0x22: "page_down", 0x23: "end", 0x24: "home",
    0x25: "left", 0x26: "up", 0x27: "right", 0x28: "down", 0x2D: "insert", 0x2E: "delete",
    0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/", 0xC0: "`", 0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'",
    **{code: chr(code) for code in range(0x30, 0x3A)},
    **{code: chr(code).lower() for code in range(0x41, 0x5B)},
    **{0x70 + i: f"f{i + 1}" for i in range(12)},
}

def pressed_inputs() -> set[str]:
    """The names (VK_NAMES) of the keys and mouse buttons held down right now, whichever window has the focus;
    empty off Windows."""
    if os.name != "nt":
        return set()
    get_state = ctypes.windll.user32.GetAsyncKeyState  # type: ignore[attr-defined]  # Windows only
    return {name for code, name in VK_NAMES.items() if get_state(code) & 0x8000}

def cursor_position() -> Point:
    """The mouse position in screen pixels; (0, 0) off Windows."""
    if os.name != "nt":
        return 0, 0
    from ctypes import wintypes
    point = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))  # type: ignore[attr-defined]  # Windows only
    return point.x, point.y

def to_reference(x: float, y: float) -> Point:
    """The reverse of to_screen(): the reference-space position of a screen pixel (outside the Roblox window it
    comes out negative or past the reference size)."""
    left, top, width, height = roblox_rect()
    return int((x - left) * cfg.REFERENCE_WIDTH / width), int((y - top) * cfg.REFERENCE_HEIGHT / height)

class InputCapture:
    """Records into `folder`: frame_<n>_<seconds>.png (grab_screen(), lossless) when the screen has changed, looked at
    every CAPTURE_FRAME_INTERVAL and right after every mouse click, and events.jsonl, one JSON object per line on the same clock ("t", seconds since
    the start): key_down / key_up with the key, mouse_down / mouse_up with the button, move (rate limited), and a
    frame line for each screenshot. Mouse events carry the screen position (x, y) and the reference-space one
    (rx, ry). The capture hotkey itself is left out. It stops by itself after CAPTURE_MAX_MINUTES or when the disk
    runs low (`stopped_because` says why). With `max_zips` the capture is kept to as many zips (zip_capture(), the events
    included) however long it runs: when it would need more, every other saved frame is deleted (the newest stays) and
    frames are then saved twice as far apart, so what is left is spread evenly over the whole run. See docs/ARCHITECTURE.md#input-capture."""

    def __init__(self, folder: str, max_zips: Optional[int] = None) -> None:
        self.folder = folder
        self.max_zips = max_zips
        self.dropped = 0
        self._kept: list[tuple[str, int]] = []          # the frames on disk, oldest first, with their sizes
        self._gap = cfg.CAPTURE_FRAME_INTERVAL if max_zips else 0.0     # least seconds between two saved frames
        self.protected: set[str] = set()                # frames the thinning leaves alone: saved around a mark(), or in the tail of a run that went wrong
        self._burst_until = 0.0                         # until then every look saves a frame, however far apart the thinning has put them
        self.stop_event = threading.Event()
        self.shot_now = threading.Event()
        self.stopped_because: Optional[str] = None
        self.frames = 0
        self.skipped = 0
        self.events = 0
        self.started = 0.0
        self._lock = threading.Lock()
        self._file: Any = None
        self._threads: list[threading.Thread] = []

    @property
    def running(self) -> bool:
        return bool(self._threads) and not self.stop_event.is_set()

    def start(self) -> None:
        os.makedirs(self.folder, exist_ok=True)
        self.started = time.time()
        left, top, width, height = roblox_rect()
        with open(os.path.join(self.folder, "info.json"), "w", encoding="utf-8") as f:
            json.dump({"started": time.strftime("%Y-%m-%d %H:%M:%S"), "screen": list(pyautogui.size()),
                       "roblox_rect": [left, top, width, height], "reference": [cfg.REFERENCE_WIDTH, cfg.REFERENCE_HEIGHT],
                       "frame_interval": cfg.CAPTURE_FRAME_INTERVAL, "max_zips": self.max_zips}, f, indent=1)
        self._file = open(os.path.join(self.folder, "events.jsonl"), "w", encoding="utf-8")
        self._threads = [threading.Thread(target=self._poll, daemon=True), threading.Thread(target=self._shots, daemon=True)]
        for thread in self._threads:
            thread.start()

    def stop(self) -> str:
        """Stop, close the files and say what was recorded."""
        self.stop_event.set()
        self.shot_now.set()                             # (the frame thread is waiting out CAPTURE_FRAME_INTERVAL: let it see the stop)
        for thread in self._threads:
            thread.join(timeout=5.0)
        self._keep_within_budget()                      # (the events grew since the last frame was saved)
        with self._lock:
            if self._file is not None:
                self._file.close()
                self._file = None
        seconds = time.time() - self.started
        size_mb = sum(os.path.getsize(os.path.join(self.folder, f)) for f in os.listdir(self.folder)) / (1024 * 1024)
        summary = (f"{self.frames} frames ({self.skipped} unchanged ones skipped"
                   + (f", {self.dropped} thinned out to stay within {self.max_zips} zips" if self.dropped else "")
                   + f"), {self.events} events, {seconds:.0f}s, {size_mb:.0f} MB in {self.folder}")
        try:
            with open(os.path.join(self.folder, "info.json"), encoding="utf-8") as f:
                info = json.load(f)
            info.update(stopped=time.strftime("%Y-%m-%d %H:%M:%S"), seconds=round(seconds, 1), frames=self.frames, frames_skipped=self.skipped,
                        frames_dropped=self.dropped, events=self.events, stopped_because=self.stopped_because or "hotkey")
            with open(os.path.join(self.folder, "info.json"), "w", encoding="utf-8") as f:
                json.dump(info, f, indent=1)
        except (OSError, ValueError):
            pass
        return summary

    def mark(self, reason: str) -> None:
        """Something went wrong just now: note it in events.jsonl (a "mark" line with the `reason`), save a frame at once and every
        look for the next RUN_CAPTURE_BURST seconds - frames the thinning keeps (see `protected`). Only on a capture with a budget."""
        if not self.running or not self.max_zips:
            return
        self._emit("mark", reason=reason)
        self._burst_until = time.time() + cfg.RUN_CAPTURE_BURST
        self.shot_now.set()

    def protect_tail(self, seconds: float) -> None:
        """Keep the frames of the last `seconds` of the run from being thinned out - where a run that went wrong shows why."""
        horizon = time.time() - self.started - seconds
        for name, _ in self._kept:
            if len(self.protected) < cfg.RUN_CAPTURE_PROTECTED and float(name[:-len(".png")].rsplit("_", 1)[1]) >= horizon:
                self.protected.add(name)

    def _emit(self, kind: str, **fields: Any) -> None:
        line = json.dumps({"t": round(time.time() - self.started, 3), "type": kind, **fields})
        with self._lock:
            if self._file is not None:
                self._file.write(line + "\n")
                self._file.flush()
                self.events += 1

    def _where(self) -> dict[str, int]:
        x, y = cursor_position()
        rx, ry = to_reference(x, y)
        return {"x": x, "y": y, "rx": rx, "ry": ry}

    def _poll(self) -> None:
        """Thread body: write a key or mouse button going down or up, and the mouse moving, as it is noticed."""
        held: set[str] = set()
        last_position: Optional[Point] = None
        last_move = 0.0
        while not self.stop_event.wait(cfg.CAPTURE_POLL_INTERVAL):
            try:
                now_held = pressed_inputs() - {cfg.HOTKEYS["capture"]}
                for name in sorted(now_held - held):
                    if name.startswith("mouse_"):
                        self._emit("mouse_down", button=name[len("mouse_"):], **self._where())
                        self.shot_now.set()
                    else:
                        self._emit("key_down", key=name)
                for name in sorted(held - now_held):
                    if name.startswith("mouse_"):
                        self._emit("mouse_up", button=name[len("mouse_"):], **self._where())
                    else:
                        self._emit("key_up", key=name)
                held = now_held
                position = cursor_position()
                if position != last_position and time.time() - last_move >= cfg.CAPTURE_MOVE_INTERVAL:
                    self._emit("move", **self._where())
                    last_position, last_move = position, time.time()
            except Exception as error:   # a failed look must not end the capture
                logger.warning(f"Input capture: {error}")

    def _shots(self) -> None:
        """Thread body: look at the screen every CAPTURE_FRAME_INTERVAL and right after each click, saving a frame when
        it differs from the last one saved (see CAPTURE_CHANGE_FRACTION), after a click, or after CAPTURE_MAX_SKIP
        seconds without one; stops the capture when it has run CAPTURE_MAX_MINUTES or the disk is nearly full."""
        last_small: Optional[Image] = None
        last_saved = 0.0
        while not self.stop_event.is_set():
            clicked = self.shot_now.is_set()
            self.shot_now.clear()
            try:
                if time.time() - self.started >= cfg.CAPTURE_MAX_MINUTES * 60:
                    self._give_up(f"{cfg.CAPTURE_MAX_MINUTES:g} minutes")
                    return
                if shutil.disk_usage(self.folder).free < cfg.CAPTURE_MIN_FREE_MB * 1024 * 1024:
                    self._give_up("the disk is nearly full")
                    return
                seconds = time.time() - self.started
                screen = grab_screen()
                small = cv2.resize(cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY), (320, 180), interpolation=cv2.INTER_AREA)
                bursting = time.time() < self._burst_until and len(self.protected) < cfg.RUN_CAPTURE_PROTECTED     # (around a mark(): every look)
                if bursting or seconds - last_saved >= self._gap and (
                        clicked or last_small is None or seconds - last_saved >= max(cfg.CAPTURE_MAX_SKIP, self._gap)
                        or np.count_nonzero(cv2.absdiff(small, last_small) > cfg.CAPTURE_CHANGE_LEVEL) / small.size >= cfg.CAPTURE_CHANGE_FRACTION):
                    name = f"frame_{self.frames + 1:06d}_{seconds:.3f}.png"
                    if cv2.imwrite(os.path.join(self.folder, name), screen, [cv2.IMWRITE_PNG_COMPRESSION, 1]):
                        self.frames += 1
                        self._emit("frame", file=name)
                        last_small, last_saved = small, seconds
                        self._kept.append((name, os.path.getsize(os.path.join(self.folder, name))))
                        if bursting:
                            self.protected.add(name)
                        self._keep_within_budget()
                else:
                    self.skipped += 1
            except Exception as error:
                logger.warning(f"Input capture: couldn't save a frame: {error}")
            self.shot_now.wait(cfg.CAPTURE_FRAME_INTERVAL)
            if self.shot_now.is_set():
                self.stop_event.wait(0.02)      # let the click land on screen before looking

    def _keep_within_budget(self) -> None:
        """Needing more than `max_zips` zips: delete every other saved frame, never the newest, and save frames twice as
        far apart from now on - until the zips needed are few enough."""
        events = os.path.join(self.folder, "events.jsonl")
        while self.max_zips and len(self._kept) > 1 and len(plan_zip_parts(
                [size for _, size in self._kept], os.path.getsize(events) if os.path.exists(events) else 0)) > self.max_zips:
            keep = [frame for i, frame in enumerate(self._kept) if i % 2 == 0 or i == len(self._kept) - 1 or frame[0] in self.protected]
            if len(keep) == len(self._kept):            # nothing went: the oldest frame that isn't protected (or the newest) goes
                loose = [frame for frame in self._kept[:-1] if frame[0] not in self.protected]
                if not loose:
                    break                               # only frames that must stay are left
                keep = [frame for frame in self._kept if frame != loose[0]]
            for name, size in self._kept:
                if (name, size) not in keep:
                    try:
                        os.remove(os.path.join(self.folder, name))
                    except OSError:
                        pass
                    self._emit("frame_dropped", file=name)
                    self.dropped += 1
            self._kept = keep
            self._gap *= 2

    def _give_up(self, reason: str) -> None:
        self.stopped_because = reason
        self.stop_event.set()
        logger.info(f"Input capture stopped by itself: {reason}")

def plan_zip_parts(frame_sizes: list[int], head: int = 0) -> list[list[int]]:
    """Which frames (by position) go in which zip: parts of at most CAPTURE_ZIP_MB, the first one also holding `head`
    bytes (events.jsonl and info.json), the frames in order, none twice and none split."""
    limit = int(cfg.CAPTURE_ZIP_MB * 1024 * 1024)
    parts: list[list[int]] = [[]]
    used = head
    for position, size in enumerate(frame_sizes):
        if used + size > limit and parts[-1]:
            parts.append([])
            used = 0
        parts[-1].append(position)
        used += size
    return parts

def zip_capture(folder: str) -> list[str]:
    """Split a capture's files into zips of at most CAPTURE_ZIP_MB each, in <folder>/zips/ as
    <name>_part01.zip, _part02.zip, ... (stored, not compressed: PNGs don't shrink). The first part also holds
    events.jsonl and info.json, which are small; the frames go in time order, none twice. Returns the zip paths."""
    out = os.path.join(folder, "zips")
    os.makedirs(out, exist_ok=True)
    frames = sorted(f for f in os.listdir(folder) if f.startswith("frame_") and f.endswith(".png"))
    head = sum(os.path.getsize(os.path.join(folder, f)) for f in ("info.json", "events.jsonl") if os.path.exists(os.path.join(folder, f)))
    plan = plan_zip_parts([os.path.getsize(os.path.join(folder, name)) for name in frames], head)
    parts = [[frames[i] for i in positions] for positions in plan]
    parts[0] = ["info.json", "events.jsonl"] + parts[0]
    paths = []
    for number, names in enumerate(parts, 1):
        path = os.path.join(out, f"{os.path.basename(folder)}_part{number:02d}.zip")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
            for name in names:
                if os.path.exists(os.path.join(folder, name)):
                    z.write(os.path.join(folder, name), name)
        paths.append(path)
    return paths

def zip_capture_in_background(folder: str) -> threading.Thread:
    """zip_capture() on a thread of its own (a long capture takes a while to copy), saying where the zips are."""
    def work() -> None:
        try:
            paths = zip_capture(folder)
            logger.info(f"Capture zipped for sending ({len(paths)} file(s), at most {cfg.CAPTURE_ZIP_MB:g} MB each): {os.path.join(folder, 'zips')}")
        except OSError as error:
            logger.warning(f"Couldn't zip the capture: {error}")
    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    return thread

def make_room_for_capture(directory: str, limit: int) -> None:
    """Create `directory` and delete its oldest folders, so that one more makes `limit`."""
    os.makedirs(directory, exist_ok=True)
    folders = sorted((os.path.join(directory, f) for f in os.listdir(directory)), key=os.path.getmtime)
    for old in folders[:max(0, len(folders) - (limit - 1))]:
        shutil.rmtree(old, ignore_errors=True)

def start_run_capture(label: str) -> Optional[InputCapture]:
    """Start recording a minigame run (when MINIGAME_CAPTURE is on, and the hotkey's capture isn't running): a folder
    in RUN_CAPTURES_DIR, its frames kept within RUN_CAPTURE_ZIPS zips. None if there is no capture. A capture that can't be
    started is only a message: the minigame is played all the same."""
    if not cfg.MINIGAME_CAPTURE or (state.capture is not None and state.capture.running):
        return None
    try:
        os.makedirs(cfg.RUN_CAPTURES_DIR, exist_ok=True)
        capture = InputCapture(os.path.join(cfg.RUN_CAPTURES_DIR, f"{label}_{time.strftime('%Y%m%d_%H%M%S')}"), max_zips=cfg.RUN_CAPTURE_ZIPS)
        capture.start()
    except Exception as error:
        logger.warning(f"Couldn't start recording the minigame: {error}")
        return None
    state.run_capture = capture
    logger.debug(f"Recording the minigame: {capture.folder}")
    return capture

def mark_run_capture(reason: str) -> None:
    """Say to the recording of the minigame being played, if there is one, that something went wrong now (InputCapture.mark()): frames of
    this moment are saved and kept. Never raises."""
    capture = state.run_capture
    if capture is None:
        return
    try:
        capture.mark(reason)
    except Exception as error:
        logger.warning(f"Couldn't mark the recording: {error}")

def run_capture_suffix(outcome: str, result: dict[str, Any]) -> str:
    """What is added to a recording's folder name when the run is over, to tell it from the others: the candy corn earned (`_2105cc`, or
    `_cc-unread`), the room reached in Hauntlet 2 (`_room10`), `_failed` for a run that did not finish and `_failures` for one that did
    with failures logged on the way."""
    earned = result.get("earned")
    parts = [f"{earned}cc" if earned is not None else "cc-unread"]
    if result.get("room") is not None:
        parts.append(f"room{result['room']}")
    if not outcome.startswith("finished"):
        parts.append("failed")
    elif result.get("failures"):
        parts.append("failures")
    return "_" + "_".join(parts)

def finish_run_capture(capture: Optional[InputCapture], outcome: str, result: Optional[dict[str, Any]] = None) -> None:
    """Stop a run capture (if there is one), note how the run ended in its info.json (with the fields of `result`, what the run earned, see
    minigame_result()), and zip it for sending."""
    if capture is None:
        return
    try:
        state.run_capture = None                                # (one at a time)
        if not outcome.startswith("finished"):
            capture.protect_tail(cfg.RUN_CAPTURE_TAIL)          # a run that went wrong: its last moments are what shows why
        capture.stopped_because = capture.stopped_because or "the minigame ended"
        logger.debug(f"Minigame recording stopped: {capture.stop()}")
        path = os.path.join(capture.folder, "info.json")
        with open(path, encoding="utf-8") as f:
            info = json.load(f)
        info["outcome"] = outcome
        info.update(result or {})
        with open(path, "w", encoding="utf-8") as f:
            json.dump(info, f, indent=1)
        renamed = capture.folder + run_capture_suffix(outcome, result or {})
        try:
            os.rename(capture.folder, renamed)                      # (before the zips are made: they are named after the folder)
            capture.folder = renamed
        except OSError as error:
            logger.warning(f"Couldn't rename the recording: {error}")
        zip_capture_in_background(capture.folder)
    except Exception as error:
        logger.warning(f"Couldn't finish recording the minigame: {error}")

def captures_to_keep(runs: list[tuple[str, Optional[int], float, bool]]) -> set[str]:
    """Of the recordings of one minigame, `runs` = (folder, candy corn earned or None, when it started, whether it failed - did not finish
    or had a failure logged), the ones to keep: the latest, the RUN_CAPTURES_FAILED latest that failed, the RUN_CAPTURES_BEST that earned
    most and the RUN_CAPTURES_WORST that earned least (a run whose candy corn wasn't read counts as the worst)."""
    if not runs:
        return set()
    def earned(run: tuple[str, Optional[int], float, bool]) -> tuple[int, float]:
        return (-1 if run[1] is None else run[1], run[2])
    ranked = sorted(runs, key=earned)
    keep = {max(runs, key=lambda run: run[2])[0]}
    failed = sorted((run for run in runs if run[3]), key=lambda run: run[2])
    keep |= {run[0] for run in failed[max(0, len(failed) - cfg.RUN_CAPTURES_FAILED):]}
    keep |= {run[0] for run in ranked[:cfg.RUN_CAPTURES_WORST]}
    keep |= {run[0] for run in ranked[len(ranked) - cfg.RUN_CAPTURES_BEST:]}
    return keep

def prune_run_captures(label: str) -> None:
    """Delete the recordings of minigame `label` in RUN_CAPTURES_DIR that captures_to_keep() doesn't keep, going by the `earned` and
    `started` that record_minigame_result() wrote into each one's info.json. Never raises."""
    try:
        if not os.path.isdir(cfg.RUN_CAPTURES_DIR):
            return                                                  # (recording is off, or nothing has been recorded yet)
        runs = []
        for name in os.listdir(cfg.RUN_CAPTURES_DIR):
            folder = os.path.join(cfg.RUN_CAPTURES_DIR, name)
            if not (name.startswith(label + "_") and os.path.isdir(folder)):
                continue
            try:
                with open(os.path.join(folder, "info.json"), encoding="utf-8") as f:
                    info = json.load(f)
            except (OSError, ValueError):
                info = {}
            try:
                started = time.mktime(time.strptime(name[len(label) + 1:][:15], "%Y%m%d_%H%M%S"))     # (the folder's name says when; its time changes as files go in and out)
            except ValueError:
                started = os.path.getmtime(folder)
            failed = info.get("finished") is False or bool(info.get("failures"))
            runs.append((folder, info.get("earned"), started, failed))
        for folder in {run[0] for run in runs} - captures_to_keep(runs):
            shutil.rmtree(folder, ignore_errors=True)
    except Exception as error:
        logger.warning(f"Couldn't tidy the recordings: {error}")

def minigame_result(name: str, outcome: str, finished: bool, seconds: float, before: Optional[int], after: Optional[int], failures: int = 0) -> dict[str, Any]:
    """What a minigame run came to: how it ended, and the candy corn it earned - the HUD total after less the total before, None when either
    wasn't read - the `failures` logged while it ran, plus what its player noted (state.minigame_detail: the room reached in Hauntlet 2)."""
    earned = after - before if before is not None and after is not None and after >= before else None
    return {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "run": state.run_number, "minigame": name, "outcome": outcome, "finished": finished,
            "seconds": round(seconds), "candy_corn_before": before, "candy_corn_after": after, "earned": earned, "failures": failures, **state.minigame_detail}

def record_minigame_result(result: dict[str, Any]) -> None:
    """Note a finished minigame run's `result` (minigame_result()): a line of RUNS_DIR/results/results.jsonl and one in the run log - then the
    recordings of that minigame that are not among its best, its worst and its latest are deleted (prune_run_captures()). Never raises."""
    try:
        folder = os.path.join(cfg.RUNS_DIR, "results")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "results.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(result) + "\n")
        name, earned = result["minigame"], result["earned"]
        extra = "".join(f", {key} {value}" for key, value in state.minigame_detail.items())
        log_run_event(f"minigame result: {cfg.MINIGAME_LABELS.get(name, name)}: " + (f"{earned} candy corn" if earned is not None else "candy corn not read") + extra)
        prune_run_captures(name)
    except Exception as error:
        logger.warning(f"Couldn't note the result: {error}")

def read_candy_corn_settled(tries: int = 4) -> Optional[int]:
    """read_candy_corn() of the screen once two looks half a second apart agree (the total counts up for a moment after a run); if they
    never do, the last total read, and None if none was. Costs a second or so, up to `tries` looks of CANDY_CORN_SETTLE apart."""
    seen: Optional[int] = None
    for attempt in range(tries):
        now = read_candy_corn(grab_screen())
        if now is not None and now == seen:
            return now
        seen = now if now is not None else seen                     # (a look that reads nothing - a white flash - doesn't forget the one before)
        if attempt < tries - 1:
            wait_interruptible(cfg.CANDY_CORN_SETTLE)
    return seen

def toggle_input_capture() -> Optional[InputCapture]:
    """Start a capture (a fresh folder in CAPTURES_DIR, the oldest beyond MAX_CAPTURES deleted) or, if one is
    running, stop it - what the capture hotkey does. Returns the capture that is running now, or None."""
    current = state.capture
    if current is not None:
        state.capture = None
        was_running = current.running
        logger.info(f"Capture stopped: {current.stop()}")
        zip_capture_in_background(current.folder)
        if was_running:
            return None            # (one that had stopped by itself is only tidied up here: this press starts a new one)
    make_room_for_capture(cfg.CAPTURES_DIR, cfg.MAX_CAPTURES)
    capture = InputCapture(os.path.join(cfg.CAPTURES_DIR, "capture_" + time.strftime("%Y%m%d_%H%M%S")))
    capture.start()
    state.capture = capture
    logger.info(f"Capture started (press {cfg.HOTKEYS['capture'].upper()} again to stop): {capture.folder}")
    return capture

# ============================================================================
# HAUNTLET 2 READER
# ============================================================================
# What the Hauntlet 2 screen says, as plain values - no decisions and no input here. The room number first; see
# docs/ARCHITECTURE.md#reading-hauntlet-2.

def room_digit(mask: Image) -> Image:
    """A digit's white pixels (`mask`, cropped to its bounding box), shrunk to HAUNTLET_DIGIT_SIZE, 0-255."""
    return cv2.resize(mask * 255, cfg.HAUNTLET_DIGIT_SIZE, interpolation=cv2.INTER_AREA)

@lru_cache(maxsize=None)
def _digit_templates(directory: str, modified: float) -> dict[int, Image]:
    found = {int(os.path.splitext(f)[0]): cv2.imread(os.path.join(directory, f), cv2.IMREAD_GRAYSCALE)
             for f in os.listdir(directory) if f.endswith(".png") and os.path.splitext(f)[0].isdigit()}
    return {digit: picture for digit, picture in found.items() if picture is not None}

def digit_templates() -> dict[int, Image]:
    """{digit: picture} for the saved room digits (HAUNTLET_DIGIT_DIR), re-read when the folder changes."""
    directory = cfg.HAUNTLET_DIGIT_DIR
    return _digit_templates(directory, os.path.getmtime(directory)) if os.path.isdir(directory) else {}

def read_hauntlet_room(img: Image) -> Optional[int]:
    """The room number on the ROOM panel of `img`, or None when it isn't readable (no panel - a transition, a
    close-up of a door - or a digit that matches none of the saved ones)."""
    left, top, right, bottom = cfg.HAUNTLET_ROOM_BOX
    mask = (img[top:bottom, left:right].min(axis=2) >= cfg.HAUNTLET_DIGIT_WHITE_LEVEL).astype(np.uint8)
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    boxes = sorted((int(stats[i, 0]), int(stats[i, 1]), int(stats[i, 2]), int(stats[i, 3]))
                   for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] >= cfg.HAUNTLET_DIGIT_MIN_AREA)
    templates = digit_templates()
    if not 1 <= len(boxes) <= cfg.HAUNTLET_ROOM_MAX_DIGITS or not templates:
        return None
    number = 0
    for x, y, w, h in boxes:
        picture = room_digit(mask[y:y + h, x:x + w])
        diffs = {digit: float(np.abs(picture.astype(int) - template.astype(int)).mean()) for digit, template in templates.items()}
        digit = min(diffs, key=lambda d: diffs[d])
        if diffs[digit] > cfg.HAUNTLET_DIGIT_MAX_DIFF:
            return None
        number = number * 10 + digit
    return number

@dataclass
class HauntletItem:
    """An item slot on the Hauntlet 2 screen: its `name` (a folder of HAUNTLET_ITEM_DIR, or "unknown"), the `count`
    on its badge (None if the badge couldn't be read), and the slot's centre `pos`."""
    name: str
    count: Optional[int]
    pos: Point

def find_item_slots(img: Image) -> list[tuple[int, int, int]]:
    """(x, y, radius) of every item slot along the bottom of `img`, left to right."""
    top, bottom = cfg.HAUNTLET_SLOT_BAND
    gray = cv2.medianBlur(cv2.cvtColor(img[top:bottom], cv2.COLOR_BGR2GRAY), 5)
    circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, dp=1, minDist=cfg.HAUNTLET_SLOT_MIN_DISTANCE, param1=100, param2=30,
                               minRadius=cfg.HAUNTLET_SLOT_RADIUS[0], maxRadius=cfg.HAUNTLET_SLOT_RADIUS[1])
    return [] if circles is None else sorted((int(x), int(y) + top, int(r)) for x, y, r in circles[0])

def _folder_stamp(directory: str) -> tuple[tuple[str, str, float], ...]:
    """What is in a folder of folders and when each file changed - different when a file is added, changed, removed or
    its folder renamed."""
    return tuple(sorted((root, f, os.path.getmtime(os.path.join(root, f))) for root, _, files in os.walk(directory) for f in files))

@lru_cache(maxsize=8)
def _picture_folders(directory: str, stamp: tuple[tuple[str, str, float], ...], gray: bool) -> dict[str, list[Image]]:
    found: dict[str, list[Image]] = {}
    for name in sorted(os.listdir(directory)):
        folder = os.path.join(directory, name)
        if os.path.isdir(folder):
            pictures = [cv2.imread(os.path.join(folder, f), cv2.IMREAD_GRAYSCALE if gray else cv2.IMREAD_COLOR)
                        for f in sorted(os.listdir(folder)) if f.endswith(".png")]
            found[name] = [p for p in pictures if p is not None]
    return found

def picture_folders(directory: str, gray: bool = True) -> dict[str, list[Image]]:
    """{folder name: its pictures} for a folder of folders of .png files (the items, the badge glyphs), re-read when
    anything in it changes; {} if it doesn't exist."""
    return _picture_folders(directory, _folder_stamp(directory), gray) if os.path.isdir(directory) else {}

def identify_item(crop: Image) -> tuple[Optional[str], float]:
    """(name, score) of the saved item `crop` (the square around a slot's centre) matches best, name None if the best
    score is under HAUNTLET_ITEM_MATCH. Compared as grey pictures: the slot's blue or dim teal background doesn't matter much."""
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    best_name, best = None, -1.0
    for name, pictures in picture_folders(cfg.HAUNTLET_ITEM_DIR).items():
        for picture in pictures:
            if picture.shape == gray.shape:
                score = float(cv2.matchTemplate(gray, picture, cv2.TM_CCOEFF_NORMED)[0][0])
                if score > best:
                    best_name, best = name, score
    return (best_name if best >= cfg.HAUNTLET_ITEM_MATCH else None), best

def read_item_count(img: Image, x: int, y: int) -> Optional[int]:
    """The number on the "x2" badge of the slot centred at (x, y), or None if it can't be read (no badge, or a glyph
    that matches none of the saved ones)."""
    left, top, right, bottom = cfg.HAUNTLET_BADGE_BOX
    region = img[max(0, y + top):y + bottom, max(0, x + left):x + right]
    if region.size == 0:
        return None
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    purple = cv2.morphologyEx(cv2.inRange(hsv, (125, 100, 120), (150, 255, 255)), cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(purple, connectivity=8)
    if count < 2:
        return None
    shaped = [i for i in range(1, count) if cfg.HAUNTLET_BADGE_WIDTH[0] <= stats[i, cv2.CC_STAT_WIDTH] <= cfg.HAUNTLET_BADGE_WIDTH[1]
              and cfg.HAUNTLET_BADGE_HEIGHT[0] <= stats[i, cv2.CC_STAT_HEIGHT] <= cfg.HAUNTLET_BADGE_HEIGHT[1]
              and stats[i, cv2.CC_STAT_AREA] >= 300]
    if not shaped:
        return None
    badge = max(shaped, key=lambda i: stats[i, cv2.CC_STAT_AREA])
    bx, by, bw, bh, area = (int(v) for v in stats[badge])
    white = ((region[by:by + bh, bx:bx + bw].min(axis=2) >= 225) & (labels[by:by + bh, bx:bx + bw] == badge)).astype(np.uint8)
    pieces_count, _, piece_stats, _ = cv2.connectedComponentsWithStats(white, connectivity=8)
    pieces = sorted((int(piece_stats[i, 0]), int(piece_stats[i, 1]), int(piece_stats[i, 2]), int(piece_stats[i, 3]))
                    for i in range(1, pieces_count) if piece_stats[i, cv2.CC_STAT_AREA] >= 6 and piece_stats[i, cv2.CC_STAT_HEIGHT] >= cfg.HAUNTLET_COUNT_MIN_HEIGHT)
    glyphs = picture_folders(cfg.HAUNTLET_COUNT_DIR)
    if len(pieces) < 2 or "x" not in glyphs:
        return None
    text = ""
    for px, py, pw, ph in pieces:
        picture = cv2.resize(white[py:py + ph, px:px + pw] * 255, cfg.HAUNTLET_COUNT_SIZE, interpolation=cv2.INTER_AREA).astype(int)
        diffs = {name: min(float(np.abs(picture - g.astype(int)).mean()) for g in pictures) for name, pictures in glyphs.items() if pictures}
        name = min(diffs, key=lambda n: diffs[n])
        if diffs[name] > cfg.HAUNTLET_COUNT_MAX_DIFF:
            return None
        text += name
    return int(text[1:]) if text.startswith("x") and text[1:].isdigit() else None

def read_hauntlet_items(img: Image) -> list[HauntletItem]:
    """The items in the slots along the bottom of `img`, left to right: each one's name, count and position. An item
    that matches none of the saved ones is named "unknown". Names in HAUNTLET_NOT_ITEMS (the lobby's backpack button) are left out."""
    items = []
    for x, y, radius in find_item_slots(img):
        half = cfg.HAUNTLET_ITEM_CROP // 2
        crop = img[y - half:y + half, x - half:x + half]
        if crop.shape[:2] != (cfg.HAUNTLET_ITEM_CROP, cfg.HAUNTLET_ITEM_CROP):
            continue
        name, _ = identify_item(crop)
        if name is None:
            name = "unknown"
        if name not in cfg.HAUNTLET_NOT_ITEMS:
            items.append(HauntletItem(name, read_item_count(img, x, y), (x, y)))
    return items

@dataclass
class HauntletHearts:
    """The player's hearts: how many are `red` (left) and `black` (lost), one heart's `width` in px, and the row's
    top-left `pos`."""
    red: int
    black: int
    width: int
    pos: Point

    @property
    def center_x(self) -> float:
        """The middle of the row, in px - above the player's head, so where the player stands."""
        return self.pos[0] + ((self.red + self.black - 1) * cfg.HAUNTLET_HEART_PITCH + 1) * self.width / 2

def pink_heart_pieces(img: Image) -> list[tuple[int, int, int, int]]:
    """(x, y, w, h) of every heart-shaped pink piece of `img` (HAUNTLET_HEART_*), nothing above HAUNTLET_HEART_MIN_Y."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.bitwise_or(cv2.inRange(hsv, *cfg.HAUNTLET_HEART_PINK[0]), cv2.inRange(hsv, *cfg.HAUNTLET_HEART_PINK[1]))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    pieces = []
    for i in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[i])
        if (y >= cfg.HAUNTLET_HEART_MIN_Y and w >= cfg.HAUNTLET_HEART_MIN_WIDTH and area >= 150
                and cfg.HAUNTLET_HEART_ASPECT[0] <= w / h <= cfg.HAUNTLET_HEART_ASPECT[1] and area / (w * h) >= cfg.HAUNTLET_HEART_MIN_FILL):
            pieces.append((x, y, w, h))
    return pieces

def read_hauntlet_hearts(img: Image) -> Optional[HauntletHearts]:
    """The player's hearts in `img`, or None when there are none to see (a close-up of a door, a transition, the lobby,
    or all of them lost - a row of nothing but black hearts has no pink one to find it by). One frame can be wrong, a
    heart hidden or a stray pink thing taken for one: a caller that acts on it should see the same on a few frames."""
    pieces = pink_heart_pieces(img)
    if not pieces:
        return None
    ax, ay, aw, ah = min(pieces, key=lambda p: abs(p[0] + p[2] / 2 - cfg.REFERENCE_CENTER_X))
    pitch = cfg.HAUNTLET_HEART_PITCH * aw
    black = img.max(axis=2) <= cfg.HAUNTLET_HEART_BLACK_LEVEL
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    pink = cv2.bitwise_or(cv2.inRange(hsv, *cfg.HAUNTLET_HEART_PINK[0]), cv2.inRange(hsv, *cfg.HAUNTLET_HEART_PINK[1])) > 0

    def kind(step: int) -> Optional[str]:
        centre = ax + aw / 2 + step * pitch
        if any(abs(px + pw / 2 - centre) <= pitch / 2 and abs(py - ay) <= 0.4 * ah for px, py, pw, ph in pieces):
            return "red"
        columns = slice(max(0, int(centre - aw / 2)), int(centre + aw / 2))
        if pink[ay:ay + ah, columns].mean() >= cfg.HAUNTLET_HEART_PINK_FRACTION:       # a heart the piece search missed (two touching, or part hidden)
            return "red"
        cell = black[ay:ay + ah, columns]
        return "black" if cell.size and cell.mean() >= cfg.HAUNTLET_HEART_BLACK_FRACTION else None

    row = {0: "red"}
    for direction in (1, -1):
        for n in range(1, cfg.HAUNTLET_HEART_MAX_EACH_SIDE + 1):
            found = kind(direction * n)
            if found is None:
                break
            row[direction * n] = found
    kinds = list(row.values())
    if not cfg.HAUNTLET_HEART_MIN <= len(kinds) <= cfg.HAUNTLET_HEART_MAX:
        return None                     # not a row of hearts: one pink thing alone, or something pink next to the row counted too
    return HauntletHearts(kinds.count("red"), kinds.count("black"), aw, (int(ax + min(row) * pitch), ay))

def hauntlet_timer_visible(img: Image) -> bool:
    """Whether the room's timer is shown (HAUNTLET_TIMER_*): it is hidden in a room's first moments and while the
    player passes through a door, when the screen still shows the last room."""
    left, top, right, bottom = cfg.HAUNTLET_TIMER_BOX
    white = img[top:bottom, left:right].min(axis=2) >= cfg.HAUNTLET_TIMER_WHITE_LEVEL
    return int(np.count_nonzero(white)) >= cfg.HAUNTLET_TIMER_MIN_WHITE

@dataclass
class HauntletDoor:
    """A door of a Hauntlet 2 room: where its TV's number is on screen (`x`, `y`) and its `kind` - "locked", "mystery"
    (the "???" door), "shadow" (smoke with glowing eyes), "dark", "boarded" or "plain"; None for a door that couldn't be looked at (or a fork's doorway,
    which isn't looked for)."""
    x: int
    y: int
    kind: Optional[str]

def find_door_tvs(img: Image) -> list[tuple[int, int]]:
    """The centre of the three TV numbers (HAUNTLET_TV_*) in `img`, left to right: the three above a room's doors, in
    the row they make - [] when there isn't one (a fork's hall, a close-up, a transition). With the camera close the
    middle TV can be off the top of the screen or behind the room panel: two TVs far enough apart to be the outer ones
    (HAUNTLET_TV_PAIR_GAP) and centred on the screen (the player stands in front of the middle door) give the middle one
    halfway between. Two neighbours high up (in the row of the room panel) with the third where the panel is give the third
    there: the panel hides its glow."""
    low, high = cfg.HAUNTLET_TV_GLOW
    mask = cv2.inRange(img, (low[2], low[1], low[0]), (high[2], high[1], high[0]))   # BGR
    left, top, right, bottom = cfg.HAUNTLET_TV_HUD_BOX
    mask[top:bottom, left:right] = 0
    count, _, stats, centres = cv2.connectedComponentsWithStats(mask, connectivity=8)
    blobs = sorted((float(centres[i][0]), float(centres[i][1]), float(stats[i, cv2.CC_STAT_AREA])) for i in range(1, count)
                   if cfg.HAUNTLET_TV_AREA[0] <= stats[i, cv2.CC_STAT_AREA] <= cfg.HAUNTLET_TV_AREA[1])
    found = [(x, y) for x, y, _ in blobs]
    for first, middle, last in itertools.combinations(found, 3):                   # (not only neighbours: a stray glow can sit between)
        gaps = (middle[0] - first[0], last[0] - middle[0])
        if (abs(first[1] - middle[1]) <= cfg.HAUNTLET_TV_ROW_TOLERANCE and abs(middle[1] - last[1]) <= cfg.HAUNTLET_TV_ROW_TOLERANCE
                and all(cfg.HAUNTLET_TV_SPACING[0] <= gap <= cfg.HAUNTLET_TV_SPACING[1] for gap in gaps)
                and abs(gaps[0] - gaps[1]) <= cfg.HAUNTLET_TV_SPACING_TOLERANCE * max(gaps)):
            return [(int(x), int(y)) for x, y in (first, middle, last)]
    for (first, last), size in zip(zip(found, found[1:]), (((a + b) / 2) ** 0.5 for (_, _, a), (_, _, b) in zip(blobs, blobs[1:]))):
        far_apart = last[0] - first[0] >= cfg.HAUNTLET_TV_PAIR_RATIO * size       # twice the neighbours' gap for the TVs' size
        if (abs(first[1] - last[1]) <= cfg.HAUNTLET_TV_ROW_TOLERANCE
                and cfg.HAUNTLET_TV_PAIR_GAP[0] <= last[0] - first[0] <= cfg.HAUNTLET_TV_PAIR_GAP[1]
                and (far_apart or abs((first[0] + last[0]) / 2 - cfg.REFERENCE_CENTER_X) <= cfg.HAUNTLET_TV_PAIR_OFF_CENTER)):
            return [(int(first[0]), int(first[1])), (int((first[0] + last[0]) / 2), int((first[1] + last[1]) / 2)), (int(last[0]), int(last[1]))]
    hud_left, _, hud_right, hud_bottom = cfg.HAUNTLET_TV_HUD_BOX
    for first, second in zip(found, found[1:]):                                  # two neighbours, the third behind the room panel
        gap = second[0] - first[0]
        if abs(first[1] - second[1]) > cfg.HAUNTLET_TV_ROW_TOLERANCE or not cfg.HAUNTLET_TV_SPACING[0] <= gap <= cfg.HAUNTLET_TV_SPACING[1] or second[1] > hud_bottom:
            continue
        for order in ((first, second, (second[0] + gap, second[1])), ((first[0] - gap, first[1]), first, second)):
            if hud_left <= order[0][0] <= hud_right or hud_left <= order[2][0] <= hud_right:
                return [(int(x), int(y)) for x, y in order]
    return []

def hauntlet_shadow_eyes(door: Image) -> bool:
    """Whether the glowing eyes of a shadow door are in `door`: a small piece of near-white (HAUNTLET_SHADOW_WHITE, HAUNTLET_SHADOW_AREA px)
    in the dark smoke - the ring around it no brighter than HAUNTLET_SHADOW_RING on average. (A door's screws, lights and the
    white of the lock do not sit in the dark.)"""
    if door.size == 0:
        return False
    white = (door.min(axis=2) >= cfg.HAUNTLET_SHADOW_WHITE).astype(np.uint8)
    gray = door.mean(axis=2)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(white)
    for i in range(1, count):
        if not cfg.HAUNTLET_SHADOW_AREA[0] <= stats[i, cv2.CC_STAT_AREA] <= cfg.HAUNTLET_SHADOW_AREA[1]:
            continue
        blob = (labels == i).astype(np.uint8)
        ring = cv2.dilate(blob, np.ones((15, 15), np.uint8)) - cv2.dilate(blob, np.ones((3, 3), np.uint8))
        if ring.any() and gray[ring > 0].mean() <= cfg.HAUNTLET_SHADOW_RING:
            return True
    return False

def hauntlet_door_kind(img: Image, x: int, y: int, scale: float) -> Optional[str]:
    """What kind of door hangs below the TV number at (x, y), from its pixels (see HAUNTLET_LOCK_LIGHT_BOX); `scale`
    is the picture's size against the one the boxes were measured at. None if the door is off the screen."""
    def region(box: tuple[int, int, int, int]) -> Image:
        left, top, right, bottom = box
        return img[max(0, int(y + top * scale)):int(y + bottom * scale), max(0, int(x + left * scale)):int(x + right * scale)]
    light = region(cfg.HAUNTLET_LOCK_LIGHT_BOX).astype(int)
    if light.size:
        red = ((light[..., 2] >= cfg.HAUNTLET_LOCK_LIGHT_RED) & (light[..., 1] <= cfg.HAUNTLET_LOCK_LIGHT_OTHER) & (light[..., 0] <= cfg.HAUNTLET_LOCK_LIGHT_OTHER)).astype(np.uint8)
        for area, width, height in cv2.connectedComponentsWithStats(red)[2][1:, 2:5][:, [2, 0, 1]]:     # the light is a small round patch: a red wall is not
            if (cfg.HAUNTLET_LOCK_LIGHT_MIN <= area <= cfg.HAUNTLET_LOCK_LIGHT_MAX
                    and max(width, height) <= cfg.HAUNTLET_LOCK_LIGHT_ROUND * min(width, height)):
                return "locked"
    if hauntlet_shadow_eyes(region(cfg.HAUNTLET_SHADOW_BOX)):
        return "shadow"
    door = region(cfg.HAUNTLET_DOOR_BOX)
    if door.size == 0:
        return None
    brightness = door.mean(axis=2)
    if np.count_nonzero(brightness <= cfg.HAUNTLET_DARK_LEVEL) >= cfg.HAUNTLET_DARK_FRACTION * brightness.size:
        return "dark"
    if brightness.mean() >= cfg.HAUNTLET_MYSTERY_BRIGHTNESS:
        return "mystery"
    planks = region(cfg.HAUNTLET_PLANK_BOX)
    if min(planks.shape[:2]) < 3:
        return None
    grey = cv2.GaussianBlur(cv2.cvtColor(planks, cv2.COLOR_BGR2GRAY).astype(np.float32), (0, 0), cfg.HAUNTLET_PLANK_BLUR)
    dx, dy = cv2.Sobel(grey, cv2.CV_32F, 1, 0), cv2.Sobel(grey, cv2.CV_32F, 0, 1)
    angle = np.degrees(np.arctan2(dy, dx)) % 180                # an edge's direction is square to its gradient: 90 is a horizontal edge
    on_plank_edge = (np.hypot(dx, dy) > cfg.HAUNTLET_PLANK_EDGE) & (np.abs(angle - 90) < cfg.HAUNTLET_PLANK_ANGLE)
    return "boarded" if np.count_nonzero(on_plank_edge) >= cfg.HAUNTLET_PLANK_FRACTION * on_plank_edge.size else "plain"

def find_ghost_door(img: Image, doors: list[HauntletDoor]) -> Optional[int]:
    """The index of the door with the phone's red arrow over it (the ghost's door), or None: a big red triangle with a ghost on
    it (HAUNTLET_ARROW_*) just above the TV of the door it points at. Doors that weren't located can't be told."""
    if len(doors) != 3 or not doors[0].x:
        return None
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.bitwise_or(cv2.inRange(hsv, *cfg.HAUNTLET_ARROW_RED[0]), cv2.inRange(hsv, *cfg.HAUNTLET_ARROW_RED[1]))
    left, top, right, bottom = cfg.HAUNTLET_ARROW_BOX
    box = np.zeros_like(mask)
    box[top:bottom, left:right] = mask[top:bottom, left:right]
    box = cv2.morphologyEx(box, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    count, _, stats, centres = cv2.connectedComponentsWithStats(box, connectivity=8)
    spacing = (doors[2].x - doors[0].x) / 2
    for i in range(1, count):
        if (cfg.HAUNTLET_ARROW_AREA[0] <= stats[i, cv2.CC_STAT_AREA] <= cfg.HAUNTLET_ARROW_AREA[1]
                and cfg.HAUNTLET_ARROW_WIDTH[0] <= stats[i, cv2.CC_STAT_WIDTH] <= cfg.HAUNTLET_ARROW_WIDTH[1]
                and cfg.HAUNTLET_ARROW_HEIGHT[0] <= stats[i, cv2.CC_STAT_HEIGHT] <= cfg.HAUNTLET_ARROW_HEIGHT[1]):
            nearest = min(range(3), key=lambda k: abs(doors[k].x - centres[i][0]))
            if abs(doors[nearest].x - centres[i][0]) <= cfg.HAUNTLET_ARROW_MAX_OFFSET * spacing:
                return nearest
    return None

def find_hauntlet_doors(img: Image) -> list[HauntletDoor]:
    """The three doors of the room in `img`, left to right, with their kinds - [] if there is no row of TVs."""
    tvs = find_door_tvs(img)
    if not tvs:
        return []
    scale = (tvs[2][0] - tvs[0][0]) / 2 / cfg.HAUNTLET_DOOR_SPACING
    return [HauntletDoor(x, y, hauntlet_door_kind(img, x, y, scale)) for x, y in tvs]

# ============================================================================
# WORKFLOWS
# ============================================================================

def format_duration(seconds: float) -> str:
    """"1m 23s" / "45s" - how long something has been going."""
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}m {secs:02d}s" if minutes else f"{secs}s"

def process_memory_mb() -> Optional[float]:
    """This process's memory use in MB (its working set on Windows), or None
    if it can't be read."""
    try:
        if os.name == "nt":
            from ctypes import wintypes
            class MemoryCounters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                    (name, ctypes.c_size_t) for name in (
                        "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                        "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
            counters = MemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            kernel32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi  # type: ignore[attr-defined]  # Windows only
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(MemoryCounters), wintypes.DWORD]
            if not psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
                return None
            return counters.WorkingSetSize / 1048576
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1048576
    except Exception:
        return None

def system_cpu_times() -> Optional[tuple[float, float]]:
    """(idle, total) CPU time of the whole machine so far, in any one unit (the difference between two readings is
    what counts - see ResourceMonitor), or None if it can't be read."""
    try:
        if os.name == "nt":
            from ctypes import wintypes
            idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
            if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):  # type: ignore[attr-defined]  # Windows only
                return None
            def value(ft: Any) -> float:
                return float((ft.dwHighDateTime << 32) | ft.dwLowDateTime)
            return value(idle), value(kernel) + value(user)     # the kernel time includes the idle time
        with open("/proc/stat") as f:
            fields = [float(x) for x in f.readline().split()[1:]]
        return fields[3] + fields[4], sum(fields[:8])          # idle + iowait; the guest columns are already in user
    except Exception:
        return None

def system_memory_percent() -> Optional[float]:
    """How much of the machine's memory is in use, in percent, or None if it can't be read."""
    try:
        if os.name == "nt":
            from ctypes import wintypes
            class MemoryStatus(ctypes.Structure):
                _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD)] + [
                    (name, ctypes.c_uint64) for name in (
                        "ullTotalPhys", "ullAvailPhys", "ullTotalPageFile", "ullAvailPageFile", "ullTotalVirtual",
                        "ullAvailVirtual", "ullAvailExtendedVirtual")]
            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(status)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]  # Windows only
                return None
            return float(status.dwMemoryLoad)
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                key, _, rest = line.partition(":")
                info[key] = float(rest.split()[0])
        return 100 * (1 - info["MemAvailable"] / info["MemTotal"])
    except Exception:
        return None

class ResourceMonitor(threading.Thread):
    """Every RESOURCE_LOG_INTERVAL logs this script's CPU and memory and the whole PC's:
    "resources: cpu 2.4% mem 215MB pc 38.5% pcmem 71%". See docs/ARCHITECTURE.md#debugging-an-unattended-run.
    """

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.stopped = threading.Event()

    def stop(self) -> None:
        self.stopped.set()

    def run(self) -> None:
        wall, cpu = time.time(), time.process_time()
        system = system_cpu_times()
        while not self.stopped.wait(cfg.RESOURCE_LOG_INTERVAL):
            now_wall, now_cpu = time.time(), time.process_time()
            percent = 100 * (now_cpu - cpu) / max(1e-6, (now_wall - wall) * (os.cpu_count() or 1))
            wall, cpu = now_wall, now_cpu
            memory = process_memory_mb()
            run_stats.cpu_percent = round(percent, 1)
            run_stats.memory_mb = None if memory is None else round(memory)
            pc = ""
            now_system = system_cpu_times()
            if system and now_system and now_system[1] > system[1]:
                pc_cpu = 100 * (1 - (now_system[0] - system[0]) / (now_system[1] - system[1]))
                pc = f" pc {min(100.0, max(0.0, pc_cpu)):.1f}%"
                pc_memory = system_memory_percent()
                if pc_memory is not None:
                    pc += f" pcmem {pc_memory:.0f}%"
            system = now_system
            if memory is not None:
                try:
                    log_run_event(f"resources: cpu {percent:.1f}% mem {memory:.0f}MB{pc}")
                except OSError:
                    pass   # a transient write failure: sample again next time


def check_quiet() -> None:
    """Every cycle: note the minutes since a need was resolved and log "quiet: ..." each
    QUIET_LOG_INTERVAL. See docs/ARCHITECTURE.md#debugging-an-unattended-run.
    """
    quiet = time.time() - state.last_progress if state.last_progress else 0.0
    run_stats.quiet_minutes = int(quiet // 60)
    reports = int(quiet // cfg.QUIET_LOG_INTERVAL)
    if reports > state.quiet_reported:
        log_run_event(f"quiet: no need resolved for {reports * cfg.QUIET_LOG_INTERVAL / 60:g} min")
    state.quiet_reported = reports

def respawn_if_no_detection() -> bool:
    """Respawn if no need was detected for NO_DETECTION_RESPAWN_INTERVAL; never touches
    state.last_progress. Returns True if it respawned. See docs/ARCHITECTURE.md#stuck-needs.
    """
    if state.last_detection is None or time.time() - state.last_detection < cfg.NO_DETECTION_RESPAWN_INTERVAL:
        return False
    minutes = (time.time() - state.last_detection) / 60
    logger.info(f"No need detected for {minutes:.0f} min - respawning")
    log_run_event(f"no need detected for {minutes:.0f} min - respawning")
    respawn_character()
    state.last_detection = time.time()
    return True

def run_full_cycle() -> None:
    """One cycle: check for needs, waiting between checks, until one is resolved.
    See docs/ARCHITECTURE.md#the-workflow-lifecycle.
    """
    waiting_since = None
    while True:
        # check_stop() only, not check_running() - clicking [LOOP]
        # in this Python GUI is what has focus at this exact instant, and
        # process_needs() below is what brings Roblox to the front. Once
        # that succeeds, every wait from here on does check_running().
        check_stop()
        run_stats.cycles += 1
        check_quiet()
        flush_idle_checks_if_due()
        write_status()
        unscrew()
        side_quest()
        if process_needs():
            return
        respawn_if_no_detection()
        waiting_since = waiting_since or time.time()
        deadline = time.time() + cfg.NEED_CHECK_RETRY_DELAY
        while time.time() < deadline:
            logger.info(f"Waiting for a need... {format_duration(time.time() - waiting_since)}", extra={"status": True})      # (a line the console overwrites)
            wait_interruptible(min(1.0, deadline - time.time()))

def run_workflow_loop() -> None:
    """Respawn once, then repeat the workflow until a stop request, which propagates (bare
    finally, not except) so the GUI reports [STOPPED]. See docs/ARCHITECTURE.md#the-workflow-lifecycle.
    """                                                                   # (STOP_EVENT is not cleared here: run_async() did that before this thread began, and a stop since then must not be lost)
    state.run_number = next_run_number()
    reset_need_tracking()
    state.idle_checks = 0
    state.run_start_pending = True
    reset_run_stats()
    state.last_rejoin = state.last_progress = state.last_detection = time.time()  # the scheduled and no-progress rejoins count from here
    state.white_since = None
    log_run_event("LOOP started")
    recorder = start_recording_if_requested()
    logger.info("=" * 50)
    logger.info(f"[LOOP] Starting continuous workflow (run {state.run_number})")
    logger.info("=" * 50)

    # Respawn once up front so the loop always starts from a known state,
    # regardless of wherever the character happened to be standing.
    needs_respawn = True

    loop_num = 0
    focus_losses, progress_at_loss = 0, None
    monitor = ResourceMonitor()
    monitor.start()
    try:
        while True:
            loop_num += 1
            logger.info(f"[LOOP {loop_num}]")
            try:
                if needs_respawn:
                    respawn_character()
                    needs_respawn = False
                run_full_cycle()
                wait_interruptible(cfg.LOOP_DELAY)
            except FocusLost:
                if stop_pressed_soon():
                    raise StopRequested()
                # Only stops the run unless "Resume after focus loss" is
                # ticked - and even then gives up after
                # FOCUS_RESUME_MAX_IN_A_ROW losses with no need resolved in
                # between, so it can't fight you for the window forever.
                if not load_game_config()["resume_on_focus_loss"]:
                    raise
                if state.last_progress != progress_at_loss:
                    focus_losses = 0
                progress_at_loss = state.last_progress
                focus_losses += 1
                if focus_losses > cfg.FOCUS_RESUME_MAX_IN_A_ROW:
                    log_failure(f"Roblox lost focus {focus_losses} times in a row - giving up")
                    raise
                run_stats.focus_resumes += 1
                log_failure(f"Roblox lost focus - resuming ({focus_losses}/{cfg.FOCUS_RESUME_MAX_IN_A_ROW})")
                release_all_inputs()
                wait_stoppable(cfg.FOCUS_RESUME_DELAY)
                focus_roblox_click()
                needs_respawn = True  # where a half-finished handler left the character is unknown
    finally:
        monitor.stop()
        if recorder is not None:
            recorder.stop()
        flush_idle_checks()
        log_run_event("LOOP stopped")
        write_run_report(state.run_number)
        logger.info("[LOOP] Stopped")

# ============================================================================
# RUN REPORT
# ============================================================================
# Debug helper: turns one run of run_log.txt into a PNG report.
#
# The log is parsed by parse_run() (pure text, no imports from the macro) and
# drawn with OpenCV by draw_report(), so there's no dependency beyond what the
# macro already has. generate_run_report() does both and writes the file.
#
# The report has five panels:
#   1. needs resolved per unit of time - the unit is picked so the run fills
#      about TARGET_BUCKETS bars - stacked by basic / teleport / other need,
#      with failures (red) and rejoins (blue) marked along the bottom and each
#      need disabled as stuck drawn as a grey bar until the next rejoin;
#   2. needs detected per check, over time;
#   3. average seconds per resolved need, with how many there were;
#   4. counts of failures by kind, popups, minigames and the like;
#   5. CPU and memory use: the script's, and the whole PC's as a second line.

LINE_RE = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\] \[RUN ([^\]]+)\] (.*)$")
RESOLVED_RE = re.compile(r"^resolved: (\S+) \((\d+)s\)$")
DETECTED_RE = re.compile(r"^detected: (.*)$")
NONE_MATCHED_RE = re.compile(r"^none matched")
IDLE_CHECKS_RE = re.compile(r"^idle checks: (\d+) over (\d+)s$")
SAME_CHECKS_RE = re.compile(r"^same checks: (\d+) over (\d+)s$")
DISABLED_RE = re.compile(r"^disabled: (\S+)$")
RESOURCES_RE = re.compile(r"^resources: cpu ([\d.]+)% mem (\d+)MB(?: pc ([\d.]+)%(?: pcmem (\d+)%)?)?$")
RESULT_RE = re.compile(r"^minigame result: (.+?): (\d+) candy corn")
MINIGAME_RE = re.compile(r"^minigame (started|finished): (.*?)(?: \(\d+s\))?$")

TARGET_BUCKETS = 40
# Nice bucket lengths (seconds), shortest first.
BUCKET_STEPS = [10, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 14400]

# BGR
COLOR_BASIC = (200, 140, 40)
COLOR_TELEPORT = (60, 160, 60)
COLOR_OTHER = (160, 100, 190)
COLOR_FAILURE = (60, 60, 220)
COLOR_REJOIN = (220, 120, 40)
COLOR_DISABLED = (90, 90, 90)
COLOR_PC = (30, 140, 230)
COLOR_TEXT = (40, 40, 40)
COLOR_GRID = (225, 225, 225)
COLOR_BG = (255, 255, 255)
COLOR_LINE = (150, 150, 150)
COLOR_MEAN = (40, 40, 200)
FONT = cv2.FONT_HERSHEY_SIMPLEX


def parse_ts(text: str) -> float:
    return time.mktime(time.strptime(text, "%Y-%m-%d %H:%M:%S"))


def parse_run(log_path: str, run: Union[int, str]) -> Optional[dict[str, Any]]:
    """Everything the report needs from run `run` of the log, as a dict, or None if the run
    isn't in it (keys: start, end, resolved, detected, failures, rejoins, disabled, results,
    resources, events). "same checks: N" lines stand for N more of the detection above.
    See docs/ARCHITECTURE.md#debugging-an-unattended-run.
    """
    run = str(run)
    lines = []
    if not os.path.exists(log_path):
        return None
    with open(log_path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            m = LINE_RE.match(raw.rstrip("\n"))
            if m and m.group(2) == run:
                lines.append((parse_ts(m.group(1)), m.group(3)))
    if not lines:
        return None
    data: dict[str, Any] = {"start": lines[0][0], "end": lines[-1][0], "resolved": [], "detected": [],
            "failures": [], "rejoins": [], "disabled": [], "resources": [], "results": [], "events": Counter()}
    for t, text in lines:
        if m := RESOLVED_RE.match(text):
            data["resolved"].append((t, m.group(1), int(m.group(2))))
        elif m := DETECTED_RE.match(text):
            body = m.group(1)
            if NONE_MATCHED_RE.match(body):
                icons = re.search(r"\((\d+) icon", body)
                data["detected"].append((t, 0, int(icons.group(1)) if icons else 0))
            else:
                names = [n for n in body.split(", ") if n]
                data["detected"].append((t, len(names), len(names)))
        elif m := IDLE_CHECKS_RE.match(text):
            count, span = int(m.group(1)), int(m.group(2))     # `count` checks that found nothing, spread over the `span` s before this line
            data["detected"] += [(t - span + span * (k + 0.5) / count, 0, 0) for k in range(count)]
        elif m := SAME_CHECKS_RE.match(text):
            count, span = int(m.group(1)), int(m.group(2))
            if data["detected"]:
                _, matched, icons = data["detected"][-1]
                data["detected"] += [(t - span + span * (k + 0.5) / count, matched, icons) for k in range(count)]
        elif m := DISABLED_RE.match(text):
            data["disabled"].append((t, m.group(1)))
        elif m := RESOURCES_RE.match(text):
            data["resources"].append((t, float(m.group(1)), int(m.group(2)),
                                      None if m.group(3) is None else float(m.group(3)),
                                      None if m.group(4) is None else int(m.group(4))))
        elif text.startswith("no need detected for "):
            data["events"]["idle respawns"] += 1
        elif text.startswith("quiet: "):
            data["events"]["quiet periods logged"] += 1
        elif text.startswith("FAILURE: "):
            data["failures"].append((t, text[len("FAILURE: "):]))
        elif text.startswith("white screen: "):
            data["events"]["white screens (rejoined)"] += 1
        elif m := RESULT_RE.match(text):
            data["results"].append((t, m.group(1), int(m.group(2))))
        elif text.startswith("recovery complete"):   # once per rejoin ("scheduled rejoin" is logged at its start too)
            data["rejoins"].append(t)
            data["events"]["rejoins"] += 1
        elif text.startswith("not cleared: "):
            data["events"]["needs not cleared"] += 1
        elif text.startswith("paycheck popup"):
            data["events"]["paycheck popups"] += 1
        elif text.startswith("stray window closed"):
            data["events"]["stray windows closed"] += 1
        elif text.startswith("backpack was left open"):
            data["events"]["backpacks closed"] += 1
        elif text.startswith("minigame popup dismissed"):
            data["events"]["minigame popups declined"] += 1
        elif m := MINIGAME_RE.match(text):
            if m.group(1) == "finished":
                data["events"]["minigames finished"] += 1
        elif " interrupted by " in text:
            data["events"]["needs interrupted"] += 1
    return data


def failure_kind(text: str) -> str:
    """A failure's kind: its first two words, digits dropped."""
    words = re.sub(r"[\d.]+s?", "", text).split()
    return " ".join(words[:2]).lower() or "failure"


def pick_bucket(duration: float) -> int:
    """The shortest of BUCKET_STEPS that makes at most TARGET_BUCKETS bars."""
    for step in BUCKET_STEPS:
        if duration / step <= TARGET_BUCKETS:
            return step
    return BUCKET_STEPS[-1]


def unit_label(seconds: int) -> str:
    if seconds % 3600 == 0:
        return f"{seconds // 3600} h"
    if seconds % 60 == 0:
        return f"{seconds // 60} min"
    return f"{seconds} s"


def duration_label(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m:02d}m" if h else (f"{m}m {s:02d}s" if m else f"{s}s")


def need_group(need: str, basic: set[str], teleport: set[str]) -> str:
    if need in basic:
        return "basic"
    if need in teleport:
        return "teleport"
    return "other"


GROUP_COLORS = {"basic": COLOR_BASIC, "teleport": COLOR_TELEPORT, "other": COLOR_OTHER}


# --- drawing ---------------------------------------------------------------

def draw_text(img: Image, s: str, x: float, y: float, scale: float = 0.5, color: Color = COLOR_TEXT, thick: int = 1, anchor: str = "left") -> None:
    (w, h), _ = cv2.getTextSize(s, FONT, scale, thick)
    if anchor == "right":
        x -= w
    elif anchor == "center":
        x -= w // 2
    cv2.putText(img, s, (int(x), int(y)), FONT, scale, color, thick, cv2.LINE_AA)


def nice_max(value: float) -> float:
    """Round `value` up to a tidy axis maximum (1, 2, 5 x 10^n)."""
    if value <= 0:
        return 1
    exp = 10 ** int(np.floor(np.log10(value)))
    for mult in (1, 2, 5, 10):
        if value <= mult * exp:
            return mult * exp
    return 10 * exp


class Panel:
    """A plot area on `img`: maps data (x in 0..x_max, y in 0..y_max) to pixels
    and draws the frame, grid and tick labels."""

    def __init__(self, img: Image, box: Box, title: str, x_max: float, y_max: float, x_ticks: Optional[Sequence[tuple[float, str]]] = None, y_label: str = "") -> None:
        self.img, self.box, self.x_max, self.y_max = img, box, x_max, y_max
        left, top, right, bottom = box
        self.plot = (left + 55, top + 30, right - 15, bottom - 38)
        draw_text(img, title, left, top + 14, 0.6, thick=2)
        pl, pt, pr, pb = self.plot
        cv2.rectangle(img, (pl, pt), (pr, pb), COLOR_LINE, 1)
        y_step: float = max(1, nice_max(y_max) // 5) if y_max >= 5 else (1 if y_max > 1 else 0.5)
        value: float = 0
        while value <= y_max + 1e-9:
            y = self.y(value)
            cv2.line(img, (pl, y), (pr, y), COLOR_GRID, 1)
            draw_text(img, f"{value:g}", pl - 6, y + 4, 0.4, anchor="right")
            value += y_step
        for x_value, label in (x_ticks or []):
            x = self.x(x_value)
            cv2.line(img, (x, pt), (x, pb), COLOR_GRID, 1)
            draw_text(img, label, x, pb + 16, 0.4, anchor="center")
        if y_label:
            draw_text(img, y_label, left, pb + 32, 0.45)

    def x(self, value: float) -> int:
        pl, _, pr, _ = self.plot
        return int(pl + (pr - pl) * value / self.x_max)

    def y(self, value: float) -> int:
        _, pt, _, pb = self.plot
        return int(pb - (pb - pt) * value / self.y_max)


def time_ticks(duration: float, unit: int = 1, max_ticks: int = 8) -> list[tuple[int, str]]:
    """Axis ticks at whole multiples of `unit` seconds (a bucket length, so
    ticks fall on bar edges) - few enough to stay readable, on a nice step
    from BUCKET_STEPS."""
    nice = next((st for st in BUCKET_STEPS if duration / st <= max_ticks), BUCKET_STEPS[-1])
    step = unit * max(1, int(np.ceil(nice / unit)))
    return [(t, duration_label(t)) for t in range(0, int(duration) + 1, step)]


def draw_report(data: dict[str, Any], run: Union[int, str], basic: set[str], teleport: set[str]) -> Image:
    """The report image (BGR numpy array) for parse_run()'s `data`."""
    width, height = 1600, 1660
    img = np.full((height, width, 3), COLOR_BG, np.uint8)
    duration = max(1.0, data["end"] - data["start"])
    start = data["start"]
    started = time.strftime("%Y-%m-%d %H:%M", time.localtime(start))
    total = len(data["resolved"])
    draw_text(img, f"Run {run} - {started}, {duration_label(duration)}: {total} needs resolved, "
              f"{len(data['failures'])} failures, {len(data['rejoins'])} rejoins",
         20, 30, 0.8, thick=2)

    # 1. resolved per bucket, stacked
    step = pick_bucket(duration)
    n_buckets = max(1, int(np.ceil(duration / step)))
    stacks: list[defaultdict[str, int]] = [defaultdict(int) for _ in range(n_buckets)]
    for t, need, _ in data["resolved"]:
        stacks[max(0, min(n_buckets - 1, int((t - start) // step)))][need_group(need, basic, teleport)] += 1
    peak = max([sum(s.values()) for s in stacks] + [1])
    y_max = nice_max(peak)
    panel = Panel(img, (20, 50, width - 20, 560), f"Needs resolved per {unit_label(step)}",
                  n_buckets * step, y_max, time_ticks(n_buckets * step, step))
    for i, s in enumerate(stacks):
        base = 0
        for group in ("basic", "teleport", "other"):
            if s[group]:
                x0, x1 = panel.x(i * step) + 1, panel.x((i + 1) * step) - 1
                cv2.rectangle(img, (x0, panel.y(base + s[group])), (x1, panel.y(base)), GROUP_COLORS[group], -1)
                base += s[group]
    pl, pt, pr, pb = panel.plot
    for t, _ in data["failures"]:
        x = panel.x(t - start)
        cv2.line(img, (x, pb), (x, pb - 14), COLOR_FAILURE, 2)
    for t in data["rejoins"]:
        x = panel.x(t - start)
        cv2.line(img, (x, pt), (x, pb), COLOR_REJOIN, 1)
    # A need that was disabled (stuck) stays off until the next rejoin: one bar per need, from then until the
    # rejoin (or the run's end), named above it, stacked above the failure ticks.
    for row, (t, need) in enumerate(data["disabled"]):
        end = next((r for r in sorted(data["rejoins"]) if r > t), start + duration)
        y = pb - 24 - 14 * (row % 12)
        cv2.line(img, (panel.x(t - start), y), (panel.x(end - start), y), COLOR_DISABLED, 5)
        draw_text(img, need, panel.x(t - start), y - 5, 0.38, COLOR_DISABLED)
    legend_x = pl + 10
    for label, color in (("basic", COLOR_BASIC), ("teleport", COLOR_TELEPORT), ("other", COLOR_OTHER),
                         ("failure", COLOR_FAILURE), ("rejoin", COLOR_REJOIN), ("disabled", COLOR_DISABLED)):
        cv2.rectangle(img, (legend_x, pt + 8), (legend_x + 12, pt + 20), color, -1)
        draw_text(img, label, legend_x + 18, pt + 19, 0.45)
        legend_x += 95

    # 2. detected per check
    checks = data["detected"]
    det_max = nice_max(max([c[1] for c in checks] + [1]))
    panel2 = Panel(img, (20, 570, 800, 1080), "Needs detected per check", duration, det_max,
                   time_ticks(duration))
    if checks:
        points = np.array([[panel2.x(t - start), panel2.y(matched)] for t, matched, _ in checks], np.int32)
        cv2.polylines(img, [points], False, COLOR_LINE, 1, cv2.LINE_AA)
        # mean per bucket
        for i in range(n_buckets):
            vals = [m for t, m, _ in checks if int((t - start) // step) == i]
            if vals:
                cv2.line(img, (panel2.x(i * step), panel2.y(np.mean(vals))),
                         (panel2.x(min((i + 1) * step, duration)), panel2.y(np.mean(vals))), COLOR_MEAN, 3)   # (the last bucket ends with the run)
        none = sum(1 for c in checks if c[1] == 0)
        note = f"grey: each of {len(checks)} checks; red: mean per {unit_label(step)}; {none} matched nothing"
        draw_text(img, note, panel2.plot[0] + 190, panel2.plot[1] - 16, 0.4)

    # 3. average seconds per need
    per_need = defaultdict(list)
    for _, need, seconds in data["resolved"]:
        per_need[need].append(seconds)
    rows = sorted(per_need.items(), key=lambda kv: -np.mean(kv[1]))
    left, top, right, bottom = 820, 570, width - 20, 810
    draw_text(img, "Average seconds per resolved need (n)", left, top + 14, 0.6, thick=2)
    if rows:
        row_h = min(26, (bottom - top - 30) // max(1, len(rows)))
        longest = max(np.mean(v) for _, v in rows) or 1
        for i, (need, secs) in enumerate(rows):
            y = top + 34 + i * row_h
            bar = int((right - left - 270) * np.mean(secs) / longest)
            cv2.rectangle(img, (left + 90, y), (left + 90 + max(2, bar), y + row_h - 6),
                          GROUP_COLORS[need_group(need, basic, teleport)], -1)
            draw_text(img, need, left, y + row_h - 8, 0.45)
            draw_text(img, f"{np.mean(secs):.0f}s (n={len(secs)})", left + 98 + bar, y + row_h - 8, 0.45)
    else:
        draw_text(img, "nothing resolved", left, top + 44, 0.5)

    # 4. counts (the minigames' best, worst and average candy corn first)
    left, top = 820, 830
    draw_text(img, "Failures and events", left, top, 0.6, thick=2)
    y = top + 24
    by_game: dict[str, list[int]] = defaultdict(list)
    for _, game, earned in data["results"]:
        by_game[game].append(earned)
    for game, earned_list in sorted(by_game.items()):
        draw_text(img, f"{game}: best {max(earned_list)}, worst {min(earned_list)}, average {sum(earned_list) / len(earned_list):.0f} candy corn ({len(earned_list)} runs)", left, y, 0.5)
        y += 22
    kinds = Counter(failure_kind(t) for _, t in data["failures"])
    for kind, count in kinds.most_common(5):
        draw_text(img, f"{count:>4}  failure: {kind}", left, y, 0.5, COLOR_FAILURE)
        y += 22
    for label, count in data["events"].most_common():
        if y > 1085:
            break
        draw_text(img, f"{count:>4}  {label}", left, y, 0.5)
        y += 22

    # 5. the script's own CPU and memory use - and the whole PC's, as a second line: CPU on the same % scale, memory
    # in % on its own scale (labelled down the right-hand edge)
    for box, title, column, color, unit, pc_column in (
            ((20, 1090, 800, 1650), "CPU use (% of the machine)", 1, COLOR_BASIC, "%", 3),
            ((820, 1090, width - 20, 1650), "Memory (script MB; PC % in use)", 2, COLOR_TELEPORT, "MB", 4)):
        values = [r[column] for r in data["resources"]]
        pc_rows = [r for r in data["resources"] if r[pc_column] is not None]
        pc_own_scale = pc_column == 4
        top_value = nice_max(max(values + ([] if pc_own_scale else [r[pc_column] for r in pc_rows]) + [1]))
        panel = Panel(img, box, title, duration, top_value, time_ticks(duration))
        if values:
            points = np.array([[panel.x(r[0] - start), panel.y(r[column])] for r in data["resources"]], np.int32)
            cv2.polylines(img, [points], False, color, 2, cv2.LINE_AA)
            note = f"script: mean {np.mean(values):.1f}{unit}, max {max(values):.1f}{unit}"
            if pc_rows:
                pc_values = [r[pc_column] for r in pc_rows]
                scale = top_value / 100 if pc_own_scale else 1
                points = np.array([[panel.x(r[0] - start), panel.y(r[pc_column] * scale)] for r in pc_rows], np.int32)
                cv2.polylines(img, [points], False, COLOR_PC, 2, cv2.LINE_AA)
                note += f"   |   PC: mean {np.mean(pc_values):.0f}%, max {max(pc_values):.0f}%"
                if pc_own_scale:
                    for pct in (0, 50, 100):
                        draw_text(img, f"{pct}%", panel.plot[2] - 4, panel.y(pct * scale) + 4, 0.4, COLOR_PC, anchor="right")
            draw_text(img, note, panel.plot[0] + 10, panel.plot[1] + 16, 0.45)
            if pc_rows:
                cv2.line(img, (panel.plot[0] + 10, panel.plot[1] + 30), (panel.plot[0] + 30, panel.plot[1] + 30), color, 3)
                draw_text(img, "script", panel.plot[0] + 36, panel.plot[1] + 34, 0.4)
                cv2.line(img, (panel.plot[0] + 100, panel.plot[1] + 30), (panel.plot[0] + 120, panel.plot[1] + 30), COLOR_PC, 3)
                draw_text(img, "whole PC", panel.plot[0] + 126, panel.plot[1] + 34, 0.4)
        else:
            draw_text(img, "no samples (run too short, or an older log)", panel.plot[0] + 10, panel.plot[1] + 20, 0.45)
    return img


def generate_run_report(log_path: str, run: Union[int, str], out_dir: str, basic: Iterable[str] = (), teleport: Iterable[str] = ()) -> Optional[str]:
    """Write run `run`'s report to <out_dir>/run<run>.png and return its path,
    or None if the run isn't in the log. Never raises: a report problem must
    not take a run down with it (returns None, having printed why)."""
    try:
        data = parse_run(log_path, run)
        if data is None:
            return None
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"run{run}.png")
        if not cv2.imwrite(path, draw_report(data, run, set(basic), set(teleport))):
            logger.warning(f"Couldn't write the run report to {path}")
            return None
        return path
    except Exception as error:
        logger.warning(f"Couldn't write the run report: {error}")
        return None

# ============================================================================
# GUI
# ============================================================================

def keep_newest_files(directory: str, keep: int, suffix: str = "") -> None:
    """Delete the oldest files in `directory` (only those ending in `suffix`) beyond the newest `keep`. Never raises. (A pruned run
    report can be drawn again from the logs, which are kept - see generate_run_report().)"""
    try:
        files = sorted((os.path.join(directory, f) for f in os.listdir(directory) if f.endswith(suffix)), key=os.path.getmtime)
        for old in files[:max(0, len(files) - keep)]:
            if os.path.isfile(old):
                os.remove(old)
    except OSError:
        pass

def write_run_report(run: Union[int, str]) -> Optional[str]:
    """Chart run `run` from the log into RUNS_DIR, beside the run's own log (see RUN REPORT above) and print
    where it went. Returns the path, or None. Never raises."""
    path = generate_run_report(cfg.RUN_LOG_PATH, run, cfg.RUNS_DIR, cfg.BUTTON_NAMES, cfg.TELEPORT_WALK_NEEDS)
    if path:
        logger.debug(f"Run report: {path}")
        keep_newest_files(cfg.RUNS_DIR, cfg.MAX_REPORTS, ".png")
    return path

def run_all_need_handlers(handlers: Optional[dict[str, Callable[[], NeedHandler]]] = None) -> dict[str, str]:
    """Run need handlers once each (the Debug tab's "test every teleport handler": TELEPORT_HANDLERS, or any given), respawning after
    each, minigame popups declined. Returns {need: "done" | "failed" | "interrupted by ..."}.
    See docs/ARCHITECTURE.md#gui-internals.
    """
    handlers = TELEPORT_HANDLERS if handlers is None else handlers
    results: dict[str, str] = {}
    logger.info(f"[TEST] Running all {len(handlers)} need handlers, one after another...")
    for i, (name, handler_cls) in enumerate(handlers.items(), 1):
        check_stop()
        logger.info(f"[TEST] ({i}/{len(handlers)}) {name}...")
        try:
            with task_unscrew(play_minigames=False):    # a minigame popup is declined, not played
                results[name] = "done" if handler_cls().handle() else "failed"
        except TaskInterrupted as interruption:
            results[name] = f"interrupted by {interruption.reason}"
        logger.info(f"[TEST] {name}: {results[name]}")
        respawn_character()
    logger.info("[TEST] Summary: " + ", ".join(f"{name} {result}" for name, result in results.items()))
    return results

class DebugCapture(logging.Handler):
    """The GUI's console as a log handler (added to `logger` while the GUI is up): each record is queued and put on the widget on Tk's
    thread, with the time, in the colour of its level (GUI_CONSOLE_LEVEL_COLORS); a status record ("Waiting for a need...") replaces
    the status line before it. The level can be raised to hide the debug lines (setLevel). See docs/ARCHITECTURE.md#gui-internals.
    """

    def __init__(self, root: tk.Tk, text_widget: tk.Text) -> None:
        super().__init__()
        self.root = root
        self.text = text_widget
        self.queue: queue.Queue[tuple[str, str, bool]] = queue.Queue()
        self.at_line_start = True        # drain() side: the widget's state
        self.status_active = False
        self.setFormatter(logging.Formatter("%(message)s"))
        for level, color in cfg.GUI_CONSOLE_LEVEL_COLORS.items():
            text_widget.tag_config(level, foreground=color)
        self.drain()

    def emit(self, record: logging.LogRecord) -> None:
        self.queue.put((record.levelname, self.format(record), bool(getattr(record, "status", False))))

    def drain(self) -> None:
        """Put every queued message on the widget, then schedule the next drain."""
        try:
            batch = []
            while True:
                try:
                    batch.append(self.queue.get_nowait())
                except queue.Empty:
                    break
            if batch:
                self.text.config(state=tk.NORMAL)
                for level, msg, status in batch:
                    self.render(level, msg, status)
                self.trim()
                self.text.see(tk.END)
                self.text.config(state=tk.DISABLED)
            self.root.after(cfg.CONSOLE_DRAIN_INTERVAL_MS, self.drain)
        except tk.TclError:
            pass   # the window is gone

    def trim(self) -> None:
        """Keep the widget to its last CONSOLE_MAX_LINES lines (Tk thread only): it holds everything logged
        otherwise, and a run left going for days is a lot of text. output.log on disk keeps all of it."""
        lines = int(self.text.index("end-1c").split(".")[0])
        if lines - 1 > cfg.CONSOLE_MAX_LINES:   # (the last "line" is the empty one after the final newline)
            self.text.delete("1.0", f"{lines - cfg.CONSOLE_MAX_LINES}.0")

    def render(self, level: str, msg: str, status: bool) -> None:
        """Add one record to the widget (Tk thread only)."""
        line = time.strftime("[%H:%M:%S] ") + msg
        if status:                      # a status line has no newline yet: the next one replaces it, anything else ends it
            if self.status_active:
                self.text.delete("end-1c linestart", "end-1c")
            elif not self.at_line_start:
                self.text.insert(tk.END, "\n")
            self.text.insert(tk.END, line, level)
            self.status_active = True
            self.at_line_start = False
            return
        if self.status_active or not self.at_line_start:
            self.text.insert(tk.END, "\n")
        self.status_active = False
        self.text.insert(tk.END, line + "\n", level)
        self.at_line_start = True

OUTSIDE_ACTIONS: dict[str, Callable[[], Any]] = {"rejoin": leave_and_rejoin, "setup": setup_game, "trade_hub": travel_to_trade_hub, "leave_trade_hub": leave_trade_hub}   # the commands that do what a button does (AdoptMeGUI.act_from_outside())

def blackout_seen_by_capture(before: Image, after: Image) -> Optional[bool]:
    """Whether the macro's screenshots see the window of the blackout test: `before` is a screenshot without it, `after` one with it up, white. Seen if
    the screen got BLACKOUT_SEEN_RISE grey levels brighter; None - cannot tell - if the screen was brighter than BLACKOUT_TOO_BRIGHT to begin with."""
    mean_before, mean_after = float(before.mean()), float(after.mean())
    if mean_before > cfg.BLACKOUT_TOO_BRIGHT:
        return None
    return mean_after - mean_before > cfg.BLACKOUT_SEEN_RISE

class Blackout:
    """The screen blanked by a black window that is click-through, never takes the focus, and is left out of screenshots (SetWindowDisplayAffinity with
    WDA_EXCLUDEFROMCAPTURE, Windows 10 version 2004 and later): the person sees black, while the macro's screenshots, recordings and the Discord bot's
    pictures still see the game, and its clicks and keys go to it. Turned on only after a test with the window white that the screenshots really do not see
    it (blackout_seen_by_capture()); if they do, or it cannot be told, it is taken away again. Tk's thread only. See docs/ARCHITECTURE.md#blanking-the-screen."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.window: Optional[tk.Toplevel] = None
        self.job: Optional[str] = None                 # the pending keep_on_top()

    @property
    def shown(self) -> bool:
        return self.window is not None

    def toggle(self) -> bool:
        """Turn it on or off; True if the screen is blank afterwards."""
        if self.shown:
            self.hide()
            return False
        return self.show()

    def supported(self) -> bool:
        return os.name == "nt"

    def show(self) -> bool:
        if not self.supported():
            logger.warning("Blanking the screen only works on Windows")
            return False
        before = grab_screen()
        window = self.make_window()
        if window is None:
            return False
        self.window = window
        self.root.update()
        time.sleep(cfg.BLACKOUT_SETTLE)
        seen = blackout_seen_by_capture(before, grab_screen())
        if seen is not False:
            self.hide()
            logger.warning("The screen was not blanked: " + ("the macro's screenshots would see the black window too" if seen else "the screen is too bright to test whether they would (try again from a darker view)"))
            return False
        window.configure(bg="black")
        logger.info(f"Screen blanked ({cfg.HOTKEYS['blackout'].upper()} again to show it)")
        self.keep_on_top()
        return True

    def keep_on_top(self) -> None:
        """While the screen is blank, put the black window back above everything every BLACKOUT_RAISE_INTERVAL_MS: the control window, coming back up from the
        taskbar, lands above it, and when exactly is for Windows to say, so no single raise is soon or late enough."""
        self.job = None
        if self.window is None:
            return
        self.raise_over()
        self.job = self.root.after(cfg.BLACKOUT_RAISE_INTERVAL_MS, self.keep_on_top)

    def raise_over(self) -> None:
        """Put the black window at the top of the always-on-top windows, without activating it."""
        if self.window is None or not self.supported():
            return
        try:
            user32 = ctypes.windll.user32  # type: ignore[attr-defined]  # Windows only
            hwnd = user32.GetParent(self.window.winfo_id()) or self.window.winfo_id()
            topmost = ctypes.c_void_p(-1)                                            # HWND_TOPMOST (a handle is as wide as a pointer: a plain -1 would be passed as 32 bits and not be the value)
            if not user32.SetWindowPos(ctypes.c_void_p(hwnd), topmost, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010):     # SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
                logger.debug("Putting the black window back on top failed")
        except (tk.TclError, AttributeError, OSError):
            pass

    def hide(self) -> None:
        if self.job is not None:
            try:
                self.root.after_cancel(self.job)
            except tk.TclError:
                pass
            self.job = None
        if self.window is not None:
            try:
                self.window.destroy()
            except tk.TclError:
                pass
            self.window = None

    def make_window(self) -> Optional[tk.Toplevel]:
        """A white full-screen topmost window with the styles above, or None (and a warning) if Windows refuses to leave it out of screenshots."""
        window = tk.Toplevel(self.root)
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.configure(bg="white", cursor="none")
        window.geometry(f"{self.root.winfo_screenwidth()}x{self.root.winfo_screenheight()}+0+0")
        window.update_idletasks()
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]  # Windows only
        hwnd = user32.GetParent(window.winfo_id()) or window.winfo_id()
        style = user32.GetWindowLongW(hwnd, -20)                                       # GWL_EXSTYLE
        user32.SetWindowLongW(hwnd, -20, style | 0x00080000 | 0x00000020 | 0x08000000 | 0x00000080)    # layered, transparent (click-through), no-activate, tool window
        user32.SetLayeredWindowAttributes(hwnd, 0, 255, 2)                             # LWA_ALPHA
        if not user32.SetWindowDisplayAffinity(hwnd, 0x00000011):                      # WDA_EXCLUDEFROMCAPTURE
            window.destroy()
            logger.warning("The screen was not blanked: this Windows cannot leave a window out of screenshots (it needs Windows 10 version 2004 or later)")
            return None
        return window

class AdoptMeGUI:
    def __init__(self, root: tk.Tk) -> None:
        state.macro_window = self
        self.root = root
        self.root.title("Adopt Me Macro")
        self.root.geometry(cfg.GUI_GEOMETRY)
        self.root.resizable(False, False)
        self.root.attributes('-topmost', True)
        self.root.attributes('-alpha', cfg.GUI_ALPHA)

        self.bg = cfg.GUI_BG
        self.accent = cfg.GUI_ACCENT
        self.fg = cfg.GUI_FG
        self.root.configure(bg=self.bg)

        self.create_ui()

        self.capture = DebugCapture(self.root, self.debug_text)
        logger.addHandler(self.capture)

        logger.info("=" * 60)
        logger.info("ADOPT ME MACRO - CONTROL PANEL")
        logger.info("=" * 60)
        logger.info(f"Needs:  {cfg.NEEDS_DIR}")
        logger.info(f"Debug:  {cfg.DEBUG_DIR}")
        logger.info("=" * 60)

        self.is_running = False
        self.pending: list[str] = []             # actions asked for from outside (OUTSIDE_ACTIONS), in order, waiting for what runs to end
        self.pending_chain = False               # drain_pending() is scheduled
        self.pending_waits = 0                   # looks so far for a loop that was told to stop to end
        self.running_loop = False                # what run_async() runs is the loop
        self.blackout = Blackout(self.root)
        self.watch_stop_hotkey()
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def start_remote_control(self, loop: bool) -> None:
        """Begin looking for commands in COMMANDS_DIR every COMMAND_POLL_MS (see poll_commands()), and with `loop` (the --loop argument) start the
        loop once the window is up, as if the loop button was pressed; without it the status file says that nothing is running. Commands left from before are dropped first."""
        discard_commands()
        self.root.after(cfg.COMMAND_POLL_MS, self.poll_commands)
        if loop:
            self.root.after(cfg.START_LOOP_DELAY_MS, self.start_loop_from_outside)
        else:
            mark_stopped()                                       # a window up with no loop going: the status of an earlier run is not one that is due an update

    def start_loop_from_outside(self) -> None:
        """Start the loop, as the loop button does (`start` command, or the --loop argument)."""
        if self.is_running:
            logger.info("Start asked for, but the macro is already running")
            return
        log_run_event("started from outside (the Discord bot)")
        self.run_async(run_workflow_loop)

    def poll_commands(self) -> None:
        """Do the oldest command waiting in COMMANDS_DIR, if any (Tk's thread): `start` the loop, `stop` it (the cooperative stop, same as [STOP]), `exit`
        (stop, release every key and the mouse, and close the window - what the X button does) `set` a switch (set_switch()), `rejoin`, `setup`, `trade_hub` or `leave_trade_hub` (act_from_outside()), or `refresh_debug`. Looks again
        COMMAND_POLL_MS later - scheduled before the command is done, and a command that raises is logged and does not stop the polling."""
        data = read_command()
        command = data["command"] if data else None
        if command == "exit":
            logger.info("Command from outside: exit")
            self.close()
            return
        self.root.after(cfg.COMMAND_POLL_MS, self.poll_commands)
        if command is None:
            return
        logger.info(f"Command from outside: {command}")
        try:
            self.do_command(command, data)
        except Exception:
            logger.exception(f"The command from outside ({command}) failed")

    def do_command(self, command: str, data: Optional[dict[str, Any]]) -> None:
        """What poll_commands() does for one command (not `exit`)."""
        if command == "start":
            self.start_loop_from_outside()
        elif command == "stop":
            if self.is_running:
                log_run_event("stopped from outside (the Discord bot)")
            self.stop()
        elif command == "set" and data:
            self.set_switch(data["kind"], data["name"], data["value"])
        elif command in OUTSIDE_ACTIONS:
            self.act_from_outside(command)
        elif command == "refresh_debug":
            self.refresh_debug_from_outside()

    def act_from_outside(self, command: str) -> None:
        """Queue what the button of the same name does (OUTSIDE_ACTIONS: `rejoin` is Leave & rejoin, `setup` is Setup, and the Trading Hub trips). Actions are
        done one after the other, in the order asked for (drain_pending()); a `stop` command stops what is running now and leaves the queue alone."""
        self.pending.append(command)
        if not self.pending_chain:
            self.pending_chain, self.pending_waits = True, 0
            self.drain_pending()

    def drain_pending(self) -> None:
        """Do the first queued action once nothing is running, looking again every REJOIN_STOP_POLL_MS. A running loop is told to stop (once) and is waited for
        up to REJOIN_STOP_WAIT_MS, after which the action is given up; another action is waited for as long as it takes."""
        if not self.pending:
            self.pending_chain = False
            return
        if self.is_running:
            if self.running_loop:
                if self.pending_waits == 0:
                    self.stop()
                self.pending_waits += 1
                if self.pending_waits * cfg.REJOIN_STOP_POLL_MS >= cfg.REJOIN_STOP_WAIT_MS:
                    logger.warning(f"{self.pending.pop(0)} asked for, but the running loop did not stop")
                    self.pending_waits = 0
        else:
            self.pending_waits = 0
            command = self.pending.pop(0)
            log_run_event(f"{command} asked for from outside (the Discord bot)")
            self.run_async(OUTSIDE_ACTIONS[command])
        self.root.after(cfg.REJOIN_STOP_POLL_MS, self.drain_pending)

    def refresh_debug_from_outside(self) -> None:
        """Empty the debug folder as the Refresh debug button does, without asking; not while something is running, which is writing there."""
        if self.is_running or state.capture is not None or state.run_capture is not None:
            logger.warning("Refresh debug asked for, but the macro is running or recording - stop it first")
            return
        logger.debug(f"Debug folder refreshed ({refresh_debug()} entries removed)")
        mark_stopped()                                           # (the status file went with the rest)

    def set_switch(self, kind: str, name: str, value: bool) -> None:
        """Change a switch as its checkbox does - persist it and tick or untick the checkbox in the window: a need's (the Debug tab), or an option (the
        Settings tab, the Debug tab's Options subtab, and `show_debug_lines`, which is only a checkbox)."""
        if kind == "need":
            self.set_need_enabled(name, value)
            self.need_vars[name].set(value)
        elif name == cfg.SHOW_DEBUG_LINES_OPTION:
            self.show_debug_var.set(value)
            self.set_debug_lines_shown()
        else:
            key = cfg.OPTION_SWITCHES[name]
            self.set_config_flag(key, value)
            if key in self.config_flag_vars:
                self.config_flag_vars[key].set(value)

    def close(self) -> None:
        """The window's X button, and the `exit` command: end the macro cleanly. The stop is asked for first (a recording that takes seconds to finish
        must not be waited for while the worker still clicks and holds keys in the game), then the capture is closed, the status says nothing is running, the worker
        is given a moment to unwind and let go of what it holds (it is a daemon thread, which dies with the process before its own cleanup could run), every key
        and the mouse are released whatever it did, the blackout is taken away, and the window is destroyed."""
        STOP_EVENT.set()
        if state.capture is not None:
            state.capture.stop()
            state.capture = None
        mark_stopped()
        worker = getattr(self, "worker", None)
        if worker is not None and worker.is_alive():
            worker.join(cfg.CLOSE_WORKER_WAIT)
        else:
            time.sleep(cfg.STOP_CHECK_INTERVAL * 3)
        release_all_inputs()
        self.blackout.hide()
        self.root.destroy()

    def watch_stop_hotkey(self) -> None:
        """Start a thread that watches the hotkeys: HOTKEYS["stop"] stops the macro (in any window), HOTKEYS["capture"] starts or
        stops an input capture (only while Roblox is focused). See docs/ARCHITECTURE.md#gui-internals."""
        hotkeys = [(cfg.HOTKEYS["stop"], self.stop_by_hotkey, True, False),
                   (cfg.HOTKEYS["capture"], self.capture_by_hotkey, False, True),
                   (cfg.HOTKEYS["blackout"], self.blackout_by_hotkey, False, True)]   # (key, action, only while a run is going, only while Roblox is focused)

        def watch():
            was_down = [False] * len(hotkeys)
            reported = [False] * len(hotkeys)
            while True:
                for i, (key, action, only_while_running, only_in_roblox) in enumerate(hotkeys):
                    try:
                        down = hotkey_down(key)
                        if hotkey_fires(down, was_down[i], only_while_running, self.is_running, only_in_roblox):
                            self.root.after(0, action)
                        was_down[i] = down
                    except (RuntimeError, tk.TclError):
                        return  # the window is gone
                    except Exception:     # (a look at the focused window that failed: this key is tried again at the next poll, and STOP stays alive for as long as the window does)
                        if not reported[i]:
                            reported[i] = True
                            logger.exception(f"Watching the '{key}' hotkey failed (the watching goes on)")
                time.sleep(cfg.STOP_HOTKEY_POLL_INTERVAL)
        threading.Thread(target=watch, daemon=True).start()

    def capture_by_hotkey(self) -> None:
        """HOTKEYS["capture"] was pressed: start or stop the input capture (Tk's thread; the capture's own threads do the work)."""
        try:
            toggle_input_capture()
        except OSError as error:
            logger.warning(f"Input capture: {error}")

    def blackout_by_hotkey(self) -> None:
        """HOTKEYS["blackout"] was pressed: blank the screen or show it again (Tk's thread), unless the Options subtab's checkbox has the key off (it is
        then typed in the game)."""
        if not self.config_flag_vars["blackout_hotkey_enabled"].get():
            return
        try:
            self.blackout.toggle()
        except Exception:
            logger.exception("Blanking the screen failed")
            self.blackout.hide()

    def stop_by_hotkey(self) -> None:
        """HOTKEYS["stop"] was pressed: stop, same as the [STOP] button, and say so."""
        if self.is_running:
            logger.info(f"'{cfg.HOTKEYS['stop'].upper()}' pressed")
            self.stop()   # first: a failing log write must not keep the macro running
            try:
                log_run_event(f"stopped with the {cfg.HOTKEYS['stop'].upper()} key")
            except OSError:
                pass

    def create_ui(self) -> None:
        """Build every widget; buttons that start a background action are added to
        self.action_buttons ([STOP] deliberately isn't). See docs/ARCHITECTURE.md#gui-internals.
        """
        # Main buttons
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.X, padx=cfg.GUI_OUTER_PADDING, pady=(cfg.GUI_OUTER_PADDING, 0))
        btn_frame = tk.Frame(notebook, bg=self.bg)
        options_tab = tk.Frame(notebook, bg=self.bg)
        debug_tab = tk.Frame(notebook, bg=self.bg)
        notebook.add(btn_frame, text="Main")
        notebook.add(options_tab, text="Settings")
        notebook.add(debug_tab, text="Debug")

        self.action_buttons = []  # every button that starts a background task

        # One row: [LOOP] expanding to fill the left, [STOP] (square) on the right, each in a fixed-HEIGHT container
        # so the row stays aligned whatever the font (see docs/ARCHITECTURE.md#gui-internals).
        NO_BORDER: dict[str, Any] = dict(bd=0, highlightthickness=0)  # flat edges, no default Tk bevel/focus ring

        main_row = tk.Frame(btn_frame, bg=self.bg)
        main_row.pack(fill=tk.X, pady=cfg.GUI_ROW_SPACING)

        # Not added to action_buttons: must remain clickable while a workflow is running
        stop_container = tk.Frame(main_row, width=cfg.GUI_SQUARE_BUTTON_SIZE, height=cfg.GUI_SQUARE_BUTTON_SIZE, bg=self.bg)
        stop_container.pack(side=tk.RIGHT, padx=(cfg.GUI_ROW_SPACING, 0))
        stop_container.pack_propagate(False)

        self.btn_stop = tk.Button(stop_container, text="■", command=self.stop,
                                   font=(cfg.GUI_FONT, cfg.GUI_STOP_ICON_FONT_SIZE, "bold"), bg=cfg.GUI_STOP_COLOR, fg=self.fg,
                                   cursor="hand2", **NO_BORDER)
        self.btn_stop.pack(fill=tk.BOTH, expand=True)

        loop_container = tk.Frame(main_row, height=cfg.GUI_SQUARE_BUTTON_SIZE, bg=self.bg)
        loop_container.pack(side=tk.LEFT, fill=tk.X, expand=True)
        loop_container.pack_propagate(False)

        btn_loop = tk.Button(loop_container, text="\U0001F504", command=lambda: self.run_async(run_workflow_loop),
                              font=(cfg.GUI_FONT, cfg.GUI_LOOP_ICON_FONT_SIZE, "bold"), bg=cfg.GUI_LOOP_COLOR, fg=self.fg,
                              cursor="hand2", **NO_BORDER)
        btn_loop.pack(fill=tk.BOTH, expand=True)
        self.action_buttons.append(btn_loop)

        tk.Label(btn_frame, text=f"Press {cfg.HOTKEYS['stop'].upper()} anywhere to stop", font=(cfg.GUI_FONT, cfg.GUI_STATUS_FONT_SIZE),
                 bg=self.bg, fg=self.accent, anchor=tk.W).pack(fill=tk.X)

        tk.Frame(btn_frame, bg=self.accent, height=cfg.GUI_DIVIDER_HEIGHT).pack(fill=tk.X, pady=cfg.GUI_ROW_SPACING)

        tk.Label(btn_frame, text="Functions", font=(cfg.GUI_FONT, cfg.GUI_SECTION_FONT_SIZE, "bold"),
                 bg=self.bg, fg=self.accent).pack(anchor=tk.W)

        for label, action in (("Respawn", respawn_character), ("Setup", setup_game), ("Leave & rejoin", leave_and_rejoin),
                              ("Go to the Trading Hub", travel_to_trade_hub), ("Leave the Trading Hub", leave_trade_hub)):
            btn = tk.Button(btn_frame, text=label, command=lambda a=action: self.run_async(a),  # type: ignore[misc]
                            font=(cfg.GUI_FONT, cfg.GUI_SECTION_FONT_SIZE), bg=cfg.GUI_RESPAWN_COLOR, fg=self.fg,
                            height=cfg.GUI_RESPAWN_BUTTON_HEIGHT, cursor="hand2")
            btn.pack(fill=tk.X, pady=cfg.GUI_WIDGET_SPACING)
            self.action_buttons.append(btn)

        # Settings tab
        # Persisted switches (see load_game_config()). Deliberately not in
        # action_buttons: they must stay usable while a workflow is running,
        # which is safe since update_game_config() is locked.
        config = load_game_config()
        self.config_flag_vars: dict[str, tk.BooleanVar] = {}
        for label, key in (("Run side quest", "side_quest_enabled"),
                           ("Resume after focus loss", "resume_on_focus_loss"),
                           *((f"Play {label}" + ("" if name in MINIGAME_PLAYERS else " (no player yet)"), f"{name}_enabled") for name, label in cfg.MINIGAME_LABELS.items())):
            self.add_config_checkbox(options_tab, label, key, config)

        self.add_config_checkbox(options_tab, "Rejoin the private server (off: a public one)", "use_private_server", config)
        tk.Label(options_tab, text="Private server link", font=(cfg.GUI_FONT, cfg.GUI_SECTION_FONT_SIZE),
                 bg=self.bg, fg=self.accent, anchor=tk.W).pack(fill=tk.X)
        self.server_link_var = tk.StringVar(value=config["private_server_link"])
        server_link_entry = tk.Entry(options_tab, textvariable=self.server_link_var, font=(cfg.GUI_FONT, cfg.GUI_SECTION_FONT_SIZE),
                                     bg=cfg.GUI_CONSOLE_BG, fg=cfg.GUI_CONSOLE_FG, insertbackground=cfg.GUI_CONSOLE_FG)
        server_link_entry.pack(fill=tk.X, pady=cfg.GUI_WIDGET_SPACING)
        server_link_entry.bind("<FocusOut>", lambda e: self.save_server_link())
        server_link_entry.bind("<Return>", lambda e: self.save_server_link())

        # Debug tab: two subtabs - the need handlers (one button each, regardless
        # of whether it's in ENABLED_NEEDS, so any can be run on its own, plus
        # one that runs them all in turn) and everything else.
        self.build_debug_tab(debug_tab)

        # Debug console
        debug_frame = tk.Frame(self.root, bg=self.bg)
        debug_frame.pack(fill=tk.BOTH, expand=True, padx=cfg.GUI_OUTER_PADDING, pady=(0, cfg.GUI_OUTER_PADDING))

        tk.Label(debug_frame, text="Output", font=(cfg.GUI_FONT, cfg.GUI_SECTION_FONT_SIZE, "bold"),
                 bg=self.bg, fg=self.accent).pack(anchor=tk.W, pady=(0, cfg.GUI_LABEL_PADDING))

        self.debug_text = scrolledtext.ScrolledText(debug_frame, height=cfg.GUI_CONSOLE_HEIGHT, width=cfg.GUI_CONSOLE_WIDTH,
                                                     bg=cfg.GUI_CONSOLE_BG, fg=cfg.GUI_CONSOLE_FG,
                                                     font=(cfg.GUI_FONT, cfg.GUI_CONSOLE_FONT_SIZE), state=tk.DISABLED)
        self.debug_text.pack(fill=tk.BOTH, expand=True)

        # Status bar
        status = tk.Frame(self.root, bg=self.accent, height=cfg.GUI_STATUS_HEIGHT)
        status.pack(fill=tk.X, side=tk.BOTTOM)
        status.pack_propagate(False)
        self.status = tk.Label(status, text="Ready", font=(cfg.GUI_FONT, cfg.GUI_STATUS_FONT_SIZE), bg=self.accent, fg=self.fg)
        self.status.pack(anchor=tk.W, padx=cfg.GUI_OUTER_PADDING, pady=cfg.GUI_LABEL_PADDING)

    def set_debug_lines_shown(self) -> None:
        """The console's checkbox: show the debug lines or leave them out (output.log has them either way)."""
        self.capture.setLevel(logging.DEBUG if self.show_debug_var.get() else logging.INFO)

    def send_to_back(self) -> None:
        """Stop being always-on-top and drop behind other windows."""
        self.root.attributes('-topmost', False)
        self.root.lower()

    def bring_to_front(self) -> None:
        """Always-on-top again, as at startup."""
        self.root.attributes('-topmost', True)
        self.root.lift()
        self.blackout.raise_over()                                    # (a blank screen covers the window too, at once)

    def run_on_ui_thread(self, func: Callable[[], Any], timeout: Optional[float] = 5.0) -> Any:
        """Run `func` on Tk's thread and wait up to `timeout` (None = as long as it takes);
        returns its result. See docs/ARCHITECTURE.md#gui-internals.
        """
        done = threading.Event()
        result = []
        def call():
            try:
                result.append(func())
            finally:
                done.set()
        self.root.after(0, call)
        done.wait(timeout)
        return result[0] if result else None

    def run_async(self, func: Callable[[], Any]) -> None:
        """Run `func` on a daemon thread so the GUI doesn't freeze; the buttons are always
        re-enabled in a finally, however it ends. See docs/ARCHITECTURE.md#gui-internals.
        """

        if self.is_running:
            logger.warning("Already running - press [STOP] first")
            return

        self.is_running = True
        self.running_loop = func is run_workflow_loop
        STOP_EVENT.clear()
        self._set_running_ui(True)

        def worker():
            final_status = "[ERROR]"   # bound before anything can fail, so the finally below always re-enables the UI

            def record(log: Callable[[str], Any], message: str) -> None:
                """Log how the action ended; a log that can't be written (a locked file, a full disk) must not
                take the worker down - the status below is what the person sees."""
                try:
                    log(message)
                except Exception:
                    pass

            try:
                func()
                final_status = "[DONE]"
                mark_stopped()
            except StopRequested:
                # Must be caught before the generic Exception handler below -
                # StopRequested is itself an Exception subclass, so if the
                # broad handler came first it would catch this too and
                # misreport a clean stop as "[ERROR]".
                record(log_run_event, "stopped by the user")
                final_status = "[STOPPED]"
                mark_stopped()
            except TaskInterrupted as interruption:
                # The GUI's Setup button ran setup_game() with the in-task checks on, and a disconnect made them
                # rejoin the game - which ran setup itself. The action is done, not crashed.
                record(log_run_event, f"interrupted by {interruption.reason}")
                final_status = "[DONE]"
            except FocusLost:
                # Same reasoning as StopRequested above - must come before
                # the generic Exception handler.
                if stop_pressed_soon():
                    record(log_run_event, "stopped by the user")
                    final_status = "[STOPPED]"
                    mark_stopped()
                else:
                    record(log_failure, "stopped: Roblox lost focus")
                    final_status = "[STOPPED: Roblox not focused]"
            except Exception as e:
                logger.error(f"{e}\n{traceback.format_exc()}")
                record(log_failure, f"crashed: {type(e).__name__}: {e}")
                final_status = "[ERROR]"
            finally:
                release_all_inputs()  # last line of defense against stuck keys/mouse
                STOP_EVENT.clear()
                # Tkinter widgets may only be touched from the main thread; root.after() hands this callback to it instead of calling
                # .config() directly from this background thread. The flag goes down there too, in the same step as the buttons: set from here
                # first, an action asked for from outside could start in between and have its buttons put back to "Ready" under it.
                self.root.after(0, lambda status=final_status: self._finish_running(status))  # type: ignore[misc]

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _finish_running(self, status_text: str) -> None:
        """What a finished run_async() worker leaves to Tk's thread: nothing runs any more, and the buttons and the status say so."""
        self.is_running = False
        self._set_running_ui(False, status_text)

    def _set_running_ui(self, running: bool, status_text: Optional[str] = None) -> None:
        """Single place that toggles button state + status text. Called both
        right before a background task starts and from its finally block
        when it ends, so the two are always kept in sync."""
        button_state = tk.DISABLED if running else tk.NORMAL
        for btn in self.action_buttons:
            btn.config(state=button_state)  # type: ignore[call-overload]
        self.status.config(text="Running..." if running else (status_text or "Ready"))

    def add_config_checkbox(self, parent: Any, label: str, key: str, config: dict[str, Any]) -> None:
        """A checkbox for the persisted switch `key`; not in action_buttons (usable while a
        workflow runs). See docs/ARCHITECTURE.md#gui-internals.
        """
        var = tk.BooleanVar(value=config[key])
        self.config_flag_vars[key] = var
        tk.Checkbutton(parent, text=label, variable=var,
                       command=lambda k=key, v=var: self.set_config_flag(k, v.get()),  # type: ignore[misc]
                       font=(cfg.GUI_FONT, cfg.GUI_SECTION_FONT_SIZE), bg=self.bg, fg=self.fg,
                       selectcolor=self.bg, activebackground=self.bg, activeforeground=self.fg,
                       anchor=tk.W, cursor="hand2").pack(fill=tk.X, pady=cfg.GUI_WIDGET_SPACING)

    def set_config_flag(self, key: str, value: Any) -> None:
        """Persist one of the config's on/off switches (a checkbox was clicked)."""
        update_game_config(lambda c: c.update({key: value}))
        logger.info(f"{key} = {value}")
        if key == "blackout_hotkey_enabled" and not value:
            self.blackout.hide()                                  # (a screen that is blank must not stay so with no key to show it)

    def save_server_link(self) -> None:
        """Persist the private server link typed into the entry box."""
        link = self.server_link_var.get().strip()
        if link != load_game_config()["private_server_link"]:
            update_game_config(lambda c: c.update(private_server_link=link))
            logger.info("Private server link saved")

    def reset_config(self) -> None:
        """Delete the persisted config (money collected, timers and the
        switches all go back to their defaults; the private server link is
        kept) and refresh the checkboxes to match."""
        reset_game_config()
        config = load_game_config()
        for key, var in self.config_flag_vars.items():
            var.set(config[key])
        logger.info("Config reset to defaults")

    def build_debug_tab(self, parent: Any) -> None:
        """The Debug tab: an Options subtab (build_other_tab()) and a Needs one
        (build_needs_tab())."""
        subtabs = ttk.Notebook(parent)
        subtabs.pack(fill=tk.BOTH, expand=True)
        needs_tab = tk.Frame(subtabs, bg=self.bg)
        other_tab = tk.Frame(subtabs, bg=self.bg)
        subtabs.add(other_tab, text="Options")
        subtabs.add(needs_tab, text="Needs")
        self.build_other_tab(other_tab)
        self.build_needs_tab(needs_tab)

    def build_needs_tab(self, parent: Any) -> None:
        """Fill the Needs subtab: a button that runs every teleport handler in turn, then a two-column grid with a switch
        (whether the macro acts on that need, kept in the game config, on unless ENABLED_NEEDS leaves it out) and a small [Test]
        button for each entry in DEBUG_HANDLERS."""
        for col in range(2):
            parent.grid_columnconfigure(col, weight=1, uniform="debug")
        btn_all = tk.Button(parent, text="Test every teleport handler, one after another", command=lambda: self.run_async(run_all_need_handlers),
                            font=(cfg.GUI_FONT, cfg.GUI_SECTION_FONT_SIZE, "bold"), bg=cfg.GUI_LOOP_COLOR, fg=self.fg, cursor="hand2")
        btn_all.grid(row=0, column=0, columnspan=2, sticky=tk.EW, padx=cfg.GUI_WIDGET_SPACING, pady=cfg.GUI_WIDGET_SPACING)
        self.action_buttons.append(btn_all)
        tk.Label(parent, text="Switch a need off to leave it alone, or test its handler on its own", font=(cfg.GUI_FONT, cfg.GUI_SECTION_FONT_SIZE, "bold"),
                 bg=self.bg, fg=self.accent).grid(row=1, column=0, columnspan=2, sticky=tk.W,
                                                   padx=cfg.GUI_ROW_SPACING, pady=(cfg.GUI_ROW_SPACING, cfg.GUI_WIDGET_SPACING))
        self.need_vars: dict[str, tk.BooleanVar] = {}
        for i, (name, handler_cls) in enumerate(DEBUG_HANDLERS.items()):
            cell = tk.Frame(parent, bg=self.bg)
            cell.grid(row=2 + i // 2, column=i % 2, sticky=tk.EW, padx=cfg.GUI_WIDGET_SPACING)
            var = tk.BooleanVar(value=need_is_enabled(name))
            self.need_vars[name] = var
            tk.Checkbutton(cell, text=name.capitalize(), variable=var, command=lambda n=name, v=var: self.set_need_enabled(n, v.get()),  # type: ignore[misc]
                           font=(cfg.GUI_FONT, cfg.GUI_STATUS_FONT_SIZE), bg=self.bg, fg=self.fg, selectcolor=self.bg,
                           activebackground=self.bg, activeforeground=self.fg, anchor=tk.W, cursor="hand2").pack(side=tk.LEFT)
            btn = tk.Button(cell, text="Test", command=lambda n=name, c=handler_cls: self.test_handler(n, c),  # type: ignore[misc]
                            font=(cfg.GUI_FONT, cfg.GUI_STATUS_FONT_SIZE), bg=cfg.GUI_TEST_COLOR, fg=self.fg, cursor="hand2", padx=cfg.GUI_ROW_SPACING, pady=0)
            btn.pack(side=tk.RIGHT, pady=cfg.GUI_LABEL_PADDING)
            self.action_buttons.append(btn)

    def set_need_enabled(self, name: str, value: bool) -> None:
        """Persist a need's switch (usable while a run goes on, like the other switches: update_game_config() is locked)."""
        update_game_config(lambda c: c.setdefault("needs_enabled", {}).update({name: value}))
        logger.info(f"{name} need {'on' if value else 'off'}")

    def build_other_tab(self, parent: Any) -> None:
        """Fill the Options subtab: record the next run, show the debug lines in the output or not, refresh the debug
        folder, reset the config."""
        self.add_config_checkbox(parent, "Record the next run (low quality video)", "record_next_run", load_game_config())
        self.add_config_checkbox(parent, f"{cfg.HOTKEYS['blackout'].upper()} blanks the screen in Roblox (off: type it)", "blackout_hotkey_enabled", load_game_config())
        self.show_debug_var = tk.BooleanVar(value=True)
        tk.Checkbutton(parent, text="Show debug lines in the output", variable=self.show_debug_var, command=self.set_debug_lines_shown,
                       font=(cfg.GUI_FONT, cfg.GUI_SECTION_FONT_SIZE), bg=self.bg, fg=self.fg,
                       selectcolor=self.bg, activebackground=self.bg, activeforeground=self.fg,
                       anchor=tk.W, cursor="hand2").pack(fill=tk.X, pady=cfg.GUI_WIDGET_SPACING)
        tk.Label(parent, text=f"Press {cfg.HOTKEYS['capture'].upper()} anywhere to start / stop capturing screenshots and input",
                 font=(cfg.GUI_FONT, cfg.GUI_STATUS_FONT_SIZE), bg=self.bg, fg=self.accent, anchor=tk.W).pack(fill=tk.X, padx=cfg.GUI_WIDGET_SPACING)
        tk.Label(parent, text=f"Press {cfg.HOTKEYS['blackout'].upper()} in Roblox to blank / show the screen (the macro still sees it)",
                 font=(cfg.GUI_FONT, cfg.GUI_STATUS_FONT_SIZE), bg=self.bg, fg=self.accent, anchor=tk.W).pack(fill=tk.X, padx=cfg.GUI_WIDGET_SPACING)
        tk.Button(parent, text="Refresh debug", command=self.refresh_debug_folder,
                  font=(cfg.GUI_FONT, cfg.GUI_SECTION_FONT_SIZE), bg=self.accent, fg=self.fg, cursor="hand2"
                  ).pack(fill=tk.X, padx=cfg.GUI_WIDGET_SPACING, pady=cfg.GUI_WIDGET_SPACING)
        tk.Button(parent, text="Reset Config", command=self.reset_config,
                  font=(cfg.GUI_FONT, cfg.GUI_SECTION_FONT_SIZE), bg=cfg.GUI_STOP_COLOR, fg=self.fg,
                  height=cfg.GUI_RESPAWN_BUTTON_HEIGHT, cursor="hand2"
                  ).pack(fill=tk.X, padx=cfg.GUI_WIDGET_SPACING, pady=cfg.GUI_WIDGET_SPACING)

    def refresh_debug_folder(self) -> None:
        """Ask, then empty the debug folder (refresh_debug()). Not while a run
        is going - it's writing there."""
        if self.is_running or state.capture is not None or state.run_capture is not None:
            logger.debug("Stop the run and the capture before refreshing the debug folder")
            return
        if messagebox.askyesno("Refresh debug", "Delete everything in the debug folder (logs, images, recordings)?\n"
                                                "The game config is kept."):
            logger.debug(f"Debug folder refreshed ({refresh_debug()} entries removed)")

    def test_handler(self, name: str, handler_cls: Callable[[], NeedHandler]) -> None:
        """Run one need handler on its own, outside the normal need-detection
        flow, so it can be tried even when it isn't in ENABLED_NEEDS."""
        def test():
            logger.info(f"[TEST] Running {name} handler...")
            try:
                with task_unscrew():
                    handler_cls().handle()
            except TaskInterrupted as interruption:
                logger.info(f"[TEST] {name} interrupted by {interruption.reason}")
            logger.info(f"[TEST] {name.capitalize()} handler complete")
        self.run_async(test)

    def stop(self) -> None:
        """Set STOP_EVENT; the cleanup happens in run_async()'s worker once the running function
        notices. See docs/ARCHITECTURE.md#stopping--focus-safety.
        """
        if not self.is_running:
            return
        logger.info("Stopping...")
        STOP_EVENT.set()
        release_all_inputs()                      # at once, from here: the macro stops doing things the moment the stop is asked for, not when the worker next looks
        self.status.config(text="Stopping...")

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    root = tk.Tk()
    gui = AdoptMeGUI(root)
    gui.start_remote_control(loop="--loop" in sys.argv[1:])
    root.mainloop()
