"""The tests of state that are about the event (Halloween 2026): scripts/Halloween2026.py, which the macro does not import."""
import os
import types
import time
import pytest
from scripts import Halloween2026 as hw  # noqa: E402


def test_a_dismissed_minigame_popup_does_not_lose_the_abort(macro, monkeypatch):
    monkeypatch.setattr(macro, "rejoin_game", lambda kinds=None: False)
    monkeypatch.setattr(macro, "close_backpack_if_stuck", lambda in_task: False)
    monkeypatch.setattr(macro, "dismiss_stray_windows", lambda: "cant_spawn_vehicle")     # flagged abort_task
    monkeypatch.setattr(hw, "close_minigame_finish_screen", lambda abandon=True: False)
    monkeypatch.setattr(hw, "minigame_popup", lambda play=True: "dismissed")
    monkeypatch.setattr(macro, "detect_paycheck", lambda: False)
    assert macro.unscrew(in_task=True) == "aborted"






def test_a_failed_minigame_does_not_count_as_progress_but_a_won_one_does(macro, monkeypatch):
    stalled = time.time() - 100
    monkeypatch.setattr(macro, "release_all_inputs", lambda: None)
    monkeypatch.setattr(hw, "accept_minigame_popup", lambda: True)
    monkeypatch.setattr(macro, "hover_click", lambda *a, **k: None)
    monkeypatch.setattr(macro, "respawn_character", lambda: None)
    monkeypatch.setattr(hw, "click_nice_until_gone", lambda: True)
    monkeypatch.setattr(macro, "log_failure", lambda reason, screenshot=True: None)
    monkeypatch.setattr(hw, "read_candy_corn_settled", lambda tries=4: None)                  # (no screen to read the total off)
    for finished in (False, True):
        macro.state.last_progress = stalled
        monkeypatch.setitem(hw.MINIGAME_PLAYERS, "ghost_gallery", lambda deadline, watch, finished=finished: finished)
        assert hw.play_minigame("ghost_gallery") is finished
        assert (macro.state.last_progress == stalled) is (not finished)
        assert time.time() - macro.state.last_detection < 5                            # needs couldn't be seen meanwhile either way


@pytest.fixture
def popup_world(macro, world, monkeypatch):
    """A screen that follows the clicks on the popup's Yes: `popup_world.after(n_clicks)` -> what the screen shows after that many
    ('dark' = the same as before, 'white', 'other' = a different view); `.popup_up` says whether the popup is still on screen."""
    import numpy as np
    shown = {"dark": np.full((108, 192, 3), 30, np.uint8), "white": np.full((108, 192, 3), 255, np.uint8),     # (small: it is looked at hundreds of times)
             "other": np.full((108, 192, 3), 200, np.uint8)}
    scene = types.SimpleNamespace(after=lambda clicks: "dark", popup_up=lambda clicks: True)
    monkeypatch.setattr(macro, "grab_screen", lambda: shown[scene.after(len(macro.clicks))])
    monkeypatch.setattr(hw, "detect_minigame_popup", lambda img: scene.popup_up(len(macro.clicks)))
    return scene


def test_a_yes_that_teleports_is_clicked_once(macro, popup_world, world):
    popup_world.after = lambda clicks: "white" if clicks else "dark"
    assert hw.accept_minigame_popup() is True and len(macro.clicks) == 1
    del macro.clicks[:]
    popup_world.after = lambda clicks: "other" if clicks else "dark"                     # a teleport whose white screen was missed
    assert hw.accept_minigame_popup() is True and len(macro.clicks) == 1


def test_a_yes_that_did_not_take_is_clicked_again_while_the_popup_is_up(macro, popup_world, world):
    popup_world.after = lambda clicks: "white" if clicks >= 2 else "dark"
    assert hw.accept_minigame_popup() is True and len(macro.clicks) == 2
    del macro.clicks[:]
    popup_world.after = lambda clicks: "dark"                                              # never does
    assert hw.accept_minigame_popup() is False and len(macro.clicks) == hw.MINIGAME_YES_TRIES


def test_a_popup_that_is_gone_with_no_teleport_is_a_failed_start_at_once(macro, popup_world, world):
    popup_world.popup_up = lambda clicks: False
    start = world.clock.now
    assert hw.accept_minigame_popup() is False and len(macro.clicks) == 1
    assert world.clock.now - start < hw.MINIGAME_TELEPORT_WAIT + 2                  # one wait, not a minigame's start wait


def test_a_minigame_whose_popup_did_not_teleport_is_not_played_but_logged(macro, monkeypatch):
    events = []
    monkeypatch.setattr(hw, "accept_minigame_popup", lambda: False)
    monkeypatch.setattr(macro, "release_all_inputs", lambda: None)
    monkeypatch.setattr(macro, "respawn_character", lambda: events.append("respawn"))
    monkeypatch.setattr(macro, "log_failure", lambda reason, screenshot=True: events.append(reason))
    monkeypatch.setattr(hw, "start_run_capture", lambda label: events.append("capture"))
    monkeypatch.setitem(hw.MINIGAME_PLAYERS, "ghost_gallery", lambda deadline, watch: events.append("played"))
    assert hw.play_minigame("ghost_gallery") is False
    assert len(events) == 2 and "could not be started" in events[0] and events[1] == "respawn"       # no capture, no play


def test_a_minigame_popup_waits_for_a_need_in_its_end_wait_but_not_for_ever(macro, world, monkeypatch):
    played = []
    monkeypatch.setattr(macro, "grab_screen", lambda: None)
    monkeypatch.setattr(hw, "detect_minigame_popup", lambda img: True)
    monkeypatch.setattr(hw, "identify_minigame", lambda img: "ghost_gallery")
    monkeypatch.setattr(macro, "load_game_config", lambda: {"ghost_gallery_enabled": True})
    monkeypatch.setattr(hw, "play_minigame", lambda name: played.append(name))
    assert hw.minigame_popup() == "played" and played == ["ghost_gallery"]            # no need waiting: at once
    played.clear()
    with hw.end_wait("sick"):
        assert hw.minigame_popup() == "waiting" and not played                        # held off while it waits...
        world.clock.now += hw.MINIGAME_POPUP_END_WAIT - 1
        assert hw.minigame_popup() == "waiting" and not played
        world.clock.now += 2                                                             # ...but only so long
        assert hw.minigame_popup() == "played" and played == ["ghost_gallery"]
    assert hw.event.end_wait_need is None and hw.event.popup_held_since is None




def test_a_minigame_that_has_no_player_yet_is_declined_even_when_switched_on(macro, monkeypatch):
    import cv2
    monkeypatch.delitem(hw.MINIGAME_PLAYERS, "stitches_spotlight")                         # (one that is recognised but cannot be played)
    clicks, played = [], []
    monkeypatch.setattr(macro, "grab_screen", lambda: cv2.imread(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "popups", "stitches_spotlight.png")))
    monkeypatch.setattr(macro, "load_game_config", lambda: {"stitches_spotlight_enabled": True})
    monkeypatch.setattr(hw, "play_minigame", lambda name: played.append(name))
    monkeypatch.setattr(macro, "hover_click", lambda x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(macro, "log_run_event", lambda message: None)
    assert hw.identify_minigame(macro.grab_screen()) == "stitches_spotlight" and "stitches_spotlight" not in hw.MINIGAME_PLAYERS
    assert hw.minigame_popup() == "dismissed" and played == [] and clicks[-1] == hw.MINIGAME_POPUP_NO_POS


def test_the_start_wait_ends_when_the_overlay_shows_or_the_game_is_lost_not_only_when_it_runs_out(macro, monkeypatch):
    looks = []
    monkeypatch.setattr(hw, "minigame_wait", lambda duration, watch: looks.append(duration))
    watch = hw.MinigameWatch("ghost_gallery")
    watch.last_seen = time.time()                                                  # the overlay is up: no waiting at all
    hw.wait_for_minigame_start(watch, "ghost_gallery")
    assert looks == [] and watch.start_grace_from is not None
    watch = hw.MinigameWatch("ghost_gallery")
    watch.disconnected = True                                                      # a disconnect in the lobby: the rejoin should not wait for the whole start wait
    hw.wait_for_minigame_start(watch, "ghost_gallery")
    assert looks == []
    watch = hw.MinigameWatch("ghost_gallery")                                   # nothing yet: it looks, at the interval, until the overlay shows
    monkeypatch.setattr(hw, "minigame_wait", lambda duration, w: (looks.append(duration), setattr(w, "last_seen", time.time())))
    hw.wait_for_minigame_start(watch, "ghost_gallery")
    assert looks == [hw.MINIGAME_START_LOOK_INTERVAL]
