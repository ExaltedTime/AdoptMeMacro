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


BAR_AT = (640, 930)                        # where the item bar crops were cut from


def frame(macro, doors=None, room=None, timer=True, bar=None):
    """A screen: the doors fixture (a number, a name, or None for a blank one), with the timer, the room panel and the item bar
    (a number of tests/hauntlet_2/item_bars/) on it."""
    name = f"{doors:03d}" if isinstance(doors, int) else doors
    screen = np.zeros((1080, 1920, 3), np.uint8) if doors is None else cv2.imread(os.path.join(HERE, "doors", f"{name}.png"))
    if bar is not None:
        crop = cv2.imread(os.path.join(HERE, "item_bars", f"{bar:03d}.png"))
        screen[BAR_AT[1]:BAR_AT[1] + crop.shape[0], BAR_AT[0]:BAR_AT[0] + crop.shape[1]] = crop
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


def test_the_three_doors_of_a_room_are_found_with_their_kinds(macro):
    kinds = {}
    for number in sorted(DOORS):
        doors = macro.find_hauntlet_doors(frame(macro, number))
        assert len(doors) == 3 and doors[0].x < doors[1].x < doors[2].x and len({door.y for door in doors}) == 1
        kinds[number] = [door.kind for door in doors]
    assert kinds == DOORS                                                                    # (a mismatch names its fixture)


def test_a_forks_hall_and_a_blank_screen_have_no_doors(macro):
    assert macro.find_hauntlet_doors(frame(macro, 82)) == []
    assert macro.find_hauntlet_doors(frame(macro)) == []


def blob(screen, x, y):
    """A TV number's light (an 18 px square of the glow colour) centred at (x, y)."""
    screen[y - 9:y + 9, x - 9:x + 9] = (255, 255, 206)


def test_a_thin_one_on_a_tv_does_not_hide_the_row(macro):
    """Room 8 of a macro-played run: the player stands before the left door, whose TV shows a "1" - a stroke of about 115 px
    where the "0"s of the other two are over 300 - and the row was not found for 25 s."""
    screen = cv2.imread(os.path.join(HERE, "doors", "thin_one.png"))
    tvs = macro.find_door_tvs(screen)
    assert [x for x, _ in tvs] == pytest.approx([862, 1315, 1786], abs=3)


def test_the_outer_two_tvs_give_the_middle_one_when_the_camera_is_close(macro):
    """Real frame of room 1: the middle TV is above the top of the screen, and the outer two are above y=150."""
    screen = cv2.imread(os.path.join(HERE, "doors", "room1_close.png"))
    tvs = macro.find_door_tvs(screen)
    assert len(tvs) == 3
    for (x, y), (want_x, want_y) in zip(tvs, [(501, 100), (960, 100), (1419, 100)]):
        assert abs(x - want_x) <= 3 and abs(y - want_y) <= 3
    assert len(macro.find_hauntlet_doors(screen)) == 3


def test_two_tvs_are_the_outer_ones_only_when_far_apart_and_centred(macro):
    screen = np.zeros((1080, 1920, 3), np.uint8)
    blob(screen, 570, 300), blob(screen, 1350, 300)                                   # 780 apart, centred
    assert [x for x, _ in macro.find_door_tvs(screen)] == pytest.approx([570, 960, 1350], abs=1)
    screen = np.zeros((1080, 1920, 3), np.uint8)
    blob(screen, 160, 300), blob(screen, 938, 300)                                    # as far apart, but two neighbours with the third off screen
    assert macro.find_door_tvs(screen) == []
    screen = np.zeros((1080, 1920, 3), np.uint8)
    blob(screen, 780, 300), blob(screen, 1140, 300)                                   # 360 apart: neighbours, not the outer two
    assert macro.find_door_tvs(screen) == []
    screen = np.zeros((1080, 1920, 3), np.uint8)
    blob(screen, 570, 300), blob(screen, 1350, 340)                                   # not level
    assert macro.find_door_tvs(screen) == []


def test_the_glow_of_the_room_panel_is_no_tv_but_a_tv_beside_it_high_up_is(macro):
    screen = np.zeros((1080, 1920, 3), np.uint8)
    blob(screen, 950, 100)                                                            # inside the room panel and timer
    assert macro.find_door_tvs(screen) == []
    blob(screen, 500, 100), blob(screen, 1420, 100)
    assert [x for x, _ in macro.find_door_tvs(screen)] == pytest.approx([500, 960, 1420], abs=1)      # (the one in the panel is not counted either way)


def test_the_players_hearts_under_a_door_are_not_a_lock_light(macro):
    cfg = macro.cfg
    screen = frame(macro, 16)                                                         # three plain doors
    x, y = macro.find_door_tvs(screen)[1]
    left, top, right, bottom = cfg.HAUNTLET_LOCK_LIGHT_BOX
    spot = (slice(y + int((top + 10) * 1.0), y + int((top + 10) * 1.0) + 12), slice(x - 6, x + 6))
    screen[spot] = (54, 68, 204)                                                      # a heart's dark red outline (BGR)
    assert macro.find_hauntlet_doors(screen)[1].kind == "plain"
    screen[spot] = (37, 37, 255)                                                      # the lock's light, pure red (BGR)
    assert macro.find_hauntlet_doors(screen)[1].kind == "locked"


def test_the_red_arrow_over_a_door_is_found_and_belongs_to_that_door(macro):
    screen = frame(macro, "phone_arrow")
    doors = macro.find_hauntlet_doors(screen)
    assert macro.find_ghost_door(screen, doors) == 1
    arrow = screen[100:260, 860:1050].copy()
    for index in (0, 2):                                                                      # the same arrow over the left and over the right door
        moved = frame(macro, "phone_arrow")
        moved[100:260, 860:1050] = frame(macro, "016")[100:260, 860:1050]
        x = doors[index].x
        moved[100:260, x - 95:x + 95] = arrow
        assert macro.find_ghost_door(moved, doors) == index
    far = frame(macro, "016")
    far[100:260, 120:310] = arrow                                                             # a red thing far to the left of every door
    assert macro.find_ghost_door(far, macro.find_hauntlet_doors(frame(macro, "phone_arrow"))) is None
    assert macro.find_ghost_door(frame(macro, 97), macro.find_hauntlet_doors(frame(macro, 97))) is None      # no arrow
    assert macro.find_ghost_door(screen, [macro.HauntletDoor(0, 0, None)]) is None                          # doors not located: can't tell


# --- choosing and walking ---

def door(kind=None):
    return types.SimpleNamespace(x=0, y=0, kind=kind)


def item(macro, name, x=0):
    return macro.HauntletItem(name, 1, (x, 1000))


def test_a_door_is_chosen_at_random_but_a_locked_one_only_with_the_grey_key_and_not_the_ghosts(macro):
    cfg = macro.cfg
    doors = [door("locked"), door(), door("locked")]
    assert {macro.choose_hauntlet_door(doors, None, []) for _ in range(60)} == {1}
    assert {macro.choose_hauntlet_door(doors, None, [item(macro, "gold_key")]) for _ in range(60)} == {1}       # the gold one is not it
    assert {macro.choose_hauntlet_door(doors, None, [item(macro, cfg.HAUNTLET_ITEM_KEY)]) for _ in range(200)} == {0, 1, 2}
    assert {macro.choose_hauntlet_door([door("locked")] * 3, None, []) for _ in range(200)} == {0, 1, 2}        # nothing else to take
    plain = [door(), door(), door()]
    assert {macro.choose_hauntlet_door(plain, None, [], avoid={1}) for _ in range(200)} == {0, 2}                  # the arrow's door is left alone
    assert {macro.choose_hauntlet_door([door("locked"), door(), door()], None, [], avoid={1}) for _ in range(200)} == {2}
    assert macro.choose_hauntlet_door(plain, None, [], avoid={0, 1}) == 2 and macro.choose_hauntlet_door(plain, None, [], avoid={0, 1, 2}) in (0, 1, 2)   # several to avoid, and nothing left
    assert macro.choose_hauntlet_door([door("locked")] * 2 + [door()], None, [], avoid={2}) in (0, 1)            # only the ghost's door open: not it, if possible


def test_each_door_and_the_hearts_call_for_their_items(macro):
    cfg = macro.cfg
    everything = [item(macro, name) for name in (cfg.HAUNTLET_ITEM_PHONE, cfg.HAUNTLET_ITEM_KEY, cfg.HAUNTLET_ITEM_BOARDED, cfg.HAUNTLET_ITEM_REFILL, cfg.HAUNTLET_ITEM_HEART, "gold_key")]
    full, low = macro.HauntletHearts(cfg.HAUNTLET_LOW_HEARTS + 1, 0, 30, (0, 0)), macro.HauntletHearts(cfg.HAUNTLET_LOW_HEARTS, 2, 30, (0, 0))
    assert macro.hauntlet_items_for(door("locked"), full, everything) == [cfg.HAUNTLET_ITEM_KEY, cfg.HAUNTLET_ITEM_HEART]
    assert macro.hauntlet_items_for(door("boarded"), full, everything) == [cfg.HAUNTLET_ITEM_BOARDED, cfg.HAUNTLET_ITEM_HEART]
    assert macro.hauntlet_items_for(door("plain"), full, everything) == [cfg.HAUNTLET_ITEM_HEART]
    assert macro.hauntlet_items_for(door("plain"), low, everything) == [cfg.HAUNTLET_ITEM_REFILL, cfg.HAUNTLET_ITEM_HEART]    # low on hearts: the red potion
    assert macro.hauntlet_items_for(door("plain"), None, everything) == [cfg.HAUNTLET_ITEM_HEART]                              # hearts not seen: not low
    assert macro.hauntlet_items_for(door("locked"), low, []) == []                                                             # only what is held
    assert macro.hauntlet_items_for(door("locked"), full, [item(macro, cfg.HAUNTLET_ITEM_BOARDED)]) == []                    # a cyan potion doesn't open a lock


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
    doorways = [macro.HauntletDoor(0, 0, None)] * 2                                                          # a fork's two: the left and the right, one spacing each
    assert [macro.hauntlet_strafe(frame(macro), doorways, i) for i in range(2)] == [
        (cfg.HAUNTLET_LEFT_KEY, cfg.HAUNTLET_STRAFE_DURATION), (cfg.HAUNTLET_RIGHT_KEY, cfg.HAUNTLET_STRAFE_DURATION)]


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
    assert world.looks == 4                                                          # it stopped for the new room, not the time...
    assert world.keys[3][2] - start == pytest.approx(cfg.HAUNTLET_STRAFE_DURATION + 3 * cfg.HAUNTLET_LOOK_INTERVAL + cfg.HAUNTLET_WALK_PAST_DURATION)   # ...and walked on past the doorway
    assert run.walked_at == world.keys[3][2] and run.stuck == 0                          # the walk's end is noted, and it got somewhere


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


def test_nothing_is_chosen_while_the_timer_is_hidden_or_the_room_is_unknown(macro):
    for screen in (frame(macro, 97, room=12, timer=False), frame(macro, 97)):                  # no timer / no room number
        run = macro.HauntletRun()
        run.look(screen, 100.0)
        for t in (102.0, 103.0, 120.0):
            assert run.look(screen, t) is None


def test_after_a_walk_the_room_is_left_alone_for_a_moment_then_chosen_from_again(macro):
    cfg = macro.cfg
    run = macro.HauntletRun()
    screen = frame(macro, 97, room=12)
    walked = 100.0 + cfg.HAUNTLET_ROOM_SETTLE + 1.0
    run.look(screen, 100.0)
    run.look(screen, walked - 0.1)
    assert run.look(screen, walked) is not None
    run.walked_at = walked
    assert run.look(screen, walked + cfg.HAUNTLET_RETRY_WAIT * 0.5) is None
    assert run.look(screen, walked + cfg.HAUNTLET_RETRY_WAIT + 0.1) is not None


def test_a_new_room_number_starts_the_room_afresh(macro):
    settled = 100.0 + macro.cfg.HAUNTLET_ROOM_SETTLE + 1.0
    run = macro.HauntletRun()
    run.look(frame(macro, 97, room=12), 100.0)
    run.look(frame(macro, 97, room=12), settled)
    run.walked_at, run.stuck, run.last_index, run.turns = settled, 2, 1, 3
    assert run.look(frame(macro, 97, room=13), settled + 2.0) is None            # the number changed: settling again
    assert (run.room, run.rooms, run.walked_at, run.stuck, run.last_index, run.turns) == (13, 2, None, 0, None, 0)


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
    macro.play_hauntlet_2(start + cfg.MINIGAME_START_WAIT + 10 * (cfg.HAUNTLET_ROOM_SETTLE + cfg.HAUNTLET_RETRY_WAIT + cfg.HAUNTLET_WALK_MAX), watch_of(macro))
    pressed = [(kind, key) for kind, key, _ in world.keys]
    assert pressed[:2] == [("down", "w"), ("up", "w")]                           # the middle door: forward only
    assert pressed.count(("down", "w")) == pressed.count(("up", "w"))
    out = capsys.readouterr().out
    assert "room 12, hearts not seen, items: none, doors: boarded, boarded, locked, going middle" in out
    assert "Hauntlet 2: room 13" in out
    assert events == ["Hauntlet 2: got as far as room 13"]
    first_walk = next(at for kind, key, at in world.keys if key == "w")
    assert len(world.zooms) == 1 and world.zooms[0] < first_walk                              # zoomed out once, before the first step


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


def slot_of(macro, name, bar=16):
    """Where the slot holding `name` is on the item bar fixture."""
    return next(i.pos for i in macro.read_hauntlet_items(frame(macro, bar=bar)) if i.name == name)


def play_one_room(macro, world, screens, seconds=1.5):
    world.screens = screens
    cfg = macro.cfg
    macro.play_hauntlet_2(world.clock.now + cfg.MINIGAME_START_WAIT + cfg.HAUNTLET_ROOM_SETTLE + seconds, watch_of(macro))


def test_the_grey_key_is_used_for_a_locked_door_a_cyan_potion_for_a_boarded_one_and_a_heart_potion_always(macro, world, monkeypatch, capsys):
    cfg = macro.cfg
    monkeypatch.setattr(macro, "log_run_event", lambda text: None)
    monkeypatch.setattr(cfg, "HAUNTLET_PHONE_CHANCE", 0.0)
    base = frame(macro, 97, room=12, bar=16)                                                  # boarded, boarded, locked; a phone, key, cyan and heart potion
    for index, expected in ((2, ["key", "heart_potion"]), (0, ["cyan_potion", "heart_potion"]), (1, ["cyan_potion", "heart_potion"])):
        macro.clicks.clear()
        world.clock.now += 1000
        monkeypatch.setattr(macro.random, "choice", lambda options, index=index: index)
        play_one_room(macro, world, [base])
        assert macro.clicks[:2] == [slot_of(macro, name) for name in expected], index
    assert "used key, heart_potion" in capsys.readouterr().out


def test_the_red_potion_is_used_when_the_hearts_are_low_and_not_otherwise(macro, world, monkeypatch):
    cfg = macro.cfg
    monkeypatch.setattr(macro, "log_run_event", lambda text: None)
    monkeypatch.setattr(cfg, "HAUNTLET_PHONE_CHANCE", 0.0)
    base = frame(macro, 97, room=12, bar=100)                                                 # a red potion only
    red = slot_of(macro, "red_potion", bar=100)
    for hearts, used in ((macro.HauntletHearts(cfg.HAUNTLET_LOW_HEARTS, 2, 30, (0, 0)), True), (macro.HauntletHearts(cfg.HAUNTLET_LOW_HEARTS + 1, 0, 30, (0, 0)), False)):
        macro.clicks.clear()
        world.clock.now += 1000
        monkeypatch.setattr(macro, "read_hauntlet_hearts", lambda img, hearts=hearts: hearts)
        play_one_room(macro, world, [base])
        assert (red in macro.clicks) is used


def test_an_item_not_held_is_not_clicked(macro, world, monkeypatch):
    world.screens = [frame(macro, bar=100)]
    assert macro.hauntlet_use_item("phone", watch_of(macro)) is None and macro.clicks == []
    assert macro.hauntlet_use_item("red_potion", watch_of(macro)) is not None and macro.clicks == [slot_of(macro, "red_potion", bar=100)]


def test_after_the_phone_the_door_with_the_red_arrow_is_not_gone_through(macro, world, monkeypatch, capsys):
    cfg = macro.cfg
    monkeypatch.setattr(macro, "log_run_event", lambda text: None)
    monkeypatch.setattr(cfg, "HAUNTLET_PHONE_CHANCE", 1.0)
    before = frame(macro, 97, room=12, bar=16)
    arrow = cv2.imread(os.path.join(HERE, "doors", "phone_arrow.png"))[100:260, 860:1050]
    after = before.copy()
    after[100:260, 860:1050] = arrow                                                          # the arrow over the middle door
    phone = slot_of(macro, "phone")
    chosen = []
    monkeypatch.setattr(macro.random, "choice", lambda options: chosen.append(list(options)) or options[0])
    play_one_room(macro, world, lambda: after if phone in macro.clicks else before)
    assert macro.clicks[0] == phone
    assert chosen and 1 not in chosen[0]                                                      # the middle door was not among those to choose from
    assert "ghost behind the middle one" in capsys.readouterr().out


def test_without_the_arrow_every_door_is_still_a_choice_and_the_phone_is_only_used_by_chance(macro, world, monkeypatch):
    cfg = macro.cfg
    monkeypatch.setattr(macro, "log_run_event", lambda text: None)
    base = frame(macro, 97, room=12, bar=16)
    phone = slot_of(macro, "phone")
    monkeypatch.setattr(cfg, "HAUNTLET_PHONE_CHANCE", 0.0)
    play_one_room(macro, world, [base])
    assert phone not in macro.clicks
    macro.clicks.clear()
    world.clock.now += 1000
    monkeypatch.setattr(cfg, "HAUNTLET_PHONE_CHANCE", 1.0)
    chosen = []
    monkeypatch.setattr(macro.random, "choice", lambda options: chosen.append(list(options)) or options[0])
    play_one_room(macro, world, [base])
    assert macro.clicks[0] == phone and chosen and chosen[0] == [0, 1, 2]                             # nothing to avoid (and the key is held, so the lock is a choice)


def test_a_room_whose_doors_are_never_found_is_walked_straight_on_and_the_screenshot_kept_once(macro, world, monkeypatch):
    cfg = macro.cfg
    number = not_a_fork_or_the_hall(macro)
    failures = []
    monkeypatch.setattr(macro, "log_failure", lambda reason, screenshot=True: failures.append(reason))
    monkeypatch.setattr(macro, "log_run_event", lambda text: None)
    world.screens = [frame(macro, room=number)]                                # the timer and the room, but no TVs at all
    macro.play_hauntlet_2(world.clock.now + cfg.MINIGAME_START_WAIT + 6 * (cfg.HAUNTLET_DOORS_TIMEOUT + cfg.HAUNTLET_RETRY_WAIT + cfg.HAUNTLET_WALK_MAX), watch_of(macro))
    keys = [(kind, key) for kind, key, _ in world.keys]
    turns = [e for e in world.mouse if e == ("down", "right")]
    assert len(turns) == cfg.HAUNTLET_SEARCH_TURNS                             # it turned the camera to look for the doors, as many times as allowed,...
    assert keys.count(("down", "w")) >= 2                                      # ...and then walked ahead, more than once
    assert failures == [f"Hauntlet 2: no doors found in room {number}"]        # reported once, not every try
    assert len(world.zooms) == 2                                               # zoomed out at the start and once more when the doors weren't found, not before each turn


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
    assert second_walk - first_walk[1][2] < 0.9 * cfg.HAUNTLET_RETRY_WAIT        # at once, not after the wait that follows a walk that "failed"


# --- getting stuck ---

def test_a_walk_that_gets_nowhere_is_followed_by_one_that_steps_back_first_and_wiggles(macro, world, monkeypatch):
    cfg = macro.cfg
    monkeypatch.setattr(cfg, "HAUNTLET_WALK_MAX", 5 * cfg.HAUNTLET_WIGGLE_PERIOD)
    run = macro.HauntletRun()
    run.room = 5
    world.screens = [frame(macro, room=5)]                                       # the room never changes: stuck
    macro.hauntlet_walk(None, 0.0, run, watch_of(macro))
    assert [key for _, key, _ in world.keys] == ["w", "w"] and run.stuck == 1     # the first walk: forward only, no wiggling
    world.keys.clear()
    macro.hauntlet_walk(None, 0.0, run, watch_of(macro))
    keys = [(kind, key) for kind, key, _ in world.keys]
    assert keys[:2] == [("down", cfg.HAUNTLET_BACK_KEY), ("up", cfg.HAUNTLET_BACK_KEY)]       # the second begins with a step back...
    assert world.keys[1][2] - world.keys[0][2] == pytest.approx(cfg.HAUNTLET_BACKUP_DURATION)
    sideways = [k for k in keys if k[0] == "down" and k[1] in (cfg.HAUNTLET_LEFT_KEY, cfg.HAUNTLET_RIGHT_KEY)]
    jumps = [k for k in keys if k[0] == "press"]
    assert len(sideways) >= 3 and len(jumps) == len(sideways)                                # ...and wiggles on the way: a step sideways and a jump together
    assert keys.count(("down", "w")) == keys.count(("up", "w")) == 1 and run.stuck == 2        # forward held all the while, and the count goes on


def test_getting_somewhere_clears_the_stuck_count_and_a_stuck_room_avoids_the_door_that_failed(macro, world, monkeypatch):
    cfg = macro.cfg
    run = macro.HauntletRun()
    run.room, run.stuck = 5, 3
    world.screens = [frame(macro, room=5)] * 2 + [frame(macro, room=6)]
    macro.hauntlet_walk(None, 0.0, run, watch_of(macro))
    assert run.stuck == 0
    monkeypatch.setattr(cfg, "HAUNTLET_PHONE_CHANCE", 0.0)
    monkeypatch.setattr(cfg, "HAUNTLET_PHONE_CHANCE_LOW", 0.0)
    options = []
    monkeypatch.setattr(macro.random, "choice", lambda opts: options.append(list(opts)) or opts[0])
    doors = macro.find_hauntlet_doors(frame(macro, 16))
    run.last_index, run.stuck = 1, 1                                                       # the middle door got us stuck
    macro.hauntlet_choose(frame(macro, 16, room=5), doors, run, watch_of(macro))
    assert options[-1] == [0, 2]
    run.stuck = 0                                                                          # (not stuck any more: it is a choice again)
    macro.hauntlet_choose(frame(macro, 16, room=5), doors, run, watch_of(macro))
    assert options[-1] == [0, 1, 2]


def test_the_phone_is_used_more_when_the_hearts_are_low(macro, world, monkeypatch):
    cfg = macro.cfg
    monkeypatch.setattr(macro, "log_run_event", lambda text: None)
    monkeypatch.setattr(cfg, "HAUNTLET_PHONE_CHANCE", 0.2)
    monkeypatch.setattr(cfg, "HAUNTLET_PHONE_CHANCE_LOW", 0.8)
    monkeypatch.setattr(macro.random, "random", lambda: 0.5)                              # between the two chances
    base = frame(macro, 97, room=12, bar=16)
    phone = slot_of(macro, "phone")
    for hearts, used in ((macro.HauntletHearts(cfg.HAUNTLET_LOW_HEARTS + 1, 0, 30, (0, 0)), False), (macro.HauntletHearts(cfg.HAUNTLET_LOW_HEARTS, 2, 30, (0, 0)), True)):
        macro.clicks.clear()
        world.clock.now += 1000
        monkeypatch.setattr(macro, "read_hauntlet_hearts", lambda img, hearts=hearts: hearts)
        play_one_room(macro, world, [base])
        assert (phone in macro.clicks) is used


def test_a_room_whose_doors_cannot_be_found_is_looked_around_for_before_walking_ahead(macro, world, monkeypatch):
    """The camera can be turned away from them (room 8 of a macro-played run): it is dragged round and looked at again."""
    cfg = macro.cfg
    number = not_a_fork_or_the_hall(macro)
    monkeypatch.setattr(macro, "log_failure", lambda reason, screenshot=True: None)
    monkeypatch.setattr(macro, "log_run_event", lambda text: None)
    nothing, doors = frame(macro, room=number), frame(macro, 97, room=number)
    turns = lambda: sum(1 for e in world.mouse if e == ("down", "right"))
    world.screens = lambda: doors if turns() >= 2 else nothing                              # the doors come into view with the second turn
    macro.play_hauntlet_2(world.clock.now + cfg.MINIGAME_START_WAIT + cfg.HAUNTLET_DOORS_TIMEOUT + 4 * cfg.HAUNTLET_WALK_MAX, watch_of(macro))
    assert turns() == 2                                                                    # it turned until it saw them, and no more
    assert any(e[0] == "down" and e[1] == "w" for e in world.keys)                        # and then went on to choose and walk
