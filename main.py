#!/usr/bin/env python3
"""Adopt Me Macro - Automated pet care with GUI control panel."""

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

import os, sys, re, time, threading, json, subprocess, traceback, ctypes, queue, random, contextlib
from abc import ABC, abstractmethod
from functools import partial
import tkinter as tk
from tkinter import scrolledtext, simpledialog, ttk

import numpy as np
import cv2
import mss
import pyautogui
import pydirectinput
import pygetwindow as gw

from magic_numbers import *

class TaskInterrupted(Exception):
    """Raised from inside a need handler's waits when something that can't
    wait (a minigame) took over the character: whatever the handler was
    doing is abandoned. process_needs() catches it; the next check decides
    from the screen what's still needed."""
    pass

class StopRequested(Exception):
    """Raised to unwind out of a running workflow when the user presses [STOP].

    This is deliberately cooperative cancellation, not thread-killing. Python
    has no safe way to force-terminate a running thread from the outside -
    the closest trick (async-raising into another thread via ctypes) is
    explicitly unsafe, and for a game macro that matters a lot: if the kill
    landed while a movement key or the mouse button was held down, it would
    never get released and would stay stuck in the game.

    Instead, wait_interruptible() and check_running() raise this the moment
    they notice STOP_FLAG, and ordinary Python exception propagation unwinds
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

def hotkey_down(key):
    """True while `key` (a single letter or digit) is held down, whichever
    window has the focus (Windows)."""
    return bool(ctypes.windll.user32.GetAsyncKeyState(ord(key.upper())) & 0x8000)

def check_stop():
    """Raise StopRequested if the user has pressed [STOP]. Prefer
    check_running() at checkpoints that should also require Roblox focus -
    this exists on its own for the one checkpoint that shouldn't (see
    run_full_cycle())."""
    if STOP_FLAG:
        raise StopRequested()

def check_focus():
    """Raise FocusLost if Roblox is running but is no longer the focused
    window. Prefer check_running() at most checkpoints."""
    if not is_roblox_focused():
        print("\n[!] Roblox is no longer focused")
        raise FocusLost()

def check_running():
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

def roblox_windows():
    """Every open window with 'roblox' in its title."""
    return [w for w in gw.getAllWindows() if "roblox" in w.title.lower()]

def roblox_window():
    """The Roblox game window: the one titled exactly "Roblox" if there is
    one (so a browser tab that merely mentions Roblox isn't picked), else the
    first window with 'roblox' in its title, else None."""
    wins = roblox_windows()
    return next((w for w in wins if w.title == "Roblox"), wins[0] if wins else None)

def focus_roblox():
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
    time.sleep(FOCUS_DELAY)
    return True

def is_roblox_focused():
    """Return True if a window with 'roblox' in its title is currently the
    active (focused) window."""
    active = gw.getActiveWindow()
    return active is not None and "roblox" in (active.title or "").lower()

def focus_roblox_click():
    """Focus Roblox and click near the top edge to make sure input registers."""
    if not FOCUS_WINDOW_ON_ACTION:
        return True
    if not focus_roblox():
        return False
    pyautogui.moveTo(*to_screen(REFERENCE_WIDTH * FOCUS_CLICK_X_PERCENT, FOCUS_CLICK_Y))
    pydirectinput.click()
    time.sleep(FOCUS_CLICK_SETTLE_DELAY)
    return True

# (time looked up, rect) of the last roblox_rect() answer - see ROBLOX_RECT_TTL.
_ROBLOX_RECT = None

def roblox_rect():
    """(left, top, width, height), in screen pixels, of the Roblox window
    clipped to the primary monitor - a maximized window reports a few pixels
    of invisible border past the screen edges, which this trims, so a
    maximized window on a 1920x1080 screen is exactly (0, 0, 1920, 1080).
    The whole monitor if there's no usable Roblox window (not running,
    minimized). Remembered for ROBLOX_RECT_TTL seconds."""
    global _ROBLOX_RECT
    now = time.time()
    if _ROBLOX_RECT and now - _ROBLOX_RECT[0] < ROBLOX_RECT_TTL:
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

def to_screen(x, y):
    """Turn a position in the reference space every constant and every
    detection uses (REFERENCE_WIDTH x REFERENCE_HEIGHT) into the screen
    pixel it is on in the Roblox window right now."""
    left, top, width, height = roblox_rect()
    return int(left + x * width / REFERENCE_WIDTH), int(top + y * height / REFERENCE_HEIGHT)

def grab_full_screen():
    """Screenshot the whole primary monitor as a BGR numpy array."""
    with mss.MSS() as sct:
        shot = np.array(sct.grab(sct.monitors[1]))
    return cv2.cvtColor(shot, cv2.COLOR_BGRA2BGR)

def grab_screen():
    """Screenshot of the Roblox window as a BGR numpy array, scaled to the
    reference size (REFERENCE_WIDTH x REFERENCE_HEIGHT) so every detection
    and position works on the same pixels whatever the window or monitor
    size. Unchanged from a plain screenshot when Roblox is maximized on a
    1920x1080 screen."""
    img = grab_full_screen()
    left, top, width, height = roblox_rect()
    if (left, top, width, height) != (0, 0, img.shape[1], img.shape[0]):
        img = img[top:top + height, left:left + width]
    if img.shape[1] != REFERENCE_WIDTH or img.shape[0] != REFERENCE_HEIGHT:
        img = cv2.resize(img, (REFERENCE_WIDTH, REFERENCE_HEIGHT), interpolation=cv2.INTER_AREA)
    return img

def exact_color_mask(img, rgb, tolerance=0):
    """Mask of every pixel in `img` (BGR, as grab_screen() returns) that
    matches `rgb` (an (R, G, B) tuple) exactly - or, with `tolerance`, to
    within that much on each channel."""
    r, g, b = rgb
    target_bgr = np.array([b, g, r])
    return cv2.inRange(img, target_bgr - tolerance, target_bgr + tolerance)

def find_exact_color(img, rgb):
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
# else (fixed config/tuning values) lives in magic_numbers.py instead.

STOP_FLAG = False

# The GUI object (set by AdoptMeGUI), so macro code running on the worker
# thread can send the macro window to the back and bring it up again - see
# send_macro_window_to_back(). None when running without the GUI.
MACRO_WINDOW = None

# When the game was last (re)joined: set when the loop starts and by every
# successful leave_and_rejoin(); rejoin_reason() schedules the next hourly
# refresh REJOIN_INTERVAL after it. None until then.
LAST_REJOIN = None

# When a need was last resolved (or the loop started, or the game was last
# rejoined): rejoin_reason() rejoins after NO_PROGRESS_REJOIN_INTERVAL
# without any. None until the loop starts.
LAST_PROGRESS = None

# Detected action-button screen positions, keyed by need name (e.g. "hungry").
# Populated by refresh_button_mapping() each time the macro walks to the
# buttons, so this is a same-run cache, not persisted state - there's
# nothing gained from saving it to disk since it's never trusted across a
# run anyway (screen layout can change between sessions).
BUTTON_POSITIONS = {}

# The run number of the currently executing workflow (run_workflow_loop()), set by next_run_number() at the start of each. Every
# log_run_event() call tags its line with this, so entries from different
# runs can be told apart in the shared RUN_LOG_PATH file.
CURRENT_RUN_NUMBER = None

# Stuck-need tracking (see record_detection()): how many times in a row each
# need was attempted and still showed on the next check, and which needs the
# last pass attempted. Reset by reset_need_tracking() at the start of every
# workflow run and after a rejoin, so a streak never spans a stop and restart.
ATTEMPT_STREAKS = {}
ATTEMPTED_LAST = set()
# True while the last "detected:" line in the run log was "none matched", so
# an unrecognised icon sitting on screen is logged once, not on every check.
NONE_MATCHED_LOGGED = False

# Needs switched off for the rest of this launch of the script because they
# kept showing up without ever clearing. Deliberately not persisted (unlike
# the game config): relaunching the script gives every enabled need a fresh
# chance.
DISABLED_THIS_RUN = set()

# ============================================================================
# LOGGING
# ============================================================================

def next_run_number():
    """Return a fresh run number, persisted in RUN_COUNTER_PATH so numbers
    stay unique across separate launches of the script, not just within one
    session."""
    try:
        with open(RUN_COUNTER_PATH, "r") as f:
            n = int(f.read().strip())
    except (FileNotFoundError, ValueError):
        n = 0
    n += 1
    with open(RUN_COUNTER_PATH, "w") as f:
        f.write(str(n))
    return n

def reset_need_tracking():
    """Forget the stuck-need streaks (see record_detection())."""
    global ATTEMPTED_LAST, NONE_MATCHED_LOGGED
    ATTEMPT_STREAKS.clear()
    ATTEMPTED_LAST = set()
    NONE_MATCHED_LOGGED = False

def log_run_event(message):
    """Append one timestamped line to RUN_LOG_PATH, tagged with
    CURRENT_RUN_NUMBER."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(RUN_LOG_PATH, "a") as f:
        f.write(f"[{timestamp}] [RUN {CURRENT_RUN_NUMBER}] {message}\n")

# What the macro knows about how this run is going, written to STATUS_PATH by
# write_status() so it can be checked without opening the GUI.
RUN_STATS = {"run": None, "started": None, "cycles": 0, "last_detected": [], "resolved": {}, "failed": {},
             "failures": 0, "recoveries": 0, "disconnects": 0, "scheduled_rejoins": 0, "focus_resumes": 0, "last_resolved": None, "last_failure": None}

OUTPUT_LOG_LOCK = threading.Lock()
_output_at_line_start = True

def log_output(text):
    """Append `text` (whatever was just printed) to OUTPUT_LOG_PATH, with a
    timestamp at the start of every line. Called by DebugCapture, so it's
    everything the GUI console shows, kept on disk. Never raises. Past
    OUTPUT_LOG_MAX_BYTES the file moves to output.log.old first."""
    global _output_at_line_start
    try:
        with OUTPUT_LOG_LOCK:
            if os.path.exists(OUTPUT_LOG_PATH) and os.path.getsize(OUTPUT_LOG_PATH) > OUTPUT_LOG_MAX_BYTES:
                os.replace(OUTPUT_LOG_PATH, OUTPUT_LOG_PATH + ".old")
            with open(OUTPUT_LOG_PATH, "a", encoding="utf-8") as f:
                for piece in text.splitlines(keepends=True):
                    if _output_at_line_start:
                        f.write(time.strftime("[%Y-%m-%d %H:%M:%S] "))
                    f.write(piece)
                    _output_at_line_start = piece.endswith("\n")
    except OSError:
        pass

def save_failure_screenshot(reason):
    """Save a screenshot of the whole screen to FAILURE_DIR, named with the
    time, run number and `reason`, and delete the oldest beyond
    MAX_FAILURE_SCREENSHOTS. Returns the file name, or None if it couldn't
    (never raises - it's called while something else is already going wrong)."""
    try:
        os.makedirs(FAILURE_DIR, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "_", reason.lower()).strip("_")[:40]
        name = f"{time.strftime('%Y%m%d_%H%M%S')}_run{CURRENT_RUN_NUMBER}_{slug}.png"
        cv2.imwrite(os.path.join(FAILURE_DIR, name), grab_full_screen())  # the whole screen, to see what has focus too
        for old in sorted(os.listdir(FAILURE_DIR))[:-MAX_FAILURE_SCREENSHOTS]:
            os.remove(os.path.join(FAILURE_DIR, old))
        return name
    except Exception as e:
        print(f"[!] couldn't save a failure screenshot: {e}")
        return None

def write_status():
    """Write RUN_STATS, the currently disabled needs and the time to
    STATUS_PATH (replacing it atomically). Never raises."""
    try:
        status = {**RUN_STATS, "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                  "disabled_needs": sorted(DISABLED_THIS_RUN)}
        tmp = STATUS_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(status, f, indent=2)
        os.replace(tmp, STATUS_PATH)
    except (OSError, TypeError):
        pass

def log_failure(reason, screenshot=True):
    """Record that something went wrong: print it, add it to the run log,
    save a screenshot of what was on screen, and note it in the status
    file. Doesn't change what the macro does next."""
    shot = save_failure_screenshot(reason) if screenshot else None
    detail = f"{reason}" + (f" (screenshot: failures/{shot})" if shot else "")
    print(f"[!] FAILURE: {detail}")
    log_run_event(f"FAILURE: {detail}")
    RUN_STATS["failures"] += 1
    RUN_STATS["last_failure"] = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "reason": reason, "screenshot": shot}
    write_status()

def reset_run_stats():
    """Start the status counters afresh (a workflow was just started)."""
    RUN_STATS.update(run=CURRENT_RUN_NUMBER, started=time.strftime("%Y-%m-%d %H:%M:%S"), cycles=0,
                     last_detected=[], resolved={}, failed={}, failures=0, recoveries=0, disconnects=0, scheduled_rejoins=0, focus_resumes=0,
                     last_resolved=None, last_failure=None)
    write_status()

# ============================================================================
# GAME CONFIG
# ============================================================================
# Game state that needs to persist across separate launches of the script
# (unlike BUTTON_POSITIONS/CURRENT_RUN_NUMBER above) - see side_quest().

def load_game_config():
    """Load GAME_CONFIG_PATH, or these defaults if it doesn't exist yet
    (first run, or reset via reset_game_config())."""
    defaults = {
        "money_collected": 0,
        "lure_timer": time.time(),  # due immediately on a fresh config
        "tree_timer": time.time(),  # likewise
        "side_quest_enabled": True, # whether side_quest() is allowed to run at all
        "resume_on_focus_loss": False,  # whether the loop takes Roblox's focus back instead of stopping
        **{f"{name}_enabled": False for name in MINIGAME_LABELS},  # which Halloween minigames get played (see minigame_popup())
        "private_server_link": "",  # used by leave_and_rejoin(); kept here, not in the source, since it's a join key
    }
    try:
        with open(GAME_CONFIG_PATH, "r") as f:
            config = json.load(f)
    except (FileNotFoundError, ValueError):
        return defaults
    return {**defaults, **config}

def save_game_config(config):
    """Persist `config` (as returned by load_game_config()) to GAME_CONFIG_PATH."""
    with open(GAME_CONFIG_PATH, "w") as f:
        json.dump(config, f)

# The GUI (Tk main thread) and the workflow (run_async's worker thread) both
# write this file, so every change goes through update_game_config() /
# reset_game_config() - a load-modify-save under one lock - rather than
# holding a loaded dict across a long action and saving it afterwards,
# which could silently undo a checkbox clicked in the meantime.
GAME_CONFIG_LOCK = threading.Lock()

def update_game_config(mutate):
    """Load the config, call mutate(config) to change it in place, save it,
    and return it - all atomically with respect to other callers."""
    with GAME_CONFIG_LOCK:
        config = load_game_config()
        mutate(config)
        save_game_config(config)
        return config

def reset_game_config():
    """Delete GAME_CONFIG_PATH so the next load starts from the defaults -
    except the private server link, which isn't progress and is kept."""
    with GAME_CONFIG_LOCK:
        link = load_game_config()["private_server_link"]
        try:
            os.remove(GAME_CONFIG_PATH)
        except FileNotFoundError:
            pass
        if link:
            save_game_config({**load_game_config(), "private_server_link": link})

# ============================================================================
# ICON PROCESSING
# ============================================================================

def preprocess_icon(icon_img):
    """High-contrast black & white version of an icon (color-independent)."""
    gray = cv2.cvtColor(icon_img, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=ICON_CLAHE_CLIP, tileGridSize=ICON_CLAHE_TILE)
    enhanced = clahe.apply(gray)
    _, binary = cv2.threshold(enhanced, ICON_BW_THRESHOLD, PIXEL_MAX, cv2.THRESH_BINARY)
    return binary

def icon_signature(icon_img):
    """Reduced, comparable form of an icon: preprocessed, resized to
    ICON_COMPARE_SIZE, scaled to 0-1, and (if MATCH_ONLY_LEFT_HALF) cut down
    to its left half so an overlapping event badge doesn't matter."""
    proc = cv2.resize(preprocess_icon(icon_img), ICON_COMPARE_SIZE).astype(np.float32) / PIXEL_MAX
    if MATCH_ONLY_LEFT_HALF:
        proc = proc[:, :proc.shape[1] // 2]
    return proc

def compare_signatures(sig1, sig2):
    """Return a 0-1 similarity score (1 = identical) between two signatures."""
    return 1.0 - float(np.mean((sig1 - sig2) ** 2))

# ============================================================================
# NEED ICON DETECTION
# ============================================================================

def extract_icon(img, cx, cy, radius, padding=ICON_EXTRACT_PADDING):
    r = radius + padding
    x0, x1 = max(0, cx - r), min(img.shape[1], cx + r)
    y0, y1 = max(0, cy - r), min(img.shape[0], cy + r)
    return img[y0:y1, x0:x1].copy()

def detect_need_icons(save_debug=False):
    """Detect the circular need icons at the top of the screen. Icons are
    found by their round outline (Hough circles, within a fixed radius
    range), not by color, so event badges overlapping an icon, differently
    colored icons and busy backgrounds don't affect detection. Returns
    ((cx, cy, radius) per icon, left to right) and the full screenshot."""
    img = grab_screen()
    h, w = img.shape[:2]

    top_strip = img[0:int(h * NEED_ICON_TOP_PERCENT), 0:int(w * NEED_ICON_WIDTH_PERCENT)].copy()
    top_strip[0:int(h * NEED_ICON_BLANK_HEIGHT), 0:int(w * NEED_ICON_BLANK_WIDTH)] = 0

    gray = cv2.medianBlur(cv2.cvtColor(top_strip, cv2.COLOR_BGR2GRAY), NEED_ICON_BLUR_KERNEL)
    circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, dp=1, minDist=NEED_ICON_MIN_DISTANCE,
                               param1=NEED_ICON_HOUGH_EDGE, param2=NEED_ICON_HOUGH_VOTES,
                               minRadius=NEED_ICON_MIN_RADIUS, maxRadius=NEED_ICON_MAX_RADIUS)
    found = [] if circles is None else sorted((int(x), int(y), int(r)) for x, y, r in circles[0])

    if save_debug:
        debug_img = top_strip.copy()
        for cx, cy, r in found:
            cv2.circle(debug_img, (cx, cy), r, DEBUG_NEED_MARKER_COLOR, DEBUG_NEED_MARKER_THICKNESS)
        cv2.imwrite(os.path.join(DEBUG_DIR, "debug_needs.png"), debug_img)

    return found, img

def icon_variants(img, cx, cy):
    """Crops of the icon centered within +/- ICON_SHIFT_TOLERANCE px of
    (cx, cy), so a detection that's a pixel or two off-center still lines up
    with the saved reference."""
    shifts = range(-ICON_SHIFT_TOLERANCE, ICON_SHIFT_TOLERANCE + 1)
    return [extract_icon(img, cx + dx, cy + dy, ICON_CROP_RADIUS) for dx in shifts for dy in shifts]

# (need file paths and modification times, [(name, signature)]) - see need_signatures().
_NEED_SIGNATURES = (None, [])

def need_signatures():
    """[(need name, signature)] for every saved need icon. Reading and
    preprocessing every .png in NEEDS_DIR is far too slow to repeat for each
    icon on each check, so it's done once and redone only when the files
    change (checking their modification times is cheap), which also picks up
    an icon added while the macro runs. Recurses, so related icons can sit in
    subfolders (e.g. needs/weather/) - a need's name is just its file name,
    wherever it lives."""
    global _NEED_SIGNATURES
    files = sorted(os.path.join(root, f) for root, _, names in os.walk(NEEDS_DIR) for f in names if f.endswith('.png'))
    key = tuple((f, os.path.getmtime(f)) for f in files)
    if _NEED_SIGNATURES[0] != key:
        loaded = []
        for f in files:
            icon = cv2.imread(f)
            if icon is not None:
                loaded.append((os.path.splitext(os.path.basename(f))[0], icon_signature(icon)))
        _NEED_SIGNATURES = (key, loaded)
    return _NEED_SIGNATURES[1]

def find_matching_need(icon_variants):
    """Compare a detected icon (its icon_variants() crops) against the saved
    needs (need_signatures()), scoring each by its best-aligned variant.
    Returns (name, score)."""
    live_signatures = [icon_signature(v) for v in icon_variants]
    best_match, best_score = None, 0.0
    for name, saved_signature in need_signatures():
        score = max(compare_signatures(sig, saved_signature) for sig in live_signatures)
        if score > best_score:
            best_match, best_score = name, score

    if best_score >= ICON_MATCH_THRESHOLD:
        return best_match, best_score
    return None, best_score

def prompt_rename_need(icon_img, idx):
    """Save a new need icon (as high-contrast B/W) and ask the user to name
    it - in a dialog when the GUI is up (the worker thread has no console to
    read from), otherwise on the console. Cancelling or an empty name saves
    it as unnamed_<idx>."""
    print("\n[!] NEW NEED DETECTED!")
    temp_path = os.path.join(NEEDS_DIR, f"temp_{idx}.png")
    cv2.imwrite(temp_path, preprocess_icon(icon_img))

    prompt = "Need name (hungry/thirsty/dirty/potty/sleepy/catch/walk/other):"
    if MACRO_WINDOW is not None:
        answer = MACRO_WINDOW.run_on_ui_thread(
            lambda: simpledialog.askstring("New need", prompt, parent=MACRO_WINDOW.root), timeout=None)
        focus_roblox_click()   # the dialog took the focus
    else:
        print(f"[!] {prompt} ", end="", flush=True)
        answer = input()
    need_name = (answer or "").strip().lower() or f"unnamed_{idx}"

    os.rename(temp_path, os.path.join(NEEDS_DIR, f"{need_name}.png"))
    print(f"[!] Saved: {need_name}")
    return need_name

def identify_icons(found_icons, full_img):
    """Match each detected icon against the saved needs. Yields
    (index, icon_image, need_name_or_None, score) per icon, checking
    check_running() between icons."""
    for idx, (cx, cy, _) in enumerate(found_icons):
        check_running()
        icon_img = extract_icon(full_img, cx, cy, ICON_CROP_RADIUS)
        need_name, score = find_matching_need(icon_variants(full_img, cx, cy))
        yield idx, icon_img, need_name, score

def need_bar_readable(img):
    """False if the strip the icons sit in is mostly near-white in `img`
    (NEED_BAR_BRIGHT_LEVEL / NEED_BAR_MAX_BRIGHT_FRACTION) - the Pizza Party's
    bright background, say. The icons can't be recognised against that, so
    a missing icon there says nothing about whether the need is gone."""
    h, w = img.shape[:2]
    strip = img[0:int(h * NEED_ICON_TOP_PERCENT), int(w * NEED_ICON_BLANK_WIDTH):int(w * NEED_ICON_WIDTH_PERCENT)]
    bright = np.count_nonzero(cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY) > NEED_BAR_BRIGHT_LEVEL)
    return bright / (strip.shape[0] * strip.shape[1]) < NEED_BAR_MAX_BRIGHT_FRACTION

def scan_needs():
    """One-shot snapshot: (every need name currently detected on screen, whether
    the icons could be read at all - see need_bar_readable())."""
    found_icons, full_img = detect_need_icons()
    if not need_bar_readable(full_img):
        return [], False
    return [name for _, _, name, _ in identify_icons(found_icons, full_img)], True

def _need_cleared(need_name):
    """True if `need_name`'s icon is missing on two quick checks in a row
    (NEED_GONE_FLICKER_RECHECK_DELAY apart) - the single-frame-flicker
    guard shared by wait_until_need_gone() and _watch_need_gone() below.
    None if the icons couldn't be read (see need_bar_readable()): that's
    neither cleared nor not cleared."""
    for attempt in range(2):
        names, readable = scan_needs()
        if not readable:
            return None
        if need_name in names:
            return False
        if attempt == 0:
            wait_interruptible(NEED_GONE_FLICKER_RECHECK_DELAY)
    return True

def wait_until_need_gone(need_name, max_wait=NEED_GONE_MAX_WAIT, poll_interval=NEED_GONE_POLL_INTERVAL):
    """Wait for `need_name`'s icon to stop being detected, for at most
    `max_wait` seconds - whichever happens first. The icon must be missing
    NEED_GONE_CONFIRMATIONS checks in a row (any sighting resets the count),
    so a single missed detection can't end the wait early - see
    _need_cleared() for how each individual check is itself debounced.
    While the icons can't be read (a bright background, see
    need_bar_readable()) nothing counts as a miss, so that wait runs its
    full length - and ending it isn't logged as a failure. (Popups and
    minigames that turn up meanwhile are handled by the unscrew() checks
    wait_interruptible() runs during a task.) Interruptible."""
    print(f"[debug] waiting up to {max_wait}s for {need_name} to clear...")
    deadline = time.time() + max_wait
    misses = 0
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
        wait_interruptible(min(NEED_GONE_CONFIRM_INTERVAL if misses else poll_interval, remaining))
        cleared = _need_cleared(need_name)
        unreadable = cleared is None
        if not cleared:
            misses = 0
            continue

        misses += 1
        print(f"[debug] {need_name} not detected ({misses}/{NEED_GONE_CONFIRMATIONS})")
        if misses >= NEED_GONE_CONFIRMATIONS:
            print(f"[debug] {need_name} cleared")
            return

def _watch_need_gone(need_name, cleared_event, stop_event):
    """Background-thread body for walk_alternating()'s optional `need_name`:
    polls whether the need has cleared *while* the character is still
    moving, using the same miss-confirmation (NEED_GONE_CONFIRMATIONS,
    debounced by _need_cleared()) wait_until_need_gone() uses standalone -
    just running alongside the movement instead of after it. Sets
    `cleared_event` the moment it's confirmed. Exits quietly - without
    raising - the instant `stop_event` is set (the walk ended on its own)
    or STOP_FLAG/focus loss is noticed: only the main thread's
    check_running() calls are allowed to unwind the workflow, so this
    thread must never let StopRequested/FocusLost escape uncaught."""
    misses = 0
    try:
        while not stop_event.is_set():
            if stop_event.wait(NEED_GONE_CONFIRM_INTERVAL):
                return
            if _need_cleared(need_name) is not True:
                misses = 0
                continue
            misses += 1
            if misses >= NEED_GONE_CONFIRMATIONS:
                cleared_event.set()
                return
    except (StopRequested, FocusLost):
        return

# ============================================================================
# CLICKING
# ============================================================================

def hover_move(x, y, duration=CLICK_MOVE_DURATION):
    """Move the mouse to (x, y) - a reference-space position, see to_screen() -
    over `duration` seconds using SendInput
    (pydirectinput), then nudge it a couple of pixels back onto the target.
    Roblox ignores pyautogui's SetCursorPos warps as hover movement and only
    reacts to real input events, so this is what makes a UI element register
    as hovered before it's clicked."""
    x, y = to_screen(x, y)
    pydirectinput.moveTo(x, y, duration=duration)
    time.sleep(CLICK_SETTLE_DELAY)
    pydirectinput.moveTo(min(pyautogui.size()[0] - 1, x + HOVER_NUDGE_PIXELS), y)
    time.sleep(CLICK_SETTLE_DELAY)
    pydirectinput.moveTo(x, y)
    time.sleep(CLICK_SETTLE_DELAY)

def hover_click(x, y, duration=CLICK_MOVE_DURATION):
    """hover_move() to (x, y), then click."""
    hover_move(x, y, duration)
    pydirectinput.click()
    time.sleep(POST_CLICK_DELAY)

# While a need handler (or setup) runs - see task_unscrew() - every
# wait_interruptible() also runs the per-cycle checks (unscrew()) now and
# then, so a popup, a stray window or a minigame doesn't wait for the handler
# to finish. Only on the thread that started the task, and never from inside
# unscrew() itself.
TASK_ACTIVE = False
TASK_PLAY_MINIGAMES = True
TASK_THREAD = None
_IN_UNSCREW = False
_LAST_TASK_UNSCREW = 0.0

@contextlib.contextmanager
def task_unscrew(play_minigames=True):
    """Within this block wait_interruptible() runs unscrew(in_task=True) every
    UNSCREW_TASK_INTERVAL seconds. With `play_minigames` False (setup) an
    offered minigame is only declined, never played - setup can't be
    abandoned halfway, since its clicks toggle things."""
    global TASK_ACTIVE, TASK_PLAY_MINIGAMES, TASK_THREAD, _LAST_TASK_UNSCREW
    previous = (TASK_ACTIVE, TASK_PLAY_MINIGAMES, TASK_THREAD)
    TASK_ACTIVE, TASK_PLAY_MINIGAMES, TASK_THREAD = True, play_minigames, threading.get_ident()
    _LAST_TASK_UNSCREW = time.time()
    try:
        yield
    finally:
        TASK_ACTIVE, TASK_PLAY_MINIGAMES, TASK_THREAD = previous

def task_unscrew_tick():
    """The hook in wait_interruptible(): run the in-task checks if one is due.
    Raises TaskInterrupted if a minigame was played."""
    global _IN_UNSCREW, _LAST_TASK_UNSCREW
    if (not TASK_ACTIVE or _IN_UNSCREW or threading.get_ident() != TASK_THREAD
            or time.time() - _LAST_TASK_UNSCREW < UNSCREW_TASK_INTERVAL):
        return
    _IN_UNSCREW = True
    try:
        played = unscrew(in_task=True, play_minigames=TASK_PLAY_MINIGAMES)
    finally:
        _IN_UNSCREW = False
        _LAST_TASK_UNSCREW = time.time()
    if played == "played":
        raise TaskInterrupted()

def wait_interruptible(duration):
    """Sleep for `duration`, in STOP_CHECK_INTERVAL chunks, checking
    check_running() between each chunk. Raises StopRequested or FocusLost
    the moment either condition is noticed, instead of returning a bool the
    caller has to check after every call."""
    elapsed = 0.0
    while elapsed < duration:
        check_running()
        task_unscrew_tick()
        sleep_chunk = min(STOP_CHECK_INTERVAL, duration - elapsed)
        time.sleep(sleep_chunk)
        elapsed += sleep_chunk
    check_running()

def wait_stoppable(duration):
    """Like wait_interruptible() but only [STOP] interrupts it, not Roblox
    losing focus. Used while the game is being closed and relaunched, when
    there is no focused Roblox window to lose."""
    end = time.time() + duration
    while time.time() < end:
        check_stop()
        time.sleep(min(STOP_CHECK_INTERVAL, max(0.0, end - time.time())))
    check_stop()

def release_all_inputs():
    """Best-effort safety net: release every key this macro ever holds down,
    plus the mouse button. The try/finally blocks around each individual
    held key/button should already guarantee this, but this is called once
    more whenever a background task ends (run_async's worker finally block)
    as a last line of defense - a bug in a handler we haven't caught yet
    still shouldn't be able to leave an input stuck down in the game."""
    for key in (*MOVE_KEYS, KEY_HELICOPTER):
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

def detect_buttons(save_debug=False):
    """Detect purple, white-outlined action buttons in the middle of the screen.
    Assumes the character is already positioned where the buttons are visible."""
    if not focus_roblox_click():
        return []

    img = grab_screen()
    h, w = img.shape[:2]
    y0, y1 = int(h * BUTTON_BAND_Y[0]), int(h * BUTTON_BAND_Y[1])
    x0, x1 = int(w * BUTTON_BAND_X[0]), int(w * BUTTON_BAND_X[1])
    band = img[y0:y1, x0:x1]
    band_h, band_w = band.shape[:2]
    hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)

    purple_mask = cv2.inRange(hsv, *PURPLE_RANGE)
    white_mask = cv2.inRange(hsv, *WHITE_RANGE)

    purple_dilated = cv2.dilate(purple_mask, None, iterations=BUTTON_PURPLE_DILATE_ITERATIONS)
    outline_zone = cv2.subtract(purple_dilated, cv2.erode(purple_mask, None, iterations=BUTTON_PURPLE_ERODE_ITERATIONS))
    has_white = cv2.bitwise_and(outline_zone, white_mask)

    contours, _ = cv2.findContours(purple_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    print(f"[debug] button scan: {len(contours)} raw purple contour(s) in search band")

    candidates = []
    rejected_area, rejected_radius, rejected_circularity = 0, 0, 0
    for c in contours:
        area = cv2.contourArea(c)
        if area < BUTTON_MIN_AREA * BUTTON_LOOSE_AREA_FACTOR:
            rejected_area += 1
            continue
        if area > band_w * band_h * BUTTON_MAX_AREA_FRACTION:  # reject blobs covering most of the band (not a button)
            rejected_area += 1
            continue

        _, radius = cv2.minEnclosingCircle(c)
        if radius < BUTTON_MIN_RADIUS:
            rejected_radius += 1
            continue

        circularity = area / (np.pi * radius * radius + CIRCULARITY_EPSILON)
        if circularity < BUTTON_MIN_CIRCULARITY * BUTTON_LOOSE_CIRCULARITY_FACTOR:
            rejected_circularity += 1
            continue

        x, y, cw, ch = cv2.boundingRect(c)
        cx, cy = x + cw // 2, y + ch // 2
        pad = BUTTON_OUTLINE_PADDING
        roi = has_white[max(0, y - pad):y + ch + pad, max(0, x - pad):x + cw + pad]
        white_score = cv2.countNonZero(roi)
        candidates.append((cx + x0, cy + y0, area, white_score))

    print(f"[debug] button scan: {len(candidates)} passed filters "
          f"(rejected: {rejected_area} area, {rejected_radius} radius, {rejected_circularity} circularity)")

    # Prefer candidates with a white outline, then by size; final order is left-to-right
    candidates.sort(key=lambda t: (t[3] > 0, t[2]), reverse=True)
    candidates = sorted(candidates[:BUTTON_MAX_COUNT], key=lambda t: t[0])
    positions = [(cx, cy) for cx, cy, _, _ in candidates]

    if save_debug:
        debug_img = img.copy()
        for i, (cx, cy) in enumerate(positions):
            cv2.circle(debug_img, (cx, cy), DEBUG_BUTTON_MARKER_RADIUS,
                       DEBUG_BUTTON_MARKER_COLOR, DEBUG_BUTTON_MARKER_THICKNESS)
            label_pos = (cx + DEBUG_BUTTON_LABEL_OFFSET[0], cy + DEBUG_BUTTON_LABEL_OFFSET[1])
            cv2.putText(debug_img, str(i + 1), label_pos, cv2.FONT_HERSHEY_SIMPLEX,
                        DEBUG_BUTTON_LABEL_SCALE, DEBUG_BUTTON_MARKER_COLOR, DEBUG_BUTTON_MARKER_THICKNESS)
        cv2.imwrite(os.path.join(DEBUG_DIR, "debug_buttons.png"), debug_img)

    if not positions:
        print("[!] No buttons detected. Check debug_buttons.png in the debug/ folder to see "
              "whether PURPLE_RANGE / BUTTON_BAND_X / BUTTON_BAND_Y need adjusting.")

    return positions

def click_button(button_x, button_y, need_name):
    """Focus the game, then click a mapped need button with a hover click."""
    if not focus_roblox():
        return False
    time.sleep(FOCUS_DELAY)
    print(f"[debug] clicking {need_name} at ({button_x}, {button_y})")
    hover_click(button_x, button_y)
    return True

def refresh_button_mapping():
    """Detect the current action button positions and cache them in
    BUTTON_POSITIONS. Assumes the character is already at the buttons.
    Always clears any previous mapping first, even on failure, so a failed
    detection can never leave a stale (possibly wrong) position behind for
    click_need_button() to use."""
    BUTTON_POSITIONS.clear()

    positions = detect_buttons(save_debug=True)
    if not positions:
        print("[!] ERROR: No buttons detected!")
        respawn_character()
        return False

    print(f"\n[!] DETECTED {len(positions)} BUTTON(S)")
    for i, (cx, cy) in enumerate(positions):
        if i < len(BUTTON_NAMES):
            name = BUTTON_NAMES[i]
            BUTTON_POSITIONS[name] = (cx, cy)
            print(f"[!] Button {i + 1}: '{name}' @ ({cx}, {cy})")

    print("\n[!] BUTTON MAPPING REFRESHED\n")
    return True

def click_need_button(need_name):
    """Click the action button mapped to `need_name` (see ButtonNeedHandler),
    then wait for its icon to actually clear (wait_until_need_gone()) rather
    than guessing how long that takes. Assumes refresh_button_mapping() has
    already been called this cycle. Returns True if a mapped button was
    actually found and clicked, False otherwise - the caller must not treat
    this need as resolved (or respawn) on a False return."""
    if need_name not in BUTTON_POSITIONS:
        print(f"[!] WARNING: no button mapped for '{need_name}'")
        return False
    button_x, button_y = BUTTON_POSITIONS[need_name]
    print(f"[!] CLICKING: {need_name}")
    if not click_button(button_x, button_y, need_name):
        return False
    wait_until_need_gone(need_name)
    return True

# ============================================================================
# MOVEMENT
# ============================================================================

def respawn_character():
    """Respawn the character (ESC, R, ENTER) and wait for it to settle."""
    print("[debug] respawning...")
    if not focus_roblox_click():
        return
    for key in RESPAWN_KEYS:
        pydirectinput.keyDown(key)
        time.sleep(RESPAWN_KEY_DURATION)
        pydirectinput.keyUp(key)
        time.sleep(RESPAWN_KEY_DURATION)
    wait_interruptible(RESPAWN_WAIT)
    print("[debug] respawn complete")

def hold_key(key, duration):
    """Hold `key` down for `duration` seconds (interruptibly). Always
    releases the key, even if StopRequested/FocusLost fires mid-hold -
    otherwise it would stay stuck held down in the game."""
    pydirectinput.keyDown(key)
    try:
        wait_interruptible(duration)
    finally:
        pydirectinput.keyUp(key)

def walk_to_buttons():
    """Walk forward from the respawn spot to where the action buttons are."""
    if not focus_roblox():
        return
    print("[debug] walking to buttons...")
    hold_key("w", WALK_TO_BUTTONS_DURATION)
    wait_interruptible(UI_SETTLE)
    print("[debug] arrived at buttons")

def walk_alternating(direction_pair, total_duration, step_duration=WALK_ALTERNATING_STEP, need_name=None):
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
            wait_interruptible(KEY_STEP_GAP)

            direction_idx += 1
            elapsed = time.time() - start_time
    finally:
        stop_event.set()
        if watcher is not None:
            watcher.join(timeout=NEED_WATCH_JOIN_TIMEOUT)

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

def lure_collect():
    """Walk to the lure, collect its rewards, then set a new one
    (set_new_lure())."""
    if not focus_roblox():
        return False
    print(f"[debug] walking to the lure for {LURE_COLLECT_WALK_DURATION}s...")
    hold_key("a", LURE_COLLECT_WALK_DURATION)
    pydirectinput.press(KEY_INTERACT)
    wait_interruptible(LURE_COLLECT_SETTLE_DELAY)
    pydirectinput.press(KEY_INTERACT)
    set_new_lure()
    respawn_character()
    print("[!] Lure collect complete!")
    return True

def set_new_lure():
    """Click the two backpack positions that place a fresh lure, once the
    current one's rewards have been collected. Split out from
    lure_collect() so the placement sequence alone can be tested/tuned
    without walking to the old lure first."""
    if not focus_roblox():
        return False
    hover_click(*LURE_NEW_POS_1)
    hover_click(*LURE_NEW_POS_2)
    print("[!] New lure set!")
    return True

def tree_collect():
    """Walk to the money tree, step back to line up with it, then harvest it."""
    if not focus_roblox():
        return False
    print(f"[debug] walking to the money tree for {TREE_COLLECT_WALK_DURATION}s...")
    hold_key("d", TREE_COLLECT_WALK_DURATION)
    print(f"[debug] stepping back for {TREE_COLLECT_BACKWARD_DURATION}s...")
    hold_key("s", TREE_COLLECT_BACKWARD_DURATION)
    pydirectinput.press(KEY_INTERACT)
    respawn_character()
    print("[!] Tree collect complete!")
    return True

def setup_game():
    """Setup: lock the house, set the backpack's item filter to favorites
    only, then disable trades (disable_trades()). Not part of the cycle -
    only run on demand (the GUI's Setup button), and after a rejoin. Stray
    windows are closed first - a join opens the daily Star Rewards, whose
    window used to eat every click here, leaving the favorites filter unset -
    and the in-task checks run throughout (minigames only declined)."""
    with task_unscrew(play_minigames=False):
        return _setup_steps()

def _setup_steps():
    for _ in range(SETUP_STRAY_TRIES):
        if not dismiss_stray_windows():
            break
    respawn_character()
    print("[debug] locking house...")
    hover_click(*SETUP_LOCK_HOUSE_POS)

    print("[debug] opening backpack...")
    pydirectinput.press(KEY_BACKPACK)
    wait_interruptible(UI_SETTLE)
    print("[debug] opening settings...")
    hover_click(*SETUP_BACKPACK_SETTINGS_POS)
    wait_interruptible(UI_SETTLE)
    print("[debug] opening sort menu...")
    hover_click(*SETUP_SORT_MENU_POS)
    wait_interruptible(UI_SETTLE)
    print("[debug] selecting favorites...")
    hover_click(*SETUP_FAVORITES_POS)
    wait_interruptible(UI_SETTLE)
    print("[debug] confirming...")
    hover_click(*SETUP_CONFIRM_POS)
    wait_interruptible(UI_SETTLE)
    print("[debug] closing backpack...")
    pydirectinput.press(KEY_BACKPACK)
    wait_interruptible(UI_SETTLE)

    disable_trades()

    print("[!] Setup complete!")
    return True

def send_macro_window_to_back():
    """Drop the macro's own window behind everything (it's normally
    always-on-top and covers part of the Roblox UI). Safe to call from the
    worker thread: the change runs on Tk's thread and this waits for it."""
    if MACRO_WINDOW is not None:
        MACRO_WINDOW.run_on_ui_thread(MACRO_WINDOW.send_to_back)

def bring_macro_window_to_front():
    """Undo send_macro_window_to_back()."""
    if MACRO_WINDOW is not None:
        MACRO_WINDOW.run_on_ui_thread(MACRO_WINDOW.bring_to_front)

def disable_trades():
    """Set the game's trade setting to "no one". The macro window covers the
    settings gear, so it's sent to the back for the duration and always
    brought back, even if the run is stopped partway."""
    print("[debug] sending the macro window to the back...")
    send_macro_window_to_back()
    try:
        wait_interruptible(UI_SETTLE)
        if not focus_roblox():
            return False
        for label, pos in (("settings", SETUP_TRADES_SETTINGS_POS),
                           ("settings menu", SETUP_TRADES_MENU_POS),
                           ("interaction tab", SETUP_TRADES_INTERACTION_TAB_POS),
                           ("trading setting", SETUP_TRADES_SETTING_POS),
                           ("no one", SETUP_TRADES_NO_ONE_POS),
                           ("closing settings", SETUP_TRADES_CLOSE_POS)):
            print(f"[debug] {label}...")
            hover_click(*pos)
            wait_interruptible(UI_SETTLE)
    finally:
        print("[debug] bringing the macro window back...")
        bring_macro_window_to_front()
    print("[!] Trades disabled!")
    return True

def parse_server_link(link):
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

def run_deeplink(server=None):
    """Start Roblox on the game through a roblox:// deeplink: onto the given
    (type, code) private server, or a public server if `server` is None."""
    if server is None:
        uri = f"roblox://placeID={ROBLOX_PLACE_ID}"
    elif server[0] == "LinkCode":
        uri = f"roblox://placeID={ROBLOX_PLACE_ID}&linkcode={server[1]}"
    else:
        uri = f"roblox://navigation/share_links?code={server[1]}&type=Server"
    os.startfile(uri)

def close_roblox(clean_leave=True):
    """Leave the game properly (esc, l, enter - only if `clean_leave`, and
    only if the window is tall enough for L to work), then kill any Roblox
    process still around and wait REJOIN_CLOSE_WAIT. Nothing here needs
    Roblox to stay focused. `clean_leave` is off when the game is already
    gone or showing the Disconnected dialog, where those keys do nothing
    useful and Enter could press Reconnect."""
    window = roblox_window()
    if clean_leave and window is not None and window.height >= REJOIN_MIN_LEAVE_HEIGHT and focus_roblox():
        print("[debug] leaving the game...")
        for key in REJOIN_LEAVE_KEYS:
            pydirectinput.press(key)
            wait_stoppable(REJOIN_LEAVE_KEY_GAP)
    print("[debug] closing Roblox...")
    for name in ROBLOX_PROCESS_NAMES:
        subprocess.run(["taskkill", "/F", "/IM", name], capture_output=True)
    wait_stoppable(REJOIN_CLOSE_WAIT)

def play_button_visible(img):
    """True if the game's green Play button is on screen around
    REJOIN_JOIN_POS in `img` - the sign that the game has loaded."""
    x, y = REJOIN_JOIN_POS
    half_w, half_h = REJOIN_PLAY_BOX
    box = img[max(0, y - half_h):y + half_h, max(0, x - half_w):x + half_w]
    mask = exact_color_mask(box, REJOIN_PLAY_COLOR, REJOIN_PLAY_COLOR_TOLERANCE)
    return int(np.count_nonzero(mask)) >= REJOIN_PLAY_MIN_PIXELS

def leave_and_rejoin(clean_leave=True):
    """Close Roblox, relaunch the game through a deeplink (to the private
    server saved in the GUI, or a public one if none is saved), wait for it
    to load, click Play, wait REJOIN_AFTER_JOIN_WAIT and respawn - see the
    REJOIN_* settings. Retries the whole thing up to REJOIN_MAX_ATTEMPTS
    times. Run from the GUI's Leave & rejoin button and by rejoin_game().
    Returns True once back in the game, False if every attempt failed (or
    the saved link isn't a valid private server link). `clean_leave` is
    passed on to close_roblox()."""
    global LAST_REJOIN, LAST_PROGRESS
    link = load_game_config()["private_server_link"].strip()
    server = None
    if link:
        server = parse_server_link(link)
        if server is None:
            print("[!] Rejoin failed: the saved private server link isn't recognised")
            return False
    else:
        print("[!] No private server link saved - rejoining a public server")

    for attempt in range(1, REJOIN_MAX_ATTEMPTS + 1):
        print(f"[debug] rejoin attempt {attempt}/{REJOIN_MAX_ATTEMPTS}")
        close_roblox(clean_leave)
        run_deeplink(server)

        print("[debug] waiting for the Roblox window...")
        deadline = time.time() + REJOIN_WINDOW_TIMEOUT
        while not roblox_windows() and time.time() < deadline:
            wait_stoppable(REJOIN_POLL_INTERVAL)
        if not roblox_windows():
            print("[!] No Roblox window appeared")
            continue

        print("[debug] waiting for the game to load...")
        loaded = False
        deadline = time.time() + REJOIN_LOAD_TIMEOUT
        while time.time() < deadline:
            if roblox_windows():
                focus_roblox()
                if play_button_visible(grab_screen()):
                    loaded = True
                    break
            wait_stoppable(REJOIN_POLL_INTERVAL)
        if not loaded:
            print("[!] The game didn't finish loading")
            continue

        print("[debug] game loaded, clicking play...")
        wait_stoppable(REJOIN_PLAY_SETTLE)
        focus_roblox()
        hover_click(*REJOIN_JOIN_POS)
        print(f"[debug] waiting {REJOIN_AFTER_JOIN_WAIT}s, then respawning...")
        wait_stoppable(REJOIN_AFTER_JOIN_WAIT)
        respawn_character()
        LAST_REJOIN = LAST_PROGRESS = time.time()
        print("[!] Rejoin complete!")
        return True

    print(f"[!] Rejoin failed after {REJOIN_MAX_ATTEMPTS} attempts")
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

def moving_blobs(img_a, img_b, top_percent=FOCUS_PET_REGION_TOP_PERCENT):
    """Compare two same-size BGR screenshots over the bottom (1 - top_percent)
    of the screen and return ([(x, y, w, h, area) per blob of pixels that
    changed between them, largest first, in screen coordinates], the change
    mask of the region, its top row). Pure image logic, no input or waiting,
    so it can be tested on its own."""
    top = int(img_a.shape[0] * top_percent)
    diff = cv2.absdiff(cv2.cvtColor(img_a[top:], cv2.COLOR_BGR2GRAY),
                       cv2.cvtColor(img_b[top:], cv2.COLOR_BGR2GRAY))
    mask = (diff > FOCUS_PET_DIFF_THRESHOLD).astype(np.uint8) * PIXEL_MAX
    # An up/down bob only changes the pet's top and bottom edges, so close
    # the gap between them to get one blob per moving thing.
    kernel = np.ones((FOCUS_PET_MERGE_KERNEL, FOCUS_PET_MERGE_KERNEL), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    blobs = []
    for contour in sorted(contours, key=cv2.contourArea, reverse=True):
        area = cv2.contourArea(contour)
        if area < FOCUS_PET_MIN_AREA:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        blobs.append((x, top + y, w, h, int(area)))
    return blobs, mask, top

def find_moving_blobs(img_a, img_b, top_percent=FOCUS_PET_REGION_TOP_PERCENT):
    """The (x, y) center of every blob moving_blobs() finds, largest first."""
    blobs, _, _ = moving_blobs(img_a, img_b, top_percent)
    return [(x + w // 2, y + h // 2) for x, y, w, h, _ in blobs]

def save_focus_pet_debug(img, blobs, mask, top, name="debug_focus_pet.png"):
    """Write debug/<name>: `img` (the second frame) with the searched region
    marked, the changed pixels in red, and a numbered box around every blob -
    so what focus_pet() thinks moved, and so where it clicks, can be seen."""
    marked = img.copy()
    region = marked[top:]
    region[mask > 0] = (0, 0, 255)
    cv2.line(marked, (0, top), (marked.shape[1], top), FOCUS_PET_DEBUG_COLOR, 1)
    for i, (x, y, w, h, area) in enumerate(blobs, 1):
        cv2.rectangle(marked, (x, y), (x + w, y + h), FOCUS_PET_DEBUG_COLOR, 2)
        cv2.putText(marked, f"{i}: {area}", (x, max(12, y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, FOCUS_PET_DEBUG_COLOR, 2)
    cv2.imwrite(os.path.join(DEBUG_DIR, name), marked)

def focus_pet(click_duration=CLICK_MOVE_DURATION):
    """Click the pet to open its interaction menu. The pet is found by
    movement: two screenshots of the bottom of the screen FOCUS_PET_FRAME_GAP
    apart, then a click on the center of everything that moved (see
    moving_blobs()). What it saw is printed and written to
    debug/debug_focus_pet.png (the blobs, marked) - and what the screen looked
    like after the clicks to debug/debug_focus_pet_after.png. Returns True if
    anything was clicked, False if nothing moved."""
    img_a = grab_screen()
    wait_interruptible(FOCUS_PET_FRAME_GAP)
    img_b = grab_screen()
    blobs, mask, top = moving_blobs(img_a, img_b)
    changed = int(np.count_nonzero(mask))
    print(f"[debug] focus_pet: {len(blobs)} moving blob(s), {changed} changed px below y={top}")
    for i, (x, y, w, h, area) in enumerate(blobs, 1):
        print(f"[debug] focus_pet:   #{i} center ({x + w // 2}, {y + h // 2}), {w}x{h}, area {area}")
    save_focus_pet_debug(img_b, blobs, mask, top)
    if not blobs:
        print("[debug] focus_pet: nothing moved, pet not found (see debug/debug_focus_pet.png)")
        return False
    for i, (x, y, w, h, _) in enumerate(blobs, 1):
        print(f"[debug] focus_pet: clicking #{i} at ({x + w // 2}, {y + h // 2})...")
        hover_click(x + w // 2, y + h // 2, duration=click_duration)
    wait_interruptible(FOCUS_PET_MENU_WAIT)
    after = grab_screen()
    save_focus_pet_debug(after, blobs, np.zeros_like(mask), top, name="debug_focus_pet_after.png")
    return True

def equip_favorite_vehicle():
    """Open the backpack, select the first vehicle (the favorite, once
    setup_game() has filtered the backpack to favorites) and equip it, then
    close the backpack. Shared by ride and the helicopter step of
    teleport-walk needs."""
    print("[debug] opening backpack...")
    pydirectinput.press(KEY_BACKPACK)
    wait_interruptible(UI_SETTLE)

    print("[debug] opening vehicles...")
    hover_click(*RIDE_VEHICLES_POS)
    wait_interruptible(UI_SETTLE)

    print("[debug] selecting first vehicle...")
    hover_click(*RIDE_FIRST_VEHICLE_POS)
    wait_interruptible(UI_SETTLE)

    print("[debug] equipping vehicle...")
    hover_click(*RIDE_EQUIP_POS)
    wait_interruptible(UI_SETTLE)

    print("[debug] closing backpack...")
    pydirectinput.press(KEY_BACKPACK)
    wait_interruptible(UI_SETTLE)

class NeedHandler(ABC):
    """Reacts to one detected need. Subclass this to add a new need."""

    @abstractmethod
    def handle(self):
        """Perform the action for this need. Return True if it was handled."""
        raise NotImplementedError

class ButtonNeedHandler(NeedHandler):
    """Walks to the action buttons and clicks the one matching `name`
    (hungry/thirsty/dirty/potty/sleepy)."""

    def __init__(self, name):
        self.name = name

    def handle(self):
        walk_to_buttons()
        if not refresh_button_mapping():
            return False
        return click_need_button(self.name)

class WalkNeedHandler(NeedHandler):
    """The 'walk' need is satisfied by walking left-right for a while -
    or less, the moment its icon is confirmed gone (see walk_alternating())."""

    def handle(self):
        print("[!] WALK NEED")
        walk_alternating(("a", "d"), NEED_GONE_MAX_WAIT, need_name="walk")
        return True

class CatchNeedHandler(NeedHandler):
    """The 'catch' need requires opening backpack, equipping a toy, then throwing it."""

    def handle(self):
        print("[!] CATCH NEED")
        if not focus_roblox():
            return False

        # Open backpack with 'b' key
        print("[debug] opening backpack...")
        pydirectinput.press(KEY_BACKPACK)
        wait_interruptible(UI_SETTLE)

        # Navigate to toys
        print("[debug] opening toys...")
        hover_click(*CATCH_TOYS_POS)
        wait_interruptible(UI_SETTLE)

        # Click squeaky toy
        print("[debug] selecting squeaky toy...")
        hover_click(*CATCH_SQUEAKY_TOY_POS)
        wait_interruptible(UI_SETTLE)

        # Equip the toy
        print("[debug] equipping toy...")
        hover_click(*CATCH_EQUIP_POS)
        wait_interruptible(UI_SETTLE)

        # Close backpack with 'b' key
        print("[debug] closing backpack...")
        pydirectinput.press(KEY_BACKPACK)
        wait_interruptible(UI_SETTLE)

        # Wait before throwing
        print(f"[debug] waiting {CATCH_WAIT_AFTER_EQUIP}s before throwing...")
        wait_interruptible(CATCH_WAIT_AFTER_EQUIP)

        # Zoom in, to avoid focusing the pet on toy throw
        hold_key(KEY_ZOOM_IN, CATCH_ZOOM_DURATION)
        
        # Click empty space to throw, with a delay between throws
        for i in range(CATCH_THROW_COUNT):
            print(f"[debug] throw {i + 1}/{CATCH_THROW_COUNT}...")
            hover_click(*EMPTY_POS)
            wait_interruptible(CATCH_EMOTE_DELAY)

        # Unequip the toy
        print("[debug] unequipping toy...")
        hover_click(*CATCH_UNEQUIP_POS)

        print("[!] Catch complete!")
        return True

class PetNeedHandler(NeedHandler):
    """The 'pet' need: click to focus the pet, then hold the mouse button
    down and move it up and down from the center of the screen."""

    def handle(self):
        print("[!] PET NEED")
        if not focus_roblox():
            return False

        print("[debug] focusing pet...")
        if not focus_pet(PET_FOCUS_CLICK_DURATION):
            return False
        wait_interruptible(UI_SETTLE)
        print(f"[debug] attempting to pet for {PET_CIRCLE_DURATION}s...")
        # Move to starting position before pressing down
        pydirectinput.moveTo(*to_screen(REFERENCE_CENTER_X, REFERENCE_CENTER_Y - PET_CIRCLE_RADIUS))
        time.sleep(PET_SETTLE_DELAY)
        # Click the center to focus
        hover_click(REFERENCE_CENTER_X, REFERENCE_CENTER_Y, duration=PET_FOCUS_CLICK_DURATION)
        time.sleep(PET_SETTLE_DELAY)
        # Hold down and move with incremental steps (much more reliable for games)
        pydirectinput.mouseDown()
        try:
            start_time = time.time()
            while True:
                check_running()
                elapsed = time.time() - start_time
                if elapsed >= PET_CIRCLE_DURATION:
                    break
                # Move up and down in a sine wave centered on screen center
                progress = elapsed / PET_CIRCLE_DURATION
                angle = progress * 2 * np.pi
                y = int(REFERENCE_CENTER_Y + PET_CIRCLE_RADIUS * np.sin(angle))
                # Use pydirectinput for better game compatibility
                pydirectinput.moveTo(*to_screen(REFERENCE_CENTER_X, y))
                time.sleep(PET_CIRCLE_STEP_MOVE_DURATION)
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

    def handle(self):
        print("[!] CHOOSE NEED")
        if not focus_roblox():
            return False

        print("[debug] focusing pet...")
        if not focus_pet():
            return False
        wait_interruptible(UI_SETTLE)

        print(f"[debug] searching screen for color {CHOOSE_BUTTON_COLOR}...")
        img = grab_screen()
        match = find_exact_color(img, CHOOSE_BUTTON_COLOR)
        if match is None:
            print(f"[!] WARNING: no pixel matching {CHOOSE_BUTTON_COLOR} found on screen")
            return False
        print(f"[debug] found at {match}, moving there slowly...")
        hover_click(*match, duration=CHOOSE_SLOW_MOVE_DURATION)
        wait_interruptible(UI_SETTLE)

        print("[debug] clicking middle of screen...")
        hover_click(REFERENCE_CENTER_X, REFERENCE_CENTER_Y, duration=CHOOSE_SLOW_MOVE_DURATION)

        print("[!] Choose complete!")
        return True

class RideNeedHandler(NeedHandler):
    """The 'ride' need: step back, mount a vehicle from the backpack, then
    walk back and forth for a while astride it - or less, the moment its
    icon is confirmed gone (see walk_alternating())."""

    def handle(self):
        print("[!] RIDE NEED")
        if not focus_roblox():
            return False

        # Step back before mounting
        print(f"[debug] stepping back for {RIDE_BACKWARD_DURATION}s...")
        hold_key("s", RIDE_BACKWARD_DURATION)

        print("[debug] pressing e...")
        pydirectinput.press(KEY_MOUNT)
        wait_interruptible(RIDE_WAIT_AFTER_E)

        # Walk forward briefly after mounting, before opening the backpack
        print(f"[debug] walking forward for {RIDE_FORWARD_DURATION}s...")
        hold_key("w", RIDE_FORWARD_DURATION)

        equip_favorite_vehicle()

        # Hold r briefly before starting to move
        print(f"[debug] holding {KEY_HELICOPTER} for {RIDE_R_HOLD_DURATION}s...")
        hold_key(KEY_HELICOPTER, RIDE_R_HOLD_DURATION)

        # Walk back and forth (forward/backward, not left/right) while riding
        walk_alternating(("w", "s"), NEED_GONE_MAX_WAIT, need_name="ride")

        print("[!] Ride complete!")
        return True

def teleport_to(category_pos):
    """Open the backpack and teleport via the given category tab
    (TELEPORT_PETS_TAB_POS for the nursery, TELEPORT_VEHICLES_TAB_POS for
    the dealership), then walk backward briefly to clear the landing spot.
    Shared by every need that teleports somewhere (TeleportWalkNeedHandler)."""
    print("[debug] opening backpack...")
    pydirectinput.press(KEY_BACKPACK)
    wait_interruptible(UI_SETTLE)

    print("[debug] selecting category...")
    hover_click(*category_pos)
    wait_interruptible(UI_SETTLE)
    hover_click(*GENERAL_TELEPORT_POS_2)
    wait_interruptible(UI_SETTLE)
    hover_click(*GENERAL_TELEPORT_POS_3)
    wait_interruptible(TELEPORT_WAIT)
    print("[debug] walking backward...")
    hold_key("s", TELEPORT_BACK_DURATION)
    wait_interruptible(TELEPORT_SETTLE_WAIT)

class TeleportWalkNeedHandler(NeedHandler):
    """A need that teleports somewhere, holds a sequence of movement keys,
    then waits for the need to clear (see wait_until_need_gone()).
    process_needs() respawns afterwards, since these leave the character
    somewhere else on the map. What each one does is configured in
    TELEPORT_WALK_NEEDS. An entry with `helicopter` set (default
    HELICOPTER_REQUIRED) flies instead of walking: after teleporting it
    steps forward, equips the favorite vehicle and holds r, and once the
    steps have brought it to the destination it presses space."""

    def __init__(self, name):
        self.name = name
        self.config = TELEPORT_WALK_NEEDS[name]

    def handle(self):
        print(f"[!] {self.name.upper()} NEED")
        if not focus_roblox():
            return False

        teleport_to(self.config["teleport_pos"])

        helicopter = self.config.get("helicopter", HELICOPTER_REQUIRED)
        if helicopter:
            print(f"[debug] stepping forward for {HELICOPTER_FORWARD_DURATION}s...")
            hold_key("w", HELICOPTER_FORWARD_DURATION)
            equip_favorite_vehicle()
            print(f"[debug] holding {KEY_HELICOPTER} for {HELICOPTER_HOLD_DURATION}s...")
            hold_key(KEY_HELICOPTER, HELICOPTER_HOLD_DURATION)

        for i, (key, duration) in enumerate(self.config["steps"]):
            if i:
                wait_interruptible(TELEPORT_WALK_STEP_GAP)
            print(f"[debug] holding {key} for {duration}s...")
            hold_key(key, duration)

        if helicopter:
            print("[debug] arrived, pressing space...")
            pydirectinput.press(KEY_JUMP)

        final_click = self.config.get("final_click")
        if final_click:
            pydirectinput.press("e")
            wait_interruptible(SICK_CONFIRM_WAIT)
            print(f"[debug] clicking {final_click}...")
            hover_click(*final_click)

        wait_until_need_gone(self.name)

        print(f"[!] {self.name.capitalize()} complete!")
        return True

# Handler factory (class, or partial of one) for each need.
# get_need_handler() uses this plus ENABLED_NEEDS (magic_numbers.py) to
# decide what to do with a detected need.
NEED_HANDLER_CLASSES = {
    "catch": CatchNeedHandler,
    "pet": PetNeedHandler,
    "choose": ChooseNeedHandler,
    "ride": RideNeedHandler,
    "walk": WalkNeedHandler,
    **{name: partial(ButtonNeedHandler, name) for name in BUTTON_NAMES},
    **{name: partial(TeleportWalkNeedHandler, name) for name in TELEPORT_WALK_NEEDS},
}

# Every handler the GUI's Debug tab can run on its own.
DEBUG_HANDLERS = NEED_HANDLER_CLASSES

def get_need_handler(need_name):
    """Return the handler for a detected need, or None if it's not
    currently enabled (see ENABLED_NEEDS), was disabled for this run (see
    record_detection()), or the macro doesn't know it."""
    if need_name not in ENABLED_NEEDS or need_name in DISABLED_THIS_RUN:
        return None
    handler_cls = NEED_HANDLER_CLASSES.get(need_name)
    return handler_cls() if handler_cls else None

def record_detection(detected):
    """Track stuck needs: one that was attempted on the last pass and is
    still on screen now has failed to clear, and an enabled need that does
    that NEED_STUCK_CHECKS times in a row is disabled for the rest of the
    run - retrying it forever just wastes time. A check where it's gone,
    even once, breaks the streak. (A need merely waiting its turn behind
    others doesn't count - only attempts do.) Call this for every check,
    including ones that detect nothing."""
    global ATTEMPTED_LAST
    for need_name in ATTEMPTED_LAST:
        if need_name in detected:
            ATTEMPT_STREAKS[need_name] = ATTEMPT_STREAKS.get(need_name, 0) + 1
        else:
            ATTEMPT_STREAKS.pop(need_name, None)
    ATTEMPTED_LAST = set()
    stuck = {n for n, count in ATTEMPT_STREAKS.items() if count >= NEED_STUCK_CHECKS} & (ENABLED_NEEDS - DISABLED_THIS_RUN)
    for need_name in sorted(stuck):
        DISABLED_THIS_RUN.add(need_name)
        print(f"[!] {need_name} still there after {NEED_STUCK_CHECKS} attempts in a row - disabling it for this run")
        log_failure(f"{need_name} stuck for {NEED_STUCK_CHECKS} attempts - disabled for this run "
                    f"({len(DISABLED_THIS_RUN)} disabled now: {', '.join(sorted(DISABLED_THIS_RUN))})")

def process_needs():
    """Detect needs on screen and resolve the first one that can be: its
    handler runs via get_need_handler(), and if it *actually* resolved the
    need the character respawns and this returns True - the caller then
    starts a fresh check, so the next need is always picked from what's on
    screen now, never from a list taken before the last one was handled. A
    need that isn't resolved (disabled, unmapped, or its handler returned
    False) is skipped for the next one, with no respawn. Returns False if
    nothing was resolved - including when nothing was on screen, which
    prints nothing (the caller shows a waiting line instead)."""
    global LAST_PROGRESS, ATTEMPTED_LAST, NONE_MATCHED_LOGGED
    if not focus_roblox_click():
        return False

    found_icons, full_img = detect_need_icons(save_debug=True)
    if not found_icons:
        record_detection([])
        return False

    matched_needs = []
    for idx, icon_img, need_name, score in identify_icons(found_icons, full_img):
        if not need_name:
            if SAVE_NEW_NEEDS:
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
        print(f"\n[!] Found {len(found_icons)} icon(s): " + ", ".join(f"{n} ({s:.4f})" for n, s in matched_needs))
        log_run_event(f"detected: {', '.join(names)}")
        NONE_MATCHED_LOGGED = False
    elif not NONE_MATCHED_LOGGED:
        log_run_event(f"detected: none matched ({len(found_icons)} icon(s) on screen)")
        NONE_MATCHED_LOGGED = True
    RUN_STATS["last_detected"] = names
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
            except TaskInterrupted:
                # A minigame took over (play_minigame() already respawned):
                # not resolved, and not a failed attempt either - the next
                # check sees what's still on screen.
                attempted.discard(need_name)
                print(f"[debug] {need_name} interrupted by a minigame")
                log_run_event(f"{need_name} interrupted by a minigame")
                return True
            if not handled:
                print(f"[debug] could not resolve '{need_name}' this pass, skipping")
                RUN_STATS["failed"][need_name] = RUN_STATS["failed"].get(need_name, 0) + 1
                log_failure(f"{need_name} handler could not resolve it")
                continue

            respawn_character()
            seconds = time.time() - started
            RUN_STATS["resolved"][need_name] = RUN_STATS["resolved"].get(need_name, 0) + 1
            RUN_STATS["last_resolved"] = {"need": need_name, "time": time.strftime("%Y-%m-%d %H:%M:%S")}
            LAST_PROGRESS = time.time()
            log_run_event(f"resolved: {need_name} ({seconds:.0f}s)")
            write_status()
            return True
        return False
    finally:
        ATTEMPTED_LAST = attempted

# ============================================================================
# PER-CYCLE CHECKS
# ============================================================================
# Run at the top of every cycle by unscrew() and side_quest(): popups to
# dismiss, recovering the game when it's gone wrong, and the timed chores.

def detect_paycheck():
    """Detect the paycheck popup by its CASH OUT button's exact color - only
    within PAYCHECK_REGION and with at least PAYCHECK_MIN_PIXELS of it, since
    that green is also the backpack's Select All button, among others - and,
    if present, dismiss it. Returns True if the popup was detected and
    dismissed, False otherwise."""
    left, top, right, bottom = PAYCHECK_REGION
    region = grab_screen()[top:bottom, left:right]
    if np.count_nonzero(exact_color_mask(region, PAYCHECK_CASHOUT_COLOR)) < PAYCHECK_MIN_PIXELS:
        return False

    print("[debug] paycheck popup detected, dismissing...")
    log_run_event("paycheck popup dismissed")
    hover_click(*PAYCHECK_DISMISS_POS_1)
    hover_click(*PAYCHECK_DISMISS_POS_2)
    return True

def detect_backpack_open(img):
    """True if the backpack is open in `img`: most of BACKPACK_HEADER_BOX is
    the purple of its header bar."""
    left, top, right, bottom = BACKPACK_HEADER_BOX
    mask = exact_color_mask(img[top:bottom, left:right], BACKPACK_HEADER_COLOR, BACKPACK_HEADER_TOLERANCE)
    return np.count_nonzero(mask) / mask.size >= BACKPACK_HEADER_MIN_FRACTION

def close_backpack_if_open():
    """Close the backpack if it's open - it's run at the start of a cycle, when
    no handler should have it open, so one that's open was left that way (a
    handler interrupted halfway, a toggle that got out of step). What
    detect_backpack_open() sees is the *expanded* backpack, which takes two
    presses of KEY_BACKPACK: the first shrinks it to the normal backpack, the
    second closes that. After the first press the expanded header must be
    gone - if it isn't, the key isn't doing what's expected, so that's logged
    as a failure and it stops instead of pressing again (another press could
    just reopen it). Does nothing unless Roblox has the focus, so the key
    can't go to another window. Returns True if it was open."""
    if not is_roblox_focused() or not detect_backpack_open(grab_screen()):
        return False
    print("[debug] backpack left open, closing it...")
    pydirectinput.press(KEY_BACKPACK)
    wait_interruptible(UI_SETTLE)
    if detect_backpack_open(grab_screen()):
        log_failure("backpack still expanded after pressing the backpack key")
        return True
    pydirectinput.press(KEY_BACKPACK)
    wait_interruptible(UI_SETTLE)
    log_run_event("backpack was left open - closed it")
    return True

def detect_disconnect(img):
    """True if the Roblox "Disconnected" dialog is on screen in `img`: most
    of DISCONNECT_PANEL_BOX is the dialog's grey (the game behind it is
    blurred, so nothing else on screen fills a box like that)."""
    left, top, right, bottom = DISCONNECT_PANEL_BOX
    mask = exact_color_mask(img[top:bottom, left:right], DISCONNECT_PANEL_COLOR, DISCONNECT_PANEL_TOLERANCE)
    return np.count_nonzero(mask) / mask.size >= DISCONNECT_PANEL_MIN_FRACTION

def rejoin_reason():
    """Why the game needs rejoining right now, as (kind, reason) - or None if
    it doesn't. In order of priority:
      "disconnect" - Roblox crashed (its crash window is open), isn't
          running, or is showing the Disconnected dialog;
      "stuck" - DISABLED_NEEDS_REJOIN_THRESHOLD needs have been disabled
          for this run;
      "stalled" - no need has been resolved for NO_PROGRESS_REJOIN_INTERVAL
          (see LAST_PROGRESS): the game is up but nothing is working;
      "scheduled" - it has been REJOIN_INTERVAL since the last rejoin (see
          LAST_REJOIN)."""
    if any(w.title == ROBLOX_CRASH_WINDOW_TITLE for w in gw.getAllWindows()):
        return "disconnect", "Roblox crashed"
    if not roblox_windows():
        return "disconnect", "Roblox isn't running"
    if detect_disconnect(grab_screen()):
        return "disconnect", "disconnected from the game"
    if len(DISABLED_THIS_RUN) >= DISABLED_NEEDS_REJOIN_THRESHOLD:
        return "stuck", f"{len(DISABLED_THIS_RUN)} needs disabled ({', '.join(sorted(DISABLED_THIS_RUN))})"
    if LAST_PROGRESS is not None and time.time() - LAST_PROGRESS >= NO_PROGRESS_REJOIN_INTERVAL:
        return "stalled", f"no need resolved for {NO_PROGRESS_REJOIN_INTERVAL / 60:g} minutes"
    if LAST_REJOIN is not None and time.time() - LAST_REJOIN >= REJOIN_INTERVAL:
        return "scheduled", f"{REJOIN_INTERVAL / 3600:g} hour(s) since the last rejoin"
    return None

def rejoin_game():
    """Recover when the game needs it (see rejoin_reason()): rejoin it
    (leave_and_rejoin()), run setup_game() - the rejoin resets the game
    settings it sets - and clear the disabled needs and their detection
    history so every need gets a fresh chance. Returns True if it recovered,
    False if nothing needed doing or the rejoin failed (the cause is still
    there, so the next cycle tries again). Everything but the scheduled
    rejoin is a failure: it's logged with a screenshot, which shows the
    Disconnected dialog and its error code if that's why. The clean esc/l/
    enter leave is skipped when the game is already gone."""
    found = rejoin_reason()
    if found is None:
        return False
    kind, reason = found
    if kind == "scheduled":
        print(f"[!] {reason} - rejoining")
        log_run_event(f"scheduled rejoin: {reason}")
        RUN_STATS["scheduled_rejoins"] += 1
    else:
        log_failure(f"{reason} - rejoining")
        RUN_STATS["recoveries"] += 1
        if kind == "disconnect":
            RUN_STATS["disconnects"] += 1
    if not leave_and_rejoin(clean_leave=(kind != "disconnect")):
        log_failure("recovery: rejoin failed, will try again next cycle")
        return False
    setup_game()
    DISABLED_THIS_RUN.clear()
    reset_need_tracking()
    log_run_event("recovery complete: rejoined, setup run, disabled needs cleared")
    print("[!] Recovery complete - carrying on")
    write_status()
    return True

def detect_minigame_popup(img):
    """True if one of the Halloween minigame popups ("Ghost Gallery is
    starting soon! Teleport there now?", "Hauntlet 2 is starting soon!...")
    is on screen in `img`. They share one layout and nothing here tells them
    apart - only that a minigame is offering to teleport you. Neither button
    color is unique on its own (the Yes green is also the paycheck's CASH OUT
    green, the No red is also the Exit Home button), so it needs both: the
    Yes green, with the No red close by to its left."""
    ys, xs = np.where(exact_color_mask(img, MINIGAME_POPUP_YES_COLOR) > 0)
    if len(xs) < MINIGAME_POPUP_MIN_BUTTON_PIXELS:
        return False
    yes_x, yes_y = int(xs.mean()), int(ys.mean())
    near_no = img[max(0, yes_y - MINIGAME_POPUP_NO_MAX_DY):yes_y + MINIGAME_POPUP_NO_MAX_DY,
                  max(0, yes_x - MINIGAME_POPUP_NO_MAX_DX):yes_x]
    return int(np.count_nonzero(exact_color_mask(near_no, MINIGAME_POPUP_NO_COLOR))) >= MINIGAME_POPUP_MIN_BUTTON_PIXELS

def load_templates(directory):
    """{lower-cased file name without extension: image} for every .png in
    `directory` (none if it doesn't exist)."""
    templates = {}
    if os.path.isdir(directory):
        for f in sorted(os.listdir(directory)):
            crop = cv2.imread(os.path.join(directory, f))
            if f.lower().endswith(".png") and crop is not None:
                templates[os.path.splitext(f)[0].lower()] = crop
    return templates

def template_score(img, crop, box):
    """How well `crop` matches anywhere within `box` (left, top, right,
    bottom) of `img`: 0-1, 1 = identical (cv2.matchTemplate)."""
    left, top, right, bottom = box
    band = img[top:bottom, left:right]
    if crop.shape[0] > band.shape[0] or crop.shape[1] > band.shape[1]:
        return 0.0
    return float(cv2.minMaxLoc(cv2.matchTemplate(band, crop, cv2.TM_CCOEFF_NORMED))[1])

def identify_minigame(img):
    """Which minigame the popup in `img` is for: the name whose title crop
    (MINIGAME_TEMPLATE_DIR) matches best within MINIGAME_TITLE_BOX, if that
    match reaches MINIGAME_TITLE_MATCH_THRESHOLD - otherwise None."""
    best_name, best_score = None, MINIGAME_TITLE_MATCH_THRESHOLD
    for name, crop in load_templates(MINIGAME_TEMPLATE_DIR).items():
        score = template_score(img, crop, MINIGAME_TITLE_BOX)
        if score >= best_score:
            best_name, best_score = name, score
    return best_name

def dismiss_stray_windows():
    """Close any window from STRAY_WINDOWS that's open (found by its title
    crop in STRAY_WINDOW_DIR): the Trading Hub the macro can open by a
    misclick - first its "Go to the Trading Hub to edit listings!" popup, by
    the green Okay button - and the daily Star Rewards that opens after a
    join. They cover the screen and everything the macro clicks, and the
    paycheck check mistakes their green buttons for CASH OUT. Returns the name
    of the window closed, or None."""
    img = grab_screen()
    for name, crop in load_templates(STRAY_WINDOW_DIR).items():
        config = STRAY_WINDOWS.get(name)
        if config is None or template_score(img, crop, config["box"]) < STRAY_WINDOW_MATCH_THRESHOLD:
            continue
        print(f"[debug] stray window open: {name}, closing it...")
        log_run_event(f"stray window closed: {name}")
        if name == "trading_hub":
            left, top, right, bottom = STRAY_OKAY_BOX
            okay = exact_color_mask(img[top:bottom, left:right], STRAY_OKAY_COLOR, STRAY_OKAY_TOLERANCE)
            if np.count_nonzero(okay) >= STRAY_OKAY_MIN_PIXELS:
                hover_click(*STRAY_OKAY_POS)
        hover_click(*config["close_pos"])
        time.sleep(STRAY_CLOSE_SETTLE)
        return name
    return None

def detect_minigame_victory(img):
    """True if the minigame victory screen is on screen in `img`: the red
    GAME OVER! banner and the green NICE! button, both by exact color (the
    same green is the Yes button's, so the banner is what rules that out)."""
    for color, tolerance, box, minimum in (
            (MINIGAME_VICTORY_BANNER_COLOR, MINIGAME_VICTORY_BANNER_TOLERANCE,
             MINIGAME_VICTORY_BANNER_BOX, MINIGAME_VICTORY_BANNER_MIN_PIXELS),
            (MINIGAME_VICTORY_BUTTON_COLOR, MINIGAME_VICTORY_BUTTON_TOLERANCE,
             MINIGAME_VICTORY_BUTTON_BOX, MINIGAME_VICTORY_BUTTON_MIN_PIXELS)):
        left, top, right, bottom = box
        if np.count_nonzero(exact_color_mask(img[top:bottom, left:right], color, tolerance)) < minimum:
            return False
    return True

def minigame_won():
    """detect_minigame_victory() on a fresh screenshot."""
    return detect_minigame_victory(grab_screen())

def play_hauntlet(deadline):
    """Hauntlet 2: wait HAUNTLET_START_WAIT for it to start (no needs can be
    seen while it runs, and the lobby counts down first), then hold forward
    until the victory screen shows. Returns True if it did before `deadline`."""
    print(f"[debug] hauntlet: waiting {HAUNTLET_START_WAIT:g}s for it to start...")
    wait_interruptible(HAUNTLET_START_WAIT)
    pydirectinput.keyDown(HAUNTLET_FORWARD_KEY)
    try:
        while time.time() < deadline:
            wait_interruptible(MINIGAME_VICTORY_CHECK_INTERVAL)
            if minigame_won():
                return True
    finally:
        pydirectinput.keyUp(HAUNTLET_FORWARD_KEY)
    return False

def play_ghost_gallery(deadline):
    """Ghost Gallery: run about at random, jumping, with the mouse held in
    GHOST_GALLERY_HOLD-second holds (GHOST_GALLERY_HOLD_GAP apart), until the
    victory screen shows. Returns True if it did before `deadline`."""
    print(f"[debug] ghost gallery: waiting {GHOST_GALLERY_START_WAIT:g}s for it to start...")
    wait_interruptible(GHOST_GALLERY_START_WAIT)
    while time.time() < deadline:
        pydirectinput.mouseDown()
        try:
            hold_until = time.time() + GHOST_GALLERY_HOLD
            while time.time() < hold_until:
                if minigame_won():
                    return True
                key = random.choice(MOVE_KEYS)
                pydirectinput.keyDown(key)
                try:
                    pydirectinput.press(KEY_JUMP)
                    wait_interruptible(min(random.uniform(GHOST_GALLERY_STEP_MIN, GHOST_GALLERY_STEP_MAX),
                                           max(0.0, hold_until - time.time())))
                finally:
                    pydirectinput.keyUp(key)
        finally:
            pydirectinput.mouseUp()
        wait_interruptible(GHOST_GALLERY_HOLD_GAP)
    return False

MINIGAME_PLAYERS = {"hauntlet": play_hauntlet, "ghost_gallery": play_ghost_gallery}

def play_minigame(name):
    """Take the popup's Yes, play minigame `name` until its victory screen
    shows, click NICE!, and respawn. Gives up (a logged failure) after
    MINIGAME_MAX_DURATION. Counts as progress for the no-progress rejoin.
    Returns True if it was won."""
    global LAST_PROGRESS
    label = MINIGAME_LABELS.get(name, name)
    print(f"[!] playing {label}")
    log_run_event(f"minigame started: {label}")
    hover_click(*MINIGAME_POPUP_YES_POS)
    started = time.time()
    try:
        won = MINIGAME_PLAYERS[name](started + MINIGAME_MAX_DURATION)
    finally:
        release_all_inputs()
    if won:
        hover_click(*MINIGAME_VICTORY_BUTTON_POS)
        log_run_event(f"minigame finished: {label} ({time.time() - started:.0f}s)")
        wait_interruptible(MINIGAME_FINISH_WAIT)
    else:
        log_failure(f"{label} didn't reach the victory screen within {MINIGAME_MAX_DURATION:g}s")
    LAST_PROGRESS = time.time()
    respawn_character()
    return won

def minigame_popup(play=True):
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
        hover_click(*MINIGAME_POPUP_NO_POS)
        return "dismissed"
    print(f"[debug] minigame popup detected ({name or 'unknown'}), dismissing...")
    log_run_event(f"minigame popup dismissed ({name or 'unknown'})")
    hover_click(*MINIGAME_POPUP_DONT_SHOW_POS)   # "Do not show again this session"
    hover_click(*MINIGAME_POPUP_NO_POS)
    return "dismissed"

def unscrew(in_task=False, play_minigames=True):
    """The per-cycle checks - run right after check_stop() at the top of every
    cycle, and, with `in_task`, every UNSCREW_TASK_INTERVAL seconds while a
    need handler or setup is running (see task_unscrew()). In order: rejoin
    if we've been disconnected (first, since nothing else works while
    disconnected) and close the backpack if it was left open - both only at
    the top of a cycle, since mid-task the backpack is open on purpose;
    close stray windows (dismiss_stray_windows()); during Halloween, handle a
    minigame popup; then the paycheck popup (mid-task not while the backpack
    is open - its green Select All button would be mistaken for CASH OUT).
    The stray windows and the minigame popup go before the paycheck check
    because their green buttons would otherwise be mistaken for CASH OUT too.
    Returns "played" if a minigame was played, otherwise None."""
    if not in_task:
        rejoin_game()
        close_backpack_if_open()
    dismiss_stray_windows()
    played = minigame_popup(play=play_minigames) if HALLOWEEN else None
    if not in_task or not detect_backpack_open(grab_screen()):
        detect_paycheck()
    return played

def side_quest():
    """Runs once per cycle, right after unscrew(), unless side_quest_enabled
    is off in GAME_CONFIG_PATH: tends to the money tree and the lure using
    state persisted there. The tree is checked at most once per
    TREE_CHECK_INTERVAL, and money_collected only ever goes up (by
    TREE_HARVEST_YIELD per harvest, until it reaches MONEY_COLLECTED_TARGET)
    - resetting it back down means resetting the config. Each piece of
    progress is saved the moment its action succeeds, so a stop partway
    through doesn't lose it."""
    config = load_game_config()
    if not config["side_quest_enabled"]:
        return
    now = time.time()

    if config["money_collected"] < MONEY_COLLECTED_TARGET and now >= config["tree_timer"]:
        if tree_collect():
            def harvested(c):
                c["money_collected"] += TREE_HARVEST_YIELD
                c["tree_timer"] = time.time() + TREE_CHECK_INTERVAL
            update_game_config(harvested)

    if now >= config["lure_timer"]:
        if lure_collect():
            update_game_config(lambda c: c.update(lure_timer=time.time() + LURE_RECOLLECT_INTERVAL))

# ============================================================================
# WORKFLOWS
# ============================================================================

def format_duration(seconds):
    """"1m 23s" / "45s" - how long something has been going."""
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}m {secs:02d}s" if minutes else f"{secs}s"

def run_full_cycle():
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
        RUN_STATS["cycles"] += 1
        write_status()
        unscrew()
        side_quest()
        if process_needs():
            return
        waiting_since = waiting_since or time.time()
        deadline = time.time() + NEED_CHECK_RETRY_DELAY
        while time.time() < deadline:
            print(f"\rWaiting for a need... {format_duration(time.time() - waiting_since)}", end="")
            wait_interruptible(min(1.0, deadline - time.time()))

def run_workflow_loop():
    """Respawn once, then repeat the workflow continuously. The only way this
    loop ever ends is via a stop request - there's no other exit condition,
    so StopRequested is expected here, not an error. It's allowed to
    propagate on up to run_async()'s worker (via the bare `finally`, not
    `except`) so the GUI still reports [STOPPED] correctly; the finally
    just prints locally first."""
    global STOP_FLAG, CURRENT_RUN_NUMBER, LAST_REJOIN, LAST_PROGRESS
    STOP_FLAG = False
    CURRENT_RUN_NUMBER = next_run_number()
    reset_need_tracking()
    reset_run_stats()
    LAST_REJOIN = LAST_PROGRESS = time.time()  # the hourly and no-progress rejoins count from here
    log_run_event("LOOP started")
    print("\n" + "=" * 50)
    print(f"[LOOP] Starting continuous workflow (run {CURRENT_RUN_NUMBER})")
    print("=" * 50)

    # Respawn once up front so the loop always starts from a known state,
    # regardless of wherever the character happened to be standing.
    needs_respawn = True

    loop_num = 0
    focus_losses, progress_at_loss = 0, None
    try:
        while True:
            loop_num += 1
            print(f"\n[LOOP {loop_num}]")
            try:
                if needs_respawn:
                    respawn_character()
                    needs_respawn = False
                run_full_cycle()
                wait_interruptible(LOOP_DELAY)
            except FocusLost:
                # Only stops the run unless "Resume after focus loss" is
                # ticked - and even then gives up after
                # FOCUS_RESUME_MAX_IN_A_ROW losses with no need resolved in
                # between, so it can't fight you for the window forever.
                if not load_game_config()["resume_on_focus_loss"]:
                    raise
                if LAST_PROGRESS != progress_at_loss:
                    focus_losses = 0
                progress_at_loss = LAST_PROGRESS
                focus_losses += 1
                if focus_losses > FOCUS_RESUME_MAX_IN_A_ROW:
                    log_failure(f"Roblox lost focus {focus_losses} times in a row - giving up")
                    raise
                RUN_STATS["focus_resumes"] += 1
                log_failure(f"Roblox lost focus - resuming ({focus_losses}/{FOCUS_RESUME_MAX_IN_A_ROW})")
                release_all_inputs()
                wait_stoppable(FOCUS_RESUME_DELAY)
                focus_roblox_click()
                needs_respawn = True  # where a half-finished handler left the character is unknown
    finally:
        log_run_event("LOOP stopped")
        print("\n[LOOP] Stopped\n")

# ============================================================================
# GUI
# ============================================================================

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

    def __init__(self, root, text_widget):
        self.root = root
        self.text = text_widget
        self.queue = queue.Queue()
        self.log_status_active = False   # write() side: a status line is the last thing logged
        self.at_line_start = True        # drain() side: the widget's state
        self.status_active = False
        self.drain()

    def write(self, msg):
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

    def drain(self):
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
                self.text.see(tk.END)
                self.text.config(state=tk.DISABLED)
            self.root.after(CONSOLE_DRAIN_INTERVAL_MS, self.drain)
        except tk.TclError:
            pass   # the window is gone

    def render(self, msg):
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

    def flush(self):
        pass

class AdoptMeGUI:
    def __init__(self, root):
        global MACRO_WINDOW
        MACRO_WINDOW = self
        self.root = root
        self.root.title("Adopt Me Macro")
        self.root.geometry(GUI_GEOMETRY)
        self.root.resizable(False, False)
        self.root.attributes('-topmost', True)
        self.root.attributes('-alpha', GUI_ALPHA)

        self.bg = GUI_BG
        self.accent = GUI_ACCENT
        self.fg = GUI_FG
        self.root.configure(bg=self.bg)

        self.create_ui()

        self.capture = DebugCapture(self.root, self.debug_text)
        sys.stdout = self.capture

        print("\n" + "=" * 60)
        print("ADOPT ME MACRO - CONTROL PANEL")
        print("=" * 60)
        print(f"Needs:  {NEEDS_DIR}")
        print(f"Debug:  {DEBUG_DIR}")
        print("=" * 60 + "\n")

        self.is_running = False
        self.watch_stop_hotkey()

    def watch_stop_hotkey(self):
        """Start a background thread that stops a running macro the moment
        STOP_HOTKEY is pressed, in any window - so you can take over the
        computer without reaching for the GUI. Only the key *going down*
        counts (holding it doesn't retrigger), and it does nothing while
        nothing is running. The stop itself is handed to Tk's thread."""
        def watch():
            was_down = False
            while True:
                try:
                    down = hotkey_down(STOP_HOTKEY)
                    if down and not was_down and self.is_running:
                        self.root.after(0, self.stop_by_hotkey)
                except (RuntimeError, tk.TclError):
                    return  # the window is gone
                was_down = down
                time.sleep(STOP_HOTKEY_POLL_INTERVAL)
        threading.Thread(target=watch, daemon=True).start()

    def stop_by_hotkey(self):
        """STOP_HOTKEY was pressed: stop, same as the [STOP] button, and say so."""
        if self.is_running:
            print(f"\n[!] '{STOP_HOTKEY.upper()}' pressed")
            log_run_event(f"stopped with the {STOP_HOTKEY.upper()} key")
            self.stop()

    def create_ui(self):
        """Build every widget. Any button that kicks off a background action
        (via run_async) is appended to self.action_buttons so run_async can
        disable/re-enable all of them together as a group. The [STOP] button
        is deliberately excluded - it must stay clickable while something
        is running, since that's the whole point of it."""
        # Main buttons
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.X, padx=GUI_OUTER_PADDING, pady=(GUI_OUTER_PADDING, 0))
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
        NO_BORDER = dict(bd=0, highlightthickness=0)  # flat edges, no default Tk bevel/focus ring

        main_row = tk.Frame(btn_frame, bg=self.bg)
        main_row.pack(fill=tk.X, pady=GUI_ROW_SPACING)

        # Not added to action_buttons: must remain clickable while a workflow is running
        stop_container = tk.Frame(main_row, width=GUI_SQUARE_BUTTON_SIZE, height=GUI_SQUARE_BUTTON_SIZE, bg=self.bg)
        stop_container.pack(side=tk.RIGHT, padx=(GUI_ROW_SPACING, 0))
        stop_container.pack_propagate(False)

        self.btn_stop = tk.Button(stop_container, text="■", command=self.stop,
                                   font=(GUI_FONT, GUI_STOP_ICON_FONT_SIZE, "bold"), bg=GUI_STOP_COLOR, fg=self.fg,
                                   cursor="hand2", **NO_BORDER)
        self.btn_stop.pack(fill=tk.BOTH, expand=True)

        loop_container = tk.Frame(main_row, height=GUI_SQUARE_BUTTON_SIZE, bg=self.bg)
        loop_container.pack(side=tk.LEFT, fill=tk.X, expand=True)
        loop_container.pack_propagate(False)

        btn_loop = tk.Button(loop_container, text="\U0001F504", command=self.run_workflow_loop,
                              font=(GUI_FONT, GUI_LOOP_ICON_FONT_SIZE, "bold"), bg=GUI_LOOP_COLOR, fg=self.fg,
                              cursor="hand2", **NO_BORDER)
        btn_loop.pack(fill=tk.BOTH, expand=True)
        self.action_buttons.append(btn_loop)

        tk.Label(btn_frame, text=f"Press {STOP_HOTKEY.upper()} anywhere to stop", font=(GUI_FONT, GUI_STATUS_FONT_SIZE),
                 bg=self.bg, fg=self.accent, anchor=tk.W).pack(fill=tk.X)

        tk.Frame(btn_frame, bg=self.accent, height=GUI_DIVIDER_HEIGHT).pack(fill=tk.X, pady=GUI_ROW_SPACING)

        tk.Label(btn_frame, text="Functions", font=(GUI_FONT, GUI_SECTION_FONT_SIZE, "bold"),
                 bg=self.bg, fg=self.accent).pack(anchor=tk.W)

        btn_respawn = tk.Button(btn_frame, text="Respawn", command=lambda: self.run_async(respawn_character),
                                 font=(GUI_FONT, GUI_SECTION_FONT_SIZE), bg=GUI_RESPAWN_COLOR, fg=self.fg,
                                 height=GUI_RESPAWN_BUTTON_HEIGHT, cursor="hand2")
        btn_respawn.pack(fill=tk.X, pady=GUI_WIDGET_SPACING)
        self.action_buttons.append(btn_respawn)

        btn_setup = tk.Button(btn_frame, text="Setup", command=lambda: self.run_async(setup_game),
                               font=(GUI_FONT, GUI_SECTION_FONT_SIZE), bg=GUI_RESPAWN_COLOR, fg=self.fg,
                               height=GUI_RESPAWN_BUTTON_HEIGHT, cursor="hand2")
        btn_setup.pack(fill=tk.X, pady=GUI_WIDGET_SPACING)
        self.action_buttons.append(btn_setup)

        btn_rejoin = tk.Button(btn_frame, text="Leave & rejoin", command=lambda: self.run_async(leave_and_rejoin),
                                font=(GUI_FONT, GUI_SECTION_FONT_SIZE), bg=GUI_RESPAWN_COLOR, fg=self.fg,
                                height=GUI_RESPAWN_BUTTON_HEIGHT, cursor="hand2")
        btn_rejoin.pack(fill=tk.X, pady=GUI_WIDGET_SPACING)
        self.action_buttons.append(btn_rejoin)

        # Options tab
        # Persisted switches (see load_game_config()). Deliberately not in
        # action_buttons: they must stay usable while a workflow is running,
        # which is safe since update_game_config() is locked.
        config = load_game_config()
        self.config_flag_vars = {}
        for label, key in (("Run side quest", "side_quest_enabled"),
                           ("Resume after focus loss", "resume_on_focus_loss"),
                           *((f"Play {label}", f"{name}_enabled") for name, label in MINIGAME_LABELS.items())):
            var = tk.BooleanVar(value=config[key])
            self.config_flag_vars[key] = var
            tk.Checkbutton(options_tab, text=label, variable=var,
                           command=lambda k=key, v=var: self.set_config_flag(k, v.get()),
                           font=(GUI_FONT, GUI_SECTION_FONT_SIZE), bg=self.bg, fg=self.fg,
                           selectcolor=self.bg, activebackground=self.bg, activeforeground=self.fg,
                           anchor=tk.W, cursor="hand2").pack(fill=tk.X, pady=GUI_WIDGET_SPACING)

        tk.Label(options_tab, text="Private server link", font=(GUI_FONT, GUI_SECTION_FONT_SIZE),
                 bg=self.bg, fg=self.accent, anchor=tk.W).pack(fill=tk.X)
        self.server_link_var = tk.StringVar(value=config["private_server_link"])
        server_link_entry = tk.Entry(options_tab, textvariable=self.server_link_var, font=(GUI_FONT, GUI_SECTION_FONT_SIZE),
                                     bg=GUI_CONSOLE_BG, fg=GUI_CONSOLE_FG, insertbackground=GUI_CONSOLE_FG)
        server_link_entry.pack(fill=tk.X, pady=GUI_WIDGET_SPACING)
        server_link_entry.bind("<FocusOut>", lambda e: self.save_server_link())
        server_link_entry.bind("<Return>", lambda e: self.save_server_link())

        tk.Button(options_tab, text="Reset Config", command=self.reset_config,
                  font=(GUI_FONT, GUI_SECTION_FONT_SIZE), bg=GUI_STOP_COLOR, fg=self.fg,
                  height=GUI_RESPAWN_BUTTON_HEIGHT, cursor="hand2").pack(fill=tk.X, pady=GUI_WIDGET_SPACING)

        # Debug tab: one button per need handler, regardless of whether it's in
        # ENABLED_NEEDS, so any handler can be run on its own.
        self.build_debug_tab(debug_tab)

        # Debug console
        debug_frame = tk.Frame(self.root, bg=self.bg)
        debug_frame.pack(fill=tk.BOTH, expand=True, padx=GUI_OUTER_PADDING, pady=(0, GUI_OUTER_PADDING))

        tk.Label(debug_frame, text="Output", font=(GUI_FONT, GUI_SECTION_FONT_SIZE, "bold"),
                 bg=self.bg, fg=self.accent).pack(anchor=tk.W, pady=(0, GUI_LABEL_PADDING))

        self.debug_text = scrolledtext.ScrolledText(debug_frame, height=GUI_CONSOLE_HEIGHT, width=GUI_CONSOLE_WIDTH,
                                                     bg=GUI_CONSOLE_BG, fg=GUI_CONSOLE_FG,
                                                     font=(GUI_FONT, GUI_CONSOLE_FONT_SIZE), state=tk.DISABLED)
        self.debug_text.pack(fill=tk.BOTH, expand=True)

        # Status bar
        status = tk.Frame(self.root, bg=self.accent, height=GUI_STATUS_HEIGHT)
        status.pack(fill=tk.X, side=tk.BOTTOM)
        status.pack_propagate(False)
        self.status = tk.Label(status, text="Ready", font=(GUI_FONT, GUI_STATUS_FONT_SIZE), bg=self.accent, fg=self.fg)
        self.status.pack(anchor=tk.W, padx=GUI_OUTER_PADDING, pady=GUI_LABEL_PADDING)

    def send_to_back(self):
        """Stop being always-on-top and drop behind other windows."""
        self.root.attributes('-topmost', False)
        self.root.lower()

    def bring_to_front(self):
        """Always-on-top again, as at startup."""
        self.root.attributes('-topmost', True)
        self.root.lift()

    def run_on_ui_thread(self, func, timeout=5.0):
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

    def run_async(self, func):
        """Run `func` on a background daemon thread so the GUI never freezes
        while a workflow is executing (Tkinter blocks the whole window if you
        run slow code directly on the main thread).

        Lifecycle: disable the action buttons -> run func() in the background
        -> ALWAYS re-enable the buttons when func() returns, whether it
        finished normally, was interrupted via STOP_FLAG, lost Roblox's
        focus, or raised an exception. That "always" is done with
        try/except/finally so there is
        no code path that leaves the UI stuck in the disabled "Running..."
        state - the bug that made buttons stop responding once a workflow
        completed on its own.
        """
        global STOP_FLAG

        if self.is_running:
            print("[!] Already running - press [STOP] first")
            return

        self.is_running = True
        STOP_FLAG = False
        self._set_running_ui(True)

        def worker():
            global STOP_FLAG
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
            except FocusLost:
                # Same reasoning as StopRequested above - must come before
                # the generic Exception handler.
                log_failure("stopped: Roblox lost focus")
                final_status = "[STOPPED: Roblox not focused]"
            except Exception as e:
                print(f"\n[ERROR] {e}\n{traceback.format_exc()}")
                log_failure(f"crashed: {type(e).__name__}: {e}")
                final_status = "[ERROR]"
            finally:
                release_all_inputs()  # last line of defense against stuck keys/mouse
                STOP_FLAG = False
                self.is_running = False
                # Tkinter widgets may only be touched from the main thread;
                # root.after() hands this callback to it instead of calling
                # .config() directly from this background thread.
                self.root.after(0, lambda status=final_status: self._set_running_ui(False, status))

        threading.Thread(target=worker, daemon=True).start()

    def _set_running_ui(self, running, status_text=None):
        """Single place that toggles button state + status text. Called both
        right before a background task starts and from its finally block
        when it ends, so the two are always kept in sync."""
        state = tk.DISABLED if running else tk.NORMAL
        for btn in self.action_buttons:
            btn.config(state=state)
        self.status.config(text="Running..." if running else (status_text or "Ready"))

    def run_workflow_loop(self):
        self.run_async(run_workflow_loop)

    def set_config_flag(self, key, value):
        """Persist one of the config's on/off switches (a checkbox was clicked)."""
        update_game_config(lambda c: c.update({key: value}))
        print(f"[!] {key} = {value}")

    def save_server_link(self):
        """Persist the private server link typed into the entry box."""
        link = self.server_link_var.get().strip()
        if link != load_game_config()["private_server_link"]:
            update_game_config(lambda c: c.update(private_server_link=link))
            print("[!] Private server link saved")

    def reset_config(self):
        """Delete the persisted config (money collected, timers and the
        switches all go back to their defaults; the private server link is
        kept) and refresh the checkboxes to match."""
        reset_game_config()
        config = load_game_config()
        for key, var in self.config_flag_vars.items():
            var.set(config[key])
        print("[!] Config reset to defaults")

    def build_debug_tab(self, parent):
        """Fill the Debug tab with a two-column grid of [TEST] buttons, one
        per entry in DEBUG_HANDLERS."""
        tk.Label(parent, text="Run a need handler on its own", font=(GUI_FONT, GUI_SECTION_FONT_SIZE, "bold"),
                 bg=self.bg, fg=self.accent).grid(row=0, column=0, columnspan=2, sticky=tk.W,
                                                   padx=GUI_ROW_SPACING, pady=(GUI_ROW_SPACING, GUI_WIDGET_SPACING))
        for col in range(2):
            parent.grid_columnconfigure(col, weight=1, uniform="debug")
        for i, (name, handler_cls) in enumerate(DEBUG_HANDLERS.items()):
            btn = tk.Button(parent, text=name.capitalize(),
                            command=lambda n=name, c=handler_cls: self.test_handler(n, c),
                            font=(GUI_FONT, GUI_SECTION_FONT_SIZE), bg=GUI_TEST_COLOR, fg=self.fg, cursor="hand2")
            btn.grid(row=1 + i // 2, column=i % 2, sticky=tk.EW, padx=GUI_WIDGET_SPACING, pady=GUI_WIDGET_SPACING)
            self.action_buttons.append(btn)

    def test_handler(self, name, handler_cls):
        """Run one need handler on its own, outside the normal need-detection
        flow, so it can be tried even when it isn't in ENABLED_NEEDS."""
        def test():
            print(f"\n[TEST] Running {name} handler...")
            try:
                with task_unscrew():
                    handler_cls().handle()
            except TaskInterrupted:
                print(f"[TEST] {name} interrupted by a minigame")
            print(f"[TEST] {name.capitalize()} handler complete\n")
        self.run_async(test)

    def stop(self):
        """Signal a running workflow to stop. This only sets STOP_FLAG; the
        actual cleanup (re-enabling buttons, resetting is_running) happens
        automatically in run_async()'s worker thread once the running
        function notices the flag (via wait_interruptible) and returns -
        there's no separate watchdog thread needed for that anymore."""
        global STOP_FLAG
        if not self.is_running:
            return
        print("\n[!] STOPPING...")
        STOP_FLAG = True
        self.status.config(text="Stopping...")

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    root = tk.Tk()
    gui = AdoptMeGUI(root)
    root.mainloop()
