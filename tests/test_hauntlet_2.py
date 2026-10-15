"""Reading the Hauntlet 2 screen. The fixtures are crops of a real 29-room run (tests/hauntlet_2/), pasted back at
the place they were taken from on an otherwise blank screen."""
import glob
import os

import cv2
import numpy as np
import pytest

HERE = os.path.join(os.path.dirname(__file__), "hauntlet_2")
PANEL_AT = (1060, 60)                      # where the room panel crops were cut from (left, top)


def screen_with(crop, at):
    screen = np.zeros((1080, 1920, 3), np.uint8)
    screen[at[1]:at[1] + crop.shape[0], at[0]:at[0] + crop.shape[1]] = crop
    return screen


def panel(name):
    return screen_with(cv2.imread(os.path.join(HERE, "room_panels", name)), PANEL_AT)


def test_every_room_of_the_real_run_is_read(macro):
    names = [os.path.basename(path) for path in sorted(glob.glob(os.path.join(HERE, "room_panels", "[0-9]*.png")))]
    assert len(names) == 29
    assert {name: macro.read_hauntlet_room(panel(name)) for name in names} == {name: int(name[:2]) for name in names}   # (a mismatch names its room)


@pytest.mark.parametrize("name", ["none_transition.png", "none_lobby.png"])
def test_a_screen_without_the_panel_reads_as_no_room(macro, name):
    assert macro.read_hauntlet_room(panel(name)) is None


def test_a_blank_screen_and_a_missing_digit_folder_read_as_no_room(macro, monkeypatch):
    assert macro.read_hauntlet_room(np.zeros((1080, 1920, 3), np.uint8)) is None
    monkeypatch.setattr(macro.cfg, "HAUNTLET_DIGIT_DIR", os.path.join(HERE, "no_such_folder"))
    assert macro.read_hauntlet_room(panel("07.png")) is None


def test_the_saved_digits_are_all_there_and_distinct(macro):
    digits = macro.digit_templates()
    assert sorted(digits) == list(range(10))
    for a in digits:
        for b in digits:
            if a < b:
                assert np.abs(digits[a].astype(int) - digits[b].astype(int)).mean() > 2 * macro.cfg.HAUNTLET_DIGIT_MAX_DIFF   # no digit can match two


def test_a_digit_that_matches_none_of_them_is_not_guessed(macro):
    screen = panel("07.png")
    left, top, right, bottom = macro.cfg.HAUNTLET_ROOM_BOX
    screen[top:bottom, left:right] = 0
    screen[top + 4:top + 24, left + 30:left + 44] = 255                          # a solid white block where a digit goes
    assert macro.read_hauntlet_room(screen) is None


# --- the item slots ---

BAR_AT = (640, 930)
# A fixture in tests/hauntlet_2/item_bars/ is named for what is in its slots, left to right, joined by hyphens, with `_x2` after an item that
# has a count of 2, then (after two underscores) what else it shows: `phone-key-cyan_potion-heart_potion.png`; `no_items__lobby.png` has only
# the backpack button.
THREE_ITEMS_AND_PHONE = "phone-key-cyan_potion-heart_potion"
PHONE_KEY_HEART = "phone-key-heart_potion"
KEY_CYAN_HEART = "key-cyan_potion-heart_potion"


def bar_fixtures():
    found = {}
    for path in sorted(glob.glob(os.path.join(HERE, "item_bars", "*.png"))):
        name = os.path.basename(path)[:-4]
        listed = name.split("__")[0]
        found[name] = [] if listed == "no_items" else [(item.split("_x")[0], int(item.split("_x")[1]) if "_x" in item else 1) for item in listed.split("-")]
    return found


def bar(name):
    return screen_with(cv2.imread(os.path.join(HERE, "item_bars", f"{name}.png")), BAR_AT)


def test_the_items_and_counts_in_the_slots_are_read(macro):
    read = {}
    for name in bar_fixtures():
        items = macro.read_hauntlet_items(bar(name))
        assert [item.pos[0] for item in items] == sorted(item.pos[0] for item in items)     # left to right
        read[name] = [(item.name, item.count) for item in items]
    assert len(read) >= 10 and read == bar_fixtures()                                       # (a mismatch names its fixture)


def test_slot_positions_are_where_the_slots_are(macro):
    items = macro.read_hauntlet_items(bar(THREE_ITEMS_AND_PHONE))
    assert [item.pos[0] for item in items] == pytest.approx([788, 903, 1018, 1133], abs=3)
    assert all(abs(item.pos[1] - 1012) <= 3 for item in items)


def test_an_item_that_is_not_saved_is_unknown_and_learned_when_asked(macro, monkeypatch, tmp_path):
    import shutil
    items_dir = tmp_path / "items"
    shutil.copytree(macro.cfg.HAUNTLET_ITEM_DIR, items_dir)
    shutil.rmtree(items_dir / "phone")                                               # forget the phone
    monkeypatch.setattr(macro.cfg, "HAUNTLET_ITEM_DIR", str(items_dir))
    assert [i.name for i in macro.read_hauntlet_items(bar(PHONE_KEY_HEART))] == ["unknown", "key", "heart_potion"]
    assert not any(n.startswith("unknown") for n in os.listdir(items_dir))              # looking doesn't save anything
    learned = macro.read_hauntlet_items(bar(PHONE_KEY_HEART), learn=True)
    assert [i.name for i in learned] == ["unknown_1", "key", "heart_potion"] and learned[0].count == 1
    assert os.path.isfile(items_dir / "unknown_1" / "1.png")
    assert [i.name for i in macro.read_hauntlet_items(bar(PHONE_KEY_HEART))] == ["unknown_1", "key", "heart_potion"]    # known now
    (items_dir / "unknown_1").rename(items_dir / "phone")                            # naming it is renaming the folder
    assert [i.name for i in macro.read_hauntlet_items(bar(PHONE_KEY_HEART))] == ["phone", "key", "heart_potion"]


def test_each_unknown_item_gets_the_next_number(macro, monkeypatch, tmp_path):
    monkeypatch.setattr(macro.cfg, "HAUNTLET_ITEM_DIR", str(tmp_path / "items"))              # nothing saved: all unknown
    names = [i.name for i in macro.read_hauntlet_items(bar(KEY_CYAN_HEART), learn=True)]
    assert names == ["unknown_1", "unknown_2", "unknown_3"]
    assert sorted(os.listdir(tmp_path / "items")) == names


def test_a_badge_that_cannot_be_read_gives_no_count_not_a_guess(macro):
    screen = bar(THREE_ITEMS_AND_PHONE)
    x, y = macro.find_item_slots(screen)[0][:2]
    left, top, right, bottom = macro.cfg.HAUNTLET_BADGE_BOX
    screen[y + top:y + bottom, x + left:x + right] = 0                                         # wipe the badge
    assert macro.read_item_count(screen, x, y) is None
    assert macro.read_item_count(np.zeros((1080, 1920, 3), np.uint8), 10, 10) is None


# --- the hearts ---

def test_the_hearts_are_counted_red_and_black(macro):
    read, expected = {}, {}
    for path in sorted(glob.glob(os.path.join(HERE, "hearts", "*.png"))):
        name = os.path.basename(path)
        parts = name[:-4].split("_")                              # <frame>_<left>_<top>_<red>_<black>  or  <frame>_<left>_<top>_none
        expected[name] = None if parts[3] == "none" else (int(parts[3]), int(parts[4]))
        hearts = macro.read_hauntlet_hearts(screen_with(cv2.imread(path), (int(parts[1]), int(parts[2]))))
        read[name] = None if hearts is None else (hearts.red, hearts.black)
    assert len(read) >= 10 and read == expected                                              # (a mismatch names its frame)


def test_no_pink_heart_on_a_blank_screen_reads_as_no_hearts(macro):
    assert macro.read_hauntlet_hearts(np.zeros((1080, 1920, 3), np.uint8)) is None


def test_a_row_of_only_black_hearts_is_not_found_by_this_reader(macro):
    """Nothing pink to find it by - documented, not guessed."""
    screen = np.full((1080, 1920, 3), 120, np.uint8)
    for k in range(4):
        screen[400:440, 880 + k * 52:880 + k * 52 + 40] = 0
    assert macro.read_hauntlet_hearts(screen) is None


def test_the_row_is_the_one_nearest_the_middle_of_the_screen(macro):
    """A second row of hearts (another player's) off to one side is not the player's."""
    path = [p for p in glob.glob(os.path.join(HERE, "hearts", "014_*.png"))][0]
    crop = cv2.imread(path)
    screen = np.zeros((1080, 1920, 3), np.uint8)
    left, top = (int(v) for v in os.path.basename(path)[:-4].split("_")[1:3])
    screen[top:top + crop.shape[0], left:left + crop.shape[1]] = crop
    other = crop.copy()
    screen[top:top + crop.shape[0], left - 600:left - 600 + crop.shape[1]] = other[:, :]
    hearts = macro.read_hauntlet_hearts(screen)
    assert (hearts.red, hearts.black) == (3, 0) and abs(hearts.pos[0] - left) < 120


def test_a_lone_pink_thing_is_not_a_row(macro):
    screen = np.zeros((1080, 1920, 3), np.uint8)
    screen[400:440, 900:940] = (130, 120, 250)                     # pink-ish square, nothing around it
    assert macro.read_hauntlet_hearts(screen) is None
