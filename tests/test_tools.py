"""The checks in tools/: they pass on the repo as it is (CI runs them too), and the constants check finds what it is for."""
import subprocess
import sys

import conftest


def run_check():
    return subprocess.run([sys.executable, "tools/check_constants.py"], cwd=conftest.ROOT, capture_output=True, text=True)


def test_the_constants_check_passes_on_the_repo():
    result = run_check()
    assert result.returncode == 0, result.stdout


def test_the_constants_check_names_an_unused_constant_a_typo_and_an_uncalled_function(tmp_path, monkeypatch):
    sys.path.insert(0, conftest.ROOT + "/tools")
    try:
        import check_constants
    finally:
        sys.path.pop(0)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "tools").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "notes.md").write_text("`USED` is described, `GONE_NAME` is not a thing, and `lower_case` is not checked.\n")
    (tmp_path / "magic_numbers.py").write_text("USED = 1\nUNUSED = 2\nCOMBINED = USED + 1\n")
    (tmp_path / "main.py").write_text("import magic_numbers as cfg\n\ndef used():\n    return cfg.COMBINED + cfg.TYPO\n\ndef lonely():\n    return used()\n")
    monkeypatch.setattr(check_constants, "ROOT", str(tmp_path))
    monkeypatch.setattr(check_constants, "CODE", ["main.py"])
    monkeypatch.setattr(check_constants, "OTHER", [])
    defined = check_constants.defined_names("magic_numbers.py")
    assert [m for _, _, m in check_constants.unknown_constants(defined)] == ["cfg.TYPO is not defined in magic_numbers.py"]
    assert [m for _, _, m in check_constants.unused_constants(defined)] == ["UNUSED is not used anywhere"]
    assert [m for _, _, m in check_constants.unknown_names_in_docs()] == ["GONE_NAME is named in the docs and exists in no code file"]
    assert [m for _, _, m in check_constants.unused_functions()] == ["lonely() is not called anywhere in the code"]


def test_the_constants_check_names_a_key_label_that_does_not_exist_and_a_key_written_as_a_letter(tmp_path, monkeypatch):
    sys.path.insert(0, conftest.ROOT + "/tools")
    try:
        import check_constants
    finally:
        sys.path.pop(0)
    (tmp_path / "magic_numbers.py").write_text('KEYS = {"forward": "w", "jump": "space"}\nHOTKEYS = {"stop": "p"}\n')
    (tmp_path / "main.py").write_text('def f():\n    hold_key(cfg.KEYS["forward"], 1)\n    hold_key(cfg.KEYS["sideways"], 1)\n    hold_key("a", 1)\n    pydirectinput.press("e")\n    cfg.HOTKEYS["stop"]\n')
    monkeypatch.setattr(check_constants, "ROOT", str(tmp_path))
    monkeypatch.setattr(check_constants, "CODE", ["main.py"])
    messages = [m for _, _, m in sorted(check_constants.key_problems())]                      # (by line)
    assert messages == ["KEYS['sideways'] is not in the table", "a key written as 'a': use cfg.KEYS, cfg.KEY_SEQUENCES or cfg.HOTKEYS", "a key written as 'e': use cfg.KEYS, cfg.KEY_SEQUENCES or cfg.HOTKEYS"]


SAMPLE_LOG = """[2026-11-07 21:10:20] [RUN 4] LOOP started
[2026-11-07 21:12:00] [RUN 4] resolved: dirty (26s)
[2026-11-07 21:13:00] [RUN 4] resolved: dirty (28s)
[2026-11-07 21:14:00] [RUN 4] resolved: sick (60s)
[2026-11-07 21:20:00] [RUN 4] sick: the doctor's prompt is not on screen
[2026-11-07 21:20:05] [RUN 4] FAILURE: sick handler could not resolve it (screenshot: failures/a/screen.png)
[2026-11-07 21:21:00] [RUN 4] FAILURE: ride still showing after 75.0s (screenshot: failures/b/screen.png)
[2026-11-07 21:22:00] [RUN 4] teleport: still in the house after both loads (the Exit Home button is on screen)
[2026-11-07 21:23:00] [RUN 4] notice: closed one by its green button at (962, 660)
[2026-11-07 21:24:00] [RUN 4] scheduled rejoin: 2 hour(s) since the last rejoin
[2026-11-07 21:30:00] [RUN 4] resolved: dirty (30s)
[2026-11-07 21:40:00] [RUN 5] LOOP started
"""


def test_the_run_summary_counts_what_was_resolved_what_failed_and_the_longest_quiet_stretch():
    sys.path.insert(0, conftest.ROOT + "/tools")
    try:
        import run_summary
    finally:
        sys.path.pop(0)
    text = run_summary.summarise(SAMPLE_LOG.splitlines(), run="4")
    assert "run 4: 2026-11-07 21:10 to 21:30" in text and "4 needs resolved, 2 failures" in text          # (the line of run 5 is left out)
    assert "dirty         3     28s" in text                                                      # (three of them, 28 s on average)
    assert "sick handler could not resolve it" in text and "ride still showing after Ns" in text       # (the numbers of a kind are folded together)
    assert "teleport: still in the house" in text and "notice: closed one" in text and "scheduled rejoin" in text
    assert "longest stretches" in text and "16.0 min from 21:14:00" in text
    assert "no lines of a run" in run_summary.summarise([], "")
