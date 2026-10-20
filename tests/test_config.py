"""The real need tables in magic_numbers.py: their shape, and that the handler carries out whatever they say.

The handler tests elsewhere use made-up entries (so they pass for any tuning). These check the real ones without
pinning any value: a typo in a key, a missing position or a step that isn't a movement key fails here, while
retuning a duration or a route doesn't. They run over whichever needs exist (HALLOWEEN decides whether `sick` does)."""
import pytest

import magic_numbers as cfg

ENTRY_KEYS = {"teleport_pos", "steps", "helicopter", "max_wait", "final_click"}
NEEDS = sorted(cfg.TELEPORT_WALK_NEEDS)


def on_screen(pos):
    return (isinstance(pos, tuple) and len(pos) == 2 and all(isinstance(v, int) for v in pos)
            and 0 <= pos[0] < cfg.REFERENCE_WIDTH and 0 <= pos[1] < cfg.REFERENCE_HEIGHT)


@pytest.mark.parametrize("name", NEEDS)
def test_a_teleport_walk_entry_is_well_formed(name):
    entry = cfg.TELEPORT_WALK_NEEDS[name]
    assert set(entry) <= ENTRY_KEYS, f"unknown key(s): {set(entry) - ENTRY_KEYS}"
    assert on_screen(entry["teleport_pos"])
    assert entry["steps"], "a need with nothing to do"
    for key, seconds in entry["steps"]:
        assert key in cfg.MOVE_KEYS and isinstance(seconds, (int, float)) and seconds > 0
    assert isinstance(entry.get("helicopter", False), bool)
    assert entry.get("max_wait", 1) > 0
    assert "final_click" not in entry or on_screen(entry["final_click"])


def test_the_two_need_lists_do_not_overlap():
    assert not set(cfg.TELEPORT_WALK_NEEDS) & set(cfg.BUTTON_NAMES)


@pytest.mark.parametrize("name", NEEDS)
def test_every_teleport_walk_need_has_a_handler(macro, name):
    assert name in macro.NEED_HANDLER_CLASSES
    assert isinstance(macro.NEED_HANDLER_CLASSES[name](), macro.TeleportWalkNeedHandler)


@pytest.mark.parametrize("name", NEEDS)
def test_the_handler_carries_out_the_real_entry(macro, monkeypatch, name):
    """Run through every real entry with the game stubbed: it must finish, and hold exactly the keys the entry lists."""
    entry = cfg.TELEPORT_WALK_NEEDS[name]
    held, teleports, waited = [], [], []
    monkeypatch.setattr(macro, "focus_roblox", lambda: True)
    monkeypatch.setattr(macro, "teleport_to", lambda pos: teleports.append(pos))
    monkeypatch.setattr(macro, "equip_favorite_vehicle", lambda: None)
    monkeypatch.setattr(macro, "hold_key", lambda key, duration: held.append((key, duration)))
    monkeypatch.setattr(macro, "wait_interruptible", lambda duration: None)
    monkeypatch.setattr(macro, "detect_sick_dialog", lambda img: True)
    monkeypatch.setattr(macro, "detect_pet_focus", lambda img: False)
    monkeypatch.setattr(macro, "find_doctor_prompt", lambda img: (10, 20))
    monkeypatch.setattr(macro, "grab_screen", lambda: None)
    monkeypatch.setattr(macro, "wait_until_need_gone", lambda name, max_wait=0, **kw: waited.append(max_wait))
    assert macro.TeleportWalkNeedHandler(name).handle() is True
    assert teleports == [entry["teleport_pos"]]
    flight = ([("w", cfg.HELICOPTER_FORWARD_DURATION), (cfg.KEYS["helicopter"], cfg.HELICOPTER_HOLD_DURATION)]
              if entry.get("helicopter", cfg.HELICOPTER_REQUIRED) else [])
    assert held == flight + list(entry["steps"])
    if "final_click" in entry:
        assert macro.clicks == [(10, 20), entry["final_click"]]            # the doctor's prompt, then the Yes
    assert waited == [entry.get("max_wait", cfg.NEED_GONE_MAX_WAIT)]
