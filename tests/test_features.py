"""The daily money reset, the Debug tab's run-every-need sequence, and judging an icon gone by how its place changes."""
import datetime
import os
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


def test_reset_config_goes_back_to_not_yet_recorded(macro, config_file):
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


def test_the_default_sequence_is_every_teleport_handler(macro, monkeypatch):
    assert set(macro.TELEPORT_HANDLERS) == set(macro.cfg.TELEPORT_WALK_NEEDS) and len(macro.TELEPORT_HANDLERS) >= 4     # (no button need is in it)
    seen = []
    monkeypatch.setattr(macro, "respawn_character", lambda: None)
    monkeypatch.setattr(macro, "check_focus", lambda: None)
    fake = {name: type("H", (), {"handle": lambda self, name=name: seen.append(name) or True}) for name in macro.TELEPORT_HANDLERS}
    monkeypatch.setattr(macro, "TELEPORT_HANDLERS", fake)
    macro.run_all_need_handlers()
    assert seen == list(fake)


def test_a_need_is_on_unless_its_switch_says_otherwise_and_the_switch_is_kept(macro, monkeypatch):
    monkeypatch.setattr(macro.cfg, "ENABLED_NEEDS", {"catch", "sick"})
    assert macro.need_is_enabled("catch") and not macro.need_is_enabled("pet")                # ENABLED_NEEDS is where the switches start
    macro.AdoptMeGUI.set_need_enabled(None, "catch", False)
    macro.AdoptMeGUI.set_need_enabled(None, "pet", True)
    assert not macro.need_is_enabled("catch") and macro.need_is_enabled("pet") and macro.need_is_enabled("sick")
    assert macro.enabled_needs() == {"pet", "sick"}
    assert macro.load_game_config()["needs_enabled"] == {"catch": False, "pet": True}          # persisted, not only remembered
    macro.reset_game_config()
    assert macro.need_is_enabled("catch") and not macro.need_is_enabled("pet")                # a reset puts them back


def test_a_need_switched_off_gets_no_handler_and_one_switched_on_does(macro, monkeypatch):
    monkeypatch.setattr(macro.cfg, "ENABLED_NEEDS", {"catch"})
    assert macro.get_need_handler("catch") is not None and macro.get_need_handler("pet") is None
    macro.AdoptMeGUI.set_need_enabled(None, "catch", False)
    macro.AdoptMeGUI.set_need_enabled(None, "pet", True)
    assert macro.get_need_handler("catch") is None and macro.get_need_handler("pet") is not None


def test_stuck_needs_are_only_counted_for_the_needs_that_are_on(macro, monkeypatch):
    monkeypatch.setattr(macro.cfg, "ENABLED_NEEDS", {"catch", "pet"})
    macro.AdoptMeGUI.set_need_enabled(None, "pet", False)
    monkeypatch.setattr(macro, "log_failure", lambda *a, **k: None)
    for _ in range(macro.cfg.NEED_STUCK_CHECKS):
        macro.state.attempted_last = {"catch", "pet"}
        macro.record_detection(["catch", "pet"])
    assert macro.state.disabled_needs == {"catch"}                                             # the one switched off is not acted on, so not disabled either


BRIGHT = (240, 250, 255)


def bright_screen():
    return np.full((1080, 1920, 3), BRIGHT, np.uint8)


def fixture(name):
    return cv2.imread(os.path.join(os.path.dirname(__file__), "fixtures", name))


# --- the minigame finish screen, closed whenever it is up ------------------------------------------------

@pytest.fixture
def winner(macro, monkeypatch):
    """A screen showing the minigame finish screen (banner and NICE! button), with the things unscrew() does around it stubbed."""
    screen = bright_screen()
    cfg = macro.cfg
    for box, color in ((cfg.MINIGAME_FINISH_BANNER_BOX, cfg.MINIGAME_FINISH_BANNER_COLOR), (cfg.MINIGAME_FINISH_BUTTON_BOX, cfg.GREEN_BUTTON_COLOR)):
        left, top, right, bottom = box
        screen[top:bottom, left:right] = color[::-1]
    state = {"screen": screen, "sticky": 0}      # "sticky": clicks the finish screen survives
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


def test_a_finish_screen_is_closed_and_the_character_respawned(macro, winner):
    assert macro.close_minigame_finish_screen() is True
    assert macro.cfg.MINIGAME_FINISH_BUTTON_POS in macro.clicks and winner["respawns"] == [1]
    assert "minigame finish screen closed" in open(macro.cfg.RUN_LOG_PATH).read()
    assert macro.state.last_progress is not None


def test_a_click_that_missed_is_repeated(macro, winner, monkeypatch):
    monkeypatch.setattr(macro.cfg, "MINIGAME_FINISH_CLICK_TRIES", 3)
    winner["sticky"] = 1
    assert macro.close_minigame_finish_screen() is True
    assert macro.clicks.count(macro.cfg.MINIGAME_FINISH_BUTTON_POS) == 2 and winner["respawns"] == [1]


def test_a_finish_screen_that_will_not_close_is_retried_then_reported(macro, winner):
    winner["sticky"] = 99
    macro.close_minigame_finish_screen()
    assert macro.clicks.count(macro.cfg.MINIGAME_FINISH_BUTTON_POS) == macro.cfg.MINIGAME_FINISH_CLICK_TRIES
    assert "would not close" in open(macro.cfg.RUN_LOG_PATH).read()


def test_the_real_finish_screen_is_recognised(macro):
    screen = bright_screen()
    for box, name in ((macro.cfg.MINIGAME_FINISH_BANNER_BOX, "finish_banner.png"), (macro.cfg.MINIGAME_FINISH_BUTTON_BOX, "finish_button.png")):
        crop = fixture(name)                                                     # (relative to this file, not the working directory)
        left, top, right, bottom = box
        screen[top:top + crop.shape[0], left:left + crop.shape[1]] = crop
    assert macro.detect_minigame_finished(screen) is True


def test_no_finish_screen_means_nothing_happens(macro, winner):
    winner["screen"] = bright_screen()
    assert macro.close_minigame_finish_screen() is False
    assert not macro.clicks and winner["respawns"] == []


def test_the_green_button_alone_is_not_a_finish_screen(macro, winner):
    screen = bright_screen()
    left, top, right, bottom = macro.cfg.MINIGAME_FINISH_BUTTON_BOX
    screen[top:bottom, left:right] = macro.cfg.GREEN_BUTTON_COLOR[::-1]
    winner["screen"] = screen                                    # a popup's Yes, an Okay: the banner rules them out
    assert macro.close_minigame_finish_screen() is False


def test_every_cycle_checks_for_the_finish_screen_and_abandons_the_task(macro, winner):
    assert macro.unscrew() == "played"
    assert macro.cfg.MINIGAME_FINISH_BUTTON_POS in macro.clicks and winner["respawns"] == [1]


def test_setup_only_clicks_nice_and_carries_on(macro, winner, monkeypatch):
    monkeypatch.setattr(macro, "minigame_popup", lambda play=True: None)
    assert macro.unscrew(in_task=True, play_minigames=False) is None
    assert macro.cfg.MINIGAME_FINISH_BUTTON_POS in macro.clicks and winner["respawns"] == []


def test_the_finish_screen_is_only_looked_for_while_halloween_is_on(macro, winner, monkeypatch):
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


def scans(monkeypatch, macro, *results):
    """Make detect_buttons() return `results` one after another; returns the waits asked for."""
    waits, queue = [], list(results)
    monkeypatch.setattr(macro, "detect_buttons", lambda save_debug=False: queue.pop(0))
    monkeypatch.setattr(macro, "wait_interruptible", lambda duration, *a, **k: waits.append(duration))
    return waits

FOUR = [(620, 600), (960, 600), (1130, 600), (1300, 600)]
FIVE = [(620 + 170 * i, 600) for i in range(5)]


def test_a_button_scan_that_misses_one_is_repeated_and_maps_nothing_if_it_misses_again(macro, monkeypatch):
    macro.state.button_positions["hungry"] = (1, 1)                                      # a stale one
    waits = scans(monkeypatch, macro, FOUR, FOUR)
    assert macro.refresh_button_mapping() is False
    assert waits == [macro.cfg.BUTTON_SCAN_RETRY_WAIT] and macro.state.button_positions == {}
    assert "button scan found 4 of 5" in open(macro.cfg.RUN_LOG_PATH).read()


def test_a_button_scan_that_finds_all_five_the_second_time_maps_them(macro, monkeypatch):
    waits = scans(monkeypatch, macro, FOUR, FIVE)
    assert macro.refresh_button_mapping() is True
    assert waits == [macro.cfg.BUTTON_SCAN_RETRY_WAIT] and macro.state.button_positions[macro.cfg.BUTTON_NAMES[4]] == FIVE[4]


def test_buttons_hidden_by_a_beam_or_the_character_leave_the_others_named_by_where_they_are(macro, monkeypatch):
    """Real frames of a run (12:37 and 12:40): a beam of light crosses the thirsty button, and in the second the character stands before the potty
    one. The scan found 4 and 3 buttons, and naming them by order sent the dirty need to the potty button."""
    import cv2
    here = os.path.join(os.path.dirname(__file__), "buttons")
    monkeypatch.setattr(macro, "focus_roblox_click", lambda: True)
    monkeypatch.setattr(macro, "wait_interruptible", lambda *a, **k: None)
    names = macro.cfg.BUTTON_NAMES                                                         # (the order of the buttons across the screen)
    for name, missing in (("beam_hides_thirsty", {names[1]}), ("beam_hides_thirsty_character_hides_potty", {names[1], names[3]})):
        screen = cv2.imread(os.path.join(here, f"{name}.png"))
        monkeypatch.setattr(macro, "grab_screen", lambda screen=screen: screen)
        assert macro.refresh_button_mapping() is True, name
        assert set(names) - set(macro.state.button_positions) == missing, name
        assert macro.state.button_positions[names[2]][0] == pytest.approx(858, abs=5), name
        assert macro.click_need_button(names[1]) is False                                    # (no button seen: nothing is clicked)


def test_buttons_that_are_not_where_the_buttons_are_are_not_named(macro):
    slots = macro.cfg.BUTTON_SLOTS_X
    names = macro.cfg.BUTTON_NAMES
    assert macro.name_buttons_by_position([(slots[0] + 5, 600), (slots[3] - 8, 640)]) == {names[0]: (slots[0] + 5, 600), names[3]: (slots[3] - 8, 640)}
    assert macro.name_buttons_by_position([(slots[0], 600), (slots[3] + 100, 600)]) == {}        # one is near none
    assert macro.name_buttons_by_position([(slots[0], 600), (slots[0] + 10, 600)]) == {}         # two are near the same one


def test_a_button_scan_that_finds_all_five_does_not_wait_or_log(macro, monkeypatch):
    waits = scans(monkeypatch, macro, FIVE)
    assert macro.refresh_button_mapping() is True and waits == []
    assert not os.path.exists(macro.cfg.RUN_LOG_PATH) or "button scan" not in open(macro.cfg.RUN_LOG_PATH).read()
