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

import os, sys, re, time, threading, json, subprocess, traceback, ctypes, queue, random, contextlib, shutil
import datetime
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Callable, Iterable, Iterator, Optional, Sequence, Union
from abc import ABC, abstractmethod
from functools import partial
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
    """Raised from inside a need handler's waits when something that can't
    wait (a minigame, a rejoin) took over the character: whatever the handler
    was doing is abandoned. process_needs() catches it; the next check
    decides from the screen what's still needed. `reason` is for the log."""
    def __init__(self, reason: str = "something") -> None:
        super().__init__(reason)
        self.reason = reason

class StopRequested(Exception):
    """Raised to unwind out of a running workflow when the user presses [STOP].

    This is deliberately cooperative cancellation, not thread-killing. Python
    has no safe way to force-terminate a running thread from the outside -
    the closest trick (async-raising into another thread via ctypes) is
    explicitly unsafe, and for a game macro that matters a lot: if the kill
    landed while a movement key or the mouse button was held down, it would
    never get released and would stay stuck in the game.

    Instead, wait_interruptible() and check_running() raise this the moment
    they notice STOP_EVENT, and ordinary Python exception propagation unwinds
    the call stack from wherever we are all the way up to run_async()'s
    worker thread, which catches it. Any try/finally in between (used
    wherever a key or the mouse is held down) still runs on the way up, so
    cleanup always happens no matter where the stop lands - without every
    function needing to manually check a flag after every single action."""
    pass

class FocusLost(Exception):
    """Raised when Roblox is no longer the focused window while a workflow
    is running. Handled the same way as StopRequested - it unwinds via
    ordinary exception propagation, releasing any held key/mouse button via
    the same try/finally blocks along the way - so the macro never keeps
    sending clicks or keypresses into whatever window the user switched to."""
    pass

def hotkey_down(key: str) -> bool:
    """True while `key` (a single letter or digit) is held down, whichever
    window has the focus (Windows)."""
    return bool(ctypes.windll.user32.GetAsyncKeyState(ord(key.upper())) & 0x8000)  # type: ignore[attr-defined]  # Windows only

def check_stop() -> None:
    """Raise StopRequested if the user has pressed [STOP]. Prefer
    check_running() at checkpoints that should also require Roblox focus -
    this exists on its own for the one checkpoint that shouldn't (see
    run_full_cycle())."""
    if STOP_EVENT.is_set():
        raise StopRequested()

def check_focus() -> None:
    """Raise FocusLost if Roblox is running but is no longer the focused
    window. Prefer check_running() at most checkpoints."""
    if not is_roblox_focused():
        print("\n[!] Roblox is no longer focused")
        raise FocusLost()

def stop_pressed_soon() -> bool:
    """True if [STOP] is pressed within STOP_CLICK_GRACE. Clicking the button
    takes the focus from Roblox on mouse-down, a moment before the click sets
    STOP_EVENT, so a FocusLost seen right then is really the user stopping."""
    return STOP_EVENT.wait(cfg.STOP_CLICK_GRACE)

def check_running() -> None:
    """Raise StopRequested or FocusLost if the workflow should not continue
    right now. This is the single checkpoint used everywhere - inside
    wait_interruptible() and any manual loop that doesn't otherwise call it
    - so both a [STOP] press and tabbing away from Roblox are noticed at
    the same, frequent cadence."""
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
        print("[!] Roblox not found")
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
    return active is not None and "roblox" in (active.title or "").lower()

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
    """(left, top, width, height), in screen pixels, of the Roblox window
    clipped to the primary monitor - a maximized window reports a few pixels
    of invisible border past the screen edges, which this trims, so a
    maximized window on a 1920x1080 screen is exactly (0, 0, 1920, 1080).
    The whole monitor if there's no usable Roblox window (not running,
    minimized). Remembered for ROBLOX_RECT_TTL seconds."""
    global _ROBLOX_RECT
    now = time.time()
    if _ROBLOX_RECT and now - _ROBLOX_RECT[0] < cfg.ROBLOX_RECT_TTL:
        return _ROBLOX_RECT[1]
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
    """Screenshot of the Roblox window as a BGR numpy array, scaled to the
    reference size (REFERENCE_WIDTH x REFERENCE_HEIGHT) so every detection
    and position works on the same pixels whatever the window or monitor
    size. Unchanged from a plain screenshot when Roblox is maximized on a
    1920x1080 screen."""
    img = grab_full_screen()
    left, top, width, height = roblox_rect()
    if (left, top, width, height) != (0, 0, img.shape[1], img.shape[0]):
        img = img[top:top + height, left:left + width]
    if img.shape[1] != cfg.REFERENCE_WIDTH or img.shape[0] != cfg.REFERENCE_HEIGHT:
        img = cv2.resize(img, (cfg.REFERENCE_WIDTH, cfg.REFERENCE_HEIGHT), interpolation=cv2.INTER_AREA)
    return img

def grab_screen_region(x: int, y: int, width: int, height: int) -> Image:
    """The same pixels as grab_screen()[y:y + height, x:x + width] (so `x`, `y`,
    `width` and `height` are in the reference space), but when the Roblox
    window is exactly the reference size, and the region is within it, only
    that region is captured rather than the whole monitor and then cropped.
    Otherwise it is exactly that crop of grab_screen()."""
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
    # Where each need's icon was last recognised in the need bar (reference
    # space, centre), by need name - see icon_gone_by_frame_change().
    need_icon_positions: dict[str, tuple[int, int]] = field(default_factory=dict)
    # Each need's icon as it last looked when the bar could be read (identify_icons()): what
    # icon_gone_by_frame_change() looks for when it can't read the bar - its stored position can be
    # another need's icon by then, the others having shifted about.
    need_icon_crops: dict[str, Image] = field(default_factory=dict)
    # How many in-task checks in a row have seen the backpack open (see
    # close_backpack_if_stuck()).
    backpack_strikes: int = 0
    # True while a handler that wants the pet focused runs (see wanting_pet_focus()).
    pet_focus_wanted: bool = False
    # When the minigame popup checks last ran, and how many "quiet:" lines have
    # been logged for the current stretch with no need resolved.
    last_minigame_popup_check: float = 0.0
    quiet_reported: int = 0

    def snapshot(self) -> dict[str, Any]:
        """The state as a JSON-friendly dict (the GUI object left out), for
        saving with a failure."""
        out = {}
        for item in fields(self):
            value = getattr(self, item.name)
            if item.name == "macro_window":
                value = None if value is None else "set"
            elif isinstance(value, set):
                value = sorted(value)
            elif item.name == "need_icon_crops":
                value = {name: f"image {crop.shape[1]}x{crop.shape[0]}" for name, crop in list(value.items())}   # pictures: not JSON
            out[item.name] = value
        return out

state = State()

# ============================================================================
# LOGGING
# ============================================================================

def migrate_debug_layout() -> None:
    """Move what an older version left in debug/ into the images/ and logs/
    subfolders (see IMAGES_DIR / LOGS_DIR), if it isn't there already. Never
    raises."""
    moves = [(name, cfg.LOGS_DIR) for name in ("run_log.txt", "run_counter.txt", "output.log", "output.log.old", "status.json")]
    moves += [(name, cfg.IMAGES_DIR) for name in ("failures", "reports")]
    try:
        moves += [(name, cfg.IMAGES_DIR) for name in os.listdir(cfg.DEBUG_DIR) if name.startswith("debug_") and name.endswith(".png")]
        for name, folder in moves:
            source, target = os.path.join(cfg.DEBUG_DIR, name), os.path.join(folder, name)
            if os.path.exists(source) and not os.path.exists(target):
                os.replace(source, target)
    except OSError as error:
        print(f"[!] couldn't move the old debug files: {error}")

migrate_debug_layout()

def refresh_debug() -> int:
    """Empty the debug folder - the images, the logs, the recordings - except
    the game config (GAME_CONFIG_PATH), then recreate the subfolders. The run
    numbers start from 1 again, since the counter goes with the logs. Returns
    the number of entries removed."""
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
            print(f"[!] couldn't delete {name}: {error}")
    for directory in (cfg.IMAGES_DIR, cfg.LOGS_DIR):
        os.makedirs(directory, exist_ok=True)
    return removed

def next_run_number() -> int:
    """Return a fresh run number, persisted in RUN_COUNTER_PATH so numbers
    stay unique across separate launches of the script, not just within one
    session."""
    try:
        with open(cfg.RUN_COUNTER_PATH, "r") as f:
            n = int(f.read().strip())
    except (FileNotFoundError, ValueError):
        n = 0
    n += 1
    with open(cfg.RUN_COUNTER_PATH, "w") as f:
        f.write(str(n))
    return n

def reset_need_tracking() -> None:
    """Forget the stuck-need streaks (see record_detection())."""
    state.attempt_streaks.clear()
    state.attempted_last = set()
    state.none_matched_logged = False

def note_idle_check() -> None:
    """Count one check that found no need. They are logged together, as one "idle checks: N over Ss" line
    (flush_idle_checks()), so a long wait doesn't write a line every few seconds - and the run report can still
    count every check, not just the ones that found something."""
    if state.idle_checks == 0:
        state.idle_since = time.time()
    state.idle_checks += 1

def flush_idle_checks() -> None:
    """Write the counted idle checks (note_idle_check()) to the run log and start counting again."""
    if state.idle_checks:
        log_run_event(f"idle checks: {state.idle_checks} over {time.time() - state.idle_since:.0f}s")
        state.idle_checks = 0

def flush_idle_checks_if_due() -> None:
    """flush_idle_checks() once QUIET_LOG_INTERVAL has passed since the first of them, so a long idle stretch
    reaches the log (and a crash loses at most that much)."""
    if state.idle_checks and time.time() - state.idle_since >= cfg.QUIET_LOG_INTERVAL:
        flush_idle_checks()

def log_run_event(message: str) -> None:
    """Append one timestamped line to RUN_LOG_PATH, tagged with
    state.run_number - and, when a run number is set, to that run's own
    file, RUN_LOGS_DIR/run<N>.txt (lines logged before any run has started
    only go to RUN_LOG_PATH)."""
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [RUN {state.run_number}] {message}\n"
    with open(cfg.RUN_LOG_PATH, "a") as f:
        f.write(line)
    if state.run_number is not None:
        try:
            os.makedirs(cfg.RUN_LOGS_DIR, exist_ok=True)
            with open(os.path.join(cfg.RUN_LOGS_DIR, f"run{state.run_number}.txt"), "a") as f:
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

    def reset(self) -> None:
        """Start afresh (a workflow was just started)."""
        fresh = RunStats()
        for item in fields(self):
            setattr(self, item.name, getattr(fresh, item.name))
        self.run, self.started = state.run_number, time.strftime("%Y-%m-%d %H:%M:%S")

run_stats = RunStats()

OUTPUT_LOG_LOCK = threading.Lock()
_output_at_line_start = True
_output_log = None   # the open output.log, kept open between writes (see log_output())

def close_output_log() -> None:
    """Close output.log if it's open (it reopens on the next write). Needed
    before anything deletes or moves the file - on Windows an open file can't
    be - e.g. refresh_debug()."""
    global _output_log
    with OUTPUT_LOG_LOCK:
        if _output_log is not None:
            _output_log.close()
            _output_log = None

def log_output(text: str) -> None:
    """Append `text` (whatever was just printed) to OUTPUT_LOG_PATH, with a
    timestamp at the start of every line. Called by DebugCapture, so it's
    everything the GUI console shows, kept on disk. The file stays open
    between calls and is flushed after each one, so nothing is lost if the
    macro dies. Never raises. Past OUTPUT_LOG_MAX_BYTES the file moves to
    output.log.old and a new one starts."""
    global _output_at_line_start, _output_log
    try:
        with OUTPUT_LOG_LOCK:
            if _output_log is None:
                _output_log = open(cfg.OUTPUT_LOG_PATH, "a", encoding="utf-8")
            for piece in text.splitlines(keepends=True):
                if _output_at_line_start:
                    _output_log.write(time.strftime("[%Y-%m-%d %H:%M:%S] "))
                _output_log.write(piece)
                _output_at_line_start = piece.endswith("\n")
            _output_log.flush()
            if _output_log.tell() > cfg.OUTPUT_LOG_MAX_BYTES:
                _output_log.close()
                _output_log = None
                os.replace(cfg.OUTPUT_LOG_PATH, cfg.OUTPUT_LOG_PATH + ".old")
    except OSError:
        pass

def save_failure_report(reason: str) -> Optional[str]:
    """Save what was going on when `reason` went wrong, as a folder in
    FAILURE_DIR named with the time, run number and reason: screen.png (the
    whole screen, to see what has focus too), need_icons.png (the strip the
    need icons are read from, when Roblox could be captured), info.txt (a
    short summary) and state.json (the whole State object and run_stats, as
    they were). Deletes the oldest beyond MAX_FAILURE_FOLDERS. Returns
    "<folder>/screen.png", or None if it couldn't (never raises - it's called
    while something else is already going wrong)."""
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
        print(f"[!] couldn't save a failure report: {e}")
        return None

def write_status() -> None:
    """Write run_stats, the currently disabled needs and the time to
    STATUS_PATH (replacing it atomically). Never raises."""
    try:
        status = {**asdict(run_stats), "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                  "disabled_needs": sorted(state.disabled_needs)}
        tmp = cfg.STATUS_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(status, f, indent=2)
        os.replace(tmp, cfg.STATUS_PATH)
    except (OSError, TypeError):
        pass

def log_failure(reason: str, screenshot: bool = True) -> None:
    """Record that something went wrong: print it, add it to the run log,
    save a screenshot of what was on screen, and note it in the status
    file. Doesn't change what the macro does next."""
    shot = save_failure_report(reason) if screenshot else None
    detail = f"{reason}" + (f" (screenshot: failures/{shot})" if shot else "")
    print(f"[!] FAILURE: {detail}")
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
    defaults = {
        "money_collected": 0,
        "lure_timer": time.time(),  # due immediately on a fresh config
        "tree_timer": time.time(),  # likewise
        "money_reset_boundary": None,   # the last daily MONEY_RESET_HOUR reset side_quest() has dealt with (None: not yet)
        "side_quest_enabled": True, # whether side_quest() is allowed to run at all
        "resume_on_focus_loss": False,  # whether the loop takes Roblox's focus back instead of stopping
        "record_next_run": False,   # record the next run as a low quality video; cleared when that run starts
        **{f"{name}_enabled": False for name in cfg.MINIGAME_LABELS},  # which Halloween minigames get played (see minigame_popup())
        "private_server_link": "",  # used by leave_and_rejoin(); kept here, not in the source, since it's a join key
    }
    try:
        with open(cfg.GAME_CONFIG_PATH, "r") as f:
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
    """Persist `config` (as returned by load_game_config()) to GAME_CONFIG_PATH.
    Written to a temporary file and moved into place, so a crash partway
    can't leave a truncated config behind (which would read as the defaults).
    Callers outside this section hold GAME_CONFIG_LOCK - use
    update_game_config()."""
    tmp = cfg.GAME_CONFIG_PATH + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(config, f)
        os.replace(tmp, cfg.GAME_CONFIG_PATH)
    except OSError:
        with open(cfg.GAME_CONFIG_PATH, "w") as f:   # the move wasn't allowed (the file is open elsewhere): write it in place
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

def icon_signature(icon_img: Image) -> Image:
    """Reduced, comparable form of an icon: preprocessed, resized to
    ICON_COMPARE_SIZE, scaled to 0-1, and (if MATCH_ONLY_LEFT_HALF) cut down
    to its left half so an overlapping event badge doesn't matter."""
    proc = cv2.resize(preprocess_icon(icon_img), cfg.ICON_COMPARE_SIZE).astype(np.float32) / cfg.PIXEL_MAX
    if cfg.MATCH_ONLY_LEFT_HALF:
        proc = proc[:, :proc.shape[1] // 2]
    return proc

def compare_signatures(sig1: Image, sig2: Image) -> float:
    """Return a 0-1 similarity score (1 = identical) between two signatures."""
    return 1.0 - float(np.mean((sig1 - sig2) ** 2))

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

def detect_need_icons(save_debug: bool = False, fresh: bool = False) -> tuple[list[tuple[int, int, int]], Image]:
    """Detect the circular need icons at the top of the screen. Icons are
    found by their round outline (Hough circles, within a fixed radius
    range), not by color, so event badges overlapping an icon, differently
    colored icons and busy backgrounds don't affect detection. The circle
    search is skipped, reusing the last result, while the strip the icons sit
    in is practically unchanged since it was last searched (at most
    NEED_STRIP_MAX_CHANGED_FRACTION of it changed, and no more than
    NEED_STRIP_MAX_REUSES times running - NEED_STRIP_MAX_EMPTY_REUSES if that
    result found nothing, since a missed icon looks the same); an icon
    appearing or going changes far more than that. `fresh` always searches
    (and refreshes the cache): used for the second look of _need_cleared(),
    which exists to survive one wrong result. Only the strip, plus the margin the icon crops reach
    past it, is captured (grab_screen_region()): returns ((cx, cy, radius) per
    icon, left to right) and that image, whose pixels sit at the same
    positions as in grab_screen() (it just ends sooner).
    Everything that uses the image (identify_icons(), detect_need_bar_readable())
    stays within it."""
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

    if save_debug:
        debug_img = top_strip.copy()
        for cx, cy, r in found:
            cv2.circle(debug_img, (cx, cy), r, cfg.DEBUG_NEED_MARKER_COLOR, cfg.DEBUG_NEED_MARKER_THICKNESS)
        cv2.imwrite(os.path.join(cfg.IMAGES_DIR, "debug_needs.png"), debug_img)

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
    """[(need name, signature)] for every saved need icon. Reading and
    preprocessing every .png in NEEDS_DIR is far too slow to repeat for each
    icon on each check, so it's done once and redone only when the files
    change (checking their modification times is cheap), which also picks up
    an icon added while the macro runs. Recurses, so related icons can sit in
    subfolders (e.g. ref/needs/weather/) - a need's name is just its file name,
    wherever it lives."""
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
    """Save a new need icon (as high-contrast B/W) and ask the user to name
    it - in a dialog when the GUI is up (the worker thread has no console to
    read from), otherwise on the console. Cancelling or an empty name saves
    it as unnamed_<idx>."""
    print("\n[!] NEW NEED DETECTED!")
    temp_path = os.path.join(cfg.NEEDS_DIR, f"temp_{idx}.png")
    cv2.imwrite(temp_path, preprocess_icon(icon_img))

    prompt = "Need name (hungry/thirsty/dirty/potty/sleepy/catch/walk/other):"
    if state.macro_window is not None:
        answer = state.macro_window.run_on_ui_thread(
            lambda: simpledialog.askstring("New need", prompt, parent=state.macro_window.root), timeout=None)
        focus_roblox_click()   # the dialog took the focus
    else:
        print(f"[!] {prompt} ", end="", flush=True)
        answer = input()
    need_name = (answer or "").strip().lower() or f"unnamed_{idx}"

    os.rename(temp_path, os.path.join(cfg.NEEDS_DIR, f"{need_name}.png"))
    print(f"[!] Saved: {need_name}")
    return need_name

def identify_icons(found_icons: list[tuple[int, int, int]], full_img: Image) -> Iterator[tuple[int, Image, Optional[str], float]]:
    """Match each detected icon against the saved needs. Yields
    (index, icon_image, need_name_or_None, score) per icon, checking
    check_running() between icons."""
    for idx, (cx, cy, _) in enumerate(found_icons):
        check_running()
        icon_img = extract_icon(full_img, cx, cy, cfg.ICON_CROP_RADIUS)
        need_name, score = find_matching_need(icon_variants(full_img, cx, cy))
        if need_name:
            state.need_icon_positions[need_name] = (cx, cy)
            r = cfg.ICON_CROP_RADIUS
            crop = full_img[cy - r:cy + r + 1, cx - r:cx + r + 1] if cx >= r and cy >= r else None
            if crop is not None and crop.shape[:2] == (2 * r + 1, 2 * r + 1):
                state.need_icon_crops[need_name] = crop.copy()
        yield idx, icon_img, need_name, score

def detect_need_bar_readable(img: Image) -> bool:
    """False if the strip the icons sit in is mostly near-white in `img`
    (NEED_BAR_BRIGHT_LEVEL / NEED_BAR_MAX_BRIGHT_FRACTION) - the Pizza Party's
    bright background, say. The icons can't be recognised against that, so
    a missing icon there says nothing about whether the need is gone."""
    strip = img[0:int(cfg.REFERENCE_HEIGHT * cfg.NEED_ICON_TOP_PERCENT),
                int(cfg.REFERENCE_WIDTH * cfg.NEED_ICON_BLANK_WIDTH):int(cfg.REFERENCE_WIDTH * cfg.NEED_ICON_WIDTH_PERCENT)]
    bright = np.count_nonzero(cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY) > cfg.NEED_BAR_BRIGHT_LEVEL)
    return bright / (strip.shape[0] * strip.shape[1]) < cfg.NEED_BAR_MAX_BRIGHT_FRACTION

def scan_needs(fresh: bool = False) -> tuple[list[Optional[str]], bool]:
    """One-shot snapshot: (every need name currently detected on screen, whether
    the icons could be read at all - see detect_need_bar_readable()). `fresh`
    makes the circle search run even if the strip looks unchanged (see
    detect_need_icons())."""
    found_icons, full_img = detect_need_icons(fresh=fresh)
    if not detect_need_bar_readable(full_img):
        return [], False
    return [name for _, _, name, _ in identify_icons(found_icons, full_img)], True

# The icon region being watched while the need bar is unreadable: {"need", "pos", "crop"} - see icon_gone_by_frame_change().
_ICON_WATCH: dict[str, Any] = {"need": None, "pos": None, "crop": None}

def reset_icon_watch() -> None:
    """Forget the icon being watched: each wait for a need to clear starts from the picture of its icon taken
    when it was last read, not from where an earlier wait for the same need happened to leave off."""
    _ICON_WATCH.update(need=None, pos=None, crop=None)

def icon_region(pos: tuple[int, int]) -> Optional[Image]:
    """The square around a need icon centred on `pos` (ICON_CROP_RADIUS each
    way), captured now - or None if it would reach past the screen's edge."""
    radius = cfg.ICON_CROP_RADIUS
    x0, y0 = pos[0] - radius, pos[1] - radius
    if x0 < 0 or y0 < 0:
        return None
    return grab_screen_region(x0, y0, 2 * radius + 1, 2 * radius + 1)

def icon_region_change(before: Image, after: Image) -> float:
    """The fraction of the circle of an icon-sized square that differs between
    two captures by more than ICON_FRAME_CHANGE_LEVEL on any colour channel."""
    size = before.shape[0]
    yy, xx = np.ogrid[:size, :size]
    inside = (yy - size // 2) ** 2 + (xx - size // 2) ** 2 <= (size // 2) ** 2
    changed = cv2.absdiff(before, after).max(axis=2) > cfg.ICON_FRAME_CHANGE_LEVEL
    return float(np.count_nonzero(changed & inside) / np.count_nonzero(inside))

def locate_icon(template: Image, row: int) -> Optional[tuple[int, int]]:
    """Where `template` (an icon-sized capture of a need icon) is in the need
    bar now: the centre (x, y) of its best match along the bar at about `row`,
    or None if nothing there matches well enough (ICON_LOCATE_THRESHOLD). The
    match is on the whiteness of the pixels (the least of B, G, R), which is
    the icon's white ring and glyph - they don't change as its timer
    changes the colour of a section of the disc - and a bright background or
    another icon matches poorly. The whole width of the bar is searched, so
    the icon is found wherever the others shifting around it have put it."""
    radius = cfg.ICON_CROP_RADIUS
    y0 = max(0, row - radius - cfg.ICON_LOCATE_ROW_MARGIN)
    band = grab_screen_region(0, y0, int(cfg.REFERENCE_WIDTH * cfg.NEED_ICON_WIDTH_PERCENT), 2 * radius + 2 * cfg.ICON_LOCATE_ROW_MARGIN + 1)
    scores = cv2.matchTemplate(band.min(axis=2), template.min(axis=2), cv2.TM_CCOEFF_NORMED)
    _, best, _, where = cv2.minMaxLoc(scores)
    if best < cfg.ICON_LOCATE_THRESHOLD:
        return None
    return where[0] + radius, y0 + where[1] + radius

def icon_gone_by_frame_change(need_name: str) -> Optional[bool]:
    """Whether `need_name`'s icon is gone, judged from how it changes between
    looks - for when the bar can't be read by matching icons (a bright
    background, see detect_need_bar_readable()). A need's icon fills its
    circle with a timer: a section that slowly grows and changes colour. So
    each look searches the bar for the icon *as it looked at the previous look*
    (locate_icon()): the timer moves on a little between looks, and the
    icon's white ring and glyph still match. Found - wherever the other icons
    shifting about when one clears have put it, which is noted in
    State.need_icon_positions - means it is still there. Not found, and still
    not found NEED_GONE_FLICKER_RECHECK_DELAY later (so a one-frame glitch
    doesn't count), means it is gone: cleared, or replaced by a neighbour that
    has slid into its place. The icon's starting place is where it was last
    recognised. The first look only records the icon (None: nothing to
    compare with yet), so one that went before that is not noticed, and the
    wait runs out as it did before. None too if the place isn't known."""
    pos = state.need_icon_positions.get(need_name)
    here = icon_region(pos) if pos is not None else None
    if pos is None or here is None:
        return None
    watch = _ICON_WATCH
    if watch["need"] != need_name or watch["crop"] is None:
        stored = state.need_icon_crops.get(need_name)
        if stored is None:
            watch.update(need=need_name, pos=pos, crop=here)
            return None
        # What is at the stored position now may be another need's icon (the others shift as they clear):
        # search for the icon as it looked when it was last read instead, so a clear is noticed at the first look.
        watch.update(need=need_name, pos=pos, crop=stored)
    found = locate_icon(watch["crop"], pos[1])
    if found is None:
        wait_interruptible(cfg.NEED_GONE_FLICKER_RECHECK_DELAY)
        found = locate_icon(watch["crop"], pos[1])
        if found is None:
            print(f"[debug] {need_name}: need bar unreadable, and its icon is no longer in the bar")
            log_run_event(f"{need_name} icon gone by frame change - the need bar is unreadable")
            reset_icon_watch()
            return True
    now = icon_region(found)
    if now is None:
        return None
    change = icon_region_change(watch["crop"], now) if found == pos else None
    where = f"{pos} -> {found}" if found != pos else f"{pos}, {change:.0%} changed"
    print(f"[debug] {need_name}: need bar unreadable, its icon is still there ({where})")
    state.need_icon_positions[need_name] = found
    watch.update(pos=found, crop=now)      # the next look searches for the icon as it is now
    return False

def _need_cleared(need_name: str) -> Optional[bool]:
    """True if `need_name`'s icon is missing on two quick checks in a row
    (NEED_GONE_FLICKER_RECHECK_DELAY apart, the second one a fresh circle
    search) - the single-frame-flicker guard shared by wait_until_need_gone()
    and _watch_need_gone() below.
    While the icons can't be read (see detect_need_bar_readable()) the answer
    comes from icon_gone_by_frame_change() instead - None if that can't tell
    either: neither cleared nor not cleared."""
    for attempt in range(2):
        names, readable = scan_needs(fresh=attempt > 0)   # the second look must not just repeat the first
        if not readable:
            return icon_gone_by_frame_change(need_name)
        reset_icon_watch()   # readable again: a later unreadable stretch starts afresh
        if need_name in names:
            return False
        if attempt == 0:
            wait_interruptible(cfg.NEED_GONE_FLICKER_RECHECK_DELAY)
    return True

def wait_until_need_gone(need_name: str, max_wait: float = cfg.NEED_GONE_MAX_WAIT, poll_interval: float = cfg.NEED_GONE_POLL_INTERVAL) -> None:
    """Wait for `need_name`'s icon to stop being detected, for at most
    `max_wait` seconds - whichever happens first. It is looked for every
    `poll_interval` seconds, and one check that finds it gone ends the wait:
    a single missed detection can't end it early because each check looks
    twice, a moment apart (see _need_cleared()).
    While the icons can't be read (a bright background, see
    detect_need_bar_readable()) nothing counts as a miss, so that wait runs its
    full length - and ending it isn't logged as a failure. (Popups and
    minigames that turn up meanwhile are handled by the unscrew() checks
    wait_interruptible() runs during a task.) Interruptible."""
    print(f"[debug] waiting up to {max_wait}s for {need_name} to clear...")
    reset_icon_watch()
    deadline = time.time() + max_wait
    unreadable = False
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            if unreadable:
                print(f"[debug] couldn't read the need icons (bright background) - waited {max_wait}s, moving on")
            else:
                print(f"[debug] {need_name} still showing after {max_wait}s, moving on")
                log_failure(f"{need_name} still showing after {max_wait}s")
            return
        wait_interruptible(min(poll_interval, remaining))
        cleared = _need_cleared(need_name)
        unreadable = cleared is None
        if cleared:
            print(f"[debug] {need_name} cleared")
            return

def _watch_need_gone(need_name: str, cleared_event: threading.Event, stop_event: threading.Event) -> None:
    """Background-thread body for walk_alternating()'s optional `need_name`:
    checks whether the need has cleared (_need_cleared()) every
    NEED_GONE_POLL_INTERVAL *while* the character is still moving - the same
    check wait_until_need_gone() makes standalone, just running alongside the
    movement instead of after it. Sets `cleared_event` the moment it finds it
    gone. Exits quietly - without raising - the instant `stop_event` is set
    (the walk ended on its own) or STOP_EVENT/focus loss is noticed: only the
    main thread's check_running() calls are allowed to unwind the workflow, so
    this thread must never let StopRequested/FocusLost escape uncaught."""
    try:
        reset_icon_watch()
        while not stop_event.is_set():
            if stop_event.wait(cfg.NEED_GONE_POLL_INTERVAL):
                return
            if _need_cleared(need_name) is True:
                cleared_event.set()
                return
    except (StopRequested, FocusLost):
        return

# ============================================================================
# CLICKING
# ============================================================================

def hover_move(x: float, y: float, duration: float = cfg.CLICK_MOVE_DURATION) -> None:
    """Move the mouse to (x, y) - a reference-space position, see to_screen() -
    over `duration` seconds using SendInput
    (pydirectinput), then nudge it a couple of pixels back onto the target.
    Roblox ignores pyautogui's SetCursorPos warps as hover movement and only
    reacts to real input events, so this is what makes a UI element register
    as hovered before it's clicked."""
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
    UNSCREW_TASK_INTERVAL seconds. With `play_minigames` False (setup) an
    offered minigame is only declined, never played - setup can't be
    abandoned halfway, since its clicks toggle things."""
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
    """Sleep for `duration`, in STOP_CHECK_INTERVAL chunks, checking
    check_running() between each chunk. Raises StopRequested or FocusLost
    the moment either condition is noticed, instead of returning a bool the
    caller has to check after every call."""
    elapsed = 0.0
    while elapsed < duration:
        check_running()
        task_unscrew_tick()
        sleep_chunk = min(cfg.STOP_CHECK_INTERVAL, duration - elapsed)
        STOP_EVENT.wait(sleep_chunk)   # returns early if [STOP] is pressed; check_running() then raises
        elapsed += sleep_chunk
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

def release_all_inputs() -> None:
    """Best-effort safety net: release every key this macro ever holds down,
    plus the mouse button. The try/finally blocks around each individual
    held key/button should already guarantee this, but this is called once
    more whenever a background task ends (run_async's worker finally block)
    as a last line of defense - a bug in a handler we haven't caught yet
    still shouldn't be able to leave an input stuck down in the game."""
    for key in (*cfg.MOVE_KEYS, cfg.KEY_HELICOPTER):
        try:
            pydirectinput.keyUp(key)
        except Exception:
            pass
    try:
        pydirectinput.mouseUp()
    except Exception:
        pass

# ============================================================================
# BUTTON DETECTION
# ============================================================================

def detect_buttons(save_debug: bool = False) -> list[Point]:
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
    print(f"[debug] button scan: {len(contours)} raw purple contour(s) in search band")

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

    print(f"[debug] button scan: {len(candidates)} passed filters "
          f"(rejected: {rejected_area} area, {rejected_radius} radius, {rejected_circularity} circularity)")

    # Prefer candidates with a white outline, then by size; final order is left-to-right
    candidates.sort(key=lambda t: (t[3] > 0, t[2]), reverse=True)
    candidates = sorted(candidates[:cfg.BUTTON_MAX_COUNT], key=lambda t: t[0])
    positions = [(cx, cy) for cx, cy, _, _ in candidates]

    if save_debug:
        debug_img = img.copy()
        for i, (cx, cy) in enumerate(positions):
            cv2.circle(debug_img, (cx, cy), cfg.DEBUG_BUTTON_MARKER_RADIUS,
                       cfg.DEBUG_BUTTON_MARKER_COLOR, cfg.DEBUG_BUTTON_MARKER_THICKNESS)
            label_pos = (cx + cfg.DEBUG_BUTTON_LABEL_OFFSET[0], cy + cfg.DEBUG_BUTTON_LABEL_OFFSET[1])
            cv2.putText(debug_img, str(i + 1), label_pos, cv2.FONT_HERSHEY_SIMPLEX,
                        cfg.DEBUG_BUTTON_LABEL_SCALE, cfg.DEBUG_BUTTON_MARKER_COLOR, cfg.DEBUG_BUTTON_MARKER_THICKNESS)
        cv2.imwrite(os.path.join(cfg.IMAGES_DIR, "debug_buttons.png"), debug_img)

    if not positions:
        print("[!] No buttons detected. Check debug_buttons.png in the debug/ folder to see "
              "whether PURPLE_RANGE / BUTTON_BAND_X / BUTTON_BAND_Y need adjusting.")

    return positions

def click_button(button_x: float, button_y: float, need_name: str) -> bool:
    """Focus the game, then click a mapped need button with a hover click."""
    if not focus_roblox():
        return False
    time.sleep(cfg.FOCUS_DELAY)
    print(f"[debug] clicking {need_name} at ({button_x}, {button_y})")
    hover_click(button_x, button_y)
    return True

def refresh_button_mapping() -> bool:
    """Detect the current action button positions and cache them in
    state.button_positions. Assumes the character is already at the buttons.
    Always clears any previous mapping first, even on failure, so a failed
    detection can never leave a stale (possibly wrong) position behind for
    click_need_button() to use."""
    state.button_positions.clear()

    positions = detect_buttons(save_debug=True)
    if not positions:
        print("[!] ERROR: No buttons detected!")
        respawn_character()
        return False

    print(f"\n[!] DETECTED {len(positions)} BUTTON(S)")
    for i, (cx, cy) in enumerate(positions):
        if i < len(cfg.BUTTON_NAMES):
            name = cfg.BUTTON_NAMES[i]
            state.button_positions[name] = (cx, cy)
            print(f"[!] Button {i + 1}: '{name}' @ ({cx}, {cy})")

    print("\n[!] BUTTON MAPPING REFRESHED\n")
    return True

def click_need_button(need_name: str) -> bool:
    """Click the action button mapped to `need_name` (see ButtonNeedHandler),
    then wait for its icon to actually clear (wait_until_need_gone()) rather
    than guessing how long that takes. Assumes refresh_button_mapping() has
    already been called this cycle. Returns True if a mapped button was
    actually found and clicked, False otherwise - the caller must not treat
    this need as resolved (or respawn) on a False return."""
    if need_name not in state.button_positions:
        print(f"[!] WARNING: no button mapped for '{need_name}'")
        return False
    button_x, button_y = state.button_positions[need_name]
    print(f"[!] CLICKING: {need_name}")
    if not click_button(button_x, button_y, need_name):
        return False
    wait_until_need_gone(need_name)
    return True

# ============================================================================
# MOVEMENT
# ============================================================================

def respawn_character() -> None:
    """Respawn the character (ESC, R, ENTER), make sure the respawn dialog
    isn't left open (dismiss_stray_windows()), and wait for it to settle."""
    print("[debug] respawning...")
    if not focus_roblox_click():
        return
    for key in cfg.RESPAWN_KEYS:
        pydirectinput.keyDown(key)
        time.sleep(cfg.RESPAWN_KEY_DURATION)
        pydirectinput.keyUp(key)
        time.sleep(cfg.RESPAWN_KEY_DURATION)
    # The keys go in within a fraction of a second; if the Enter came before the
    # "Are you sure you want to respawn your character?" dialog did, it's still
    # open and blocks everything - click its Respawn (see STRAY_WINDOWS).
    time.sleep(cfg.RESPAWN_CONFIRM_LOOK_DELAY)
    for _ in range(2):
        if dismiss_stray_windows() != "respawn_confirm":
            break
    wait_interruptible(cfg.RESPAWN_WAIT)
    print("[debug] respawn complete")

def hold_key(key: str, duration: float) -> None:
    """Hold `key` down for `duration` seconds (interruptibly). Always
    releases the key, even if StopRequested/FocusLost fires mid-hold -
    otherwise it would stay stuck held down in the game."""
    pydirectinput.keyDown(key)
    try:
        wait_interruptible(duration)
    finally:
        pydirectinput.keyUp(key)

def walk_to_buttons() -> None:
    """Walk forward from the respawn spot to where the action buttons are."""
    if not focus_roblox():
        return
    print("[debug] walking to buttons...")
    hold_key("w", cfg.WALK_TO_BUTTONS_DURATION)
    wait_interruptible(cfg.UI_SETTLE)
    print("[debug] arrived at buttons")

def walk_alternating(direction_pair: tuple[str, str], total_duration: float, step_duration: float = cfg.WALK_ALTERNATING_STEP, need_name: Optional[str] = None) -> None:
    """Alternate between the two given keys, holding each for `step_duration`,
    for a total of `total_duration` - or less, if `need_name` is given and
    confirmed gone first. Shared by the walk need (a/d, left-right) and the
    ride need (w/s, forward-back) - same pattern, different keys.

    When `need_name` is given, a background thread (_watch_need_gone())
    polls for it clearing *while* the character is still moving, instead
    of only checking once the movement finishes - the walk stops as soon
    as that thread confirms it, rather than always running the full
    total_duration."""
    print(f"[debug] walking alternating {direction_pair} pattern for {total_duration}s...")
    if not focus_roblox():
        return

    cleared_event = threading.Event()
    stop_event = threading.Event()
    watcher = None
    if need_name is not None:
        watcher = threading.Thread(target=_watch_need_gone, args=(need_name, cleared_event, stop_event), daemon=True)
        watcher.start()

    try:
        direction_idx = 0
        elapsed = 0.0
        start_time = time.time()

        while elapsed < total_duration and not cleared_event.is_set():
            direction = direction_pair[direction_idx % 2]
            print(f"[debug] step {direction_idx + 1}: {direction} for {step_duration}s...")
            hold_key(direction, step_duration)
            wait_interruptible(cfg.KEY_STEP_GAP)

            direction_idx += 1
            elapsed = time.time() - start_time
    finally:
        stop_event.set()
        if watcher is not None:
            watcher.join(timeout=cfg.NEED_WATCH_JOIN_TIMEOUT)

    if cleared_event.is_set():
        print(f"[debug] {need_name} cleared, stopping early")
    print("[debug] alternating walk complete")

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
    print(f"[debug] walking to the lure for {cfg.LURE_COLLECT_WALK_DURATION}s...")
    hold_key("a", cfg.LURE_COLLECT_WALK_DURATION)
    pydirectinput.press(cfg.KEY_INTERACT)
    wait_interruptible(cfg.LURE_COLLECT_SETTLE_DELAY)
    pydirectinput.press(cfg.KEY_INTERACT)
    wait_interruptible(cfg.UI_SETTLE)
    if not set_new_lure():
        return False
    respawn_character()
    print("[!] Lure collect complete!")
    return True

def set_new_lure() -> bool:
    """Click the two backpack positions that place a fresh lure, once the
    current one's rewards have been collected. Split out from
    lure_collect() so the placement sequence alone can be tested/tuned
    without walking to the old lure first."""
    if not focus_roblox():
        return False
    hover_click(*cfg.LURE_NEW_POS_1)
    wait_interruptible(cfg.UI_SETTLE)
    hover_click(*cfg.LURE_NEW_POS_2)
    wait_interruptible(cfg.UI_SETTLE)
    print("[!] New lure set!")
    return True

def tree_collect() -> bool:
    """Walk to the money tree, step back to line up with it, then harvest it."""
    if not focus_roblox():
        return False
    print(f"[debug] walking to the money tree for {cfg.TREE_COLLECT_WALK_DURATION}s...")
    hold_key("d", cfg.TREE_COLLECT_WALK_DURATION)
    print(f"[debug] stepping back for {cfg.TREE_COLLECT_BACKWARD_DURATION}s...")
    hold_key("s", cfg.TREE_COLLECT_BACKWARD_DURATION)
    pydirectinput.press(cfg.KEY_INTERACT)
    respawn_character()
    print("[!] Tree collect complete!")
    return True

def setup_game() -> bool:
    """Setup: lock the house, set the backpack's item filter to favorites
    only, then disable trades (disable_trades()). Not part of the cycle -
    only run on demand (the GUI's Setup button), and after a rejoin. Stray
    windows are closed first, since an open one would take its clicks, and
    the in-task checks run throughout (minigames only declined)."""
    with task_unscrew(play_minigames=False):
        return _setup_steps()

def _setup_steps() -> bool:
    for _ in range(cfg.SETUP_STRAY_TRIES):
        if not dismiss_stray_windows():
            break
    respawn_character()
    print("[debug] locking house...")
    hover_click(*cfg.SETUP_LOCK_HOUSE_POS)

    print("[debug] opening backpack...")
    pydirectinput.press(cfg.KEY_BACKPACK)
    wait_interruptible(cfg.UI_SETTLE)
    print("[debug] opening settings...")
    hover_click(*cfg.SETUP_BACKPACK_SETTINGS_POS)
    wait_interruptible(cfg.UI_SETTLE)
    print("[debug] opening sort menu...")
    hover_click(*cfg.SETUP_SORT_MENU_POS)
    wait_interruptible(cfg.UI_SETTLE)
    print("[debug] selecting favorites...")
    hover_click(*cfg.SETUP_FAVORITES_POS)
    wait_interruptible(cfg.UI_SETTLE)
    print("[debug] confirming...")
    hover_click(*cfg.SETUP_CONFIRM_POS)
    wait_interruptible(cfg.UI_SETTLE)
    print("[debug] closing backpack...")
    close_backpack("setup")

    disable_trades()

    print("[!] Setup complete!")
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
    print("[debug] sending the macro window to the back...")
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
            print(f"[debug] {label}...")
            hover_click(*pos)
            wait_interruptible(cfg.UI_SETTLE)
    finally:
        print("[debug] bringing the macro window back...")
        bring_macro_window_to_front()
    print("[!] Trades disabled!")
    return True

def parse_server_link(link: str) -> Optional[tuple[str, str]]:
    """Turn a private server link into (type, code) for run_deeplink(), or
    None if it isn't one. Understands the share form
    (https://www.roblox.com/share?code=...&type=Server) and the older
    ...?privateServerLinkCode=... form."""
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
    """Leave the game properly (esc, l, enter - only if `clean_leave`, and
    only if the window is tall enough for L to work), then kill any Roblox
    process still around and wait REJOIN_CLOSE_WAIT. Nothing here needs
    Roblox to stay focused. `clean_leave` is off when the game is already
    gone or showing the Disconnected dialog, where those keys do nothing
    useful and Enter could press Reconnect."""
    window = roblox_window()
    if clean_leave and window is not None and window.height >= cfg.REJOIN_MIN_LEAVE_HEIGHT and focus_roblox():
        print("[debug] leaving the game...")
        for key in cfg.REJOIN_LEAVE_KEYS:
            pydirectinput.press(key)
            wait_stoppable(cfg.REJOIN_LEAVE_KEY_GAP)
    print("[debug] closing Roblox...")
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

def leave_and_rejoin(clean_leave: bool = True) -> bool:
    """Close Roblox, relaunch the game through a deeplink (to the private
    server saved in the GUI, or a public one if none is saved), wait for it
    to load, click Play, wait REJOIN_AFTER_JOIN_WAIT and respawn - see the
    REJOIN_* settings. Retries the whole thing up to REJOIN_MAX_ATTEMPTS
    times. Run from the GUI's Leave & rejoin button and by rejoin_game().
    Returns True once back in the game, False if every attempt failed (or
    the saved link isn't a valid private server link). `clean_leave` is
    passed on to close_roblox()."""
    link = load_game_config()["private_server_link"].strip()
    server = None
    if link:
        server = parse_server_link(link)
        if server is None:
            print("[!] Rejoin failed: the saved private server link isn't recognised")
            return False
    else:
        print("[!] No private server link saved - rejoining a public server")

    for attempt in range(1, cfg.REJOIN_MAX_ATTEMPTS + 1):
        print(f"[debug] rejoin attempt {attempt}/{cfg.REJOIN_MAX_ATTEMPTS}")
        close_roblox(clean_leave)
        run_deeplink(server)

        print("[debug] waiting for the Roblox window...")
        deadline = time.time() + cfg.REJOIN_WINDOW_TIMEOUT
        while not roblox_windows() and time.time() < deadline:
            wait_stoppable(cfg.REJOIN_POLL_INTERVAL)
        if not roblox_windows():
            print("[!] No Roblox window appeared")
            continue

        print("[debug] waiting for the game to load...")
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
            print("[!] The game didn't finish loading")
            continue

        print("[debug] game loaded, clicking play...")
        wait_stoppable(cfg.REJOIN_PLAY_SETTLE)
        focus_roblox()
        hover_click(*cfg.REJOIN_JOIN_POS)
        print(f"[debug] waiting {cfg.REJOIN_AFTER_JOIN_WAIT}s, then respawning...")
        wait_stoppable(cfg.REJOIN_AFTER_JOIN_WAIT)
        respawn_character()
        state.last_rejoin = state.last_progress = state.last_detection = time.time()
        print("[!] Rejoin complete!")
        return True

    print(f"[!] Rejoin failed after {cfg.REJOIN_MAX_ATTEMPTS} attempts")
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
    """The macro's own window as a (left, top, right, bottom) box in reference
    space (plus FOCUS_PET_IGNORE_MARGIN), or None when there's no GUI or it
    can't be measured. Its console scrolls and flickers, which looks like
    movement to focus_pet()."""
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
    """Compare two same-size BGR screenshots over the bottom (1 - top_percent)
    of the screen - leaving out every (left, top, right, bottom) box in
    `ignore`, e.g. the macro's own window - and return ([(x, y, w, h, area) per blob of pixels that
    changed between them, largest first, in screen coordinates], the change
    mask of the region, its top row). Pure image logic, no input or waiting,
    so it can be tested on its own."""
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

def find_moving_blobs(img_a: Image, img_b: Image, top_percent: float = cfg.FOCUS_PET_REGION_TOP_PERCENT, ignore: Sequence[Box] = ()) -> list[Point]:
    """The (x, y) center of every blob moving_blobs() finds, largest first."""
    blobs, _, _ = moving_blobs(img_a, img_b, top_percent, ignore)
    return [(x + w // 2, y + h // 2) for x, y, w, h, _ in blobs]

def save_focus_pet_debug(img: Image, blobs: list[tuple[int, int, int, int, int]], mask: Image, top: int, name: str = "debug_focus_pet.png", ignore: Sequence[Box] = ()) -> None:
    """Write debug/<name>: `img` (the second frame) with the searched region
    marked, the changed pixels in red, and a numbered box around every blob -
    so what focus_pet() thinks moved, and so where it clicks, can be seen."""
    marked = img.copy()
    region = marked[top:]
    region[mask > 0] = (0, 0, 255)
    cv2.line(marked, (0, top), (marked.shape[1], top), cfg.FOCUS_PET_DEBUG_COLOR, 1)
    for left, box_top, right, bottom in ignore:   # ignored areas, in blue
        cv2.rectangle(marked, (left, box_top), (right, bottom), (255, 0, 0), 2)
    for i, (x, y, w, h, area) in enumerate(blobs, 1):
        cv2.rectangle(marked, (x, y), (x + w, y + h), cfg.FOCUS_PET_DEBUG_COLOR, 2)
        cv2.putText(marked, f"{i}: {area}", (x, max(12, y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, cfg.FOCUS_PET_DEBUG_COLOR, 2)
    cv2.imwrite(os.path.join(cfg.IMAGES_DIR, name), marked)

def detect_pet_focus(img: Image) -> bool:
    """True if the pet focus view is showing in `img` - the "pet_focus" crop
    of STRAY_WINDOWS (its BACK button)."""
    crop = load_templates(cfg.STRAY_WINDOW_DIR).get("pet_focus")
    return crop is not None and template_score(img, crop, cfg.STRAY_WINDOWS["pet_focus"]["box"]) >= cfg.STRAY_WINDOW_MATCH_THRESHOLD

def focus_pet(click_duration: float = cfg.CLICK_MOVE_DURATION) -> bool:
    """Click the pet to open its interaction menu. The pet is found by
    movement: two screenshots of the bottom of the screen FOCUS_PET_FRAME_GAP
    apart, then a click on the center of everything that moved (see
    moving_blobs(), which leaves out the macro's own window). What it saw is printed and written to
    debug/debug_focus_pet.png (the blobs, marked) - and what the screen looked
    like after the clicks to debug/debug_focus_pet_after.png. Returns True if
    anything was clicked, False if nothing moved. It looks at whether the pet
    is focused after each click and stops clicking the moment it is. If the pet is already
    focused (the pet focus view's BACK button is on screen) there's nothing
    to click: it just returns True."""
    if detect_pet_focus(grab_screen()):
        print("[debug] focus_pet: the pet is already focused")
        return True
    img_a = grab_screen()
    wait_interruptible(cfg.FOCUS_PET_FRAME_GAP)
    img_b = grab_screen()
    ignore = [box for box in [macro_window_reference_rect()] if box is not None]
    blobs, mask, top = moving_blobs(img_a, img_b, ignore=ignore)
    changed = int(np.count_nonzero(mask))
    print(f"[debug] focus_pet: {len(blobs)} moving blob(s), {changed} changed px below y={top}")
    for i, (x, y, w, h, area) in enumerate(blobs, 1):
        print(f"[debug] focus_pet:   #{i} center ({x + w // 2}, {y + h // 2}), {w}x{h}, area {area}")
    save_focus_pet_debug(img_b, blobs, mask, top, ignore=ignore)
    if not blobs:
        print("[debug] focus_pet: nothing moved, pet not found (see debug/debug_focus_pet.png)")
        return False
    for i, (x, y, w, h, _) in enumerate(blobs, 1):
        print(f"[debug] focus_pet: clicking #{i} at ({x + w // 2}, {y + h // 2})...")
        hover_click(x + w // 2, y + h // 2, duration=click_duration)
        wait_interruptible(cfg.FOCUS_PET_CLICK_SETTLE)
        if detect_pet_focus(grab_screen()):
            print(f"[debug] focus_pet: focused after click #{i}, no more clicks needed")
            break
    else:
        wait_interruptible(cfg.FOCUS_PET_MENU_WAIT)
    after = grab_screen()
    save_focus_pet_debug(after, blobs, np.zeros_like(mask), top, name="debug_focus_pet_after.png", ignore=ignore)
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
        print("[debug] tab already selected, not clicking it")
        return
    hover_click(*pos)

def equip_favorite_vehicle() -> None:
    """Open the backpack, select the first vehicle (the favorite, once
    setup_game() has filtered the backpack to favorites) and equip it, then
    close the backpack. Shared by ride and the helicopter step of
    teleport-walk needs."""
    print("[debug] opening backpack...")
    pydirectinput.press(cfg.KEY_BACKPACK)
    wait_interruptible(cfg.UI_SETTLE)

    print("[debug] opening vehicles...")
    select_backpack_tab(cfg.RIDE_VEHICLES_POS)
    wait_interruptible(cfg.UI_SETTLE)

    print("[debug] selecting first vehicle...")
    hover_click(*cfg.RIDE_FIRST_VEHICLE_POS)
    wait_interruptible(cfg.UI_SETTLE)

    print("[debug] equipping vehicle...")
    hover_click(*cfg.RIDE_EQUIP_POS)
    wait_interruptible(cfg.UI_SETTLE)

    print("[debug] closing backpack...")
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
        print("[!] WALK NEED")
        walk_alternating(("a", "d"), cfg.NEED_GONE_MAX_WAIT, need_name="walk")
        return True

def click_until_need_gone(pos: Point, interval: float, max_wait: float, need_name: str) -> None:
    """Click `pos` every `interval` seconds for at most `max_wait` - or less,
    once `need_name` is confirmed gone (the watcher thread walk_alternating()
    uses)."""
    print(f"[debug] clicking {pos} every {interval}s for up to {max_wait}s until {need_name} clears...")
    cleared_event = threading.Event()
    stop_event = threading.Event()
    watcher = threading.Thread(target=_watch_need_gone, args=(need_name, cleared_event, stop_event), daemon=True)
    watcher.start()
    try:
        deadline = time.time() + max_wait
        while time.time() < deadline and not cleared_event.is_set():
            hover_click(*pos)
            wait_interruptible(interval)
    finally:
        stop_event.set()
        watcher.join(timeout=cfg.NEED_WATCH_JOIN_TIMEOUT)

class CatchNeedHandler(NeedHandler):
    """The 'catch' need requires opening backpack, equipping a toy, then throwing it
    (clicking) until the need clears, then unequipping it."""

    def handle(self) -> bool:
        print("[!] CATCH NEED")
        if not focus_roblox():
            return False

        # Open backpack with 'b' key
        print("[debug] opening backpack...")
        pydirectinput.press(cfg.KEY_BACKPACK)
        wait_interruptible(cfg.UI_SETTLE)

        # Navigate to toys
        print("[debug] opening toys...")
        select_backpack_tab(cfg.CATCH_TOYS_POS)
        wait_interruptible(cfg.UI_SETTLE)

        # Click squeaky toy
        print("[debug] selecting squeaky toy...")
        hover_click(*cfg.CATCH_SQUEAKY_TOY_POS)
        wait_interruptible(cfg.UI_SETTLE)

        # Equip the toy
        print("[debug] equipping toy...")
        hover_click(*cfg.CATCH_EQUIP_POS)
        wait_interruptible(cfg.UI_SETTLE)

        # Close backpack with 'b' key
        print("[debug] closing backpack...")
        close_backpack("equipping the toy")

        # Wait before throwing
        print(f"[debug] waiting {cfg.CATCH_WAIT_AFTER_EQUIP}s before throwing...")
        wait_interruptible(cfg.CATCH_WAIT_AFTER_EQUIP)

        # Zoom in, to avoid focusing the pet on toy throw
        hold_key(cfg.KEY_ZOOM_IN, cfg.CATCH_ZOOM_DURATION)
        
        # Click empty space to throw, every CATCH_THROW_INTERVAL, until the need clears
        click_until_need_gone(cfg.EMPTY_POS, cfg.CATCH_THROW_INTERVAL, cfg.NEED_GONE_MAX_WAIT, "catch")

        # Unequip the toy
        print("[debug] unequipping toy...")
        hover_click(*cfg.CATCH_UNEQUIP_POS)

        print("[!] Catch complete!")
        return True

class PetNeedHandler(NeedHandler):
    """The 'pet' need: click to focus the pet, then hold the mouse button
    down and swipe it once down from just above the center of the screen to
    just below it, over PET_SWIPE_DURATION."""

    def handle(self) -> bool:
        print("[!] PET NEED")
        if not focus_roblox():
            return False

        print("[debug] focusing pet...")
        with wanting_pet_focus():
            return self._pet()

    def _pet(self) -> bool:
        if not focus_pet(cfg.PET_FOCUS_CLICK_DURATION):
            return False
        wait_interruptible(cfg.UI_SETTLE)
        print(f"[debug] petting: one swipe down over {cfg.PET_SWIPE_DURATION}s...")
        # Click the center to focus
        hover_click(cfg.REFERENCE_CENTER_X, cfg.REFERENCE_CENTER_Y, duration=cfg.PET_FOCUS_CLICK_DURATION)
        time.sleep(cfg.PET_SETTLE_DELAY)
        start_y = cfg.REFERENCE_CENTER_Y + cfg.PET_SWIPE_START_OFFSET
        end_y = cfg.REFERENCE_CENTER_Y + cfg.PET_SWIPE_END_OFFSET
        pydirectinput.moveTo(*to_screen(cfg.REFERENCE_CENTER_X, start_y))
        time.sleep(cfg.PET_SETTLE_DELAY)
        # Hold down and move with incremental steps (much more reliable for games)
        pydirectinput.mouseDown()
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
        print("[!] Pet complete!")
        return True

class ChooseNeedHandler(NeedHandler):
    """The 'choose' need: focus_pet() to open the pet's interaction menu,
    find the button with a distinctive exact color (it has no distinguishing
    icon, so shape detection doesn't apply here), move to it slowly rather
    than jumping straight there, click it, then click the middle of the
    screen to dismiss the menu. It doesn't wait for the icon to clear - that
    happens at once, and process_needs() checks again after every need."""

    def handle(self) -> bool:
        print("[!] CHOOSE NEED")
        if not focus_roblox():
            return False

        print("[debug] focusing pet...")
        with wanting_pet_focus():
            return self._choose()

    def _choose(self) -> bool:
        if not focus_pet():
            return False
        wait_interruptible(cfg.UI_SETTLE)

        print(f"[debug] searching screen for color {cfg.CHOOSE_BUTTON_COLOR}...")
        img = grab_screen()
        match = find_exact_color(img, cfg.CHOOSE_BUTTON_COLOR)
        if match is None:
            print(f"[!] WARNING: no pixel matching {cfg.CHOOSE_BUTTON_COLOR} found on screen")
            return False
        print(f"[debug] found at {match}, moving there slowly...")
        hover_click(*match, duration=cfg.CHOOSE_SLOW_MOVE_DURATION)
        wait_interruptible(cfg.UI_SETTLE)

        print("[debug] clicking middle of screen...")
        hover_click(cfg.REFERENCE_CENTER_X, cfg.REFERENCE_CENTER_Y, duration=cfg.CHOOSE_SLOW_MOVE_DURATION)

        print("[!] Choose complete!")
        return True

class RideNeedHandler(NeedHandler):
    """The 'ride' need: step back, mount a vehicle from the backpack, then
    walk back and forth for a while astride it - or less, the moment its
    icon is confirmed gone (see walk_alternating())."""

    def handle(self) -> bool:
        print("[!] RIDE NEED")
        if not focus_roblox():
            return False

        # Step back before mounting
        print(f"[debug] stepping back for {cfg.RIDE_BACKWARD_DURATION}s...")
        hold_key("s", cfg.RIDE_BACKWARD_DURATION)

        print("[debug] pressing e...")
        pydirectinput.press(cfg.KEY_MOUNT)
        wait_interruptible(cfg.RIDE_WAIT_AFTER_E)

        # Walk forward briefly after mounting, before opening the backpack
        print(f"[debug] walking forward for {cfg.RIDE_FORWARD_DURATION}s...")
        hold_key("w", cfg.RIDE_FORWARD_DURATION)

        equip_favorite_vehicle()

        # Hold r briefly before starting to move
        print(f"[debug] holding {cfg.KEY_HELICOPTER} for {cfg.RIDE_R_HOLD_DURATION}s...")
        hold_key(cfg.KEY_HELICOPTER, cfg.RIDE_R_HOLD_DURATION)

        # Walk back and forth (forward/backward, not left/right) while riding
        walk_alternating(("w", "s"), cfg.NEED_GONE_MAX_WAIT, need_name="ride")

        print("[!] Ride complete!")
        return True

def teleport_to(category_pos: Point) -> None:
    """Open the backpack and teleport via the given category tab
    (TELEPORT_PETS_TAB_POS for the nursery, TELEPORT_VEHICLES_TAB_POS for
    the dealership), then walk backward briefly to clear the landing spot.
    Shared by every need that teleports somewhere (TeleportWalkNeedHandler)."""
    print("[debug] opening backpack...")
    pydirectinput.press(cfg.KEY_BACKPACK)
    wait_interruptible(cfg.UI_SETTLE)

    print("[debug] selecting category...")
    hover_click(*category_pos)
    wait_interruptible(cfg.UI_SETTLE)
    hover_click(*cfg.GENERAL_TELEPORT_POS_2)
    wait_interruptible(cfg.UI_SETTLE)
    hover_click(*cfg.GENERAL_TELEPORT_POS_3)
    wait_interruptible(cfg.TELEPORT_WAIT)
    print("[debug] walking backward...")
    hold_key("s", cfg.TELEPORT_BACK_DURATION)
    wait_interruptible(cfg.TELEPORT_SETTLE_WAIT)

class TeleportWalkNeedHandler(NeedHandler):
    """A need that teleports somewhere, holds a sequence of movement keys,
    then waits for the need to clear (see wait_until_need_gone()).
    process_needs() respawns afterwards, since these leave the character
    somewhere else on the map. What each one does is configured in
    TELEPORT_WALK_NEEDS. An entry with `helicopter` set (default
    HELICOPTER_REQUIRED) flies instead of walking: after teleporting it
    steps forward, equips the favorite vehicle and holds r, and once the
    steps have brought it to the destination it presses space."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.config = cfg.TELEPORT_WALK_NEEDS[name]

    def handle(self) -> bool:
        print(f"[!] {self.name.upper()} NEED")
        if not focus_roblox():
            return False

        teleport_to(self.config["teleport_pos"])

        helicopter = self.config.get("helicopter", cfg.HELICOPTER_REQUIRED)
        if helicopter:
            print(f"[debug] stepping forward for {cfg.HELICOPTER_FORWARD_DURATION}s...")
            hold_key("w", cfg.HELICOPTER_FORWARD_DURATION)
            equip_favorite_vehicle()
            print(f"[debug] holding {cfg.KEY_HELICOPTER} for {cfg.HELICOPTER_HOLD_DURATION}s...")
            hold_key(cfg.KEY_HELICOPTER, cfg.HELICOPTER_HOLD_DURATION)

        for i, (key, duration) in enumerate(self.config["steps"]):
            if i:
                wait_interruptible(cfg.TELEPORT_WALK_STEP_GAP)
            print(f"[debug] holding {key} for {duration}s...")
            hold_key(key, duration)

        if helicopter:
            print("[debug] arrived, pressing space...")
            pydirectinput.press(cfg.KEY_JUMP)

        final_click = self.config.get("final_click")
        if final_click:
            wait_interruptible(cfg.SICK_PRE_E_WAIT)
            pydirectinput.press("e")
            wait_interruptible(cfg.SICK_CONFIRM_WAIT)
            print(f"[debug] clicking {final_click}...")
            hover_click(*final_click)

        wait_until_need_gone(self.name, max_wait=self.config.get("max_wait", cfg.NEED_GONE_MAX_WAIT))

        print(f"[!] {self.name.capitalize()} complete!")
        return True

# Handler factory (class, or partial of one) for each need.
# get_need_handler() uses this plus ENABLED_NEEDS (magic_numbers.py) to
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

# Every handler the GUI's Debug tab can run on its own.
DEBUG_HANDLERS = NEED_HANDLER_CLASSES

def get_need_handler(need_name: str) -> Optional[NeedHandler]:
    """Return the handler for a detected need, or None if it's not
    currently enabled (see ENABLED_NEEDS), was disabled for this run (see
    record_detection()), or the macro doesn't know it."""
    if need_name not in cfg.ENABLED_NEEDS or need_name in state.disabled_needs:
        return None
    handler_cls = NEED_HANDLER_CLASSES.get(need_name)
    return handler_cls() if handler_cls else None

def record_detection(detected: list[str]) -> None:
    """Track stuck needs: one that was attempted on the last pass and is
    still on screen now has failed to clear, and an enabled need that does
    that NEED_STUCK_CHECKS times in a row is disabled for the rest of the
    run - retrying it forever just wastes time. A check where it's gone,
    even once, breaks the streak. (A need merely waiting its turn behind
    others doesn't count - only attempts do.) Call this for every check,
    including ones that detect nothing."""
    for need_name in state.attempted_last:
        if need_name in detected:
            state.attempt_streaks[need_name] = state.attempt_streaks.get(need_name, 0) + 1
        else:
            state.attempt_streaks.pop(need_name, None)
    state.attempted_last = set()
    stuck = {n for n, count in state.attempt_streaks.items() if count >= cfg.NEED_STUCK_CHECKS} & (cfg.ENABLED_NEEDS - state.disabled_needs)
    for need_name in sorted(stuck):
        state.disabled_needs.add(need_name)
        print(f"[!] {need_name} still there after {cfg.NEED_STUCK_CHECKS} attempts in a row - disabling it for this run")
        log_failure(f"{need_name} stuck for {cfg.NEED_STUCK_CHECKS} attempts - disabled for this run "
                    f"({len(state.disabled_needs)} disabled now: {', '.join(sorted(state.disabled_needs))})")

def process_needs() -> bool:
    """Detect needs on screen and resolve the first one that can be: its
    handler runs via get_need_handler(), and if it *actually* resolved the
    need the character respawns and this returns True - the caller then
    starts a fresh check, so the next need is always picked from what's on
    screen now, never from a list taken before the last one was handled. A
    need that isn't resolved (disabled, unmapped, or its handler returned
    False) is skipped for the next one, with no respawn. Returns False if
    nothing was resolved - including when nothing was on screen, which
    prints nothing (the caller shows a waiting line instead)."""
    if not focus_roblox_click():
        return False

    found_icons, full_img = detect_need_icons(save_debug=True)
    if not detect_need_bar_readable(full_img):
        state.last_detection = time.time()   # can't tell whether there are needs - not a reason to respawn
    if not found_icons:
        record_detection([])
        note_idle_check()
        return False

    matched_needs = []
    for idx, icon_img, need_name, score in identify_icons(found_icons, full_img):
        if not need_name:
            if cfg.SAVE_NEW_NEEDS:
                print(f"\n[!] NEW NEED (best score: {score:.4f})")
                # prompt_rename_need() already saves the reference icon into
                # NEEDS_DIR - that .png file is the only record needed for this
                # need to be recognized next time, so there's nothing further to
                # persist here.
                prompt_rename_need(icon_img, idx)
            continue
        matched_needs.append((need_name, score))

    names = [name for name, _ in matched_needs]
    if names:
        flush_idle_checks()
        print(f"\n[!] Found {len(found_icons)} icon(s): " + ", ".join(f"{n} ({s:.4f})" for n, s in matched_needs))
        log_run_event(f"detected: {', '.join(names)}")
        state.none_matched_logged = False
    elif not state.none_matched_logged:
        flush_idle_checks()
        log_run_event(f"detected: none matched ({len(found_icons)} icon(s) on screen)")
        state.none_matched_logged = True
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
                print(f"[debug] {need_name} is disabled, skipping")
                continue
            attempted.add(need_name)
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
                print(f"[debug] {need_name} interrupted by {interruption.reason}")
                log_run_event(f"{need_name} interrupted by {interruption.reason}")
                return True
            if not handled:
                print(f"[debug] could not resolve '{need_name}' this pass, skipping")
                run_stats.failed[need_name] = run_stats.failed.get(need_name, 0) + 1
                log_failure(f"{need_name} handler could not resolve it")
                continue

            respawn_character()
            seconds = time.time() - started
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
    """Detect the paycheck popup by the crop of its bank-card header
    (ref/popups/paycheck.png, matched within PAYCHECK_BOX) and, if present,
    dismiss it. Returns True if the popup was detected and dismissed, False
    otherwise. Not by the green of its CASH OUT button, which the backpack's
    tiles share."""
    crop = load_templates(cfg.STRAY_WINDOW_DIR).get("paycheck")
    if crop is None or template_score(grab_screen(), crop, cfg.PAYCHECK_BOX) < cfg.PAYCHECK_MATCH_THRESHOLD:
        return False

    print("[debug] paycheck popup detected, dismissing...")
    log_run_event("paycheck popup dismissed")
    hover_click(*cfg.PAYCHECK_DISMISS_POS_1)
    hover_click(*cfg.PAYCHECK_DISMISS_POS_2)
    return True

def detect_backpack_expanded(img: Image) -> bool:
    """True if the EXPANDED backpack is open in `img`: most of
    BACKPACK_HEADER_BOX is the purple of its header bar."""
    left, top, right, bottom = cfg.BACKPACK_HEADER_BOX
    mask = exact_color_mask(img[top:bottom, left:right], cfg.BACKPACK_HEADER_COLOR, cfg.BACKPACK_HEADER_TOLERANCE)
    return bool(np.count_nonzero(mask) / mask.size >= cfg.BACKPACK_HEADER_MIN_FRACTION)

def detect_backpack_normal(img: Image) -> bool:
    """True if the NORMAL backpack is open in `img`: its purple frame within
    BACKPACK_NORMAL_PANEL_BOX and the white of its item grid
    (BACKPACK_NORMAL_WHITE_BOX), both - the white alone would also be any
    bright scenery. Or its "BACKPACK" title crop."""
    title = load_templates(cfg.STRAY_WINDOW_DIR).get("backpack_normal")
    if title is not None and template_score(img, title, cfg.BACKPACK_NORMAL_TITLE_BOX) >= cfg.BACKPACK_NORMAL_TITLE_THRESHOLD:
        return True
    left, top, right, bottom = cfg.BACKPACK_NORMAL_PANEL_BOX
    frame = exact_color_mask(img[top:bottom, left:right], cfg.BACKPACK_NORMAL_COLOR, cfg.BACKPACK_NORMAL_TOLERANCE)
    if np.count_nonzero(frame) < cfg.BACKPACK_NORMAL_MIN_PIXELS:
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
    """Close the backpack if it's open, in either form: press KEY_BACKPACK,
    look again, and press once more if it's still open (the expanded one takes
    two presses - the first shrinks it to the normal one, the second closes
    that). If it's still open after the second press the key isn't doing what's
    expected, so that's logged as a failure and it stops instead of pressing
    again (another press could just reopen it). Does nothing unless Roblox has
    the focus, so the key can't go to another window. `step` names what just
    ran (see close_backpack()), for the log. Returns True if it was open."""
    if not is_roblox_focused() or not detect_backpack_open(grab_screen()):
        return False
    img = grab_screen()
    print(f"[debug] backpack left open, closing it... (expanded: {detect_backpack_expanded(img)}, "
          f"normal: {detect_backpack_normal(img)})")
    cv2.imwrite(os.path.join(cfg.IMAGES_DIR, "debug_backpack_open.png"), img)
    if detect_backpack_expanded(img):
        # Its BACK button closes it entirely; the key would only shrink it first.
        crop = load_templates(cfg.STRAY_WINDOW_DIR).get("backpack_back")
        score, center = find_template(img, crop, cfg.BACKPACK_BACK_BOX) if crop is not None else (0.0, None)
        if center is not None and score >= cfg.BACKPACK_BACK_MATCH_THRESHOLD:
            hover_click(*center)
            wait_interruptible(cfg.UI_SETTLE)
            if not detect_backpack_open(grab_screen()):
                log_run_event("backpack was left open - closed it (BACK)" + (f" after {step}" if step else ""))
                return True
    for _ in range(2):
        pydirectinput.press(cfg.KEY_BACKPACK)
        wait_interruptible(cfg.UI_SETTLE)
        if not detect_backpack_open(grab_screen()):
            log_run_event("backpack was left open - closed it" + (f" after {step}" if step else ""))
            return True
    log_failure("backpack still open after pressing the backpack key twice")
    return True

def close_backpack(step: str) -> None:
    """End of a click sequence (`step` names it, for the log): press the
    backpack key, then check it really closed - a misplaced click earlier can
    have expanded the backpack, when the key only shrinks it - and close it
    properly if not (close_backpack_if_open())."""
    pydirectinput.press(cfg.KEY_BACKPACK)
    wait_interruptible(cfg.UI_SETTLE)
    close_backpack_if_open(step)

def close_backpack_if_stuck(in_task: bool) -> bool:
    """The backpack part of unscrew(). At the top of a cycle no handler has it
    open, so it's closed at once (close_backpack_if_open()). During a task a
    handler has the normal one open for a few seconds on purpose, so it's only
    closed once it has been seen open on BACKPACK_CLOSE_CONFIRMATIONS checks
    in a row - at UNSCREW_TASK_INTERVAL apart that's a backpack open for
    longer than that, which a handler never needs. Returns True if it's still
    open (a first sighting, mid-task)."""
    if not in_task:
        close_backpack_if_open()
        return False
    if not is_roblox_focused() or not detect_backpack_open(grab_screen()):
        state.backpack_strikes = 0
        return False
    state.backpack_strikes += 1
    if state.backpack_strikes < cfg.BACKPACK_CLOSE_CONFIRMATIONS:
        return True
    state.backpack_strikes = 0
    close_backpack_if_open()
    return False

def detect_disconnect(img: Image) -> bool:
    """True if the Roblox "Disconnected" dialog is on screen in `img`: most
    of DISCONNECT_PANEL_BOX is the dialog's grey (the game behind it is
    blurred, so nothing else on screen fills a box like that)."""
    left, top, right, bottom = cfg.DISCONNECT_PANEL_BOX
    mask = exact_color_mask(img[top:bottom, left:right], cfg.DISCONNECT_PANEL_COLOR, cfg.DISCONNECT_PANEL_TOLERANCE)
    return bool(np.count_nonzero(mask) / mask.size >= cfg.DISCONNECT_PANEL_MIN_FRACTION)

def rejoin_reason() -> Optional[tuple[str, str]]:
    """Why the game needs rejoining right now, as (kind, reason) - or None if
    it doesn't. In order of priority:
      "disconnect" - Roblox crashed (its crash window is open), isn't
          running, or is showing the Disconnected dialog;
      "stuck" - DISABLED_NEEDS_REJOIN_THRESHOLD needs have been disabled
          for this run;
      "stalled" - no need has been resolved for NO_PROGRESS_REJOIN_INTERVAL
          (see State.last_progress): the game is up but nothing is working;
      "scheduled" - it has been REJOIN_INTERVAL since the last rejoin (see
          State.last_rejoin)."""
    windows = gw.getAllWindows()   # listed once (a Windows call that reads every window's title) for both questions
    if any(w.title == cfg.ROBLOX_CRASH_WINDOW_TITLE for w in windows):
        return "disconnect", "Roblox crashed"
    if not any("roblox" in w.title.lower() for w in windows):
        return "disconnect", "Roblox isn't running"
    if detect_disconnect(grab_screen()):
        return "disconnect", "disconnected from the game"
    if len(state.disabled_needs) >= cfg.DISABLED_NEEDS_REJOIN_THRESHOLD:
        return "stuck", f"{len(state.disabled_needs)} needs disabled ({', '.join(sorted(state.disabled_needs))})"
    if state.last_progress is not None and time.time() - state.last_progress >= cfg.NO_PROGRESS_REJOIN_INTERVAL:
        return "stalled", f"no need resolved for {cfg.NO_PROGRESS_REJOIN_INTERVAL / 60:g} minutes"
    if state.last_rejoin is not None and time.time() - state.last_rejoin >= cfg.REJOIN_INTERVAL:
        return "scheduled", f"{cfg.REJOIN_INTERVAL / 3600:g} hour(s) since the last rejoin"
    return None

def rejoin_game(kinds: Optional[Sequence[str]] = None) -> bool:
    """Recover when the game needs it (see rejoin_reason()), or - with `kinds`
    (e.g. ("disconnect",), for the mid-task check) - only when it needs it for
    one of those reasons: rejoin it
    (leave_and_rejoin()), run setup_game() - the rejoin resets the game
    settings it sets - and clear the disabled needs and their detection
    history so every need gets a fresh chance. Returns True if it recovered,
    False if nothing needed doing or the rejoin failed (the cause is still
    there, so the next cycle tries again). Everything but the scheduled
    rejoin is a failure: it's logged with a screenshot, which shows the
    Disconnected dialog and its error code if that's why. The clean esc/l/
    enter leave is skipped when the game is already gone."""
    found = rejoin_reason()
    if found is None or (kinds is not None and found[0] not in kinds):
        return False
    kind, reason = found
    if kind == "scheduled":
        print(f"[!] {reason} - rejoining")
        log_run_event(f"scheduled rejoin: {reason}")
        run_stats.scheduled_rejoins += 1
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
    print("[!] Recovery complete - carrying on")
    write_status()
    return True

def detect_minigame_popup(img: Image) -> bool:
    """True if one of the Halloween minigame popups ("Ghost Gallery is
    starting soon! Teleport there now?", "Hauntlet 2 is starting soon!...")
    is on screen in `img`. They share one layout and nothing here tells them
    apart - only that a minigame is offering to teleport you. Neither button
    color is unique on its own (the Yes green is also the paycheck's CASH OUT
    green, the No red is also the Exit Home button), so it needs both: the
    Yes green, with the No red close by to its left. Not the sick need's
    doctor dialog, which looks the same but belongs to that handler."""
    sick = load_templates(cfg.STRAY_WINDOW_DIR).get("sick_dialog")
    if sick is not None and template_score(img, sick, cfg.SICK_DIALOG_BOX) >= cfg.SICK_DIALOG_MATCH_THRESHOLD:
        return False
    ys, xs = np.where(exact_color_mask(img, cfg.GREEN_BUTTON_COLOR) > 0)
    if len(xs) < cfg.MINIGAME_POPUP_MIN_BUTTON_PIXELS:
        return False
    yes_x, yes_y = int(xs.mean()), int(ys.mean())
    near_no = img[max(0, yes_y - cfg.MINIGAME_POPUP_NO_MAX_DY):yes_y + cfg.MINIGAME_POPUP_NO_MAX_DY,
                  max(0, yes_x - cfg.MINIGAME_POPUP_NO_MAX_DX):yes_x]
    return int(np.count_nonzero(exact_color_mask(near_no, cfg.MINIGAME_POPUP_NO_COLOR))) >= cfg.MINIGAME_POPUP_MIN_BUTTON_PIXELS

# directory -> (the files' names and modification times, {name: image}) - see load_templates().
_TEMPLATE_CACHE: dict[str, tuple[Any, dict[str, Image]]] = {}

def load_templates(directory: str) -> dict[str, Image]:
    """{lower-cased file name without extension: image} for every .png in
    `directory` (none if it doesn't exist). The images are read once and kept:
    a call only looks at the files' modification times (a few cheap stats,
    against decoding every PNG on every check), and reads them again when one
    is added, changed or removed - so a crop replaced while the macro runs is
    picked up. The images are shared between calls, so they must not be
    modified."""
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
    """Close any window from STRAY_WINDOWS that's open (found by its title
    crop in STRAY_WINDOW_DIR): the Trading Hub the macro can open by a
    misclick - first its "Go to the Trading Hub to edit listings!" popup, by
    the green Okay button - and the Star Rewards, which opens once a day.
    They cover the screen and get in the way of everything the macro clicks
    (the Star Rewards also hides the need icons; the Trading Hub doesn't, but
    needs can't be resolved with it in the way), and the paycheck check
    mistakes their green buttons for CASH OUT. Returns the name of the window
    closed, or None."""
    img = grab_screen()
    for name, crop in load_templates(cfg.STRAY_WINDOW_DIR).items():
        config = cfg.STRAY_WINDOWS.get(name)
        if config is None or template_score(img, crop, config["box"]) < cfg.STRAY_WINDOW_MATCH_THRESHOLD:
            continue
        if config.get("only_when_unwanted") and state.pet_focus_wanted:
            continue
        print(f"[debug] stray window open: {name}, closing it...")
        log_run_event(f"stray window closed: {name}")
        if name == "trading_hub":
            left, top, right, bottom = cfg.STRAY_OKAY_BOX
            okay = exact_color_mask(img[top:bottom, left:right], cfg.GREEN_BUTTON_COLOR, cfg.GREEN_BUTTON_TOLERANCE)
            if np.count_nonzero(okay) >= cfg.STRAY_OKAY_MIN_PIXELS:
                hover_click(*cfg.STRAY_OKAY_POS)
        hover_click(*config["close_pos"])
        time.sleep(cfg.STRAY_CLOSE_SETTLE)
        return name
    return None

def detect_minigame_victory(img: Image) -> bool:
    """True if the minigame victory screen is on screen in `img`: the red
    GAME OVER! banner and the green NICE! button, both by exact color (the
    same green is the Yes button's, so the banner is what rules that out)."""
    for color, tolerance, box, minimum in (
            (cfg.MINIGAME_VICTORY_BANNER_COLOR, cfg.MINIGAME_VICTORY_BANNER_TOLERANCE,
             cfg.MINIGAME_VICTORY_BANNER_BOX, cfg.MINIGAME_VICTORY_BANNER_MIN_PIXELS),
            (cfg.GREEN_BUTTON_COLOR, cfg.GREEN_BUTTON_TOLERANCE,
             cfg.MINIGAME_VICTORY_BUTTON_BOX, cfg.MINIGAME_VICTORY_BUTTON_MIN_PIXELS)):
        left, top, right, bottom = box
        if np.count_nonzero(exact_color_mask(img[top:bottom, left:right], color, tolerance)) < minimum:
            return False
    return True

def detect_minigame_overlay(img: Image) -> Optional[str]:
    """Which minigame's overlay (MINIGAME_OVERLAYS, crops in
    MINIGAME_OVERLAY_DIR) is on screen in `img`, or None."""
    crops = load_templates(cfg.MINIGAME_OVERLAY_DIR)
    best_name, best_score = None, cfg.MINIGAME_OVERLAY_THRESHOLD
    for name, box in cfg.MINIGAME_OVERLAYS.items():
        crop = crops.get(name)
        score = template_score(img, crop, box) if crop is not None else 0.0
        if score >= best_score:
            best_name, best_score = name, score
    return best_name

class MinigameWatch:
    """Keeps track of whether the minigame being played is on screen (its
    overlay - detect_minigame_overlay()), fed one screenshot at a time by
    minigame_won(). `problem` says why to stop, or None."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.began = time.time()
        self.last_seen: Optional[float] = None
        self.start_grace_from: Optional[float] = None   # set once the start wait is over

    def update(self, img: Image) -> None:
        if detect_minigame_overlay(img) == self.name:
            self.last_seen = time.time()

    def start_wait_over(self) -> None:
        self.start_grace_from = time.time()

    @property
    def problem(self) -> Optional[str]:
        now = time.time()
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
    if not dismiss_stray_windows():
        detect_paycheck()

def minigame_won(watch: MinigameWatch) -> bool:
    """Run the popup checks if they're due, then look at a fresh screenshot:
    True on the victory screen (detect_minigame_victory()); either way `watch`
    is told what the screenshot shows."""
    minigame_popup_checks()
    img = grab_screen()
    watch.update(img)
    return detect_minigame_victory(img)

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

def play_hauntlet_2(deadline: float, watch: MinigameWatch) -> bool:
    """Hauntlet 2: wait MINIGAME_START_WAIT for it to start (no needs can be
    seen while it runs, and the lobby counts down first), then hold forward
    until the victory screen shows - or the overlay says it's over. Returns
    True if it was won before `deadline`."""
    print(f"[debug] hauntlet 2: waiting {cfg.MINIGAME_START_WAIT:g}s for it to start...")
    minigame_wait(cfg.MINIGAME_START_WAIT, watch)
    watch.start_wait_over()
    pydirectinput.keyDown(cfg.HAUNTLET_2_FORWARD_KEY)
    try:
        while time.time() < deadline and not watch.problem:
            wait_interruptible(cfg.MINIGAME_VICTORY_CHECK_INTERVAL)
            if minigame_won(watch):
                return True
    finally:
        pydirectinput.keyUp(cfg.HAUNTLET_2_FORWARD_KEY)
    return False

def play_ghost_gallery(deadline: float, watch: MinigameWatch) -> bool:
    """Ghost Gallery: run about at random, jumping, with the mouse held in
    GHOST_GALLERY_HOLD-second holds (GHOST_GALLERY_HOLD_GAP apart), until the
    victory screen shows - or the overlay says it's over. Returns True if it
    was won before `deadline`."""
    print(f"[debug] ghost gallery: waiting {cfg.MINIGAME_START_WAIT:g}s for it to start...")
    minigame_wait(cfg.MINIGAME_START_WAIT, watch)
    watch.start_wait_over()
    while time.time() < deadline and not watch.problem:
        pydirectinput.mouseDown()
        try:
            hold_until = time.time() + cfg.GHOST_GALLERY_HOLD
            while time.time() < hold_until and not watch.problem:
                if minigame_won(watch):
                    return True
                key = random.choice(cfg.MOVE_KEYS)
                pydirectinput.keyDown(key)
                try:
                    pydirectinput.press(cfg.KEY_JUMP)
                    wait_interruptible(min(random.uniform(cfg.GHOST_GALLERY_STEP_MIN, cfg.GHOST_GALLERY_STEP_MAX),
                                           max(0.0, hold_until - time.time())))
                finally:
                    pydirectinput.keyUp(key)
        finally:
            pydirectinput.mouseUp()
        wait_interruptible(cfg.GHOST_GALLERY_HOLD_GAP)
    return False

MINIGAME_PLAYERS = {"hauntlet_2": play_hauntlet_2, "ghost_gallery": play_ghost_gallery}

def click_nice_until_gone() -> bool:
    """Click the win screen's NICE! button, wait MINIGAME_FINISH_WAIT, and
    look again: while the win screen is still up (the click missed, or the
    screen was still animating in) click once more, up to
    MINIGAME_VICTORY_CLICK_TRIES times in all. Returns True if it's gone."""
    for attempt in range(cfg.MINIGAME_VICTORY_CLICK_TRIES):
        hover_click(*cfg.MINIGAME_VICTORY_BUTTON_POS)
        wait_interruptible(cfg.MINIGAME_FINISH_WAIT)
        if not detect_minigame_victory(grab_screen()):
            return True
        print(f"[debug] win screen still up after click {attempt + 1}")
    log_failure("minigame win screen would not close")
    return False

def play_minigame(name: str) -> bool:
    """Take the popup's Yes, play minigame `name` until its victory screen
    shows, click NICE!, and respawn. Gives up (a logged failure) when the
    overlay shows it's over or never started (MinigameWatch), or after
    MINIGAME_MAX_DURATION.
    Counts as progress for the no-progress rejoin. Returns True if it was won."""
    label = cfg.MINIGAME_LABELS.get(name, name)
    print(f"[!] playing {label}")
    log_run_event(f"minigame started: {label}")
    # The handler this interrupted may be holding a key or the mouse button
    # down (hold_key(), the pet swipe); that must not carry into the minigame.
    release_all_inputs()
    hover_click(*cfg.MINIGAME_POPUP_YES_POS)
    started = time.time()
    watch = MinigameWatch(name)
    try:
        won = MINIGAME_PLAYERS[name](started + cfg.MINIGAME_MAX_DURATION, watch)
    finally:
        release_all_inputs()
    if won:
        log_run_event(f"minigame finished: {label} ({time.time() - started:.0f}s)")
        click_nice_until_gone()
    else:
        reason = watch.problem or f"did not reach the victory screen within {cfg.MINIGAME_MAX_DURATION:g}s"
        log_failure(f"{label} {reason}")
    state.last_progress = state.last_detection = time.time()
    respawn_character()
    return won

def close_minigame_win_screen(abandon: bool = True) -> bool:
    """If the minigame win screen (detect_minigame_victory()) is up - whether
    the macro was playing that minigame or not: one that ended after the
    macro had stopped watching it, say - click NICE! and wait
    MINIGAME_FINISH_WAIT. With `abandon` (the default) it then counts as
    progress and the character is respawned, as after a minigame it played
    itself (the win screen has put it back in the world somewhere unknown);
    without (setup, which can't be abandoned halfway) only the click is made.
    Returns True if the screen was there."""
    if not detect_minigame_victory(grab_screen()):
        return False
    print("[debug] minigame win screen is up, clicking NICE!...")
    log_run_event("minigame win screen closed")
    click_nice_until_gone()
    if abandon:
        state.last_progress = state.last_detection = time.time()
        respawn_character()
    return True

def minigame_popup(play: bool = True) -> Optional[str]:
    """Halloween only (see HALLOWEEN): handles a minigame popup (see
    detect_minigame_popup()). Which minigame it is comes from
    identify_minigame(). One that is switched on ("<name>_enabled" in the game
    config) is played (play_minigame()) - unless `play` is False, when it's
    only closed with No so it can offer itself again; any other (switched off,
    or not recognised) is dismissed for the session with "do not show again"
    ticked. Returns "played" or "dismissed" if a popup was there, None if not."""
    img = grab_screen()
    if not detect_minigame_popup(img):
        return None
    name = identify_minigame(img)
    if name is not None and load_game_config().get(f"{name}_enabled", False):
        if play:
            print(f"[debug] minigame popup detected: playing {name}")
            play_minigame(name)
            return "played"
        print(f"[debug] minigame popup detected ({name}), declining it for now...")
        hover_click(*cfg.MINIGAME_POPUP_NO_POS)
        return "dismissed"
    print(f"[debug] minigame popup detected ({name or 'unknown'}), dismissing...")
    log_run_event(f"minigame popup dismissed ({name or 'unknown'})")
    hover_click(*cfg.MINIGAME_POPUP_DONT_SHOW_POS)   # "Do not show again this session"
    hover_click(*cfg.MINIGAME_POPUP_NO_POS)
    return "dismissed"

def unscrew(in_task: bool = False, play_minigames: bool = True) -> Optional[str]:
    """The per-cycle checks - run right after check_stop() at the top of every
    cycle, and, with `in_task`, every UNSCREW_TASK_INTERVAL seconds while a
    need handler or setup is running (see task_unscrew()). In order:
      - rejoin if we've been disconnected (first, since nothing else works
        while disconnected; mid-task only that, not the scheduled or stalled
        rejoins, which wait for the top of a cycle);
      - close the backpack, in either form (close_backpack_if_stuck(): at once
        at the top of a cycle, mid-task once it's been seen open twice in a
        row);
      - close stray windows (dismiss_stray_windows());
      - during Halloween, close a minigame win screen if one is up
        (close_minigame_win_screen()) and handle a minigame popup;
      - the paycheck popup (not while the backpack is still open - its green
        Select All button would be mistaken for CASH OUT).
    The stray windows and the minigame popup go before the paycheck check
    because their green buttons would otherwise be mistaken for CASH OUT too.
    Returns "played" if a minigame was played, "rejoined" if the game was
    rejoined mid-task or "aborted" if a stray window that spoils the task was
    closed (see "abort_task" in STRAY_WINDOWS) or a paycheck popup was
    dismissed - its clicks and the dismissal leave the backpack and the
    character in an unknown state, so the task starts over (never with
    `play_minigames` False, i.e. in setup, which can't be abandoned halfway) - the callers
    abandon the task - otherwise None."""
    if in_task:
        if rejoin_game(kinds=("disconnect",)):
            return "rejoined"
    else:
        rejoin_game()
    backpack_open = close_backpack_if_stuck(in_task)
    closed = dismiss_stray_windows()
    if cfg.HALLOWEEN and close_minigame_win_screen(abandon=play_minigames):
        if play_minigames:
            return "played"    # back in the world somewhere unknown: the task is abandoned like after a minigame
    played = minigame_popup(play=play_minigames) if cfg.HALLOWEEN else None
    paycheck = detect_paycheck() if not backpack_open else False
    if in_task and play_minigames and played is None and (paycheck or (closed and cfg.STRAY_WINDOWS[closed].get("abort_task"))):
        return "aborted"   # (not setup, which can't be abandoned halfway - that is `play_minigames` False)
    return played

def run_side_action(action: Callable[[], bool]) -> bool:
    """Run lure_collect() / tree_collect() with the in-task checks on (popups,
    stray windows, a minigame can turn up while they walk about), like a need
    handler. Returns the action's result - False if a minigame or a rejoin
    took over (TaskInterrupted), so it's tried again next cycle."""
    try:
        with task_unscrew():
            return action()
    except TaskInterrupted as interruption:
        print(f"[debug] {action.__name__} interrupted by {interruption.reason}")
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
    """The tree's money total starts again from 0 every day at
    MONEY_RESET_HOUR. `config["money_reset_boundary"]` is the last such
    moment already dealt with; when a later one has passed the total is reset
    (and, like each harvest, saved at once). The first time it is seen (a
    fresh or older config) it is only recorded, not acted on, so an update
    doesn't wipe the day's total. Returns the config as it is now."""
    boundary = last_money_reset_time(time.time())
    recorded = config["money_reset_boundary"]
    if recorded is not None and recorded >= boundary:
        return config
    if recorded is None:
        return update_game_config(lambda c: c.update(money_reset_boundary=boundary))
    print(f"[!] money total reset (daily at {cfg.MONEY_RESET_HOUR}:00): {config['money_collected']} -> 0")
    log_run_event(f"money total reset: {config['money_collected']} -> 0")
    return update_game_config(lambda c: c.update(money_collected=0, money_reset_boundary=boundary))

def side_quest() -> None:
    """Runs once per cycle, right after unscrew(), unless side_quest_enabled
    is off in GAME_CONFIG_PATH: tends to the money tree and the lure using
    state persisted there. The tree is checked at most once per
    TREE_CHECK_INTERVAL, and money_collected only ever goes up (by
    TREE_HARVEST_YIELD per harvest, until it reaches MONEY_COLLECTED_TARGET)
    until reset_money_if_due() starts it from 0 again at MONEY_RESET_HOUR
    each day (or the config is reset). Each piece of
    progress is saved the moment its action succeeds, so a stop partway
    through doesn't lose it."""
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
    """Records the Roblox window to a low quality video (RECORD_WIDTH x
    RECORD_HEIGHT at RECORD_FPS) from a background thread, each frame stamped
    with the time so it lines up with output.log and run_log.txt. Stops by
    itself after RECORD_MAX_MINUTES. MP4 where OpenCV can write it, otherwise
    MJPG in an .avi."""

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
            self.stop_event.wait(max(0.0, (written / cfg.RECORD_FPS) - (time.time() - started)))

    def stop(self) -> None:
        """Stop recording and close the file."""
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=5.0)
        if self.writer is not None:
            self.writer.release()
        size_mb = os.path.getsize(self.path) / (1024 * 1024) if os.path.exists(self.path) else 0
        log_run_event(f"recording stopped: {os.path.basename(self.path)} ({size_mb:.0f} MB)")
        print(f"[!] Recording saved: {self.path} ({size_mb:.0f} MB)")

def start_recording_if_requested() -> Optional[RunRecorder]:
    """If "Record the next run" is ticked (the record_next_run config switch):
    clear it - it's for one run only, and the Options checkbox is unticked to
    match - start a RunRecorder in RECORDINGS_DIR, named after the run number
    and time, delete the oldest videos beyond MAX_RECORDINGS, and return the
    recorder. Returns None if it isn't ticked or no video file could be opened
    (which is logged)."""
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
        print("[!] Couldn't open a video file - not recording")
        log_run_event("recording: couldn't open a video file")
        return None
    log_run_event(f"recording started: {os.path.basename(recorder.path)}")
    print(f"[!] Recording this run to {recorder.path}")
    return recorder

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

class ResourceMonitor(threading.Thread):
    """Every RESOURCE_LOG_INTERVAL while the loop runs, logs this script's CPU
    use (percent of the whole machine, over the interval just gone) and memory
    - "resources: cpu 2.4% mem 215MB" - and keeps the latest in run_stats. The
    run report charts them. Stopped by stop()."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.stopped = threading.Event()

    def stop(self) -> None:
        self.stopped.set()

    def run(self) -> None:
        wall, cpu = time.time(), time.process_time()
        while not self.stopped.wait(cfg.RESOURCE_LOG_INTERVAL):
            now_wall, now_cpu = time.time(), time.process_time()
            percent = 100 * (now_cpu - cpu) / max(1e-6, (now_wall - wall) * (os.cpu_count() or 1))
            wall, cpu = now_wall, now_cpu
            memory = process_memory_mb()
            run_stats.cpu_percent = round(percent, 1)
            run_stats.memory_mb = None if memory is None else round(memory)
            if memory is not None:
                try:
                    log_run_event(f"resources: cpu {percent:.1f}% mem {memory:.0f}MB")
                except OSError:
                    pass   # a transient write failure: sample again next time


def check_quiet() -> None:
    """Every cycle: note in run_stats how many whole minutes no need has been
    resolved for, and log "quiet: no need resolved for N min" each
    QUIET_LOG_INTERVAL of it, so a stretch where nothing happens (the loop
    alive but stuck, or the need bar unreadable) shows in the log well before
    the NO_PROGRESS_REJOIN_INTERVAL rejoin."""
    quiet = time.time() - state.last_progress if state.last_progress else 0.0
    run_stats.quiet_minutes = int(quiet // 60)
    reports = int(quiet // cfg.QUIET_LOG_INTERVAL)
    if reports > state.quiet_reported:
        log_run_event(f"quiet: no need resolved for {reports * cfg.QUIET_LOG_INTERVAL / 60:g} min")
    state.quiet_reported = reports

def respawn_if_no_detection() -> bool:
    """Respawn the character if no need has been detected on screen for
    NO_DETECTION_RESPAWN_INTERVAL - the character may be stuck somewhere the
    need icons don't show up from. Counts from the last detection (see
    State.last_detection) and again from the respawn, so it repeats at most
    once per interval. It never touches state.last_progress, so the
    NO_PROGRESS_REJOIN_INTERVAL rejoin (rejoin_reason()) still comes when it
    would have. Returns True if it respawned."""
    if state.last_detection is None or time.time() - state.last_detection < cfg.NO_DETECTION_RESPAWN_INTERVAL:
        return False
    minutes = (time.time() - state.last_detection) / 60
    print(f"[!] no need detected for {minutes:.0f} min - respawning")
    log_run_event(f"no need detected for {minutes:.0f} min - respawning")
    respawn_character()
    state.last_detection = time.time()
    return True

def run_full_cycle() -> None:
    """One cycle: keep checking for needs, waiting NEED_CHECK_RETRY_DELAY
    between checks whenever none are found (showing how long it has been
    waiting on one overwritten line), until one is resolved. Returning after
    a single need means the next cycle - popups, side quest and all - starts
    from a fresh look at the screen."""
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
            print(f"\rWaiting for a need... {format_duration(time.time() - waiting_since)}", end="")
            wait_interruptible(min(1.0, deadline - time.time()))

def run_workflow_loop() -> None:
    """Respawn once, then repeat the workflow continuously. The only way this
    loop ever ends is via a stop request - there's no other exit condition,
    so StopRequested is expected here, not an error. It's allowed to
    propagate on up to run_async()'s worker (via the bare `finally`, not
    `except`) so the GUI still reports [STOPPED] correctly; the finally
    just prints locally first."""
    STOP_EVENT.clear()
    state.run_number = next_run_number()
    reset_need_tracking()
    state.idle_checks = 0
    reset_run_stats()
    state.last_rejoin = state.last_progress = state.last_detection = time.time()  # the scheduled and no-progress rejoins count from here
    log_run_event("LOOP started")
    recorder = start_recording_if_requested()
    print("\n" + "=" * 50)
    print(f"[LOOP] Starting continuous workflow (run {state.run_number})")
    print("=" * 50)

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
            print(f"\n[LOOP {loop_num}]")
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
        print("\n[LOOP] Stopped\n")

# ============================================================================
# RUN REPORT
# ============================================================================
# Debug helper: turns one run of run_log.txt into a PNG report.
#
# The log is parsed by parse_run() (pure text, no imports from the macro) and
# drawn with OpenCV by draw_report(), so there's no dependency beyond what the
# macro already has. generate_run_report() does both and writes the file.
#
# The report has four panels:
#   1. needs resolved per unit of time - the unit is picked so the run fills
#      about TARGET_BUCKETS bars - stacked by basic / teleport / other need,
#      with failures (red) and rejoins (blue) marked along the bottom;
#   2. needs detected per check, over time;
#   3. average seconds per resolved need, with how many there were;
#   4. counts of failures by kind, popups, minigames and the like.

LINE_RE = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\] \[RUN ([^\]]+)\] (.*)$")
RESOLVED_RE = re.compile(r"^resolved: (\S+) \((\d+)s\)$")
DETECTED_RE = re.compile(r"^detected: (.*)$")
NONE_MATCHED_RE = re.compile(r"^none matched")
IDLE_CHECKS_RE = re.compile(r"^idle checks: (\d+) over (\d+)s$")
RESOURCES_RE = re.compile(r"^resources: cpu ([\d.]+)% mem (\d+)MB$")
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
COLOR_TEXT = (40, 40, 40)
COLOR_GRID = (225, 225, 225)
COLOR_BG = (255, 255, 255)
COLOR_LINE = (150, 150, 150)
COLOR_MEAN = (40, 40, 200)
FONT = cv2.FONT_HERSHEY_SIMPLEX


def parse_ts(text: str) -> float:
    return time.mktime(time.strptime(text, "%Y-%m-%d %H:%M:%S"))


def parse_run(log_path: str, run: Union[int, str]) -> Optional[dict[str, Any]]:
    """Everything the report needs from run `run` (a number or its text) of
    the log at `log_path`, as a dict, or None if the run isn't in it:
      start, end - epoch seconds of the first and last line;
      resolved - [(t, need, seconds)]; detected - [(t, matched, icons)] per check;
      failures - [(t, text)]; rejoins - [t]; resources - [(t, cpu %, memory MB)]; events - Counter of everything else."""
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
            "failures": [], "rejoins": [], "resources": [], "events": Counter()}
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
        elif m := RESOURCES_RE.match(text):
            data["resources"].append((t, float(m.group(1)), int(m.group(2))))
        elif text.startswith("no need detected for "):
            data["events"]["idle respawns"] += 1
        elif text.startswith("quiet: "):
            data["events"]["quiet periods logged"] += 1
        elif text.startswith("FAILURE: "):
            data["failures"].append((t, text[len("FAILURE: "):]))
        elif text.startswith("recovery complete"):   # once per rejoin ("scheduled rejoin" is logged at its start too)
            data["rejoins"].append(t)
            data["events"]["rejoins"] += 1
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
                data["events"]["minigames won"] += 1
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
    legend_x = pl + 10
    for label, color in (("basic", COLOR_BASIC), ("teleport", COLOR_TELEPORT), ("other", COLOR_OTHER),
                         ("failure", COLOR_FAILURE), ("rejoin", COLOR_REJOIN)):
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
                         (panel2.x((i + 1) * step), panel2.y(np.mean(vals))), COLOR_MEAN, 3)
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

    # 5. the script's own CPU and memory use
    for box, title, column, color, unit in (
            ((20, 1090, 800, 1650), "Script CPU use (% of the machine)", 1, COLOR_BASIC, "%"),
            ((820, 1090, width - 20, 1650), "Script memory (MB)", 2, COLOR_TELEPORT, "MB")):
        values = [r[column] for r in data["resources"]]
        top_value = nice_max(max(values + [1]))
        panel = Panel(img, box, title, duration, top_value, time_ticks(duration))
        if values:
            points = np.array([[panel.x(r[0] - start), panel.y(r[column])] for r in data["resources"]], np.int32)
            cv2.polylines(img, [points], False, color, 2, cv2.LINE_AA)
            draw_text(img, f"mean {np.mean(values):.1f}{unit}, max {max(values):.1f}{unit}",
                      panel.plot[0] + 10, panel.plot[1] + 16, 0.45)
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
            print(f"[!] couldn't write the run report to {path}")
            return None
        return path
    except Exception as error:
        print(f"[!] couldn't write the run report: {error}")
        return None

# ============================================================================
# GUI
# ============================================================================

def keep_newest_files(directory: str, keep: int) -> None:
    """Delete the oldest files in `directory` beyond the newest `keep`. Never raises. (A pruned run report can be
    drawn again from the logs, which are kept - see generate_run_report().)"""
    try:
        files = sorted((os.path.join(directory, f) for f in os.listdir(directory)), key=os.path.getmtime)
        for old in files[:max(0, len(files) - keep)]:
            if os.path.isfile(old):
                os.remove(old)
    except OSError:
        pass

def write_run_report(run: Union[int, str]) -> Optional[str]:
    """Chart run `run` from the log into REPORTS_DIR (see RUN REPORT above) and print
    where it went. Returns the path, or None. Never raises."""
    path = generate_run_report(cfg.RUN_LOG_PATH, run, cfg.REPORTS_DIR, cfg.BUTTON_NAMES, cfg.TELEPORT_WALK_NEEDS)
    if path:
        print(f"[debug] run report: {path}")
        keep_newest_files(cfg.REPORTS_DIR, cfg.MAX_REPORTS)
    return path

def run_all_need_handlers(handlers: Optional[dict[str, Callable[[], NeedHandler]]] = None) -> dict[str, str]:
    """Run every need handler (default: DEBUG_HANDLERS, whether or not it is in
    ENABLED_NEEDS) once, one after another - the Debug tab's "test every
    need" button. Each runs with the in-task checks on, like a real need, and
    the character is respawned after it, since the handlers walk from the
    spawn point. Minigame popups are declined, not played (play_minigames=False),
    so they can't cut a handler short. A stop ends the whole sequence (StopRequested propagates).
    Prints and returns how each went: "done", "failed" (the handler returned
    False) or "interrupted by ...", keyed by need."""
    handlers = DEBUG_HANDLERS if handlers is None else handlers
    results: dict[str, str] = {}
    print(f"\n[TEST] Running all {len(handlers)} need handlers, one after another...")
    for i, (name, handler_cls) in enumerate(handlers.items(), 1):
        check_stop()
        print(f"\n[TEST] ({i}/{len(handlers)}) {name}...")
        try:
            with task_unscrew(play_minigames=False):    # a minigame popup is declined, not played
                results[name] = "done" if handler_cls().handle() else "failed"
        except TaskInterrupted as interruption:
            results[name] = f"interrupted by {interruption.reason}"
        print(f"[TEST] {name}: {results[name]}")
        respawn_character()
    print("\n[TEST] Summary: " + ", ".join(f"{name} {result}" for name, result in results.items()) + "\n")
    return results

class DebugCapture:
    """Replaces sys.stdout for the lifetime of the GUI: everything printed
    anywhere in the macro goes to the on-screen console - every line
    starting with the time - and, via log_output(), to OUTPUT_LOG_PATH.
    A message starting with a carriage return is a status line instead: it
    replaces the previous status line rather than adding one (and is logged
    to the file only when it first appears), until something else is
    printed.

    write() can be called from any thread, but Tk widgets may only be
    touched from Tk's own, so it only logs the message and queues it; the
    queue is drained onto the widget every CONSOLE_DRAIN_INTERVAL_MS by
    root.after() on the Tk thread."""

    def __init__(self, root: tk.Tk, text_widget: tk.Text) -> None:
        self.root = root
        self.text = text_widget
        self.queue: queue.Queue[str] = queue.Queue()
        self.log_status_active = False   # write() side: a status line is the last thing logged
        self.at_line_start = True        # drain() side: the widget's state
        self.status_active = False
        self.drain()

    def write(self, msg: str) -> None:
        if not msg:
            return
        if msg.startswith("\r"):
            if not self.log_status_active:
                log_output(msg[1:] + "\n")
            self.log_status_active = True
        else:
            self.log_status_active = False
            log_output(msg)
        self.queue.put(msg)

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
                for msg in batch:
                    self.render(msg)
                self.trim()
                self.text.see(tk.END)
                self.text.config(state=tk.DISABLED)
            self.root.after(cfg.CONSOLE_DRAIN_INTERVAL_MS, self.drain)
        except tk.TclError:
            pass   # the window is gone

    def trim(self) -> None:
        """Keep the widget to its last CONSOLE_MAX_LINES lines (Tk thread only): it holds everything printed
        otherwise, and a run left going for days is a lot of text. output.log on disk keeps all of it."""
        lines = int(self.text.index("end-1c").split(".")[0])
        if lines - 1 > cfg.CONSOLE_MAX_LINES:   # (the last "line" is the empty one after the final newline)
            self.text.delete("1.0", f"{lines - cfg.CONSOLE_MAX_LINES}.0")

    def render(self, msg: str) -> None:
        """Add one message to the widget (Tk thread only)."""
        if msg.startswith("\r"):
            line = time.strftime("[%H:%M:%S] ") + msg[1:]
            if self.status_active:
                self.text.delete("end-1c linestart", "end-1c")
            elif not self.at_line_start:
                self.text.insert(tk.END, "\n")
            self.text.insert(tk.END, line)
            self.status_active = True
            self.at_line_start = False
            return
        if self.status_active:
            # the status line has no newline yet; the message supplies
            # one if it starts with it, otherwise end the line first
            if not msg.startswith("\n"):
                self.text.insert(tk.END, "\n")
            self.status_active = False
            self.at_line_start = True
        for piece in msg.splitlines(keepends=True):
            if self.at_line_start and piece != "\n":
                self.text.insert(tk.END, time.strftime("[%H:%M:%S] "))
            self.text.insert(tk.END, piece)
            self.at_line_start = piece.endswith("\n")

    def flush(self) -> None:
        pass

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
        sys.stdout = self.capture

        print("\n" + "=" * 60)
        print("ADOPT ME MACRO - CONTROL PANEL")
        print("=" * 60)
        print(f"Needs:  {cfg.NEEDS_DIR}")
        print(f"Debug:  {cfg.DEBUG_DIR}")
        print("=" * 60 + "\n")

        self.is_running = False
        self.watch_stop_hotkey()
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def close(self) -> None:
        """The window's X button: stop whatever is running and let go of every key and the mouse before the
        process ends - the worker is a daemon thread, which dies with the process before its own cleanup can run
        (a movement key or the mouse button would stay held down in the game)."""
        STOP_EVENT.set()
        time.sleep(cfg.STOP_CHECK_INTERVAL * 3)   # the worker notices the stop within a check or two and releases what it holds
        release_all_inputs()
        self.root.destroy()

    def watch_stop_hotkey(self) -> None:
        """Start a background thread that stops a running macro the moment
        STOP_HOTKEY is pressed, in any window - so you can take over the
        computer without reaching for the GUI. Only the key *going down*
        counts (holding it doesn't retrigger), and it does nothing while
        nothing is running. The stop itself is handed to Tk's thread."""
        def watch():
            was_down = False
            while True:
                try:
                    down = hotkey_down(cfg.STOP_HOTKEY)
                    if down and not was_down and self.is_running:
                        self.root.after(0, self.stop_by_hotkey)
                except (RuntimeError, tk.TclError):
                    return  # the window is gone
                was_down = down
                time.sleep(cfg.STOP_HOTKEY_POLL_INTERVAL)
        threading.Thread(target=watch, daemon=True).start()

    def stop_by_hotkey(self) -> None:
        """STOP_HOTKEY was pressed: stop, same as the [STOP] button, and say so."""
        if self.is_running:
            print(f"\n[!] '{cfg.STOP_HOTKEY.upper()}' pressed")
            self.stop()   # first: a failing log write must not keep the macro running
            try:
                log_run_event(f"stopped with the {cfg.STOP_HOTKEY.upper()} key")
            except OSError:
                pass

    def create_ui(self) -> None:
        """Build every widget. Any button that kicks off a background action
        (via run_async) is appended to self.action_buttons so run_async can
        disable/re-enable all of them together as a group. The [STOP] button
        is deliberately excluded - it must stay clickable while something
        is running, since that's the whole point of it."""
        # Main buttons
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.X, padx=cfg.GUI_OUTER_PADDING, pady=(cfg.GUI_OUTER_PADDING, 0))
        btn_frame = tk.Frame(notebook, bg=self.bg)
        options_tab = tk.Frame(notebook, bg=self.bg)
        debug_tab = tk.Frame(notebook, bg=self.bg)
        notebook.add(btn_frame, text="Main")
        notebook.add(options_tab, text="Options")
        notebook.add(debug_tab, text="Debug")

        self.action_buttons = []  # every button that starts a background task

        # One row: [LOOP] (the continuous workflow) expanding to fill the left,
        # [STOP] (square) on the right. Both sit in their own fixed-HEIGHT
        # container (pack_propagate(False)) so they're always the same height
        # regardless of font metrics - a
        # Button's own width/height (character units) don't scale consistently
        # across font sizes, and an unconstrained button's natural height grows
        # with its font size, which is what broke this row last time the icon
        # font got bigger. The stop container also fixes its WIDTH (making it a
        # square); the loop container only fixes height and otherwise expands
        # to fill the remaining width.
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

        btn_loop = tk.Button(loop_container, text="\U0001F504", command=self.run_workflow_loop,
                              font=(cfg.GUI_FONT, cfg.GUI_LOOP_ICON_FONT_SIZE, "bold"), bg=cfg.GUI_LOOP_COLOR, fg=self.fg,
                              cursor="hand2", **NO_BORDER)
        btn_loop.pack(fill=tk.BOTH, expand=True)
        self.action_buttons.append(btn_loop)

        tk.Label(btn_frame, text=f"Press {cfg.STOP_HOTKEY.upper()} anywhere to stop", font=(cfg.GUI_FONT, cfg.GUI_STATUS_FONT_SIZE),
                 bg=self.bg, fg=self.accent, anchor=tk.W).pack(fill=tk.X)

        tk.Frame(btn_frame, bg=self.accent, height=cfg.GUI_DIVIDER_HEIGHT).pack(fill=tk.X, pady=cfg.GUI_ROW_SPACING)

        tk.Label(btn_frame, text="Functions", font=(cfg.GUI_FONT, cfg.GUI_SECTION_FONT_SIZE, "bold"),
                 bg=self.bg, fg=self.accent).pack(anchor=tk.W)

        btn_respawn = tk.Button(btn_frame, text="Respawn", command=lambda: self.run_async(respawn_character),
                                 font=(cfg.GUI_FONT, cfg.GUI_SECTION_FONT_SIZE), bg=cfg.GUI_RESPAWN_COLOR, fg=self.fg,
                                 height=cfg.GUI_RESPAWN_BUTTON_HEIGHT, cursor="hand2")
        btn_respawn.pack(fill=tk.X, pady=cfg.GUI_WIDGET_SPACING)
        self.action_buttons.append(btn_respawn)

        btn_setup = tk.Button(btn_frame, text="Setup", command=lambda: self.run_async(setup_game),
                               font=(cfg.GUI_FONT, cfg.GUI_SECTION_FONT_SIZE), bg=cfg.GUI_RESPAWN_COLOR, fg=self.fg,
                               height=cfg.GUI_RESPAWN_BUTTON_HEIGHT, cursor="hand2")
        btn_setup.pack(fill=tk.X, pady=cfg.GUI_WIDGET_SPACING)
        self.action_buttons.append(btn_setup)

        btn_rejoin = tk.Button(btn_frame, text="Leave & rejoin", command=lambda: self.run_async(leave_and_rejoin),
                                font=(cfg.GUI_FONT, cfg.GUI_SECTION_FONT_SIZE), bg=cfg.GUI_RESPAWN_COLOR, fg=self.fg,
                                height=cfg.GUI_RESPAWN_BUTTON_HEIGHT, cursor="hand2")
        btn_rejoin.pack(fill=tk.X, pady=cfg.GUI_WIDGET_SPACING)
        self.action_buttons.append(btn_rejoin)

        # Options tab
        # Persisted switches (see load_game_config()). Deliberately not in
        # action_buttons: they must stay usable while a workflow is running,
        # which is safe since update_game_config() is locked.
        config = load_game_config()
        self.config_flag_vars: dict[str, tk.BooleanVar] = {}
        for label, key in (("Run side quest", "side_quest_enabled"),
                           ("Resume after focus loss", "resume_on_focus_loss"),
                           *((f"Play {label}", f"{name}_enabled") for name, label in cfg.MINIGAME_LABELS.items())):
            self.add_config_checkbox(options_tab, label, key, config)

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

    def send_to_back(self) -> None:
        """Stop being always-on-top and drop behind other windows."""
        self.root.attributes('-topmost', False)
        self.root.lower()

    def bring_to_front(self) -> None:
        """Always-on-top again, as at startup."""
        self.root.attributes('-topmost', True)
        self.root.lift()

    def run_on_ui_thread(self, func: Callable[[], Any], timeout: Optional[float] = 5.0) -> Any:
        """Run `func` on Tk's own thread (Tk isn't safe to touch from the
        worker thread) and wait until it has run, or `timeout` seconds (None
        = as long as it takes, for a dialog). Returns what `func` returned
        (None if it was still running when the wait ended)."""
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
        """Run `func` on a background daemon thread so the GUI never freezes
        while a workflow is executing (Tkinter blocks the whole window if you
        run slow code directly on the main thread).

        Lifecycle: disable the action buttons -> run func() in the background
        -> ALWAYS re-enable the buttons when func() returns, whether it
        finished normally, was interrupted via STOP_EVENT, lost Roblox's
        focus, or raised an exception. That "always" is done with
        try/except/finally so there is
        no code path that leaves the UI stuck in the disabled "Running..."
        state - the bug that made buttons stop responding once a workflow
        completed on its own.
        """

        if self.is_running:
            print("[!] Already running - press [STOP] first")
            return

        self.is_running = True
        STOP_EVENT.clear()
        self._set_running_ui(True)

        def worker():
            final_status = "[ERROR]"   # bound before anything can fail, so the finally below always re-enables the UI
            try:
                func()
                final_status = "[DONE]"
            except StopRequested:
                # Must be caught before the generic Exception handler below -
                # StopRequested is itself an Exception subclass, so if the
                # broad handler came first it would catch this too and
                # misreport a clean stop as "[ERROR]".
                log_run_event("stopped by the user")
                final_status = "[STOPPED]"
            except TaskInterrupted as interruption:
                # The GUI's Setup button ran setup_game() with the in-task checks on, and a disconnect made them
                # rejoin the game - which ran setup itself. The action is done, not crashed.
                log_run_event(f"interrupted by {interruption.reason}")
                final_status = "[DONE]"
            except FocusLost:
                # Same reasoning as StopRequested above - must come before
                # the generic Exception handler.
                if stop_pressed_soon():
                    log_run_event("stopped by the user")
                    final_status = "[STOPPED]"
                else:
                    log_failure("stopped: Roblox lost focus")
                    final_status = "[STOPPED: Roblox not focused]"
            except Exception as e:
                print(f"\n[ERROR] {e}\n{traceback.format_exc()}")
                log_failure(f"crashed: {type(e).__name__}: {e}")
                final_status = "[ERROR]"
            finally:
                release_all_inputs()  # last line of defense against stuck keys/mouse
                STOP_EVENT.clear()
                self.is_running = False
                # Tkinter widgets may only be touched from the main thread;
                # root.after() hands this callback to it instead of calling
                # .config() directly from this background thread.
                self.root.after(0, lambda status=final_status: self._set_running_ui(False, status))  # type: ignore[misc]

        threading.Thread(target=worker, daemon=True).start()

    def _set_running_ui(self, running: bool, status_text: Optional[str] = None) -> None:
        """Single place that toggles button state + status text. Called both
        right before a background task starts and from its finally block
        when it ends, so the two are always kept in sync."""
        button_state = tk.DISABLED if running else tk.NORMAL
        for btn in self.action_buttons:
            btn.config(state=button_state)  # type: ignore[call-overload]
        self.status.config(text="Running..." if running else (status_text or "Ready"))

    def run_workflow_loop(self) -> None:
        self.run_async(run_workflow_loop)

    def add_config_checkbox(self, parent: Any, label: str, key: str, config: dict[str, Any]) -> None:
        """A checkbox for the persisted on/off switch `key` (see
        load_game_config()). Deliberately not in action_buttons: they must stay
        usable while a workflow is running, which is safe since
        update_game_config() is locked."""
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
        print(f"[!] {key} = {value}")

    def save_server_link(self) -> None:
        """Persist the private server link typed into the entry box."""
        link = self.server_link_var.get().strip()
        if link != load_game_config()["private_server_link"]:
            update_game_config(lambda c: c.update(private_server_link=link))
            print("[!] Private server link saved")

    def reset_config(self) -> None:
        """Delete the persisted config (money collected, timers and the
        switches all go back to their defaults; the private server link is
        kept) and refresh the checkboxes to match."""
        reset_game_config()
        config = load_game_config()
        for key, var in self.config_flag_vars.items():
            var.set(config[key])
        print("[!] Config reset to defaults")

    def build_debug_tab(self, parent: Any) -> None:
        """The Debug tab: a Needs subtab (build_needs_tab()) and an Other one
        (build_other_tab())."""
        subtabs = ttk.Notebook(parent)
        subtabs.pack(fill=tk.BOTH, expand=True)
        needs_tab = tk.Frame(subtabs, bg=self.bg)
        other_tab = tk.Frame(subtabs, bg=self.bg)
        subtabs.add(needs_tab, text="Needs")
        subtabs.add(other_tab, text="Other")
        self.build_needs_tab(needs_tab)
        self.build_other_tab(other_tab)

    def build_needs_tab(self, parent: Any) -> None:
        """Fill the Needs subtab: a button that runs every need handler in turn,
        then a two-column grid of [TEST] buttons, one per entry in
        DEBUG_HANDLERS."""
        for col in range(2):
            parent.grid_columnconfigure(col, weight=1, uniform="debug")
        btn_all = tk.Button(parent, text="Test every need, one after another", command=self.test_all_handlers,
                            font=(cfg.GUI_FONT, cfg.GUI_SECTION_FONT_SIZE, "bold"), bg=cfg.GUI_LOOP_COLOR, fg=self.fg, cursor="hand2")
        btn_all.grid(row=0, column=0, columnspan=2, sticky=tk.EW, padx=cfg.GUI_WIDGET_SPACING, pady=cfg.GUI_WIDGET_SPACING)
        self.action_buttons.append(btn_all)
        tk.Label(parent, text="Or run one need handler on its own", font=(cfg.GUI_FONT, cfg.GUI_SECTION_FONT_SIZE, "bold"),
                 bg=self.bg, fg=self.accent).grid(row=1, column=0, columnspan=2, sticky=tk.W,
                                                   padx=cfg.GUI_ROW_SPACING, pady=(cfg.GUI_ROW_SPACING, cfg.GUI_WIDGET_SPACING))
        for i, (name, handler_cls) in enumerate(DEBUG_HANDLERS.items()):
            btn = tk.Button(parent, text=name.capitalize(),
                            command=lambda n=name, c=handler_cls: self.test_handler(n, c),  # type: ignore[misc]
                            font=(cfg.GUI_FONT, cfg.GUI_SECTION_FONT_SIZE), bg=cfg.GUI_TEST_COLOR, fg=self.fg, cursor="hand2")
            btn.grid(row=2 + i // 2, column=i % 2, sticky=tk.EW, padx=cfg.GUI_WIDGET_SPACING, pady=cfg.GUI_WIDGET_SPACING)
            self.action_buttons.append(btn)

    def build_other_tab(self, parent: Any) -> None:
        """Fill the Other subtab: record the next run, refresh the debug
        folder, reset the config."""
        self.add_config_checkbox(parent, "Record the next run (low quality video)", "record_next_run", load_game_config())
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
        if self.is_running:
            print("[debug] stop the run before refreshing the debug folder")
            return
        if messagebox.askyesno("Refresh debug", "Delete everything in the debug folder (logs, images, recordings)?\n"
                                                "The game config is kept."):
            print(f"[debug] debug folder refreshed ({refresh_debug()} entries removed)")

    def test_all_handlers(self) -> None:
        """Run every need handler in DEBUG_HANDLERS once, one after another
        (run_all_need_handlers())."""
        self.run_async(run_all_need_handlers)

    def test_handler(self, name: str, handler_cls: Callable[[], NeedHandler]) -> None:
        """Run one need handler on its own, outside the normal need-detection
        flow, so it can be tried even when it isn't in ENABLED_NEEDS."""
        def test():
            print(f"\n[TEST] Running {name} handler...")
            try:
                with task_unscrew():
                    handler_cls().handle()
            except TaskInterrupted as interruption:
                print(f"[TEST] {name} interrupted by {interruption.reason}")
            print(f"[TEST] {name.capitalize()} handler complete\n")
        self.run_async(test)

    def stop(self) -> None:
        """Signal a running workflow to stop. This only sets STOP_EVENT; the
        actual cleanup (re-enabling buttons, resetting is_running) happens
        automatically in run_async()'s worker thread once the running
        function notices the flag (via wait_interruptible) and returns -
        there's no separate watchdog thread needed for that anymore."""
        if not self.is_running:
            return
        print("\n[!] STOPPING...")
        STOP_EVENT.set()
        self.status.config(text="Stopping...")

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    root = tk.Tk()
    gui = AdoptMeGUI(root)
    root.mainloop()
