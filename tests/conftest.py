"""Test setup: stands in for the libraries that only work on Windows or with a
screen (pyautogui, pydirectinput, mss, pygetwindow - and tkinter if it isn't
installed), so main.py can be imported anywhere, and gives each test its own
fresh State/RunStats and its own log/debug folders."""
import importlib
import os
import sys
import types

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

import main  # noqa: E402  (after the stubs)


@pytest.fixture
def macro(tmp_path, monkeypatch):
    """main.py with a fresh State and RunStats, every file it writes redirected into tmp_path, and the
    things that would touch the game (clicks, keys, sleeping) replaced by recorders."""
    cfg = main.cfg
    for name, value in (("RUN_LOG_PATH", tmp_path / "run_log.txt"), ("RUN_LOGS_DIR", tmp_path / "runs"), ("STATUS_PATH", tmp_path / "status.json"),
                        ("OUTPUT_LOG_PATH", tmp_path / "output.log"), ("FAILURE_DIR", tmp_path / "failures"),
                        ("REPORTS_DIR", tmp_path / "reports")):
        monkeypatch.setattr(cfg, name, str(value))
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
