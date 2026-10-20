"""Run the test suite against changed copies of the repo, to catch tests that only pass for today's setup.

magic_numbers.py is the file that gets edited all the time (timings, routes, intervals, the event flag), and the
machine the tests run on is never the one they were written on (another time zone, a debug/ folder full of real
files, a different working directory). A test that only passes for today's values breaks the build for a harmless
change. This copies the repo to a temporary folder once per variant, applies the variant's edits (to the copy of
magic_numbers.py, the environment, the files around it, or the order the tests run in), and runs pytest there:

    python tools/retune_check.py            # every variant
    python tools/retune_check.py halloween_off timings_x1.7

Each variant should pass exactly as the unchanged repo does. A failure names a test that depends on a tuned value:
give it a made-up entry or read the constant (see the `walker` fixture in tests/test_state.py) instead of pinning it.
(The exception is tests/test_icon_screenshots.py, which checks that the icon-recognition settings work on real screenshots:
the variants that change those settings on purpose skip it.)
"""
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The one test file that is *meant* to depend on the recognition settings: it checks that they recognise real screenshots.
# A variant that changes those settings on purpose ("deselect") leaves it out.
SCREENSHOT_TEST = "tests/test_icon_screenshots.py"

SCALE = '''
import re as _re
def _scale(_k):
    for _n, _v in list(globals().items()):
        if (isinstance(_v, (int, float)) and not isinstance(_v, bool) and _n not in ("STOP_CHECK_INTERVAL", "REJOIN_INTERVAL", "GHOST_ALERT_DOT_GAP")
                and _re.search(r"(WAIT|DURATION|INTERVAL|DELAY|TIMEOUT|GAP|SETTLE|GRACE)$", _n)):
            globals()[_n] = _v * _k
    for _table in (TELEPORT_WALK_NEEDS_NORMAL, TELEPORT_WALK_NEEDS_HALLOWEEN):
        for _entry in _table.values():
            _entry["steps"] = tuple((_key, _d * _k) for _key, _d in _entry["steps"])
_scale(%s)
TELEPORT_WALK_NEEDS = {**TELEPORT_WALK_NEEDS_NORMAL, **TELEPORT_WALK_NEEDS_HALLOWEEN} if HALLOWEEN else TELEPORT_WALK_NEEDS_NORMAL
'''

VARIANTS = {
    "baseline": "",
    # the event ends: the Halloween-only needs (sick) stop existing
    "halloween_off": "HALLOWEEN = False\nTELEPORT_WALK_NEEDS = TELEPORT_WALK_NEEDS_NORMAL\n",
    "timings_x1.7": SCALE % "1.7",
    "timings_x0.5": SCALE % "0.5",
    "other_tuning": '''
NEED_GONE_MAX_WAIT = 60.0
NEED_GONE_POLL_INTERVAL = 2.0
REJOIN_INTERVAL = 3 * 60 * 60
NO_PROGRESS_REJOIN_INTERVAL = 30 * 60
NO_DETECTION_RESPAWN_INTERVAL = 6 * 60
QUIET_LOG_INTERVAL = 10 * 60
MONEY_RESET_HOUR = 5
TREE_CHECK_INTERVAL = 5 * 60
MAX_FAILURE_FOLDERS = 20
MAX_REPORTS = 10
CONSOLE_MAX_LINES = 500
RUN_CAPTURES_BEST = 3
RUN_CAPTURES_WORST = 3
RUN_CAPTURES_FAILED = 2
STITCHES_LINE_DEADBAND = 7.0
UNSCREW_TASK_INTERVAL = 6.0
NEED_STUCK_CHECKS = 3
DISABLED_NEEDS_REJOIN_THRESHOLD = 2
MINIGAME_FINISH_CLICK_TRIES = 2
ENABLED_NEEDS = ENABLED_NEEDS - {"walk", "pet"}
''',
    # every click position moved a little
    "positions_shifted": '''
for _n, _v in list(globals().items()):
    if _n.endswith("_POS") and isinstance(_v, tuple) and len(_v) == 2:
        globals()[_n] = (_v[0] + 7, _v[1] + 5)
''',
    # optional keys of the need tables left out
    "drop_optional_keys": '''
for _table in (TELEPORT_WALK_NEEDS_NORMAL, TELEPORT_WALK_NEEDS_HALLOWEEN):
    for _entry in _table.values():
        for _key in ("max_wait", "helicopter"):
            _entry.pop(_key, None)
TELEPORT_WALK_NEEDS = {**TELEPORT_WALK_NEEDS_NORMAL, **TELEPORT_WALK_NEEDS_HALLOWEEN} if HALLOWEEN else TELEPORT_WALK_NEEDS_NORMAL
''',
}



KEYS_REBUILD = ('MOVE_KEYS = tuple(KEYS[name] for name in ("forward", "left", "back", "right"))\n'      # (what magic_numbers.py derives from the key tables, derived again)
                'HELD_KEYS = tuple(dict.fromkeys([*KEYS.values(), *(key for keys in KEY_SEQUENCES.values() for key in keys)]))\n')
REBUILD = "TELEPORT_WALK_NEEDS = {**TELEPORT_WALK_NEEDS_NORMAL, **TELEPORT_WALK_NEEDS_HALLOWEEN} if HALLOWEEN else TELEPORT_WALK_NEEDS_NORMAL\n"

VARIANTS.update({
    # a need deleted from the table / a new one added
    "needs_removed": 'for _n in ("cafe", "pizza", "camping", "bored"):\n    TELEPORT_WALK_NEEDS_NORMAL.pop(_n, None)\n    TELEPORT_WALK_NEEDS_HALLOWEEN.pop(_n, None)\nNEED_NAMES = tuple(_n for _n in NEED_NAMES if _n not in ("cafe", "pizza", "camping", "bored"))\n' + REBUILD,
    "need_added": 'TELEPORT_WALK_NEEDS_NORMAL["museum"] = dict(teleport_pos=TELEPORT_PETS_TAB_POS, steps=(("w", 3.0), ("d", 2.0)))\nNEED_NAMES = (*NEED_NAMES, "museum")\n' + REBUILD,
    "button_names_reordered": 'BUTTON_NAMES[:] = ["sleepy", "potty", "dirty", "thirsty", "hungry"]\n',
    "helicopter_everywhere": "HELICOPTER_REQUIRED = True\n",
    # thresholds and tolerances nudged either way (detection tuning)
    "thresholds_up": 'for _n, _v in list(globals().items()):\n    if _n.endswith("_THRESHOLD") and isinstance(_v, float):\n        globals()[_n] = min(0.99, _v * 1.03)\nfor _e in POPUP_TEMPLATES.values():\n    _e["threshold"] = min(0.99, _e["threshold"] * 1.03)\n',
    "thresholds_down": 'for _n, _v in list(globals().items()):\n    if _n.endswith("_THRESHOLD") and isinstance(_v, float):\n        globals()[_n] = _v * 0.94\nfor _e in POPUP_TEMPLATES.values():\n    _e["threshold"] = _e["threshold"] * 0.94\n',
    "tolerances_wider": 'for _n, _v in list(globals().items()):\n    if _n.endswith("_TOLERANCE") and "SLOT" not in _n and isinstance(_v, int):\n        globals()[_n] = _v * 2 + 1\n',
    # every search box moved a few pixels
    "boxes_shifted": 'for _n, _v in list(globals().items()):\n    if _n.endswith("_BOX") and isinstance(_v, tuple) and len(_v) == 4:\n        globals()[_n] = (_v[0] + 4, _v[1] + 3, _v[2] + 4, _v[3] + 3)\nfor _e in POPUP_TEMPLATES.values():\n    _v = _e["box"]\n    _e["box"] = (_v[0] + 4, _v[1] + 3, _v[2] + 4, _v[3] + 3)\n',
    "timings_x0.05": SCALE % "0.05",
    "timings_x20": SCALE % "20",
    # counts and limits at both ends
    "counts_low": {"patch": "NEED_STUCK_CHECKS = 1\nBACKPACK_CLOSE_CONFIRMATIONS = 1\nREJOIN_MAX_ATTEMPTS = 1\nFOCUS_RESUME_MAX_IN_A_ROW = 1\nMINIGAME_FINISH_CLICK_TRIES = 1\nMAX_FAILURE_FOLDERS = 1\nMAX_REPORTS = 1\nMAX_RECORDINGS = 1\nCONSOLE_MAX_LINES = 10\nICON_SHIFT_TOLERANCE = 0\nDISABLED_NEEDS_REJOIN_THRESHOLD = 1\nRUN_CAPTURES_BEST = 1\nRUN_CAPTURES_WORST = 1\nRUN_CAPTURES_FAILED = 1\nSTITCHES_LINE_DEADBAND = 0.0\n", "deselect": [SCREENSHOT_TEST]},
    "counts_high": "NEED_STUCK_CHECKS = 10\nBACKPACK_CLOSE_CONFIRMATIONS = 4\nREJOIN_MAX_ATTEMPTS = 8\nFOCUS_RESUME_MAX_IN_A_ROW = 20\nMINIGAME_FINISH_CLICK_TRIES = 6\nMAX_FAILURE_FOLDERS = 1000\nMAX_REPORTS = 1000\nMAX_RECORDINGS = 50\nCONSOLE_MAX_LINES = 100000\nICON_SHIFT_TOLERANCE = 4\nDISABLED_NEEDS_REJOIN_THRESHOLD = 9\nRUN_CAPTURES_BEST = 8\nRUN_CAPTURES_WORST = 8\nRUN_CAPTURES_FAILED = 9\nSTITCHES_LINE_DEADBAND = 30.0\n",
    # other key bindings in the game
    "keys_remapped": 'KEYS.update(backpack="v", mount="f", interact="f", zoom_in="o", helicopter="t", jump="x")\nHOTKEYS["stop"] = "q"\n' + KEYS_REBUILD,

    # behaviour flags flipped
    "flags_flipped": {"patch": "MATCH_ONLY_LEFT_HALF = False\nFOCUS_WINDOW_ON_ACTION = False\nSAVE_NEW_NEEDS = True\n", "deselect": [SCREENSHOT_TEST]},
    # the machine and the folders around the code
    "tz_new_york": {"env": {"TZ": "America/New_York"}},
    "tz_kolkata": {"env": {"TZ": "Asia/Kolkata"}},
    "hash_seed": {"env": {"PYTHONHASHSEED": "7"}},
    "other_cwd": {"cwd": "elsewhere"},
    "dirty_debug_folder": {"setup": "dirty_debug"},
    "extra_reference_files": {"setup": "extra_refs"},
    # the order the tests run in (leaking state shows up as a failure)
    "reverse_order": {"env": {"RETUNE_ORDER": "reverse"}},
    "shuffled_order": {"env": {"RETUNE_ORDER": "shuffle:12345"}},
    "each_file_alone": {"alone": True},
})

PLUGIN = '''import os, random
def pytest_collection_modifyitems(items):
    order = os.environ.get("RETUNE_ORDER", "")
    if order == "reverse":
        items.reverse()
    elif order.startswith("shuffle:"):
        random.Random(int(order.split(":")[1])).shuffle(items)
'''


def prepare(copy: str, setup: str) -> None:
    """Things around the code, as they are on a real machine."""
    if setup == "dirty_debug":       # a debug/ folder with a real config and logs in it
        for sub in ("logs", "failures", "recordings"):
            os.makedirs(os.path.join(copy, "debug", sub), exist_ok=True)
        with open(os.path.join(copy, "debug", ".config"), "w") as f:
            f.write('{"side_quest_enabled": false, "ghost_gallery_enabled": true, "hauntlet_2_enabled": true, "resume_on_focus_loss": true, "record_next_run": true, "private_server_link": "https://www.roblox.com/share?code=%s&type=Server", "money_collected": 99999}' % ("a" * 32))
        with open(os.path.join(copy, "debug", "logs", "run_counter.txt"), "w") as f:
            f.write("41")
        with open(os.path.join(copy, "debug", "logs", "run_log.txt"), "w") as f:
            f.write("[2026-10-11 23:04:31] [RUN 40] LOOP started\\n")
    elif setup == "extra_refs":      # a learned icon, an unknown popup crop, an unknown minigame crop
        import cv2
        import numpy as np
        noise = np.random.RandomState(0).randint(0, 255, (60, 60, 3), np.uint8)
        icon = np.full((66, 66, 3), 255, np.uint8)      # a white disc with a dark bar: the learned need (noise is mostly black once binarised, like a dark patch of scenery)
        cv2.line(icon, (10, 56), (56, 10), (0, 0, 0), 8)
        for folder, name, image in (("needs", "zz_new_need", icon), ("popups", "zz_unknown_popup", noise), ("halloween", "zz_new_minigame", noise), ("overlays", "zz_overlay", noise)):
            cv2.imwrite(os.path.join(copy, "ref", folder, name + ".png"), image)


def run_variant(name: str) -> bool:
    """One variant: a string (appended to magic_numbers.py) or a dict of what to change: env, cwd, setup, alone."""
    spec = VARIANTS[name]
    options = spec if isinstance(spec, dict) else {}
    patch = spec if isinstance(spec, str) else spec.get("patch", "")
    folder = tempfile.mkdtemp()
    try:
        copy = os.path.join(folder, "repo")
        shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(".git", ".claude", "debug", "__pycache__", ".mypy_cache", ".pytest_cache"))
        with open(os.path.join(copy, "magic_numbers.py"), "a") as f:
            f.write(f"\n# --- variant: {name}\n{patch}")
        with open(os.path.join(copy, "retune_plugin.py"), "w") as f:
            f.write(PLUGIN)
        prepare(copy, options.get("setup", ""))
        env = {**os.environ, "PYTHONPATH": copy, **options.get("env", {})}
        cwd = copy
        target = ["tests"]
        if options.get("cwd") == "elsewhere":           # run from some other folder, with the tests' full path
            cwd = os.path.join(folder, "elsewhere")
            os.makedirs(cwd)
            target = [os.path.join(copy, "tests")]
        deselect = [arg for test in options.get("deselect", []) for arg in ("--deselect", test)]
        runs = [[t] for t in sorted(glob.glob(os.path.join(copy, "tests", "test_*.py")))] if options.get("alone") else [target]
        ok, summaries, failed = True, [], set()
        for paths in runs:
            result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "retune_plugin", *deselect, *paths],
                                    cwd=cwd, env=env, capture_output=True, text=True)
            ok = ok and result.returncode == 0
            failed |= set(re.findall(r"^(?:FAILED|ERROR) (\S+)", result.stdout, re.M))
            summaries.append(next((line for line in reversed(result.stdout.splitlines()) if re.search(r"\d+ (passed|failed|error)", line)), "no result"))
        print(f"{'ok  ' if ok else 'FAIL'} {name}: {summaries[0] if len(summaries) == 1 else f'{len(summaries)} files, each on its own'}")
        for test in sorted(failed):
            print(f"       {test}")
        return ok
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def main() -> int:
    names = sys.argv[1:] or list(VARIANTS)
    unknown = [n for n in names if n not in VARIANTS]
    if unknown:
        print(f"unknown variant(s): {', '.join(unknown)} (known: {', '.join(VARIANTS)})")
        return 2
    results = [run_variant(name) for name in names]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
