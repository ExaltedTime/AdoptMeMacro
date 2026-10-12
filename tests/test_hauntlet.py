"""Reading the Hauntlet 2 screen. The fixtures are crops of a real 29-room run (tests/hauntlet2/), pasted back at
the place they were taken from on an otherwise blank screen."""
import glob
import os

import cv2
import numpy as np
import pytest

HERE = os.path.join(os.path.dirname(__file__), "hauntlet2")
PANEL_AT = (1060, 60)                      # where the room panel crops were cut from (left, top)


def screen_with(crop, at):
    screen = np.zeros((1080, 1920, 3), np.uint8)
    screen[at[1]:at[1] + crop.shape[0], at[0]:at[0] + crop.shape[1]] = crop
    return screen


def panel(name):
    return screen_with(cv2.imread(os.path.join(HERE, "room_panels", name)), PANEL_AT)


@pytest.mark.parametrize("path", sorted(glob.glob(os.path.join(HERE, "room_panels", "[0-9]*.png"))), ids=os.path.basename)
def test_every_room_of_the_real_run_is_read(macro, path):
    assert macro.read_hauntlet_room(panel(os.path.basename(path))) == int(os.path.basename(path)[:2])


@pytest.mark.parametrize("name", ["none_transition.png", "none_lobby.png"])
def test_a_screen_without_the_panel_reads_as_no_room(macro, name):
    assert macro.read_hauntlet_room(panel(name)) is None


def test_a_blank_screen_and_a_missing_digit_folder_read_as_no_room(macro, monkeypatch):
    assert macro.read_hauntlet_room(np.zeros((1080, 1920, 3), np.uint8)) is None
    monkeypatch.setattr(macro.cfg, "HAUNTLET_DIGIT_DIR", os.path.join(HERE, "no_such_folder"))
    assert macro.read_hauntlet_room(panel("07.png")) is None


def test_the_saved_digits_are_all_there_and_distinct(macro):
    digits = macro.digit_templates()
    assert sorted(digits) == list(range(10))
    for a in digits:
        for b in digits:
            if a < b:
                assert np.abs(digits[a].astype(int) - digits[b].astype(int)).mean() > 2 * macro.cfg.HAUNTLET_DIGIT_MAX_DIFF   # no digit can match two


def test_a_digit_that_matches_none_of_them_is_not_guessed(macro):
    screen = panel("07.png")
    left, top, right, bottom = macro.cfg.HAUNTLET_ROOM_BOX
    screen[top:bottom, left:right] = 0
    screen[top + 4:top + 24, left + 30:left + 44] = 255                          # a solid white block where a digit goes
    assert macro.read_hauntlet_room(screen) is None
