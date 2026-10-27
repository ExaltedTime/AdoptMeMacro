"""The trade listings through the Trading Hub Phone (trades_tour(), decline_offer()): the screens are real frames of a human's run (tests/trades/, only the phone's screen and
the hotbar kept), the game is a small state machine over them."""
import json
import os
import types

import cv2
import numpy as np
import pytest

HERE = os.path.join(os.path.dirname(__file__), "trades")


def frame(name):
    return cv2.imread(os.path.join(HERE, name + ".png"))


NOTCH = 95                                                                                     # (px one notch of the wheel moves the list, from the pictures of a run)
LEFT, RIGHT = 640, 1262


def list_frame(offers, offset, rows):
    """The real offers frame with its list moved up by `offset` px: `rows` offers that look exactly alike (the first row of the frame, again and again: what taken for each other by their pictures
    was the bug), 136 px apart."""
    block = offers[399:535, LEFT:RIGHT]
    out = offers.copy()
    content = np.vstack([block] * rows + [np.zeros((336, RIGHT - LEFT, 3), np.uint8)])          # (and nothing under the last)
    out[399:735, LEFT:RIGHT] = content[offset:offset + 336]
    return out


def scrolling_list(macro, game, rows, glide=True):
    """The game's offers list as `rows` alike offers that a step of the wheel moves NOTCH px (not past the last), and that glides: the first look after a step shows it 40 px short of where it stops."""
    offers = frame("offers")
    state = types.SimpleNamespace(offset=0, gliding=False)
    last = max(0, rows * 136 - 336)

    def grab():
        shown = state.offset - 40 if state.gliding and state.offset >= 40 else state.offset
        state.gliding = False
        return list_frame(offers, shown, rows)

    def scroll(clicks):
        game.scrolls.append(("down", clicks))
        state.offset = min(last, state.offset + NOTCH * clicks)
        state.gliding = glide
    macro.grab_screen = grab
    macro.scroll_down = scroll
    return state


@pytest.fixture
def game(macro, monkeypatch, tmp_path):
    """The phone as a state machine: `state` names the frame grab_screen() shows (`frames`), a click moves it as `moves` says ((state, position): the next state); `clicks` is every click."""
    cfg = macro.cfg
    for name, path in (("TRADES_DIR", tmp_path / "trades"), ("TRADES_ROWS_DIR", tmp_path / "trades" / "rows"), ("TRADES_DONE_PATH", tmp_path / "trades" / "done.json"),
                       ("TRADES_DECLINED_PATH", tmp_path / "trades" / "declined.png"), ("TRADES_DECLINE_DONE_PATH", tmp_path / "trades" / "decline.json")):
        monkeypatch.setattr(cfg, name, str(path))
    clock = types.SimpleNamespace(now=1000.0)
    monkeypatch.setattr(macro, "time", types.SimpleNamespace(**{**vars(macro.time), "time": lambda: clock.now, "sleep": lambda s: None}))
    monkeypatch.setattr(macro, "wait_interruptible", lambda s: setattr(clock, "now", clock.now + s))
    hotbar = np.zeros((1080, 1920, 3), np.uint8)
    hotbar[940:1080, 900:1120] = frame("menu")[940:1080, 900:1120]
    world = types.SimpleNamespace(state="world", clicks=[], scrolls=[], moves={}, extra={}, frames={"world": hotbar, "bare": np.zeros((1080, 1920, 3), np.uint8)},
                                  log=[], equipped=0)
    for name in ("phone", "menu", "listing_1", "listing_2", "offers", "detail", "dialog", "declining"):
        world.frames[name] = frame(name)

    def click(x, y, duration=0):
        world.clicks.append((x, y))
        world.state = world.moves.get((world.state, (x, y)), world.state)
    monkeypatch.setattr(macro, "focus_roblox", lambda: True)
    monkeypatch.setattr(macro, "grab_screen", lambda: world.frames[world.state])
    monkeypatch.setattr(macro, "hover_click", click)
    monkeypatch.setattr(macro, "hover_move", lambda x, y, duration=0: world.clicks.append(("move", x, y)))
    monkeypatch.setattr(macro, "scroll_down", lambda clicks: world.scrolls.append(("down", clicks)))
    monkeypatch.setattr(macro, "send_macro_window_to_back", lambda: None)
    monkeypatch.setattr(macro, "bring_macro_window_to_front", lambda: None)
    monkeypatch.setattr(macro, "log_run_event", lambda text: world.log.append(text))
    monkeypatch.setattr(macro, "equip_trade_phone", lambda: setattr(world, "equipped", world.equipped + 1) or setattr(world, "state", "world") or True)
    cfg_moves = {("world", cfg.TRADE_HUB_USE_POS): "phone", ("phone", cfg.TRADES_PHONE_LISTINGS_POS): "menu", ("menu", cfg.TRADES_MENU_VIEW_POS): "listing_1",
                 ("listing_1", cfg.TRADES_NEXT_POS): "listing_2", ("listing_2", cfg.TRADES_NEXT_POS): "listing_1",
                 ("listing_1", cfg.TRADES_OFFERS_POS): "offers", ("listing_2", cfg.TRADES_OFFERS_POS): "offers",
                 ("offers", cfg.TRADES_BACK_POS): "listing_1", ("detail", cfg.TRADES_BACK_POS): "offers", ("dialog", cfg.EXIT_STAY_POS): "detail",
                 ("detail", cfg.TRADES_DETAIL_DECLINE_POS): "dialog", ("dialog", cfg.TRADES_DECLINE_YES_POS): "declining",
                 ("offers", cfg.TRADES_CLOSE_POS): "world", ("listing_1", cfg.TRADES_CLOSE_POS): "world", ("listing_2", cfg.TRADES_CLOSE_POS): "world"}
    world.moves.update(cfg_moves)
    return world


def test_each_screen_of_the_phone_is_told_from_a_real_frame_and_the_listing_from_its_numbers(macro):
    wanted = {"phone": "phone", "menu": "menu", "listing_1": "listings", "listing_2": "listings", "offers": "offers", "detail": "detail", "dialog": "dialog", "declining": "offers"}
    for name, screen in wanted.items():
        assert macro.trade_screen(frame(name)) == screen, name
    assert macro.trade_screen(np.zeros((1080, 1920, 3), np.uint8)) is None
    assert macro.listing_shown(frame("listing_1")) == 1 and macro.listing_shown(frame("listing_2")) == 2
    assert all(macro.listing_shown(frame(name)) is None for name in ("phone", "menu", "offers", "detail"))
    assert macro.popup_visible(frame("menu"), "trade_phone_hotbar") and not macro.popup_visible(np.zeros((1080, 1920, 3), np.uint8), "trade_phone_hotbar")


def test_the_listings_are_known_outside_the_trading_hub_too_where_their_title_is_trading_hub_and_not_my_listings(macro):
    """The player's own screenshot: in the house the listing's window is "TRADING HUB  LISTING 1/2" (what the stray window `trading_hub` closes), and /trades went no further."""
    house = cv2.imread(os.path.join(os.path.dirname(__file__), "popups", "trading_hub.png"))
    assert macro.trade_screen(house) == "listings" and macro.listing_shown(house) == 1
    assert macro.trade_screen(frame("menu")) == "menu" and macro.listing_shown(frame("menu")) is None          # (the menu has the same title and no numbers)


def test_the_rows_whole_in_the_list_are_found_by_their_accept_buttons_and_a_row_is_told_from_its_picture(macro):
    offers = frame("offers")
    assert macro.visible_offers(offers) == pytest.approx([453, 589], abs=3)                  # (the third row is cut off by the list's bottom)
    assert macro.visible_offers(frame("declining")) == pytest.approx([589], abs=3)           # (the one being declined has no ACCEPT)
    assert macro.visible_offers(frame("detail")) == [] and macro.visible_offers(np.zeros((1080, 1920, 3), np.uint8)) == []
    first, second = (macro.offer_row(offers, y) for y in macro.visible_offers(offers))
    assert macro.row_is(offers, 453, first) and macro.row_is(offers, 589, second) and not macro.row_is(offers, 589, first)       # (the second row is not the first, though the same kind of row)
    assert macro.row_is(offers, 453, first[:-1]) is False                                    # (a picture of another size is no match)


def test_the_phone_is_opened_from_the_world_equipping_it_only_when_it_is_not_in_the_hotbar_and_from_a_screen_further_in_it_is_gone_back_from(macro, game):
    cfg = macro.cfg
    assert macro.open_trade_listings() is True and game.state == "listing_1" and game.equipped == 0
    assert game.clicks == [cfg.TRADE_HUB_USE_POS, cfg.TRADES_PHONE_LISTINGS_POS, cfg.TRADES_MENU_VIEW_POS]
    game.clicks.clear()
    game.state = "bare"                                                                      # no phone in the hotbar
    game.moves[("bare", cfg.TRADE_HUB_USE_POS)] = "phone"
    assert macro.open_trade_listings() is True and game.equipped == 1
    for start in ("offers", "detail", "dialog"):
        game.state, game.clicks[:] = start, []
        assert macro.open_trade_listings() is True and game.state == "listing_1", start
    game.state, game.clicks[:] = "dialog", []
    macro.open_trade_listings()
    assert game.clicks[0] == cfg.EXIT_STAY_POS and cfg.TRADES_DECLINE_YES_POS not in game.clicks            # an "Are you sure?" that is up is cancelled, never answered Yes
    game.state, game.log[:] = "world", []
    game.moves[("world", cfg.TRADE_HUB_USE_POS)] = "world"                                   # the phone never comes up
    assert macro.open_trade_listings() is False and game.log == ["trade listings: the phone did not come up"]


def test_the_boards_of_offers_is_known_in_a_real_screenshot_and_closed_before_the_phone_is_used(macro, game):
    """The pink board of the Trading Hub's offers came up and /trades could not go on: it is closed first, by its X (a stray window of the loop too, STRAY_WINDOWS)."""
    cfg = macro.cfg
    board = cv2.imread(os.path.join(os.path.dirname(__file__), "popups", "trade_board.png"))
    assert macro.trade_screen(board) == "board"
    assert all(macro.trade_screen(frame(name)) != "board" for name in ("phone", "menu", "listing_1", "listing_2", "offers", "detail", "dialog", "declining"))
    game.frames["board"] = board
    game.moves[("board", cfg.STRAY_WINDOWS["trade_board"]["close_pos"])] = "world"
    game.state = "board"
    assert macro.open_trade_listings() is True and game.state == "listing_1"
    assert game.clicks[0] == cfg.STRAY_WINDOWS["trade_board"]["close_pos"] and game.clicks[1] == cfg.TRADE_HUB_USE_POS
    game.state, game.clicks[:], game.log[:] = "board", [], []
    game.moves[("board", cfg.STRAY_WINDOWS["trade_board"]["close_pos"])] = "board"                    # it does not close
    assert macro.open_trade_listings() is False and game.log == ["trade listings: the board of offers could not be closed"]


def without_item_match(macro, monkeypatch):
    """The phone's picture is not seen in the grid, so that the search is what finds it."""
    real = macro.popup_found
    monkeypatch.setattr(macro, "popup_found", lambda img, name: None if name == "phone_item" else real(img, name))


def test_the_phone_is_found_by_typing_its_name_in_the_toys_search_clicking_the_first_item_and_the_search_is_cleared_before_the_backpack_is_closed(macro, game, monkeypatch):
    cfg = macro.cfg
    events = []
    monkeypatch.undo()                                                                       # (the fixture's stand-ins are the game's; this one needs its own)
    clock = types.SimpleNamespace(now=1000.0)
    monkeypatch.setattr(macro, "time", types.SimpleNamespace(**{**vars(macro.time), "time": lambda: clock.now, "sleep": lambda s: events.append(("sleep", s))}))
    monkeypatch.setattr(macro, "wait_interruptible", lambda s: setattr(clock, "now", clock.now + s))
    monkeypatch.setattr(macro, "check_running", lambda: None)
    monkeypatch.setattr(macro.pydirectinput, "press", lambda key, **k: events.append(("press", key, macro.state.typing)), raising=False)
    monkeypatch.setattr(macro, "hover_click", lambda x, y, duration=0: events.append(("click", (x, y))))
    monkeypatch.setattr(macro, "focus_roblox", lambda: True)
    monkeypatch.setattr(macro, "close_backpack", lambda step: events.append(("close", step)))
    without_item_match(macro, monkeypatch)
    looks = [frame("menu"), frame("toys_unfiltered"), frame("toys_unfiltered"), frame("toys_unfiltered"), frame("toys_filtered"), frame("toys_card"), frame("menu")]       # (no payday popup; the box has taken the focus (no placeholder in it); the list follows the typed text late: two looks of the old list, then the filtered one; the phone's card after the first item is clicked; at the end the hotbar has its icon)
    def grab():
        events.append(("grab",))
        return looks.pop(0) if len(looks) > 1 else looks[0]
    monkeypatch.setattr(macro, "grab_screen", grab)
    monkeypatch.setattr(macro, "log_run_event", lambda text: events.append(("log", text)))
    assert macro.equip_trade_phone() is True
    first_item = next(i for i, e in enumerate(events) if e == ("click", cfg.TRADE_HUB_PHONE_POS))
    assert [e[0] for e in events[:first_item]].count("grab") == 5                              # (the first item is not clicked before the list is the search's)
    clicks = [e[1] for e in events if e[0] == "click"]
    presses = [e[1] for e in events if e[0] == "press"]
    assert presses == [cfg.KEYS["backpack"], *["backspace"] * 5, *"phone", *["backspace"] * 5]          # (the box emptied first: what a search before left in it)
    equip = (1111 + cfg.TRADES_EQUIP_OFFSET[0], 780 + cfg.TRADES_EQUIP_OFFSET[1])                       # from the middle of the card's name, where the real frame has it
    assert clicks == [cfg.TRADE_HUB_TOYS_TAB_POS, cfg.TRADES_SEARCH_POS, cfg.TRADE_HUB_PHONE_POS, (equip[0], equip[1]), cfg.TRADES_SEARCH_POS, cfg.TRADES_UNFOCUS_POS]
    assert abs(equip[0] - 1073) <= 3 and abs(equip[1] - 855) <= 3                                      # (the Equip button of the real frame)
    assert [e for e in events if e[0] != "grab"][-1] == ("close", "the Trading Hub Phone") and macro.state.typing is False
    typed ={e[1]: e[2] for e in events if e[0] == "press"}
    assert all(typed[key] is True for key in "phone") and typed[cfg.KEYS["backpack"]] is False        # (the hotkeys are left alone while it types: "phone" has the stop key)


@pytest.mark.parametrize("shown, clicked_first, log", [
    ("toys_unfiltered", False, "the search did not leave the phone first"),                 # the list never follows the text: the badge is first, and is not clicked
    ("toys_filtered", True, "the item clicked is not the Trading Hub Phone")])              # the list is the search's but the card that comes up is the badge's (the capture's frame): not the phone, no Equip
def test_the_badge_is_never_equipped_a_search_that_does_not_work_is_tried_again_and_then_nothing_is_equipped(macro, monkeypatch, shown, clicked_first, log):
    cfg = macro.cfg
    events = []
    clock = types.SimpleNamespace(now=1000.0)
    monkeypatch.setattr(macro, "time", types.SimpleNamespace(**{**vars(macro.time), "time": lambda: clock.now, "sleep": lambda s: None}))
    monkeypatch.setattr(macro, "wait_interruptible", lambda s: setattr(clock, "now", clock.now + s))
    monkeypatch.setattr(macro, "check_running", lambda: None)
    monkeypatch.setattr(macro.pydirectinput, "press", lambda key, **k: events.append(("press", key)), raising=False)
    monkeypatch.setattr(macro, "hover_click", lambda x, y, duration=0: events.append(("click", (x, y))))
    monkeypatch.setattr(macro, "focus_roblox", lambda: True)
    monkeypatch.setattr(macro, "close_backpack", lambda step: events.append(("close", step)))
    monkeypatch.setattr(macro, "log_run_event", lambda text: events.append(("log", text)))
    monkeypatch.setattr(macro, "grab_screen", lambda: frame(shown))
    without_item_match(macro, monkeypatch)
    assert macro.equip_trade_phone() is False
    clicks = [e[1] for e in events if e[0] == "click"]
    assert clicks.count(cfg.TRADE_HUB_PHONE_POS) == (cfg.TRADES_EQUIP_TRIES if clicked_first else 0) and clicks.count(cfg.TRADES_SEARCH_POS) == cfg.TRADES_EQUIP_TRIES + 1
    assert not [c for c in clicks if c not in (cfg.TRADE_HUB_TOYS_TAB_POS, cfg.TRADES_SEARCH_POS, cfg.TRADE_HUB_PHONE_POS, cfg.TRADES_UNFOCUS_POS)]        # (no Equip anywhere)
    logs = [e[1] for e in events if e[0] == "log"]
    search = "trade listings: the phone's picture is not in the Toys tab, searching for it"
    assert logs == [search, f"trade listings: {log} - trying again", search, f"trade listings: {log}"]
    assert events[-1] == ("close", "the Trading Hub Phone")                                    # (the search cleared, the backpack closed)


def test_the_phone_is_found_by_its_picture_in_the_grid_and_clicked_where_it_is_without_the_search(macro, monkeypatch):
    cfg = macro.cfg
    events = []
    clock = types.SimpleNamespace(now=1000.0)
    monkeypatch.setattr(macro, "time", types.SimpleNamespace(**{**vars(macro.time), "time": lambda: clock.now, "sleep": lambda s: None}))
    monkeypatch.setattr(macro, "wait_interruptible", lambda s: setattr(clock, "now", clock.now + s))
    monkeypatch.setattr(macro, "check_running", lambda: None)
    monkeypatch.setattr(macro.pydirectinput, "press", lambda key, **k: events.append(("press", key)), raising=False)
    monkeypatch.setattr(macro, "hover_click", lambda x, y, duration=0: events.append(("click", (x, y))))
    monkeypatch.setattr(macro, "focus_roblox", lambda: True)
    monkeypatch.setattr(macro, "close_backpack", lambda step: events.append(("close", step)))
    monkeypatch.setattr(macro, "log_run_event", lambda text: events.append(("log", text)))
    looks = [frame("menu"), frame("toys_unfiltered"), frame("toys_card"), frame("menu")]               # (no popup; the unfiltered grid has the phone second, behind the badge; its card; the hotbar)
    monkeypatch.setattr(macro, "grab_screen", lambda: looks.pop(0) if len(looks) > 1 else looks[0])
    assert macro.equip_trade_phone() is True
    clicks = [e[1] for e in events if e[0] == "click"]
    assert clicks[0] == cfg.TRADE_HUB_TOYS_TAB_POS and clicks[2] == (1111 + cfg.TRADES_EQUIP_OFFSET[0], 780 + cfg.TRADES_EQUIP_OFFSET[1]) and len(clicks) == 3
    assert abs(clicks[1][0] - 975) <= 3 and abs(clicks[1][1] - 743) <= 3                                 # (the second slot, not the badge's first)
    assert [e[1] for e in events if e[0] == "press"] == [cfg.KEYS["backpack"]] and not [e for e in events if e[0] == "log"]


def test_a_payday_popup_over_the_backpack_is_dismissed_first_and_letters_are_typed_only_once_the_box_has_the_focus(macro, monkeypatch):
    cfg = macro.cfg
    events = []
    clock = types.SimpleNamespace(now=1000.0)
    monkeypatch.setattr(macro, "time", types.SimpleNamespace(**{**vars(macro.time), "time": lambda: clock.now, "sleep": lambda s: None}))
    monkeypatch.setattr(macro, "wait_interruptible", lambda s: setattr(clock, "now", clock.now + s))
    monkeypatch.setattr(macro, "check_running", lambda: None)
    monkeypatch.setattr(macro.pydirectinput, "press", lambda key, **k: events.append(("press", key)), raising=False)
    monkeypatch.setattr(macro, "hover_click", lambda x, y, duration=0: events.append(("click", (x, y))))
    monkeypatch.setattr(macro, "focus_roblox", lambda: True)
    monkeypatch.setattr(macro, "close_backpack", lambda step: events.append(("close", step)))
    monkeypatch.setattr(macro, "log_run_event", lambda text: events.append(("log", text)))
    monkeypatch.setattr(macro, "grab_screen", lambda: frame("payday_over_backpack"))
    assert macro.detect_paycheck() is True and ("click", cfg.PAYCHECK_DISMISS_POS_1) in events           # (the real frame of the popup over the backpack's header)
    events.clear()
    monkeypatch.setattr(macro, "detect_paycheck", lambda: False)
    monkeypatch.setattr(macro, "grab_screen", lambda: frame("search_unfocused"))                          # (the click did not reach the box: its placeholder is still there after the letter)
    assert macro.focus_search_box() is False
    assert [e[1] for e in events if e[0] == "press"] == ["backspace"] * 5 + ["p"]                         # (one letter in the game, not the whole word)
    monkeypatch.setattr(macro, "grab_screen", lambda: frame("toys_unfiltered"))
    assert macro.focus_search_box() is True


def test_the_hotkeys_are_not_acted_on_while_the_macro_types_its_own_letters(macro, monkeypatch):
    seen = []
    monkeypatch.setattr(macro, "check_running", lambda: seen.append(macro.state.typing))
    monkeypatch.setattr(macro.pydirectinput, "press", lambda key, **k: None, raising=False)
    monkeypatch.setattr(macro.time, "sleep", lambda s: None)
    macro.type_text("phone")
    assert seen == [True] * 5 and macro.state.typing is False
    def stop(): raise macro.StopRequested()
    monkeypatch.setattr(macro, "check_running", stop)
    with pytest.raises(macro.StopRequested):
        macro.type_text("phone")
    assert macro.state.typing is False                                                       # (a stop while typing does not leave the hotkeys off)


def test_a_phone_that_shows_the_listings_at_once_has_no_menu_to_wait_for(macro, game):
    """Run on the latest version, in a capture: from the house TRADE LISTINGS opened "TRADING HUB  LISTING 1/2" itself; the macro waited 4 s for the menu, made no more clicks and ended."""
    cfg = macro.cfg
    game.moves[("phone", cfg.TRADES_PHONE_LISTINGS_POS)] = "listing_1"
    assert macro.open_trade_listings() is True and game.state == "listing_1"
    assert game.clicks == [cfg.TRADE_HUB_USE_POS, cfg.TRADES_PHONE_LISTINGS_POS]              # (no VIEW MY LISTINGS click: that menu was not there)


def test_a_listing_is_shown_by_the_arrow_and_one_that_is_not_there_is_said(macro, game):
    game.state = "listing_1"
    assert macro.select_listing(2) is True and game.state == "listing_2"
    assert macro.select_listing(2) is True and len(game.clicks) == 1                        # already shown: no click
    game.moves[("listing_1", macro.cfg.TRADES_NEXT_POS)] = "listing_1"                       # (one listing only: the arrow stays)
    game.state = "listing_1"
    assert macro.select_listing(2) is False


def test_the_tour_numbers_the_offers_by_their_place_in_the_list_not_by_how_they_look_keeps_a_picture_of_each_step_that_showed_a_new_one_and_ends_when_the_list_does_not_move(macro, game):
    scrolling_list(macro, game, rows=6)                                                      # (six offers that look the same, the list gliding after each step)
    os.makedirs(macro.cfg.TRADES_ROWS_DIR)
    result = macro.offers_tour(1)
    assert result["offers"] == 6 and result["ended"] == "the end of the list"
    assert sorted(os.listdir(macro.cfg.TRADES_ROWS_DIR)) == [f"L1_{n:02d}.png" for n in range(1, 7)]       # (each number once, though the rows are alike)
    assert result["pictures"] == len([f for f in os.listdir(macro.cfg.TRADES_DIR) if f != "rows"]) and 3 <= result["pictures"] <= 6
    assert set(game.scrolls) == {("down", macro.cfg.TRADES_SCROLL_CLICKS)} and NOTCH * macro.cfg.TRADES_SCROLL_CLICKS < macro.cfg.TRADES_ROW_PITCH
    panel = cv2.imread(os.path.join(macro.cfg.TRADES_DIR, "L1_01.png"))
    assert panel.shape[:2] == (macro.cfg.TRADES_PANEL_BOX[3] - macro.cfg.TRADES_PANEL_BOX[1], macro.cfg.TRADES_PANEL_BOX[2] - macro.cfg.TRADES_PANEL_BOX[0])


def test_a_picture_is_taken_only_once_the_list_has_stopped_gliding(macro, game):
    state = scrolling_list(macro, game, rows=6)
    macro.scroll_down(1)                                                                     # (a step: the first look shows the list 40 px short)
    img = macro.grab_still()
    assert (img == list_frame(frame("offers"), NOTCH, 6)).all() and state.offset == NOTCH


def test_a_list_of_one_offer_is_numbered_one_and_ends_at_once(macro, game):
    scrolling_list(macro, game, rows=1)
    os.makedirs(macro.cfg.TRADES_ROWS_DIR)
    assert macro.offers_tour(2) == {"offers": 1, "pictures": 1, "ended": "the end of the list"}


def test_a_step_that_no_row_can_be_followed_over_is_said(macro, game):
    scrolling_list(macro, game, rows=6)
    os.makedirs(macro.cfg.TRADES_ROWS_DIR)
    real = macro.visible_offers
    looks = []
    macro.visible_offers = lambda img: [] if len(looks.append(1) or looks) == 2 else real(img)      # (the second picture shows no whole row)
    assert "place in the list was lost" in macro.offers_tour(1)["ended"]


def test_the_trades_tour_looks_at_both_listings_puts_the_phone_away_and_says_what_it_found(macro, game, monkeypatch):
    ran = []
    monkeypatch.setattr(macro, "offers_tour", lambda listing: ran.append(listing) or {"offers": listing, "pictures": 1, "ended": "the end of the list"})
    assert macro.trades_tour() is True
    assert ran == [1, 2] and game.state == "world"                                           # (the X of the last screen)
    done = json.loads(open(macro.cfg.TRADES_DONE_PATH).read())
    assert done == {"listings": {"1": {"offers": 1, "pictures": 1, "ended": "the end of the list"}, "2": {"offers": 2, "pictures": 1, "ended": "the end of the list"}}, "problem": ""}
    game.state = "world"
    game.moves[("world", macro.cfg.TRADE_HUB_USE_POS)] = "world"                             # the phone never comes up
    assert macro.trades_tour() is False
    assert json.loads(open(macro.cfg.TRADES_DONE_PATH).read())["problem"].startswith("the trade listings could not be opened")


def test_a_listing_with_no_offers_is_said_and_the_tour_goes_on_to_the_other(macro, game, monkeypatch):
    monkeypatch.setattr(macro, "offers_tour", lambda listing: {"offers": 2, "pictures": 1, "ended": "the end of the list"})
    game.moves[("listing_1", macro.cfg.TRADES_OFFERS_POS)] = "listing_1"                     # the button does nothing on the first listing
    assert macro.trades_tour() is True
    done = json.loads(open(macro.cfg.TRADES_DONE_PATH).read())
    assert done["listings"]["1"]["offers"] == 0 and done["listings"]["2"]["offers"] == 2


def decline_game(macro, game, monkeypatch, listing=2, number=1):
    """A game that is on the offers list of `listing` with the picture of row `number` saved from it (the first one of the real frame)."""
    cfg = macro.cfg
    offers = frame("offers")
    os.makedirs(cfg.TRADES_ROWS_DIR, exist_ok=True)
    y = macro.visible_offers(offers)[0]
    cv2.imwrite(os.path.join(cfg.TRADES_ROWS_DIR, f"L{listing}_{number:02d}.png"), macro.offer_row(offers, y))
    monkeypatch.setattr(macro, "open_trade_listings", lambda: True)
    monkeypatch.setattr(macro, "open_offers", lambda n: True)
    game.state = "offers"
    game.moves[("offers", (cfg.TRADES_ROW_CLICK_X, y + cfg.TRADES_ROW_CLICK_DY))] = "detail"
    return y


def decline_result(macro):
    return json.loads(open(macro.cfg.TRADES_DECLINE_DONE_PATH).read())


def test_an_offer_is_declined_by_opening_its_row_pressing_decline_and_yes_and_never_accept(macro, game, monkeypatch):
    cfg = macro.cfg
    y = decline_game(macro, game, monkeypatch)
    assert macro.decline_offer(2, 1) is True
    assert game.clicks == [(cfg.TRADES_ROW_CLICK_X, y + cfg.TRADES_ROW_CLICK_DY), cfg.TRADES_DETAIL_DECLINE_POS, cfg.TRADES_DECLINE_YES_POS]
    assert decline_result(macro) == {"listing": 2, "offer": 1, "declined": True, "why": ""} and os.path.exists(cfg.TRADES_DECLINED_PATH)
    assert cv2.imread(cfg.TRADES_DECLINED_PATH).shape[:2] == (cfg.TRADES_PANEL_BOX[3] - cfg.TRADES_PANEL_BOX[1], cfg.TRADES_PANEL_BOX[2] - cfg.TRADES_PANEL_BOX[0])        # (the offer as it was, kept for the channel)


def test_an_offer_that_is_not_in_the_list_or_has_no_picture_is_not_touched(macro, game, monkeypatch):
    cfg = macro.cfg
    decline_game(macro, game, monkeypatch)
    assert macro.decline_offer(2, 5) is False and decline_result(macro)["why"].startswith("there is no picture of that offer")
    game.frames["offers"] = frame("declining")                                               # the list is not the one that was photographed any more
    game.clicks.clear()
    assert macro.decline_offer(2, 1) is False
    assert decline_result(macro)["why"].startswith("that offer is not in the list any more")
    assert game.clicks == [cfg.TRADES_CLOSE_POS]                                              # nothing but the phone put away: no row, no DECLINE


def test_an_offer_down_the_list_is_found_by_its_place_scrolling_to_it_and_one_that_looks_other_is_not_touched(macro, game, monkeypatch):
    cfg = macro.cfg
    state = scrolling_list(macro, game, rows=8)
    offers = frame("offers")
    os.makedirs(cfg.TRADES_ROWS_DIR, exist_ok=True)
    row = macro.offer_row(offers, macro.visible_offers(offers)[0])
    cv2.imwrite(os.path.join(cfg.TRADES_ROWS_DIR, "L1_05.png"), row)
    monkeypatch.setattr(macro, "open_trade_listings", lambda: True)
    monkeypatch.setattr(macro, "open_offers", lambda n: True)
    game.state = "offers"
    assert macro.decline_offer(1, 5) is False and game.scrolls                                # (the list is not the game's phone any more: the row click opens nothing)
    y = 453 + 4 * 136 - state.offset                                                            # (the fifth row, where it was seen when the list stopped)
    assert (cfg.TRADES_ROW_CLICK_X, y + cfg.TRADES_ROW_CLICK_DY) == game.clicks[0] or abs(game.clicks[0][1] - (y + cfg.TRADES_ROW_CLICK_DY)) <= 3
    cv2.imwrite(os.path.join(cfg.TRADES_ROWS_DIR, "L1_05.png"), np.random.default_rng(1).integers(0, 255, row.shape, dtype=np.uint8))            # (a picture of another offer: what is there now is not it)
    game.clicks.clear()
    state.offset = 0                                                                            # (the list is opened at its top again)
    assert macro.decline_offer(1, 5) is False and decline_result(macro)["why"].startswith("that offer is not in the list any more") and game.clicks == [cfg.TRADES_CLOSE_POS]


def test_every_offer_of_a_listing_is_declined_one_after_the_other_until_none_is_left(macro, game, monkeypatch):
    cfg = macro.cfg
    offers = frame("offers")
    monkeypatch.setattr(macro, "open_trade_listings", lambda: True)
    monkeypatch.setattr(macro, "open_offers", lambda n: True)
    left = [3]
    monkeypatch.setattr(macro, "grab_still", lambda: offers if left[0] else np.zeros_like(offers))
    monkeypatch.setattr(macro, "visible_offers", lambda img: [] if not left[0] else [453, 589])
    declined = []

    def decline_row(y, result):
        declined.append(y)
        left[0] -= 1
        return True
    monkeypatch.setattr(macro, "decline_row", decline_row)
    assert macro.decline_all(2) is True
    assert declined == [453] * 3 and decline_result(macro) == {"listing": 2, "offer": "*", "declined": True, "count": 3, "why": ""}
    assert macro.decline_all(2) is False and decline_result(macro)["why"] == "there are no offers in that listing"      # (nothing left)
    left[0] = 99                                                                                # (a list that never ends is stopped)
    declined.clear()
    assert macro.decline_all(1) is False and len(declined) == cfg.TRADES_MAX_OFFERS and "there may be more" in decline_result(macro)["why"]
    left[0] = 2
    monkeypatch.setattr(macro, "decline_row", lambda y, result: result.update(why="the offer did not open") or False)
    assert macro.decline_all(1) is False and decline_result(macro)["why"] == "the offer did not open" and decline_result(macro)["count"] == 0


def test_the_next_offer_is_waited_for_at_the_top_while_the_one_declined_still_shows_without_accept(macro, game, monkeypatch):
    looks = iter([[589], [589], [453, 589]])                                                    # (the declined row has no ACCEPT: the second is first in sight until it is gone)
    monkeypatch.setattr(macro, "grab_still", lambda: macro.wait_interruptible(1))                # (a look takes time)
    monkeypatch.setattr(macro, "visible_offers", lambda img: next(looks))
    assert macro.wait_list_settled(453) == [453, 589]
    monkeypatch.setattr(macro, "visible_offers", lambda img: [589])                             # (never gone)
    assert macro.wait_list_settled(453) is None


def test_nothing_is_confirmed_unless_the_dialog_is_seen_and_a_dialog_that_stays_is_said(macro, game, monkeypatch):
    cfg = macro.cfg
    y = decline_game(macro, game, monkeypatch)
    game.moves[("detail", cfg.TRADES_DETAIL_DECLINE_POS)] = "detail"                         # DECLINE does nothing
    assert macro.decline_offer(2, 1) is False and cfg.TRADES_DECLINE_YES_POS not in game.clicks
    assert "dialog did not come up" in decline_result(macro)["why"]
    game.state, game.clicks[:] = "offers", []
    game.moves[("detail", cfg.TRADES_DETAIL_DECLINE_POS)] = "dialog"
    game.moves[("dialog", cfg.TRADES_DECLINE_YES_POS)] = "dialog"                            # Yes does nothing
    assert macro.decline_offer(2, 1) is False and "still up after Yes" in decline_result(macro)["why"]
    game.state, game.clicks[:] = "offers", []
    game.moves[(("offers"), (cfg.TRADES_ROW_CLICK_X, y + cfg.TRADES_ROW_CLICK_DY))] = "offers"          # the row does not open
    assert macro.decline_offer(2, 1) is False and decline_result(macro)["why"] == "the offer did not open" and game.clicks == [(cfg.TRADES_ROW_CLICK_X, y + cfg.TRADES_ROW_CLICK_DY)]


def test_the_trade_commands_are_read_for_a_listing_and_an_offer_the_macro_has_and_queue_the_actions(macro, monkeypatch, tmp_path):
    from test_state import write_command, remote_gui
    monkeypatch.setattr(macro.cfg, "COMMANDS_DIR", str(tmp_path / "commands"))
    cfg = macro.cfg
    write_command(macro, "decline", listing=2, offer=3)
    assert macro.read_command_with_id()[1]["offer"] == 3
    write_command(macro, "decline", listing=1, offer="*")
    assert macro.read_command_with_id()[1]["offer"] == "*"
    for details in (dict(listing=3, offer=1), dict(listing=1, offer=0), dict(listing=1, offer=cfg.TRADES_MAX_OFFERS + 1), dict(listing=1, offer="1"), dict(listing=1, offer=True),
                    dict(listing="1", offer=1), dict(offer=1), {}):
        write_command(macro, "decline", **details)
        assert macro.read_command_with_id()[1] is None, details
    assert {name for name in macro.OUTSIDE_ACTIONS if name.startswith("decline:")} == {f"decline:{l}:{n}" for l in cfg.TRADES_LISTINGS for n in range(1, cfg.TRADES_MAX_OFFERS + 1)} | {f"decline:{l}:*" for l in cfg.TRADES_LISTINGS}
    gui = remote_gui(macro, monkeypatch, tmp_path, running=True)
    gui.do_command("decline", {"command": "decline", "listing": 2, "offer": 3}, "d1")
    assert gui.calls == [("stop",)] and gui.pending == ["decline:2:3"] and gui.resume_loop is False
    action = macro.OUTSIDE_ACTIONS["decline:2:3"]
    assert action.func is macro.decline_offer and action.args == (2, 3)
    assert macro.OUTSIDE_ACTIONS["trades"] is macro.trades_tour and "trades" in cfg.COMMANDS


def test_recenter_closes_the_trade_phone_and_the_stray_windows_respawns_and_closes_what_is_left(macro, monkeypatch):
    cfg = macro.cfg
    log, screens, strays = [], ["phone", None], [["profile", None], [None]]
    monkeypatch.setattr(macro, "focus_roblox", lambda: True)
    monkeypatch.setattr(macro, "grab_screen", lambda: None)
    monkeypatch.setattr(macro, "trade_screen", lambda img: screens.pop(0) if screens else None)
    monkeypatch.setattr(macro, "exit_minigame", lambda: log.append("minigame left") or True)
    monkeypatch.setattr(macro, "close_trade_phone", lambda: log.append("phone closed"))
    monkeypatch.setattr(macro, "hover_click", lambda x, y, duration=0: log.append(("click", (x, y))))
    monkeypatch.setattr(macro, "wait_interruptible", lambda s: None)
    monkeypatch.setattr(macro, "dismiss_stray_windows", lambda: (strays[0].pop(0) if strays[0] else None) if strays else None)
    monkeypatch.setattr(macro, "respawn_character", lambda: (log.append("respawned"), strays.pop(0)))
    monkeypatch.setattr(macro, "log_run_event", lambda text: log.append(text))
    assert macro.recenter() is True
    assert log == ["minigame left", "phone closed", "respawned", "recenter: 2 window(s) closed, respawned"]            # (the phone first, the stray window; after the respawn nothing is left)
    screens[:] = ["board"]
    log.clear()
    assert macro.close_open_windows() == 1 and log == [("click", cfg.STRAY_WINDOWS["trade_board"]["close_pos"])]
    monkeypatch.setattr(macro, "exit_minigame", lambda: False)                                      # (a minigame that cannot be left: the respawn is done all the same, and it says so)
    screens[:] = []
    assert macro.recenter() is False and log[-1] == "recenter: 0 window(s) closed, respawned (the minigame could not be left)"
