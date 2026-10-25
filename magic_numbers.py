"""Every tunable constant the macro uses: screen positions, timings,
detection thresholds, and GUI layout. main.py does `import magic_numbers as cfg` and refers to these as cfg.NAME - this file holds no logic,
only values, so that's the one thing to edit when something needs
retuning (a moved button, a slow computer, a different monitor)."""

import os
from typing import Any
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
# The green of Adopt Me's buttons (a popup's Yes, NICE!, Okay, Play, the paycheck's Cash Out): every green button is found by this
# colour, within GREEN_BUTTON_TOLERANCE per channel (the popup's Yes by the exact colour) in case of compression/scaling.
GREEN_BUTTON_COLOR = (74, 198, 85)
GREEN_BUTTON_TOLERANCE = 3
MINIGAME_POPUP_NO_COLOR = (216, 42, 63)
MINIGAME_POPUP_MIN_BUTTON_PIXELS = 1000     # a button is ~5000 exact-color px; fewer is noise
MINIGAME_POPUP_NO_MAX_DX = 300              # No button must be within this many px left of Yes...
MINIGAME_POPUP_NO_MAX_DY = 40               # ...and this many px above/below it
MINIGAME_POPUP_DONT_SHOW_POS = (826, 666)
MINIGAME_POPUP_NO_POS = (879, 618)
MINIGAME_POPUP_YES_POS = (1049, 612)

# Which minigame a popup is for: the first line of its title ("Ghost Gallery", "Hauntlet 2") is matched
# against the crops in MINIGAME_TEMPLATE_DIR, searched for within MINIGAME_TITLE_BOX. A minigame is the
# lower-cased file name (ghost_gallery.png, hauntlet_2.png -> "ghost_gallery", "hauntlet_2"); each one has an on/off switch in the game
# config ("<name>_enabled", off by default) - an off one is dismissed, an on one is played.
MINIGAME_TITLE_BOX = (730, 410, 1200, 510)   # left, top, right, bottom
MINIGAME_TITLE_MATCH_THRESHOLD = 0.85
MINIGAME_LABELS = {"hauntlet_2": "Hauntlet 2", "ghost_gallery": "Ghost Gallery", "stitches_spotlight": "Stitches' Spotlight"}   # for the GUI and the logs

# Playing one. Each ends on the game over screen (a red "GAME OVER!" banner over a green NICE! button), which
# is looked for on every look at the screen, then the button is clicked.
MINIGAME_FINISH_BANNER_COLOR = (255, 45, 89)
MINIGAME_FINISH_BANNER_TOLERANCE = 4
MINIGAME_FINISH_BANNER_BOX = (740, 255, 1190, 370)
MINIGAME_FINISH_BANNER_MIN_PIXELS = 3000
MINIGAME_FINISH_BUTTON_BOX = (860, 725, 1060, 800)
MINIGAME_FINISH_BUTTON_MIN_PIXELS = 2000
MINIGAME_FINISH_BUTTON_POS = (966, 762)
# While one runs it has its own overlay along the top of the screen (Hauntlet 2: the ROOM counter beside the
# timer and monitor; Ghost Gallery: the TIME LEFT / GHOST GALLERY / Score banner; Stitches' Spotlight: TIME LEFT / STITCHES' SPOTLIGHT / Candy Corn). Crops of each overlay (ref/overlays/<minigame>.png, found within its box here, at least
# MINIGAME_OVERLAY_THRESHOLD) say whether the game is on: one that was seen and then isn't, for
# MINIGAME_OVERLAY_LOST_TIMEOUT seconds, was left (game over, kicked out) - and one never seen within
# MINIGAME_OVERLAY_START_GRACE seconds after its start wait never started.
MINIGAME_OVERLAYS = {"hauntlet_2": (1040, 40, 1210, 135), "ghost_gallery": (840, 25, 1080, 120), "stitches_spotlight": (860, 25, 1065, 125)}   # name: search box
MINIGAME_OVERLAY_THRESHOLD = 0.7
MINIGAME_OVERLAY_LOST_TIMEOUT = 10.0
MINIGAME_OVERLAY_START_GRACE = 60.0
MINIGAME_POPUP_CHECK_INTERVAL = 4.0   # the popup checks (stray windows, paycheck) run this often while a minigame is played
MINIGAME_POPUP_END_WAIT = 30.0        # a popup of a minigame to be played waits this long, at most, while a need is in its end wait (the need can clear first)
# The candy corn total in the HUD (top right, red digits on a white flag, shown in the lobby and in the world - not at home - and in the
# game over screen) is read by read_candy_corn(): the red pixels (CANDY_CORN_RED, BGR) of CANDY_CORN_BOX that make up pieces CANDY_CORN_DIGIT_HEIGHT
# tall (the commas are about half that) are the digits, each shrunk to CANDY_CORN_DIGIT_SIZE and compared with the saved ones; the best must differ
# by less than CANDY_CORN_MAX_DIFF (0-255; the closest two saved digits differ by 50, and a digit not seen before by 11 at most in a leave-one-out test).
# A piece that is as tall as a digit but wider, or digits further apart than CANDY_CORN_PITCH, mean something is in the way: not read.
CANDY_CORN_BOX = (1495, 58, 1635, 88)
CANDY_CORN_RED = ((0, 0, 180), (110, 110, 255))
CANDY_CORN_DIGIT_HEIGHT = (15, 25)
CANDY_CORN_DIGIT_MAX_WIDTH = 16
CANDY_CORN_PITCH = (12, 26)                  # px between the left edges of two digits next to each other (15, 23-24 across a comma): more means one is missing
CANDY_CORN_DIGIT_SIZE = (12, 20)
CANDY_CORN_MAX_DIFF = 25
CANDY_CORN_MAX_DIGITS = 9
CANDY_CORN_BEFORE_TRIES = 12                 # looks (CANDY_CORN_SETTLE apart) at most for the total before a run: the lobby shows after the white screen of the teleport
CANDY_CORN_SETTLE = 0.5                      # seconds between the looks of read_candy_corn_settled(): two in a row must agree
MINIGAME_RESULT_BOX = (450, 130, 1500, 880)  # the GAME OVER! screen: the card with the candy corn earned (seen: x3606 for a Ghost Gallery score of 61600, x7560 for Hauntlet 2's room 18) and the score or the room
MAX_RESULT_SHOTS = 200                      # the newest this many result screens are kept in RUNS_DIR/results
MINIGAME_YES_TRIES = 3              # the popup's Yes is clicked this often at most, if it did not teleport the character...
MINIGAME_TELEPORT_WAIT = 6.0        # ...which is looked for this long after each click: a white screen, or the whole view changing
MINIGAME_TELEPORT_POLL = 0.1
SCREEN_CHANGE_SIZE = (80, 45)         # screen_changed() compares the two screens shrunk to this many pixels (grey)
MINIGAME_TELEPORT_CHANGE = 25       # mean grey-level difference (0-255, over the whole screen, shrunk) from before the click that counts as a changed view
MINIGAME_MAX_DURATION = 600.0       # gives up (and logs a failure) if the minigame hasn't finished by then
MINIGAME_ZOOM_OUT_DURATION = 3.0    # Hauntlet 2 and Ghost Gallery hold KEYS["zoom_out"] this long when they begin (Hauntlet 2 again where it can't find the doors, if HAUNTLET_SEARCH_TURNS allows)
MINIGAME_FINISH_WAIT = 5.0          # after clicking NICE!, before carrying on
# Leaving a minigame (exit_minigame(), the start of the bot's /sloop): the red Exit Minigame button at the bottom left (POPUP_TEMPLATES["exit_minigame"]; the house has an
# Exit Home one in the same place), then "Do you want to exit this game?" (POPUP_TEMPLATES["exit_game"]) with a red Cancel and, at its right, a grey button counting down
# that turns green as Exit Game. Positions and crops from three screenshots of a minigame at 1920x1080 (tests/exit_hub/); the state is told by the colours of the two
# buttons (HSV ranges, OpenCV's: hue 0-179), which are wide.
EXIT_MINIGAME_POS = (100, 1040)
EXIT_DIALOG_CANCEL_BOX = (782, 635, 956, 685)                   # the red button
EXIT_DIALOG_RIGHT_BOX = (962, 635, 1136, 685)                   # grey while it counts down, then green
EXIT_GAME_POS = (1048, 660)
EXIT_STAY_POS = (869, 660)                                      # the red button at the left: Cancel, which stays (clicked on a dialog that is up outside a minigame)
EXIT_DIALOG_MIN_FRACTION = 0.4                                  # of a box that is its button's colour (the button's text takes some of it)
EXIT_RED_HSV = ((0, 150, 150), (8, 255, 255))                   # (low, high); red is also at the other end of the hue circle
EXIT_RED_HSV_HIGH = ((172, 150, 150), (179, 255, 255))
EXIT_GREEN_HSV = ((45, 110, 130), (80, 255, 255))
EXIT_GREY_HSV = ((0, 0, 40), (179, 60, 120))
EXIT_DIALOG_WAIT = 4.0                                          # seconds the dialog is waited for after the button is clicked
EXIT_COUNTDOWN_WAIT = 8.0                                       # and then the right button's countdown, until it is green
HUB_LEAVE_WAIT = 20.0                                           # seconds the Trading Hub is waited to be gone after leaving it (the game loads a regular server)
EXIT_LEAVE_WAIT = 12.0                                          # and then the minigame's overlay to be gone
EXIT_POLL = 0.25                                                # seconds between looks
MINIGAME_FINISH_CLICK_TRIES = 3      # NICE! clicks before giving up on the finish screen closing
MINIGAME_START_LOOK_INTERVAL = 0.5     # how often each minigame looks for its overlay while waiting for the game to begin
MINIGAME_START_WAIT = 90.0          # each minigame waits for its overlay (the game has begun: the lobby's countdown to a round that starts at a fixed time) and act
                                    # when it shows - this is the most they wait for it, a limit that must outlast the longest queue (the overlay came 50.5 s after the Yes in one run;
                                    # the Yes comes 12-52 s before the round, and a need's end wait holds it back by up to MINIGAME_POPUP_END_WAIT)

# ============================================================================
# STRAY WINDOWS
# ============================================================================
# Windows that open by accident (a misclick) or on their own (the daily Star Rewards) and block everything: each is
# recognised by a crop of its title in STRAY_WINDOW_DIR (file name = the name here), searched for within
# "box", and closed by clicking "close_pos" - see dismiss_stray_windows(). The Trading Hub also throws up a
# "Go to the Trading Hub to edit listings!" popup, found by its green Okay button and clicked first.
STRAY_WINDOW_MATCH_THRESHOLD = 0.8
STRAY_WINDOWS: dict[str, dict[str, Any]] = {
    "trading_hub": {"box": (580, 290, 1020, 400), "close_pos": (1278, 339)},
    "star_rewards": {"box": (640, 385, 1060, 475), "close_pos": (1209, 441)},      # the calendar view (the star icon opens it): closed by its red X
    # The Task Board (opened by a notification such as "Trading Hub: Your listing got an offer!", on its Alerts tab): its red X closes it. Only closed, for now.
    "task_board": {"box": (540, 300, 1000, 430), "close_pos": (1318, 369)},
    "star_rewards_claim": {"box": (680, 410, 1080, 520), "close_pos": (959, 675)},   # the view that opens on its own once a day: no X, only the green CLAIM! button
    # "Are you sure you want to respawn your character?" - what respawn_character()'s esc, r, enter leaves
    # open when the Enter comes before the dialog does; "close_pos" is its Respawn button.
    "respawn_confirm": {"box": (600, 330, 1320, 430), "close_pos": (850, 496)},
    # The five-button character menu (Profile, Emotions, Dances, Actions, Activities): a click in the middle of
    # the screen dismisses it.
    "character_menu": {"box": (840, 450, 1080, 540), "close_pos": (960, 540)},
    # The camera-on-the-pet view with its BACK button. Only choose and pet want it ("only_when_unwanted"), so
    # it's backed out of unless one of those handlers is running - see wanting_pet_focus().
    "pet_focus": {"box": (400, 60, 700, 180), "close_pos": (545, 120), "only_when_unwanted": True},
    # "You can't spawn a vehicle here." - turns up now and then when a need's vehicle gets equipped in a spot that
    # doesn't allow it. "close_pos" is its Okay button; "abort_task" abandons the need that was running (it's
    # tried again from scratch on the next check) - see unscrew().
    "cant_spawn_vehicle": {"box": (760, 420, 1160, 630), "close_pos": (962, 657), "abort_task": True},
    # The player's own profile page (Stickers / Preview / paint / save, with its red X): a click on a player or on the character
    # menu's Profile opens it. The crop is the toolbar; "close_pos" is the X.
    "profile": {"box": (480, 170, 1560, 360), "close_pos": (1462, 262)},
}

# Other things recognised by a crop in STRAY_WINDOW_DIR (file name = the name here) found within "box"
# (left, top, right, bottom) with a TM_CCOEFF_NORMED score of at least "threshold" - see popup_visible(). The
# stray windows above work the same way (their "threshold" is STRAY_WINDOW_MATCH_THRESHOLD) and "pet_focus"
# is also looked up there.
POPUP_TEMPLATES: dict[str, dict[str, Any]] = {
    # The paycheck popup's bank-card header ("BANK of Adopt Me!").
    "paycheck":        {"box": (740, 400, 1180, 490), "threshold": 0.8},
    # The minigame popup's "Teleport there now?" line (the same in every minigame's popup; the popup is always in the same place).
    "minigame_popup":  {"box": (780, 515, 1160, 585), "threshold": 0.8},
    # The sick need's Yes/No dialog.
    "sick_dialog":     {"box": (700, 400, 1220, 700), "threshold": 0.8},
    "doctor_prompt":   {"box": (880, 420, 1050, 580), "threshold": 0.8},
    # The normal backpack's "BACKPACK" title, and the expanded one's BACK button.
    "backpack_normal": {"box": (700, 600, 1000, 690), "threshold": 0.8},
    "backpack_back":   {"box": (700, 80, 1000, 200), "threshold": 0.8},
    # The exit dialog's "Do you want to exit this game?" title (the same with its right button grey or green; the teleport's Yes / No dialogs
    # have the same buttons and another title), the red "Exit Minigame" button at the bottom left of every minigame, and the "GO HOME" text under
    # the button at the top of the Trading Hub island (the crop is the text alone: what is around the button is the island).
    "exit_game":       {"box": (760, 445, 1160, 605), "threshold": 0.8},
    "exit_minigame":   {"box": (0, 1000, 220, 1078), "threshold": 0.8},
    "go_home":         {"box": (1280, 60, 1460, 140), "threshold": 0.8},
}
# The sick need's own "Ask Doctor Heart to heal your family?" Yes/No dialog (POPUP_TEMPLATES["sick_dialog"]) has the
# same Yes green + No red as a minigame popup; it is the sick handler's to answer (SICK_FINAL_CLICK_POS is its Yes),
# so detect_minigame_popup() ignores it.
# "Only Pets, Babies, and Dolls can use this." - the game's answer to a need button clicked while the pet is too far from it (or
# otherwise not taken). The text is found by the white of its letters (>= ONLY_PETS_WHITE in every channel) matched against the same of
# the crop ref/popups/only_pets.png, whatever is behind it: a score of at least ONLY_PETS_THRESHOLD within ONLY_PETS_BOX. A need
# button is clicked again after BUTTON_RETRY_WAIT seconds, up to BUTTON_CLICK_TRIES clicks in all (the pet needs time to come: three clicks 3 s apart all failed);
# after each, the message is looked for for BUTTON_REJECT_LOOK seconds. A click that is refused again gives the need up; one that takes after a retry gets BUTTON_RETRY_CLEAR_WAIT seconds for the icon to clear.
ONLY_PETS_BOX = (600, 830, 1320, 930)
ONLY_PETS_WHITE = 235
ONLY_PETS_THRESHOLD = 0.6
BUTTON_CLICK_TRIES = 2
BUTTON_RETRY_WAIT = 10.0
BUTTON_RETRY_CLEAR_WAIT = 15.0
BUTTON_REJECT_LOOK = 1.5
BUTTON_SCAN_RETRY_WAIT = 5.0              # seconds before a button scan that did not find all five is repeated
STRAY_OKAY_BOX = (800, 637, 1120, 680)
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
MINIGAME_OVERLAY_DIR = os.path.join(SCRIPT_DIR, "ref", "overlays")
NEEDS_DIR = os.path.join(SCRIPT_DIR, "ref", "needs")      # the learned need icons - the one folder in ref/ the macro also writes to (see prompt_rename_need())
HAUNTLET_DIGIT_DIR = os.path.join(SCRIPT_DIR, "ref", "hauntlet_2", "digits")   # 0.png ... 9.png: the digits of the ROOM panel, see read_hauntlet_room()
HAUNTLET_ITEM_DIR = os.path.join(SCRIPT_DIR, "ref", "hauntlet_2", "items")      # a folder per item (phone/, key/, ...) of pictures of its slot; see read_hauntlet_items()
HAUNTLET_COUNT_DIR = os.path.join(SCRIPT_DIR, "ref", "hauntlet_2", "counts")    # x/, 1/, 2/: the glyphs of an item's "x2" badge
CANDY_CORN_DIGIT_DIR = os.path.join(SCRIPT_DIR, "ref", "candy_corn_digits")      # 0.png ... 9.png: the digits of the candy corn total in the HUD, see read_candy_corn()
DEBUG_DIR = os.path.join(SCRIPT_DIR, "debug")
# debug/ holds failures/ (a folder per failure), logs/ for every text and json file, runs/ for
# what belongs to one run (its log lines, run<N>.txt, and its chart, run<N>.png) and recordings/ for the run videos - plus
# the game config, which stays in debug/ itself so "Refresh debug" (which empties everything else) leaves it alone.
LOGS_DIR = os.path.join(DEBUG_DIR, "logs")
SCREENSHOTS_DIR = os.path.join(DEBUG_DIR, "screenshots")      # the pictures of HOTKEYS["screenshot"], screenshot_<date>_<time>.png
MACRO_STDERR_PATH = os.path.join(LOGS_DIR, "macro_stderr.log")    # the error output of a macro the Discord bot started (the bot opens it for the process, which keeps it open)
RUNS_DIR = os.path.join(DEBUG_DIR, "runs")                    # run<N>.txt (this run's lines of the log on their own, see log_run_event()) and run<N>.png (its chart, see the RUN REPORT section of main.py)
for _directory in (NEEDS_DIR, DEBUG_DIR, LOGS_DIR, RUNS_DIR):
    Path(_directory).mkdir(exist_ok=True)

COMMANDS_DIR = os.path.join(DEBUG_DIR, "commands")          # the commands for the macro, one <time>-<id>.json file each, written by the Discord bot (see read_command_with_id()); the macro removes each once read and leaves a <time>-<id>.ack saying it was taken or why not
COMMANDS = ("start", "stop", "exit", "set", "rejoin", "setup", "refresh_debug", "trade_hub", "leave_trade_hub", "inventory", "sloop", "focus", "blackout")     # what it may hold: start the loop, stop it, stop and close the window, change a switch (see NEED_NAMES, OPTION_SWITCHES), leave and rejoin the game, run Setup, empty the debug folder, travel to / leave the Trading Hub, take pictures of the backpack's pets tab, or run Setup and then the loop (`sloop`), bring the Roblox window to the front (`focus`), or blank / show the screen (`blackout`)
NEED_NAMES = ("catch", "pet", "choose", "ride", "walk", "hungry", "thirsty", "dirty", "potty", "sleepy", "bored", "beach", "school", "cafe", "salon", "pizza", "camping", "sick")   # the Debug tab's need switches (the keys of main.py's NEED_HANDLER_CLASSES)
OPTION_SWITCHES = {"side_quest": "side_quest_enabled", "resume_on_focus_loss": "resume_on_focus_loss", "record_next_run": "record_next_run",
                   **{f"play_{name}": f"{name}_enabled" for name in MINIGAME_LABELS}}   # the other switches a command may change: name -> the game config's key ("show_debug_lines" too, which is only in the window)
SHOW_DEBUG_LINES_OPTION = "show_debug_lines"
COMMAND_POLL_MS = 1000                                      # how often the macro looks for one
COMMAND_MAX_AGE = 30                                        # seconds: an older command is ignored (left over from before the macro was started)
RESUME_LOOP_AFTER = ("setup",)                              # the outside actions after which a loop that was running (and was stopped for them) is started again once they end
REJOIN_STOP_POLL_MS = 500                                   # a rejoin asked for while the loop runs: how often to see whether it has stopped
REJOIN_STOP_WAIT_MS = 15000                                 # and how long to wait for that before giving up
START_LOOP_DELAY_MS = 1500                                  # after the window is up, before the --loop argument starts the loop
BOT_COMMAND_ACK_WAIT = 5.0                                  # seconds the bot waits for the macro to take a command (it looks every COMMAND_POLL_MS) before it withdraws it and says so
BOT_COMMAND_ACK_POLL = 0.25                                 # seconds between the bot's looks for the macro's answer
BOT_ACK_KEEP = 300                                          # seconds an answer of the macro that nobody read stays in the commands folder
BOT_ACTION_WAIT = 3600.0                                    # seconds the bot waits for an action it asked for (Setup, a rejoin, a trip) to report that it ended, before it says it has not
BOT_BLANK_WAIT = 15.0                                       # seconds the bot waits for the macro to say whether the screen was blanked (the test flash takes a moment)
BOT_ACTION_POLL = 2.0                                       # seconds between its looks for that
BOT_INVENTORY_WAIT = 300.0                                  # seconds the bot waits, after /inventory, for the macro to end its tour (INVENTORY_DONE_PATH)
BOT_INVENTORY_POLL = 1.0                                    # seconds between its looks for that
BOT_FILES_PER_MESSAGE = 10                                  # pictures in one message (Discord takes ten attachments)
BOT_START_CONFIRM = 60                                     # seconds the bot waits, after /start, for the macro's loop to show in status.json
BOT_MAX_PULL_MB = 10240                                     # /pull copies nothing that comes to more than this (10 GB)
BOT_PULL_CONFIRM_MB = 1024                                  # a /pull of more than this says its size and waits for a yes (1 GB)
BOT_PULL_CONFIRM_TIMEOUT = 120.0                            # seconds that yes is waited for
BOT_PROCESS_LIST_TRIES = 3                                  # times the bot lists the PC's processes (a second apart) before it says it cannot tell whether the macro runs
BOT_IMPORT_TIMEOUT = 60                                     # seconds the bot's check that its updated code imports may take, before it restarts itself
BOT_UPDATE_CONFIRM_TIMEOUT = 300.0                          # seconds an /update waits for a yes
BOT_GIT_TIMEOUT = 120                                       # seconds a git command of /update may take (a fetch over a slow connection)
BOT_PIP_TIMEOUT = 900                                       # seconds pip may take to install the packages of an update
BOT_CHANNEL_TRIES = 5                                       # times the bot tries to fetch its channel at start (a network hiccup) before the watching is given up
BOT_CHANNEL_RETRY_WAIT = 2.0                                # seconds before the second try; doubled for each one after
BOT_WATCH_INTERVAL = 10                                     # seconds between the Discord bot's looks at what the macro wrote (failures, run charts, captures, status)
BOT_ALERT_GAP = 60                                          # seconds: at most one failure post per this long, the rest counted ("N more failures")
BOT_FILE_SETTLE = 5                                         # seconds a run chart must be unchanged before it is posted
BOT_RVIEW_SECONDS = 30                                      # /rview: how long the rolling view runs
BOT_RVIEW_INTERVAL = 1.0                                    # seconds between its pictures
BOT_RVIEW_WIDTH = 1280                                      # pixels wide each picture is made (a full-size one is slow to upload every second)
BOT_RVIEW_JPEG_QUALITY = 80
BOT_MAX_UPLOAD_MB = 8                                       # bigger files are not posted (Discord's limit for a bot is about 10 MB)
RUN_LOG_PATH = os.path.join(LOGS_DIR, "run_log.txt")        # appended to, never overwritten
RUN_COUNTER_PATH = os.path.join(LOGS_DIR, "run_counter.txt")  # holds the last-used run number
GAME_CONFIG_PATH = os.path.join(DEBUG_DIR, ".config")       # persisted game state - see load_game_config()
OUTPUT_LOG_PATH = os.path.join(LOGS_DIR, "output.log")      # everything the macro logs, timestamped, with its level - see OutputLog
STATUS_PATH = os.path.join(LOGS_DIR, "status.json")         # live "is it alive and what's it doing" summary - see write_status()
FAILURE_DIR = os.path.join(DEBUG_DIR, "failures")              # a folder per failure (screenshot, need icons, info) - see log_failure()
RECORDINGS_DIR = os.path.join(DEBUG_DIR, "recordings")      # run videos - see RunRecorder
OUTPUT_LOG_MAX_BYTES = 5 * 1024 * 1024   # output.log moves to output.log.old (replacing the last one) past this size
MAX_FAILURE_FOLDERS = 100                # oldest failure folders are deleted beyond this many
MAX_REPORTS = 50                         # oldest run report images (run<N>.png in RUNS_DIR) are deleted beyond this many (the run logs they are drawn from are kept)
CONSOLE_MAX_LINES = 2000                 # the GUI's output box keeps its last this many lines (output.log keeps them all)

# Recording a run ("Record the next run" on the Debug tab, in Options): low quality on purpose, a video of a whole
# unattended run is large. Every frame has the time on it, to line it up with the logs.
RECORD_WIDTH = 640
RECORD_HEIGHT = 360
RECORD_FPS = 4
RECORD_MAX_MINUTES = 480                 # recording stops by itself after this long
MAX_RECORDINGS = 5                       # oldest videos are deleted beyond this many

# The input capture (press HOTKEYS["capture"] in any window to start it and again to stop it): lossless screenshots plus
# every key, mouse button and mouse movement, in a folder of its own, to work out how something is played by hand
# (see InputCapture). Screenshots are large, so it stops by itself after a while or when the disk runs low, and a frame
# that looks like the last one saved is skipped.
CAPTURES_DIR = os.path.join(DEBUG_DIR, "captures")          # a folder per capture
CAPTURE_FRAME_INTERVAL = 0.5             # a screenshot is looked at this often (seconds), and one more right after every mouse click
# A frame is only saved if it differs from the last one saved: at least CAPTURE_CHANGE_FRACTION of the picture (shrunk to
# 320x180 first) has moved by more than CAPTURE_CHANGE_LEVEL (0-255 grey) - a character walking does, a ticking timer or
# a blinking light doesn't. A click always gets a frame, and so does CAPTURE_MAX_SKIP seconds without one.
CAPTURE_CHANGE_LEVEL = 12
CAPTURE_CHANGE_FRACTION = 0.002
CAPTURE_MAX_SKIP = 3.0
CAPTURE_POLL_INTERVAL = 0.01             # how often keys, mouse buttons and the mouse position are looked at
CAPTURE_MOVE_INTERVAL = 0.03             # the mouse position is written at most this often
CAPTURE_MAX_MINUTES = 15                 # a capture stops by itself after this long...
CAPTURE_MIN_FREE_MB = 3000               # ...or when less than this much disk is free
MAX_CAPTURES = 5                         # oldest captures are deleted beyond this many
# Every minigame played is recorded the same way, automatically (MINIGAME_CAPTURE; not while a capture of the hotkey's is
# running), into a folder of RUN_CAPTURES_DIR. Of each minigame the captures of its RUN_CAPTURES_BEST best runs (the most candy corn
# earned), its RUN_CAPTURES_WORST worst, its latest and the latest RUN_CAPTURES_FAILED that failed are kept (prune_run_captures()). A lossless frame is 1-3 MB, so a run
# is kept to RUN_CAPTURE_ZIPS zips of CAPTURE_ZIP_MB by thinning its frames out evenly (see InputCapture) - except frames of
# when things went wrong: at a mark() (a walk that got nowhere, a failure, a stall...) a frame is saved at once and every look for
# the next RUN_CAPTURE_BURST seconds, and a run that didn't finish keeps the frames of its last RUN_CAPTURE_TAIL seconds; such
# frames are not thinned out (up to RUN_CAPTURE_PROTECTED of them, or they would use up the budget).
MINIGAME_CAPTURE = True
RUN_CAPTURES_DIR = os.path.join(DEBUG_DIR, "minigame_captures")
RUN_CAPTURE_ZIPS = 5
# What the Discord bot's /pull can copy into OneDrive: the folders of debug/ (not the game config, which holds the private server link).
PULL_SOURCES = {"failures": FAILURE_DIR, "logs": LOGS_DIR, "runs": RUNS_DIR, "minigame_captures": RUN_CAPTURES_DIR, "captures": CAPTURES_DIR, "recordings": RECORDINGS_DIR}
RUN_CAPTURES_BEST = 2
RUN_CAPTURES_WORST = 2
RUN_CAPTURES_FAILED = 5                   # the latest this many runs that failed (did not finish, or had a failure logged) are kept too
RUN_CAPTURE_BURST = 4.0
RUN_CAPTURE_TAIL = 15.0
RUN_CAPTURE_PROTECTED = 25
CAPTURE_ZIP_MB = 25                       # a stopped capture is also split into zips of at most this size (chat attachments are limited to 30 MB)

# Playing Ghost Gallery (from a human's 162 s run). The ghosts and the boss are drawn in a translucent cyan, HSV (85, 140, 250) under the
# cursor of a player holding the button on one: GHOST_COLOR (OpenCV HSV, low and high) finds it; a ghost is at least GHOST_MIN_AREA px
# (880-7200 seen; the smaller bits are the character's glow and the score popups), the boss 9500-41000. Nothing counts in GHOST_IGNORE_BOXES
# (left, top, right, bottom): the HUD along the top, the character's own glow and the bottom of the screen (a pet in the same cyan sits there). The boss phase begins about 1:08 before the end
# and is told by the boss's health bar, a gold frame (15800-16800 gold pixels in GHOST_BOSS_BAR_BOX; 0-230 before). The player is aimed
# with the mouse: the beam goes to the cursor. The ghost aimed at is followed while a ghost is within GHOST_STICK px of where it was.
GHOST_COLOR = ((78, 90, 200), (100, 210, 255))
# The ghosts of a view from afar (the hall seen from above, the character tiny) are small blue-cyan ovals, hue 92-99, and as saturated as 245-255 (the first version of
# this range stopped at 240 and saw none of the ghosts of a run's first 100 s: its score stayed 0 until the camera was close). Hue from 90 keeps out the slime puddles
# (hue 83-85, saturation 255, not as bright) and the haunted paintings' glow (hue 100 and over). The pet's cyan overlaps it (see GHOST_SKIP_TIME).
GHOST_COLOR_FAR = ((90, 100, 235), (99, 255, 255))
GHOST_CLOSE = 7
# The white "!" over furniture a ghost hides in: a white bar (all channels at least GHOST_ALERT_WHITE) at least GHOST_ALERT_MIN_HEIGHT px high and GHOST_ALERT_ASPECT times
# as high as wide, with a white dot under it (within half the bar's height); looked for in GHOST_ALERT_BAND's rows. The furniture is GHOST_ALERT_BELOW bar heights below it.
GHOST_ALERT_WHITE = 235
GHOST_ALERT_MIN_HEIGHT = 14
GHOST_ALERT_ASPECT = 1.5
GHOST_ALERT_BAND = (150, 860)
GHOST_ALERT_BELOW = 4.0
GHOST_ALERT_MIN_PIECE = 8                    # px of a white piece that can be the bar or the dot
GHOST_ALERT_BAR_FILL = 0.55                  # of its bounding box a bar fills (a letter or a glow does not)
GHOST_ALERT_DOT_GAP = 1.5                    # the dot's top is within this many bar heights of the bar's top...
GHOST_ALERT_DOT_SHIFT = 0.8                  # ...its middle within this many bar widths of the bar's middle (and GHOST_ALERT_DOT_SLACK px more)...
GHOST_ALERT_DOT_SLACK = 4
GHOST_ALERT_DOT_WIDTH = (0.35, 1.6)          # ...its width within these bar widths...
GHOST_ALERT_DOT_ASPECT = 1.6                 # ...at most this many times as high as wide (plus 2 px)...
GHOST_ALERT_DOT_FILL = 0.5                   # ...and filling this much of its bounding box
GHOST_BOSS_TRIAL = 6.0                       # the beam on the biggest blob of the boss phase for this long without a score: it is fog, not the boss
# What the score says is no ghost is left alone - followed as it moves, not by where on the screen it was - for GHOST_SKIP_TIME seconds, then looked at again: the beam on
# a blob scored nothing for GHOST_SPOT_TRIAL seconds, then on the furniture around it GHOST_HIDE_TRIES times (a pet is the same cyan as a ghost, and flies about wherever the camera is),
# or in the boss phase for GHOST_BOSS_TRIAL seconds (fog, a light shaft). A skipped blob is one within GHOST_SKIP_RADIUS px or its own size (its area's square root) of the place.
GHOST_SKIP_TIME = 45.0
GHOST_HIDE_TRIES = 2
GHOST_SKIP_RADIUS = 110
GHOST_EMBLEM_HSV = ((140, 40, 200), (175, 255, 255))   # the pink glow of the emblem on the character's backpack: with the camera close it is 750-10000 px, far away there is none seen
GHOST_EMBLEM_BOX = (500, 250, 1400, 950)
GHOST_EMBLEM_MIN = 500
GHOST_SELF_SCALE = (4.5, 3.5, 6.0)           # the character's box with the camera close, in units of the emblem's size: to each side, up, down (the pet sits below)
GHOST_SELF_MIN_UNIT = 45
GHOST_SMALL_MAX = 8500                       # in the shielded phase a blob bigger than this is no small ghost (the beam's glow was 9500, the boss's fog 400000)
GHOST_BOSS_MIN = 6000                        # in the boss phase the boss is at least this big (9500-41000 seen); a smaller blob is a minion, or some fog
GHOST_MIN_AREA = 250                         # ghosts from afar are 260-1500 px (their trails are white and don't count)
GHOST_IGNORE_BOXES = ((0, 0, 1920, 150), (900, 520, 1020, 680), (0, 860, 1920, 1080))
GHOST_CHARACTER = (960, 540)
GHOST_STICK = 120
GHOST_BOSS_BAR_BOX = (680, 150, 1240, 290)
GHOST_BOSS_BAR_GOLD = ((15, 150, 160), (30, 255, 255))
GHOST_BOSS_BAR_MIN = 3000
GHOST_BOSS_BAR_IGNORE = (680, 150, 1240, 275)  # the bar itself, in the boss phase: its crest and its ghost icons are the same cyan
GHOST_LOOK_INTERVAL = 0.06
STITCHES_CENTER_INTERVAL = 0.5               # between looks at the ring's edge line, to turn the camera towards the middle
STITCHES_LINE_BOX = (500, 560, 1500, 1010)   # where the ring walkway's gold edge line is looked for (below the horizon, round the character)
STITCHES_LINE_HUE = (26, 40)                 # the line's dim olive-gold (OpenCV hue), with the saturation and value below (candy corn is brighter and more orange)
STITCHES_LINE_MIN_SATURATION = 110
STITCHES_LINE_VALUE = (85, 175)
STITCHES_LINE_MIN_PIXELS = 3500              # less of it than this is not a line to go by
STITCHES_LINE_DEADBAND = 12.0                # degrees from straight across that need no turn
STITCHES_TURN_MIN_HOLD = 0.03               # a turn shorter than this (seconds) is not made
STITCHES_TURN_BIAS_GAIN = 0.002             # how fast the steady turn is learned: share of the time the turn key is held, per degree off, per second
STITCHES_TURN_BIAS_MAX = 0.6                # the most of the time the turn key is held for that
STITCHES_LOST_AFTER = 4.0                   # seconds without the ring's line, turning at the learned rate, before the camera is swept round to find it
STITCHES_INTRO_MAX = 15.0                   # the most seconds waited at the start for the ring's line (the intro camera looks at the sky first)
STITCHES_CALIBRATE_HOLD = 0.15              # seconds the test turn of the camera keys is held
STITCHES_CALIBRATE_MIN = 3.0                # degrees the ring's line must move in it to tell which way the keys turn
STITCHES_BOOST_INTERVAL = 2.0
STITCHES_TURN_HOLD_MAX = 0.2                 # seconds a turn key is held for a line 90 degrees off, less for less
GHOST_INTRO_WAIT = 6.0                       # after its overlay shows, the game's own intro camera (a look at the ceiling) runs this long; the zoom and tilt come after, or are undone
# The boss can't be hurt while its bar shows two or three ghost icons instead of its health: the bar's cyan fill (BGR (255, 222, 0),
# 500-20000 px in GHOST_BOSS_BAR_BOX) is gone then (0 px), the boss is dim - too dim for GHOST_COLOR, so only the small ghosts that fly in
# are found - and it is those the beam goes on (a human's run: three such phases, 6-7 s each).
GHOST_BOSS_FILL = ((86, 230, 230), (96, 255, 255))
GHOST_BOSS_FILL_MIN = 200
# Before the boss, with no ghost in sight, the beam is held on a random place in GHOST_FURNITURE_BOX (ghosts hide in the furniture; a new
# place every GHOST_FURNITURE_PERIOD seconds) and the camera is turned every GHOST_CAMERA_PERIOD seconds with nothing to aim at: the right
# button held while the mouse is dragged GHOST_CAMERA_DRAG px (a random way, and up to GHOST_CAMERA_TILT px up or down). The character jumps
# every GHOST_JUMP_MIN-GHOST_JUMP_MAX seconds.
GHOST_FURNITURE_BOX = (100, 120, 1820, 880)
GHOST_FURNITURE_MIN_DISTANCE = 300           # px: the place is at least this far from the character
GHOST_FURNITURE_PERIOD = 1.0
GHOST_CAMERA_PERIOD = 1.5
GHOST_CAMERA_DRAG = (150, 450)
GHOST_CAMERA_TILT = 0                        # px up or down in a turn of the camera: none - the pitch was a random walk, and ended on the ceiling, the floor, a wall (the start's tilt sets it)
GHOST_CAMERA_DRAG_DURATION = 0.15
GHOST_CAMERA_DRAG_STEPS = 6
GHOST_START_TILT = 200                       # px of mouse drag down, right button held, at the start (after zooming out): a view from above
GHOST_MOVE_PERIOD = 4.0                      # the character walks a step (a random MOVE_KEY held GHOST_MOVE_STEP_MIN-MAX seconds) this often
GHOST_MOVE_STEP_MIN = 0.4
GHOST_MOVE_STEP_MAX = 1.2
GHOST_BOSS_TURN_DRAG = (150, 250)             # in the boss phase, with nothing in sight, the camera is turned (one way, until something is) by this many px of drag at a time
GHOST_FURNITURE_PROBES = 6                   # places tried in GHOST_FURNITURE_BOX for one that gets the white outline of furniture that can be hovered...
GHOST_OUTLINE_RADIUS = 160                   # ...looking within this many px of the mouse...
GHOST_OUTLINE_WHITE = 190                    # ...for newly pale pixels (all channels at least this: the outline measured 190-230, in a 1920x1080 screenshot)...
GHOST_OUTLINE_MIN = 150                      # ...at least this many
GHOST_OUTLINE_WAIT = 0.12                    # seconds after moving the mouse, before looking
GHOST_REGRAB_PERIOD = 3.0                   # the beam is let go of and pressed again this often: a player knocked down loses it and the game wants it pressed afresh
GHOST_START_FORWARD = 3.0                    # seconds of W after the camera is set up: out of the doorway and into the middle of the hall
GHOST_TOWARD_MARGIN = 130                    # px: a ghost or the boss farther than this from the character (GHOST_CHARACTER) on an axis is walked towards along it, the key held until it is nearer
GHOST_JUMP_MIN = 3.0
GHOST_JUMP_MAX = 7.0
# Score feedback. The score (the purple pill of the banner, GHOST_SCORE_BOX, its colour GHOST_SCORE_PILL, at least GHOST_SCORE_PILL_MIN px of it when the banner is
# there) says whether what the beam is held on is a ghost: its white digits (every channel at least GHOST_SCORE_WHITE) change by GHOST_SCORE_CHANGE px or more when it
# moves (unchanged digits differ by 0 px, a changed score by 160 and more). Before the boss, a spot the beam has been on for GHOST_SPOT_TRIAL seconds without a score is
# no use: furniture is put aside (places within GHOST_BAD_SPOT_RADIUS px of it aren't tried for GHOST_BAD_SPOT_TIME seconds) and another looked for; a ghost in sight
# that scores nothing is hiding in furniture, which is looked for GHOST_RING px (nearest, farthest) around it. No score at all for GHOST_STALL seconds (a view blocked by a
# pillar, a beam on the floor) is a stall: the beam is let go of, a random key held GHOST_STALL_WALK seconds, a jump, and the camera turned by GHOST_STALL_TURN px of drag.
GHOST_SCORE_BOX = (1050, 56, 1170, 88)
GHOST_SCORE_PILL = ((128, 150, 240), (134, 200, 255))
GHOST_SCORE_PILL_MIN = 1500
GHOST_SCORE_WHITE = 230
GHOST_SCORE_CHANGE = 60
GHOST_SPOT_TRIAL = 3.0
GHOST_BAD_SPOT_RADIUS = 150
GHOST_BAD_SPOT_TIME = 20.0
GHOST_RING = (60, 220)
GHOST_STALL = 12.0
GHOST_STALL_WALK = 1.0
GHOST_STALL_TURN = (450, 700)

# Hauntlet 2, read from the screen (see the HAUNTLET 2 READER section of main.py).
# The ROOM panel's number: white digits on a dark panel, always in the same place, at most HAUNTLET_ROOM_MAX_DIGITS long.
# Each digit is cut out, shrunk to HAUNTLET_DIGIT_SIZE and compared with the saved ones; the mean difference (0-255) of the
# best must be under HAUNTLET_DIGIT_MAX_DIFF. The panel is flat UI, so a digit matches its saved picture exactly (0.0 on
# every frame of a 256-frame run) and the two most alike saved digits are 43.7 apart: under half of that, the best
# match can't be mistaken for another digit.
HAUNTLET_ROOM_BOX = (1086, 84, 1170, 116)    # left, top, right, bottom
HAUNTLET_DIGIT_WHITE_LEVEL = 200             # a pixel is part of a digit when all its channels are at least this
HAUNTLET_DIGIT_MIN_AREA = 25                 # px: smaller white specks are not digits
HAUNTLET_DIGIT_SIZE = (14, 20)
HAUNTLET_DIGIT_MAX_DIFF = 20
HAUNTLET_ROOM_MAX_DIGITS = 3

# The item slots along the bottom of the Hauntlet 2 screen: round white-ringed icons, about HAUNTLET_SLOT_PITCH apart,
# centred on the screen, up to four. They are found as circles within HAUNTLET_SLOT_BAND (top, bottom). A slot's picture is
# the HAUNTLET_ITEM_CROP px square around its centre, matched by correlation (0-1) with the pictures of each saved item;
# the best must reach HAUNTLET_ITEM_MATCH or the item is unknown. These names are not items and are left out of the list.
HAUNTLET_SLOT_BAND = (930, 1080)
HAUNTLET_SLOT_RADIUS = (44, 58)
HAUNTLET_SLOT_MIN_DISTANCE = 80
HAUNTLET_ITEM_CROP = 60
HAUNTLET_ITEM_MATCH = 0.75
HAUNTLET_NOT_ITEMS = {"backpack"}          # the lobby's backpack button sits where the first slot would
# The "x2" badge on a slot: purple, at the slot's top left. The "x" and each digit are white pieces on it, cut out, shrunk
# to HAUNTLET_COUNT_SIZE and compared with the saved glyphs (mean difference, 0-255, under HAUNTLET_COUNT_MAX_DIFF).
HAUNTLET_BADGE_BOX = (-62, -60, 12, -18)    # left, top, right, bottom, relative to the slot's centre
HAUNTLET_BADGE_WIDTH = (40, 70)              # px: the badge is a purple piece this wide and tall; a bigger or smaller
HAUNTLET_BADGE_HEIGHT = (16, 30)             # one is background of the same colour, not the badge
HAUNTLET_COUNT_MIN_HEIGHT = 6                # px: a flatter white piece is a bit of the slot's ring, not a glyph
HAUNTLET_COUNT_SIZE = (10, 14)
HAUNTLET_COUNT_MAX_DIFF = 22

# The hearts above the player's head: a row of heart icons, pink with a black outline, or solid black once lost. Found by
# their pink (HSV, two ranges because red wraps round 0): pieces of the right shape and size, none higher up the screen
# than HAUNTLET_HEART_MIN_Y (the room panel); the row nearest the middle of the screen is the player's. The row is then
# followed along its line, one HAUNTLET_HEART_PITCH x a heart's width at a time: a cell with a pink heart is red, one
# that is nearly all pure black (every channel at most HAUNTLET_HEART_BLACK_LEVEL) is black, anything else ends the row.
HAUNTLET_HEART_PINK = (((0, 70, 190), (12, 175, 255)), ((168, 70, 190), (180, 175, 255)))
HAUNTLET_HEART_MIN_WIDTH = 15
HAUNTLET_HEART_MIN_Y = 110
HAUNTLET_HEART_MIN_FILL = 0.45             # a heart piece fills this much of its bounding box (a bar or a ring doesn't)
HAUNTLET_HEART_ASPECT = (0.8, 1.25)        # width / height of a heart piece
HAUNTLET_HEART_PITCH = 1.3
HAUNTLET_HEART_BLACK_LEVEL = 6
HAUNTLET_HEART_BLACK_FRACTION = 0.55
HAUNTLET_HEART_MAX_EACH_SIDE = 4
HAUNTLET_HEART_PINK_FRACTION = 0.35           # a cell this pink is a heart even where no heart-shaped piece was found
HAUNTLET_HEART_MIN = 2                     # a row shorter than this isn't one (every row seen had 3 or 4 hearts, lost ones included)
HAUNTLET_HEART_MAX = 5                     # a row longer than this isn't a row of hearts (4 were seen at most)

# The doors of a Hauntlet 2 room: each has a TV above it, its screen a light-cyan number (206, 255, 255 - the glow
# saturates the green and blue channels). A room is a row of three such numbers (the same height, evenly spaced);
# the TV spacing is the picture's scale (the camera zooms in and out), so the boxes below are in px at a spacing of
# HAUNTLET_DOOR_SPACING and scaled to the spacing seen. The fork rooms have no TVs, just two doorways with signs.
# The timer (white digits, top left of the ROOM panel) is hidden in a room's first moments and while the player passes
# through a door: 950-1190 white pixels when shown, 0 when not - and a frame without the timer may still show the
# last room's doors, so a room is only looked at while the timer shows.
HAUNTLET_TIMER_BOX = (730, 55, 865, 122)     # left, top, right, bottom
HAUNTLET_TIMER_WHITE_LEVEL = 215
HAUNTLET_TIMER_MIN_WHITE = 500
HAUNTLET_TV_GLOW = ((150, 248, 248), (225, 255, 255))   # (low, high) red, green, blue of the number's light
HAUNTLET_TV_AREA = (80, 1200)                # px of one such number (a thin "1" is the smallest, a TV close to the camera the biggest)
HAUNTLET_TV_HUD_BOX = (700, 0, 1220, 150)      # left, top, right, bottom: the room panel and timer; their glow is no TV
HAUNTLET_TV_PAIR_GAP = (560, 1000)           # px between two TVs that are the outer two of three (900 with the camera close), when...
HAUNTLET_TV_PAIR_OFF_CENTER = 150            # px: ...their middle is this close to the middle of the screen (neighbours with the third off screen are not), or...
HAUNTLET_TV_PAIR_RATIO = 34                  # ...their gap is this many times the side of a number's blob (neighbours: about 21, the outer two: about 43; the middle TV is hidden, by a balloon say)
HAUNTLET_TV_SPACING = (200, 500)             # px between neighbouring TVs
HAUNTLET_TV_ROW_TOLERANCE = 15               # px: the TVs of a row are this level...
HAUNTLET_TV_SPACING_TOLERANCE = 0.25         # ...and these spacings this close
HAUNTLET_DOOR_SPACING = 360
# What a door looks like, from the pixels below its TV, boxes (left, top, right, bottom) relative to the TV's number.
# Told apart so far, from the 29 rooms of one captured run: a locked door (a red light above it, 40-99 pixels of it where
# every other door has none), a "???" door (pale grey, mean brightness 180-203 where the others are at most 163), a
# dark door (more than 40% of it near black, 44-74% where no other door has more than 37%), and of the rest a boarded
# door from a plain one by its planks: the share of the door's pixels on a strong, near-horizontal edge (the planks'
# edges; a plain door's edges are mostly the vertical frame and panel). It is 0.03-0.13 on plain doors (median 0.06) and
# 0.11-0.24 on boarded ones (median 0.18); the threshold is the best one found on the doors of 25 rooms, 94% right on
# rooms left out of the search, and wrong mostly on the third layer's spiked doors and on boarded doors half hidden by the player.
HAUNTLET_LOCK_LIGHT_BOX = (-60, 20, 60, 70)
HAUNTLET_LOCK_LIGHT_RED = 225                # the light is pure red, (255, 37, 37); the player's hearts, which can be under the door, have dark red outlines (204, 68, 54)
HAUNTLET_LOCK_LIGHT_OTHER = 70
HAUNTLET_LOCK_LIGHT_MIN = 25                 # px of a red patch that is the light (about 45)...
HAUNTLET_LOCK_LIGHT_MAX = 200                # ...at most this many (a wall painted red is bigger)...
HAUNTLET_LOCK_LIGHT_ROUND = 2                # ...and no longer than this many times its width (or the other way round)
HAUNTLET_DOOR_BOX = (-45, 35, 45, 130)
HAUNTLET_MYSTERY_BRIGHTNESS = 172
HAUNTLET_DARK_LEVEL = 55
HAUNTLET_DARK_FRACTION = 0.4
HAUNTLET_PLANK_BOX = (-55, 35, 55, 140)
HAUNTLET_PLANK_BLUR = 1.2                    # px: the picture's grain is smoothed out before looking for edges
HAUNTLET_PLANK_EDGE = 20                     # Sobel strength (of the 0-255 grey picture) from which a pixel is on an edge
HAUNTLET_PLANK_ANGLE = 35                    # degrees: an edge this close to horizontal is a plank's
HAUNTLET_PLANK_FRACTION = 0.103

# Playing Hauntlet 2: after the start wait the screen is read every HAUNTLET_LOOK_INTERVAL seconds. The room number
# changes as the player passes through a door, while the last room's doors are still on screen (open, the camera
# following the player), so a room is only looked at HAUNTLET_ROOM_SETTLE seconds after its number changed, with the
# timer showing and the row of TVs still where it was on the look before (within HAUNTLET_TV_STABLE px: the camera
# has stopped). Then a door is chosen and walked to - first A or D alone, to bring the player in front of the
# door: HAUNTLET_STRAFE_DURATION seconds per door spacing (the distance between neighbouring doors) of distance, measured
# from the player's hearts to the door's TV, no strafe under HAUNTLET_STRAFE_MIN spacings and never more than
# HAUNTLET_STRAFE_MAX (a human's runs, from the middle: left door 1.0-1.1 s, right 0.9-1.3 s), then W (steered every HAUNTLET_STEER_EVERY seconds while the TVs are in sight, and unstuck by HAUNTLET_UNSTICK_* when the player is not moving) until the room number
# changes, at most HAUNTLET_WALK_MAX seconds (a human's room: W held 3-4.8 s), and then HAUNTLET_WALK_PAST_DURATION longer: the
# number changes with the player still in the doorway, the camera close on the door, and the next room's doors can't be seen from there. If the number still hasn't changed
# HAUNTLET_RETRY_WAIT seconds after the walk, a door is chosen again (a walk that gets nowhere is followed by a step back, a different door and wiggling).
# A room whose number is a multiple of HAUNTLET_FORK_EVERY (10 and 20 were seen) is a fork: no TVs, two doorways. A
# room without a row of TVs is a single doorway ahead - at once for room 1, the entrance hall (its doorway leads into a room
# with three doors that is still room 1), else HAUNTLET_DOORS_TIMEOUT seconds after its number changed - and is walked straight on.
HAUNTLET_STRAFE_DURATION = 1.1
HAUNTLET_STRAFE_MIN = 0.15
HAUNTLET_STRAFE_MAX = 2.5
HAUNTLET_WALK_MAX = 6.0
# A door is only as wide as a fraction of the distance between two doors, so a player that is a bit off its door stands against the wall beside it and W gets it nowhere
# (room 5 of the room 8 run and room 4 of the room 15 run: 10-30 s of the room's 30 s timer, the player 0.3-0.4 door spacings off, which the old limit for steering, 0.4,
# never corrected). A strafe worked out in one go from where the doors were also ends a little short or long (the room 15 run's 2.4 s of A moved the doors 20-30 % more than
# the strafe's 1.1 s per spacing says, and the player stood against the left wall, beside its door). So the player is aimed in steps with a fresh look at the doors before each:
# first, up to HAUNTLET_AIM_STEPS of them, each a hold of the key towards the door for HAUNTLET_STEER_GAIN of the strafe (at most HAUNTLET_STEER_MAX seconds), until the door is within
# HAUNTLET_STEER_MIN door spacings of the player; then, with W held, the same every HAUNTLET_STEER_EVERY seconds while the three TVs are in sight.
HAUNTLET_STEER_EVERY = 0.3
HAUNTLET_STEER_MIN = 0.15
HAUNTLET_STEER_MAX = 0.8
HAUNTLET_STEER_GAIN = 0.75
HAUNTLET_AIM_STEPS = 8
HAUNTLET_WALK_PAST_DURATION = 1.5            # more of W after the number changed (a human's: 1.0-2.0 s, median 1.4)
HAUNTLET_START_DELAY = 1.5                    # after the view is set up, before the first step: the entrance hall's doors open on their own (a macro-played run that had not moved by the 45 s the room's timer gave it was thrown out)
HAUNTLET_START_FORWARD = 1.4                  # the first step, forward (a human's: 1.3-1.5 s)
HAUNTLET_LOOK_INTERVAL = 0.25
HAUNTLET_ROOM_SETTLE = 2.0
HAUNTLET_TV_STABLE = 4
HAUNTLET_RETRY_WAIT = 1.0                    # after a walk that didn't change the room, this long before the doors are chosen from again
HAUNTLET_VIEW_SIZE = (96, 54)                 # what the walk looks at to tell whether the view moves: the screen shrunk to this many pixels (grey)...
HAUNTLET_VIEW_ROWS = (8, 45)                 # ...of which only these rows count (not the room panel at the top and the item bar at the bottom)
HAUNTLET_SEARCH_TURNS = 0                    # times the camera is turned, in a room whose doors can't be found, before walking straight ahead (0: never - turning it changes where the keys take the player: in a captured fork the doorway was a step away until the camera was turned, and the player then stood against the wall for 20 s)
HAUNTLET_CAMERA_DRAG = (200, 500)            # px of mouse drag (right button held) for such a turn
HAUNTLET_BACKUP_DURATION = 0.5               # a walk after one that got nowhere begins with a step back this long
HAUNTLET_STILL_LEVEL = 2.0                   # a walk whose view changes less than this (mean grey level of the shrunk view, 0-255) between two looks isn't moving: standing still read 0.1-1.1, walking 11-44 over 1.4 s
HAUNTLET_STILL_AFTER = 0.8                   # seconds of that, forward held, before the player is taken to be caught on something...
HAUNTLET_UNSTICK_STEP = 0.5                  # ...and steps sideways (towards its door) and jumps: this long, and this much longer at each try...
HAUNTLET_UNSTICK_MAX = 2.0                   # ...up to this long
HAUNTLET_UNSTICK_MARGIN = 40                 # px: a door's TV this far or more to a side of the player is the way to step
HAUNTLET_CIRCLE_HSV = ((115, 60, 90), (145, 255, 255))   # the purple circles on the floor in front of the doors: stepping into one picks that door
HAUNTLET_CIRCLE_WIDTH = (150, 520)           # px, of a ring's bounding box (the purple badges and labels are smaller)
HAUNTLET_CIRCLE_HEIGHT = (60, 300)
HAUNTLET_CIRCLE_MIN_AREA = 600
HAUNTLET_CIRCLE_FILL = 0.5                   # a ring fills less than this of its box
HAUNTLET_CIRCLE_BAND = (150, 900)            # rows of the screen the circles are looked for in (not the panel above, nor the item bar)
HAUNTLET_IN_CIRCLE = 0.35                    # the player is in the circle it hides when it stands this close to where it would be (in circle spacings)
HAUNTLET_LOCK_WAIT = 3.0                     # after stepping into a circle the player can't move for a few moments: no unsticking for this long
HAUNTLET_SHADOW_BOX = (-110, 20, 110, 200)   # where a shadow door's eyes are looked for, relative to its TV (scaled like the other boxes)
HAUNTLET_SHADOW_WHITE = 225                  # the glowing eyes: all channels at least this...
HAUNTLET_SHADOW_AREA = (6, 500)              # ...px each
HAUNTLET_SHADOW_RING = 60                    # ...in dark smoke: the mean brightness of the ring around them at most this (screws and lights: 73 and more; the eyes: 46-49)
HAUNTLET_FORK_EVERY = 10
# A fork's doorways have no TV to find them by. Each opening is dark inside (grey, little colour: HAUNTLET_OPENING_HSV) and begins right
# under the wall's pale stripes (HAUNTLET_WALL_HSV) - while everywhere else between the wall and the floor there is the gold wainscot, about
# 60 px of it. So in each column of HAUNTLET_FORK_BAND (rows) the first dark pixel, of a run of HAUNTLET_OPENING_RUN rows, with at least
# HAUNTLET_OPENING_WALL wall pixels HAUNTLET_OPENING_ABOVE rows (nearest, furthest) above it is a column of an opening; columns less than HAUNTLET_OPENING_JOIN px
# apart are one opening. Two openings HAUNTLET_OPENING_WIDTH px wide and HAUNTLET_OPENING_GAP px apart (middle to middle) are the fork's doorways (seen with the
# camera near the wall: 124-170 wide, 574-609 apart; from the room's entrance only one is seen, so the walk goes on until both are).
HAUNTLET_FORK_BAND = (160, 850)
HAUNTLET_WALL_HSV = ((0, 85, 205), (40, 145, 255))
HAUNTLET_OPENING_HSV = ((0, 0, 70), (40, 104, 164))
HAUNTLET_OPENING_RUN = 6
HAUNTLET_OPENING_ABOVE = (4, 45)
HAUNTLET_OPENING_WALL = 10
HAUNTLET_OPENING_JOIN = 12
HAUNTLET_OPENING_WIDTH = (100, 260)
HAUNTLET_OPENING_GAP = (400, 800)
# Seen at an angle, the far doorway may show only a narrow piece (HAUNTLET_OPENING_PIECE px wide: its inside is lit): one whole opening no wider than
# HAUNTLET_OPENING_MAIN_MAX and exactly one such piece HAUNTLET_OPENING_PIECE_GAP px from it (middle to middle) are the two doorways.
HAUNTLET_OPENING_PIECE = (40, 99)
HAUNTLET_OPENING_MAIN_MAX = 200
HAUNTLET_OPENING_PIECE_GAP = (420, 680)
HAUNTLET_DOORS_TIMEOUT = 8.0
# The items (their folder names in HAUNTLET_ITEM_DIR) and what each is for: the grey key opens a locked door; a cyan potion makes a heart
# that lasts one room, for a boarded door; the red potion refills the hearts, used when only HAUNTLET_LOW_HEARTS or fewer are left; a
# heart potion adds one and is used whenever there is one; the phone, used with a chance of HAUNTLET_PHONE_CHANCE at each choice
# of a door, puts a red arrow over the door with a ghost behind it - a big red triangle (HAUNTLET_ARROW_RED, HSV; the area, width and
# height in px it was seen with, in HAUNTLET_ARROW_BOX = left, top, right, bottom), over the TV of its door, no more than
# HAUNTLET_ARROW_MAX_OFFSET door spacings off its middle - which is then not gone through. The arrow is looked for HAUNTLET_ARROW_LOOKS times.
HAUNTLET_ITEM_KEY = "key"
HAUNTLET_ITEM_BOARDED = "cyan_potion"
HAUNTLET_ITEM_REFILL = "red_potion"
HAUNTLET_ITEM_HEART = "heart_potion"
HAUNTLET_ITEM_PHONE = "phone"
HAUNTLET_LOW_HEARTS = 1
HAUNTLET_PHONE_CHANCE = 0.35
HAUNTLET_PHONE_CHANCE_LOW = 0.9              # the phone's chance when the hearts are down to HAUNTLET_LOW_HEARTS or fewer
HAUNTLET_ITEM_WAIT = 0.6                     # seconds after clicking an item
HAUNTLET_ARROW_RED = (((0, 200, 170), (8, 255, 255)), ((172, 200, 170), (180, 255, 255)))
HAUNTLET_ARROW_BOX = (0, 90, 1780, 640)
HAUNTLET_ARROW_AREA = (2200, 6000)
HAUNTLET_ARROW_WIDTH = (100, 150)
HAUNTLET_ARROW_HEIGHT = (45, 125)
HAUNTLET_ARROW_MAX_OFFSET = 0.5
HAUNTLET_ARROW_LOOKS = 4

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
                               # (so you can take over the computer) - see watch_stop_hotkey()
BLACKOUT_RAISE_INTERVAL_MS = 200  # milliseconds between the black window being put back above everything while the screen is blank (the control window, coming back up, lands above it)
BLACKOUT_SETTLE = 0.4         # seconds the test flash is shown before the screen is looked at, to see whether the macro's screenshots see the window
BLACKOUT_SEEN_RISE = 40       # if the screenshot's mean grey level rises this much (0-255) while the white test window is up, the screenshots see the window
BLACKOUT_TOO_BRIGHT = 215     # a screen whose mean grey level is above this is too bright for that test to tell (white against white)
CLOSE_WORKER_WAIT = 3.0       # seconds the window's close waits for the running work to end, before it lets go of the keys and the mouse itself
STOP_HOTKEY_POLL_INTERVAL = 0.05  # how often the key is looked at (seconds)
CONSOLE_DRAIN_INTERVAL_MS = 50   # how often queued output is put on the GUI console (see DebugCapture)
CATCH_WAIT_AFTER_EQUIP = 1.0   # wait after equipping toy before throwing
CATCH_THROW_COUNT = 3           # number of times the toy is thrown
CATCH_EMOTE_DELAY = 5.0        # delay between throw clicks
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
FOCUS_PET_CLICK_SETTLE = 1.0         # after each click, before checking whether the pet is focused now
FOCUS_PET_MENU_WAIT = 4.0            # wait after the clicks, for the pet's menu to open

# The 'choose' need's button is matched by its exact color rather than shape,
# since it's just a plain circle with no distinguishing icon. Given as (R, G, B).
CHOOSE_BUTTON_COLOR = (181, 6, 254)
CHOOSE_SLOW_MOVE_DURATION = 1.0  # deliberate, slow mouse travel to the found button

# The paycheck popup's CASH OUT button, matched by exact color the same way
# as CHOOSE_BUTTON_COLOR above.
# The popup itself is recognised by a crop of its bank-card header ("BANK of Adopt Me!", ref/popups/paycheck.png)
# found as described in POPUP_TEMPLATES["paycheck"].
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
# The inventory tour (inventory_tour(), the bot's /inventory): the backpack's pets tab, in the EXPANDED backpack (opened with the orange button left of the
# settings one in the normal backpack's header; clicking the selected tab again does nothing), scrolled
# a step at a time with a picture of its panel after each. First run: the backpack expanded, a picture was made, and the wheel moved nothing (it was given 1/120 of
# a notch a step: see WHEEL_NOTCH), so the tour ended after that one picture; the window of the macro was in the picture.
INVENTORY_DIR = os.path.join(DEBUG_DIR, "inventory")            # the pictures (001.png, 002.png ...), emptied at the start of each tour
INVENTORY_DONE_PATH = os.path.join(INVENTORY_DIR, "done.json")  # written when the tour ends, however it ends: {"pictures": N, "ended": why}
INVENTORY_PETS_TAB_POS = (817, 713)
INVENTORY_EXPAND_POS = (938, 642)                               # the orange button left of the settings one, in the normal backpack's header (measured on tests/popups/backpack_open.png)
INVENTORY_EXPAND_SETTLE = 1.0                                   # seconds after it is clicked, for the backpack to expand
INVENTORY_PANEL_BOX = (1090, 0, 1920, 1080)                    # (left, top, right, bottom) of each picture: the right side of the screen, where the expanded backpack is (the first picture, of (1090, 36, 1840, 1050), showed its right-hand column cut off)
INVENTORY_SCROLL_POS = (1465, 560)                              # the mouse is over the pets here while the wheel turns
WHEEL_NOTCH = 120                                               # what a turn of the mouse wheel by one notch is, in the units Windows takes (pyautogui.scroll() passes them on as they are: 10 is a twelfth of a notch)
INVENTORY_SCROLL_CLICKS = 300                                   # notches of the wheel per step
INVENTORY_SCROLL_CHUNK = 10                                     # notches sent at a time (a game may take one huge turn of the wheel for a small one)
INVENTORY_SCROLL_PAUSE = 0.02                                   # seconds between those
INVENTORY_SCROLL_SETTLE = 0.6                                   # seconds after a step, before the picture
INVENTORY_MAX_PICTURES = 40                                     # the tour ends at this many, if the list has not ended
INVENTORY_SAME_LEVEL = 1.0                                      # a panel whose mean grey level differs from the last picture's by less than this is the same: the list did not move, it has ended
# unscrew() closes either backpack at once at the start of a cycle; during a task (where a handler has the
# normal one open for a few seconds on purpose) only once it's been seen open on this many checks in a
# row, UNSCREW_TASK_INTERVAL apart.
# The normal backpack is also recognised by a crop of its "BACKPACK" title (POPUP_TEMPLATES["backpack_normal"]):
# the color check above was tuned on one screenshot and missed another.
BACKPACK_CLOSE_CONFIRMATIONS = 2
# The EXPANDED backpack has its own BACK button (POPUP_TEMPLATES["backpack_back"]): clicking it closes the
# backpack entirely, where the key only shrinks it to the normal form.

# Ride need: positions for the backpack -> vehicles -> first vehicle -> equip
# sequence, plus how long to hold each step.
RIDE_BACKWARD_DURATION = 1.0   # step back before pressing e to mount
RIDE_WAIT_AFTER_E = 5.0        # wait after e before opening the backpack
RIDE_FORWARD_DURATION = 2.0    # walk forward briefly after mounting, before the backpack
RIDE_VEHICLES_POS = (816, 804)
RIDE_FIRST_VEHICLE_POS = (976, 708)
RIDE_EQUIP_POS = (1062, 814)
RIDE_R_HOLD_DURATION = 1.5     # hold KEYS["helicopter"] this long after equipping, before walking back and forth

# Backpack-teleport sequence, shared by every teleport destination: open
# backpack -> category tab -> two more fixed clicks that confirm/execute
# the teleport -> wait for it to take effect -> walk backward to clear the
# landing spot. Only the category tab differs per destination (passed into
# teleport_to() as `category_pos`); everything else is always the same,
# hence "general".
TELEPORT_YES_TRIES = 2            # the teleport dialog's Yes is clicked this often at most, until the view changes or the screen goes white (as for a minigame popup)
TELEPORT_WAIT = 5.0               # wait after the teleport click, for it to take effect
TELEPORT_SETTLE_WAIT = 7.0         # wait after stepping back, for the landing to settle
GENERAL_TELEPORT_POS_2 = (895, 705)
GENERAL_TELEPORT_POS_3 = (1048, 658)
TELEPORT_BACK_DURATION = 1.0      # how long to hold 's' to clear the landing spot
TELEPORT_WALK_STEP_GAP = 4.0      # pause between consecutive steps of a teleport-walk need's sequence
SICK_PRE_CLICK_WAIT = 2.0         # after the sick need's walk, before the doctor's prompt is looked for (let the character come to rest)
SICK_PROMPT_LOOK = 3.0            # how long the doctor's prompt (POPUP_TEMPLATES["doctor_prompt"]) is looked for before the need is given up
SICK_CONFIRM_WAIT = 7.0           # how long the doctor's dialog (POPUP_TEMPLATES["sick_dialog"]) is looked for after the prompt was clicked (it took about 2.5 s) before the need is given up
SICK_LOOK_INTERVAL = 0.4          # between two looks for either

TELEPORT_PETS_TAB_POS = (817, 713)       # nursery: pets tab
TELEPORT_VEHICLES_TAB_POS = (813, 810)   # dealership: vehicles tab
SICK_FINAL_CLICK_POS = (1045, 660)       # click after the sick need's walk

# Whether a teleport-walk need flies by helicopter by default; an entry can
# override it with `helicopter=True/False`. Only the Halloween bored, beach
# and camping entries turn it on.
HELICOPTER_REQUIRED = False
HELICOPTER_FORWARD_DURATION = 1.0  # step forward this long after teleporting, before equipping
HELICOPTER_HOLD_DURATION = 4.5     # then hold KEYS["helicopter"] this long (it was 0.5 s less, and the helicopter could be caught on a tree)

# Every key the macro presses, by what it is for (code says cfg.KEYS["forward"], not "w"): the game's own bindings, then the macro's hotkeys.
KEYS = {
    "forward": "w", "left": "a", "back": "s", "right": "d",   # walking (every minigame walks with them; Stitches' Spotlight holds "right" all round the ring)
    "jump": "space",
    "backpack": "b",
    "mount": "e",                  # mounting a vehicle...
    "interact": "e",               # ...and collecting/harvesting (lure_collect(), tree_collect()): the same physical key, named for each use
    "speed_boost": "e",            # Stitches' Spotlight's speed boost ("Press E for a Speed Boost!"), pressed every STITCHES_BOOST_INTERVAL
    "zoom_in": "i",
    "zoom_out": "o",               # zooms the camera out while held (the minigames hold it to see the whole room)
    "helicopter": "r",             # held to fly the equipped helicopter (also held briefly by ride)
    "camera_left": "left",         # the arrow keys turn the camera (Stitches' Spotlight turns it to keep the middle of the ring in view)
    "camera_right": "right",
}
KEY_SEQUENCES = {                  # keys pressed one after the other, each held KEY_STEP_GAP-ish (RESPAWN_KEY_DURATION, REJOIN_LEAVE_KEY_GAP)
    "respawn": ("esc", "r", "enter"),
    "leave_game": ("esc", "l", "enter"),
}
HOTKEYS = {                        # the macro's own keys, each a letter or digit (or a character that is a key of its own on the keyboard layout), read in any window (the last three only while Roblox is focused)
    "stop": "p",                   # stops a running macro
    "capture": "m",                # starts or stops an input capture
    "blackout": "h",               # blanks the screen with a black window (and again, to take it away)
    "screenshot": "ô",             # saves a picture of the whole screen in SCREENSHOTS_DIR (not a letter of the alphabet: its key is looked up on the keyboard layout, see hotkey_vk())
}
MOVE_KEYS = tuple(KEYS[name] for name in ("forward", "left", "back", "right"))
HELD_KEYS = tuple(dict.fromkeys([*KEYS.values(), *(key for keys in KEY_SEQUENCES.values() for key in keys)]))   # every key the macro can hold down: let go of when it ends, at a stop, or when it is killed


# Needs that teleport somewhere and walk (see TeleportWalkNeedHandler), then
# wait for the need to clear. Each entry: where to teleport, the
# (key, seconds) holds to perform in order, optionally a `final_click` (a need with one has a doctor's dialog to answer after the last hold:
# its prompt is clicked, the dialog waited for and this is its Yes - sick; see TeleportWalkNeedHandler._open_dialog()), optionally `helicopter` (above) and optionally
# `max_wait` (how long to wait for the need to clear afterwards; default NEED_GONE_MAX_WAIT).
TELEPORT_WALK_NEEDS_NORMAL: dict[str, dict[str, Any]] = {
    "bored":   dict(teleport_pos=TELEPORT_PETS_TAB_POS,     steps=((KEYS["forward"], 16.0), (KEYS["left"], 10.0))),
    "beach":   dict(teleport_pos=TELEPORT_PETS_TAB_POS,     steps=((KEYS["left"], 27.0),)),
    "school":  dict(teleport_pos=TELEPORT_PETS_TAB_POS,     steps=((KEYS["forward"], 1.2), (KEYS["left"], 10.0))),
    "cafe":    dict(teleport_pos=TELEPORT_VEHICLES_TAB_POS, steps=((KEYS["left"], 3.2), (KEYS["back"], 5), (KEYS["left"], 0.2), (KEYS["back"], 3))),
    "salon":   dict(teleport_pos=TELEPORT_VEHICLES_TAB_POS, steps=((KEYS["left"], 2.5), (KEYS["forward"], 10.0))),
    "pizza":   dict(teleport_pos=TELEPORT_VEHICLES_TAB_POS, steps=((KEYS["forward"], 3.0), (KEYS["left"], 7.2), (KEYS["back"], 4.0))),
    "camping": dict(teleport_pos=TELEPORT_PETS_TAB_POS,     steps=((KEYS["forward"], 0.1), (KEYS["right"], 3.0), (KEYS["back"], 2.2), (KEYS["right"], 15.0), (KEYS["forward"],20.0))),
    "sick":    dict(teleport_pos=TELEPORT_PETS_TAB_POS,     steps=((KEYS["forward"], 2.5), (KEYS["right"], 3.0), (KEYS["forward"], 0.8)),
                    final_click=SICK_FINAL_CLICK_POS),
}

# Used instead of the entry of the same name above while HALLOWEEN is on (Halloween
# moves the nursery) - and "sick" only exists here, so it isn't a need at all
# outside Halloween.
TELEPORT_WALK_NEEDS_HALLOWEEN: dict[str, dict[str, Any]] = {
    "bored":   dict(teleport_pos=TELEPORT_PETS_TAB_POS,     steps=((KEYS["forward"], 9),), helicopter=True),
    "beach":   dict(teleport_pos=TELEPORT_PETS_TAB_POS,     steps=((KEYS["left"], 20.0),), helicopter=True),
    "school":  dict(teleport_pos=TELEPORT_PETS_TAB_POS,     steps=((KEYS["forward"], 3), (KEYS["left"], 6))),
    "camping": dict(teleport_pos=TELEPORT_PETS_TAB_POS,     steps=((KEYS["back"], 20),), helicopter=True),
    "sick":    dict(teleport_pos=TELEPORT_PETS_TAB_POS,     steps=((KEYS["forward"], 2.5), (KEYS["right"], 4.0), (KEYS["forward"], 1.2)),
                    final_click=SICK_FINAL_CLICK_POS),
}

# The set actually used everywhere else (TeleportWalkNeedHandler and the
# handler registry both read this).
TELEPORT_WALK_NEEDS: dict[str, dict[str, Any]] = {**TELEPORT_WALK_NEEDS_NORMAL, **TELEPORT_WALK_NEEDS_HALLOWEEN} if HALLOWEEN \
    else TELEPORT_WALK_NEEDS_NORMAL

WATCH_POLL_INTERVAL = 0.2           # how often the screen is looked at while waiting for the game's "Only Pets..." answer to a need button click
DISABLED_NEEDS_REJOIN_THRESHOLD = 4  # this many needs disabled for the run (see NEED_STUCK_CHECKS) means something is
                                     # badly wrong: rejoin the game, run setup, and give every need a fresh chance
NEED_STUCK_CHECKS = 5            # an enabled need still on screen after this many attempts in a row is
                                  # disabled for the rest of the run - see record_detection()
NEED_GONE_MAX_WAIT = 75.0        # most any need is waited on to clear: wait_until_need_gone() (the teleport-walk needs and the button
                                  # needs; they time out by themselves in the game, and camping was seen clearing ~66s after its walk),
                                  # and the total duration of the walk/ride actions that end early once it has cleared
NEED_GONE_POLL_INTERVAL = 3.5    # how often to check for the icon during that wait (and, while walking, from the watcher thread)
NEED_GONE_FLICKER_RECHECK_DELAY = 0.3  # before counting any single miss, re-sample this soon after -
                                        # the icon can flash for a frame (or briefly drop out against a
                                        # busy background) without the need actually having cleared
NEED_WATCH_JOIN_TIMEOUT = 5.0    # most walk_alternating() waits for its background need-gone
                                 # watcher thread to exit before moving on without it

# Lure/tree collection (run automatically by side_quest(), not tied to a detected need)
LURE_COLLECT_WALK_DURATION = 2.0  # hold 'a' this long before pressing KEYS["interact"] at the lure
LURE_COLLECT_SETTLE_DELAY = 1.0   # wait this long between the two KEYS["interact"] presses at the lure
LURE_NEW_POS_1 = (876, 711)       # the two backpack clicks that place a fresh lure - see set_new_lure()
LURE_NEW_POS_2 = (978, 814)
TREE_COLLECT_WALK_DURATION = 2.0  # hold 'd' this long before stepping back
TREE_COLLECT_BACKWARD_DURATION = 1.0  # then hold 's' this long before pressing KEYS["interact"] at the money tree
TREE_HARVEST_YIELD = 16           # money_collected added to the persisted total per tree_collect() call

# side_quest(): how often to tend the money tree/lure, and the money_collected
# target that pauses tree harvesting (see load_game_config()/save_game_config()).
# money_collected only goes up here, until it is reset to 0 every day at MONEY_RESET_HOUR
# (local time - see side_quest()), or by the Debug tab's Reset Config.
MONEY_COLLECTED_TARGET = 200
MONEY_RESET_HOUR = 17                  # the tree's money total starts again from 0 at this hour (0-23, local time) every day
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

# travel_to_trade_hub() and leave_trade_hub(): the clicks of a human's run (the backpack opens with the key; the Trading Hub Phone is the first of the Toys' Special row).
TRADE_HUB_TOYS_TAB_POS = (762, 855)
TRADE_HUB_PHONE_POS = (895, 738)
TRADE_HUB_EQUIP_POS = (999, 840)
TRADE_HUB_USE_POS = (1217, 521)            # anywhere in the world: a held toy is used by a click
TRADE_HUB_TRAVEL_POS = (1040, 595)         # the phone's TRAVEL TO HUB
TRADE_HUB_YES_POS = (1050, 665)            # "Travel to the Trade Hub?"
TRADE_HUB_HOME_POS = (1372, 97)            # the GO HOME button at the top of the Trading Hub
TRADE_HUB_REGULAR_SERVER_POS = (917, 540)  # REGULAR SERVER in "You are in the Trading Hub. Go to a Regular Server?"

# leave_and_rejoin(), modelled on how Natro Macro (a Bee Swarm Simulator macro) reconnects: close
# Roblox properly (esc, l, enter, then kill any leftover process), start the game again through a
# roblox:// deeplink - to the private server whose link is saved in the GUI, else a public server -
# then wait in stages, checking the screen, instead of waiting a fixed time and hoping.
ROBLOX_PLACE_ID = 920587237
ROBLOX_PROCESS_NAMES = ("RobloxPlayerBeta.exe", "RobloxCrashHandler.exe")  # killed after leaving
REJOIN_LEAVE_KEY_GAP = 0.25        # between those keys (Natro uses the same)
REJOIN_MIN_LEAVE_HEIGHT = 500      # px; the L shortcut only works in a Roblox window at least this tall
REJOIN_CLOSE_WAIT = 5.0            # after closing, before relaunching (relaunching too soon gives Roblox error 264)
REJOIN_INTERVAL = 2 * 60 * 60      # while the loop runs, rejoin this often (seconds) even if nothing is wrong, to
                                   # refresh a client that has been up a long time; also gives stuck needs a fresh chance
NO_PROGRESS_REJOIN_INTERVAL = 20 * 60  # while the loop runs, rejoin if no need has been resolved for this long (seconds)
NO_DETECTION_RESPAWN_INTERVAL = 4 * 60  # while the loop runs, respawn if no need has been *detected* for this long (seconds); counts
                                       # separately from the line above (a respawn doesn't delay that rejoin), and repeats every this often
QUIET_LOG_INTERVAL = 5 * 60        # while no need is being resolved, log "quiet: no need resolved for N min" every this often (seconds)
RESOURCE_LOG_INTERVAL = 60.0       # how often the macro logs its own CPU and memory use while the loop runs (seconds)
REJOIN_MAX_ATTEMPTS = 3            # close -> launch -> wait cycles before giving up
REJOIN_WINDOW_TIMEOUT = 240.0      # most it waits for the Roblox window to appear after launching (Natro: 4 minutes,
                                   # as Roblox may be installing an update first)
REJOIN_LOAD_TIMEOUT = 180.0        # most it waits for the game's Play popup after that (Natro: 3 minutes)
REJOIN_POLL_INTERVAL = 1.0         # how often it looks at the screen while waiting
# The game has loaded once its green Play button is on screen around REJOIN_JOIN_POS.
REJOIN_JOIN_POS = (910, 817)       # the Play button - clicked once it has loaded
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
# The white screen that shows briefly on a respawn, teleport or change of location: when at least this fraction of
# the Roblox window is completely white (every channel at or above the level) for WHITE_SCREEN_REJOIN_AFTER seconds
# in a row, the game is rejoined (see detect_white_screen()). A normal scene, even a bright one, has far less.
WHITE_SCREEN_LEVEL = 250
WHITE_SCREEN_MIN_FRACTION = 0.6
WHITE_SCREEN_REJOIN_AFTER = 30.0
ROBLOX_CRASH_WINDOW_TITLE = "Roblox Crash"

# Optional: resume after Roblox loses focus instead of stopping (the "Resume after focus loss" checkbox).
# Gives up after this many focus losses in a row without a need being resolved in between, so it can't
# fight you for the window forever.
FOCUS_RESUME_MAX_IN_A_ROW = 5
STOP_CLICK_GRACE = 0.6    # after a focus loss, how long to wait for a [STOP] press that is still mid-click (pressing it takes the focus first)
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
# While the need strip is practically unchanged since the circle search last ran, that result is reused:
NEED_STRIP_CHANGED_LEVEL = 25           # a pixel counts as changed if any channel moved by more than this...
NEED_STRIP_MAX_CHANGED_FRACTION = 0.002 # ...and the strip as unchanged if fewer than this fraction of its pixels did
NEED_STRIP_MAX_REUSES = 10              # most checks in a row that reuse a result before the search runs again anyway
NEED_STRIP_MAX_EMPTY_REUSES = 2         # ...and when that result found no icon at all (a missed icon looks the same, so trust it less)
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
ICON_MATCH_RADIUS_FRACTION = 0.7   # only the disc this fraction of the icon crop's width across is compared: the crop's corners and
                                   # the ring's edge show the background, which on a bright scene is what used to break the match
ICON_CROP_RADIUS = 28              # px, icon radius that saved/compared crops are cut at (plus ICON_EXTRACT_PADDING)
ICON_SHIFT_TOLERANCE = 2           # px, how far off-center a detection may be and still match

# Button detection (middle band of screen)
BUTTON_BAND_Y = (0.40, 0.70)
BUTTON_BAND_X = (0.25, 0.75)
BUTTON_MIN_AREA = 150
BUTTON_MIN_CIRCULARITY = 0.65
BUTTON_MAX_COUNT = 5
BUTTON_NAMES = ["hungry", "thirsty", "dirty", "potty", "sleepy"]
BUTTON_SLOTS_X = (673, 752, 858, 998, 1219)    # where each of them is across the screen (x of the buttons of 12 scans, within 10 px of these) - for when one is not seen (a beam of light, the character)
BUTTON_SLOT_TOLERANCE = 35                     # px: a button this close to a slot is that one (the slots are 77 or more apart)
BUTTON_LOOSE_AREA_FACTOR = 0.7         # how much looser than BUTTON_MIN_AREA a candidate may be
BUTTON_MAX_AREA_FRACTION = 0.5         # reject a blob covering more than this fraction of the band
BUTTON_MIN_RADIUS = 5                  # reject a candidate smaller than this radius (px)
BUTTON_LOOSE_CIRCULARITY_FACTOR = 0.8  # how much looser than BUTTON_MIN_CIRCULARITY a candidate may be
BUTTON_PURPLE_DILATE_ITERATIONS = 3    # dilation used to build the outline search zone
BUTTON_PURPLE_ERODE_ITERATIONS = 1     # erosion used to build the outline search zone
BUTTON_OUTLINE_PADDING = 3             # px of padding around a candidate when scoring its white outline

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
GUI_CONSOLE_LEVEL_COLORS = {"DEBUG": "#4f9f4f", "INFO": GUI_CONSOLE_FG, "WARNING": "#ffb000", "ERROR": "#ff5050"}     # the console's text by log level (the debug lines dimmer)
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
