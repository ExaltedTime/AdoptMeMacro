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

import os, re, sys, time, threading, json, subprocess, traceback, ctypes, queue, contextlib, shutil, zipfile
import datetime
import logging
import logging.handlers
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Callable, ContextManager, Iterable, Iterator, Optional, Sequence, TypeAlias, Union
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

Image: TypeAlias = np.ndarray                  # a BGR screenshot or crop (what grab_screen() returns)
Point = tuple[int, int]             # (x, y) in the reference space
Box = tuple[int, int, int, int]     # (left, top, right, bottom) - or (left, top, width, height) for roblox_rect()
Color = tuple[int, int, int]        # (R, G, B)

import magic_numbers as cfg

class TaskInterrupted(Exception):
    """A rejoin or a popup took over the character: the handler is abandoned and
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

_WARNED_HOTKEYS: set[str] = set()

def hotkey_vk(key: str) -> Optional[int]:
    """The virtual-key code of the hotkey `key`: a letter or digit of the alphabet is its own, any other character (an accented one, say) is looked up on the
    keyboard layout in use (VkKeyScanW). None when the layout has no key for it by itself - it is typed with Shift, AltGr or a dead key, or not at all."""
    if key.isascii() and key.isalnum():
        return ord(key.upper())
    scan = ctypes.windll.user32.VkKeyScanW(ord(key)) & 0xFFFF  # type: ignore[attr-defined]  # Windows only
    if scan == 0xFFFF or scan >> 8:                         # (no key, or one that needs a modifier)
        return None
    return scan & 0xFF

def hotkey_down(key: str) -> bool:
    """True while `key` (a single letter or digit, or a character of its own key on the layout: hotkey_vk()) is held down, whichever window has the focus
    (Windows). A key the layout does not have is never down (said once)."""
    vk = hotkey_vk(key)
    if vk is None:
        if key not in _WARNED_HOTKEYS:
            _WARNED_HOTKEYS.add(key)
            logger.warning(f"The '{key}' hotkey is not a key of its own on this keyboard layout - it does nothing")
        return False
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)  # type: ignore[attr-defined]  # Windows only

def save_screenshot() -> str:
    """The whole primary monitor now, at its own size (grab_full_screen(): nothing cut out or scaled, so what is read off it is read off the pixels the screen
    has) as SCREENSHOTS_DIR/screenshot_<date>_<time>.png, a number added if there is one of that second already; the path."""
    os.makedirs(cfg.SCREENSHOTS_DIR, exist_ok=True)
    base = os.path.join(cfg.SCREENSHOTS_DIR, time.strftime("screenshot_%Y%m%d_%H%M%S"))
    path, n = base + ".png", 1
    while os.path.exists(path):
        n += 1
        path = f"{base}_{n}.png"
    if not cv2.imwrite(path, grab_full_screen()):
        raise OSError(f"could not write {path}")
    return path

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
    try:
        w.activate()
    except gw.PyGetWindowException as error:        # Windows would not hand the focus over (its error code is often 0: "completed successfully"): another program has the foreground, say the bot's console that has just opened
        logger.warning(f"Roblox could not be brought to the front ({error}) - trying another way")
        if not force_foreground(w):
            logger.warning("Roblox could not be brought to the front")
            return False
    _ROBLOX_RECT = None  # it may have just moved or changed size
    time.sleep(cfg.FOCUS_DELAY)
    return True

def force_foreground(window: Any) -> bool:
    """Take the foreground for `window` when activate() was refused: a tap of the Alt key (a program that was just given the keys may then ask for the foreground), activate() again, and
    if that is refused too minimize and restore the window, which brings it to the front. True if Roblox is the focused window after any of them."""
    for attempt in range(cfg.FOCUS_FORCE_TRIES):
        try:
            if attempt == 0:
                key_down(cfg.KEYS["foreground_tap"])
                key_up(cfg.KEYS["foreground_tap"])
                window.activate()
            else:
                window.minimize()
                time.sleep(cfg.FOCUS_DELAY)
                window.restore()
        except gw.PyGetWindowException:
            pass
        time.sleep(cfg.FOCUS_DELAY)
        if is_roblox_focused():
            return True
    return False

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

def grab_roblox_native() -> Optional[Image]:
    """The Roblox window as it is on screen (not scaled), or None when there is no window or it is minimised: a picture that is kept and sent on (a failure's) is of the game only, not of whatever
    else is on the monitor."""
    window = roblox_window()
    if window is None or window.isMinimized:
        return None
    left, top, width, height = roblox_rect()
    return grab_full_screen()[top:top + height, left:left + width]

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
    # rejoined, or the need bar couldn't be read):
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
    moments: Optional[Any] = None               # the capture of the run's moments (start_moments()), None when there is none
    moments_log: Optional[Any] = None           # its MomentsLog
    moments_marked: float = 0.0                 # when mark_moment() last marked it
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
    hopeless: set = field(default_factory=set)       # needs disabled that had never worked in the run: a rejoin does not give them another chance (pizza and sick, 3 more failures each after every rejoin of a 10-hour run)
    # The in-task checks (see task_unscrew()): whether a need handler or setup
    # is running and its thread, whether it may be abandoned (setup may not), when the checks
    # last ran, and whether they are running now.
    task_active: bool = False
    task_interruptible: bool = True
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
    task_board_wanted: bool = False
    activity: str = ""                     # what the macro is doing now (task_unscrew(what=...)): a need handler, a side action, setup; "" between them
    moments_notes: list = field(default_factory=list)       # (seconds into the run, the log line) of the moments start_moments() marked
    typing: bool = False                   # type_keys() is typing into a text box: the hotkeys (a letter each) are not acted on meanwhile
    # How many "quiet:" lines have been logged for the current stretch with no need resolved.
    quiet_reported: int = 0

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
        if path == cfg.LOGS_DIR:
            removed += empty_logs_folder()
            continue
        try:
            shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
            removed += 1
        except OSError as error:   # a locked file (a video still open, say): leave it, carry on with the rest
            logger.warning(f"Couldn't delete {name}: {error}")
    for directory in (cfg.LOGS_DIR, cfg.RUNS_DIR, cfg.FAILURE_DIR):
        os.makedirs(directory, exist_ok=True)
    return removed

def empty_logs_folder() -> int:
    """Empty the logs folder (1 if it was emptied, 0 if something is left). macro_stderr.log is emptied, not deleted: the bot started this macro with its error
    output going to that file, which the process keeps open, and Windows will not delete a file that is open."""
    clean = True
    for name in os.listdir(cfg.LOGS_DIR):
        path = os.path.join(cfg.LOGS_DIR, name)
        try:
            if path == cfg.MACRO_STDERR_PATH:
                open(path, "w").close()
            elif os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
        except OSError as error:
            clean = False
            logger.warning(f"Couldn't delete logs/{name}: {error}")
    return int(clean)

def acknowledge_command(stem: str, text: str) -> None:
    """Tell the bot what became of the command in <stem>.json: "taken", or "dropped: <why>" (the bot waits for this before it says it asked)."""
    try:
        with open(os.path.join(cfg.COMMANDS_DIR, stem + ".ack"), "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        logger.warning(f"Could not write the acknowledgement of the command {stem}")


def finish_command(stem: str, text: str) -> None:
    """Tell the bot how the action of the command in <stem>.json ended (<stem>.done: the status the buttons show, or why it was given up); the bot says so in
    the channel. Nothing for a command with no name (one that did not come from a file)."""
    if not stem:
        return
    try:
        os.makedirs(cfg.COMMANDS_DIR, exist_ok=True)
        with open(os.path.join(cfg.COMMANDS_DIR, stem + ".done"), "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        logger.warning(f"Could not write the end of the command {stem}")


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


def read_command_with_id() -> tuple[str, Optional[dict[str, Any]]]:
    """(the command's name - the file's, without .json - and) the oldest command waiting in COMMANDS_DIR (a file written by the Discord bot: {"command": ..., "time": ...}), or None. The file is always removed
    once read, and the bot is told in an acknowledgement file whether the command was taken; a command that isn't one of COMMANDS, or is older than
    COMMAND_MAX_AGE seconds (left over from before the macro started, say), is none - and a warning says why, so that a bot host whose clock is off does
    not have every command ignored without a trace. A `set` command must also name a switch that exists (`kind` "need" and a NEED_NAMES name, or "option"
    and an OPTION_SWITCHES name or SHOW_DEBUG_LINES_OPTION) and give it a true or false `value`. One command is read per call, the oldest first, so two sent
    close together are both done, in order. A `decline` names a `listing` of TRADES_LISTINGS and an `offer` of 1 to TRADES_MAX_OFFERS or TRADES_ALL_OFFERS. A `test` names a need handler (DEBUG_HANDLERS) or TEST_ALL_TELEPORTS, and a `server_link` gives a `link` that parse_server_link() knows. Never raises. See docs/ARCHITECTURE.md#remote-control."""
    try:
        names = sorted(name for name in os.listdir(cfg.COMMANDS_DIR) if name.endswith(".json"))
    except OSError:
        return "", None
    if not names:
        return "", None
    path = os.path.join(cfg.COMMANDS_DIR, names[0])
    stem = names[0][:-len(".json")]

    def dropped(why: str) -> None:
        logger.warning(f"Command from outside dropped: {why}")
        acknowledge_command(stem, "dropped: " + why)

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return stem, None
    except (OSError, ValueError) as error:
        data = None
        dropped(f"the command file could not be read ({error})")
    try:
        os.remove(path)
    except OSError:
        pass
    if data is None:
        return stem, None
    if not isinstance(data, dict) or data.get("command") not in cfg.COMMANDS:
        dropped(f"not a command the macro knows ({str(data)[:80]})")
        return stem, None
    sent = data.get("time")
    if not isinstance(sent, (int, float)):
        dropped(f"{data['command']} has no time")
        return stem, None
    age = time.time() - sent
    if abs(age) > cfg.COMMAND_MAX_AGE:
        dropped(f"{data['command']} is {age:.0f} s old (more than COMMAND_MAX_AGE; if the PCs' clocks differ, set them right)")
        return stem, None
    if data["command"] == "set":
        switches = {"need": cfg.NEED_NAMES, "option": (*cfg.OPTION_SWITCHES, cfg.SHOW_DEBUG_LINES_OPTION)}.get(str(data.get("kind")), ())
        if data.get("name") not in switches or not isinstance(data.get("value"), bool):
            dropped(f"set {data.get('kind')} {data.get('name')} is not a switch the macro has")
            return stem, None
    if data["command"] == "test" and data.get("handler") not in (*DEBUG_HANDLERS, cfg.TEST_ALL_TELEPORTS):
        dropped(f"test {data.get('handler')} is not a handler the macro has")
        return stem, None
    if data["command"] == "report" and "run" in data and (not isinstance(data["run"], int) or isinstance(data["run"], bool) or data["run"] < 1):
        dropped("report is asked for with the number of a run, or none")
        return stem, None
    if data["command"] == "capture" and "value" in data and not isinstance(data["value"], bool):
        dropped("capture is asked for with a true or false value, or none")
        return stem, None
    if data["command"] == "decline" and (data.get("listing") not in cfg.TRADES_LISTINGS or not (data.get("offer") == cfg.TRADES_ALL_OFFERS or (
            isinstance(data.get("offer"), int) and not isinstance(data.get("offer"), bool) and 1 <= data["offer"] <= cfg.TRADES_MAX_OFFERS))):
        dropped(f"decline listing {data.get('listing')} offer {data.get('offer')} is not an offer the macro can be asked for")
        return stem, None
    if data["command"] == "server_link" and (not isinstance(data.get("link"), str) or parse_server_link(data["link"].strip()) is None):
        dropped("that is not a private server link")                                  # (the link itself is a join key: not said)
        return stem, None
    acknowledge_command(stem, "taken")
    return stem, data

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
    note_moment(message)

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
    busy: bool = False                                               # the window is running something (the loop or an action: its buttons are off): the bot's /rview until that is over

    def reset(self) -> None:
        """Start afresh (a workflow was just started); `busy` is the window's, not the run's, and stays."""
        busy = self.busy
        fresh = RunStats()
        for item in fields(self):
            setattr(self, item.name, getattr(fresh, item.name))
        self.run, self.started, self.busy = state.run_number, time.strftime("%Y-%m-%d %H:%M:%S"), busy

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
        game = grab_roblox_native()                                       # (the game's window: the rest of the monitor is nobody's business, and this goes to Discord)
        if game is not None:
            cv2.imwrite(os.path.join(folder, "screen.png"), game)
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
        return f"{name}/screen.png" if game is not None else None
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
# link gone).
GAME_CONFIG_LOCK = threading.Lock()

def _read_game_config() -> dict[str, Any]:
    """load_game_config() without taking the lock (for callers that hold it)."""
    defaults: dict[str, Any] = {
        "money_collected": 0,
        "lure_timer": time.time(),  # due immediately on a fresh config
        "tree_timer": time.time(),  # likewise
        "money_reset_boundary": None,   # the last daily MONEY_RESET_HOUR reset side_quest() has dealt with (None: not yet)
        "money_tree_enabled": True, # whether side_quest() tends the money tree
        "lure_enabled": True,       # whether side_quest() tends the lure
        "resume_on_focus_loss": False,  # whether the loop takes Roblox's focus back instead of stopping
        "setup_next_loop": False,   # run Setup at the next pass of the loop (once; cleared when it runs)
        "record_next_run": False,   # record the next run as a low quality video; cleared when that run starts
        "blackout_hotkey_enabled": True,   # whether HOTKEYS["blackout"] blanks the screen (off: that key can be typed in Roblox)
        "keep_moments": True,              # whether the loop captures the run's moments (start_moments()), kept when the run lasted MOMENTS_MIN_RUN seconds or more
        "pet_pen_enabled": True,           # whether pet_pen() collects the Pet Pen when the Task Board says it is ready
        "pet_repicked": 0.0,               # when pet_repick() last picked the pet to level
        "pet_pen_looked": 0.0,
        "pet_pen_swap": False,             # a pet of the pen reached Friend: pet_pen_swap_due() switches the full-grown ones out
        "pet_pen_swap_tried": 0.0,         # when it last tried             # when pet_pen() last opened the board
        "level_growing_pet": True,         # whether Setup filters the eggs and full-grown pets out of the backpack and equips the first pet left (off: the pet is left, the Alicorn equipped if none is out)
        "new_need_hotkey_enabled": True,   # whether HOTKEYS["new_need"] teaches the macro a need icon it does not know (off: that key can be typed in Roblox)
        "needs_enabled": {},        # {need: whether it is acted on}, the Debug tab's switches; a need not in it follows ENABLED_NEEDS (see need_is_enabled())
        "private_server_link": "",  # used by leave_and_rejoin(); kept here, not in the source, since it's a join key
        "use_private_server": True,   # whether a rejoin goes to that private server (off: a public one, with the link kept)
    }
    try:
        with open(cfg.GAME_CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
    except (FileNotFoundError, ValueError):
        return defaults
    if "side_quest_enabled" in config:                      # (one switch for both, before they were two: it is carried over to each, once)
        for key in ("money_tree_enabled", "lure_enabled"):
            config.setdefault(key, config["side_quest_enabled"])
        del config["side_quest_enabled"]
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

@lru_cache(maxsize=None)
def icon_disc_mask(width: int, height: int, radius_fraction: float) -> Image:
    """Boolean mask, True inside the disc (centred, `radius_fraction` of half the width across) of a width x height image."""
    yy, xx = np.mgrid[:height, :width]
    cx, cy = (width - 1) / 2, (height - 1) / 2
    return (xx - cx) ** 2 + (yy - cy) ** 2 <= (radius_fraction * width / 2) ** 2

@lru_cache(maxsize=None)
def icon_match_mask(size: tuple[int, int], radius_fraction: float) -> Image:
    """The part of a signature (see icon_signature()) that is compared: the left half of the disc inside the icon's ring (a badge sits at the icon's top right)."""
    mask = icon_disc_mask(size[0], size[1], radius_fraction)
    return mask[:, :mask.shape[1] // 2]

def icon_signature(icon_img: Image) -> Image:
    """Comparable form of an icon: outside-the-ring pixels blacked out, preprocessed,
    resized, left half only. See docs/ARCHITECTURE.md#need-icon-matching.
    """
    height, width = icon_img.shape[:2]
    icon_img = np.where(icon_disc_mask(width, height, cfg.ICON_MATCH_RADIUS_FRACTION)[:, :, None], icon_img, 0).astype(icon_img.dtype)
    proc = cv2.resize(preprocess_icon(icon_img), cfg.ICON_COMPARE_SIZE).astype(np.float32) / cfg.PIXEL_MAX
    return proc[:, :proc.shape[1] // 2]

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
    """Compare a detected icon (its icon_variants() crops) against the saved needs (need_signatures()), scoring each by its best-aligned variant. Returns (name, score). The scores of every
    variant against every saved icon are worked out at once (score_variants()): 25 variants against every saved need was most of a look at the need bar."""
    saved = need_signatures()
    if not saved:
        return None, 0.0
    scores = score_variants([icon_signature(v) for v in icon_variants], [signature for _, signature in saved]).max(axis=0)
    best = int(np.argmax(scores))                       # (the first of equal scores, as the loop it replaced took)
    best_match, best_score = saved[best][0], float(scores[best])
    if best_score >= cfg.ICON_MATCH_THRESHOLD:
        return best_match, best_score
    return None, best_score

def score_variants(live: Sequence[Image], saved: Sequence[Image]) -> Image:
    """The similarity (0-1, 1 = identical: one minus the mean squared difference over the part inside the icon's ring, icon_match_mask()) of every signature of `live` with every one of `saved`:
    an array [live, saved]. The squared difference over the icon's disc is worked out as
    sum(L^2) - 2 sum(L S) + sum(S^2) over the pixels of the mask, the middle term as one matrix product."""
    mask = icon_match_mask(cfg.ICON_COMPARE_SIZE, cfg.ICON_MATCH_RADIUS_FRACTION)
    a = np.stack([signature[mask] for signature in live]).astype(np.float64)
    b = np.stack([signature[mask] for signature in saved]).astype(np.float64)
    squared = (a * a).sum(axis=1)[:, None] - 2.0 * (a @ b.T) + (b * b).sum(axis=1)[None, :]
    return 1.0 - squared / mask.sum()

def prompt_rename_need(icon_img: Image, idx: int, on_ui_thread: bool = False) -> str:
    """Save a new need icon and ask for its name (dialog or console); a cancelled dialog saves it as unnamed_<idx>. `on_ui_thread`: the caller is Tk's thread, which asks the dialog itself
    (run_on_ui_thread() would wait for itself). See docs/ARCHITECTURE.md#teaching-a-new-need."""
    logger.info("New need detected")
    temp_path = os.path.join(cfg.NEEDS_DIR, f"temp_{idx}.png")
    cv2.imwrite(temp_path, preprocess_icon(icon_img))

    prompt = "Need name (hungry/thirsty/dirty/potty/sleepy/catch/walk/other):"
    if state.macro_window is not None:
        window = state.macro_window
        ask = lambda: simpledialog.askstring("New need", prompt, parent=window.root)
        answer = ask() if on_ui_thread else window.run_on_ui_thread(ask, timeout=None)
        if not on_ui_thread:
            focus_roblox_click()   # the dialog took the focus
    else:
        print(f"{prompt} ", end="", flush=True)                           # (a question to the person at the terminal, not a log line)
        answer = input()
    need_name = (answer or "").strip().lower() or f"unnamed_{idx}"

    os.rename(temp_path, os.path.join(cfg.NEEDS_DIR, f"{need_name}.png"))
    logger.info(f"Saved: {need_name}")
    return need_name

def teach_new_needs() -> int:
    """The new-need key's action (Tk's thread): look at the need bar now (detect_need_icons(), a fresh search) and ask for the name of each icon on it that matches no saved need
    (prompt_rename_need(): saved into NEEDS_DIR under that name, so that it is recognised from the next look). Says in the log if there was none. Returns how many were taught."""
    found_icons, full_img = detect_need_icons(fresh=True)
    taught = 0
    for idx, icon_img, need_name, score in identify_icons(found_icons, full_img):
        if need_name:
            continue
        logger.info(f"New need on the bar (best score: {score:.4f})")
        prompt_rename_need(icon_img, idx, on_ui_thread=True)
        taught += 1
    if taught:
        logger.info(f"{taught} new need(s) taught - they are recognised from the next look")
    else:
        logger.info(f"No new need on the bar ({len(found_icons)} icon(s), all known)" if found_icons else "No need icon on the bar")
    return taught

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

def icon_saturation(icon_img: Image) -> float:
    """The mean saturation (0-255) inside the circle of an icon's crop (extract_icon()), PULSE_DISC_MARGIN smaller than it."""
    height, width = icon_img.shape[:2]
    mask = np.zeros((height, width), np.uint8)
    cv2.circle(mask, (width // 2, height // 2), max(1, min(height, width) // 2 - cfg.PULSE_DISC_MARGIN), 255, -1)
    return float(cv2.mean(cv2.cvtColor(icon_img, cv2.COLOR_BGR2HSV)[..., 1], mask)[0])

class PulseWatch:
    """Looks at the icon of `need_name` now and then (look()) and tells whether it pulses (pulsing(): its saturation has moved PULSE_MIN_SWING between its highest and lowest look) - the sign that
    the game is doing the need."""
    def __init__(self, need_name: str) -> None:
        self.need_name = need_name
        self.waited = 0.0                 # seconds since the action, as wait_until_need_gone() counts them
        self.levels: list[float] = []
        self.first_seen: Optional[float] = None

    def look(self) -> None:
        """One look at the need bar (the circle search is made afresh, the icon may have moved along as others went): the saturation of this need's icon is noted if it is there."""
        found, img = detect_need_icons(fresh=True)
        for _, icon_img, name, _ in identify_icons(found, img):
            if name == self.need_name:
                self.levels.append(icon_saturation(icon_img))
                if self.first_seen is None and self.pulsing():
                    self.first_seen = self.waited
                return

    def pulsing(self) -> bool:
        return len(self.levels) >= 2 and max(self.levels) - min(self.levels) >= cfg.PULSE_MIN_SWING

    def overdue(self) -> bool:
        """True if the need is one whose pulse is known (PULSE_NEEDS) and none has shown PULSE_START_WAIT seconds after the action."""
        return self.need_name in cfg.PULSE_NEEDS and not self.pulsing() and self.waited >= cfg.PULSE_START_WAIT

def wait_until_need_gone(need_name: str, max_wait: float = cfg.NEED_GONE_MAX_WAIT, poll_interval: float = cfg.NEED_GONE_POLL_INTERVAL) -> None:
    """Wait up to `max_wait` for `need_name` to clear, polling every `poll_interval`. A
    timeout is logged as a failure and sets state.need_cleared False.
    See docs/ARCHITECTURE.md#waiting-for-a-need-to-clear.
    """
    logger.debug(f"Waiting up to {max_wait}s for {need_name} to clear...")
    state.need_cleared = True
    pulse = PulseWatch(need_name)
    next_check = poll_interval
    while True:
        remaining = max_wait - pulse.waited
        if remaining <= 0:
            logger.debug(f"{need_name} still showing after {max_wait}s, moving on")
            log_failure(f"{need_name} still showing after {max_wait}s" + (f" (its icon pulsed {pulse.first_seen:.1f}s after the action)" if pulse.first_seen is not None else " (its icon never pulsed)"))
            state.need_cleared = False
            return
        step = min(cfg.PULSE_POLL, remaining)
        wait_interruptible(step)
        pulse.waited += step
        pulse.look()
        if pulse.waited >= next_check - 1e-9:
            next_check += poll_interval
            if _need_cleared(need_name):
                logger.debug(f"{need_name} cleared")
                if pulse.first_seen is not None:
                    log_run_event(f"{need_name}: its icon pulsed {pulse.first_seen:.1f}s after the action")
                return
        if pulse.overdue():
            log_failure(f"{need_name}: its icon has not pulsed {cfg.PULSE_START_WAIT}s after the action - it is not being done")
            state.need_cleared = False
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
# then, so a popup or a stray window doesn't wait for the handler
# to finish. Only on the thread that started the task, and never from inside
# unscrew() itself.

@contextlib.contextmanager
def task_unscrew(interruptible: bool = True, what: str = "") -> Iterator[None]:
    """Within this block wait_interruptible() runs unscrew(in_task=True) every
    UNSCREW_TASK_INTERVAL; `interruptible` False (setup) is never abandoned because of a popup; `what` is noted as state.activity meanwhile (the moments capture says it with every frame).
    See docs/ARCHITECTURE.md#need-handlers.
    """
    previous = (state.task_active, state.task_interruptible, state.task_thread, state.activity)
    state.task_active, state.task_interruptible, state.task_thread = True, interruptible, threading.get_ident()
    state.activity = what or state.activity
    state.last_task_unscrew = time.time()
    try:
        yield
    finally:
        state.task_active, state.task_interruptible, state.task_thread, state.activity = previous

def task_unscrew_tick() -> None:
    """The hook in wait_interruptible(): run the in-task checks if one is due.
    Raises TaskInterrupted if the game was rejoined or a popup spoiled the task."""
    if (not state.task_active or state.in_unscrew or threading.get_ident() != state.task_thread
            or time.time() - state.last_task_unscrew < cfg.UNSCREW_TASK_INTERVAL):
        return
    state.in_unscrew = True
    try:
        result = unscrew(in_task=True, interruptible=state.task_interruptible)
    finally:
        state.in_unscrew = False
        state.last_task_unscrew = time.time()
    if result == "rejoined":
        release_all_inputs()
        raise TaskInterrupted("a rejoin")
    if result == "aborted":
        release_all_inputs()
        respawn_character()   # unlike a rejoin this doesn't respawn by itself: start the retry from a known spot
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

def in_house(img: Image) -> bool:
    """True if the player is in their house: the red "Exit Home" button at the bottom left (HOUSE_EXIT_BOX) is on screen. It is hidden by a white screen, so it is looked at when one has gone."""
    left, top, right, bottom = cfg.HOUSE_EXIT_BOX
    hsv = cv2.cvtColor(img[top:bottom, left:right], cv2.COLOR_BGR2HSV)
    red = sum(int(np.count_nonzero(cv2.inRange(hsv, np.array(low), np.array(high)))) for low, high in cfg.HOUSE_EXIT_HSV)
    return red >= cfg.HOUSE_EXIT_MIN_PIXELS

def wait_for_white_to_pass(static: float, look: float = cfg.WHITE_LOOK, *, what: str = "") -> bool:
    """Wait out a transition by its white screen (a door, a respawn, a teleport: detect_white_screen()) instead of a fixed time: for it to show - within `look` seconds - and then go (WHITE_CLEAR_LOOKS looks
    in a row that are not white, WHITE_PASS_MAX at most), then WHITE_SETTLE more; True. When none shows in `look` seconds the wait is `static` seconds in all (what it was before, so that a transition without
    a white screen costs no more than it did); False, and with `what` named a line in the run log: a transition that should have a white screen and had none is a sign that it did not happen."""
    started = time.time()
    seen, clear = False, 0
    while time.time() - started < cfg.WHITE_PASS_MAX:
        if detect_white_screen(grab_screen()):
            seen, clear = True, 0
        elif seen:
            clear += 1
            if clear >= cfg.WHITE_CLEAR_LOOKS:
                wait_interruptible(cfg.WHITE_SETTLE)
                return True
        elif time.time() - started >= look:
            if what:
                log_run_event(f"{what}: no white screen showed in {look:.0f} s")
            wait_interruptible(max(0.0, static - (time.time() - started)))
            return False
        wait_interruptible(cfg.WHITE_POLL)
    return seen

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
    wait_for_white_to_pass(cfg.RESPAWN_WAIT, what="respawn")                                 # (the respawn's white screen and its fade, or the old fixed wait when none shows)
    if not in_house(grab_screen()):
        log_run_event("respawn: not in the house afterwards (no Exit Home button)")
    logger.debug("Respawn complete")

def close_open_windows() -> int:
    """Close what is open on top of the game: the trade phone's screens (close_trade_phone(), and the board of offers) and the stray windows (dismiss_stray_windows(), one after the
    other, at most RECENTER_CLOSE_TRIES). Returns how many were closed."""
    closed = 0
    screen = trade_screen(grab_screen())
    if screen == "board":
        hover_click(*cfg.STRAY_WINDOWS["trade_board"]["close_pos"])
        wait_interruptible(cfg.UI_SETTLE)
        closed += 1
    elif screen == "dialog":
        hover_click(*cfg.EXIT_STAY_POS)                                  # (Cancel: nothing is declined)
        wait_interruptible(cfg.UI_SETTLE)
        screen = trade_screen(grab_screen())
        closed += 1
    if screen in ("detail", "offers", "listings", "menu", "phone"):
        close_trade_phone()
        wait_interruptible(cfg.UI_SETTLE)
        closed += 1
    for _ in range(cfg.RECENTER_CLOSE_TRIES):
        if dismiss_stray_windows() is None:
            break
        closed += 1
    return closed

def recenter() -> bool:
    """The bot's /recenter: close what is open (close_open_windows()), respawn the character (it is back where it spawns), and close what the respawn left."""
    if not focus_roblox():
        return False
    closed = close_open_windows()
    respawn_character()
    closed += close_open_windows()
    log_run_event(f"recenter: {closed} window(s) closed, respawned")
    return True

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
        while elapsed < total_duration and not cleared_event.is_set():
            direction = direction_pair[direction_idx % 2]
            logger.debug(f"Step {direction_idx + 1}: {direction} for {step_duration}s...")
            hold_key(direction, step_duration)
            wait_interruptible(cfg.KEY_STEP_GAP)

            direction_idx += 1
            elapsed = time.time() - start_time
    finally:
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
    log_run_event("setup: begins")
    with task_unscrew(interruptible=False, what="setup"):
        done = _setup_steps()
    log_run_event(f"setup: ends, {'every step as it should be' if done else 'some step did not take'}")
    return done

def leave_hub_and_setup() -> bool:
    """The action of /sloop before the loop: leave the Trading Hub if the player is in it, then run Setup. A hub that cannot be left is an error (the status says so and the loop does not
    start): Setup would click in it."""
    if not leave_hub_if_in():
        raise RuntimeError("could not leave the Trading Hub")
    return setup_or_fail()

def setup_or_fail() -> bool:
    """setup_game() for the commands from outside (the bot's /setup): a step that did not take is an error, so that the answer says so."""
    if not setup_game():
        raise RuntimeError("setup did not take: see the failure and the log")
    return True

def _setup_steps() -> bool:
    """Respawn (the pen's check does it when it is on: its walk starts with one and ends with one back in the house), then lock the house, set the backpack's filter and the trade setting - each looked at first and left as it is when it is what it should be, clicked when it is not, and
    looked at again after (a step that did not take is a failure with a screenshot, and the others still run). True if every one is as it should be."""
    for _ in range(cfg.SETUP_STRAY_TRIES):
        if not dismiss_stray_windows():
            break
    if not load_game_config()["pet_pen_enabled"]:
        respawn_character()                                                  # (the pen's walk starts with a respawn of its own, and ends with one back in the house: no third one here)
    elif not check_pet_pen():                                                # (not a failed Setup: said in the log, and looked at again when a pet shows Friend)
        log_run_event("setup: the pet pen could not be checked")
    failed = [name for name, step in (("the house is not locked", lock_house), ("the backpack is not filtered to favorites", set_backpack_filter),
                                      ("trades are not set to No One", disable_trades)) if not step()]
    if failed:
        log_failure("setup: " + "; ".join(failed))
        return False
    logger.info("Setup complete")
    log_run_event("setup: the house is locked, the backpack is filtered to favorites and trades are set to No One")
    return True

def look_until(find: Callable[[Image], Any], seconds: float, poll: float = cfg.SETUP_POLL) -> Any:
    """What `find` returns for a screenshot - one is taken every `poll` seconds - as soon as that is something (not None or False), else None once `seconds` have gone by (there is always at least one
    look). The one waiting loop of the setup, the trades, the sick need and the Pet Pen."""
    deadline = time.time() + seconds
    while True:
        found = find(grab_screen())
        if found or time.time() >= deadline:
            return found or None
        wait_interruptible(poll)

def wait_for_any(names: Sequence[str], seconds: float = cfg.SETUP_UI_WAIT) -> Optional[str]:
    """The first of the crops `names` (POPUP_TEMPLATES) seen on screen, within `seconds`; None if none was."""
    return look_until(lambda img: next((name for name in names if popup_visible(img, name)), None), seconds)

def wait_for_template(name: str, seconds: float = cfg.SETUP_UI_WAIT) -> Optional[Point]:
    """Where the crop `name` is, once it is on screen (within `seconds`); None if it did not come."""
    return look_until(lambda img: popup_found(img, name), seconds)

def lock_house() -> bool:
    """The house's lock button (top of the screen, in the house after the respawn): its Unlock label means it is locked already and nothing is clicked; its Lock label is clicked, and the
    Unlock label must follow. False (logged) if neither is seen, or it did not lock."""
    seen = wait_for_any(("house_unlock", "house_lock"))
    if seen == "house_unlock":
        log_run_event("setup: the house is locked already")
        return True
    if seen is None:
        log_run_event("setup: the house's lock button was not seen")
        return False
    logger.debug("Locking house...")
    hover_click(*cfg.SETUP_LOCK_HOUSE_POS)
    if wait_for_any(("house_unlock",)) is None:
        log_run_event("setup: the house did not lock")
        return False
    log_run_event("setup: the house is locked")
    return True

def open_filter_panel(button: Point = cfg.SETUP_BACKPACK_SETTINGS_POS) -> bool:
    """In the open backpack: its settings button (`button`: the big backpack's, or the pen's small one), and the FILTERS panel that comes up (tried twice). False (logged) if it did not."""
    for _ in range(2):
        logger.debug("Opening the filters...")
        hover_click(*button)
        if wait_for_template("filter_panel") is not None:
            return True
    log_run_event("setup: the backpack's filters did not open")
    return False

def scroll_filters_to_top() -> bool:
    """In the open FILTERS panel: scroll it back up (the wheel over its scroll bar) until its Sort By button is in view. The game keeps the panel where it was scrolled to - the Age rows, after Setup or the pen
    had it there - and then the sort button is not where Setup clicks. False (logged by the caller) if it never came into view."""
    for _ in range(cfg.FILTERS_SCROLL_TRIES + 1):
        if wait_for_any(("filter_sort_default", "filter_sort_favorites"), 0.0) is not None:
            return True
        hover_move(*cfg.FILTERS_SCROLL_POS)
        scroll_up(1)
        wait_interruptible(cfg.UI_SETTLE)
    return False

def sort_is_favorites() -> bool:
    """Whether the open FILTERS panel says Favorites on its sort button (its crop, looked for for SETUP_LOOK_WAIT seconds)."""
    return wait_for_any(("filter_sort_favorites",), cfg.SETUP_LOOK_WAIT) is not None

def log_setup_problem(text: str) -> None:
    """A step of Setup that did not go right: in the run log with the screen as it was (a folder of FAILURE_DIR, not counted as a failure: Setup says the whole of it at its end)."""
    shot = save_failure_report(text)
    log_run_event(text + (f" (screenshot: failures/{shot})" if shot else ""))

def choose_sort_favorites() -> bool:
    """In the open FILTERS panel, whose sort is not Favorites: open the sort menu, click Favorites where its text is, and see the button say Favorites. False (logged) if a step did not happen."""
    logger.debug("Opening sort menu...")
    hover_click(*cfg.SETUP_SORT_MENU_POS)
    option = wait_for_template("filter_option_favorites")
    if option is None:
        log_setup_problem("setup: Favorites was not in the sort menu")
        return False
    hover_click(*option)
    if sort_is_favorites():
        return True
    log_run_event("setup: the sort did not change to Favorites")
    return False

def age_checkbox(img: Image, label: str) -> Optional[tuple[Point, bool]]:
    """Where the checkbox of the Age filter's row `label` (POPUP_TEMPLATES: "age_egg", "age_full_grown" - the crop of its text) is, and whether it is ticked (a tick is purple, AGE_TICK_HSV,
    at least AGE_TICK_MIN_PIXELS of the AGE_CHECKBOX_HALF square round its middle). None if the row is not in view."""
    for name in (label, *cfg.AGE_ALTERNATES.get(label, ())):
        centre = popup_found(img, name)
        if centre is not None:
            break
    else:
        return None
    x = centre[0] - load_templates(cfg.STRAY_WINDOW_DIR)[name].shape[1] // 2 + cfg.AGE_CHECKBOX_DX            # (the box is a fixed way left of where the text starts: the text's width differs)
    half = cfg.AGE_CHECKBOX_HALF
    patch = cv2.cvtColor(img[centre[1] - half:centre[1] + half, x - half:x + half], cv2.COLOR_BGR2HSV)
    ticked = cv2.countNonZero(cv2.inRange(patch, *cfg.AGE_TICK_HSV)) >= cfg.AGE_TICK_MIN_PIXELS
    return (x, centre[1]), ticked

def scroll_filters_to_age() -> bool:
    """In the open FILTERS panel: scroll its list (the wheel over its scroll bar, so that no item's description opens) until the Age rows are in view, FILTERS_SCROLL_TRIES notches at most. False if they never were."""
    for _ in range(cfg.FILTERS_SCROLL_TRIES + 1):
        img = grab_screen()
        if all(age_checkbox(img, label) is not None for label in cfg.AGE_LABELS):
            return True
        hover_move(*cfg.FILTERS_SCROLL_POS)
        scroll_down(1)
        wait_interruptible(cfg.UI_SETTLE)
    return False

def age_filter_is_right() -> bool:
    """In the open FILTERS panel, the Age rows in view: Egg and Full Grown are unticked (the pets the macro is to look after are the ones that are neither, and still grow)."""
    states = [age_checkbox(grab_screen(), label) for label in cfg.AGE_LABELS]
    return all(state is not None and not state[1] for state in states)

def untick_egg_and_full_grown() -> Optional[bool]:
    """In the open FILTERS panel: scroll to the Age rows and untick Egg and Full Grown where they are ticked (a box that is unticked already is left alone), looking at each after its click.
    True if something was clicked (the panel is to be confirmed), False if both were unticked already, None (logged) if the rows were not found or a click did not take."""
    if not scroll_filters_to_age():
        log_setup_problem("setup: the filters' Age rows were not found")
        return None
    clicked = False
    for label in cfg.AGE_LABELS:
        found = age_checkbox(grab_screen(), label)
        if found is None:
            log_run_event(f"setup: the Age row {label} was not found")
            return None
        if not found[1]:
            continue
        hover_click(*found[0])
        wait_interruptible(cfg.UI_SETTLE)
        after = age_checkbox(grab_screen(), label)
        if after is None or after[1]:
            log_run_event(f"setup: the {label} box did not untick")
            return None
        clicked = True
    return clicked

def level_growing_pets() -> bool:
    """Whether Setup is to leave the pets that cannot grow out of the backpack and equip the first that can (the game config's level_growing_pet, the Options subtab's checkbox)."""
    return bool(load_game_config()["level_growing_pet"])

def ensure_pets_tab() -> bool:
    """In the open backpack: the pets tab selected (it is the one the Age rows of the filters are for; the backpack opens on the tab it was last left on, a vehicle's after a ride). Clicked when it is not, up to twice, and looked at after.
    True if it is selected."""
    for _ in range(2):
        if is_backpack_tab_selected(cfg.INVENTORY_PETS_TAB_POS):
            return True
        hover_click(*cfg.INVENTORY_PETS_TAB_POS)
        wait_interruptible(cfg.UI_SETTLE)
    return is_backpack_tab_selected(cfg.INVENTORY_PETS_TAB_POS)

def set_backpack_filter() -> bool:
    """Set the backpack's filters: open it and the FILTERS panel, with the sort Favorites (left alone when it says so, else chosen: choose_sort_favorites()) and, when level_growing_pets(), Egg and
    Full Grown unticked in the Age rows (untick_egg_and_full_grown()); if anything was changed it is confirmed and the panel is opened again to see that both took, and the panel is
    cancelled (one that was only looked at is left as it was). With level_growing_pets() the first pet of the pets tab - one that is neither an egg nor full grown, and so one to level up - is then
    equipped (equip_pet()); without, the Alicorn is, if the pet is not out. Setup does it, and so does a ride that did not clear its need (when Setup did not run, the first vehicle was a car, not
    the helicopter). True if the filters are as they should be at the end."""
    pet_out = bool(find_pet_bar(grab_screen()))
    growing = level_growing_pets()
    ok, panel_open = False, True
    log_run_event("backpack: filters and pet looked at")
    if growing:
        update_game_config(lambda c: c.update(pet_repicked=time.time()))                        # (the pet is picked now, from Setup, a ride or the repick alike, and whether or not every step takes: pet_repick() waits its interval from here, not a second pass at once behind a Setup that failed)
    with backpack_on_purpose():
        logger.debug("Opening backpack...")
        pydirectinput.press(cfg.KEYS["backpack"])
        wait_interruptible(cfg.UI_SETTLE)
        on_pets = ensure_pets_tab() if growing else True                                          # (the Age rows are the pets tab's: not ticked on another one)
        if growing and not on_pets:
            log_setup_problem("setup: the backpack's pets tab could not be selected")
        if open_filter_panel():
            changed, sound = False, True
            if not scroll_filters_to_top():
                log_setup_problem("setup: the filters' panel could not be scrolled up to its Sort By")
            if not sort_is_favorites():
                sound = choose_sort_favorites()
                changed = sound
            if growing and not on_pets:
                sound = False
            elif growing:                                                                         # (whether or not the sort took: a pet that has grown up must not be the first of the list)
                aged = untick_egg_and_full_grown()
                sound = sound and aged is not None
                changed = changed or bool(aged)
            if not sound:
                ok = False
            elif not changed:
                log_run_event("setup: the backpack's filters are as they should be already")
                ok = True
            else:
                hover_click(*cfg.SETUP_CONFIRM_POS)
                wait_interruptible(cfg.UI_SETTLE)
                panel_open = open_filter_panel()                                                  # (looked at again: the Confirm took)
                ok = panel_open and scroll_filters_to_top() and sort_is_favorites() and (not growing or (scroll_filters_to_age() and age_filter_is_right()))     # (the panel is where it was scrolled to: back up to the sort first)
                if ok:
                    log_run_event("setup: the backpack's filters are set")
                else:
                    log_setup_problem("setup: the backpack's filters are not as set after Confirm")
            if panel_open:
                hover_click(*cfg.SETUP_FILTER_CANCEL_POS)                                         # (a panel only looked at - or one that could not be set - is left as it was)
                wait_interruptible(cfg.UI_SETTLE)
        if growing and not on_pets:
            log_run_event("setup: not on the pets tab - no pet is equipped from the list")
        elif growing:
            select_backpack_tab(cfg.INVENTORY_PETS_TAB_POS)
            wait_interruptible(cfg.UI_SETTLE)
            pick = choose_list_pet(stop_at_equipped=True)                                        # (not the Add tile, a grey pet in a listing or one in the pen; the equipped one is kept)
            if pick is None:
                log_run_event("setup: no pet that can be levelled was seen in the list - the pet that is out is kept")
            elif pick[0] == "equipped":
                log_run_event("setup: the equipped pet is the first of the list - kept")
            else:
                equip_pet("the first pet of the list", lambda: pick[1])
        elif not pet_out:
            log_run_event("setup: the pet is not out - equipping the Alicorn")
            equip_pet("the Alicorn", lambda: wait_for_template("pet_alicorn"))
        close_backpack("setup")
    return ok

def find_equip_button(img: Image) -> Optional[Point]:
    """The middle of the green Equip button of the backpack's card of the item clicked, or None (an item that is equipped has another button, an Unequip): the blob of GREEN_BUTTON_COLOR of the
    size of one (EQUIP_BUTTON_SIZE) in EQUIP_BUTTON_BOX, where a card can be (the green plus tile of the pets tab is smaller)."""
    left, top, right, bottom = cfg.EQUIP_BUTTON_BOX
    mask = exact_color_mask(img[top:bottom, left:right], cfg.GREEN_BUTTON_COLOR, cfg.GREEN_BUTTON_TOLERANCE)
    count, _, stats, centres = cv2.connectedComponentsWithStats(mask, connectivity=8)
    (min_w, max_w), (min_h, max_h) = cfg.EQUIP_BUTTON_SIZE
    for i in range(1, count):
        if min_w <= stats[i, cv2.CC_STAT_WIDTH] <= max_w and min_h <= stats[i, cv2.CC_STAT_HEIGHT] <= max_h:
            return left + int(centres[i][0]), top + int(centres[i][1])
    return None

def equip_pet(what: str, where: Callable[[], Optional[Point]]) -> bool:
    """In the open backpack: the pets tab, the pet `what` (named in the log) clicked where `where()` says it is (None: not in view), and the Equip button of its card, found by its colour
    (find_equip_button()). A card that says Unequip needs nothing. Looked at after: the card must say Unequip. True if the pet is equipped."""
    logger.debug("Opening the pets tab...")
    select_backpack_tab(cfg.INVENTORY_PETS_TAB_POS)
    wait_interruptible(cfg.UI_SETTLE)
    point = where()
    if point is None:
        log_run_event(f"setup: {what} was not seen in the pets tab")
        return False
    hover_click(*point)
    wait_interruptible(cfg.UI_SETTLE)
    button = find_equip_button(grab_screen())
    if button is None:
        equipped = wait_for_template("pet_unequip", cfg.SETUP_LOOK_WAIT) is not None
        log_run_event(f"setup: {what} is equipped already" if equipped else f"setup: the card of {what} has no Equip button and does not say Unequip (no pet there?)")
        return equipped
    logger.debug("Equipping the pet...")
    hover_click(*button)
    wait_interruptible(cfg.UI_SETTLE)
    equipped = look_until(lambda img: popup_visible(img, "pet_unequip") or cell_at_is_equipped(img, point), cfg.SETUP_UI_WAIT, cfg.SETUP_POLL) is not None     # (the card says Unequip - or it has closed and the pet's cell has the green frame: in a 10-hour run 3 Equips that took were called failures, 8 s each)
    if equipped:
        log_run_event(f"setup: {what} is equipped")
    else:
        log_setup_problem(f"setup: the card of {what} does not say Unequip after Equip")
    return equipped

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
    """Set the game's trade setting to "no one": the settings (the gear), the Interaction page (when its label is not the one up), and the Trading setting, which is left alone when it says
    No One and else opened and set to No One where that is in the list (found by its text); looked at after, and the settings closed. The macro window covers the settings gear, so it's
    sent to the back for the duration and always brought back, even if the run is stopped partway. True if the setting says No One at the end."""
    logger.debug("Sending the macro window to the back...")
    send_macro_window_to_back()
    ok = False
    try:
        wait_interruptible(cfg.UI_SETTLE)
        if not focus_roblox():
            return False
        if wait_for_any(("settings_title",), 0.0) is None:
            hover_click(*cfg.SETUP_TRADES_SETTINGS_POS)
        if wait_for_template("settings_title") is None:
            log_run_event("setup: the game's settings did not open")
            return False
        if wait_for_any(("settings_page_interaction",), cfg.SETUP_LOOK_WAIT) is None:
            hover_click(*cfg.SETUP_TRADES_MENU_POS)
            page = wait_for_template("settings_option_interaction")
            if page is None:
                log_run_event("setup: Interaction was not in the settings' menu")
            else:
                hover_click(*page)
                if wait_for_any(("settings_page_interaction",)) is None:
                    log_run_event("setup: the settings did not change to Interaction")
        if wait_for_any(("settings_page_interaction",), 0.0) is not None:
            if wait_for_any(("trading_no_one",), cfg.SETUP_LOOK_WAIT) is not None:
                log_run_event("setup: trading says No One already")
                ok = True
            else:
                hover_click(*cfg.SETUP_TRADES_SETTING_POS)
                option = wait_for_template("trading_option_no_one")
                if option is None:
                    log_run_event("setup: No One was not in the trading setting's list")
                else:
                    hover_click(*option)
                    ok = wait_for_any(("trading_no_one",)) is not None
                    log_run_event("setup: trading is set to No One" if ok else "setup: trading does not say No One after the click")
        hover_click(*cfg.SETUP_TRADES_CLOSE_POS)
        wait_interruptible(cfg.UI_SETTLE)
        if wait_for_any(("settings_title",), 0.0) is not None:
            log_run_event("setup: the game's settings did not close")
    finally:
        logger.debug("Bringing the macro window back...")
        bring_macro_window_to_front()
    return ok

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

def scroll_down(clicks: int, direction: int = -1) -> None:
    """Turn the mouse wheel `clicks` notches down (up with `direction` 1; WHEEL_NOTCH units each), over wherever the mouse is, INVENTORY_SCROLL_CHUNK at a time with INVENTORY_SCROLL_PAUSE
    between: a game may take one huge turn of the wheel for a small one."""
    while clicks > 0:
        check_running()
        step = min(clicks, cfg.INVENTORY_SCROLL_CHUNK)
        pyautogui.scroll(direction * step * cfg.WHEEL_NOTCH)         # (Windows takes the wheel in 120ths of a notch: 10 would hardly move a list)
        clicks -= step
        time.sleep(cfg.INVENTORY_SCROLL_PAUSE)

def scroll_up(clicks: int) -> None:
    """scroll_down() the other way."""
    scroll_down(clicks, 1)

def panel_picture(img: Image) -> Image:
    left, top, right, bottom = cfg.INVENTORY_PANEL_BOX
    return img[top:bottom, left:right]

def same_picture(a: Image, b: Image, level: Optional[float] = None) -> bool:
    """Whether two pictures of the panel show the same thing: their mean grey levels differ by less than `level` (INVENTORY_SAME_LEVEL)."""
    return a.shape == b.shape and float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean()) < (cfg.INVENTORY_SAME_LEVEL if level is None else level)

def scroll_shift(before: Image, after: Image) -> Optional[int]:
    """How many pixels the list moved up between two pictures of the panel: the band of `before` at INVENTORY_SHIFT_BAND is looked for in `after` (in the strip at the left and the strip at
    the right of the panel, the better match counts), at least INVENTORY_SHIFT_MATCH good; its place in `after` is how far the list moved. None if it did not move, or the band was not found."""
    if before.shape != after.shape:
        return None
    height, width = before.shape[:2]
    top, bottom = int(height * cfg.INVENTORY_SHIFT_BAND[0]), int(height * cfg.INVENTORY_SHIFT_BAND[1])
    best: Optional[tuple[float, int]] = None
    for left, right in ((0, width // 4), (width * 3 // 4, width)):
        band = before[top:bottom, left:right]
        if float(band.std()) < cfg.INVENTORY_SHIFT_MIN_SPREAD:
            continue
        _, score, _, (_, y) = cv2.minMaxLoc(cv2.matchTemplate(after[:, left:right], band, cv2.TM_CCOEFF_NORMED))
        if score >= cfg.INVENTORY_SHIFT_MATCH and (best is None or score > best[0]):
            best = (float(score), top - y)
    return best[1] if best is not None and best[1] > 0 else None

def inventory_tour() -> bool:
    """Open the backpack, select its pets tab, click the expand button (INVENTORY_EXPAND_POS, checked with detect_backpack_expanded()) and scroll down it a step at a
    time over INVENTORY_SCROLL_POS (a step is as many notches of the wheel as move the list INVENTORY_STEP_PIXELS, measured on the first step: scroll_shift()),
    saving a picture of the panel (INVENTORY_PANEL_BOX) at each step in INVENTORY_DIR - emptied first - until a step moves nothing (the end of the list) or there
    are INVENTORY_MAX_PICTURES. The backpack is closed again. INVENTORY_DONE_PATH says how many pictures and why it ended, however it ended (the bot waits for it).
    True if the tour was made, even if scrolling moved nothing at all (then there is one picture)."""
    shutil.rmtree(cfg.INVENTORY_DIR, ignore_errors=True)
    os.makedirs(cfg.INVENTORY_DIR, exist_ok=True)
    pictures, ended = 0, "stopped"
    send_macro_window_to_back()                              # it is always on top and would be in every picture
    try:
        if not focus_roblox():
            ended = "Roblox could not be focused"
            return False
        with backpack_on_purpose():
            logger.debug("Opening backpack...")
            pydirectinput.press(cfg.KEYS["backpack"])
            wait_interruptible(cfg.UI_SETTLE)
            if not detect_backpack_open(grab_screen()):
                ended = "the backpack did not open"
                return False
            select_backpack_tab(cfg.INVENTORY_PETS_TAB_POS)
            wait_interruptible(cfg.UI_SETTLE)
            hover_click(*cfg.INVENTORY_EXPAND_POS)                         # the orange button next to the settings one: the backpack expands
            wait_interruptible(cfg.INVENTORY_EXPAND_SETTLE)
            if not detect_backpack_expanded(grab_screen()):
                ended = "the backpack did not expand"
                close_backpack("the inventory")
                return False
            wait_interruptible(cfg.INVENTORY_LOAD_WAIT)                    # (the items of an expanded backpack load a while after it opens: the first picture had blank cells)
            hover_move(*cfg.INVENTORY_SCROLL_POS)                          # (with the nudge that makes Roblox see the pointer over the list: a plain move did not, and the wheel turned nothing)
            previous, clicks, measured = None, cfg.INVENTORY_SCROLL_CLICKS, False
            while True:
                picture = panel_picture(grab_screen())
                if previous is not None and same_picture(previous, picture):
                    ended = "the end of the list"
                    break
                if pictures >= cfg.INVENTORY_MAX_PICTURES:
                    ended = f"{pictures} pictures taken"
                    break
                pictures += 1
                cv2.imwrite(os.path.join(cfg.INVENTORY_DIR, f"{pictures:03d}.png"), picture)
                previous = picture
                if measured:
                    scroll_down(clicks)
                else:                                                      # the first step: a notch or so, and the pictures say how far a notch goes
                    probe = min(cfg.INVENTORY_PROBE_CLICKS, clicks)
                    scroll_down(probe)
                    wait_interruptible(cfg.INVENTORY_SCROLL_SETTLE)
                    shift = scroll_shift(picture, panel_picture(grab_screen()))
                    measured = True
                    if shift is not None:
                        clicks = max(1, round(cfg.INVENTORY_STEP_PIXELS * probe / shift))
                        logger.debug(f"The list moves {shift / probe:.0f} px a notch: {clicks} notches a step")
                    else:
                        logger.debug(f"How far a notch goes could not be measured: {clicks} notches a step")
                    scroll_down(max(0, clicks - probe))
            close_backpack("the inventory")
    finally:
        bring_macro_window_to_front()
        with open(cfg.INVENTORY_DONE_PATH, "w", encoding="utf-8") as f:
            json.dump({"pictures": pictures, "ended": ended}, f)
    log_run_event(f"inventory: {pictures} pictures ({ended})")
    return True

# --- the trade listings: the offers on them, and declining one ---

def trade_screen(img: Image) -> Optional[str]:
    """Which screen of the Trading Hub Phone is up in `img`: "dialog" (Are you sure you want to decline?), "board" (the Trading Hub's board of offers: not the phone's, but in the way), "detail" (an offer on its own, with its DECLINE button), "offers" (the list),
    "listings" (MY LISTINGS), "menu" (the TRADING HUB menu), "phone" (the phone's own) - or None. Each is a crop of POPUP_TEMPLATES; the earlier ones are looked for first, since the
    later ones are also there (dimmed) under them."""
    for name, screen in (("decline_dialog", "dialog"), ("trade_board", "board"), ("decline_button", "detail"), ("offers_title", "offers"), ("my_listings", "listings"), ("trade_menu", "menu"), ("trade_phone_home", "phone")):
        if popup_visible(img, name):
            if screen == "menu" and listing_shown(img) is not None:
                return "listings"                    # (outside the Trading Hub the listings' title is "TRADING HUB", not "MY LISTINGS", with the same "LISTING 1/2": the menu's title is the same, and has no such numbers)
            return screen
    return None

def wait_trade_screen(*screens: str) -> bool:
    """Wait up to TRADES_SCREEN_WAIT seconds for one of `screens` to be up (trade_screen())."""
    return bool(look_until(lambda img: trade_screen(img) in screens, cfg.TRADES_SCREEN_WAIT, cfg.TRADES_POLL))

def wait_for_phone_first() -> bool:
    """Wait up to TRADES_SEARCH_MAX_WAIT seconds for the backpack's list to be the search's: the phone's picture in the first slot (POPUP_TEMPLATES["phone_icon"]; in the unfiltered list it
    is in the second, behind the badge). In a capture the list followed the typed text by about two seconds."""
    return bool(look_until(lambda img: popup_visible(img, "phone_icon"), cfg.TRADES_SEARCH_MAX_WAIT, cfg.TRADES_POLL))

def focus_search_box() -> bool:
    """Click the backpack's search box, empty it and type the first letter of TRADES_SEARCH_TEXT, which is only left in it if the box has the focus: its placeholder
    (POPUP_TEMPLATES["search_placeholder"]) is gone then. A payday popup (detect_paycheck()) covers the header and takes the click, and the letters would then be typed into the game
    (a capture: "e" used the world). Only the first letter is typed to find out."""
    detect_paycheck()
    if not click_steps([("Search box", cfg.TRADES_SEARCH_POS)]):
        return False
    type_keys(["backspace"] * len(cfg.TRADES_SEARCH_TEXT))              # (what a search before left in the box)
    type_text(cfg.TRADES_SEARCH_TEXT[:1])
    wait_interruptible(cfg.UI_SETTLE)
    return not popup_visible(grab_screen(), "search_placeholder")

def equip_trade_phone() -> bool:
    """Open the backpack, the Toys tab, type TRADES_SEARCH_TEXT in its search box, click the first item (the phone: the Special row's order changed with the favourites filter, and the
    slot of the badge was taken for it) and Equip, clear the search (a filter left in it would show the other features' backpack items wrong) and close the backpack with its key (the
    phone is then in the hotbar)."""
    with backpack_on_purpose():
        logger.debug("Opening backpack...")
        pydirectinput.press(cfg.KEYS["backpack"])
        wait_interruptible(cfg.UI_SETTLE)
        if detect_paycheck():                                                # (a payday popup covers the backpack's header and takes the clicks)
            wait_interruptible(cfg.UI_SETTLE)
        if not click_steps([("Toys tab", cfg.TRADE_HUB_TOYS_TAB_POS)]):
            return False
        card = None
        searched = False
        for attempt in range(cfg.TRADES_EQUIP_TRIES):
            again = " - trying again" if attempt + 1 < cfg.TRADES_EQUIP_TRIES else ""
            item = popup_found(grab_screen(), "phone_item")                  # (the primary way: its picture, wherever it is in the grid, is clicked)
            if item is not None:
                hover_click(*item)
                wait_interruptible(cfg.UI_SETTLE)
                card = popup_found(grab_screen(), "phone_card")
                if card is not None:
                    break
                log_run_event("trade listings: the item clicked is not the Trading Hub Phone" + again)
                continue
            log_run_event("trade listings: the phone's picture is not in the Toys tab, searching for it")
            searched = True
            if not focus_search_box():
                log_run_event("trade listings: the backpack's search box did not take the focus" + again)
                continue
            type_text(cfg.TRADES_SEARCH_TEXT[1:])
            if not wait_for_phone_first():                                   # (the list follows the text a couple of seconds late: a click before it was the badge's, the item that was first)
                log_run_event("trade listings: the search did not leave the phone first" + again)
                continue
            hover_click(*cfg.TRADE_HUB_PHONE_POS)                           # the first item of what the search leaves: the phone
            wait_interruptible(cfg.UI_SETTLE)
            card = popup_found(grab_screen(), "phone_card")
            if card is not None:
                break
            log_run_event("trade listings: the item clicked is not the Trading Hub Phone" + again)
        if card is not None:
            hover_click(card[0] + cfg.TRADES_EQUIP_OFFSET[0], card[1] + cfg.TRADES_EQUIP_OFFSET[1])      # (its Equip, where the card is: never the badge's)
            wait_interruptible(cfg.UI_SETTLE)
        if searched and click_steps([("Search box", cfg.TRADES_SEARCH_POS)]):
            type_keys(["backspace"] * len(cfg.TRADES_SEARCH_TEXT))          # (cleared: a filter left in it would show the other features' backpack items wrong)
            hover_click(*cfg.TRADES_UNFOCUS_POS)                            # (out of the box: the backpack key would be typed into it)
            wait_interruptible(cfg.UI_SETTLE)
        close_backpack("the Trading Hub Phone")
    wait_interruptible(cfg.UI_SETTLE)
    return card is not None and popup_visible(grab_screen(), "trade_phone_hotbar")

def type_keys(keys: Sequence[str]) -> None:
    """Press `keys` one after the other, TYPE_KEY_DELAY apart, into whatever text box has the focus. The macro's own hotkeys are not acted on meanwhile (state.typing): the stop
    key is a letter, and "phone" has it."""
    state.typing = True
    try:
        for key in keys:
            check_running()
            pydirectinput.press(key)
            time.sleep(cfg.TYPE_KEY_DELAY)
    finally:
        state.typing = False

def type_text(text: str) -> None:
    """type_keys() of the letters of `text`."""
    type_keys(list(text))

def open_trade_listings() -> bool:
    """Get to MY LISTINGS: from wherever the phone is (a screen further in is gone back from), else equip the phone if it is not in the hotbar, use it with a click in the world, and
    go TRADE LISTINGS and VIEW MY LISTINGS. False (logged) if a screen did not come up."""
    if not focus_roblox():
        return False
    screen = trade_screen(grab_screen())
    if screen == "board":
        hover_click(*cfg.STRAY_WINDOWS["trade_board"]["close_pos"])        # the board of offers is closed, whatever is asked: the phone cannot be used under it
        wait_interruptible(cfg.UI_SETTLE)
        screen = trade_screen(grab_screen())
        if screen == "board":
            log_run_event("trade listings: the board of offers could not be closed")
            return False
    if screen == "dialog":
        hover_click(*cfg.EXIT_STAY_POS)                                  # (Cancel: the same place as the exit dialog's)
        wait_interruptible(cfg.UI_SETTLE)
        screen = trade_screen(grab_screen())
    for _ in range(2):                                                   # (an offer on its own, then the list)
        if screen in ("detail", "offers"):
            hover_click(*cfg.TRADES_BACK_POS)
            wait_interruptible(cfg.UI_SETTLE)
            screen = trade_screen(grab_screen())
    if screen is None:
        if not popup_visible(grab_screen(), "trade_phone_hotbar") and not equip_trade_phone():
            log_run_event("trade listings: the Trading Hub Phone could not be equipped")
            return False
        hover_click(*cfg.TRADE_HUB_USE_POS)                              # (a held toy is used by a click anywhere in the world)
        if not wait_trade_screen("phone"):
            log_run_event("trade listings: the phone did not come up")
            return False
        screen = "phone"
    if screen == "phone":
        hover_click(*cfg.TRADES_PHONE_LISTINGS_POS)
        if not wait_trade_screen("menu", "listings"):                        # (from the house a player with listings gets them at once: the menu was waited for 4 s and the tour ended, in a capture)
            log_run_event("trade listings: the Trading Hub menu did not come up")
            return False
        screen = trade_screen(grab_screen())
    if screen == "menu":
        hover_click(*cfg.TRADES_MENU_VIEW_POS)
        if not wait_trade_screen("listings"):
            log_run_event("trade listings: MY LISTINGS did not come up")
            return False
    return trade_screen(grab_screen()) == "listings"

def listing_shown(img: Image) -> Optional[int]:
    """Which listing MY LISTINGS shows in `img` (its "LISTING 1/2" or "2/2"), or None."""
    for number in cfg.TRADES_LISTINGS:
        if popup_visible(img, f"listing_{number}_of_2"):
            return number
    return None

def select_listing(number: int) -> bool:
    """On MY LISTINGS, show listing `number` (the arrow goes on to the other one): False if it does not come up - there is no such listing."""
    for _ in range(len(cfg.TRADES_LISTINGS)):
        if listing_shown(grab_screen()) == number:
            return True
        hover_click(*cfg.TRADES_NEXT_POS)
        wait_interruptible(cfg.UI_SETTLE)
    return listing_shown(grab_screen()) == number

def visible_offers(img: Image) -> list[int]:
    """The y (top to bottom) of the middle of the ACCEPT button of each offer row that is whole in the list. The buttons looked for are the red DECLINE ones (the blobs of TRADES_BUTTON_BOX
    of the size of one, TRADES_BUTTON_SIZE), TRADES_DECLINE_DY below: an "Exact Match" offer's row is green, and so is its ACCEPT button, which is lost in it. A row is whole when its box
    (TRADES_ROW_BOX) is inside the list's top and bottom."""
    left, top, right, bottom = cfg.TRADES_BUTTON_BOX
    hsv = cv2.cvtColor(img[top:bottom, left:right], cv2.COLOR_BGR2HSV)
    mask = cv2.bitwise_or(cv2.inRange(hsv, *cfg.TRADES_BUTTON_HSV[0]), cv2.inRange(hsv, *cfg.TRADES_BUTTON_HSV[1]))
    count, _, stats, centres = cv2.connectedComponentsWithStats(mask, connectivity=8)
    (min_w, max_w), (min_h, max_h) = cfg.TRADES_BUTTON_SIZE
    found = []
    for i in range(1, count):
        if min_w <= stats[i, cv2.CC_STAT_WIDTH] <= max_w and min_h <= stats[i, cv2.CC_STAT_HEIGHT] <= max_h:
            y = top + int(centres[i][1]) - cfg.TRADES_DECLINE_DY
            if y + cfg.TRADES_ROW_BOX[1] >= top and y + cfg.TRADES_ROW_BOX[3] <= bottom:
                found.append(y)
    return sorted(found)

def offer_row(img: Image, y: int) -> Image:
    """The picture of the offer row whose ACCEPT button's middle is at `y` (TRADES_ROW_BOX)."""
    left, top, right, bottom = cfg.TRADES_ROW_BOX
    return img[y + top:y + bottom, left:right]

def row_is(img: Image, y: int, saved: Image) -> bool:
    """Whether the offer row of `img` whose ACCEPT button's middle is at `y` looks like the picture `saved` (at least TRADES_ROW_MATCH)."""
    row = offer_row(img, y)
    return row.shape == saved.shape and float(cv2.matchTemplate(row, saved, cv2.TM_CCOEFF_NORMED)[0][0]) >= cfg.TRADES_ROW_MATCH

class OfferList:
    """Which offer is which in the offers list as it is scrolled, by where it is in the list - never by how it looks, since offers that look alike (the same eggs on both sides) were
    taken for each other. The rows are TRADES_ROW_PITCH apart, so how far a step moved the list is told, to within a row, by where the first row of the picture before is among the
    rows of the picture after (a step is shorter than a row). look() takes each picture in turn; an offer's number is its row counted from the first row seen (the top of the list)."""
    def __init__(self) -> None:
        self.ys: Optional[list[int]] = None          # the rows of the last picture
        self.origin: Optional[int] = None            # where the first row was, in the first picture
        self.offset = 0                              # how far the list has been moved
        self.lost = False                            # a step that no row could be followed over

    def look(self, img: Image) -> list[tuple[int, int]]:
        """(number, y) of each row that is whole in `img`, top to bottom."""
        ys = visible_offers(img)
        if self.ys is not None:
            if self.ys and ys:
                self.offset += (self.ys[0] - ys[0]) % cfg.TRADES_ROW_PITCH
            else:
                self.lost = True
        self.ys = ys
        if self.origin is None and ys:
            self.origin = ys[0]
        return [(round((self.offset + y - self.origin) / cfg.TRADES_ROW_PITCH) + 1, y) for y in ys] if self.origin is not None else []

def panel_of(img: Image) -> Image:
    left, top, right, bottom = cfg.TRADES_PANEL_BOX
    return img[top:bottom, left:right]

def grab_still() -> Image:
    """grab_screen() once the phone's panel has stopped moving (two looks TRADES_POLL apart alike), after TRADES_SCROLL_MAX_WAIT seconds at the latest: the list glides for a moment after
    a turn of the wheel, and a picture of it then is of a place it is not at."""
    deadline = time.time() + cfg.TRADES_SCROLL_MAX_WAIT
    img = grab_screen()
    while True:
        wait_interruptible(cfg.TRADES_POLL)
        again = grab_screen()
        if same_picture(panel_of(img), panel_of(again), cfg.TRADES_SAME_LEVEL) or time.time() >= deadline:
            return again
        img = again

def open_offers(number: int) -> bool:
    """From MY LISTINGS, show listing `number` and press its OFFERS: False (logged) if the listing is not there or no list of offers came up (a listing with none)."""
    if not select_listing(number):
        log_run_event(f"trade listings: listing {number} is not there")
        return False
    hover_click(*cfg.TRADES_OFFERS_POS)
    if not wait_trade_screen("offers"):
        log_failure(f"trade listings: the offers of listing {number} did not come up (screen: {trade_screen(grab_screen())})")       # (with the screenshot: what is on screen then is what to look at)
        return False
    hover_move(*cfg.TRADES_SCROLL_POS)
    return True

def draw_offer_number(picture: Image, y: int, number: int) -> None:
    """Write #`number` over the picture of the panel (TRADES_PANEL_BOX), by the row whose ACCEPT button's middle is at `y` (screen coordinates), in white with a black edge."""
    panel_left, panel_top = cfg.TRADES_PANEL_BOX[:2]
    at = (cfg.TRADES_ROW_BOX[0] - panel_left + 12, y - panel_top - 12)
    for colour, width in (((0, 0, 0), 6), ((255, 255, 255), 2)):
        cv2.putText(picture, f"#{number}", at, cv2.FONT_HERSHEY_SIMPLEX, 1.3, colour, width, cv2.LINE_AA)

def offers_tour(listing: int) -> dict[str, Any]:
    """On the offers list of listing `listing`: scroll it a step at a time (TRADES_SCROLL_CLICKS notches, a step that is shorter than an offer, so that none is skipped), number the
    offers by where they are in the list (OfferList), and keep a picture of the panel (TRADES_DIR/L<listing>_<n>.png) at each step that showed one not seen before, with the pictures of the
    rows (TRADES_ROWS_DIR) that /decline checks. Ends at the end of the list (a step that moves nothing) or at TRADES_MAX_STEPS. Returns how it went."""
    seen: set[int] = set()
    offers, pictures, ended = OfferList(), 0, "the end of the list"
    previous = None
    for _ in range(cfg.TRADES_MAX_STEPS):
        img = grab_still()
        panel = panel_of(img)
        if previous is not None and same_picture(previous, panel, cfg.TRADES_SAME_LEVEL):
            break
        previous = panel.copy()
        fresh = False
        marked = panel.copy()
        for number, y in offers.look(img):
            if number not in seen:
                seen.add(number)
                fresh = True
                cv2.imwrite(os.path.join(cfg.TRADES_ROWS_DIR, f"L{listing}_{number:02d}.png"), offer_row(img, y))
            draw_offer_number(marked, y, number)
        if fresh:
            pictures += 1
            cv2.imwrite(os.path.join(cfg.TRADES_DIR, f"L{listing}_{pictures:02d}.png"), marked)
        scroll_down(cfg.TRADES_SCROLL_CLICKS)
    else:
        ended = f"{cfg.TRADES_MAX_STEPS} steps made"
    return {"offers": len(seen), "pictures": pictures, "ended": ended + (" (the place in the list was lost at a step: some offers may be numbered wrong)" if offers.lost else "")}

def close_trade_phone() -> None:
    """The red X of the screen that is up (any of them but the offer on its own, which goes back first): the phone is away."""
    if trade_screen(grab_screen()) in ("detail",):
        hover_click(*cfg.TRADES_BACK_POS)
        wait_interruptible(cfg.UI_SETTLE)
    if trade_screen(grab_screen()) in ("offers", "listings", "menu", "phone"):
        hover_click(*cfg.TRADES_CLOSE_POS)

def write_json_file(path: str, data: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)

def trades_tour() -> bool:
    """The bot's /trades: open MY LISTINGS through the phone, and for each listing of TRADES_LISTINGS its offers (offers_tour()), the pictures kept in TRADES_DIR (emptied first), the
    phone put away. TRADES_DONE_PATH says what each listing gave and what went wrong, however it ends (the bot waits for it). True if the listings were looked at."""
    shutil.rmtree(cfg.TRADES_DIR, ignore_errors=True)
    os.makedirs(cfg.TRADES_ROWS_DIR, exist_ok=True)
    result: dict[str, Any] = {"listings": {}, "problem": "stopped"}
    send_macro_window_to_back()                              # it is always on top and would be in every picture
    try:
        if not open_trade_listings():
            result["problem"] = "the trade listings could not be opened (see the log)"
            log_failure(f"trade listings: could not be opened (screen: {trade_screen(grab_screen())})")
            return False
        for listing in cfg.TRADES_LISTINGS:
            if open_offers(listing):
                result["listings"][str(listing)] = offers_tour(listing)
                hover_click(*cfg.TRADES_BACK_POS)
                wait_trade_screen("listings")
            else:
                result["listings"][str(listing)] = {"offers": 0, "pictures": 0, "ended": "no list of offers (no such listing, or no offers)"}
                if trade_screen(grab_screen()) not in ("listings",):
                    break
        close_trade_phone()
        result["problem"] = ""
    finally:
        bring_macro_window_to_front()
        write_json_file(cfg.TRADES_DONE_PATH, result)
    log_run_event("trade listings: " + ", ".join(f"listing {k}: {v['offers']} offers" for k, v in result["listings"].items()))
    return True

def decline_offer(listing: int, number: int) -> bool:
    """The bot's /decline: decline offer `number` of listing `listing` as /trades numbered it. The offer is found by its place in the list (OfferList), scrolling a step at a time
    (TRADES_SCROLL_CLICKS notches), and must look like its picture from that tour (TRADES_ROWS_DIR) - one that does not (it was declined, taken back, or the list is not the one that was photographed) is not
    touched: then nothing is clicked and the answer says so. The row is opened (an offer on its own), its DECLINE clicked, and Yes in "Are you sure?" - each only when the
    screen it is on is seen. The offer as it was is kept (TRADES_DECLINED_PATH). TRADES_DECLINE_DONE_PATH says how it ended, however it does. True if declined."""
    result: dict[str, Any] = {"listing": listing, "offer": number, "declined": False, "why": "stopped"}
    send_macro_window_to_back()
    try:
        row = cv2.imread(os.path.join(cfg.TRADES_ROWS_DIR, f"L{listing}_{number:02d}.png"))
        if row is None:
            result["why"] = "there is no picture of that offer: /trades first"
            return False
        if not open_trade_listings() or not open_offers(listing):
            result["why"] = "the offers of that listing could not be opened (see the log)"
            return False
        y, previous, offers = None, None, OfferList()
        for _ in range(cfg.TRADES_MAX_STEPS):
            img = grab_still()
            panel = panel_of(img)
            if previous is not None and same_picture(previous, panel, cfg.TRADES_SAME_LEVEL):
                break
            previous = panel.copy()
            y = next((at for found, at in offers.look(img) if found == number), None)
            if y is not None:
                break
            scroll_down(cfg.TRADES_SCROLL_CLICKS)
        if y is not None and not row_is(img, y, row):
            y = None                                                     # (the offer at that place is not the one that was photographed: the list changed)
        if y is None:
            result["why"] = "that offer is not in the list any more (it changed since /trades: /trades again)"
            close_trade_phone()
            return False
        if not decline_row(y, result):
            return False
        result["declined"], result["why"] = True, ""
        log_run_event(f"trade listings: declined offer {number} of listing {listing}")
        return True
    finally:
        bring_macro_window_to_front()
        write_json_file(cfg.TRADES_DECLINE_DONE_PATH, result)

def decline_row(y: int, result: dict[str, Any]) -> bool:
    """Decline the offer whose row has its ACCEPT button's middle at `y` of the list on screen: open it (an offer on its own), keep its picture (TRADES_DECLINED_PATH), press DECLINE
    and Yes - each only once the screen it is on is seen. False, with `result["why"]` saying where it ended, if one was not."""
    hover_click(cfg.TRADES_ROW_CLICK_X, y + cfg.TRADES_ROW_CLICK_DY)
    if not wait_trade_screen("detail"):
        result["why"] = "the offer did not open"
        return False
    cv2.imwrite(cfg.TRADES_DECLINED_PATH, panel_of(grab_screen()))
    hover_click(*cfg.TRADES_DETAIL_DECLINE_POS)
    if not wait_trade_screen("dialog"):
        result["why"] = "the \"Are you sure?\" dialog did not come up (nothing was declined)"
        return False
    hover_click(*cfg.TRADES_DECLINE_YES_POS)
    wait_interruptible(cfg.UI_SETTLE)
    if trade_screen(grab_screen()) == "dialog":
        result["why"] = "the dialog is still up after Yes"
        return False
    return True

def wait_list_settled(top: int) -> Optional[list[int]]:
    """After a decline: the rows (visible_offers()) of the list once it has stopped moving and its first row is at `top` again or none is left - the offer being declined is shown for a while
    with no ACCEPT button, and the row under it is the first in sight until it is gone. None if that did not happen in TRADES_DECLINE_MAX_WAIT seconds."""
    deadline = time.time() + cfg.TRADES_DECLINE_MAX_WAIT
    while True:
        ys = visible_offers(grab_still())
        if not ys or ys[0] <= top + cfg.TRADES_TOP_TOLERANCE:
            return ys
        if time.time() >= deadline:
            return None

def decline_all(listing: int) -> bool:
    """The bot's /decline with `*`: decline every offer of listing `listing`, the first in sight each time (decline_row()) until none is left, at most TRADES_MAX_OFFERS. TRADES_DECLINE_DONE_PATH
    says how many were declined and how it ended, however it does. True if the list was emptied."""
    result: dict[str, Any] = {"listing": listing, "offer": "*", "declined": False, "count": 0, "why": "stopped"}
    send_macro_window_to_back()
    try:
        if not open_trade_listings() or not open_offers(listing):
            result["why"] = "the offers of that listing could not be opened (see the log)"
            return False
        ys = visible_offers(grab_still())
        top = ys[0] if ys else 0
        while ys:
            if result["count"] >= cfg.TRADES_MAX_OFFERS:
                result["why"] = f"stopped after {cfg.TRADES_MAX_OFFERS} offers (there may be more)"
                return False
            if not decline_row(ys[0], result):
                return False
            result["count"] += 1
            settled = wait_list_settled(top)
            if settled is None:
                result["why"] = "the list did not settle after a decline"
                return False
            ys = settled
        result["declined"], result["why"] = result["count"] > 0, "" if result["count"] else "there are no offers in that listing"
        log_run_event(f"trade listings: declined {result['count']} offers of listing {listing}")
        return result["declined"]
    finally:
        bring_macro_window_to_front()
        write_json_file(cfg.TRADES_DECLINE_DONE_PATH, result)

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
    match = re.search(r"privateServerLinkCode=([0-9A-Za-z]{32})", link, re.I)
    if match:
        return "LinkCode", match.group(1)
    match = re.search(r"share\?code=([0-9A-Za-z]{32})&type=Server", link, re.I)
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
        logger.debug("Focus_pet: nothing moved, pet not found - looking for its name bar")
        return focus_pet_by_bar(img_b, click_duration)
    for i, (x, y, w, h, _) in enumerate(blobs, 1):
        logger.debug(f"Focus_pet: clicking #{i} at ({x + w // 2}, {y + h // 2})...")
        hover_click(x + w // 2, y + h // 2, duration=click_duration)
        wait_interruptible(cfg.FOCUS_PET_CLICK_SETTLE)
        if detect_pet_focus(grab_screen()):
            logger.debug(f"Focus_pet: focused after click #{i}, no more clicks needed")
            break
    else:
        wait_interruptible(cfg.FOCUS_PET_MENU_WAIT)
        if not detect_pet_focus(grab_screen()):                          # none of what moved was the pet: its name bar, if it shows
            focus_pet_by_bar(grab_screen(), click_duration)
    return True

def focus_pet_reliably(click_duration: float = cfg.CLICK_MOVE_DURATION) -> bool:
    """focus_pet() and then the pet's view is waited for (PET_FOCUS_VIEW_WAIT: the first three chooses of a run clicked the pet and looked at once, found no button, and the view came a few seconds
    after); when it does not come the E key is pressed - the pet's own prompt, which opens its menu when the player is by it, whatever the pet is and wherever its name pill is - and the whole is tried
    again, PET_FOCUS_TRIES times. True once the view is up."""
    for attempt in range(1, cfg.PET_FOCUS_TRIES + 1):
        if detect_pet_focus(grab_screen()):
            return True
        if focus_pet(click_duration) and look_until(detect_pet_focus, cfg.PET_FOCUS_VIEW_WAIT, cfg.PET_FOCUS_POLL) is not None:
            return True
        pydirectinput.press(cfg.KEYS["interact"])
        if look_until(detect_pet_focus, cfg.PET_FOCUS_VIEW_WAIT, cfg.PET_FOCUS_POLL) is not None:
            log_run_event("pet: focused with the E key")
            return True
        log_run_event(f"pet: not focused (try {attempt} of {cfg.PET_FOCUS_TRIES})")
    return False

def find_pet_bar(img: Image) -> list[Point]:
    """Where to click the pet by the name bar over it, for a pet that stands still (nothing moves for focus_pet() to find): the purple part of the bar by its hue (PET_BAR_HSV), a wide
    short blob (PET_BAR_ASPECT, PET_BAR_AREA) with white at its right (PET_BAR_WHITE_*), whatever its words and outline. The pet is a different number of bar heights under the bar's bottom
    edge with its pose (PET_BAR_BELOW_STEPS: 4 on one frame, 1.5 on a lying pet near the camera), at the join of the purple and the white: those points, the first to try first, for the
    biggest such bar. No points if there is no bar."""
    mask = cv2.inRange(cv2.cvtColor(img, cv2.COLOR_BGR2HSV), cfg.PET_BAR_HSV[0], cfg.PET_BAR_HSV[1])
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    best: Optional[tuple[int, list[Point]]] = None
    for i in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[i])
        if not cfg.PET_BAR_AREA[0] <= area <= cfg.PET_BAR_AREA[1] or not cfg.PET_BAR_ASPECT[0] <= w / h <= cfg.PET_BAR_ASPECT[1]:
            continue
        beside = img[y:y + h, x + w:x + w + int(w * 0.9)]
        if beside.size == 0 or float((beside.min(axis=2) >= cfg.PET_BAR_WHITE_LEVEL).mean()) < cfg.PET_BAR_WHITE_FRACTION:
            continue
        if best is None or area > best[0]:
            best = (area, [(x + w, int(y + h + below * h)) for below in cfg.PET_BAR_BELOW_STEPS])
    return best[1] if best is not None else []

def find_pet_pill(img: Image, ignore: Optional[Box] = None) -> list[Point]:
    """Where to click the pet by its name pill, whatever the pet and however much it has grown: the pill is bright purple where it is filled and dark purple beyond (PET_PILL_HSV, the white text
    closed over, PET_PILL_CLOSE), a wide short blob (PET_PILL_ASPECT, PET_PILL_AREA, PET_PILL_FILL) below the game's own buttons (PET_PILL_MIN_Y) and outside `ignore` (the macro's window). The pet is
    centred under it, PET_BAR_BELOW_STEPS pill heights below its bottom edge (the biggest pill first). No points if there is none."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.bitwise_or(cv2.inRange(hsv, *cfg.PET_PILL_HSV[0]), cv2.inRange(hsv, *cfg.PET_PILL_HSV[1]))
    mask[:cfg.PET_PILL_MIN_Y] = 0
    if ignore is not None:
        left, top, right, bottom = ignore
        mask[top:bottom, left:right] = 0
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((cfg.PET_PILL_CLOSE, cfg.PET_PILL_CLOSE), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    best: Optional[tuple[int, list[Point]]] = None
    for i in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[i])
        if not cfg.PET_PILL_AREA[0] <= area <= cfg.PET_PILL_AREA[1] or not cfg.PET_PILL_ASPECT[0] <= w / h <= cfg.PET_PILL_ASPECT[1] or area < cfg.PET_PILL_FILL * w * h:
            continue
        if best is None or area > best[0]:
            best = (area, [(x + w // 2, int(y + h + below * h)) for below in cfg.PET_BAR_BELOW_STEPS])
    return best[1] if best is not None else []

def focus_pet_by_bar(img: Image, click_duration: float = cfg.CLICK_MOVE_DURATION) -> bool:
    """Click the pet by the name bar over it (find_pet_bar(), or find_pet_pill() when that finds none, each place in turn) and say whether that focused it (detect_pet_focus())."""
    points = find_pet_bar(img) or find_pet_pill(img, macro_window_reference_rect())
    if not points:
        logger.debug("Focus_pet: no name bar either")
        return False
    for point in points:
        logger.debug(f"Focus_pet: clicking under the name bar at {point}...")
        hover_click(*point, duration=click_duration)
        wait_interruptible(cfg.FOCUS_PET_CLICK_SETTLE)
        if detect_pet_focus(grab_screen()):
            log_run_event("pet: found by its name bar, focused")
            return True
    log_run_event("pet: found by its name bar, not focused")
    return False

def is_backpack_tab_selected(pos: Point) -> bool:
    """True if the backpack category tab at `pos` is already selected (orange)."""
    x, y = pos
    hx, hy = cfg.TAB_HALF_SIZE
    tile = grab_screen()[y - hy:y + hy, x - hx:x + hx]
    mask = exact_color_mask(tile, cfg.TAB_SELECTED_COLOR, cfg.TAB_SELECTED_TOLERANCE)
    return bool(np.count_nonzero(mask) >= cfg.TAB_SELECTED_MIN_PIXELS)

def select_backpack_tab(pos: Point) -> None:
    """Click the backpack category tab at `pos` unless it's already selected (a click on a selected tab does nothing; what expands the backpack is the
    orange button next to the settings one, INVENTORY_EXPAND_POS)."""
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

# The needs that are a fixed sequence (catch, pet, ride) are tables of steps, run by SequenceNeedHandler: (action, *arguments), the arguments naming the
# magic_numbers constants (read when the step runs, so a retuned value is used) or the KEYS label. The actions are STEP_ACTIONS; a step that returns False
# ends the need as not done.
STEP_ACTIONS: dict[str, Callable[..., Optional[bool]]] = {
    "say": lambda text: logger.debug(text.format_map(vars(cfg))),                                   # a debug line, {CONSTANT} filled in
    "press": lambda key: pydirectinput.press(cfg.KEYS[key]),
    "wait": lambda duration: wait_interruptible(getattr(cfg, duration)),
    "sleep": lambda duration: time.sleep(getattr(cfg, duration)),                                  # (not interruptible: a pause inside a mouse movement)
    "tab": lambda pos: select_backpack_tab(getattr(cfg, pos)),
    "click": lambda pos: hover_click(*getattr(cfg, pos)),
    "close_backpack": lambda step: close_backpack(step),
    "hold": lambda key, duration: hold_key(cfg.KEYS[key], getattr(cfg, duration)),
    "walk": lambda keys, duration, need: walk_alternating((cfg.KEYS[keys[0]], cfg.KEYS[keys[1]]), getattr(cfg, duration), need_name=need),
    "focus_pet": lambda duration: focus_pet(getattr(cfg, duration)),
    "click_centre": lambda duration: hover_click(cfg.REFERENCE_CENTER_X, cfg.REFERENCE_CENTER_Y, duration=getattr(cfg, duration)),
    "equip_vehicle": lambda: equip_favorite_vehicle(),
    "swipe_down": lambda: pet_swipe_down(),
    "with_backpack": lambda steps: run_steps_with_backpack(steps),                                # the steps with the backpack open on purpose (the in-task check leaves it alone)
    "repeat": lambda count, label, steps: repeat_steps(getattr(cfg, count), label, steps),
}

def run_steps(steps: Sequence[tuple[Any, ...]]) -> bool:
    """Do the steps in order (see STEP_ACTIONS); False as soon as one says it was not done."""
    for action, *arguments in steps:
        if STEP_ACTIONS[action](*arguments) is False:
            return False
    return True

def run_steps_with_backpack(steps: Sequence[tuple[Any, ...]]) -> bool:
    with backpack_on_purpose():
        return run_steps(steps)

def repeat_steps(count: int, label: str, steps: Sequence[tuple[Any, ...]]) -> bool:
    for i in range(count):
        logger.debug(f"{label} {i + 1}/{count}...")
        if not run_steps(steps):
            return False
    return True

def pet_swipe_down() -> None:
    """Hold the mouse button down at PET_SWIPE_START_OFFSET from the middle of the screen and move it, step by step (much more reliable for games than one
    jump), to PET_SWIPE_END_OFFSET over PET_SWIPE_DURATION; the button is let go of however it ends."""
    start_y = cfg.REFERENCE_CENTER_Y + cfg.PET_SWIPE_START_OFFSET
    end_y = cfg.REFERENCE_CENTER_Y + cfg.PET_SWIPE_END_OFFSET
    pydirectinput.moveTo(*to_screen(cfg.REFERENCE_CENTER_X, start_y))
    time.sleep(cfg.PET_SETTLE_DELAY)
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
        pydirectinput.mouseUp()                                                                    # always, even if interrupted

class SequenceNeedHandler(NeedHandler):
    """A need that is a table of steps (`steps`, run by run_steps()) once Roblox is focused; `around()` is what the steps are done inside of."""
    label = ""
    steps: tuple[tuple[Any, ...], ...] = ()

    def around(self) -> ContextManager[Any]:
        return contextlib.nullcontext()

    def handle(self) -> bool:
        logger.info(f"{self.label} need")
        if not focus_roblox():
            return False
        with self.around():
            if not run_steps(self.steps):
                return False
        logger.info(f"{self.label} complete")
        return True

class CatchNeedHandler(SequenceNeedHandler):
    """The 'catch' need requires opening backpack, equipping a toy, throwing it
    (clicking) CATCH_THROW_COUNT times, then unequipping it."""
    label = "Catch"
    steps = (
        ("with_backpack", (
            ("say", "Opening backpack..."), ("press", "backpack"), ("wait", "UI_SETTLE"),
            ("say", "Opening toys..."), ("tab", "CATCH_TOYS_POS"), ("wait", "UI_SETTLE"),
            ("say", "Selecting squeaky toy..."), ("click", "CATCH_SQUEAKY_TOY_POS"), ("wait", "UI_SETTLE"),
            ("say", "Equipping toy..."), ("click", "CATCH_EQUIP_POS"), ("wait", "UI_SETTLE"),
            ("say", "Closing backpack..."), ("close_backpack", "equipping the toy"))),
        ("say", "Waiting {CATCH_WAIT_AFTER_EQUIP}s before throwing..."), ("wait", "CATCH_WAIT_AFTER_EQUIP"),
        ("hold", "zoom_in", "CATCH_ZOOM_DURATION"),                                                # zoomed in, to avoid focusing the pet on a toy throw
        ("repeat", "CATCH_THROW_COUNT", "Throw", (("click", "EMPTY_POS"), ("wait", "CATCH_EMOTE_DELAY"))),   # click empty space to throw, with a delay between throws
        ("say", "Unequipping toy..."), ("click", "CATCH_UNEQUIP_POS"),
    )

class PetNeedHandler(SequenceNeedHandler):
    """The 'pet' need: click to focus the pet, then hold the mouse button
    down and swipe it once down from just above the center of the screen to
    just below it, over PET_SWIPE_DURATION."""
    label = "Pet"
    steps = (
        ("say", "Focusing pet..."), ("focus_pet", "PET_FOCUS_CLICK_DURATION"), ("wait", "UI_SETTLE"),
        ("say", "Petting: one swipe down over {PET_SWIPE_DURATION}s..."),
        ("click_centre", "PET_FOCUS_CLICK_DURATION"), ("sleep", "PET_SETTLE_DELAY"),
        ("swipe_down",),
    )

    def around(self) -> ContextManager[Any]:
        return wanting_pet_focus()

def find_choose_disc(img: Image) -> Optional[Point]:
    """Where the choose button is by its look when its exact colour (CHOOSE_BUTTON_COLOR) is not on screen: a disc that shades from purple to magenta (CHOOSE_DISC_HSV), the
    biggest roundish blob of that hue (CHOOSE_DISC_AREA, CHOOSE_DISC_ROUNDNESS) - not its white outline, which is not always there, nor the "?" in it, which can change: both
    only make a hole or an edge in the disc. Its middle (x, y), or None."""
    mask = cv2.inRange(cv2.cvtColor(img, cv2.COLOR_BGR2HSV), cfg.CHOOSE_DISC_HSV[0], cfg.CHOOSE_DISC_HSV[1])
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((cfg.CHOOSE_DISC_CLOSE, cfg.CHOOSE_DISC_CLOSE), np.uint8))
    count, _, stats, centres = cv2.connectedComponentsWithStats(mask, connectivity=8)
    best: Optional[tuple[int, Point]] = None
    for i in range(1, count):
        width, height, area = stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT], int(stats[i, cv2.CC_STAT_AREA])
        if not cfg.CHOOSE_DISC_AREA[0] <= area <= cfg.CHOOSE_DISC_AREA[1]:
            continue
        if not 1 / cfg.CHOOSE_DISC_SQUARENESS <= width / height <= cfg.CHOOSE_DISC_SQUARENESS:
            continue
        if area < cfg.CHOOSE_DISC_ROUNDNESS * np.pi * (max(width, height) / 2) ** 2:             # (a disc fills this much of its circle, with the "?" cut out of it)
            continue
        if best is None or area > best[0]:
            best = (area, (int(stats[i, cv2.CC_STAT_LEFT] + width // 2), int(stats[i, cv2.CC_STAT_TOP] + height // 2)))
    return best[1] if best is not None else None

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
            done = self._choose()
        if not done:
            dismiss_stray_windows()                          # (a choose that failed left the pet in focus, where the next need's walk goes nowhere: backed out of now)
        return done

    @staticmethod
    def _find_button(img: Image) -> Optional[tuple[Point, bool]]:
        """(where the button is, whether its exact colour was how it was found) - the colour first, then the purple disc - or None."""
        point = find_exact_color(img, cfg.CHOOSE_BUTTON_COLOR)
        if point is not None:
            return point, True
        point = find_choose_disc(img)
        return (point, False) if point is not None else None

    def _choose(self) -> bool:
        if not focus_pet_reliably():
            log_run_event("choose: the pet's view did not come up")
            return False
        wait_interruptible(cfg.UI_SETTLE)

        logger.debug(f"Searching screen for color {cfg.CHOOSE_BUTTON_COLOR}...")
        found = look_until(self._find_button, cfg.CHOOSE_FIND_WAIT, cfg.PET_FOCUS_POLL)             # (the button moves in with the view: looked for for a few seconds, not once)
        if found is None:
            logger.warning("No choose button found, by colour or by shape")
            shot = save_failure_report("choose: no button in the focused view")          # (the failure screenshot taken after this is of the pet focus closed: the button is in this view)
            log_run_event(f"choose: no button found - the focused view is in failures/{shot}")
            return False
        match: Point
        by_colour: bool
        match, by_colour = found
        if not by_colour:
            logger.info(f"The choose button was found as a purple disc at {match}")
            log_run_event("choose: the button was found by its shape and hue, not its exact colour")
        logger.debug(f"Found at {match}, moving there slowly...")
        hover_click(*match, duration=cfg.CHOOSE_SLOW_MOVE_DURATION)
        wait_interruptible(cfg.UI_SETTLE)

        logger.debug("Clicking middle of screen...")
        hover_click(cfg.REFERENCE_CENTER_X, cfg.REFERENCE_CENTER_Y, duration=cfg.CHOOSE_SLOW_MOVE_DURATION)

        logger.info("Choose complete")
        return True

class RideNeedHandler(SequenceNeedHandler):
    """The 'ride' need: step back, mount a vehicle from the backpack, then
    walk back and forth for a while astride it - or less, the moment its
    icon is confirmed gone (see walk_alternating())."""
    label = "Ride"
    steps = (
        ("say", "Stepping back for {RIDE_BACKWARD_DURATION}s..."), ("hold", "back", "RIDE_BACKWARD_DURATION"),         # step back before mounting
        ("say", "Pressing e..."), ("press", "mount"), ("wait", "RIDE_WAIT_AFTER_E"),
        ("say", "Walking forward for {RIDE_FORWARD_DURATION}s..."), ("hold", "forward", "RIDE_FORWARD_DURATION"),       # a little way on after mounting, before the backpack
        ("equip_vehicle",),
        ("say", "Holding the helicopter key for {RIDE_R_HOLD_DURATION}s..."), ("hold", "helicopter", "RIDE_R_HOLD_DURATION"),   # briefly before starting to move
        ("walk", ("forward", "back"), "NEED_GONE_MAX_WAIT", "ride"),                                                    # back and forth (not left and right) while riding
    )

    def handle(self) -> bool:
        """The ride, and if it did not clear its need (or could not be done), the backpack filter set again and the ride once more: with Setup not run the backpack's first vehicle is
        a car, not the helicopter the keys are for."""
        state.need_cleared = True
        if super().handle() and state.need_cleared:
            return True
        logger.info("The ride did not clear the need - setting the backpack filter, then trying once more")
        log_run_event("ride: the need was not cleared - the backpack filter is set again and the ride tried once more")
        set_backpack_filter()
        state.need_cleared = True
        return super().handle()

def screen_changed(before: Image, after: Image) -> bool:
    """Whether the view is a different one: the mean grey-level difference of the two (shrunk) screens is over TELEPORT_CHANGE."""
    small = [cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cfg.SCREEN_CHANGE_SIZE, interpolation=cv2.INTER_AREA).astype(np.int16) for img in (before, after)]
    return bool(np.abs(small[0] - small[1]).mean() > cfg.TELEPORT_CHANGE)

def teleport_took(before: Image) -> bool:
    """Wait up to TELEPORT_WAIT_FOR_VIEW seconds for the character to be teleported after the dialog's Yes: the white screen of a teleport (detect_white_screen()) or a view changed from
    `before` (the screen as it was at the click; screen_changed())."""
    end = time.time() + cfg.TELEPORT_WAIT_FOR_VIEW
    while time.time() < end:
        wait_interruptible(cfg.TELEPORT_VIEW_POLL)
        img = grab_screen()
        if detect_white_screen(img) or screen_changed(before, img):
            return True
    return False

def find_teleport_yes(img: Image) -> Optional[Point]:
    """Where the Yes of the teleport dialog is (its centre), else None."""
    return popup_found(img, "teleport_yes")

def teleport_to(category_pos: Point) -> bool:
    """Open the backpack, teleport via the category tab, walk back to clear the landing
    spot. Shared by the teleport-walk needs. False (logged) when it can be told that the teleport did not happen: the dialog's Yes took nothing
    after TELEPORT_YES_TRIES clicks, the dialog did not come up, or the player is still in the house (its Exit Home button) after both loads. A teleport comes with two
    white screens (a capture of the teleport tests), so each one that does not show is logged too. See docs/ARCHITECTURE.md#need-handlers.
    """
    started_in_house = in_house(grab_screen())
    took = False
    with backpack_on_purpose():
        logger.debug("Opening backpack...")
        pydirectinput.press(cfg.KEYS["backpack"])
        wait_interruptible(cfg.UI_SETTLE)

        logger.debug("Selecting category...")
        hover_click(*category_pos)
        wait_interruptible(cfg.UI_SETTLE)
        yes = None
        for _ in range(2):                                      # the "Get More" tile, clicked once more if the dialog did not come up (the click can be lost)
            hover_click(*cfg.GENERAL_TELEPORT_POS_2)
            wait_interruptible(cfg.UI_SETTLE)
            yes = look_until(find_teleport_yes, cfg.TELEPORT_DIALOG_WAIT, 0.3)
            if yes is not None:
                break
        if yes is None:
            log_run_event("teleport: the dialog did not come up after the Get More tile was clicked twice")
            return False
        for attempt in range(cfg.TELEPORT_YES_TRIES):            # the dialog's Yes (where it was found): clicked again if nothing teleported (the click can be lost)
            before = grab_screen()
            hover_click(*yes)
            if teleport_took(before):
                took = True
                break
            logger.debug(f"The teleport did not take after click {attempt + 1}")
        else:
            log_run_event("teleport: nothing happened after the dialog's Yes")
    if not took:
        return False
    wait_for_white_to_pass(cfg.TELEPORT_WAIT, what="teleport, first load")                    # (the teleport's white screen to fade, not a fixed time)
    wait_for_white_to_pass(cfg.TELEPORT_SETTLE_WAIT, cfg.TELEPORT_SECOND_WHITE_LOOK, what="teleport, second load")   # (the second load: a landing in a red room, then its own white screen; the walk back belongs at the park, so it comes after both; the fixed time when none shows)
    if started_in_house and in_house(grab_screen()):
        log_run_event("teleport: still in the house after both loads (the Exit Home button is on screen)")
        return False
    logger.debug("Walking backward...")
    hold_key(cfg.KEYS["back"], cfg.TELEPORT_BACK_DURATION)
    wait_interruptible(cfg.UI_SETTLE)
    return True

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

        if not teleport_to(self.config["teleport_pos"]):
            logger.debug(f"The {self.name} teleport did not happen - the need failed")      # (no walking blind from the house)
            respawn_character()
            return False

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
        prompt = look_until(find_doctor_prompt, cfg.SICK_PROMPT_LOOK, cfg.SICK_LOOK_INTERVAL)
        if prompt is None:
            log_run_event(f"{self.name}: the doctor's prompt is not on screen")
            return False
        logger.debug(f"Clicking the {self.name} prompt at {prompt}...")
        hover_click(*prompt)
        if look_until(detect_sick_dialog, cfg.SICK_CONFIRM_WAIT, cfg.SICK_LOOK_INTERVAL) is None:
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
        if not run_stats.resolved.get(need_name):
            state.hopeless.add(need_name)
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

    matched_needs = [(need_name, score) for _, _, need_name, score in identify_icons(found_icons, full_img) if need_name]      # (an icon that matches none is skipped: the new-need key teaches it)

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
                with task_unscrew(what=f"need handler: {need_name}"):
                    handled = handler.handle()
            except TaskInterrupted as interruption:
                # A rejoin took over (it respawns): not
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
                if run_stats.failed[need_name] >= cfg.NEED_NEVER_WORKED_FAILS and not run_stats.resolved.get(need_name) and need_name not in state.disabled_needs:
                    state.disabled_needs.add(need_name)
                    state.hopeless.add(need_name)                                              # (a need that has failed this often and never once worked costs its wait every time it comes)
                    log_run_event(f"disabled: {need_name}")
                    log_failure(f"{need_name} failed {run_stats.failed[need_name]} times and never worked in this run - disabled for this run")
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
    """True if the normal backpack is open: its purple frame (at both sides of the panel) plus the white item grid, or its
    title crop. See docs/ARCHITECTURE.md#need-handlers.
    """
    if popup_visible(img, "backpack_normal"):
        return True
    if color_pixels(img, cfg.BACKPACK_NORMAL_PANEL_BOX, cfg.BACKPACK_NORMAL_COLOR, cfg.BACKPACK_NORMAL_TOLERANCE) < cfg.BACKPACK_NORMAL_MIN_PIXELS:
        return False
    panel_left, panel_top, panel_right, panel_bottom = cfg.BACKPACK_NORMAL_PANEL_BOX
    for side in ((panel_left, panel_top, panel_left + cfg.BACKPACK_NORMAL_SIDE_WIDTH, panel_bottom), (panel_right - cfg.BACKPACK_NORMAL_SIDE_WIDTH, panel_top, panel_right, panel_bottom)):
        if color_pixels(img, side, cfg.BACKPACK_NORMAL_COLOR, cfg.BACKPACK_NORMAL_TOLERANCE) < cfg.BACKPACK_NORMAL_MIN_SIDE_PIXELS:
            return False                                            # (a purple blob in the middle, not a frame)
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
    white = cv2.countNonZero(cv2.inRange(img, (cfg.WHITE_SCREEN_LEVEL,) * 3, (cfg.PIXEL_MAX,) * 3))        # (every channel at least the level: one pass, not a minimum over a copy of the picture)
    return bool(white / (img.shape[0] * img.shape[1]) >= cfg.WHITE_SCREEN_MIN_FRACTION)

ROBLOX_NOT_RUNNING = "Roblox isn't running"

def server_closing_visible(img: Image) -> bool:
    """Whether the white screen of the game's update shows its blue "The server is closing for an update" (SERVER_CLOSING_BOX: enough blue pixels)."""
    left, top, right, bottom = cfg.SERVER_CLOSING_BOX
    band = img[top:bottom, left:right].astype(np.int16)
    blue = (band[:, :, 0] >= 140) & (band[:, :, 0] - band[:, :, 2] >= cfg.SERVER_CLOSING_BLUE_LEAD)
    return int(np.count_nonzero(blue)) >= cfg.SERVER_CLOSING_MIN_PIXELS

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
    else:
        updating = server_closing_visible(screen)                                           # (the game is updating: its servers are back in a few minutes, not in half a minute)
        wait = cfg.SERVER_UPDATE_REJOIN_AFTER if updating else cfg.WHITE_SCREEN_REJOIN_AFTER
        if time.time() - state.white_since >= wait:
            return "white_screen", (f"the server is closing for an update: {wait:g}s of it" if updating else f"the screen has been white for {wait:g}s")
    disabled_now = state.disabled_needs - state.hopeless                                          # (needs that never worked are not what a rejoin is for)
    if len(disabled_now) >= cfg.DISABLED_NEEDS_REJOIN_THRESHOLD:
        return "stuck", f"{len(disabled_now)} needs disabled ({', '.join(sorted(disabled_now))})"
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
    previous_activity, state.activity = state.activity, f"rejoin ({kind})"
    try:
        rejoined = leave_and_rejoin(clean_leave=(kind != "disconnect"))
    finally:
        state.activity = previous_activity
    if not rejoined:
        log_failure("recovery: rejoin failed, will try again next cycle")
        return False
    try:
        setup_game()
    except TaskInterrupted as interruption:
        # The game was rejoined again while setup ran (its in-task checks do that on a disconnect), and that
        # nested recovery already ran setup itself - so this one carries on instead of ending the run.
        log_run_event(f"setup interrupted by {interruption.reason} - the nested recovery redid it")
    state.disabled_needs &= state.hopeless                                                   # (the others get a fresh chance after a rejoin; a need that never worked in the run does not)
    reset_need_tracking()
    log_run_event("recovery complete: rejoined, setup run, disabled needs cleared" + (f" (still disabled: {', '.join(sorted(state.disabled_needs))})" if state.disabled_needs else ""))
    logger.info("Recovery complete - carrying on")
    write_status()
    return True

def detect_sick_dialog(img: Image) -> bool:
    """True if the sick need's "Ask Doctor Heart to heal your family?" dialog is on screen in `img`."""
    return popup_visible(img, "sick_dialog")

def find_doctor_prompt(img: Image) -> Optional[Point]:
    """Where the button over the player's head is (its centre) when the player stands in front of Doctor Heart, else None: a hand
    (`doctor_prompt`) before the Lightning Dimension update, an E (`doctor_prompt_e`) since."""
    return popup_found(img, "doctor_prompt_e") or popup_found(img, "doctor_prompt")

# directory -> (the files' names and modification times, {name: image}) - see load_templates().
_TEMPLATE_CACHE: dict[str, tuple[Any, dict[str, Image]]] = {}
_TEMPLATE_CHECKED: dict[str, float] = {}      # directory -> when its files were last looked at

def load_templates(directory: str) -> dict[str, Image]:
    """{name: image} for every .png in `directory`, cached and re-read when a file changes.
    The images are shared: don't modify them. See docs/ARCHITECTURE.md#need-handlers.
    """
    now = time.monotonic()
    cached = _TEMPLATE_CACHE.get(directory)
    if cached is not None and now - _TEMPLATE_CHECKED.get(directory, -1e9) < cfg.TEMPLATE_RECHECK_INTERVAL:
        return dict(cached[1])                         # (the folder was looked at a moment ago: this is called a dozen times a look at the screen)
    if not os.path.isdir(directory):
        return {}
    files = sorted(f for f in os.listdir(directory) if f.lower().endswith(".png"))
    key = tuple((f, os.stat(os.path.join(directory, f)).st_mtime_ns) for f in files)
    _TEMPLATE_CHECKED[directory] = now
    if cached is None or cached[0] != key:
        templates = {}
        for f in files:
            crop = cv2.imread(os.path.join(directory, f))
            if crop is not None:
                templates[os.path.splitext(f)[0].lower()] = crop
        cached = _TEMPLATE_CACHE[directory] = (key, templates)
    return dict(cached[1])

def find_template(img: Image, crop: Image, box: Box) -> tuple[float, Optional[Point]]:
    """Like template_score(), but also where: (score, (x, y) of the match's
    center in `img`)."""
    left, top, right, bottom = box
    band = img[top:bottom, left:right]
    if crop.shape[0] > band.shape[0] or crop.shape[1] > band.shape[1]:
        return 0.0, None
    _, score, _, (x, y) = cv2.minMaxLoc(cv2.matchTemplate(band, crop, cv2.TM_CCOEFF_NORMED))
    return float(score), (left + x + crop.shape[1] // 2, top + y + crop.shape[0] // 2)

# crop name -> (the colour crop it is for, its grey version) - see popup_found().
_GRAY_CROPS: dict[str, tuple[Image, Image]] = {}

def popup_found(img: Image, name: str) -> Optional[Point]:
    """Where the STRAY_WINDOW_DIR crop `name` is in `img` - its centre - if it matches within its box well enough
    (POPUP_TEMPLATES, or STRAY_WINDOWS for a stray window), else None. See docs/ARCHITECTURE.md#popup-templates."""
    crop = load_templates(cfg.STRAY_WINDOW_DIR).get(name)
    entry = cfg.POPUP_TEMPLATES.get(name) or cfg.STRAY_WINDOWS[name]
    if crop is None:
        return None
    left, top, right, bottom = entry["box"]
    band = img[top:bottom, left:right]
    if cfg.POPUP_GRAY_GATE and crop.shape[0] <= band.shape[0] and crop.shape[1] <= band.shape[1]:
        cached = _GRAY_CROPS.get(name)
        if cached is None or cached[0] is not crop:
            cached = _GRAY_CROPS[name] = (crop, cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY))
        if float(cv2.minMaxLoc(cv2.matchTemplate(cv2.cvtColor(band, cv2.COLOR_BGR2GRAY), cached[1], cv2.TM_CCOEFF_NORMED))[1]) < cfg.POPUP_GRAY_GATE:
            return None                                    # (a crop that is on screen is there in grey too: the colour search, three times the work, is for the ones that might be)
    score, centre = find_template(img, crop, entry["box"])
    return centre if score >= entry.get("threshold", cfg.STRAY_WINDOW_MATCH_THRESHOLD) else None

def popup_visible(img: Image, name: str) -> bool:
    """True if the crop `name` is on screen in `img` (see popup_found())."""
    return popup_found(img, name) is not None


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


def find_dialog_button(img: Image, spec: dict[str, Any]) -> Optional[Point]:
    """The middle of the biggest blob of colour `spec` (DIALOG_BUTTONS: "box", "hsv" - one or two (low, high) ranges -, "size" - (width, height) ranges) in `img`, or None."""
    left, top, right, bottom = spec["box"]
    hsv = cv2.cvtColor(img[top:bottom, left:right], cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, *spec["hsv"][0])
    for extra in spec["hsv"][1:]:
        mask = cv2.bitwise_or(mask, cv2.inRange(hsv, *extra))
    count, _, stats, centres = cv2.connectedComponentsWithStats(mask, connectivity=8)
    (min_w, max_w), (min_h, max_h) = spec["size"]
    best: Optional[tuple[int, Point]] = None
    for i in range(1, count):
        width, height, area = (int(stats[i, key]) for key in (cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT, cv2.CC_STAT_AREA))
        if min_w <= width <= max_w and min_h <= height <= max_h and (best is None or area > best[0]):
            best = (area, (left + int(centres[i][0]), top + int(centres[i][1])))
    return best[1] if best is not None else None

def dismiss_stray_windows() -> Optional[str]:
    """Close any window from STRAY_WINDOWS that is open (found by its title crop); returns
    its name or None. See docs/ARCHITECTURE.md#need-handlers.
    """
    img = grab_screen()
    for name, config in cfg.STRAY_WINDOWS.items():
        if not popup_visible(img, name):
            continue
        if name == "task_board" and state.task_board_wanted:                    # (pet_pen() has it open)
            continue
        if config.get("only_when_unwanted") and state.pet_focus_wanted:
            continue
        if "button" in config:                                              # (closed by a button of its own colour and size, found: the card's height follows its text)
            button = find_dialog_button(img, cfg.DIALOG_BUTTONS[config["button"]])
            if button is None:
                continue
            logger.debug(f"Stray window open: {name}, pressing its {config['button']} button...")
            log_run_event(f"stray window closed: {name}" + (" (trading is not set to No One)" if name.startswith("trade_request") else ""))
            hover_click(*button)
            time.sleep(cfg.STRAY_CLOSE_SETTLE)
            if name == "pet_full_grown_notice":
                update_game_config(lambda c: c.update(pet_repicked=0.0))                      # (the pet that is out has grown up: pick another at the next cycle)
            return name
        logger.debug(f"Stray window open: {name}, closing it...")
        log_run_event(f"stray window closed: {name}")
        if name == "trading_hub":
            if color_pixels(img, cfg.STRAY_OKAY_BOX, cfg.GREEN_BUTTON_COLOR, cfg.GREEN_BUTTON_TOLERANCE) >= cfg.STRAY_OKAY_MIN_PIXELS:
                hover_click(*cfg.STRAY_OKAY_POS)
        hover_click(*config["close_pos"])
        time.sleep(cfg.STRAY_CLOSE_SETTLE)
        return name
    if not state.task_board_wanted:                                                       # (the pen's own age-up screen is closed by collect_pet_pen())
        nice = find_dialog_button(img, cfg.DIALOG_BUTTONS["nice"])
        if nice is not None:
            logger.debug("The age-up screen is up: pressing its NICE!...")
            log_run_event("stray window closed: age_up_screen")
            hover_click(*nice)
            time.sleep(cfg.STRAY_CLOSE_SETTLE)
            return "age_up_screen"
    return None

def in_trading_hub(img: Image) -> bool:
    """True if the Trading Hub island is on screen: its GO HOME button's text."""
    return popup_visible(img, "go_home")

def leave_hub_if_in() -> bool:
    """Leave the Trading Hub (leave_trade_hub()) if the player is in it and wait for it to be gone (HUB_LEAVE_WAIT) and the regular server to load (HUB_LOAD_WAIT). True if the player was not there or left; False (logged) if not."""
    if not focus_roblox():
        return False
    if not in_trading_hub(grab_screen()):
        return True
    if not leave_trade_hub():
        return False
    deadline = time.time() + cfg.HUB_LEAVE_WAIT
    while in_trading_hub(grab_screen()):
        if time.time() >= deadline:
            log_run_event("leave the Trading Hub: it is still there")
            return False
        wait_interruptible(cfg.HUB_POLL)
    deadline = time.time() + cfg.HUB_LOAD_WAIT                # the loading screen has no backpack button: the regular server is there once it is back
    while not popup_visible(grab_screen(), "backpack_button"):
        if time.time() >= deadline:
            log_run_event("leave the Trading Hub: the regular server did not load")
            return False
        wait_interruptible(cfg.HUB_POLL)
    return True
























































def unscrew(in_task: bool = False, interruptible: bool = True) -> Optional[str]:
    """The per-cycle checks (at the top of every cycle; with `in_task` also during a task): rejoin, backpack, stray windows, then the paycheck popup - in that order, since the green
    buttons of the earlier ones look like CASH OUT. Returns "rejoined" or "aborted" (the callers abandon the task) or None. See docs/ARCHITECTURE.md#need-handlers.
    """
    if in_task:
        if rejoin_game(kinds=("disconnect", "white_screen")):
            return "rejoined"
    else:
        rejoin_game()
    # Setup (interruptible False) keeps the backpack open for ~8s on purpose - longer than the two checks that
    # count as stuck - and closes it itself at the end, so the check leaves it alone there.
    backpack_open = False if (in_task and not interruptible) else close_backpack_if_stuck(in_task)
    closed = dismiss_stray_windows()
    paycheck = detect_paycheck() if not backpack_open else False
    if in_task and interruptible and (paycheck or (closed and cfg.STRAY_WINDOWS.get(closed, {}).get("abort_task"))):
        return "aborted"   # (not setup, which can't be abandoned halfway - that is `interruptible` False)
    return None

def run_side_action(action: Callable[[], bool]) -> bool:
    """Run lure_collect() / tree_collect() with the in-task checks on. False if a
    rejoin took over. See docs/ARCHITECTURE.md#side-actions.
    """
    try:
        with task_unscrew(what=f"side action: {action.__name__}"):
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
    """Once per cycle after unscrew(): tend the money tree and the lure (each unless its own switch, money_tree_enabled or lure_enabled, is off) from the persisted state, saving each success at once.
    See docs/ARCHITECTURE.md#side-actions.
    """
    config = reset_money_if_due(load_game_config())
    now = time.time()

    if config["money_tree_enabled"] and config["money_collected"] < cfg.MONEY_COLLECTED_TARGET and now >= config["tree_timer"]:
        if run_side_action(tree_collect):
            def harvested(c):
                c["money_collected"] += cfg.TREE_HARVEST_YIELD
                c["tree_timer"] = time.time() + cfg.TREE_CHECK_INTERVAL
            update_game_config(harvested)

    if config["lure_enabled"] and now >= config["lure_timer"]:
        if run_side_action(lure_collect):
            update_game_config(lambda c: c.update(lure_timer=time.time() + cfg.LURE_RECOLLECT_INTERVAL))

def find_task_board_button(img: Image) -> Optional[Point]:
    """The middle of the Task Board's sign at the left edge of the screen (DIALOG_BUTTONS["task_board"]: found by the colour of its wood and its size), or None."""
    return find_dialog_button(img, cfg.DIALOG_BUTTONS["task_board"])

def task_board_badge(img: Image) -> bool:
    """Whether the Task Board's button shows its red alert badge (looked for beside the sign, where it is found)."""
    sign = find_task_board_button(img)
    if sign is None:
        return False
    left, top, right, bottom = (sign[0] + cfg.TASK_BOARD_BADGE_BOX[0], sign[1] + cfg.TASK_BOARD_BADGE_BOX[1], sign[0] + cfg.TASK_BOARD_BADGE_BOX[2], sign[1] + cfg.TASK_BOARD_BADGE_BOX[3])
    left, top = max(0, left), max(0, top)
    hsv = cv2.cvtColor(img[top:bottom, left:right], cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, *cfg.TASK_BOARD_BADGE_HSV[0])
    for extra in cfg.TASK_BOARD_BADGE_HSV[1:]:
        mask = cv2.bitwise_or(mask, cv2.inRange(hsv, *extra))
    return int(cv2.countNonZero(mask)) >= cfg.TASK_BOARD_BADGE_MIN_PIXELS

def note_pet_friend(img: Image) -> bool:
    """On the age-up screen: say in the run log if a pet shows the Friend pill (it is to be switched out of the pen), and where it is. True if one does."""
    found = popup_found(img, "pet_friend")
    if found is None:
        return False
    log_run_event(f"pet pen: a pet reached Friend ({'top' if found[1] < img.shape[0] // 2 else 'bottom'} {'left' if found[0] < img.shape[1] // 2 else 'right'}) - it is to be switched out of the pen")
    update_game_config(lambda c: c.update(pet_pen_swap=True))
    return True

def collect_pet_pen() -> bool:
    """Open the Task Board and, when it has the "Pet Pen Ready" card, click it, wait out the age-up screen and click its NICE!. True once the board has been looked at (and the pets collected if they were
    ready), False when the game was not in front or the NICE! never came."""
    if not focus_roblox():
        return False
    state.task_board_wanted = True
    try:
        sign = look_until(find_task_board_button, cfg.PET_PEN_UI_WAIT, cfg.PET_PEN_POLL)                 # (just after a respawn the screen is black or white for a moment: 3 of 4 looks in a 2-hour run saw no sign)
        if sign is None:
            log_run_event("pet pen: the Task Board's sign was not seen")
            return False
        hover_click(*sign)
        if not look_until(lambda img: popup_visible(img, "task_board"), cfg.PET_PEN_UI_WAIT, cfg.PET_PEN_POLL):
            log_run_event("pet pen: the Task Board did not open")
            return False
        card = popup_found(grab_screen(), "pet_pen_ready")
        if card is None:
            hover_click(*cfg.STRAY_WINDOWS["task_board"]["close_pos"])
            return True
        hover_click(*card)
        wait_interruptible(cfg.PET_PEN_ANIMATION_WAIT)
        for _ in range(cfg.PET_PEN_NICE_TRIES):
            button = look_until(lambda img: find_dialog_button(img, cfg.DIALOG_BUTTONS["nice"]), cfg.PET_PEN_NICE_WAIT, cfg.PET_PEN_POLL)
            if button is None:
                break
            note_pet_friend(grab_screen())
            hover_click(*button)
            wait_interruptible(cfg.UI_SETTLE)
            if find_dialog_button(grab_screen(), cfg.DIALOG_BUTTONS["nice"]) is None:
                log_run_event("pet pen: pets aged up")
                return True
        log_run_event("pet pen: the NICE! button did not go")
        return False
    finally:
        state.task_board_wanted = False

def repick_pet() -> bool:
    """Open the backpack and equip the first pet of the filtered list (set_backpack_filter() with the filters already right does no more), then respawn: a pet that has become full grown is
    not in the list any more, so the first one is another that still grows. True if the filters and the pet are as they should be."""
    if not focus_roblox():
        return False
    ok = set_backpack_filter()
    respawn_character()
    return ok

def pet_repick() -> None:
    """Once per cycle after pet_pen(), when the game config's level_growing_pet is on: every PET_REPICK_INTERVAL seconds repick_pet() puts out the first pet that is neither an egg nor full grown. The
    macro has no way yet to tell that the pet out has grown up (no capture of it), so it asks the list instead."""
    config = load_game_config()
    if not config["level_growing_pet"] or time.time() - config["pet_repicked"] < cfg.PET_REPICK_INTERVAL:
        return
    run_side_action(repick_pet)
    update_game_config(lambda c: c.update(pet_repicked=time.time()))

def slot_has(img: Image, name: str, slot: int) -> bool:
    """Whether the crop `name` is in the box of the pen's place number `slot`."""
    crop = load_templates(cfg.STRAY_WINDOW_DIR).get(name)
    centre_x, centre_y = cfg.PET_PEN_SLOTS[slot]
    half_x, half_y = cfg.PET_PEN_SLOT_HALF
    return crop is not None and find_template(img, crop, (centre_x - half_x, centre_y - half_y, centre_x + half_x, centre_y + half_y))[0] >= cfg.POPUP_TEMPLATES[name]["threshold"]

def full_grown_slots(img: Image) -> list[int]:
    """The pen's places whose pet carries the "Full grown!" sign."""
    return [slot for slot in range(len(cfg.PET_PEN_SLOTS)) if slot_has(img, "pet_pen_full_grown", slot)]

def pet_cell_state(img: Image, row: int, column: int) -> str:
    """What the cell of the small backpack's grid (the pets tab, in the house and over the pen alike) holds: "plus" (the green Add tile), "empty", "locked" (grey: the pet is in a trade listing; its corners are dark),
    "pen" (an orange frame with a little house: the pet is in the pen), "equipped" (a green frame) or "pet" (one that can be picked)."""
    x, y, half = cfg.PET_GRID_X[column], cfg.PET_GRID_Y[row], cfg.PET_CELL_HALF
    cell = img[y - half:y + half, x - half:x + half]
    if cell.shape[0] < 2 * half or cell.shape[1] < 2 * half:
        return "empty"
    hsv = cv2.cvtColor(cell, cv2.COLOR_BGR2HSV)
    ring = np.concatenate([cell[0:3, :].reshape(-1, 3), cell[-3:, :].reshape(-1, 3), cell[:, 0:3].reshape(-1, 3), cell[:, -3:].reshape(-1, 3)]).astype(int)
    ring_hsv = np.concatenate([hsv[0:3, :].reshape(-1, 3), hsv[-3:, :].reshape(-1, 3), hsv[:, 0:3].reshape(-1, 3), hsv[:, -3:].reshape(-1, 3)]).astype(int)
    green = float(((ring[:, 1] > 150) & (ring[:, 2] < 120) & (ring[:, 0] < 120)).mean())
    if green >= cfg.PET_CELL_PLUS_GREEN:
        return "plus"
    if float(cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY).std()) < cfg.PET_CELL_CONTENT_MIN_STD:
        return "empty"
    corners = np.concatenate([hsv[2:10, 2:10].reshape(-1, 3), hsv[2:10, -10:-2].reshape(-1, 3)])
    if corners[:, 2].mean() < cfg.PET_CELL_LOCKED_MAX_V:
        return "locked"
    orange = float(((ring_hsv[:, 0] >= 5) & (ring_hsv[:, 0] <= 22) & (ring_hsv[:, 1] >= 140) & (ring_hsv[:, 2] >= 180)).mean())
    if green >= cfg.PET_CELL_GREEN_BORDER_MIN and green >= orange:                                  # (a gold pet's background is orange too: the green frame wins when it is the stronger)
        return "equipped"
    return "pen" if orange >= cfg.PET_CELL_ORANGE_MIN else "pet"

def cell_at_is_equipped(img: Image, point: Point) -> bool:
    """Whether the cell of the pets grid at `point` (the middle of a cell, or near it) now has the green frame of the equipped pet; False for a point that is not in the grid."""
    for row, y in enumerate(cfg.PET_GRID_Y):
        for column, x in enumerate(cfg.PET_GRID_X):
            if abs(point[0] - x) <= cfg.PET_CELL_HALF and abs(point[1] - y) <= cfg.PET_CELL_HALF:
                return pet_cell_state(img, row, column) == "equipped"
    return False

def first_listed_cell(img: Image, stop_at_equipped: bool = False) -> Optional[tuple[str, Point]]:
    """The first cell (rows from the top, left to right) of the small backpack's grid that can be picked - ("pet", its middle) - or, with `stop_at_equipped`, the equipped pet if it comes first: ("equipped", its
    middle). None if there is neither."""
    for row in range(len(cfg.PET_GRID_Y)):
        for column in range(len(cfg.PET_GRID_X)):
            state_here = pet_cell_state(img, row, column)
            if state_here == "pet" or (stop_at_equipped and state_here == "equipped"):
                return state_here, (cfg.PET_GRID_X[column], cfg.PET_GRID_Y[row])
    return None

def choose_list_pet(stop_at_equipped: bool = False) -> Optional[tuple[str, Point]]:
    """In the small backpack: first_listed_cell(), scrolling the list down (the wheel on its scroll bar) a notch at a time while there is none, until a scroll changes nothing (the end of the list) or
    PET_GRID_SCROLL_MAX scrolls. None if there is none."""
    grid_box = (cfg.PET_GRID_X[0] - cfg.PET_CELL_HALF, cfg.PET_GRID_Y[0] - cfg.PET_CELL_HALF, cfg.PET_GRID_X[-1] + cfg.PET_CELL_HALF, cfg.PET_GRID_Y[-1] + cfg.PET_CELL_HALF)

    def grid(img: Image) -> Image:
        left, top, right, bottom = grid_box
        return img[top:bottom, left:right]
    img = grab_screen()
    for _ in range(cfg.PET_GRID_SCROLL_MAX + 1):
        found = first_listed_cell(img, stop_at_equipped)
        if found is not None:
            return found
        hover_move(*cfg.PET_GRID_SCROLL_POS)
        scroll_down(1)
        wait_interruptible(cfg.PET_PEN_UI_SETTLE)
        after = grab_screen()
        if np.array_equal(grid(after), grid(img)):
            return None                                                      # (nothing moved: that was the end of the list)
        img = after
    return None

def choose_pen_pet() -> Optional[Point]:
    """The pet to put in the pen: choose_list_pet() (the equipped one is skipped), its middle, or None when none fits."""
    found = choose_list_pet()
    return found[1] if found is not None else None

def open_pet_pen() -> bool:
    """From a respawn (in the house) to the pen's screen: PET_PEN_ROUTE, then the pen's title is waited for. True if it is up."""
    respawn_character()
    for kind, what, amount in cfg.PET_PEN_ROUTE:
        if kind == "hold":
            hold_key(cfg.KEYS[what], amount)
        elif kind == "press":
            pydirectinput.press(cfg.KEYS[what])
        elif kind == "door":
            if not wait_for_white_to_pass(amount, cfg.WHITE_LOOK + 1.0, what=f"the pen's {what} door"):      # (the door's white screen to fade: the yard has loaded then)
                logger.debug("No white screen at the door")
            elif in_house(grab_screen()):
                log_run_event(f"the pen's {what} door: still in the house afterwards (the Exit Home button is on screen)")
        else:
            wait_interruptible(amount)
    return look_until(lambda img: popup_visible(img, "pet_pen_title"), cfg.PET_PEN_OPEN_WAIT, cfg.PET_PEN_POLL) is not None

def ensure_pen_age_filter() -> bool:
    """In the pen's small backpack: the Age filter without Egg and Full Grown, which the game keeps from one opening to the next. The FILTERS panel is opened, the two boxes looked at first and only a ticked one clicked
    (untick_egg_and_full_grown()); a panel that was only looked at is cancelled and one that was changed confirmed, opened again and looked at (the same panel and positions as Setup's). False (logged) if it did not come up, the
    rows were not found or the change did not take."""
    if not open_filter_panel(cfg.PET_PEN_FILTER_BUTTON_POS):
        return False
    changed = untick_egg_and_full_grown()
    if changed is None:
        hover_click(*cfg.SETUP_FILTER_CANCEL_POS)
        wait_interruptible(cfg.UI_SETTLE)
        return False
    if not changed:
        hover_click(*cfg.SETUP_FILTER_CANCEL_POS)                                      # (already as it should be: left as it is)
        wait_interruptible(cfg.UI_SETTLE)
        return True
    hover_click(*cfg.SETUP_CONFIRM_POS)
    wait_interruptible(cfg.UI_SETTLE)
    ok = open_filter_panel(cfg.PET_PEN_FILTER_BUTTON_POS) and scroll_filters_to_age() and age_filter_is_right()
    log_run_event("pet pen: the backpack's Age filter is set" if ok else "pet pen: the backpack's Age filter did not take")
    hover_click(*cfg.SETUP_FILTER_CANCEL_POS)
    wait_interruptible(cfg.UI_SETTLE)
    return ok

def put_a_pet_in_the_pen() -> bool:
    """The pen's first free place: Add Pet clicked, the small backpack waited for, the first pet that fits clicked (choose_pen_pet()). False, with the backpack closed again, when there is no free place, the backpack did
    not come up or no pet fits."""
    add = popup_found(grab_screen(), "pet_pen_add")
    if add is None:
        log_run_event("pet pen: no free place to put a pet in")
        return False
    hover_click(*add)
    if look_until(lambda img: popup_visible(img, "pet_backpack_title"), cfg.PET_PEN_OPEN_WAIT, cfg.PET_PEN_POLL) is None:
        log_run_event("pet pen: the backpack did not come up after Add Pet")
        return False
    if not ensure_pen_age_filter():
        hover_click(*cfg.PET_PEN_BACKPACK_CLOSE_POS)
        wait_interruptible(cfg.PET_PEN_UI_SETTLE)
        return False
    pet = choose_pen_pet()
    if pet is None:
        log_run_event("pet pen: no pet fits (all in a trade listing, equipped or none left)")
        hover_click(*cfg.PET_PEN_BACKPACK_CLOSE_POS)
        wait_interruptible(cfg.PET_PEN_UI_SETTLE)
        return False
    hover_click(*pet)
    wait_interruptible(cfg.PET_PEN_UI_SETTLE)
    if popup_visible(grab_screen(), "pet_backpack_title"):
        log_run_event("pet pen: the pet clicked did not go in")
        hover_click(*cfg.PET_PEN_BACKPACK_CLOSE_POS)
        return False
    return True

def switch_pen_pets() -> bool:
    """The pen's full-grown pets out and others in: the pen opened (open_pet_pen()), the red X of each place with the "Full grown!" sign clicked (the rest move up, so each time it is looked at again), then a pet that fits put in
    for each. A pen with none full grown is closed again and counts as done; a pet that cannot be put in is left out and said (the place stays free). True when the pen is as it should be, False when it could not be opened or a pet
    could not be taken out."""
    if not focus_roblox():
        return False
    if not open_pet_pen():
        log_run_event("pet pen: it did not open")
        return False
    out = 0
    for _ in range(len(cfg.PET_PEN_SLOTS)):
        grown = full_grown_slots(grab_screen())
        if not grown:
            break
        x, y = cfg.PET_PEN_SLOTS[grown[0]]
        hover_click(x + cfg.PET_PEN_X_OFFSET[0], y + cfg.PET_PEN_X_OFFSET[1])
        wait_interruptible(cfg.PET_PEN_UI_SETTLE)
        if len(full_grown_slots(grab_screen())) >= len(grown):
            log_failure("pet pen: a full-grown pet did not go out of the pen")
            return False
        out += 1
    if out == 0:
        log_run_event("pet pen: no pet in it is full grown")
    for _ in range(out):
        if not put_a_pet_in_the_pen():
            break
    else:
        if out:
            log_run_event(f"pet pen: {out} full-grown pet(s) switched")
    hover_click(*cfg.PET_PEN_CLOSE_POS)
    wait_interruptible(cfg.PET_PEN_UI_SETTLE)
    return True

def check_pet_pen() -> bool:
    """Setup's look at the pen (the game config's pet_pen_enabled): switch_pen_pets() - its full-grown pets out and others in, none if none is full grown - before the pet to level is picked, so that what was put in
    the pen is not. The character is respawned after, back in the house where Setup goes on. True when the pen is as it should be (or the option is off)."""
    if not load_game_config()["pet_pen_enabled"]:
        return True
    done = switch_pen_pets()
    update_game_config(lambda c: c.update(pet_pen_swap_tried=time.time(), **({"pet_pen_swap": False} if done else {})))
    respawn_character()
    return done

def pet_pen_swap_due() -> None:
    """Once per cycle after pet_pen(): when the age-up screen showed a pet that reached Friend (the game config's pet_pen_swap) and PET_PEN_SWAP_RETRY seconds have passed since the last try, switch_pen_pets(); the flag is cleared
    once it came through."""
    config = load_game_config()
    if not config["pet_pen_enabled"] or not config["pet_pen_swap"] or time.time() - config["pet_pen_swap_tried"] < cfg.PET_PEN_SWAP_RETRY:
        return
    done = run_side_action(switch_pen_pets)
    update_game_config(lambda c: c.update(pet_pen_swap_tried=time.time(), **({"pet_pen_swap": False} if done else {})))

def pet_pen() -> None:
    """Once per cycle after side_quest(), unless the game config's pet_pen_enabled is off: when the Task Board button shows its alert badge (at most every PET_PEN_BADGE_INTERVAL) or
    PET_PEN_CHECK_INTERVAL has passed since the last look (the badge was not on in the capture of the Pet Pen notice), collect the Pet Pen with collect_pet_pen()."""
    config = load_game_config()
    if not config["pet_pen_enabled"]:
        return
    since = time.time() - config["pet_pen_looked"]
    if since < cfg.PET_PEN_BADGE_INTERVAL or (since < cfg.PET_PEN_CHECK_INTERVAL and not task_board_badge(grab_screen())):
        return
    done = run_side_action(collect_pet_pen)
    update_game_config(lambda c: c.update(pet_pen_looked=time.time()))
    if not done:
        logger.debug("pet pen: not collected")

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
# movement; press it again to stop. For working out how something is done by hand (a walk to a need's place, say):
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

    def __init__(self, folder: str, max_zips: Optional[int] = None, interval: Optional[float] = None, protected_max: Optional[int] = None,
                 max_minutes: Optional[float] = None, inputs: bool = True, context: Optional[Callable[[], dict[str, Any]]] = None, reserve_bytes: int = 0) -> None:
        """`interval`: seconds between looks (CAPTURE_FRAME_INTERVAL); `protected_max`: most frames mark() and protect_tail() keep from being thinned (RUN_CAPTURE_PROTECTED); `max_minutes`: when it stops
        by itself (CAPTURE_MAX_MINUTES); `inputs` False: no keys and mouse are recorded (the run's moments, see start_moments()); `context`: called for every frame saved, its dict goes into the frame's line of
        events.jsonl (what the macro was doing); `reserve_bytes`: room in the first zip for files that are added to the folder when it ends."""
        self.folder = folder
        self.max_zips = max_zips
        self.interval = interval or cfg.CAPTURE_FRAME_INTERVAL
        self.protected_max = protected_max or cfg.RUN_CAPTURE_PROTECTED
        self.max_minutes = max_minutes or cfg.CAPTURE_MAX_MINUTES
        self.inputs = inputs
        self.context = context
        self.reserve_bytes = reserve_bytes
        self.dropped = 0
        self._kept: list[tuple[str, int]] = []          # the frames on disk, oldest first, with their sizes
        self._gap = self.interval if max_zips else 0.0     # least seconds between two saved frames
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
                       "frame_interval": self.interval, "max_zips": self.max_zips}, f, indent=1)
        self._file = open(os.path.join(self.folder, "events.jsonl"), "w", encoding="utf-8")
        self._threads = [threading.Thread(target=self._shots, daemon=True)] + ([threading.Thread(target=self._poll, daemon=True)] if self.inputs else [])
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

    def note(self, text: str) -> None:
        """A line of the run's log into events.jsonl (a "log" line), so that the frames and what the macro was doing are on one clock."""
        self._emit("log", text=text)

    def protect_tail(self, seconds: float) -> None:
        """Keep the frames of the last `seconds` of the run from being thinned out - where a run that went wrong shows why."""
        horizon = time.time() - self.started - seconds
        for name, _ in self._kept:
            if len(self.protected) < self.protected_max and float(name[:-len(".png")].rsplit("_", 1)[1]) >= horizon:
                self.protected.add(name)

    def _context(self) -> dict[str, Any]:
        try:
            return self.context() if self.context is not None else {}
        except Exception:
            return {}

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
                if time.time() - self.started >= self.max_minutes * 60:
                    self._give_up(f"{self.max_minutes:g} minutes")
                    return
                if shutil.disk_usage(self.folder).free < cfg.CAPTURE_MIN_FREE_MB * 1024 * 1024:
                    self._give_up("the disk is nearly full")
                    return
                seconds = time.time() - self.started
                screen = grab_screen()
                small = cv2.resize(cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY), (320, 180), interpolation=cv2.INTER_AREA)
                bursting = time.time() < self._burst_until and len(self.protected) < self.protected_max     # (around a mark(): every look)
                if bursting or seconds - last_saved >= self._gap and (
                        clicked or last_small is None or seconds - last_saved >= max(cfg.CAPTURE_MAX_SKIP, self._gap)
                        or np.count_nonzero(cv2.absdiff(small, last_small) > cfg.CAPTURE_CHANGE_LEVEL) / small.size >= cfg.CAPTURE_CHANGE_FRACTION):
                    name = f"frame_{self.frames + 1:06d}_{seconds:.3f}.png"
                    if cv2.imwrite(os.path.join(self.folder, name), screen, [cv2.IMWRITE_PNG_COMPRESSION, 1]):
                        self.frames += 1
                        self._emit("frame", file=name, **self._context())
                        last_small, last_saved = small, seconds
                        self._kept.append((name, os.path.getsize(os.path.join(self.folder, name))))
                        if bursting:
                            self.protected.add(name)
                        self._keep_within_budget()
                else:
                    self.skipped += 1
            except Exception as error:
                logger.warning(f"Input capture: couldn't save a frame: {error}")
            self.shot_now.wait(cfg.CAPTURE_FRAME_INTERVAL if time.time() < self._burst_until else self.interval)
            if self.shot_now.is_set():
                self.stop_event.wait(0.02)      # let the click land on screen before looking

    def _keep_within_budget(self) -> None:
        """Needing more than `max_zips` zips: delete every other saved frame, never the newest, and save frames twice as
        far apart from now on - until the zips needed are few enough."""
        events = os.path.join(self.folder, "events.jsonl")
        while self.max_zips and len(self._kept) > 1 and len(plan_zip_parts(
                [size for _, size in self._kept], (os.path.getsize(events) if os.path.exists(events) else 0) + self.reserve_bytes)) > self.max_zips:
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
    <name>_part01.zip, _part02.zip, ... (the PNGs stored, not compressed: they don't shrink; the text files deflated). The first part also holds
    events.jsonl and info.json, which are small; the frames go in time order, none twice. Returns the zip paths."""
    out = os.path.join(folder, "zips")
    building = out + ".partial"                                                                  # (the zips are only in zips/ once all are made: /pull and OneDrive must not take half of them)
    shutil.rmtree(building, ignore_errors=True)
    os.makedirs(building)
    frames = sorted(f for f in os.listdir(folder) if f.startswith("frame_") and f.endswith(".png"))
    first = ["README.txt", "info.json", "events.jsonl"]                                           # (what a reader opens first)
    heads = [f for f in first if os.path.exists(os.path.join(folder, f))] + sorted(
        f for f in os.listdir(folder) if f not in first and not f.startswith("frame_") and os.path.isfile(os.path.join(folder, f)))
    head = sum(os.path.getsize(os.path.join(folder, f)) for f in heads)
    plan = plan_zip_parts([os.path.getsize(os.path.join(folder, name)) for name in frames], head)
    parts = [[frames[i] for i in positions] for positions in plan]
    parts[0] = heads + parts[0]
    paths = []
    for number, names in enumerate(parts, 1):
        path = os.path.join(out, f"{os.path.basename(folder)}_part{number:02d}.zip")
        with zipfile.ZipFile(os.path.join(building, os.path.basename(path)), "w", zipfile.ZIP_STORED) as z:
            for name in names:
                if os.path.exists(os.path.join(folder, name)):
                    z.write(os.path.join(folder, name), name, compress_type=zipfile.ZIP_STORED if name.endswith(".png") else zipfile.ZIP_DEFLATED)
        paths.append(path)
    shutil.rmtree(out, ignore_errors=True)                                                       # (the zips of an earlier stop of the same folder)
    os.replace(building, out)
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
    finish_moments()                                                                      # (the capture of the run's moments is not kept beside this one)
    make_room_for_capture(cfg.CAPTURES_DIR, cfg.MAX_CAPTURES)
    capture = InputCapture(os.path.join(cfg.CAPTURES_DIR, "capture_" + time.strftime("%Y%m%d_%H%M%S")))
    capture.start()
    state.capture = capture
    logger.info(f"Capture started (press {cfg.HOTKEYS['capture'].upper()} again to stop): {capture.folder}")
    return capture

# The moments of a run: a capture of the whole run kept to MOMENTS_ZIPS zips however long it goes (InputCapture with a budget: it thins itself out, so what is left is spread over the run), with the
# frames of failures, rejoins and disabled needs kept first (note_moment() marks them) and the last MOMENTS_TAIL seconds. A run shorter than MOMENTS_MIN_RUN leaves nothing; a longer one is zipped
# into MOMENTS_DIR, to be sent for improving the macro. The game config's keep_moments switches it off.

class MomentsLog(logging.Handler):
    """Every line the macro prints or logs (the debug ones too), into output.log of the run's moments capture: two files of at most MOMENTS_LOG_MB / 2 each, so that the end of a long run is there."""
    def __init__(self, folder: str) -> None:
        super().__init__(logging.DEBUG)
        self.files = logging.handlers.RotatingFileHandler(os.path.join(folder, "output.log"), maxBytes=int(cfg.MOMENTS_LOG_MB * 1024 * 1024 / 2), backupCount=1, encoding="utf-8")
        self.files.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        self.files.emit(record)

    def close(self) -> None:
        self.files.close()
        super().close()

def moments_context() -> dict[str, Any]:
    """What the macro is doing, for every frame of the moments capture: its activity, the needs it last saw and how the run is going."""
    return {"activity": state.activity or "waiting for needs", "detected": list(run_stats.last_detected), "cycles": run_stats.cycles, "resolved": sum(run_stats.resolved.values()),
            "failures": run_stats.failures, "disabled": sorted(state.disabled_needs), "task": state.task_active}

def start_moments() -> None:
    """Start the capture of the run's moments (the loop starts it), unless keep_moments is off or the hotkey's capture is running (two recorders would only get in each other's way). Never raises."""
    finish_moments()                                                   # (one left over from a loop that did not end properly)
    if not load_game_config()["keep_moments"] or (state.capture is not None and state.capture.running):
        return
    try:
        make_room_for_capture(cfg.MOMENTS_DIR, cfg.MOMENTS_KEEP)
        capture = InputCapture(os.path.join(cfg.MOMENTS_DIR, f"run{state.run_number}_{time.strftime('%Y%m%d_%H%M%S')}"), max_zips=cfg.MOMENTS_ZIPS, interval=cfg.MOMENTS_FRAME_INTERVAL,
                               protected_max=cfg.MOMENTS_PROTECTED, max_minutes=cfg.MOMENTS_MAX_HOURS * 60, inputs=False, context=moments_context,
                               reserve_bytes=int(cfg.MOMENTS_RESERVE_MB * 1024 * 1024))
        capture.start()
        state.moments, state.moments_marked, state.moments_notes = capture, 0.0, []
        state.moments_log = MomentsLog(capture.folder)
        logger.addHandler(state.moments_log)
    except Exception as error:
        logger.warning(f"Couldn't start the capture of the run's moments: {error}")

def note_moment(message: str) -> None:
    """A line of the run log for the moments capture (log_run_event() calls it); one that starts as MOMENTS_MARK_PREFIXES says is also a moment to keep (at most one every MOMENTS_MARK_GAP seconds). Never raises."""
    capture = state.moments
    if capture is None:
        return
    try:
        capture.note(message)
        if message.startswith(cfg.MOMENTS_MARK_PREFIXES) and time.time() - state.moments_marked >= cfg.MOMENTS_MARK_GAP:
            state.moments_marked = time.time()
            state.moments_notes.append((round(time.time() - capture.started), message[:200]))
            capture.mark(message[:80])
    except Exception as error:
        logger.warning(f"The capture of the run's moments: {error}")

def macro_version() -> str:
    """The commit the macro is at (git's short hash, and whether files are changed), or "unknown"."""
    try:
        run = lambda *args: subprocess.run(["git", *args], cwd=cfg.SCRIPT_DIR, capture_output=True, text=True, timeout=10).stdout.strip()
        return f"{run('rev-parse', '--short', 'HEAD')}{' (with changes)' if run('status', '--porcelain', '--untracked-files=no') else ''}"
    except Exception:
        return "unknown"

def copy_tail(source: str, target: str, limit: int) -> None:
    """The last `limit` bytes of the text file `source` into `target` (all of it when it is shorter); nothing if there is no source."""
    try:
        with open(source, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - limit))
            data = f.read()
        with open(target, "wb") as f:
            f.write(data)
    except OSError:
        pass

def write_moments_files(capture: InputCapture, seconds: float) -> None:
    """The files of a kept run's moments besides the frames: README.txt (what everything is and what the macro is, for a reader with nothing else), the run's own log (run_log.txt), status.json,
    config.json (the game config but for the private server link), failures.txt (what was saved for the run's failures), the commit in info.json. Never raises."""
    folder = capture.folder
    try:
        run = state.run_number
        copy_tail(os.path.join(cfg.RUNS_DIR, f"run{run}.txt"), os.path.join(folder, "run_log.txt"), int(cfg.MOMENTS_RUN_LOG_MB * 1024 * 1024))
        copy_tail(cfg.STATUS_PATH, os.path.join(folder, "status.json"), 1024 * 1024)
        config = {key: value for key, value in load_game_config().items() if key != "private_server_link"}
        with open(os.path.join(folder, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"game_config": config, "enabled_needs": sorted(cfg.ENABLED_NEEDS), "disabled_this_run": sorted(state.disabled_needs), "run_stats": asdict(run_stats)}, f, indent=1, default=str)
        failures = []
        if os.path.isdir(cfg.FAILURE_DIR):
            for name in sorted(os.listdir(cfg.FAILURE_DIR)):
                if f"_run{run}_" in name and os.path.isfile(os.path.join(cfg.FAILURE_DIR, name, "info.txt")):
                    with open(os.path.join(cfg.FAILURE_DIR, name, "info.txt"), encoding="utf-8", errors="replace") as f:
                        failures.append(f"--- {name} (its screen.png, need_icons.png and state.json are in debug/failures/)\n{f.read()}")
        with open(os.path.join(folder, "failures.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(failures) or "no failure folders were saved for this run\n")
        shots = sorted(name for name in os.listdir(cfg.FAILURE_DIR) if f"_run{run}_" in name and os.path.isfile(os.path.join(cfg.FAILURE_DIR, name, "screen.png"))) if os.path.isdir(cfg.FAILURE_DIR) else []
        if len(shots) > cfg.MOMENTS_FAILURE_SHOTS:
            shots = [shots[i * len(shots) // cfg.MOMENTS_FAILURE_SHOTS] for i in range(cfg.MOMENTS_FAILURE_SHOTS)]            # (spread over the run)
        for name in shots:
            picture = cv2.imread(os.path.join(cfg.FAILURE_DIR, name, "screen.png"))
            if picture is not None:
                cv2.imwrite(os.path.join(folder, f"failure_{name}.jpg"), picture, [cv2.IMWRITE_JPEG_QUALITY, 85])
        version = macro_version()
        info_path = os.path.join(folder, "info.json")
        with open(info_path, encoding="utf-8") as f:
            info = json.load(f)
        info.update(run=run, macro_version=version, run_seconds=round(seconds), moments=[{"seconds": t, "what": what} for t, what in state.moments_notes])
        with open(info_path, "w", encoding="utf-8") as f:
            json.dump(info, f, indent=1)
        marks = "\n".join(f"  {t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}  {what}" for t, what in state.moments_notes) or "  (none: nothing went wrong enough to mark)"
        with open(os.path.join(folder, "README.txt"), "w", encoding="utf-8") as f:
            f.write(MOMENTS_README.format(run=run, version=version, hours=seconds / 3600, started=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(capture.started)), frames=capture.frames, shots=cfg.MOMENTS_FAILURE_SHOTS,
                                          dropped=capture.dropped, zips=cfg.MOMENTS_ZIPS, marks=marks, tail=cfg.MOMENTS_TAIL, interval=cfg.MOMENTS_FRAME_INTERVAL, burst=cfg.RUN_CAPTURE_BURST))
    except Exception as error:
        logger.warning(f"Couldn't write the files of the run's moments: {error}")

MOMENTS_README = """MOMENTS OF RUN {run} - Adopt Me macro, commit {version}
Started {started}, ran {hours:.1f} hours. For improving the macro and fixing what failed; this folder is all that is needed.

THE MACRO: a Python program (github.com/ExaltedTime/AdoptMeMacro, main.py; docs/ARCHITECTURE.md describes it) that plays Roblox's Adopt Me by itself: it reads the need icons on the bar at the top of the
screen (hungry, thirsty, dirty, sleepy, ...), walks the character to the place that need is done and clicks, and checks the icon went away. It rejoins the game now and then, and collects a money tree, a lure
and the pet pen on timers. Screens are 1920x1080 (the positions in events are in that space).
PRIVACY: the frames are of the Roblox window only, but the game shows other players' names and chat; the private server link is not in any file here.

THE FRAMES (frame_<n>_<seconds>.png): lossless screenshots, <seconds> from the start of the capture. Taken every {interval:g} s, thinned out to fit {zips} zips ({frames} saved, {dropped} thinned): what is
left is spread over the run. Frames of the moments below were saved every 0.5 s for {burst:g} s and are never thinned, and so are the last {tail:g} s of the run.

THE FILES:
  events.jsonl   one JSON line per event, "t" = seconds from the start (the frames' clock):
                 type "frame": a frame saved, with "activity" (what the macro was doing: a need handler, a side action, setup, a rejoin, or waiting for needs), "detected" (the needs it last saw on the
                 bar), "cycles", "resolved" (needs resolved so far), "failures", "disabled" (needs switched off as stuck), "task" (a handler running);
                 type "log": a line of the run log (what happened: needs detected and resolved, failures, rejoins, popups closed); type "mark": a moment kept (see below); "frame_dropped": thinned out.
  output.log     everything the macro printed, debug lines too (the last part of the run when it was long), with times.
  run_log.txt    the run's own log (the same lines as the "log" events, with their wall-clock times).
  failures.txt   what the macro saved for each failure (reason, time, what was detected, disabled needs); failure_<folder>.jpg are their screenshots (the game window, JPEG; at most {shots} of them, spread over the run).
  status.json    the macro's status when the run ended. config.json: its settings (the private server link is left out), the enabled needs and the counts of the run.
  info.json      the capture's facts, the commit, and the moments.

THE MOMENTS KEPT (time into the capture, what was logged): a failure, a rejoin, a white screen, a recovery, a need disabled as stuck, or a quiet period (no need resolved for a while), at most one a minute:
{marks}

To read it: find a moment's time, open the frames around it (names carry the seconds), read events.jsonl and output.log around the same time, and compare what the macro logged with what the frames show.
"""

def finish_moments() -> None:
    """Stop the capture of the run's moments (the loop's end): after MOMENTS_MIN_RUN seconds or more its tail is kept, its files are written (write_moments_files()) and it is zipped, else it is deleted.
    Never raises."""
    capture, state.moments = state.moments, None
    if capture is None:
        return
    try:
        if state.moments_log is not None:
            logger.removeHandler(state.moments_log)
            state.moments_log.close()
            state.moments_log = None
        seconds = time.time() - capture.started
        long = seconds >= cfg.MOMENTS_MIN_RUN
        if long:
            capture.protect_tail(cfg.MOMENTS_TAIL)
        capture.stopped_because = capture.stopped_because or "the run ended"
        summary = capture.stop()
        if long:
            write_moments_files(capture, seconds)
            logger.info(f"Moments of the run: {summary}")
            zip_capture_in_background(capture.folder)
        else:
            shutil.rmtree(capture.folder, ignore_errors=True)
    except Exception as error:
        logger.warning(f"Couldn't finish the capture of the run's moments: {error}")

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
        pet_pen()
        pet_pen_swap_due()
        pet_repick()
        if process_needs():
            return
        respawn_if_no_detection()
        waiting_since = waiting_since or time.time()
        deadline = time.time() + cfg.NEED_CHECK_RETRY_DELAY
        while time.time() < deadline:
            logger.info(f"Waiting for a need... {format_duration(time.time() - waiting_since)}", extra={"status": True})      # (a line the console overwrites)
            wait_interruptible(min(1.0, deadline - time.time()))

def run_setup_if_requested() -> bool:
    """The Settings tab's "Run Setup at the next loop" (the `setup_next_loop` switch): clear it, untick its checkbox and run Setup once. A Setup that did not take is logged (setup_game() keeps the
    pictures); the loop goes on. True if it ran (it respawns by itself, so the loop's own respawn before it is left out)."""
    if not load_game_config()["setup_next_loop"]:
        return False
    update_game_config(lambda c: c.update(setup_next_loop=False))
    if state.macro_window is not None:
        state.macro_window.run_on_ui_thread(lambda: state.macro_window.config_flag_vars["setup_next_loop"].set(False), timeout=1.0)
    log_run_event("setup: asked for at the next loop")
    if not setup_game():
        log_run_event("setup: some step did not take (see the failures)")
    return True

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
    start_moments()
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
                    if not run_setup_if_requested():                           # (Setup respawns by itself: not twice)
                        respawn_character()
                    needs_respawn = False
                else:
                    run_setup_if_requested()
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
        finish_moments()
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
#   4. counts of failures by kind, popups and the like;
#   5. CPU and memory use: the script's, and the whole PC's as a second line.

LINE_RE = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\] \[RUN ([^\]]+)\] (.*)$")
RESOLVED_RE = re.compile(r"^resolved: (\S+) \((\d+)s\)$")
DETECTED_RE = re.compile(r"^detected: (.*)$")
NONE_MATCHED_RE = re.compile(r"^none matched")
IDLE_CHECKS_RE = re.compile(r"^idle checks: (\d+) over (\d+)s$")
SAME_CHECKS_RE = re.compile(r"^same checks: (\d+) over (\d+)s$")
DISABLED_RE = re.compile(r"^disabled: (\S+)$")
RESOURCES_RE = re.compile(r"^resources: cpu ([\d.]+)% mem (\d+)MB(?: pc ([\d.]+)%(?: pcmem (\d+)%)?)?$")

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
    isn't in it (keys: start, end, resolved, detected, failures, rejoins, disabled,
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
            "failures": [], "rejoins": [], "disabled": [], "resources": [], "events": Counter()}
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

    # 4. counts
    left, top = 820, 830
    draw_text(img, "Failures and events", left, top, 0.6, thick=2)
    y = top + 24
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

def newest_run_in_log() -> Optional[int]:
    """The number of the run the last line of the run log is of (a number from the log's last 200 kB), or None."""
    try:
        with open(cfg.RUN_LOG_PATH, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 200_000))
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return None
    runs = [m.group(2) for m in map(LINE_RE.match, text.split("\n")) if m]
    return int(runs[-1]) if runs and runs[-1].isdigit() else None

def report_from_outside(run: Optional[int]) -> str:
    """The `report` command: draw the chart of run `run` (the newest in the log when none is named) into RUNS_DIR - the charts are not drawn when a run stops. How it ended, for the bot."""
    run = run or newest_run_in_log()
    if run is None:
        return "given up: there is no run in the log"
    return "done" if write_run_report(run) else f"given up: run {run} is not in the log"

def test_need_handler(name: str, handler_cls: Optional[Callable[[], NeedHandler]] = None) -> None:
    """Run the need handler `name` on its own, outside the normal need-detection flow (the Debug tab's Test button, the `test` command), so it can be tried even
    when it is not in ENABLED_NEEDS; TEST_ALL_TELEPORTS runs every teleport handler in turn instead."""
    if name == cfg.TEST_ALL_TELEPORTS:
        run_all_need_handlers()
        return
    logger.info(f"[TEST] Running {name} handler...")
    try:
        with task_unscrew():
            (handler_cls or DEBUG_HANDLERS[name])().handle()
    except TaskInterrupted as interruption:
        logger.info(f"[TEST] {name} interrupted by {interruption.reason}")
    logger.info(f"[TEST] {name.capitalize()} handler complete")

def run_all_need_handlers(handlers: Optional[dict[str, Callable[[], NeedHandler]]] = None) -> dict[str, str]:
    """Run need handlers once each (the Debug tab's "test every teleport handler": TELEPORT_HANDLERS, or any given), respawning after
    each. Returns {need: "done" | "failed" | "interrupted by ..."}.
    See docs/ARCHITECTURE.md#gui-internals.
    """
    handlers = TELEPORT_HANDLERS if handlers is None else handlers
    results: dict[str, str] = {}
    logger.info(f"[TEST] Running all {len(handlers)} need handlers, one after another...")
    for i, (name, handler_cls) in enumerate(handlers.items(), 1):
        check_stop()
        logger.info(f"[TEST] ({i}/{len(handlers)}) {name}...")
        try:
            with task_unscrew(interruptible=False):    # (not abandoned by a popup: the test goes on)
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

OUTSIDE_ACTIONS: dict[str, Callable[[], Any]] = {"rejoin": leave_and_rejoin, "setup": setup_or_fail, "trade_hub": travel_to_trade_hub, "leave_trade_hub": leave_trade_hub, "inventory": inventory_tour, "sloop": leave_hub_and_setup,
                                          "trades": trades_tour, "recenter": recenter,
                                          **{f"test:{name}": partial(test_need_handler, name) for name in (*DEBUG_HANDLERS, cfg.TEST_ALL_TELEPORTS)},
                                          **{f"decline:{listing}:{number}": partial(decline_offer, listing, number) for listing in cfg.TRADES_LISTINGS for number in range(1, cfg.TRADES_MAX_OFFERS + 1)},
                                          **{f"decline:{listing}:{cfg.TRADES_ALL_OFFERS}": partial(decline_all, listing) for listing in cfg.TRADES_LISTINGS}}   # the commands that do what a button does (AdoptMeGUI.act_from_outside())

def blackout_seen_by_capture(before: Image, after: Image) -> Optional[bool]:
    """Whether the macro's screenshots see the window of the blackout test: `before` is a screenshot without it, `after` one with it up, white. Seen if
    the screen got BLACKOUT_SEEN_RISE grey levels brighter; None - cannot tell - if the screen was brighter than BLACKOUT_TOO_BRIGHT to begin with."""
    mean_before, mean_after = float(before.mean()), float(after.mean())
    if mean_before > cfg.BLACKOUT_TOO_BRIGHT:
        return None
    return mean_after - mean_before > cfg.BLACKOUT_SEEN_RISE

def virtual_display_present() -> bool:
    """Whether a virtual display (a software monitor: the Virtual Display Driver, see docs/ARCHITECTURE.md#blanking-the-screen) is installed on this Windows: the name of one of the PC's display
    adapters has one of VIRTUAL_DISPLAY_NAMES in it. False elsewhere, and when the adapters cannot be listed."""
    if os.name != "nt":
        return False
    try:
        from ctypes import wintypes

        class DisplayDevice(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("DeviceName", ctypes.c_wchar * 32), ("DeviceString", ctypes.c_wchar * 128), ("StateFlags", wintypes.DWORD),
                        ("DeviceID", ctypes.c_wchar * 128), ("DeviceKey", ctypes.c_wchar * 128)]
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]  # Windows only
        for number in range(cfg.DISPLAY_ADAPTERS_MAX):
            device = DisplayDevice()
            device.cb = ctypes.sizeof(device)
            if not user32.EnumDisplayDevicesW(None, number, ctypes.byref(device), 0):
                break
            if any(name in device.DeviceString.lower() for name in cfg.VIRTUAL_DISPLAY_NAMES):
                return True
    except (OSError, AttributeError, ValueError):
        pass
    return False

def display_switch(mode: str) -> bool:
    """Run Windows' own DisplaySwitch.exe (what Win+P does): `mode` is "/external" (the second screen only - the PC's own panel goes off) or "/internal" (the PC screen only). Nothing else is
    ever passed to it. False (logged) if it could not be run."""
    if mode not in ("/external", "/internal") or os.name != "nt":
        return False
    program = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "DisplaySwitch.exe")
    try:
        subprocess.run([program, mode], timeout=cfg.DISPLAY_SWITCH_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        logger.warning(f"DisplaySwitch {mode} failed: {error}")
        return False
    return True

def display_switch_worked() -> bool:
    """After the switch to the second screen: Roblox is found and a screenshot of it is not one flat colour (a black screen, or no picture)."""
    if roblox_window() is None:
        return False
    img = grab_screen()
    return bool(int(img.max()) - int(img.min()) >= cfg.BLACKOUT_FLAT_LEVEL)

def keep_awake(on: bool) -> None:
    """While the screen is blank, ask Windows not to sleep or turn the display off by itself (SetThreadExecutionState; the same thread - Tk's - must undo it, and it goes with the process)."""
    if os.name != "nt":
        return
    flags = 0x80000000 | (0x00000001 | 0x00000002 if on else 0)               # ES_CONTINUOUS, with ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED while on
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(flags)  # type: ignore[attr-defined]  # Windows only
    except (OSError, AttributeError):
        pass

def remember_display_switched(value: bool) -> None:
    """Keep in the game config whether the PC's own panel is off (a macro that dies while it is must put it back on its next start: Blackout.recover_display())."""
    update_game_config(lambda config: config.__setitem__("display_switched", value))

class Blackout:
    """The screen blanked by a black window that is click-through, never takes the focus, and is left out of screenshots (SetWindowDisplayAffinity with
    WDA_EXCLUDEFROMCAPTURE, Windows 10 version 2004 and later): the person sees black, while the macro's screenshots, recordings and the Discord bot's
    pictures still see the game, and its clicks and keys go to it. Turned on only after a test with the window white that the screenshots really do not see
    it (blackout_seen_by_capture()); if they do, or it cannot be told, it is taken away again. Tk's thread only. See docs/ARCHITECTURE.md#blanking-the-screen."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.window: Optional[tk.Toplevel] = None
        self.job: Optional[str] = None                 # the pending keep_on_top()
        self.display_switched = False                  # the PC's own panel is off: the game is on the virtual display (switch_display())

    @property
    def shown(self) -> bool:
        return self.window is not None or self.display_switched

    def switch_display(self) -> bool:
        """With a virtual display installed (virtual_display_present(); BLACKOUT_VIRTUAL_DISPLAY): switch to it alone, as Win+P's Second screen only does - the PC's own panel is then off, which
        no black window can make it - and look that Roblox is still there and shows a picture (display_switch_worked(), BLACKOUT_DISPLAY_SETTLE seconds after the switch). If not, back to the PC screen and
        False: the black window is used instead. True if the panel is off."""
        if not cfg.BLACKOUT_VIRTUAL_DISPLAY or not virtual_display_present():
            return False
        remember_display_switched(True)                                       # (before: a crash in the middle must not leave the panel off with nothing that knows)
        if display_switch("/external"):
            time.sleep(cfg.BLACKOUT_DISPLAY_SETTLE)
            if display_switch_worked():
                self.display_switched = True
                return True
            logger.warning("The virtual display showed no picture of Roblox - back to the PC screen")
        display_switch("/internal")
        time.sleep(cfg.BLACKOUT_DISPLAY_SETTLE)
        remember_display_switched(False)
        return False

    def recover_display(self) -> None:
        """At the macro's start: a panel that was left off by a macro that died (display_switched in the game config) is put back on."""
        if load_game_config().get("display_switched"):
            logger.warning("The PC's own screen was left off by an earlier run - switching it back on")
            display_switch("/internal")
            remember_display_switched(False)

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
        if self.switch_display():
            keep_awake(True)
            logger.info(f"Screen off: the game is on the virtual display ({cfg.HOTKEYS['blackout'].upper()} again to show it)")
            return True
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
        keep_awake(True)
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
        was_blank = self.shown
        if self.display_switched:
            display_switch("/internal")                                        # (the PC's own screen back; a macro that dies before this does it at its next start)
            time.sleep(cfg.BLACKOUT_DISPLAY_SETTLE)
            self.display_switched = False
            remember_display_switched(False)
        if was_blank:
            keep_awake(False)
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
        self.pending_stems: list[str] = []       # the names of those commands' files (finish_command() tells the bot how each ended), one for each in `pending`
        self.running_stem = ""                   # the name of the command whose action runs now
        self.resume_loop = False                 # a loop that was running was stopped for an action that is followed by the loop again (RESUME_LOOP_AFTER)
        self.running_loop = False                # what run_async() runs is the loop
        self.blackout = Blackout(self.root)
        self.blackout.recover_display()
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
        stem, data = read_command_with_id()
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
            self.do_command(command, data, stem)
        except Exception:
            logger.exception(f"The command from outside ({command}) failed")

    def do_command(self, command: str, data: Optional[dict[str, Any]], stem: str = "") -> None:
        """What poll_commands() does for one command (not `exit`); `stem` is its file's name, for the action that ends later (finish_command())."""
        if command == "start":
            self.start_loop_from_outside()
        elif command == "stop":
            if self.is_running:
                log_run_event("stopped from outside (the Discord bot)")
            self.stop()
        elif command == "set" and data:
            self.set_switch(data["kind"], data["name"], data["value"])
        elif command == "blackout":
            value = data.get("value") if data else None
            finish_command(stem, self.blackout_command(value if isinstance(value, bool) else None))
        elif command == "capture":
            value = data.get("value") if data else None
            finish_command(stem, self.capture_command(value if isinstance(value, bool) else None))
        elif command == "focus":
            focus_roblox()                                                          # the window to the front (no click): after an update the macro's own window may be
        elif command == "sloop":
            self.act_from_outside("sloop", stem, resume=True)                       # (leave the Trading Hub and) Setup, and then the loop, whether or not one was running
        elif command == "test" and data:
            self.act_from_outside(f"test:{data['handler']}", stem)                   # (what the Test button of that handler does; the loop is stopped first and not resumed)
        elif command == "decline" and data:
            self.act_from_outside(f"decline:{data['listing']}:{data['offer']}", stem)           # (the loop is stopped first and not resumed)
        elif command == "reset_config":
            finish_command(stem, self.reset_config_from_outside())
        elif command == "report":
            finish_command(stem, report_from_outside(data.get("run") if data else None))
        elif command == "server_link" and data:
            self.server_link_var.set(data["link"].strip())
            self.save_server_link()
            finish_command(stem, "done")
        elif command in OUTSIDE_ACTIONS:
            self.act_from_outside(command, stem)
        elif command == "refresh_debug":
            self.refresh_debug_from_outside()

    def act_from_outside(self, command: str, stem: str = "", resume: bool = False) -> None:
        """Queue what the button of the same name does (OUTSIDE_ACTIONS: `rejoin` is Leave & rejoin, `setup` is Setup, and the Trading Hub trips). Actions are
        done one after the other, in the order asked for (drain_pending()); a `stop` command stops what is running now and leaves the queue alone."""
        self.pending.append(command)
        self.pending_stems.append(stem)
        self.resume_loop = self.resume_loop or resume
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
                    self.resume_loop = self.resume_loop or self.pending[0] in cfg.RESUME_LOOP_AFTER
                    self.stop()
                self.pending_waits += 1
                if self.pending_waits * cfg.REJOIN_STOP_POLL_MS >= cfg.REJOIN_STOP_WAIT_MS:
                    logger.warning(f"{self.pending.pop(0)} asked for, but the running loop did not stop")
                    finish_command(self.pending_stems.pop(0), "given up: the running loop did not stop")
                    self.pending_waits = 0
                    self.resume_loop = False
        else:
            self.pending_waits = 0
            command = self.pending.pop(0)
            stem = self.pending_stems.pop(0)
            log_run_event(f"{command} asked for from outside (the Discord bot)")
            self.run_async(OUTSIDE_ACTIONS[command])
            self.running_stem = stem if self.is_running else ""
        self.root.after(cfg.REJOIN_STOP_POLL_MS, self.drain_pending)

    def reset_config_from_outside(self) -> str:
        """Reset the config as the Reset Config button does, without asking; not while something is running (it would go on with the old switches and totals). The
        text is how it ended, for the bot."""
        if self.is_running:
            logger.warning("Reset config asked for, but the macro is running - stop it first")
            return "given up: the macro is running"
        self.reset_config()
        return "done"

    def refresh_debug_from_outside(self) -> None:
        """Empty the debug folder as the Refresh debug button does, without asking; not while something is running, which is writing there."""
        if self.is_running or state.capture is not None:
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
                   (cfg.HOTKEYS["blackout"], self.blackout_by_hotkey, False, True),
                   (cfg.HOTKEYS["screenshot"], self.screenshot_by_hotkey, False, True),
                   (cfg.HOTKEYS["new_need"], self.new_need_by_hotkey, False, True)]   # (key, action, only while a run is going, only while Roblox is focused)

        def watch():
            was_down = [False] * len(hotkeys)
            reported = [False] * len(hotkeys)
            while True:
                for i, (key, action, only_while_running, only_in_roblox) in enumerate(hotkeys):
                    try:
                        down = hotkey_down(key)
                        if not state.typing and hotkey_fires(down, was_down[i], only_while_running, self.is_running, only_in_roblox):         # (not while the macro types a word: its own letters)
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

    def blackout_command(self, value: Optional[bool]) -> str:
        """The `blackout` command (Tk's thread): blank the screen (value True), show it (False) or switch it (None) - whatever the hotkey's checkbox says. What
        the screen is afterwards, for the bot: "blanked", "shown" or "not blanked" (the reason is in the log)."""
        want = (not self.blackout.shown) if value is None else value
        try:
            if want and not self.blackout.shown:
                self.blackout.show()
            elif not want:
                self.blackout.hide()
        except Exception:
            logger.exception("Blanking the screen failed")
            self.blackout.hide()
        return ("blanked (display off)" if self.blackout.display_switched else "blanked") if self.blackout.shown else ("not blanked" if want else "shown")

    def new_need_by_hotkey(self) -> None:
        """HOTKEYS["new_need"] was pressed in Roblox: look at the need bar and ask for the name of any icon on it the macro does not know (Tk's thread), unless the Options subtab's checkbox
        has the key off (it is then typed in the game)."""
        if not self.config_flag_vars["new_need_hotkey_enabled"].get():
            return
        try:
            teach_new_needs()
        except Exception:
            logger.exception("Looking for a new need failed")

    def screenshot_by_hotkey(self) -> None:
        """HOTKEYS["screenshot"] was pressed in Roblox: a picture of the whole screen into SCREENSHOTS_DIR (Tk's thread)."""
        try:
            logger.info(f"Screenshot saved: {save_screenshot()}")
        except Exception:
            logger.exception("Saving a screenshot failed")

    def capture_by_hotkey(self) -> None:
        """HOTKEYS["capture"] was pressed: start or stop the input capture (Tk's thread; the capture's own threads do the work)."""
        try:
            toggle_input_capture()
        except OSError as error:
            logger.warning(f"Input capture: {error}")

    def capture_command(self, value: Optional[bool] = None) -> str:
        """The `capture` command (Tk's thread): what the capture hotkey does - start a capture, or stop the one that is running - while a command of the bot goes on, to see what it
        does; with `value` True only a start (a capture that is running already is left: "running"), False only a stop ("none running" if there is none). What happened, for the bot:
        "started", "stopped", "running", "none running" or "failed: why"."""
        running = state.capture is not None and state.capture.running
        if value is True and running:
            return "running"
        if value is False and not running:
            return "none running"                                          # (a capture that stopped by itself is tidied up by the next toggle)
        try:
            return "started" if toggle_input_capture() is not None else "stopped"
        except OSError as error:
            logger.warning(f"Input capture: {error}")
            return f"failed: {error}"

    def blackout_by_hotkey(self) -> None:
        """HOTKEYS["blackout"] was pressed: blank the screen or show it again (Tk's thread), unless the Options subtab's checkbox has the key off (it is
        then typed in the game). The screen is only blanked while the macro is doing something (the loop or an action such as Setup); one that is blank can always be shown again."""
        if not self.config_flag_vars["blackout_hotkey_enabled"].get():
            return
        if not self.blackout.shown and not self.is_running:
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

        for label, action in (("Respawn", respawn_character), ("Leave & rejoin", leave_and_rejoin)):
            btn = tk.Button(btn_frame, text=label, command=lambda a=action: self.run_async(a),  # type: ignore[misc]
                            font=(cfg.GUI_FONT, cfg.GUI_SECTION_FONT_SIZE), bg=cfg.GUI_RESPAWN_COLOR, fg=self.fg,
                            height=cfg.GUI_RESPAWN_BUTTON_HEIGHT, cursor="hand2")
            btn.pack(fill=tk.X, pady=cfg.GUI_WIDGET_SPACING)
            self.action_buttons.append(btn)

        # Setup is not a button: it is ticked here and runs once, at the next pass of the loop (a loop that is running takes it at its next cycle)
        self.config_flag_vars: dict[str, tk.BooleanVar] = {}
        self.add_config_checkbox(btn_frame, "Run Setup at the next loop (once)", "setup_next_loop", load_game_config())

        # Settings tab
        # Persisted switches (see load_game_config()). Deliberately not in
        # action_buttons: they must stay usable while a workflow is running,
        # which is safe since update_game_config() is locked.
        self.build_settings_tab(options_tab)

        # Debug tab: the need handlers (a switch and a Test button each, regardless of whether it's in
        # ENABLED_NEEDS, so any can be run on its own, plus one that runs them all in turn).
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
                # A Setup run with the in-task checks on (the bot's /setup), and a disconnect made them
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
        was_loop = self.running_loop
        self.is_running = False
        self._set_running_ui(False, status_text)
        if self.running_stem:
            finish_command(self.running_stem, status_text.strip("[]").lower())
            self.running_stem = ""
        if self.resume_loop and not was_loop and not self.pending:                  # the action that stopped the loop has ended (and no other waits): the loop goes on
            self.resume_loop = False
            if status_text == "[DONE]":
                logger.info("The action is done - the loop that was running starts again")
                self.start_loop_from_outside()

    def _set_running_ui(self, running: bool, status_text: Optional[str] = None) -> None:
        """Single place that toggles button state + status text. Called both
        right before a background task starts and from its finally block
        when it ends, so the two are always kept in sync."""
        button_state = tk.DISABLED if running else tk.NORMAL
        for btn in self.action_buttons:
            btn.config(state=button_state)  # type: ignore[call-overload]
        self.status.config(text="Running..." if running else (status_text or "Ready"))
        run_stats.busy = running                                         # (in the status file, for the bot)
        write_status()

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
        """The Debug tab: an Options subtab (build_debug_options()) and a Needs one (build_needs_tab())."""
        subtabs = ttk.Notebook(parent)
        subtabs.pack(fill=tk.BOTH, expand=True)
        options_tab = tk.Frame(subtabs, bg=self.bg)
        needs_tab = tk.Frame(subtabs, bg=self.bg)
        subtabs.add(options_tab, text="Options")
        subtabs.add(needs_tab, text="Needs")
        self.build_debug_options(options_tab)
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

    def add_section(self, parent: Any, title: str) -> None:
        """A heading of the Settings tab, with a line under it."""
        tk.Label(parent, text=title, font=(cfg.GUI_FONT, cfg.GUI_SECTION_FONT_SIZE, "bold"), bg=self.bg, fg=self.accent, anchor=tk.W
                 ).pack(fill=tk.X, padx=cfg.GUI_WIDGET_SPACING, pady=(cfg.GUI_ROW_SPACING, 0))
        tk.Frame(parent, bg=self.accent, height=cfg.GUI_DIVIDER_HEIGHT).pack(fill=tk.X, padx=cfg.GUI_WIDGET_SPACING)

    def add_hint(self, parent: Any, text: str) -> None:
        """A line of small print on the Settings tab."""
        tk.Label(parent, text=text, font=(cfg.GUI_FONT, cfg.GUI_STATUS_FONT_SIZE), bg=self.bg, fg=self.accent, anchor=tk.W, justify=tk.LEFT
                 ).pack(fill=tk.X, padx=cfg.GUI_WIDGET_SPACING)

    def build_settings_tab(self, parent: Any) -> None:
        """Fill the Settings tab, in three groups: what the loop does, the rejoin and the keys (the debugging options are on the Debug tab). Every checkbox is a persisted switch (add_config_checkbox()), usable while a run goes on."""
        config = load_game_config()
        self.add_section(parent, "The loop")
        for label, key in (("Tend the money tree", "money_tree_enabled"),
                           ("Tend the lure", "lure_enabled"),
                           ("Resume after focus loss (take Roblox's focus back instead of stopping)", "resume_on_focus_loss"),
                           ("Collect the Pet Pen when the Task Board says it is ready", "pet_pen_enabled"),
                           ("Setup levels a pet that still grows (no eggs, none full grown)", "level_growing_pet")):
            self.add_config_checkbox(parent, label, key, config)

        self.add_section(parent, "Rejoin")
        self.add_config_checkbox(parent, "Rejoin the private server (off: a public one)", "use_private_server", config)
        self.add_hint(parent, "Private server link")
        self.server_link_var = tk.StringVar(value=config["private_server_link"])
        server_link_entry = tk.Entry(parent, textvariable=self.server_link_var, font=(cfg.GUI_FONT, cfg.GUI_SECTION_FONT_SIZE),
                                     bg=cfg.GUI_CONSOLE_BG, fg=cfg.GUI_CONSOLE_FG, insertbackground=cfg.GUI_CONSOLE_FG)
        server_link_entry.pack(fill=tk.X, padx=cfg.GUI_WIDGET_SPACING, pady=cfg.GUI_WIDGET_SPACING)
        server_link_entry.bind("<FocusOut>", lambda e: self.save_server_link())
        server_link_entry.bind("<Return>", lambda e: self.save_server_link())

        self.add_section(parent, "Keys")
        self.add_config_checkbox(parent, f"{cfg.HOTKEYS['blackout'].upper()} blanks the screen in Roblox (off: type it)", "blackout_hotkey_enabled", config)
        self.add_config_checkbox(parent, f"{cfg.HOTKEYS['new_need'].upper()} teaches a new need icon in Roblox (off: type it)", "new_need_hotkey_enabled", config)
        self.add_hint(parent, f"{cfg.HOTKEYS['stop'].upper()} stops, anywhere. {cfg.HOTKEYS['capture'].upper()} starts / stops an input capture (in Roblox). "
                              f"{cfg.HOTKEYS['screenshot'].upper()} saves a screenshot to debug/screenshots (in Roblox)")

    def build_debug_options(self, parent: Any) -> None:
        """Fill the Debug tab's Options subtab: record the next run, capture the moments, show the debug lines in the output, refresh the debug folder, reset the config."""
        config = load_game_config()
        self.add_config_checkbox(parent, "Record the next run (low quality video)", "record_next_run", config)
        self.add_config_checkbox(parent, "Capture the moments of a run (kept when it lasts over an hour)", "keep_moments", config)
        self.show_debug_var = tk.BooleanVar(value=True)
        tk.Checkbutton(parent, text="Show debug lines in the output", variable=self.show_debug_var, command=self.set_debug_lines_shown,
                       font=(cfg.GUI_FONT, cfg.GUI_SECTION_FONT_SIZE), bg=self.bg, fg=self.fg,
                       selectcolor=self.bg, activebackground=self.bg, activeforeground=self.fg,
                       anchor=tk.W, cursor="hand2").pack(fill=tk.X, pady=cfg.GUI_WIDGET_SPACING)
        buttons = tk.Frame(parent, bg=self.bg)
        buttons.pack(fill=tk.X, padx=cfg.GUI_WIDGET_SPACING, pady=cfg.GUI_WIDGET_SPACING)
        buttons.grid_columnconfigure(0, weight=1, uniform="settings")
        buttons.grid_columnconfigure(1, weight=1, uniform="settings")
        tk.Button(buttons, text="Refresh debug", command=self.refresh_debug_folder, font=(cfg.GUI_FONT, cfg.GUI_SECTION_FONT_SIZE),
                  bg=self.accent, fg=self.fg, cursor="hand2").grid(row=0, column=0, sticky=tk.EW, padx=(0, cfg.GUI_WIDGET_SPACING))
        tk.Button(buttons, text="Reset Config", command=self.reset_config, font=(cfg.GUI_FONT, cfg.GUI_SECTION_FONT_SIZE),
                  bg=cfg.GUI_STOP_COLOR, fg=self.fg, cursor="hand2").grid(row=0, column=1, sticky=tk.EW, padx=(cfg.GUI_WIDGET_SPACING, 0))

    def refresh_debug_folder(self) -> None:
        """Ask, then empty the debug folder (refresh_debug()). Not while a run
        is going - it's writing there."""
        if self.is_running or state.capture is not None:
            logger.debug("Stop the run and the capture before refreshing the debug folder")
            return
        if messagebox.askyesno("Refresh debug", "Delete everything in the debug folder (logs, images, recordings)?\n"
                                                "The game config is kept."):
            logger.debug(f"Debug folder refreshed ({refresh_debug()} entries removed)")

    def test_handler(self, name: str, handler_cls: Callable[[], NeedHandler]) -> None:
        """Run one need handler on its own, outside the normal need-detection
        flow, so it can be tried even when it isn't in ENABLED_NEEDS."""
        self.run_async(lambda: test_need_handler(name, handler_cls))

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
