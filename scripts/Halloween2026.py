"""Halloween 2026: the event's minigames and routes, kept here now that it is over (docs/HALLOWEEN_2026.md).

Everything in this file was part of the macro while the event ran: reading the Hauntlet 2 screen and playing its rooms, hunting the Ghost Gallery's ghosts, following the Stitches'
Spotlight's ring, reading the candy corn total, accepting or dismissing the minigame popups, recording a run of one, and the constants all of that used. The macro does not import it.
To bring the event (or a later one like it) back, the hooks the macro had are listed in docs/HALLOWEEN_2026.md: `unscrew()` calling `minigame_popup()` and `close_minigame_finish_screen()`,
the GUI's "Play ..." switches, the run report's minigame lines. The code reaches the macro's own functions as `m.<name>` (main.py), and the state it keeps for itself is `event`."""
from __future__ import annotations

import itertools
import contextlib
import json
import random
import shutil
import math
import os
import re
from functools import lru_cache
from dataclasses import dataclass, field
from typing import Any, Callable, Collection, Iterator, Optional, Sequence

import cv2
import numpy as np

import main as m
import magic_numbers as cfg
from main import Image, Point

# ============================================================================
# CONSTANTS
# ============================================================================

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
EXIT_DIALOG_MIN_FRACTION = 0.4                                  # of a box that is its button's colour (the button's text takes some of it)
EXIT_RED_HSV = ((0, 150, 150), (8, 255, 255))                   # (low, high); red is also at the other end of the hue circle
EXIT_RED_HSV_HIGH = ((172, 150, 150), (179, 255, 255))
EXIT_GREEN_HSV = ((45, 110, 130), (80, 255, 255))
EXIT_GREY_HSV = ((0, 0, 40), (179, 60, 120))
EXIT_DIALOG_WAIT = 4.0                                          # seconds the dialog is waited for after the button is clicked
EXIT_COUNTDOWN_WAIT = 8.0                                       # and then the right button's countdown, until it is green
EXIT_LEAVE_WAIT = 12.0                                          # and then the minigame's overlay to be gone
EXIT_POLL = 0.25                                                # seconds between looks
MINIGAME_FINISH_CLICK_TRIES = 3      # NICE! clicks before giving up on the finish screen closing
MINIGAME_START_LOOK_INTERVAL = 0.5     # how often each minigame looks for its overlay while waiting for the game to begin
MINIGAME_START_WAIT = 90.0          # each minigame waits for its overlay (the game has begun: the lobby's countdown to a round that starts at a fixed time) and act
MINIGAME_TEMPLATE_DIR = os.path.join(cfg.SCRIPT_DIR, "ref", "halloween")
MINIGAME_OVERLAY_DIR = os.path.join(cfg.SCRIPT_DIR, "ref", "overlays")
HAUNTLET_DIGIT_DIR = os.path.join(cfg.SCRIPT_DIR, "ref", "hauntlet_2", "digits")   # 0.png ... 9.png: the digits of the ROOM panel, see read_hauntlet_room()
HAUNTLET_ITEM_DIR = os.path.join(cfg.SCRIPT_DIR, "ref", "hauntlet_2", "items")      # a folder per item (phone/, key/, ...) of pictures of its slot; see read_hauntlet_items()
HAUNTLET_COUNT_DIR = os.path.join(cfg.SCRIPT_DIR, "ref", "hauntlet_2", "counts")    # x/, 1/, 2/: the glyphs of an item's "x2" badge
CANDY_CORN_DIGIT_DIR = os.path.join(cfg.SCRIPT_DIR, "ref", "candy_corn_digits")      # 0.png ... 9.png: the digits of the candy corn total in the HUD, see read_candy_corn()
# Every minigame played is recorded the same way, automatically (MINIGAME_CAPTURE; not while a capture of the hotkey's is
# running), into a folder of RUN_CAPTURES_DIR. Of each minigame the captures of its RUN_CAPTURES_BEST best runs (the most candy corn
# earned), its RUN_CAPTURES_WORST worst, its latest and the latest RUN_CAPTURES_FAILED that failed are kept (prune_run_captures()). A lossless frame is 1-3 MB, so a run
# is kept to RUN_CAPTURE_ZIPS zips of CAPTURE_ZIP_MB by thinning its frames out evenly (see InputCapture) - except frames of
# when things went wrong: at a mark() (a walk that got nowhere, a failure, a stall...) a frame is saved at once and every look for
# the next RUN_CAPTURE_BURST seconds, and a run that didn't finish keeps the frames of its last RUN_CAPTURE_TAIL seconds; such
# frames are not thinned out (up to RUN_CAPTURE_PROTECTED of them, or they would use up the budget).
MINIGAME_CAPTURE = True
RUN_CAPTURES_DIR = os.path.join(cfg.DEBUG_DIR, "minigame_captures")
RUN_CAPTURE_ZIPS = 5
RUN_CAPTURES_BEST = 2
RUN_CAPTURES_WORST = 2
RUN_CAPTURES_FAILED = 5                   # the latest this many runs that failed (did not finish, or had a failure logged) are kept too
RUN_CAPTURE_TAIL = 15.0
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
HAUNTLET_TV_GLOW = ((120, 215, 245), (240, 255, 255))   # (low, high) red, green, blue of the number's light: cyan, but in a red room (room 12) the digits were 140-240 red and 228-255 green on real frames, so the range stopped at 225 red and 248 green saw none of three
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
HAUNTLET_CIRCLE_WAIT = 25.0                  # a walk that has stepped into a door's circle goes on this long for the room to change: it did 9-13 s after the step in real runs (the walk's HAUNTLET_WALK_MAX ended first, "got nowhere", and the player was jumped out of the circle)
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

# What the event's moved nursery needed instead of the macro's own routes (TELEPORT_WALK_NEEDS): the nursery is back where it was, and these are not used.
KEYS = cfg.KEYS
TELEPORT_WALK_NEEDS_HALLOWEEN = {
    "bored":   dict(teleport_pos=cfg.TELEPORT_PETS_TAB_POS, steps=((KEYS["forward"], 9),), helicopter=True),
    "beach":   dict(teleport_pos=cfg.TELEPORT_PETS_TAB_POS, steps=((KEYS["left"], 20.0),), helicopter=True),
    "school":  dict(teleport_pos=cfg.TELEPORT_PETS_TAB_POS, steps=((KEYS["forward"], 3), (KEYS["left"], 6))),
    "camping": dict(teleport_pos=cfg.TELEPORT_PETS_TAB_POS, steps=((KEYS["back"], 20),), helicopter=True),
    "sick":    dict(teleport_pos=cfg.TELEPORT_PETS_TAB_POS, steps=((KEYS["forward"], 2.5), (KEYS["right"], 4.0), (KEYS["forward"], 1.2)),
                    final_click=cfg.SICK_FINAL_CLICK_POS),
}



# The crops the event's code looks for with m.popup_visible() (ref/popups/): the macro's POPUP_TEMPLATES had these entries while the event ran; a restored event adds them back to it.
POPUP_TEMPLATES_EVENT = {
    "minigame_popup": {"box": (780, 515, 1160, 585), "threshold": 0.8},       # the minigame popup's "Teleport there now?" line (the same in every minigame's popup, always in the same place)
    "exit_game": {"box": (760, 445, 1160, 605), "threshold": 0.8},            # the exit dialog's "Do you want to exit this game?" title (the same with its right button grey or green)
    "exit_minigame": {"box": (0, 1000, 220, 1078), "threshold": 0.8},         # the red "Exit Minigame" button at the bottom left of every minigame
}


@dataclass
class EventState:
    """What the event's code keeps between its calls (it was fields of the macro's State)."""
    run_capture: Optional[Any] = None                       # the recording of the minigame being played, see start_run_capture()
    minigame_detail: dict = field(default_factory=dict)     # what the player of the minigame being played says about it ("room")
    exit_dialog_wanted: bool = False                        # exit_minigame() is going on: dismiss_stray_windows() leaves the "exit this game?" dialog alone
    last_minigame_popup_check: float = 0.0                  # when the minigame popup checks last ran
    popup_held_since: Optional[float] = None                # when a minigame popup that is being held off for a need's end wait was first seen
    end_wait_need: Optional[str] = None                     # the need whose end wait is running (set_end_wait_need())
    task_play_minigames: bool = True                        # whether the task that runs may play a minigame


event = EventState()

# ============================================================================
# CODE
# ============================================================================


@contextlib.contextmanager
def end_wait(need_name: Optional[str]) -> Iterator[None]:
    """Mark `need_name`'s end wait as running (event.end_wait_need), so that a minigame popup waits for it - see minigame_popup(). The macro's wait_until_need_gone() and walk_alternating()
    ran inside it while the event did."""
    event.end_wait_need = need_name
    try:
        yield
    finally:
        event.end_wait_need = None


def mark_run_capture(reason: str) -> None:
    """Say to the recording of the minigame being played, if there is one, that something went wrong now (InputCapture.mark()): frames of
    this moment are saved and kept. Never raises. The macro's log_failure() called it while the event ran."""
    capture = event.run_capture
    if capture is None:
        return
    try:
        capture.mark(reason)
    except Exception as error:
        m.logger.warning(f"Couldn't mark the recording: {error}")

def detect_minigame_popup(img: Image) -> bool:
    """True if a Halloween minigame popup is on screen: a button of the Yes green (a piece of it of at least
    MINIGAME_POPUP_MIN_BUTTON_PIXELS px) with the No red close to its left (neither colour is unique alone), under the popup's own "Teleport there now?" line (a crop,
    POPUP_TEMPLATES["minigame_popup"]). Each green piece
    is looked at on its own: a popup over the open backpack, whose buttons are green too, was missed when all the green on the
    screen was taken together. See docs/ARCHITECTURE.md#need-handlers.
    """
    if m.detect_sick_dialog(img) or not m.popup_visible(img, "minigame_popup"):
        return False                                                  # (another dialog with a green and a red button is not the popup: it has its "Teleport there now?")
    green = (m.exact_color_mask(img, cfg.GREEN_BUTTON_COLOR) > 0).astype(np.uint8)
    count, _, stats, centres = cv2.connectedComponentsWithStats(green)
    for i in range(1, count):
        if stats[i, cv2.CC_STAT_AREA] < MINIGAME_POPUP_MIN_BUTTON_PIXELS:
            continue
        yes_x, yes_y = int(centres[i][0]), int(centres[i][1])
        near_no = img[max(0, yes_y - MINIGAME_POPUP_NO_MAX_DY):yes_y + MINIGAME_POPUP_NO_MAX_DY,
                      max(0, yes_x - MINIGAME_POPUP_NO_MAX_DX):yes_x]
        if int(np.count_nonzero(m.exact_color_mask(near_no, MINIGAME_POPUP_NO_COLOR))) >= MINIGAME_POPUP_MIN_BUTTON_PIXELS:
            return True
    return False

def identify_minigame(img: Image) -> Optional[str]:
    """Which minigame the popup in `img` is for: the name whose title crop
    (MINIGAME_TEMPLATE_DIR) matches best within MINIGAME_TITLE_BOX, if that
    match reaches MINIGAME_TITLE_MATCH_THRESHOLD - otherwise None."""
    best_name, best_score = None, MINIGAME_TITLE_MATCH_THRESHOLD
    for name, crop in m.load_templates(MINIGAME_TEMPLATE_DIR).items():
        score = m.template_score(img, crop, MINIGAME_TITLE_BOX)
        if score >= best_score:
            best_name, best_score = name, score
    return best_name

@contextlib.contextmanager
def wanting_exit_dialog() -> Iterator[None]:
    """Within this block dismiss_stray_windows() does not stay (click Cancel) on the "exit this game?" dialog: the macro is leaving a minigame on purpose."""
    event.exit_dialog_wanted = True
    try:
        yield
    finally:
        event.exit_dialog_wanted = False

def detect_exit_dialog(img: Image) -> Optional[str]:
    """"Do you want to exit this game?" - its title (POPUP_TEMPLATES["exit_game"]: the teleport's Yes / No dialogs have the same buttons) and a red Cancel with, at its
    right, a grey button (still counting down: "counting") or a green one (Exit Game: "ready"); None if it is not there."""
    if not m.popup_visible(img, "exit_game"):
        return None
    if m.hsv_fraction(img, EXIT_DIALOG_CANCEL_BOX, (EXIT_RED_HSV, EXIT_RED_HSV_HIGH)) < EXIT_DIALOG_MIN_FRACTION:
        return None
    if m.hsv_fraction(img, EXIT_DIALOG_RIGHT_BOX, (EXIT_GREEN_HSV,)) >= EXIT_DIALOG_MIN_FRACTION:
        return "ready"
    if m.hsv_fraction(img, EXIT_DIALOG_RIGHT_BOX, (EXIT_GREY_HSV,)) >= EXIT_DIALOG_MIN_FRACTION:
        return "counting"
    return None

def in_minigame(img: Image) -> bool:
    """True if a minigame is being played: the red Exit Minigame button (every minigame has it) or the overlay of one the macro knows (MINIGAME_LABELS) is on screen."""
    return m.popup_visible(img, "exit_minigame") or any(minigame_overlay_visible(img, name) for name in MINIGAME_LABELS)

def exit_minigame() -> bool:
    with wanting_exit_dialog():
        return _exit_minigame()

def _exit_minigame() -> bool:
    """Leave the minigame being played, if one is (in_minigame()): click the Exit Minigame button (EXIT_MINIGAME_POS) - unless the "exit this game?" dialog is up
    already - wait for the dialog, for its right button to turn from grey to green (EXIT_COUNTDOWN_WAIT), click it (EXIT_GAME_POS) and wait for the overlay to be gone
    (EXIT_LEAVE_WAIT). True if there was none to leave or it was left; False (logged) if a step did not happen."""
    if not m.focus_roblox():
        return False
    img = m.grab_screen()
    if detect_exit_dialog(img) is None:
        if not in_minigame(img):
            return True
        m.logger.info("In a minigame - exiting it")
        m.hover_click(*EXIT_MINIGAME_POS)

    def wait_for(condition: Callable[[Image], bool], seconds: float) -> bool:
        deadline = m.time.time() + seconds
        while True:
            if condition(m.grab_screen()):
                return True
            if m.time.time() >= deadline:
                return False
            m.wait_interruptible(EXIT_POLL)
    if not wait_for(lambda frame: detect_exit_dialog(frame) is not None, EXIT_DIALOG_WAIT):
        m.log_run_event("exit minigame: the dialog did not come up")
        return False
    if not wait_for(lambda frame: detect_exit_dialog(frame) == "ready", EXIT_COUNTDOWN_WAIT):
        m.log_run_event("exit minigame: the Exit Game button did not turn green")
        return False
    m.hover_click(*EXIT_GAME_POS)
    if not wait_for(lambda frame: not in_minigame(frame), EXIT_LEAVE_WAIT):
        m.log_run_event("exit minigame: the minigame is still there")
        return False
    m.log_run_event("minigame exited")
    return True

def exit_minigame_and_setup() -> bool:
    """The action of /sloop before the loop: leave the Trading Hub if the player is in it and a minigame if one is being played, then run Setup. Neither that cannot be left
    is an error (the status says so and the loop does not start): Setup would click in it."""
    if not m.leave_hub_if_in():
        raise RuntimeError("could not leave the Trading Hub")
    if not exit_minigame():
        raise RuntimeError("could not leave the minigame")
    return m.setup_or_fail()

def detect_minigame_finished(img: Image) -> bool:
    """True if the minigame finish screen is on screen in `img`: the red
    GAME OVER! banner and the green NICE! button, both by exact color (the
    same green is the Yes button's, so the banner is what rules that out)."""
    for color, tolerance, box, minimum in (
            (MINIGAME_FINISH_BANNER_COLOR, MINIGAME_FINISH_BANNER_TOLERANCE,
             MINIGAME_FINISH_BANNER_BOX, MINIGAME_FINISH_BANNER_MIN_PIXELS),
            (cfg.GREEN_BUTTON_COLOR, cfg.GREEN_BUTTON_TOLERANCE,
             MINIGAME_FINISH_BUTTON_BOX, MINIGAME_FINISH_BUTTON_MIN_PIXELS)):
        if m.color_pixels(img, box, color, tolerance) < minimum:
            return False
    return True

def minigame_overlay_visible(img: Image, name: str) -> bool:
    """True if minigame `name`'s overlay (its crop in MINIGAME_OVERLAY_DIR, within its box in MINIGAME_OVERLAYS) is on screen in `img`, at least
    MINIGAME_OVERLAY_THRESHOLD: one template match, not one per minigame, since this is asked at every look."""
    crop = m.load_templates(MINIGAME_OVERLAY_DIR).get(name)
    return crop is not None and name in MINIGAME_OVERLAYS and m.template_score(img, crop, MINIGAME_OVERLAYS[name]) >= MINIGAME_OVERLAY_THRESHOLD

class MinigameWatch:
    """Keeps track of whether the minigame being played is on screen (its
    overlay - minigame_overlay_visible()) and whether the game disconnected
    (detect_disconnect()), fed one screenshot at a time by minigame_look() and minigame_wait().
    `problem` says why to stop, or None."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.last_seen: Optional[float] = None
        self.start_grace_from: Optional[float] = None   # set once the start wait is over
        self.disconnected = False                       # the "Disconnected" dialog has been seen

    def update(self, img: Image) -> None:
        if m.detect_disconnect(img):
            self.disconnected = True
        elif minigame_overlay_visible(img, self.name):
            self.last_seen = m.time.time()

    def start_wait_over(self) -> None:
        self.start_grace_from = m.time.time()

    @property
    def problem(self) -> Optional[str]:
        now = m.time.time()
        if self.disconnected:
            return "disconnected from the game"
        if self.last_seen is not None and now - self.last_seen > MINIGAME_OVERLAY_LOST_TIMEOUT:
            return f"left the minigame (its overlay was gone for {MINIGAME_OVERLAY_LOST_TIMEOUT:g}s)"
        if (self.last_seen is None and self.start_grace_from is not None
                and now - self.start_grace_from > MINIGAME_OVERLAY_START_GRACE):
            return f"never started (no overlay within {MINIGAME_OVERLAY_START_GRACE:g}s of the start wait)"
        return None

def minigame_popup_checks() -> None:
    """The popups that can turn up mid-minigame - stray windows and the
    paycheck - checked at most every MINIGAME_POPUP_CHECK_INTERVAL. (The
    in-task unscrew() can't run here: the minigame is itself played from it.)"""
    if m.time.time() - event.last_minigame_popup_check < MINIGAME_POPUP_CHECK_INTERVAL:
        return
    event.last_minigame_popup_check = m.time.time()
    if m.dismiss_stray_windows():
        mark_run_capture("a stray window was closed")
    elif m.detect_paycheck():
        mark_run_capture("the paycheck popup was closed")

def minigame_look(watch: MinigameWatch) -> Image:
    """Run the popup checks if they're due, then take a fresh screenshot, which `watch` is told about."""
    minigame_popup_checks()
    img = m.grab_screen()
    watch.update(img)
    return img

def zoom_out() -> None:
    """Zoom the camera out (KEYS["zoom_out"] held MINIGAME_ZOOM_OUT_DURATION seconds) to see the whole room: close up, the TVs
    over a Hauntlet 2 room's doors are cut off by the top of the screen."""
    m.logger.debug("Zooming the camera out")
    m.hold_key(cfg.KEYS["zoom_out"], MINIGAME_ZOOM_OUT_DURATION)

def minigame_wait(duration: float, watch: MinigameWatch) -> None:
    """wait_interruptible(), with the popup checks every
    MINIGAME_POPUP_CHECK_INTERVAL and `watch` kept up to date - for the long
    waits of a minigame. Ends early if the minigame is plainly over."""
    end = m.time.time() + duration
    while m.time.time() < end:
        m.wait_interruptible(min(MINIGAME_POPUP_CHECK_INTERVAL, end - m.time.time()))
        minigame_popup_checks()
        watch.update(m.grab_screen())
        if watch.problem:
            return

def wait_for_minigame_start(watch: MinigameWatch, name: str) -> None:
    """Wait, looking every MINIGAME_START_LOOK_INTERVAL, until the minigame's overlay shows (the game has begun: the lobby's countdown to a round that starts
    at a fixed time), for at most MINIGAME_START_WAIT, or until `watch` (the minigame `name`'s) has a problem (a disconnect); then tell `watch` the start wait is over."""
    m.logger.debug(f"{MINIGAME_LABELS[name]}: waiting for it to start (at most {MINIGAME_START_WAIT:g}s)...")
    waited_until = m.time.time() + MINIGAME_START_WAIT
    while m.time.time() < waited_until and watch.last_seen is None and not watch.problem:
        minigame_wait(MINIGAME_START_LOOK_INTERVAL, watch)
    watch.start_wait_over()

class HauntletRun:
    """What play_hauntlet_2() knows about the room it is in: its number, when that changed, and the TVs of the last
    look. look() says when the doors can be chosen from. See docs/ARCHITECTURE.md#playing-hauntlet-2."""

    def __init__(self) -> None:
        self.room: Optional[int] = None
        self.since = 0.0                              # when the number changed
        self.walked_at: Optional[float] = None        # when the last walk in this room ended
        self.stuck = 0                                # walks in a row in this room that didn't change the room number
        self.last_index: Optional[int] = None         # the door of the last of them
        self.picked: Optional[int] = None             # the door whose circle the player has stepped into: that is the choice made, for good
        self.turns = 0                                # times the camera was turned to find the doors, in this room
        self.rooms = 0                                # numbers seen
        self.tvs: Optional[list[tuple[int, int]]] = None   # of the last look: None if the timer was hidden
        self.empty_looks = 0                          # looks in a row at a room without TVs
        self.doors_missing_reported = False           # the failure for a room with no doors found has been logged
        self.white = False                            # the last look was a white screen

    @property
    def fork(self) -> bool:
        """Whether this room's number is one of the forks (HAUNTLET_FORK_EVERY)."""
        return self.room is not None and self.room > 0 and self.room % HAUNTLET_FORK_EVERY == 0

    def look(self, img: Image, now: float) -> Optional[list[HauntletDoor]]:
        """Take in a screenshot. The doors to choose from - three, or a fork's two - if it is time to, else None."""
        room = read_hauntlet_room(img)
        if room is not None and room != self.room:
            self.room, self.since, self.walked_at, self.rooms = room, now, None, self.rooms + 1
            self.stuck, self.last_index, self.turns, self.picked = 0, None, 0, None
            self.tvs, self.empty_looks = None, 0
            m.logger.debug(f"Hauntlet 2: room {room}")
        if self.room is None:
            return None
        if m.detect_white_screen(img):                  # a heart lost to a ghost, a death: the game whites the screen out and brings the player back to the room's start,
            if not self.white:                        # so the room is looked at afresh from now - not taken for one with no doors because it was entered long ago
                m.logger.debug("Hauntlet 2: the screen went white (a ghost, or a fall): the room starts again")
            self.white, self.since, self.walked_at, self.tvs, self.empty_looks = True, now, None, None, 0
            return None
        self.white = False
        age = now - self.since
        before, self.tvs = self.tvs, (find_door_tvs(img) if hauntlet_timer_visible(img) else None)
        if self.tvs is None or age < HAUNTLET_ROOM_SETTLE or (self.walked_at is not None and now < self.walked_at + HAUNTLET_RETRY_WAIT):
            self.empty_looks = 0
            return None
        if self.tvs:
            self.empty_looks = 0
            steady = before is not None and len(before) == len(self.tvs) and all(
                abs(a - b) <= HAUNTLET_TV_STABLE for old, new in zip(before, self.tvs) for a, b in zip(old, new))
            return find_hauntlet_doors(img) if steady or age >= HAUNTLET_DOORS_TIMEOUT else None
        self.empty_looks += 1
        if self.fork and self.empty_looks >= 2:
            return [HauntletDoor(0, 0, None), HauntletDoor(0, 0, None)]       # two doorways, not located
        if not self.fork and (age >= HAUNTLET_DOORS_TIMEOUT or (self.room == 1 and self.empty_looks >= 2)):       # room 1 is the entrance hall (a fork waits for its second empty look: after a walk the looks start again, and a fork past the timeout was taken for one doorway and the camera was turned)
            return [HauntletDoor(0, 0, None)]                                  # one doorway, straight ahead
        return None

def choose_hauntlet_door(doors: list[HauntletDoor], items: list[HauntletItem], avoid: Collection[int] = ()) -> int:
    """The index in `doors` (left to right) of the door to go through: a random one, but none of `avoid` (the one the phone's
    arrow is over, where a ghost is, and the one that just got the player stuck) and a locked one only when the grey key is
    held - unless that leaves nothing, then whatever is left."""
    has_key = any(item.name == HAUNTLET_ITEM_KEY for item in items)
    allowed = [i for i in range(len(doors)) if i not in avoid]
    options = [i for i in allowed if doors[i].kind != "locked" or has_key]
    return random.choice(options or allowed or list(range(len(doors))))

def hauntlet_items_for(door: HauntletDoor, hearts: Optional[HauntletHearts], items: list[HauntletItem]) -> list[str]:
    """The items (names) to use before going through `door`, in the order to use them: the grey key for a locked door, a cyan potion
    (a heart that lasts the room) for a boarded one, the red potion when the hearts are down to HAUNTLET_LOW_HEARTS or fewer, and a
    heart potion whenever there is one. Only those held."""
    held = {item.name for item in items}
    wanted = []
    if door.kind == "locked":
        wanted.append(HAUNTLET_ITEM_KEY)
    if door.kind == "boarded":
        wanted.append(HAUNTLET_ITEM_BOARDED)
    if hearts is not None and hearts.red <= HAUNTLET_LOW_HEARTS:
        wanted.append(HAUNTLET_ITEM_REFILL)
    wanted.append(HAUNTLET_ITEM_HEART)
    return [name for name in wanted if name in held]

def find_door_circles(img: Image) -> list[tuple[int, int, int, int]]:
    """The purple circles on the floor in front of a room's doors - stepping into one picks that door - as (x, y, width, height)
    of each one's middle, left to right: the ring-shaped pieces of HAUNTLET_CIRCLE_HSV of about the right size (HAUNTLET_CIRCLE_*), which
    leaves out the purple badges and labels. One the player stands on is mostly hidden, so it can be missing."""
    top, bottom = HAUNTLET_CIRCLE_BAND
    mask = cv2.inRange(cv2.cvtColor(img[top:bottom], cv2.COLOR_BGR2HSV), HAUNTLET_CIRCLE_HSV[0], HAUNTLET_CIRCLE_HSV[1])
    count, _, stats, _ = cv2.connectedComponentsWithStats(cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)))
    found = []
    for i in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[i])
        y += top
        if (HAUNTLET_CIRCLE_WIDTH[0] <= w <= HAUNTLET_CIRCLE_WIDTH[1] and HAUNTLET_CIRCLE_HEIGHT[0] <= h <= HAUNTLET_CIRCLE_HEIGHT[1]
                and HAUNTLET_CIRCLE_MIN_AREA <= area < HAUNTLET_CIRCLE_FILL * w * h):
            found.append((x + w // 2, y + h // 2, w, h))
    return sorted(found)

def find_fork_doorways(img: Image) -> Optional[tuple[int, int]]:
    """The x of the middle of a fork's two doorways, left and right, or None unless exactly two openings of the right size and
    distance are seen (HAUNTLET_OPENING_*): each opening is dark inside and starts right under the wall's pale stripes, where the wall
    elsewhere has the gold wainscot below it. Only seen with the camera near the wall - from the room's entrance one is, or none."""
    top, bottom = HAUNTLET_FORK_BAND
    hsv = cv2.cvtColor(img[top:bottom], cv2.COLOR_BGR2HSV)
    wall = cv2.inRange(hsv, HAUNTLET_WALL_HSV[0], HAUNTLET_WALL_HSV[1]) > 0
    dark = cv2.inRange(hsv, HAUNTLET_OPENING_HSV[0], HAUNTLET_OPENING_HSV[1]) > 0
    run = dark.copy()
    for rows in range(1, HAUNTLET_OPENING_RUN):                       # a row that starts a run of HAUNTLET_OPENING_RUN dark ones
        run[:-rows] &= dark[rows:]
    first = run.argmax(axis=0)                                            # the first such row of each column (0 if none)
    near, far = HAUNTLET_OPENING_ABOVE
    wall_above = np.vstack([np.zeros((1, wall.shape[1]), int), np.cumsum(wall, axis=0)])
    columns = np.arange(wall.shape[1])
    walled = wall_above[np.maximum(first - near, 0), columns] - wall_above[np.maximum(first - far, 0), columns]
    openings = run.any(axis=0) & (walled >= HAUNTLET_OPENING_WALL)
    spans: list[list[int]] = []
    for x in np.flatnonzero(openings):
        if spans and x - spans[-1][1] <= HAUNTLET_OPENING_JOIN:
            spans[-1][1] = int(x)
        else:
            spans.append([int(x), int(x)])
    whole = [span for span in spans if HAUNTLET_OPENING_WIDTH[0] <= span[1] - span[0] + 1 <= HAUNTLET_OPENING_WIDTH[1]]
    if len(whole) == 2:
        left, right = ((a + b) // 2 for a, b in whole)
        return (left, right) if HAUNTLET_OPENING_GAP[0] <= right - left <= HAUNTLET_OPENING_GAP[1] else None
    # Seen at an angle the second doorway shows only as a narrow piece (its far side is lit, not dark): one whole opening that isn't
    # too wide, with exactly one narrow piece the distance away that the doorways are of each other, is the pair.
    pairs = []
    for opening in whole:
        if opening[1] - opening[0] + 1 > HAUNTLET_OPENING_MAIN_MAX:
            continue
        for piece in spans:
            if HAUNTLET_OPENING_PIECE[0] <= piece[1] - piece[0] + 1 <= HAUNTLET_OPENING_PIECE[1] and \
                    HAUNTLET_OPENING_PIECE_GAP[0] <= abs((piece[0] + piece[1]) // 2 - (opening[0] + opening[1]) // 2) <= HAUNTLET_OPENING_PIECE_GAP[1]:
                pairs.append(sorted(((opening[0] + opening[1]) // 2, (piece[0] + piece[1]) // 2)))
    return (pairs[0][0], pairs[0][1]) if len(pairs) == 1 else None

def player_in_circle(img: Image, circles: list[tuple[int, int, int, int]]) -> bool:
    """Whether the player stands in one of the three circles - and so has picked that door, and is locked in place for a few moments:
    two circles are seen, the third being hidden under the player, whose place (where the hearts are, else mid-screen) is where
    the third one would be (the same distance from its neighbour as the other two are from each other, HAUNTLET_IN_CIRCLE of it)."""
    if len(circles) != 2:
        return False
    hearts = read_hauntlet_hearts(img)
    x = hearts.center_x if hearts else cfg.REFERENCE_CENTER_X
    left, right = circles[0][0], circles[1][0]
    gap = right - left
    if left < x < right:                                   # between them: the middle one (they are two spacings apart)
        return abs(x - (left + right) / 2) <= HAUNTLET_IN_CIRCLE * gap / 2
    return abs(x - (left - gap if x < left else right + gap)) <= HAUNTLET_IN_CIRCLE * gap

def hauntlet_strafe(img: Image, doors: list[HauntletDoor], index: int) -> tuple[Optional[str], float]:
    """The key to strafe with, and for how long, to reach door `index` from where the player stands in `img`: the
    distance to the door in door spacings (the TVs' spacing) times HAUNTLET_STRAFE_DURATION, or no strafe when that
    is under HAUNTLET_STRAFE_MIN spacings. Where the player starts a room can depend on the door it came through, so
    it is measured (the hearts above the head); not seen, it is taken to be in the middle of the screen (the camera follows the player; the
    middle door is not always in front of it - in a captured room 3 the player stood 350 px right of the middle door, hearts unread, and walked into the wall). Doors that
    aren't located have no distance to go: a fork's two doorways are the left key or the right one for one spacing, a
    single doorway ahead (an entrance hall) none. A fork's doorways that were found (find_fork_doorways(): the two x are
    in `doors`) are two door spacings apart, like a room's outer doors, and are walked to like a room's."""
    spacing = (doors[-1].x - doors[0].x) / 2 if len(doors) in (2, 3) else 0
    if spacing <= 0:
        if len(doors) == 1 or (len(doors) == 3 and index == 1):
            return None, 0.0
        return (cfg.KEYS["left"] if index == 0 else cfg.KEYS["right"]), HAUNTLET_STRAFE_DURATION
    hearts = read_hauntlet_hearts(img)
    circles = find_door_circles(img)
    target = circles[index][0] if len(circles) == len(doors) == 3 else doors[index].x       # the circle in front of the door, where it can be seen: it is what is to be stepped in
    distance = (target - (hearts.center_x if hearts else cfg.REFERENCE_CENTER_X)) / spacing
    if abs(distance) < HAUNTLET_STRAFE_MIN:
        return None, 0.0
    return (cfg.KEYS["right"] if distance > 0 else cfg.KEYS["left"]), min(abs(distance), HAUNTLET_STRAFE_MAX) * HAUNTLET_STRAFE_DURATION

def hauntlet_use_item(name: str, watch: MinigameWatch) -> Optional[Image]:
    """Click the slot of the item called `name` and give the game HAUNTLET_ITEM_WAIT seconds to use it. The slots move together
    when an item is used up, so the slot is looked for on a fresh screenshot each time. The screenshot taken after the click,
    or None if the item isn't there."""
    item = next((i for i in read_hauntlet_items(minigame_look(watch)) if i.name == name), None)
    if item is None:
        return None
    m.hover_click(*item.pos)
    m.wait_interruptible(HAUNTLET_ITEM_WAIT)
    return minigame_look(watch)

def hauntlet_choose(img: Image, doors: list[HauntletDoor], run: HauntletRun, watch: MinigameWatch) -> tuple[int, Image]:
    """Choose the door and use the items it calls for, logging the lot. With a chance of HAUNTLET_PHONE_CHANCE the phone is used first, and
    the door its red arrow shows is not gone through. Returns the door's index and the latest screenshot."""
    if run.picked is not None and run.picked < len(doors):
        m.logger.debug(f"Hauntlet 2: room {run.room}, the door is picked already (its circle was stepped in): going to the same one")
        return run.picked, img
    hearts = read_hauntlet_hearts(img)
    items = read_hauntlet_items(img)
    names = {1: ("ahead",), 2: ("left", "right"), 3: ("left", "middle", "right")}[len(doors)]
    ghost: Optional[int] = None
    used: list[str] = []
    low = hearts is not None and hearts.red <= HAUNTLET_LOW_HEARTS
    chance = HAUNTLET_PHONE_CHANCE_LOW if low else HAUNTLET_PHONE_CHANCE                 # the phone shows where the ghost is: more use when hearts are few
    if any(item.name == HAUNTLET_ITEM_PHONE for item in items) and random.random() < chance:
        shown = hauntlet_use_item(HAUNTLET_ITEM_PHONE, watch)
        if shown is not None:
            used.append(HAUNTLET_ITEM_PHONE)
            img = shown
            for _ in range(HAUNTLET_ARROW_LOOKS):                           # the arrow takes a moment to show
                ghost = find_ghost_door(img, doors)
                if ghost is not None:
                    break
                m.wait_interruptible(HAUNTLET_LOOK_INTERVAL)
                img = minigame_look(watch)
            items = read_hauntlet_items(img)
    avoid = {door for door in (ghost, run.last_index if run.stuck else None) if door is not None}      # the ghost's, and the one that got us stuck
    index = choose_hauntlet_door(doors, items, avoid=avoid)
    run.last_index = index
    for name in hauntlet_items_for(doors[index], hearts, items):
        shown = hauntlet_use_item(name, watch)
        if shown is not None:
            used.append(name)
            img = shown
    m.logger.debug(f"Hauntlet 2: room {run.room}, "
          + (f"{hearts.red} hearts ({hearts.black} lost)" if hearts else "hearts not seen")
          + f", items: {', '.join(f'{item.name} x{item.count or 1}' for item in items) or 'none'}"
          + f", doors: {', '.join(door.kind or '-' for door in doors)}"
          + (f", ghost behind the {names[ghost]} one" if ghost is not None else "")
          + f", going {names[index]}" + (f", used {', '.join(used)}" if used else ""))
    return index, img

def hauntlet_view(img: Image) -> np.ndarray:
    """`img` shrunk to a grey HAUNTLET_VIEW_SIZE, without the room panel at the top and the item bar at the bottom (HAUNTLET_VIEW_ROWS): what the walk looks at to
    tell whether anything is moving (the camera follows the player, so a player who moves makes the whole view move)."""
    small = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), HAUNTLET_VIEW_SIZE, interpolation=cv2.INTER_AREA).astype(np.int16)
    small[:HAUNTLET_VIEW_ROWS[0]] = 0
    small[HAUNTLET_VIEW_ROWS[1]:] = 0
    return small

def hauntlet_unstick(img: Image, target_index: Optional[int], tries: int, last_key: Optional[str]) -> str:
    """Get off whatever the player is caught on while holding forward: a step sideways (the forward key stays held, so it slides
    along) and a jump. The way is towards the door being walked to (its TV against where the player stands, HAUNTLET_UNSTICK_MARGIN
    px or more off), the other way each time after that, and when the way isn't known a random one first; the step is
    HAUNTLET_UNSTICK_STEP seconds longer for each try, up to HAUNTLET_UNSTICK_MAX. The key used. In a fork the door's place is the doorway
    (find_fork_doorways()), when both are in sight."""
    tvs = find_door_tvs(img)
    where = [x for x, _ in tvs] if len(tvs) == 3 else list(find_fork_doorways(img) or ())
    key: Optional[str] = None
    if last_key is not None:
        key = cfg.KEYS["right"] if last_key == cfg.KEYS["left"] else cfg.KEYS["left"]
    elif target_index is not None and target_index < len(where):
        hearts = read_hauntlet_hearts(img)
        dx = where[target_index] - (hearts.center_x if hearts else cfg.REFERENCE_CENTER_X)
        if abs(dx) >= HAUNTLET_UNSTICK_MARGIN:
            key = cfg.KEYS["right"] if dx > 0 else cfg.KEYS["left"]
    if key is None:
        key = random.choice((cfg.KEYS["left"], cfg.KEYS["right"]))
    m.logger.debug(f"Hauntlet 2: not moving - a step {key}")
    mark_run_capture("Hauntlet 2: not moving")
    m.pydirectinput.press(cfg.KEYS["jump"])
    m.hold_key(key, min(HAUNTLET_UNSTICK_STEP * (tries + 1), HAUNTLET_UNSTICK_MAX))
    return key

def hauntlet_steer(img: Image, target_index: int) -> Optional[tuple[str, float]]:
    """While walking to door `target_index`: the key to strafe with and for how long, if the player has drifted off the door (as hauntlet_strafe()
    measures it: the three TVs, the circle in front of the door, the hearts) by HAUNTLET_STEER_MIN door spacings or more, else None - also None when the
    three TVs aren't all on screen (close to the doors they are cut off). HAUNTLET_STEER_GAIN of the strafe, at most HAUNTLET_STEER_MAX seconds."""
    doors = find_hauntlet_doors(img)
    if len(doors) != 3:
        return None
    key, seconds = hauntlet_strafe(img, doors, target_index)
    if key is None or seconds < HAUNTLET_STEER_MIN * HAUNTLET_STRAFE_DURATION:
        return None
    return key, min(seconds * HAUNTLET_STEER_GAIN, HAUNTLET_STEER_MAX)

def hauntlet_walk(direction: Optional[str], duration: float, run: HauntletRun, watch: MinigameWatch, until_doors: bool = False,
                  target_index: Optional[int] = None, until_doorways: bool = False) -> bool:
    """Walk to a door: first strafe, `direction` (a strafe key, or None) held for `duration` seconds on its own, then hold forward
    until the room number changes (at most HAUNTLET_WALK_MAX seconds) and then HAUNTLET_WALK_PAST_DURATION longer, as
    the number changes with the player still in the doorway - or, with `until_doors`, until a row of TVs is
    on screen (a hall's doorway leads into a room whose number is the same), or, with `until_doorways`, until both of a
    fork's doorways can be seen (find_fork_doorways()). The view is looked at every HAUNTLET_LOOK_INTERVAL
    seconds: if it hasn't moved (hauntlet_view(), HAUNTLET_STILL_LEVEL) for HAUNTLET_STILL_AFTER seconds the player is caught
    on something and hauntlet_unstick() steps sideways, towards door `target_index` of the three (or the fork's two). A walk that doesn't get
    anywhere counts in `run.stuck`; the next one steps back first (HAUNTLET_BACKUP_DURATION). True if the minigame finished."""
    run.walked_at = m.time.time()
    if run.stuck > 0:
        m.hold_key(cfg.KEYS["back"], HAUNTLET_BACKUP_DURATION)
    aimed = False
    if target_index is not None and not run.fork:                               # aimed in small steps, looking at the doors again before each (HAUNTLET_AIM_STEPS)
        for _ in range(HAUNTLET_AIM_STEPS):
            img = minigame_look(watch)
            if detect_minigame_finished(img):
                return True
            steer = hauntlet_steer(img, target_index)
            if steer is None:
                break
            m.hold_key(*steer)
            aimed = True
    if not aimed and direction and duration > 0:
        m.hold_key(direction, duration)                                           # one key at a time: the strafe, then the walk (what couldn't be aimed is a strafe worked out in one go)
    arrived = False
    started = m.time.time()
    m.key_down(cfg.KEYS["forward"])
    try:
        last_view: Optional[np.ndarray] = None
        still_since: Optional[float] = None
        tries, last_key = 0, None
        locked_until, was_inside = 0.0, False
        steer_at = started + HAUNTLET_STEER_EVERY
        walk_until = started + HAUNTLET_WALK_MAX
        inside = False
        while m.time.time() < walk_until:
            img = minigame_look(watch)
            if detect_minigame_finished(img):
                return True
            room = read_hauntlet_room(img)
            if room is not None and room != run.room:
                arrived = True
                m.wait_interruptible(HAUNTLET_WALK_PAST_DURATION)             # the number changes in the doorway: on into the room
                break
            if watch.problem:
                break
            if (until_doors and find_door_tvs(img)) or (until_doorways and find_fork_doorways(img)):
                arrived = True
                break
            now = m.time.time()
            inside = player_in_circle(img, find_door_circles(img))
            if inside and not was_inside:                                       # just stepped in: the door is picked and the player is held for a moment
                locked_until = now + HAUNTLET_LOCK_WAIT
                walk_until = max(walk_until, now + HAUNTLET_CIRCLE_WAIT)            # (and the room only changes some seconds later: not a walk that got nowhere)
                if run.picked is None and target_index is not None:
                    run.picked = target_index
                    m.logger.debug(f"Hauntlet 2: in the circle of door {target_index + 1} - picked")
            was_inside = inside
            if now < locked_until:
                still_since, last_view = None, None
                m.wait_interruptible(HAUNTLET_LOOK_INTERVAL)
                continue
            if target_index is not None and not run.fork and now >= steer_at:      # the way forward isn't always towards the door (a room seen at an angle): steer
                steer_at = now + HAUNTLET_STEER_EVERY
                steer = hauntlet_steer(img, target_index)
                if steer is not None:
                    m.logger.debug(f"Hauntlet 2: off the door - a step {steer[0]} for {steer[1]:.2f}s")
                    m.key_up(cfg.KEYS["forward"])                # one key at a time
                    m.hold_key(*steer)
                    m.key_down(cfg.KEYS["forward"])
                    still_since, last_view = None, None
                    continue
            view = hauntlet_view(img)
            if last_view is not None and float(np.abs(view - last_view).mean()) < HAUNTLET_STILL_LEVEL:
                still_since = now if still_since is None else still_since
            else:
                still_since = None
            last_view = view
            if still_since is not None and now - still_since >= HAUNTLET_STILL_AFTER and not inside:        # (standing in the door's circle is what is wanted: no jumping out of it)
                last_key = hauntlet_unstick(img, target_index, tries, last_key)
                tries += 1
                still_since, last_view = None, None
                continue
            m.wait_interruptible(HAUNTLET_LOOK_INTERVAL)
    finally:
        m.key_up(cfg.KEYS["forward"])
        run.walked_at = m.time.time()
        run.stuck = 0 if arrived else run.stuck + 1
    if not arrived:
        mark_run_capture(f"Hauntlet 2: the walk in room {run.room} got nowhere")
    return False

def play_hauntlet_2(deadline: float, watch: MinigameWatch) -> bool:
    """Hauntlet 2: wait for it to start, then room after room choose a door and walk to it (HauntletRun,
    hauntlet_choose(), hauntlet_walk()) until the minigame finishes or the overlay says it's over. True if it
    finished before `deadline`. See docs/ARCHITECTURE.md#playing-hauntlet-2.
    """
    wait_for_minigame_start(watch, "hauntlet_2")
    zoom_out()
    if HAUNTLET_START_FORWARD > 0:
        m.wait_interruptible(HAUNTLET_START_DELAY)                   # the doors of the entrance hall open after a moment...
        m.hold_key(cfg.KEYS["forward"], HAUNTLET_START_FORWARD)    # ...and the way out is a step or two ahead
    run = HauntletRun()
    try:
        while m.time.time() < deadline and not watch.problem:
            img = minigame_look(watch)
            if detect_minigame_finished(img):
                return True
            doors = run.look(img, m.time.time())
            if doors is None:
                m.wait_interruptible(HAUNTLET_LOOK_INTERVAL)
                continue
            if len(doors) == 1 and run.room and run.room > 1 and not run.doors_missing_reported:   # not the entrance hall (room 1): keep the screenshot
                run.doors_missing_reported = True
                m.log_failure(f"Hauntlet 2: no doors found in room {run.room}")
            if len(doors) == 1 and run.room and run.room > 1 and run.turns < HAUNTLET_SEARCH_TURNS:
                if run.turns == 0:
                    zoom_out()                                                  # perhaps the camera is too close to see the TVs...
                rotate_camera(HAUNTLET_CAMERA_DRAG, 0)                      # ...or turned away from them: look around, then look again
                run.turns += 1
                continue
            index, img = hauntlet_choose(img, doors, run, watch)
            if len(doors) == 2:
                doorways = find_fork_doorways(img)
                if doorways is None:                                            # the doorways are too far to be seen from the entrance: walk on until both are
                    if hauntlet_walk(None, 0.0, run, watch, until_doorways=True):
                        return True
                    img = minigame_look(watch)
                    if read_hauntlet_room(img) not in (None, run.room):         # (the walk went through one)
                        continue
                    doorways = find_fork_doorways(img)
                if doorways is not None:
                    doors = [HauntletDoor(x, 0, None) for x in doorways]
                else:
                    mark_run_capture(f"Hauntlet 2: the fork's doorways of room {run.room} were not seen")
            direction, duration = hauntlet_strafe(img, doors, index)
            if hauntlet_walk(direction, duration, run, watch, until_doors=len(doors) == 1, target_index=index if len(doors) >= 2 else None):
                return True
            if len(doors) == 1:
                run.walked_at = None                                            # whatever is past the doorway is looked at at once
    finally:
        if run.room is not None:
            m.log_run_event(f"Hauntlet 2: got as far as room {run.room}")
            event.minigame_detail["room"] = run.room
    return False

def ghost_character_box(hsv: Image) -> Optional[tuple[int, int, int, int]]:
    """Where the character (and its pet, hoodie and beam) is when the camera is close to it - pulled in by a wall, as it is in the
    dark of the boss fight - and its cyan clothes are blobs as big as ghosts: the glowing pink emblem of its backpack
    (GHOST_EMBLEM_HSV, at least GHOST_EMBLEM_MIN px, in GHOST_EMBLEM_BOX) gives the place and, by its size, how close the camera is;
    the box is GHOST_SELF_SCALE times the emblem's size (its side's square root, at least GHOST_SELF_MIN_UNIT) to the sides, up and down.
    None when the emblem isn't seen: the camera is far, and GHOST_IGNORE_BOXES' small box does."""
    left, top, right, bottom = GHOST_EMBLEM_BOX
    mask = cv2.inRange(hsv[top:bottom, left:right], GHOST_EMBLEM_HSV[0], GHOST_EMBLEM_HSV[1])
    count, _, stats, centres = cv2.connectedComponentsWithStats(mask)
    best = max(range(1, count), key=lambda i: stats[i, cv2.CC_STAT_AREA], default=None)
    if best is None or stats[best, cv2.CC_STAT_AREA] < GHOST_EMBLEM_MIN:
        return None
    x, y = left + int(centres[best][0]), top + int(centres[best][1])
    unit = max(float(np.sqrt(stats[best, cv2.CC_STAT_AREA])), GHOST_SELF_MIN_UNIT)
    side, up, down = GHOST_SELF_SCALE
    return int(x - side * unit), int(y - up * unit), int(x + side * unit), int(y + down * unit)

def find_ghost_blobs(img: Image, boss: bool = False) -> list[tuple[int, int, int]]:
    """(x, y, area) of every ghost in `img`, biggest first: the connected pieces of the translucent cyan the ghosts and the
    boss are drawn in (GHOST_COLOR), at least GHOST_MIN_AREA px, not in the HUD (GHOST_IGNORE_BOXES) or on the character -
    and, in the boss phase (`boss`), not on the boss's health bar, whose crest and icons are cyan too."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.bitwise_or(cv2.inRange(hsv, *GHOST_COLOR), cv2.inRange(hsv, *GHOST_COLOR_FAR))
    closeup = ghost_character_box(hsv)
    for left, top, right, bottom in GHOST_IGNORE_BOXES + ((GHOST_BOSS_BAR_IGNORE,) if boss else ()) + ((closeup,) if closeup else ()):
        mask[max(0, top):bottom, max(0, left):right] = 0
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((GHOST_CLOSE, GHOST_CLOSE), np.uint8))
    count, _, stats, centres = cv2.connectedComponentsWithStats(mask, connectivity=8)
    blobs = [(int(centres[i][0]), int(centres[i][1]), int(stats[i, cv2.CC_STAT_AREA])) for i in range(1, count)
             if stats[i, cv2.CC_STAT_AREA] >= GHOST_MIN_AREA]
    return sorted(blobs, key=lambda blob: -blob[2])

def find_ghost_alerts(img: Image) -> list[tuple[int, int, int]]:
    """(x, y, height) of every white "!" in `img` - the mark over furniture a ghost hides in: a white bar with a white dot under it
    (GHOST_ALERT_*), whatever the camera's distance, left to right."""
    top, bottom = GHOST_ALERT_BAND
    level = GHOST_ALERT_WHITE
    white = cv2.inRange(img[top:bottom], (level, level, level), (255, 255, 255))
    count, _, stats, _ = cv2.connectedComponentsWithStats(white, connectivity=8)
    pieces = [tuple(int(v) for v in stats[i]) for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] >= GHOST_ALERT_MIN_PIECE]
    found = []
    for x, y, w, h, area in pieces:
        if h < GHOST_ALERT_ASPECT * w or h < GHOST_ALERT_MIN_HEIGHT or area < GHOST_ALERT_BAR_FILL * w * h:
            continue                                                                   # not a bar
        for x2, y2, w2, h2, area2 in pieces:
            if (y + h < y2 <= y + GHOST_ALERT_DOT_GAP * h and abs((x2 + w2 / 2) - (x + w / 2)) <= GHOST_ALERT_DOT_SHIFT * w + GHOST_ALERT_DOT_SLACK
                    and GHOST_ALERT_DOT_WIDTH[0] * w <= w2 <= GHOST_ALERT_DOT_WIDTH[1] * w
                    and h2 <= GHOST_ALERT_DOT_ASPECT * w2 + 2 and area2 >= GHOST_ALERT_DOT_FILL * w2 * h2):                    # a dot under it
                found.append((x + w // 2, top + y + h // 2, h))
                break
    return sorted(found)

def ghost_boss_bar_visible(img: Image) -> bool:
    """Whether the boss's health bar (a gold frame along the top, GHOST_BOSS_BAR_*) is on screen: the boss phase."""
    left, top, right, bottom = GHOST_BOSS_BAR_BOX
    low, high = GHOST_BOSS_BAR_GOLD
    gold = cv2.inRange(cv2.cvtColor(img[top:bottom, left:right], cv2.COLOR_BGR2HSV), low, high)
    return int(np.count_nonzero(gold)) >= GHOST_BOSS_BAR_MIN

def ghost_boss_shielded(img: Image) -> bool:
    """Whether the boss, its bar being up, can't be hurt now: the bar shows two or three ghost icons instead of its
    cyan health (no GHOST_BOSS_FILL pixels), the boss itself is dim and small ghosts fly in - they are what is
    to be caught then. (The bar's health is down to a sliver at the very end, but not nothing.)"""
    left, top, right, bottom = GHOST_BOSS_BAR_BOX
    low, high = GHOST_BOSS_FILL
    fill = cv2.inRange(cv2.cvtColor(img[top:bottom, left:right], cv2.COLOR_BGR2HSV), low, high)
    return int(np.count_nonzero(fill)) < GHOST_BOSS_FILL_MIN

def choose_ghost_target(blobs: list[tuple[int, int, int]], boss: bool, previous: Optional[Point]) -> Optional[Point]:
    """Where to aim, from `blobs` (find_ghost_blobs()): when the boss can be hurt (`boss`) the biggest one (the boss), else the
    one that is still the one aimed at (`previous`, if a ghost is within GHOST_STICK px of it - they move, and aiming at
    another each look would lose them all), else the one nearest the character (GHOST_CHARACTER). None if there are none."""
    if not blobs:
        return None
    if boss:
        return blobs[0][:2]
    if previous is not None:
        near = min(blobs, key=lambda b: (b[0] - previous[0]) ** 2 + (b[1] - previous[1]) ** 2)
        if (near[0] - previous[0]) ** 2 + (near[1] - previous[1]) ** 2 <= GHOST_STICK ** 2:
            return near[:2]
    cx, cy = GHOST_CHARACTER
    return min(blobs, key=lambda b: (b[0] - cx) ** 2 + (b[1] - cy) ** 2)[:2]

def start_camera_drag() -> None:
    """Move the mouse to the middle of the screen and press the right button there. The drag that follows moves the cursor by
    hundreds of px, and a right button released over the window's border or title bar opens its menu and takes the focus from
    the game - which is where the cursor was left by whatever had been aimed at before."""
    m.pydirectinput.moveTo(*m.to_screen(cfg.REFERENCE_CENTER_X, cfg.REFERENCE_CENTER_Y))
    m.mouse_down("right")

def tilt_camera_down() -> None:
    """Drag the mouse down with the right button held, GHOST_START_TILT px: the camera rises behind the character, for a view
    from above, over GHOST_CAMERA_DRAG_DURATION seconds in GHOST_CAMERA_DRAG_STEPS moves."""
    start_camera_drag()
    try:
        for _ in range(GHOST_CAMERA_DRAG_STEPS):
            m.pydirectinput.moveRel(0, GHOST_START_TILT // GHOST_CAMERA_DRAG_STEPS, relative=True)
            m.wait_interruptible(GHOST_CAMERA_DRAG_DURATION / GHOST_CAMERA_DRAG_STEPS)
    finally:
        m.pydirectinput.mouseUp(button="right")

def far_from_character(box: tuple[int, int, int, int], minimum: float) -> Point:
    """A random point in `box` at least `minimum` px from the character (GHOST_CHARACTER); the middle of the farthest corner if the box has none."""
    left, top, right, bottom = box
    cx, cy = GHOST_CHARACTER
    for _ in range(50):
        x, y = random.randint(left, right), random.randint(top, bottom)
        if (x - cx) ** 2 + (y - cy) ** 2 >= minimum ** 2:
            return x, y
    return max(((left, top), (right, top), (left, bottom), (right, bottom)), key=lambda c: (c[0] - cx) ** 2 + (c[1] - cy) ** 2)

def ghost_chase_key(target: Point) -> Optional[str]:
    """The key to walk towards `target` with: W if it is above the character (GHOST_CHARACTER), S below, A/D left/right - along the
    axis it is farther off on - or None when it is no more than GHOST_TOWARD_MARGIN px off on either: near enough for the beam."""
    dx, dy = target[0] - GHOST_CHARACTER[0], target[1] - GHOST_CHARACTER[1]
    if max(abs(dx), abs(dy)) <= GHOST_TOWARD_MARGIN:
        return None
    if abs(dx) > abs(dy):
        return cfg.KEYS["right"] if dx > 0 else cfg.KEYS["left"]
    return cfg.KEYS["back"] if dy > 0 else cfg.KEYS["forward"]

def rotate_camera(drag: tuple[int, int] = GHOST_CAMERA_DRAG, tilt: int = GHOST_CAMERA_TILT, direction: Optional[int] = None) -> None:
    """Turn the camera: hold the right mouse button and drag the mouse sideways (`direction` -1 or 1, else a random way; `drag` = least and most px, and
    up to `tilt` px up or down too) over GHOST_CAMERA_DRAG_DURATION seconds, in GHOST_CAMERA_DRAG_STEPS moves, as Roblox wants a drag."""
    dx = (direction or random.choice((-1, 1))) * random.randint(*drag)
    dy = random.randint(-tilt, tilt)
    start_camera_drag()
    try:
        for _ in range(GHOST_CAMERA_DRAG_STEPS):
            m.pydirectinput.moveRel(dx // GHOST_CAMERA_DRAG_STEPS, dy // GHOST_CAMERA_DRAG_STEPS, relative=True)
            m.wait_interruptible(GHOST_CAMERA_DRAG_DURATION / GHOST_CAMERA_DRAG_STEPS)
    finally:
        m.pydirectinput.mouseUp(button="right")

def furniture_outlined(before: Image, after: Image, point: Point) -> bool:
    """Whether the mouse, moved to `point` between the screenshots `before` and `after`, is over a piece of furniture the beam can
    be held on, which is highlighted (a pale outline) near the mouse: at least GHOST_OUTLINE_MIN pixels within GHOST_OUTLINE_RADIUS px of `point`
    have turned pale (every channel at least GHOST_OUTLINE_WHITE) that weren't, or weren't beside a pale pixel, before."""
    x, y = point
    r = GHOST_OUTLINE_RADIUS
    top, left = max(0, y - r), max(0, x - r)
    was = (before[top:y + r, left:x + r].min(axis=2) >= GHOST_OUTLINE_WHITE).astype(np.uint8)
    now = after[top:y + r, left:x + r].min(axis=2) >= GHOST_OUTLINE_WHITE
    new = now & (cv2.dilate(was, np.ones((5, 5), np.uint8)) == 0)
    return int(np.count_nonzero(new)) >= GHOST_OUTLINE_MIN

def ring_point(centre: Point, box: tuple[int, int, int, int], radii: tuple[int, int]) -> Point:
    """A random point GHOST_RING-ish px (`radii`, nearest and farthest) from `centre`, pulled into `box`."""
    angle, radius = random.uniform(0, 2 * np.pi), random.uniform(*radii)
    left, top, right, bottom = box
    return (min(max(int(centre[0] + radius * np.cos(angle)), left), right), min(max(int(centre[1] + radius * np.sin(angle)), top), bottom))

def find_furniture(watch: MinigameWatch, around: Optional[Point] = None, avoid: Sequence[Point] = ()) -> Point:
    """Somewhere the beam can be held on furniture: the mouse is moved to up to GHOST_FURNITURE_PROBES places - random ones in
    GHOST_FURNITURE_BOX far from the character (far_from_character()), or, with `around`, ones in a ring of GHOST_RING px around that point
    (a ghost that is hiding in furniture) - none within GHOST_BAD_SPOT_RADIUS px of any of `avoid`, a look at each (furniture_outlined()),
    and the first one that gets the white outline is it; none did, the last place tried."""
    def place() -> Point:
        point = (0, 0)
        for _ in range(50):
            point = (ring_point(around, GHOST_FURNITURE_BOX, GHOST_RING) if around is not None
                     else far_from_character(GHOST_FURNITURE_BOX, GHOST_FURNITURE_MIN_DISTANCE))
            if all((point[0] - x) ** 2 + (point[1] - y) ** 2 >= GHOST_BAD_SPOT_RADIUS ** 2 for x, y in avoid):
                break
        return point
    point = place()
    before = minigame_look(watch)
    for probe in range(GHOST_FURNITURE_PROBES):
        if probe:
            point = place()
        m.pydirectinput.moveTo(*m.to_screen(*point))
        m.wait_interruptible(GHOST_OUTLINE_WAIT)
        after = minigame_look(watch)
        if furniture_outlined(before, after, point):
            return point
        before = after
    return point

class GhostFeedback:
    """What the score says about where the beam is held, fed one screenshot at a time (look()): the digits of the score pill, as a mask of
    their white pixels, differ from the last look's when the score changed - which means the beam is on a ghost. Nothing is concluded
    while the banner isn't on screen (`visible`). See docs/ARCHITECTURE.md#playing-ghost-gallery."""

    def __init__(self, now: float) -> None:
        self.digits: Optional[np.ndarray] = None            # the white pixels of the score at the last look; None if the banner wasn't there
        self.visible = False
        self.scored_at = now                                # the last time the score changed (or the start)
        self.spot_since = now                               # since when the beam has been on what it is on
        self.bad: list[tuple[Point, float]] = []            # places that scored nothing, and until when to keep away from them
        self.skipped: list[list[float]] = []                # [x, y, until]: blobs the score says are no ghosts, where they were at the last look
        self.hide_tries = 0                                 # furniture tried around the blob the beam is on, without a score

    def look(self, img: Image, now: float) -> bool:
        """Take in a screenshot. True if the score changed since the look before."""
        left, top, right, bottom = GHOST_SCORE_BOX
        box = img[top:bottom, left:right]
        pill = cv2.inRange(cv2.cvtColor(box, cv2.COLOR_BGR2HSV), GHOST_SCORE_PILL[0], GHOST_SCORE_PILL[1])
        self.visible = int(np.count_nonzero(pill)) >= GHOST_SCORE_PILL_MIN
        if not self.visible:
            self.digits = None
            return False
        digits = box.min(axis=2) >= GHOST_SCORE_WHITE
        changed = self.digits is not None and int(np.count_nonzero(digits ^ self.digits)) >= GHOST_SCORE_CHANGE
        self.digits = digits
        if changed:
            self.scored_at = self.spot_since = now
            self.hide_tries = 0
        return changed

    def new_spot(self, now: float, new_target: bool = False) -> None:
        """The beam goes somewhere else: it has a trial of its own (and with another blob - `new_target` - the furniture tried is counted afresh)."""
        self.spot_since = now
        if new_target:
            self.hide_tries = 0

    def skip(self, point: Point, now: float) -> None:
        """What is at `point` is no ghost: left alone for GHOST_SKIP_TIME seconds, wherever it goes."""
        self.skipped.append([float(point[0]), float(point[1]), now + GHOST_SKIP_TIME])
        self.hide_tries = 0

    def follow_skipped(self, blobs: list[tuple[int, int, int]], now: float) -> list[tuple[int, int, int]]:
        """`blobs` without the skipped ones; each skipped one moves to the blob it has become (the nearest within GHOST_STICK px), so that a pet that
        flies about stays skipped, and is dropped when its time is up."""
        self.skipped = [entry for entry in self.skipped if entry[2] > now]
        kept = []
        for blob in blobs:
            for entry in self.skipped:
                reach = max(GHOST_SKIP_RADIUS, float(blob[2]) ** 0.5)
                if (blob[0] - entry[0]) ** 2 + (blob[1] - entry[1]) ** 2 <= reach ** 2:
                    entry[0], entry[1] = float(blob[0]), float(blob[1])
                    break
            else:
                kept.append(blob)
        return kept

    def trial_over(self, now: float, trial: Optional[float] = None) -> bool:
        """The beam has been where it is for GHOST_SPOT_TRIAL seconds (or `trial`) without a score."""
        return self.visible and now - max(self.scored_at, self.spot_since) >= (GHOST_SPOT_TRIAL if trial is None else trial)

    def stalled(self, now: float) -> bool:
        """No score for GHOST_STALL seconds."""
        return self.visible and now - self.scored_at >= GHOST_STALL

    def mark_bad(self, point: Point, now: float) -> None:
        self.bad.append((point, now + GHOST_BAD_SPOT_TIME))

    def avoid(self, now: float) -> list[Point]:
        """The places to keep away from for now."""
        self.bad = [(point, until) for point, until in self.bad if until > now]
        return [point for point, _ in self.bad]

def play_ghost_gallery(deadline: float, watch: MinigameWatch) -> bool:
    """Ghost Gallery: once its overlay shows (or the start wait is over), look at the screen every GHOST_LOOK_INTERVAL,
    aim the mouse at a ghost (choose_ghost_target()) and hold the button down on it, until the minigame finishes or the
    overlay says it's over. It begins by zooming the camera out and tilting it down for a better view. In the boss phase it is the boss, but while the boss's bar shows ghost icons it can't be hurt
    (ghost_boss_shielded()) and the small ghosts are aimed at instead. With no ghost in sight, before the boss phase the beam
    is held on random furniture (ghosts hide there), and the camera is turned every GHOST_CAMERA_PERIOD to find some;
    it jumps now and then, and walks about a step at a time. The score is watched (GhostFeedback): a ghost the beam scores nothing on is
    taken to hide, and the furniture around it is tried; no score for GHOST_STALL seconds moves the player and turns the camera.
    True if the minigame finished before `deadline`. See docs/ARCHITECTURE.md#playing-ghost-gallery.
    """
    wait_for_minigame_start(watch, "ghost_gallery")
    minigame_wait(GHOST_INTRO_WAIT, watch)
    zoom_out()
    tilt_camera_down()
    m.hold_key(cfg.KEYS["forward"], GHOST_START_FORWARD)           # into the middle of the hall, from where the game puts the player
    target: Optional[Point] = None
    held = False
    moving: Optional[str] = None                     # the key being held to walk, and until when
    regrab_at = m.time.time() + GHOST_REGRAB_PERIOD   # when the beam is let go of and pressed again
    chasing: Optional[str] = None                    # the key held to get to what is aimed at (a `moving` that has no end)
    turn: Optional[int] = None                       # the way the camera is being turned to find the boss (-1 or 1)
    move_until = 0.0
    next_move = m.time.time() + GHOST_MOVE_PERIOD
    phase = "ghosts"
    furniture: Optional[Point] = None
    furniture_until = 0.0
    feedback = GhostFeedback(m.time.time())            # what the score says about where the beam is
    hide_spot: Optional[Point] = None                # furniture near a ghost the score says is hiding, which the beam is held on until `hide_until`
    hide_until = 0.0
    last_target: Optional[Point] = None
    last_turn = last_seen = m.time.time()
    next_jump = m.time.time() + random.uniform(GHOST_JUMP_MIN, GHOST_JUMP_MAX)

    def aim(point: Point) -> None:
        nonlocal held
        m.pydirectinput.moveTo(*m.to_screen(*point))
        if not held:
            m.mouse_down()
            held = True

    def let_go() -> None:
        nonlocal held
        if held:
            m.pydirectinput.mouseUp()
            held = False

    def stop_walking() -> None:
        nonlocal moving
        if moving is not None:
            m.key_up(moving)
            moving = None

    try:
        while m.time.time() < deadline and not watch.problem:
            img = minigame_look(watch)
            if detect_minigame_finished(img):
                return True
            if feedback.look(img, m.time.time()):
                hide_spot, hide_until = None, 0.0                           # it scored: whatever the beam is on is right
            bar = ghost_boss_bar_visible(img)
            if held and m.time.time() >= regrab_at:                           # knocked down, the player lets go of the beam and has to press again: do both now and then
                let_go()
                regrab_at = m.time.time() + GHOST_REGRAB_PERIOD
            now_phase = ("shielded" if ghost_boss_shielded(img) else "boss") if bar else "ghosts"
            if now_phase != phase:
                m.logger.debug(f"Ghost Gallery: {dict(ghosts='the boss phase is over', boss='the boss can be hurt', shielded='the boss is shielded: after the small ghosts')[now_phase]}")
                phase = now_phase
            blobs = find_ghost_blobs(img, bar)
            if phase == "shielded":                                         # only the small ghosts count: what is bigger is the beam's glow or the boss's fog
                blobs = [blob for blob in blobs if blob[2] <= GHOST_SMALL_MAX]
            if phase == "ghosts":
                alerts = find_ghost_alerts(img)                             # a "!" is a ghost hiding in the furniture below it: that comes first
                if alerts:
                    blobs = [(x, y + int(GHOST_ALERT_BELOW * height), GHOST_MIN_AREA) for x, y, height in alerts]
            blobs = feedback.follow_skipped(blobs, m.time.time())
            target = choose_ghost_target(blobs, phase == "boss" and bool(blobs) and blobs[0][2] >= GHOST_BOSS_MIN, target)   # (the boss is a big one, else it's the ghosts that are there)
            now = m.time.time()
            if target is not None and (last_target is None or (target[0] - last_target[0]) ** 2 + (target[1] - last_target[1]) ** 2 > GHOST_STICK ** 2):
                feedback.new_spot(now, True)                                # another ghost: it has a trial of its own
            last_target = target
            if phase == "ghosts" and feedback.stalled(now):                 # nothing has scored for a long time (a view blocked by a pillar, a beam on the floor): somewhere else, looking another way
                m.logger.debug(f"Ghost Gallery: no score for {GHOST_STALL:g}s - moving and turning the camera")
                mark_run_capture(f"Ghost Gallery: no score for {GHOST_STALL:g}s")
                let_go()
                stop_walking()
                chasing = None
                m.hold_key(random.choice(cfg.MOVE_KEYS), GHOST_STALL_WALK)
                m.pydirectinput.press(cfg.KEYS["jump"])
                rotate_camera(GHOST_STALL_TURN, 0, random.choice((-1, 1)))
                feedback.scored_at = last_turn = m.time.time()
                feedback.new_spot(last_turn)
                furniture, hide_spot, hide_until = None, None, 0.0
                now = m.time.time()
            elif phase == "ghosts" and target is not None and feedback.trial_over(now):   # a ghost the beam on it doesn't score: it is hiding in furniture, which is somewhere around it
                mark_run_capture("Ghost Gallery: the beam on a ghost scores nothing")
                if hide_spot is not None:
                    feedback.mark_bad(hide_spot, now)                       # (the furniture tried last scored nothing either)
                    feedback.hide_tries += 1
                if feedback.hide_tries >= GHOST_HIDE_TRIES:             # the blob and the furniture around it score nothing: no ghost (the pet)
                    feedback.skip(target, now)
                    hide_spot, hide_until, target, last_target = None, 0.0, None, None
                    m.logger.debug("Ghost Gallery: what the beam is on scores nothing, furniture or not - left alone")
                else:
                    hide_spot = find_furniture(watch, around=target, avoid=feedback.avoid(now))
                    hide_until = m.time.time() + GHOST_SPOT_TRIAL
                    feedback.new_spot(m.time.time())
                now = m.time.time()
            elif phase == "boss" and target is not None and feedback.trial_over(now, GHOST_BOSS_TRIAL):
                mark_run_capture("Ghost Gallery: the beam on what is taken for the boss scores nothing")
                feedback.skip(target, now)                                  # fog, a light shaft: not the boss
                m.logger.debug("Ghost Gallery: the biggest blob scores nothing - not the boss, left alone")
                target, last_target = None, None
                now = m.time.time()
            if target is not None:
                last_seen = now
                aim(hide_spot if hide_spot is not None and now < hide_until else target)
            elif phase == "ghosts":                                         # nothing to be seen before the boss: try the furniture
                if furniture is None or now >= furniture_until:
                    furniture = find_furniture(watch)                       # (the mouse is moved around with the beam down)
                    furniture_until = m.time.time() + GHOST_FURNITURE_PERIOD
                aim(furniture)
            if target is not None:
                turn = None                                                 # the next search for the boss turns a way of its own
            elif phase != "ghosts":                                         # the boss, or the ghosts that shield it, out of sight: turn the camera until they are
                turn = turn or random.choice((-1, 1))
                rotate_camera(GHOST_BOSS_TURN_DRAG, 0, turn)
                last_turn = m.time.time()
            elif now - max(last_turn, last_seen) >= GHOST_CAMERA_PERIOD:
                turn = turn or random.choice((-1, 1))                       # one way round, not back and forth: a sweep sees the whole hall
                rotate_camera(direction=turn)
                last_turn = m.time.time()
            chase = ghost_chase_key(target) if target is not None else None   # always towards the ghost or the boss, until it is near
            if chase != chasing:
                stop_walking()
                chasing = chase
                if chase is not None:
                    moving = chase
                    m.key_down(chase)
                move_until = now
            if chasing is None and target is None and phase == "ghosts":     # nothing in sight: walking about, a step at a time, without stopping the aiming
                if moving is not None and now >= move_until:
                    stop_walking()
                    next_move = now + GHOST_MOVE_PERIOD
                elif moving is None and now >= next_move:
                    moving = random.choice(cfg.MOVE_KEYS)
                    m.key_down(moving)
                    move_until = m.time.time() + random.uniform(GHOST_MOVE_STEP_MIN, GHOST_MOVE_STEP_MAX)      # (`now` is from before a search for furniture, which can take longer than a step)
            elif chasing is None:
                stop_walking()
            if now >= next_jump:
                m.pydirectinput.press(cfg.KEYS["jump"])
                next_jump = now + random.uniform(GHOST_JUMP_MIN, GHOST_JUMP_MAX)
            m.wait_interruptible(GHOST_LOOK_INTERVAL)
    finally:
        let_go()
        stop_walking()
    return False

def stitches_ring_angle(img: Image) -> Optional[float]:
    """The slope, in degrees, of the gold line along the ring walkway's inner edge in `img` (Stitches' Spotlight): 0 when it runs straight across the screen,
    which it does when the camera looks at the middle of the ring; positive when it falls to the right (the middle is to the right), negative when it
    rises. None when too little of it shows (STITCHES_LINE_MIN_PIXELS: it is not seen in the red of a searchlight, or with the camera turned from the ring)."""
    left, top, right, bottom = STITCHES_LINE_BOX
    hsv = cv2.cvtColor(img[top:bottom, left:right], cv2.COLOR_BGR2HSV)
    hue, saturation, value = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    mask = ((hue >= STITCHES_LINE_HUE[0]) & (hue <= STITCHES_LINE_HUE[1]) & (saturation >= STITCHES_LINE_MIN_SATURATION)
            & (value >= STITCHES_LINE_VALUE[0]) & (value <= STITCHES_LINE_VALUE[1]))
    ys, xs = np.nonzero(mask)
    if len(xs) < STITCHES_LINE_MIN_PIXELS:
        return None
    dx, dy = cv2.fitLine(np.column_stack([xs, ys]).astype(np.float32), cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()[:2]
    angle = math.degrees(math.atan2(dy, dx))
    return angle - 180 if angle > 90 else angle + 180 if angle < -90 else angle

class RingCamera:
    """What turns the camera towards the middle of Stitches' Spotlight's ring, from the angle of the ring's edge line (stitches_ring_angle()). Walking
    round the ring needs the camera turned all the time, at a steady rate, so besides the turn the angle asks for (proportional: up to STITCHES_TURN_HOLD_MAX
    seconds of key for a line 90 degrees off) it learns that rate (`bias`: the share of the time the turn key is held, grown by STITCHES_TURN_BIAS_GAIN
    per degree per second while the line is off by more than STITCHES_LINE_DEADBAND) and keeps turning at it while the line is out of sight, and after
    STITCHES_LOST_AFTER seconds without it sweeps the camera round to find it. `keys` are the (left, right) arrow keys; swap_keys() when a test turn
    shows they work the other way."""

    def __init__(self) -> None:
        self.keys = (cfg.KEYS["camera_left"], cfg.KEYS["camera_right"])
        self.bias = 0.0
        self.lost_since: Optional[float] = None

    def swap_keys(self) -> None:
        self.keys = (self.keys[1], self.keys[0])
        self.bias = -self.bias

    def decide(self, angle: Optional[float], now: float, interval: float) -> Optional[tuple[str, float]]:
        """(key, seconds to hold it) for this look, or None. `angle` is None when the line is not in sight."""
        if angle is None:
            if self.lost_since is None:
                self.lost_since = now
            seconds = STITCHES_TURN_HOLD_MAX if now - self.lost_since > STITCHES_LOST_AFTER else self.bias * interval
        else:
            self.lost_since = None
            error = angle if abs(angle) > STITCHES_LINE_DEADBAND else 0.0
            self.bias = max(-STITCHES_TURN_BIAS_MAX, min(STITCHES_TURN_BIAS_MAX, self.bias + STITCHES_TURN_BIAS_GAIN * error * interval))
            seconds = STITCHES_TURN_HOLD_MAX * max(-90.0, min(90.0, error)) / 90.0 + self.bias * interval
        seconds = max(-STITCHES_TURN_HOLD_MAX, min(STITCHES_TURN_HOLD_MAX, seconds))
        if abs(seconds) < STITCHES_TURN_MIN_HOLD:
            return None
        return self.keys[seconds > 0], abs(seconds)

def calibrate_ring_camera(camera: RingCamera, watch: MinigameWatch) -> None:
    """Find out which arrow key turns the camera which way, by turning with the right one for STITCHES_CALIBRATE_HOLD seconds and seeing the ring's line
    move: turning right must make a line that falls to the right (positive angle) level. If it moved the other way the camera's keys are swapped. Too small a
    move, or no line in sight before or after, is logged and the keys are left as they are."""
    before = stitches_ring_angle(minigame_look(watch))
    if before is None:
        m.logger.debug("Stitches' Spotlight: no ring line in sight to test the camera keys against")
        return
    m.hold_key(camera.keys[1], STITCHES_CALIBRATE_HOLD)
    minigame_wait(STITCHES_CALIBRATE_HOLD, watch)
    after = stitches_ring_angle(minigame_look(watch))
    if after is None or abs(after - before) < STITCHES_CALIBRATE_MIN:
        m.logger.debug(f"Stitches' Spotlight: the camera keys test showed nothing clear (line {before:.0f} -> {'not seen' if after is None else f'{after:.0f}'} degrees)")
        mark_run_capture("Stitches' Spotlight: the camera keys test was unclear")
        return
    if after > before:
        camera.swap_keys()
    m.logger.debug(f"Stitches' Spotlight: line {before:.0f} -> {after:.0f} degrees, the camera keys are {camera.keys}")

def play_stitches_spotlight(deadline: float, watch: MinigameWatch) -> bool:
    """Stitches' Spotlight: once its overlay shows, wait (at most STITCHES_INTRO_MAX seconds) for the ring's edge line - the game's intro camera looks at
    the sky first - test which way the arrow keys turn the camera (calibrate_ring_camera()), then walk round the ring by holding KEYS["right"] and, every
    STITCHES_CENTER_INTERVAL, turn the camera (RingCamera) until the line runs straight across the screen, i.e. the camera looks at the middle. KEYS["speed_boost"]
    is pressed every STITCHES_BOOST_INTERVAL. Nothing is read or avoided: the candy corn on the way is picked up by walking over it.
    True if the minigame finished before `deadline`. See docs/ARCHITECTURE.md#playing-stitches-spotlight."""
    wait_for_minigame_start(watch, "stitches_spotlight")
    intro_until = m.time.time() + STITCHES_INTRO_MAX
    while m.time.time() < intro_until and not watch.problem and stitches_ring_angle(minigame_look(watch)) is None:
        m.wait_interruptible(STITCHES_CENTER_INTERVAL)
    camera = RingCamera()
    calibrate_ring_camera(camera, watch)
    next_boost = m.time.time()
    m.key_down(cfg.KEYS["right"])
    try:
        while m.time.time() < deadline and not watch.problem:
            img = minigame_look(watch)
            if detect_minigame_finished(img):
                return True
            angle = stitches_ring_angle(img)
            turn = camera.decide(angle, m.time.time(), STITCHES_CENTER_INTERVAL)
            m.logger.debug(f"Stitches' Spotlight: ring line {'not seen' if angle is None else f'{angle:.0f} degrees'}, turn {turn if turn else 'none'}, bias {camera.bias:+.2f}")
            if turn is not None:
                m.hold_key(*turn)
            if m.time.time() >= next_boost:
                m.pydirectinput.press(cfg.KEYS["speed_boost"])
                next_boost = m.time.time() + STITCHES_BOOST_INTERVAL
            m.wait_interruptible(STITCHES_CENTER_INTERVAL)
    finally:
        m.key_up(cfg.KEYS["right"])
    return False

MINIGAME_PLAYERS = {"hauntlet_2": play_hauntlet_2, "ghost_gallery": play_ghost_gallery, "stitches_spotlight": play_stitches_spotlight}

def click_nice_until_gone() -> bool:
    """Click the finish screen's NICE! until it is gone (up to MINIGAME_FINISH_CLICK_TRIES).
    Returns True if it is. See docs/ARCHITECTURE.md#need-handlers.
    """
    for attempt in range(MINIGAME_FINISH_CLICK_TRIES):
        m.hover_click(*MINIGAME_FINISH_BUTTON_POS)
        m.wait_interruptible(MINIGAME_FINISH_WAIT)
        if not detect_minigame_finished(m.grab_screen()):
            return True
        m.logger.debug(f"Finish screen still up after click {attempt + 1}")
    m.log_failure("minigame finish screen would not close")
    return False

def screen_changed(before: Image, after: Image) -> bool:
    """Whether the view is a different one: the mean grey-level difference of the two (shrunk) screens is over MINIGAME_TELEPORT_CHANGE."""
    small = [cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cfg.SCREEN_CHANGE_SIZE, interpolation=cv2.INTER_AREA).astype(np.int16) for img in (before, after)]
    return bool(np.abs(small[0] - small[1]).mean() > MINIGAME_TELEPORT_CHANGE)

def minigame_teleported(before: Image) -> bool:
    """Wait up to MINIGAME_TELEPORT_WAIT seconds for the character to be teleported after the popup's Yes: the white screen of a
    teleport (detect_white_screen()) or a view changed from `before` (the screen as it was at the click; screen_changed())."""
    end = m.time.time() + MINIGAME_TELEPORT_WAIT
    while m.time.time() < end:
        m.wait_interruptible(MINIGAME_TELEPORT_POLL)
        img = m.grab_screen()
        if m.detect_white_screen(img) or screen_changed(before, img):
            return True
    return False

def accept_minigame_popup() -> bool:
    """Click the popup's Yes and check that it teleported the character (minigame_teleported()). If not and the popup is still
    up, the click didn't take: click again, up to MINIGAME_YES_TRIES clicks in all. False if it never did, or the popup is gone
    without a teleport (it was gone before the click, or the click was lost). See docs/ARCHITECTURE.md#need-handlers."""
    for attempt in range(MINIGAME_YES_TRIES):
        before = m.grab_screen()
        m.hover_click(*MINIGAME_POPUP_YES_POS)
        if minigame_teleported(before):
            return True
        if not detect_minigame_popup(m.grab_screen()):
            m.logger.debug("The minigame popup is gone and nothing teleported")
            return False
        m.logger.debug(f"The popup is still up after click {attempt + 1}")
    return False

def candy_corn_digit_templates() -> dict[int, Image]:
    """{digit: picture} for the saved HUD digits (CANDY_CORN_DIGIT_DIR), re-read when the folder changes."""
    directory = CANDY_CORN_DIGIT_DIR
    return _digit_templates(directory, os.path.getmtime(directory)) if os.path.isdir(directory) else {}

def read_candy_corn(img: Image) -> Optional[int]:
    """The candy corn total in the HUD of `img`, or None when it isn't readable: no counter on screen (at home, in a minigame), or a
    digit that matches none of the saved ones. See docs/ARCHITECTURE.md#candy-corn."""
    left, top, right, bottom = CANDY_CORN_BOX
    mask = cv2.inRange(img[top:bottom, left:right], CANDY_CORN_RED[0], CANDY_CORN_RED[1])
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    tall = sorted((int(stats[i, 0]), int(stats[i, 1]), int(stats[i, 2]), int(stats[i, 3])) for i in range(1, count)
                  if CANDY_CORN_DIGIT_HEIGHT[0] <= stats[i, 3] <= CANDY_CORN_DIGIT_HEIGHT[1])
    boxes = [box for box in tall if box[2] <= CANDY_CORN_DIGIT_MAX_WIDTH]
    templates = candy_corn_digit_templates()
    if not 1 <= len(boxes) <= CANDY_CORN_MAX_DIGITS or len(boxes) != len(tall) or not templates:
        return None
    if any(not CANDY_CORN_PITCH[0] <= b[0] - a[0] <= CANDY_CORN_PITCH[1] for a, b in zip(boxes, boxes[1:])):
        return None                                                          # a digit is missing, or something is between them
    total = 0
    for x, y, w, h in boxes:
        picture = cv2.resize(mask[y:y + h, x:x + w], CANDY_CORN_DIGIT_SIZE, interpolation=cv2.INTER_AREA)
        diffs = {digit: float(np.abs(picture.astype(int) - template.astype(int)).mean()) for digit, template in templates.items()}
        digit = min(diffs, key=lambda d: diffs[d])
        if diffs[digit] > CANDY_CORN_MAX_DIFF:
            return None
        total = total * 10 + digit
    return total

def save_minigame_result(name: str) -> Optional[str]:
    """Save the GAME OVER! screen of the minigame `name` (the card with what the run earned, in candy corn, and its score) as
    RUNS_DIR/results/run<N>_<name>_<time>.png, the newest MAX_RESULT_SHOTS kept. The path, or None. Never raises."""
    try:
        folder = os.path.join(cfg.RUNS_DIR, "results")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"run{m.state.run_number}_{name}_{m.time.strftime('%H%M%S')}.png")
        left, top, right, bottom = MINIGAME_RESULT_BOX
        if not cv2.imwrite(path, m.grab_screen()[top:bottom, left:right]):
            return None
        m.keep_newest_files(folder, MAX_RESULT_SHOTS, ".png")
        return path
    except Exception as error:
        m.logger.warning(f"Couldn't save the result screen: {error}")
        return None

def play_minigame(name: str) -> bool:
    """Take the popup's Yes, play `name` until its GAME OVER! screen (which every run ends on: what a run earned is the candy corn
    on that screen, which is saved, see save_minigame_result()), click NICE!, respawn. Gives up (a logged failure) if the overlay shows it over or never
    started, or after MINIGAME_MAX_DURATION. Counts as progress only if it ran to the end. See docs/ARCHITECTURE.md#need-handlers.
    """
    label = MINIGAME_LABELS.get(name, name)
    m.logger.info(f"Playing {label}")
    m.log_run_event(f"minigame started: {label}")
    # The handler this interrupted may be holding a key or the mouse button
    # down (hold_key(), the pet swipe); that must not carry into the minigame.
    m.release_all_inputs()
    if not accept_minigame_popup():
        m.log_failure(f"{label} could not be started (the popup's Yes did not teleport the character)")
        m.state.last_detection = m.time.time()
        m.respawn_character()
        return False
    started = m.time.time()
    failures_before = m.run_stats.failures
    watch = MinigameWatch(name)
    event.minigame_detail = {}
    before = read_candy_corn_settled(CANDY_CORN_BEFORE_TRIES)      # in the lobby the total is on screen (after the white of the teleport): what the run earned is the difference
    capture = start_run_capture(name)
    outcome = "interrupted"
    finished = False
    after: Optional[int] = None
    m.send_macro_window_to_back()                 # it covers part of the game's screen, and would be in the recording and the readers' way
    try:
        finished = MINIGAME_PLAYERS[name](started + MINIGAME_MAX_DURATION, watch)
        outcome = "finished (the game over screen)" if finished else (watch.problem or "did not reach the game over screen in time")
        after = read_candy_corn_settled()        # (the game over screen has the total too, counting up for a moment)
    finally:
        m.release_all_inputs()
        m.bring_macro_window_to_front()
        result = minigame_result(name, outcome, finished, m.time.time() - started, before, after, m.run_stats.failures - failures_before)
        finish_run_capture(capture, outcome, result)
    record_minigame_result(result)
    if finished:
        m.log_run_event(f"minigame finished: {label} ({m.time.time() - started:.0f}s)")
        save_minigame_result(name)
        click_nice_until_gone()
    elif watch.disconnected:
        m.log_run_event(f"{label} ended by a disconnect")      # (the failure is the rejoin's to log)
        m.state.last_detection = m.time.time()
        return False
    else:
        reason = watch.problem or f"did not finish within {MINIGAME_MAX_DURATION:g}s"
        m.log_failure(f"{label} {reason}")
    m.state.last_detection = m.time.time()      # no needs could be seen while it ran
    if finished:
        m.state.last_progress = m.time.time()   # a failed one (never started, left early) must not reset the no-progress rejoin
    m.respawn_character()
    return finished

def close_minigame_finish_screen(abandon: bool = True) -> bool:
    """If the finish screen is up, click NICE!; with `abandon` also count it as progress and
    respawn. Returns True if it was there. See docs/ARCHITECTURE.md#need-handlers.
    """
    if not detect_minigame_finished(m.grab_screen()):
        return False
    m.logger.debug("Minigame finish screen is up, clicking NICE!...")
    m.log_run_event("minigame finish screen closed")
    click_nice_until_gone()
    if abandon:
        m.state.last_progress = m.state.last_detection = m.time.time()
        m.respawn_character()
    return True

def minigame_popup(play: bool = True) -> Optional[str]:
    """Halloween only: play a popup's minigame if it is switched on, else dismiss it.
    Returns "played", "dismissed", "waiting" (a playable popup is held off while a need is in its end wait, at most MINIGAME_POPUP_END_WAIT
    seconds) or None. See docs/ARCHITECTURE.md#need-handlers.
    """
    img = m.grab_screen()
    if not detect_minigame_popup(img):
        event.popup_held_since = None
        return None
    name = identify_minigame(img)
    if name is not None and name in MINIGAME_PLAYERS and m.load_game_config().get(f"{name}_enabled", False):    # (one with no player yet is declined, switched on or not)
        if play and event.end_wait_need is not None:
            # A need is in its end wait: give it the time to clear (and count as resolved) before the minigame abandons it.
            event.popup_held_since = event.popup_held_since or m.time.time()
            if m.time.time() - event.popup_held_since < MINIGAME_POPUP_END_WAIT:
                return "waiting"
        event.popup_held_since = None
        if play:
            m.logger.debug(f"Minigame popup detected: playing {name}")
            play_minigame(name)
            return "played"
        m.logger.debug(f"Minigame popup detected ({name}), declining it for now...")
        m.hover_click(*MINIGAME_POPUP_NO_POS)
        return "dismissed"
    m.logger.debug(f"Minigame popup detected ({name or 'unknown'}), dismissing...")
    m.log_run_event(f"minigame popup dismissed ({name or 'unknown'})")
    m.hover_click(*MINIGAME_POPUP_DONT_SHOW_POS)   # "Do not show again this session"
    m.hover_click(*MINIGAME_POPUP_NO_POS)
    return "dismissed"

def start_run_capture(label: str) -> Optional[m.InputCapture]:
    """Start recording a minigame run (when MINIGAME_CAPTURE is on, and the hotkey's capture isn't running): a folder
    in RUN_CAPTURES_DIR, its frames kept within RUN_CAPTURE_ZIPS zips. None if there is no capture. A capture that can't be
    started is only a message: the minigame is played all the same."""
    if not MINIGAME_CAPTURE or (m.state.capture is not None and m.state.capture.running):
        return None
    try:
        os.makedirs(RUN_CAPTURES_DIR, exist_ok=True)
        capture = m.InputCapture(os.path.join(RUN_CAPTURES_DIR, f"{label}_{m.time.strftime('%Y%m%d_%H%M%S')}"), max_zips=RUN_CAPTURE_ZIPS)
        capture.start()
    except Exception as error:
        m.logger.warning(f"Couldn't start recording the minigame: {error}")
        return None
    event.run_capture = capture
    m.logger.debug(f"Recording the minigame: {capture.folder}")
    return capture

def run_capture_suffix(outcome: str, result: dict[str, Any]) -> str:
    """What is added to a recording's folder name when the run is over, to tell it from the others: the candy corn earned (`_2105cc`, or
    `_cc-unread`), the room reached in Hauntlet 2 (`_room10`), `_failed` for a run that did not finish and `_failures` for one that did
    with failures logged on the way."""
    earned = result.get("earned")
    parts = [f"{earned}cc" if earned is not None else "cc-unread"]
    if result.get("room") is not None:
        parts.append(f"room{result['room']}")
    if not outcome.startswith("finished"):
        parts.append("failed")
    elif result.get("failures"):
        parts.append("failures")
    return "_" + "_".join(parts)

def finish_run_capture(capture: Optional[m.InputCapture], outcome: str, result: Optional[dict[str, Any]] = None) -> None:
    """Stop a run capture (if there is one), note how the run ended in its info.json (with the fields of `result`, what the run earned, see
    minigame_result()), and zip it for sending."""
    if capture is None:
        return
    try:
        event.run_capture = None                                # (one at a time)
        if not outcome.startswith("finished"):
            capture.protect_tail(RUN_CAPTURE_TAIL)          # a run that went wrong: its last moments are what shows why
        capture.stopped_because = capture.stopped_because or "the minigame ended"
        m.logger.debug(f"Minigame recording stopped: {capture.stop()}")
        path = os.path.join(capture.folder, "info.json")
        with open(path, encoding="utf-8") as f:
            info = json.load(f)
        info["outcome"] = outcome
        info.update(result or {})
        with open(path, "w", encoding="utf-8") as f:
            json.dump(info, f, indent=1)
        renamed = capture.folder + run_capture_suffix(outcome, result or {})
        try:
            os.rename(capture.folder, renamed)                      # (before the zips are made: they are named after the folder)
            capture.folder = renamed
        except OSError as error:
            m.logger.warning(f"Couldn't rename the recording: {error}")
        m.zip_capture_in_background(capture.folder)
    except Exception as error:
        m.logger.warning(f"Couldn't finish recording the minigame: {error}")

def captures_to_keep(runs: list[tuple[str, Optional[int], float, bool]]) -> set[str]:
    """Of the recordings of one minigame, `runs` = (folder, candy corn earned or None, when it started, whether it failed - did not finish
    or had a failure logged), the ones to keep: the latest, the RUN_CAPTURES_FAILED latest that failed, the RUN_CAPTURES_BEST that earned
    most and the RUN_CAPTURES_WORST that earned least (a run whose candy corn wasn't read counts as the worst)."""
    if not runs:
        return set()
    def earned(run: tuple[str, Optional[int], float, bool]) -> tuple[int, float]:
        return (-1 if run[1] is None else run[1], run[2])
    ranked = sorted(runs, key=earned)
    keep = {max(runs, key=lambda run: run[2])[0]}
    failed = sorted((run for run in runs if run[3]), key=lambda run: run[2])
    keep |= {run[0] for run in failed[max(0, len(failed) - RUN_CAPTURES_FAILED):]}
    keep |= {run[0] for run in ranked[:RUN_CAPTURES_WORST]}
    keep |= {run[0] for run in ranked[len(ranked) - RUN_CAPTURES_BEST:]}
    return keep

def prune_run_captures(label: str) -> None:
    """Delete the recordings of minigame `label` in RUN_CAPTURES_DIR that captures_to_keep() doesn't keep, going by the `earned` and
    `started` that record_minigame_result() wrote into each one's info.json. Never raises."""
    try:
        if not os.path.isdir(RUN_CAPTURES_DIR):
            return                                                  # (recording is off, or nothing has been recorded yet)
        runs = []
        for name in os.listdir(RUN_CAPTURES_DIR):
            folder = os.path.join(RUN_CAPTURES_DIR, name)
            if not (name.startswith(label + "_") and os.path.isdir(folder)):
                continue
            try:
                with open(os.path.join(folder, "info.json"), encoding="utf-8") as f:
                    info = json.load(f)
            except (OSError, ValueError):
                info = {}
            try:
                started = m.time.mktime(m.time.strptime(name[len(label) + 1:][:15], "%Y%m%d_%H%M%S"))     # (the folder's name says when; its time changes as files go in and out)
            except ValueError:
                started = os.path.getmtime(folder)
            failed = info.get("finished") is False or bool(info.get("failures"))
            runs.append((folder, info.get("earned"), started, failed))
        for folder in {run[0] for run in runs} - captures_to_keep(runs):
            shutil.rmtree(folder, ignore_errors=True)
    except Exception as error:
        m.logger.warning(f"Couldn't tidy the recordings: {error}")

def minigame_result(name: str, outcome: str, finished: bool, seconds: float, before: Optional[int], after: Optional[int], failures: int = 0) -> dict[str, Any]:
    """What a minigame run came to: how it ended, and the candy corn it earned - the HUD total after less the total before, None when either
    wasn't read - the `failures` logged while it ran, plus what its player noted (event.minigame_detail: the room reached in Hauntlet 2)."""
    earned = after - before if before is not None and after is not None and after >= before else None
    return {"time": m.time.strftime("%Y-%m-%d %H:%M:%S"), "run": m.state.run_number, "minigame": name, "outcome": outcome, "finished": finished,
            "seconds": round(seconds), "candy_corn_before": before, "candy_corn_after": after, "earned": earned, "failures": failures, **event.minigame_detail}

def record_minigame_result(result: dict[str, Any]) -> None:
    """Note a finished minigame run's `result` (minigame_result()): a line of RUNS_DIR/results/results.jsonl and one in the run log - then the
    recordings of that minigame that are not among its best, its worst and its latest are deleted (prune_run_captures()). Never raises."""
    try:
        folder = os.path.join(cfg.RUNS_DIR, "results")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "results.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(result) + "\n")
        name, earned = result["minigame"], result["earned"]
        extra = "".join(f", {key} {value}" for key, value in event.minigame_detail.items())
        m.log_run_event(f"minigame result: {MINIGAME_LABELS.get(name, name)}: " + (f"{earned} candy corn" if earned is not None else "candy corn not read") + extra)
        prune_run_captures(name)
    except Exception as error:
        m.logger.warning(f"Couldn't note the result: {error}")

def read_candy_corn_settled(tries: int = 4) -> Optional[int]:
    """read_candy_corn() of the screen once two looks half a second apart agree (the total counts up for a moment after a run); if they
    never do, the last total read, and None if none was. Costs a second or so, up to `tries` looks of CANDY_CORN_SETTLE apart."""
    seen: Optional[int] = None
    for attempt in range(tries):
        now = read_candy_corn(m.grab_screen())
        if now is not None and now == seen:
            return now
        seen = now if now is not None else seen                     # (a look that reads nothing - a white flash - doesn't forget the one before)
        if attempt < tries - 1:
            m.wait_interruptible(CANDY_CORN_SETTLE)
    return seen

def room_digit(mask: Image) -> Image:
    """A digit's white pixels (`mask`, cropped to its bounding box), shrunk to HAUNTLET_DIGIT_SIZE, 0-255."""
    return cv2.resize(mask * 255, HAUNTLET_DIGIT_SIZE, interpolation=cv2.INTER_AREA)

@lru_cache(maxsize=None)
def _digit_templates(directory: str, modified: float) -> dict[int, Image]:
    found = {int(os.path.splitext(f)[0]): cv2.imread(os.path.join(directory, f), cv2.IMREAD_GRAYSCALE)
             for f in os.listdir(directory) if f.endswith(".png") and os.path.splitext(f)[0].isdigit()}
    return {digit: picture for digit, picture in found.items() if picture is not None}

def digit_templates() -> dict[int, Image]:
    """{digit: picture} for the saved room digits (HAUNTLET_DIGIT_DIR), re-read when the folder changes."""
    directory = HAUNTLET_DIGIT_DIR
    return _digit_templates(directory, os.path.getmtime(directory)) if os.path.isdir(directory) else {}

def read_hauntlet_room(img: Image) -> Optional[int]:
    """The room number on the ROOM panel of `img`, or None when it isn't readable (no panel - a transition, a
    close-up of a door - or a digit that matches none of the saved ones)."""
    left, top, right, bottom = HAUNTLET_ROOM_BOX
    mask = (img[top:bottom, left:right].min(axis=2) >= HAUNTLET_DIGIT_WHITE_LEVEL).astype(np.uint8)
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    boxes = sorted((int(stats[i, 0]), int(stats[i, 1]), int(stats[i, 2]), int(stats[i, 3]))
                   for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] >= HAUNTLET_DIGIT_MIN_AREA)
    templates = digit_templates()
    if not 1 <= len(boxes) <= HAUNTLET_ROOM_MAX_DIGITS or not templates:
        return None
    number = 0
    for x, y, w, h in boxes:
        picture = room_digit(mask[y:y + h, x:x + w])
        diffs = {digit: float(np.abs(picture.astype(int) - template.astype(int)).mean()) for digit, template in templates.items()}
        digit = min(diffs, key=lambda d: diffs[d])
        if diffs[digit] > HAUNTLET_DIGIT_MAX_DIFF:
            return None
        number = number * 10 + digit
    return number

@dataclass
class HauntletItem:
    """An item slot on the Hauntlet 2 screen: its `name` (a folder of HAUNTLET_ITEM_DIR, or "unknown"), the `count`
    on its badge (None if the badge couldn't be read), and the slot's centre `pos`."""
    name: str
    count: Optional[int]
    pos: Point

def find_item_slots(img: Image) -> list[tuple[int, int, int]]:
    """(x, y, radius) of every item slot along the bottom of `img`, left to right."""
    top, bottom = HAUNTLET_SLOT_BAND
    gray = cv2.medianBlur(cv2.cvtColor(img[top:bottom], cv2.COLOR_BGR2GRAY), 5)
    circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, dp=1, minDist=HAUNTLET_SLOT_MIN_DISTANCE, param1=100, param2=30,
                               minRadius=HAUNTLET_SLOT_RADIUS[0], maxRadius=HAUNTLET_SLOT_RADIUS[1])
    return [] if circles is None else sorted((int(x), int(y) + top, int(r)) for x, y, r in circles[0])

def _folder_stamp(directory: str) -> tuple[tuple[str, str, float], ...]:
    """What is in a folder of folders and when each file changed - different when a file is added, changed, removed or
    its folder renamed."""
    return tuple(sorted((root, f, os.path.getmtime(os.path.join(root, f))) for root, _, files in os.walk(directory) for f in files))

@lru_cache(maxsize=8)
def _picture_folders(directory: str, stamp: tuple[tuple[str, str, float], ...], gray: bool) -> dict[str, list[Image]]:
    found: dict[str, list[Image]] = {}
    for name in sorted(os.listdir(directory)):
        folder = os.path.join(directory, name)
        if os.path.isdir(folder):
            pictures = [cv2.imread(os.path.join(folder, f), cv2.IMREAD_GRAYSCALE if gray else cv2.IMREAD_COLOR)
                        for f in sorted(os.listdir(folder)) if f.endswith(".png")]
            found[name] = [p for p in pictures if p is not None]
    return found

def picture_folders(directory: str, gray: bool = True) -> dict[str, list[Image]]:
    """{folder name: its pictures} for a folder of folders of .png files (the items, the badge glyphs), re-read when
    anything in it changes; {} if it doesn't exist."""
    return _picture_folders(directory, _folder_stamp(directory), gray) if os.path.isdir(directory) else {}

def identify_item(crop: Image) -> tuple[Optional[str], float]:
    """(name, score) of the saved item `crop` (the square around a slot's centre) matches best, name None if the best
    score is under HAUNTLET_ITEM_MATCH. Compared as grey pictures: the slot's blue or dim teal background doesn't matter much."""
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    best_name, best = None, -1.0
    for name, pictures in picture_folders(HAUNTLET_ITEM_DIR).items():
        for picture in pictures:
            if picture.shape == gray.shape:
                score = float(cv2.matchTemplate(gray, picture, cv2.TM_CCOEFF_NORMED)[0][0])
                if score > best:
                    best_name, best = name, score
    return (best_name if best >= HAUNTLET_ITEM_MATCH else None), best

def read_item_count(img: Image, x: int, y: int) -> Optional[int]:
    """The number on the "x2" badge of the slot centred at (x, y), or None if it can't be read (no badge, or a glyph
    that matches none of the saved ones)."""
    left, top, right, bottom = HAUNTLET_BADGE_BOX
    region = img[max(0, y + top):y + bottom, max(0, x + left):x + right]
    if region.size == 0:
        return None
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    purple = cv2.morphologyEx(cv2.inRange(hsv, (125, 100, 120), (150, 255, 255)), cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(purple, connectivity=8)
    if count < 2:
        return None
    shaped = [i for i in range(1, count) if HAUNTLET_BADGE_WIDTH[0] <= stats[i, cv2.CC_STAT_WIDTH] <= HAUNTLET_BADGE_WIDTH[1]
              and HAUNTLET_BADGE_HEIGHT[0] <= stats[i, cv2.CC_STAT_HEIGHT] <= HAUNTLET_BADGE_HEIGHT[1]
              and stats[i, cv2.CC_STAT_AREA] >= 300]
    if not shaped:
        return None
    badge = max(shaped, key=lambda i: stats[i, cv2.CC_STAT_AREA])
    bx, by, bw, bh, area = (int(v) for v in stats[badge])
    white = ((region[by:by + bh, bx:bx + bw].min(axis=2) >= 225) & (labels[by:by + bh, bx:bx + bw] == badge)).astype(np.uint8)
    pieces_count, _, piece_stats, _ = cv2.connectedComponentsWithStats(white, connectivity=8)
    pieces = sorted((int(piece_stats[i, 0]), int(piece_stats[i, 1]), int(piece_stats[i, 2]), int(piece_stats[i, 3]))
                    for i in range(1, pieces_count) if piece_stats[i, cv2.CC_STAT_AREA] >= 6 and piece_stats[i, cv2.CC_STAT_HEIGHT] >= HAUNTLET_COUNT_MIN_HEIGHT)
    glyphs = picture_folders(HAUNTLET_COUNT_DIR)
    if len(pieces) < 2 or "x" not in glyphs:
        return None
    text = ""
    for px, py, pw, ph in pieces:
        picture = cv2.resize(white[py:py + ph, px:px + pw] * 255, HAUNTLET_COUNT_SIZE, interpolation=cv2.INTER_AREA).astype(int)
        diffs = {name: min(float(np.abs(picture - g.astype(int)).mean()) for g in pictures) for name, pictures in glyphs.items() if pictures}
        name = min(diffs, key=lambda n: diffs[n])
        if diffs[name] > HAUNTLET_COUNT_MAX_DIFF:
            return None
        text += name
    return int(text[1:]) if text.startswith("x") and text[1:].isdigit() else None

def read_hauntlet_items(img: Image) -> list[HauntletItem]:
    """The items in the slots along the bottom of `img`, left to right: each one's name, count and position. An item
    that matches none of the saved ones is named "unknown". Names in HAUNTLET_NOT_ITEMS (the lobby's backpack button) are left out."""
    items = []
    for x, y, radius in find_item_slots(img):
        half = HAUNTLET_ITEM_CROP // 2
        crop = img[y - half:y + half, x - half:x + half]
        if crop.shape[:2] != (HAUNTLET_ITEM_CROP, HAUNTLET_ITEM_CROP):
            continue
        name, _ = identify_item(crop)
        if name is None:
            name = "unknown"
        if name not in HAUNTLET_NOT_ITEMS:
            items.append(HauntletItem(name, read_item_count(img, x, y), (x, y)))
    return items

@dataclass
class HauntletHearts:
    """The player's hearts: how many are `red` (left) and `black` (lost), one heart's `width` in px, and the row's
    top-left `pos`."""
    red: int
    black: int
    width: int
    pos: Point

    @property
    def center_x(self) -> float:
        """The middle of the row, in px - above the player's head, so where the player stands."""
        return self.pos[0] + ((self.red + self.black - 1) * HAUNTLET_HEART_PITCH + 1) * self.width / 2

def pink_heart_pieces(img: Image) -> list[tuple[int, int, int, int]]:
    """(x, y, w, h) of every heart-shaped pink piece of `img` (HAUNTLET_HEART_*), nothing above HAUNTLET_HEART_MIN_Y."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.bitwise_or(cv2.inRange(hsv, *HAUNTLET_HEART_PINK[0]), cv2.inRange(hsv, *HAUNTLET_HEART_PINK[1]))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    pieces = []
    for i in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[i])
        if (y >= HAUNTLET_HEART_MIN_Y and w >= HAUNTLET_HEART_MIN_WIDTH and area >= 150
                and HAUNTLET_HEART_ASPECT[0] <= w / h <= HAUNTLET_HEART_ASPECT[1] and area / (w * h) >= HAUNTLET_HEART_MIN_FILL):
            pieces.append((x, y, w, h))
    return pieces

def read_hauntlet_hearts(img: Image) -> Optional[HauntletHearts]:
    """The player's hearts in `img`, or None when there are none to see (a close-up of a door, a transition, the lobby,
    or all of them lost - a row of nothing but black hearts has no pink one to find it by). One frame can be wrong, a
    heart hidden or a stray pink thing taken for one: a caller that acts on it should see the same on a few frames."""
    pieces = pink_heart_pieces(img)
    if not pieces:
        return None
    ax, ay, aw, ah = min(pieces, key=lambda p: abs(p[0] + p[2] / 2 - cfg.REFERENCE_CENTER_X))
    pitch = HAUNTLET_HEART_PITCH * aw
    black = img.max(axis=2) <= HAUNTLET_HEART_BLACK_LEVEL
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    pink = cv2.bitwise_or(cv2.inRange(hsv, *HAUNTLET_HEART_PINK[0]), cv2.inRange(hsv, *HAUNTLET_HEART_PINK[1])) > 0

    def kind(step: int) -> Optional[str]:
        centre = ax + aw / 2 + step * pitch
        if any(abs(px + pw / 2 - centre) <= pitch / 2 and abs(py - ay) <= 0.4 * ah for px, py, pw, ph in pieces):
            return "red"
        columns = slice(max(0, int(centre - aw / 2)), int(centre + aw / 2))
        if pink[ay:ay + ah, columns].mean() >= HAUNTLET_HEART_PINK_FRACTION:       # a heart the piece search missed (two touching, or part hidden)
            return "red"
        cell = black[ay:ay + ah, columns]
        return "black" if cell.size and cell.mean() >= HAUNTLET_HEART_BLACK_FRACTION else None

    row = {0: "red"}
    for direction in (1, -1):
        for n in range(1, HAUNTLET_HEART_MAX_EACH_SIDE + 1):
            found = kind(direction * n)
            if found is None:
                break
            row[direction * n] = found
    kinds = list(row.values())
    if not HAUNTLET_HEART_MIN <= len(kinds) <= HAUNTLET_HEART_MAX:
        return None                     # not a row of hearts: one pink thing alone, or something pink next to the row counted too
    return HauntletHearts(kinds.count("red"), kinds.count("black"), aw, (int(ax + min(row) * pitch), ay))

def hauntlet_timer_visible(img: Image) -> bool:
    """Whether the room's timer is shown (HAUNTLET_TIMER_*): it is hidden in a room's first moments and while the
    player passes through a door, when the screen still shows the last room."""
    left, top, right, bottom = HAUNTLET_TIMER_BOX
    white = img[top:bottom, left:right].min(axis=2) >= HAUNTLET_TIMER_WHITE_LEVEL
    return int(np.count_nonzero(white)) >= HAUNTLET_TIMER_MIN_WHITE

@dataclass
class HauntletDoor:
    """A door of a Hauntlet 2 room: where its TV's number is on screen (`x`, `y`) and its `kind` - "locked", "mystery"
    (the "???" door), "shadow" (smoke with glowing eyes), "dark", "boarded" or "plain"; None for a door that couldn't be looked at (or a fork's doorway,
    which isn't looked for)."""
    x: int
    y: int
    kind: Optional[str]

def find_door_tvs(img: Image) -> list[tuple[int, int]]:
    """The centre of the three TV numbers (HAUNTLET_TV_*) in `img`, left to right: the three above a room's doors, in
    the row they make - [] when there isn't one (a fork's hall, a close-up, a transition). With the camera close the
    middle TV can be off the top of the screen or behind the room panel: two TVs far enough apart to be the outer ones
    (HAUNTLET_TV_PAIR_GAP) and centred on the screen (the player stands in front of the middle door) give the middle one
    halfway between. Two neighbours high up (in the row of the room panel) with the third where the panel is give the third
    there: the panel hides its glow. Two neighbours with the room for the third only off the screen's left or right edge give the third there (the
    camera looks along a wall, the player against it: the doors run off the screen)."""
    low, high = HAUNTLET_TV_GLOW
    mask = cv2.inRange(img, (low[2], low[1], low[0]), (high[2], high[1], high[0]))   # BGR
    left, top, right, bottom = HAUNTLET_TV_HUD_BOX
    mask[top:bottom, left:right] = 0
    count, _, stats, centres = cv2.connectedComponentsWithStats(mask, connectivity=8)
    blobs = sorted((float(centres[i][0]), float(centres[i][1]), float(stats[i, cv2.CC_STAT_AREA])) for i in range(1, count)
                   if HAUNTLET_TV_AREA[0] <= stats[i, cv2.CC_STAT_AREA] <= HAUNTLET_TV_AREA[1])
    found = [(x, y) for x, y, _ in blobs]
    for first, middle, last in itertools.combinations(found, 3):                   # (not only neighbours: a stray glow can sit between)
        gaps = (middle[0] - first[0], last[0] - middle[0])
        if (abs(first[1] - middle[1]) <= HAUNTLET_TV_ROW_TOLERANCE and abs(middle[1] - last[1]) <= HAUNTLET_TV_ROW_TOLERANCE
                and all(HAUNTLET_TV_SPACING[0] <= gap <= HAUNTLET_TV_SPACING[1] for gap in gaps)
                and abs(gaps[0] - gaps[1]) <= HAUNTLET_TV_SPACING_TOLERANCE * max(gaps)):
            return [(int(x), int(y)) for x, y in (first, middle, last)]
    for (first, last), size in ((((a[0], a[1]), (b[0], b[1])), ((a[2] + b[2]) / 2) ** 0.5) for a, b in itertools.combinations(blobs, 2)):       # (any two, not only neighbours: stray glows can sit between the outer two)
        far_apart = last[0] - first[0] >= HAUNTLET_TV_PAIR_RATIO * size       # twice the neighbours' gap for the TVs' size
        if (abs(first[1] - last[1]) <= HAUNTLET_TV_ROW_TOLERANCE
                and HAUNTLET_TV_PAIR_GAP[0] <= last[0] - first[0] <= HAUNTLET_TV_PAIR_GAP[1]
                and (far_apart or abs((first[0] + last[0]) / 2 - cfg.REFERENCE_CENTER_X) <= HAUNTLET_TV_PAIR_OFF_CENTER)):
            return [(int(first[0]), int(first[1])), (int((first[0] + last[0]) / 2), int((first[1] + last[1]) / 2)), (int(last[0]), int(last[1]))]
    hud_left, _, hud_right, hud_bottom = HAUNTLET_TV_HUD_BOX
    for first, second in zip(found, found[1:]):                                  # two neighbours, the third behind the room panel
        gap = second[0] - first[0]
        if abs(first[1] - second[1]) > HAUNTLET_TV_ROW_TOLERANCE or not HAUNTLET_TV_SPACING[0] <= gap <= HAUNTLET_TV_SPACING[1] or second[1] > hud_bottom:
            continue
        for order in ((first, second, (second[0] + gap, second[1])), ((first[0] - gap, first[1]), first, second)):
            if hud_left <= order[0][0] <= hud_right or hud_left <= order[2][0] <= hud_right:
                return [(int(x), int(y)) for x, y in order]
    for first, second in zip(found, found[1:]):                                  # two neighbours, the third off the screen (the camera looks along a wall)
        gap = second[0] - first[0]
        if abs(first[1] - second[1]) > HAUNTLET_TV_ROW_TOLERANCE or not HAUNTLET_TV_SPACING[0] <= gap <= HAUNTLET_TV_SPACING[1]:
            continue
        if first[0] - gap < 0:
            return [(int(first[0] - gap), int(first[1])), (int(first[0]), int(first[1])), (int(second[0]), int(second[1]))]
        if second[0] + gap >= cfg.REFERENCE_WIDTH:
            return [(int(first[0]), int(first[1])), (int(second[0]), int(second[1])), (int(second[0] + gap), int(second[1]))]
    return []

def hauntlet_shadow_eyes(door: Image) -> bool:
    """Whether the glowing eyes of a shadow door are in `door`: a small piece of near-white (HAUNTLET_SHADOW_WHITE, HAUNTLET_SHADOW_AREA px)
    in the dark smoke - the ring around it no brighter than HAUNTLET_SHADOW_RING on average. (A door's screws, lights and the
    white of the lock do not sit in the dark.)"""
    if door.size == 0:
        return False
    white = (door.min(axis=2) >= HAUNTLET_SHADOW_WHITE).astype(np.uint8)
    gray = door.mean(axis=2)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(white)
    for i in range(1, count):
        if not HAUNTLET_SHADOW_AREA[0] <= stats[i, cv2.CC_STAT_AREA] <= HAUNTLET_SHADOW_AREA[1]:
            continue
        blob = (labels == i).astype(np.uint8)
        ring = cv2.dilate(blob, np.ones((15, 15), np.uint8)) - cv2.dilate(blob, np.ones((3, 3), np.uint8))
        if ring.any() and gray[ring > 0].mean() <= HAUNTLET_SHADOW_RING:
            return True
    return False

def hauntlet_door_kind(img: Image, x: int, y: int, scale: float) -> Optional[str]:
    """What kind of door hangs below the TV number at (x, y), from its pixels (see HAUNTLET_LOCK_LIGHT_BOX); `scale`
    is the picture's size against the one the boxes were measured at. None if the door is off the screen."""
    def region(box: tuple[int, int, int, int]) -> Image:
        left, top, right, bottom = box
        return img[max(0, int(y + top * scale)):max(0, int(y + bottom * scale)), max(0, int(x + left * scale)):max(0, int(x + right * scale))]       # (not a negative stop, which counts from the far edge)
    light = region(HAUNTLET_LOCK_LIGHT_BOX).astype(int)
    if light.size:
        red = ((light[..., 2] >= HAUNTLET_LOCK_LIGHT_RED) & (light[..., 1] <= HAUNTLET_LOCK_LIGHT_OTHER) & (light[..., 0] <= HAUNTLET_LOCK_LIGHT_OTHER)).astype(np.uint8)
        for area, width, height in cv2.connectedComponentsWithStats(red)[2][1:, 2:5][:, [2, 0, 1]]:     # the light is a small round patch: a red wall is not
            if (HAUNTLET_LOCK_LIGHT_MIN <= area <= HAUNTLET_LOCK_LIGHT_MAX
                    and max(width, height) <= HAUNTLET_LOCK_LIGHT_ROUND * min(width, height)):
                return "locked"
    if hauntlet_shadow_eyes(region(HAUNTLET_SHADOW_BOX)):
        return "shadow"
    door = region(HAUNTLET_DOOR_BOX)
    if door.size == 0:
        return None
    brightness = door.mean(axis=2)
    if np.count_nonzero(brightness <= HAUNTLET_DARK_LEVEL) >= HAUNTLET_DARK_FRACTION * brightness.size:
        return "dark"
    if brightness.mean() >= HAUNTLET_MYSTERY_BRIGHTNESS:
        return "mystery"
    planks = region(HAUNTLET_PLANK_BOX)
    if min(planks.shape[:2]) < 3:
        return None
    grey = cv2.GaussianBlur(cv2.cvtColor(planks, cv2.COLOR_BGR2GRAY).astype(np.float32), (0, 0), HAUNTLET_PLANK_BLUR)
    dx, dy = cv2.Sobel(grey, cv2.CV_32F, 1, 0), cv2.Sobel(grey, cv2.CV_32F, 0, 1)
    angle = np.degrees(np.arctan2(dy, dx)) % 180                # an edge's direction is square to its gradient: 90 is a horizontal edge
    on_plank_edge = (np.hypot(dx, dy) > HAUNTLET_PLANK_EDGE) & (np.abs(angle - 90) < HAUNTLET_PLANK_ANGLE)
    return "boarded" if np.count_nonzero(on_plank_edge) >= HAUNTLET_PLANK_FRACTION * on_plank_edge.size else "plain"

def find_ghost_door(img: Image, doors: list[HauntletDoor]) -> Optional[int]:
    """The index of the door with the phone's red arrow over it (the ghost's door), or None: a big red triangle with a ghost on
    it (HAUNTLET_ARROW_*) just above the TV of the door it points at. Doors that weren't located can't be told."""
    if len(doors) != 3 or not doors[0].x:
        return None
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.bitwise_or(cv2.inRange(hsv, *HAUNTLET_ARROW_RED[0]), cv2.inRange(hsv, *HAUNTLET_ARROW_RED[1]))
    left, top, right, bottom = HAUNTLET_ARROW_BOX
    box = np.zeros_like(mask)
    box[top:bottom, left:right] = mask[top:bottom, left:right]
    box = cv2.morphologyEx(box, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    count, _, stats, centres = cv2.connectedComponentsWithStats(box, connectivity=8)
    spacing = (doors[2].x - doors[0].x) / 2
    for i in range(1, count):
        if (HAUNTLET_ARROW_AREA[0] <= stats[i, cv2.CC_STAT_AREA] <= HAUNTLET_ARROW_AREA[1]
                and HAUNTLET_ARROW_WIDTH[0] <= stats[i, cv2.CC_STAT_WIDTH] <= HAUNTLET_ARROW_WIDTH[1]
                and HAUNTLET_ARROW_HEIGHT[0] <= stats[i, cv2.CC_STAT_HEIGHT] <= HAUNTLET_ARROW_HEIGHT[1]):
            nearest = min(range(3), key=lambda k: abs(doors[k].x - centres[i][0]))
            if abs(doors[nearest].x - centres[i][0]) <= HAUNTLET_ARROW_MAX_OFFSET * spacing:
                return nearest
    return None

def find_hauntlet_doors(img: Image) -> list[HauntletDoor]:
    """The three doors of the room in `img`, left to right, with their kinds - [] if there is no row of TVs."""
    tvs = find_door_tvs(img)
    if not tvs:
        return []
    scale = (tvs[2][0] - tvs[0][0]) / 2 / HAUNTLET_DOOR_SPACING
    return [HauntletDoor(x, y, hauntlet_door_kind(img, x, y, scale)) for x, y in tvs]

MINIGAME_RE = re.compile(r"^minigame (started|finished): (.*?)(?: \(\d+s\))?$")
