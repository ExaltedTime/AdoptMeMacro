"""Playing Ghost Gallery. The fixtures are crops of a real 162 s run played by a human (tests/ghost_gallery/), pasted back where they
were taken from on a blank screen: ghosts in the hall, and the boss with its health bar. Time, keys and the mouse are faked."""
import os
import types

import cv2
import numpy as np
import pytest

HERE = os.path.join(os.path.dirname(__file__), "ghost_gallery")


@pytest.fixture(autouse=True)
def no_intro_wait(macro, monkeypatch):
    """The game's intro camera is waited out before the camera is set up, and the walk into the hall follows it; the tests that
    don't look at those skip the time (the walk stays, as a hold of no length)."""
    monkeypatch.setattr(macro.cfg, "GHOST_INTRO_WAIT", 0.0)
    monkeypatch.setattr(macro.cfg, "GHOST_START_FORWARD", 0.0)
    monkeypatch.setattr(macro.cfg, "GHOST_REGRAB_PERIOD", 10 ** 6)                  # (the tests that look at it set it)
    monkeypatch.setattr(macro.cfg, "MINIGAME_START_WAIT", 6.0)                       # (the fake clock still has every look of it to do)


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
    several = macro.find_ghost_blobs(shot("ghosts_66"))                                       # four ghosts at once
    near = lambda x, y: any(abs(bx - x) <= 20 and abs(by - y) <= 20 for bx, by, _ in several)
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


def test_it_aims_at_the_ghost_follows_it_and_moves_on_to_furniture_when_it_is_gone(macro, world):
    cfg = macro.cfg
    ghost = shot("ghosts_50")
    big = macro.find_ghost_blobs(ghost)[0][:2]
    world.screens = [ghost] * 5 + [blank()]
    macro.play_ghost_gallery(world.clock.now + 4, watch_started(macro))
    assert world.mouse[:2] == [("move", *big), ("down",)]                                  # aimed first, then held
    moves = [m for m in world.mouse if m[0] == "move"]
    assert all(m[1:] == big for m in moves[:5])                                            # follows it every look
    left, top, right, bottom = cfg.GHOST_FURNITURE_BOX
    assert all(left <= m[1] <= right and top <= m[2] <= bottom for m in moves[5:]) and len(moves) > 5      # then the furniture
    assert world.mouse.count(("down",)) == 1 and ("up",) not in world.mouse[:-1]            # the beam is not let go of in a short while, only moved around


def test_in_the_boss_phase_it_holds_on_the_boss(macro, world):
    boss = shot("boss_100")
    x, y, _ = macro.find_ghost_blobs(boss, boss=True)[0]
    world.screens = [boss]
    macro.play_ghost_gallery(world.clock.now + 1, watch_started(macro))
    assert world.mouse[0] == ("move", x, y) and ("down",) in world.mouse


def left_held_on(world):
    """The places the beam was held on: the mouse moves made while the left button was down (but not the one to the middle of the
    screen that a camera drag, the right button going down, starts with)."""
    places, down = [], False
    for i, event in enumerate(world.mouse):
        if event == ("down",):
            down = True
        elif event == ("up",):
            down = False
        elif event[0] == "move" and down and world.mouse[i + 1:i + 2] != [("down", "right")]:
            places.append(event[1:])
    return places


def test_before_the_boss_with_no_ghost_in_sight_the_beam_is_held_on_random_furniture(macro, world):
    cfg = macro.cfg
    world.screens = [blank()]
    macro.play_ghost_gallery(world.clock.now + cfg.GHOST_FURNITURE_PERIOD * 5.5, watch_started(macro))
    places = left_held_on(world)
    left, top, right, bottom = cfg.GHOST_FURNITURE_BOX
    assert places and all(left <= x <= right and top <= y <= bottom for x, y in places)
    cx, cy = cfg.GHOST_CHARACTER
    assert all(((x - cx) ** 2 + (y - cy) ** 2) ** .5 >= cfg.GHOST_FURNITURE_MIN_DISTANCE for x, y in places)      # not next to the character
    assert len(set(places)) >= 3                                                               # somewhere else every GHOST_FURNITURE_PERIOD


def test_the_camera_is_turned_by_dragging_with_the_right_button_when_there_is_nothing_to_aim_at(macro, world):
    cfg = macro.cfg
    world.screens = [blank()]
    macro.play_ghost_gallery(world.clock.now + cfg.GHOST_CAMERA_PERIOD * 2.5, watch_started(macro))
    drags, current = [], None
    for event in world.mouse:
        if event == ("down", "right"):
            current = []
        elif event == ("up", "right"):
            drags.append(current)
            current = None
        elif event[0] == "rel" and current is not None:
            current.append(event[1:])
    assert len(drags) >= 2 and current is None                                                 # turned more than once, and every time let go
    centre = ("move", cfg.REFERENCE_CENTER_X, cfg.REFERENCE_CENTER_Y)
    downs = [i for i, event in enumerate(world.mouse) if event == ("down", "right")]
    assert all(world.mouse[i - 1] == centre for i in downs)                                    # each drag starts in the middle, far from the window's border
    for steps in drags:
        assert len(steps) == cfg.GHOST_CAMERA_DRAG_STEPS
        total_x = sum(dx for dx, _ in steps)
        assert cfg.GHOST_CAMERA_DRAG[0] - cfg.GHOST_CAMERA_DRAG_STEPS <= abs(total_x) <= cfg.GHOST_CAMERA_DRAG[1]
        assert abs(sum(dy for _, dy in steps)) <= cfg.GHOST_CAMERA_TILT


def test_the_camera_is_left_alone_while_there_is_a_ghost(macro, world):
    cfg = macro.cfg
    world.screens = [shot("ghosts_50")]
    macro.play_ghost_gallery(world.clock.now + cfg.GHOST_CAMERA_PERIOD * 3, watch_started(macro))
    assert ("down", "right") not in world.mouse


def test_it_jumps_every_so_often(macro, world):
    cfg = macro.cfg
    world.screens = [shot("ghosts_50")]
    start = world.clock.now
    macro.play_ghost_gallery(start + cfg.GHOST_JUMP_MAX * 4.5, watch_started(macro))
    jumps = [at for kind, key, at in world.keys if kind == "press" and key == cfg.KEY_JUMP]
    assert 3 <= len(jumps) <= 10
    gaps = np.diff([start] + jumps)
    assert gaps.min() >= cfg.GHOST_JUMP_MIN - 0.5 and gaps.max() <= cfg.GHOST_JUMP_MAX + 0.5


# --- the boss's shield ---

def test_the_boss_is_shielded_while_its_bar_shows_ghost_icons_not_health(macro):
    assert not macro.ghost_boss_shielded(shot("boss_100")) and not macro.ghost_boss_shielded(shot("boss_118"))
    for name in ("shield_129", "shield_141"):
        assert macro.ghost_boss_bar_visible(shot(name)) and macro.ghost_boss_shielded(shot(name))
    cx, cy = macro.cfg.GHOST_CHARACTER
    blobs = macro.find_ghost_blobs(shot("shield_129"), boss=True)
    assert len(blobs) == 3 and max(b[2] for b in blobs) < macro.cfg.GHOST_SMALL_MAX                                  # only small ghosts: the dim boss is not cyan enough
    assert macro.choose_ghost_target(blobs, False, None) == min(((x, y) for x, y, _ in blobs), key=lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2)


def test_the_player_aims_at_the_small_ghosts_while_shielded_and_at_the_boss_otherwise(macro, world):
    shielded = shot("shield_129")
    want = macro.choose_ghost_target(macro.find_ghost_blobs(shielded, boss=True), False, None)
    world.screens = [shielded]
    macro.play_ghost_gallery(world.clock.now + 1, watch_started(macro))
    assert world.mouse[0] == ("move", *want)
    world.mouse.clear()
    boss = shot("boss_100")
    world.screens = [boss]
    macro.play_ghost_gallery(world.clock.now + 1, watch_started(macro))
    assert world.mouse[0] == ("move", *macro.find_ghost_blobs(boss, boss=True)[0][:2])


def test_in_the_boss_phase_with_nothing_to_aim_at_it_does_not_hold_the_beam_on_furniture(macro, world):
    cfg = macro.cfg
    bar_only = np.zeros((1080, 1920, 3), np.uint8)
    boss = shot("boss_100")
    left, top, right, bottom = cfg.GHOST_BOSS_BAR_BOX
    bar_only[top:270, left:right] = boss[top:270, left:right]                                # the bar, and no boss, no ghost
    world.screens = [boss] * 3 + [bar_only]
    macro.play_ghost_gallery(world.clock.now + cfg.GHOST_CAMERA_PERIOD * 2.5, watch_started(macro))
    assert ("down",) in world.mouse and world.mouse.count(("down",)) == 1                  # held on the boss while it was there...
    assert left_held_on(world)[3:] == []                                                   # ...and not on anything once it is not
    assert ("up",) not in world.mouse[:-1]                                                 # the beam is not let go of in a short while


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
        if ("down",) in world.mouse:                     # (once the beam is held)
            raise macro.StopRequested()
        world.clock.now += duration
    monkeypatch.setattr(macro, "wait_interruptible", stop)
    with pytest.raises(macro.StopRequested):
        macro.play_ghost_gallery(world.clock.now + 100, watch_started(macro))
    assert world.mouse[-1] == ("up",) and world.mouse.count(("down",)) == 1


# --- the view, walking, the furniture ---

def test_it_starts_with_a_zoomed_out_tilted_down_view_once_the_overlay_shows(macro, world, monkeypatch):
    cfg = macro.cfg
    looks, seen = [], {}

    def look(w):
        looks.append(1)
        if len(looks) == 3:
            w.last_seen = 1.0
            seen["zoomed_before"] = len(world.zooms)
        return blank()
    watch = watch_started(macro, started=False)
    monkeypatch.setattr(macro, "minigame_look", look)
    monkeypatch.setattr(macro, "minigame_wait", lambda duration, w: look(w) is None)
    macro.play_ghost_gallery(world.clock.now + cfg.MINIGAME_START_WAIT + 2, watch)
    assert seen["zoomed_before"] == 0 and len(world.zooms) == 1 and len(world.tilts) == 1      # once, after the game began


def test_zooming_out_holds_the_zoom_out_key_and_the_tilt_drags_down_with_the_right_button(macro, world):
    cfg = macro.cfg
    world.real_zoom_out()
    assert [(kind, key) for kind, key, _ in world.keys] == [("down", cfg.KEY_ZOOM_OUT), ("up", cfg.KEY_ZOOM_OUT)]
    assert world.keys[1][2] - world.keys[0][2] == pytest.approx(cfg.MINIGAME_ZOOM_OUT_DURATION)
    world.real_tilt()
    assert world.mouse[:2] == [("move", cfg.REFERENCE_CENTER_X, cfg.REFERENCE_CENTER_Y), ("down", "right")] and world.mouse[-1] == ("up", "right")
    drags = [e for e in world.mouse if e[0] == "rel"]
    assert len(drags) == cfg.GHOST_CAMERA_DRAG_STEPS and all(dx == 0 and dy > 0 for _, dx, dy in drags)       # straight down
    assert sum(dy for _, _, dy in drags) == pytest.approx(cfg.GHOST_START_TILT, abs=cfg.GHOST_CAMERA_DRAG_STEPS)


def test_it_walks_about_a_step_at_a_time_without_stopping_the_aiming(macro, world):
    cfg = macro.cfg
    world.screens = [blank()]                                                                  # nothing in sight
    start = world.clock.now
    macro.play_ghost_gallery(start + cfg.GHOST_MOVE_PERIOD * 3.5, watch_started(macro))
    steps = [(kind, key, at) for kind, key, at in world.keys if key in cfg.MOVE_KEYS][2:]      # (after the walk at the start)
    downs = [at for kind, _, at in steps if kind == "down"]
    assert len(downs) >= 2 and downs[0] - start >= cfg.GHOST_MOVE_PERIOD - 0.5                  # not at once
    held = 0
    for kind, _, _ in steps:                                                                   # one key at a time, always let go
        held += 1 if kind == "down" else -1
        assert held in (0, 1)
    assert held == 0
    for (kind, _, at), (kind2, _, at2) in zip(steps[::2], steps[1::2]):
        assert cfg.GHOST_MOVE_STEP_MIN - 0.3 <= at2 - at <= cfg.GHOST_MOVE_STEP_MAX + 0.3 + (cfg.GHOST_FURNITURE_PROBES + 1) * cfg.GHOST_OUTLINE_WAIT      # (a step ends at the next look, which can be after a search for furniture)
    assert world.mouse.count(("down",)) >= 1 and len([m for m in world.mouse if m[0] == "move"]) > 20       # it kept aiming meanwhile


def test_the_furniture_is_aimed_at_far_from_the_character(macro):
    cfg = macro.cfg
    cx, cy = cfg.GHOST_CHARACTER
    left, top, right, bottom = cfg.GHOST_FURNITURE_BOX
    for _ in range(300):
        x, y = macro.far_from_character(cfg.GHOST_FURNITURE_BOX, cfg.GHOST_FURNITURE_MIN_DISTANCE)
        assert left <= x <= right and top <= y <= bottom and ((x - cx) ** 2 + (y - cy) ** 2) ** .5 >= cfg.GHOST_FURNITURE_MIN_DISTANCE
    assert macro.far_from_character((cx - 5, cy - 5, cx + 5, cy + 5), 400) in ((cx - 5, cy - 5), (cx + 5, cy - 5), (cx - 5, cy + 5), (cx + 5, cy + 5))   # a box with nothing far enough


def test_the_camera_is_set_up_only_after_the_games_intro_camera_has_run(macro, world, monkeypatch):
    cfg = macro.cfg
    monkeypatch.setattr(cfg, "GHOST_INTRO_WAIT", 6.0)
    world.screens = [blank()]
    start = world.clock.now
    macro.play_ghost_gallery(start + 10, watch_started(macro))
    assert len(world.zooms) == 1 and world.zooms[0] - start >= cfg.GHOST_INTRO_WAIT          # zoomed out after the intro, not during it
    assert world.tilts and world.tilts[0] >= world.zooms[0]


def test_what_is_cyan_at_the_bottom_of_the_screen_is_not_a_ghost(macro):
    """A pet (or a costume) in the ghosts' own cyan sat there in the macro-played run, and was aimed at for the whole run."""
    screen = blank()
    cyan = cv2.cvtColor(np.full((200, 300, 3), (90, 125, 252), np.uint8), cv2.COLOR_HSV2BGR)
    screen[880:1080, 1500:1800] = cyan
    assert macro.find_ghost_blobs(screen) == []
    screen[300:500, 700:1000] = cyan                                                          # the same blob higher up is one
    assert len(macro.find_ghost_blobs(screen)) == 1


def test_it_walks_forward_at_the_start_and_then_towards_the_ghost(macro, world, monkeypatch):
    cfg = macro.cfg
    monkeypatch.setattr(cfg, "GHOST_START_FORWARD", 2.0)
    cx, cy = cfg.GHOST_CHARACTER
    far = cfg.GHOST_TOWARD_MARGIN * 3
    assert macro.ghost_chase_key((cx, cy - far)) == "w" and macro.ghost_chase_key((cx, cy + far)) == cfg.HAUNTLET_BACK_KEY
    assert macro.ghost_chase_key((cx - far, cy)) == cfg.HAUNTLET_LEFT_KEY and macro.ghost_chase_key((cx + far, cy - 10)) == cfg.HAUNTLET_RIGHT_KEY
    assert macro.ghost_chase_key((cx + 5, cy - 5)) is None and macro.ghost_chase_key((cx, cy - cfg.GHOST_TOWARD_MARGIN)) is None    # near enough for the beam
    world.screens = [blank()]
    start = world.clock.now
    macro.play_ghost_gallery(start + cfg.GHOST_START_FORWARD + 1, watch_started(macro))
    first = [(kind, key, at) for kind, key, at in world.keys if key in cfg.MOVE_KEYS][:2]
    assert [k[:2] for k in first] == [("down", "w"), ("up", "w")]
    assert first[1][2] - first[0][2] == pytest.approx(cfg.GHOST_START_FORWARD)
    assert len(world.tilts) == 1 and first[0][2] >= world.tilts[0]                              # once the view is set up


def test_a_ghost_far_from_the_character_is_chased_with_a_key_held_until_it_is_near(macro, world):
    cfg = macro.cfg
    far = shot("shield_129")                                                         # a small ghost well to the right
    near = shot("ghosts_50")                                                         # one close to the character
    key = macro.ghost_chase_key(macro.choose_ghost_target(macro.find_ghost_blobs(far, True), False, None))
    world.screens = [far] * 6 + [near]
    macro.play_ghost_gallery(world.clock.now + 4, watch_started(macro))
    moves = [(kind, k, at) for kind, k, at in world.keys if k == key]
    assert key != "w" and [m[0] for m in moves[:2]] == ["down", "up"]                # held, then let go
    assert moves[1][2] - moves[0][2] >= 5 * cfg.GHOST_LOOK_INTERVAL                  # for as many looks as the ghost was far, not a step of GHOST_MOVE_STEP_MAX


def test_in_the_boss_phase_with_nothing_in_sight_it_turns_the_camera_one_way_until_it_sees_something(macro, world):
    cfg = macro.cfg
    bar_only = np.zeros((1080, 1920, 3), np.uint8)
    boss = shot("boss_100")
    left, top, right, bottom = cfg.GHOST_BOSS_BAR_BOX
    bar_only[top:270, left:right] = boss[top:270, left:right]
    world.screens = [bar_only] * 8 + [boss]
    macro.play_ghost_gallery(world.clock.now + 6, watch_started(macro))
    drags, current = [], None
    for event in world.mouse:
        if event == ("down", "right"):
            current = 0
        elif event == ("up", "right"):
            drags.append(current)
        elif event[0] == "rel" and current is not None:
            current += event[1]
    first = drags[:8]
    assert len(first) == 8 and len({d > 0 for d in first}) == 1                       # a turn at every look, all the same way
    assert all(cfg.GHOST_BOSS_TURN_DRAG[0] - cfg.GHOST_CAMERA_DRAG_STEPS <= abs(d) <= cfg.GHOST_BOSS_TURN_DRAG[1] + cfg.GHOST_CAMERA_DRAG_STEPS for d in first)
    assert len(drags) == 8                                                            # and no more once the boss is in sight


def white_ring(point, radius=60):
    ring = np.zeros((1080, 1920, 3), np.uint8)
    cv2.circle(ring, point, radius, (215, 220, 205), 3)                              # pale, as the real outline is: 190-230, not pure white
    return ring


def test_furniture_is_the_place_that_turns_white_when_the_mouse_is_over_it(macro):
    cfg = macro.cfg
    spot = (900, 400)
    dark = blank()
    assert macro.furniture_outlined(dark, white_ring(spot), spot)
    assert not macro.furniture_outlined(dark, dark, spot)                            # nothing happened
    assert not macro.furniture_outlined(dark, white_ring((300, 700)), spot)           # a white thing elsewhere
    assert not macro.furniture_outlined(white_ring(spot), white_ring(spot), spot)     # white that was already there is not an outline
    assert not macro.furniture_outlined(dark, white_ring(spot, 3), spot)              # a speck, not an outline
    assert cfg.GHOST_OUTLINE_MIN > 3


def test_the_beam_is_held_on_the_furniture_that_gets_the_outline(macro, world, monkeypatch):
    cfg = macro.cfg
    places = [(400, 300), (1500, 700), (1200, 200), (300, 600)]
    queue = iter(places * 20)
    monkeypatch.setattr(macro, "far_from_character", lambda box, minimum: next(queue))
    chosen = places[2]

    def screen():
        moved = [m for m in world.mouse if m[0] == "move"]
        return white_ring(chosen) if moved and moved[-1][1:] == chosen else blank()
    world.screens = screen
    macro.play_ghost_gallery(world.clock.now + cfg.GHOST_FURNITURE_PERIOD * 0.5, watch_started(macro))
    held = left_held_on(world)
    assert held and held[0] == chosen                                                  # the third place tried got the outline: the beam goes there


def test_it_stands_still_while_the_ghost_is_near_enough_and_never_wanders_while_one_is_in_sight(macro, world):
    cfg = macro.cfg
    world.screens = [shot("ghosts_50")]                                               # a ghost near the character
    macro.play_ghost_gallery(world.clock.now + cfg.GHOST_MOVE_PERIOD * 3.5, watch_started(macro))
    assert not [k for k in world.keys if k[0] == "down" and k[1] in cfg.MOVE_KEYS][1:]    # (after the start's walk)


def test_the_beam_is_let_go_of_and_pressed_again_every_regrab_period(macro, world, monkeypatch):
    cfg = macro.cfg
    monkeypatch.setattr(cfg, "GHOST_REGRAB_PERIOD", 4.0)
    world.screens = [shot("ghosts_50")]
    start = world.clock.now
    macro.play_ghost_gallery(start + cfg.GHOST_REGRAB_PERIOD * 2.5, watch_started(macro))
    beam = [(e, i) for i, e in enumerate(world.mouse) if e in (("down",), ("up",))]
    assert [e for e, _ in beam[:5]] == [("down",), ("up",), ("down",), ("up",), ("down",)]     # held, let go and held again, twice


def test_a_close_camera_does_not_make_the_characters_own_clothes_and_pet_ghosts(macro, world):
    img = shot("closeup_189")                                                      # a shielded boss fight, the camera pulled in against a wall
    assert macro.ghost_character_box(cv2.cvtColor(img, cv2.COLOR_BGR2HSV)) is not None
    assert macro.find_ghost_blobs(img, True) == []                                 # the hoodie, the pet and the beam are not targets (they were, and it was chased)
    assert macro.ghost_character_box(cv2.cvtColor(blank(), cv2.COLOR_BGR2HSV)) is None and macro.ghost_character_box(cv2.cvtColor(shot("ghosts_50"), cv2.COLOR_BGR2HSV)) is None


def test_in_the_shielded_phase_a_blob_too_big_for_a_small_ghost_is_not_aimed_at(macro, world, monkeypatch):
    cfg = macro.cfg
    img = shot("shield_175")                                                       # the bar with the ghost icons: shielded; the beam's glow was 9500 px
    assert macro.ghost_boss_shielded(img) and any(area > cfg.GHOST_SMALL_MAX for _, _, area in macro.find_ghost_blobs(img, True))
    glow, small = (1000, 700, cfg.GHOST_SMALL_MAX + 1000), (1500, 845, 1121)       # (the glow nearer the character than the small ghost)
    monkeypatch.setattr(macro, "find_ghost_blobs", lambda image, boss=False: [glow, small])
    world.screens = [img]
    macro.play_ghost_gallery(world.clock.now + 1, watch_started(macro))
    assert world.mouse[0] == ("move", small[0], small[1])                          # the small ghost, not the glow


def test_in_the_boss_phase_a_blob_too_small_for_the_boss_is_a_minion_not_the_boss(macro, world):
    cfg = macro.cfg
    boss = shot("boss_100")
    screen = np.zeros((1080, 1920, 3), np.uint8)
    left, top, right, bottom = cfg.GHOST_BOSS_BAR_BOX
    screen[top:270, left:right] = boss[top:270, left:right]                        # the bar of the boss phase, the boss able to be hurt
    ghost = (244, 234, 56)                                                         # (BGR) a ghost's cyan
    limit = cfg.GHOST_BOSS_MIN
    big_side, small_side = int((limit * 0.8) ** 0.5), int(1200 ** 0.5)
    screen[400:400 + big_side, 300:300 + big_side] = ghost                         # bigger than the other, smaller than a boss, far from the character
    screen[600:600 + small_side, 1100:1100 + small_side] = ghost                   # a small one near the character
    world.screens = [screen]
    macro.play_ghost_gallery(world.clock.now + 1, watch_started(macro))
    first = next(m[1:] for m in world.mouse if m[0] == "move")
    assert abs(first[0] - 1100 - small_side / 2) < 40 and abs(first[1] - 600 - small_side / 2) < 40       # the near small one: nothing is the boss


def test_a_ghost_seen_from_afar_is_found_and_the_slime_puddle_beside_it_is_not(macro):
    blobs = macro.find_ghost_blobs(shot("far_91"))                                  # a blue oval ghost (hue 91, saturation 216) at the right, a slime puddle (hue 81, saturation 251) in the middle
    assert any(abs(x - 1783) < 25 and abs(y - 629) < 25 for x, y, _ in blobs)          # (it was missed: the colour range stopped at saturation 210)
    assert not any(abs(x - 1344) < 120 and abs(y - 778) < 120 for x, y, _ in blobs)


# --- score feedback: the beam is on a ghost when the score goes up ---

def with_banner(screen, name):
    """`screen` with the banner of tests/ghost_gallery/ (rows 20-110, columns 700-1260 of a real frame) where it was taken from."""
    screen = screen.copy()
    crop = shot(name)
    screen[20:20 + crop.shape[0], 700:700 + crop.shape[1]] = crop
    return screen


def distance(a, b):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** .5


def test_a_changed_score_is_seen_in_the_banner_and_nothing_is_concluded_without_one(macro):
    feedback = macro.GhostFeedback(0.0)
    assert not feedback.look(with_banner(blank(), "banner_450"), 1.0) and feedback.visible    # (the first look has nothing to compare with)
    assert not feedback.look(with_banner(blank(), "banner_450_later"), 2.0)                   # the same score, the time beside it is another
    assert feedback.look(with_banner(blank(), "banner_1450"), 3.0) and feedback.scored_at == 3.0
    for no_banner in (blank(), np.full((1080, 1920, 3), 255, np.uint8)):                      # nothing, or the white of a loading screen
        assert not feedback.look(no_banner, 4.0) and not feedback.visible
        assert not feedback.stalled(10 ** 6) and not feedback.trial_over(10 ** 6)             # however long it has been


def test_a_spot_has_a_trial_a_stall_is_a_long_time_without_a_score_and_bad_places_are_forgotten(macro):
    cfg = macro.cfg
    feedback = macro.GhostFeedback(100.0)
    feedback.look(with_banner(blank(), "banner_450"), 100.0)
    assert not feedback.trial_over(100.0 + cfg.GHOST_SPOT_TRIAL - 0.1) and feedback.trial_over(100.0 + cfg.GHOST_SPOT_TRIAL)
    feedback.new_spot(110.0)
    assert not feedback.trial_over(110.0 + cfg.GHOST_SPOT_TRIAL - 0.1) and feedback.trial_over(110.0 + cfg.GHOST_SPOT_TRIAL)       # a new spot has a trial of its own
    assert not feedback.stalled(100.0 + cfg.GHOST_STALL - 0.1) and feedback.stalled(100.0 + cfg.GHOST_STALL)                       # but the stall counts from the last score
    feedback.mark_bad((5, 5), 100.0)
    assert feedback.avoid(100.0 + cfg.GHOST_BAD_SPOT_TIME - 1) == [(5, 5)] and feedback.avoid(100.0 + cfg.GHOST_BAD_SPOT_TIME + 1) == []


def test_furniture_is_looked_for_around_a_hiding_ghost_and_away_from_places_that_scored_nothing(macro, world):
    cfg = macro.cfg
    centre, bad = (900, 500), (1000, 500)
    world.screens = [blank()]                                                                  # no outline anywhere: every probe is made
    macro.find_furniture(watch_started(macro), around=centre, avoid=[bad])
    probes = [m[1:] for m in world.mouse if m[0] == "move"]
    near, far = cfg.GHOST_RING
    assert len(probes) == cfg.GHOST_FURNITURE_PROBES
    assert all(near - 1 <= distance(p, centre) <= far + 1 for p in probes)
    assert all(distance(p, bad) >= cfg.GHOST_BAD_SPOT_RADIUS for p in probes)


def ghost_with_banner(macro, name="banner_450"):
    ghost = shot("ghosts_50")
    return with_banner(ghost, name), macro.find_ghost_blobs(ghost)[0][:2]


@pytest.fixture
def short_trial(macro, monkeypatch):
    monkeypatch.setattr(macro.cfg, "GHOST_SPOT_TRIAL", 1.0)                                   # (less fake time to live through)


def test_a_ghost_the_beam_scores_nothing_on_is_taken_to_hide_and_the_furniture_around_it_is_tried(macro, world, short_trial):
    cfg = macro.cfg
    screen, ghost = ghost_with_banner(macro)
    world.screens = [screen]                                                                   # the score never changes
    macro.play_ghost_gallery(world.clock.now + cfg.GHOST_SPOT_TRIAL * 2, watch_started(macro))
    places = left_held_on(world)
    near, far = cfg.GHOST_RING
    assert places[0] == ghost                                                                  # first the ghost itself, for the trial...
    around = [p for p in places if p != ghost]
    assert around and all(near - 1 <= distance(p, ghost) <= far + 1 for p in around)           # ...then the furniture around it


def test_the_beam_goes_back_to_the_ghost_when_the_score_goes_up(macro, world, short_trial):
    cfg = macro.cfg
    still, ghost = ghost_with_banner(macro)
    scored, _ = ghost_with_banner(macro, "banner_1450")
    on_furniture = lambda: any(p != ghost for p in left_held_on(world))
    world.screens = lambda: (scored if world.looks % 2 else still) if on_furniture() else still     # the score goes up from the moment the beam is on the furniture
    macro.play_ghost_gallery(world.clock.now + cfg.GHOST_SPOT_TRIAL * 4, watch_started(macro))
    places = left_held_on(world)
    last_furniture = max(i for i, p in enumerate(places) if p != ghost)
    assert places[-1] == ghost and places.count(places[last_furniture]) <= 3                  # back at once, not after the furniture's time was up


def test_each_try_for_a_hiding_ghost_keeps_away_from_the_furniture_that_scored_nothing(macro, world, short_trial, monkeypatch):
    cfg = macro.cfg
    screen, ghost = ghost_with_banner(macro)
    world.screens = [screen]
    tried = iter([(300, 300), (1500, 700), (400, 800), (1600, 200)])
    calls = []

    def find(watch, around=None, avoid=()):
        calls.append((around, list(avoid)))
        return next(tried)
    monkeypatch.setattr(macro, "find_furniture", find)
    macro.play_ghost_gallery(world.clock.now + cfg.GHOST_SPOT_TRIAL * 3.5, watch_started(macro))
    assert len(calls) >= 3 and all(around == ghost for around, _ in calls)                    # looked for around the ghost, each time
    assert calls[0][1] == [] and calls[1][1] == [(300, 300)] and calls[2][1] == [(300, 300), (1500, 700)]    # and not where it was tried before


def test_a_new_ghost_gets_a_trial_of_its_own(macro, world, short_trial):
    cfg = macro.cfg
    first = shot("ghosts_50")
    second = np.roll(first, 400, axis=1)                                                      # the same ghost somewhere else: another one
    a = macro.choose_ghost_target(macro.find_ghost_blobs(first), False, None)
    b = macro.choose_ghost_target(macro.find_ghost_blobs(second), False, None)
    assert distance(a, b) > cfg.GHOST_STICK                                                   # (another ghost, not the one followed)
    start = world.clock.now
    world.screens = lambda: with_banner(first if world.clock.now - start < cfg.GHOST_SPOT_TRIAL * 0.8 else second, "banner_450")
    macro.play_ghost_gallery(start + cfg.GHOST_SPOT_TRIAL * 1.3, watch_started(macro))        # the first one has had more than its trial by the end, the second less
    assert set(left_held_on(world)) <= {a, b}


@pytest.fixture
def quiet_player(macro, monkeypatch):
    """A player that does nothing of its own with no ghost in sight: no camera turns, walking about or jumping."""
    for name, value in (("GHOST_CAMERA_PERIOD", 10 ** 6), ("GHOST_MOVE_PERIOD", 10 ** 6), ("GHOST_JUMP_MIN", 10 ** 6), ("GHOST_JUMP_MAX", 10 ** 6), ("GHOST_STALL", 2.0)):
        monkeypatch.setattr(macro.cfg, name, value)


def test_no_score_for_a_long_time_is_a_stall_the_player_walks_away_jumps_and_turns_the_camera(macro, world, quiet_player):
    cfg = macro.cfg
    world.screens = [with_banner(blank(), "banner_450")]
    start = world.clock.now
    macro.play_ghost_gallery(start + cfg.GHOST_STALL * 4, watch_started(macro))
    walks = [(key, at) for kind, key, at in world.keys if kind == "down" and key in cfg.MOVE_KEYS][1:]        # (the first is the start's walk)
    assert walks and walks[0][1] - start >= cfg.GHOST_STALL
    ups = [at for kind, key, at in world.keys if kind == "up" and key == walks[0][0]]
    assert ups[0] - walks[0][1] == pytest.approx(cfg.GHOST_STALL_WALK)
    assert [k for k in world.keys if k[0] == "press" and k[1] == cfg.KEY_JUMP] != []
    drags = [i for i, e in enumerate(world.mouse) if e == ("down", "right")]
    assert len(drags) == len(walks)
    turned = sum(abs(e[1]) for e in world.mouse[drags[0]:drags[0] + 1 + cfg.GHOST_CAMERA_DRAG_STEPS] if e[0] == "rel" and e[1])
    assert turned >= cfg.GHOST_STALL_TURN[0] - cfg.GHOST_CAMERA_DRAG_STEPS                    # a big turn
    assert len(walks) >= 2 and walks[1][1] - walks[0][1] >= cfg.GHOST_STALL                   # the next stall is a stall period after the last, not at once


@pytest.mark.parametrize("banners", [("banner_450", "banner_1450"), (None,)])
def test_no_stall_while_the_score_goes_up_or_when_the_banner_cannot_be_seen(macro, world, quiet_player, banners):
    cfg = macro.cfg
    screens = [with_banner(blank(), name) if name else blank() for name in banners]
    world.screens = lambda: screens[world.looks % len(screens)]
    macro.play_ghost_gallery(world.clock.now + cfg.GHOST_STALL * 1.5, watch_started(macro))
    assert [k for k in world.keys if k[0] == "down" and k[1] in cfg.MOVE_KEYS][1:] == [] and ("down", "right") not in world.mouse
