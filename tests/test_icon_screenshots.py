"""Need-icon recognition against real screenshots (tests/icon_detection/<folder>/<scene>__<needs>.png), each with
the need bar over a different background - dark, bright, busy, white. A file is named for what it shows and what is on its bar:
the scene, then two underscores, then the needs on the bar left to right joined by hyphens (`hospital_1__bored-pizza-cafe-dirty-ride.png`).
The needs are what the test expects; a file named otherwise fails, so a newly added screenshot gets a label."""
import glob
import os
import re

import cv2
import pytest

import conftest

SHOTS = os.path.join(conftest.ROOT, "tests", "icon_detection")


def screenshots():
    found = []
    for folder in sorted(os.listdir(SHOTS)):
        for path in sorted(glob.glob(os.path.join(SHOTS, folder, "*.png"))):
            found.append((folder, os.path.basename(path), path))
    return found


@pytest.mark.parametrize("folder,name,path", screenshots(), ids=lambda v: v if isinstance(v, str) and not v.endswith(".png") else None)
def test_every_icon_on_the_bar_is_recognised_and_nothing_else_is(macro, monkeypatch, folder, name, path):
    named = re.fullmatch(r"[a-z0-9_]+__([a-z-]+)\.png", name)
    assert named, f"{path} is not named <scene>__<needs>.png - name it for what it shows and the needs on its bar"
    expected = named.group(1).replace("-", " ")
    screen = cv2.imread(path)
    monkeypatch.setattr(macro, "grab_screen_region", lambda x, y, w, h: screen[y:y + h, x:x + w].copy())
    monkeypatch.setattr(macro, "check_running", lambda: None)
    macro._NEED_STRIP_CACHE.update(strip=None, found=[], reuses=0)
    found, image = macro.detect_need_icons(fresh=True)
    recognised = [name for name, _ in macro.identify_icons(found, image) if name]
    assert recognised == expected.split()
