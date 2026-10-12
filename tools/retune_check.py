"""Run the test suite against retuned copies of magic_numbers.py, to catch tests that pin a tuned value.

magic_numbers.py is the file that gets edited all the time (timings, routes, intervals, the event flag), so a test
that only passes for today's values breaks the build for a harmless retune. This copies the repo to a temporary
folder once per variant, appends the variant's edits to the copy of magic_numbers.py, and runs pytest there:

    python tools/retune_check.py            # every variant
    python tools/retune_check.py halloween_off timings_x1.7

Each variant should pass exactly as the unchanged repo does. A failure names a test that depends on a tuned value:
give it a made-up entry or read the constant (see the `walker` fixture in tests/test_state.py) instead of pinning it.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCALE = '''
import re as _re
def _scale(_k):
    for _n, _v in list(globals().items()):
        if (isinstance(_v, (int, float)) and not isinstance(_v, bool) and _n not in ("STOP_CHECK_INTERVAL", "REJOIN_INTERVAL")
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
UNSCREW_TASK_INTERVAL = 6.0
NEED_STUCK_CHECKS = 3
DISABLED_NEEDS_REJOIN_THRESHOLD = 2
MINIGAME_VICTORY_CLICK_TRIES = 2
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


def run_variant(name: str) -> bool:
    folder = tempfile.mkdtemp()
    try:
        copy = os.path.join(folder, "repo")
        shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(".git", ".claude", "debug", "__pycache__", ".mypy_cache", ".pytest_cache"))
        with open(os.path.join(copy, "magic_numbers.py"), "a") as f:
            f.write(f"\n# --- variant: {name}\n{VARIANTS[name]}")
        result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=copy, capture_output=True, text=True)
        failed = sorted(set(re.findall(r"^(?:FAILED|ERROR) (\S+)", result.stdout, re.M)))
        summary = next((line for line in reversed(result.stdout.splitlines()) if re.search(r"\d+ (passed|failed|error)", line)), "no result")
        print(f"{'ok  ' if result.returncode == 0 else 'FAIL'} {name}: {summary}")
        for test in failed:
            print(f"       {test}")
        return result.returncode == 0
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
