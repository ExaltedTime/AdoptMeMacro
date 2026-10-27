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


def scrolled(img, rows=1, with_new_row=True):
    """`img` (the offers list) with its list moved up by `rows` rows (136 px each): the second row is where the first was, and below it a third one that was not there (the first row's picture
    with its "Them" cells painted over, so that it is another offer)."""
    out = img.copy()
    top, bottom, left, right = 395, 735, 630, 1275
    shift = 136 * rows
    out[top:bottom - shift, left:right] = img[top + shift:bottom, left:right]
    if with_new_row:
        new = img[399:535, left:right].copy()
        new[20:120, 440:620] = (40, 90, 200)
        out[bottom - shift:bottom, left:right] = new[:shift]
    return out


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
    monkeypatch.setattr(macro, "scroll_down", lambda clicks, direction=-1: world.scrolls.append(("up" if direction > 0 else "down", clicks)))
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


def test_the_rows_whole_in_the_list_are_found_by_their_accept_buttons_and_known_again_by_their_pictures(macro):
    offers = frame("offers")
    assert macro.visible_offers(offers) == pytest.approx([453, 589], abs=3)                  # (the third row is cut off by the list's bottom)
    assert macro.visible_offers(frame("declining")) == pytest.approx([589], abs=3)           # (the one being declined has no ACCEPT)
    assert macro.visible_offers(frame("detail")) == [] and macro.visible_offers(np.zeros((1080, 1920, 3), np.uint8)) == []
    first, second = (macro.offer_row(offers, y) for y in macro.visible_offers(offers))
    assert macro.find_offer_row(offers, first) == pytest.approx(453, abs=3) and macro.find_offer_row(offers, second) == pytest.approx(589, abs=3)
    moved = scrolled(offers)
    assert macro.find_offer_row(moved, second) == pytest.approx(453, abs=3)                  # the second row after a step; the first is out of sight
    assert macro.find_offer_row(moved, first) is None


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


def test_the_phone_is_found_by_typing_its_name_in_the_toys_search_clicking_the_first_item_and_the_search_is_cleared_before_the_backpack_is_closed(macro, game, monkeypatch):
    cfg = macro.cfg
    events = []
    monkeypatch.undo()                                                                       # (the fixture's stand-ins are the game's; this one needs its own)
    clock = types.SimpleNamespace(now=1000.0)
    monkeypatch.setattr(macro, "time", types.SimpleNamespace(**{**vars(macro.time), "time": lambda: clock.now, "sleep": lambda s: events.append(("sleep", s))}))
    monkeypatch.setattr(macro, "wait_interruptible", lambda s: None)
    monkeypatch.setattr(macro, "check_running", lambda: None)
    monkeypatch.setattr(macro.pydirectinput, "press", lambda key, **k: events.append(("press", key, macro.state.typing)), raising=False)
    monkeypatch.setattr(macro, "hover_click", lambda x, y, duration=0: events.append(("click", (x, y))))
    monkeypatch.setattr(macro, "focus_roblox", lambda: True)
    monkeypatch.setattr(macro, "close_backpack", lambda step: events.append(("close", step)))
    looks = [frame("toys_card"), frame("menu")]                                               # (the phone's card is up after the first item is clicked; at the end the hotbar has its icon)
    monkeypatch.setattr(macro, "grab_screen", lambda: looks.pop(0) if len(looks) > 1 else looks[0])
    monkeypatch.setattr(macro, "log_run_event", lambda text: events.append(("log", text)))
    assert macro.equip_trade_phone() is True
    clicks = [e[1] for e in events if e[0] == "click"]
    presses = [e[1] for e in events if e[0] == "press"]
    assert presses == [cfg.KEYS["backpack"], *["backspace"] * 5, *"phone", *["backspace"] * 5]          # (the box emptied first: what a search before left in it)
    equip = (1111 + cfg.TRADES_EQUIP_OFFSET[0], 780 + cfg.TRADES_EQUIP_OFFSET[1])                       # from the middle of the card's name, where the real frame has it
    assert clicks == [cfg.TRADE_HUB_TOYS_TAB_POS, cfg.TRADES_SEARCH_POS, cfg.TRADE_HUB_PHONE_POS, (equip[0], equip[1]), cfg.TRADES_SEARCH_POS, cfg.TRADES_UNFOCUS_POS]
    assert abs(equip[0] - 1073) <= 3 and abs(equip[1] - 855) <= 3                                      # (the Equip button of the real frame)
    assert events[-1] == ("close", "the Trading Hub Phone") and macro.state.typing is False
    typed = {e[1]: e[2] for e in events if e[0] == "press"}
    assert all(typed[key] is True for key in "phone") and typed[cfg.KEYS["backpack"]] is False        # (the hotkeys are left alone while it types: "phone" has the stop key)


def test_the_badge_is_never_equipped_when_the_search_leaves_it_first_the_search_is_tried_again_and_then_nothing_is_equipped(macro, monkeypatch):
    """The phone's card is looked for after the first item is clicked, and Equip only goes where that card is: a search that did not filter (the badge first) gets no Equip."""
    cfg = macro.cfg
    events = []
    monkeypatch.undo()
    monkeypatch.setattr(macro, "time", types.SimpleNamespace(**{**vars(macro.time), "time": lambda: 1000.0, "sleep": lambda s: None}))
    monkeypatch.setattr(macro, "wait_interruptible", lambda s: None)
    monkeypatch.setattr(macro, "check_running", lambda: None)
    monkeypatch.setattr(macro.pydirectinput, "press", lambda key, **k: events.append(("press", key)), raising=False)
    monkeypatch.setattr(macro, "hover_click", lambda x, y, duration=0: events.append(("click", (x, y))))
    monkeypatch.setattr(macro, "focus_roblox", lambda: True)
    monkeypatch.setattr(macro, "close_backpack", lambda step: events.append(("close", step)))
    monkeypatch.setattr(macro, "log_run_event", lambda text: events.append(("log", text)))
    monkeypatch.setattr(macro, "grab_screen", lambda: frame("menu"))                          # (no card of the phone: the hotbar of this frame has the phone's icon, but the card is not there)
    assert macro.equip_trade_phone() is False
    clicks = [e[1] for e in events if e[0] == "click"]
    assert clicks.count(cfg.TRADE_HUB_PHONE_POS) == cfg.TRADES_EQUIP_TRIES and clicks.count(cfg.TRADES_SEARCH_POS) == cfg.TRADES_EQUIP_TRIES + 1
    assert not [c for c in clicks if c not in (cfg.TRADE_HUB_TOYS_TAB_POS, cfg.TRADES_SEARCH_POS, cfg.TRADE_HUB_PHONE_POS, cfg.TRADES_UNFOCUS_POS)]        # (no Equip anywhere)
    assert [e[1] for e in events if e[0] == "log"].count("trade listings: the item clicked is not the Trading Hub Phone - searching again") == 1
    assert events[-1] == ("close", "the Trading Hub Phone")                                    # (the search cleared, the backpack closed)


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


def test_the_tour_numbers_the_offers_in_the_order_they_are_seen_keeps_a_picture_of_each_step_that_showed_a_new_one_and_ends_when_the_list_does_not_move(macro, game):
    offers = frame("offers")
    sequence = [offers, scrolled(offers), scrolled(offers)]                                  # the third look is the same as the second: the end of the list
    looks = iter(sequence)
    last = []
    def grab():
        last[:] = [next(looks, last[0] if last else sequence[-1])]
        return last[0]
    macro.grab_screen = grab
    os.makedirs(macro.cfg.TRADES_ROWS_DIR)
    result = macro.offers_tour(1)
    assert result == {"offers": 3, "pictures": 2, "ended": "the end of the list"}
    assert sorted(os.listdir(macro.cfg.TRADES_DIR)) == ["L1_01.png", "L1_02.png", "rows"]
    assert sorted(os.listdir(macro.cfg.TRADES_ROWS_DIR)) == ["L1_01.png", "L1_02.png", "L1_03.png"]
    assert game.scrolls == [("down", macro.cfg.TRADES_SCROLL_CLICKS)] * 2                      # (not after the last look: the list had not moved)
    second = cv2.imread(os.path.join(macro.cfg.TRADES_DIR, "L1_02.png"))
    assert second.shape[:2] == (macro.cfg.TRADES_PANEL_BOX[3] - macro.cfg.TRADES_PANEL_BOX[1], macro.cfg.TRADES_PANEL_BOX[2] - macro.cfg.TRADES_PANEL_BOX[0])


def test_a_step_that_lost_every_row_seen_before_is_taken_back_and_made_shorter(macro, game):
    offers = frame("offers")
    lost = scrolled(offers, with_new_row=True)
    lost[395:735, 640:1262] = np.roll(lost[395:735, 640:1262], 7, axis=1)                    # (nothing in it is a row seen before, though rows are in sight)
    sequence = iter([offers, lost, scrolled(offers), scrolled(offers)])                        # (after the step back, one notch shows the second row and a new one)
    macro.grab_screen = lambda: next(sequence, scrolled(offers))
    os.makedirs(macro.cfg.TRADES_ROWS_DIR)
    result = macro.offers_tour(2)
    assert game.scrolls[:3] == [("down", 3), ("up", 3), ("down", 1)]                         # too long: back, and one notch next
    assert result["offers"] == 3 and "skipped" not in result["ended"]


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
    assert game.scrolls and all(kind == "down" and clicks == cfg.TRADES_FIND_CLICKS for kind, clicks in game.scrolls)    # (it looked down the list first)


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
    for details in (dict(listing=3, offer=1), dict(listing=1, offer=0), dict(listing=1, offer=cfg.TRADES_MAX_OFFERS + 1), dict(listing=1, offer="1"), dict(listing=1, offer=True),
                    dict(listing="1", offer=1), dict(offer=1), {}):
        write_command(macro, "decline", **details)
        assert macro.read_command_with_id()[1] is None, details
    assert {name for name in macro.OUTSIDE_ACTIONS if name.startswith("decline:")} == {f"decline:{l}:{n}" for l in cfg.TRADES_LISTINGS for n in range(1, cfg.TRADES_MAX_OFFERS + 1)}
    gui = remote_gui(macro, monkeypatch, tmp_path, running=True)
    gui.do_command("decline", {"command": "decline", "listing": 2, "offer": 3}, "d1")
    assert gui.calls == [("stop",)] and gui.pending == ["decline:2:3"] and gui.resume_loop is False
    action = macro.OUTSIDE_ACTIONS["decline:2:3"]
    assert action.func is macro.decline_offer and action.args == (2, 3)
    assert macro.OUTSIDE_ACTIONS["trades"] is macro.trades_tour and "trades" in cfg.COMMANDS
