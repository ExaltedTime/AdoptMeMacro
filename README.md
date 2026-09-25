# Adopt Me Bot

A Python desktop bot that watches the Roblox game **Adopt Me** for pet-care
"need" icons (hunger, thirst, etc.) and automatically performs the action
that satisfies each one, controlled from a small `tkinter` GUI.

Everything lives in a single file: `main.py`.

## How it works, in one paragraph

The bot takes a screenshot, looks at a strip along the top of the screen for
colored circular icons, and compares each one (using shape/contrast, not
raw color) against a folder of reference icons it has seen before
(`needs/`). Once it recognizes a need, it hands it off to whatever satisfies
it - walk to the action buttons and click one, open the backpack and throw
a toy, pet the animal, etc. This repeats in a loop until you press Stop.

## Requirements

- Windows (the automation relies on clicking into an actual Roblox window;
  it won't do anything useful on a headless machine)
- Python 3.9+
- Roblox running with Adopt Me open

Install dependencies:

```bash
pip install numpy opencv-python mss pyautogui pydirectinput pygetwindow
```

## Running it

```bash
python main.py
```

This opens the control panel, pinned to the top-right of your screen. Put
your Roblox window somewhere it won't be covered by the panel.

## The GUI

The top row has three buttons side by side: a small square on the left, a
wide button filling the center, and a small square on the right.

| Button | What it does |
|---|---|
| 🔄 with a **1** (left square) | Waits for a need to appear (checking every few seconds), then handles it, once. |
| 🔄 (center, wide) | Same as the button above, but repeats continuously until stopped. |
| ■ (right square) | Signals whatever is currently running to stop as soon as it safely can. Always clickable, even mid-action. |
| **Respawn** | Runs just the respawn sequence (Esc, R, Enter) on its own. |
| **[TEST] Catch / Pet / Ride / Choose** | Runs that one need handler directly, bypassing icon detection - useful for tuning a handler without waiting for its icon to appear naturally. |

The scrolling **Output** panel at the bottom mirrors everything printed to
the console, so you can watch what the bot is doing/deciding in real time.

## Code structure

What this does: watches the Roblox "Adopt Me" window for pet-care need icons
(hunger, thirst, etc.) and either clicks the matching action button or runs
a dedicated routine for it (catch, pet, choose, ride, walk).

Flow: `run_full_cycle()` checks for needs, and if none are on screen, waits
`NEED_CHECK_RETRY_DELAY` seconds and checks again - it keeps doing this
until it finds something. Once needs are found, `process_needs()` splits
them into two groups and handles each differently:

- **Basic needs** (hungry/thirsty/dirty/potty/sleepy, or any other need
  with no dedicated handler) are handled in place: walk forward to the
  action buttons once, refresh the button mapping, then click each matching
  button.
- **Special needs** (catch, pet, choose, ride, walk) each have dedicated
  logic and work from wherever the character currently is.

Either way, the character respawns once after handling whatever was
found - that's also what puts it back at a known spot in time for the next
cycle's check.

`PET_ENABLED` and `CHOOSE_ENABLED` are OFF by default (near the top of
`CONSTANTS`) - both are fully implemented but not yet wired into automatic
need processing, so they're skipped in `process_needs()` until flipped on.
Their `[TEST]` buttons in the GUI run them directly regardless. `CATCH_ENABLED`
exists for the same purpose but defaults to on. Every other need type is
live and has no manual GUI trigger of its own - Respawn is the only one
that still does, since it's occasionally useful to fire on its own.

Key pieces, top to bottom:

- **CONSTANTS** - every tunable number, timing, screen position, and color
  range lives here in one place.
- **WINDOW FOCUS & SCREEN CAPTURE** - bring Roblox to front, grab
  screenshots, and `find_exact_color()` for buttons matched by color rather
  than shape (used by the choose need).
- **STATE** - `BUTTON_POSITIONS`, the in-memory cache of the last-detected
  action button positions.
- **ICON PROCESSING** - turns an icon into strict black & white so it can be
  matched regardless of its original color.
- **NEED ICON DETECTION** - finds circular need icons at the top of screen,
  compares them to saved reference icons in `needs/`.
- **CLICKING** - `jitter_click()` (click, nudge, click again - used for the
  actual need buttons) / `simple_click()` (used for backpack/toy UI clicks)
  / `slow_click()` (deliberately slow travel before clicking).
- **BUTTON DETECTION** - finds the purple action buttons (hungry, thirsty,
  dirty, potty, sleepy) and caches their screen positions in
  `BUTTON_POSITIONS`.
- **MOVEMENT** - `respawn_character()`, `walk_to_buttons()` (walk forward to
  where the action buttons are), and `walk_alternating()` (shared by walk,
  a/d, and ride, w/s - same pattern, different keys and duration passed in).
- **NEED HANDLERS** - one class per special need type (ABC pattern), each
  declaring what to do to satisfy it. `CatchNeedHandler` runs a backpack ->
  equip toy -> throw x3 -> unequip sequence; `RideNeedHandler` backpack ->
  equip vehicle -> walk; `ChooseNeedHandler` finds and clicks a button by
  its exact color. `is_basic_need()` / `get_special_need_handler()` decide
  whether a detected need goes to one of these or is treated as basic.
- **WORKFLOWS** - `run_full_cycle()` (wait for and handle one need),
  `run_workflow_loop()` (repeats it forever until stopped).
- **GUI** - tkinter control panel; every button that starts a background
  task runs it via `run_async()`, which disables all such buttons while
  it's running and ALWAYS re-enables them in a `finally` block once it ends
  - normal finish, stop, or crash alike.

## Need handlers

| Need | How it's handled |
|---|---|
| `hungry` / `thirsty` | Basic: walk to the action buttons, click the matching one, wait 10s (`POST_NEED_CLICK_WAIT_SHORT`). |
| `dirty` / `potty` / `sleepy` (and any unrecognized need) | Basic: walk to the action buttons, click the matching one, wait 15s (`POST_NEED_CLICK_WAIT`). |
| `catch` | Opens the backpack, equips the squeaky toy, throws it 3x into empty space, unequips it. |
| `pet` | Clicks to focus the pet, then holds the mouse down and moves it in a circle. *(Disabled by default - see `PET_ENABLED`.)* |
| `choose` | Focuses the pet's menu, finds a button by its exact color, clicks it. *(Disabled by default - see `CHOOSE_ENABLED`.)* |
| `ride` | Mounts a vehicle from the backpack, then walks back and forth for a while. |
| `walk` | Walks left-right for a while. |

A respawn always follows, whether the need was basic or special.

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

## The `needs/` folder

Reference icons the bot has learned, saved as high-contrast black & white
PNGs (e.g. `need_hungry.png`). This *is* the bot's memory of what each need
looks like - delete a file here and it will ask you to re-name that icon
the next time it sees it. It's the only persistent state this bot has;
there is no config file.

## Debug

Every detection pass writes screenshots into a `debug/` folder next to
`main.py`, so you can see exactly what the bot is looking at:

- `debug_needs.png` - the top strip with a circle drawn around every
  detected need icon.
- `debug_buttons.png` - the full screen with a numbered circle on every
  detected action button.

Both are overwritten on every pass and are pure debugging output - safe to
delete anytime, and the first place to look whenever detection isn't
finding what you expect (e.g. tune `PURPLE_RANGE` / `BUTTON_BAND_X` /
`BUTTON_BAND_Y` if `debug_buttons.png` shows no or wrong markers).
