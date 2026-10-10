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
MINIGAME_POPUP_YES_POS = (1049, 612)

# Which minigame a popup is for: the first line of its title ("Ghost Gallery", "Hauntlet 2") is matched
# against the crops in MINIGAME_TEMPLATE_DIR, searched for within MINIGAME_TITLE_BOX. A minigame is the
# lower-cased file name (Ghost_gallery.png -> "ghost_gallery"); each one has an on/off switch in the game
# config ("<name>_enabled", off by default) - an off one is dismissed, an on one is played.
MINIGAME_TITLE_BOX = (730, 410, 1200, 510)   # left, top, right, bottom
MINIGAME_TITLE_MATCH_THRESHOLD = 0.85
MINIGAME_LABELS = {"hauntlet": "Hauntlet 2", "ghost_gallery": "Ghost Gallery"}   # for the GUI and the logs

# Playing one. Both end on the victory screen (a red "GAME OVER!" banner over a green NICE! button), which
# is looked for every MINIGAME_VICTORY_CHECK_INTERVAL seconds, then the button is clicked.
MINIGAME_VICTORY_BANNER_COLOR = (255, 45, 89)
MINIGAME_VICTORY_BANNER_TOLERANCE = 4
MINIGAME_VICTORY_BANNER_BOX = (740, 255, 1190, 370)
MINIGAME_VICTORY_BANNER_MIN_PIXELS = 3000
MINIGAME_VICTORY_BUTTON_COLOR = (74, 198, 85)
MINIGAME_VICTORY_BUTTON_TOLERANCE = 3
MINIGAME_VICTORY_BUTTON_BOX = (860, 725, 1060, 800)
MINIGAME_VICTORY_BUTTON_MIN_PIXELS = 2000
MINIGAME_VICTORY_BUTTON_POS = (966, 762)
MINIGAME_VICTORY_CHECK_INTERVAL = 1.0
MINIGAME_POPUP_CHECK_INTERVAL = 4.0   # the popup checks (stray windows, paycheck) run this often while a minigame is played
MINIGAME_MAX_DURATION = 600.0       # gives up (and logs a failure) if the victory screen hasn't shown by then
MINIGAME_FINISH_WAIT = 5.0          # after clicking NICE!, before carrying on
HAUNTLET_START_WAIT = 50.0          # waits this long after teleporting, then holds HAUNTLET_FORWARD_KEY
HAUNTLET_FORWARD_KEY = "w"
GHOST_GALLERY_START_WAIT = 5.0      # after teleporting, before it starts running about
GHOST_GALLERY_HOLD = 5.0            # the mouse is held down in holds this long...
GHOST_GALLERY_HOLD_GAP = 0.2        # ...with this short a pause between them
GHOST_GALLERY_STEP_MIN = 0.3        # while it's held, a random direction (MOVE_KEYS) is run in, with a jump,
GHOST_GALLERY_STEP_MAX = 1.0        # for a random time between these

# ============================================================================
# STRAY WINDOWS
# ============================================================================
# Windows that open by accident (a misclick) or on their own (the daily Star Rewards) and block everything: each is
# recognised by a crop of its title in STRAY_WINDOW_DIR (file name = the name here), searched for within
# "box", and closed by clicking "close_pos" - see dismiss_stray_windows(). The Trading Hub also throws up a
# "Go to the Trading Hub to edit listings!" popup, found by its green Okay button and clicked first.
STRAY_WINDOW_MATCH_THRESHOLD = 0.8
STRAY_WINDOWS = {
    "trading_hub": {"box": (580, 290, 1020, 400), "close_pos": (1278, 339)},
    "star_rewards": {"box": (640, 385, 1060, 475), "close_pos": (1209, 441)},
    # "Are you sure you want to respawn your character?" - what respawn_character()'s esc, r, enter leaves
    # open when the Enter comes before the dialog does; "close_pos" is its Respawn button.
    "respawn_confirm": {"box": (600, 330, 1320, 430), "close_pos": (850, 496)},
    # The five-button character menu (Profile, Emotions, Dances, Actions, Activities): a click in the middle of
    # the screen dismisses it.
    "character_menu": {"box": (840, 450, 1080, 540), "close_pos": (960, 540)},
    # The camera-on-the-pet view with its BACK button. Only choose and pet want it ("only_when_unwanted"), so
    # it's backed out of unless one of those handlers is running - see wanting_pet_focus().
    "pet_focus": {"box": (400, 60, 700, 180), "close_pos": (545, 120), "only_when_unwanted": True},
}
STRAY_OKAY_BOX = (800, 637, 1120, 680)
STRAY_OKAY_COLOR = (74, 198, 85)
STRAY_OKAY_TOLERANCE = 3
STRAY_OKAY_MIN_PIXELS = 5000      # the button is ~11000 exact-color px in that box
STRAY_OKAY_POS = (962, 657)
SETUP_STRAY_TRIES = 3             # setup closes up to this many stray windows before it starts
STRAY_CLOSE_SETTLE = 1.0          # after a click, before looking again
# While a need handler (or setup) is running, the per-cycle checks (unscrew()) also run, this often.
UNSCREW_TASK_INTERVAL = 4.0

# ============================================================================
# PATHS
# ============================================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MINIGAME_TEMPLATE_DIR = os.path.join(SCRIPT_DIR, "ref", "halloween")
STRAY_WINDOW_DIR = os.path.join(SCRIPT_DIR, "ref", "popups")
NEEDS_DIR = os.path.join(SCRIPT_DIR, "needs")
DEBUG_DIR = os.path.join(SCRIPT_DIR, "debug")
Path(NEEDS_DIR).mkdir(exist_ok=True)
Path(DEBUG_DIR).mkdir(exist_ok=True)

RUN_LOG_PATH = os.path.join(DEBUG_DIR, "run_log.txt")      # appended to, never overwritten
RUN_COUNTER_PATH = os.path.join(DEBUG_DIR, "run_counter.txt")  # holds the last-used run number
GAME_CONFIG_PATH = os.path.join(DEBUG_DIR, ".config")      # persisted game state - see load_game_config()
OUTPUT_LOG_PATH = os.path.join(DEBUG_DIR, "output.log")    # everything the macro prints, timestamped - see log_output()
FAILURE_DIR = os.path.join(DEBUG_DIR, "failures")          # a screenshot per failure - see log_failure()
RECORDINGS_DIR = os.path.join(DEBUG_DIR, "recordings")      # run videos - see RunRecorder
STATUS_PATH = os.path.join(DEBUG_DIR, "status.json")       # live "is it alive and what's it doing" summary - see write_status()
OUTPUT_LOG_MAX_BYTES = 5 * 1024 * 1024   # output.log moves to output.log.old (replacing the last one) past this size
MAX_FAILURE_SCREENSHOTS = 40             # oldest are deleted beyond this many

# Recording a run ("Record the next run" on the Options tab): low quality on purpose, a video of a whole
# unattended run is large. Every frame has the time on it, to line it up with the logs.
RECORD_WIDTH = 640
RECORD_HEIGHT = 360
RECORD_FPS = 4
RECORD_MAX_MINUTES = 480                 # recording stops by itself after this long
MAX_RECORDINGS = 5                       # oldest videos are deleted beyond this many

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
RESPAWN_CONFIRM_LOOK_DELAY = 0.5  # after the respawn keys, before checking the respawn dialog is gone
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
CONSOLE_DRAIN_INTERVAL_MS = 50   # how often queued output is put on the GUI console (see DebugCapture)
CATCH_WAIT_AFTER_EQUIP = 1.0   # wait after equipping toy before throwing
CATCH_THROW_INTERVAL = 0.5     # the toy is thrown (a click) this often until the need clears
CATCH_MAX_WAIT = 60.0          # ...for at most this long
CATCH_ZOOM_DURATION = 2.0      # seconds the zoom-in key is held before throwing
PET_SWIPE_DURATION = 8.0      # how long the one downward swipe (mouse held down) takes
PET_SWIPE_START_OFFSET = -15           # px from screen center the swipe starts at (negative = above it)
PET_SWIPE_END_OFFSET = 45              # px from screen center the swipe ends at (positive = below it)
PET_SETTLE_DELAY = 0.1                 # pause after each mouse move/click before the next pet step
PET_SWIPE_STEP_DURATION = 0.05         # time for each small step down the swipe
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
FOCUS_PET_IGNORE_MARGIN = 20           # px around the macro's own window that focus_pet() ignores too
FOCUS_PET_DEBUG_COLOR = (0, 255, 0)    # BGR, the boxes drawn on debug/debug_focus_pet.png
FOCUS_PET_CLICK_SETTLE = 1.0         # after each click, before checking whether the pet is focused now
FOCUS_PET_MENU_WAIT = 4.0            # wait after the clicks, for the pet's menu to open

# The 'choose' need's button is matched by its exact color rather than shape,
# since it's just a plain circle with no distinguishing icon. Given as (R, G, B).
CHOOSE_BUTTON_COLOR = (181, 6, 254)
CHOOSE_SLOW_MOVE_DURATION = 1.0  # deliberate, slow mouse travel to the found button

# The paycheck popup's CASH OUT button, matched by exact color the same way
# as CHOOSE_BUTTON_COLOR above.
PAYCHECK_CASHOUT_COLOR = (74, 198, 85)
# The popup itself is recognised by a crop of its bank-card header ("BANK of Adopt Me!", ref/popups/paycheck.png)
# found within PAYCHECK_BOX (left, top, right, bottom) with a match of at least PAYCHECK_MATCH_THRESHOLD.
PAYCHECK_BOX = (740, 400, 1180, 490)
PAYCHECK_MATCH_THRESHOLD = 0.8
PAYCHECK_DISMISS_POS_1 = (946, 679)
PAYCHECK_DISMISS_POS_2 = (948, 627)

# The EXPANDED backpack is open when its purple header bar fills BACKPACK_HEADER_BOX (left, top, right,
# bottom) - see detect_backpack_expanded(). It should never be open during a task.
BACKPACK_HEADER_COLOR = (143, 74, 255)
BACKPACK_HEADER_TOLERANCE = 4
BACKPACK_HEADER_BOX = (1090, 36, 1840, 70)
BACKPACK_HEADER_MIN_FRACTION = 0.5
# The NORMAL backpack (the small panel at the bottom of the screen, which handlers open for a few seconds):
# its purple frame (BACKPACK_NORMAL_COLOR, at least BACKPACK_NORMAL_MIN_PIXELS of it within the panel box)
# and the white of its item grid (at least BACKPACK_NORMAL_MIN_WHITE_FRACTION of the white box at or above
# BACKPACK_NORMAL_WHITE_LEVEL) - see detect_backpack_normal(). Calibrated on a single screenshot.
BACKPACK_NORMAL_COLOR = (150, 92, 255)
BACKPACK_NORMAL_TOLERANCE = 12
BACKPACK_NORMAL_PANEL_BOX = (712, 640, 1210, 930)
BACKPACK_NORMAL_MIN_PIXELS = 800
BACKPACK_NORMAL_WHITE_BOX = (850, 690, 1190, 920)
BACKPACK_NORMAL_WHITE_LEVEL = 240
BACKPACK_NORMAL_MIN_WHITE_FRACTION = 0.2
# unscrew() closes either backpack at once at the start of a cycle; during a task (where a handler has the
# normal one open for a few seconds on purpose) only once it's been seen open on this many checks in a
# row, UNSCREW_TASK_INTERVAL apart.
BACKPACK_CLOSE_CONFIRMATIONS = 2
# The EXPANDED backpack has its own BACK button (ref/popups/backpack_back.png, searched for within
# BACKPACK_BACK_BOX): clicking it closes the backpack entirely, where the key only shrinks it to the normal form.
BACKPACK_BACK_BOX = (700, 80, 1000, 200)
BACKPACK_BACK_MATCH_THRESHOLD = 0.8

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

# A backpack category tab that is already selected has an orange background; clicking it again expands the
# backpack, so handlers only click a tab that isn't orange yet. The tile is TAB_HALF_SIZE (x, y) around its
# click position, and counts as selected with at least TAB_SELECTED_MIN_PIXELS of TAB_SELECTED_COLOR in it.
TAB_SELECTED_COLOR = (250, 149, 30)
TAB_SELECTED_TOLERANCE = 12
TAB_HALF_SIZE = (26, 18)
TAB_SELECTED_MIN_PIXELS = 300
