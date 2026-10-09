# Add next

- [ ] Pet focusing for choose and pet sometimes breaks
- [ ] Add auto minigame functions if the halloween toggle is on, and a toggle for whether to play them (MINIGAME_POPUP_PLAY is the toggle; the dismiss branch of minigame_popup() works, the minigame branch is comments only so far). There are two minigames now - Ghost Gallery and Hauntlet 2 - with the same popup, so playing them needs a way to tell which one the popup is for (e.g. matching a crop of its title against saved images, like the need icons)
- [ ] Tune the timings and make the clicks not hard-coded
      
# Ideas from Natro Macro

- [ ] Notify somewhere when something breaks (Natro posts disconnects, errors and screenshots to a Discord webhook), so an unattended failure doesn't wait for you to look

# Expansion

- [ ] Trade helper
- [ ] Auto-pen/neon maker

# Claude

Suggestions from reading through the code, none of them started. Roughly in order of payoff within each group.

## Organization

- [ ] Split `main.py` (~2,300 lines) into a package along the sections `ARCHITECTURE.md` already uses: window/screen, detection, logging, actions (setup/rejoin/lure/tree), handlers, per-cycle checks + workflow, GUI. The module-level globals (`STOP_FLAG`, `LAST_PROGRESS`, `LAST_REJOIN`, `DISABLED_THIS_RUN`, ...) need one shared state module first, so do it in a single pass with no feature work mixed in
- [ ] Replace `from magic_numbers import *` with `import magic_numbers as cfg` (or split the constants per module): linters can then catch typos and missing constants, which the star import hides
- [ ] Save `requirements.txt` as UTF-8 (it was written by PowerShell's `>` as UTF-16, which pip reads but most tools don't) and drop the packages nothing imports directly (`MouseInfo`, `PyMsgBox`, `pyperclip`, `PyRect`, `PyScreeze`, `pytweening` and `pillow` come in as dependencies of PyAutoGUI/opencv)
- [ ] `tests/`: the pure parts are easy to test with the Windows-only imports stubbed - `need_bar_readable()`, `record_detection()`, `parse_server_link()`, `exact_color_mask()`, `DebugCapture`, `format_duration()`. Use lossless PNG fixtures (compressed `.webp` screenshots don't match exact colours)
- [ ] A GitHub Action running pyflakes and the tests on every push
- [ ] Merge to `main` only once a batch is verified on the real machine, so `main` always means "known to work"
- [ ] A `tools/summarize_log.py` that turns `run_log.txt` into a short report (needs resolved per hour, average time per need, failures by kind, rejoins), instead of pasting the whole log to find patterns

## Code

- [ ] Several functions share state through bare globals; a small `State` object (or class) would make them testable and remove the `global` statements
- [ ] `STOP_FLAG` as a `threading.Event`, rather than a bool read from other threads
- [ ] `DebugCapture.write()` calls `self.text.update()` from the worker thread; Tk isn't thread-safe. Queue the text and let `root.after()` drain it
- [ ] `prompt_rename_need()` calls `input()` from the worker thread, which can't work with the GUI console (stdin isn't the console). Either remove `SAVE_NEW_NEEDS` or ask through a Tk dialog
- [ ] The teleport and walk handlers are already table-driven; the button handlers, `catch` and `pet` could be too (a list of steps instead of code), which also makes the timing tuning item easier
- [ ] Use the `logging` module instead of `print("[debug] ...")`, so levels and the console/file split come for free and the on-screen console can hide debug lines
- [ ] `RUN_STATS` as a dataclass, written to `status.json` atomically (write to a temp file, then `os.replace`) so a reader never sees half a file
- [ ] Type hints on the public functions, then a `mypy` pass
- [ ] Hard-coded click positions (backpack categories, settings, setup) break on a different layout or UI update; where something has a distinctive look, find it (colour or template match) instead, as the Play button and popups already do

## Optimization

- [ ] `find_matching_need()` walks `needs/` and reads and preprocesses every PNG on every call, for every icon, every check (and twice per flicker re-check). Load the reference signatures once at startup and reuse them
- [ ] `detect_need_icons()` grabs the whole screen and then crops the top strip; grab only that region (`mss` can capture a sub-rectangle), and share one grab between the readability check and the detection
- [ ] `log_output()` reopens the file for every write; keep one handle open (flushed) instead
- [ ] While waiting for a need, detection runs every few seconds; the check that nothing changed in the strip could be a cheap frame diff against the last one before running Hough circles and matching
- [ ] `hover_move()` does three `moveTo` calls with sleeps for every click; fine for reliability but it adds up over a 100-click handler. Check which clicks actually need the nudge

## Reliability

- [ ] Minigame popups: tell them apart by the width of the title's first line (see below), so the right minigame can be started later
- [ ] A health check that notices the loop going quiet without any rejoin trigger (e.g. the macro window itself frozen), and writes it to the status file
- [ ] After a failed handler, save the screenshot *and* the need-icon crop so a misidentified icon can be seen straight away
- [ ] Failure screenshots only keep the newest 40; keep the first failure of each kind per run as well, so an early cause isn't pushed out by later noise

## Telling the minigame popups apart

The Ghost Gallery and Hauntlet 2 popups are identical except for the first line of the title ("Ghost Gallery is" / "Hauntlet 2 is"). Two ways to tell them apart in a screenshot, both in the 1920x1080 reference space:

1. **Title width, no saved images.** The title text is purple on cream paper, so in the band `y 435-485`, `x 730-1200` count the purple pixels (roughly blue > 150, green < 120, red 90-190) and take the left-most and right-most column. In the screenshots: Ghost Gallery spans x 823-1101 (278 px), Hauntlet 2 spans x 852-1072 (220 px). Anything above ~250 px is Ghost Gallery, below it Hauntlet; a third minigame would need its own measurement
2. **Template match, more robust.** Save a crop of that first line for each minigame (e.g. `minigames/ghost_gallery.png`, `minigames/hauntlet_2.png`, taken from lossless screenshots), and use `cv2.matchTemplate(band, template, cv2.TM_CCOEFF_NORMED)`; the best score above ~0.9 names the minigame. This is the same idea as the need icons, and a new minigame is just a new file

Either would sit in `detect_minigame_popup()` (returning the name instead of `True`), and `minigame_popup()` would use the name once playing is implemented. Option 1 needs no assets, so it is the quick start; option 2 survives UI changes better.
