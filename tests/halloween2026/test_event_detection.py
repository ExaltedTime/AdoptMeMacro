"""The tests of detection that are about the event (Halloween 2026): scripts/Halloween2026.py, which the macro does not import."""
import os
import numpy as np
from scripts import Halloween2026 as hw  # noqa: E402


BLACK = (1080, 1920, 3)


def blank():
    return np.zeros(BLACK, np.uint8)


def put(img, crop, box):
    """Paste `crop` at the top-left of `box` (left, top, right, bottom)."""
    left, top = box[0], box[1]
    img[top:top + crop.shape[0], left:left + crop.shape[1]] = crop
    return img


def reference(macro, name):
    crop = macro.load_templates(hw.POPUP_DIR).get(name)
    if crop is None:
        crop = macro.load_templates(macro.cfg.STRAY_WINDOW_DIR).get(name)
    assert crop is not None, f"ref/popups/{name}.png is missing"
    return crop


def test_the_finish_screens_of_both_minigames_are_recognised_in_real_screenshots_and_nothing_else_is(macro):
    for name in ("finish_screen_ghost_gallery", "finish_screen_hauntlet_2", "game_over"):            # (the second has the next game's countdown over the banner's top)
        assert hw.detect_minigame_finished(screenshot(name)) is True, name
    for name in ("trading_hub", "task_board_alerts", "star_rewards", "star_rewards_claim", "hauntlet_house", "ghost_house", "paycheck", "backpack_open"):
        assert hw.detect_minigame_finished(screenshot(name)) is False, name


def with_popup_text(macro, img):
    return put(img, reference(macro, "minigame_popup"), hw.POPUP_TEMPLATES_EVENT["minigame_popup"]["box"])


def test_minigame_popup_is_a_green_yes_with_a_red_no_beside_it_under_its_teleport_text(macro):
    yes, no = macro.cfg.GREEN_BUTTON_COLOR, hw.MINIGAME_POPUP_NO_COLOR
    img = with_popup_text(macro, blank())
    img[600:640, 1000:1140] = yes[::-1]          # the colours are RGB, the image BGR
    assert not hw.detect_minigame_popup(img)  # Yes alone (a paycheck's button, say) isn't one
    img[600:640, 800:940] = no[::-1]
    assert hw.detect_minigame_popup(img)
    elsewhere = blank()                          # another dialog with the same two colours and no "Teleport there now?": not the popup
    elsewhere[600:640, 1000:1140] = yes[::-1]
    elsewhere[600:640, 800:940] = no[::-1]
    assert not hw.detect_minigame_popup(elsewhere)


def test_sick_dialog_is_not_a_minigame_popup(macro):
    """The doctor's Yes/No dialog has the same two colours, but it is the sick handler's to answer."""
    yes, no = macro.cfg.GREEN_BUTTON_COLOR, hw.MINIGAME_POPUP_NO_COLOR
    screen = with_popup_text(macro, blank())
    screen[600:640, 1000:1140] = yes[::-1]
    screen[600:640, 800:940] = no[::-1]
    assert hw.detect_minigame_popup(screen)                          # without the dialog's title it is a popup...
    put(screen, reference(macro, "sick_dialog"), macro.cfg.POPUP_TEMPLATES["sick_dialog"]["box"])
    assert not hw.detect_minigame_popup(screen)                      # ...with it, it is ignored


POPUP_SCREENSHOTS = {                    # real screenshots of a 1920x1080 window (tests/popups): name -> (is a minigame popup, which one)
    "hauntlet_house": (True, "hauntlet_2"),
    "ghost_house": (True, "ghost_gallery"),
    "stitches_spotlight": (True, "stitches_spotlight"),   
    "ghost_over_backpack": (True, "ghost_gallery"),      # the open backpack behind it has green buttons too: it was not found, with all the green taken together
    "paycheck": (False, None),                           # the paycheck's green CASH OUT, a green Okay, the finish screen's NICE!: no red No beside them
    "star_rewards": (False, None),                       # the calendar view (real screenshot, lossless)
    "star_rewards_claim": (False, None),                 # the view that opens by itself once a day, with its green CLAIM! button
    "game_over": (False, None),
    "teleport_nursery": (False, None),                   # the macro's own teleport dialogs (backpack -> category -> Yes) have a green Yes and a red No too: 30 of them were
    "teleport_dealership": (False, None),                # taken for a minigame popup and "dismissed" in a run, which broke the teleports and the needs after them
    "cant_spawn_vehicle": (False, None),                 # "You can't spawn a vehicle here." with its green Okay
    "backpack_open": (False, None),
}


def test_minigame_popups_are_found_and_named_in_real_screenshots_and_other_green_buttons_are_not(macro):
    import cv2
    for name, (popup, which) in POPUP_SCREENSHOTS.items():
        img = cv2.imread(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "popups", f"{name}.png"))
        assert hw.detect_minigame_popup(img) is popup, name
        if popup:
            assert hw.identify_minigame(img) == which, name


def screenshot(name):
    import cv2
    return cv2.imread(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "popups", f"{name}.png"))


def test_the_disconnected_dialog_ends_a_minigame_watch_and_its_clean_screens_do_not(macro):
    """Real frame of Hauntlet 2's start (Error Code 277): the dialog stayed up for 90 s, the minigame not noticing."""
    watch = hw.MinigameWatch("hauntlet_2")
    for name in ("backpack_open", "game_over", "paycheck"):
        watch.update(screenshot(name))
    assert watch.problem is None and not watch.disconnected
    watch.update(screenshot("disconnected_error_277"))
    assert watch.disconnected and watch.problem == "disconnected from the game"


def test_an_open_backpack_is_seen_in_a_real_screenshot(macro):
    assert macro.detect_backpack_open(screenshot("backpack_open"))
    for name in ("teleport_nursery", "teleport_dealership", "cant_spawn_vehicle"):          # (these have the backpack's expanded grid behind them: the dialog is over it)
        assert hw.detect_minigame_popup(screenshot(name)) is False
