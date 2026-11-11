"""Screen detection on synthetic and reference images (the crops in ref/ are real, lossless PNGs)."""

import os

import cv2
import numpy as np
import pytest

BLACK = (1080, 1920, 3)


def blank():
    return np.zeros(BLACK, np.uint8)


def put(img, crop, box):
    """Paste `crop` at the top-left of `box` (left, top, right, bottom)."""
    left, top = box[0], box[1]
    img[top:top + crop.shape[0], left:left + crop.shape[1]] = crop
    return img


def test_need_icon_strip_is_the_configured_top_left_part(macro):
    strip = macro.need_icon_strip(blank())
    assert strip.shape[0] == int(1080 * macro.cfg.NEED_ICON_TOP_PERCENT)
    assert strip.shape[1] == int(1920 * macro.cfg.NEED_ICON_WIDTH_PERCENT)


def test_strip_changed_fraction(macro):
    a = np.zeros((10, 10, 3), np.uint8)
    b = a.copy()
    b[0, :5] = 200
    assert macro.strip_changed_fraction(a, a) == 0.0
    assert macro.strip_changed_fraction(a, b) == pytest.approx(0.05)


@pytest.fixture
def icon_screen(macro, monkeypatch):
    """detect_need_icons() on a screen we control: returns a setter for the screen, and a counter of circle searches."""
    screen = {"img": blank()}
    searches = []
    real = cv2.HoughCircles

    def region(x, y, width, height):
        return screen["img"][y:y + height, x:x + width].copy()

    def counting(*args, **kwargs):
        searches.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(macro, "grab_screen_region", region)
    monkeypatch.setattr(macro.cv2, "HoughCircles", counting)
    macro._NEED_STRIP_CACHE.update(strip=None, found=[], reuses=0)
    return screen, searches


def ring(img, center, radius=30):
    cv2.circle(img, center, radius, (255, 255, 255), 4)
    cv2.circle(img, center, radius - 6, (60, 90, 200), -1)


def test_icons_are_found_by_their_round_outline(macro, icon_screen):
    screen, _ = icon_screen
    ring(screen["img"], (700, 80))
    found, img = macro.detect_need_icons()
    assert len(found) == 1 and abs(found[0][0] - 700) <= 3 and abs(found[0][1] - 80) <= 3
    assert img.shape[0] > int(1080 * macro.cfg.NEED_ICON_TOP_PERCENT)      # the strip plus the crop margin


def test_the_circle_search_is_skipped_while_the_strip_is_unchanged(macro, icon_screen):
    screen, searches = icon_screen
    ring(screen["img"], (700, 80))
    results = [macro.detect_need_icons()[0] for _ in range(5)]
    assert len(searches) == 1 and all(r == results[0] for r in results)


def test_the_search_runs_again_after_enough_reuses(macro, icon_screen):
    screen, searches = icon_screen
    ring(screen["img"], (700, 80))
    for _ in range(macro.cfg.NEED_STRIP_MAX_REUSES + 2):
        macro.detect_need_icons()
    assert len(searches) == 2


def test_an_empty_result_is_reused_less_than_one_with_icons(macro, icon_screen):
    """A missed icon looks like an empty strip, so an empty result is trusted for fewer checks."""
    screen, searches = icon_screen
    for _ in range(macro.cfg.NEED_STRIP_MAX_EMPTY_REUSES + 2):
        macro.detect_need_icons()
    assert len(searches) == 2 and macro.cfg.NEED_STRIP_MAX_EMPTY_REUSES < macro.cfg.NEED_STRIP_MAX_REUSES


def test_fresh_always_searches_and_refreshes_the_cache(macro, icon_screen):
    screen, searches = icon_screen
    ring(screen["img"], (700, 80))
    macro.detect_need_icons()
    macro.detect_need_icons(fresh=True)
    assert len(searches) == 2 and macro._NEED_STRIP_CACHE["reuses"] == 0
    macro.detect_need_icons()
    assert len(searches) == 2                                  # reused the refreshed result


def test_a_new_icon_forces_a_new_search(macro, icon_screen):
    screen, searches = icon_screen
    ring(screen["img"], (700, 80))
    first, _ = macro.detect_need_icons()
    ring(screen["img"], (800, 80))
    second, _ = macro.detect_need_icons()
    assert len(searches) == 2 and len(first) == 1 and len(second) == 2


def test_a_one_off_missed_icon_cannot_pass_the_flicker_recheck(macro, icon_screen, monkeypatch):
    """_need_cleared() looks twice. If the first search misses an icon that is still there and the strip then looks
    unchanged, the second look must still be a real search - not the same wrong answer from the cache."""
    screen, searches = icon_screen
    ring(screen["img"], (700, 80))
    real = macro.cv2.HoughCircles
    calls = []

    def misses_once(*args, **kwargs):
        calls.append(1)
        return None if len(calls) == 1 else real(*args, **kwargs)

    monkeypatch.setattr(macro.cv2, "HoughCircles", misses_once)
    monkeypatch.setattr(macro, "identify_icons", lambda found, img: iter([(i, img[:5, :5], "hungry", 0.9) for i, _ in enumerate(found)]))
    monkeypatch.setattr(macro, "check_focus", lambda: None)
    monkeypatch.setattr(macro.cfg, "NEED_GONE_FLICKER_RECHECK_DELAY", 0)
    assert macro._need_cleared("hungry") is False              # the second, fresh look saw it
    assert len(calls) == 2


def test_a_need_that_is_really_gone_is_cleared(macro, icon_screen, monkeypatch):
    screen, searches = icon_screen
    monkeypatch.setattr(macro, "check_focus", lambda: None)
    monkeypatch.setattr(macro.cfg, "NEED_GONE_FLICKER_RECHECK_DELAY", 0)
    assert macro._need_cleared("hungry") is True


class FakeMonitor:
    """Stands in for mss: serves a region of `screen`, and records what was asked for."""
    def __init__(self, screen):
        self.screen, self.requests = screen, []
        outer = self

        class MSS:
            monitors = [{}, {"left": 0, "top": 0, "width": 1920, "height": 1080}]

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def grab(self, region):
                outer.requests.append(region)
                x, y, w, h = region["left"], region["top"], region["width"], region["height"]
                part = outer.screen[y:y + h, x:x + w]
                return np.dstack([part, np.full(part.shape[:2], 255, np.uint8)])    # BGRA, as mss gives
        self.MSS = MSS


def test_region_capture_equals_a_crop_of_the_whole_screen(macro, monkeypatch):
    whole = np.random.default_rng(1).integers(0, 255, BLACK, dtype=np.uint8)
    monitor = FakeMonitor(whole)
    monkeypatch.setattr(macro, "mss", monitor)
    monkeypatch.setattr(macro, "grab_screen", lambda: whole)
    monkeypatch.setattr(macro, "roblox_rect", lambda: (0, 0, 1920, 1080))
    assert np.array_equal(macro.grab_screen_region(5, 7, 300, 100), whole[7:107, 5:305])
    assert monitor.requests == [{"left": 5, "top": 7, "width": 300, "height": 100}]     # only that region was captured


def test_region_capture_falls_back_for_a_window_that_is_not_the_reference_size(macro, monkeypatch):
    whole = np.random.default_rng(2).integers(0, 255, BLACK, dtype=np.uint8)
    monitor = FakeMonitor(whole)
    monkeypatch.setattr(macro, "mss", monitor)
    monkeypatch.setattr(macro, "grab_screen", lambda: whole)
    monkeypatch.setattr(macro, "roblox_rect", lambda: (10, 10, 1280, 720))
    assert np.array_equal(macro.grab_screen_region(5, 7, 300, 100), whole[7:107, 5:305])
    assert monitor.requests == []                                                         # the whole-screen path was used


def reference(macro, name):
    crop = macro.load_templates(macro.cfg.STRAY_WINDOW_DIR).get(name)
    assert crop is not None, f"ref/popups/{name}.png is missing"
    return crop


@pytest.mark.parametrize("name", ["trading_hub", "star_rewards", "respawn_confirm", "character_menu", "cant_spawn_vehicle", "profile", "trade_board"])
def test_stray_windows_are_recognised_and_closed(macro, monkeypatch, name):
    config = macro.cfg.STRAY_WINDOWS[name]
    screen = put(blank(), reference(macro, name), config["box"])
    monkeypatch.setattr(macro, "grab_screen", lambda: screen)
    monkeypatch.setattr(macro.time, "sleep", lambda s: None)
    assert macro.dismiss_stray_windows() == name
    assert config["close_pos"] in macro.clicks


@pytest.mark.parametrize("shot, name, closed_at", [("star_rewards_claim", "star_rewards_claim", (959, 675)), ("star_rewards", "star_rewards", (1209, 441)),
                                                   ("trading_hub", "trading_hub", (1278, 339)), ("task_board_alerts", "task_board", (1318, 369)),
                                                   ("trade_board", "trade_board", (1282, 339)), ("promo_limited_time", "promo_limited_time", (1085, 665)),
                                                   ("choose_card_screen", "choose_card", (869, 659))])
def test_each_stray_window_is_told_from_the_others_in_a_real_screenshot_and_closed_where_it_closes(macro, monkeypatch, shot, name, closed_at):
    """Real screenshots (the Roblox ones, which lack the window's title bar, with 23 rows added on top as the macro's frames have). The Star Rewards
    that opens by itself once a day has only a CLAIM! button (no X), the calendar one that the star icon opens has a red X: each is clicked where its
    own way out is - the X was clicked on both before, which closes only one of them. The Trading Hub and the Task Board close with their X."""
    screen = screenshot(shot)
    monkeypatch.setattr(macro, "grab_screen", lambda: screen)
    monkeypatch.setattr(macro.time, "sleep", lambda s: None)
    assert macro.dismiss_stray_windows() == name
    assert macro.clicks == [closed_at]
    assert {k for k in macro.cfg.STRAY_WINDOWS if macro.popup_visible(screen, k)} == {name}


def test_a_trade_request_is_declined_never_accepted_and_an_expired_one_is_okayed_by_buttons_found_by_their_colour(macro, monkeypatch):
    """Real screenshots of the two cards (the name in them changes: both are known by the pin, and the buttons are found, not clicked at fixed places)."""
    monkeypatch.setattr(macro.time, "sleep", lambda s: None)
    request = cv2.imread(os.path.join(os.path.dirname(__file__), "popups", "trade_request.png"))
    expired = cv2.imread(os.path.join(os.path.dirname(__file__), "popups", "trade_request_expired.png"))
    monkeypatch.setattr(macro, "grab_screen", lambda: request)
    assert macro.dismiss_stray_windows() == "trade_request"
    x, y = macro.clicks[-1]
    assert abs(x - 870) <= 6 and abs(y - 660) <= 6 and abs(x - 1048) > 100                      # (Decline, the red one: Accept is the green one to its right)
    macro.clicks.clear()
    monkeypatch.setattr(macro, "grab_screen", lambda: expired)
    assert macro.dismiss_stray_windows() == "trade_request_expired"
    x, y = macro.clicks[-1]
    assert abs(x - 961) <= 6 and abs(y - 660) <= 6                                              # (Okay, the wide green one)
    macro.clicks.clear()
    moved = request.copy()                                                                      # a shorter name: the card has a line less and the buttons are higher
    moved[540:700, 640:1260] = request[568:728, 640:1260]
    monkeypatch.setattr(macro, "grab_screen", lambda: moved)
    assert macro.dismiss_stray_windows() == "trade_request" and abs(macro.clicks[-1][1] - 632) <= 8
    macro.clicks.clear()
    for name in ("teleport_nursery", "teleport_dealership", "cant_spawn_vehicle", "backpack_open", "trading_hub", "task_board_alerts", "paycheck", "star_rewards_claim"):
        monkeypatch.setattr(macro, "grab_screen", lambda name=name: screenshot(name))
        assert not any(macro.popup_visible(screenshot(name), key) for key in ("trade_request", "trade_request_expired")), name        # (no other dialog is taken for one)




def test_an_empty_screen_has_no_stray_window(macro, monkeypatch):
    monkeypatch.setattr(macro, "grab_screen", blank)
    assert macro.dismiss_stray_windows() is None and not macro.clicks


def test_pet_focus_is_backed_out_of_unless_a_handler_wants_it(macro, monkeypatch):
    screen = put(blank(), reference(macro, "pet_focus"), macro.cfg.STRAY_WINDOWS["pet_focus"]["box"])
    monkeypatch.setattr(macro, "grab_screen", lambda: screen)
    monkeypatch.setattr(macro.time, "sleep", lambda s: None)
    assert macro.detect_pet_focus(screen)
    with macro.wanting_pet_focus():
        assert macro.dismiss_stray_windows() is None
    assert macro.dismiss_stray_windows() == "pet_focus"


def test_cant_spawn_vehicle_aborts_the_running_task(macro, monkeypatch):
    screen = put(blank(), reference(macro, "cant_spawn_vehicle"), macro.cfg.STRAY_WINDOWS["cant_spawn_vehicle"]["box"])
    monkeypatch.setattr(macro, "grab_screen", lambda: screen)
    monkeypatch.setattr(macro.time, "sleep", lambda s: None)
    monkeypatch.setattr(macro, "rejoin_game", lambda kinds=None: False)
    monkeypatch.setattr(macro, "close_backpack_if_stuck", lambda in_task: False)
    monkeypatch.setattr(macro, "detect_paycheck", lambda: False)
    respawns = []
    monkeypatch.setattr(macro, "respawn_character", lambda: respawns.append(1))
    assert macro.unscrew(in_task=True) == "aborted"
    macro.state.task_active, macro.state.task_thread = True, __import__("threading").get_ident()
    macro.state.last_task_unscrew = 0.0
    with pytest.raises(macro.TaskInterrupted) as stopped:
        macro.task_unscrew_tick()
    assert stopped.value.reason == "a popup"
    assert respawns == [1]                                  # the retry starts from the spawn point


def test_setup_is_never_aborted_by_the_popup(macro, monkeypatch):
    """Setup can't be abandoned halfway (its clicks toggle things), so it only closes the popup."""
    screen = put(blank(), reference(macro, "cant_spawn_vehicle"), macro.cfg.STRAY_WINDOWS["cant_spawn_vehicle"]["box"])
    monkeypatch.setattr(macro, "grab_screen", lambda: screen)
    monkeypatch.setattr(macro.time, "sleep", lambda s: None)
    monkeypatch.setattr(macro, "rejoin_game", lambda kinds=None: False)
    monkeypatch.setattr(macro, "close_backpack_if_stuck", lambda in_task: False)
    monkeypatch.setattr(macro, "detect_paycheck", lambda: False)
    assert macro.unscrew(in_task=True, interruptible=False) is None
    assert macro.cfg.STRAY_WINDOWS["cant_spawn_vehicle"]["close_pos"] in macro.clicks


def test_backpack_title_marks_the_normal_backpack(macro):
    title = reference(macro, "backpack_normal")
    screen = put(blank(), title, macro.cfg.POPUP_TEMPLATES["backpack_normal"]["box"])
    assert macro.detect_backpack_normal(screen) and macro.detect_backpack_open(screen)
    assert not macro.detect_backpack_open(blank())






def test_the_learned_need_icons_load_from_ref_needs(macro):
    assert macro.cfg.NEEDS_DIR.endswith(os.path.join("ref", "needs"))
    names = {name for name, _ in macro.need_signatures()}
    assert {"hungry", "thirsty", "pizza"} <= names
    assert {"diving", "puddle"} <= names                       # the subfolder (ref/needs/weather/) is searched too


def test_a_folder_is_not_even_listed_again_within_the_recheck_interval(macro, tmp_path, monkeypatch):
    folder = tmp_path / "crops"
    folder.mkdir()
    cv2.imwrite(str(folder / "a.png"), np.full((4, 4, 3), 10, np.uint8))
    clock = [100.0]
    monkeypatch.setattr(macro.time, "monotonic", lambda: clock[0])
    assert list(macro.load_templates(str(folder))) == ["a"]
    cv2.imwrite(str(folder / "b.png"), np.full((4, 4, 3), 20, np.uint8))
    clock[0] += macro.cfg.TEMPLATE_RECHECK_INTERVAL / 2
    assert list(macro.load_templates(str(folder))) == ["a"]                       # (too soon)
    clock[0] += macro.cfg.TEMPLATE_RECHECK_INTERVAL
    assert sorted(macro.load_templates(str(folder))) == ["a", "b"]


def test_templates_are_read_once_and_reread_when_a_file_changes(macro, tmp_path, monkeypatch):
    folder = tmp_path / "crops"
    folder.mkdir()
    monkeypatch.setattr(macro.cfg, "TEMPLATE_RECHECK_INTERVAL", 0)                 # (the folder is looked at every time here)
    cv2.imwrite(str(folder / "A.png"), np.full((4, 4, 3), 10, np.uint8))
    reads = []
    real = macro.cv2.imread
    monkeypatch.setattr(macro.cv2, "imread", lambda path, *a: reads.append(path) or real(path, *a))
    first = macro.load_templates(str(folder))
    assert list(first) == ["a"] and len(reads) == 1
    macro.load_templates(str(folder))
    macro.load_templates(str(folder))
    assert len(reads) == 1                                       # not read again
    first["junk"] = 1                                            # callers can't corrupt the cache through the dict
    assert "junk" not in macro.load_templates(str(folder))
    cv2.imwrite(str(folder / "b.png"), np.full((4, 4, 3), 20, np.uint8))     # a file is added...
    assert sorted(macro.load_templates(str(folder))) == ["a", "b"] and len(reads) == 3
    os.utime(folder / "A.png", ns=(1, 1))                                      # ...changed...
    macro.load_templates(str(folder))
    assert len(reads) == 5
    (folder / "b.png").unlink()                                                # ...removed
    assert list(macro.load_templates(str(folder))) == ["a"]
    assert macro.load_templates(str(tmp_path / "missing")) == {}
    (folder / "notes.txt").write_text("x")
    assert list(macro.load_templates(str(folder))) == ["a"]                    # only .png files count


def test_the_sick_dialog_is_found_by_its_crop(macro):
    screen = blank()
    assert not macro.detect_sick_dialog(screen)
    put(screen, reference(macro, "sick_dialog"), macro.cfg.POPUP_TEMPLATES["sick_dialog"]["box"])
    assert macro.detect_sick_dialog(screen)


SICK_SCREENS = os.path.join(os.path.dirname(__file__), "sick")


def sick_screen(name):
    return cv2.imread(os.path.join(SICK_SCREENS, f"{name}.png"))


@pytest.mark.parametrize("name, prompt, dialog", [("at_the_doctor", True, False), ("dialog", False, True), ("pet_view", False, False)])
def test_the_doctors_prompt_and_dialog_are_told_apart_on_real_screenshots(macro, name, prompt, dialog):
    """The three things seen at Doctor Heart: the hand button over the player's head, the dialog it brings up, and the pet's
    menu that pressing 'e' there brought up instead."""
    screen = sick_screen(name)
    assert (macro.find_doctor_prompt(screen) is not None) == prompt
    assert macro.detect_sick_dialog(screen) == dialog
    if prompt:
        x, y = macro.find_doctor_prompt(screen)
        assert abs(x - 965) <= 3 and abs(y - 498) <= 3


def test_the_doctors_prompt_is_found_as_the_e_button_of_the_update(macro):
    """The Lightning Dimension update made the hand an E, behind the desk (the sick need's capture: the prompt was not found and the need failed)."""
    x, y = macro.find_doctor_prompt(sick_screen("at_the_doctor_e"))
    assert abs(x - 966) <= 3 and abs(y - 415) <= 3
    assert not macro.detect_sick_dialog(sick_screen("at_the_doctor_e"))


# --- popup_found() / popup_visible(): one lookup for every crop in ref/popups --------------------------------

@pytest.mark.parametrize("name", ["paycheck", "sick_dialog", "backpack_normal", "backpack_back"])
def test_every_popup_template_has_a_crop_a_box_and_a_threshold(macro, name):
    entry = macro.cfg.POPUP_TEMPLATES[name]
    crop = reference(macro, name)
    left, top, right, bottom = entry["box"]
    assert 0 <= left < right <= 1920 and 0 <= top < bottom <= 1080
    assert crop.shape[0] <= bottom - top and crop.shape[1] <= right - left     # it fits in its own box
    assert 0 < entry["threshold"] <= 1


@pytest.mark.parametrize("name", ["paycheck", "sick_dialog", "backpack_normal", "backpack_back", "pet_focus", "trading_hub"])
def test_a_crop_is_found_at_its_boxs_corner_and_not_on_an_empty_screen(macro, name):
    box = (macro.cfg.POPUP_TEMPLATES.get(name) or macro.cfg.STRAY_WINDOWS[name])["box"]
    crop = reference(macro, name)
    screen = put(blank(), crop, box)
    found = macro.popup_found(screen, name)
    assert found == (box[0] + crop.shape[1] // 2, box[1] + crop.shape[0] // 2)       # its centre, in screen coordinates
    assert macro.popup_visible(screen, name)
    assert macro.popup_found(blank(), name) is None and not macro.popup_visible(blank(), name)


def test_the_threshold_decides_and_a_stray_window_uses_the_shared_one(macro, monkeypatch):
    crop = reference(macro, "paycheck")
    box = macro.cfg.POPUP_TEMPLATES["paycheck"]["box"]
    noisy = put(blank(), (crop.astype(int) + np.random.RandomState(1).randint(-40, 40, crop.shape)).clip(0, 255).astype(np.uint8), box)
    score = macro.find_template(noisy, crop, box)[0]
    assert 0 < score < 1
    monkeypatch.setitem(macro.cfg.POPUP_TEMPLATES["paycheck"], "threshold", score - 0.01)
    assert macro.popup_visible(noisy, "paycheck")
    monkeypatch.setitem(macro.cfg.POPUP_TEMPLATES["paycheck"], "threshold", score + 0.01)
    assert not macro.popup_visible(noisy, "paycheck")
    # a stray window has no threshold of its own: STRAY_WINDOW_MATCH_THRESHOLD applies
    hub = put(blank(), reference(macro, "trading_hub"), macro.cfg.STRAY_WINDOWS["trading_hub"]["box"])
    monkeypatch.setattr(macro.cfg, "STRAY_WINDOW_MATCH_THRESHOLD", 1.01)
    assert not macro.popup_visible(hub, "trading_hub")


def test_a_name_with_no_crop_file_is_never_found(macro, monkeypatch):
    monkeypatch.setitem(macro.cfg.POPUP_TEMPLATES, "no_such_crop", {"box": (0, 0, 100, 100), "threshold": 0.5})
    assert macro.popup_found(blank(), "no_such_crop") is None


def test_the_only_pets_message_is_found_by_its_letters_over_any_background(macro):
    crop = reference(macro, "only_pets")
    letters = crop.min(axis=2) >= macro.cfg.ONLY_PETS_WHITE
    box = macro.cfg.ONLY_PETS_BOX
    assert macro.only_pets_message_visible(put(blank(), crop, box))
    noisy = np.random.default_rng(3).integers(0, 200, BLACK, dtype=np.uint8)                  # a busy world behind the text
    top, left = box[1] + 20, box[0] + 40
    patch = noisy[top:top + crop.shape[0], left:left + crop.shape[1]]
    patch[letters] = 255
    assert macro.only_pets_message_visible(noisy)
    assert not macro.only_pets_message_visible(blank())
    assert not macro.only_pets_message_visible(np.random.default_rng(4).integers(0, 255, BLACK, dtype=np.uint8))
    assert not macro.only_pets_message_visible(np.full(BLACK, 255, np.uint8))                 # nor is a white screen


def screenshot(name):
    import cv2
    return cv2.imread(os.path.join(os.path.dirname(__file__), "popups", f"{name}.png"))




def test_the_big_friend_pill_under_the_cafe_banner_is_no_backpack_but_a_purple_frame_with_a_white_grid_is(macro):
    cfg = macro.cfg
    assert not macro.detect_backpack_open(screenshot("cafe_banner_friend_pill"))            # (it was: "backpack still open after pressing the backpack key twice")
    drawn = blank()
    left, top, right, bottom = cfg.BACKPACK_NORMAL_PANEL_BOX
    purple = tuple(reversed(cfg.BACKPACK_NORMAL_COLOR))
    drawn[top:bottom, left:left + 2] = purple                                        # the frame's colour at the two sides of the panel (a few hundred px, as the real one has)...
    drawn[top:bottom, right - 2:right] = purple
    wl, wt, wr, wb = cfg.BACKPACK_NORMAL_WHITE_BOX
    drawn[wt:wb, wl:wr] = 255                                                                # ...and the item grid's white in the middle of it
    assert macro.detect_backpack_normal(drawn)
    blob = drawn.copy()
    blob[top:bottom, left:left + cfg.BACKPACK_NORMAL_SIDE_WIDTH] = 255                      # no frame at the left: only a blob of purple
    assert not macro.detect_backpack_normal(blob)
    banner = drawn.copy()
    banner[top:bottom, left:right] = purple                                                  # a banner's worth of purple (run 5's Cat Cafe: 2000 px and more) is no frame
    banner[wt:wb, wl:wr] = 255
    assert not macro.detect_backpack_normal(banner)
    assert not macro.detect_backpack_open(screenshot("cat_cafe_banner_no_backpack"))          # (run 5's frame: it passed for a backpack twice)




def test_the_cant_spawn_a_vehicle_window_is_closed_where_its_okay_is(macro, monkeypatch):
    clicks = []
    monkeypatch.setattr(macro, "grab_screen", lambda: screenshot("cant_spawn_vehicle"))
    monkeypatch.setattr(macro, "hover_click", lambda x, y, duration=0: clicks.append((x, y)))
    monkeypatch.setattr(macro.time, "sleep", lambda d: None)
    assert macro.dismiss_stray_windows() == "cant_spawn_vehicle"
    assert clicks == [macro.cfg.STRAY_WINDOWS["cant_spawn_vehicle"]["close_pos"]]
    clicks.clear()
    for name in ("teleport_nursery", "teleport_dealership", "backpack_open"):                # the teleport dialogs are no stray windows: they are the teleport's to answer
        monkeypatch.setattr(macro, "grab_screen", lambda name=name: screenshot(name))
        assert macro.dismiss_stray_windows() is None
    assert clicks == []


def test_all_the_variants_are_scored_against_all_the_saved_icons_at_once_to_the_scores_of_one_by_one(macro):
    rng = np.random.default_rng(3)
    size = macro.cfg.ICON_COMPARE_SIZE
    shape = (size[1], size[0] // 2)                                                         # (a signature is the left half)
    live = [rng.random(shape).astype(np.float32) for _ in range(5)]
    saved = [rng.random(shape).astype(np.float32) for _ in range(4)]
    mask = macro.icon_match_mask(size, macro.cfg.ICON_MATCH_RADIUS_FRACTION)
    scores = macro.score_variants(live, saved)
    assert scores.shape == (5, 4)
    for i, a in enumerate(live):
        for j, b in enumerate(saved):
            assert scores[i, j] == pytest.approx(1.0 - float(np.sum(((a - b) ** 2) * mask) / np.count_nonzero(mask)), abs=1e-6)


def test_a_white_screen_is_told_by_every_channel_being_light_over_most_of_the_picture(macro):
    white = np.full((100, 100, 3), 255, np.uint8)
    assert macro.detect_white_screen(white) is True
    mostly = white.copy()
    mostly[:, :20] = 0                                                                      # (80 % light)
    assert macro.detect_white_screen(mostly) is (0.8 >= macro.cfg.WHITE_SCREEN_MIN_FRACTION)
    tinted = white.copy()
    tinted[:, :, 0] = 200                                                                   # (one channel under the level: not white)
    assert macro.detect_white_screen(tinted) is False
    assert macro.detect_white_screen(np.zeros((100, 100, 3), np.uint8)) is False


def test_the_notice_that_the_pet_out_is_full_grown_is_closed_by_its_okay_and_asks_for_another_pet(macro, monkeypatch):
    screen = screenshot("pet_full_grown_notice")
    monkeypatch.setattr(macro, "grab_screen", lambda: screen)
    monkeypatch.setattr(macro.time, "sleep", lambda s: None)
    macro.update_game_config(lambda c: c.update(pet_repicked=macro.time.time()))
    assert macro.dismiss_stray_windows() == "pet_full_grown_notice"
    x, y = macro.clicks[0]
    assert 900 <= x <= 1020 and 630 <= y <= 680                                                    # (its Okay button)
    assert macro.load_game_config()["pet_repicked"] == 0.0                                          # (the pet is picked again at the next cycle)


def test_the_age_up_screen_that_stays_over_the_game_is_closed_by_its_nice_button_but_not_while_the_pen_has_it(macro, monkeypatch):
    screen = screenshot("age_up_stuck")
    monkeypatch.setattr(macro, "grab_screen", lambda: screen)
    monkeypatch.setattr(macro.time, "sleep", lambda s: None)
    macro.state.task_board_wanted = True
    assert macro.dismiss_stray_windows() is None and macro.clicks == []
    macro.state.task_board_wanted = False
    assert macro.dismiss_stray_windows() == "age_up_screen" and abs(macro.clicks[0][0] - 960) <= 20 and abs(macro.clicks[0][1] - 1016) <= 20
    monkeypatch.setattr(macro, "grab_screen", lambda: screenshot("paycheck"))
    macro.clicks.clear()
    assert macro.dismiss_stray_windows() is None                                                   # (no other screen is taken for it)


def test_the_pets_question_card_is_not_taken_for_anything_in_the_teleport_frames(macro):
    """A run lost 13 minutes behind the pet's question card, closed by nothing (the crop is cut from a frame of it)."""
    assert macro.popup_visible(screenshot("choose_card_screen"), "choose_card")
    for frame in ("house_before", "dialog_nursery", "dialog_dealership", "white_first", "red_room", "red_room_stuck", "park_landing", "hospital_lobby", "house_idle"):
        image = cv2.imread(os.path.join(os.path.dirname(__file__), "teleport", f"{frame}.png"))
        assert not macro.popup_visible(image, "choose_card"), frame


def test_the_pets_question_card_is_left_alone_while_the_pets_view_is_wanted(macro, monkeypatch):
    monkeypatch.setattr(macro, "grab_screen", lambda: screenshot("choose_card_screen"))
    monkeypatch.setattr(macro.time, "sleep", lambda s: None)
    macro.state.pet_focus_wanted = True
    assert macro.dismiss_stray_windows() is None and macro.clicks == []
    macro.state.pet_focus_wanted = False
    assert macro.dismiss_stray_windows() == "choose_card" and macro.clicks == [(869, 659)]


# --- notices: the popups of an event, closed by their one green button, with no crop of them -------------------------------------------------------------

def notice_screen(macro, button=((782, 635), (1142, 685)), red=None, extra_green=None):
    """A dark screen with a card in the middle: a wide green button (Awesome!-sized by default, with white text on it) and optionally a red one beside it."""
    screen = np.full((1080, 1920, 3), 30, np.uint8)
    screen[420:700, 720:1200] = 255
    (left, top), (right, bottom) = button
    screen[top:bottom, left:right] = (85, 198, 74)                                           # (BGR of GREEN_BUTTON_COLOR)
    screen[top + 15:bottom - 15, left + 120:right - 120] = 255
    if red:
        (rl, rt), (rr, rb) = red
        screen[rt:rb, rl:rr] = (60, 50, 215)
    return screen


def test_a_lone_wide_green_button_in_the_middle_is_a_notice_whatever_it_says(macro):
    assert macro.find_notice_button(notice_screen(macro)) == (962, 660)
    assert macro.find_notice_button(notice_screen(macro, button=((880, 635), (1040, 675)))) == (960, 655)       # (a Thanks!-sized one)


def test_a_question_a_backpack_and_an_empty_screen_are_no_notice(macro):
    assert macro.find_notice_button(notice_screen(macro, button=((960, 635), (1140, 685)), red=((782, 635), (955, 685)))) is None      # (a No beside a Yes)
    assert macro.find_notice_button(np.full((1080, 1920, 3), 30, np.uint8)) is None
    for name in ("backpack_open", "teleport_nursery", "teleport_dealership", "paycheck", "cant_spawn_vehicle", "task_board_alerts", "star_rewards_claim"):
        assert macro.find_notice_button(screenshot(name)) is None or name in ("paycheck", "star_rewards_claim", "cant_spawn_vehicle"), name        # (those three are notices with a green button of their own, closed by their own entries first)
    for frame in ("house_before", "dialog_nursery", "dialog_dealership", "white_first", "red_room", "red_room_stuck", "park_landing", "hospital_lobby", "house_idle"):
        image = cv2.imread(os.path.join(os.path.dirname(__file__), "teleport", f"{frame}.png"))
        assert macro.find_notice_button(image) is None, frame


def test_a_notice_is_closed_by_its_button_reported_once_and_left_alone_when_it_does_not_go(macro, monkeypatch):
    clicks, events, reports = [], [], []
    monkeypatch.setattr(macro, "grab_screen", lambda: notice_screen(macro))
    monkeypatch.setattr(macro, "hover_click", lambda x, y, duration=0: clicks.append((x, y)))
    monkeypatch.setattr(macro.time, "sleep", lambda s: None)
    monkeypatch.setattr(macro, "log_run_event", events.append)
    monkeypatch.setattr(macro, "save_failure_report", lambda reason: reports.append(reason))
    for _ in range(macro.cfg.NOTICE_MAX_CLICKS):
        assert macro.dismiss_notice() == "notice"
    assert clicks == [(962, 660)] * macro.cfg.NOTICE_MAX_CLICKS and len(reports) == 1                 # (the screenshot of the first, not of every one)
    assert macro.dismiss_notice() is None and len(clicks) == macro.cfg.NOTICE_MAX_CLICKS             # (still there after that many: not a notice, left alone)
    assert any("left alone" in line for line in events)


def test_a_notice_is_not_looked_for_while_something_is_open_on_purpose(macro, monkeypatch):
    monkeypatch.setattr(macro, "grab_screen", lambda: notice_screen(macro))
    monkeypatch.setattr(macro, "hover_click", lambda x, y, duration=0: macro.clicks.append((x, y)))
    for attribute, value in (("backpack_wanted", 1), ("pet_focus_wanted", True), ("task_board_wanted", True)):
        setattr(macro.state, attribute, value)
        assert macro.dismiss_notice() is None, attribute
        setattr(macro.state, attribute, 0 if attribute == "backpack_wanted" else False)
