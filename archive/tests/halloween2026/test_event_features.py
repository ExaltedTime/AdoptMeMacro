"""The tests of features that are about the event (Halloween 2026): scripts/Halloween2026.py, which the macro does not import."""
import os
import cv2
import numpy as np
import pytest
from scripts import Halloween2026 as hw  # noqa: E402


BRIGHT = (240, 250, 255)


def bright_screen():
    return np.full((1080, 1920, 3), BRIGHT, np.uint8)


def fixture(name):
    return cv2.imread(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures", name))


@pytest.fixture
def winner(macro, monkeypatch):
    """A screen showing the minigame finish screen (banner and NICE! button), with the things unscrew() does around it stubbed."""
    screen = bright_screen()
    cfg = macro.cfg
    for box, color in ((hw.MINIGAME_FINISH_BANNER_BOX, hw.MINIGAME_FINISH_BANNER_COLOR), (hw.MINIGAME_FINISH_BUTTON_BOX, cfg.GREEN_BUTTON_COLOR)):
        left, top, right, bottom = box
        screen[top:bottom, left:right] = color[::-1]
    state = {"screen": screen, "sticky": 0}      # "sticky": clicks the finish screen survives
    respawns = []

    def click(x, y, duration=0):
        macro.clicks.append((x, y))
        if state["sticky"] > 0:
            state["sticky"] -= 1
        else:
            state["screen"] = bright_screen()

    monkeypatch.setattr(macro, "hover_click", click)
    monkeypatch.setattr(macro, "grab_screen", lambda: state["screen"])
    monkeypatch.setattr(macro, "respawn_character", lambda: respawns.append(1))
    monkeypatch.setattr(macro, "wait_interruptible", lambda duration: None)
    monkeypatch.setattr(macro, "rejoin_game", lambda kinds=None: False)
    monkeypatch.setattr(macro, "close_backpack_if_stuck", lambda in_task: False)
    monkeypatch.setattr(macro, "dismiss_stray_windows", lambda: None)
    monkeypatch.setattr(macro, "detect_paycheck", lambda: False)
    state["respawns"] = respawns
    return state


def test_a_finish_screen_is_closed_and_the_character_respawned(macro, winner):
    assert hw.close_minigame_finish_screen() is True
    assert hw.MINIGAME_FINISH_BUTTON_POS in macro.clicks and winner["respawns"] == [1]
    assert "minigame finish screen closed" in open(macro.cfg.RUN_LOG_PATH).read()
    assert macro.state.last_progress is not None


def test_a_click_that_missed_is_repeated(macro, winner, monkeypatch):
    monkeypatch.setattr(hw, "MINIGAME_FINISH_CLICK_TRIES", 3)
    winner["sticky"] = 1
    assert hw.close_minigame_finish_screen() is True
    assert macro.clicks.count(hw.MINIGAME_FINISH_BUTTON_POS) == 2 and winner["respawns"] == [1]


def test_a_finish_screen_that_will_not_close_is_retried_then_reported(macro, winner):
    winner["sticky"] = 99
    hw.close_minigame_finish_screen()
    assert macro.clicks.count(hw.MINIGAME_FINISH_BUTTON_POS) == hw.MINIGAME_FINISH_CLICK_TRIES
    assert "would not close" in open(macro.cfg.RUN_LOG_PATH).read()


def test_the_real_finish_screen_is_recognised(macro):
    screen = bright_screen()
    for box, name in ((hw.MINIGAME_FINISH_BANNER_BOX, "finish_banner.png"), (hw.MINIGAME_FINISH_BUTTON_BOX, "finish_button.png")):
        crop = fixture(name)                                                     # (relative to this file, not the working directory)
        left, top, right, bottom = box
        screen[top:top + crop.shape[0], left:left + crop.shape[1]] = crop
    assert hw.detect_minigame_finished(screen) is True


def test_no_finish_screen_means_nothing_happens(macro, winner):
    winner["screen"] = bright_screen()
    assert hw.close_minigame_finish_screen() is False
    assert not macro.clicks and winner["respawns"] == []


def test_the_green_button_alone_is_not_a_finish_screen(macro, winner):
    screen = bright_screen()
    left, top, right, bottom = hw.MINIGAME_FINISH_BUTTON_BOX
    screen[top:bottom, left:right] = macro.cfg.GREEN_BUTTON_COLOR[::-1]
    winner["screen"] = screen                                    # a popup's Yes, an Okay: the banner rules them out
    assert hw.close_minigame_finish_screen() is False

