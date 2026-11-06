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

# The green of Adopt Me's buttons (a popup's Yes, NICE!, Okay, Play, the paycheck's Cash Out): every green button is found by this
# colour, within GREEN_BUTTON_TOLERANCE per channel (the popup's Yes by the exact colour) in case of compression/scaling.
GREEN_BUTTON_COLOR = (74, 198, 85)
GREEN_BUTTON_TOLERANCE = 3

SCREEN_CHANGE_SIZE = (80, 45)         # screen_changed() compares the two screens shrunk to this many pixels (grey)
TELEPORT_CHANGE = 25                  # mean grey-level difference (0-255, over the whole screen, shrunk) from before a teleport dialog's Yes that counts as a changed view
TELEPORT_WAIT_FOR_VIEW = 6.0          # seconds teleport_took() looks after each Yes: a white screen, or the whole view changing
TELEPORT_VIEW_POLL = 0.1
EXIT_STAY_POS = (869, 660)                                      # the red button at the left of a dialog ("Are you sure?"): Cancel, which stays (the trade phone's decline dialog has it in the same place)
HUB_POLL = 0.25                                                 # seconds between looks while the Trading Hub is waited to be gone and the regular server to be back
HUB_LEAVE_WAIT = 20.0                                           # seconds the Trading Hub is waited to be gone after leaving it...
HUB_LOAD_WAIT = 90.0                                            # ...and then the regular server to have loaded (the backpack button back)

# ============================================================================
# STRAY WINDOWS
# ============================================================================
# Windows that open by accident (a misclick) or on their own (the daily Star Rewards) and block everything: each is
# recognised by a crop of its title in STRAY_WINDOW_DIR (file name = the name here), searched for within
# "box", and closed by clicking "close_pos" - see dismiss_stray_windows(). The Trading Hub also throws up a
# "Go to the Trading Hub to edit listings!" popup, found by its green Okay button and clicked first.
STRAY_WINDOW_MATCH_THRESHOLD = 0.8
TEMPLATE_RECHECK_INTERVAL = 1.0   # seconds: load_templates() looks at a folder's files for changes at most this often
POPUP_GRAY_GATE = 0.5          # popup_found() looks for a crop in grey first (about 5 times faster than in colour) and does the colour search only when that scored at least this (0 turns the gate off)
STRAY_WINDOWS: dict[str, dict[str, Any]] = {
    "trading_hub": {"box": (580, 290, 1020, 400), "close_pos": (1278, 339)},
    # The Trading Hub's board of offers ("TRADING HUB" with a magnifier on a pink board: the offers made on the player's listings, "OFFER DECLINED" with a Thanks! button, Listing #1 and #2
    # with Modify and Remove): it came up over the world and /trades could not go on (trade_screen() knows it as "board" and closes it).
    "trade_board": {"box": (590, 300, 880, 390), "close_pos": (1282, 339)},
    "star_rewards": {"box": (640, 385, 1060, 475), "close_pos": (1209, 441)},      # the calendar view (the star icon opens it): closed by its red X
    # The Task Board (opened by a notification such as "Trading Hub: Your listing got an offer!", on its Alerts tab): its red X closes it. Only closed, for now.
    "task_board": {"box": (540, 300, 1000, 430), "close_pos": (1318, 369)},
    # The shop's ad that comes up over the game (a "Limited Time" ribbon over a product, a red X and a blue COOL button; the one seen was merch): its COOL closes it. Only the ribbon is the crop, as the product changes.
    "promo_limited_time": {"box": (560, 380, 1000, 480), "close_pos": (1085, 665)},
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
    # A trade request from another player ("<name> sent you a trade request" with Decline and Accept) and its "Trade request from <name> has expired!" with Okay: they come up while trading is not
    # set to No One. The words change with the name, so the request is known by its Decline button (the crop: the red button with its label - the teleport dialogs' red button says No) and the
    # expired one by the first line of its card, "Trade request" (the name wraps to the next line); each is closed by a button found by its colour and size (DIALOG_BUTTONS, as the card's
    # height follows its text): Decline - never Accept, which is green - or Okay (a third as wide again as Accept). A card whose button is not found is left alone.
    "trade_request": {"box": (700, 560, 1260, 800), "button": "decline"},
    "trade_request_expired": {"box": (700, 380, 1240, 560), "button": "okay"},
}
DIALOG_BUTTONS = {                              # (and the Task Board's wooden sign at the left edge - found by its wood brown, not by a crop: its background is the world - 100 x 60 to 110 x 80 on the captures; and the green NICE! of the Pet Pen's age-up screen: 270 x 67 on the capture)
    "task_board": {"box": (0, 480, 200, 680), "hsv": (((0, 100, 60), (22, 255, 230)),), "size": ((90, 125), (45, 90))},
    "nice":    {"box": (800, 970, 1120, 1070), "hsv": (((45, 110, 130), (80, 255, 255)),), "size": ((240, 300), (50, 80))},                              # the buttons of those cards (measured on a real frame: Decline 174 x 47 and Accept 174 x 49 side by side, Okay 360 x 49)
    "decline": {"box": (700, 540, 1260, 790), "hsv": (((170, 150, 150), (180, 255, 255)), ((0, 150, 150), (5, 255, 255))), "size": ((150, 210), (30, 60))},
    "okay":    {"box": (700, 540, 1260, 790), "hsv": (((45, 110, 130), (80, 255, 255)),), "size": ((300, 420), (30, 60))},
}

# Other things recognised by a crop in STRAY_WINDOW_DIR (file name = the name here) found within "box"
# (left, top, right, bottom) with a TM_CCOEFF_NORMED score of at least "threshold" - see popup_visible(). The
# stray windows above work the same way (their "threshold" is STRAY_WINDOW_MATCH_THRESHOLD) and "pet_focus"
# is also looked up there.
POPUP_TEMPLATES: dict[str, dict[str, Any]] = {
    # The paycheck popup's bank-card header ("BANK of Adopt Me!").
    "paycheck":        {"box": (740, 400, 1180, 490), "threshold": 0.8},
    # The sick need's Yes/No dialog.
    "sick_dialog":     {"box": (700, 400, 1220, 700), "threshold": 0.8},
    "doctor_prompt":   {"box": (880, 420, 1050, 580), "threshold": 0.8},
    # The normal backpack's "BACKPACK" title, and the expanded one's BACK button.
    "backpack_normal": {"box": (700, 600, 1000, 690), "threshold": 0.8},
    "backpack_back":   {"box": (700, 80, 1000, 200), "threshold": 0.8},
    # The "GO HOME" text under the button at the top of the Trading Hub island (the crop is the text alone: what is around the button is the island).
    "go_home":         {"box": (1280, 60, 1460, 140), "threshold": 0.8},
    # The purple backpack button at the bottom centre: the game's normal UI, there in the world and the Trading Hub and not on a loading screen.
    "backpack_button": {"box": (880, 950, 1040, 1075), "threshold": 0.8},
    # The "Pet Pen Ready" card of the Task Board's Alerts tab (its title line; the cards are listed one under the other, so it is looked for in the whole list).
    # The age-up screen's purple pill that says Friend (a pet of the pen has grown as far as it does: it is to be switched out; the crop is the word, the pill has a gold edge and a heart count). Four pills sit
    # over the four pets, two above and two below; the one in the top left was the one in the capture.
    "pet_friend":      {"box": (560, 60, 1360, 620), "threshold": 0.8},
    # The Pet Pen's own screen (opened at the pen in the yard): its title, the "Full grown!" sign a pet of it carries when it has grown as far as it does, the green Add Pet tile of the first free place, and the
    # small BACKPACK that Add Pet opens (a panel over the pen, with the pets in a grid of four columns).
    "pet_pen_title":      {"box": (700, 240, 1000, 360), "threshold": 0.8},
    "pet_pen_full_grown": {"box": (760, 330, 1330, 660), "threshold": 0.8},
    "pet_pen_add":        {"box": (760, 330, 1330, 660), "threshold": 0.8},
    "pet_backpack_title": {"box": (700, 600, 1000, 700), "threshold": 0.8},
    "pet_pen_ready":   {"box": (836, 430, 1310, 720), "threshold": 0.8},
    # The Trading Hub Phone's screens (trades_tour(), decline_offer(); crops of tests/trades/): the phone's TRADE LISTINGS button, the "TRADING HUB" menu's title, the
    # "MY LISTINGS" title and the "1/2" / "2/2" of its "LISTING n/2", the "OFFERS" title (also there, dimmed, over an offer opened on its own), that offer's red DECLINE
    # button, the "Are you sure you want to decline this offer?" dialog, and the phone's icon in the hotbar (it is equipped: a click in the world uses it).
    "trade_phone_home":   {"box": (700, 300, 1250, 560), "threshold": 0.85},
    "trade_menu":         {"box": (640, 300, 1000, 390), "threshold": 0.85},
    "my_listings":        {"box": (640, 300, 1000, 390), "threshold": 0.85},
    "listing_1_of_2":     {"box": (1060, 300, 1200, 390), "threshold": 0.85},
    "listing_2_of_2":     {"box": (1060, 300, 1200, 390), "threshold": 0.85},
    "offers_title":       {"box": (640, 300, 1000, 390), "threshold": 0.85},
    "decline_button":     {"box": (680, 660, 1000, 760), "threshold": 0.85},
    "decline_dialog":     {"box": (760, 430, 1160, 620), "threshold": 0.85},
    "trade_phone_hotbar": {"box": (940, 950, 1100, 1075), "threshold": 0.85},
    # The backpack's card of the item clicked, with "Trading Hub Phone" (the badge next to it in the Toys tab has another name): found, it says the phone is the item that is selected,
    # and where its Equip button is (TRADES_EQUIP_OFFSET from the middle of the name).
    # The phone's picture in the first slot of the Toys tab's Special row: there once the search has filtered the list (before that the badge is first and it is the second).
    "search_placeholder": {"box": (990, 620, 1180, 665), "threshold": 0.8},   # ("Enter Search...": in the backpack's search box while it is empty and has no focus)
    "phone_item":         {"box": (640, 400, 1290, 960), "threshold": 0.8},   # (the phone's picture anywhere in the Toys tab's grid: the primary way to find it, clicked where it is)
    # Setup's states (setup_game(): crops of tests/setup/, real frames of a run): the house's lock button as Lock and as Unlock, the backpack's FILTERS panel and its sort button as Default and as Favorites,
    # Favorites in the sort menu, the game's settings, the Interaction page's label and menu entry, the Trading setting as No One and No One in its list, and the pets tab's Alicorn and its card's Unequip.
    "house_lock":                  {"box": (1040, 36, 1180, 84), "threshold": 0.85},
    "house_unlock":                {"box": (1040, 36, 1180, 84), "threshold": 0.85},
    "filter_panel":                {"box": (780, 570, 1000, 650), "threshold": 0.85},
    "filter_sort_default":         {"box": (960, 650, 1100, 710), "threshold": 0.85},
    "filter_sort_favorites":       {"box": (960, 650, 1100, 710), "threshold": 0.85},
    "filter_option_favorites":     {"box": (950, 720, 1110, 960), "threshold": 0.85},       # (the sort menu's list: a longer one - 4 repicks in a 2-hour run did not find Favorites in the old box)
    "settings_title":              {"box": (780, 300, 980, 370), "threshold": 0.85},
    "settings_page_interaction":   {"box": (940, 335, 1110, 385), "threshold": 0.85},
    "settings_option_interaction": {"box": (940, 400, 1110, 520), "threshold": 0.85},
    "trading_no_one":              {"box": (990, 525, 1090, 570), "threshold": 0.85},
    "trading_option_no_one":       {"box": (960, 570, 1100, 640), "threshold": 0.85},
    "pet_alicorn":                 {"box": (900, 640, 1230, 900), "threshold": 0.8},
    "age_egg":                     {"box": (800, 640, 1130, 880), "threshold": 0.85},       # the Age rows of the backpack's FILTERS panel: the text of Egg and of Full Grown (their boxes are found left of it)
    "age_full_grown":              {"box": (800, 640, 1130, 880), "threshold": 0.85},
    "age_full_grown_pen":          {"box": (800, 640, 1130, 880), "threshold": 0.85},       # the same row in the pen's small backpack, whose panel has no blue line under the text (the first crop was cut in the big backpack's)
    "pet_unequip":                 {"box": (980, 780, 1215, 840), "threshold": 0.85},
    "phone_icon":         {"box": (850, 690, 950, 790), "threshold": 0.8},
    "phone_card":         {"box": (850, 700, 1250, 900), "threshold": 0.85},   # (the box is where the card's name can be: with the item in the first slot or the second)
}
# The sick need's own "Ask Doctor Heart to heal your family?" Yes/No dialog (POPUP_TEMPLATES["sick_dialog"]); it is the sick handler's to answer (SICK_FINAL_CLICK_POS is its Yes).
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
SETUP_UI_WAIT = 5.0               # setup: seconds a button or window is waited for before it is said not to be there
SETUP_LOOK_WAIT = 1.0             # setup: seconds a state that is probably there already (a label that says what it should) is looked for
SETUP_POLL = 0.25                 # setup: seconds between looks while waiting
SETUP_STRAY_TRIES = 3             # setup closes up to this many stray windows before it starts
STRAY_CLOSE_SETTLE = 1.0          # after a click, before looking again
# While a need handler (or setup) is running, the per-cycle checks (unscrew()) also run, this often.
UNSCREW_TASK_INTERVAL = 4.0

# ============================================================================
# PATHS
# ============================================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STRAY_WINDOW_DIR = os.path.join(SCRIPT_DIR, "ref", "popups")
NEEDS_DIR = os.path.join(SCRIPT_DIR, "ref", "needs")      # the learned need icons - the one folder in ref/ the macro also writes to (see prompt_rename_need())
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
COMMANDS = ("start", "stop", "exit", "set", "rejoin", "setup", "refresh_debug", "trade_hub", "leave_trade_hub", "inventory", "sloop", "focus", "blackout", "test", "reset_config", "server_link", "trades", "decline", "capture", "recenter", "report")     # what it may hold: start the loop, stop it, stop and close the window, change a switch (see NEED_NAMES, OPTION_SWITCHES), leave and rejoin the game, run Setup, empty the debug folder, travel to / leave the Trading Hub, take pictures of the backpack's pets tab, or run Setup and then the loop (`sloop`), bring the Roblox window to the front (`focus`), blank / show the screen (`blackout`), run one need handler on its own or every teleport one (`test`, what the Debug tab's Test buttons do), reset the config (`reset_config`), save the private server link (`server_link`), take pictures of the offers on the two trade listings (`trades`), decline one of them (`decline`), or start the input capture (the capture hotkey's) and stop it again (`capture`), or close what is open and respawn (`recenter`), or draw the chart of a run (`report`, the newest in the log or the one named)
NEED_NAMES = ("catch", "pet", "choose", "ride", "walk", "hungry", "thirsty", "dirty", "potty", "sleepy", "bored", "beach", "school", "cafe", "salon", "pizza", "camping", "sick")   # the Debug tab's need switches (the keys of main.py's NEED_HANDLER_CLASSES)
OPTION_SWITCHES = {"side_quest": "side_quest_enabled", "resume_on_focus_loss": "resume_on_focus_loss", "record_next_run": "record_next_run",
                   "use_private_server": "use_private_server", "pet_pen": "pet_pen_enabled", "blackout_hotkey": "blackout_hotkey_enabled", "new_need_hotkey": "new_need_hotkey_enabled", "level_growing_pet": "level_growing_pet", "keep_moments": "keep_moments"}   # the other switches a command may change: name -> the game config's key ("show_debug_lines" too, which is only in the window)
SHOW_DEBUG_LINES_OPTION = "show_debug_lines"
TEST_ALL_TELEPORTS = "teleports"                       # the `test` command's handler for the Debug tab's "Test every teleport handler" (the others are NEED_NAMES)
COMMAND_POLL_MS = 1000                                      # how often the macro looks for one
COMMAND_MAX_AGE = 30                                        # seconds: an older command is ignored (left over from before the macro was started)
RESUME_LOOP_AFTER = ("setup",)                              # the outside actions after which a loop that was running (and was stopped for them) is started again once they end
REJOIN_STOP_POLL_MS = 500                                   # a rejoin asked for while the loop runs: how often to see whether it has stopped
REJOIN_STOP_WAIT_MS = 15000                                 # and how long to wait for that before giving up
START_LOOP_DELAY_MS = 1500                                  # after the window is up, before the --loop argument starts the loop
BOT_COMMAND_ACK_WAIT = 30.0                                 # seconds the bot waits for the macro to take a command (it looks every COMMAND_POLL_MS) before it withdraws it and says so
BOT_REPORT_WAIT = 60.0                                      # seconds the bot waits for the macro to draw a run chart (/report)
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
BOT_CAPTURE_WAIT = 20.0                                     # seconds the bot waits, after /capture, for the macro to say whether a capture started or stopped
BOT_RVIEW_SECONDS = 30                                     # /rview: how long the rolling view runs when it is not told
BOT_RVIEW_MAX_SECONDS = 900                                 # the longest it can be asked to run, in seconds, and the most "until done" runs
BOT_RVIEW_START_GRACE = 15.0                                # seconds "until done" waits for the macro to show that it is busy, before an idle macro means that what was asked for is over
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
# What the Discord bot's /pull can copy into OneDrive: the folders of debug/ (not the game config, which holds the private server link).
MOMENTS_DIR = os.path.join(DEBUG_DIR, "moments")     # the capture of a run's moments (see start_moments()): a folder per run
PULL_SOURCES = {"failures": FAILURE_DIR, "logs": LOGS_DIR, "runs": RUNS_DIR, "captures": CAPTURES_DIR, "recordings": RECORDINGS_DIR, "moments": MOMENTS_DIR, "screenshots": SCREENSHOTS_DIR}
RUN_CAPTURE_BURST = 4.0
RUN_CAPTURE_PROTECTED = 25
MOMENTS_MIN_RUN = 60 * 60              # seconds a run lasts for its moments to be kept (a shorter run leaves nothing)
MOMENTS_ZIPS = 20                      # the most zips (of CAPTURE_ZIP_MB) a run's moments come to: the frames are thinned out to fit
MOMENTS_FRAME_INTERVAL = 20.0          # seconds between two looks (a mark makes every CAPTURE_FRAME_INTERVAL look save a frame for RUN_CAPTURE_BURST seconds)
MOMENTS_PROTECTED = 100                # the most frames that marks and the tail keep from being thinned out (the zips hold about 200)
MOMENTS_MAX_HOURS = 48                 # a run's capture stops by itself after this long
MOMENTS_MARK_PREFIXES = ("FAILURE", "scheduled rejoin", "white screen", "recovery complete", "disabled:", "quiet:")     # the run-log lines that are moments worth keeping frames of
MOMENTS_MARK_GAP = 60.0                # at most one mark this often (a failure repeated every cycle is one moment)
MOMENTS_TAIL = 90.0                    # seconds at the end of a run whose frames are kept
MOMENTS_LOG_MB = 8                     # the most of the macro's output kept with a run's moments (the last part of it)
MOMENTS_RUN_LOG_MB = 2                 # the most of the run's own log kept (its last part)
MOMENTS_FAILURE_SHOTS = 30             # the most failure screenshots (JPEG, about 0.3 MB each) kept with a run's moments
MOMENTS_RESERVE_MB = 16                # room left in the first zip for those files (they are deflated, so they take less)
MOMENTS_KEEP = 3                       # the moments of this many runs are kept (the oldest folders go)
CAPTURE_ZIP_MB = 25                       # a stopped capture is also split into zips of at most this size (chat attachments are limited to 30 MB)







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

# Timing (seconds)
RESPAWN_KEY_DURATION = 0.05    # how long each respawn key is held
RECENTER_CLOSE_TRIES = 4           # /recenter: windows closed one after the other (a stray window can have another behind it), at most this many
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
BLACKOUT_VIRTUAL_DISPLAY = True   # blank by switching to an installed virtual display (the PC's own panel off, Win+P's Second screen only) rather than a black window; without one, the window is used
VIRTUAL_DISPLAY_NAMES = ("virtual display", "mttvdd", "iddsample")   # what the name of a display adapter has in it when it is a virtual display (lower case; the Virtual Display Driver's is "Virtual Display Driver by MTT": not read off a real PC)
DISPLAY_ADAPTERS_MAX = 32     # the display adapters looked through for one of those names
DISPLAY_SWITCH_TIMEOUT = 15.0 # seconds DisplaySwitch.exe is given
BLACKOUT_DISPLAY_SETTLE = 4.0 # seconds after a switch of display before the screen is looked at (Windows moves its windows over)
BLACKOUT_FLAT_LEVEL = 8       # a screenshot whose brightest and darkest pixels are closer than this is one flat colour: black, or no picture
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
# The pet's name bar ("Friend" on purple, a heart and the number on white, a gold outline that is not always there; its words change) shows over a pet that is standing still, where
# focus_pet()'s search by movement finds nothing: the purple part of it is found by hue (HSV, OpenCV's: 129 / 148 / 255 and 131 / 181 / 255 on a real frame), a wide short blob
# (PET_BAR_ASPECT) of PET_BAR_AREA px that has white right of it (at least PET_BAR_WHITE_FRACTION of that strip at least PET_BAR_WHITE_LEVEL in every channel), and the pet is
# PET_BAR_BELOW_STEPS bar heights under its bottom edge, at the join of the purple and the white (measured: bar 101 px of purple by 38, the pet's body about 4 heights below; on a lying pet
# near the camera, a bar 115 by 41 with the pet's body 1.5 heights below it, and only 0.28 of the strip at the bar's right white, where a standing pet's had 0.5).
PET_BAR_HSV = ((122, 110, 200), (136, 215, 255))
PET_BAR_AREA = (500, 20000)
PET_BAR_ASPECT = (1.8, 4.0)
PET_BAR_WHITE_LEVEL = 225
PET_BAR_WHITE_FRACTION = 0.15
PET_PILL_HSV = (((122, 110, 200), (138, 215, 255)), ((126, 140, 60), (138, 215, 125)))     # the name pill as a whole: its filled part is bright purple and the rest dark purple (the pet's age progress), the text white over both
PET_PILL_MIN_Y = 130                      # px: pills higher than this are the game's buttons (Edit House, My Homes, Lock), not a pet's
PET_PILL_CLOSE = 11                       # px: the text's white strokes are closed over so that the pill is one blob
PET_PILL_AREA = (800, 20000)
PET_PILL_ASPECT = (2.0, 7.0)              # width / height
PET_PILL_FILL = 0.6                       # of its box the blob fills (a pill nearly does)
PET_FOCUS_TRIES = 3                       # tries at focusing the pet (the clicks, then the E key that opens its menu when the player is by it) before choose gives up
PET_FOCUS_VIEW_WAIT = 2.5                 # seconds the view is waited for after each try (the first three chooses of a run clicked the pet and looked at once: the view came 20 s later)
PET_FOCUS_POLL = 0.25
CHOOSE_FIND_WAIT = 3.0                    # seconds the button is looked for in the view once it is up (it moves in)
PET_BAR_BELOW_STEPS = (3.5, 1.5, 2.5)                          # bar heights under the bar's bottom edge to click, in turn until the pet is focused
# The choose button when no pixel is CHOOSE_BUTTON_COLOR (twice in run 34 it was in plain sight: a purple-to-magenta disc with a "?" and, not always, a white outline, under the
# focused pet): the biggest roundish blob of this hue (OpenCV's HSV: purple 141, magenta 154; the pink BACK button is 167, the backpack's purple is 130 and paler), at least
# CHOOSE_DISC_ROUNDNESS of its enclosing circle (the "?" is cut out of it), of CHOOSE_DISC_AREA px (about 180 px across, as the button is under the focused pet; the choose need's icon in the need bar is the same hue and 56 px across, 1801 px: not it, and a range that took it clicked the bar), within CHOOSE_DISC_SQUARENESS of square. The hue is measured on a real frame (a failure in run 57, tests/popups/choose_disc.png): the exact colour above is not in the
# disc then at all (the nearest pixel is 4 off in a channel: the disc's shading moves).
CHOOSE_DISC_HSV = ((136, 180, 170), (158, 255, 255))
CHOOSE_DISC_AREA = (6000, 45000)
CHOOSE_DISC_SQUARENESS = 1.3
CHOOSE_DISC_ROUNDNESS = 0.55
CHOOSE_DISC_CLOSE = 5                                          # px: the closing that joins the disc round the "?" and its outline

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
# The frame is at the panel's two sides: at least BACKPACK_NORMAL_MIN_SIDE_PIXELS of that purple in each of the BACKPACK_NORMAL_SIDE_WIDTH columns at the left and at
# the right of the panel box. The big "Friend" pill in the middle of the screen (shown with the cafe's name banner) is the same purple and white, and has
# 1040 purple pixels in the box and a quarter of the white box white: it was taken for the backpack ("still open after pressing the key twice") and stopped a run. It has 5 at
# its left; the real panel of tests/popups/backpack_open.png has 70 and 31.
BACKPACK_NORMAL_SIDE_WIDTH = 80
BACKPACK_NORMAL_MIN_SIDE_PIXELS = 20
BACKPACK_NORMAL_WHITE_BOX = (850, 690, 1190, 920)
BACKPACK_NORMAL_WHITE_LEVEL = 240
BACKPACK_NORMAL_MIN_WHITE_FRACTION = 0.2
# The inventory tour (inventory_tour(), the bot's /inventory): the backpack's pets tab, in the EXPANDED backpack (opened with the orange button left of the
# settings one in the normal backpack's header; clicking the selected tab again does nothing), scrolled
# a step at a time with a picture of its panel after each. First run: the backpack expanded, a picture was made, and the wheel moved nothing (it was given 1/120 of
# a notch a step: see WHEEL_NOTCH), so the tour ended after that one picture; the window of the macro was in the picture.
# The trade listings (trades_tour(), the bot's /trades, and decline_offer(), /decline), after a human's run through the Trading Hub Phone (tests/trades/): the phone is used
# by a click in the world once it is equipped, its TRADE LISTINGS button, VIEW MY LISTINGS, the listing's OFFERS button, and then the list of offers: a row each (the green
# ACCEPT button, the red DECLINE, what you and they put up), a few rows tall, scrolled with the wheel. A row clicked opens that offer on its own, with a DECLINE button that asks "Are you sure?".
TRADES_DIR = os.path.join(DEBUG_DIR, "trades")                  # L1_01.png, L1_02.png ... (the list of the first listing at each scroll that showed offers not seen before, the offers numbered on them), L2_01.png ... for the second
TRADES_ROWS_DIR = os.path.join(TRADES_DIR, "rows")              # L1_01.png ...: a picture of each numbered offer's row, which decline_offer() looks for in the list (so that it declines that one, whatever the list has become)
TRADES_DONE_PATH = os.path.join(TRADES_DIR, "done.json")        # written when a tour ends: {"listings": {"1": {"offers": n, "pictures": n, "ended": why}, ...}, "problem": ""}
TRADES_DECLINED_PATH = os.path.join(TRADES_DIR, "declined.png")  # the offer that was declined, as it showed on its own
TRADES_DECLINE_DONE_PATH = os.path.join(TRADES_DIR, "decline.json")   # written when a decline ends: {"listing": n, "offer": n, "declined": true or false, "why": ...}
TRADES_LISTINGS = (1, 2)                                        # the listings looked at (the game allows two)
TRADES_ALL_OFFERS = "*"                                        # /decline's offer for every offer of the listing
TRADES_DECLINE_MAX_WAIT = 8.0                                   # seconds a list is waited for after a decline, to have the next offer at its top (the offer declined shows for a while with no ACCEPT)
TRADES_TOP_TOLERANCE = 12                                       # px a first row may be below where the list's first row was and still be at the top
TRADES_MAX_OFFERS = 30                                          # the most offers of a listing /decline can be asked for
TRADES_SEARCH_POS = (1070, 642)                                 # the backpack's search box ("Enter Search...")
TRADES_SEARCH_TEXT = "phone"                                    # typed in it in the Toys tab: the phone is then the first item (TRADE_HUB_PHONE_POS), where the slot-by-slot search had the badge there
TRADES_SEARCH_MAX_WAIT = 6.0                                    # seconds after the text is typed, for the list to be filtered (it was about 2 s late in a capture: the first item clicked sooner was the badge)
TRADES_EQUIP_TRIES = 2                                          # the search and the first item are tried this often until the card that comes up is the phone's
TRADES_EQUIP_OFFSET = (-38, 75)                                 # from the middle of the card's name to its Equip button (measured on a frame of the phone's card)
TRADES_UNFOCUS_POS = (745, 681)                                # a click here (the tab's name) takes the focus out of the search box, so that the backpack key is not typed into it
FOCUS_FORCE_TRIES = 2                                           # when Windows refuses activate(): force_foreground()'s ways (an Alt tap and activate again, then minimize and restore)
TYPE_KEY_DELAY = 0.08                                          # seconds after each key of type_keys()
TRADES_PHONE_LISTINGS_POS = (870, 430)                          # the phone's TRADE LISTINGS
TRADES_MENU_VIEW_POS = (855, 650)                               # the menu's VIEW MY LISTINGS
TRADES_NEXT_POS = (1290, 530)                                   # the arrow at the right of the listing (with two listings, it goes from one to the other)
TRADES_OFFERS_POS = (755, 702)                                  # the listing's OFFERS
TRADES_BACK_POS = (1210, 342)                                   # the orange arrow at the top: a screen back
TRADES_CLOSE_POS = (1277, 339)                                  # the red X at the top: the phone away
TRADES_ROW_CLICK_DY = 12                                        # an offer is opened by a click at TRADES_ROW_CLICK_X, this far below its ACCEPT button's middle
TRADES_ROW_CLICK_X = 1069
TRADES_DETAIL_DECLINE_POS = (835, 702)                          # DECLINE of an offer on its own
TRADES_DECLINE_YES_POS = (1048, 660)                            # Yes of the dialog
TRADES_BUTTON_BOX = (640, 395, 840, 735)                        # where the red DECLINE buttons of the rows are (and the list's top and bottom: a row must be whole in it). They are found, not the green ACCEPT buttons: an "Exact Match" offer has a green row, in which ACCEPT is lost
TRADES_BUTTON_HSV = (((170, 150, 150), (180, 255, 255)), ((0, 150, 150), (5, 255, 255)))     # their colour (OpenCV's HSV; red is at both ends of the hue)
TRADES_BUTTON_SIZE = ((150, 230), (22, 55))                     # (width, height) range in px of one
TRADES_DECLINE_DY = 40                                          # px from the middle of a row's ACCEPT button, which is where a row is placed, to the middle of its DECLINE
TRADES_ROW_BOX = (640, -52, 1262, 78)                           # a row, from an ACCEPT button's middle: (left, top, right, bottom), top and bottom as offsets
TRADES_ROW_MATCH = 0.93                                         # TM_CCOEFF_NORMED score of a row that is the one that was photographed (/decline checks it)
TRADES_ROW_PITCH = 136                                          # px from one offer row to the next: a step of the wheel is shorter, and the rows are told apart by their place in the list
TRADES_PANEL_BOX = (570, 270, 1350, 780)                        # the phone's screen, the picture of each step
TRADES_SCROLL_POS = (1274, 560)                                 # the mouse is on the list's scroll bar here while the wheel turns (over a row, the description of the item under it covers the rows)
TRADES_SCROLL_CLICKS = 1                                        # notches of the wheel per step of a tour: one is about 95 px in the game, under the TRADES_ROW_PITCH of a row, so that no offer is skipped
TRADES_SCROLL_MAX_WAIT = 3.0                                    # seconds the list is waited for to stop gliding after a step (grab_still()), before the picture
TRADES_SAME_LEVEL = 1.0                                         # two pictures of the panel whose mean difference is under this are the same: the list does not move any more (and at a step, it is at its end)
TRADES_MAX_STEPS = 60                                           # steps of one listing's tour at the most
TRADES_SCREEN_WAIT = 4.0                                        # seconds a screen of the phone is waited for after the click that opens it
TRADES_POLL = 0.25
BOT_TRADES_WAIT = 300.0                                         # seconds the bot waits, after /trades or /decline, for the macro to end (TRADES_DONE_PATH, TRADES_DECLINE_DONE_PATH)
BOT_TRADES_POLL = 1.0
INVENTORY_DIR = os.path.join(DEBUG_DIR, "inventory")            # the pictures (001.png, 002.png ...), emptied at the start of each tour
INVENTORY_DONE_PATH = os.path.join(INVENTORY_DIR, "done.json")  # written when the tour ends, however it ends: {"pictures": N, "ended": why}
INVENTORY_PETS_TAB_POS = (817, 713)
EQUIP_BUTTON_BOX = (900, 780, 1215, 930)                        # where the Equip button of the backpack's card is (find_equip_button())...
EQUIP_BUTTON_SIZE = ((100, 200), (25, 45))                      # ...its (width, height) range in px: 148 x 35 on a real frame
INVENTORY_EXPAND_POS = (938, 642)                               # the orange button left of the settings one, in the normal backpack's header (measured on tests/popups/backpack_open.png)
INVENTORY_LOAD_WAIT = 3.0                                       # seconds after the backpack has expanded, for its items to load before the first picture (blank cells in it, otherwise)
INVENTORY_EXPAND_SETTLE = 1.0                                  # seconds after it is clicked, for the backpack to expand
INVENTORY_PANEL_BOX = (1090, 0, 1920, 1080)                    # (left, top, right, bottom) of each picture: the right side of the screen, where the expanded backpack is (the first picture, of (1090, 36, 1840, 1050), showed its right-hand column cut off)
INVENTORY_SCROLL_POS = (1907, 560)                              # the mouse is over the list's scroll bar (the thin white bar at the list's right edge, 817 px into INVENTORY_PANEL_BOX in a picture) while the wheel turns: over an item, its description opens and covers the items round it
WHEEL_NOTCH = 120                                               # what a turn of the mouse wheel by one notch is, in the units Windows takes (pyautogui.scroll() passes them on as they are: 10 is a twelfth of a notch)
INVENTORY_SCROLL_CLICKS = 10                                    # notches of the wheel per step when how far a notch goes could not be measured (300 went from the first rows of the pets to the end of the list in one step)
INVENTORY_PROBE_CLICKS = 1                                      # the first step is this many notches, and the two pictures say how many pixels the list moved: how many notches make a step of INVENTORY_STEP_PIXELS
INVENTORY_STEP_PIXELS = 700                                     # a step moves the list this far (the list is about 890 px tall: the rows at the bottom of one picture are at the top of the next)
INVENTORY_SHIFT_BAND = (0.55, 0.65)                             # the band of the picture (its share of the height) that is looked for in the next one: the list moved up, so it is where the band went...
INVENTORY_SHIFT_MATCH = 0.8                                     # ...if the match is at least this good (TM_CCOEFF_NORMED); looked for in a strip at the left and at the right of the panel, the one that matches better (a tooltip hides one)
INVENTORY_SHIFT_MIN_SPREAD = 4.0                                # a band whose grey levels hardly differ (the standard deviation) cannot be found again
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
TELEPORT_YES_TRIES = 2            # the teleport dialog's Yes is clicked this often at most, until the view changes or the screen goes white 
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
# override it with `helicopter=True/False`. No entry turns it on now (the event's did: scripts/Halloween2026.py).
HELICOPTER_REQUIRED = False
HELICOPTER_FORWARD_DURATION = 1.0  # step forward this long after teleporting, before equipping
HELICOPTER_HOLD_DURATION = 4.5     # then hold KEYS["helicopter"] this long (it was 0.5 s less, and the helicopter could be caught on a tree)

# Every key the macro presses, by what it is for (code says cfg.KEYS["forward"], not "w"): the game's own bindings, then the macro's hotkeys.
KEYS = {
    "forward": "w", "left": "a", "back": "s", "right": "d",   # walking
    "jump": "space",
    "backpack": "b",
    "mount": "e",                  # mounting a vehicle...
    "interact": "e",               # ...and collecting/harvesting (lure_collect(), tree_collect()): the same physical key, named for each use
    "zoom_in": "i",
    "helicopter": "r",             # held to fly the equipped helicopter (also held briefly by ride)
    "foreground_tap": "alt",       # tapped when Windows refuses to bring Roblox to the front: a program that was just given the keys may then ask for the foreground (force_foreground())
}
KEY_SEQUENCES = {                  # keys pressed one after the other, each held KEY_STEP_GAP-ish (RESPAWN_KEY_DURATION, REJOIN_LEAVE_KEY_GAP)
    "respawn": ("esc", "r", "enter"),
    "leave_game": ("esc", "l", "enter"),
}
HOTKEYS = {                        # the macro's own keys, each a letter or digit (or a character that is a key of its own on the keyboard layout), read in any window (the last three only while Roblox is focused)
    "stop": "p",                   # stops a running macro
    "capture": "m",                # starts or stops an input capture
    "blackout": "h",               # blanks the screen with a black window (and again, to take it away)
    "new_need": "ú",               # looks at the need bar and asks the name of an icon on it that no saved need matches (taught for the next look; same layout lookup as the screenshot key)
    "screenshot": "ô",             # saves a picture of the whole screen in SCREENSHOTS_DIR (not a letter of the alphabet: its key is looked up on the keyboard layout, see hotkey_vk())
}
MOVE_KEYS = tuple(KEYS[name] for name in ("forward", "left", "back", "right"))
HELD_KEYS = tuple(dict.fromkeys([*KEYS.values(), *(key for keys in KEY_SEQUENCES.values() for key in keys)]))   # every key the macro can hold down: let go of when it ends, at a stop, or when it is killed


# Needs that teleport somewhere and walk (see TeleportWalkNeedHandler), then
# wait for the need to clear. Each entry: where to teleport, the
# (key, seconds) holds to perform in order, optionally a `final_click` (a need with one has a doctor's dialog to answer after the last hold:
# its prompt is clicked, the dialog waited for and this is its Yes - sick; see TeleportWalkNeedHandler._open_dialog()), optionally `helicopter` (above) and optionally
# `max_wait` (how long to wait for the need to clear afterwards; default NEED_GONE_MAX_WAIT). One table: the nursery's routes (bored, beach, school, camping, sick); the
# event's own routes are in scripts/Halloween2026.py.
TELEPORT_WALK_NEEDS: dict[str, dict[str, Any]] = {
    "bored":   dict(teleport_pos=TELEPORT_PETS_TAB_POS,     steps=((KEYS["forward"], 16.0), (KEYS["left"], 10.0))),
    "beach":   dict(teleport_pos=TELEPORT_PETS_TAB_POS,     steps=((KEYS["left"], 27.0),)),
    "school":  dict(teleport_pos=TELEPORT_PETS_TAB_POS,     steps=((KEYS["forward"], 1.2), (KEYS["left"], 10.0))),
    "cafe":    dict(teleport_pos=TELEPORT_VEHICLES_TAB_POS, steps=((KEYS["left"], 3.2), (KEYS["back"], 5), (KEYS["left"], 0.2), (KEYS["back"], 3))),
    "salon":   dict(teleport_pos=TELEPORT_VEHICLES_TAB_POS, steps=((KEYS["left"], 2.5), (KEYS["forward"], 10.0))),
    "pizza":   dict(teleport_pos=TELEPORT_VEHICLES_TAB_POS, steps=((KEYS["forward"], 3.0), (KEYS["left"], 7.2), (KEYS["back"], 4.0))),
    "camping": dict(teleport_pos=TELEPORT_PETS_TAB_POS,     steps=((KEYS["right"], 7), (KEYS["back"], 18)), helicopter=True),
    "sick":    dict(teleport_pos=TELEPORT_PETS_TAB_POS,     steps=((KEYS["forward"], 2.5), (KEYS["right"], 3.0), (KEYS["forward"], 0.8)),
                    final_click=SICK_FINAL_CLICK_POS),
}

WATCH_POLL_INTERVAL = 0.2           # how often the screen is looked at while waiting for the game's "Only Pets..." answer to a need button click
DISABLED_NEEDS_REJOIN_THRESHOLD = 4  # this many needs disabled for the run (see NEED_STUCK_CHECKS) means something is
                                     # badly wrong: rejoin the game, run setup, and give every need a fresh chance
NEED_NEVER_WORKED_FAILS = 3        # a need that was not cleared this many times in a run, and never was, is disabled for it (camping: a wait of 75 s four times and no success in a 2 hour run)
NEED_STUCK_CHECKS = 5            # an enabled need still on screen after this many attempts in a row is
                                  # disabled for the rest of the run - see record_detection()
NEED_GONE_MAX_WAIT = 75.0        # most any need is waited on to clear: wait_until_need_gone() (the teleport-walk needs and the button
                                  # needs; they time out by themselves in the game, and camping was seen clearing ~66s after its walk),
                                  # and the total duration of the walk/ride actions that end early once it has cleared
NEED_GONE_POLL_INTERVAL = 3.5    # how often to check for the icon during that wait (and, while walking, from the watcher thread)
# The icon of a need that is being done pulses: its saturation swings from the normal level towards white and back about every 2.5 s, deeper as it goes, until the need clears (a capture of
# dirty, thirsty and sleepy: the other icons stay constant). wait_until_need_gone() watches for it - see PulseWatch.
PULSE_NEEDS = ("hungry", "thirsty", "dirty", "potty", "sleepy", "bored", "beach", "school", "cafe", "salon")   # the needs whose pulse has been seen (a need not in here is only watched and logged, never judged by it)
PULSE_POLL = 0.5                 # seconds between looks at the icon while waiting for a need to clear (the pulse is about 2.5 s long)
PULSE_MIN_SWING = 10             # the saturation (0-255) of the icon moves this much between its highest and lowest look: a pulse (the same icon idle varies by 1-2)
PULSE_START_WAIT = 12.0          # seconds from the click within which the pulse must show (it showed 1 to 5.5 s after in a 2-hour run: cafe slowest, the button needs 1 to 3 s)
PULSE_DISC_MARGIN = 6            # px: the icon's saturation is taken inside its crop's circle this much smaller than the crop, to leave the ring and what is behind it out
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
# The Pet Pen: its "ready to age up" notice is a card on the Task Board (the button at the left edge of the screen, with a red badge while there is an alert); the card opens the age-up
# screen, which ends with a green NICE! - see pet_pen().
TASK_BOARD_BADGE_BOX = (-62, -28, 78, 92) # where its red alert badge is (a speech bubble with a "!"), from the middle of the sign (found by find_task_board_button())
TASK_BOARD_BADGE_HSV = (((0, 170, 170), (8, 255, 255)), ((172, 170, 170), (180, 255, 255)))
TASK_BOARD_BADGE_MIN_PIXELS = 500         # red pixels there for a badge (about 1000 with it, under 100 without)
PET_PEN_CHECK_INTERVAL = 15 * 60          # seconds between looks at the board when no badge shows
PET_PEN_BADGE_INTERVAL = 2 * 60           # seconds between looks at the board while a badge shows (it is there for the other alerts too)
PET_PEN_UI_WAIT = 4.0                     # seconds for the board to open
PET_PEN_POLL = 0.5                        # seconds between looks while waiting for it
PET_PEN_ANIMATION_WAIT = 8.0              # seconds from the card's click to the age-up animation being over (the capture clicked NICE! 8 s after)
PET_PEN_NICE_WAIT = 15.0                  # seconds to see the NICE! button in
PET_PEN_NICE_TRIES = 3                    # clicks on it while it is still there
PET_PEN_ROUTE = (("hold", "back", 0.7), ("press", "interact", 0), ("wait", "exit", 5.0), ("hold", "left", 2.23), ("hold", "back", 0.3), ("hold", "left", 0.18), ("press", "interact", 0))      # from the respawn in the house to the pen: out of the house (E at the door, the yard loads), then along the fence to the pen; the second E opens it (the capture of a person doing it)
PET_PEN_OPEN_WAIT = 4.0                   # seconds for the pen's screen to come up after that E
PET_PEN_SLOTS = ((855, 415), (1045, 415), (855, 580), (1045, 580))      # the middles of the pen's four places (two above, two below)
PET_PEN_SLOT_HALF = (95, 80)              # half the width and height of the box a place is looked at in
PET_PEN_X_OFFSET = (77, -60)              # the red X that takes a pet out is this far from its place's middle
PET_PEN_CLOSE_POS = (1187, 300)           # the red X of the pen's screen
PET_PEN_GRID_X = (895, 975, 1055, 1135)   # the middles of the cells of the small backpack's grid, by column...
PET_PEN_GRID_Y = (706, 787, 867)          # ...and by row (three rows show)
PET_PEN_CELL_HALF = 36                    # half the size of a cell
PET_PEN_LOCKED_MAX_V = 200                # a cell whose corners are darker than this (brightness 0-255) is grey: a pet that is in a trade listing (white cells are 250 and more, grey ones 160)
PET_PEN_GREEN_BORDER_MIN = 0.05           # a cell whose edge is this green (share of its pixels) is the equipped pet (0.25 on the capture)
PET_PEN_CONTENT_MIN_STD = 8.0             # a cell with a pet in it has a picture (grey levels vary this much); an empty one is flat
PET_PEN_SCROLL_POS = (1193, 760)          # the mouse is put on the grid's scroll bar to turn the wheel (over a pet it would open its card)
PET_PEN_SCROLL_MAX = 40                   # most scrolls looking for a pet that fits
PET_PEN_BACKPACK_CLOSE_POS = (1188, 640)  # the small backpack's red X (from the picture; not clicked in the capture)
PET_PEN_FILTER_BUTTON_POS = (956, 640)    # the orange filter button in the small backpack's header (it opens the same FILTERS panel as the big backpack's, in the same place)
PET_PEN_UI_SETTLE = 1.5                   # seconds after a click in the pen's screens
PET_PEN_SWAP_RETRY = 10 * 60              # seconds before a swap that did not come through is tried again
PET_REPICK_INTERVAL = 30 * 60            # seconds between the loop's picks of the pet to level (see pet_repick(): a pet that has grown up is no longer in the filtered list)
TREE_CHECK_INTERVAL = 10 * 60          # seconds (10 minutes) between tree harvests
LURE_RECOLLECT_INTERVAL = 4 * 60 * 60  # seconds (4 hours)

# setup_game(): run on demand (the GUI's Setup button) and by the automatic recovery
# in rejoin_game(), never by the normal cycle.
SETUP_LOCK_HOUSE_POS = (1112, 65)
SETUP_BACKPACK_SETTINGS_POS = (977, 646)
SETUP_SORT_MENU_POS = (1031, 680)
SETUP_CONFIRM_POS = (1047, 908)
PET_FIRST_ITEM_POS = (975, 707)                  # the pets tab's first pet, right of the green plus tile: the first of the list the filters leave
AGE_ALTERNATES = {"age_full_grown": ("age_full_grown_pen",)}   # other crops of the same row, tried when the first is not found
AGE_LABELS = ("age_egg", "age_full_grown")        # the Age rows Setup unticks (POPUP_TEMPLATES): what is neither an egg nor full grown still grows
AGE_CHECKBOX_DX = -17                             # px from where the text of a row starts to the middle of its box
AGE_CHECKBOX_HALF = 12                            # half the side of the square looked at for a tick
AGE_TICK_HSV = ((120, 100, 100), (150, 255, 255))   # a tick is purple (OpenCV's HSV); an unticked box has none
AGE_TICK_MIN_PIXELS = 40                          # of that colour in the square (a tick is about 110, an empty box 0)
FILTERS_SCROLL_POS = (1123, 760)                  # the wheel is turned over the FILTERS panel's scroll bar (over a row the description of an item pops up)
FILTERS_SCROLL_TRIES = 8                          # notches at most to bring the Age rows into view
SETUP_FILTER_CANCEL_POS = (874, 904)         # the FILTERS panel's Cancel: a panel that was only looked at is left as it was
# Disabling trades: settings (gear) -> settings menu -> Interaction (found by its text) -> trading
# setting -> "No One" (found by its text) -> close. Each is looked at first and left as it is when it is right. The macro window is sent to the back for this, since
# it covers the gear.
SETUP_TRADES_SETTINGS_POS = (1894, 757)
SETUP_TRADES_MENU_POS = (1027, 364)
SETUP_TRADES_SETTING_POS = (1029, 549)
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
