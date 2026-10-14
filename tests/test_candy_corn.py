"""The candy corn total in the HUD (the fixtures are crops of real screens, pasted back on a blank one) and what a minigame run earned."""
import json
import os

import cv2
import numpy as np
import pytest

HERE = os.path.join(os.path.dirname(__file__), "candy_corn")
TOTALS = (1007037, 1106631, 1117615, 1126945, 1285606, 1290412)       # each on another background: dark, blue, yellow, red, white


def hud(total):
    screen = np.zeros((1080, 1920, 3), np.uint8)
    screen[30:100, 1400:1700] = cv2.imread(os.path.join(HERE, f"hud_{total}.png"))
    return screen


@pytest.mark.parametrize("total", TOTALS)
def test_the_candy_corn_total_is_read_off_the_hud(macro, total):
    assert macro.read_candy_corn(hud(total)) == total


def test_nothing_is_read_where_there_is_no_total_or_a_digit_is_not_one_of_the_saved(macro):
    assert macro.read_candy_corn(np.zeros((1080, 1920, 3), np.uint8)) is None            # (at home, in a minigame: no counter)
    screen = hud(1285606)
    left, top, _, _ = macro.cfg.CANDY_CORN_BOX
    screen[top + 5:top + 24, left + 56:left + 69] = (40, 40, 244)                         # a red block in place of the fourth digit
    assert macro.read_candy_corn(screen) is None


def digit_boxes(macro, screen):
    left, top, right, bottom = macro.cfg.CANDY_CORN_BOX
    mask = cv2.inRange(screen[top:bottom, left:right], macro.cfg.CANDY_CORN_RED[0], macro.cfg.CANDY_CORN_RED[1])
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    return sorted((left + int(stats[i, 0]), top + int(stats[i, 1]), int(stats[i, 2]), int(stats[i, 3])) for i in range(1, count) if stats[i, 3] >= 15)


def test_a_total_with_a_digit_hidden_or_something_red_beside_it_is_not_read(macro):
    screen = hud(1285606)                                                                   # (on white)
    x, y, w, h = digit_boxes(macro, screen)[3]
    hidden = screen.copy()
    hidden[y - 1:y + h + 1, x - 1:x + w + 1] = 255                                          # the fourth digit gone: 126,606 would be a wrong total, not a smaller one
    assert macro.read_candy_corn(hidden) is None
    left, top, _, _ = macro.cfg.CANDY_CORN_BOX
    first_x, _, first_w, _ = digit_boxes(macro, screen)[0]
    covered = screen.copy()
    covered[top + 5:top + 24, left:first_x + first_w + 2] = (40, 40, 244)                  # something red over the first digit: 285,606 would be a wrong total too
    assert macro.read_candy_corn(covered) is None


def test_what_a_run_earned_is_how_much_the_total_rose(macro):
    result = macro.minigame_result("ghost_gallery", "finished (the game over screen)", True, 187.4, 1285606, 1288146)
    assert result["earned"] == 2540 and result["seconds"] == 187 and result["finished"] and result["minigame"] == "ghost_gallery"
    assert macro.minigame_result("ghost_gallery", "x", False, 1, None, 5)["earned"] is None          # either total unread
    assert macro.minigame_result("ghost_gallery", "x", False, 1, 5, None)["earned"] is None
    assert macro.minigame_result("ghost_gallery", "x", False, 1, 9, 5)["earned"] is None             # (a total that fell was misread)


def test_a_result_is_noted_in_the_results_file_and_the_run_log_and_the_recordings_are_tidied(macro, monkeypatch, tmp_path):
    monkeypatch.setattr(macro.cfg, "RUN_CAPTURES_DIR", str(tmp_path / "captures"))
    logged = []
    monkeypatch.setattr(macro, "log_run_event", logged.append)
    pruned = []
    monkeypatch.setattr(macro, "prune_run_captures", pruned.append)
    macro.state.minigame_detail = {"room": 12}
    result = macro.minigame_result("hauntlet_2", "finished (the game over screen)", True, 100, 1000, 3500)
    macro.record_minigame_result(result)
    lines = [json.loads(line) for line in open(os.path.join(macro.cfg.RUNS_DIR, "results", "results.jsonl"), encoding="utf-8")]
    assert len(lines) == 1 and lines[0]["earned"] == 2500 and lines[0]["room"] == 12 and lines[0]["minigame"] == "hauntlet_2"
    assert logged == ["minigame result: Hauntlet 2: 2500 candy corn, room 12"] and pruned == ["hauntlet_2"]
    macro.record_minigame_result(macro.minigame_result("hauntlet_2", "x", False, 5, None, None))
    assert len(open(os.path.join(macro.cfg.RUNS_DIR, "results", "results.jsonl"), encoding="utf-8").readlines()) == 2      # appended to
    assert logged[-1].startswith("minigame result: Hauntlet 2: candy corn not read")


def test_a_settled_reading_waits_until_two_looks_agree(macro, world, monkeypatch):
    totals = iter([1000, 1500, 2540, 2540, 9999])
    monkeypatch.setattr(macro, "read_candy_corn", lambda img: next(totals))
    monkeypatch.setattr(macro, "grab_screen", lambda: None)
    assert macro.read_candy_corn_settled() == 2540                                         # it was still counting up
    totals = iter([None, None, None, None])
    monkeypatch.setattr(macro, "read_candy_corn", lambda img: next(totals))
    assert macro.read_candy_corn_settled() is None


def test_play_minigame_notes_the_total_before_and_after_the_run(macro, monkeypatch):
    totals = iter([1000, 3500])
    monkeypatch.setattr(macro, "read_candy_corn_settled", lambda: next(totals))
    noted, finished = [], []
    monkeypatch.setattr(macro, "record_minigame_result", noted.append)
    monkeypatch.setattr(macro, "start_run_capture", lambda label: "the capture")
    monkeypatch.setattr(macro, "finish_run_capture", lambda capture, how, result=None: finished.append(result))
    for name in ("release_all_inputs", "respawn_character", "send_macro_window_to_back", "bring_macro_window_to_front", "save_minigame_result"):
        monkeypatch.setattr(macro, name, lambda *a, **k: None)
    monkeypatch.setattr(macro, "accept_minigame_popup", lambda: True)
    monkeypatch.setattr(macro, "click_nice_until_gone", lambda: True)
    monkeypatch.setitem(macro.MINIGAME_PLAYERS, "ghost_gallery", lambda deadline, watch: True)
    assert macro.play_minigame("ghost_gallery") is True
    assert noted[0]["earned"] == 2500 and finished[0]["earned"] == 2500                    # in the log, and in the recording's info.json
