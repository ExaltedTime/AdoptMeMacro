"""Doors, respawns and teleports are waited out by their white screen, not by a fixed time (wait_for_white_to_pass())."""
import numpy as np
import pytest

WHITE = np.full((1080, 1920, 3), 255, np.uint8)
SCENE = np.full((1080, 1920, 3), 60, np.uint8)


@pytest.fixture
def clock(macro, monkeypatch):
    """The game as a list of frames, one per look; the fake time advances by what is waited."""
    now = [1000.0]
    frames: list = []
    waited: list[float] = []
    monkeypatch.setattr(macro.time, "time", lambda: now[0])

    def wait(seconds):
        waited.append(seconds)
        now[0] += seconds
    monkeypatch.setattr(macro, "wait_interruptible", wait)
    monkeypatch.setattr(macro, "grab_screen", lambda: frames.pop(0) if len(frames) > 1 else frames[0])
    return frames, waited


def test_a_transition_is_over_when_its_white_screen_has_gone_and_not_after_a_fixed_time(macro, clock):
    frames, waited = clock
    frames[:] = [SCENE, WHITE, WHITE, WHITE, SCENE, SCENE]
    assert macro.wait_for_white_to_pass(static=10.0) is True
    assert sum(waited) < 3.0                                                                      # (not the 10 s of the old wait: 5 looks and the settle)
    assert waited[-1] == macro.cfg.WHITE_SETTLE


def test_a_flicker_of_one_look_is_not_the_end_and_a_white_screen_that_never_goes_stops_at_the_maximum(macro, clock):
    frames, waited = clock
    frames[:] = [WHITE, SCENE, WHITE, WHITE, SCENE, SCENE]
    assert macro.wait_for_white_to_pass(static=10.0) is True and len(frames) == 1 and sum(waited) < 3.0       # (one look that is not white after it is white is not enough)
    frames[:] = [WHITE]
    waited.clear()
    assert macro.wait_for_white_to_pass(static=10.0) is True and macro.cfg.WHITE_PASS_MAX <= sum(waited) < macro.cfg.WHITE_PASS_MAX + 1


def test_without_a_white_screen_the_wait_is_the_old_fixed_one_and_no_longer(macro, clock):
    frames, waited = clock
    frames[:] = [SCENE]
    assert macro.wait_for_white_to_pass(static=4.0) is False
    assert 3.9 <= sum(waited) <= 4.0 + macro.cfg.WHITE_POLL                                       # (4 s in all, the looks counted in it)
    waited.clear()
    assert macro.wait_for_white_to_pass(static=1.0) is False and sum(waited) <= macro.cfg.WHITE_LOOK + macro.cfg.WHITE_POLL


def test_the_respawn_the_teleport_and_the_pens_door_wait_for_it(macro, monkeypatch):
    calls = []
    monkeypatch.setattr(macro, "wait_for_white_to_pass", lambda static, look=2.0, **kw: calls.append((static, look)) or True)
    monkeypatch.setattr(macro, "focus_roblox_click", lambda: True)
    monkeypatch.setattr(macro, "in_house", lambda img: True)
    monkeypatch.setattr(macro, "grab_screen", lambda: None)
    monkeypatch.setattr(macro, "key_down", lambda key: None)
    monkeypatch.setattr(macro, "key_up", lambda key: None)
    monkeypatch.setattr(macro.time, "sleep", lambda s: None)
    monkeypatch.setattr(macro, "dismiss_stray_windows", lambda: None)
    macro.respawn_character()
    assert calls == [(macro.cfg.RESPAWN_WAIT, 2.0)]
    calls.clear()
    monkeypatch.setattr(macro, "respawn_character", lambda: None)
    monkeypatch.setattr(macro, "hold_key", lambda key, seconds: None)
    monkeypatch.setattr(macro.pydirectinput, "press", lambda key: None, raising=False)
    monkeypatch.setattr(macro, "wait_interruptible", lambda s: None)
    monkeypatch.setattr(macro, "look_until", lambda *a, **k: (1, 1))
    assert macro.open_pet_pen() is True
    assert len(calls) == 1 and calls[0][0] == 5.0                                                 # (the door of the route: 5 s only when no white screen shows)


def test_the_teleport_waits_for_its_first_and_its_second_white_screen(macro, monkeypatch):
    calls = []
    monkeypatch.setattr(macro, "wait_for_white_to_pass", lambda static, look=2.0, **kw: calls.append((static, look)) or True)
    monkeypatch.setattr(macro, "backpack_on_purpose", __import__("contextlib").nullcontext)
    monkeypatch.setattr(macro.pydirectinput, "press", lambda key: None, raising=False)
    monkeypatch.setattr(macro, "wait_interruptible", lambda s: None)
    monkeypatch.setattr(macro, "hover_click", lambda *a, **k: None)
    monkeypatch.setattr(macro, "grab_screen", lambda: None)
    monkeypatch.setattr(macro, "teleport_took", lambda before: True)
    monkeypatch.setattr(macro, "in_house", lambda img: False)
    monkeypatch.setattr(macro, "find_teleport_yes", lambda img: (1, 1))
    monkeypatch.setattr(macro, "look_until", lambda find, seconds, poll=0.3: find(None))
    monkeypatch.setattr(macro, "hold_key", lambda key, seconds: None)
    order = []
    monkeypatch.setattr(macro, "hold_key", lambda key, seconds: order.append("walk"))
    monkeypatch.setattr(macro, "wait_for_white_to_pass", lambda static, look=2.0, **kw: order.append("white") or calls.append((static, look)) or True)
    macro.teleport_to((1, 1))
    assert order == ["white", "walk", "white"]                                                    # (the walk back is the way out of the landing and starts the second load)
    assert calls == [(macro.cfg.TELEPORT_WAIT, 2.0), (macro.cfg.TELEPORT_SETTLE_WAIT, macro.cfg.TELEPORT_SECOND_WHITE_LOOK)]


# --- the frames of a capture of the teleport tests (tests/teleport/): what each moment of a teleport looks like -----------------------------

import os
import cv2

TELEPORT_FRAMES = os.path.join(os.path.dirname(__file__), "teleport")


def teleport_frame(name):
    return cv2.imread(os.path.join(TELEPORT_FRAMES, f"{name}.png"))


@pytest.mark.parametrize("name, white, house", [
    ("house_before", False, True), ("dialog_nursery", False, True), ("dialog_dealership", False, True), ("house_idle", False, True),
    ("white_first", True, False), ("white_second", True, False),
    ("red_room", False, False), ("red_room_stuck", False, False), ("park_landing", False, False), ("road_landing", False, False), ("hospital_lobby", False, False),
])
def test_each_moment_of_a_teleport_is_told_by_its_white_screen_and_the_houses_exit_button(macro, name, white, house):
    """The house shows its red Exit Home button, a white screen hides everything, and the red room and every place outside have none:
    so "still in the house" after a teleport is a teleport that did not happen. `red_room_stuck` is the nursery's room from a run that did not walk back out of it: its red walls fill the box more than the button does."""
    frame = teleport_frame(name)
    assert macro.detect_white_screen(frame) is white
    assert macro.in_house(frame) is house


def test_a_missing_white_screen_is_written_to_the_run_log_when_it_is_named(macro, clock, monkeypatch):
    frames, waited = clock
    lines = []
    monkeypatch.setattr(macro, "log_run_event", lines.append)
    frames[:] = [SCENE]
    assert macro.wait_for_white_to_pass(static=1.0, what="respawn") is False
    assert len(lines) == 1 and lines[0].startswith("respawn: no white screen")
    frames[:] = [SCENE, WHITE, SCENE, SCENE]
    assert macro.wait_for_white_to_pass(static=1.0, what="respawn") is True and len(lines) == 1      # (one that showed is not logged)


@pytest.fixture
def teleporting(macro, monkeypatch):
    """teleport_to() with the game stubbed: `state` says whether the Yes took and whether the house is still on screen."""
    state = {"dialog": True, "took": True, "house": [True, False], "walked": []}
    monkeypatch.setattr(macro, "backpack_on_purpose", __import__("contextlib").nullcontext)
    monkeypatch.setattr(macro.pydirectinput, "press", lambda key: None, raising=False)
    monkeypatch.setattr(macro, "wait_interruptible", lambda s: None)
    monkeypatch.setattr(macro, "hover_click", lambda *a, **k: None)
    monkeypatch.setattr(macro, "grab_screen", lambda: None)
    monkeypatch.setattr(macro, "teleport_took", lambda before: state["took"])
    monkeypatch.setattr(macro, "find_teleport_yes", lambda img: (1048, 658) if state["dialog"] else None)
    monkeypatch.setattr(macro, "look_until", lambda find, seconds, poll=0.3: find(None))
    monkeypatch.setattr(macro, "wait_for_white_to_pass", lambda static, look=2.0, **kw: True)
    monkeypatch.setattr(macro, "hold_key", lambda key, seconds: state["walked"].append(key))
    monkeypatch.setattr(macro, "in_house", lambda img: state["house"].pop(0) if len(state["house"]) > 1 else state["house"][0])
    lines = []
    monkeypatch.setattr(macro, "log_run_event", lines.append)
    state["lines"] = lines
    return state


def test_a_teleport_that_left_the_player_in_the_house_fails_after_the_step_back(macro, teleporting):
    teleporting["house"] = [True, True]
    assert macro.teleport_to((1, 1)) is False
    assert "still in the house" in teleporting["lines"][0]


def test_a_teleport_whose_yes_took_nothing_fails_and_one_that_worked_walks_back(macro, teleporting):
    teleporting["took"] = False
    assert macro.teleport_to((1, 1)) is False and teleporting["walked"] == []
    teleporting["took"] = True
    teleporting["house"] = [True, False]
    assert macro.teleport_to((1, 1)) is True and teleporting["walked"] == [macro.cfg.KEYS["back"]]


def test_a_walk_need_whose_teleport_failed_respawns_and_fails_without_walking(macro, monkeypatch):
    walked, respawned = [], []
    monkeypatch.setattr(macro, "focus_roblox", lambda: True)
    monkeypatch.setattr(macro, "teleport_to", lambda pos: False)
    monkeypatch.setattr(macro, "respawn_character", lambda: respawned.append(1))
    monkeypatch.setattr(macro, "hold_key", lambda key, seconds: walked.append(key))
    assert macro.TeleportWalkNeedHandler("bored").handle() is False
    assert walked == [] and respawned == [1]


@pytest.mark.parametrize("name, yes", [("dialog_nursery", True), ("dialog_dealership", True), ("house_before", False), ("white_first", False), ("red_room", False), ("hospital_lobby", False)])
def test_the_teleport_dialogs_yes_is_found_where_it_is_clicked_and_nowhere_else(macro, name, yes):
    found = macro.find_teleport_yes(teleport_frame(name))
    assert (found is not None) is yes
    if yes:
        x, y = found
        assert abs(x - macro.cfg.GENERAL_TELEPORT_POS_3[0]) <= 4 and abs(y - macro.cfg.GENERAL_TELEPORT_POS_3[1]) <= 4


def test_a_teleport_whose_dialog_never_came_up_fails_without_clicking_a_yes(macro, teleporting):
    teleporting["dialog"] = False
    assert macro.teleport_to((1, 1)) is False
    assert teleporting["walked"] == [] and "dialog did not come up" in teleporting["lines"][0]


@pytest.mark.parametrize("name", ["dialog_nursery", "dialog_dealership"])
def test_the_fixed_clicks_of_a_teleport_land_on_their_buttons_in_the_real_dialog_frames(macro, name):
    """The category tabs and the Get More tile are fixed pixels: a coloured (saturated) button must be under each in the frames of the capture, so a moved backpack shows up here."""
    hsv = cv2.cvtColor(teleport_frame(name), cv2.COLOR_BGR2HSV)
    for pos in (macro.cfg.TELEPORT_PETS_TAB_POS, macro.cfg.TELEPORT_VEHICLES_TAB_POS, macro.cfg.GENERAL_TELEPORT_POS_2):
        x, y = pos
        patch = hsv[y - 22:y + 22, x - 22:x + 22, 1]
        assert patch.mean() > 70, (name, pos, patch.mean())
