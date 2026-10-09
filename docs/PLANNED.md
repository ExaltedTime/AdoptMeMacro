# Add next

- [ ] Pet focusing for choose and pet sometimes breaks
- [ ] Check what happens after a minigame ends: `play_minigame()` respawns afterwards, which is only right if the game then puts you back where the needs can be handled (the button needs walk from the spawn point)
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
- [ ] The teleport and walk handlers are already table-driven; the button handlers, `catch` and `pet` could be too (a list of steps instead of code), which also makes the timing tuning item easier
- [ ] Use the `logging` module instead of `print("[debug] ...")`, so levels and the console/file split come for free and the on-screen console can hide debug lines
- [ ] `RUN_STATS` as a dataclass, written to `status.json` atomically (write to a temp file, then `os.replace`) so a reader never sees half a file
- [ ] Type hints on the public functions, then a `mypy` pass
- [ ] Hard-coded click positions (backpack categories, settings, setup) break on a different layout or UI update; where something has a distinctive look, find it (colour or template match) instead, as the Play button and popups already do

## Optimization

- [ ] `detect_need_icons()` grabs the whole screen and then crops the top strip; grab only that region (`mss` can capture a sub-rectangle), and share one grab between the readability check and the detection
- [ ] `log_output()` reopens the file for every write; keep one handle open (flushed) instead
- [ ] While waiting for a need, detection runs every few seconds; the check that nothing changed in the strip could be a cheap frame diff against the last one before running Hough circles and matching
- [ ] `hover_move()` does three `moveTo` calls with sleeps for every click; fine for reliability but it adds up over a 100-click handler. Check which clicks actually need the nudge

## Reliability

- [ ] A health check that notices the loop going quiet without any rejoin trigger (e.g. the macro window itself frozen), and writes it to the status file
- [ ] After a failed handler, save the screenshot *and* the need-icon crop so a misidentified icon can be seen straight away
- [ ] Failure screenshots only keep the newest 40; keep the first failure of each kind per run as well, so an early cause isn't pushed out by later noise
