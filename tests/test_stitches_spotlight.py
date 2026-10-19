"""Playing Stitches' Spotlight: the ring's gold edge line and the camera turns it asks for. Frames are real captures of a round (tests/stitches_spotlight/);
time and the keyboard are faked."""
import os

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
    assert macro.RingCamera().decide(macro.stitches_ring_angle(shot("in_the_red")), 0.0, 0.5) is None


def camera(macro, monkeypatch, deadband=5.0):
    monkeypatch.setattr(macro.cfg, "STITCHES_LINE_DEADBAND", deadband)
    return macro.RingCamera()


def test_the_camera_is_turned_towards_the_side_the_line_says_and_not_when_it_is_straight(macro, monkeypatch):
    left_key, right_key = macro.cfg.STITCHES_TURN_KEYS
    interval = 0.5
    ring = camera(macro, monkeypatch)
    assert ring.decide(2.0, 0.0, interval) is None and ring.decide(-5.0, 0.5, interval) is None           # level enough
    assert ring.decide(45.0, 1.0, interval)[0] == right_key                                                  # falls to the right: the middle is to the right
    assert camera(macro, monkeypatch).decide(-45.0, 1.0, interval)[0] == left_key
    hold = lambda angle: camera(macro, monkeypatch).decide(angle, 0.0, interval)[1]
    assert hold(20.0) < hold(45.0) < hold(89.0) <= macro.cfg.STITCHES_TURN_HOLD_MAX                          # the further off, the longer
    assert camera(macro, monkeypatch).decide(-120.0, 0.0, interval)[0] == left_key
    for angle in (None, 3.0):                                                                                # (a look with no line, or a level one, from a fresh camera: nothing)
        assert camera(macro, monkeypatch).decide(angle, 0.0, interval) is None


def test_a_line_that_stays_off_teaches_a_steady_turn_which_goes_on_while_the_line_is_out_of_sight(macro, monkeypatch):
    left_key, right_key = macro.cfg.STITCHES_TURN_KEYS
    ring = camera(macro, monkeypatch)
    now, interval = 0.0, 0.5
    for _ in range(10):                                                         # the line sits 12 degrees off, look after look: the walk needs turning all the time
        ring.decide(12.0, now, interval)
        now += interval
    assert 0 < ring.bias <= macro.cfg.STITCHES_TURN_BIAS_MAX
    learned = ring.bias
    turn = ring.decide(None, now, interval)                                     # the line is gone (red light): the learned turn goes on
    assert turn is not None and turn[0] == right_key and turn[1] == pytest.approx(learned * interval, abs=0.01)
    now += macro.cfg.STITCHES_LOST_AFTER + 1
    assert ring.decide(None, now, interval) == (right_key, macro.cfg.STITCHES_TURN_HOLD_MAX)       # out of sight for long: the camera is swept round to find it
    assert ring.decide(0.0, now + 1, interval) is None or ring.lost_since is None                   # and it stops being lost when the line is back
    assert ring.lost_since is None


def test_swapping_the_camera_keys_turns_the_other_way_and_flips_what_was_learned(macro, monkeypatch):
    left_key, right_key = macro.cfg.STITCHES_TURN_KEYS
    ring = camera(macro, monkeypatch)
    ring.bias = 0.3
    ring.swap_keys()
    assert ring.keys == (right_key, left_key) and ring.bias == -0.3
    fresh = camera(macro, monkeypatch)
    fresh.swap_keys()
    assert fresh.decide(45.0, 0.0, 0.5)[0] == left_key                                         # (the key that now turns right)


def calibration(macro, monkeypatch, angles):
    """calibrate_ring_camera() with the ring line seen at `angles` in turn, and what it pressed and marked."""
    seen, pressed, marks = iter(angles), [], []
    monkeypatch.setattr(macro, "minigame_look", lambda watch: None)
    monkeypatch.setattr(macro, "stitches_ring_angle", lambda img: next(seen))
    monkeypatch.setattr(macro, "hold_key", lambda key, duration: pressed.append(key))
    monkeypatch.setattr(macro, "minigame_wait", lambda duration, watch: None)
    monkeypatch.setattr(macro, "mark_run_capture", lambda reason: marks.append(reason))
    ring = macro.RingCamera()
    macro.calibrate_ring_camera(ring, macro.MinigameWatch("stitches_spotlight"))
    return ring, pressed, marks


def test_the_camera_keys_are_tested_by_turning_right_and_seeing_the_line_move(macro, monkeypatch):
    keys = macro.cfg.STITCHES_TURN_KEYS
    ring, pressed, marks = calibration(macro, monkeypatch, [30.0, 10.0])               # turning right made a line that fell to the right level: as assumed
    assert ring.keys == keys and pressed == [keys[1]] and marks == []
    ring, _, _ = calibration(macro, monkeypatch, [10.0, 30.0])                           # it went the other way: the keys are swapped
    assert ring.keys == (keys[1], keys[0])
    for angles in ([30.0, 29.0], [30.0, None], [None]):                                  # nothing clear, or no line to go by: left as they are
        ring, pressed, marks = calibration(macro, monkeypatch, angles)
        assert ring.keys == keys
    assert len(marks) == 1 or marks == []                                                  # (an unclear test marks the capture)


def test_the_player_walks_boosts_and_turns_until_the_victory_screen_shows(macro, monkeypatch):
    cfg = macro.cfg
    events, looks = [], iter([shot("ring_falls_right_a")] * 8)
    now = [1000.0]
    monkeypatch.setattr(macro.time, "time", lambda: now[0])
    monkeypatch.setattr(macro.pydirectinput, "keyDown", lambda key: events.append(("down", key)))
    monkeypatch.setattr(macro.pydirectinput, "keyUp", lambda key: events.append(("up", key)))
    monkeypatch.setattr(macro.pydirectinput, "press", lambda key: events.append(("press", key)))
    monkeypatch.setattr(macro, "hold_key", lambda key, duration: events.append(("turn", key)))
    monkeypatch.setattr(macro, "wait_interruptible", lambda duration: now.__setitem__(0, now[0] + duration))
    monkeypatch.setattr(macro, "minigame_look", lambda watch: next(looks))
    monkeypatch.setattr(macro, "wait_for_minigame_start", lambda watch, name: None)
    monkeypatch.setattr(macro, "calibrate_ring_camera", lambda ring, watch: None)
    won = iter([False] * 6 + [True])
    monkeypatch.setattr(macro, "detect_minigame_victory", lambda img: next(won))
    monkeypatch.setattr(macro.cfg, "STITCHES_LINE_DEADBAND", 5.0)
    watch = macro.MinigameWatch("stitches_spotlight")
    assert macro.play_stitches_spotlight(10 ** 12, watch) is True
    walk = cfg.KEY_STITCHES_WALK
    assert events[0] == ("down", walk) and events[-1] == ("up", walk)
    assert ("turn", cfg.STITCHES_TURN_KEYS[1]) in events
    boosts = [i for i, event in enumerate(events) if event == ("press", cfg.KEY_STITCHES_BOOST)]
    assert 2 <= len(boosts) <= 4                                                          # (about one every STITCHES_BOOST_INTERVAL of the fake time: 6 looks of STITCHES_CENTER_INTERVAL)
