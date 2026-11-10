"""Setup (setup_game()): each of its steps looks at the screen first and leaves a button alone when it says what it should, clicks when it does not, and looks again after. The screens are
real frames of a run (tests/setup/, a capture of Setup), the game a small state machine over them."""
import os
import types

import cv2
import numpy as np
import pytest

HERE = os.path.join(os.path.dirname(__file__), "setup")
TRADES = os.path.join(os.path.dirname(__file__), "trades")


def frame(name):
    path = os.path.join(HERE, name + ".png")
    return cv2.imread(path if os.path.exists(path) else os.path.join(TRADES, name + ".png"))


@pytest.fixture
def game(macro, monkeypatch):
    """`state` names the frame grab_screen() shows; a click within 8 px of a position in `moves` ((state, (x, y)): next state), a key in `keys` ((state, key): next state) moves it on."""
    clock = types.SimpleNamespace(now=1000.0)
    world = types.SimpleNamespace(state="world", clicks=[], presses=[], log=[], scrolls=[], moves={}, keys={}, frames={"world": np.zeros((1080, 1920, 3), np.uint8)}, equip_tab=[])
    monkeypatch.setattr(macro, "time", types.SimpleNamespace(**{**vars(macro.time), "time": lambda: clock.now, "sleep": lambda s: None}))
    monkeypatch.setattr(macro, "wait_interruptible", lambda s: setattr(clock, "now", clock.now + s))
    monkeypatch.setattr(macro, "check_running", lambda: None)
    monkeypatch.setattr(macro, "focus_roblox", lambda: True)
    monkeypatch.setattr(macro, "send_macro_window_to_back", lambda: None)
    monkeypatch.setattr(macro, "bring_macro_window_to_front", lambda: None)

    def click(x, y, duration=0):
        world.clicks.append((x, y))
        for (state, (mx, my)), after in world.moves.items():
            if state == world.state and abs(mx - x) <= 8 and abs(my - y) <= 8:
                world.state = after
                break
    monkeypatch.setattr(macro, "hover_click", click)
    monkeypatch.setattr(macro, "grab_screen", lambda: world.frames.get(world.state) if world.state in world.frames else world.frames.setdefault(world.state, frame(world.state)))

    def press(key, **k):
        world.presses.append(key)
        world.state = world.keys.get((world.state, key), world.state)
    monkeypatch.setattr(macro.pydirectinput, "press", press, raising=False)
    monkeypatch.setattr(macro, "backpack_on_purpose", lambda: __import__("contextlib").nullcontext())
    monkeypatch.setattr(macro, "close_backpack", lambda step: world.log.append(("close", step)))
    monkeypatch.setattr(macro, "select_backpack_tab", lambda pos: world.equip_tab.append(pos))
    monkeypatch.setattr(macro, "log_run_event", lambda text: world.log.append(text))
    return world


def texts(game):
    return [entry for entry in game.log if isinstance(entry, str)]


def test_a_house_that_says_lock_is_locked_by_a_click_one_that_says_unlock_is_left_alone_and_one_that_does_not_lock_is_said(macro, game):
    cfg = macro.cfg
    game.moves[("house_locking", cfg.SETUP_LOCK_HOUSE_POS)] = "house_locked"
    game.state = "house_locking"
    assert macro.lock_house() is True and game.clicks == [cfg.SETUP_LOCK_HOUSE_POS] and texts(game) == ["setup: the house is locked"]
    game.clicks.clear()
    game.log.clear()
    assert macro.lock_house() is True and game.clicks == [] and texts(game) == ["setup: the house is locked already"]              # (Unlock is up: leave it)
    game.state = "house_locking"
    game.moves.clear()                                                                         # (the click does nothing)
    assert macro.lock_house() is False and "setup: the house did not lock" in texts(game)
    game.state = "world"
    assert macro.lock_house() is False and "setup: the house's lock button was not seen" in texts(game)


EGG_BOX, FULL_GROWN_BOX = (930, 793), (1014, 793)               # the middles of the two boxes in the real frames (the Age rows scrolled into view)


FIRST_PET = (975, 706)                                                      # (the first cell of the pets grid that can be picked, on the real frames: right of the green Add tile)


class Backpack:
    """The backpack's FILTERS panel and pets tab as the game has them, over real frames: the panel's edits are kept until Confirm (Cancel drops them), a wheel turn scrolls it to the Age rows,
    a click toggles a box, the sort menu sets Favorites; the pets tab's first pet has a card with Equip (or Unequip when it is out). `stuck` names clicks that do nothing."""
    def __init__(self, macro, game, monkeypatch, sort="default", egg=True, full_grown=True, pet_out=False, stuck=(), notches=1, grid="pets_grid", start_turned=0):
        self.grid = grid                                                                                        # (the frame of the pets tab: the first pet of "pets_grid" can be picked, "backpack_pets" has the Alicorn)
        self.cfg, self.game = macro.cfg, game
        self.applied = {"sort": sort, "egg": egg, "fg": full_grown}
        self.pending, self.open, self.menu, self.notches, self.turned = dict(self.applied), False, False, notches, start_turned          # (the panel keeps where it was scrolled to between openings: `turned` notches down)
        self.pet_out, self.card, self.stuck = pet_out, None, set(stuck)
        unticked = frame("filters_age_full_grown_off")
        self.empty = unticked[793 - 14:793 + 14, 1014 - 14:1014 + 14].copy()
        monkeypatch.setattr(macro, "grab_screen", self.grab)
        monkeypatch.setattr(macro, "hover_click", self.click)
        monkeypatch.setattr(macro, "hover_move", lambda x, y, duration=0: None)
        monkeypatch.setattr(macro, "scroll_down", self.scroll)
        monkeypatch.setattr(macro, "scroll_up", self.scroll_up)
        game.keys[("world", macro.cfg.KEYS["backpack"])] = "backpack"

    def grab(self):
        if self.game.state == "world":
            return self.game.frames["world"]
        if not self.open:
            return frame("pet_card_equipped" if self.card == "unequip" else "pet_card_equip" if self.card == "equip" else self.grid)
        if self.scrolled:
            img = frame("filters_age_both_ticked").copy()
            for key, (x, y) in (("egg", EGG_BOX), ("fg", FULL_GROWN_BOX)):
                if not self.pending[key]:
                    img[y - 14:y + 14, x - 14:x + 14] = self.empty
            return img
        return frame("filters_dropdown" if self.menu else "filters_favorites" if self.pending["sort"] == "favorites" else "filters_default")

    def near(self, x, y, pos, slack=10):
        return abs(x - pos[0]) <= slack and abs(y - pos[1]) <= slack

    def click(self, x, y, duration=0):
        self.game.clicks.append((x, y))
        cfg = self.cfg
        if self.near(x, y, cfg.SETUP_BACKPACK_SETTINGS_POS) and "settings" not in self.stuck:                  # (a panel opened again shows what the game has, not edits that were never confirmed)
            self.open, self.pending, self.menu = True, dict(self.applied), False
        elif self.open and self.near(x, y, cfg.SETUP_SORT_MENU_POS) and not self.scrolled and "menu" not in self.stuck:
            self.menu = True
        elif self.open and self.menu and self.near(x, y, (1028, 806)) and "favorites" not in self.stuck:
            self.pending["sort"], self.menu = "favorites", False
        elif self.open and self.scrolled and self.near(x, y, EGG_BOX) and "egg" not in self.stuck:
            self.pending["egg"] = not self.pending["egg"]
        elif self.open and self.scrolled and self.near(x, y, FULL_GROWN_BOX) and "fg" not in self.stuck:
            self.pending["fg"] = not self.pending["fg"]
        elif self.open and self.near(x, y, cfg.SETUP_CONFIRM_POS) and "confirm" not in self.stuck:
            self.applied, self.open = dict(self.pending), False
        elif self.open and self.near(x, y, cfg.SETUP_FILTER_CANCEL_POS):
            self.open = False
        elif not self.open and self.near(x, y, FIRST_PET):
            self.card = "unequip" if self.pet_out else "equip"
        elif not self.open and self.card == "equip" and 1000 <= x <= 1120 and 795 <= y <= 835 and "equip" not in self.stuck:
            self.pet_out, self.card = True, "unequip"

    @property
    def scrolled(self):
        return self.open and self.turned >= self.notches

    def scroll(self, clicks):
        self.game.scrolls.append(("down", clicks))
        self.turned += clicks

    def scroll_up(self, clicks):
        self.game.scrolls.append(("up", clicks))
        self.turned = max(0, self.turned - clicks)


def settings_clicks(game, cfg):
    return [c for c in game.clicks if c == cfg.SETUP_BACKPACK_SETTINGS_POS]


def test_the_backpack_is_sorted_by_favorites_with_eggs_and_full_grown_pets_filtered_out_confirmed_and_looked_at_again(macro, game, monkeypatch):
    cfg = macro.cfg
    backpack = Backpack(macro, game, monkeypatch, notches=2)
    game.frames["world"] = frame("house_locked")                                               # (the pet's name bar is over the character: it is out, but the list's first pet is what is wanted)
    assert macro.set_backpack_filter() is True
    assert backpack.applied == {"sort": "favorites", "egg": False, "fg": False}               # (both unticked, in one Confirm)
    clicks = game.clicks
    assert clicks[:2] == [cfg.SETUP_BACKPACK_SETTINGS_POS, cfg.SETUP_SORT_MENU_POS] and abs(clicks[2][0] - 1028) <= 6 and abs(clicks[2][1] - 806) <= 6        # (Favorites where its text is)
    assert [c for c in clicks if abs(c[0] - EGG_BOX[0]) <= 3 and abs(c[1] - EGG_BOX[1]) <= 3] and [c for c in clicks if abs(c[0] - FULL_GROWN_BOX[0]) <= 3 and abs(c[1] - FULL_GROWN_BOX[1]) <= 3]
    assert clicks.count(cfg.SETUP_CONFIRM_POS) == 1 and len(settings_clicks(game, cfg)) == 2 and clicks[clicks.index(cfg.SETUP_CONFIRM_POS) + 1] == cfg.SETUP_BACKPACK_SETTINGS_POS      # (reopened after Confirm)
    assert game.scrolls and set(game.scrolls) == {("down", 1), ("up", 1)}                  # (a notch at a time, until the rows are in view; back up to the sort when the panel is opened again to be looked at)
    assert "setup: the backpack's filters are set" in texts(game) and cfg.FILTERS_SCROLL_TRIES >= backpack.notches
    assert backpack.card == "unequip" and backpack.pet_out and FIRST_PET in game.clicks                                 # (the first pet of the list is equipped)
    assert game.log[-1] == ("close", "setup")


def test_filters_that_are_right_already_are_left_as_they_are_and_cancelled(macro, game, monkeypatch):
    cfg = macro.cfg
    backpack = Backpack(macro, game, monkeypatch, sort="favorites", egg=False, full_grown=False, pet_out=True)
    assert macro.set_backpack_filter() is True
    assert cfg.SETUP_CONFIRM_POS not in game.clicks and game.clicks.count(cfg.SETUP_FILTER_CANCEL_POS) == 1 and "setup: the backpack's filters are as they should be already" in texts(game)
    assert backpack.applied == {"sort": "favorites", "egg": False, "fg": False}
    assert "setup: the first pet of the list is equipped already" in texts(game)                  # (its card says Unequip: Equip is not pressed)
    only_full_grown = Backpack(macro, game, monkeypatch, sort="favorites", egg=False, full_grown=True, pet_out=True)
    game.clicks.clear()
    assert macro.set_backpack_filter() is True and only_full_grown.applied["fg"] is False and not [c for c in game.clicks if abs(c[0] - EGG_BOX[0]) <= 3 and abs(c[1] - EGG_BOX[1]) <= 3]       # (a box that is unticked is not clicked)


def test_a_sort_a_box_or_a_confirm_that_does_not_take_is_a_failure_and_the_panel_is_cancelled(macro, game, monkeypatch):
    cfg = macro.cfg
    for stuck, said in (("favorites", "setup: the sort did not change to Favorites"), ("menu", "setup: Favorites was not in the sort menu"), ("egg", "egg box did not untick"),
                        ("fg", "age_full_grown box did not untick")):
        game.clicks.clear(), game.log.clear()
        backpack = Backpack(macro, game, monkeypatch, stuck=(stuck,))
        game.state = "world"
        assert macro.set_backpack_filter() is False, stuck
        assert any(said in text or text.endswith(said) or said.replace("egg box", "age_egg box") in text for text in texts(game)), (stuck, texts(game))
        assert cfg.SETUP_CONFIRM_POS not in game.clicks and game.clicks.count(cfg.SETUP_FILTER_CANCEL_POS) == 1 and backpack.applied == {"sort": "default", "egg": True, "fg": True}      # (nothing confirmed: nothing changed)
    game.clicks.clear(), game.log.clear()
    backpack = Backpack(macro, game, monkeypatch, stuck=("confirm",))
    game.state = "world"
    assert macro.set_backpack_filter() is False and "setup: the backpack's filters are not as set after Confirm" in texts(game)
    game.clicks.clear(), game.log.clear()
    Backpack(macro, game, monkeypatch, notches=99)                                      # (the Age rows never come into view)
    game.state = "world"
    assert macro.set_backpack_filter() is False and "setup: the filters' Age rows were not found" in texts(game) and len(game.scrolls) >= cfg.FILTERS_SCROLL_TRIES


def test_with_the_option_off_the_age_filter_and_the_pet_are_left_and_the_alicorn_is_equipped_only_when_no_pet_is_out(macro, game, monkeypatch):
    macro.update_game_config(lambda config: config.__setitem__("level_growing_pet", False))
    backpack = Backpack(macro, game, monkeypatch, sort="favorites", grid="backpack_pets")
    game.frames["world"] = frame("house_locked")                                               # (a pet's name bar: it is out)
    assert macro.set_backpack_filter() is True and game.scrolls == [] and backpack.applied == {"sort": "favorites", "egg": True, "fg": True}
    assert FIRST_PET not in game.clicks and not any("Alicorn" in text for text in texts(game))
    game.frames["world"] = np.zeros((1080, 1920, 3), np.uint8)                                  # (no pet out: the Alicorn is looked for by its picture)
    game.clicks.clear(), game.log.clear()
    game.state = "world"
    macro.set_backpack_filter()
    assert "setup: the pet is not out - equipping the Alicorn" in texts(game) and "setup: the Alicorn is equipped" in texts(game)         # (the grid of the frame has it)


def test_a_pet_is_equipped_by_its_green_button_and_the_card_must_say_unequip_after_and_no_card_is_said(macro, game, monkeypatch):
    backpack = Backpack(macro, game, monkeypatch)
    game.state = "backpack"
    assert macro.equip_pet("the first pet of the list", lambda: FIRST_PET) is True and backpack.pet_out
    assert "setup: the first pet of the list is equipped" in texts(game)
    Backpack(macro, game, monkeypatch, stuck=("equip",))
    assert macro.equip_pet("a pet", lambda: FIRST_PET) is False and "setup: the card of a pet does not say Unequip after Equip" in texts(game)
    assert macro.equip_pet("a pet", lambda: None) is False and "setup: a pet was not seen in the pets tab" in texts(game)
    Backpack(macro, game, monkeypatch)
    assert macro.equip_pet("the first pet of the list", lambda: (5, 5)) is False and "no pet there?" in texts(game)[-1]         # (nothing under the click: no card comes up)


def trades_moves(macro, game):
    cfg = macro.cfg
    game.moves.update({("world", cfg.SETUP_TRADES_SETTINGS_POS): "settings_main", ("settings_main", cfg.SETUP_TRADES_MENU_POS): "settings_dropdown",
                       ("settings_dropdown", (1026, 433)): "settings_interaction", ("settings_interaction", cfg.SETUP_TRADES_SETTING_POS): "trading_dropdown",
                       ("trading_dropdown", (1035, 601)): "trading_no_one", ("trading_no_one", cfg.SETUP_TRADES_CLOSE_POS): "settings_closed",
                       ("settings_interaction", cfg.SETUP_TRADES_CLOSE_POS): "settings_closed", ("settings_main", cfg.SETUP_TRADES_CLOSE_POS): "settings_closed"})


def test_trades_are_set_to_no_one_through_the_settings_and_looked_at_after(macro, game):
    cfg = macro.cfg
    trades_moves(macro, game)
    assert macro.disable_trades() is True
    assert game.clicks[0] == cfg.SETUP_TRADES_SETTINGS_POS and game.clicks[1] == cfg.SETUP_TRADES_MENU_POS and game.clicks[-1] == cfg.SETUP_TRADES_CLOSE_POS and game.state == "settings_closed"
    assert abs(game.clicks[2][0] - 1026) <= 6 and abs(game.clicks[2][1] - 433) <= 6 and abs(game.clicks[4][1] - 601) <= 6                # (the entries found by their text)
    assert "setup: trading is set to No One" in texts(game)


def test_a_trading_setting_that_says_no_one_already_is_left_alone_and_one_that_does_not_take_is_a_failure(macro, game):
    cfg = macro.cfg
    trades_moves(macro, game)
    game.state = "trading_no_one"                                                               # (the settings are up on Interaction, Trading says No One)
    assert macro.disable_trades() is True and game.clicks == [cfg.SETUP_TRADES_CLOSE_POS] and "setup: trading says No One already" in texts(game)
    game.state, game.clicks[:] = "world", []
    game.moves[("trading_dropdown", (1035, 601))] = "trading_dropdown"                            # (the click on No One does nothing)
    game.moves[("trading_dropdown", cfg.SETUP_TRADES_CLOSE_POS)] = "settings_closed"
    assert macro.disable_trades() is False and "setup: trading does not say No One after the click" in texts(game)
    game.state, game.clicks[:] = "world", []
    game.moves[("world", cfg.SETUP_TRADES_SETTINGS_POS)] = "world"                                 # (the settings do not open)
    assert macro.disable_trades() is False and "setup: the game's settings did not open" in texts(game)


def test_setup_runs_its_steps_after_the_respawn_and_says_which_did_not_take(macro, monkeypatch):
    order, failures = [], []
    monkeypatch.setattr(macro, "dismiss_stray_windows", lambda: None)
    monkeypatch.setattr(macro, "respawn_character", lambda: order.append("respawn"))
    monkeypatch.setattr(macro, "check_pet_pen", lambda: order.append("pen") or True)
    monkeypatch.setattr(macro, "lock_house", lambda: order.append("lock") or True)
    monkeypatch.setattr(macro, "set_backpack_filter", lambda: order.append("filter") or False)
    monkeypatch.setattr(macro, "disable_trades", lambda: order.append("trades") or True)
    monkeypatch.setattr(macro, "log_failure", lambda text: failures.append(text))
    monkeypatch.setattr(macro, "log_run_event", lambda text: None)
    assert macro._setup_steps() is False
    assert order == ["pen", "lock", "filter", "trades"] and failures == ["setup: the backpack is not filtered to favorites"]       # (every step ran, the failure has a screenshot)
    monkeypatch.setattr(macro, "set_backpack_filter", lambda: True)
    assert macro._setup_steps() is True


def test_setup_respawns_once_before_the_pens_walk_and_once_after_it_not_three_times(macro, monkeypatch):
    """The pen's walk starts with a respawn and ends with one back in the house: Setup's own respawn in front of them was a third, for nothing."""
    order = []
    monkeypatch.setattr(macro, "dismiss_stray_windows", lambda: None)
    monkeypatch.setattr(macro, "respawn_character", lambda: order.append("respawn"))
    monkeypatch.setattr(macro, "switch_pen_pets", lambda: order.append("pen walk") or True)
    monkeypatch.setattr(macro, "lock_house", lambda: True)
    monkeypatch.setattr(macro, "set_backpack_filter", lambda: True)
    monkeypatch.setattr(macro, "disable_trades", lambda: True)
    monkeypatch.setattr(macro, "log_run_event", lambda text: None)
    macro.update_game_config(lambda c: c.update(pet_pen_enabled=True))
    assert macro._setup_steps() is True and order == ["pen walk", "respawn"]                    # (switch_pen_pets() starts with open_pet_pen()'s own respawn, not stubbed here)
    order.clear()
    macro.update_game_config(lambda c: c.update(pet_pen_enabled=False))
    assert macro._setup_steps() is True and order == ["respawn"]                                # (no pen: the one respawn is Setup's own)


def test_setup_from_outside_is_an_error_when_a_step_did_not_take(macro, monkeypatch):
    monkeypatch.setattr(macro, "setup_game", lambda: False)
    with pytest.raises(RuntimeError, match="setup did not take"):
        macro.setup_or_fail()
    with pytest.raises(RuntimeError, match="setup did not take"):                                # (and the setup of /sloop, whose loop must not start on it)
        monkeypatch.setattr(macro, "leave_hub_if_in", lambda: True)
        macro.leave_hub_and_setup()
    monkeypatch.setattr(macro, "setup_game", lambda: True)
    assert macro.setup_or_fail() is True


def test_a_pen_that_could_not_be_checked_is_said_and_does_not_fail_setup(macro, monkeypatch):
    log = []
    monkeypatch.setattr(macro, "dismiss_stray_windows", lambda: None)
    monkeypatch.setattr(macro, "respawn_character", lambda: None)
    monkeypatch.setattr(macro, "check_pet_pen", lambda: False)
    monkeypatch.setattr(macro, "lock_house", lambda: True)
    monkeypatch.setattr(macro, "set_backpack_filter", lambda: True)
    monkeypatch.setattr(macro, "disable_trades", lambda: True)
    monkeypatch.setattr(macro, "log_run_event", log.append)
    assert macro._setup_steps() is True and "setup: the pet pen could not be checked" in log


def test_the_pen_is_checked_as_a_step_of_setup_unless_it_is_switched_off(macro, monkeypatch):
    switched, respawned = [], []
    monkeypatch.setattr(macro, "switch_pen_pets", lambda: switched.append(1) or True)
    monkeypatch.setattr(macro, "respawn_character", lambda: respawned.append(1))
    macro.update_game_config(lambda c: c.update(pet_pen_enabled=False, pet_pen_swap=True))
    assert macro.check_pet_pen() is True and switched == [] and respawned == []
    macro.update_game_config(lambda c: c.update(pet_pen_enabled=True))
    assert macro.check_pet_pen() is True and switched == [1] and respawned == [1]                 # (back in the house after it)
    assert macro.load_game_config()["pet_pen_swap"] is False and macro.load_game_config()["pet_pen_swap_tried"] > 0
    monkeypatch.setattr(macro, "switch_pen_pets", lambda: False)
    macro.update_game_config(lambda c: c.update(pet_pen_swap=True))
    assert macro.check_pet_pen() is False and macro.load_game_config()["pet_pen_swap"] is True


def test_the_pet_to_level_is_not_the_add_tile_a_pet_in_the_pen_orange_framed_or_a_grey_one_and_the_equipped_one_is_kept(macro):
    real = frame("pets_tab_pen_and_equipped")
    states = [[macro.pet_cell_state(real, row, column) for column in range(4)] for row in range(3)]
    assert states == [["plus", "equipped", "pen", "pen"], ["pen", "pen", "pet", "pet"], ["pet", "pet", "pet", "pet"]]
    assert macro.first_listed_cell(real, stop_at_equipped=True) == ("equipped", (975, 706))                   # (the pet that is out comes first: kept)
    assert macro.first_listed_cell(real) == ("pet", (1055, 787))                                                # (pen pets and the equipped one skipped)
    assert macro.first_listed_cell(frame("pets_grid"), stop_at_equipped=True) == ("pet", FIRST_PET)


def test_a_pet_with_the_green_frame_anywhere_in_the_filtered_list_is_kept_not_swapped_for_the_first_pet(macro):
    """The list is filtered to pets that still grow: the pet that is out showing in it means that it still grows, whichever cell it is in."""
    real = frame("pets_tab_pen_and_equipped").copy()
    half, xs, ys = macro.cfg.PET_CELL_HALF, macro.cfg.PET_GRID_X, macro.cfg.PET_GRID_Y
    cell = lambda r, c: (slice(ys[r] - half, ys[r] + half), slice(xs[c] - half, xs[c] + half))
    real[cell(2, 3)] = real[cell(0, 1)]                                                           # the equipped cell moves to the last row...
    real[cell(0, 1)] = real[cell(2, 0)]                                                           # ...and a pet that can be picked is first
    assert macro.pet_cell_state(real, 0, 1) == "pet" and macro.pet_cell_state(real, 2, 3) == "equipped"
    assert macro.first_listed_cell(real, stop_at_equipped=True) == ("equipped", (xs[3], ys[2]))
    assert macro.first_listed_cell(real) == ("pet", (xs[1], ys[0]))                               # (the pen's pick, which does not keep the equipped one, takes the first pet)


def test_a_filters_panel_that_opens_scrolled_down_to_the_age_rows_is_scrolled_back_up_to_the_sort_first(macro, game, monkeypatch):
    backpack = Backpack(macro, game, monkeypatch, notches=2, start_turned=2)                    # (the game kept the panel where Setup or the pen left it)
    game.frames["world"] = frame("house_locked")
    assert macro.set_backpack_filter() is True
    assert backpack.applied == {"sort": "favorites", "egg": False, "fg": False}                   # (the sort took: its button was in view when it was clicked)
    assert ("up", 1) in game.scrolls and game.scrolls.index(("up", 1)) < game.scrolls.index(("down", 1))
    stuck = Backpack(macro, game, monkeypatch, notches=2, start_turned=2)
    monkeypatch.setattr(macro, "scroll_up", lambda clicks: None)                                 # (a wheel that does nothing: the panel stays down)
    game.clicks.clear(), game.scrolls.clear(), game.log.clear()
    game.state = "world"
    assert macro.set_backpack_filter() is False and stuck.applied["sort"] == "default"


def test_an_equip_that_took_is_seen_by_the_green_frame_of_the_cell_when_the_card_has_closed(macro, monkeypatch):
    after = frame("pets_tab_after_equip")                                                       # (a 10-hour run: the card is gone after Equip and the porcupine has the green frame)
    assert macro.cell_at_is_equipped(after, (895, 787)) is True and macro.cell_at_is_equipped(after, (975, 787)) is False
    assert macro.cell_at_is_equipped(after, (100, 100)) is False
    card, clicks = frame("pet_card_equip"), []
    frames = [frame("pets_grid"), card, after]
    monkeypatch.setattr(macro, "select_backpack_tab", lambda pos: None)
    monkeypatch.setattr(macro, "wait_interruptible", lambda s: None)
    monkeypatch.setattr(macro, "hover_click", lambda x, y, duration=0: clicks.append((x, y)))
    monkeypatch.setattr(macro, "grab_screen", lambda: frames.pop(0) if len(frames) > 1 else frames[0])
    monkeypatch.setattr(macro, "find_equip_button", lambda img: (1060, 815))
    monkeypatch.setattr(macro, "log_run_event", lambda text: None)
    assert macro.equip_pet("a pet", lambda: (895, 787)) is True and clicks == [(895, 787), (1060, 815)]


def test_the_pets_tab_is_selected_before_the_filters_age_is_touched(macro, game, monkeypatch):
    """The backpack opens on the tab it was last left on (a vehicle's after a ride): the Age rows are the pets tab's, so the tab is clicked first and looked at."""
    cfg = macro.cfg
    Backpack(macro, game, monkeypatch, notches=2)
    game.frames["world"] = frame("house_locked")
    selected = []
    monkeypatch.setattr(macro, "is_backpack_tab_selected", lambda pos: bool(selected))
    original = macro.hover_click
    monkeypatch.setattr(macro, "hover_click", lambda x, y, duration=0: (selected.append(1) if (x, y) == cfg.INVENTORY_PETS_TAB_POS else None) or original(x, y, duration))
    assert macro.set_backpack_filter() is True
    assert game.clicks[0] == cfg.INVENTORY_PETS_TAB_POS and cfg.SETUP_BACKPACK_SETTINGS_POS in game.clicks[1:2]


def test_a_backpack_whose_pets_tab_cannot_be_selected_is_not_filtered_by_age(macro, game, monkeypatch):
    backpack = Backpack(macro, game, monkeypatch, notches=2)
    game.frames["world"] = frame("house_locked")
    monkeypatch.setattr(macro, "is_backpack_tab_selected", lambda pos: False)
    assert macro.set_backpack_filter() is False
    assert "setup: the backpack's pets tab could not be selected" in texts(game)
    assert backpack.applied["egg"] is True and backpack.applied["fg"] is True                  # (the Age boxes were left alone)
    assert not game.equip_tab                                                                  # (and no pet was picked from the list)


def test_the_equip_button_is_found_when_the_card_opens_to_the_right_of_a_pet_in_the_last_column(macro):
    """A run's repick said the card had no Equip button five times: the pet was in the fourth column, its card opens further right, and the button was outside the box it was looked for in (a real frame of it)."""
    x, y = macro.find_equip_button(frame("pets_card_equip_far"))
    assert abs(x - 1220) <= 12 and abs(y - 805) <= 12
    assert macro.find_equip_button(frame("pets_grid")) is None                                  # (the green plus tile of the list is no Equip button)


def test_a_pet_the_game_called_full_grown_is_not_kept_by_the_pick_even_if_it_still_shows_in_the_list(macro, monkeypatch):
    """Run 6: after "Yay! Your pet is full grown!" the repick found the equipped pet in the filtered list and kept it. The notice says to pick another."""
    seen = []
    monkeypatch.setattr(macro, "select_backpack_tab", lambda pos: None)
    monkeypatch.setattr(macro, "wait_interruptible", lambda s: None)
    monkeypatch.setattr(macro, "log_run_event", lambda text: None)
    monkeypatch.setattr(macro, "choose_list_pet", lambda stop_at_equipped=False: seen.append(stop_at_equipped) or ("pet", (975, 787)))
    monkeypatch.setattr(macro, "equip_pet", lambda what, where: seen.append(("equip", where())) or True)
    macro.update_game_config(lambda c: c.update(pet_outgrown=False))
    macro.pick_pet_in_open_backpack(True, True, True)
    assert seen[0] is True                                                                     # (an ordinary pick keeps the equipped pet)
    seen.clear()
    macro.update_game_config(lambda c: c.update(pet_outgrown=True))
    macro.pick_pet_in_open_backpack(True, True, True)
    assert seen[0] is False and ("equip", (975, 787)) in seen and macro.load_game_config()["pet_outgrown"] is False      # (not kept this time, and asked once)


def test_the_effect_and_rarity_rows_of_the_filters_are_found_with_their_ticks_in_their_own_columns(macro):
    cfg = macro.cfg
    top = frame("filters_top")
    for row in (*cfg.FILTER_EFFECT_ROWS, *cfg.FILTER_RARITY_ROWS):
        found = macro.filter_checkbox(top, row)
        assert found is not None and found[1] is True, row                                      # (every row is in view and ticked on the real frame)
    lower = frame("filters_age_both_ticked")
    assert macro.filter_checkbox(lower, "filter_rarity_rare") is None                           # ("Rare" is also the end of "Ultra Rare", a column to the left: not that row)
    assert macro.filter_checkbox(lower, "filter_rarity_ultra_rare")[1] is True


def test_a_neon_task_leaves_only_the_neon_effects_ticked_and_a_common_one_only_common_and_only_the_rows_that_differ_are_clicked(macro, monkeypatch):
    cfg = macro.cfg
    assert {k for k, v in macro.task_pet_rows("neon").items() if v} == set(cfg.FILTER_NEON_ROWS) and set(macro.task_pet_rows("neon")) == set(cfg.FILTER_EFFECT_ROWS)
    assert {k for k, v in macro.task_pet_rows("common").items() if v} == {cfg.FILTER_COMMON_ROW} and set(macro.task_pet_rows("common")) == set(cfg.FILTER_RARITY_ROWS)
    assert all(macro.task_pet_rows("all").values())
    state = {row: True for row in (*cfg.FILTER_EFFECT_ROWS, *cfg.FILTER_RARITY_ROWS)}
    position = {row: (100 + i, 200) for i, row in enumerate(state)}
    monkeypatch.setattr(macro, "grab_screen", lambda: None)
    monkeypatch.setattr(macro, "filter_checkbox", lambda img, label: (position[label], state[label]))
    monkeypatch.setattr(macro, "wait_interruptible", lambda s: None)
    monkeypatch.setattr(macro, "scroll_filters_to_top", lambda: True)
    clicked = []
    def click(x, y, duration=0):
        row = next(r for r, p in position.items() if p == (x, y))
        clicked.append(row)
        state[row] = not state[row]
    monkeypatch.setattr(macro, "hover_click", click)
    assert macro.set_filter_rows(macro.task_pet_rows("neon")) is True
    assert sorted(clicked) == sorted(set(cfg.FILTER_EFFECT_ROWS) - set(cfg.FILTER_NEON_ROWS))    # (the four others unticked, the two neon rows left)
    clicked.clear()
    assert macro.set_filter_rows(macro.task_pet_rows("neon")) is True and clicked == []          # (already so: nothing clicked)
    assert macro.set_filter_rows(macro.task_pet_rows("all")) is True and len(clicked) == 4        # (and back)


def test_neon_is_found_in_its_own_column_though_the_end_of_mega_neon_matches_as_well_as_it_does(macro):
    """The CI failed on this at random: the crop of "Neon" matches the end of "Mega Neon" exactly as well (1.0 on the same frame), and which of the two the best match was came down to the CPU's rounding."""
    top = frame("filters_top")
    band = top[640:880, 800:1130]
    crop = macro.load_templates(macro.cfg.STRAY_WINDOW_DIR)["filter_effect_neon"]
    scores = cv2.matchTemplate(band, crop, cv2.TM_CCOEFF_NORMED)
    assert int((scores >= 0.99).sum()) >= 2                                                      # (two places match: the row's own and the end of Mega Neon)
    (x, y), ticked = macro.filter_checkbox(top, "filter_effect_neon")
    assert abs(x - 931) <= 3 and abs(y - 749) <= 3 and ticked is True
    (x, y), _ = macro.filter_checkbox(top, "filter_effect_mega_neon")
    assert abs(x - 1015) <= 3
