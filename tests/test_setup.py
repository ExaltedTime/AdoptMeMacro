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
    world = types.SimpleNamespace(state="world", clicks=[], presses=[], log=[], moves={}, keys={}, frames={"world": np.zeros((1080, 1920, 3), np.uint8)}, equip_tab=[])
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


def filter_moves(macro, game):
    cfg = macro.cfg
    game.keys[("world", cfg.KEYS["backpack"])] = "backpack_pets"
    game.moves.update({("backpack_pets", cfg.SETUP_BACKPACK_SETTINGS_POS): "filters_default", ("filters_default", cfg.SETUP_SORT_MENU_POS): "filters_dropdown",
                       ("filters_dropdown", (1028, 806)): "filters_favorites", ("filters_favorites", cfg.SETUP_CONFIRM_POS): "backpack_pets",
                       ("filters_favorites", cfg.SETUP_FILTER_CANCEL_POS): "backpack_pets", ("filters_default", cfg.SETUP_FILTER_CANCEL_POS): "backpack_pets"})


def test_the_backpack_is_sorted_by_favorites_by_the_sort_menu_and_confirm_and_looked_at_again(macro, game, monkeypatch):
    cfg = macro.cfg
    filter_moves(macro, game)
    game.frames["world"] = frame("house_locked")                                               # (the pet's name bar is over the character: it is out)
    # the first look at the filters shows Default; the second, after Confirm, Favorites
    game.moves[("backpack_pets", cfg.SETUP_BACKPACK_SETTINGS_POS)] = "filters_default"
    real = macro.hover_click

    def click(x, y, duration=0):
        real(x, y, duration)
        if game.state == "backpack_pets" and (x, y) == cfg.SETUP_CONFIRM_POS:
            game.moves[("backpack_pets", cfg.SETUP_BACKPACK_SETTINGS_POS)] = "filters_favorites"            # (it took: the filters say Favorites now)
    monkeypatch.setattr(macro, "hover_click", click)
    assert macro.set_backpack_filter() is True
    assert game.clicks == [cfg.SETUP_BACKPACK_SETTINGS_POS, cfg.SETUP_SORT_MENU_POS, game.clicks[2], cfg.SETUP_CONFIRM_POS, cfg.SETUP_BACKPACK_SETTINGS_POS, cfg.SETUP_FILTER_CANCEL_POS]
    assert abs(game.clicks[2][0] - 1028) <= 6 and abs(game.clicks[2][1] - 806) <= 6                # (Favorites where its text is)
    assert game.presses == [cfg.KEYS["backpack"]] and game.equip_tab == [] and game.log[-1] == ("close", "setup")                 # (the pet is out: nothing equipped)
    assert "setup: the backpack is sorted by Favorites" in texts(game)


def test_a_sort_that_says_favorites_already_is_left_as_it_is(macro, game):
    cfg = macro.cfg
    filter_moves(macro, game)
    game.moves[("backpack_pets", cfg.SETUP_BACKPACK_SETTINGS_POS)] = "filters_favorites"
    game.frames["world"] = frame("house_locked")
    assert macro.set_backpack_filter() is True
    assert game.clicks == [cfg.SETUP_BACKPACK_SETTINGS_POS, cfg.SETUP_FILTER_CANCEL_POS] and "setup: the backpack's sort is Favorites already" in texts(game)


def test_a_sort_that_does_not_change_or_a_menu_without_favorites_is_a_failure_and_the_panel_is_cancelled(macro, game):
    cfg = macro.cfg
    filter_moves(macro, game)
    game.frames["world"] = frame("house_locked")
    game.moves[("filters_dropdown", (1028, 806))] = "filters_dropdown"                           # (the click on Favorites does nothing)
    game.moves[("filters_dropdown", cfg.SETUP_FILTER_CANCEL_POS)] = "backpack_pets"
    assert macro.set_backpack_filter() is False
    assert "setup: the sort did not change to Favorites" in texts(game) and game.clicks[-1] == cfg.SETUP_FILTER_CANCEL_POS
    game.state, game.clicks[:] = "world", []
    game.moves[("filters_default", cfg.SETUP_SORT_MENU_POS)] = "filters_default"                  # (the menu does not open)
    assert macro.set_backpack_filter() is False and "setup: Favorites was not in the sort menu" in texts(game)


def test_the_alicorn_is_found_by_its_picture_and_equipped_only_when_the_pet_is_not_out_and_a_card_that_says_unequip_is_left_alone(macro, game):
    cfg = macro.cfg
    filter_moves(macro, game)
    game.moves[("backpack_pets", cfg.SETUP_BACKPACK_SETTINGS_POS)] = "filters_favorites"
    game.moves[("backpack_pets", (975, 702))] = "pet_card_equipped"                              # (the card of the Alicorn, which says Unequip)
    assert macro.set_backpack_filter() is True                                                  # (the world frame is black: no name bar, the pet is not out)
    assert "setup: the pet is not out - equipping the Alicorn" in texts(game) and "setup: the Alicorn is equipped already" in texts(game)
    assert game.equip_tab == [cfg.INVENTORY_PETS_TAB_POS] and abs(game.clicks[-1][0] - 975) <= 8 and abs(game.clicks[-1][1] - 702) <= 8       # (no Equip clicked)


def test_an_alicorn_that_is_not_equipped_is_equipped_by_its_green_button_and_the_card_must_say_unequip_after(macro, game):
    game.state = "backpack_pets"
    game.moves[("backpack_pets", (975, 702))] = "toys_card"                                      # (a card with a green Equip button, as a frame of the trade phone's has)
    game.moves[("toys_card", (1072, 857))] = "pet_card_equipped"
    assert macro.equip_favorite_pet() is True and abs(game.clicks[-1][0] - 1072) <= 4 and abs(game.clicks[-1][1] - 857) <= 4
    assert "setup: the Alicorn is equipped" in texts(game)
    game.state, game.clicks[:] = "backpack_pets", []
    game.moves[("toys_card", (1072, 857))] = "toys_card"                                         # (Equip does nothing)
    assert macro.equip_favorite_pet() is False and "setup: the Alicorn's card does not say Unequip after Equip" in texts(game)
    game.state = "world"                                                                          # (no Alicorn in the grid)
    assert macro.equip_favorite_pet() is False and "setup: the Alicorn was not seen in the pets tab" in texts(game)


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
    monkeypatch.setattr(macro, "lock_house", lambda: order.append("lock") or True)
    monkeypatch.setattr(macro, "set_backpack_filter", lambda: order.append("filter") or False)
    monkeypatch.setattr(macro, "disable_trades", lambda: order.append("trades") or True)
    monkeypatch.setattr(macro, "log_failure", lambda text: failures.append(text))
    monkeypatch.setattr(macro, "log_run_event", lambda text: None)
    assert macro._setup_steps() is False
    assert order == ["respawn", "lock", "filter", "trades"] and failures == ["setup: the backpack is not filtered to favorites"]       # (every step ran, the failure has a screenshot)
    monkeypatch.setattr(macro, "set_backpack_filter", lambda: True)
    assert macro._setup_steps() is True


def test_setup_from_outside_is_an_error_when_a_step_did_not_take(macro, monkeypatch):
    monkeypatch.setattr(macro, "setup_game", lambda: False)
    with pytest.raises(RuntimeError, match="setup did not take"):
        macro.setup_or_fail()
    with pytest.raises(RuntimeError, match="setup did not take"):                                # (and the setup of /sloop, whose loop must not start on it)
        monkeypatch.setattr(macro, "leave_hub_if_in", lambda: True)
        monkeypatch.setattr(macro, "exit_minigame", lambda: True)
        macro.exit_minigame_and_setup()
    monkeypatch.setattr(macro, "setup_game", lambda: True)
    assert macro.setup_or_fail() is True
