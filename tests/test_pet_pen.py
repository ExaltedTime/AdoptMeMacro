"""The Pet Pen: the Task Board's alert badge, its "Pet Pen Ready" card and the NICE! button of the age-up screen, against real frames (tests/pet_pen/), and the walk through them."""
import os

import cv2
import pytest

import conftest

SHOTS = os.path.join(conftest.ROOT, "tests", "pet_pen")


def shot(name):
    return cv2.imread(os.path.join(SHOTS, name + ".png"))


def test_the_badge_is_seen_only_while_the_board_shows_it(macro):
    assert macro.task_board_badge(shot("badge")) is True
    assert macro.task_board_badge(shot("no_badge")) is False
    assert macro.task_board_badge(shot("world_after")) is False                      # (a green check there instead)


def test_the_pet_pen_card_is_found_in_the_alerts_list_and_the_other_cards_are_not_it(macro):
    x, y = macro.popup_found(shot("board_pet_pen_ready"), "pet_pen_ready")
    assert 840 <= x <= 1080 and 440 <= y <= 500
    assert macro.popup_found(shot("board_other_alerts"), "pet_pen_ready") is None
    assert macro.popup_visible(shot("board_pet_pen_ready"), "task_board") and macro.popup_visible(shot("board_other_alerts"), "task_board")


def test_the_nice_button_is_found_on_the_age_up_screens_and_nowhere_else(macro):
    for name in ("age_up_nice", "earned_nice"):
        x, y = macro.find_dialog_button(shot(name), macro.cfg.DIALOG_BUTTONS["nice"])
        assert abs(x - 959) < 10 and abs(y - 1016) < 10, name
    for name in ("board_pet_pen_ready", "world_after", "badge"):
        assert macro.find_dialog_button(shot(name), macro.cfg.DIALOG_BUTTONS["nice"]) is None, name


@pytest.fixture
def screens(macro, monkeypatch):
    """The game with `frames` as what grab_screen() shows next (the last stays); the clock and the waits are fake."""
    frames, log = [], []
    clock = [1000.0]
    monkeypatch.setattr(macro, "focus_roblox", lambda: True)
    monkeypatch.setattr(macro, "grab_screen", lambda: frames.pop(0) if len(frames) > 1 else frames[0])
    monkeypatch.setattr(macro, "wait_interruptible", lambda s: clock.__setitem__(0, clock[0] + s))
    monkeypatch.setattr(macro.time, "time", lambda: clock[0])
    monkeypatch.setattr(macro, "log_run_event", log.append)
    return frames, log


def test_a_ready_pet_pen_is_collected_through_the_card_and_the_nice_button(macro, screens):
    frames, log = screens
    frames[:] = [shot("board_pet_pen_ready"), shot("board_pet_pen_ready"), shot("age_up_nice"), shot("world_after")]
    assert macro.collect_pet_pen() is True
    assert macro.clicks[0] == macro.cfg.TASK_BOARD_BUTTON_POS
    assert 840 <= macro.clicks[1][0] <= 1080 and 440 <= macro.clicks[1][1] <= 500
    assert abs(macro.clicks[2][0] - 959) < 10 and log == ["pet pen: pets aged up"]
    assert macro.state.task_board_wanted is False


def test_a_board_without_the_card_is_closed_again_and_one_that_does_not_open_is_a_failure(macro, screens):
    frames, log = screens
    frames[:] = [shot("board_other_alerts")]
    assert macro.collect_pet_pen() is True
    assert macro.clicks[-1] == macro.cfg.STRAY_WINDOWS["task_board"]["close_pos"]
    macro.clicks.clear()
    frames[:] = [shot("world_after")]
    assert macro.collect_pet_pen() is False and log == ["pet pen: the Task Board did not open"]
    assert macro.state.task_board_wanted is False


def test_the_board_is_looked_at_when_a_badge_shows_but_not_too_often_and_otherwise_only_now_and_then(macro, screens, monkeypatch):
    frames, _ = screens
    looked = []
    monkeypatch.setattr(macro, "run_side_action", lambda action: looked.append(action.__name__) or True)
    macro.update_game_config(lambda c: c.update(pet_pen_looked=macro.time.time() - 10 * macro.cfg.PET_PEN_CHECK_INTERVAL))
    frames[:] = [shot("no_badge")]
    macro.pet_pen()
    assert looked == ["collect_pet_pen"]                                              # (a long time since the last look: no badge needed)
    macro.pet_pen()
    assert looked == ["collect_pet_pen"]                                              # (just looked)
    macro.update_game_config(lambda c: c.update(pet_pen_looked=macro.time.time() - macro.cfg.PET_PEN_BADGE_INTERVAL - 1))
    macro.pet_pen()
    assert len(looked) == 1                                                           # (a while, no badge)
    frames[:] = [shot("badge")]
    macro.pet_pen()
    assert len(looked) == 2                                                           # (a while, a badge)
    macro.update_game_config(lambda c: c.update(pet_pen_enabled=False, pet_pen_looked=0.0))
    macro.pet_pen()
    assert len(looked) == 2


def test_the_task_board_is_not_closed_as_a_stray_window_while_the_pet_pen_has_it_open(macro, monkeypatch):
    monkeypatch.setattr(macro, "grab_screen", lambda: shot("board_pet_pen_ready"))
    macro.state.task_board_wanted = True
    assert macro.dismiss_stray_windows() is None
    macro.state.task_board_wanted = False
    assert macro.dismiss_stray_windows() == "task_board"


def test_the_pet_to_level_is_picked_again_every_interval_when_the_option_is_on(macro, monkeypatch):
    picked = []
    monkeypatch.setattr(macro, "run_side_action", lambda action: picked.append(action.__name__) or True)
    macro.pet_repick()
    assert picked == ["repick_pet"]                                                   # (never done: due)
    macro.pet_repick()
    assert picked == ["repick_pet"]                                                   # (just done)
    macro.update_game_config(lambda c: c.update(pet_repicked=macro.time.time() - macro.cfg.PET_REPICK_INTERVAL - 1))
    macro.pet_repick()
    assert len(picked) == 2
    macro.update_game_config(lambda c: c.update(level_growing_pet=False, pet_repicked=0.0))
    macro.pet_repick()
    assert len(picked) == 2


def test_picking_again_sets_the_filter_and_equips_and_respawns(macro, monkeypatch):
    calls = []
    monkeypatch.setattr(macro, "focus_roblox", lambda: True)
    monkeypatch.setattr(macro, "set_backpack_filter", lambda: calls.append("filter") or True)
    monkeypatch.setattr(macro, "respawn_character", lambda: calls.append("respawn"))
    assert macro.repick_pet() is True and calls == ["filter", "respawn"]
