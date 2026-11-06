"""The choose need's way in: the pet's name pill (plain, dark, however far its age bar is filled), the wait for the pet's view, the E key, and the button looked for while it moves in (tests/choose/: frames of a 2-hour run)."""
import os

import cv2
import numpy as np

import conftest


def shot(name):
    return cv2.imread(os.path.join(conftest.ROOT, "tests", "choose", name + ".png"))


def test_the_pet_is_found_by_a_pill_that_is_half_bright_and_half_dark_where_the_old_bar_search_found_nothing(macro):
    for name, pet in (("pill_plain_standing", (1125, 735)), ("pill_plain_ghost", (1170, 735))):
        frame = shot(name)
        assert macro.find_pet_bar(frame) == []                                                   # (no white beside it: the old search saw no bar)
        points = macro.find_pet_pill(frame)
        assert len(points) == 3 and min(abs(x - pet[0]) + abs(y - pet[1]) for x, y in points) < 40, name      # (one of the places clicked is on the pet)
    assert macro.find_pet_pill(shot("pill_dark_dragon"))                                          # (a dragon with a dark, nearly empty pill)
    assert macro.find_pet_pill(shot("focus_view_disc")) == [] and macro.find_pet_pill(np.full((1080, 1920, 3), 30, np.uint8)) == []


def test_the_pill_is_not_looked_for_in_the_games_own_buttons_or_the_macros_window(macro):
    frame = np.zeros((1080, 1920, 3), np.uint8)
    frame[:] = (40, 40, 40)
    hsv = np.zeros((40, 150, 3), np.uint8)
    hsv[:] = (131, 181, 255)                                                                     # a bright purple pill
    pill = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    frame[20:60, 500:650] = pill                                                                  # (at the top: Edit House and the like)
    assert macro.find_pet_pill(frame) == []
    frame[600:640, 500:650] = pill
    assert macro.find_pet_pill(frame)
    assert macro.find_pet_pill(frame, (450, 560, 700, 700)) == []                                 # (inside the macro's window: ignored)


def test_the_view_found_at_a_late_look_is_waited_for_and_the_e_key_is_the_next_try(macro, monkeypatch):
    pressed, log = [], []
    monkeypatch.setattr(macro, "grab_screen", lambda: np.zeros((1080, 1920, 3), np.uint8))
    monkeypatch.setattr(macro, "wait_interruptible", lambda s: None)
    monkeypatch.setattr(macro, "log_run_event", log.append)
    monkeypatch.setattr(macro, "focus_pet", lambda *a: True)
    monkeypatch.setattr(macro.pydirectinput, "press", lambda key: pressed.append(key), raising=False)
    monkeypatch.setattr(macro.cfg, "PET_FOCUS_VIEW_WAIT", 0.0)
    looks = iter([False, False] + [True] * 10)                                                    # (not up at the start, not after the clicks, up after the E key)
    monkeypatch.setattr(macro, "detect_pet_focus", lambda img: next(looks))
    assert macro.focus_pet_reliably() is True and pressed == [macro.cfg.KEYS["interact"]] and log == ["pet: focused with the E key"]
    pressed.clear()
    log.clear()
    monkeypatch.setattr(macro, "detect_pet_focus", lambda img: False)
    assert macro.focus_pet_reliably() is False and len(pressed) == macro.cfg.PET_FOCUS_TRIES and len(log) == macro.cfg.PET_FOCUS_TRIES   # (every try pressed E and said so)


def test_a_view_that_is_up_already_is_left_alone_and_no_key_is_pressed(macro, monkeypatch):
    pressed = []
    monkeypatch.setattr(macro, "grab_screen", lambda: np.zeros((1080, 1920, 3), np.uint8))
    monkeypatch.setattr(macro, "detect_pet_focus", lambda img: True)
    monkeypatch.setattr(macro.pydirectinput, "press", lambda key: pressed.append(key), raising=False)
    assert macro.focus_pet_reliably() is True and pressed == []                                   # (E there would close it: BACK is on E)


def test_the_choose_button_is_looked_for_while_the_view_comes_in_and_the_disc_is_found_on_the_real_frame(macro, monkeypatch):
    assert macro.ChooseNeedHandler._find_button(shot("focus_view_disc")) == ((640, 736), False)
    frames = [np.zeros((1080, 1920, 3), np.uint8)] * 2 + [shot("focus_view_disc")]
    clicks = []
    monkeypatch.setattr(macro, "focus_roblox", lambda: True)
    monkeypatch.setattr(macro, "focus_pet_reliably", lambda *a: True)
    monkeypatch.setattr(macro, "wait_interruptible", lambda s: None)
    monkeypatch.setattr(macro, "log_run_event", lambda text: None)
    monkeypatch.setattr(macro, "grab_screen", lambda: frames.pop(0) if len(frames) > 1 else frames[0])
    monkeypatch.setattr(macro, "hover_click", lambda x, y, duration=0: clicks.append((x, y)))
    monkeypatch.setattr(macro, "dismiss_stray_windows", lambda: None)
    assert macro.ChooseNeedHandler().handle() is True and clicks[0] == (640, 736)
    monkeypatch.setattr(macro.cfg, "CHOOSE_FIND_WAIT", 0.0)
    monkeypatch.setattr(macro, "save_failure_report", lambda reason: None)
    frames[:] = [np.zeros((1080, 1920, 3), np.uint8)]
    assert macro.ChooseNeedHandler().handle() is False
