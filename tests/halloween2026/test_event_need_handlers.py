"""The tests of need_handlers that are about the event (Halloween 2026): scripts/Halloween2026.py, which the macro does not import."""
import os
import types
import pytest
from scripts import Halloween2026 as hw  # noqa: E402


def exit_screen(macro, left="red", right="grey", overlay=True):
    """A 1920x1080 frame with the dialog's two buttons in the colours given (BGR drawn from the HSV the detector reads) and, if `overlay`, a marker for the minigame."""
    import numpy as np
    img = np.full((macro.cfg.REFERENCE_HEIGHT, macro.cfg.REFERENCE_WIDTH, 3), 235, np.uint8)         # (a pale scene)
    colours = {"red": (60, 40, 216), "grey": (70, 65, 62), "green": (85, 198, 74)}                         # BGR
    title = macro.load_templates(macro.cfg.STRAY_WINDOW_DIR)["exit_game"]
    left_, top_, *_ = macro.cfg.POPUP_TEMPLATES["exit_game"]["box"]
    img[top_ + 20:top_ + 20 + title.shape[0], left_ + 20:left_ + 20 + title.shape[1]] = title
    for box, name in ((hw.EXIT_DIALOG_CANCEL_BOX, left), (hw.EXIT_DIALOG_RIGHT_BOX, right)):
        if name:
            l, t, r, b = box
            img[t:b, l:r] = colours[name]
    return img


def real_frame(name):
    import cv2
    return cv2.imread(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "exit_hub", name + ".png"))


def test_real_screenshots_the_exit_dialog_is_found_in_both_states_the_exit_button_marks_a_minigame_and_the_hub_is_told_from_the_rest(macro):
    counting, ready = real_frame("exit_dialog_counting"), real_frame("exit_dialog_ready")
    assert hw.detect_exit_dialog(counting) == "counting" and hw.detect_exit_dialog(ready) == "ready"
    button, hub, hub2 = real_frame("exit_minigame_button"), real_frame("trading_hub_island"), real_frame("trading_hub_island_2")
    assert hw.detect_exit_dialog(button) is None and hw.detect_exit_dialog(hub) is None
    assert all(hw.in_minigame(frame) for frame in (counting, ready, button)) and not hw.in_minigame(hub)
    assert macro.in_trading_hub(hub) and macro.in_trading_hub(hub2)
    assert macro.popup_visible(hub, "backpack_button") and macro.popup_visible(hub2, "backpack_button") and not macro.popup_visible(button, "backpack_button")
    assert not any(macro.in_trading_hub(frame) for frame in (counting, ready, button))


def test_the_exit_dialog_is_found_by_the_colours_of_its_buttons_and_says_whether_it_is_still_counting(macro):
    assert hw.detect_exit_dialog(exit_screen(macro, "red", "grey")) == "counting"
    assert hw.detect_exit_dialog(exit_screen(macro, "red", "green")) == "ready"
    assert hw.detect_exit_dialog(exit_screen(macro, None, "green")) is None                              # no Cancel
    assert hw.detect_exit_dialog(exit_screen(macro, "red", None)) is None                                # nothing at its right
    assert hw.detect_exit_dialog(exit_screen(macro, "green", "green")) is None and hw.detect_exit_dialog(exit_screen(macro, None, None)) is None


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
    plain = exit_screen(macro, None, None)
    leaving.frames.extend([plain, plain, exit_screen(macro, "red", "grey"), exit_screen(macro, "red", "grey"), exit_screen(macro, "red", "green"), plain])
    state_of = iter([True, False])                                                                           # (in a minigame at first; the overlay is gone after the click)
    monkeypatch.setattr(hw, "in_minigame", lambda img: next(state_of, False))
    assert hw.exit_minigame() is True
    assert leaving.log == [hw.EXIT_MINIGAME_POS, hw.EXIT_GAME_POS, "minigame exited"]
    leaving.log.clear()
    leaving.frames[:] = [plain]
    monkeypatch.setattr(hw, "in_minigame", lambda img: False)                                             # not in one (Exit Home is in the same place): nothing clicked
    assert hw.exit_minigame() is True and leaving.log == []


def test_a_dialog_that_is_up_already_is_not_opened_again_and_a_step_that_does_not_happen_is_said(macro, leaving, monkeypatch):
    plain = exit_screen(macro, None, None)
    ready = exit_screen(macro, "red", "green")
    leaving.frames[:] = [ready, ready, ready, plain]                                                           # (the first look, the wait for the dialog, for the green, and then it is gone)
    monkeypatch.setattr(hw, "in_minigame", lambda img: False)                                             # (no overlay under the dialog is not needed to go on)
    assert hw.exit_minigame() is True and leaving.log == [hw.EXIT_GAME_POS, "minigame exited"]
    leaving.log.clear()
    leaving.frames[:] = [plain]                                                                              # the button is clicked and no dialog comes
    monkeypatch.setattr(hw, "in_minigame", lambda img: True)
    assert hw.exit_minigame() is False and leaving.log == [hw.EXIT_MINIGAME_POS, "exit minigame: the dialog did not come up"]
    leaving.log.clear()
    leaving.frames[:] = [exit_screen(macro, "red", "grey")]                                                  # it counts for ever
    assert hw.exit_minigame() is False and leaving.log == ["exit minigame: the Exit Game button did not turn green"]
    leaving.log.clear()
    leaving.frames[:] = [exit_screen(macro, "red", "green")]                                                 # the minigame stays
    assert hw.exit_minigame() is False and leaving.log == [hw.EXIT_GAME_POS, "exit minigame: the minigame is still there"]


def test_sloop_leaves_the_hub_and_a_minigame_before_setup_and_one_that_cannot_be_left_is_an_error_with_no_setup(macro, monkeypatch):
    order = []
    monkeypatch.setattr(macro, "leave_hub_if_in", lambda: order.append("hub") or True)
    monkeypatch.setattr(hw, "exit_minigame", lambda: order.append("exit") or True)
    monkeypatch.setattr(macro, "setup_game", lambda: order.append("setup") or True)
    assert hw.exit_minigame_and_setup() is True and order == ["hub", "exit", "setup"]
    order.clear()
    monkeypatch.setattr(hw, "exit_minigame", lambda: order.append("exit") or False)
    with pytest.raises(RuntimeError, match="could not leave the minigame"):
        hw.exit_minigame_and_setup()
    assert order == ["hub", "exit"]
    order.clear()
    monkeypatch.setattr(macro, "leave_hub_if_in", lambda: order.append("hub") or False)
    with pytest.raises(RuntimeError, match="could not leave the Trading Hub"):
        hw.exit_minigame_and_setup()
    assert order == ["hub"]




def test_leaving_a_minigame_on_purpose_keeps_the_stray_window_check_off_the_dialog_and_lets_go_after(macro, leaving, monkeypatch):
    seen = []
    monkeypatch.setattr(hw, "_exit_minigame", lambda: seen.append(hw.event.exit_dialog_wanted) or True)
    assert hw.exit_minigame() is True and seen == [True] and hw.event.exit_dialog_wanted is False
    monkeypatch.setattr(hw, "_exit_minigame", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        hw.exit_minigame()
    assert hw.event.exit_dialog_wanted is False
