"""The run report: the parser reads the exact wording the macro logs, so these tests guard that wording."""
import subprocess
import sys
import time

import cv2

import conftest

LOG = """\
[2026-10-10 22:01:14] [RUN 327] LOOP started
[2026-10-10 22:01:20] [RUN 327] resolved: dirty (30s)
[2026-10-10 22:01:14] [RUN 328] LOOP started
[2026-10-10 22:01:22] [RUN 328] detected: pizza, choose, hungry, pet
[2026-10-10 22:01:55] [RUN 328] resolved: hungry (33s)
[2026-10-10 22:02:10] [RUN 328] detected: none matched (3 icon(s) on screen)
[2026-10-10 22:03:00] [RUN 328] resolved: beach (110s)
[2026-10-10 22:03:30] [RUN 328] FAILURE: pizza still showing after 60.0s (screenshot: failures/x/screen.png)
[2026-10-10 22:04:00] [RUN 328] FAILURE: pizza still showing after 60.0s (screenshot: failures/y/screen.png)
[2026-10-10 22:05:00] [RUN 328] FAILURE: Roblox isn't running - rejoining (screenshot: failures/z/screen.png)
[2026-10-10 22:06:00] [RUN 328] recovery complete: rejoined, setup run, disabled needs cleared
[2026-10-10 22:07:00] [RUN 328] scheduled rejoin: 1 hour(s) since the last rejoin
[2026-10-10 22:08:00] [RUN 328] minigame started: Hauntlet 2
[2026-10-10 22:09:30] [RUN 328] minigame finished: Hauntlet 2 (89s)
[2026-10-10 22:09:40] [RUN 328] beach interrupted by a minigame
[2026-10-10 22:09:50] [RUN 328] paycheck popup dismissed
[2026-10-10 22:09:55] [RUN 328] stray window closed: trading_hub
[2026-10-10 22:10:00] [RUN 328] backpack was left open - closed it after equipping the toy
[2026-10-10 22:10:10] [RUN 328] minigame popup dismissed (unknown)
[2026-10-10 22:11:00] [RUN 328] resources: cpu 3.2% mem 215MB
[2026-10-10 22:12:00] [RUN 328] resources: cpu 4.0% mem 220MB
[2026-10-10 22:13:00] [RUN 328] quiet: no need resolved for 5 min
[2026-10-10 22:17:00] [RUN 328] no need detected for 4 min - respawning
[2026-10-10 22:20:00] [RUN 328] LOOP stopped
"""


def write_log(tmp_path):
    path = tmp_path / "log.txt"
    path.write_text(LOG)
    return str(path)


def test_parse_run_reads_only_the_asked_for_run(macro, tmp_path):
    data = macro.parse_run(write_log(tmp_path), 328)
    assert [need for _, need, _ in data["resolved"]] == ["hungry", "beach"]
    assert data["end"] - data["start"] == 18 * 60 + 46
    assert macro.parse_run(write_log(tmp_path), "327")["resolved"][0][1:] == ("dirty", 30)
    assert macro.parse_run(write_log(tmp_path), 999) is None
    assert macro.parse_run(str(tmp_path / "missing.txt"), 328) is None


def test_parse_run_counts_detections_failures_rejoins_and_resources(macro, tmp_path):
    data = macro.parse_run(write_log(tmp_path), 328)
    assert [(matched, icons) for _, matched, icons in data["detected"]] == [(4, 4), (0, 3)]
    assert [macro.failure_kind(text) for _, text in data["failures"]] == ["pizza still", "pizza still", "Roblox isn't".lower()]
    assert len(data["rejoins"]) == 2                                  # the recovery and the scheduled one
    assert [(cpu, mem) for _, cpu, mem in data["resources"]] == [(3.2, 215), (4.0, 220)]


def test_parse_run_event_counts(macro, tmp_path):
    events = macro.parse_run(write_log(tmp_path), 328)["events"]
    assert dict(events) == {"rejoins": 2, "minigames won": 1, "needs interrupted": 1, "paycheck popups": 1,
                            "stray windows closed": 1, "backpacks closed": 1, "minigame popups declined": 1,
                            "quiet periods logged": 1, "idle respawns": 1}


def test_what_the_macro_logs_is_what_the_parser_reads(macro, tmp_path, monkeypatch):
    """Round trip through log_run_event(): a wording change there must change the regexes too."""
    macro.state.run_number = 5
    macro.log_run_event("detected: hungry, thirsty")
    macro.log_run_event("detected: none matched (2 icon(s) on screen)")
    macro.log_run_event("resolved: hungry (28s)")
    macro.log_run_event("resources: cpu 2.5% mem 100MB")
    macro.state.last_detection = time.time() - macro.cfg.NO_DETECTION_RESPAWN_INTERVAL - 1
    monkeypatch.setattr(macro, "respawn_character", lambda: None)
    macro.respawn_if_no_detection()
    macro.state.last_progress = time.time() - 6 * 60
    macro.check_quiet()
    data = macro.parse_run(macro.cfg.RUN_LOG_PATH, 5)
    assert data["detected"][0][1:] == (2, 2) and data["detected"][1][1:] == (0, 2)
    assert data["resolved"][0][1:] == ("hungry", 28) and data["resources"][0][1:] == (2.5, 100)
    assert data["events"]["idle respawns"] == 1 and data["events"]["quiet periods logged"] == 1


def test_generate_run_report_draws_the_png(macro, tmp_path):
    path = macro.generate_run_report(write_log(tmp_path), 328, str(tmp_path / "reports"),
                                     basic={"hungry"}, teleport={"beach"})
    image = cv2.imread(path)
    assert path.endswith("run328.png") and image.shape == (1660, 1600, 3)
    assert image.std() > 10                                            # something was drawn


def test_generate_run_report_for_an_unknown_run_is_none(macro, tmp_path):
    assert macro.generate_run_report(write_log(tmp_path), 1, str(tmp_path / "reports")) is None


def test_generate_run_report_never_raises(macro, tmp_path, monkeypatch):
    monkeypatch.setattr(macro, "draw_report", lambda *a, **k: 1 / 0)
    assert macro.generate_run_report(write_log(tmp_path), 328, str(tmp_path / "reports")) is None


def test_constants_check_passes_on_the_repo():
    result = subprocess.run([sys.executable, "tools/check_constants.py"], cwd=conftest.ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout
