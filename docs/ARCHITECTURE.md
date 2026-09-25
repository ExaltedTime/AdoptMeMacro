# Architecture

Detailed technical walkthrough of `main.py`. This is for understanding or
modifying the code - if you just want to run the macro, see the main
[README](../README.md) instead.

Everything lives in the one file, organized top to bottom as a sequence of
`# === SECTION ===` blocks. This doc follows that same order.

## The workflow lifecycle

At the top level, exactly two things ever run:

- **`run_workflow()`** - a single pass: wait for a resolvable need, resolve
  it, done.
- **`run_workflow_loop()`** - respawns once up front (so the loop always
  starts from a known, on-the-ground state), then repeats `run_full_cycle()`
  forever, pausing `LOOP_DELAY` seconds between cycles, until stopped.

Both are built on **`run_full_cycle()`**, which is a tight retry loop:

```
while True:
    if process_needs():   # found something AND actually resolved it
        return
    wait NEED_CHECK_RETRY_DELAY seconds, then check again
```

The key word is *resolved*. `process_needs()` returns `True` only if it
actually did something - clicking a button or running a special handler.
Finding icons that don't lead to any action (see below) still returns
`False`, so the cycle keeps waiting and re-checking rather than treating a
no-op as progress.

### `process_needs()`, step by step

1. Focus Roblox and take a screenshot (`focus_roblox_click()`).
2. Detect every need icon currently on screen (`detect_need_icons()`).
   Nothing found → return `False` immediately.
3. For each detected icon, match it against the saved reference icons in
   `needs/` (`find_matching_need()`). An icon that doesn't match anything
   well enough triggers `prompt_rename_need()` (asks you, in the console,
   to name and save it) and is otherwise skipped this pass - it isn't
   "resolved," it's just been taught for next time.
4. Every icon that *did* match is collected into an ordered list of need
   names (`matched_needs`), in the order they were detected.
5. That list is then worked through **one need at a time**, and each one
   ends with its own respawn before moving to the next - nothing is
   batched. For each `need_name`:
   - `is_basic_need(need_name)` decides the path:
     - **Basic** (hungry/thirsty/dirty/potty/sleepy, or any other need
       name with no dedicated handler): walk to the action buttons
       (`walk_to_buttons()`), refresh the button mapping
       (`refresh_button_mapping()`), click the one matching button
       (`click_basic_need_button()`).
     - **Special** (catch, pet, choose, ride, or anything starting with
       `"walk"`): `get_special_need_handler(need_name)` returns a handler
       instance, or `None` if that need type is currently disabled
       (`CATCH_ENABLED` / `PET_ENABLED` / `CHOOSE_ENABLED`) - a `None`
       result is logged and skipped, without walking anywhere or
       respawning. A special need never walks to the buttons first; it
       acts from wherever the character already is.
   - If the need actually ran (a basic click, or an enabled special
     handler), the character respawns immediately - **before** the next
     matched need is even looked at.
6. `process_needs()` returns `True` if at least one need in the list
   actually ran, `False` otherwise (e.g. every match turned out to be a
   disabled special). A `False` return is what sends `run_full_cycle()`
   back to waiting and re-checking instead of treating a no-op as
   progress.

Respawning between every individual need - rather than once per cycle - is
what keeps `walk_to_buttons()` reliable when multiple needs are detected
together: it always starts from the same known respawn point, instead of
compounding an extra walk from wherever the previous need left the
character standing.

## Code structure, top to bottom

- **CONSTANTS** - every tunable number, timing, screen position, and color
  range lives here in one place. See [Configuration reference](#configuration-reference)
  below for the groups; exact values and their one-line rationale are in
  the comments in `main.py` itself.
- **`StopRequested` / `FocusLost` / `check_stop()` / `check_focus()` /
  `check_running()`** - see [Stopping & focus safety](#stopping--focus-safety).
- **WINDOW FOCUS & SCREEN CAPTURE** - `focus_roblox()` / `focus_roblox_click()`
  bring Roblox to the front; `is_roblox_focused()` checks whether it still
  is; `grab_screen()` takes a screenshot via `mss`; `find_exact_color()`
  finds a button by its exact pixel color rather than its shape (used by
  the choose need, whose button has no distinguishing icon).
- **STATE** - `BUTTON_POSITIONS`, the in-memory cache of the
  last-detected action button positions. Never persisted to disk - see
  [Why there's no config file](#why-theres-no-config-file).
- **ICON PROCESSING** - `preprocess_icon()` / `compare_icons()`. See
  [Need-icon matching](#need-icon-matching).
- **NEED ICON DETECTION** - `detect_need_icons()`, `find_matching_need()`,
  `prompt_rename_need()`. See [Need-icon detection](#need-icon-detection).
- **CLICKING** - `jitter_click()`, `simple_click()`, `slow_click()`,
  `scroll_wheel_up()`, `wait_interruptible()`, `release_all_inputs()`. See
  [Click & input primitives](#click--input-primitives).
- **BUTTON DETECTION** - `detect_buttons()`, `refresh_button_mapping()`,
  `click_basic_need_button()`. See [Button detection](#button-detection).
- **MOVEMENT** - `respawn_character()`, `walk_to_buttons()`,
  `walk_alternating()` (shared by the walk need, a/d, and the ride need,
  w/s - same pattern, different keys and duration passed in).
- **NEED HANDLERS** - one class per special need. See [Need handlers](#need-handlers).
- **WORKFLOWS** - `run_full_cycle()`, `run_workflow()`, `run_workflow_loop()`.
  See [The workflow lifecycle](#the-workflow-lifecycle) above.
- **GUI** - the `tkinter` control panel. See [GUI internals](#gui-internals).

## Need-icon detection

`detect_need_icons()` scans a region of the screen for circular icons of
any color:

1. Crop to the top `NEED_ICON_TOP_PERCENT` (15%) of screen height and the
   left `NEED_ICON_WIDTH_PERCENT` (70%) of screen width. The width crop
   exists specifically so the GUI panel itself - docked top-right and kept
   always-on-top - is never captured in the same screenshot the macro is
   scanning, which would otherwise let the macro mistake its own colorful
   buttons for a need icon.
2. Blank out a small top-left rectangle (`NEED_ICON_BLANK_HEIGHT` x
   `NEED_ICON_BLANK_WIDTH`) where Roblox's own persistent UI cluster
   (chat, player list toggle, etc.) lives, so it's never mistaken for a
   need icon either.
3. Build a combined mask from several HSV color ranges
   (`NEED_ICON_COLOR_RANGES`: blue, purple, both red wraps, yellow, green,
   cyan) - need icons can be almost any color, so this is deliberately
   broad rather than tuned to one hue.
4. Find contours in the mask, keep the ones large enough
   (`NEED_ICON_MIN_AREA`) and round enough
   (`NEED_ICON_MIN_CIRCULARITY = area / (π·r²)`) to plausibly be an icon
   rather than noise.

Each surviving icon is cropped out of the full screenshot
(`extract_icon()`, with `ICON_EXTRACT_PADDING` px of margin) and handed to
`find_matching_need()`.

## Need-icon matching

Icons are matched by *shape*, not color, because the same need can render
in different colors depending on context. `preprocess_icon()` converts a
crop to a high-contrast black & white image:

1. Grayscale.
2. CLAHE (adaptive local contrast enhancement - `ICON_CLAHE_CLIP` /
   `ICON_CLAHE_TILE`) so the icon's internal detail stands out regardless
   of ambient lighting/brightness in the screenshot.
3. Hard threshold (`ICON_BW_THRESHOLD`) - only near-white pixels survive,
   collapsing the icon to a black-and-white silhouette.

`compare_icons()` then resizes both images to `ICON_COMPARE_SIZE` and
combines two similarity measures, averaged:

- **MSE similarity** - `1 - (mean squared pixel difference / 255²)`, a
  blunt pixel-by-pixel closeness score.
- **Histogram similarity** - `1 / (1 + Bhattacharyya distance)` between the
  two images' intensity histograms, which is more tolerant of small
  shifts/rotations than raw pixel comparison.

`find_matching_need()` compares the new icon against every saved `.png` in
`needs/` and keeps the best score. A best score below
`ICON_MATCH_THRESHOLD` (0.92) means "not confident this is anything we've
seen" and falls through to `prompt_rename_need()` instead.

## Button detection

`detect_buttons()` finds the five purple, white-outlined action buttons
(hungry/thirsty/dirty/potty/sleepy) that appear once the character is at
the buttons:

1. Crop to a band across the middle of the screen (`BUTTON_BAND_X` /
   `BUTTON_BAND_Y`).
2. Build a purple mask (`PURPLE_RANGE`) and a white mask (`WHITE_RANGE`) in
   that band.
3. Build an "outline zone" by dilating the purple mask
   (`BUTTON_PURPLE_DILATE_ITERATIONS`) and subtracting a slightly eroded
   version of itself (`BUTTON_PURPLE_ERODE_ITERATIONS`) - this isolates a
   thin ring just outside each purple blob, which is where a real button's
   white outline should be. Intersecting that ring with the white mask
   gives a "has a white outline" score per candidate.
4. Find purple contours, filtering out ones that are too small
   (`BUTTON_MIN_AREA * BUTTON_LOOSE_AREA_FACTOR`), too large
   (`BUTTON_MAX_AREA_FRACTION` of the band - a blob covering most of the
   band isn't a button), too small in radius (`BUTTON_MIN_RADIUS`), or not
   round enough (`BUTTON_MIN_CIRCULARITY * BUTTON_LOOSE_CIRCULARITY_FACTOR`).
   These thresholds are deliberately looser than the need-icon ones, since
   buttons are more variable in how cleanly they contour.
5. Prefer candidates with a white outline, then by size; take the top
   `BUTTON_MAX_COUNT` (5), then sort left-to-right.
6. `refresh_button_mapping()` zips that left-to-right order against
   `BUTTON_NAMES = ["hungry", "thirsty", "dirty", "potty", "sleepy"]` and
   stores the result in `BUTTON_POSITIONS`. This assumes the five buttons
   are always laid out in that fixed order on screen - if Roblox ever
   changes that order, this mapping (and only this mapping) would need
   updating.

## Click & input primitives

Three click styles, all built on the same shape (move → settle → click →
settle):

- **`jitter_click()`** - clicks, nudges the mouse a few random pixels
  (`JITTER_PIXELS`), clicks again. Used for real in-game action buttons,
  since a single perfectly-still click sometimes doesn't register.
- **`simple_click()`** - one click, no jitter. Used for backpack/toy UI.
- **`slow_click()`** - moves deliberately slowly (a caller-supplied
  duration, e.g. `CHOOSE_SLOW_MOVE_DURATION`) instead of the usual quick
  `CLICK_MOVE_DURATION`, for targets that don't register a snapped-in
  click the same way.
- **`scroll_wheel_up()`** - moves to a point and scrolls up
  (`CATCH_SCROLL_AMOUNT` notches) via `pyautogui.scroll()`, since
  `pydirectinput` has no scroll function of its own. Used by the catch
  need before each throw.

`wait_interruptible(duration)` is the delay primitive nearly everything
else is built on - see [Stopping & focus safety](#stopping--focus-safety).

## Need handlers

**Currently active** (handler exists *and* enabled - these are the only
needs the macro will act on by itself right now):

- `hungry`, `thirsty`, `dirty`, `potty`, `sleepy` - basic, always on
- `catch` - `CATCH_ENABLED = True`
- `ride` - no flag, always on
- `walk` - no flag, always on

**Implemented but disabled** (handler exists, flag is off - a detected
icon for these is logged and skipped, not resolved):

- `pet` - `PET_ENABLED = False`
- `choose` - `CHOOSE_ENABLED = False`

| Need | How it's handled |
|---|---|
| `hungry` / `thirsty` | Basic. Walk to the buttons, click, wait `POST_NEED_CLICK_WAIT_SHORT` (10s). |
| `dirty` / `potty` / `sleepy` / any unrecognized need | Basic. Walk to the buttons, click, wait `POST_NEED_CLICK_WAIT` (15s). |
| `catch` | `CatchNeedHandler`: open backpack → toys → squeaky toy → equip → close backpack → wait `CATCH_WAIT_AFTER_EQUIP` → scroll up + click empty space, `CATCH_THROW_COUNT` (3) times, `CATCH_EMOTE_DELAY` apart → unequip. |
| `pet` | `PetNeedHandler`: click to focus the pet, then hold the mouse down and trace a circle of radius `PET_CIRCLE_RADIUS` around screen center for `PET_CIRCLE_DURATION`. *Disabled by default (`PET_ENABLED`).* |
| `choose` | `ChooseNeedHandler`: focus the pet, find the exact-color button (`CHOOSE_BUTTON_COLOR`, since it has no distinguishing icon), move to it slowly and click, then click screen-center to dismiss the menu. *Disabled by default (`CHOOSE_ENABLED`).* |
| `ride` | `RideNeedHandler`: step back, mount (`e`), walk forward briefly, then backpack → vehicles → first vehicle → equip → close backpack, then `walk_alternating(("w", "s"), RIDE_WALK_DURATION)`. |
| `walk` (any name starting with it) | `WalkNeedHandler`: `walk_alternating(("a", "d"), WALK_TOTAL_DURATION)`. |

Dispatch: `is_basic_need(name)` returns `False` for anything starting with
`"walk"` or in `SPECIAL_NEED_NAMES = {"catch", "pet", "choose", "ride"}` -
everything else is basic. `get_special_need_handler(name)` is only ever
called for a non-basic name, and returns `None` if that need's `_ENABLED`
flag is off.

A basic need only counts as resolved (and only then triggers a respawn) if
`refresh_button_mapping()` actually found buttons *and*
`click_basic_need_button()` found this specific need's button in that
mapping - both return a bool for exactly this reason. If either fails (no
buttons detected at all, or this particular one wasn't among them), the
need is logged and skipped for this pass with no respawn, the same as a
disabled special.

`PET_ENABLED` and `CHOOSE_ENABLED` default to `False` - both handlers are
fully implemented but not wired into automatic processing yet. Their
`[TEST]` buttons in the GUI run them directly regardless of the flag.
`CATCH_ENABLED` exists for the same purpose but defaults to `True`.

## Stopping & focus safety

Two independent conditions can interrupt a running workflow, and both are
checked the same way:

- **`StopRequested`** - raised when `STOP_FLAG` is set (the user pressed
  **[STOP]**).
- **`FocusLost`** - raised when Roblox is no longer the focused window.
  Without this, a long-running action (a 20s walk, a 10s pet-circle, a
  wait between catch throws) would keep sending keypresses/clicks into
  whatever window the user actually switched to.

`check_running()` checks both (`check_stop()` then `check_focus()`) and is
the single checkpoint used everywhere: inside `wait_interruptible()` (so
essentially every delay in the macro is a checkpoint), and in any manual
loop that doesn't otherwise call it (e.g. `PetNeedHandler`'s mouse-circle
loop).

One exception: the very first check at the top of `run_full_cycle()`'s
retry loop uses `check_stop()` alone, not `check_running()`. That's
deliberate - the moment you click **[START]**/**[LOOP]** in the Python
GUI, *that* window has focus, not Roblox. `process_needs()` is what brings
Roblox to the front a moment later; requiring focus before that first
attempt would make the buttons non-functional. Every checkpoint after that
first one does require focus.

Both exceptions unwind via ordinary Python exception propagation, all the
way up to `run_async()`'s worker thread, which reports `[STOPPED]` or
`[STOPPED: Roblox not focused]` respectively. Anywhere a key or the mouse
button is held down (walking, petting) wraps the hold in `try/finally` so
it's always released on the way up, regardless of which exception caused
the unwind. `release_all_inputs()` runs once more in `run_async`'s
`finally` block as a last line of defense.

This is deliberately *not* solved with a second "killer" thread that
force-stops the running one from outside - Python has no safe way to do
that, and the unsafe tricks that exist (async-raising into another thread)
can land mid-action and leave a key stuck down in the actual game, which
is worse than the small delay this cooperative approach costs instead.

## Why there's no config file

The only persistent state this macro has is the `needs/` folder (the
learned reference icons). Button positions (`BUTTON_POSITIONS`) are
detected fresh every single time the character walks to the buttons, so
there's nothing gained by saving them to disk - they're never trusted
across a run anyway, and a stale save would just be actively wrong if the
screen resolution or Roblox's UI ever changed between sessions.

## GUI internals

`AdoptMeGUI.create_ui()` builds the whole panel. A few things worth
knowing if you're modifying it:

- **`run_async(func)`** is how every long-running action is started: it
  disables every button in `self.action_buttons`, runs `func` on a
  background daemon thread (so Tkinter's main loop never blocks), and
  *always* re-enables them in a `finally` block - normal finish, a
  `StopRequested`/`FocusLost`, or an unexpected exception all take the
  same path back to a usable UI. `[STOP]` is deliberately excluded from
  `action_buttons`, since it must stay clickable while something is
  running.
- **The main row** (start/loop/stop) gives all three buttons their own
  fixed-pixel-**height** `Frame` container (`pack_propagate(False)`),
  rather than relying on `Button`'s own `width`/`height` (character
  units) - those scale inconsistently across different font sizes, and an
  unconstrained button's natural height grows with its font, which is
  what broke the row's alignment the first two times a font size changed.
  The two side containers fix both width and height (making them equal
  squares); the center container fixes only height and otherwise expands
  (`fill=tk.X, expand=True`) to fill the remaining width. Fixing height on
  all three individually, rather than letting the center one inherit it
  from its siblings, is what keeps the row aligned regardless of any
  future font size change to any one of them.
- Every button in that row is created with `bd=0, highlightthickness=0` to
  flatten Tk's default border/focus-ring rendering.
- **The single-cycle button's label** is plain button text
  (`"\U0001F5041"`, the refresh icon followed by "1") rather than a
  separate overlay widget - an earlier version tried layering a `Label`
  with `place()` on top of the icon for a cleaner look, but Tk's border
  rendering kept showing a visible seam/box behind it even with matching
  fill colors, so plain text turned out simpler and more reliable.
- **`DebugCapture`** redirects `sys.stdout` into the on-screen console
  (`self.debug_text`) for the lifetime of the GUI, so every `print()`
  anywhere in the macro shows up there automatically.

## Configuration reference

Everything in `CONSTANTS` falls into one of these groups (see the comments
next to each constant in `main.py` for exact values and rationale):

| Group | Examples |
|---|---|
| Behavior flags | `CATCH_ENABLED`, `PET_ENABLED`, `CHOOSE_ENABLED`, `FOCUS_WINDOW_ON_ACTION` |
| Timing | `RESPAWN_WAIT`, `WALK_TO_BUTTONS_DURATION`, `POST_NEED_CLICK_WAIT[_SHORT]`, `NEED_CHECK_RETRY_DELAY`, `LOOP_DELAY`, `STOP_CHECK_INTERVAL` |
| Screen positions | `CATCH_*_POS`, `EMPTY_POS`, `FOCUS_PET_POS`, `RIDE_*_POS` |
| Need-icon detection | `NEED_ICON_TOP_PERCENT`, `NEED_ICON_WIDTH_PERCENT`, `NEED_ICON_BLANK_*`, `NEED_ICON_MIN_AREA`, `NEED_ICON_MIN_CIRCULARITY` |
| Need-icon matching | `ICON_MATCH_THRESHOLD`, `ICON_BW_THRESHOLD`, `ICON_CLAHE_*`, `ICON_COMPARE_SIZE` |
| Button detection | `BUTTON_BAND_X/Y`, `BUTTON_MIN_AREA`, `BUTTON_MIN_CIRCULARITY`, `BUTTON_MAX_COUNT`, `BUTTON_NAMES`, `BUTTON_LOOSE_*_FACTOR`, `BUTTON_PURPLE_*_ITERATIONS`, `BUTTON_OUTLINE_PADDING` |
| Debug drawing | `DEBUG_NEED_MARKER_*`, `DEBUG_BUTTON_MARKER_*`, `DEBUG_BUTTON_LABEL_*` |
| Color ranges (HSV) | `PURPLE_RANGE`, `WHITE_RANGE`, `BLUE_RANGE`, `RED_RANGE_1/2`, `YELLOW_RANGE`, `GREEN_RANGE`, `CYAN_RANGE` |

If detection isn't finding what you expect after changing screen
resolution or Roblox's UI, check `debug/debug_needs.png` and
`debug/debug_buttons.png` first - both are overwritten every detection
pass and show exactly what the macro is looking at - before tuning any of
these.
