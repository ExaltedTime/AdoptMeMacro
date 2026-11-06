"""Doors, respawns and teleports are waited out by their white screen, not by a fixed time (wait_for_white_to_pass())."""
import numpy as np
import pytest

WHITE = np.full((1080, 1920, 3), 255, np.uint8)
SCENE = np.full((1080, 1920, 3), 60, np.uint8)


@pytest.fixture
def clock(macro, monkeypatch):
    """The game as a list of frames, one per look; the fake time advances by what is waited."""
    now = [1000.0]
    frames: list = []
    waited: list[float] = []
    monkeypatch.setattr(macro.time, "time", lambda: now[0])

    def wait(seconds):
        waited.append(seconds)
        now[0] += seconds
    monkeypatch.setattr(macro, "wait_interruptible", wait)
    monkeypatch.setattr(macro, "grab_screen", lambda: frames.pop(0) if len(frames) > 1 else frames[0])
    return frames, waited


def test_a_transition_is_over_when_its_white_screen_has_gone_and_not_after_a_fixed_time(macro, clock):
    frames, waited = clock
    frames[:] = [SCENE, WHITE, WHITE, WHITE, SCENE, SCENE]
    assert macro.wait_for_white_to_pass(static=10.0) is True
    assert sum(waited) < 3.0                                                                      # (not the 10 s of the old wait: 5 looks and the settle)
    assert waited[-1] == macro.cfg.WHITE_SETTLE


def test_a_flicker_of_one_look_is_not_the_end_and_a_white_screen_that_never_goes_stops_at_the_maximum(macro, clock):
    frames, waited = clock
    frames[:] = [WHITE, SCENE, WHITE, WHITE, SCENE, SCENE]
    assert macro.wait_for_white_to_pass(static=10.0) is True and len(frames) == 1 and sum(waited) < 3.0       # (one look that is not white after it is white is not enough)
    frames[:] = [WHITE]
    waited.clear()
    assert macro.wait_for_white_to_pass(static=10.0) is True and macro.cfg.WHITE_PASS_MAX <= sum(waited) < macro.cfg.WHITE_PASS_MAX + 1


def test_without_a_white_screen_the_wait_is_the_old_fixed_one_and_no_longer(macro, clock):
    frames, waited = clock
    frames[:] = [SCENE]
    assert macro.wait_for_white_to_pass(static=4.0) is False
    assert 3.9 <= sum(waited) <= 4.0 + macro.cfg.WHITE_POLL                                       # (4 s in all, the looks counted in it)
    waited.clear()
    assert macro.wait_for_white_to_pass(static=1.0) is False and sum(waited) <= macro.cfg.WHITE_LOOK + macro.cfg.WHITE_POLL


def test_the_respawn_the_teleport_and_the_pens_door_wait_for_it(macro, monkeypatch):
    calls = []
    monkeypatch.setattr(macro, "wait_for_white_to_pass", lambda static, look=2.0: calls.append((static, look)) or True)
    monkeypatch.setattr(macro, "focus_roblox_click", lambda: True)
    monkeypatch.setattr(macro, "key_down", lambda key: None)
    monkeypatch.setattr(macro, "key_up", lambda key: None)
    monkeypatch.setattr(macro.time, "sleep", lambda s: None)
    monkeypatch.setattr(macro, "dismiss_stray_windows", lambda: None)
    macro.respawn_character()
    assert calls == [(macro.cfg.RESPAWN_WAIT, 2.0)]
    calls.clear()
    monkeypatch.setattr(macro, "respawn_character", lambda: None)
    monkeypatch.setattr(macro, "hold_key", lambda key, seconds: None)
    monkeypatch.setattr(macro.pydirectinput, "press", lambda key: None, raising=False)
    monkeypatch.setattr(macro, "wait_interruptible", lambda s: None)
    monkeypatch.setattr(macro, "look_until", lambda *a, **k: (1, 1))
    assert macro.open_pet_pen() is True
    assert len(calls) == 1 and calls[0][0] == 5.0                                                 # (the door of the route: 5 s only when no white screen shows)
