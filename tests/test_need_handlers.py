"""The catch, pet and ride needs as the game sees them: the order of the keys, clicks and waits, recorded with everything that touches the game replaced."""
import os
import types

import pytest


@pytest.fixture
def events(macro, monkeypatch):
    """Every call the handlers make to the game, in order, as tuples; time moves only when a handler waits or sleeps."""
    log = []
    clock = types.SimpleNamespace(now=1000.0)
    monkeypatch.setattr(macro, "time", types.SimpleNamespace(**{**vars(macro.time), "time": lambda: clock.now, "sleep": lambda s: (log.append(("sleep", s)), setattr(clock, "now", clock.now + s))}))
    monkeypatch.setattr(macro, "focus_roblox", lambda: log.append(("focus_roblox",)) or True)
    monkeypatch.setattr(macro, "wait_interruptible", lambda s: (log.append(("wait", s)), setattr(clock, "now", clock.now + s)))
    monkeypatch.setattr(macro, "hold_key", lambda key, s: log.append(("hold", key, s)))
    monkeypatch.setattr(macro, "select_backpack_tab", lambda pos: log.append(("tab", pos)))
    monkeypatch.setattr(macro, "close_backpack", lambda step: log.append(("close", step)))
    monkeypatch.setattr(macro, "hover_click", lambda x, y, duration=0: log.append(("click", (x, y), duration)))
    monkeypatch.setattr(macro, "mouse_down", lambda button="left": log.append(("mouse_down",)))
    monkeypatch.setattr(macro, "check_running", lambda: None)
    monkeypatch.setattr(macro, "to_screen", lambda x, y: (x, y))
    monkeypatch.setattr(macro, "focus_pet", lambda *a: log.append(("focus_pet", *a)) or True)
    monkeypatch.setattr(macro, "equip_favorite_vehicle", lambda: log.append(("equip_vehicle",)))
    monkeypatch.setattr(macro, "walk_alternating", lambda pair, total, step=0, need_name=None: log.append(("walk", pair, total, need_name)))
    monkeypatch.setattr(macro.pydirectinput, "press", lambda key, **k: log.append(("press", key)), raising=False)
    monkeypatch.setattr(macro.pydirectinput, "moveTo", lambda x, y, **k: log.append(("move", x, y)), raising=False)
    monkeypatch.setattr(macro.pydirectinput, "mouseUp", lambda **k: log.append(("mouse_up",)), raising=False)
    return log


def test_catch_equips_the_toy_waits_throws_it_as_often_as_asked_and_takes_it_off(macro, events):
    cfg = macro.cfg
    assert macro.CatchNeedHandler().handle() is True
    ui = ("wait", cfg.UI_SETTLE)
    assert events == [
        ("focus_roblox",), ("press", cfg.KEYS["backpack"]), ui, ("tab", cfg.CATCH_TOYS_POS), ui, ("click", cfg.CATCH_SQUEAKY_TOY_POS, 0), ui,
        ("click", cfg.CATCH_EQUIP_POS, 0), ui, ("close", "equipping the toy"), ("wait", cfg.CATCH_WAIT_AFTER_EQUIP),
        ("hold", cfg.KEYS["zoom_in"], cfg.CATCH_ZOOM_DURATION),
        *[event for _ in range(cfg.CATCH_THROW_COUNT) for event in (("click", cfg.EMPTY_POS, 0), ("wait", cfg.CATCH_EMOTE_DELAY))],
        ("click", cfg.CATCH_UNEQUIP_POS, 0)]


def test_catch_does_nothing_when_roblox_cannot_be_focused(macro, events, monkeypatch):
    monkeypatch.setattr(macro, "focus_roblox", lambda: False)
    assert macro.CatchNeedHandler().handle() is False and events == []


def test_pet_focuses_the_pet_clicks_the_centre_and_swipes_once_down_with_the_button_held(macro, events):
    cfg = macro.cfg
    assert macro.PetNeedHandler().handle() is True
    centre = (cfg.REFERENCE_CENTER_X, cfg.REFERENCE_CENTER_Y)
    start_y, end_y = centre[1] + cfg.PET_SWIPE_START_OFFSET, centre[1] + cfg.PET_SWIPE_END_OFFSET
    assert events[:7] == [("focus_roblox",), ("focus_pet", cfg.PET_FOCUS_CLICK_DURATION), ("wait", cfg.UI_SETTLE), ("click", centre, cfg.PET_FOCUS_CLICK_DURATION),
                          ("sleep", cfg.PET_SETTLE_DELAY), ("move", centre[0], start_y), ("sleep", cfg.PET_SETTLE_DELAY)]
    assert events[7] == ("mouse_down",) and events[-1] == ("mouse_up",)
    swipe = [e for e in events[8:-1]]
    moves = [e for e in swipe if e[0] == "move"]
    assert all(e[1] == centre[0] for e in moves) and [m[2] for m in moves] == sorted(m[2] for m in moves) and moves[0][2] == start_y
    assert end_y - 5 <= moves[-1][2] <= end_y and len(moves) >= cfg.PET_SWIPE_DURATION / cfg.PET_SWIPE_STEP_DURATION - 2     # one step at a time, to the end
    assert {e for e in swipe if e[0] == "sleep"} == {("sleep", cfg.PET_SWIPE_STEP_DURATION)}


def test_pet_lets_go_of_the_button_when_the_swipe_is_interrupted(macro, events, monkeypatch):
    calls = []

    def check():
        calls.append(1)
        if len(calls) == 3:
            raise macro.StopRequested()
    monkeypatch.setattr(macro, "check_running", check)
    with pytest.raises(macro.StopRequested):
        macro.PetNeedHandler().handle()
    assert events[-1] == ("mouse_up",)


def test_pet_gives_up_when_the_pet_cannot_be_focused(macro, events, monkeypatch):
    monkeypatch.setattr(macro, "focus_pet", lambda *a: False)
    assert macro.PetNeedHandler().handle() is False and ("mouse_down",) not in events


def test_ride_steps_back_mounts_walks_on_equips_a_vehicle_and_rides_to_and_fro(macro, events):
    cfg = macro.cfg
    assert macro.RideNeedHandler().handle() is True
    assert events == [
        ("focus_roblox",), ("hold", cfg.KEYS["back"], cfg.RIDE_BACKWARD_DURATION), ("press", cfg.KEYS["mount"]), ("wait", cfg.RIDE_WAIT_AFTER_E),
        ("hold", cfg.KEYS["forward"], cfg.RIDE_FORWARD_DURATION), ("equip_vehicle",), ("hold", cfg.KEYS["helicopter"], cfg.RIDE_R_HOLD_DURATION),
        ("walk", (cfg.KEYS["forward"], cfg.KEYS["back"]), cfg.NEED_GONE_MAX_WAIT, "ride")]


def named_in(steps):
    """(action, argument) of every step of a table, the steps inside a step too."""
    for action, *arguments in steps:
        for argument in arguments:
            if isinstance(argument, tuple) and argument and isinstance(argument[0], tuple):
                yield from named_in(argument)
        yield action, arguments


def test_every_constant_and_key_a_step_table_names_exists(macro):
    cfg = macro.cfg
    key_arguments = {"press": [0], "hold": [0], "walk": [0]}
    constant_arguments = {"wait": [0], "sleep": [0], "tab": [0], "click": [0], "hold": [1], "walk": [1], "focus_pet": [0], "click_centre": [0], "repeat": [0]}
    checked = 0
    for handler in (macro.CatchNeedHandler, macro.PetNeedHandler, macro.RideNeedHandler):
        for action, arguments in named_in(handler.steps):
            assert action in macro.STEP_ACTIONS, action
            for i in key_arguments.get(action, []):
                labels = arguments[i] if isinstance(arguments[i], tuple) else (arguments[i],)
                assert all(label in cfg.KEYS for label in labels), (action, arguments)
                checked += 1
            for i in constant_arguments.get(action, []):
                assert hasattr(cfg, arguments[i]), (action, arguments)
                checked += 1
            if action == "say":
                arguments[0].format_map(vars(cfg))                                                    # (every {CONSTANT} in a line exists)
    assert checked > 20


# --- the inventory tour ---

@pytest.fixture
def tour(macro, monkeypatch, tmp_path):
    """inventory_tour() with the game replaced: `screens` is what grab_screen() shows next (the last one stays), and `log` what the tour did."""
    import numpy as np
    log, screens = [], []
    monkeypatch.setattr(macro.cfg, "INVENTORY_DIR", str(tmp_path / "inventory"))
    monkeypatch.setattr(macro.cfg, "INVENTORY_DONE_PATH", str(tmp_path / "inventory" / "done.json"))
    monkeypatch.setattr(macro, "focus_roblox", lambda: True)
    monkeypatch.setattr(macro, "wait_interruptible", lambda s: None)
    monkeypatch.setattr(macro, "detect_backpack_open", lambda img: True)
    monkeypatch.setattr(macro, "detect_backpack_expanded", lambda img: True)
    monkeypatch.setattr(macro, "hover_click", lambda x, y, duration=0: log.append(("click", (x, y))))
    monkeypatch.setattr(macro, "select_backpack_tab", lambda pos: log.append(("tab", pos)))
    monkeypatch.setattr(macro, "close_backpack", lambda step: log.append(("close", step)))
    monkeypatch.setattr(macro, "scroll_down", lambda clicks: log.append(("scroll", clicks)))
    monkeypatch.setattr(macro, "to_screen", lambda x, y: (x, y))
    monkeypatch.setattr(macro.pydirectinput, "press", lambda key, **k: log.append(("press", key)), raising=False)
    monkeypatch.setattr(macro.pydirectinput, "moveTo", lambda x, y, **k: log.append(("move", x, y)), raising=False)
    monkeypatch.setattr(macro, "grab_screen", lambda: screens.pop(0) if len(screens) > 1 else screens[0])

    def screen(level):
        return np.full((macro.cfg.REFERENCE_HEIGHT, macro.cfg.REFERENCE_WIDTH, 3), level, np.uint8)
    return types.SimpleNamespace(log=log, screens=screens, screen=screen, folder=tmp_path / "inventory")


def done_marker(tour):
    import json
    return json.loads((tour.folder / "done.json").read_text())


def test_the_tour_takes_a_picture_scrolls_and_stops_when_a_step_moves_nothing(macro, tour):
    cfg = macro.cfg
    tour.screens.extend([tour.screen(level) for level in (50, 50, 50, 120, 200, 200)])      # (the first two are the looks at the backpack opening and expanding)
    (tour.folder).mkdir(parents=True)
    (tour.folder / "007.png").write_text("from an earlier tour")
    assert macro.inventory_tour() is True
    assert sorted(os.listdir(tour.folder)) == ["001.png", "002.png", "003.png", "done.json"]
    assert done_marker(tour) == {"pictures": 3, "ended": "the end of the list"}
    kinds = [event[0] for event in tour.log]
    assert tour.log[:4] == [("press", cfg.KEYS["backpack"]), ("tab", cfg.INVENTORY_PETS_TAB_POS), ("click", cfg.INVENTORY_PETS_TAB_POS), ("move", *cfg.INVENTORY_SCROLL_POS)]     # the tab clicked again expands it
    assert kinds.count("scroll") == 3 and tour.log[-1] == ("close", "the inventory") and ("scroll", cfg.INVENTORY_SCROLL_CLICKS) in tour.log
    import cv2
    left, top, right, bottom = cfg.INVENTORY_PANEL_BOX
    assert cv2.imread(str(tour.folder / "001.png")).shape[:2] == (bottom - top, right - left)               # each picture is the panel


def test_the_tour_ends_at_the_most_pictures_asked_for_when_the_list_goes_on(macro, tour, monkeypatch):
    monkeypatch.setattr(macro.cfg, "INVENTORY_MAX_PICTURES", 2)
    counter = iter(range(10, 250, 10))
    monkeypatch.setattr(macro, "grab_screen", lambda: tour.screen(next(counter)))
    assert macro.inventory_tour() is True
    assert done_marker(tour) == {"pictures": 2, "ended": "2 pictures taken"} and tour.log[-1] == ("close", "the inventory")


def test_a_backpack_that_did_not_open_is_said_in_the_marker_and_nothing_is_scrolled(macro, tour, monkeypatch):
    tour.screens.append(tour.screen(50))
    monkeypatch.setattr(macro, "detect_backpack_open", lambda img: False)
    assert macro.inventory_tour() is False
    assert done_marker(tour) == {"pictures": 0, "ended": "the backpack did not open"} and not [e for e in tour.log if e[0] == "scroll"]


def test_a_backpack_that_did_not_expand_is_closed_and_said_in_the_marker(macro, tour, monkeypatch):
    tour.screens.append(tour.screen(50))
    monkeypatch.setattr(macro, "detect_backpack_expanded", lambda img: False)
    assert macro.inventory_tour() is False
    assert done_marker(tour) == {"pictures": 0, "ended": "the backpack did not expand"} and tour.log[-1] == ("close", "the inventory") and not [e for e in tour.log if e[0] == "scroll"]


def test_the_wheel_is_turned_in_chunks_with_a_pause_between_that_add_up_to_what_was_asked(macro, monkeypatch):
    turned, slept = [], []
    monkeypatch.setattr(macro.pyautogui, "scroll", lambda amount: turned.append(amount), raising=False)
    monkeypatch.setattr(macro.time, "sleep", slept.append)
    monkeypatch.setattr(macro, "check_running", lambda: None)
    monkeypatch.setattr(macro.cfg, "INVENTORY_SCROLL_CHUNK", 10)
    macro.scroll_down(25)
    assert turned == [-10, -10, -5] and len(slept) == 3 and sum(turned) == -25                  # (down is negative)
    turned.clear()
    macro.scroll_down(0)
    assert turned == []


def test_a_roblox_that_cannot_be_focused_is_said_in_the_marker(macro, tour, monkeypatch):
    monkeypatch.setattr(macro, "focus_roblox", lambda: False)
    assert macro.inventory_tour() is False and done_marker(tour)["ended"] == "Roblox could not be focused" and tour.log == []


def test_a_stop_in_the_middle_of_the_tour_still_leaves_the_marker_for_the_bot(macro, tour, monkeypatch):
    tour.screens.extend([tour.screen(50), tour.screen(90)])
    monkeypatch.setattr(macro, "grab_screen", lambda: tour.screen(90))

    def stopped(clicks):
        raise macro.StopRequested()
    monkeypatch.setattr(macro, "scroll_down", stopped)
    with pytest.raises(macro.StopRequested):
        macro.inventory_tour()
    assert done_marker(tour) == {"pictures": 1, "ended": "stopped"}


def test_two_pictures_of_the_panel_are_the_same_when_their_grey_levels_hardly_differ(macro):
    import numpy as np
    flat = np.full((10, 10, 3), 100, np.uint8)
    assert macro.same_picture(flat, flat + 0) and macro.same_picture(flat, np.full((10, 10, 3), 100 + int(macro.cfg.INVENTORY_SAME_LEVEL) - 1, np.uint8))
    assert not macro.same_picture(flat, np.full((10, 10, 3), 100 + int(macro.cfg.INVENTORY_SAME_LEVEL) + 5, np.uint8)) and not macro.same_picture(flat, flat[:5])
