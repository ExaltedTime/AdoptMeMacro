"""Test setup: stands in for the libraries that only work on Windows or with a
screen (pyautogui, pydirectinput, mss, pygetwindow - and tkinter if it isn't
installed), so main.py can be imported anywhere, and gives each test its own
fresh State/RunStats and its own log/debug folders."""
import contextlib
import copy
import importlib
import os
import sys
import types
from typing import Any

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class _Anything:
    """Any attribute, call or iteration works and gives another _Anything."""
    def __getattr__(self, name):
        return _Anything()

    def __call__(self, *args, **kwargs):
        return _Anything()

    def __iter__(self):
        return iter(())


def _stub(name):
    module = types.ModuleType(name)
    module.__getattr__ = lambda attribute: _Anything()
    sys.modules[name] = module
    return module


for _name in ("pyautogui", "pydirectinput", "mss", "pygetwindow"):
    _stub(_name)
sys.modules["pyautogui"].size = lambda: (1920, 1080)
try:
    for _name in ("tkinter", "tkinter.messagebox", "tkinter.scrolledtext", "tkinter.simpledialog", "tkinter.ttk"):
        importlib.import_module(_name)
except ImportError:
    for _name in ("tkinter", "tkinter.messagebox", "tkinter.scrolledtext", "tkinter.simpledialog", "tkinter.ttk"):
        _stub(_name)
    sys.modules["tkinter"].TclError = type("TclError", (Exception,), {})          # (an exception class, since the code catches it)

import main  # noqa: E402  (after the stubs)


@pytest.fixture
def macro(tmp_path, monkeypatch):
    """main.py with a fresh State and RunStats, every file it writes redirected into tmp_path, and the
    things that would touch the game (clicks, keys, sleeping) replaced by recorders."""
    cfg = main.cfg
    for name, value in (("RUN_LOG_PATH", tmp_path / "run_log.txt"), ("RUNS_DIR", tmp_path / "runs"), ("STATUS_PATH", tmp_path / "status.json"),
                        ("OUTPUT_LOG_PATH", tmp_path / "output.log"), ("FAILURE_DIR", tmp_path / "failures"), ("GAME_CONFIG_PATH", tmp_path / ".config")):
        monkeypatch.setattr(cfg, name, str(value))
    monkeypatch.setattr(cfg, "MINIGAME_CAPTURE", False)      # (the tests of the recording switch it on)
    monkeypatch.setattr(main, "state", main.State())
    monkeypatch.setattr(main, "run_stats", main.RunStats())
    main.STOP_EVENT.clear()
    main.close_output_log()
    main._NEED_STRIP_CACHE.update(strip=None, found=[], reuses=0)
    clicks = []
    monkeypatch.setattr(main, "hover_click", lambda x, y, duration=0: clicks.append((x, y)))
    monkeypatch.setattr(main, "clicks", clicks, raising=False)
    yield main
    main.STOP_EVENT.clear()
    main.close_output_log()
    main._NEED_STRIP_CACHE.update(strip=None, found=[], reuses=0)


# What the minigame players ask of a frame again and again - the same frame is "on screen" for many looks - and what costs most of the time of
# the tests that run them: each is answered once per frame (see memoized_detectors()).
MEMOIZED_DETECTORS = ("find_ghost_blobs", "find_ghost_alerts", "ghost_boss_bar_visible", "ghost_boss_shielded", "find_door_tvs", "find_door_circles",
                      "find_fork_doorways", "find_hauntlet_doors", "read_hauntlet_hearts", "read_hauntlet_items", "read_hauntlet_room", "hauntlet_timer_visible")


@contextlib.contextmanager
def memoized_detectors(monkeypatch, names=MEMOIZED_DETECTORS):
    """Within the block the functions `names` of main.py answer once for each frame (a numpy array, by identity, with the other arguments) and give
    a copy of that answer after. The frames are made read-only while they are remembered - a test that changes one in place would get an old
    answer otherwise, so it fails loudly instead (copy the frame first) - and writable again at the end."""
    remembered: dict[tuple, Any] = {}
    frozen = []

    def memoized(name, function):
        def wrapper(img, *args, **kwargs):
            if not isinstance(img, np.ndarray):
                return function(img, *args, **kwargs)
            key = (name, id(img), args, tuple(sorted(kwargs.items())))
            if key not in remembered:
                if img.flags.writeable:
                    img.flags.writeable = False
                    frozen.append(img)
                remembered[key] = (img, function(img, *args, **kwargs))           # (the frame is kept: its id stays its own)
            return copy.copy(remembered[key][1])
        return wrapper

    for name in names:
        monkeypatch.setattr(main, name, memoized(name, getattr(main, name)))
    try:
        yield
    finally:
        for img in frozen:
            img.flags.writeable = True


class Clock:
    """A time that only moves when the macro waits."""
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def world(macro, monkeypatch):
    """Fake time, keyboard and mouse for the minigame players: `world.keys` lists ("down"/"up", key, time), `world.mouse` lists
    ("move", x, y) / ("down",) / ("up",) (for the right button ("down", "right") ...) / ("rel", dx, dy), `world.screens` is what the next looks see (a list, the last one staying, or a
    function of the world), `world.waits` the waits, `world.zooms` / `world.tilts` when the camera was zoomed out / tilted down."""
    clock = Clock()
    world = types.SimpleNamespace(clock=clock, keys=[], mouse=[], waits=[], screens=[], looks=0, zooms=[], tilts=[], real_zoom_out=macro.zoom_out, real_tilt=macro.tilt_camera_down)
    monkeypatch.setattr(macro, "time", types.SimpleNamespace(**{**vars(macro.time), "time": clock, "sleep": lambda duration: setattr(clock, "now", clock.now + duration)}))

    def wait(duration):
        world.waits.append(duration)
        clock.now += duration

    def look(watch):
        world.looks += 1
        if callable(world.screens):
            return world.screens()
        return world.screens.pop(0) if len(world.screens) > 1 else world.screens[0]

    monkeypatch.setattr(macro, "wait_interruptible", wait)
    monkeypatch.setattr(macro, "minigame_look", look)
    monkeypatch.setattr(macro, "minigame_wait", lambda duration, watch: clock.__setattr__("now", clock.now + duration))
    monkeypatch.setattr(macro, "detect_minigame_finished", lambda img: False)
    monkeypatch.setattr(macro, "zoom_out", lambda: world.zooms.append(clock.now))      # (world.real_zoom_out is the real one)
    monkeypatch.setattr(macro, "tilt_camera_down", lambda: world.tilts.append(clock.now))
    monkeypatch.setattr(macro.pydirectinput, "keyDown", lambda key: world.keys.append(("down", key, clock.now)), raising=False)
    monkeypatch.setattr(macro.pydirectinput, "keyUp", lambda key: world.keys.append(("up", key, clock.now)), raising=False)
    monkeypatch.setattr(macro.pydirectinput, "moveTo", lambda x, y, **kw: world.mouse.append(("move", x, y)), raising=False)
    monkeypatch.setattr(macro.pydirectinput, "mouseDown", lambda *a, button="left", **k: world.mouse.append(("down",) if button == "left" else ("down", button)), raising=False)
    monkeypatch.setattr(macro.pydirectinput, "mouseUp", lambda *a, button="left", **k: world.mouse.append(("up",) if button == "left" else ("up", button)), raising=False)
    monkeypatch.setattr(macro.pydirectinput, "moveRel", lambda dx, dy, **k: world.mouse.append(("rel", dx, dy)), raising=False)
    monkeypatch.setattr(macro.pydirectinput, "press", lambda key, **k: world.keys.append(("press", key, clock.now)), raising=False)
    monkeypatch.setattr(macro, "to_screen", lambda x, y: (x, y))
    with memoized_detectors(monkeypatch):
        yield world
