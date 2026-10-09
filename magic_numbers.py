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
# skipped (not resolved) when it comes up.
# ============================================================================
ENABLED_NEEDS = {
    "hungry", "thirsty", "dirty", "potty", "sleepy",  # button needs - always safe to leave on
    "catch", "pet", "ride", "walk", "choose",
    "cafe", "salon", "sick", "pizza", "school", "beach", "camping", "bored",  # teleport-walk needs
}

# ============================================================================
# EVENT FLAGS
# ============================================================================
# While HALLOWEEN is on, some teleport needs use different steps (see
# TELEPORT_WALK_NEEDS_HALLOWEEN) and unscrew() also runs minigame_popup().
HALLOWEEN = True
# Only matters while HALLOWEEN is on: True = minigame_popup() plays the
# minigame, False = it just dismisses the popup (and ticks "do not show again").
MINIGAME_POPUP_PLAY = False

# The "Ghost Gallery / Hauntlet 2 is starting soon! Teleport there now?" popups (one layout) are recognised by their
# Yes button (green) with its No button (red) close to the left - see detect_minigame_popup().
# Dismissing it ticks "Do not show again this session", then clicks No.
MINIGAME_POPUP_YES_COLOR = (74, 198, 85)    # same green as PAYCHECK_CASHOUT_COLOR
MINIGAME_POPUP_NO_COLOR = (216, 42, 63)
MINIGAME_POPUP_MIN_BUTTON_PIXELS = 1000     # a button is ~5000 exact-color px; fewer is noise
MINIGAME_POPUP_NO_MAX_DX = 300              # No button must be within this many px left of Yes...
MINIGAME_POPUP_NO_MAX_DY = 40               # ...and this many px above/below it
MINIGAME_POPUP_DONT_SHOW_POS = (826, 666)
MINIGAME_POPUP_NO_POS = (879, 618)

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
GAME_CONFIG_PATH = os.path.join(DEBUG_DIR, ".config")      # persisted game state - see load_game_config()
OUTPUT_LOG_PATH = os.path.join(DEBUG_DIR, "output.log")    # everything the macro prints, timestamped - see log_output()
FAILURE_DIR = os.path.join(DEBUG_DIR, "failures")          # a screenshot per failure - see log_failure()
STATUS_PATH = os.path.join(DEBUG_DIR, "status.json")       # live "is it alive and what's it doing" summary - see write_status()
OUTPUT_LOG_MAX_BYTES = 5 * 1024 * 1024   # output.log moves to output.log.old (replacing the last one) past this size
MAX_FAILURE_SCREENSHOTS = 40             # oldest are deleted beyond this many

# ============================================================================
# SCREEN / INPUT SETUP
# ============================================================================
pyautogui.FAILSAFE = True
pydirectinput.FAILSAFE = True
pydirectinput.PAUSE = 0.05

# Every position, size and screen region in this file was measured with the Roblox window maximized on
# a 1920x1080 screen. The macro works in that "reference" space: grab_screen() crops the screenshot to
# the Roblox window and scales it to this size, and clicks and mouse moves are scaled from it back onto
# the window (see to_screen()). On a 1920x1080 screen with Roblox maximized nothing is scaled at all.
REFERENCE_WIDTH, REFERENCE_HEIGHT = 1920, 1080
REFERENCE_CENTER_X, REFERENCE_CENTER_Y = REFERENCE_WIDTH // 2, REFERENCE_HEIGHT // 2
ROBLOX_RECT_TTL = 1.0   # seconds the Roblox window's position and size are remembered between lookups

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
UI_SETTLE = 0.5                # generic pause for UI to catch up (between clicks in a sequence)
FOCUS_DELAY = 0.3              # pause after focusing the window
FOCUS_CLICK_SETTLE_DELAY = 0.1 # pause after the window-focus click
CLICK_MOVE_DURATION = 0.3      # mouse travel time for an ordinary click
CLICK_SETTLE_DELAY = 0.2       # pause after moving the mouse, before clicking
POST_CLICK_DELAY = 0.4         # pause after each click
NEED_CHECK_RETRY_DELAY = 5.0   # pause before re-checking when no need was found
LOOP_DELAY = 2.0               # pause between iterations of the workflow loop
STOP_CHECK_INTERVAL = 0.1      # granularity of the interruptible wait loop
STOP_HOTKEY = "p"              # a letter or digit: pressing it, in any window, stops a running macro
                               # (so you can take over the computer) - see watch_stop_hotkey()
STOP_HOTKEY_POLL_INTERVAL = 0.05  # how often the key is looked at (seconds)
CATCH_WAIT_AFTER_EQUIP = 1.0   # wait after equipping toy before throwing
CATCH_EMOTE_DELAY = 5.0       # delay between throw clicks
CATCH_THROW_COUNT = 3          # number of times the toy is thrown
CATCH_ZOOM_DURATION = 2.0      # seconds the zoom-in key is held before throwing
PET_CIRCLE_DURATION = 8.0     # how long to make circles with mouse
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

# focus_pet() finds the pet by what moves: it grabs the bottom part of the screen twice,
# FOCUS_PET_FRAME_GAP apart, and clicks the center of every blob of pixels that changed
# (the pet bobs up and down a little even when idle). Clicking it opens the pet's
# interaction menu (pet/weather/feed/dress up/tricks/pick up/ride/fly icons).
FOCUS_PET_REGION_TOP_PERCENT = 0.4   # only look at the bottom (1 - this) of the screen
FOCUS_PET_FRAME_GAP = 0.1            # seconds between the two screenshots
FOCUS_PET_DIFF_THRESHOLD = 25        # per-pixel brightness change that counts as movement (0-255)
FOCUS_PET_MERGE_KERNEL = 25          # px; changed pixels this close together merge into one blob
FOCUS_PET_MIN_AREA = 150             # px^2; smaller blobs are noise, not the pet
FOCUS_PET_MENU_WAIT = 4.0            # wait after the clicks, for the pet's menu to open

# The 'choose' need's button is matched by its exact color rather than shape,
# since it's just a plain circle with no distinguishing icon. Given as (R, G, B).
CHOOSE_BUTTON_COLOR = (181, 6, 254)
CHOOSE_SLOW_MOVE_DURATION = 1.0  # deliberate, slow mouse travel to the found button

# The paycheck popup's CASH OUT button, matched by exact color the same way
# as CHOOSE_BUTTON_COLOR above.
PAYCHECK_CASHOUT_COLOR = (74, 198, 85)
# That green is everywhere in the game (the backpack's Select All button, the Play and Yes buttons...), so
# it only counts inside PAYCHECK_REGION (left, top, right, bottom, around the dismiss positions below), and
# only with at least PAYCHECK_MIN_PIXELS of it.
PAYCHECK_REGION = (700, 540, 1220, 800)
PAYCHECK_MIN_PIXELS = 200
PAYCHECK_DISMISS_POS_1 = (946, 679)
PAYCHECK_DISMISS_POS_2 = (948, 627)

# The backpack is open when its purple header bar fills BACKPACK_HEADER_BOX (left, top, right, bottom) -
# see detect_backpack_open(). unscrew() presses KEY_BACKPACK if it's open at the start of a cycle, when
# no handler should have it open.
BACKPACK_HEADER_COLOR = (143, 74, 255)
BACKPACK_HEADER_TOLERANCE = 4
BACKPACK_HEADER_BOX = (1090, 36, 1840, 70)
BACKPACK_HEADER_MIN_FRACTION = 0.5

# Ride need: positions for the backpack -> vehicles -> first vehicle -> equip
# sequence, plus how long to hold each step.
RIDE_BACKWARD_DURATION = 1.0   # step back before pressing e to mount
RIDE_WAIT_AFTER_E = 5.0        # wait after e before opening the backpack
RIDE_FORWARD_DURATION = 2.0    # walk forward briefly after mounting, before the backpack
RIDE_VEHICLES_POS = (816, 804)
RIDE_FIRST_VEHICLE_POS = (976, 708)
RIDE_EQUIP_POS = (1062, 814)
RIDE_R_HOLD_DURATION = 1.0     # hold KEY_HELICOPTER this long after equipping, before walking back and forth

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
SICK_FINAL_CLICK_POS = (1045, 660)       # click after the sick need's walk

# Whether a teleport-walk need flies by helicopter by default; an entry can
# override it with `helicopter=True/False`. Only the Halloween bored, beach
# and camping entries turn it on.
HELICOPTER_REQUIRED = False
HELICOPTER_FORWARD_DURATION = 1.0  # step forward this long after teleporting, before equipping
HELICOPTER_HOLD_DURATION = 4.0     # then hold KEY_HELICOPTER this long

# Needs that teleport somewhere and walk (see TeleportWalkNeedHandler), then
# wait for the need to clear. Each entry: where to teleport, the
# (key, seconds) holds to perform in order, optionally a `final_click`
# position to click after the last hold, and optionally `helicopter` (above).
TELEPORT_WALK_NEEDS_NORMAL = {
    "bored":   dict(teleport_pos=TELEPORT_PETS_TAB_POS,     steps=(("w", 16.0), ("a", 10.0))),
    "beach":   dict(teleport_pos=TELEPORT_PETS_TAB_POS,     steps=(("a", 27.0),)),
    "school":  dict(teleport_pos=TELEPORT_PETS_TAB_POS,     steps=(("w", 1.2), ("a", 10.0))),
    "cafe":    dict(teleport_pos=TELEPORT_VEHICLES_TAB_POS, steps=(("a", 3.1), ("s", 8.0), ("d", 4))),
    "salon":   dict(teleport_pos=TELEPORT_VEHICLES_TAB_POS, steps=(("a", 2.5), ("w", 15.0))),
    "pizza":   dict(teleport_pos=TELEPORT_VEHICLES_TAB_POS, steps=(("w", 2.0), ("a", 6.5), ("s", 3.0), ("w", 15.0))),
    "camping": dict(teleport_pos=TELEPORT_PETS_TAB_POS,     steps=(("w", 0.1), ("d", 3.0), ("s", 2.2), ("d", 15.0), ("w",20.0))),
}

# Used instead of the entry of the same name above while HALLOWEEN is on (Halloween
# moves the nursery) - and "sick" only exists here, so it isn't a need at all
# outside Halloween.
TELEPORT_WALK_NEEDS_HALLOWEEN = {
    "bored":   dict(teleport_pos=TELEPORT_PETS_TAB_POS,     steps=(("w", 7.7),), helicopter=True),
    "beach":   dict(teleport_pos=TELEPORT_PETS_TAB_POS,     steps=(("a", 20.0),), helicopter=True),
    "school":  dict(teleport_pos=TELEPORT_PETS_TAB_POS,     steps=(("w", 2.8), ("a", 10))),
    "camping": dict(teleport_pos=TELEPORT_PETS_TAB_POS,     steps=(("s", 20),), helicopter=True),
    "sick":    dict(teleport_pos=TELEPORT_PETS_TAB_POS,     steps=(("w", 2.5), ("d", 5.0), ("w", 1.5)),
                    final_click=SICK_FINAL_CLICK_POS),
}

# The set actually used everywhere else (TeleportWalkNeedHandler and the
# handler registry both read this).
TELEPORT_WALK_NEEDS = {**TELEPORT_WALK_NEEDS_NORMAL, **TELEPORT_WALK_NEEDS_HALLOWEEN} if HALLOWEEN \
    else TELEPORT_WALK_NEEDS_NORMAL

DISABLED_NEEDS_REJOIN_THRESHOLD = 4  # this many needs disabled for the run (see NEED_STUCK_CHECKS) means something is
                                     # badly wrong: rejoin the game, run setup, and give every need a fresh chance
NEED_STUCK_CHECKS = 5            # an enabled need still on screen after this many attempts in a row is
                                  # disabled for the rest of the run - see record_detection()
NEED_GONE_MAX_WAIT = 60.0        # most wait_until_need_gone() ever waits for an icon to disappear -
                                  # also reused as walk_alternating()'s total duration cap for walk/ride
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
KEY_HELICOPTER = "r"           # held to fly the equipped helicopter (also held briefly by ride)
KEY_JUMP = "space"
MOVE_KEYS = ("w", "a", "s", "d")
RESPAWN_KEYS = ("esc", "r", "enter")

# Lure/tree collection (run automatically by side_quest(), not tied to a detected need)
LURE_COLLECT_WALK_DURATION = 2.0  # hold 'a' this long before pressing KEY_INTERACT at the lure
LURE_COLLECT_SETTLE_DELAY = 1.0   # wait this long between the two KEY_INTERACT presses at the lure
LURE_NEW_POS_1 = (876, 711)       # the two backpack clicks that place a fresh lure - see set_new_lure()
LURE_NEW_POS_2 = (978, 814)
TREE_COLLECT_WALK_DURATION = 2.0  # hold 'd' this long before stepping back
TREE_COLLECT_BACKWARD_DURATION = 1.0  # then hold 's' this long before pressing KEY_INTERACT at the money tree
TREE_HARVEST_YIELD = 16           # money_collected added to the persisted total per tree_collect() call

# side_quest(): how often to tend the money tree/lure, and the money_collected
# target that pauses tree harvesting (see load_game_config()/save_game_config()).
# money_collected only ever goes up here - resetting it means deleting
# GAME_CONFIG_PATH by hand.
MONEY_COLLECTED_TARGET = 200
TREE_CHECK_INTERVAL = 10 * 60          # seconds (10 minutes) between tree harvests
LURE_RECOLLECT_INTERVAL = 4 * 60 * 60  # seconds (4 hours)

# setup_game(): run on demand (the GUI's Setup button) and by the automatic recovery
# in rejoin_game(), never by the normal cycle.
SETUP_LOCK_HOUSE_POS = (1112, 65)
SETUP_BACKPACK_SETTINGS_POS = (977, 646)
SETUP_SORT_MENU_POS = (1031, 680)
SETUP_FAVORITES_POS = (1023, 810)
SETUP_CONFIRM_POS = (1047, 908)
# Disabling trades: settings (gear) -> settings menu -> interaction tab -> trading
# setting -> "no one" -> close. The macro window is sent to the back for this, since
# it covers the gear.
SETUP_TRADES_SETTINGS_POS = (1894, 757)
SETUP_TRADES_MENU_POS = (1027, 364)
SETUP_TRADES_INTERACTION_TAB_POS = (1022, 436)
SETUP_TRADES_SETTING_POS = (1029, 549)
SETUP_TRADES_NO_ONE_POS = (1028, 604)
SETUP_TRADES_CLOSE_POS = (1121, 358)

# leave_and_rejoin(), modelled on how Natro Macro (a Bee Swarm Simulator macro) reconnects: close
# Roblox properly (esc, l, enter, then kill any leftover process), start the game again through a
# roblox:// deeplink - to the private server whose link is saved in the GUI, else a public server -
# then wait in stages, checking the screen, instead of waiting a fixed time and hoping.
ROBLOX_PLACE_ID = 920587237
ROBLOX_PROCESS_NAMES = ("RobloxPlayerBeta.exe", "RobloxCrashHandler.exe")  # killed after leaving
REJOIN_LEAVE_KEYS = ("esc", "l", "enter")
REJOIN_LEAVE_KEY_GAP = 0.25        # between those keys (Natro uses the same)
REJOIN_MIN_LEAVE_HEIGHT = 500      # px; the L shortcut only works in a Roblox window at least this tall
REJOIN_CLOSE_WAIT = 5.0            # after closing, before relaunching (relaunching too soon gives Roblox error 264)
REJOIN_INTERVAL = 60 * 60          # while the loop runs, rejoin this often (seconds) even if nothing is wrong, to
                                   # refresh a client that has been up a long time; also gives stuck needs a fresh chance
NO_PROGRESS_REJOIN_INTERVAL = 20 * 60  # while the loop runs, rejoin if no need has been resolved for this long (seconds)
REJOIN_MAX_ATTEMPTS = 3            # close -> launch -> wait cycles before giving up
REJOIN_WINDOW_TIMEOUT = 240.0      # most it waits for the Roblox window to appear after launching (Natro: 4 minutes,
                                   # as Roblox may be installing an update first)
REJOIN_LOAD_TIMEOUT = 180.0        # most it waits for the game's Play popup after that (Natro: 3 minutes)
REJOIN_POLL_INTERVAL = 1.0         # how often it looks at the screen while waiting
# The game has loaded once its green Play button is on screen around REJOIN_JOIN_POS.
REJOIN_JOIN_POS = (910, 817)       # the Play button - clicked once it has loaded
REJOIN_PLAY_COLOR = (74, 198, 85)  # same green as PAYCHECK_CASHOUT_COLOR
REJOIN_PLAY_COLOR_TOLERANCE = 3    # per channel, in case of compression/scaling
REJOIN_PLAY_BOX = (120, 25)        # half width, half height of the area around REJOIN_JOIN_POS checked for it
REJOIN_PLAY_MIN_PIXELS = 2000      # the button is ~15000 px of that color; fewer is something else
REJOIN_PLAY_SETTLE = 1.0           # wait after the button appears, before clicking it
REJOIN_AFTER_JOIN_WAIT = 15.0      # wait after clicking it, then the character respawns

# The Roblox "Disconnected" dialog (a dark grey panel in the middle of the screen): it counts as
# showing when most of DISCONNECT_PANEL_BOX is that one color - the game behind it is blurred,
# so nothing else on screen fills a box like that. See detect_disconnect().
DISCONNECT_PANEL_COLOR = (57, 59, 60)
DISCONNECT_PANEL_TOLERANCE = 3
DISCONNECT_PANEL_BOX = (760, 427, 1160, 677)   # left, top, right, bottom
DISCONNECT_PANEL_MIN_FRACTION = 0.5
ROBLOX_CRASH_WINDOW_TITLE = "Roblox Crash"

# Optional: resume after Roblox loses focus instead of stopping (the "Resume after focus loss" checkbox).
# Gives up after this many focus losses in a row without a need being resolved in between, so it can't
# fight you for the window forever.
FOCUS_RESUME_MAX_IN_A_ROW = 5
FOCUS_RESUME_DELAY = 3.0   # wait before taking the window back, so whatever took it is out of the way

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
# The icons can't be recognised against a bright background (the Pizza Party's, say), so when
# more than this fraction of the icon strip is brighter than this gray level it counts as unreadable
# (need_bar_readable()) and "the icon is gone" can't be concluded from it.
NEED_BAR_BRIGHT_LEVEL = 200
NEED_BAR_MAX_BRIGHT_FRACTION = 0.5
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
GUI_STOP_COLOR = "#d62828"
GUI_LOOP_COLOR = "#2980b9"
GUI_RESPAWN_COLOR = "#8e44ad"
GUI_TEST_COLOR = "#e74c3c"
GUI_CONSOLE_BG = "#0a0a0a"
GUI_CONSOLE_FG = "#00ff00"
GUI_SQUARE_BUTTON_SIZE = 44         # px, the stop button is square (and the loop button as tall)
GUI_STATUS_HEIGHT = 25              # px

# GUI layout spacing
GUI_OUTER_PADDING = 8       # outer padx/pady around the notebook, debug console, and status bar
GUI_ROW_SPACING = 4         # vertical gap between stacked rows, and the horizontal gap between the start/loop/stop buttons
GUI_WIDGET_SPACING = 2      # small gap between tightly packed widgets (debug-tab [TEST] buttons, label pady)
GUI_LABEL_PADDING = 3       # bottom pady under the "Output" label, and around the status text
GUI_DIVIDER_HEIGHT = 2      # px, thickness of the horizontal divider line

# GUI font sizes (family is GUI_FONT; weight is given at each call site)
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
    "NEEDS_DIR", "DEBUG_DIR", "RUN_LOG_PATH", "RUN_COUNTER_PATH", "GAME_CONFIG_PATH",
    "OUTPUT_LOG_PATH", "FAILURE_DIR", "STATUS_PATH", "OUTPUT_LOG_MAX_BYTES", "MAX_FAILURE_SCREENSHOTS",
    "DISABLED_NEEDS_REJOIN_THRESHOLD",
    "REFERENCE_WIDTH", "REFERENCE_HEIGHT", "REFERENCE_CENTER_X", "REFERENCE_CENTER_Y", "ROBLOX_RECT_TTL",
    "FOCUS_WINDOW_ON_ACTION", "SAVE_NEW_NEEDS", "MATCH_ONLY_LEFT_HALF",
    "RESPAWN_KEY_DURATION", "RESPAWN_WAIT", "WALK_TO_BUTTONS_DURATION", "WALK_ALTERNATING_STEP",
    "KEY_STEP_GAP", "UI_SETTLE", "FOCUS_DELAY", "FOCUS_CLICK_SETTLE_DELAY",
    "CLICK_MOVE_DURATION", "CLICK_SETTLE_DELAY", "POST_CLICK_DELAY",
    "NEED_CHECK_RETRY_DELAY", "LOOP_DELAY",
    "STOP_CHECK_INTERVAL", "STOP_HOTKEY", "STOP_HOTKEY_POLL_INTERVAL", "CATCH_WAIT_AFTER_EQUIP", "CATCH_EMOTE_DELAY", "CATCH_THROW_COUNT",
    "CATCH_ZOOM_DURATION", "PET_CIRCLE_DURATION", "PET_CIRCLE_RADIUS", "PET_SETTLE_DELAY",
    "PET_CIRCLE_STEP_MOVE_DURATION", "PET_FOCUS_CLICK_DURATION", "ICON_EXTRACT_PADDING",
    "CATCH_TOYS_POS", "CATCH_SQUEAKY_TOY_POS", "CATCH_EQUIP_POS", "CATCH_UNEQUIP_POS",
    "EMPTY_POS", "FOCUS_PET_REGION_TOP_PERCENT", "FOCUS_PET_FRAME_GAP", "FOCUS_PET_DIFF_THRESHOLD",
    "FOCUS_PET_MERGE_KERNEL", "FOCUS_PET_MIN_AREA", "FOCUS_PET_MENU_WAIT",
    "CHOOSE_BUTTON_COLOR", "CHOOSE_SLOW_MOVE_DURATION",
    "BACKPACK_HEADER_COLOR", "BACKPACK_HEADER_TOLERANCE", "BACKPACK_HEADER_BOX",
    "BACKPACK_HEADER_MIN_FRACTION", "PAYCHECK_CASHOUT_COLOR", "PAYCHECK_REGION", "PAYCHECK_MIN_PIXELS", "PAYCHECK_DISMISS_POS_1", "PAYCHECK_DISMISS_POS_2",
    "RIDE_BACKWARD_DURATION", "RIDE_WAIT_AFTER_E", "RIDE_FORWARD_DURATION", "RIDE_VEHICLES_POS",
    "RIDE_FIRST_VEHICLE_POS", "RIDE_EQUIP_POS", "RIDE_R_HOLD_DURATION",
    "TELEPORT_WAIT", "TELEPORT_SETTLE_WAIT", "GENERAL_TELEPORT_POS_2", "GENERAL_TELEPORT_POS_3",
    "TELEPORT_BACK_DURATION", "TELEPORT_WALK_STEP_GAP", "SICK_CONFIRM_WAIT",
    "TELEPORT_PETS_TAB_POS", "TELEPORT_VEHICLES_TAB_POS", "SICK_FINAL_CLICK_POS",
    "TELEPORT_WALK_NEEDS", "HELICOPTER_REQUIRED", "HELICOPTER_FORWARD_DURATION", "HELICOPTER_HOLD_DURATION",
    "NEED_STUCK_CHECKS", "HALLOWEEN", "MINIGAME_POPUP_PLAY",
    "MINIGAME_POPUP_YES_COLOR", "MINIGAME_POPUP_NO_COLOR", "MINIGAME_POPUP_MIN_BUTTON_PIXELS",
    "MINIGAME_POPUP_NO_MAX_DX", "MINIGAME_POPUP_NO_MAX_DY", "MINIGAME_POPUP_DONT_SHOW_POS", "MINIGAME_POPUP_NO_POS",
    "ROBLOX_PLACE_ID", "ROBLOX_PROCESS_NAMES", "REJOIN_LEAVE_KEYS", "REJOIN_LEAVE_KEY_GAP",
    "REJOIN_MIN_LEAVE_HEIGHT", "REJOIN_CLOSE_WAIT", "REJOIN_INTERVAL", "NO_PROGRESS_REJOIN_INTERVAL",
    "FOCUS_RESUME_MAX_IN_A_ROW", "FOCUS_RESUME_DELAY", "REJOIN_MAX_ATTEMPTS", "REJOIN_WINDOW_TIMEOUT",
    "REJOIN_LOAD_TIMEOUT", "REJOIN_POLL_INTERVAL", "REJOIN_JOIN_POS", "REJOIN_PLAY_COLOR",
    "REJOIN_PLAY_COLOR_TOLERANCE", "REJOIN_PLAY_BOX", "REJOIN_PLAY_MIN_PIXELS", "REJOIN_PLAY_SETTLE",
    "REJOIN_AFTER_JOIN_WAIT",
    "DISCONNECT_PANEL_COLOR", "DISCONNECT_PANEL_TOLERANCE", "DISCONNECT_PANEL_BOX",
    "DISCONNECT_PANEL_MIN_FRACTION", "ROBLOX_CRASH_WINDOW_TITLE",
    "NEED_GONE_MAX_WAIT", "NEED_GONE_POLL_INTERVAL", "NEED_GONE_CONFIRMATIONS",
    "NEED_GONE_CONFIRM_INTERVAL", "NEED_GONE_FLICKER_RECHECK_DELAY", "NEED_WATCH_JOIN_TIMEOUT",
    "KEY_BACKPACK", "KEY_MOUNT", "KEY_INTERACT", "KEY_ZOOM_IN", "KEY_HELICOPTER", "KEY_JUMP", "MOVE_KEYS", "RESPAWN_KEYS",
    "LURE_COLLECT_WALK_DURATION", "LURE_COLLECT_SETTLE_DELAY", "LURE_NEW_POS_1", "LURE_NEW_POS_2",
    "TREE_COLLECT_WALK_DURATION", "TREE_COLLECT_BACKWARD_DURATION", "TREE_HARVEST_YIELD",
    "MONEY_COLLECTED_TARGET", "TREE_CHECK_INTERVAL", "LURE_RECOLLECT_INTERVAL",
    "SETUP_LOCK_HOUSE_POS", "SETUP_BACKPACK_SETTINGS_POS", "SETUP_SORT_MENU_POS",
    "SETUP_FAVORITES_POS", "SETUP_CONFIRM_POS",
    "SETUP_TRADES_SETTINGS_POS", "SETUP_TRADES_MENU_POS", "SETUP_TRADES_INTERACTION_TAB_POS",
    "SETUP_TRADES_SETTING_POS", "SETUP_TRADES_NO_ONE_POS", "SETUP_TRADES_CLOSE_POS",
    "FOCUS_CLICK_X_PERCENT", "FOCUS_CLICK_Y", "HOVER_NUDGE_PIXELS",
    "NEED_ICON_TOP_PERCENT", "NEED_ICON_WIDTH_PERCENT", "NEED_ICON_BLANK_HEIGHT", "NEED_ICON_BLANK_WIDTH",
    "NEED_BAR_BRIGHT_LEVEL", "NEED_BAR_MAX_BRIGHT_FRACTION", "NEED_ICON_MIN_RADIUS", "NEED_ICON_MAX_RADIUS", "NEED_ICON_MIN_DISTANCE", "NEED_ICON_BLUR_KERNEL",
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
    "GUI_GEOMETRY", "GUI_ALPHA", "GUI_FONT", "GUI_BG", "GUI_ACCENT", "GUI_FG",
    "GUI_STOP_COLOR", "GUI_LOOP_COLOR", "GUI_RESPAWN_COLOR", "GUI_TEST_COLOR", "GUI_CONSOLE_BG",
    "GUI_CONSOLE_FG", "GUI_SQUARE_BUTTON_SIZE", "GUI_STATUS_HEIGHT",
    "GUI_OUTER_PADDING", "GUI_ROW_SPACING", "GUI_WIDGET_SPACING", "GUI_LABEL_PADDING", "GUI_DIVIDER_HEIGHT",
    "GUI_STOP_ICON_FONT_SIZE", "GUI_LOOP_ICON_FONT_SIZE",
    "GUI_SECTION_FONT_SIZE", "GUI_STATUS_FONT_SIZE", "GUI_CONSOLE_FONT_SIZE",
    "GUI_CONSOLE_HEIGHT", "GUI_CONSOLE_WIDTH", "GUI_RESPAWN_BUTTON_HEIGHT",
]
