"""The daily money reset, the Debug tab's run-every-need sequence, and judging an icon gone by how its place changes."""
import datetime
import os
import threading
import time

import cv2
import numpy as np
import pytest


def stamp(year, month, day, hour, minute=0):
    return datetime.datetime(year, month, day, hour, minute).timestamp()


# --- the tree's money total, reset every day at MONEY_RESET_HOUR -------------------------------

@pytest.fixture
def hour_17(macro, monkeypatch):
    """These tests are about the daily reset at 17:00, whatever MONEY_RESET_HOUR is tuned to."""
    monkeypatch.setattr(macro.cfg, "MONEY_RESET_HOUR", 17)


@pytest.mark.parametrize("now, expected", [
    (stamp(2026, 10, 11, 16, 59), stamp(2026, 10, 10, 17)),    # just before: still yesterday's
    (stamp(2026, 10, 11, 17, 0), stamp(2026, 10, 11, 17)),     # on the dot
    (stamp(2026, 10, 11, 23, 30), stamp(2026, 10, 11, 17)),
    (stamp(2026, 10, 12, 0, 30), stamp(2026, 10, 11, 17)),     # after midnight: still the previous 17:00
    (stamp(2026, 3, 1, 3, 0), stamp(2026, 2, 28, 17)),         # across a month end
])
def test_last_money_reset_time(macro, hour_17, now, expected):
    assert macro.last_money_reset_time(now) == expected


@pytest.fixture
def config_file(macro, tmp_path, monkeypatch):
    monkeypatch.setattr(macro.cfg, "GAME_CONFIG_PATH", str(tmp_path / ".config"))

    def put(**values):
        macro.save_game_config({**macro.load_game_config(), **values})
    return put


def at(macro, monkeypatch, when):
    monkeypatch.setattr(macro.time, "time", lambda: when)


def test_money_is_reset_once_the_daily_moment_has_passed(macro, hour_17, config_file, monkeypatch):
    config_file(money_collected=150, money_reset_boundary=stamp(2026, 10, 10, 17))
    at(macro, monkeypatch, stamp(2026, 10, 11, 17, 30))
    config = macro.reset_money_if_due(macro.load_game_config())
    assert config["money_collected"] == 0 and config["money_reset_boundary"] == stamp(2026, 10, 11, 17)
    assert macro.load_game_config()["money_collected"] == 0                      # saved
    assert "money total reset: 150 -> 0" in open(macro.cfg.RUN_LOG_PATH).read()


def test_money_is_kept_before_the_next_daily_moment(macro, hour_17, config_file, monkeypatch):
    config_file(money_collected=150, money_reset_boundary=stamp(2026, 10, 10, 17))
    at(macro, monkeypatch, stamp(2026, 10, 11, 16, 59))
    assert macro.reset_money_if_due(macro.load_game_config())["money_collected"] == 150


def test_the_first_time_it_is_seen_the_moment_is_only_recorded(macro, hour_17, config_file, monkeypatch):
    """A fresh or older config has no recorded moment: an update must not wipe the day's total."""
    config_file(money_collected=150)
    assert macro.load_game_config()["money_reset_boundary"] is None
    at(macro, monkeypatch, stamp(2026, 10, 11, 20))
    config = macro.reset_money_if_due(macro.load_game_config())
    assert config["money_collected"] == 150 and config["money_reset_boundary"] == stamp(2026, 10, 11, 17)
    at(macro, monkeypatch, stamp(2026, 10, 12, 17, 5))                            # and the next day it does reset
    assert macro.reset_money_if_due(macro.load_game_config())["money_collected"] == 0


def test_side_quest_resets_the_money_and_the_tree_is_harvested_again(macro, config_file, monkeypatch):
    config_file(money_collected=macro.cfg.MONEY_COLLECTED_TARGET, money_reset_boundary=stamp(2026, 10, 10, 17),
                tree_timer=0, lure_timer=stamp(2030, 1, 1, 0))
    at(macro, monkeypatch, stamp(2026, 10, 11, 17, 1))
    harvested = []
    monkeypatch.setattr(macro, "run_side_action", lambda action: harvested.append(action.__name__) or True)
    macro.side_quest()
    assert harvested == ["tree_collect"]
    assert macro.load_game_config()["money_collected"] == macro.cfg.TREE_HARVEST_YIELD


def test_reset_config_goes_back_to_not_yet_recorded(macro, config_file, monkeypatch):
    config_file(money_collected=5, money_reset_boundary=stamp(2026, 10, 10, 17))
    macro.reset_game_config()
    assert macro.load_game_config()["money_reset_boundary"] is None


# --- the Debug tab's "test every need" ---------------------------------------------------------

def handlers(macro, *results):
    """Fake need handlers that return the given results (an exception class is raised instead), in order."""
    calls = []
    made = {}
    for i, result in enumerate(results):
        def handle(self, i=i, result=result):
            calls.append(f"n{i}")
            if isinstance(result, BaseException):
                raise result
            return result
        made[f"n{i}"] = type(f"H{i}", (), {"handle": handle})
    return made, calls


def test_minigame_popups_are_declined_not_played_while_testing_every_need(macro, monkeypatch):
    seen = []
    monkeypatch.setattr(macro, "respawn_character", lambda: None)
    monkeypatch.setattr(macro, "check_focus", lambda: None)

    class Handler:
        def handle(self):
            seen.append(macro.state.task_play_minigames)
            return True
    macro.run_all_need_handlers({"a": Handler})
    assert seen == [False]                                   # the in-task checks only decline an offered minigame


def test_every_handler_runs_once_in_order_with_a_respawn_after_each(macro, monkeypatch):
    respawns = []
    monkeypatch.setattr(macro, "respawn_character", lambda: respawns.append(len(respawns)))
    monkeypatch.setattr(macro, "check_focus", lambda: None)
    made, calls = handlers(macro, True, False, macro.TaskInterrupted("a minigame"), True)
    results = macro.run_all_need_handlers(made)
    assert calls == ["n0", "n1", "n2", "n3"] and len(respawns) == 4
    assert results == {"n0": "done", "n1": "failed", "n2": "interrupted by a minigame", "n3": "done"}


def test_a_stop_ends_the_sequence(macro, monkeypatch):
    monkeypatch.setattr(macro, "respawn_character", lambda: None)
    monkeypatch.setattr(macro, "check_focus", lambda: None)
    made, calls = handlers(macro, True, True, True)

    def stop_after_first(self):
        calls.append("n0")
        macro.STOP_EVENT.set()
        return True
    made["n0"].handle = stop_after_first
    with pytest.raises(macro.StopRequested):
        macro.run_all_need_handlers(made)
    assert calls == ["n0"]


def test_the_default_sequence_is_every_debug_handler(macro, monkeypatch):
    seen = []
    monkeypatch.setattr(macro, "respawn_character", lambda: None)
    monkeypatch.setattr(macro, "check_focus", lambda: None)
    fake = {name: type("H", (), {"handle": lambda self, name=name: seen.append(name) or True}) for name in macro.DEBUG_HANDLERS}
    monkeypatch.setattr(macro, "DEBUG_HANDLERS", fake)
    macro.run_all_need_handlers()
    assert seen == list(fake) and len(seen) >= 10


# --- an icon judged gone by how it changes between looks, while the need bar can't be read --------

POS = (400, 80)
SLOT = 68                                  # how far apart need icons sit in the bar
BRIGHT = (240, 250, 255)


def bright_screen():
    return np.full((1080, 1920, 3), BRIGHT, np.uint8)


def draw_icon(screen, timer_degrees=0, position=POS, disc=(60, 140, 230)):
    """A need icon on a bright background: a coloured disc with a white glyph, and the timer's darker section."""
    x, y = position
    cv2.circle(screen, position, 28, disc, -1)
    cv2.line(screen, (x - 12, y - 10), (x + 12, y + 10), (255, 255, 255), 5)
    cv2.line(screen, (x + 12, y - 10), (x - 12, y + 10), (255, 255, 255), 5)
    cv2.circle(screen, (x - 9, y + 12), 5, (255, 255, 255), -1)
    cv2.circle(screen, position, 28, (255, 255, 255), 3)
    if timer_degrees:
        cv2.ellipse(screen, position, (24, 24), -90, 0, timer_degrees, (20, 60, 110), -1)
    return screen


@pytest.fixture
def watch(macro, monkeypatch):
    """icon_gone_by_frame_change() on a screen the test controls: set view["screen"] before each look."""
    view = {"screen": draw_icon(bright_screen())}
    monkeypatch.setattr(macro, "grab_screen_region", lambda x, y, w, h: view["screen"][y:y + h, x:x + w].copy())
    monkeypatch.setattr(macro, "wait_interruptible", lambda duration: None)
    monkeypatch.setattr(macro, "check_focus", lambda: None)
    macro._ICON_WATCH.update(need=None, pos=None, crop=None)
    macro.state.need_icon_positions["pizza"] = POS
    yield view
    macro._ICON_WATCH.update(need=None, pos=None, crop=None)


def test_the_first_look_only_records_the_icon(macro, watch):
    assert macro.icon_gone_by_frame_change("pizza") is None


def test_an_icon_whose_timer_creeps_on_is_still_there(macro, watch):
    answers = []
    for degrees in (30, 40, 52, 66, 120):                   # the timer's section grows (and the disc changes) look by look
        watch["screen"] = draw_icon(bright_screen(), degrees)
        answers.append(macro.icon_gone_by_frame_change("pizza"))
    assert answers == [None, False, False, False, False]


def test_an_icon_that_vanishes_is_gone(macro, watch):
    macro.icon_gone_by_frame_change("pizza")
    watch["screen"] = bright_screen()
    assert macro.icon_gone_by_frame_change("pizza") is True
    assert "pizza icon gone by frame change" in open(macro.cfg.RUN_LOG_PATH).read()


def test_a_one_frame_glitch_is_not_taken_for_the_icon_going(macro, watch, monkeypatch):
    macro.icon_gone_by_frame_change("pizza")
    real = macro.locate_icon
    calls = []

    def glitchy(template, row):                              # the first search finds nothing; the second look finds the icon
        calls.append(1)
        return None if len(calls) == 1 else real(template, row)
    monkeypatch.setattr(macro, "locate_icon", glitchy)
    assert macro.icon_gone_by_frame_change("pizza") is False and len(calls) == 2


def test_the_icon_is_followed_when_the_icons_shift_left_because_a_neighbour_cleared(macro, watch):
    """The icons slide left by a slot when one to the left of this one clears: it must not be taken for gone."""
    macro.icon_gone_by_frame_change("pizza")
    watch["screen"] = draw_icon(bright_screen(), 20, position=(POS[0] - SLOT, POS[1]))
    assert macro.icon_gone_by_frame_change("pizza") is False
    assert macro.state.need_icon_positions["pizza"] == (POS[0] - SLOT, POS[1])
    watch["screen"] = draw_icon(bright_screen(), 30, position=(POS[0] - SLOT, POS[1]))   # and it keeps being followed from there
    assert macro.icon_gone_by_frame_change("pizza") is False
    watch["screen"] = draw_icon(bright_screen(), 30, position=(POS[0] - 2 * SLOT, POS[1] + 2))   # shifting again, a pixel or two lower
    assert macro.icon_gone_by_frame_change("pizza") is False


def test_the_icon_is_gone_when_a_neighbour_slides_into_its_place(macro, watch):
    """The need clears and the icon to its right slides into the gap: that is a different icon, so this one is gone."""
    macro.icon_gone_by_frame_change("pizza")
    neighbour = bright_screen()
    cv2.circle(neighbour, POS, 28, (230, 160, 20), -1)                      # a different icon: a blue disc with a moon-like crescent
    cv2.ellipse(neighbour, POS, (16, 16), 0, 40, 300, (255, 255, 255), 6)
    cv2.circle(neighbour, POS, 28, (255, 255, 255), 3)
    watch["screen"] = neighbour
    assert macro.icon_gone_by_frame_change("pizza") is True


def test_nothing_can_be_said_without_a_known_place(macro, watch):
    assert macro.icon_gone_by_frame_change("hungry") is None
    macro.state.need_icon_positions["edge"] = (5, 5)          # its square would reach past the screen's edge
    assert macro.icon_gone_by_frame_change("edge") is None


def test_the_wait_ends_when_an_unreadable_bar_shows_the_icon_gone(macro, watch, monkeypatch):
    """wait_until_need_gone() on a bright bar: it used to run its whole length; now the icon going ends it."""
    monkeypatch.setattr(macro, "scan_needs", lambda fresh=False: ([], False))      # the bar can't be read
    polls = []
    failures = []
    monkeypatch.setattr(macro, "log_failure", lambda reason, screenshot=True: failures.append(reason))

    def wait(duration):
        if duration == macro.cfg.NEED_GONE_POLL_INTERVAL:
            polls.append(1)
            watch["screen"] = draw_icon(bright_screen(), 30 * len(polls)) if len(polls) < 4 else bright_screen()
    monkeypatch.setattr(macro, "wait_interruptible", wait)
    macro.wait_until_need_gone("pizza", max_wait=10_000)
    assert not failures and len(polls) == 4                                 # three polls with the icon there, the fourth without


def test_a_readable_bar_starts_the_next_unreadable_stretch_afresh(macro, watch, monkeypatch):
    macro._ICON_WATCH.update(need="pizza", pos=POS, crop=np.zeros((57, 57, 3), np.uint8))
    monkeypatch.setattr(macro, "scan_needs", lambda fresh=False: (["pizza"], True))
    assert macro._need_cleared("pizza") is False
    assert macro._ICON_WATCH["crop"] is None


def test_icons_remember_where_they_were_recognised(macro, monkeypatch):
    img = np.zeros((300, 300, 3), np.uint8)
    monkeypatch.setattr(macro, "check_running", lambda: None)
    monkeypatch.setattr(macro, "find_matching_need", lambda variants: ("pizza", 0.99))
    list(macro.identify_icons([(120, 60, 30)], img))
    assert macro.state.need_icon_positions == {"pizza": (120, 60)}


# --- the same, on real frames: the salon icon three times while the salon need is served, and its neighbours (tests/fixtures) ---

def fixture(name):
    return cv2.imread(os.path.join(os.path.dirname(__file__), "fixtures", name))


def paste(*placed):
    """A bright screen with icon crops pasted at the given centres: paste((crop, (x, y)), ...)."""
    screen = bright_screen()
    for crop, (x, y) in placed:
        screen[y - 28:y + 29, x - 28:x + 29] = crop
    return screen


def test_real_frames_the_timer_growing_changes_the_icon_a_lot_but_it_is_still_found(macro, watch):
    one, two, three = (fixture(f"salon_icon_{k}.png") for k in (1, 2, 3))
    assert macro.icon_region_change(one, two) == pytest.approx(0.22, abs=0.03)       # a lot of change per look...
    assert macro.icon_region_change(two, three) == pytest.approx(0.39, abs=0.03)
    answers = []
    for frame in (one, two, three):
        watch["screen"] = paste((frame, POS))
        macro.icon_gone_by_frame_change("pizza")
        answers.append(macro._ICON_WATCH["pos"])
    assert answers == [POS, POS, POS]                                            # ...yet each look finds it where it was


def test_real_frames_the_salon_icon_follows_the_shift_and_is_not_confused_with_its_neighbours(macro, watch):
    one, two, three = (fixture(f"salon_icon_{k}.png") for k in (1, 2, 3))
    sleepy, bath = fixture("sleepy_icon.png"), fixture("bath_icon.png")
    watch["screen"] = paste((sleepy, (POS[0] - SLOT, POS[1])), (one, POS), (bath, (POS[0] + SLOT, POS[1])))
    assert macro.icon_gone_by_frame_change("pizza") is None
    # the sleepy icon clears: salon and bath slide left one slot each, and the salon's timer has moved on
    watch["screen"] = paste((two, (POS[0] - SLOT, POS[1])), (bath, POS))
    assert macro.icon_gone_by_frame_change("pizza") is False
    assert macro.state.need_icon_positions["pizza"] == (POS[0] - SLOT, POS[1])
    watch["screen"] = paste((three, (POS[0] - SLOT, POS[1])), (bath, POS))
    assert macro.icon_gone_by_frame_change("pizza") is False
    # the salon is served: its icon goes and the bath slides into its slot - gone, though something coloured is there
    watch["screen"] = paste((bath, (POS[0] - SLOT, POS[1])))
    assert macro.icon_gone_by_frame_change("pizza") is True


def test_real_frames_how_well_an_icon_matches_itself_and_the_others(macro, watch):
    one, two, three = (fixture(f"salon_icon_{k}.png") for k in (1, 2, 3))
    sleepy, bath = fixture("sleepy_icon.png"), fixture("bath_icon.png")
    watch["screen"] = paste((three, POS))
    assert macro.locate_icon(one, POS[1]) == POS                                  # the earliest look still matches the latest
    watch["screen"] = paste((sleepy, POS), (bath, (POS[0] + SLOT, POS[1])))
    assert macro.locate_icon(one, POS[1]) is None                                 # the neighbours are not the salon
    watch["screen"] = bright_screen()
    assert macro.locate_icon(one, POS[1]) is None


# --- and on a real bright need bar: the Pizza Party, five icons (tests/fixtures/pizza_party_bar.png) ---------------

BAR_ORIGIN = (780, 24)                      # where the fixture was cut from, in the 1920x1080 frame
ICONS = [(823, 63), (891, 64), (959, 63), (1027, 63), (1095, 64)]    # the five icons' centres in that frame


def bar_screen(*keep, shifted_left=0):
    """The real bar with only the icons at indexes `keep` left in it (the rest painted over with the bar's background),
    those icons moved `shifted_left` slots left."""
    bar = fixture("pizza_party_bar.png")
    screen = bright_screen()
    background = bar[2, 2].copy()                                       # a bare patch of the cream background
    screen[BAR_ORIGIN[1]:BAR_ORIGIN[1] + bar.shape[0], BAR_ORIGIN[0]:BAR_ORIGIN[0] + bar.shape[1]] = background
    for i in keep:
        x, y = ICONS[i]
        crop = bar[y - 28 - BAR_ORIGIN[1]:y + 29 - BAR_ORIGIN[1], x - 28 - BAR_ORIGIN[0]:x + 29 - BAR_ORIGIN[0]]
        nx = x - shifted_left * SLOT
        screen[y - 28:y + 29, nx - 28:nx + 29] = crop
    return screen


def test_real_bright_bar_is_unreadable_to_the_matching_but_each_icon_is_found_again(macro, watch, monkeypatch):
    screen = bar_screen(0, 1, 2, 3, 4)
    watch["screen"] = screen
    assert not macro.detect_need_bar_readable(screen)                       # the Pizza Party's background: the bar can't be read
    for x, y in ICONS:
        template = screen[y - 28:y + 29, x - 28:x + 29].copy()
        assert macro.locate_icon(template, y) == (x, y)                      # each icon finds itself, and not one of the others
    scores = []
    for i, (x, y) in enumerate(ICONS):
        template = screen[y - 28:y + 29, x - 28:x + 29].copy()
        only_others = bar_screen(*[j for j in range(5) if j != i])
        watch["screen"] = only_others
        scores.append(macro.locate_icon(template, y))
    assert scores == [None] * 5                                              # with its own icon removed it is not found


def test_real_bright_bar_the_awaited_icon_is_followed_and_then_seen_to_go(macro, watch):
    macro.state.need_icon_positions["pizza"] = ICONS[3]
    watch["screen"] = bar_screen(0, 1, 2, 3, 4)
    assert macro.icon_gone_by_frame_change("pizza") is None
    watch["screen"] = bar_screen(0, 1, 2, 3, 4)
    assert macro.icon_gone_by_frame_change("pizza") is False
    # the icon at index 1 is served and goes: the ones to its right (2, 3, 4) slide left a slot
    shifted = bar_screen(0)
    for i in (2, 3, 4):
        part = bar_screen(i, shifted_left=1)
        x, y = ICONS[i][0] - SLOT, ICONS[i][1]
        shifted[y - 28:y + 29, x - 28:x + 29] = part[y - 28:y + 29, x - 28:x + 29]
    watch["screen"] = shifted
    assert macro.icon_gone_by_frame_change("pizza") is False
    assert macro.state.need_icon_positions["pizza"] == (ICONS[3][0] - SLOT, ICONS[3][1])
    # now the awaited icon is served too: only 0, and 2 and 4 (slid left) remain
    gone = bar_screen(0)
    for i in (2, 4):
        part = bar_screen(i, shifted_left=1)
        x, y = ICONS[i][0] - SLOT, ICONS[i][1]
        gone[y - 28:y + 29, x - 28:x + 29] = part[y - 28:y + 29, x - 28:x + 29]
    watch["screen"] = gone
    assert macro.icon_gone_by_frame_change("pizza") is True


def test_a_need_cleared_while_the_bar_was_unreadable_is_seen_even_when_a_neighbour_took_its_place(macro, watch):
    """Run 3, 17:09: the sick icon cleared but its stored position held another icon by then (the others had shifted
    left), so the icon at that position was followed instead and the wait ran out. The icon as last read is searched for."""
    full = bar_screen(0, 1, 2, 3, 4)
    x, y = ICONS[1]
    macro.state.need_icon_positions["pizza"] = (x, y)                                  # where it was last read ...
    macro.state.need_icon_crops["pizza"] = full[y - 28:y + 29, x - 28:x + 29].copy()   # ... and how it looked
    after = bar_screen(0)                                                              # index 1 cleared; 2, 3, 4 slid left a slot
    for i in (2, 3, 4):
        part = bar_screen(i, shifted_left=1)
        nx, ny = ICONS[i][0] - SLOT, ICONS[i][1]
        after[ny - 28:ny + 29, nx - 28:nx + 29] = part[ny - 28:ny + 29, nx - 28:nx + 29]
    watch["screen"] = after
    assert macro.icon_gone_by_frame_change("pizza") is True                            # at the very first look


def test_each_wait_starts_from_the_icon_as_last_read_not_from_an_earlier_wait(macro, monkeypatch):
    """An earlier wait for the same need that ended on a timeout leaves its last picture in the watch; if that was
    the wrong thing (a neighbour), the next wait must not carry on from it."""
    macro._ICON_WATCH.update(need="pizza", pos=(1, 1), crop=np.zeros((57, 57, 3), np.uint8))
    seen = []
    monkeypatch.setattr(macro, "wait_interruptible", lambda duration: None)
    monkeypatch.setattr(macro, "_need_cleared", lambda name: seen.append(dict(macro._ICON_WATCH)) or True)
    macro.wait_until_need_gone("pizza", max_wait=10)
    assert seen and seen[0] == {"need": None, "pos": None, "crop": None}
    macro._ICON_WATCH.update(need="pizza", pos=(1, 1), crop=np.zeros((57, 57, 3), np.uint8))
    stop = threading.Event()
    stop.set()
    macro._watch_need_gone("pizza", threading.Event(), stop)                    # the walking watcher thread's body
    assert macro._ICON_WATCH["need"] is None


def test_the_stored_icon_is_found_again_when_it_has_only_moved(macro, watch):
    full = bar_screen(0, 1, 2, 3, 4)
    x, y = ICONS[3]
    macro.state.need_icon_positions["pizza"] = (x, y)
    macro.state.need_icon_crops["pizza"] = full[y - 28:y + 29, x - 28:x + 29].copy()
    watch["screen"] = bar_screen(0, 3)                                                # 3 is still there, and 1 and 2 are not
    assert macro.icon_gone_by_frame_change("pizza") is False


def test_identify_icons_keeps_each_icon_as_it_was_read(macro, monkeypatch):
    img = np.full((120, 300, 3), 200, np.uint8)
    monkeypatch.setattr(macro, "find_matching_need", lambda variants: ("hungry", 0.99))
    monkeypatch.setattr(macro, "check_running", lambda: None)
    list(macro.identify_icons([(100, 60, 27)], img))
    assert macro.state.need_icon_crops["hungry"].shape == (57, 57, 3) and macro.state.need_icon_positions["hungry"] == (100, 60)


# --- the minigame win screen, closed whenever it is up ------------------------------------------------

@pytest.fixture
def winner(macro, monkeypatch):
    """A screen showing the minigame win screen (banner and NICE! button), with the things unscrew() does around it stubbed."""
    screen = bright_screen()
    cfg = macro.cfg
    for box, color in ((cfg.MINIGAME_VICTORY_BANNER_BOX, cfg.MINIGAME_VICTORY_BANNER_COLOR), (cfg.MINIGAME_VICTORY_BUTTON_BOX, cfg.GREEN_BUTTON_COLOR)):
        left, top, right, bottom = box
        screen[top:bottom, left:right] = color[::-1]
    state = {"screen": screen, "sticky": 0}      # "sticky": clicks the win screen survives
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
    monkeypatch.setattr(macro.cfg, "HALLOWEEN", True)
    state["respawns"] = respawns
    return state


def test_a_win_screen_is_closed_and_the_character_respawned(macro, winner):
    assert macro.close_minigame_win_screen() is True
    assert macro.cfg.MINIGAME_VICTORY_BUTTON_POS in macro.clicks and winner["respawns"] == [1]
    assert "minigame win screen closed" in open(macro.cfg.RUN_LOG_PATH).read()
    assert macro.state.last_progress is not None


def test_a_click_that_missed_is_repeated(macro, winner, monkeypatch):
    monkeypatch.setattr(macro.cfg, "MINIGAME_VICTORY_CLICK_TRIES", 3)
    winner["sticky"] = 1
    assert macro.close_minigame_win_screen() is True
    assert macro.clicks.count(macro.cfg.MINIGAME_VICTORY_BUTTON_POS) == 2 and winner["respawns"] == [1]


def test_a_win_screen_that_will_not_close_is_retried_then_reported(macro, winner):
    winner["sticky"] = 99
    macro.close_minigame_win_screen()
    assert macro.clicks.count(macro.cfg.MINIGAME_VICTORY_BUTTON_POS) == macro.cfg.MINIGAME_VICTORY_CLICK_TRIES
    assert "would not close" in open(macro.cfg.RUN_LOG_PATH).read()


def test_the_real_win_screen_is_recognised(macro):
    import cv2
    screen = bright_screen()
    for box, name in ((macro.cfg.MINIGAME_VICTORY_BANNER_BOX, "win_banner.png"), (macro.cfg.MINIGAME_VICTORY_BUTTON_BOX, "win_button.png")):
        crop = fixture(name)                                                     # (relative to this file, not the working directory)
        left, top, right, bottom = box
        screen[top:top + crop.shape[0], left:left + crop.shape[1]] = crop
    assert macro.detect_minigame_victory(screen) is True


def test_no_win_screen_means_nothing_happens(macro, winner):
    winner["screen"] = bright_screen()
    assert macro.close_minigame_win_screen() is False
    assert not macro.clicks and winner["respawns"] == []


def test_the_green_button_alone_is_not_a_win_screen(macro, winner):
    screen = bright_screen()
    left, top, right, bottom = macro.cfg.MINIGAME_VICTORY_BUTTON_BOX
    screen[top:bottom, left:right] = macro.cfg.GREEN_BUTTON_COLOR[::-1]
    winner["screen"] = screen                                    # a popup's Yes, an Okay: the banner rules them out
    assert macro.close_minigame_win_screen() is False


def test_every_cycle_checks_for_the_win_screen_and_abandons_the_task(macro, winner):
    assert macro.unscrew() == "played"
    assert macro.cfg.MINIGAME_VICTORY_BUTTON_POS in macro.clicks and winner["respawns"] == [1]


def test_setup_only_clicks_nice_and_carries_on(macro, winner, monkeypatch):
    monkeypatch.setattr(macro, "minigame_popup", lambda play=True: None)
    assert macro.unscrew(in_task=True, play_minigames=False) is None
    assert macro.cfg.MINIGAME_VICTORY_BUTTON_POS in macro.clicks and winner["respawns"] == []


def test_the_win_screen_is_only_looked_for_while_halloween_is_on(macro, winner, monkeypatch):
    monkeypatch.setattr(macro.cfg, "HALLOWEEN", False)
    assert macro.unscrew() is None and not macro.clicks


def test_a_scheduled_rejoin_comes_once_the_interval_has_passed(macro, monkeypatch):
    interval = macro.cfg.REJOIN_INTERVAL
    macro.state.last_rejoin = time.time() - (interval - 60)
    macro.state.last_progress = time.time()
    monkeypatch.setattr(macro.gw, "getAllWindows", lambda: [type("W", (), {"title": "Roblox"})()])
    monkeypatch.setattr(macro, "grab_screen", lambda: np.zeros((1080, 1920, 3), np.uint8))
    assert macro.rejoin_reason() is None
    macro.state.last_rejoin = time.time() - (interval + 60)
    assert macro.rejoin_reason()[0] == "scheduled"


# --- a paycheck popup mid-task restarts the task -------------------------------------------------------

@pytest.fixture
def quiet_screen(macro, monkeypatch):
    """unscrew() with nothing on screen but a paycheck popup that detect_paycheck() reports."""
    monkeypatch.setattr(macro, "grab_screen", bright_screen)
    monkeypatch.setattr(macro, "rejoin_game", lambda kinds=None: False)
    monkeypatch.setattr(macro, "close_backpack_if_stuck", lambda in_task: False)
    monkeypatch.setattr(macro, "dismiss_stray_windows", lambda: None)
    monkeypatch.setattr(macro.cfg, "HALLOWEEN", False)


def test_a_paycheck_mid_task_aborts_the_task(macro, quiet_screen, monkeypatch):
    monkeypatch.setattr(macro, "detect_paycheck", lambda: True)
    assert macro.unscrew(in_task=True) == "aborted"


def test_a_paycheck_at_the_top_of_a_cycle_does_not_abort(macro, quiet_screen, monkeypatch):
    monkeypatch.setattr(macro, "detect_paycheck", lambda: True)
    assert macro.unscrew() is None


def test_setup_is_not_aborted_by_a_paycheck(macro, quiet_screen, monkeypatch):
    monkeypatch.setattr(macro, "detect_paycheck", lambda: True)
    assert macro.unscrew(in_task=True, play_minigames=False) is None


def test_a_stored_icon_from_a_dark_scene_is_still_found_on_the_bright_bar(macro, watch):
    """The picture stored while the bar was readable has the old scene in its corners (outside the icon's circle);
    an unmasked match against the bright bar scored 0.60-0.73 with a dark one, under ICON_LOCATE_THRESHOLD, so the
    icon read as gone at the first look. Only the circle counts now."""
    bright = bar_screen(0, 1, 2, 3, 4)
    r = macro.cfg.ICON_CROP_RADIUS
    yy, xx = np.ogrid[:2 * r + 1, :2 * r + 1]
    corners = (yy - r) ** 2 + (xx - r) ** 2 > r * r
    watch["screen"] = bright
    for corner_level in (0, 25, 60, 120):
        for x, y in ICONS:
            stored = bright[y - r:y + r + 1, x - r:x + r + 1].copy()
            stored[corners] = corner_level                                       # a dark world behind the icon, as when it was read
            assert macro.locate_icon(stored, y) == (x, y), (corner_level, x)


def test_the_masked_match_is_the_ordinary_one_when_nothing_is_masked(macro):
    rng = np.random.RandomState(0)
    image = rng.randint(0, 255, (77, 400), np.uint8)
    template = image[10:67, 150:207].copy()
    ours = macro.masked_match_scores(image, template, np.ones((57, 57), np.uint8))
    theirs = cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED)
    assert np.abs(ours - theirs).max() < 1e-4


def test_the_masked_match_ignores_what_the_mask_leaves_out(macro):
    rng = np.random.RandomState(1)
    image = rng.randint(0, 255, (77, 400), np.uint8)
    mask = macro.icon_mask(57)
    template = image[10:67, 150:207].copy()
    template[mask == 0] = rng.randint(0, 255, int((mask == 0).sum()))            # garbage where the mask is 0
    scores = macro.masked_match_scores(image, template, mask)
    assert scores[10, 150] > 0.999 and np.unravel_index(scores.argmax(), scores.shape) == (10, 150)


def test_a_flat_patch_scores_zero_not_noise(macro):
    scores = macro.masked_match_scores(np.full((77, 200), 240, np.uint8), np.random.RandomState(2).randint(0, 255, (57, 57), np.uint8), macro.icon_mask(57))
    assert (scores == 0).all()


def test_the_icon_mask_is_a_disc(macro):
    mask = macro.icon_mask(57)
    assert mask[28, 28] == 1 and mask[0, 0] == 0 and mask[0, 56] == 0 and mask[28, 1] == 1 and mask[28, 0] == 0


def test_the_stored_icon_picture_is_none_at_the_screen_edge(macro):
    assert macro.icon_crop_for_storage(np.zeros((200, 200, 3), np.uint8), 10, 100) is None
    assert macro.icon_crop_for_storage(np.zeros((100, 200, 3), np.uint8), 100, 90) is None


def test_a_button_scan_that_misses_one_is_logged(macro, monkeypatch):
    monkeypatch.setattr(macro, "detect_buttons", lambda save_debug=False: [(620, 600), (960, 600), (1130, 600), (1300, 600)])
    assert macro.refresh_button_mapping() is True
    assert "button scan found 4 of 5" in open(macro.cfg.RUN_LOG_PATH).read()
    five = [(620 + 170 * i, 600) for i in range(5)]
    monkeypatch.setattr(macro, "detect_buttons", lambda save_debug=False: five)
    macro.refresh_button_mapping()
    assert open(macro.cfg.RUN_LOG_PATH).read().count("button scan found") == 1       # all five: nothing to say
