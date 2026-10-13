"""Playing Ghost Gallery. The fixtures are crops of a real 162 s run played by a human (tests/ghost_gallery/), pasted back where they
were taken from on a blank screen: ghosts in the hall, and the boss with its health bar. Time, keys and the mouse are faked."""
import os
import types

import cv2
import numpy as np
import pytest

HERE = os.path.join(os.path.dirname(__file__), "ghost_gallery")


def shot(name):
    return cv2.imread(os.path.join(HERE, f"{name}.png"))


def blank():
    return np.zeros((1080, 1920, 3), np.uint8)


# --- seeing ghosts and the boss ---

def test_the_ghosts_in_the_hall_are_found_biggest_first(macro):
    blobs = macro.find_ghost_blobs(shot("ghosts_50"))
    (x, y, area), *rest = blobs
    assert abs(x - 1047) <= 5 and abs(y - 471) <= 5 and area > 5000                   # the ghost the human held the beam on
    assert [b[2] for b in blobs] == sorted((b[2] for b in blobs), reverse=True)
    assert all(b[2] >= macro.cfg.GHOST_MIN_AREA for b in blobs)


def test_several_ghosts_at_once_are_all_found(macro):
    blobs = macro.find_ghost_blobs(shot("ghosts_66"))
    near = lambda x, y: any(abs(bx - x) <= 8 and abs(by - y) <= 8 for bx, by, _ in blobs)
    assert near(450, 290) and near(1018, 388) and near(1184, 778) and near(1137, 334)


def test_nothing_is_a_ghost_on_a_blank_screen_or_in_the_hud(macro):
    assert macro.find_ghost_blobs(blank()) == []
    screen = blank()
    screen[20:60, 900:1000] = cv2.cvtColor(np.full((40, 100, 3), (90, 140, 250), np.uint8), cv2.COLOR_HSV2BGR)     # ghost-coloured, but in the HUD
    assert macro.find_ghost_blobs(screen) == []


def test_the_boss_bar_marks_the_boss_phase(macro):
    assert macro.ghost_boss_bar_visible(shot("boss_100")) and macro.ghost_boss_bar_visible(shot("boss_118"))
    assert not macro.ghost_boss_bar_visible(shot("ghosts_50")) and not macro.ghost_boss_bar_visible(shot("ghosts_66"))
    assert not macro.ghost_boss_bar_visible(blank())


def test_the_boss_is_the_biggest_blob_and_its_health_bar_is_not_a_ghost(macro):
    blobs = macro.find_ghost_blobs(shot("boss_100"), boss=True)
    assert len(blobs) == 1 and blobs[0][2] > 20000 and abs(blobs[0][0] - 1089) <= 10        # just the boss...
    assert any(y < 200 for _, y, _ in macro.find_ghost_blobs(shot("boss_100")))               # ...where without the exception the bar's crest counts too
    assert macro.find_ghost_blobs(shot("boss_118"), boss=True)[0][2] > 4000


# --- choosing where to aim ---

def test_the_ghost_nearest_the_character_is_aimed_at_and_the_boss_phase_takes_the_biggest(macro):
    cx, cy = macro.cfg.GHOST_CHARACTER
    blobs = [(cx + 400, cy, 9000), (cx + 100, cy - 50, 1000), (cx - 300, cy, 3000)]
    assert macro.choose_ghost_target(blobs, False, None) == (cx + 100, cy - 50)
    assert macro.choose_ghost_target(blobs, True, None) == (cx + 400, cy)
    assert macro.choose_ghost_target([], False, None) is None and macro.choose_ghost_target([], True, (1, 1)) is None


def test_a_ghost_being_followed_is_not_swapped_for_a_nearer_one(macro):
    cx, cy = macro.cfg.GHOST_CHARACTER
    stick = macro.cfg.GHOST_STICK
    blobs = [(cx + 300, cy, 2000), (cx + 50, cy, 2000)]
    assert macro.choose_ghost_target(blobs, False, (cx + 300 - stick // 2, cy)) == (cx + 300, cy)      # it moved a little: still that one
    assert macro.choose_ghost_target(blobs, False, (cx + 300 + stick * 3, cy)) == (cx + 50, cy)       # it is gone: the nearest


# --- the player ---

def watch_started(macro, started=True):
    return types.SimpleNamespace(problem=None, last_seen=1.0 if started else None, start_wait_over=lambda: None)


def test_it_aims_at_the_ghost_holds_the_button_and_lets_go_when_it_is_gone(macro, world, monkeypatch):
    ghost = shot("ghosts_50")
    big = macro.find_ghost_blobs(ghost)[0][:2]
    world.screens = [ghost] * 5 + [blank()]
    ups = []
    monkeypatch.setattr(macro.pydirectinput, "mouseUp", lambda *a, **k: ups.append(world.looks), raising=False)
    macro.play_ghost_gallery(world.clock.now + 4, watch_started(macro))
    assert ups[0] <= 7 < world.looks                                                       # let go at once when it was gone, not only at the end
    assert world.mouse[:2] == [("move", *big), ("down",)]                                  # aimed first, then held
    assert world.mouse.count(("down",)) == 1                                              # held through, once
    moves = [m for m in world.mouse if m[0] == "move"]
    assert len(moves) >= 5 and all(m[1:] == big for m in moves[:5])                         # follows it every look


def test_in_the_boss_phase_it_holds_on_the_boss(macro, world):
    boss = shot("boss_100")
    x, y, _ = macro.find_ghost_blobs(boss, boss=True)[0]
    world.screens = [boss]
    macro.play_ghost_gallery(world.clock.now + 1, watch_started(macro))
    assert world.mouse[0] == ("move", x, y) and ("down",) in world.mouse


def test_without_a_ghost_it_walks_about_after_a_while_and_not_before(macro, world):
    cfg = macro.cfg
    world.screens = [blank()]
    macro.play_ghost_gallery(world.clock.now + cfg.GHOST_SEARCH_AFTER * 0.8, watch_started(macro))
    assert world.keys == [] and world.mouse == []
    world.keys.clear()
    macro.play_ghost_gallery(world.clock.now + cfg.GHOST_SEARCH_AFTER * 2.5, watch_started(macro))
    keys = [key for kind, key, _ in world.keys if kind == "down"]
    assert keys and set(keys) <= set(cfg.MOVE_KEYS)
    assert [k for k, _, _ in world.keys].count("down") == [k for k, _, _ in world.keys].count("up")


def test_it_waits_for_the_overlay_before_acting(macro, world, monkeypatch):
    cfg = macro.cfg
    ghost = shot("ghosts_50")
    watch = watch_started(macro, started=False)
    looks, before = [], []

    def look(w):
        looks.append(1)
        if len(looks) == 4:
            w.last_seen = 1.0                                                              # the game begins
            before.extend(world.mouse)
        return ghost
    monkeypatch.setattr(macro, "minigame_look", look)
    monkeypatch.setattr(macro, "minigame_wait", lambda duration, w: look(w) is None)
    macro.play_ghost_gallery(world.clock.now + cfg.MINIGAME_START_WAIT + 5, watch)
    assert before == []                                                                    # nothing was done while the game had not begun
    assert len(looks) >= 4 and world.mouse and world.mouse[0][0] == "move"


def test_a_game_that_never_shows_its_overlay_is_acted_in_after_the_start_wait(macro, world):
    cfg = macro.cfg
    world.screens = [shot("ghosts_50")]
    start = world.clock.now
    macro.play_ghost_gallery(start + cfg.MINIGAME_START_WAIT + 3, watch_started(macro, started=False))
    assert world.mouse and world.clock.now - start >= cfg.MINIGAME_START_WAIT           # it waited the whole time, then went on regardless


def test_the_victory_screen_ends_it_and_the_button_is_let_go(macro, world, monkeypatch):
    world.screens = [shot("ghosts_50")]
    monkeypatch.setattr(macro, "detect_minigame_victory", lambda img: len(world.mouse) > 6)
    assert macro.play_ghost_gallery(world.clock.now + 100, watch_started(macro)) is True
    assert world.mouse[-1] == ("up",)


def test_stopping_lets_go_of_the_button(macro, world, monkeypatch):
    world.screens = [shot("ghosts_50")]

    def stop(duration):
        raise macro.StopRequested()
    monkeypatch.setattr(macro, "wait_interruptible", stop)
    with pytest.raises(macro.StopRequested):
        macro.play_ghost_gallery(world.clock.now + 100, watch_started(macro))
    assert world.mouse[-1] == ("up",) and world.mouse.count(("down",)) == 1
