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
    monkeypatch.setattr(macro, "hover_move", lambda x, y, duration=0: log.append(("move", (x, y))))
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
    tour.screens.extend([tour.screen(level) for level in (50, 50, 50, 50, 120, 200, 200)])  # (the first two are the looks at the backpack opening and expanding, the fourth the look after the first notch)
    (tour.folder).mkdir(parents=True)
    (tour.folder / "007.png").write_text("from an earlier tour")
    assert macro.inventory_tour() is True
    assert sorted(os.listdir(tour.folder)) == ["001.png", "002.png", "003.png", "done.json"]
    assert done_marker(tour) == {"pictures": 3, "ended": "the end of the list"}
    assert tour.log[:4] == [("press", cfg.KEYS["backpack"]), ("tab", cfg.INVENTORY_PETS_TAB_POS), ("click", cfg.INVENTORY_EXPAND_POS), ("move", cfg.INVENTORY_SCROLL_POS)]     # the expand button, not the selected tab again
    scrolls = [event[1] for event in tour.log if event[0] == "scroll"]
    assert scrolls == [cfg.INVENTORY_PROBE_CLICKS, cfg.INVENTORY_SCROLL_CLICKS - cfg.INVENTORY_PROBE_CLICKS, cfg.INVENTORY_SCROLL_CLICKS, cfg.INVENTORY_SCROLL_CLICKS]     # (a first notch that could not be measured: the usual step, less that notch)
    assert tour.log[-1] == ("close", "the inventory")
    import cv2
    left, top, right, bottom = cfg.INVENTORY_PANEL_BOX
    assert cv2.imread(str(tour.folder / "001.png")).shape[:2] == (bottom - top, right - left)               # each picture is the panel


def textured(macro, height=1080, width=830, seed=1):
    """A tall made-up list: noise, so that every band of it is found once."""
    import numpy as np
    return np.random.default_rng(seed).integers(0, 255, (height * 4, width, 3), dtype=np.uint8)


def test_how_far_the_list_moved_is_found_by_where_a_band_of_the_picture_went(macro):
    import numpy as np
    tall = textured(macro)
    first, after = tall[0:1080], tall[37:37 + 1080]                                           # the list moved up by 37 px
    assert macro.scroll_shift(first, after) == 37
    covered = after.copy()
    covered[:, :260] = 0                                                                     # a tooltip over the strip at the left: the one at the right still finds it
    assert macro.scroll_shift(first, covered) == 37
    both = after.copy()
    both[:, :260] = 0
    both[:, 570:] = 0
    assert macro.scroll_shift(first, both) is None                                           # (neither strip: not found)
    assert macro.scroll_shift(first, first) is None                                          # did not move
    assert macro.scroll_shift(np.full((1080, 830, 3), 90, np.uint8), np.full((1080, 830, 3), 90, np.uint8)) is None      # nothing to find in a flat picture
    assert macro.scroll_shift(first, after[:500]) is None


def test_the_step_is_as_many_notches_as_move_the_list_the_wanted_distance_after_measuring_the_first_notch(macro, tour, monkeypatch):
    cfg = macro.cfg
    tall = textured(macro)
    position = {"y": 0}
    per_notch = 45
    monkeypatch.setattr(macro, "scroll_down", lambda clicks: (tour.log.append(("scroll", clicks)), position.update(y=min(position["y"] + clicks * per_notch, len(tall) - 1080))))
    monkeypatch.setattr(macro, "panel_picture", lambda img: tall[position["y"]:position["y"] + 1080])
    tour.screens.append(tour.screen(0))                                                      # (what the looks at the backpack opening and expanding see; the pictures are the list's)
    (tour.folder).mkdir(parents=True)
    assert macro.inventory_tour() is True
    scrolls = [event[1] for event in tour.log if event[0] == "scroll"]
    wanted = round(cfg.INVENTORY_STEP_PIXELS / per_notch)
    assert scrolls[:3] == [cfg.INVENTORY_PROBE_CLICKS, wanted - cfg.INVENTORY_PROBE_CLICKS, wanted]                    # the first notch, the rest of the first step, then whole steps
    assert done_marker(tour)["ended"] == "the end of the list"
    pictures = [cv2_read(tour.folder / name) for name in sorted(os.listdir(tour.folder)) if name.endswith(".png")]
    import numpy as np
    step = wanted * per_notch
    assert len(pictures) > 3 and step < 1080 and np.array_equal(pictures[0][step:], pictures[1][:1080 - step])          # each picture's bottom is the next one's top: nothing is skipped


def cv2_read(path):
    import cv2
    return cv2.imread(str(path))


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
    notch = macro.cfg.WHEEL_NOTCH
    assert turned == [-10 * notch, -10 * notch, -5 * notch] and len(slept) == 3 and sum(turned) == -25 * notch      # (down is negative, in the units Windows takes: 120 to a notch)
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


def test_the_macro_window_is_sent_to_the_back_for_the_tour_and_brought_back_however_it_ends(macro, tour, monkeypatch):
    window = types.SimpleNamespace(run_on_ui_thread=lambda func, timeout=5.0: func(), send_to_back=lambda: tour.log.append(("gui back",)), bring_to_front=lambda: tour.log.append(("gui front",)))
    monkeypatch.setattr(macro.state, "macro_window", window)
    tour.screens.extend([tour.screen(level) for level in (50, 50, 50, 120, 120)])
    assert macro.inventory_tour() is True
    assert tour.log[0] == ("gui back",) and tour.log[-1] == ("gui front",) and ("close", "the inventory") in tour.log        # back before the first look, front after the backpack is closed
    tour.log.clear()
    monkeypatch.setattr(macro, "detect_backpack_open", lambda img: False)
    macro.inventory_tour()
    assert tour.log == [("gui back",), ("press", macro.cfg.KEYS["backpack"]), ("gui front",)]
    tour.log.clear()
    monkeypatch.setattr(macro, "focus_roblox", lambda: False)
    macro.inventory_tour()
    assert tour.log == [("gui back",), ("gui front",)]


def test_two_pictures_of_the_panel_are_the_same_when_their_grey_levels_hardly_differ(macro):
    import numpy as np
    flat = np.full((10, 10, 3), 100, np.uint8)
    assert macro.same_picture(flat, flat + 0) and macro.same_picture(flat, np.full((10, 10, 3), 100 + int(macro.cfg.INVENTORY_SAME_LEVEL) - 1, np.uint8))
    assert not macro.same_picture(flat, np.full((10, 10, 3), 100 + int(macro.cfg.INVENTORY_SAME_LEVEL) + 5, np.uint8)) and not macro.same_picture(flat, flat[:5])


# --- leaving a minigame ---

def exit_screen(macro, left="red", right="grey", overlay=True):
    """A 1920x1080 frame with the dialog's two buttons in the colours given (BGR drawn from the HSV the detector reads) and, if `overlay`, a marker for the minigame."""
    import numpy as np
    img = np.full((macro.cfg.REFERENCE_HEIGHT, macro.cfg.REFERENCE_WIDTH, 3), 235, np.uint8)         # (a pale scene)
    colours = {"red": (60, 40, 216), "grey": (70, 65, 62), "green": (85, 198, 74)}                         # BGR
    title = macro.load_templates(macro.cfg.STRAY_WINDOW_DIR)["exit_game"]
    left_, top_, *_ = macro.cfg.POPUP_TEMPLATES["exit_game"]["box"]
    img[top_ + 20:top_ + 20 + title.shape[0], left_ + 20:left_ + 20 + title.shape[1]] = title
    for box, name in ((macro.cfg.EXIT_DIALOG_CANCEL_BOX, left), (macro.cfg.EXIT_DIALOG_RIGHT_BOX, right)):
        if name:
            l, t, r, b = box
            img[t:b, l:r] = colours[name]
    return img


def real_frame(name):
    import cv2
    return cv2.imread(os.path.join(os.path.dirname(__file__), "exit_hub", name + ".png"))


def test_real_screenshots_the_exit_dialog_is_found_in_both_states_the_exit_button_marks_a_minigame_and_the_hub_is_told_from_the_rest(macro):
    counting, ready = real_frame("exit_dialog_counting"), real_frame("exit_dialog_ready")
    assert macro.detect_exit_dialog(counting) == "counting" and macro.detect_exit_dialog(ready) == "ready"
    button, hub, hub2 = real_frame("exit_minigame_button"), real_frame("trading_hub_island"), real_frame("trading_hub_island_2")
    assert macro.detect_exit_dialog(button) is None and macro.detect_exit_dialog(hub) is None
    assert all(macro.in_minigame(frame) for frame in (counting, ready, button)) and not macro.in_minigame(hub)
    assert macro.in_trading_hub(hub) and macro.in_trading_hub(hub2)
    assert macro.popup_visible(hub, "backpack_button") and macro.popup_visible(hub2, "backpack_button") and not macro.popup_visible(button, "backpack_button")
    assert not any(macro.in_trading_hub(frame) for frame in (counting, ready, button))


def test_the_exit_dialog_is_found_by_the_colours_of_its_buttons_and_says_whether_it_is_still_counting(macro):
    assert macro.detect_exit_dialog(exit_screen(macro, "red", "grey")) == "counting"
    assert macro.detect_exit_dialog(exit_screen(macro, "red", "green")) == "ready"
    assert macro.detect_exit_dialog(exit_screen(macro, None, "green")) is None                              # no Cancel
    assert macro.detect_exit_dialog(exit_screen(macro, "red", None)) is None                                # nothing at its right
    assert macro.detect_exit_dialog(exit_screen(macro, "green", "green")) is None and macro.detect_exit_dialog(exit_screen(macro, None, None)) is None


@pytest.fixture
def leaving(macro, monkeypatch):
    """exit_minigame() with the game replaced: `frames` is what grab_screen() shows next (the last one stays), `log` the clicks."""
    log, frames = [], []
    monkeypatch.setattr(macro, "focus_roblox", lambda: True)
    monkeypatch.setattr(macro, "wait_interruptible", lambda s: None)
    monkeypatch.setattr(macro, "hover_click", lambda x, y, duration=0: log.append((x, y)))
    monkeypatch.setattr(macro, "log_run_event", lambda text: log.append(text))
    monkeypatch.setattr(macro, "grab_screen", lambda: frames.pop(0) if len(frames) > 1 else frames[0])
    clock = types.SimpleNamespace(now=1000.0)
    monkeypatch.setattr(macro, "time", types.SimpleNamespace(**{**vars(macro.time), "time": lambda: clock.now, "sleep": lambda s: None}))
    monkeypatch.setattr(macro, "wait_interruptible", lambda s: setattr(clock, "now", clock.now + s))
    return types.SimpleNamespace(log=log, frames=frames, in_minigame=[True])


def test_a_minigame_is_left_by_the_button_the_dialog_and_its_green_button_and_nothing_is_clicked_in_the_house(macro, leaving, monkeypatch):
    cfg = macro.cfg
    plain = exit_screen(macro, None, None)
    leaving.frames.extend([plain, plain, exit_screen(macro, "red", "grey"), exit_screen(macro, "red", "grey"), exit_screen(macro, "red", "green"), plain])
    state_of = iter([True, False])                                                                           # (in a minigame at first; the overlay is gone after the click)
    monkeypatch.setattr(macro, "in_minigame", lambda img: next(state_of, False))
    assert macro.exit_minigame() is True
    assert leaving.log == [cfg.EXIT_MINIGAME_POS, cfg.EXIT_GAME_POS, "minigame exited"]
    leaving.log.clear()
    leaving.frames[:] = [plain]
    monkeypatch.setattr(macro, "in_minigame", lambda img: False)                                             # not in one (Exit Home is in the same place): nothing clicked
    assert macro.exit_minigame() is True and leaving.log == []


def test_a_dialog_that_is_up_already_is_not_opened_again_and_a_step_that_does_not_happen_is_said(macro, leaving, monkeypatch):
    cfg = macro.cfg
    plain = exit_screen(macro, None, None)
    ready = exit_screen(macro, "red", "green")
    leaving.frames[:] = [ready, ready, ready, plain]                                                           # (the first look, the wait for the dialog, for the green, and then it is gone)
    monkeypatch.setattr(macro, "in_minigame", lambda img: False)                                             # (no overlay under the dialog is not needed to go on)
    assert macro.exit_minigame() is True and leaving.log == [cfg.EXIT_GAME_POS, "minigame exited"]
    leaving.log.clear()
    leaving.frames[:] = [plain]                                                                              # the button is clicked and no dialog comes
    monkeypatch.setattr(macro, "in_minigame", lambda img: True)
    assert macro.exit_minigame() is False and leaving.log == [cfg.EXIT_MINIGAME_POS, "exit minigame: the dialog did not come up"]
    leaving.log.clear()
    leaving.frames[:] = [exit_screen(macro, "red", "grey")]                                                  # it counts for ever
    assert macro.exit_minigame() is False and leaving.log == ["exit minigame: the Exit Game button did not turn green"]
    leaving.log.clear()
    leaving.frames[:] = [exit_screen(macro, "red", "green")]                                                 # the minigame stays
    assert macro.exit_minigame() is False and leaving.log == [cfg.EXIT_GAME_POS, "exit minigame: the minigame is still there"]


def test_sloop_leaves_the_hub_and_a_minigame_before_setup_and_one_that_cannot_be_left_is_an_error_with_no_setup(macro, monkeypatch):
    order = []
    monkeypatch.setattr(macro, "leave_hub_if_in", lambda: order.append("hub") or True)
    monkeypatch.setattr(macro, "exit_minigame", lambda: order.append("exit") or True)
    monkeypatch.setattr(macro, "setup_game", lambda: order.append("setup") or True)
    assert macro.exit_minigame_and_setup() is True and order == ["hub", "exit", "setup"]
    order.clear()
    monkeypatch.setattr(macro, "exit_minigame", lambda: order.append("exit") or False)
    with pytest.raises(RuntimeError, match="could not leave the minigame"):
        macro.exit_minigame_and_setup()
    assert order == ["hub", "exit"]
    order.clear()
    monkeypatch.setattr(macro, "leave_hub_if_in", lambda: order.append("hub") or False)
    with pytest.raises(RuntimeError, match="could not leave the Trading Hub"):
        macro.exit_minigame_and_setup()
    assert order == ["hub"]


def test_the_trading_hub_is_left_by_its_button_and_waited_for_until_the_regular_server_has_loaded(macro, leaving, monkeypatch):
    hub, plain = real_frame("trading_hub_island"), exit_screen(macro, None, None)
    world = plain.copy()                                                                                     # the normal UI: the backpack button
    button = macro.load_templates(macro.cfg.STRAY_WINDOW_DIR)["backpack_button"]
    left_, top_, *_ = macro.cfg.POPUP_TEMPLATES["backpack_button"]["box"]
    world[top_ + 5:top_ + 5 + button.shape[0], left_ + 5:left_ + 5 + button.shape[1]] = button
    left = []
    monkeypatch.setattr(macro, "leave_trade_hub", lambda: left.append(True) or True)
    leaving.frames[:] = [plain]
    assert macro.leave_hub_if_in() is True and left == []                                                    # not in it
    leaving.frames[:] = [hub, hub, plain, plain, world]                                                      # the hub goes, a loading screen, then the world
    assert macro.leave_hub_if_in() is True and left == [True]
    left.clear()
    leaving.frames[:] = [hub]                                                                                # it stays
    assert macro.leave_hub_if_in() is False and leaving.log == ["leave the Trading Hub: it is still there"]
    leaving.log.clear()
    leaving.frames[:] = [hub, plain]                                                                         # loading for ever
    assert macro.leave_hub_if_in() is False and leaving.log == ["leave the Trading Hub: the regular server did not load"]
    monkeypatch.setattr(macro, "leave_trade_hub", lambda: False)
    leaving.frames[:] = [hub]
    assert macro.leave_hub_if_in() is False


def test_an_exit_dialog_outside_a_minigame_is_stayed_on_in_either_state_and_nothing_else_with_such_buttons_is(macro, monkeypatch):
    clicks = []
    monkeypatch.setattr(macro, "hover_click", lambda x, y, duration=0: clicks.append((x, y)))
    monkeypatch.setattr(macro.time, "sleep", lambda s: None)
    monkeypatch.setattr(macro, "log_run_event", lambda text: None)
    monkeypatch.setattr(macro, "in_minigame", lambda img: False)
    counting, ready = exit_screen(macro, "red", "grey"), exit_screen(macro, "red", "green")
    monkeypatch.setattr(macro, "grab_screen", lambda: counting)
    assert macro.dismiss_stray_windows() == "exit_dialog" and clicks == [macro.cfg.EXIT_STAY_POS]
    clicks.clear()
    monkeypatch.setattr(macro, "grab_screen", lambda: ready)
    assert macro.dismiss_stray_windows() == "exit_dialog" and clicks == [macro.cfg.EXIT_STAY_POS]
    clicks.clear()
    untitled = exit_screen(macro, "red", "green")                                                            # the same buttons without the title (the teleport's Yes / No): not ours to cancel
    left_, top_, right_, bottom_ = macro.cfg.POPUP_TEMPLATES["exit_game"]["box"]
    untitled[top_:bottom_, left_:right_] = 235
    monkeypatch.setattr(macro, "grab_screen", lambda: untitled)
    assert macro.dismiss_stray_windows() is None and clicks == []
    monkeypatch.setattr(macro, "grab_screen", lambda: counting)
    monkeypatch.setattr(macro, "in_minigame", lambda img: True)                                              # in a minigame: its own exit, not a stray window
    assert macro.dismiss_stray_windows() is None and clicks == []
    monkeypatch.setattr(macro, "in_minigame", lambda img: False)
    macro.state.exit_dialog_wanted = True                                                                    # the macro is leaving on purpose
    assert macro.dismiss_stray_windows() is None and clicks == []
    macro.state.exit_dialog_wanted = False


def test_leaving_a_minigame_on_purpose_keeps_the_stray_window_check_off_the_dialog_and_lets_go_after(macro, leaving, monkeypatch):
    seen = []
    monkeypatch.setattr(macro, "_exit_minigame", lambda: seen.append(macro.state.exit_dialog_wanted) or True)
    assert macro.exit_minigame() is True and seen == [True] and macro.state.exit_dialog_wanted is False
    monkeypatch.setattr(macro, "_exit_minigame", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        macro.exit_minigame()
    assert macro.state.exit_dialog_wanted is False
