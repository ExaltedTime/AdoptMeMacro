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

import os, sys, time, random, threading
from abc import ABC, abstractmethod
from functools import partial
from pathlib import Path
import tkinter as tk
from tkinter import scrolledtext, ttk

import numpy as np
import cv2
import mss
import pyautogui
import pydirectinput
import pygetwindow as gw

# ============================================================================
# CONSTANTS
# ============================================================================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
NEEDS_DIR = os.path.join(SCRIPT_DIR, "needs")
DEBUG_DIR = os.path.join(SCRIPT_DIR, "debug")
Path(NEEDS_DIR).mkdir(exist_ok=True)
Path(DEBUG_DIR).mkdir(exist_ok=True)

RUN_LOG_PATH = os.path.join(DEBUG_DIR, "run_log.txt")      # appended to, never overwritten
RUN_COUNTER_PATH = os.path.join(DEBUG_DIR, "run_counter.txt")  # holds the last-used run number

pyautogui.FAILSAFE = True
pydirectinput.FAILSAFE = True
pydirectinput.PAUSE = 0.05
SCREEN_WIDTH, SCREEN_HEIGHT = pyautogui.size()
SCREEN_CENTER_X, SCREEN_CENTER_Y = SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2

# Behavior flags
FOCUS_WINDOW_ON_ACTION = True  # click the Roblox window to focus it before acting
SAVE_NEW_NEEDS = False
MATCH_ONLY_LEFT_HALF = True    # compare only the left half of each icon (a badge sits at its top right)
PAYCHECK_RECEIVED = False      # set True once detect_paycheck() has dismissed the paycheck popup

# Timing (seconds)
RESPAWN_KEY_DURATION = 0.05    # how long each respawn key is held
RESPAWN_WAIT = 4.0             # settle time after respawning, before it's usable
WALK_TO_BUTTONS_DURATION = 0.8 # time spent walking forward to reach the action buttons
WALK_ALTERNATING_STEP = 1.0    # duration of each a/d press in alternating walk pattern
KEY_STEP_GAP = 0.1             # pause between consecutive key holds (walk patterns, teleport-walk needs)
WALK_TOTAL_DURATION = 35.0     # total duration to keep walking back and forth
UI_SETTLE = 0.5                # generic pause for UI to catch up (between clicks in a sequence)
FOCUS_DELAY = 0.3              # pause after focusing the window
FOCUS_CLICK_SETTLE_DELAY = 0.1 # pause after the window-focus click
CLICK_MOVE_DURATION = 0.3      # mouse travel time for an ordinary click
CLICK_SETTLE_DELAY = 0.2       # pause after moving the mouse, before clicking
POST_CLICK_DELAY = 0.4         # pause after each click
POST_NEED_CLICK_WAIT = 15      # pause after satisfying a need via button click
POST_NEED_CLICK_WAIT_SHORT = 10  # shorter pause for needs that refill quickly
SHORT_WAIT_NEED_NAMES = {"hungry", "thirsty"}  # basic needs that use the shorter wait above
NEED_CHECK_RETRY_DELAY = 5.0   # pause before re-checking when no need was found
LOOP_DELAY = 2.0               # pause between iterations of the workflow loop
STOP_CHECK_INTERVAL = 0.1      # granularity of the interruptible wait loop
CATCH_WAIT_AFTER_EQUIP = 2.0   # wait after equipping toy before throwing
CATCH_EMOTE_DELAY = 10.0       # delay between throw clicks
CATCH_THROW_COUNT = 3          # number of times the toy is thrown
CATCH_ZOOM_DURATION = 3.0      # seconds the zoom-in key is held before throwing
PET_CIRCLE_DURATION = 10.0     # how long to make circles with mouse
PET_CIRCLE_RADIUS = 100                # radius (px) of the circle traced around screen center
PET_SETTLE_DELAY = 0.1                 # pause after each mouse move/click before the next pet step
PET_CIRCLE_STEP_MOVE_DURATION = 0.05   # time for each small step around the circle
ICON_EXTRACT_PADDING = 5       # px of padding added around a detected icon's radius

# Catch need positions (screen coordinates for the toy-throwing sequence;
# opening/closing the backpack itself uses the 'b' key, not a click)
CATCH_TOYS_POS = (754, 854)
CATCH_SQUEAKY_TOY_POS = (976, 875)
CATCH_EQUIP_POS = (1083, 980)
CATCH_UNEQUIP_POS = (1054, 983)

# A spot on screen that's just empty game world - no UI, no character menu.
# Used as the "click into empty space" throw motion in catch.
EMPTY_POS = (142, 233)

# Click this to open the pet's interaction menu (pet/weather/feed/dress up/
# tricks/pick up/ride/fly icons arranged around the pet).
FOCUS_PET_POS = (1114, 692)

# The 'choose' need's button is matched by its exact color rather than shape,
# since it's just a plain circle with no distinguishing icon. Given as (R, G, B).
CHOOSE_BUTTON_COLOR = (181, 6, 254)
CHOOSE_SLOW_MOVE_DURATION = 1.0  # deliberate, slow mouse travel to the found button
CHOOSE_HOVER_WAIT = 2.0          # hover over the found button this long before the real click
CHOOSE_DISMISS_WAIT = 1.0        # pause after the first center click, before the hover-and-click dismiss

# The paycheck popup's CASH OUT button, matched by exact color the same way
# as CHOOSE_BUTTON_COLOR above. TODO: sample the real RGB from your own
# screen - this is an uncalibrated placeholder and will not match yet.
PAYCHECK_CASHOUT_COLOR = (74, 198, 85)
PAYCHECK_DISMISS_POS_1 = (946, 679)
PAYCHECK_DISMISS_POS_2 = (948, 627)

# Ride need: positions for the backpack -> vehicles -> first vehicle -> equip
# sequence, plus how long to hold each step.
RIDE_BACKWARD_DURATION = 1.0   # step back before pressing e to mount
RIDE_WAIT_AFTER_E = 5.0        # wait after e before opening the backpack
RIDE_FORWARD_DURATION = 2.0    # walk forward briefly after mounting, before the backpack
RIDE_VEHICLES_POS = (816, 804)
RIDE_FIRST_VEHICLE_POS = (976, 708)
RIDE_EQUIP_POS = (1062, 814)
RIDE_WALK_DURATION = 40.0      # total time spent walking back and forth while riding

# Backpack-teleport sequence, shared by every teleport destination: open
# backpack -> category tab -> two more fixed clicks that confirm/execute
# the teleport -> wait for it to take effect -> walk backward to clear the
# landing spot. Only the category tab differs per destination (passed into
# teleport_to() as `category_pos`); everything else is always the same,
# hence "general".
TELEPORT_WAIT = 5.0               # wait after the teleport click, for it to take effect
TELEPORT_SETTLE_WAIT = 7.0         # wait after stepping back, for the landing to settle
GENERAL_TELEPORT_POS_2 = (895, 705)
GENERAL_TELEPORT_POS_3 = (1048, 658)
TELEPORT_BACK_DURATION = 1.0      # how long to hold 's' to clear the landing spot

TELEPORT_PETS_TAB_POS = (817, 713)       # nursery: pets tab
TELEPORT_VEHICLES_TAB_POS = (813, 810)   # dealership: vehicles tab
TELEPORT_FOOD_TAB_POS = (753, 804)       # supermarket: food tab
SICK_FINAL_CLICK_POS = (1045, 660)       # click after the sick need's walk

# Needs that teleport somewhere and walk (see TeleportWalkNeedHandler), then
# wait for the need to clear. Each entry: where to teleport, the
# (key, seconds) holds to perform in order, and optionally a `final_click`
# position to click after the last hold. Pizza and camping durations are
# placeholders until tuned.
TELEPORT_WALK_NEEDS = {
    "bored":   dict(teleport_pos=TELEPORT_PETS_TAB_POS,     steps=(("w", 16.0), ("a", 10.0))),
    "beach":   dict(teleport_pos=TELEPORT_PETS_TAB_POS,     steps=(("a", 27.0),)),
    "school":  dict(teleport_pos=TELEPORT_PETS_TAB_POS,     steps=(("w", 1.2), ("a", 10.0))),
    "cafe":    dict(teleport_pos=TELEPORT_VEHICLES_TAB_POS, steps=(("a", 3.1), ("s", 15.0))),
    "salon":   dict(teleport_pos=TELEPORT_VEHICLES_TAB_POS, steps=(("a", 2.5), ("w", 15.0))),
    "pizza":   dict(teleport_pos=TELEPORT_VEHICLES_TAB_POS, steps=(("w", 2.0), ("a", 6.5), ("s", 3.0))),
    "camping": dict(teleport_pos=TELEPORT_PETS_TAB_POS,     steps=(("w", 0.1), ("d", 3.0), ("s", 2.2), ("d", 15.0), ("w",20.0))),
    "sick":    dict(teleport_pos=TELEPORT_PETS_TAB_POS,     steps=(("w", 2.5), ("d", 5.0), ("w", 1.5)),
                    final_click=SICK_FINAL_CLICK_POS),
}
NEED_GONE_MAX_WAIT = 60.0        # most a teleport need waits for its icon to disappear
NEED_GONE_POLL_INTERVAL = 5.0    # how often to re-check for the icon during that wait
NEED_GONE_CONFIRMATIONS = 3      # consecutive checks that must all miss the icon before it counts as cleared
NEED_GONE_CONFIRM_INTERVAL = 1.0 # pause between those confirming checks

# Game key bindings
KEY_BACKPACK = "b"
KEY_MOUNT = "e"
KEY_ZOOM_IN = "i"
MOVE_KEYS = ("w", "a", "s", "d")
RESPAWN_KEYS = ("esc", "r", "enter")

# Window focus click (near top edge, right of center)
FOCUS_CLICK_X_PERCENT = 0.75
FOCUS_CLICK_Y = 5

# Jitter click (click, nudge mouse a few pixels, click again)
JITTER_PIXELS = 5

# Hover move: wiggle so Roblox registers real mouse movement
HOVER_NUDGE_PIXELS = 2

# Need-icon detection (top strip of screen)
NEED_ICON_TOP_PERCENT = 0.15        # fraction of screen height searched for icons
NEED_ICON_WIDTH_PERCENT = 0.70      # fraction of screen width searched, from the left edge -
                                     # keeps the GUI panel (docked top-right) out of frame
NEED_ICON_BLANK_HEIGHT = 0.15       # top-left UI cluster to ignore (height)
NEED_ICON_BLANK_WIDTH = 0.20        # top-left UI cluster to ignore (width)
NEED_ICON_MIN_RADIUS = 24           # px, icon ring radius range searched for (Hough circles)
NEED_ICON_MAX_RADIUS = 34
NEED_ICON_MIN_DISTANCE = 40         # px, minimum spacing between two icon centers
NEED_ICON_BLUR_KERNEL = 5           # median blur applied before circle detection
NEED_ICON_HOUGH_EDGE = 100          # Hough: Canny upper edge threshold
NEED_ICON_HOUGH_VOTES = 25          # Hough: accumulator votes needed to accept a circle
CIRCULARITY_EPSILON = 1e-6          # avoids dividing by zero when scoring circularity
PIXEL_MAX = 255                     # max value of an 8-bit pixel channel

# Need-icon matching (shape comparison, color-independent)
ICON_MATCH_THRESHOLD = 0.93
ICON_BW_THRESHOLD = 230            # only near-white pixels survive B/W conversion
ICON_CLAHE_CLIP = 3.0
ICON_CLAHE_TILE = (8, 8)
ICON_COMPARE_SIZE = (32, 32)
ICON_CROP_RADIUS = 28              # px, icon radius that saved/compared crops are cut at (plus ICON_EXTRACT_PADDING)
ICON_SHIFT_TOLERANCE = 2           # px, how far off-center a detection may be and still match

# Button detection (middle band of screen)
BUTTON_BAND_Y = (0.40, 0.70)
BUTTON_BAND_X = (0.25, 0.75)
BUTTON_MIN_AREA = 150
BUTTON_MIN_CIRCULARITY = 0.65
BUTTON_MAX_COUNT = 5
BUTTON_NAMES = ["hungry", "thirsty", "dirty", "potty", "sleepy"]
BUTTON_LOOSE_AREA_FACTOR = 0.7         # how much looser than BUTTON_MIN_AREA a candidate may be
BUTTON_MAX_AREA_FRACTION = 0.5         # reject a blob covering more than this fraction of the band
BUTTON_MIN_RADIUS = 5                  # reject a candidate smaller than this radius (px)
BUTTON_LOOSE_CIRCULARITY_FACTOR = 0.8  # how much looser than BUTTON_MIN_CIRCULARITY a candidate may be
BUTTON_PURPLE_DILATE_ITERATIONS = 3    # dilation used to build the outline search zone
BUTTON_PURPLE_ERODE_ITERATIONS = 1     # erosion used to build the outline search zone
BUTTON_OUTLINE_PADDING = 3             # px of padding around a candidate when scoring its white outline

# Debug drawing
DEBUG_NEED_MARKER_COLOR = (0, 255, 0)      # BGR
DEBUG_NEED_MARKER_THICKNESS = 2
DEBUG_BUTTON_MARKER_COLOR = (0, 0, 255)    # BGR
DEBUG_BUTTON_MARKER_RADIUS = 8
DEBUG_BUTTON_MARKER_THICKNESS = 2
DEBUG_BUTTON_LABEL_OFFSET = (-5, -15)      # px offset of the number label from its marker
DEBUG_BUTTON_LABEL_SCALE = 0.5

# Color ranges (HSV): lower, upper
PURPLE_RANGE = (np.array([130, 80, 80]), np.array([160, 255, 255]))
WHITE_RANGE = (np.array([0, 0, 200]), np.array([180, 40, 255]))

# GUI
GUI_GEOMETRY = "380x650+1533+110"   # size + position (docked top-right)
GUI_ALPHA = 0.95
GUI_FONT = "Courier"
GUI_BG = "#1a1a1a"
GUI_ACCENT = "#0d7377"
GUI_FG = "#fff"
GUI_START_COLOR = "#2ecc71"
GUI_STOP_COLOR = "#d62828"
GUI_LOOP_COLOR = "#2980b9"
GUI_RESPAWN_COLOR = "#8e44ad"
GUI_TEST_COLOR = "#e74c3c"
GUI_CONSOLE_BG = "#0a0a0a"
GUI_CONSOLE_FG = "#00ff00"
GUI_SQUARE_BUTTON_SIZE = 44         # px, start/stop buttons are square
GUI_STATUS_HEIGHT = 25              # px

STOP_FLAG = False

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
        print("\n[!] Roblox is no longer focused - stopping.")
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

def focus_roblox():
    """Bring the Roblox window to the front (no click)."""
    wins = [w for w in gw.getAllWindows() if "roblox" in w.title.lower()]
    if not wins:
        print("[!] Roblox not found")
        return False
    w = wins[0]
    if w.isMinimized:
        w.restore()
    w.activate()
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
    click_x = int(SCREEN_WIDTH * FOCUS_CLICK_X_PERCENT)
    pyautogui.moveTo(click_x, FOCUS_CLICK_Y)
    pydirectinput.click()
    time.sleep(FOCUS_CLICK_SETTLE_DELAY)
    return True

def grab_screen():
    """Screenshot the primary monitor as a BGR numpy array."""
    with mss.MSS() as sct:
        shot = np.array(sct.grab(sct.monitors[1]))
    return cv2.cvtColor(shot, cv2.COLOR_BGRA2BGR)

def find_exact_color(img, rgb):
    """Find the centroid of every pixel in `img` that matches `rgb` (an
    (R, G, B) tuple) exactly. Returns (x, y), or None if nothing matches.
    `img` is expected in BGR (as grab_screen() returns), so this converts
    the given RGB triple to BGR before comparing - an exact match means
    passing the same value as both the lower and upper bound to inRange."""
    r, g, b = rgb
    target_bgr = np.array([b, g, r])
    mask = cv2.inRange(img, target_bgr, target_bgr)
    ys, xs = np.where(mask > 0)
    if len(xs) == 0:
        return None
    return int(xs.mean()), int(ys.mean())

# ============================================================================
# STATE
# ============================================================================

# Detected action-button screen positions, keyed by need name (e.g. "hungry").
# Populated by refresh_button_mapping() each time the macro walks to the
# buttons, so this is a same-run cache, not persisted state - there's
# nothing gained from saving it to disk since it's never trusted across a
# run anyway (screen layout can change between sessions).
BUTTON_POSITIONS = {}

# The run number of the currently executing workflow (run_workflow() or
# run_workflow_loop()), set by next_run_number() at the start of each. Every
# log_run_event() call tags its line with this, so entries from different
# runs can be told apart in the shared RUN_LOG_PATH file.
CURRENT_RUN_NUMBER = None

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

def log_run_event(message):
    """Append one timestamped line to RUN_LOG_PATH, tagged with
    CURRENT_RUN_NUMBER."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(RUN_LOG_PATH, "a") as f:
        f.write(f"[{timestamp}] [RUN {CURRENT_RUN_NUMBER}] {message}\n")

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

def find_matching_need(icon_variants):
    """Compare a detected icon (its icon_variants() crops) against the saved
    needs, scoring each by its best-aligned variant. Returns (name, score)."""
    live_signatures = [icon_signature(v) for v in icon_variants]
    need_files = sorted(f for f in os.listdir(NEEDS_DIR) if f.endswith('.png'))
    if not need_files:
        return None, 0.0

    best_match, best_score = None, 0.0
    for need_file in need_files:
        saved_icon = cv2.imread(os.path.join(NEEDS_DIR, need_file))
        if saved_icon is None:
            continue
        saved_signature = icon_signature(saved_icon)
        score = max(compare_signatures(sig, saved_signature) for sig in live_signatures)
        if score > best_score:
            best_match, best_score = need_file[:-4], score

    if best_score >= ICON_MATCH_THRESHOLD:
        return best_match, best_score
    return None, best_score

def prompt_rename_need(icon_img, idx):
    """Save a new need icon (as high-contrast B/W) and ask the user to name it."""
    print("\n[!] NEW NEED DETECTED!")
    temp_path = os.path.join(NEEDS_DIR, f"temp_{idx}.png")
    cv2.imwrite(temp_path, preprocess_icon(icon_img))

    print("[!] Enter need name (hungry/thirsty/dirty/potty/sleepy/catch/walk/other): ", end="", flush=True)
    need_name = input().strip().lower() or f"unnamed_{idx}"

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

def wait_until_need_gone(need_name, max_wait=NEED_GONE_MAX_WAIT, poll_interval=NEED_GONE_POLL_INTERVAL):
    """Wait for `need_name`'s icon to stop being detected, for at most
    `max_wait` seconds - whichever happens first. The icon must be missing
    NEED_GONE_CONFIRMATIONS checks in a row (any sighting resets the count),
    so a single missed detection can't end the wait early. Interruptible."""
    print(f"[debug] waiting up to {max_wait}s for {need_name} to clear...")
    deadline = time.time() + max_wait
    misses = 0
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            print(f"[debug] {need_name} still showing after {max_wait}s, moving on")
            return
        wait_interruptible(min(NEED_GONE_CONFIRM_INTERVAL if misses else poll_interval, remaining))
        found_icons, full_img = detect_need_icons()
        visible = [name for _, _, name, _ in identify_icons(found_icons, full_img)]
        if need_name in visible:
            misses = 0
            continue
        misses += 1
        print(f"[debug] {need_name} not detected ({misses}/{NEED_GONE_CONFIRMATIONS})")
        if misses >= NEED_GONE_CONFIRMATIONS:
            print(f"[debug] {need_name} cleared")
            return

# ============================================================================
# CLICKING
# ============================================================================

def jitter_click(x, y, duration=0.1):
    '''
    """Click at (x, y), nudge the mouse a few pixels, then click again."""
    pyautogui.moveTo(x, y, duration=CLICK_MOVE_DURATION)
    time.sleep(CLICK_SETTLE_DELAY)
    pydirectinput.click()
    time.sleep(POST_CLICK_DELAY)

    offset_x = random.randint(-JITTER_PIXELS, JITTER_PIXELS)
    offset_y = random.randint(-JITTER_PIXELS, JITTER_PIXELS)
    new_x = max(0, min(SCREEN_WIDTH, x + offset_x))
    new_y = max(0, min(SCREEN_HEIGHT, y + offset_y))
    pydirectinput.moveTo(new_x, new_y, duration=CLICK_MOVE_DURATION)
    time.sleep(CLICK_SETTLE_DELAY)

    pydirectinput.click()
    time.sleep(POST_CLICK_DELAY)
    '''
    """hover_move() to (x, y), then click."""
    hover_move(x, y, duration)
    pydirectinput.click()
    time.sleep(POST_CLICK_DELAY)

def simple_click(x, y):
    """Simple click without jitter."""
    pyautogui.moveTo(x, y, duration=CLICK_MOVE_DURATION)
    time.sleep(CLICK_SETTLE_DELAY)
    pydirectinput.click()
    time.sleep(POST_CLICK_DELAY)

def hover_move(x, y, duration=CLICK_MOVE_DURATION):
    """Move the mouse to (x, y) over `duration` seconds using SendInput
    (pydirectinput), then nudge it a couple of pixels back onto the target.
    Roblox ignores pyautogui's SetCursorPos warps as hover movement and only
    reacts to real input events, so this is what makes a UI element register
    as hovered before it's clicked."""
    pydirectinput.moveTo(x, y, duration=duration)
    time.sleep(CLICK_SETTLE_DELAY)
    pydirectinput.moveTo(min(SCREEN_WIDTH, x + HOVER_NUDGE_PIXELS), y)
    time.sleep(CLICK_SETTLE_DELAY)
    pydirectinput.moveTo(x, y)
    time.sleep(CLICK_SETTLE_DELAY)

def hover_click(x, y, duration=CLICK_MOVE_DURATION):
    """hover_move() to (x, y), then click."""
    hover_move(x, y, duration)
    pydirectinput.click()
    time.sleep(POST_CLICK_DELAY)

def wait_interruptible(duration):
    """Sleep for `duration`, in STOP_CHECK_INTERVAL chunks, checking
    check_running() between each chunk. Raises StopRequested or FocusLost
    the moment either condition is noticed, instead of returning a bool the
    caller has to check after every call."""
    elapsed = 0.0
    while elapsed < duration:
        check_running()
        sleep_chunk = min(STOP_CHECK_INTERVAL, duration - elapsed)
        time.sleep(sleep_chunk)
        elapsed += sleep_chunk
    check_running()

def release_all_inputs():
    """Best-effort safety net: release every key this macro ever holds down,
    plus the mouse button. The try/finally blocks around each individual
    held key/button should already guarantee this, but this is called once
    more whenever a background task ends (run_async's worker finally block)
    as a last line of defense - a bug in a handler we haven't caught yet
    still shouldn't be able to leave an input stuck down in the game."""
    for key in MOVE_KEYS:
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
    click_basic_need_button() to use."""
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

def click_basic_need_button(need_name):
    """Click the action button mapped to a basic need (see is_basic_need()).
    Assumes refresh_button_mapping() has already been called this cycle.
    Returns True if a mapped button was actually found and clicked, False
    otherwise - the caller must not treat this need as resolved (or
    respawn) on a False return."""
    if need_name not in BUTTON_POSITIONS:
        print(f"[!] WARNING: no button mapped for '{need_name}'")
        return False
    button_x, button_y = BUTTON_POSITIONS[need_name]
    print(f"[!] CLICKING: {need_name}")
    click_button(button_x, button_y, need_name)
    wait_time = POST_NEED_CLICK_WAIT_SHORT if need_name in SHORT_WAIT_NEED_NAMES else POST_NEED_CLICK_WAIT
    print(f"[debug] waiting {wait_time}s before next action...")
    wait_interruptible(wait_time)
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

def walk_alternating(direction_pair, total_duration, step_duration=WALK_ALTERNATING_STEP):
    """Alternate between the two given keys, holding each for `step_duration`,
    for a total of `total_duration`. Shared by the walk need (a/d, left-right)
    and the ride need (w/s, forward-back) - same pattern, different keys."""
    print(f"[debug] walking alternating {direction_pair} pattern for {total_duration}s...")
    if not focus_roblox():
        return

    direction_idx = 0
    elapsed = 0.0
    start_time = time.time()

    while elapsed < total_duration:
        direction = direction_pair[direction_idx % 2]
        print(f"[debug] step {direction_idx + 1}: {direction} for {step_duration}s...")
        hold_key(direction, step_duration)
        wait_interruptible(KEY_STEP_GAP)

        direction_idx += 1
        elapsed = time.time() - start_time

    print("[debug] alternating walk complete")

# ============================================================================
# NEED HANDLERS
# ============================================================================
# A need with no dedicated handler is a "basic" need (see is_basic_need()):
# it's satisfied by walking to the action buttons and clicking the one that
# matches its name - hungry/thirsty/dirty/potty/sleepy today, and any future
# need the macro is taught that also works the same way. Needs with dedicated
# logic (catch, pet, choose, ride, walk) get their own handler class below,
# so each can be customized independently without touching the others.

class NeedHandler(ABC):
    """Reacts to one detected need that needs more than a basic button click.
    Subclass this to add a new such need behavior."""


    @abstractmethod
    def handle(self):
        """Perform the action for this need. Return True if it was handled."""
        raise NotImplementedError

class WalkNeedHandler(NeedHandler):
    """The 'walk' need is satisfied by walking left-right for a while."""

    def handle(self):
        print("[!] WALK NEED")
        walk_alternating(("a", "d"), WALK_TOTAL_DURATION)
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
            simple_click(*EMPTY_POS)
            wait_interruptible(CATCH_EMOTE_DELAY)

        # Unequip the toy
        print("[debug] unequipping toy...")
        simple_click(*CATCH_UNEQUIP_POS)

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
        jitter_click(*FOCUS_PET_POS)
        wait_interruptible(UI_SETTLE)
        print(f"[debug] attempting to pet for {PET_CIRCLE_DURATION}s...")
        # Move to starting position before pressing down
        pydirectinput.moveTo(SCREEN_CENTER_X, SCREEN_CENTER_Y - PET_CIRCLE_RADIUS)
        time.sleep(PET_SETTLE_DELAY)
        # Click the center to focus
        jitter_click(SCREEN_CENTER_X, SCREEN_CENTER_Y)
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
                y = int(SCREEN_CENTER_Y + PET_CIRCLE_RADIUS * np.sin(angle))
                # Use pydirectinput for better game compatibility
                pydirectinput.moveTo(SCREEN_CENTER_X, y)
                time.sleep(PET_CIRCLE_STEP_MOVE_DURATION)
        finally:
            # Always release, even if interrupted
            pydirectinput.mouseUp()
        print("[!] Pet complete!")
        return True

class ChooseNeedHandler(NeedHandler):
    """The 'choose' need: jitter-click to open the pet's interaction menu,
    find the button with a distinctive exact color (it has no distinguishing
    icon, so shape detection doesn't apply here), move to it slowly rather
    than jumping straight there, click it, then click the middle of the
    screen to dismiss the menu."""

    def handle(self):
        print("[!] CHOOSE NEED")
        if not focus_roblox():
            return False

        print("[debug] focusing pet...")
        hover_click(*FOCUS_PET_POS)
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
        hover_click(SCREEN_CENTER_X, SCREEN_CENTER_Y, duration=CHOOSE_SLOW_MOVE_DURATION)

        print("[!] Choose complete!")
        return True

class RideNeedHandler(NeedHandler):
    """The 'ride' need: step back, mount a vehicle from the backpack, then
    walk back and forth for a while astride it."""

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

        # Open backpack, select and equip the first vehicle
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

        # Walk back and forth (forward/backward, not left/right) while riding
        walk_alternating(("w", "s"), RIDE_WALK_DURATION)

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
    TELEPORT_WALK_NEEDS."""

    def __init__(self, name):
        self.name = name
        self.config = TELEPORT_WALK_NEEDS[name]

    def handle(self):
        print(f"[!] {self.name.upper()} NEED")
        if not focus_roblox():
            return False

        teleport_to(self.config["teleport_pos"])

        for i, (key, duration) in enumerate(self.config["steps"]):
            if i:
                time.sleep(KEY_STEP_GAP*40)
            print(f"[debug] holding {key} for {duration}s...")
            hold_key(key, duration)

        final_click = self.config.get("final_click")
        if final_click:
            pydirectinput.press("e")
            wait_interruptible(7)
            print(f"[debug] clicking {final_click}...")
            hover_click(*final_click)

        wait_until_need_gone(self.name)

        print(f"[!] {self.name.capitalize()} complete!")
        return True

# Handler factory (class, or partial of one) for each special need.
# get_special_need_handler() uses this plus ENABLED_NEEDS below to decide
# what to do with a detected special need.
SPECIAL_NEED_HANDLER_CLASSES = {
    "catch": CatchNeedHandler,
    "pet": PetNeedHandler,
    "choose": ChooseNeedHandler,
    "ride": RideNeedHandler,
    **{name: partial(TeleportWalkNeedHandler, name) for name in TELEPORT_WALK_NEEDS},
}

# Every handler the GUI's Debug tab can run on its own: the special needs
# above plus walk, which is matched by prefix and so isn't in the dict.
DEBUG_HANDLERS = {"walk": WalkNeedHandler, **SPECIAL_NEED_HANDLER_CLASSES}

# Special needs enabled for automatic processing - the single place that
# decides whether a detected special need actually runs. A need not listed
# here is still detected and matched, but logged and skipped (not resolved)
# when it comes up. "walk" covers every
# need name that starts with "walk" (see is_basic_need() below).
ENABLED_NEEDS = {"catch", "pet", "ride", "walk", "beach", "bored", "school", "cafe", "salon", "choose", "sick", "pizza"}

def is_basic_need(need_name):
    """A basic need has no dedicated handler - it's satisfied by walking to
    the action buttons and clicking the one matching its name."""
    return need_name in BUTTON_NAMES

def get_special_need_handler(need_name):
    """Return the handler for a need with dedicated logic. Only call this
    when is_basic_need(need_name) is False. Returns None if this need type
    is not currently enabled (see ENABLED_NEEDS)."""
    if need_name.startswith("walk"):
        return WalkNeedHandler() if "walk" in ENABLED_NEEDS else None
    if need_name not in ENABLED_NEEDS:
        return None
    handler_cls = SPECIAL_NEED_HANDLER_CLASSES.get(need_name)
    return handler_cls() if handler_cls else None

def process_needs():
    """Detect needs on screen and resolve them one at a time: for each
    matched need, walk to the buttons first only if it's a basic one
    (hungry/thirsty/dirty/potty/sleepy, or any other need with no dedicated
    handler - see is_basic_need()); otherwise its own handler runs directly
    (catch, pet, choose, ride, walk). Either way, the character respawns
    immediately after that single need is *actually* resolved, before
    moving on to the next matched need - never batched, and never respawned
    for a need that wasn't really handled (a disabled special, or a basic
    need whose button wasn't found this pass - e.g. because
    refresh_button_mapping() failed to detect any buttons at all). Returns
    True if anything was actually resolved this check, False if there was
    nothing to do."""
    if not focus_roblox_click():
        return False

    found_icons, full_img = detect_need_icons(save_debug=True)
    if not found_icons:
        print("[debug] no need icons detected")
        return False

    print(f"[debug] found {len(found_icons)} icon(s)")

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

        print(f"\n[!] MATCHED: {need_name} (score: {score:.4f})")
        matched_needs.append(need_name)

    log_run_event(f"detected: {', '.join(matched_needs) if matched_needs else 'none matched'}")

    if not matched_needs:
        return False

    resolved = False

    for need_name in matched_needs:
        check_running()

        if is_basic_need(need_name):
            walk_to_buttons()
            if not refresh_button_mapping() or not click_basic_need_button(need_name):
                print(f"[debug] could not resolve '{need_name}' this pass, skipping")
                continue
        else:
            handler = get_special_need_handler(need_name)
            if handler is None:
                print(f"[debug] {need_name} is disabled, skipping")
                continue
            handler.handle()

        respawn_character()
        resolved = True

    return resolved

# ============================================================================
# PAYCHECK
# ============================================================================

def detect_paycheck():
    """Detect the paycheck popup by its CASH OUT button's exact color and,
    if present, dismiss it. Returns True if the popup was detected and
    dismissed, False otherwise."""
    global PAYCHECK_RECEIVED

    img = grab_screen()
    if find_exact_color(img, PAYCHECK_CASHOUT_COLOR) is None:
        return False

    print("[debug] paycheck popup detected, dismissing...")
    hover_click(*PAYCHECK_DISMISS_POS_1)
    simple_click(*EMPTY_POS)
    hover_click(*PAYCHECK_DISMISS_POS_2)
    PAYCHECK_RECEIVED = True
    return True

def unscrew():
    """Runs once per cycle, right after check_stop(). Currently only checks
    for the paycheck popup; more checks may be added here later."""
    detect_paycheck()

# ============================================================================
# WORKFLOWS
# ============================================================================

def run_full_cycle():
    """One full cycle: keep checking for needs, waiting NEED_CHECK_RETRY_DELAY
    between checks whenever none are found, until something is detected and
    handled."""
    while True:
        # check_stop() only, not check_running() - clicking [START]/[LOOP]
        # in this Python GUI is what has focus at this exact instant, and
        # process_needs() below is what brings Roblox to the front. Once
        # that succeeds, every wait from here on does check_running().
        check_stop()
        unscrew()
        print("\n[debug] checking needs...")
        if process_needs():
            return
        print(f"[debug] no needs found, waiting {NEED_CHECK_RETRY_DELAY}s...")
        wait_interruptible(NEED_CHECK_RETRY_DELAY)

def run_workflow():
    """Run a single cycle: wait for a need to appear, then handle it."""
    global STOP_FLAG, CURRENT_RUN_NUMBER
    STOP_FLAG = False
    CURRENT_RUN_NUMBER = next_run_number()
    log_run_event("WORKFLOW started")
    print("\n" + "=" * 50)
    print(f"[WORKFLOW] Starting (run {CURRENT_RUN_NUMBER})")
    print("=" * 50)
    run_full_cycle()
    log_run_event("WORKFLOW done")
    print("\n[WORKFLOW] Done\n")

def run_workflow_loop():
    """Respawn once, then repeat the workflow continuously. The only way this
    loop ever ends is via a stop request - there's no other exit condition,
    so StopRequested is expected here, not an error. It's allowed to
    propagate on up to run_async()'s worker (via the bare `finally`, not
    `except`) so the GUI still reports [STOPPED] correctly; the finally
    just prints locally first."""
    global STOP_FLAG, CURRENT_RUN_NUMBER
    STOP_FLAG = False
    CURRENT_RUN_NUMBER = next_run_number()
    log_run_event("LOOP started")
    print("\n" + "=" * 50)
    print(f"[LOOP] Starting continuous workflow (run {CURRENT_RUN_NUMBER})")
    print("=" * 50)

    # Respawn once up front so the loop always starts from a known state,
    # regardless of wherever the character happened to be standing.
    respawn_character()

    loop_num = 0
    try:
        while True:
            loop_num += 1
            print(f"\n[LOOP {loop_num}]")
            run_full_cycle()
            wait_interruptible(LOOP_DELAY)
    finally:
        log_run_event("LOOP stopped")
        print("\n[LOOP] Stopped\n")

# ============================================================================
# GUI
# ============================================================================

class DebugCapture:
    def __init__(self, text_widget):
        self.text = text_widget
        self.original_stdout = sys.stdout

    def write(self, msg):
        self.text.config(state=tk.NORMAL)
        self.text.insert(tk.END, msg)
        self.text.see(tk.END)
        self.text.config(state=tk.DISABLED)
        self.text.update()

    def flush(self):
        pass

    def restore(self):
        sys.stdout = self.original_stdout

class AdoptMeGUI:
    def __init__(self, root):
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

        self.capture = DebugCapture(self.debug_text)
        sys.stdout = self.capture

        print("\n" + "=" * 60)
        print("ADOPT ME MACRO - CONTROL PANEL")
        print("=" * 60)
        print(f"Needs:  {NEEDS_DIR}")
        print(f"Debug:  {DEBUG_DIR}")
        print("=" * 60 + "\n")

        self.is_running = False

    def create_ui(self):
        """Build every widget. Any button that kicks off a background action
        (via run_async) is appended to self.action_buttons so run_async can
        disable/re-enable all of them together as a group. The [STOP] button
        is deliberately excluded - it must stay clickable while something
        is running, since that's the whole point of it."""
        # Main buttons
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.X, padx=8, pady=(8, 0))
        btn_frame = tk.Frame(notebook, bg=self.bg)
        debug_tab = tk.Frame(notebook, bg=self.bg)
        notebook.add(btn_frame, text="Main")
        notebook.add(debug_tab, text="Debug")

        self.action_buttons = []  # every button that starts a background task

        # One row: [START] (single cycle, square) on the left, [LOOP] (continuous
        # workflow) expanding to fill the center, [STOP] (square) on the right.
        # All three sit in their own fixed-HEIGHT container (pack_propagate(False))
        # so they're always the same height regardless of font metrics - a
        # Button's own width/height (character units) don't scale consistently
        # across font sizes, and an unconstrained button's natural height grows
        # with its font size, which is what broke this row last time the icon
        # font got bigger. The two side containers also fix their WIDTH (making
        # them equal squares); the center container only fixes height and
        # otherwise expands to fill the remaining width.
        NO_BORDER = dict(bd=0, highlightthickness=0)  # flat edges, no default Tk bevel/focus ring

        main_row = tk.Frame(btn_frame, bg=self.bg)
        main_row.pack(fill=tk.X, pady=4)

        start_container = tk.Frame(main_row, width=GUI_SQUARE_BUTTON_SIZE, height=GUI_SQUARE_BUTTON_SIZE, bg=self.bg)
        start_container.pack(side=tk.LEFT, padx=(0, 4))
        start_container.pack_propagate(False)

        # Single cycle: icon + "1" as plain button text (no overlay Label -
        # a Label placed on top of the button kept showing a visible seam/
        # box behind it despite matching colors, so this is just simpler).
        btn_start = tk.Button(start_container, text="\U0001F5041", command=self.run_workflow,
                               font=(GUI_FONT, 14, "bold"), bg=GUI_START_COLOR, fg=self.fg,
                               cursor="hand2", **NO_BORDER)
        btn_start.pack(fill=tk.BOTH, expand=True)
        self.action_buttons.append(btn_start)

        # Not added to action_buttons: must remain clickable while a workflow is running
        stop_container = tk.Frame(main_row, width=GUI_SQUARE_BUTTON_SIZE, height=GUI_SQUARE_BUTTON_SIZE, bg=self.bg)
        stop_container.pack(side=tk.RIGHT, padx=(4, 0))
        stop_container.pack_propagate(False)

        self.btn_stop = tk.Button(stop_container, text="■", command=self.stop,
                                   font=(GUI_FONT, 16, "bold"), bg=GUI_STOP_COLOR, fg=self.fg,
                                   cursor="hand2", **NO_BORDER)
        self.btn_stop.pack(fill=tk.BOTH, expand=True)

        loop_container = tk.Frame(main_row, height=GUI_SQUARE_BUTTON_SIZE, bg=self.bg)
        loop_container.pack(side=tk.LEFT, fill=tk.X, expand=True)
        loop_container.pack_propagate(False)

        btn_loop = tk.Button(loop_container, text="\U0001F504", command=self.run_workflow_loop,
                              font=(GUI_FONT, 20, "bold"), bg=GUI_LOOP_COLOR, fg=self.fg,
                              cursor="hand2", **NO_BORDER)
        btn_loop.pack(fill=tk.BOTH, expand=True)
        self.action_buttons.append(btn_loop)

        tk.Frame(btn_frame, bg=self.accent, height=2).pack(fill=tk.X, pady=4)

        tk.Label(btn_frame, text="Functions", font=(GUI_FONT, 9, "bold"), bg=self.bg, fg=self.accent).pack(anchor=tk.W)

        btn_respawn = tk.Button(btn_frame, text="Respawn", command=lambda: self.run_async(respawn_character),
                                 font=(GUI_FONT, 9), bg=GUI_RESPAWN_COLOR, fg=self.fg, height=1, cursor="hand2")
        btn_respawn.pack(fill=tk.X, pady=2)
        self.action_buttons.append(btn_respawn)

        # Debug tab: one button per need handler, regardless of whether it's in
        # ENABLED_NEEDS, so any handler can be run on its own.
        self.build_debug_tab(debug_tab)

        # Debug console
        debug_frame = tk.Frame(self.root, bg=self.bg)
        debug_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        tk.Label(debug_frame, text="Output", font=(GUI_FONT, 9, "bold"), bg=self.bg, fg=self.accent).pack(anchor=tk.W, pady=(0, 3))

        self.debug_text = scrolledtext.ScrolledText(debug_frame, height=14, width=45, bg=GUI_CONSOLE_BG, fg=GUI_CONSOLE_FG,
                                                     font=(GUI_FONT, 7), state=tk.DISABLED)
        self.debug_text.pack(fill=tk.BOTH, expand=True)

        # Status bar
        status = tk.Frame(self.root, bg=self.accent, height=GUI_STATUS_HEIGHT)
        status.pack(fill=tk.X, side=tk.BOTTOM)
        status.pack_propagate(False)
        self.status = tk.Label(status, text="Ready", font=(GUI_FONT, 8), bg=self.accent, fg=self.fg)
        self.status.pack(anchor=tk.W, padx=8, pady=3)

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
                final_status = "[STOPPED]"
            except FocusLost:
                # Same reasoning as StopRequested above - must come before
                # the generic Exception handler.
                final_status = "[STOPPED: Roblox not focused]"
            except Exception as e:
                print(f"\n[ERROR] {e}")
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

    def run_workflow(self):
        self.run_async(run_workflow)

    def run_workflow_loop(self):
        self.run_async(run_workflow_loop)

    def build_debug_tab(self, parent):
        """Fill the Debug tab with a two-column grid of [TEST] buttons, one
        per entry in DEBUG_HANDLERS."""
        tk.Label(parent, text="Run a need handler on its own", font=(GUI_FONT, 9, "bold"),
                 bg=self.bg, fg=self.accent).grid(row=0, column=0, columnspan=2, sticky=tk.W, padx=4, pady=(4, 2))
        for col in range(2):
            parent.grid_columnconfigure(col, weight=1, uniform="debug")
        for i, (name, handler_cls) in enumerate(DEBUG_HANDLERS.items()):
            btn = tk.Button(parent, text=name.capitalize(),
                            command=lambda n=name, c=handler_cls: self.test_handler(n, c),
                            font=(GUI_FONT, 9), bg=GUI_TEST_COLOR, fg=self.fg, cursor="hand2")
            btn.grid(row=1 + i // 2, column=i % 2, sticky=tk.EW, padx=2, pady=2)
            self.action_buttons.append(btn)

    def test_handler(self, name, handler_cls):
        """Run one need handler on its own, outside the normal need-detection
        flow, so it can be tried even when it isn't in ENABLED_NEEDS."""
        def test():
            print(f"\n[TEST] Running {name} handler...")
            handler_cls().handle()
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
