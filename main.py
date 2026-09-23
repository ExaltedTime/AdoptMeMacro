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
# satisfying one of the five basic needs (hungry/thirsty/dirty/potty/sleepy)
# requires being home, and that handler walks there (and refreshes the
# button mapping) itself, on demand.
#
# Key pieces, top to bottom:
#   - CONSTANTS          all tunable numbers/timings/colors in one place
#   - CONFIG              load/save adoptme_config.json (button positions)
#   - WINDOW FOCUS         bring Roblox to front, grab screenshots
#   - POSITIONING          tracks whether the character is at "respawn" or
#                          "home" (Position enum); go_home() walks there
#                          only if not already there
#   - ICON PROCESSING       turns an icon into strict black & white so it
#                          can be matched regardless of its original color
#   - NEED ICON DETECTION   finds circular need icons at the top of screen,
#                          compares them to saved reference icons in needs/
#   - CLICKING              jitter_click / simple_click / double_click /
#                          triple_click - different click patterns for
#                          different UI elements
#   - BUTTON DETECTION      finds the purple action buttons (hungry,
#                          thirsty, dirty, potty, sleepy) and saves their
#                          screen positions to config
#   - MOVEMENT              respawn_character(), walk_left_right()
#   - NEED HANDLERS         one class per need type (ABC pattern), each
#                          knows what position it needs and what to click.
#                          CatchNeedHandler runs a longer backpack ->
#                          equip toy -> throw x3 -> unequip sequence
#   - WORKFLOWS             run_full_cycle() ties respawn/map/process
#                          together; run_workflow_loop() repeats it
#   - GUI                   tkinter control panel; runs workflows on a
#                          background thread so the UI stays responsive;
#                          STOP_FLAG + wait_interruptible() let a running
#                          workflow bail out of long waits within ~0.1s

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
CATCH_ENABLED = False          # disable catch need handler (set to True to re-enable)

# Timing (seconds)
RESPAWN_KEY_DURATION = 0.05    # how long each respawn key is held
RESPAWN_WAIT = 4.0             # settle time after respawning, before it's usable
HOME_WALK_DURATION = 0.8       # time spent walking from respawn to home
WALK_LR_DURATION = 5.0         # walk time per direction in walk_left_right()
UI_SETTLE = 0.3                # generic pause for UI to catch up
FOCUS_DELAY = 0.3              # pause after focusing the window
POST_CLICK_DELAY = 0.4         # pause after each click
POST_NEED_CLICK_WAIT = 15      # pause after satisfying a need via button click
LOOP_DELAY = 2.0               # pause between iterations of the workflow loop
CATCH_WAIT_AFTER_EQUIP = 2.0   # wait after equipping toy before throwing
CATCH_EMOTE_DELAY = 10.0       # delay between throw clicks
CATCH_CLICK_DELAY = 0.5        # delay between catch sequence clicks

# Catch need positions (remembered for backpack interactions)
CATCH_BACKPACK_POS = (956, 1013)
CATCH_TOYS_POS = (754, 854)
CATCH_SQUEAKY_TOY_POS = (976, 875)
CATCH_EQUIP_POS = (1083, 1187)
CATCH_CLOSE_BACKPACK_POS = (1188, 862)
CATCH_EMOTE_POS = (380, 557)
CATCH_UNEQUIP_POS = (1089, 1429)

# Window focus click (near top edge, right of center)
FOCUS_CLICK_X_PERCENT = 0.75
FOCUS_CLICK_Y = 5

# Jitter click (click, nudge mouse, click again)
JITTER_PIXELS = 5
JITTER_MOVE_DURATION = 0.1

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
    """Move the character to the given position, if a route for it exists."""
    if position == Position.HOME:
        go_home()

def go_home():
    """Walk forward from the respawn spot to the home position, if not there already."""
    global CURRENT_POSITION
    if CURRENT_POSITION == Position.HOME:
        return
    if not focus_roblox():
        return
    print("[debug] walking to home position...")
    pydirectinput.keyDown("w")
    if wait_interruptible(HOME_WALK_DURATION):
        pydirectinput.keyUp("w")
        return
    pydirectinput.keyUp("w")
    if wait_interruptible(UI_SETTLE):
        return
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

def double_click(x, y):
    """Double click at position."""
    pyautogui.moveTo(x, y, duration=0.3)
    time.sleep(0.2)
    pydirectinput.click()
    time.sleep(0.1)
    pydirectinput.click()
    time.sleep(POST_CLICK_DELAY)

def triple_click(x, y):
    """Triple click at position."""
    pyautogui.moveTo(x, y, duration=0.3)
    time.sleep(0.2)
    pydirectinput.click()
    time.sleep(0.1)
    pydirectinput.click()
    time.sleep(0.1)
    pydirectinput.click()
    time.sleep(POST_CLICK_DELAY)

def wait_interruptible(duration):
    """Sleep for duration, checking STOP_FLAG every 0.1s. Returns True if interrupted."""
    global STOP_FLAG
    elapsed = 0
    while elapsed < duration:
        if STOP_FLAG:
            return True
        sleep_chunk = min(0.1, duration - elapsed)
        time.sleep(sleep_chunk)
        elapsed += sleep_chunk
    return False

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

def map_buttons():
    """Move to the home position (if needed) and (re)detect the action buttons.
    This is the manual entry point used by the GUI's 'Map Buttons' button."""
    print("\n[debug] mapping buttons...")
    go_home()
    refresh_button_mapping()

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
    if wait_interruptible(RESPAWN_WAIT):
        return
    CURRENT_POSITION = Position.RESPAWN
    print("[debug] respawn complete")

def walk_left_right():
    """Walk left then right (~5s each). Internal use only - not exposed in the GUI."""
    global STOP_FLAG
    print("[debug] walking left and right...")
    if not focus_roblox():
        return
    for direction in ("a", "d"):
        if STOP_FLAG:
            break
        print(f"[debug] walking {direction}...")
        pydirectinput.keyDown(direction)
        if wait_interruptible(WALK_LR_DURATION):
            pydirectinput.keyUp(direction)
            return
        pydirectinput.keyUp(direction)
        time.sleep(0.2)
    print("[debug] walk complete")

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
    """The 'walk' need is satisfied by moving around, not clicking a button."""

    def handle(self, config):
        print("[!] WALK NEED")
        walk_left_right()
        return True

class CatchNeedHandler(NeedHandler):
    """The 'catch' need requires opening backpack, equipping a toy, then throwing it."""
    position = None  # works at any position (respawn or home)

    def handle(self, config):
        global STOP_FLAG
        print("[!] CATCH NEED")
        if not focus_roblox():
            return False

        # Open backpack (simple click)
        print("[debug] opening backpack...")
        simple_click(*CATCH_BACKPACK_POS)
        if STOP_FLAG:
            return False
        if wait_interruptible(CATCH_CLICK_DELAY):
            return False

        # Click empty space before navigating to toys
        print("[debug] clicking empty space...")
        simple_click(*CATCH_EMOTE_POS)
        if STOP_FLAG:
            return False
        if wait_interruptible(CATCH_CLICK_DELAY):
            return False

        # Navigate to toys (simple click)
        print("[debug] opening toys...")
        simple_click(*CATCH_TOYS_POS)
        if STOP_FLAG:
            return False
        if wait_interruptible(CATCH_CLICK_DELAY):
            return False

        # Click squeaky toy (simple click)
        print("[debug] selecting squeaky toy...")
        simple_click(*CATCH_SQUEAKY_TOY_POS)
        if STOP_FLAG:
            return False
        if wait_interruptible(CATCH_CLICK_DELAY):
            return False

        # Equip the toy (simple click)
        print("[debug] equipping toy...")
        simple_click(*CATCH_EQUIP_POS)
        if STOP_FLAG:
            return False
        if wait_interruptible(CATCH_CLICK_DELAY):
            return False

        # Close backpack (simple click)
        print("[debug] closing backpack...")
        simple_click(*CATCH_CLOSE_BACKPACK_POS)
        if STOP_FLAG:
            return False
        if wait_interruptible(CATCH_CLICK_DELAY):
            return False

        # Wait before throwing
        print(f"[debug] waiting {CATCH_WAIT_AFTER_EQUIP}s before throwing...")
        if wait_interruptible(CATCH_WAIT_AFTER_EQUIP):
            return False

        # Click empty space three times to throw with delays
        for i in range(3):
            if STOP_FLAG:
                return False
            print(f"[debug] throw {i + 1}/3...")
            simple_click(*CATCH_EMOTE_POS)
            if STOP_FLAG:
                return False
            if i < 2:  # Don't wait after the last throw
                if wait_interruptible(CATCH_EMOTE_DELAY):
                    return False

        # Unequip the toy (simple click)
        print("[debug] unequipping toy...")
        simple_click(*CATCH_UNEQUIP_POS)

        print("[!] Catch complete!")
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
        global STOP_FLAG
        self.ensure_position()
        if STOP_FLAG:
            return False

        refresh_button_mapping()
        if STOP_FLAG:
            return False
        config = load_config()  # reload to pick up the freshly mapped buttons

        if self.need_name not in config["buttons"]:
            print(f"[!] WARNING: no button mapped for '{self.need_name}'")
            return False

        button_x, button_y = config["buttons"][self.need_name]
        print(f"[!] CLICKING: {self.need_name}")
        click_button(button_x, button_y, self.need_name)
        print(f"[debug] waiting {POST_NEED_CLICK_WAIT}s before next action...")
        if wait_interruptible(POST_NEED_CLICK_WAIT):
            return False
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
}

def get_need_handler(need_name):
    """Return the handler responsible for a given need name."""
    if need_name.startswith("walk"):
        return WalkNeedHandler()
    if need_name == "catch" and not CATCH_ENABLED:
        return None  # catch is disabled
    handler_cls = NEED_HANDLER_CLASSES.get(need_name)
    if handler_cls:
        return handler_cls()
    return ButtonNeedHandler(need_name)  # fallback for any custom need

def needs_action_pending(config):
    """True if a need currently on screen already has a handler ready to act."""
    found_icons, full_img = detect_need_icons()
    for cx, cy, radius in found_icons:
        icon_img = extract_icon(full_img, cx, cy, radius)
        matched_need, score = find_matching_need(icon_img)
        if not matched_need or score < ICON_MATCH_THRESHOLD:
            continue
        need_name = base_need_name(matched_need)
        if need_name.startswith("walk") or need_name in config["buttons"]:
            return True
        if need_name == "catch" and CATCH_ENABLED:
            return True
    return False

def process_needs():
    """Detect needs on screen and dispatch each to its handler."""
    global STOP_FLAG

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
        if STOP_FLAG:
            break

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
    """Repeat the workflow continuously."""
    global STOP_FLAG
    STOP_FLAG = False
    print("\n" + "=" * 50)
    print("[LOOP] Starting continuous workflow")
    print("=" * 50)

    loop_num = 0
    while not STOP_FLAG:
        loop_num += 1
        print(f"\n[LOOP {loop_num}]")

        run_full_cycle()
        if STOP_FLAG:
            break

        if wait_interruptible(LOOP_DELAY):
            break

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
        self.current_thread = None  # track the active workflow thread

    def create_ui(self):
        # Title bar
        title = tk.Frame(self.root, bg=self.accent, height=40)
        title.pack(fill=tk.X)
        title.pack_propagate(False)
        tk.Label(title, text="ADOPT ME BOT", font=("Courier", 11, "bold"), bg=self.accent, fg=self.fg).pack(pady=8)

        # Main buttons
        btn_frame = tk.Frame(self.root, bg=self.bg)
        btn_frame.pack(fill=tk.BOTH, expand=False, padx=8, pady=8)

        self.btn_start = tk.Button(btn_frame, text="[START] WORKFLOW", command=self.run_workflow,
                                    font=("Courier", 10, "bold"), bg="#2ecc71", fg=self.fg, height=3, cursor="hand2")
        self.btn_start.pack(fill=tk.X, pady=4)

        self.btn_loop = tk.Button(btn_frame, text="[LOOP] FULL WORKFLOW", command=self.run_workflow_loop,
                                   font=("Courier", 10, "bold"), bg="#27ae60", fg=self.fg, height=3, cursor="hand2")
        self.btn_loop.pack(fill=tk.X, pady=4)

        self.btn_stop = tk.Button(btn_frame, text="[STOP]", command=self.stop,
                                   font=("Courier", 10, "bold"), bg="#d62828", fg=self.fg, height=2, cursor="hand2")
        self.btn_stop.pack(fill=tk.X, pady=4)

        tk.Frame(btn_frame, bg=self.accent, height=2).pack(fill=tk.X, pady=4)

        tk.Label(btn_frame, text="Functions", font=("Courier", 9, "bold"), bg=self.bg, fg=self.accent).pack(anchor=tk.W)

        for label, func in [("1. Respawn", respawn_character),
                            ("2. Map Buttons", map_buttons),
                            ("3. Process Needs", process_needs)]:
            tk.Button(btn_frame, text=label, command=lambda f=func: self.run_async(f),
                     font=("Courier", 9), bg=self.accent, fg=self.fg, height=1, cursor="hand2").pack(fill=tk.X, pady=2)

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
        global STOP_FLAG

        if self.is_running:
            print("[!] Already running")
            return
        self.is_running = True
        self.status.config(text="Running...")
        self.btn_start.config(state=tk.DISABLED)
        self.btn_loop.config(state=tk.DISABLED)

        def run():
            global STOP_FLAG
            try:
                func()
                self.status.config(text="[DONE]")
            except Exception as e:
                print(f"\n[ERROR] {e}")
                self.status.config(text="[ERROR]")
            finally:
                STOP_FLAG = False

        self.current_thread = threading.Thread(target=run, daemon=True)
        self.current_thread.start()

    def run_workflow(self):
        self.run_async(run_workflow)

    def run_workflow_loop(self):
        self.run_async(run_workflow_loop)

    def stop(self):
        global STOP_FLAG
        if not self.is_running:
            return
        print("\n[!] STOPPING...")
        STOP_FLAG = True
        self.status.config(text="[STOPPED]")
        self.btn_start.config(state=tk.NORMAL)
        self.btn_loop.config(state=tk.NORMAL)

        # Watchdog thread: monitor the workflow thread and clean up when it's done
        def cleanup_watchdog():
            if self.current_thread:
                # Check every second if the thread is still alive
                while self.current_thread.is_alive():
                    time.sleep(1.0)
            # Thread is done, update final state
            self.is_running = False

        threading.Thread(target=cleanup_watchdog, daemon=True).start()

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    root = tk.Tk()
    gui = AdoptMeGUI(root)
    root.mainloop()