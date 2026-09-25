# Adopt Me Bot

A Python desktop bot that watches the Roblox game **Adopt Me** for pet-care
"need" icons (hunger, thirst, etc.) and automatically performs the action
that satisfies each one, controlled from a small `tkinter` GUI.

Everything lives in a single file: `main.py`.

## How it works, in one paragraph

The bot takes a screenshot, looks at a strip along the top of the screen for
colored circular icons, and compares each one (using shape/contrast, not
raw color) against a folder of reference icons it has seen before
(`needs/`). Once it recognizes a need, it hands it off to a handler class
that knows how to satisfy it - walk home and click a button, open the
backpack and throw a toy, pet the animal, etc. This repeats in a loop until
you press Stop.

## Requirements

- Windows (the automation relies on clicking into an actual Roblox window;
  it won't do anything useful on a headless machine)
- Python 3.9+
- Roblox running with Adopt Me open

Install dependencies:

```bash
pip install numpy opencv-python mss pyautogui pydirectinput pygetwindow
```

(`tkinter`, `os`, `sys`, `time`, `random`, `threading`, `abc`, `enum`, and
`pathlib` are all standard library - no install needed.)

## Running it

```bash
python main.py
```

This opens the control panel, pinned to the top-right of your screen. Put
your Roblox window somewhere it won't be covered by the panel.

## The GUI

| Button | What it does |
|---|---|
| **[START] WORKFLOW** | Runs one cycle: respawn, then detect and handle whatever needs are on screen. |
| **[LOOP] FULL WORKFLOW** | Repeats that cycle continuously until stopped. |
| **[STOP]** | Signals whatever is currently running to stop as soon as it safely can. Always clickable, even mid-action. |
| **Respawn** | Runs just the respawn sequence (Esc, R, Enter) on its own. |
| **[TEST] Pet / Ride / Choose** | Runs that one need handler directly, bypassing icon detection - useful for tuning a handler without waiting for its icon to appear naturally. |

The scrolling **Output** panel at the bottom mirrors everything printed to
the console, so you can watch what the bot is doing/deciding in real time.

## Code structure (top to bottom in `main.py`)

- **CONSTANTS** - every tunable number, timing, screen position, and color
  range lives here in one place. This is almost always what you edit when
  something needs recalibrating (see "Recalibrating" below).
- **WINDOW FOCUS & SCREEN CAPTURE** - brings the Roblox window to the
  front, grabs screenshots via `mss`, and `find_exact_color()`, which finds
  a button by its exact pixel color rather than its shape (used by the
  "choose" need, since that button has no distinguishing icon).
- **POSITIONING** - the character is modeled as being at one of a small
  set of known positions (`Position.RESPAWN`, `Position.HOME`). Each need
  handler declares which position (if any) it needs, and `move_to()` is
  the single place that knows how to actually get to each one.
- **ICON PROCESSING** - `preprocess_icon()` converts an icon crop to a
  high-contrast black & white image (via CLAHE + thresholding) so icons can
  be compared by *shape*, independent of their original color.
- **NEED ICON DETECTION** - `detect_need_icons()` scans a strip along the
  top of the screen for circular blobs matching a set of HSV color ranges,
  then `find_matching_need()` compares each one against every saved
  reference icon in `needs/` and returns the best match (if above
  `ICON_MATCH_THRESHOLD`). Icons the bot has never seen trigger
  `prompt_rename_need()`, which asks you (in the console) to name it and
  saves it into `needs/` for next time.
- **CLICKING** - three click helpers: `jitter_click()` (click, nudge the
  mouse a few pixels, click again - used for real in-game buttons, since a
  single perfectly-still click sometimes doesn't register), `simple_click()`
  (no jitter, used for backpack/toy UI), and `slow_click()` (deliberately
  slow mouse travel before clicking, used where a snapped-in click doesn't
  register the same way).
- **BUTTON DETECTION** - `detect_buttons()` looks for the purple,
  white-outlined action buttons (hungry/thirsty/dirty/potty/sleepy) in a
  band across the middle of the screen and returns their positions, sorted
  left to right. `refresh_button_mapping()` runs this and caches the result
  in the in-memory `BUTTON_POSITIONS` dict, keyed by name (e.g. `"hungry"`).
  This is re-detected fresh every single time the character reaches the
  home position, so nothing here is ever loaded from a stale save - the
  cache only exists to pass positions from detection to the click that
  immediately follows it.
- **MOVEMENT** - `respawn_character()` (Esc, R, Enter) and
  `walk_alternating()`, a shared helper for alternating between two keys
  for a duration (used by both the "walk" need, a/d, and the "ride" need,
  w/s).
- **NEED HANDLERS** - one class per need type, all implementing a common
  `NeedHandler` interface (`position` + `handle()`). See "Need handlers" below.
- **WORKFLOWS** - `run_full_cycle()` (respawn then process needs) and
  `run_workflow_loop()` (repeat forever until stopped).
- **GUI** - the `tkinter` control panel. `run_async()` is the important
  bit: it runs a workflow function on a background thread (so the GUI
  doesn't freeze) and *always* re-enables the buttons afterward, whether
  the workflow finished normally, was stopped, or crashed.

## Need handlers

Every need type is a small class with two things: a `position` it requires
(or `None` if it works from anywhere) and a `handle()` method with the
actual steps.

| Need | Requires | What it does |
|---|---|---|
| `hungry` / `thirsty` / `dirty` / `potty` / `sleepy` | HOME | Refreshes the button mapping, then clicks the matching button. |
| `catch` | anywhere | Opens the backpack, equips the squeaky toy, throws it 3x into empty space, unequips it. |
| `pet` | RESPAWN | Clicks to focus the pet, then holds the mouse down and moves it in a circle. *(Disabled by default - see `PET_ENABLED`.)* |
| `choose` | RESPAWN | Focuses the pet's menu, finds a button by its exact color, clicks it. *(Disabled by default - see `CHOOSE_ENABLED`.)* |
| `ride` | anywhere | Mounts a vehicle from the backpack, then walks back and forth for a while. |
| `walk` | none | Walks left-right for a while, then respawns. |

`get_need_handler()` is the dispatch point that maps a detected need name to
its handler class (with `CATCH_ENABLED` / `PET_ENABLED` / `CHOOSE_ENABLED`
flags letting you turn any of the three optional ones on or off without
touching the rest of the code).

## Stopping safely

Pressing **[STOP]** sets a flag (`STOP_FLAG`). `wait_interruptible()` (used
for essentially every delay in the bot) checks that flag between short
sleep chunks and raises `StopRequested` the instant it's set. That exception
unwinds the call stack all the way up through ordinary Python exception
propagation - no handler needs to manually check "did the user stop me?"
after every action.

The one thing that doesn't happen automatically is releasing a key or mouse
button that's currently held down (e.g. holding "w" to walk, or holding the
mouse button to pet). Every place that holds an input down wraps the wait in
`try/finally` so the release always happens, stop or no stop. As a final
safety net, `release_all_inputs()` runs once more whenever any background
task ends, releasing every key/button the bot ever touches.

This is deliberately *not* solved with a second thread that force-kills the
running one from outside - Python has no safe way to do that, and the
unsafe tricks that exist can land mid-action and leave a key stuck down in
the actual game, which is worse than the ~0.1s worst-case delay this
cooperative approach costs instead.

## Generated folders (not part of the source)

- **`needs/`** - reference icons the bot has learned, saved as high-contrast
  black & white PNGs (e.g. `need_hungry.png`). This *is* the bot's memory of
  what each need looks like; delete a file here and it will ask you to
  re-name that icon the next time it sees it.
- **`debug/`** - screenshots written on every detection pass so you can see
  what the bot is looking at:
  - `debug_needs.png` - the top strip with a circle drawn around every
    detected need icon.
  - `debug_buttons.png` - the full screen with a numbered circle on every
    detected action button.
  These are overwritten constantly and are pure debugging output - safe to
  delete anytime, and worth checking whenever detection isn't finding what
  you expect.

There is no config file - the only persistent state this bot has is the
`needs/` folder. Button positions are detected fresh every time the
character reaches home and are only ever kept in memory for the duration of
that detection, so nothing about screen positions is saved across runs.

## Recalibrating for your screen

Because this relies on fixed screen coordinates and color ranges tuned to a
specific resolution/UI, moving to a different monitor size or Roblox window
layout means updating the `CONSTANTS` section:

- Click positions (e.g. `CATCH_TOYS_POS`, `RIDE_VEHICLES_POS`, `FOCUS_PET_POS`)
  need to be re-measured against your own screen.
- `BUTTON_BAND_X` / `BUTTON_BAND_Y` (as a fraction of screen size) and the
  HSV color ranges (`PURPLE_RANGE`, `WHITE_RANGE`, etc.) may need tuning if
  button/icon detection isn't finding things - check `debug/debug_buttons.png`
  and `debug/debug_needs.png` first to see what the bot is actually seeing.

## Extending it

Adding a new need type means: create a `NeedHandler` subclass with a
`position` and a `handle()` method, add it to `NEED_HANDLER_CLASSES`, and
show the bot an example icon so it lands in `needs/` (or add the reference
PNG there yourself, named `need_<yourname>.png`).
