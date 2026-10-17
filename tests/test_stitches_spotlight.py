"""Playing Stitches' Spotlight: where candy corn lies, whether the searchlight is on the character or planks hide it, and what is done about it.
Frames are real captures of a round (tests/stitches_spotlight/); time and the keyboard are faked."""
import os
import time

import cv2
import numpy as np

HERE = os.path.join(os.path.dirname(__file__), "stitches_spotlight")


def shot(name):
    return cv2.imread(os.path.join(HERE, f"{name}.png"))


def test_the_searchlight_on_the_character_is_told_from_a_safe_screen_and_from_hiding_behind_planks(macro):
    for name, red, covered in (("safe", False, False), ("red_on_character", True, False), ("behind_planks", False, True)):
        left, right = macro.stitches_red(shot(name))
        assert ((left + right) / 2 >= macro.cfg.STITCHES_DANGER_FRACTION) == red, name
        assert macro.stitches_covered(shot(name)) == covered, name


def test_candy_corn_is_found_on_the_screen_and_not_in_the_hud_or_as_stripes(macro):
    pieces = macro.find_candy_corn(shot("red_on_character"))
    assert len(pieces) >= 10
    left, top, right, bottom = macro.cfg.STITCHES_SEARCH_BOX
    assert all(left <= x < right and top <= y < bottom for x, y in pieces)
    assert macro.find_candy_corn(np.zeros((1080, 1920, 3), np.uint8)) == []


def test_in_the_red_the_player_steps_away_from_it_but_behind_planks_stays_put(macro, monkeypatch):
    img = shot("red_on_character")
    left, right = macro.stitches_red(img)
    key, seconds = macro.stitches_key(img)
    assert key == ("d" if left > right else "a") and seconds == macro.cfg.STITCHES_FLEE_STEP
    monkeypatch.setattr(macro, "stitches_covered", lambda img: True)
    assert macro.stitches_key(img) is None


def test_out_of_the_red_the_player_walks_towards_the_nearest_candy_or_wanders_without_any(macro, monkeypatch):
    cx, cy = macro.cfg.STITCHES_CHARACTER
    for piece, expected in (((cx + 300, cy + 20), "d"), ((cx - 300, cy), "a"), ((cx, cy - 250), "w"), ((cx + 10, cy + 200), "s")):
        monkeypatch.setattr(macro, "find_candy_corn", lambda img, piece=piece: [piece, (cx + 900, cy + 400)])
        assert macro.stitches_key(shot("safe"))[0] == expected
    monkeypatch.setattr(macro, "find_candy_corn", lambda img: [(cx + 10, cy + 10)])      # one already under the character (the magnet has it)
    assert macro.stitches_key(shot("safe"))[0] in macro.cfg.MOVE_KEYS


def test_the_player_holds_keys_until_the_victory_screen_shows(macro, monkeypatch):
    held, looks = [], iter([shot("safe"), shot("safe"), cv2.imread(os.path.join(os.path.dirname(__file__), "fixtures", "win_banner.png"))])
    monkeypatch.setattr(macro, "hold_key", lambda key, duration: held.append(key))
    monkeypatch.setattr(macro, "minigame_wait", lambda duration, watch: None)
    monkeypatch.setattr(macro, "minigame_look", lambda watch: next(looks))
    monkeypatch.setattr(macro, "detect_minigame_victory", lambda img: img.shape[0] != 1080)
    watch = macro.MinigameWatch("stitches_spotlight")
    watch.last_seen = time.time()
    assert macro.play_stitches_spotlight(10 ** 12, watch) is True
    assert len(held) == 2
