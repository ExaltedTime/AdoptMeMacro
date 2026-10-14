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


def test_the_debug_image_is_still_saved_when_the_search_is_skipped(macro, icon_screen, monkeypatch, tmp_path):
    screen, searches = icon_screen
    monkeypatch.setattr(macro.cfg, "IMAGES_DIR", str(tmp_path))
    ring(screen["img"], (700, 80))
    macro.detect_need_icons(save_debug=True)
    (tmp_path / "debug_needs.png").unlink()
    macro.detect_need_icons(save_debug=True)
    assert len(searches) == 1 and (tmp_path / "debug_needs.png").exists()


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


@pytest.mark.parametrize("name", ["trading_hub", "star_rewards", "respawn_confirm", "character_menu", "cant_spawn_vehicle", "profile"])
def test_stray_windows_are_recognised_and_closed(macro, monkeypatch, name):
    config = macro.cfg.STRAY_WINDOWS[name]
    screen = put(blank(), reference(macro, name), config["box"])
    monkeypatch.setattr(macro, "grab_screen", lambda: screen)
    monkeypatch.setattr(macro.time, "sleep", lambda s: None)
    assert macro.dismiss_stray_windows() == name
    assert config["close_pos"] in macro.clicks


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
    assert macro.unscrew(in_task=True, play_minigames=False) is None
    assert macro.cfg.STRAY_WINDOWS["cant_spawn_vehicle"]["close_pos"] in macro.clicks


def test_backpack_title_marks_the_normal_backpack(macro):
    title = reference(macro, "backpack_normal")
    screen = put(blank(), title, macro.cfg.POPUP_TEMPLATES["backpack_normal"]["box"])
    assert macro.detect_backpack_normal(screen) and macro.detect_backpack_open(screen)
    assert not macro.detect_backpack_open(blank())


def with_popup_text(macro, img):
    return put(img, reference(macro, "minigame_popup"), macro.cfg.POPUP_TEMPLATES["minigame_popup"]["box"])


def test_minigame_popup_is_a_green_yes_with_a_red_no_beside_it_under_its_teleport_text(macro):
    yes, no = macro.cfg.GREEN_BUTTON_COLOR, macro.cfg.MINIGAME_POPUP_NO_COLOR
    img = with_popup_text(macro, blank())
    img[600:640, 1000:1140] = yes[::-1]          # the colours are RGB, the image BGR
    assert not macro.detect_minigame_popup(img)  # Yes alone (a paycheck's button, say) isn't one
    img[600:640, 800:940] = no[::-1]
    assert macro.detect_minigame_popup(img)
    elsewhere = blank()                          # another dialog with the same two colours and no "Teleport there now?": not the popup
    elsewhere[600:640, 1000:1140] = yes[::-1]
    elsewhere[600:640, 800:940] = no[::-1]
    assert not macro.detect_minigame_popup(elsewhere)


def test_sick_dialog_is_not_a_minigame_popup(macro):
    """The doctor's Yes/No dialog has the same two colours, but it is the sick handler's to answer."""
    yes, no = macro.cfg.GREEN_BUTTON_COLOR, macro.cfg.MINIGAME_POPUP_NO_COLOR
    screen = with_popup_text(macro, blank())
    screen[600:640, 1000:1140] = yes[::-1]
    screen[600:640, 800:940] = no[::-1]
    assert macro.detect_minigame_popup(screen)                          # without the dialog's title it is a popup...
    put(screen, reference(macro, "sick_dialog"), macro.cfg.POPUP_TEMPLATES["sick_dialog"]["box"])
    assert not macro.detect_minigame_popup(screen)                      # ...with it, it is ignored


def test_the_learned_need_icons_load_from_ref_needs(macro):
    assert macro.cfg.NEEDS_DIR.endswith(os.path.join("ref", "needs"))
    names = {name for name, _ in macro.need_signatures()}
    assert {"hungry", "thirsty", "pizza"} <= names
    assert {"diving", "puddle"} <= names                       # the subfolder (ref/needs/weather/) is searched too


def test_templates_are_read_once_and_reread_when_a_file_changes(macro, tmp_path, monkeypatch):
    folder = tmp_path / "crops"
    folder.mkdir()
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


POPUP_SCREENSHOTS = {                    # real screenshots of a 1920x1080 window (tests/popups): name -> (is a minigame popup, which one)
    "hauntlet_house": (True, "hauntlet_2"),
    "ghost_house": (True, "ghost_gallery"),
    "ghost_over_backpack": (True, "ghost_gallery"),      # the open backpack behind it has green buttons too: it was not found, with all the green taken together
    "paycheck": (False, None),                           # the paycheck's green CASH OUT, a green Okay, the win screen's NICE!: no red No beside them
    "star_rewards": (False, None),
    "game_over": (False, None),
}


def test_minigame_popups_are_found_and_named_in_real_screenshots_and_other_green_buttons_are_not(macro):
    import cv2
    for name, (popup, which) in POPUP_SCREENSHOTS.items():
        img = cv2.imread(os.path.join(os.path.dirname(__file__), "popups", f"{name}.png"))
        assert macro.detect_minigame_popup(img) is popup, name
        if popup:
            assert macro.identify_minigame(img) == which, name
