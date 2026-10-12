"""The run report: the parser reads the exact wording the macro logs, so these tests guard that wording."""
import os
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
    assert len(data["rejoins"]) == 1                                  # a rejoin is logged twice (its start and "recovery complete"): counted once
    assert [(r[1], r[2]) for r in data["resources"]] == [(3.2, 215), (4.0, 220)]


def test_parse_run_event_counts(macro, tmp_path):
    events = macro.parse_run(write_log(tmp_path), 328)["events"]
    assert dict(events) == {"rejoins": 1, "minigames won": 1, "needs interrupted": 1, "paycheck popups": 1,
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
    macro.state.last_progress = time.time() - macro.cfg.QUIET_LOG_INTERVAL - 60
    macro.check_quiet()
    data = macro.parse_run(macro.cfg.RUN_LOG_PATH, 5)
    assert data["detected"][0][1:] == (2, 2) and data["detected"][1][1:] == (0, 2)
    assert data["resolved"][0][1:] == ("hungry", 28) and data["resources"][0][1:3] == (2.5, 100)
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


def test_idle_check_lines_become_one_empty_check_each_spread_over_their_span(macro, tmp_path):
    log = tmp_path / "log.txt"
    log.write_text("\n".join([
        "[2026-10-10 22:00:00] [RUN 7] LOOP started",
        "[2026-10-10 22:10:00] [RUN 7] idle checks: 4 over 600s",
        "[2026-10-10 22:10:03] [RUN 7] detected: hungry, thirsty",
        "[2026-10-10 22:10:40] [RUN 7] resolved: hungry (30s)"]) + "\n")
    data = macro.parse_run(str(log), 7)
    assert [matched for _, matched, _ in data["detected"]] == [0, 0, 0, 0, 2]
    times = [t for t, _, _ in data["detected"]]
    assert times == sorted(times) and data["start"] < times[0] and times[3] < data["start"] + 600     # inside the idle stretch
    assert "idle checks" not in data["events"]


def log_of(tmp_path, *lines):
    path = tmp_path / "log.txt"
    path.write_text("\n".join(f"[2026-10-10 22:{line}" for line in lines) + "\n")
    return str(path)


def test_same_checks_lines_stand_for_that_many_more_of_the_detection_above(macro, tmp_path):
    path = log_of(tmp_path, "00:00] [RUN 7] LOOP started", "01:00] [RUN 7] detected: hungry, pet",
                  "11:00] [RUN 7] same checks: 4 over 600s", "11:05] [RUN 7] detected: none matched (2 icon(s) on screen)")
    data = macro.parse_run(path, 7)
    assert [(matched, icons) for _, matched, icons in data["detected"]] == [(2, 2)] * 5 + [(0, 2)]
    times = [t for t, _, _ in data["detected"][1:5]]
    assert times == sorted(times) and data["start"] + 60 < times[0] and times[-1] < data["start"] + 660
    assert "same checks" not in data["events"]


def test_disabled_lines_are_read_with_their_time(macro, tmp_path):
    path = log_of(tmp_path, "00:00] [RUN 7] LOOP started", "05:00] [RUN 7] disabled: pizza", "06:00] [RUN 7] LOOP stopped")
    assert [(t - macro.parse_run(path, 7)["start"], need) for t, need in macro.parse_run(path, 7)["disabled"]] == [(300, "pizza")]


def test_resources_lines_carry_the_whole_pcs_use_when_it_was_logged(macro, tmp_path):
    path = log_of(tmp_path, "00:00] [RUN 7] resources: cpu 1.0% mem 100MB",
                  "01:00] [RUN 7] resources: cpu 2.0% mem 110MB pc 35.5%",
                  "02:00] [RUN 7] resources: cpu 3.0% mem 120MB pc 40.0% pcmem 71%")
    assert [r[1:] for r in macro.parse_run(path, 7)["resources"]] == [(1.0, 100, None, None), (2.0, 110, 35.5, None), (3.0, 120, 40.0, 71)]


def test_the_resource_monitor_logs_what_the_parser_reads(macro, monkeypatch):
    readings = iter([(100.0, 1000.0), (150.0, 1200.0), (170.0, 1400.0), (170.0, 1400.0)])    # the PC: (idle, total) each time
    monkeypatch.setattr(macro, "system_cpu_times", lambda: next(readings, None))
    monkeypatch.setattr(macro, "system_memory_percent", lambda: 62.4)
    monkeypatch.setattr(macro.cfg, "RESOURCE_LOG_INTERVAL", 0.02)
    macro.state.run_number = 9
    monitor = macro.ResourceMonitor()
    monitor.start()
    deadline = time.time() + 5
    while time.time() < deadline and (not os.path.exists(macro.cfg.RUN_LOG_PATH)
                                      or open(macro.cfg.RUN_LOG_PATH).read().count("resources: cpu") < 2):
        time.sleep(0.02)
    monitor.stop()
    monitor.join(2)
    rows = macro.parse_run(macro.cfg.RUN_LOG_PATH, 9)["resources"]
    assert rows[0][3:] == (75.0, 62) and rows[1][3:] == (90.0, 62)           # 1 - 50/200 and 1 - 20/200 of the interval was busy


def test_the_machines_own_readings_are_sane(macro):
    first, second = macro.system_cpu_times(), macro.system_cpu_times()
    assert first and second and 0 <= first[0] <= first[1] and second[1] >= first[1]
    assert 0 <= macro.system_memory_percent() <= 100


def pixels_of(image, color, box):
    left, top, right, bottom = box
    return int((abs(image[top:bottom, left:right].astype(int) - color).sum(axis=2) == 0).sum())


def test_the_report_draws_the_whole_pcs_line_only_when_it_was_logged(macro, tmp_path):
    with_pc = log_of(tmp_path, "00:00] [RUN 7] LOOP started", "01:00] [RUN 7] resources: cpu 1.0% mem 100MB pc 35.5% pcmem 60%",
                     "02:00] [RUN 7] resources: cpu 2.0% mem 110MB pc 50.0% pcmem 62%", "03:00] [RUN 7] LOOP stopped")
    without = tmp_path / "old.txt"
    without.write_text(open(with_pc).read().replace(" pc 35.5% pcmem 60%", "").replace(" pc 50.0% pcmem 62%", ""))
    drawn = macro.draw_report(macro.parse_run(with_pc, 7), 7, set(), set())
    old = macro.draw_report(macro.parse_run(str(without), 7), 7, set(), set())
    bottom = (0, 1090, 1600, 1660)
    assert pixels_of(drawn, macro.COLOR_PC, bottom) > 100 and pixels_of(old, macro.COLOR_PC, bottom) == 0


def test_the_report_shows_a_disabled_need_until_the_next_rejoin(macro, tmp_path):
    path = log_of(tmp_path, "00:00] [RUN 7] LOOP started", "05:00] [RUN 7] disabled: pizza",
                  "10:00] [RUN 7] recovery complete: rejoined", "20:00] [RUN 7] LOOP stopped")
    data = macro.parse_run(path, 7)
    plain = macro.draw_report({**data, "disabled": []}, 7, set(), set())
    drawn = macro.draw_report(data, 7, set(), set())
    top = (0, 50, 1600, 560)
    assert pixels_of(drawn, macro.COLOR_DISABLED, top) > pixels_of(plain, macro.COLOR_DISABLED, top) + 100


def test_the_per_check_means_stay_inside_the_plot(macro, tmp_path):
    """The last bucket is usually only part of a bucket: its mean line must stop where the run does, not run on
    past the right edge of the panel."""
    path = tmp_path / "log.txt"
    path.write_text("\n".join([
        "[2026-10-10 22:00:00] [RUN 7] LOOP started",
        "[2026-10-10 23:50:10] [RUN 7] detected: hungry, pet",
        "[2026-10-10 23:50:50] [RUN 7] LOOP stopped"]) + "\n")        # 1h50m50s: the last 5-minute bucket is mostly empty
    data = macro.parse_run(str(path), 7)
    duration = data["end"] - data["start"]
    img = macro.draw_report(data, 7, set(), set())
    right = macro.Panel(img.copy(), (20, 570, 800, 1080), "", duration, 5).plot[2]
    beyond = img[570:1080, right + 3:800]                                  # between the plot's frame and the panel's edge
    assert not (beyond == macro.COLOR_MEAN).all(axis=2).any()
