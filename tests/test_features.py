"""The daily money reset, the Debug tab's run-every-need sequence, and judging an icon gone by how its place changes."""
import datetime

import cv2
import numpy as np
import pytest


def stamp(year, month, day, hour, minute=0):
    return datetime.datetime(year, month, day, hour, minute).timestamp()


# --- the tree's money total, reset every day at MONEY_RESET_HOUR -------------------------------

@pytest.mark.parametrize("now, expected", [
    (stamp(2026, 10, 11, 16, 59), stamp(2026, 10, 10, 17)),    # just before: still yesterday's
    (stamp(2026, 10, 11, 17, 0), stamp(2026, 10, 11, 17)),     # on the dot
    (stamp(2026, 10, 11, 23, 30), stamp(2026, 10, 11, 17)),
    (stamp(2026, 10, 12, 0, 30), stamp(2026, 10, 11, 17)),     # after midnight: still the previous 17:00
    (stamp(2026, 3, 1, 3, 0), stamp(2026, 2, 28, 17)),         # across a month end
])
def test_last_money_reset_time(macro, now, expected):
    assert macro.cfg.MONEY_RESET_HOUR == 17
    assert macro.last_money_reset_time(now) == expected


@pytest.fixture
def config_file(macro, tmp_path, monkeypatch):
    monkeypatch.setattr(macro.cfg, "GAME_CONFIG_PATH", str(tmp_path / ".config"))

    def put(**values):
        macro.save_game_config({**macro.load_game_config(), **values})
    return put


def at(macro, monkeypatch, when):
    monkeypatch.setattr(macro.time, "time", lambda: when)


def test_money_is_reset_once_the_daily_moment_has_passed(macro, config_file, monkeypatch):
    config_file(money_collected=150, money_reset_boundary=stamp(2026, 10, 10, 17))
    at(macro, monkeypatch, stamp(2026, 10, 11, 17, 30))
    config = macro.reset_money_if_due(macro.load_game_config())
    assert config["money_collected"] == 0 and config["money_reset_boundary"] == stamp(2026, 10, 11, 17)
    assert macro.load_game_config()["money_collected"] == 0                      # saved
    assert "money total reset: 150 -> 0" in open(macro.cfg.RUN_LOG_PATH).read()


def test_money_is_kept_before_the_next_daily_moment(macro, config_file, monkeypatch):
    config_file(money_collected=150, money_reset_boundary=stamp(2026, 10, 10, 17))
    at(macro, monkeypatch, stamp(2026, 10, 11, 16, 59))
    assert macro.reset_money_if_due(macro.load_game_config())["money_collected"] == 150


def test_the_first_time_it_is_seen_the_moment_is_only_recorded(macro, config_file, monkeypatch):
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


# --- an icon judged gone by how its place changes, while the need bar can't be read --------------

POS = (400, 80)
BRIGHT = (240, 250, 255)


def draw_icon(screen, timer_degrees):
    """A need icon on a bright background: a coloured disc, with the timer's darker section `timer_degrees` wide."""
    cv2.circle(screen, POS, 28, (60, 140, 230), -1)
    cv2.circle(screen, POS, 28, (255, 255, 255), 3)
    if timer_degrees:
        cv2.ellipse(screen, POS, (24, 24), -90, 0, timer_degrees, (20, 60, 110), -1)
    return screen


def bright_screen():
    return np.full((1080, 1920, 3), BRIGHT, np.uint8)


@pytest.fixture
def watch(macro, monkeypatch):
    """icon_gone_by_frame_change() on a screen that shows whatever `looks` says, one entry per look at the icon's place."""
    looks = []

    def region(x, y, width, height):
        screen = looks.pop(0) if looks else last[0]
        last[0] = screen
        return screen[y:y + height, x:x + width].copy()
    last = [draw_icon(bright_screen(), 0)]
    monkeypatch.setattr(macro, "grab_screen_region", region)
    monkeypatch.setattr(macro, "wait_interruptible", lambda duration: None)
    monkeypatch.setattr(macro, "check_focus", lambda: None)
    macro._ICON_WATCH.update(need=None, pos=None, crop=None)
    macro.state.need_icon_positions["pizza"] = POS
    yield looks
    macro._ICON_WATCH.update(need=None, pos=None, crop=None)


def test_the_first_look_only_records_the_circle(macro, watch):
    assert macro.icon_gone_by_frame_change("pizza") is None


def test_an_icon_that_stays_or_whose_timer_creeps_on_is_still_there(macro, watch):
    degrees = [30, 40, 52, 66]
    watch.extend(draw_icon(bright_screen(), d) for d in degrees)
    assert macro.icon_gone_by_frame_change("pizza") is None
    assert [macro.icon_gone_by_frame_change("pizza") for _ in degrees[1:]] == [False, False, False]


def test_an_icon_that_vanishes_is_gone(macro, watch):
    watch.extend([draw_icon(bright_screen(), 90), draw_icon(bright_screen(), 100), bright_screen(), bright_screen()])
    assert macro.icon_gone_by_frame_change("pizza") is None
    assert macro.icon_gone_by_frame_change("pizza") is False
    assert macro.icon_gone_by_frame_change("pizza") is True
    assert "pizza icon gone by frame change" in open(macro.cfg.RUN_LOG_PATH).read()


def test_a_one_frame_change_is_not_taken_for_the_icon_going(macro, watch):
    here = draw_icon(bright_screen(), 90)
    watch.extend([here, bright_screen(), here, here])        # baseline, one blank frame, then back, back
    assert macro.icon_gone_by_frame_change("pizza") is None
    assert macro.icon_gone_by_frame_change("pizza") is False
    assert macro.icon_gone_by_frame_change("pizza") is False


def test_nothing_can_be_said_without_a_known_place(macro, watch):
    assert macro.icon_gone_by_frame_change("hungry") is None
    macro.state.need_icon_positions["edge"] = (5, 5)          # its square would reach past the screen's edge
    assert macro.icon_gone_by_frame_change("edge") is None


def test_the_wait_ends_when_an_unreadable_bar_shows_the_icon_gone(macro, watch, monkeypatch):
    """wait_until_need_gone() on a bright bar: it used to run its whole length; now the icon going ends it."""
    monkeypatch.setattr(macro, "scan_needs", lambda fresh=False: ([], False))      # the bar can't be read
    waited = []
    monkeypatch.setattr(macro, "wait_interruptible", lambda duration: waited.append(duration))
    watch.extend([draw_icon(bright_screen(), 50)] * 3 + [bright_screen()] * 3)
    failures = []
    monkeypatch.setattr(macro, "log_failure", lambda reason, screenshot=True: failures.append(reason))
    macro.wait_until_need_gone("pizza", max_wait=10_000)
    assert not failures
    assert len([w for w in waited if w == macro.cfg.NEED_GONE_POLL_INTERVAL]) == 4   # a baseline look, two more of the icon, then it is gone


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
