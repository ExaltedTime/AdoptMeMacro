"""The small pure functions: no screen, no game."""
import numpy as np
import pytest


def test_parse_server_link_share_form(macro):
    code = "a" * 32
    assert macro.parse_server_link(f"https://www.roblox.com/share?code={code}&type=Server") == ("ShareCode", code)


def test_parse_server_link_old_form(macro):
    code = "0123456789abcdef0123456789abcdef"
    assert macro.parse_server_link(f"https://www.roblox.com/games/1?privateServerLinkCode={code}") == ("LinkCode", code)


@pytest.mark.parametrize("link", ["", "https://www.roblox.com/games/920587237", "share?code=tooshort&type=Server"])
def test_parse_server_link_rejects_other_text(macro, link):
    assert macro.parse_server_link(link) is None


@pytest.mark.parametrize("seconds, text", [(0, "0s"), (45, "45s"), (59.9, "59s"), (60, "1m 00s"), (83, "1m 23s"), (3725, "62m 05s")])
def test_format_duration(macro, seconds, text):
    assert macro.format_duration(seconds) == text


@pytest.mark.parametrize("seconds, text", [(5, "5s"), (65, "1m 05s"), (3600, "1h 00m"), (3725, "1h 02m")])
def test_duration_label(macro, seconds, text):
    assert macro.duration_label(seconds) == text


@pytest.mark.parametrize("seconds, text", [(30, "30 s"), (120, "2 min"), (7200, "2 h")])
def test_unit_label(macro, seconds, text):
    assert macro.unit_label(seconds) == text


def test_exact_color_mask_takes_rgb_for_a_bgr_image(macro):
    img = np.zeros((4, 4, 3), np.uint8)
    img[1, 2] = (30, 20, 10)             # BGR - so the RGB colour is (10, 20, 30)
    mask = macro.exact_color_mask(img, (10, 20, 30))
    assert mask[1, 2] == 255 and np.count_nonzero(mask) == 1


def test_exact_color_mask_tolerance(macro):
    img = np.full((2, 2, 3), (30, 20, 12), np.uint8)
    assert np.count_nonzero(macro.exact_color_mask(img, (10, 20, 30))) == 0
    assert np.count_nonzero(macro.exact_color_mask(img, (10, 20, 30), tolerance=3)) == 4


def test_find_exact_color(macro):
    img = np.zeros((10, 10, 3), np.uint8)
    img[2:4, 6:8] = (3, 2, 1)
    assert macro.find_exact_color(img, (1, 2, 3)) == (6, 2)
    assert macro.find_exact_color(img, (9, 9, 9)) is None


@pytest.mark.parametrize("value, top", [(0, 1), (0.5, 0.5), (1, 1), (3, 5), (7, 10), (11, 20), (45, 50), (160, 200)])
def test_nice_max(macro, value, top):
    assert macro.nice_max(value) == top


def test_pick_bucket_fills_about_the_target(macro):
    assert macro.pick_bucket(60) == 10
    step = macro.pick_bucket(9.8 * 3600)
    assert 9.8 * 3600 / step <= macro.TARGET_BUCKETS
    assert macro.pick_bucket(10 ** 9) == macro.BUCKET_STEPS[-1]


def test_failure_kind_is_the_first_two_words_without_numbers(macro):
    assert macro.failure_kind("pizza still showing after 60.0s (screenshot: failures/x/screen.png)") == "pizza still"
    assert macro.failure_kind("") == "failure"


def test_need_group(macro):
    assert macro.need_group("hungry", {"hungry"}, {"beach"}) == "basic"
    assert macro.need_group("beach", {"hungry"}, {"beach"}) == "teleport"
    assert macro.need_group("pet", {"hungry"}, {"beach"}) == "other"
