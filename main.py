#!/usr/bin/env python3
"""Adopt Me Bot - Automated pet care with GUI control panel."""

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
# Standard library: os, sys, json, time, random, threading, abc, enum, pathlib, tkinter

# ============================================================================
# CODE RUNDOWN
# ============================================================================
# What this does: watches the Roblox "Adopt Me" window for pet-care need
# icons (hunger, thirst, etc.) and clicks the matching action button.
#
# Flow: respawn_character() -> process_needs() -> repeat. Detecting which
# needs are active never requires a specific position; only actually
# satisfying a need does, and each need's handler moves there itself:
#   - hungry/thirsty/dirty/potty/sleepy require HOME (walks there, then
#     refreshes the button mapping, since that's the only reason to be there)
#   - pet requires RESPAWN (respawns first if we're anywhere else)
#   - catch, choose, ride, and walk work from wherever the character is
#
# PET_ENABLED is OFF by default (near the top of CONSTANTS) - it's fully
# implemented but not yet wired into automatic need processing, so it's
# skipped in process_needs() until flipped on. Its [TEST] button in the GUI
# runs it directly regardless. Every other need type is live and has no
# manual GUI trigger of its own - respawn is the only one that still does,
# since it's occasionally useful to fire on its own.
#
# Key pieces, top to bottom:
#   - CONSTANTS          all tunable numbers/timings/colors in one place
#   - CONFIG              load/save adoptme_config.json (button positions)
#   - WINDOW FOCUS         bring Roblox to front, grab screenshots, and
#                          find_exact_color() for buttons matched by color
#                          rather than shape (used by the choose need)
#   - POSITIONING          tracks whether the character is at "respawn" or
#                          "home" (Position enum); move_to() is the single
#                          dispatch point that routes a Position to the
#                          function that actually gets you there
#   - ICON PROCESSING       turns an icon into strict black & white so it
#                          can be matched regardless of its original color
#   - NEED ICON DETECTION   finds circular need icons at the top of screen,
#                          compares them to saved reference icons in needs/
#   - CLICKING              jitter_click (click, nudge, click again - used
#                          for the actual need buttons) / simple_click
#                          (used for backpack/toy UI clicks)
#   - BUTTON DETECTION      finds the purple action buttons (hungry,
#                          thirsty, dirty, potty, sleepy) and saves their
#                          screen positions to config
#   - MOVEMENT              respawn_character(), walk_alternating() (shared
#                          by walk, a/d, and ride, w/s - same pattern,
#                          different keys and duration passed in)
#   - NEED HANDLERS         one class per need type (ABC pattern), each
#                          declares the Position it needs (or None) and
#                          what to do once there. CatchNeedHandler runs a
#                          backpack -> equip toy -> throw x3 -> unequip
#                          sequence; RideNeedHandler backpack -> equip
#                          vehicle -> walk; ChooseNeedHandler finds and
#                          clicks a button by its exact color
#   - WORKFLOWS             run_full_cycle() ties respawn + process
#                          together; run_workflow_loop() repeats it
#   - GUI                   tkinter control panel; every button that starts
#                          a background task runs it via run_async(), which
#                          disables all such buttons while it's running and
#                          ALWAYS re-enables them in a finally block once it
#                          ends - normal finish, stop, or crash alike.
#
# STOPPING: pressing [STOP] sets STOP_FLAG, and wait_interruptible()/
# check_stop() raise StopRequested the moment they next notice it. That
# exception unwinds the call stack on its own via ordinary Python exception
# propagation, all the way up to run_async()'s worker thread - so a handler
# doesn't need to manually check a flag after every single action, it just
# calls wait_interruptible() like normal and stopping "just happens." The
# one thing this doesn't do for free is release a key or mouse button that's
# being held down at the moment of the stop, so anywhere that happens (e.g.
# holding "w" to walk, holding the mouse button to pet) wraps the wait in
# try/finally to guarantee the release. This is deliberately NOT solved by
# running the workflow and a second "killer" thread that terminates it from
# outside: Python has no safe way to force-kill a running thread, and the
# unsafe tricks that exist (async-raising into another thread) can land
# mid-action and leave an input stuck down in the game, which is worse than
# the ~0.1s worst-case delay this cooperative approach has instead.

import os, sys, json, time, random, threading
from abc import ABC, abstractmethod
from enum import Enum
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
CONFIG_FILE = os.path.join(SCRIPT_DIR, "adoptme_config.json")
Path(NEEDS_DIR).mkdir(exist_ok=True)

pyautogui.FAILSAFE = True
pydirectinput.FAILSAFE = True
pydirectinput.PAUSE = 0.05
SCREEN_WIDTH, SCREEN_HEIGHT = pyautogui.size()

# Behavior flags
FOCUS_WINDOW_ON_ACTION = True  # click the Roblox window to focus it before acting
CATCH_ENABLED = True           # catch need handler is live (set to False to disable)
PET_ENABLED = False            # disable pet need handler (set to True to re-enable)
CHOOSE_ENABLED = False         # disable choose need handler (set to True to re-enable)

# Timing (seconds)
RESPAWN_KEY_DURATION = 0.05    # how long each respawn key is held
RESPAWN_WAIT = 4.0             # settle time after respawning, before it's usable
HOME_WALK_DURATION = 0.8       # time spent walking from respawn to home
WALK_ALTERNATING_STEP = 1.0    # duration of each a/d press in alternating walk pattern
WALK_TOTAL_DURATION = 20.0     # total duration to keep walking back and forth
UI_SETTLE = 0.5                # generic pause for UI to catch up
FOCUS_DELAY = 0.3              # pause after focusing the window
POST_CLICK_DELAY = 0.4         # pause after each click
POST_NEED_CLICK_WAIT = 15      # pause after satisfying a need via button click
LOOP_DELAY = 2.0               # pause between iterations of the workflow loop
CATCH_WAIT_AFTER_EQUIP = 2.0   # wait after equipping toy before throwing
CATCH_EMOTE_DELAY = 10.0       # delay between throw clicks
CATCH_CLICK_DELAY = 0.5        # delay between catch sequence clicks
PET_FIRST_E_WAIT = 6.0         # wait between first and second 'e' press in pet need
PET_CIRCLE_DURATION = 10.0     # how long to make circles with mouse

# Catch need positions (screen coordinates for the toy-throwing sequence;
# opening/closing the backpack itself uses the 'b' key, not a click)
CATCH_TOYS_POS = (754, 854)
CATCH_SQUEAKY_TOY_POS = (976, 875)
CATCH_EQUIP_POS = (1083, 980)
CATCH_UNEQUIP_POS = (1089, 977)

# A spot on screen that's just empty game world - no UI, no character menu.
# Used as the "click into empty space" throw motion in catch.
EMPTY_POS = (380, 557)

# Click this to open the pet's interaction menu (pet/weather/feed/dress up/
# tricks/pick up/ride/fly icons arranged around the pet).
FOCUS_PET_POS = (1114, 692)

# The 'choose' need's button is matched by its exact color rather than shape,
# since it's just a plain circle with no distinguishing icon. Given as (R, G, B).
CHOOSE_BUTTON_COLOR = (181, 6, 254)
CHOOSE_SLOW_MOVE_DURATION = 1.0  # deliberate, slow mouse travel to the found button

# Ride need: positions for the backpack -> vehicles -> first vehicle -> equip
# sequence, plus how long to hold each step.
RIDE_BACKWARD_DURATION = 1.0   # step back before pressing e to mount
RIDE_WAIT_AFTER_E = 5.0        # wait after e before opening the backpack
RIDE_FORWARD_DURATION = 2.0    # walk forward briefly after mounting, before the backpack
RIDE_VEHICLES_POS = (816, 804)
RIDE_FIRST_VEHICLE_POS = (976, 708)
RIDE_EQUIP_POS = (1062, 814)
RIDE_WALK_DURATION = 40.0      # total time spent walking back and forth while riding

# Window focus click (near top edge, right of center)
FOCUS_CLICK_X_PERCENT = 0.75
FOCUS_CLICK_Y = 5

# Jitter click (click, nudge mouse a few pixels, click again)
JITTER_PIXELS = 5

# Need-icon detection (top strip of screen)
NEED_ICON_TOP_PERCENT = 0.15        # fraction of screen height searched for icons
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
    explicitly unsafe, and for a game bot that matters a lot: if the kill
    landed while a movement key or the mouse button was held down, it would
    never get released and would stay stuck in the game.

    Instead, wait_interruptible() and check_stop() raise this the moment they
    notice STOP_FLAG, and ordinary Python exception propagation unwinds the
    call stack from wherever we are all the way up to run_async()'s worker
    thread, which catches it. Any try/finally in between (used wherever a key
    or the mouse is held down) still runs on the way up, so cleanup always
    happens no matter where the stop lands - without every function needing
    to manually check a flag after every single action."""
    pass

def check_stop():
    """Raise StopRequested if the user has pressed [STOP]. Call this at safe
    checkpoints inside a loop that doesn't otherwise call wait_interruptible()."""
    if STOP_FLAG:
        raise StopRequested()

# ============================================================================
# CONFIG
# ============================================================================

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {"buttons": {}, "needs": {}}

def save_config(config):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)

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

def focus_roblox_click():
    """Focus Roblox and click near the top edge to make sure input registers."""
    if not FOCUS_WINDOW_ON_ACTION:
        return True
    if not focus_roblox():
        return False
    click_x = int(SCREEN_WIDTH * FOCUS_CLICK_X_PERCENT)
    pyautogui.moveTo(click_x, FOCUS_CLICK_Y)
    pydirectinput.click()
    time.sleep(0.2)
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
# POSITIONING
# ============================================================================
# The character can be at one of a few known locations. Each need declares
# which one it requires; we only move there if we aren't already.

class Position(Enum):
    RESPAWN = "respawn"  # where the character lands right after respawning
    HOME = "home"        # where the pet-care buttons are, just past the respawn spot

CURRENT_POSITION = None  # unknown until the first respawn

def move_to(position):
    """Move the character to the given position, if a route for it exists.
    This is the single dispatch point ensure_position() calls through, so
    adding a new named position later just means adding a branch here."""
    if position == Position.HOME:
        go_home()
    elif position == Position.RESPAWN:
        respawn_character()

def go_home():
    """Walk forward from the respawn spot to the home position, if not there already."""
    global CURRENT_POSITION
    if CURRENT_POSITION == Position.HOME:
        return
    if not focus_roblox():
        return
    print("[debug] walking to home position...")
    pydirectinput.keyDown("w")
    try:
        wait_interruptible(HOME_WALK_DURATION)
    finally:
        # Always release the key, even if StopRequested fires mid-wait -
        # otherwise "w" stays stuck held down in the game.
        pydirectinput.keyUp("w")
    wait_interruptible(UI_SETTLE)
    CURRENT_POSITION = Position.HOME
    print("[debug] arrived home")

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
    """Return a 0-1 similarity score between two icon images."""
    proc1 = cv2.resize(preprocess_icon(icon1), ICON_COMPARE_SIZE)
    proc2 = cv2.resize(preprocess_icon(icon2), ICON_COMPARE_SIZE)

    mse = np.mean((proc1.astype(float) - proc2.astype(float)) ** 2)
    mse_sim = 1.0 - (mse / (255 ** 2))

    hist1 = cv2.normalize(cv2.calcHist([proc1], [0], None, [256], [0, 256]), None).flatten()
    hist2 = cv2.normalize(cv2.calcHist([proc2], [0], None, [256], [0, 256]), None).flatten()
    hist_sim = 1.0 / (1.0 + cv2.compareHist(hist1, hist2, cv2.HISTCMP_BHATTACHARYYA))

    return (mse_sim + hist_sim) / 2.0

# ============================================================================
# NEED ICON DETECTION
# ============================================================================

def extract_icon(img, cx, cy, radius, padding=5):
    r = radius + padding
    x0, x1 = max(0, cx - r), min(img.shape[1], cx + r)
    y0, y1 = max(0, cy - r), min(img.shape[0], cy + r)
    return img[y0:y1, x0:x1].copy()

def detect_need_icons(save_debug=False):
    """Detect circular need icons at the top of the screen (any color)."""
    img = grab_screen()
    h, w = img.shape[:2]

    top_strip = img[0:int(h * NEED_ICON_TOP_PERCENT), :].copy()
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
            cv2.circle(debug_img, (cx, cy), r, (0, 255, 0), 2)
        cv2.imwrite(os.path.join(SCRIPT_DIR, "debug_needs.png"), debug_img)

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
    pyautogui.moveTo(x, y, duration=0.3)
    time.sleep(0.2)
    pydirectinput.click()
    time.sleep(POST_CLICK_DELAY)

    offset_x = random.randint(-JITTER_PIXELS, JITTER_PIXELS)
    offset_y = random.randint(-JITTER_PIXELS, JITTER_PIXELS)
    new_x = max(0, min(SCREEN_WIDTH, x + offset_x))
    new_y = max(0, min(SCREEN_HEIGHT, y + offset_y))
    pydirectinput.moveTo(new_x, new_y, duration=0.3)
    time.sleep(0.2)

    pydirectinput.click()
    time.sleep(POST_CLICK_DELAY)

def simple_click(x, y):
    """Simple click without jitter."""
    pyautogui.moveTo(x, y, duration=0.3)
    time.sleep(0.2)
    pydirectinput.click()
    time.sleep(POST_CLICK_DELAY)

def slow_click(x, y, duration=1.0):
    """Move the mouse to (x, y) deliberately slowly (over `duration` seconds,
    instead of the usual quick 0.3s), then click. Used where jumping the
    mouse straight to the target might not register the same way a slower,
    more human-like movement would."""
    pyautogui.moveTo(x, y, duration=duration)
    time.sleep(0.2)
    pydirectinput.click()
    time.sleep(POST_CLICK_DELAY)

def wait_interruptible(duration):
    """Sleep for `duration`, in 0.1s chunks, checking for a stop request
    between each chunk. Raises StopRequested the moment STOP_FLAG is set,
    instead of returning a bool the caller has to check after every call."""
    elapsed = 0.0
    while elapsed < duration:
        check_stop()
        sleep_chunk = min(0.1, duration - elapsed)
        time.sleep(sleep_chunk)
        elapsed += sleep_chunk
    check_stop()

def release_all_inputs():
    """Best-effort safety net: release every key this bot ever holds down,
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

    purple_dilated = cv2.dilate(purple_mask, None, iterations=3)
    outline_zone = cv2.subtract(purple_dilated, cv2.erode(purple_mask, None, iterations=1))
    has_white = cv2.bitwise_and(outline_zone, white_mask)

    contours, _ = cv2.findContours(purple_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    print(f"[debug] button scan: {len(contours)} raw purple contour(s) in search band")

    candidates = []
    rejected_area, rejected_radius, rejected_circularity = 0, 0, 0
    for c in contours:
        area = cv2.contourArea(c)
        if area < BUTTON_MIN_AREA * 0.7:  # slightly looser than the base threshold
            rejected_area += 1
            continue
        if area > band_w * band_h * 0.5:  # reject blobs covering most of the band (not a button)
            rejected_area += 1
            continue

        _, radius = cv2.minEnclosingCircle(c)
        if radius < 5:
            rejected_radius += 1
            continue

        circularity = area / (np.pi * radius * radius + 1e-6)
        if circularity < BUTTON_MIN_CIRCULARITY * 0.8:  # slightly looser than the base threshold
            rejected_circularity += 1
            continue

        x, y, cw, ch = cv2.boundingRect(c)
        cx, cy = x + cw // 2, y + ch // 2
        roi = has_white[max(0, y - 3):y + ch + 3, max(0, x - 3):x + cw + 3]
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
            cv2.circle(debug_img, (cx, cy), 8, (0, 0, 255), 2)
            cv2.putText(debug_img, str(i + 1), (cx - 5, cy - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
        cv2.imwrite(os.path.join(SCRIPT_DIR, "debug_buttons.png"), debug_img)
        # Also save the raw masks so a failed detection can actually be diagnosed
        cv2.imwrite(os.path.join(SCRIPT_DIR, "debug_buttons_band.png"), band)
        cv2.imwrite(os.path.join(SCRIPT_DIR, "debug_buttons_purple_mask.png"), purple_mask)
        cv2.imwrite(os.path.join(SCRIPT_DIR, "debug_buttons_white_mask.png"), white_mask)

    if not positions:
        print("[!] No buttons detected. Check debug_buttons_band.png (what was scanned) and "
              "debug_buttons_purple_mask.png (what counted as 'purple') in the script folder "
              "to see whether PURPLE_RANGE / BUTTON_BAND_X / BUTTON_BAND_Y need adjusting.")

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
    """Detect the current action button positions and save them to config.
    Assumes the character is already at the home position."""
    positions = detect_buttons(save_debug=True)
    if not positions:
        print("[!] ERROR: No buttons detected!")
        return False

    config = load_config()
    config["buttons"] = {}

    print(f"\n[!] DETECTED {len(positions)} BUTTON(S)")
    for i, (cx, cy) in enumerate(positions):
        if i < len(BUTTON_NAMES):
            name = BUTTON_NAMES[i]
            config["buttons"][name] = [cx, cy]
            print(f"[!] Button {i + 1}: '{name}' @ ({cx}, {cy})")

    save_config(config)
    print("\n[!] CONFIG SAVED\n")
    return True

# ============================================================================
# MOVEMENT
# ============================================================================

def respawn_character():
    """Respawn (ESC, R, ENTER), wait to settle, and mark the position as 'respawn'."""
    global CURRENT_POSITION
    print("[debug] respawning...")
    if not focus_roblox_click():
        return
    for key in ("esc", "r", "enter"):
        pydirectinput.keyDown(key)
        time.sleep(RESPAWN_KEY_DURATION)
        pydirectinput.keyUp(key)
        time.sleep(RESPAWN_KEY_DURATION)
    wait_interruptible(RESPAWN_WAIT)
    CURRENT_POSITION = Position.RESPAWN
    print("[debug] respawn complete")

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
        time.sleep(0.1)

        direction_idx += 1
        elapsed = time.time() - start_time

    print("[debug] alternating walk complete")

# ============================================================================
# NEED HANDLERS
# ============================================================================
# Each need type has its own handler. A handler knows what position it needs
# (if any) and what to do once there. Splitting these out means each need can
# later be customized independently without touching the others.

class NeedHandler(ABC):
    """Reacts to one detected need. Subclass this to add new need behaviors."""
    position = None  # Position required before handle() runs; None = no requirement

    def ensure_position(self):
        """Move the character to this need's required position, if not already there."""
        if self.position is not None and CURRENT_POSITION != self.position:
            move_to(self.position)

    @abstractmethod
    def handle(self, config):
        """Perform the action for this need. Return True if it was handled."""
        raise NotImplementedError

class WalkNeedHandler(NeedHandler):
    """The 'walk' need is satisfied by moving around, not clicking a button.
    After walking, respawns to reset position before any further processing."""
    position = None

    def handle(self, config):
        print("[!] WALK NEED")
        walk_alternating(("a", "d"), WALK_TOTAL_DURATION)
        # After walking, respawn to reset position
        print("[debug] respawning after walk...")
        respawn_character()
        return True

class CatchNeedHandler(NeedHandler):
    """The 'catch' need requires opening backpack, equipping a toy, then throwing it."""
    position = None  # works at any position (respawn or home)

    def handle(self, config):
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

        # Click empty space three times to throw, with a delay between throws
        for i in range(3):
            print(f"[debug] throw {i + 1}/3...")
            simple_click(*EMPTY_POS)
            wait_interruptible(CATCH_EMOTE_DELAY)

        # Unequip the toy
        print("[debug] unequipping toy...")
        simple_click(*CATCH_UNEQUIP_POS)

        print("[!] Catch complete!")
        return True

class PetNeedHandler(NeedHandler):
    """The 'pet' need requires being at the respawn position (not home) - it
    respawns first if we're anywhere else, then: press e, wait, press e
    again, then hold the mouse button down and move it in a circle around
    the middle of the screen."""
    position = Position.RESPAWN

    def handle(self, config):
        print("[!] PET NEED")
        self.ensure_position()  # respawns only if we aren't at RESPAWN already
        if not focus_roblox():
            return False

        # First e press
        print("[debug] pressing e (first)...")
        pydirectinput.press('e')
        wait_interruptible(PET_FIRST_E_WAIT)

        # Second e press
        print("[debug] pressing e (second)...")
        pydirectinput.press('e')
        wait_interruptible(2)

        # Hold the mouse button down and trace a circle around the middle of the screen
        print(f"[debug] holding mouse and circling for {PET_CIRCLE_DURATION}s...")
        center_x, center_y = (960, 540)
        circle_radius = 100

        # Move to the circle's starting point before pressing down, so the
        # button goes down already on the circle rather than jumping to it.
        pyautogui.moveTo(center_x + circle_radius, center_y, duration=0.1)
        pydirectinput.mouseDown()
        try:
            start_time = time.time()
            elapsed = 0.0
            while elapsed < PET_CIRCLE_DURATION:
                check_stop()  # inside a manual loop, not wait_interruptible - check explicitly
                angle = (elapsed / PET_CIRCLE_DURATION) * 2 * np.pi
                x = int(center_x + circle_radius * np.cos(angle))
                y = int(center_y + circle_radius * np.sin(angle))
                pyautogui.moveTo(x, y, duration=0.05)
                elapsed = time.time() - start_time
        finally:
            # Always release, even if StopRequested fires mid-circle -
            # otherwise the mouse button stays stuck held down in the game.
            pydirectinput.mouseUp()

        print("[!] Pet complete!")
        return True

class ChooseNeedHandler(NeedHandler):
    """The 'choose' need: focusing the pet requires being at RESPAWN, so
    respawn first if we aren't already there. Then: jitter-click to open
    the pet's interaction menu, find the button with a distinctive exact
    color (it has no distinguishing icon, so shape detection doesn't apply
    here), move to it slowly rather than jumping straight there, click it,
    then click the middle of the screen to dismiss the menu."""
    position = Position.RESPAWN

    def handle(self, config):
        print("[!] CHOOSE NEED")
        self.ensure_position()  # respawns only if we aren't at RESPAWN already
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

        print("[debug] clicking middle of screen...")
        simple_click(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2)

        print("[!] Choose complete!")
        return True

class RideNeedHandler(NeedHandler):
    """The 'ride' need: step back, mount a vehicle from the backpack, then
    walk back and forth for a while astride it."""
    position = None  # works at any position (respawn or home)

    def handle(self, config):
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

class ButtonNeedHandler(NeedHandler):
    """Base for needs satisfied by clicking a mapped button at the home position.
    Getting home also refreshes the button mapping, since that's the only reason
    to be there - other need types (catch, walk) never trigger this."""
    position = Position.HOME
    need_name = None  # subclasses hardcode this; or pass one in for an unrecognized need

    def __init__(self, need_name=None):
        if need_name is not None:
            self.need_name = need_name

    def handle(self, config):
        self.ensure_position()
        refresh_button_mapping()
        config = load_config()  # reload to pick up the freshly mapped buttons

        if self.need_name not in config["buttons"]:
            print(f"[!] WARNING: no button mapped for '{self.need_name}'")
            return False

        button_x, button_y = config["buttons"][self.need_name]
        print(f"[!] CLICKING: {self.need_name}")
        click_button(button_x, button_y, self.need_name)
        print(f"[debug] waiting {POST_NEED_CLICK_WAIT}s before next action...")
        wait_interruptible(POST_NEED_CLICK_WAIT)
        return True

class HungryNeedHandler(ButtonNeedHandler):
    need_name = "hungry"

class ThirstyNeedHandler(ButtonNeedHandler):
    need_name = "thirsty"

class DirtyNeedHandler(ButtonNeedHandler):
    need_name = "dirty"

class PottyNeedHandler(ButtonNeedHandler):
    need_name = "potty"

class SleepyNeedHandler(ButtonNeedHandler):
    need_name = "sleepy"

NEED_HANDLER_CLASSES = {
    "hungry": HungryNeedHandler,
    "thirsty": ThirstyNeedHandler,
    "dirty": DirtyNeedHandler,
    "potty": PottyNeedHandler,
    "sleepy": SleepyNeedHandler,
    "catch": CatchNeedHandler,
    "pet": PetNeedHandler,
    "choose": ChooseNeedHandler,
    "ride": RideNeedHandler,
}

def get_need_handler(need_name):
    """Return the handler responsible for a given need name."""
    if need_name.startswith("walk"):
        return WalkNeedHandler()
    if need_name == "catch" and not CATCH_ENABLED:
        return None  # catch is disabled
    if need_name == "pet" and not PET_ENABLED:
        return None  # pet is disabled
    if need_name == "choose" and not CHOOSE_ENABLED:
        return None  # choose is disabled
    handler_cls = NEED_HANDLER_CLASSES.get(need_name)
    if handler_cls:
        return handler_cls()
    return ButtonNeedHandler(need_name)  # fallback for any custom need

def process_needs():
    """Detect needs on screen and dispatch each to its handler."""
    print("\n[debug] processing needs...")
    if not focus_roblox_click():
        return

    config = load_config()
    found_icons, full_img = detect_need_icons(save_debug=True)
    if not found_icons:
        print("[debug] no need icons detected")
        return

    print(f"[debug] found {len(found_icons)} icon(s)")

    for idx, (cx, cy, radius) in enumerate(found_icons):
        check_stop()

        icon_img = extract_icon(full_img, cx, cy, radius)
        matched_need, score = find_matching_need(icon_img)

        if matched_need:
            need_name = base_need_name(matched_need)
            print(f"\n[!] MATCHED: {need_name} (score: {score:.4f})")
            handler = get_need_handler(need_name)
            if handler is None:
                print(f"[debug] {need_name} is disabled, skipping")
                continue
            handler.handle(config)
        else:
            print(f"\n[!] NEW NEED (best score: {score:.4f})")
            need_name = prompt_rename_need(icon_img, idx)
            config.setdefault("needs", {})[need_name] = True
            save_config(config)

# ============================================================================
# WORKFLOWS
# ============================================================================

def run_full_cycle():
    """One full cycle: respawn, then process needs. Each need's handler decides
    for itself whether it needs to walk home (only the five basic needs do)."""
    print("\n[1/2] Respawning...")
    respawn_character()

    print("\n[2/2] Processing needs...")
    process_needs()

def run_workflow():
    """Run a single cycle: respawn -> process needs (buttons are mapped on demand)."""
    global STOP_FLAG
    STOP_FLAG = False
    print("\n" + "=" * 50)
    print("[WORKFLOW] Starting")
    print("=" * 50)
    run_full_cycle()
    print("\n[WORKFLOW] Done\n")

def run_workflow_loop():
    """Repeat the workflow continuously. The only way this loop ever ends is
    via a stop request - there's no other exit condition, so StopRequested
    is expected here, not an error. It's allowed to propagate on up to
    run_async()'s worker (via the bare `finally`, not `except`) so the GUI
    still reports [STOPPED] correctly; the finally just prints locally first."""
    global STOP_FLAG
    STOP_FLAG = False
    print("\n" + "=" * 50)
    print("[LOOP] Starting continuous workflow")
    print("=" * 50)

    loop_num = 0
    try:
        while True:
            loop_num += 1
            print(f"\n[LOOP {loop_num}]")
            run_full_cycle()
            wait_interruptible(LOOP_DELAY)
    finally:
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
        self.root.title("Adopt Me Bot")
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
        print("ADOPT ME BOT - CONTROL PANEL")
        print("=" * 60)
        print(f"Config: {CONFIG_FILE}")
        print(f"Needs:  {NEEDS_DIR}")
        print("=" * 60 + "\n")

        self.is_running = False

    def create_ui(self):
        """Build every widget. Any button that kicks off a background action
        (via run_async) is appended to self.action_buttons so run_async can
        disable/re-enable all of them together as a group. The [STOP] button
        is deliberately excluded - it must stay clickable while something
        is running, since that's the whole point of it."""
        # Title bar
        title = tk.Frame(self.root, bg=self.accent, height=40)
        title.pack(fill=tk.X)
        title.pack_propagate(False)
        tk.Label(title, text="ADOPT ME BOT", font=("Courier", 11, "bold"), bg=self.accent, fg=self.fg).pack(pady=8)

        # Main buttons
        btn_frame = tk.Frame(self.root, bg=self.bg)
        btn_frame.pack(fill=tk.BOTH, expand=False, padx=8, pady=8)

        self.action_buttons = []  # every button that starts a background task

        btn_start = tk.Button(btn_frame, text="[START] WORKFLOW", command=self.run_workflow,
                               font=("Courier", 10, "bold"), bg="#2ecc71", fg=self.fg, height=3, cursor="hand2")
        btn_start.pack(fill=tk.X, pady=4)
        self.action_buttons.append(btn_start)

        btn_loop = tk.Button(btn_frame, text="[LOOP] FULL WORKFLOW", command=self.run_workflow_loop,
                              font=("Courier", 10, "bold"), bg="#27ae60", fg=self.fg, height=3, cursor="hand2")
        btn_loop.pack(fill=tk.X, pady=4)
        self.action_buttons.append(btn_loop)

        # Not added to action_buttons: must remain clickable while a workflow is running
        self.btn_stop = tk.Button(btn_frame, text="[STOP]", command=self.stop,
                                   font=("Courier", 10, "bold"), bg="#d62828", fg=self.fg, height=2, cursor="hand2")
        self.btn_stop.pack(fill=tk.X, pady=4)

        tk.Frame(btn_frame, bg=self.accent, height=2).pack(fill=tk.X, pady=4)

        tk.Label(btn_frame, text="Functions", font=("Courier", 9, "bold"), bg=self.bg, fg=self.accent).pack(anchor=tk.W)

        btn_respawn = tk.Button(btn_frame, text="Respawn", command=lambda: self.run_async(respawn_character),
                                 font=("Courier", 9), bg=self.accent, fg=self.fg, height=1, cursor="hand2")
        btn_respawn.pack(fill=tk.X, pady=2)
        self.action_buttons.append(btn_respawn)

        tk.Frame(btn_frame, bg=self.accent, height=1).pack(fill=tk.X, pady=2)
        tk.Label(btn_frame, text="Tests", font=("Courier", 9, "bold"), bg=self.bg, fg=self.accent).pack(anchor=tk.W)

        # Pet stays behind PET_ENABLED, so this is its only way to run
        # outside of the [TEST] button being pressed directly.
        btn_test_pet = tk.Button(btn_frame, text="[TEST] Pet", command=self.test_pet,
                                  font=("Courier", 9), bg="#e74c3c", fg=self.fg, height=1, cursor="hand2")
        btn_test_pet.pack(fill=tk.X, pady=2)
        self.action_buttons.append(btn_test_pet)

        btn_test_ride = tk.Button(btn_frame, text="[TEST] Ride", command=self.test_ride,
                                   font=("Courier", 9), bg="#e74c3c", fg=self.fg, height=1, cursor="hand2")
        btn_test_ride.pack(fill=tk.X, pady=2)
        self.action_buttons.append(btn_test_ride)

        btn_test_choose = tk.Button(btn_frame, text="[TEST] Choose", command=self.test_choose,
                                     font=("Courier", 9), bg="#e74c3c", fg=self.fg, height=1, cursor="hand2")
        btn_test_choose.pack(fill=tk.X, pady=2)
        self.action_buttons.append(btn_test_choose)

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
        finished normally, was interrupted via STOP_FLAG, or raised an
        exception. That "always" is done with try/except/finally so there is
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

    def test_pet(self):
        """Run the pet handler on its own, outside the normal need-detection
        flow. Useful while PET_ENABLED = False, since the handler is fully
        working code that's just not wired into automatic need processing yet."""
        def test():
            print("\n[TEST] Running pet handler...")
            PetNeedHandler().handle(load_config())
            print("[TEST] Pet handler complete\n")
        self.run_async(test)

    def test_ride(self):
        """Run the ride handler on its own, outside the normal need-detection
        flow - useful for testing it in isolation without waiting for a ride
        icon to actually appear on screen."""
        def test():
            print("\n[TEST] Running ride handler...")
            RideNeedHandler().handle(load_config())
            print("[TEST] Ride handler complete\n")
        self.run_async(test)

    def test_choose(self):
        """Run the choose handler on its own, outside the normal need-detection
        flow. Useful while CHOOSE_ENABLED = False, since the handler is fully
        working code that's just not wired into automatic need processing yet."""
        def test():
            print("\n[TEST] Running choose handler...")
            ChooseNeedHandler().handle(load_config())
            print("[TEST] Choose handler complete\n")
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