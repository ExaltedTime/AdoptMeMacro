"""Playing Hauntlet 2: finding the doors, the timer, choosing and walking to a door, and the loop that does it room
after room. Frames are built from the fixtures of tests/hauntlet_2/ (crops of a real run pasted on a blank screen);
time and the keyboard are faked."""
import os
import types

import cv2
import numpy as np
import pytest

HERE = os.path.join(os.path.dirname(__file__), "hauntlet_2")
PANEL_AT = (1060, 60)


def frame(macro, doors=None, room=None, timer=True):
    """A screen: the doors fixture (a number, or None for a blank one), with the timer and the room panel on it."""
    screen = np.zeros((1080, 1920, 3), np.uint8) if doors is None else cv2.imread(os.path.join(HERE, "doors", f"{doors:03d}.png"))
    if timer:
        left, top, right, bottom = macro.cfg.HAUNTLET_TIMER_BOX
        screen[top:bottom, left:right] = cv2.imread(os.path.join(HERE, "timer", "shown.png"))
    if room is not None:
        crop = cv2.imread(os.path.join(HERE, "room_panels", f"{room:02d}.png"))
        screen[PANEL_AT[1]:PANEL_AT[1] + crop.shape[0], PANEL_AT[0]:PANEL_AT[0] + crop.shape[1]] = crop
    return screen


# --- the timer ---

def test_the_timer_is_there_or_hidden(macro):
    left, top, right, bottom = macro.cfg.HAUNTLET_TIMER_BOX
    for name, shown in (("shown", True), ("hidden", False), ("hidden_passing", False)):
        screen = np.zeros((1080, 1920, 3), np.uint8)
        screen[top:bottom, left:right] = cv2.imread(os.path.join(HERE, "timer", f"{name}.png"))
        assert macro.hauntlet_timer_visible(screen) is shown, name
    assert macro.hauntlet_timer_visible(np.zeros((1080, 1920, 3), np.uint8)) is False


# --- the doors ---

# what is on each fixture's three doors, left to right (from the pictures)
DOORS = {
    16: ["plain", "plain", "plain"],                  # the first layer, yellow walls
    25: ["boarded", "boarded", "plain"],
    46: ["locked", "plain", "dark"],
    88: ["mystery", "dark", "dark"],                  # the second layer, white walls
    97: ["boarded", "boarded", "locked"],
    209: ["locked", "locked", "plain"],               # the third layer, blue walls
}


@pytest.mark.parametrize("number", sorted(DOORS))
def test_the_three_doors_of_a_room_are_found_with_their_kinds(macro, number):
    doors = macro.find_hauntlet_doors(frame(macro, number))
    assert [door.kind for door in doors] == DOORS[number]
    assert doors[0].x < doors[1].x < doors[2].x
    assert len({door.y for door in doors}) == 1


def test_a_forks_hall_and_a_blank_screen_have_no_doors(macro):
    assert macro.find_hauntlet_doors(frame(macro, 82)) == []
    assert macro.find_hauntlet_doors(frame(macro)) == []


def test_two_tvs_are_not_a_room(macro):
    screen = frame(macro, 97)
    first = macro.find_door_tvs(screen)[0]
    screen[first[1] - 30:first[1] + 20, first[0] - 30:first[0] + 30] = 0        # the left TV's number gone
    assert macro.find_door_tvs(screen) == []


# --- choosing and walking ---

def door(kind=None):
    return types.SimpleNamespace(x=0, y=0, kind=kind)


def test_a_door_is_chosen_at_random_but_a_locked_one_only_with_a_key(macro):
    doors = [door("locked"), door(), door("locked")]
    assert {macro.choose_hauntlet_door(doors, None, []) for _ in range(60)} == {1}
    key = macro.HauntletItem("gold_key", 1, (0, 0))
    assert {macro.choose_hauntlet_door(doors, None, [key]) for _ in range(200)} == {0, 1, 2}
    assert {macro.choose_hauntlet_door([door("locked")] * 3, None, []) for _ in range(200)} == {0, 1, 2}   # nothing else to take


def test_an_item_is_used_with_the_set_chance_and_any_item_can_be_the_one(macro, monkeypatch):
    items = [macro.HauntletItem(name, 1, (100 * i, 1000)) for i, name in enumerate(["phone", "key", "cyan_potion"])]
    monkeypatch.setattr(macro.cfg, "HAUNTLET_ITEM_CHANCE", 1.0)
    assert {macro.choose_hauntlet_item(items, None).name for _ in range(200)} == {"phone", "key", "cyan_potion"}
    assert macro.choose_hauntlet_item([], None) is None
    monkeypatch.setattr(macro.cfg, "HAUNTLET_ITEM_CHANCE", 0.0)
    assert macro.choose_hauntlet_item(items, None) is None
    monkeypatch.setattr(macro.cfg, "HAUNTLET_ITEM_CHANCE", 0.5)
    used = sum(macro.choose_hauntlet_item(items, None) is not None for _ in range(2000))
    assert 800 < used < 1200


def hearts_at(macro, monkeypatch, x):
    """Make the player's hearts read as centred on x."""
    hearts = macro.HauntletHearts(3, 1, 30, (0, 700))
    hearts.pos = (int(x - hearts.center_x + hearts.pos[0]), 700)
    monkeypatch.setattr(macro, "read_hauntlet_hearts", lambda img: hearts)


def test_the_strafe_is_the_distance_to_the_door_in_door_spacings(macro, monkeypatch):
    cfg = macro.cfg
    doors = macro.find_hauntlet_doors(frame(macro, 97))
    spacing = (doors[2].x - doors[0].x) / 2
    screen = frame(macro, 97)
    hearts_at(macro, monkeypatch, doors[1].x)                                        # in front of the middle door
    assert macro.hauntlet_strafe(screen, doors, 1) == (None, 0.0)
    key, seconds = macro.hauntlet_strafe(screen, doors, 0)
    assert key == cfg.HAUNTLET_LEFT_KEY and seconds == pytest.approx((doors[1].x - doors[0].x) / spacing * cfg.HAUNTLET_STRAFE_DURATION, rel=0.01)
    key, seconds = macro.hauntlet_strafe(screen, doors, 2)
    assert key == cfg.HAUNTLET_RIGHT_KEY and seconds == pytest.approx((doors[2].x - doors[1].x) / spacing * cfg.HAUNTLET_STRAFE_DURATION, rel=0.01)


def test_a_player_who_starts_elsewhere_strafes_the_distance_from_there(macro, monkeypatch):
    """The spot a room is entered at can depend on the door it came through: the strafe follows where the player is."""
    cfg = macro.cfg
    screen = frame(macro, 97)
    doors = macro.find_hauntlet_doors(screen)
    hearts_at(macro, monkeypatch, doors[0].x)                                        # came in at the left door
    assert macro.hauntlet_strafe(screen, doors, 0) == (None, 0.0)
    key_far, far = macro.hauntlet_strafe(screen, doors, 2)
    hearts_at(macro, monkeypatch, doors[1].x)
    _, from_the_middle = macro.hauntlet_strafe(screen, doors, 2)
    assert key_far == cfg.HAUNTLET_RIGHT_KEY and far == pytest.approx(2 * from_the_middle, rel=0.15)
    hearts_at(macro, monkeypatch, doors[2].x)                                        # came in at the right: the middle is to its left
    assert macro.hauntlet_strafe(screen, doors, 1)[0] == cfg.HAUNTLET_LEFT_KEY


def test_the_strafe_is_capped_and_a_player_not_seen_is_taken_to_be_in_the_middle(macro, monkeypatch):
    cfg = macro.cfg
    screen = frame(macro, 97)
    doors = macro.find_hauntlet_doors(screen)
    hearts_at(macro, monkeypatch, doors[0].x - 5 * (doors[1].x - doors[0].x))        # far off to the left
    assert macro.hauntlet_strafe(screen, doors, 2) == (cfg.HAUNTLET_RIGHT_KEY, cfg.HAUNTLET_STRAFE_MAX * cfg.HAUNTLET_STRAFE_DURATION)
    monkeypatch.setattr(macro, "read_hauntlet_hearts", lambda img: None)
    assert macro.hauntlet_strafe(screen, doors, 1) == (None, 0.0)
    assert macro.hauntlet_strafe(screen, doors, 0)[0] == cfg.HAUNTLET_LEFT_KEY


def test_doors_without_positions_are_strafed_to_without_dividing_by_their_spacing(macro):
    """The doors HauntletRun.look() makes up have no positions (all at 0): a run crashed on three of them."""
    cfg = macro.cfg
    assert macro.hauntlet_strafe(frame(macro), [macro.HauntletDoor(0, 0, None)], 0) == (None, 0.0)           # a single doorway ahead
    unlocated = [macro.HauntletDoor(0, 0, None) for _ in range(3)]                                          # (not made up any more, but still no crash)
    assert [macro.hauntlet_strafe(frame(macro), unlocated, i) for i in range(3)] == [
        (cfg.HAUNTLET_LEFT_KEY, cfg.HAUNTLET_STRAFE_DURATION), (None, 0.0), (cfg.HAUNTLET_RIGHT_KEY, cfg.HAUNTLET_STRAFE_DURATION)]


def test_a_forks_doorways_are_the_left_and_the_right_for_one_door_spacing(macro):
    cfg = macro.cfg
    doorways = [macro.HauntletDoor(0, 0, None)] * 2
    assert macro.hauntlet_strafe(frame(macro), doorways, 0) == (cfg.HAUNTLET_LEFT_KEY, cfg.HAUNTLET_STRAFE_DURATION)
    assert macro.hauntlet_strafe(frame(macro), doorways, 1) == (cfg.HAUNTLET_RIGHT_KEY, cfg.HAUNTLET_STRAFE_DURATION)


class Clock:
    """A time that only moves when the macro waits."""
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def world(macro, monkeypatch):
    """Fake time and keyboard: `world.keys` lists ("down"/"up", key, time), `world.screens` is what the next looks see (a list,
    the last one staying, or a function of the world), `world.waits` the waits."""
    clock = Clock()
    world = types.SimpleNamespace(clock=clock, keys=[], waits=[], screens=[], looks=0)
    monkeypatch.setattr(macro, "time", types.SimpleNamespace(**{**vars(macro.time), "time": clock}))

    def wait(duration):
        world.waits.append(duration)
        clock.now += duration

    def look(watch):
        world.looks += 1
        if callable(world.screens):
            return world.screens()
        return world.screens.pop(0) if len(world.screens) > 1 else world.screens[0]

    monkeypatch.setattr(macro, "wait_interruptible", wait)
    monkeypatch.setattr(macro, "minigame_look", look)
    monkeypatch.setattr(macro, "minigame_wait", lambda duration, watch: clock.__setattr__("now", clock.now + duration))
    monkeypatch.setattr(macro, "detect_minigame_victory", lambda img: False)
    monkeypatch.setattr(macro.pydirectinput, "keyDown", lambda key: world.keys.append(("down", key, clock.now)), raising=False)
    monkeypatch.setattr(macro.pydirectinput, "keyUp", lambda key: world.keys.append(("up", key, clock.now)), raising=False)
    return world


def room_to_look_around(macro, monkeypatch):
    """A walk time long enough for several looks after the strafe, whatever the timings are tuned to."""
    cfg = macro.cfg
    monkeypatch.setattr(cfg, "HAUNTLET_WALK_MAX", cfg.HAUNTLET_STRAFE_DURATION + 10 * cfg.HAUNTLET_LOOK_INTERVAL)


def watch_of(macro):
    return types.SimpleNamespace(problem=None, start_wait_over=lambda: None)


def test_a_walk_holds_forward_and_the_strafe_key_for_the_strafe_then_stops_when_the_room_changes(macro, world, monkeypatch):
    cfg = macro.cfg
    room_to_look_around(macro, monkeypatch)
    run = macro.HauntletRun()
    run.room = 5
    world.screens = [frame(macro, room=5)] * 3 + [frame(macro, room=6)]
    start = world.clock.now
    assert macro.hauntlet_walk(cfg.HAUNTLET_LEFT_KEY, cfg.HAUNTLET_STRAFE_DURATION, run, watch_of(macro)) is False
    assert [(kind, key) for kind, key, _ in world.keys] == [("down", "w"), ("down", cfg.HAUNTLET_LEFT_KEY), ("up", cfg.HAUNTLET_LEFT_KEY), ("up", "w")]
    assert world.keys[2][2] - start == pytest.approx(cfg.HAUNTLET_STRAFE_DURATION)
    assert world.looks == 4 and world.keys[3][2] - start < cfg.HAUNTLET_WALK_MAX     # it stopped for the new room, not the time
    assert run.chosen_at == start


def test_a_walk_to_the_middle_door_strafes_not_at_all_and_gives_up_after_the_walk_time(macro, world, monkeypatch):
    room_to_look_around(macro, monkeypatch)
    run = macro.HauntletRun()
    run.room = 5
    world.screens = [frame(macro, room=5)]                                       # the room never changes
    start = world.clock.now
    assert macro.hauntlet_walk(None, 0.0, run, watch_of(macro)) is False
    assert [key for _, key, _ in world.keys] == ["w", "w"]
    assert world.keys[1][2] - start >= macro.cfg.HAUNTLET_WALK_MAX


def test_a_walk_lets_go_of_its_keys_when_stopped(macro, world, monkeypatch):
    def stop(duration):
        raise macro.StopRequested()
    monkeypatch.setattr(macro, "wait_interruptible", stop)
    run = macro.HauntletRun()
    with pytest.raises(macro.StopRequested):
        macro.hauntlet_walk(macro.cfg.HAUNTLET_RIGHT_KEY, macro.cfg.HAUNTLET_STRAFE_DURATION, run, watch_of(macro))
    assert [(kind, key) for kind, key, _ in world.keys] == [("down", "w"), ("down", macro.cfg.HAUNTLET_RIGHT_KEY),
                                                              ("up", macro.cfg.HAUNTLET_RIGHT_KEY), ("up", "w")]


def test_a_walk_reports_the_victory_screen(macro, world, monkeypatch):
    monkeypatch.setattr(macro, "detect_minigame_victory", lambda img: True)
    world.screens = [frame(macro, room=5)]
    run = macro.HauntletRun()
    assert macro.hauntlet_walk(None, 0.0, run, watch_of(macro)) is True
    assert [kind for kind, _, _ in world.keys][-1] == "up"


# --- the room being looked at ---

def test_the_doors_are_chosen_from_once_the_room_has_settled_and_the_camera_stopped(macro):
    cfg = macro.cfg
    run = macro.HauntletRun()
    screen = frame(macro, 97, room=12)
    assert run.look(screen, 100.0) is None                                       # the number just changed
    assert run.look(screen, 100.0 + cfg.HAUNTLET_ROOM_SETTLE / 2) is None       # still settling
    doors = run.look(screen, 100.0 + cfg.HAUNTLET_ROOM_SETTLE + 0.1)
    assert doors is not None and [d.kind for d in doors] == ["boarded", "boarded", "locked"]


def test_doors_still_moving_wait_for_the_timeout(macro):
    cfg = macro.cfg
    run = macro.HauntletRun()
    still = frame(macro, 97, room=12)
    moved = np.roll(still, 40, axis=1)                                           # the camera is moving: every TV shifts
    run.look(still, 100.0)
    step = (cfg.HAUNTLET_DOORS_TIMEOUT - cfg.HAUNTLET_ROOM_SETTLE) / 10
    t = 100.0 + cfg.HAUNTLET_ROOM_SETTLE + step
    for i in range(4):
        assert run.look(moved if i % 2 == 0 else still, t) is None
        t += step
    assert run.look(still, 100.0 + cfg.HAUNTLET_DOORS_TIMEOUT + step) is not None


def test_nothing_is_chosen_while_the_timer_is_hidden(macro):
    run = macro.HauntletRun()
    screen = frame(macro, 97, room=12, timer=False)
    run.look(screen, 100.0)
    for t in (102.0, 103.0, 120.0):
        assert run.look(screen, t) is None


def test_a_room_without_a_number_is_not_looked_at(macro):
    run = macro.HauntletRun()
    for t in (100.0, 101.0, 130.0):
        assert run.look(frame(macro, 97), t) is None


def test_after_a_choice_the_room_is_left_alone_until_the_timeout_then_chosen_from_again(macro):
    cfg = macro.cfg
    run = macro.HauntletRun()
    screen = frame(macro, 97, room=12)
    chosen = 100.0 + cfg.HAUNTLET_ROOM_SETTLE + 1.0
    run.look(screen, 100.0)
    run.look(screen, chosen - 0.1)
    assert run.look(screen, chosen) is not None
    run.chosen_at = chosen
    assert run.look(screen, chosen + 0.1) is None
    assert run.look(screen, chosen + cfg.HAUNTLET_ROOM_TIMEOUT - 0.1) is None
    assert run.look(screen, chosen + cfg.HAUNTLET_ROOM_TIMEOUT + 0.1) is not None


def test_a_new_room_number_starts_the_room_afresh(macro):
    settled = 100.0 + macro.cfg.HAUNTLET_ROOM_SETTLE + 1.0
    run = macro.HauntletRun()
    run.look(frame(macro, 97, room=12), 100.0)
    run.look(frame(macro, 97, room=12), settled)
    run.chosen_at = settled
    assert run.look(frame(macro, 97, room=13), settled + 2.0) is None            # the number changed: settling again
    assert (run.room, run.rooms, run.chosen_at) == (13, 2, None)


def fork_room(macro):
    return macro.cfg.HAUNTLET_FORK_EVERY


def test_a_fork_is_two_doorways_when_its_hall_has_no_tvs(macro):
    cfg = macro.cfg
    run = macro.HauntletRun()
    hall = frame(macro, 82, room=fork_room(macro))
    assert run.look(hall, 100.0) is None
    assert run.fork
    assert run.look(hall, 100.0 + cfg.HAUNTLET_ROOM_SETTLE + 0.1) is None        # once is not enough to be sure
    doors = run.look(hall, 100.0 + cfg.HAUNTLET_ROOM_SETTLE + 0.5)
    assert doors is not None and len(doors) == 2


def not_a_fork_or_the_hall(macro):
    return next(n for n in range(2, 30) if n % macro.cfg.HAUNTLET_FORK_EVERY)


def test_a_room_without_tvs_that_is_no_fork_is_one_doorway_ahead_after_the_timeout(macro):
    cfg = macro.cfg
    run = macro.HauntletRun()
    nothing = frame(macro, 82, room=not_a_fork_or_the_hall(macro))
    run.look(nothing, 100.0)
    for part in (0.3, 0.5, 0.9):
        assert run.look(nothing, 100.0 + part * cfg.HAUNTLET_DOORS_TIMEOUT) is None
    doors = run.look(nothing, 100.0 + cfg.HAUNTLET_DOORS_TIMEOUT + 0.1)
    assert doors is not None and len(doors) == 1 and not run.fork


def test_room_1_is_the_entrance_hall_a_single_doorway_found_as_soon_as_it_has_settled(macro):
    cfg = macro.cfg
    run = macro.HauntletRun()
    hall = frame(macro, 82, room=1)
    assert run.look(hall, 100.0) is None
    assert run.look(hall, 100.0 + cfg.HAUNTLET_ROOM_SETTLE + 0.1) is None             # one look without TVs is not enough
    doors = run.look(hall, 100.0 + cfg.HAUNTLET_ROOM_SETTLE + 0.5)
    assert doors is not None and len(doors) == 1 and 100.0 + cfg.HAUNTLET_ROOM_SETTLE + 0.5 < 100.0 + cfg.HAUNTLET_DOORS_TIMEOUT


# --- the whole loop ---

def test_the_loop_chooses_a_door_walks_to_it_and_goes_on_in_the_next_room(macro, world, monkeypatch, capsys):
    cfg = macro.cfg
    monkeypatch.setattr(macro.random, "choice", lambda options: options[-1])    # always the right-most allowed door
    monkeypatch.setattr(macro, "log_run_event", lambda text: events.append(text))
    events = []
    room_12, room_13 = frame(macro, 97, room=12), frame(macro, 97, room=13)
    # room 12 (a locked door on the right, which isn't taken without a key) until the first walk is under way, then 13
    world.screens = lambda: room_13 if world.keys and world.clock.now > world.keys[0][2] + 1 else room_12
    start = world.clock.now
    macro.play_hauntlet_2(start + cfg.MINIGAME_START_WAIT + 10 * (cfg.HAUNTLET_ROOM_SETTLE + cfg.HAUNTLET_ROOM_TIMEOUT + cfg.HAUNTLET_WALK_MAX), watch_of(macro))
    pressed = [(kind, key) for kind, key, _ in world.keys]
    assert pressed[:2] == [("down", "w"), ("up", "w")]                           # the middle door: forward only
    assert pressed.count(("down", "w")) == pressed.count(("up", "w"))
    out = capsys.readouterr().out
    assert "room 12, hearts not seen, items: none, doors: boarded, boarded, locked, going middle" in out
    assert "Hauntlet 2: room 13" in out
    assert events == ["Hauntlet 2: got as far as room 13"]


def test_the_loop_returns_true_on_the_victory_screen(macro, world, monkeypatch):
    monkeypatch.setattr(macro, "detect_minigame_victory", lambda img: True)
    world.screens = [frame(macro, room=5)]
    assert macro.play_hauntlet_2(world.clock.now + macro.cfg.MINIGAME_START_WAIT + 1000, watch_of(macro)) is True
    assert world.keys == []


def test_the_loop_stops_when_the_overlay_says_the_minigame_is_over(macro, world):
    watch = types.SimpleNamespace(problem="left the minigame", start_wait_over=lambda: None)
    world.screens = [frame(macro, 97, room=12)]
    assert macro.play_hauntlet_2(world.clock.now + 1000, watch) is False
    assert world.keys == [] and world.looks == 0


def test_the_loop_clicks_the_item_it_chose_before_walking(macro, world, monkeypatch, capsys):
    cfg = macro.cfg
    item = macro.HauntletItem("phone", 1, (850, 1000))
    monkeypatch.setattr(cfg, "HAUNTLET_ITEM_CHANCE", 1.0)
    monkeypatch.setattr(macro, "read_hauntlet_items", lambda img, learn=False: [item])
    monkeypatch.setattr(macro, "log_run_event", lambda text: None)
    world.screens = [frame(macro, 97, room=12)]
    macro.play_hauntlet_2(world.clock.now + cfg.MINIGAME_START_WAIT + 3 * (cfg.HAUNTLET_ROOM_SETTLE + cfg.HAUNTLET_ROOM_TIMEOUT + cfg.HAUNTLET_WALK_MAX), watch_of(macro))
    assert macro.clicks and set(macro.clicks) == {(850, 1000)}
    assert "using phone" in capsys.readouterr().out
    assert cfg.HAUNTLET_ITEM_WAIT in world.waits


def test_the_loop_uses_no_item_when_there_is_none(macro, world, monkeypatch, capsys):
    cfg = macro.cfg
    monkeypatch.setattr(cfg, "HAUNTLET_ITEM_CHANCE", 1.0)
    monkeypatch.setattr(macro, "log_run_event", lambda text: None)
    world.screens = [frame(macro, 97, room=12)]
    macro.play_hauntlet_2(world.clock.now + cfg.MINIGAME_START_WAIT + 2 * (cfg.HAUNTLET_ROOM_SETTLE + cfg.HAUNTLET_WALK_MAX), watch_of(macro))
    assert macro.clicks == [] and "using" not in capsys.readouterr().out


def test_a_room_whose_doors_are_never_found_is_walked_straight_on_and_the_screenshot_kept_once(macro, world, monkeypatch):
    cfg = macro.cfg
    number = not_a_fork_or_the_hall(macro)
    failures = []
    monkeypatch.setattr(macro, "log_failure", lambda reason, screenshot=True: failures.append(reason))
    monkeypatch.setattr(macro, "log_run_event", lambda text: None)
    world.screens = [frame(macro, room=number)]                                # the timer and the room, but no TVs at all
    macro.play_hauntlet_2(world.clock.now + cfg.MINIGAME_START_WAIT + 3 * (cfg.HAUNTLET_DOORS_TIMEOUT + cfg.HAUNTLET_ROOM_TIMEOUT + cfg.HAUNTLET_WALK_MAX), watch_of(macro))
    keys = [key for _, key, _ in world.keys]
    assert keys.count("w") >= 4 and set(keys) == {"w"}                         # walked more than once, forward only
    assert failures == [f"Hauntlet 2: no doors found in room {number}"]        # reported once, not every try


def test_the_entrance_hall_is_walked_through_until_the_doors_show_without_a_failure(macro, world, monkeypatch, capsys):
    """Room 1 of a real run: a hall with one doorway, then the room with three doors, both numbered 1."""
    cfg = macro.cfg
    failures = []
    monkeypatch.setattr(macro, "log_failure", lambda reason, screenshot=True: failures.append(reason))
    monkeypatch.setattr(macro, "log_run_event", lambda text: None)
    hall, room = frame(macro, 82, room=1), frame(macro, 97, room=1)
    # the hall until the walk has been under way a moment, then the room with its doors
    world.screens = lambda: room if world.keys and world.clock.now > world.keys[0][2] + 2 * cfg.HAUNTLET_LOOK_INTERVAL else hall
    start = world.clock.now
    macro.play_hauntlet_2(start + cfg.MINIGAME_START_WAIT + 3 * (cfg.HAUNTLET_ROOM_SETTLE + cfg.HAUNTLET_DOORS_TIMEOUT + cfg.HAUNTLET_WALK_MAX), watch_of(macro))
    out = capsys.readouterr().out
    assert "going ahead" in out and failures == []
    first_walk = [(kind, key, at) for kind, key, at in world.keys][:2]
    assert [(kind, key) for kind, key, _ in first_walk] == [("down", "w"), ("up", "w")]
    assert first_walk[1][2] - first_walk[0][2] < cfg.HAUNTLET_WALK_MAX          # it stopped at the doors, not after the full walk time
    assert "doors: boarded, boarded, locked" in out                              # and went on to choose among the three
    second_walk = [at for kind, key, at in world.keys if kind == "down" and key == "w"][1]
    assert second_walk - first_walk[1][2] < cfg.HAUNTLET_ROOM_TIMEOUT / 2        # at once, not after waiting out a walk that "failed"
