"""The Pet Pen: the Task Board's alert badge, its "Pet Pen Ready" card and the NICE! button of the age-up screen, against real frames (tests/pet_pen/), and the walk through them."""
import os
import time

import cv2
import numpy as np
import pytest

import conftest

SHOTS = os.path.join(conftest.ROOT, "tests", "pet_pen")


def shot(name):
    return cv2.imread(os.path.join(SHOTS, name + ".png"))


def test_the_sign_is_found_on_every_background_and_not_in_a_scene_without_it(macro):
    for name in ("badge", "no_badge", "world_after", "board_pet_pen_ready", "board_other_alerts", "sign_stormy", "sign_done"):               # (sign_stormy: the Lightning Dimension update, a dark cloud round the sign and darker wood; sign_done: its green check and a chick over the wood, which broke the blob in pieces - not found in 127 of 177 frames of a run before the pieces were joined)
        x, y = macro.find_task_board_button(shot(name))
        assert 40 <= x <= 90 and 530 <= y <= 590, name
    assert macro.find_task_board_button(shot("age_up_nice")) is None
    assert macro.task_board_badge(shot("age_up_nice")) is False


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
    frames[:] = [shot("world_after"), shot("board_pet_pen_ready"), shot("board_pet_pen_ready"), shot("age_up_nice"), shot("world_after")]
    assert macro.collect_pet_pen() is True
    assert 20 <= macro.clicks[0][0] <= 110 and 520 <= macro.clicks[0][1] <= 590                  # (the sign, found)
    assert 840 <= macro.clicks[1][0] <= 1080 and 440 <= macro.clicks[1][1] <= 500
    assert abs(macro.clicks[2][0] - 959) < 10 and log == ["pet pen: pets aged up"]
    assert macro.state.task_board_wanted is False
    assert macro.load_game_config()["pet_pen_swap"] is True                                      # (a collect that aged pets up always sends the macro to look at the pen: the full-grown ones are replaced)


def test_a_board_without_the_card_is_closed_again_and_one_that_does_not_open_is_a_failure(macro, screens):
    frames, log = screens
    frames[:] = [shot("world_after"), shot("board_other_alerts")]
    assert macro.collect_pet_pen() is True
    assert macro.clicks[-1] == macro.cfg.STRAY_WINDOWS["task_board"]["close_pos"]
    macro.clicks.clear()
    frames[:] = [shot("world_after")]
    assert macro.collect_pet_pen() is False and log == ["pet pen: the Task Board did not open"]
    log.clear()
    frames[:] = [shot("age_up_nice")]
    assert macro.collect_pet_pen() is False and log == ["pet pen: the Task Board's sign was not seen"]
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


def test_the_pick_of_the_pet_is_stamped_when_the_backpack_is_looked_at_even_if_a_step_failed(macro, monkeypatch):
    """A Setup that failed on some step used to leave pet_repicked alone, so the repick ran at once behind it: the backpack's steps twice."""
    monkeypatch.setattr(macro, "grab_screen", lambda: None)
    monkeypatch.setattr(macro, "find_pet_bar", lambda img: None)
    monkeypatch.setattr(macro, "backpack_on_purpose", lambda: __import__("contextlib").nullcontext())
    monkeypatch.setattr(macro.pydirectinput, "press", lambda key: None, raising=False)
    monkeypatch.setattr(macro, "wait_interruptible", lambda s: None)
    monkeypatch.setattr(macro, "ensure_pets_tab", lambda: True)
    monkeypatch.setattr(macro, "open_filter_panel", lambda *a: False)            # (the filters did not open: this pass fails)
    monkeypatch.setattr(macro, "select_backpack_tab", lambda pos: None)
    monkeypatch.setattr(macro, "choose_list_pet", lambda stop_at_equipped=False: None)
    monkeypatch.setattr(macro, "close_backpack", lambda step: None)
    monkeypatch.setattr(macro, "log_run_event", lambda text: None)
    assert macro.load_game_config()["pet_repicked"] == 0.0
    assert macro.set_backpack_filter() is False
    assert macro.load_game_config()["pet_repicked"] > 0.0
    macro.update_game_config(lambda c: c.update(level_growing_pet=False, pet_repicked=0.0))
    monkeypatch.setattr(macro, "find_pet_bar", lambda img: (1, 1))                    # (a pet is out: no Alicorn to equip)
    macro.set_backpack_filter()
    assert macro.load_game_config()["pet_repicked"] == 0.0                         # (not levelling: nothing to wait an interval for)


def test_the_friend_pill_of_a_pet_that_has_grown_up_is_found_with_its_place_and_no_other_screen_has_one(macro, monkeypatch):
    log = []
    monkeypatch.setattr(macro, "log_run_event", log.append)
    x, y = macro.popup_found(shot("age_up_friend"), "pet_friend")
    assert abs(x - 700) < 10 and abs(y - 111) < 10
    assert macro.note_pet_friend(shot("age_up_friend")) is True and "reached Friend (top left)" in log[0]
    log.clear()
    for name in ("age_up_nice", "earned_nice", "board_pet_pen_ready", "world_after"):
        assert macro.note_pet_friend(shot(name)) is False, name
    assert log == []


# --- switching a full-grown pet out of the pen (real frames of a person doing it: the pen with a "Full grown!" pet, after taking it out, the small backpack, after putting a pet in) ---

def test_the_pens_full_grown_pet_the_free_place_and_the_small_backpack_are_found_on_the_real_frames(macro):
    assert macro.full_grown_slots(shot("pen_full_grown")) == [0]
    assert macro.full_grown_slots(shot("pen_add_pet")) == [] and macro.full_grown_slots(shot("pen_after_add")) == []
    assert macro.popup_visible(shot("pen_full_grown"), "pet_pen_title") and not macro.popup_visible(shot("age_up_nice"), "pet_pen_title")
    x, y = macro.popup_found(shot("pen_add_pet"), "pet_pen_add")
    assert abs(x - 857) < 10 and abs(y - 584) < 10
    assert macro.popup_visible(shot("pen_backpack"), "pet_backpack_title") and not macro.popup_visible(shot("pen_add_pet"), "pet_backpack_title")


def test_a_pet_fits_unless_it_is_grey_in_a_trade_listing_or_has_the_green_edge_of_the_equipped_one_and_the_empty_cells_do_not(macro):
    frame = shot("pen_backpack")
    assert [(macro.pet_cell_state(frame, 0, column) == "pet") for column in range(4)] == [False, False, False, False]       # (the equipped pet, then three in listings)
    assert [(macro.pet_cell_state(frame, 1, column) == "pet") for column in range(4)] == [False, False, True, True]
    assert macro.first_listed_cell(frame) == ("pet", (1055, 787))                                                       # (what the person clicked)
    flat = frame.copy()
    flat[630:910, 850:1190] = 255                                                                               # (a list that has ended: nothing in the cells)
    assert macro.first_listed_cell(flat) is None


def test_the_list_is_scrolled_while_no_pet_fits_and_given_up_when_a_scroll_changes_nothing(macro, monkeypatch):
    frames = [np.full((1080, 1920, 3), 5, np.uint8)]
    monkeypatch.setattr(macro, "grab_screen", lambda: frames[0])
    monkeypatch.setattr(macro, "hover_move", lambda *a, **k: None)
    monkeypatch.setattr(macro, "wait_interruptible", lambda s: None)
    scrolls = []
    monkeypatch.setattr(macro, "scroll_down", lambda n: scrolls.append(n))
    assert macro.choose_pen_pet() is None and scrolls == [1]                                                    # (nothing fits, one scroll moved nothing: the end)
    scrolls.clear()
    moving = iter(range(1, 100))
    monkeypatch.setattr(macro, "scroll_down", lambda n: (scrolls.append(n), frames.__setitem__(0, np.full((1080, 1920, 3), next(moving) % 250, np.uint8)))[0])
    monkeypatch.setattr(macro, "first_listed_cell", lambda img, stop=False: ("pet", (1, 2)) if len(scrolls) == 3 else None)
    assert macro.choose_pen_pet() == (1, 2) and scrolls == [1, 1, 1]
    scrolls.clear()
    monkeypatch.setattr(macro, "first_listed_cell", lambda img, stop=False: None)
    assert macro.choose_pen_pet() is None and len(scrolls) == macro.cfg.PET_GRID_SCROLL_MAX + 1


def test_a_full_grown_pet_is_taken_out_and_the_first_pet_that_fits_is_put_in(macro, monkeypatch):
    stage = ["pen"]
    frames = {"pen": "pen_full_grown", "out": "pen_add_pet", "backpack": "pen_backpack", "in": "pen_after_add"}
    monkeypatch.setattr(macro, "focus_roblox", lambda: True)
    monkeypatch.setattr(macro, "open_pet_pen", lambda: True)
    monkeypatch.setattr(macro, "ensure_pen_age_filter", lambda: True)                                           # (its own tests)
    monkeypatch.setattr(macro, "wait_interruptible", lambda s: None)
    monkeypatch.setattr(macro, "grab_screen", lambda: shot(frames[stage[0]]))
    log = []
    monkeypatch.setattr(macro, "log_run_event", log.append)
    x_button = (macro.cfg.PET_PEN_SLOTS[0][0] + macro.cfg.PET_PEN_X_OFFSET[0], macro.cfg.PET_PEN_SLOTS[0][1] + macro.cfg.PET_PEN_X_OFFSET[1])

    def click(x, y, duration=0):
        macro.clicks.append((x, y))
        if (x, y) == x_button:
            stage[0] = "out"
        elif stage[0] == "out" and abs(x - 857) < 15:
            stage[0] = "backpack"
        elif stage[0] == "backpack" and (x, y) == (1055, 787):
            stage[0] = "in"
    monkeypatch.setattr(macro, "hover_click", click)
    assert macro.switch_pen_pets() is True
    assert macro.clicks[0] == x_button and abs(macro.clicks[1][0] - 857) < 15 and macro.clicks[2] == (1055, 787) and macro.clicks[-1] == macro.cfg.PET_PEN_CLOSE_POS
    assert log == ["pet pen: 1 full-grown pet(s) switched"]


def test_a_pen_with_no_full_grown_pet_is_closed_again_and_a_pet_that_does_not_come_out_is_a_failure(macro, monkeypatch):
    monkeypatch.setattr(macro, "focus_roblox", lambda: True)
    monkeypatch.setattr(macro, "open_pet_pen", lambda: True)
    monkeypatch.setattr(macro, "wait_interruptible", lambda s: None)
    log, failures = [], []
    monkeypatch.setattr(macro, "log_run_event", log.append)
    monkeypatch.setattr(macro, "log_failure", lambda reason, screenshot=True: failures.append(reason))
    monkeypatch.setattr(macro, "hover_click", lambda x, y, duration=0: macro.clicks.append((x, y)))
    monkeypatch.setattr(macro, "grab_screen", lambda: shot("pen_add_pet"))
    assert macro.switch_pen_pets() is True and macro.clicks == [macro.cfg.PET_PEN_CLOSE_POS] and log == ["pet pen: no pet in it is full grown"]
    macro.clicks.clear()
    monkeypatch.setattr(macro, "grab_screen", lambda: shot("pen_full_grown"))                                   # (the click changes nothing)
    assert macro.switch_pen_pets() is False and failures == ["pet pen: a full-grown pet did not go out of the pen"]


def test_nothing_fits_the_pet_stays_out_and_a_swap_is_asked_for_by_friend_and_tried_again_later(macro, monkeypatch):
    macro.update_game_config(lambda c: c.update(pet_pen_swap=False))
    monkeypatch.setattr(macro, "log_run_event", lambda text: None)
    assert macro.note_pet_friend(shot("age_up_friend")) is True and macro.load_game_config()["pet_pen_swap"] is True
    tried = []
    monkeypatch.setattr(macro, "run_side_action", lambda action: tried.append(action.__name__) or False)
    macro.pet_pen_swap_due()
    macro.pet_pen_swap_due()
    assert tried == ["switch_pen_pets"] and macro.load_game_config()["pet_pen_swap"] is True                  # (failed: asked for again, but not at once)
    macro.update_game_config(lambda c: c.update(pet_pen_swap_tried=time.time() - macro.cfg.PET_PEN_SWAP_RETRY - 1))
    monkeypatch.setattr(macro, "run_side_action", lambda action: tried.append(action.__name__) or True)
    macro.pet_pen_swap_due()
    assert len(tried) == 2 and macro.load_game_config()["pet_pen_swap"] is False


def test_both_age_rows_are_found_and_read_in_the_pens_filters_panel_whatever_their_state(macro):
    for name, egg, full in (("pen_filters_both_ticked", True, True), ("pen_filters_full_grown_off", True, False), ("pen_filters_both_off", False, False)):
        img = shot(name)
        assert macro.age_checkbox(img, "age_egg")[1] is egg and macro.age_checkbox(img, "age_full_grown")[1] is full, name
        assert macro.age_filter_is_right.__name__                                                               # (it reads the live screen: see the next tests)
    assert macro.age_checkbox(shot("pen_filters_top"), "age_egg") is None                                        # (the Age rows are not in view at the top of the panel)


def pen_filter_world(macro, monkeypatch, start):
    """The pen's FILTERS panel as a state machine over the real frames: clicking the Egg or the Full Grown box flips it, Confirm and Cancel close the panel, the filter button opens it again."""
    state = {"egg": start[0], "full": start[1], "open": False}
    mixed = shot("pen_filters_both_ticked").copy()                                                              # (Egg off, Full Grown on: made of the two real ones)
    mixed[770:815, 900:960] = shot("pen_filters_both_off")[770:815, 900:960]
    frames = {(True, True): "pen_filters_both_ticked", (True, False): "pen_filters_full_grown_off", (False, False): "pen_filters_both_off", (False, True): mixed}
    monkeypatch.setattr(macro, "wait_interruptible", lambda s: None)
    monkeypatch.setattr(macro, "grab_screen", lambda: (frames[(state["egg"], state["full"])] if not isinstance(frames[(state["egg"], state["full"])], str) else shot(frames[(state["egg"], state["full"])])) if state["open"] else shot("pen_backpack"))
    egg_box, full_box = macro.age_checkbox(shot("pen_filters_both_ticked"), "age_egg")[0], macro.age_checkbox(shot("pen_filters_both_ticked"), "age_full_grown")[0]
    clicks = []

    def click(x, y, duration=0):
        clicks.append((x, y))
        if (x, y) == macro.cfg.PET_PEN_FILTER_BUTTON_POS:
            state["open"] = True
        elif (x, y) in (macro.cfg.SETUP_CONFIRM_POS, macro.cfg.SETUP_FILTER_CANCEL_POS):
            state["open"] = False
        elif abs(x - egg_box[0]) < 15 and abs(y - egg_box[1]) < 15:
            state["egg"] = not state["egg"]
        elif abs(x - full_box[0]) < 15 and abs(y - full_box[1]) < 15:
            state["full"] = not state["full"]
    monkeypatch.setattr(macro, "hover_click", click)
    monkeypatch.setattr(macro, "log_run_event", lambda text: None)
    return state, clicks, egg_box, full_box


def test_a_filter_that_is_already_set_is_looked_at_and_left_alone_and_cancelled(macro, monkeypatch):
    state, clicks, egg_box, full_box = pen_filter_world(macro, monkeypatch, (False, False))
    assert macro.ensure_pen_age_filter() is True
    assert clicks == [macro.cfg.PET_PEN_FILTER_BUTTON_POS, macro.cfg.SETUP_FILTER_CANCEL_POS]                  # (no box was clicked: that would have ticked it again)
    assert (state["egg"], state["full"]) == (False, False)


def test_only_the_boxes_that_are_ticked_are_clicked_then_confirmed_and_looked_at_again(macro, monkeypatch):
    state, clicks, egg_box, full_box = pen_filter_world(macro, monkeypatch, (True, False))
    assert macro.ensure_pen_age_filter() is True
    assert [c for c in clicks if abs(c[0] - full_box[0]) < 15 and abs(c[1] - full_box[1]) < 15] == []          # (Full Grown was unticked: not clicked)
    assert (state["egg"], state["full"]) == (False, False) and macro.cfg.SETUP_CONFIRM_POS in clicks
    assert clicks[-1] == macro.cfg.SETUP_FILTER_CANCEL_POS and clicks.count(macro.cfg.PET_PEN_FILTER_BUTTON_POS) == 2     # (opened, changed, confirmed, opened again to look, cancelled)
    state, clicks, *_ = pen_filter_world(macro, monkeypatch, (True, True))
    assert macro.ensure_pen_age_filter() is True and (state["egg"], state["full"]) == (False, False)


def test_a_pet_is_not_put_in_when_the_filter_did_not_come_up_and_the_picking_waits_half_an_hour(macro, monkeypatch):
    monkeypatch.setattr(macro, "wait_interruptible", lambda s: None)
    monkeypatch.setattr(macro, "log_run_event", lambda text: None)
    monkeypatch.setattr(macro, "grab_screen", lambda: shot("pen_backpack"))
    monkeypatch.setattr(macro, "hover_click", lambda x, y, duration=0: macro.clicks.append((x, y)))
    monkeypatch.setattr(macro, "look_until", lambda find, seconds, poll=0: (1, 1))
    monkeypatch.setattr(macro, "ensure_pen_age_filter", lambda: False)
    monkeypatch.setattr(macro, "popup_found", lambda img, name: (857, 584))
    assert macro.put_a_pet_in_the_pen() is False and macro.clicks[-1] == macro.cfg.PET_PEN_BACKPACK_CLOSE_POS
    assert macro.cfg.PET_REPICK_INTERVAL == 30 * 60


def test_the_sign_is_waited_for_after_a_respawn_when_the_screen_is_black_or_white_for_a_moment(macro, monkeypatch):
    frames = [np.zeros((1080, 1920, 3), np.uint8), np.full((1080, 1920, 3), 255, np.uint8), shot("world_after"), shot("board_other_alerts")]
    monkeypatch.setattr(macro, "focus_roblox", lambda: True)
    monkeypatch.setattr(macro, "grab_screen", lambda: frames.pop(0) if len(frames) > 1 else frames[0])
    monkeypatch.setattr(macro, "wait_interruptible", lambda s: None)
    monkeypatch.setattr(macro, "log_run_event", lambda text: None)
    monkeypatch.setattr(macro, "hover_click", lambda x, y, duration=0: macro.clicks.append((x, y)))
    assert macro.collect_pet_pen() is True and 40 <= macro.clicks[0][0] <= 90                                       # (the sign was found on the third look)


def test_the_sign_is_found_by_its_title_where_the_colours_behind_it_are_wood_too(macro):
    """At the school (a brown floor) and the campfire (orange) the wood of the sign is lost in what is behind it: the blob is the whole strip. The title cloud does not depend on that."""
    for need in ("school", "camping"):
        frame = cv2.imread(os.path.join(os.path.dirname(__file__), "needs_end", f"{need}.png"))
        assert macro.find_dialog_button(frame, macro.cfg.DIALOG_BUTTONS["task_board"]) is None, need
        x, y = macro.find_task_board_button(frame)
        assert 40 <= x <= 90 and 530 <= y <= 600, need
    assert macro.find_task_board_button(shot("age_up_nice")) is None


def test_two_full_grown_pets_are_both_taken_out_even_when_the_pen_is_slow_to_move_the_others_up(macro, monkeypatch):
    states = iter([[0, 1], [0, 1], [0], [0], []])            # the looks at the pen: two grown; still two just after the first X (the others have not moved yet); then one; then none
    taken, put = [], []
    monkeypatch.setattr(macro, "focus_roblox", lambda: True)
    monkeypatch.setattr(macro, "open_pet_pen", lambda: True)
    monkeypatch.setattr(macro, "grab_screen", lambda: None)
    monkeypatch.setattr(macro, "full_grown_slots", lambda img: next(states, []))
    monkeypatch.setattr(macro, "hover_click", lambda x, y, duration=0: taken.append((x, y)))
    monkeypatch.setattr(macro, "wait_interruptible", lambda s: None)
    monkeypatch.setattr(macro, "put_a_pet_in_the_pen", lambda: put.append(1) or True)
    monkeypatch.setattr(macro, "log_run_event", lambda text: None)
    monkeypatch.setattr(macro, "log_failure", lambda text: put.append("failure"))
    assert macro.switch_pen_pets() is True
    assert "failure" not in put and put.count(1) == 2


def pen_with_grown_slots(macro, slots):
    """The real frame of a pen with one full-grown pet (slot 0), with its place copied into the other `slots` as well: a pen with several. (Only one place of a pen with a full-grown pet was captured.)"""
    frame = shot("pen_full_grown").copy()
    half_x, half_y = macro.cfg.PET_PEN_SLOT_HALF
    cx, cy = macro.cfg.PET_PEN_SLOTS[0]
    place = shot("pen_full_grown")[cy - half_y:cy + half_y, cx - half_x:cx + half_x]
    for slot in slots:
        x, y = macro.cfg.PET_PEN_SLOTS[slot]
        frame[y - half_y:y + half_y, x - half_x:x + half_x] = place
    return frame


def test_the_full_grown_sign_is_found_in_every_place_of_the_pen_and_only_where_it_is(macro):
    assert macro.full_grown_slots(pen_with_grown_slots(macro, [1, 2, 3])) == [0, 1, 2, 3]
    assert macro.full_grown_slots(pen_with_grown_slots(macro, [1])) == [0, 1]
    assert macro.full_grown_slots(pen_with_grown_slots(macro, [3])) == [0, 3]
    assert macro.full_grown_slots(shot("pen_after_add")) == []


def test_two_full_grown_pets_are_both_taken_out_and_two_others_put_in_on_the_real_frames(macro, monkeypatch):
    """The pets of a pen grew up and were not replaced: the pen with two full-grown pets, played through with the real frames of a pen taking one out, putting one in and its small backpack."""
    frames = {"pen2": pen_with_grown_slots(macro, [1]), "pen1": shot("pen_full_grown"), "free": shot("pen_add_pet"), "backpack": shot("pen_backpack"), "full": shot("pen_after_add")}
    stage = ["pen2"]
    put_in = []
    monkeypatch.setattr(macro, "focus_roblox", lambda: True)
    monkeypatch.setattr(macro, "open_pet_pen", lambda: True)
    monkeypatch.setattr(macro, "ensure_pen_age_filter", lambda: True)
    monkeypatch.setattr(macro, "wait_interruptible", lambda s: None)
    monkeypatch.setattr(macro, "grab_screen", lambda: frames[stage[0]])
    log, failures = [], []
    monkeypatch.setattr(macro, "log_run_event", log.append)
    monkeypatch.setattr(macro, "log_failure", lambda reason, screenshot=True: failures.append(reason))
    x_first = (macro.cfg.PET_PEN_SLOTS[0][0] + macro.cfg.PET_PEN_X_OFFSET[0], macro.cfg.PET_PEN_SLOTS[0][1] + macro.cfg.PET_PEN_X_OFFSET[1])

    def click(x, y, duration=0):
        macro.clicks.append((x, y))
        if (x, y) == x_first and stage[0] == "pen2":
            stage[0] = "pen1"                                                                  # (the other moves up into the first place)
        elif (x, y) == x_first and stage[0] == "pen1":
            stage[0] = "free"
        elif stage[0] == "free" and abs(x - 857) < 15:                                         # (Add Pet)
            stage[0] = "backpack"
        elif stage[0] == "backpack" and (x, y) == (1055, 787):
            put_in.append(1)
            stage[0] = "free" if len(put_in) < 2 else "full"
    monkeypatch.setattr(macro, "hover_click", click)
    assert macro.switch_pen_pets() is True and failures == []
    assert macro.clicks.count(x_first) == 2 and len(put_in) == 2
    assert log == ["pet pen: 2 full-grown pet(s) switched"] and macro.clicks[-1] == macro.cfg.PET_PEN_CLOSE_POS


def test_the_pen_is_looked_at_every_half_hour_whether_or_not_a_pet_was_seen_to_grow_up(macro, monkeypatch):
    tried = []
    monkeypatch.setattr(macro, "run_side_action", lambda action: tried.append(action.__name__) or True)
    now = time.time()
    macro.update_game_config(lambda c: c.update(pet_pen_enabled=True, pet_pen_swap=False, pet_pen_swap_tried=now - macro.cfg.PET_PEN_SWAP_INTERVAL / 2))
    macro.pet_pen_swap_due()
    assert tried == []                                                                           # (nothing grew up, not yet half an hour)
    macro.update_game_config(lambda c: c.update(pet_pen_swap_tried=now - macro.cfg.PET_PEN_SWAP_INTERVAL - 1))
    macro.pet_pen_swap_due()
    assert tried == ["switch_pen_pets"]                                                          # (the half hour: looked at all the same)
    macro.pet_pen_swap_due()
    assert len(tried) == 1                                                                       # (and the interval starts again)
    macro.update_game_config(lambda c: c.update(pet_pen_swap=True, pet_pen_swap_tried=now - macro.cfg.PET_PEN_SWAP_RETRY - 1))
    macro.pet_pen_swap_due()
    assert len(tried) == 2                                                                       # (a pet that grew up: after the retry time, long before the half hour)
    macro.update_game_config(lambda c: c.update(pet_pen_enabled=False, pet_pen_swap_tried=0.0))
    macro.pet_pen_swap_due()
    assert len(tried) == 2                                                                       # (switched off: never)


def test_the_debug_button_switches_the_pen_at_once_and_respawns(macro, monkeypatch):
    seen = []
    monkeypatch.setattr(macro, "switch_pen_pets", lambda: seen.append("switch") or True)
    monkeypatch.setattr(macro, "respawn_character", lambda: seen.append("respawn"))
    macro.update_game_config(lambda c: c.update(pet_pen_enabled=False, pet_pen_swap=True))        # (whatever the switches say)
    assert macro.switch_pen_now() is True and seen == ["switch", "respawn"]
    assert macro.load_game_config()["pet_pen_swap"] is False and macro.load_game_config()["pet_pen_swap_tried"] > 0
