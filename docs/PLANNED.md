# Add next

- [ ] Pet focusing for choose and pet sometimes breaks
- [ ] Add logic to the minigames
- [ ] Tune the timings and make the clicks not hard-coded
      
# Ideas from Natro Macro

- [ ] Notify somewhere when something breaks (Natro posts disconnects, errors and screenshots to a Discord webhook), so an unattended failure doesn't wait for you to look

# Expansion

- [ ] Trade helper
- [ ] Auto-pen/neon maker

# Claude

Suggestions from reading through the code, none of them started. Roughly in order of payoff within each group.

## Organization

- [ ] Replace `from magic_numbers import *` with `import magic_numbers as cfg` (or split the constants per module): linters can then catch typos and missing constants, which the star import hides
- [ ] `tests/`: the pure parts are easy to test with the Windows-only imports stubbed - `need_bar_readable()`, `record_detection()`, `parse_server_link()`, `exact_color_mask()`, `DebugCapture`, `format_duration()`. Use lossless PNG fixtures (compressed `.webp` screenshots don't match exact colours)
- [ ] A GitHub Action running pyflakes and the tests on every push

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

## Reliability

- [ ] Hard-coded click positions that could be found on screen instead (each is a fixed pixel, so a different backpack layout, a new item row or a UI update breaks it): the vehicle / toy / pet items and Equip button in `equip_favorite_vehicle()` and `CatchNeedHandler` (`RIDE_FIRST_VEHICLE_POS`, `RIDE_EQUIP_POS`, `CATCH_SQUEAKY_TOY_POS`, `CATCH_EQUIP_POS`, `CATCH_UNEQUIP_POS`), the teleport clicks (`GENERAL_TELEPORT_POS_*`, `TELEPORT_*_TAB_POS`), the lure clicks (`LURE_NEW_POS_*`) and the setup menus (`SETUP_*_POS`). Each would be a `ref/` crop of the button plus `template_score()` to find it (the same way the stray windows work), falling back to the fixed pixel. The Equip button in particular moves with the item panel
- [ ] The dismiss clicks in the paycheck (`PAYCHECK_DISMISS_POS_*`), minigame popup (`MINIGAME_POPUP_*_POS`), Okay and rejoin Play buttons could be the centre of the matched crop instead of a fixed pixel, so they follow the popup if it ever moves
- [ ] Roblox itself closed twice in run 328 (00:37 and 03:35, "Roblox lost focus" then "Roblox isn't running", both a few minutes after a sick need started) with no recording to show why. Find the cause: keep a rolling recording or the last few screenshots to save when Roblox disappears, log Roblox's memory use / the Windows event log entry at that point, and check whether it only happens during sick or at the half-hour minigame time
