"""Playing Stitches' Spotlight: the ring's gold edge line and the camera turns it asks for. Frames are real captures of a round (tests/stitches_spotlight/);
time and the keyboard are faked."""
import os
import time

import cv2
import numpy as np
import pytest

HERE = os.path.join(os.path.dirname(__file__), "stitches_spotlight")


def shot(name):
    return cv2.imread(os.path.join(HERE, f"{name}.png"))


def test_the_ring_line_falls_to_the_right_when_the_middle_is_to_the_right_and_rises_when_it_is_to_the_left(macro):
    for name in ("ring_falls_right_a", "ring_falls_right_b"):
        assert macro.stitches_ring_angle(shot(name)) > 0, name
    for name in ("ring_rises_a", "ring_rises_b"):
        assert macro.stitches_ring_angle(shot(name)) < 0, name


def test_no_line_is_used_where_too_little_of_it_is_seen(macro):
    assert macro.stitches_ring_angle(shot("in_the_red")) is None                 # (the searchlight's red covers it)
    assert macro.stitches_ring_angle(np.zeros((1080, 1920, 3), np.uint8)) is None
    assert macro.stitches_camera_turn(shot("in_the_red")) is None


def test_the_camera_is_turned_towards_the_side_the_line_says_and_not_when_it_is_straight(macro, monkeypatch):
    left_key, right_key = macro.cfg.STITCHES_TURN_KEYS
    monkeypatch.setattr(macro.cfg, "STITCHES_LINE_DEADBAND", 5.0)                  # (the real frames' angles are well outside this)
    assert macro.stitches_camera_turn(shot("ring_falls_right_a"))[0] == right_key
    assert macro.stitches_camera_turn(shot("ring_rises_a"))[0] == left_key
    deadband = min(macro.cfg.STITCHES_LINE_DEADBAND, 40.0)                       # (the angles below must be outside it)
    assert macro.stitches_camera_turn(shot("ring_falls_right_a"))[0] == right_key
    assert macro.stitches_camera_turn(shot("ring_rises_a"))[0] == left_key
    for angle, expected in ((deadband / 2, None), (-deadband, None), (45.0, (right_key, 0.5)), (-45.0, (left_key, 0.5)), (89.0, (right_key, 1.0)), (-80.0, (left_key, 1.0))):
        monkeypatch.setattr(macro, "stitches_ring_angle", lambda img, angle=angle: angle)
        turn = macro.stitches_camera_turn(None)
        assert turn is None if expected is None else (turn[0], turn[1] / macro.cfg.STITCHES_TURN_HOLD_MAX) == (expected[0], pytest.approx(expected[1], abs=0.02) if expected[1] == 0.5 else pytest.approx(min(abs(angle), 90) / 90, abs=0.02)), angle


def test_the_player_holds_the_walking_key_all_round_and_lets_go_of_it_at_the_end(macro, monkeypatch):
    events, looks = [], iter([shot("ring_falls_right_a"), shot("in_the_red"), shot("in_the_red")])
    monkeypatch.setattr(macro.pydirectinput, "keyDown", lambda key: events.append(("down", key)))
    monkeypatch.setattr(macro.pydirectinput, "keyUp", lambda key: events.append(("up", key)))
    monkeypatch.setattr(macro, "hold_key", lambda key, duration: events.append(("turn", key)))
    monkeypatch.setattr(macro, "wait_interruptible", lambda duration: None)
    monkeypatch.setattr(macro, "minigame_look", lambda watch: next(looks))
    won = iter([False, False, True])
    monkeypatch.setattr(macro, "detect_minigame_victory", lambda img: next(won))
    watch = macro.MinigameWatch("stitches_spotlight")
    watch.last_seen = time.time()
    assert macro.play_stitches_spotlight(10 ** 12, watch) is True
    walk = macro.cfg.KEY_STITCHES_WALK
    assert events == [("down", walk), ("turn", macro.cfg.STITCHES_TURN_KEYS[1]), ("up", walk)]
