# Move mouse to a screen corner to kill instantly (pyautogui fail-safe).

import time, sys, os, json
import numpy as np
import cv2
import mss
import pyautogui
import pydirectinput
import pygetwindow as gw
import keyboard
from pathlib import Path

pyautogui.FAILSAFE = True
pydirectinput.FAILSAFE = True
pydirectinput.PAUSE = 0.05

# ============================================================================
# ============================= CONSTANTS ===================================
# ============================================================================

# Setup data directories
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
NEEDS_DIR = os.path.join(SCRIPT_DIR, "needs")
CONFIG_FILE = os.path.join(SCRIPT_DIR, "adoptme_config.json")

Path(NEEDS_DIR).mkdir(exist_ok=True)

# Timing
RESPAWN_DURATION = 0.05  # Duration each key is held during respawn
WALK_FORWARD_DURATION = 0.8  # Duration to walk forward for button detection
UI_SETTLE_TIME = 0.3  # Time to wait for UI to settle after actions
CLICK_HOVER_TIME = 0.2  # Time to hover over button before clicking
CLICK_MOVE_DURATION = 0.3  # Duration of mouse movement to button
POST_CLICK_DELAY = 0.4  # Delay after clicking

# Focus and detection
FOCUS_DELAY = 0.3  # Delay after focusing window
ICON_DETECTION_TIMEOUT = 2.0  # Timeout for detecting icons

# Need icon detection (top strip)
NEED_ICON_TOP_PERCENT = 0.15  # Top 15% of screen for need icons
NEED_ICON_ZONE_WIDTH_PERCENT = 0.12  # Blank out top-left 12% width
NEED_ICON_ZONE_HEIGHT_PERCENT = 0.08  # Blank out top-left 8% height
NEED_ICON_MIN_AREA = 50  # Minimum area for contour
NEED_ICON_MIN_CIRCULARITY = 0.6  # Minimum circularity to be considered circular
BLUE_LOWER = np.array([95, 100, 100])  # HSV lower bound for blue
BLUE_UPPER = np.array([125, 255, 255])  # HSV upper bound for blue

# Button detection (middle band)
BUTTON_BAND_Y_START_PERCENT = 0.35  # Start at 35% from top
BUTTON_BAND_Y_END_PERCENT = 0.65  # End at 65% from top
BUTTON_BAND_X_START_PERCENT = 0.25  # Start at 25% from left
BUTTON_BAND_X_END_PERCENT = 0.75  # End at 75% from left
BUTTON_MIN_AREA = 150  # Minimum area for button contour
BUTTON_MIN_CIRCULARITY = 0.65  # Minimum circularity for buttons
BUTTON_MAX_COUNT = 5  # Maximum buttons to detect
PURPLE_LOWER = np.array([130, 80, 80])  # HSV lower bound for purple
PURPLE_UPPER = np.array([160, 255, 255])  # HSV upper bound for purple
WHITE_LOWER = np.array([0, 0, 200])  # HSV lower bound for white
WHITE_UPPER = np.array([180, 40, 255])  # HSV upper bound for white
PURPLE_DILATE_ITERATIONS = 3  # Dilation iterations for purple mask
OUTLINE_CHECK_PADDING = 3  # Padding around contour for outline check

# Icon comparison thresholds
ICON_RESIZE_SIZE = 32  # Size to resize icons for comparison
ICON_MATCH_THRESHOLD = 0.82  # Threshold for icon matching (0.0-1.0)
ICON_COLOR_DETECTION_BRIGHTNESS = 100  # Minimum brightness for color detection
ICON_BRIGHTNESS_RANGE = {
    "red": (0, 15, 165, 180),
    "orange": (15, 35),
    "yellow": (35, 50),
    "green": (50, 85),
    "cyan": (85, 110),
    "blue": (110, 135),
    "purple": (135, 165),
}

# Button names (left to right)
BUTTON_NAMES = ["food", "water", "bath", "toilet", "sleep"]

# ============================================================================
# ============================= END CONSTANTS ===============================
# ============================================================================


def load_config():
    """Load button coordinates and needs mapping."""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    return {"buttons": {}, "needs": {}}


def save_config(config):
    """Save button coordinates and needs mapping."""
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)


def focus_roblox():
    wins = [w for w in gw.getAllWindows() if "roblox" in w.title.lower()]
    print(f"[debug] windows matching 'roblox': {[w.title for w in wins]}")
    if not wins:
        print("[debug] Roblox not found. Is it open? Title must contain 'roblox'.")
        return False
    w = wins[0]
    print(f"[debug] focusing window: {w.title} (minimized={w.isMinimized})")
    if w.isMinimized:
        w.restore()
    w.activate()
    time.sleep(FOCUS_DELAY)
    print("[debug] focus_roblox done")
    return True


def walk_forward(duration=None):
    """Walk forward for specified duration."""
    global STOP_FLAG
    
    if duration is None:
        duration = WALK_FORWARD_DURATION
    
    print(f"[debug] walking forward for {duration}s...")
    if not focus_roblox():
        return
    
    try:
        pydirectinput.keyDown("w")
        time.sleep(duration)
    finally:
        pydirectinput.keyUp("w")
    
    print("[debug] walk finished")


def respawn_character():
    global STOP_FLAG
    
    print("[debug] respawning character...")
    if not focus_roblox():
        return
    
    for key in ("esc", "r", "enter"):
        if STOP_FLAG:
            print("[debug] STOP FLAG - aborting respawn")
            break
        
        print(f"[debug]   pressing '{key}'")
        pydirectinput.keyDown(key)
        time.sleep(RESPAWN_DURATION)
        pydirectinput.keyUp(key)
        time.sleep(RESPAWN_DURATION)
    
    print("[debug] respawn finished")


def grab_screen():
    """Screenshot the primary monitor, return as a BGR numpy array (for OpenCV)."""
    with mss.mss() as sct:
        mon = sct.monitors[1]
        shot = np.array(sct.grab(mon))
    return cv2.cvtColor(shot, cv2.COLOR_BGRA2BGR)


def detect_need_icons(save_debug=True):
    """
    Look at the top strip of the screen for pet-need icons: blue filled
    circles with a white outline. Returns their (x, y) center points
    in full-screen coordinates.
    """
    print("[debug] detect_need_icons triggered")
    img = grab_screen()
    h, w = img.shape[:2]

    top_strip = img[0:int(h * NEED_ICON_TOP_PERCENT), 0:w].copy()

    # blank out the top-left icon cluster
    icon_zone_w = int(w * NEED_ICON_ZONE_WIDTH_PERCENT)
    icon_zone_h = int(h * NEED_ICON_ZONE_HEIGHT_PERCENT)
    top_strip[0:icon_zone_h, 0:icon_zone_w] = 0

    hsv = cv2.cvtColor(top_strip, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, BLUE_LOWER, BLUE_UPPER)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    found = []
    debug_img = top_strip.copy()
    icon_info = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < NEED_ICON_MIN_AREA:
            continue
        (cx, cy), radius = cv2.minEnclosingCircle(c)
        circularity = area / (np.pi * radius * radius + 1e-6)
        if circularity < NEED_ICON_MIN_CIRCULARITY:
            continue
        full_x, full_y = int(cx), int(cy)
        found.append((full_x, full_y, int(radius)))
        cv2.circle(debug_img, (full_x, full_y), int(radius), (0, 255, 0), 2)
        
        # Detect color for this icon
        icon_img = extract_need_icon(img, full_x, full_y, int(radius))
        color_name, _ = get_icon_color(icon_img)
        icon_info.append(f"[Icon @ ({full_x},{full_y}) r={int(radius)} color={color_name}]")

    if icon_info:
        print(f"[debug] DETECTED ICONS:")
        for info in icon_info:
            print(f"[debug]   {info}")
    else:
        print(f"[debug] NO NEED ICONS FOUND")

    if save_debug:
        path = os.path.join(SCRIPT_DIR, "adoptme_debug_needs.png")
        cv2.imwrite(path, debug_img)
        print(f"[debug] saved debug image: {path}")

    return found, img


def extract_need_icon(img, cx, cy, radius, padding=5):
    """Extract a square ROI around the detected need icon."""
    r = radius + padding
    x0 = max(0, cx - r)
    x1 = min(img.shape[1], cx + r)
    y0 = max(0, cy - r)
    y1 = min(img.shape[0], cy + r)
    return img[y0:y1, x0:x1].copy()


def get_icon_color(icon_img):
    """Detect the dominant color of the icon. Returns color name and HSV value."""
    # Convert to HSV
    hsv = cv2.cvtColor(icon_img, cv2.COLOR_BGR2HSV)
    
    # Mask out very dark pixels (background)
    v_channel = hsv[:, :, 2]
    bright_mask = v_channel > 100
    
    if np.sum(bright_mask) == 0:
        return "unknown", (0, 0, 0)
    
    # Get average hue of bright pixels
    h_channel = hsv[:, :, 0]
    bright_hues = h_channel[bright_mask]
    avg_hue = np.mean(bright_hues)
    
    # Classify by hue
    # Hue ranges (0-180 in OpenCV): Red=0, Yellow=30, Green=60, Cyan=90, Blue=120, Magenta=150
    if avg_hue < 15 or avg_hue > 165:
        return "red", (0, 0, 255)
    elif 15 <= avg_hue < 35:
        return "orange", (0, 165, 255)
    elif 35 <= avg_hue < 50:
        return "yellow", (0, 255, 255)
    elif 50 <= avg_hue < 85:
        return "green", (0, 255, 0)
    elif 85 <= avg_hue < 110:
        return "cyan", (255, 255, 0)
    elif 110 <= avg_hue < 135:
        return "blue", (255, 0, 0)
    elif 135 <= avg_hue < 165:
        return "purple", (255, 0, 255)
    else:
        return "unknown", (0, 0, 0)


def compare_icons(icon1, icon2, threshold=None):
    """
    Compare two icons using multiple strict methods.
    Returns (is_match, similarity_score).
    """
    if threshold is None:
        threshold = ICON_MATCH_THRESHOLD
    
    size = (ICON_RESIZE_SIZE, ICON_RESIZE_SIZE)
    img1 = cv2.resize(icon1, size)
    img2 = cv2.resize(icon2, size)
    
    gray1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY)
    gray2 = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY)
    
    # Method 1: Direct pixel comparison (MSE)
    mse = np.mean((gray1.astype(float) - gray2.astype(float)) ** 2)
    max_mse = 255 ** 2
    mse_similarity = 1.0 - (mse / max_mse)
    
    # Method 2: Histogram comparison
    hist1 = cv2.calcHist([gray1], [0], None, [256], [0, 256])
    hist2 = cv2.calcHist([gray2], [0], None, [256], [0, 256])
    hist1 = cv2.normalize(hist1, hist1).flatten()
    hist2 = cv2.normalize(hist2, hist2).flatten()
    hist_distance = cv2.compareHist(hist1, hist2, cv2.HISTCMP_BHATTACHARYYA)
    hist_similarity = 1.0 / (1.0 + hist_distance)
    
    # Average methods
    combined_score = (mse_similarity + hist_similarity) / 2.0
    
    print(f"[debug]     MSE={mse_similarity:.4f}, Hist={hist_similarity:.4f}, Combined={combined_score:.4f}, Threshold={threshold}")
    
    is_match = combined_score >= threshold
    return is_match, combined_score


def find_matching_need(icon_img):
    """
    Compare detected icon against all known needs.
    First filters by color, then by symbol similarity.
    Returns (need_name, similarity) or (None, 0) if no match.
    """
    need_files = sorted([f for f in os.listdir(NEEDS_DIR) if f.endswith('.png')])
    
    if not need_files:
        print(f"[debug] NO SAVED NEEDS - no files in {NEEDS_DIR}")
        return None, 0
    
    # Get detected icon color
    detected_color, _ = get_icon_color(icon_img)
    print(f"[debug] ===== MATCHING DETECTED ICON (color={detected_color}) =====")
    print(f"[debug] available need files: {need_files}")
    
    best_match = None
    best_score = 0
    
    # First pass: only compare icons of the same color
    same_color_files = [f for f in need_files if f.startswith(detected_color + "_")]
    print(f"[debug] files matching color '{detected_color}': {same_color_files}")
    
    if not same_color_files:
        print(f"[debug] NO FILES MATCH COLOR '{detected_color}' - cannot match")
        return None, 0
    
    for need_file in same_color_files:
        need_path = os.path.join(NEEDS_DIR, need_file)
        saved_icon = cv2.imread(need_path)
        if saved_icon is None:
            print(f"[debug] FAILED TO READ: {need_file}")
            continue
        
        print(f"[debug] comparing vs {need_file}:")
        is_match, score = compare_icons(icon_img, saved_icon, threshold=0.92)
        
        if score > best_score:
            best_score = score
            best_match = need_file.replace('.png', '')
        
        match_str = "✓ MATCH" if is_match else "✗ NO MATCH"
        print(f"[debug]   {match_str} (score {score:.4f} vs threshold {ICON_MATCH_THRESHOLD})")
    
    # Only match if we found something VERY close
    if best_score >= ICON_MATCH_THRESHOLD:
        print(f"[debug] ===== MATCH FOUND: {best_match} (score {best_score:.4f}) =====")
        return best_match, best_score
    else:
        print(f"[debug] ===== NO MATCH - best was {best_match} with {best_score:.4f} (threshold {ICON_MATCH_THRESHOLD}) =====")
        return None, best_score


def prompt_rename_need(icon_img, icon_num):
    """
    Save a new need icon and prompt user to name it.
    Returns the chosen need name.
    """
    # Detect color
    color_name, _ = get_icon_color(icon_img)
    
    print(f"\n[!] New need detected! Color: {color_name}")
    print(f"[!] Saving as temp_{icon_num}.png")
    temp_path = os.path.join(NEEDS_DIR, f"temp_{icon_num}.png")
    cv2.imwrite(temp_path, icon_img)
    
    print(f"[!] Enter a name for this need (food/water/bath/toilet/sleep/other): ", end="", flush=True)
    need_name = input().strip().lower()
    
    if not need_name:
        need_name = f"need_{icon_num}"
    
    # Include color in the filename for better distinction
    final_name = f"{color_name}_{need_name}"
    final_path = os.path.join(NEEDS_DIR, f"{final_name}.png")
    os.rename(temp_path, final_path)
    print(f"[!] Saved need as: {final_name}")
    
    return final_name


def click_button(button_x, button_y, need_name):
    """
    Click a button with smooth mouse movement so the game registers it.
    """
    # Ensure Roblox is focused
    if not focus_roblox():
        print(f"[!] Failed to focus Roblox, skipping click")
        return False
    
    time.sleep(FOCUS_DELAY)
    
    # Smoothly move to button position so game sees the movement
    print(f"[debug] moving mouse to ({button_x}, {button_y})")
    pyautogui.moveTo(button_x, button_y, duration=CLICK_MOVE_DURATION)
    time.sleep(CLICK_HOVER_TIME)
    
    # Click
    print(f"[debug] clicking {need_name} button")
    pyautogui.click()
    time.sleep(POST_CLICK_DELAY)
    
    return True


def process_needs():
    """
    Detect all need icons, match against known needs, handle new ones,
    and click corresponding buttons if they exist.
    """
    global STOP_FLAG
    
    print("\n[debug] ===== PROCESS_NEEDS START =====")
    config = load_config()
    
    print(f"[debug] current config: {json.dumps(config, indent=2)}")
    
    found_icons, full_img = detect_need_icons()
    
    if not found_icons:
        print("[debug] NO NEED ICONS DETECTED")
        print("[debug] ===== PROCESS_NEEDS END =====\n")
        return
    
    print(f"[debug] found {len(found_icons)} need icon(s)")
    
    for idx, (cx, cy, radius) in enumerate(found_icons):
        if STOP_FLAG:
            print("[debug] STOP FLAG SET - aborting")
            break
        
        print(f"\n[debug] --- ICON #{idx+1} at ({cx}, {cy}) ---")
        icon_img = extract_need_icon(full_img, cx, cy, radius)
        print(f"[debug] extracted icon size: {icon_img.shape}")
        
        # Try to match against known needs
        matched_need, score = find_matching_need(icon_img)
        
        if matched_need:
            print(f"\n[!] ✓ MATCHED: {matched_need} (score: {score:.4f})")
            
            # Extract the base need name (without color prefix)
            # Format is "color_needname", so split on first underscore
            parts = matched_need.split('_', 1)
            base_need = parts[1] if len(parts) > 1 else matched_need
            print(f"[debug] base need name: {base_need}")
            
            # Check if we have a button for this need
            if base_need in config["buttons"]:
                button_x, button_y = config["buttons"][base_need]
                print(f"[!] CLICKING: {base_need} button at ({button_x}, {button_y})")
                click_button(button_x, button_y, base_need)
                time.sleep(0.3)
            else:
                print(f"[!] WARNING: known need '{base_need}' but NO BUTTON MAPPED")
                print(f"[debug] available buttons: {list(config['buttons'].keys())}")
        else:
            print(f"\n[!] ✗ NEW NEED DETECTED (best match score: {score:.4f})")
            need_name = prompt_rename_need(icon_img, idx)
            
            # Update config with new need
            if "needs" not in config:
                config["needs"] = {}
            config["needs"][need_name] = True
            save_config(config)
            print(f"[debug] config updated and saved")
    
    print(f"\n[debug] ===== PROCESS_NEEDS END =====\n")


def go_forward_and_detect_buttons(duration=0.8, save_debug=True):
    """
    After respawning: hold 'w' for `duration` seconds, then look at the
    middle of the screen for up to 5 button-like shapes: purple fill with
    a white outline. Returns their (x, y) centers (left to right).
    """
    global STOP_FLAG
    
    print("[debug] detecting buttons (walk forward then screenshot)...")
    if not focus_roblox():
        print("[debug] failed to focus - aborting")
        return []

    print(f"[debug] holding 'w' for {duration}s...")
    try:
        pydirectinput.keyDown("w")
        time.sleep(duration)
    finally:
        pydirectinput.keyUp("w")
        print("[debug] released 'w' key")

    time.sleep(0.3)  # let UI settle
    img = grab_screen()
    h, w = img.shape[:2]

    # middle band of the screen
    y0, y1 = int(h * 0.35), int(h * 0.65)
    x0, x1 = int(w * 0.25), int(w * 0.75)
    band = img[y0:y1, x0:x1]

    hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)

    PURPLE_LOWER = np.array([130, 80, 80])
    PURPLE_UPPER = np.array([160, 255, 255])
    purple_mask = cv2.inRange(hsv, PURPLE_LOWER, PURPLE_UPPER)

    WHITE_LOWER = np.array([0, 0, 200])
    WHITE_UPPER = np.array([180, 40, 255])
    white_mask = cv2.inRange(hsv, WHITE_LOWER, WHITE_UPPER)

    purple_dilated = cv2.dilate(purple_mask, None, iterations=3)
    outline_zone = cv2.subtract(purple_dilated, cv2.erode(purple_mask, None, iterations=1))
    has_white_outline = cv2.bitwise_and(outline_zone, white_mask)

    contours, _ = cv2.findContours(purple_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    candidates = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < 150:
            continue

        (ecx, ecy), radius = cv2.minEnclosingCircle(c)
        circularity = area / (np.pi * radius * radius + 1e-6)
        if circularity < 0.65:
            continue

        x, y, cw, ch = cv2.boundingRect(c)
        cx, cy = x + cw // 2, y + ch // 2

        roi = has_white_outline[max(0, y - 3):y + ch + 3, max(0, x - 3):x + cw + 3]
        white_score = cv2.countNonZero(roi)

        candidates.append((cx + x0, cy + y0, area, white_score))

    # Sort by position (left to right) and take top 5
    candidates.sort(key=lambda t: (t[3] > 0, t[2]), reverse=True)
    candidates = candidates[:5]
    candidates.sort(key=lambda t: t[0])  # sort by x coordinate (left to right)
    
    positions = [(cx, cy) for cx, cy, _, _ in candidates]

    print(f"[debug] button positions found (left to right): {positions}")

    if save_debug:
        debug_img = img.copy()
        for i, (cx, cy) in enumerate(positions):
            cv2.circle(debug_img, (cx, cy), 8, (0, 0, 255), 2)
            cv2.putText(debug_img, str(i+1), (cx-5, cy-15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
        path = os.path.join(SCRIPT_DIR, "adoptme_debug_buttons.png")
        cv2.imwrite(path, debug_img)
        print(f"[debug] saved debug image: {path}")

    return positions


def map_buttons():
    """
    Detect buttons and map them to need names (food, water, bath, toilet, sleep).
    Saves mapping to config.
    """
    print("\n[debug] ===== MAP_BUTTONS START =====")
    if not focus_roblox():
        print("[debug] ===== MAP_BUTTONS END (focus failed) =====\n")
        return
    
    positions = go_forward_and_detect_buttons()
    
    if not positions:
        print("[!] ERROR: No buttons detected!")
        print("[debug] ===== MAP_BUTTONS END (no buttons) =====\n")
        return
    
    config = load_config()
    
    if "buttons" not in config:
        config["buttons"] = {}
    
    print(f"\n[!] ✓ DETECTED {len(positions)} BUTTON(S)")
    for i, (cx, cy) in enumerate(positions):
        if i < len(BUTTON_NAMES):
            need_name = BUTTON_NAMES[i]
            config["buttons"][need_name] = [cx, cy]
            print(f"[!]   Button {i+1}: '{need_name}' @ ({cx}, {cy})")
        else:
            print(f"[!]   Button {i+1}: EXTRA @ ({cx}, {cy}) (no need assigned)")
    
    save_config(config)
    print(f"\n[!] ✓ SAVED: {CONFIG_FILE}")
    print(f"[debug] button mapping: {config['buttons']}")
    print(f"[debug] ===== MAP_BUTTONS END =====\n")


# Global flag to stop current action
STOP_FLAG = False


def run_full_workflow():
    """
    Full automated workflow:
    1. Respawn character
    2. Map buttons (walks forward as part of detection)
    3. Process needs
    """
    global STOP_FLAG
    STOP_FLAG = False
    
    print("\n" + "="*60)
    print("[START] FULL WORKFLOW")
    print("="*60)
    
    # Step 1: Respawn
    print("\n[STEP 1/3] Respawning character...")
    if STOP_FLAG:
        print("[STOPPED]")
        return
    respawn_character()
    time.sleep(UI_SETTLE_TIME)
    
    # Step 2: Map buttons (includes walking forward)
    print("\n[STEP 2/3] Detecting button coordinates...")
    if STOP_FLAG:
        print("[STOPPED]")
        return
    map_buttons()
    time.sleep(UI_SETTLE_TIME)
    
    # Step 3: Process needs
    print("\n[STEP 3/3] Processing pet needs...")
    if STOP_FLAG:
        print("[STOPPED]")
        return
    process_needs()
    
    print("\n" + "="*60)
    print("[COMPLETE] WORKFLOW FINISHED")
    print("="*60 + "\n")


def main():
    global STOP_FLAG
    
    print("\n" + "="*60)
    print("=== ADOPT ME BOT ===")
    print("="*60)
    print("Ctrl+Alt+2  Run full workflow (respawn → walk → detect needs)")
    print("Ctrl+Alt+3  Stop current action")
    print("Ctrl+Alt+4  Exit bot")
    print(f"\nConfig: {CONFIG_FILE}")
    print(f"Needs:  {NEEDS_DIR}")
    print("="*60)
    print("[debug] entering main loop, waiting for hotkeys...\n")
    
    while True:
        if keyboard.is_pressed("ctrl+alt+2"):
            print("[debug] detected Ctrl+Alt+2 - starting workflow")
            time.sleep(0.3)  # debounce
            run_full_workflow()
            time.sleep(0.5)
        elif keyboard.is_pressed("ctrl+alt+3"):
            print("[debug] detected Ctrl+Alt+3 - STOPPING")
            STOP_FLAG = True
            time.sleep(0.3)
        elif keyboard.is_pressed("ctrl+alt+4"):
            print("[debug] detected Ctrl+Alt+4 - EXITING")
            sys.exit(0)
        time.sleep(0.05)


if __name__ == "__main__":
    main()