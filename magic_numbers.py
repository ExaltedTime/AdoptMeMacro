"""Every tunable constant the macro uses: screen positions, timings,
detection thresholds, and GUI layout. main.py does `from magic_numbers
import *` and refers to these by bare name - this file holds no logic,
only values, so that's the one thing to edit when something needs
retuning (a moved button, a slow computer, a different monitor)."""

import os
from pathlib import Path

import numpy as np
import pyautogui
import pydirectinput

# ============================================================================
# ENABLED NEEDS - the first thing to edit: which needs the macro acts on.
# A need not listed here is still detected and matched, but logged and
# skipped (not resolved) when it comes up. Kept as two separate groups -
# teleport-walk needs leave the map and run considerably longer than
# everything else, so toggling them is its own kind of decision.
# ============================================================================
ENABLED_BUTTON_AND_SPECIAL_NEEDS = {
    "hungry", "thirsty", "dirty", "potty", "sleepy",  # button needs - always safe to leave on
    "catch", "pet", "ride", "walk", "choose",
}
ENABLED_TELEPORT_NEEDS = {"cafe", "salon", "sick", "pizza"}
# bored, beach, school and camping (also teleport-walk needs) are disabled

ENABLED_NEEDS = ENABLED_BUTTON_AND_SPECIAL_NEEDS | ENABLED_TELEPORT_NEEDS

# ============================================================================
# PATHS
# ============================================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
NEEDS_DIR = os.path.join(SCRIPT_DIR, "needs")
DEBUG_DIR = os.path.join(SCRIPT_DIR, "debug")
Path(NEEDS_DIR).mkdir(exist_ok=True)
Path(DEBUG_DIR).mkdir(exist_ok=True)

RUN_LOG_PATH = os.path.join(DEBUG_DIR, "run_log.txt")      # appended to, never overwritten
RUN_COUNTER_PATH = os.path.join(DEBUG_DIR, "run_counter.txt")  # holds the last-used run number

# ============================================================================
# SCREEN / INPUT SETUP
# ============================================================================
pyautogui.FAILSAFE = True
pydirectinput.FAILSAFE = True
pydirectinput.PAUSE = 0.05
SCREEN_WIDTH, SCREEN_HEIGHT = pyautogui.size()
SCREEN_CENTER_X, SCREEN_CENTER_Y = SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2

# Behavior flags
FOCUS_WINDOW_ON_ACTION = True  # click the Roblox window to focus it before acting
SAVE_NEW_NEEDS = False
MATCH_ONLY_LEFT_HALF = True    # compare only the left half of each icon (a badge sits at its top right)

# Timing (seconds)
RESPAWN_KEY_DURATION = 0.05    # how long each respawn key is held
RESPAWN_WAIT = 4.0             # settle time after respawning, before it's usable
WALK_TO_BUTTONS_DURATION = 0.8 # time spent walking forward to reach the action buttons
WALK_ALTERNATING_STEP = 1.0    # duration of each a/d press in alternating walk pattern
KEY_STEP_GAP = 0.1             # pause between consecutive key holds (walk patterns, teleport-walk needs)
WALK_TOTAL_DURATION = 60.0     # total duration to keep walking back and forth (unless need_name confirms gone first)
UI_SETTLE = 0.5                # generic pause for UI to catch up (between clicks in a sequence)
FOCUS_DELAY = 0.3              # pause after focusing the window
FOCUS_CLICK_SETTLE_DELAY = 0.1 # pause after the window-focus click
CLICK_MOVE_DURATION = 0.3      # mouse travel time for an ordinary click
CLICK_SETTLE_DELAY = 0.2       # pause after moving the mouse, before clicking
POST_CLICK_DELAY = 0.4         # pause after each click
NEED_CHECK_RETRY_DELAY = 5.0   # pause before re-checking when no need was found
LOOP_DELAY = 2.0               # pause between iterations of the workflow loop
STOP_CHECK_INTERVAL = 0.1      # granularity of the interruptible wait loop
CATCH_WAIT_AFTER_EQUIP = 2.0   # wait after equipping toy before throwing
CATCH_EMOTE_DELAY = 10.0       # delay between throw clicks
CATCH_THROW_COUNT = 3          # number of times the toy is thrown
CATCH_ZOOM_DURATION = 3.0      # seconds the zoom-in key is held before throwing
PET_CIRCLE_DURATION = 10.0     # how long to make circles with mouse
PET_CIRCLE_RADIUS = 100                # amplitude (px) of the up/down sine motion around screen center
PET_SETTLE_DELAY = 0.1                 # pause after each mouse move/click before the next pet step
PET_CIRCLE_STEP_MOVE_DURATION = 0.05   # time for each small step around the circle
PET_FOCUS_CLICK_DURATION = 0.1         # mouse travel time for the two focus clicks before petting starts
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
# as CHOOSE_BUTTON_COLOR above.
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
RIDE_WALK_DURATION = 60.0      # total time spent walking back and forth while riding (unless need_name confirms gone first)

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
TELEPORT_WALK_STEP_GAP = 4.0      # pause between consecutive steps of a teleport-walk need's sequence
SICK_CONFIRM_WAIT = 7.0           # wait after pressing 'e' and before the sick need's final click

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
NEED_GONE_FLICKER_RECHECK_DELAY = 0.3  # before counting any single miss, re-sample this soon after -
                                        # the icon can flash for a frame (or briefly drop out against a
                                        # busy background) without the need actually having cleared
NEED_WATCH_JOIN_TIMEOUT = 5.0    # most walk_alternating() waits for its background need-gone
                                 # watcher thread to exit before moving on without it

# Game key bindings
KEY_BACKPACK = "b"
KEY_MOUNT = "e"
KEY_INTERACT = "e"             # same physical key as KEY_MOUNT, named for its other use: collecting/
                                # harvesting (lure_collect(), tree_collect()) rather than mounting a vehicle
KEY_ZOOM_IN = "i"
MOVE_KEYS = ("w", "a", "s", "d")
RESPAWN_KEYS = ("esc", "r", "enter")

# Lure/tree collection (GUI-only functions, not tied to a detected need)
LURE_COLLECT_WALK_DURATION = 2.0  # hold 'a' this long before pressing KEY_INTERACT at the lure
TREE_COLLECT_WALK_DURATION = 2.0  # hold 'd' this long before pressing KEY_INTERACT at the money tree

# Window focus click (near top edge, right of center)
FOCUS_CLICK_X_PERCENT = 0.75
FOCUS_CLICK_Y = 5

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

# ============================================================================
# GUI
# ============================================================================
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

# GUI layout spacing
GUI_OUTER_PADDING = 8       # outer padx/pady around the notebook, debug console, and status bar
GUI_ROW_SPACING = 4         # vertical gap between stacked rows, and the horizontal gap between the start/loop/stop buttons
GUI_WIDGET_SPACING = 2      # small gap between tightly packed widgets (debug-tab [TEST] buttons, label pady)
GUI_LABEL_PADDING = 3       # bottom pady under the "Output" label, and around the status text
GUI_DIVIDER_HEIGHT = 2      # px, thickness of the horizontal divider line

# GUI font sizes (family is GUI_FONT; weight is given at each call site)
GUI_START_ICON_FONT_SIZE = 14
GUI_STOP_ICON_FONT_SIZE = 16
GUI_LOOP_ICON_FONT_SIZE = 20
GUI_SECTION_FONT_SIZE = 9   # section headers, ordinary buttons/labels
GUI_STATUS_FONT_SIZE = 8
GUI_CONSOLE_FONT_SIZE = 7

GUI_CONSOLE_HEIGHT = 14          # lines
GUI_CONSOLE_WIDTH = 45           # chars
GUI_RESPAWN_BUTTON_HEIGHT = 1    # lines

__all__ = [
    "ENABLED_NEEDS",
    "SCRIPT_DIR", "NEEDS_DIR", "DEBUG_DIR", "RUN_LOG_PATH", "RUN_COUNTER_PATH",
    "SCREEN_WIDTH", "SCREEN_HEIGHT", "SCREEN_CENTER_X", "SCREEN_CENTER_Y",
    "FOCUS_WINDOW_ON_ACTION", "SAVE_NEW_NEEDS", "MATCH_ONLY_LEFT_HALF",
    "RESPAWN_KEY_DURATION", "RESPAWN_WAIT", "WALK_TO_BUTTONS_DURATION", "WALK_ALTERNATING_STEP",
    "KEY_STEP_GAP", "WALK_TOTAL_DURATION", "UI_SETTLE", "FOCUS_DELAY", "FOCUS_CLICK_SETTLE_DELAY",
    "CLICK_MOVE_DURATION", "CLICK_SETTLE_DELAY", "POST_CLICK_DELAY",
    "NEED_CHECK_RETRY_DELAY", "LOOP_DELAY",
    "STOP_CHECK_INTERVAL", "CATCH_WAIT_AFTER_EQUIP", "CATCH_EMOTE_DELAY", "CATCH_THROW_COUNT",
    "CATCH_ZOOM_DURATION", "PET_CIRCLE_DURATION", "PET_CIRCLE_RADIUS", "PET_SETTLE_DELAY",
    "PET_CIRCLE_STEP_MOVE_DURATION", "PET_FOCUS_CLICK_DURATION", "ICON_EXTRACT_PADDING",
    "CATCH_TOYS_POS", "CATCH_SQUEAKY_TOY_POS", "CATCH_EQUIP_POS", "CATCH_UNEQUIP_POS",
    "EMPTY_POS", "FOCUS_PET_POS",
    "CHOOSE_BUTTON_COLOR", "CHOOSE_SLOW_MOVE_DURATION",
    "PAYCHECK_CASHOUT_COLOR", "PAYCHECK_DISMISS_POS_1", "PAYCHECK_DISMISS_POS_2",
    "RIDE_BACKWARD_DURATION", "RIDE_WAIT_AFTER_E", "RIDE_FORWARD_DURATION", "RIDE_VEHICLES_POS",
    "RIDE_FIRST_VEHICLE_POS", "RIDE_EQUIP_POS", "RIDE_WALK_DURATION",
    "TELEPORT_WAIT", "TELEPORT_SETTLE_WAIT", "GENERAL_TELEPORT_POS_2", "GENERAL_TELEPORT_POS_3",
    "TELEPORT_BACK_DURATION", "TELEPORT_WALK_STEP_GAP", "SICK_CONFIRM_WAIT",
    "TELEPORT_PETS_TAB_POS", "TELEPORT_VEHICLES_TAB_POS", "TELEPORT_FOOD_TAB_POS", "SICK_FINAL_CLICK_POS",
    "TELEPORT_WALK_NEEDS",
    "NEED_GONE_MAX_WAIT", "NEED_GONE_POLL_INTERVAL", "NEED_GONE_CONFIRMATIONS",
    "NEED_GONE_CONFIRM_INTERVAL", "NEED_GONE_FLICKER_RECHECK_DELAY", "NEED_WATCH_JOIN_TIMEOUT",
    "KEY_BACKPACK", "KEY_MOUNT", "KEY_INTERACT", "KEY_ZOOM_IN", "MOVE_KEYS", "RESPAWN_KEYS",
    "LURE_COLLECT_WALK_DURATION", "TREE_COLLECT_WALK_DURATION",
    "FOCUS_CLICK_X_PERCENT", "FOCUS_CLICK_Y", "HOVER_NUDGE_PIXELS",
    "NEED_ICON_TOP_PERCENT", "NEED_ICON_WIDTH_PERCENT", "NEED_ICON_BLANK_HEIGHT", "NEED_ICON_BLANK_WIDTH",
    "NEED_ICON_MIN_RADIUS", "NEED_ICON_MAX_RADIUS", "NEED_ICON_MIN_DISTANCE", "NEED_ICON_BLUR_KERNEL",
    "NEED_ICON_HOUGH_EDGE", "NEED_ICON_HOUGH_VOTES", "CIRCULARITY_EPSILON", "PIXEL_MAX",
    "ICON_MATCH_THRESHOLD", "ICON_BW_THRESHOLD", "ICON_CLAHE_CLIP", "ICON_CLAHE_TILE",
    "ICON_COMPARE_SIZE", "ICON_CROP_RADIUS", "ICON_SHIFT_TOLERANCE",
    "BUTTON_BAND_Y", "BUTTON_BAND_X", "BUTTON_MIN_AREA", "BUTTON_MIN_CIRCULARITY", "BUTTON_MAX_COUNT",
    "BUTTON_NAMES", "BUTTON_LOOSE_AREA_FACTOR", "BUTTON_MAX_AREA_FRACTION", "BUTTON_MIN_RADIUS",
    "BUTTON_LOOSE_CIRCULARITY_FACTOR", "BUTTON_PURPLE_DILATE_ITERATIONS", "BUTTON_PURPLE_ERODE_ITERATIONS",
    "BUTTON_OUTLINE_PADDING",
    "DEBUG_NEED_MARKER_COLOR", "DEBUG_NEED_MARKER_THICKNESS", "DEBUG_BUTTON_MARKER_COLOR",
    "DEBUG_BUTTON_MARKER_RADIUS", "DEBUG_BUTTON_MARKER_THICKNESS", "DEBUG_BUTTON_LABEL_OFFSET",
    "DEBUG_BUTTON_LABEL_SCALE",
    "PURPLE_RANGE", "WHITE_RANGE",
    "GUI_GEOMETRY", "GUI_ALPHA", "GUI_FONT", "GUI_BG", "GUI_ACCENT", "GUI_FG", "GUI_START_COLOR",
    "GUI_STOP_COLOR", "GUI_LOOP_COLOR", "GUI_RESPAWN_COLOR", "GUI_TEST_COLOR", "GUI_CONSOLE_BG",
    "GUI_CONSOLE_FG", "GUI_SQUARE_BUTTON_SIZE", "GUI_STATUS_HEIGHT",
    "GUI_OUTER_PADDING", "GUI_ROW_SPACING", "GUI_WIDGET_SPACING", "GUI_LABEL_PADDING", "GUI_DIVIDER_HEIGHT",
    "GUI_START_ICON_FONT_SIZE", "GUI_STOP_ICON_FONT_SIZE", "GUI_LOOP_ICON_FONT_SIZE",
    "GUI_SECTION_FONT_SIZE", "GUI_STATUS_FONT_SIZE", "GUI_CONSOLE_FONT_SIZE",
    "GUI_CONSOLE_HEIGHT", "GUI_CONSOLE_WIDTH", "GUI_RESPAWN_BUTTON_HEIGHT",
]
