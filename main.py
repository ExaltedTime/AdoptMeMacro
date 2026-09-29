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
from pathlib import Path
import tkinter as tk
from tkinter import scrolledtext

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
CATCH_ENABLED = True           # catch need handler is live (set to False to disable)
PET_ENABLED = True            # disable pet need handler (set to True to re-enable)
CHOOSE_ENABLED = False         # disable choose need handler (set to True to re-enable)
SAVE_NEW_NEEDS = False
MATCH_ONLY_TOP_HALF = True
PAYCHECK_RECEIVED = False      # set True once detect_paycheck() has dismissed the paycheck popup
BEACH_ENABLED = True          # disable beach need handler (set to True to enable)
BORED_ENABLED = True          # disable bored need handler (set to True to enable)

# Timing (seconds)
RESPAWN_KEY_DURATION = 0.05    # how long each respawn key is held
RESPAWN_WAIT = 4.0             # settle time after respawning, before it's usable
WALK_TO_BUTTONS_DURATION = 0.8 # time spent walking forward to reach the action buttons
WALK_ALTERNATING_STEP = 1.0    # duration of each a/d press in alternating walk pattern
WALK_STEP_GAP = 0.1            # pause between steps in the alternating walk pattern
WALK_TOTAL_DURATION = 35.0     # total duration to keep walking back and forth
UI_SETTLE = 0.5                # generic pause for UI to catch up
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
CATCH_CLICK_DELAY = 0.5        # delay between catch sequence clicks
CATCH_THROW_COUNT = 3          # number of times the toy is thrown
CATCH_SCROLL_TIME = 3        # mouse wheel notches scrolled up before each throw
PET_CIRCLE_DURATION = 10.0     # how long to make circles with mouse
PET_CIRCLE_RADIUS = 100                # radius (px) of the circle traced around screen center
PET_CIRCLE_START_MOVE_DURATION = 0.1   # time to move to the circle's starting point
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

# Teleport-to-nursery sequence, shared by the bored and beach need handlers:
# open backpack -> category -> nursery -> teleport, then step back briefly
# to clear the landing spot.
TELEPORT_CLICK_DELAY = 0.5        # pause between each step of the sequence
TELEPORT_NURSERY_POS_1 = (817, 713)
TELEPORT_NURSERY_POS_2 = (895, 705)
TELEPORT_NURSERY_POS_3 = (1048, 658)
TELEPORT_NURSERY_WAIT = 5.0       # wait after teleporting, before stepping back
TELEPORT_NURSERY_BACK_DURATION = 1.0  # how long to hold 's' afterward

# Bored need: walk forward, then left, then wait it out at the nursery.
BORED_WALK_FORWARD_DURATION = 17.0
BORED_WALK_LEFT_DURATION = 10.0
BORED_WAIT_AFTER_WALK = 60.0

# Beach need: walk left, then wait it out at the nursery.
BEACH_WALK_LEFT_DURATION = 27.0
BEACH_WAIT_AFTER_WALK = 60.0

# Window focus click (near top edge, right of center)
FOCUS_CLICK_X_PERCENT = 0.75
FOCUS_CLICK_Y = 5

# Jitter click (click, nudge mouse a few pixels, click again)
JITTER_PIXELS = 5

# Need-icon detection (top strip of screen)
NEED_ICON_TOP_PERCENT = 0.15        # fraction of screen height searched for icons
NEED_ICON_WIDTH_PERCENT = 0.70      # fraction of screen width searched, from the left edge -
                                     # keeps the GUI panel (docked top-right) out of frame
NEED_ICON_BLANK_HEIGHT = 0.15       # top-left UI cluster to ignore (height)
NEED_ICON_BLANK_WIDTH = 0.20        # top-left UI cluster to ignore (width)
NEED_ICON_MIN_AREA = 50
NEED_ICON_MIN_CIRCULARITY = 0.6

# Need-icon matching (shape comparison, color-independent)
ICON_MATCH_THRESHOLD = 0.92
ICON_BW_THRESHOLD = 230            # only near-white pixels survive B/W conversion
ICON_CLAHE_CLIP = 3.0
ICON_CLAHE_TILE = (8, 8)
ICON_COMPARE_SIZE = (32, 32)

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
BLUE_RANGE = (np.array([95, 100, 100]), np.array([125, 255, 255]))
PURPLE_RANGE = (np.array([130, 80, 80]), np.array([160, 255, 255]))
RED_RANGE_1 = (np.array([0, 100, 100]), np.array([10, 255, 255]))
RED_RANGE_2 = (np.array([170, 100, 100]), np.array([180, 255, 255]))
YELLOW_RANGE = (np.array([15, 100, 100]), np.array([35, 255, 255]))
GREEN_RANGE = (np.array([40, 80, 80]), np.array([80, 255, 255]))
CYAN_RANGE = (np.array([85, 80, 80]), np.array([110, 255, 255]))
WHITE_RANGE = (np.array([0, 0, 200]), np.array([180, 40, 255]))

NEED_ICON_COLOR_RANGES = [BLUE_RANGE, PURPLE_RANGE, RED_RANGE_1, RED_RANGE_2,
                           YELLOW_RANGE, GREEN_RANGE, CYAN_RANGE]

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
    _, binary = cv2.threshold(enhanced, ICON_BW_THRESHOLD, 255, cv2.THRESH_BINARY)
    return binary

def compare_icons(icon1, icon2):
    """Return a 0-1 similarity score between two icon images.
    If MATCH_ONLY_TOP_HALF is True, only compares the top half of the images.
    """
    proc1 = cv2.resize(preprocess_icon(icon1), ICON_COMPARE_SIZE)
    proc2 = cv2.resize(preprocess_icon(icon2), ICON_COMPARE_SIZE)

    # If MATCH_ONLY_TOP_HALF flag is set, crop to top half
    if globals().get('MATCH_ONLY_TOP_HALF', False):
        height = proc1.shape[0]
        top_half = height // 2
        proc1 = proc1[:top_half, :]
        proc2 = proc2[:top_half, :]

    mse = np.mean((proc1.astype(float) - proc2.astype(float)) ** 2)
    mse_sim = 1.0 - (mse / (255 ** 2))

    hist1 = cv2.normalize(cv2.calcHist([proc1], [0], None, [256], [0, 256]), None).flatten()
    hist2 = cv2.normalize(cv2.calcHist([proc2], [0], None, [256], [0, 256]), None).flatten()
    hist_sim = 1.0 / (1.0 + cv2.compareHist(hist1, hist2, cv2.HISTCMP_BHATTACHARYYA))

    return (mse_sim + hist_sim) / 2.0

# ============================================================================
# NEED ICON DETECTION
# ============================================================================

def extract_icon(img, cx, cy, radius, padding=ICON_EXTRACT_PADDING):
    r = radius + padding
    x0, x1 = max(0, cx - r), min(img.shape[1], cx + r)
    y0, y1 = max(0, cy - r), min(img.shape[0], cy + r)
    return img[y0:y1, x0:x1].copy()

def detect_need_icons(save_debug=False):
    """Detect circular need icons at the top of the screen (any color)."""
    img = grab_screen()
    h, w = img.shape[:2]

    top_strip = img[0:int(h * NEED_ICON_TOP_PERCENT), 0:int(w * NEED_ICON_WIDTH_PERCENT)].copy()
    top_strip[0:int(h * NEED_ICON_BLANK_HEIGHT), 0:int(w * NEED_ICON_BLANK_WIDTH)] = 0

    hsv = cv2.cvtColor(top_strip, cv2.COLOR_BGR2HSV)
    mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
    for lower, upper in NEED_ICON_COLOR_RANGES:
        mask = cv2.bitwise_or(mask, cv2.inRange(hsv, lower, upper))

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    found = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < NEED_ICON_MIN_AREA:
            continue
        (cx, cy), radius = cv2.minEnclosingCircle(c)
        circularity = area / (np.pi * radius * radius + 1e-6)
        if circularity >= NEED_ICON_MIN_CIRCULARITY:
            found.append((int(cx), int(cy), int(radius)))

    if save_debug:
        debug_img = top_strip.copy()
        for cx, cy, r in found:
            cv2.circle(debug_img, (cx, cy), r, DEBUG_NEED_MARKER_COLOR, DEBUG_NEED_MARKER_THICKNESS)
        cv2.imwrite(os.path.join(DEBUG_DIR, "debug_needs.png"), debug_img)

    return found, img

def find_matching_need(icon_img):
    """Compare a detected icon against saved needs. Returns (name, score)."""
    need_files = sorted(f for f in os.listdir(NEEDS_DIR) if f.endswith('.png'))
    if not need_files:
        return None, 0.0

    best_match, best_score = None, 0.0
    for need_file in need_files:
        saved_icon = cv2.imread(os.path.join(NEEDS_DIR, need_file))
        if saved_icon is None:
            continue
        score = compare_icons(icon_img, saved_icon)
        if score > best_score:
            best_match, best_score = need_file[:-4], score

    if best_score >= ICON_MATCH_THRESHOLD:
        return best_match, best_score
    return None, best_score

def base_need_name(matched_need):
    """Strip the 'need_' prefix used when an icon was saved."""
    return matched_need.split('_', 1)[1] if '_' in matched_need else matched_need

def prompt_rename_need(icon_img, idx):
    """Save a new need icon (as high-contrast B/W) and ask the user to name it."""
    print("\n[!] NEW NEED DETECTED!")
    temp_path = os.path.join(NEEDS_DIR, f"temp_{idx}.png")
    cv2.imwrite(temp_path, preprocess_icon(icon_img))

    print("[!] Enter need name (hungry/thirsty/dirty/potty/sleepy/catch/walk/other): ", end="", flush=True)
    need_name = input().strip().lower() or f"need_{idx}"

    final_name = f"need_{need_name}"
    os.rename(temp_path, os.path.join(NEEDS_DIR, f"{final_name}.png"))
    print(f"[!] Saved: {final_name}")
    return final_name

# ============================================================================
# CLICKING
# ============================================================================

def jitter_click(x, y):
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

def simple_click(x, y):
    """Simple click without jitter."""
    pyautogui.moveTo(x, y, duration=CLICK_MOVE_DURATION)
    time.sleep(CLICK_SETTLE_DELAY)
    pydirectinput.click()
    time.sleep(POST_CLICK_DELAY)

def scroll_wheel_up(x, y, amount):
    """Move to (x, y) and scroll the mouse wheel up by `amount` notches.
    Uses pyautogui rather than pydirectinput - pydirectinput has no scroll
    function of its own."""
    pyautogui.moveTo(x, y, duration=CLICK_MOVE_DURATION)
    time.sleep(CLICK_SETTLE_DELAY)
    pyautogui.scroll(amount)
    time.sleep(POST_CLICK_DELAY)

def slow_click(x, y, duration):
    """Move the mouse to (x, y) deliberately slowly (over `duration` seconds,
    instead of the usual quick CLICK_MOVE_DURATION), then click. Used where
    jumping the mouse straight to the target might not register the same way
    a slower, more human-like movement would."""
    pyautogui.moveTo(x, y, duration=duration)
    time.sleep(CLICK_SETTLE_DELAY)
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
    for key in ("w", "a", "s", "d"):
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

        circularity = area / (np.pi * radius * radius + 1e-6)
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
    """Focus the game, then click a mapped need button with a jitter pattern."""
    if not focus_roblox():
        return False
    time.sleep(FOCUS_DELAY)
    print(f"[debug] clicking {need_name} at ({button_x}, {button_y})")
    jitter_click(button_x, button_y)
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
    for key in ("esc", "r", "enter"):
        pydirectinput.keyDown(key)
        time.sleep(RESPAWN_KEY_DURATION)
        pydirectinput.keyUp(key)
        time.sleep(RESPAWN_KEY_DURATION)
    wait_interruptible(RESPAWN_WAIT)
    print("[debug] respawn complete")

def walk_to_buttons():
    """Walk forward from the respawn spot to where the action buttons are."""
    if not focus_roblox():
        return
    print("[debug] walking to buttons...")
    pydirectinput.keyDown("w")
    try:
        wait_interruptible(WALK_TO_BUTTONS_DURATION)
    finally:
        # Always release the key, even if StopRequested fires mid-wait -
        # otherwise "w" stays stuck held down in the game.
        pydirectinput.keyUp("w")
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
        pydirectinput.keyDown(direction)
        try:
            wait_interruptible(step_duration)
        finally:
            # Always release, even if StopRequested fires mid-step - otherwise
            # this direction key stays stuck held down in the game.
            pydirectinput.keyUp(direction)
        time.sleep(WALK_STEP_GAP)

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
        pydirectinput.press('b')
        wait_interruptible(CATCH_CLICK_DELAY)

        # Navigate to toys
        print("[debug] opening toys...")
        jitter_click(*CATCH_TOYS_POS)
        wait_interruptible(CATCH_CLICK_DELAY)

        # Click squeaky toy
        print("[debug] selecting squeaky toy...")
        jitter_click(*CATCH_SQUEAKY_TOY_POS)
        wait_interruptible(CATCH_CLICK_DELAY)

        # Equip the toy
        print("[debug] equipping toy...")
        jitter_click(*CATCH_EQUIP_POS)
        wait_interruptible(CATCH_CLICK_DELAY)

        # Close backpack with 'b' key
        print("[debug] closing backpack...")
        pydirectinput.press('b')
        wait_interruptible(CATCH_CLICK_DELAY)

        # Wait before throwing
        print(f"[debug] waiting {CATCH_WAIT_AFTER_EQUIP}s before throwing...")
        wait_interruptible(CATCH_WAIT_AFTER_EQUIP)

        # Zoom in, to avoid focusing the pet on toy throw
        pydirectinput.keyDown('i')
        time.sleep(CATCH_SCROLL_TIME)
        pydirectinput.keyUp('i')
        
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
        time.sleep(0.1)
        # Click the center to focus
        jitter_click(SCREEN_CENTER_X, SCREEN_CENTER_Y)
        time.sleep(0.1)
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
        jitter_click(*FOCUS_PET_POS)
        wait_interruptible(UI_SETTLE)

        print(f"[debug] searching screen for color {CHOOSE_BUTTON_COLOR}...")
        img = grab_screen()
        match = find_exact_color(img, CHOOSE_BUTTON_COLOR)
        if match is None:
            print(f"[!] WARNING: no pixel matching {CHOOSE_BUTTON_COLOR} found on screen")
            return False
        print(f"[debug] found at {match}, moving there slowly...")
        slow_click(*match, duration=CHOOSE_SLOW_MOVE_DURATION)
        wait_interruptible(UI_SETTLE)
        time.sleep(2)
        jitter_click(*match)

        
        print("[debug] clicking middle of screen...")
        slow_click(SCREEN_CENTER_X, SCREEN_CENTER_Y, duration=CHOOSE_SLOW_MOVE_DURATION)
        time.sleep(1)
        pyautogui.moveTo(*EMPTY_POS, duration=CHOOSE_SLOW_MOVE_DURATION)
        time.sleep(CLICK_SETTLE_DELAY)
        pyautogui.moveTo(SCREEN_CENTER_X, SCREEN_CENTER_Y, duration=CHOOSE_SLOW_MOVE_DURATION)
        pydirectinput.click()
        time.sleep(POST_CLICK_DELAY)

        print(f"[debug] searching screen for color {CHOOSE_BUTTON_COLOR}...")
        img = grab_screen()
        match = find_exact_color(img, CHOOSE_BUTTON_COLOR)
        if match is None:
            print(f"[!] WARNING: no pixel matching {CHOOSE_BUTTON_COLOR} found on screen")
            return False
        print(f"[debug] found at {match}, moving there slowly...")
        slow_click(*match, duration=CHOOSE_SLOW_MOVE_DURATION)
        wait_interruptible(UI_SETTLE)
        time.sleep(2)
        jitter_click(*match)

        
        print("[debug] clicking middle of screen...")
        slow_click(SCREEN_CENTER_X, SCREEN_CENTER_Y, duration=CHOOSE_SLOW_MOVE_DURATION)
        time.sleep(1)
        pyautogui.moveTo(*EMPTY_POS, duration=CHOOSE_SLOW_MOVE_DURATION)
        time.sleep(CLICK_SETTLE_DELAY)
        pyautogui.moveTo(SCREEN_CENTER_X, SCREEN_CENTER_Y, duration=CHOOSE_SLOW_MOVE_DURATION)
        pydirectinput.click()
        time.sleep(POST_CLICK_DELAY)
        #pydirectinput.click()

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
        pydirectinput.keyDown("s")
        try:
            wait_interruptible(RIDE_BACKWARD_DURATION)
        finally:
            # Always release, even if StopRequested fires mid-step - otherwise
            # "s" stays stuck held down in the game.
            pydirectinput.keyUp("s")

        print("[debug] pressing e...")
        pydirectinput.press('e')
        wait_interruptible(RIDE_WAIT_AFTER_E)

        # Walk forward briefly after mounting, before opening the backpack
        print(f"[debug] walking forward for {RIDE_FORWARD_DURATION}s...")
        pydirectinput.keyDown("w")
        try:
            wait_interruptible(RIDE_FORWARD_DURATION)
        finally:
            # Always release, even if StopRequested fires mid-step - otherwise
            # "w" stays stuck held down in the game.
            pydirectinput.keyUp("w")

        # Open backpack, select and equip the first vehicle
        print("[debug] opening backpack...")
        pydirectinput.press('b')
        wait_interruptible(CATCH_CLICK_DELAY)

        print("[debug] opening vehicles...")
        jitter_click(*RIDE_VEHICLES_POS)
        wait_interruptible(CATCH_CLICK_DELAY)

        print("[debug] selecting first vehicle...")
        jitter_click(*RIDE_FIRST_VEHICLE_POS)
        wait_interruptible(CATCH_CLICK_DELAY)

        print("[debug] equipping vehicle...")
        jitter_click(*RIDE_EQUIP_POS)
        wait_interruptible(CATCH_CLICK_DELAY)

        print("[debug] closing backpack...")
        pydirectinput.press('b')
        wait_interruptible(CATCH_CLICK_DELAY)

        # Walk back and forth (forward/backward, not left/right) while riding
        walk_alternating(("w", "s"), RIDE_WALK_DURATION)

        print("[!] Ride complete!")
        return True

def teleport_to_nursery():
    """Open the backpack and teleport to the nursery, then step back briefly
    to clear the landing spot. Shared first step of BoredNeedHandler and
    BeachNeedHandler below."""
    print("[debug] opening backpack...")
    pydirectinput.press('b')
    wait_interruptible(TELEPORT_CLICK_DELAY)

    print("[debug] navigating to nursery...")
    jitter_click(*TELEPORT_NURSERY_POS_1)
    wait_interruptible(TELEPORT_CLICK_DELAY)
    jitter_click(*TELEPORT_NURSERY_POS_2)
    wait_interruptible(TELEPORT_CLICK_DELAY)
    jitter_click(*TELEPORT_NURSERY_POS_3)
    wait_interruptible(TELEPORT_NURSERY_WAIT)

    print("[debug] stepping back...")
    pydirectinput.keyDown("s")
    try:
        wait_interruptible(TELEPORT_NURSERY_BACK_DURATION)
    finally:
        # Always release, even if StopRequested fires mid-step - otherwise
        # "s" stays stuck held down in the game.
        pydirectinput.keyUp("s")

class BoredNeedHandler(NeedHandler):
    """The 'bored' need: teleport to the nursery, walk forward then left for
    a while, wait it out, then respawn (since, unlike every other special
    need, this one leaves the character somewhere else on the map)."""

    def handle(self):
        print("[!] BORED NEED")
        if not focus_roblox():
            return False

        teleport_to_nursery()

        print(f"[debug] walking forward for {BORED_WALK_FORWARD_DURATION}s...")
        pydirectinput.keyDown("w")
        try:
            wait_interruptible(BORED_WALK_FORWARD_DURATION)
        finally:
            pydirectinput.keyUp("w")

        time.sleep(0.1)
        print(f"[debug] walking left for {BORED_WALK_LEFT_DURATION}s...")
        pydirectinput.keyDown("a")
        try:
            wait_interruptible(BORED_WALK_LEFT_DURATION)
        finally:
            pydirectinput.keyUp("a")

        print(f"[debug] waiting {BORED_WAIT_AFTER_WALK}s...")
        wait_interruptible(BORED_WAIT_AFTER_WALK)

        respawn_character()
        print("[!] Bored complete!")
        return True

class BeachNeedHandler(NeedHandler):
    """The 'beach' need: teleport to the nursery, walk left for a while,
    wait it out, then respawn (since, unlike every other special need, this
    one leaves the character somewhere else on the map)."""

    def handle(self):
        print("[!] BEACH NEED")
        if not focus_roblox():
            return False

        teleport_to_nursery()

        print(f"[debug] walking left for {BEACH_WALK_LEFT_DURATION}s...")
        pydirectinput.keyDown("a")
        try:
            wait_interruptible(BEACH_WALK_LEFT_DURATION)
        finally:
            pydirectinput.keyUp("a")

        print(f"[debug] waiting {BEACH_WAIT_AFTER_WALK}s...")
        wait_interruptible(BEACH_WAIT_AFTER_WALK)

        respawn_character()
        print("[!] Beach complete!")
        return True

# Need names with dedicated handler logic above, rather than being a basic
# at-home button click. "walk"/"walk2"/etc. are matched by prefix instead of
# being listed here - see is_basic_need() / get_special_need_handler().
SPECIAL_NEED_NAMES = {"catch", "pet", "choose", "ride", "bored", "beach"}

def is_basic_need(need_name):
    """A basic need has no dedicated handler - it's satisfied by walking to
    the action buttons and clicking the one matching its name."""
    return need_name in BUTTON_NAMES

def get_special_need_handler(need_name):
    """Return the handler for a need with dedicated logic. Only call this
    when is_basic_need(need_name) is False. Returns None if this need type
    is currently disabled via its _ENABLED flag."""
    if need_name.startswith("walk"):
        return WalkNeedHandler()
    if need_name == "catch":
        return CatchNeedHandler() if CATCH_ENABLED else None
    if need_name == "pet":
        return PetNeedHandler() if PET_ENABLED else None
    if need_name == "choose":
        return ChooseNeedHandler() if CHOOSE_ENABLED else None
    if need_name == 'ride':
        return RideNeedHandler()
    if need_name == "bored":
        return BoredNeedHandler() if BORED_ENABLED else None
    if need_name == "beach":
        return BeachNeedHandler() if BEACH_ENABLED else None

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
    for idx, (cx, cy, radius) in enumerate(found_icons):
        check_running()

        icon_img = extract_icon(full_img, cx, cy, radius)
        matched_need, score = find_matching_need(icon_img)

        if not matched_need:
            if SAVE_NEW_NEEDS:
                print(f"\n[!] NEW NEED (best score: {score:.4f})")
                # prompt_rename_need() already saves the reference icon into
                # NEEDS_DIR - that .png file is the only record needed for this
                # need to be recognized next time, so there's nothing further to
                # persist here.
                prompt_rename_need(icon_img, idx)
            continue

        need_name = base_need_name(matched_need)
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
    jitter_click(*PAYCHECK_DISMISS_POS_1)
    simple_click(*EMPTY_POS)
    jitter_click(*PAYCHECK_DISMISS_POS_2)
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
        self.root.geometry("380x650+1550+30")
        self.root.resizable(False, False)
        self.root.attributes('-topmost', True)
        self.root.attributes('-alpha', 0.95)

        self.bg = "#1a1a1a"
        self.accent = "#0d7377"
        self.fg = "#fff"
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
        btn_frame = tk.Frame(self.root, bg=self.bg)
        btn_frame.pack(fill=tk.BOTH, expand=False, padx=8, pady=8)

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
        SQUARE_BUTTON_SIZE = 44  # px, short and square
        NO_BORDER = dict(bd=0, highlightthickness=0)  # flat edges, no default Tk bevel/focus ring

        main_row = tk.Frame(btn_frame, bg=self.bg)
        main_row.pack(fill=tk.X, pady=4)

        start_container = tk.Frame(main_row, width=SQUARE_BUTTON_SIZE, height=SQUARE_BUTTON_SIZE, bg=self.bg)
        start_container.pack(side=tk.LEFT, padx=(0, 4))
        start_container.pack_propagate(False)

        START_BUTTON_COLOR = "#2ecc71"

        # Single cycle: icon + "1" as plain button text (no overlay Label -
        # a Label placed on top of the button kept showing a visible seam/
        # box behind it despite matching colors, so this is just simpler).
        btn_start = tk.Button(start_container, text="\U0001F5041", command=self.run_workflow,
                               font=("Courier", 14, "bold"), bg=START_BUTTON_COLOR, fg=self.fg,
                               cursor="hand2", **NO_BORDER)
        btn_start.pack(fill=tk.BOTH, expand=True)
        self.action_buttons.append(btn_start)

        # Not added to action_buttons: must remain clickable while a workflow is running
        stop_container = tk.Frame(main_row, width=SQUARE_BUTTON_SIZE, height=SQUARE_BUTTON_SIZE, bg=self.bg)
        stop_container.pack(side=tk.RIGHT, padx=(4, 0))
        stop_container.pack_propagate(False)

        self.btn_stop = tk.Button(stop_container, text="■", command=self.stop,
                                   font=("Courier", 16, "bold"), bg="#d62828", fg=self.fg,
                                   cursor="hand2", **NO_BORDER)
        self.btn_stop.pack(fill=tk.BOTH, expand=True)

        loop_container = tk.Frame(main_row, height=SQUARE_BUTTON_SIZE, bg=self.bg)
        loop_container.pack(side=tk.LEFT, fill=tk.X, expand=True)
        loop_container.pack_propagate(False)

        btn_loop = tk.Button(loop_container, text="\U0001F504", command=self.run_workflow_loop,
                              font=("Courier", 20, "bold"), bg="#2980b9", fg=self.fg,
                              cursor="hand2", **NO_BORDER)
        btn_loop.pack(fill=tk.BOTH, expand=True)
        self.action_buttons.append(btn_loop)

        tk.Frame(btn_frame, bg=self.accent, height=2).pack(fill=tk.X, pady=4)

        tk.Label(btn_frame, text="Functions", font=("Courier", 9, "bold"), bg=self.bg, fg=self.accent).pack(anchor=tk.W)

        btn_respawn = tk.Button(btn_frame, text="Respawn", command=lambda: self.run_async(respawn_character),
                                 font=("Courier", 9), bg="#8e44ad", fg=self.fg, height=1, cursor="hand2")
        btn_respawn.pack(fill=tk.X, pady=2)
        self.action_buttons.append(btn_respawn)

        tk.Frame(btn_frame, bg=self.accent, height=1).pack(fill=tk.X, pady=2)
        tk.Label(btn_frame, text="Tests", font=("Courier", 9, "bold"), bg=self.bg, fg=self.accent).pack(anchor=tk.W)

        btn_test_choose = tk.Button(btn_frame, text="[TEST] Choose", command=self.test_choose,
                                     font=("Courier", 9), bg="#e74c3c", fg=self.fg, height=1, cursor="hand2")
        btn_test_choose.pack(fill=tk.X, pady=2)
        self.action_buttons.append(btn_test_choose)

        # Bored/beach stay behind their _ENABLED flags, so these are their
        # only way to run outside of automatic need processing, same as choose.
        btn_test_bored = tk.Button(btn_frame, text="[TEST] Bored", command=self.test_bored,
                                    font=("Courier", 9), bg="#e74c3c", fg=self.fg, height=1, cursor="hand2")
        btn_test_bored.pack(fill=tk.X, pady=2)
        self.action_buttons.append(btn_test_bored)

        btn_test_beach = tk.Button(btn_frame, text="[TEST] Beach", command=self.test_beach,
                                    font=("Courier", 9), bg="#e74c3c", fg=self.fg, height=1, cursor="hand2")
        btn_test_beach.pack(fill=tk.X, pady=2)
        self.action_buttons.append(btn_test_beach)

        # Debug console
        debug_frame = tk.Frame(self.root, bg=self.bg)
        debug_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        tk.Label(debug_frame, text="Output", font=("Courier", 9, "bold"), bg=self.bg, fg=self.accent).pack(anchor=tk.W, pady=(0, 3))

        self.debug_text = scrolledtext.ScrolledText(debug_frame, height=14, width=45, bg="#0a0a0a", fg="#00ff00",
                                                     font=("Courier", 7), state=tk.DISABLED)
        self.debug_text.pack(fill=tk.BOTH, expand=True)

        # Status bar
        status = tk.Frame(self.root, bg=self.accent, height=25)
        status.pack(fill=tk.X, side=tk.BOTTOM)
        status.pack_propagate(False)
        self.status = tk.Label(status, text="Ready", font=("Courier", 8), bg=self.accent, fg=self.fg)
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

    def test_choose(self):
        """Run the choose handler on its own, outside the normal need-detection
        flow. Useful while CHOOSE_ENABLED = False, since the handler is fully
        working code that's just not wired into automatic need processing yet."""
        def test():
            print("\n[TEST] Running choose handler...")
            ChooseNeedHandler().handle()
            print("[TEST] Choose handler complete\n")
        self.run_async(test)

    def test_bored(self):
        """Run the bored handler on its own, outside the normal need-detection
        flow. Useful while BORED_ENABLED = False, since the handler is fully
        working code that's just not wired into automatic need processing yet."""
        def test():
            print("\n[TEST] Running bored handler...")
            BoredNeedHandler().handle()
            print("[TEST] Bored handler complete\n")
        self.run_async(test)

    def test_beach(self):
        """Run the beach handler on its own, outside the normal need-detection
        flow. Useful while BEACH_ENABLED = False, since the handler is fully
        working code that's just not wired into automatic need processing yet."""
        def test():
            print("\n[TEST] Running beach handler...")
            BeachNeedHandler().handle()
            print("[TEST] Beach handler complete\n")
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
