"""Need-icon recognition against real screenshots (tests/icon_detection/<folder>/Screenshot (N).png), each with
the need bar over a different background - dark, bright, busy, white. Every screenshot must be listed in EXPECTED with
the needs on its bar, left to right; one that isn't fails, so a newly added screenshot gets a label."""
import glob
import os
import re

import cv2
import pytest

import conftest

SHOTS = os.path.join(conftest.ROOT, "tests", "icon_detection")

ALL = "bored pizza cafe dirty ride"
CAMPING = "bored camping pizza cafe dirty"
SCHOOL = "bored school pizza cafe dirty"
NIGHT = "bored pizza cafe dirty sleepy"
EXPECTED = {
    "halloween1": {
        **{n: ALL for n in range(278, 285)},
        285: CAMPING, 287: CAMPING,
        **{n: SCHOOL for n in range(288, 297)},
        297: "bored pizza cafe dirty",
        **{n: NIGHT for n in range(298, 302)},
        **{n: "bored pizza dirty sleepy" for n in range(302, 308)},
    },
}


def screenshots():
    found = []
    for folder in sorted(os.listdir(SHOTS)):
        for path in sorted(glob.glob(os.path.join(SHOTS, folder, "*.png"))):
            number = int(re.search(r"\((\d+)\)", path).group(1))
            found.append((folder, number, path))
    return found


@pytest.mark.parametrize("folder,number,path", screenshots(), ids=lambda v: v if isinstance(v, str) and not v.endswith(".png") else None)
def test_every_icon_on_the_bar_is_recognised_and_nothing_else_is(macro, monkeypatch, folder, number, path):
    expected = EXPECTED.get(folder, {}).get(number)
    assert expected is not None, f"{path} has no entry in EXPECTED - add the needs on its bar"
    screen = cv2.imread(path)
    monkeypatch.setattr(macro, "grab_screen_region", lambda x, y, w, h: screen[y:y + h, x:x + w].copy())
    monkeypatch.setattr(macro, "check_running", lambda: None)
    macro._NEED_STRIP_CACHE.update(strip=None, found=[], reuses=0)
    found, image = macro.detect_need_icons(fresh=True)
    recognised = [name for _, _, name, _ in macro.identify_icons(found, image) if name]
    assert recognised == expected.split()
