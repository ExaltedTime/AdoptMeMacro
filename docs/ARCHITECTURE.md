# Architecture

Detailed technical walkthrough of `main.py`. This is for understanding or
modifying the code - if you just want to run the macro, see the main
[README](../README.md) instead.

Almost everything lives in `main.py`, organized top to bottom as a sequence
of `# === SECTION ===` blocks; this doc follows that same order. The one
exception is `magic_numbers.py`, which holds every tunable constant (see
[Configuration reference](#configuration-reference)) - `main.py` pulls
them all in with `from magic_numbers import *` and refers to them by bare
name everywhere else.

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
   batched. For each `need_name`, `get_need_handler(need_name)` looks it up
   in `NEED_HANDLER_CLASSES` and returns an instance, or `None` if that
   need isn't in `ENABLED_NEEDS` - a `None` result is logged and skipped
   without doing anything. Every need is handled the same way from here:
   calling `.handle()` on whatever handler came back (see
   [Need handlers](#need-handlers)).
   - If `handle()` returns `True`, the character respawns immediately
     (always done by `process_needs()`, never by the handler itself) -
     **before** the next matched need is even looked at.
   - If it returns `False` (the need wasn't actually resolved this pass -
     e.g. no buttons detected, or a UI element `ChooseNeedHandler` needed
     wasn't found), the need is logged and skipped with no respawn.
6. `process_needs()` returns `True` if at least one need in the list
   actually ran, `False` otherwise (e.g. every match turned out to be
   disabled, or every handler that ran returned `False`). A `False` return
   is what sends `run_full_cycle()` back to waiting and re-checking instead
   of treating a no-op as progress.

Respawning between every individual need - rather than once per cycle - is
what keeps `walk_to_buttons()` reliable when multiple needs are detected
together: it always starts from the same known respawn point, instead of
compounding an extra walk from wherever the previous need left the
character standing.

## Code structure, top to bottom

- **`StopRequested` / `FocusLost` / `check_stop()` / `check_focus()` /
  `check_running()`** - see [Stopping & focus safety](#stopping--focus-safety).
- **WINDOW FOCUS & SCREEN CAPTURE** - `focus_roblox()` / `focus_roblox_click()`
  bring Roblox to the front; `is_roblox_focused()` checks whether it still
  is; `grab_screen()` takes a screenshot via `mss`; `find_exact_color()`
  finds a button by its exact pixel color rather than its shape (used by
  the choose need, whose button has no distinguishing icon).
- **STATE** - this module's own mutable state: `STOP_FLAG`, and
  `BUTTON_POSITIONS`, the in-memory cache of the last-detected action
  button positions (never persisted to disk - see
  [Persisted game config](#persisted-game-config)). Every fixed
  config/tuning value lives in `magic_numbers.py` instead - see
  [Configuration reference](#configuration-reference).
- **GAME CONFIG** - `load_game_config()`/`save_game_config()`: the state
  that *does* persist across separate launches of the script (unlike
  `BUTTON_POSITIONS` above) - see [Persisted game config](#persisted-game-config).
- **ICON PROCESSING** - `preprocess_icon()` / `icon_signature()` / `compare_signatures()`. See
  [Need-icon matching](#need-icon-matching).
- **NEED ICON DETECTION** - `detect_need_icons()`, `find_matching_need()`,
  `prompt_rename_need()`. See [Need-icon detection](#need-icon-detection).
- **CLICKING** - `hover_click()`, `hover_move()`, `wait_interruptible()`,
  `release_all_inputs()`. See
  [Click & input primitives](#click--input-primitives).
- **BUTTON DETECTION** - `detect_buttons()`, `refresh_button_mapping()`,
  `click_need_button()`. See [Button detection](#button-detection).
- **MOVEMENT** - `respawn_character()`, `walk_to_buttons()`,
  `walk_alternating()` (shared by the walk need, a/d, and the ride need,
  w/s - same pattern, different keys and duration passed in).
- **GUI-ONLY ACTIONS** - `lure_collect()`, `set_new_lure()`, `tree_collect()`,
  `setup_game()`: plain callable actions, not tied to any detected
  need/icon, reachable from the GUI's Functions section (same as
  `respawn_character()`) and also auto-triggered by `side_quest()`/
  `ensure_setup()` - see the table at the end of
  [Need handlers](#need-handlers).
- **NEED HANDLERS** - `NeedHandler` and one subclass per need (or per group
  of needs, via `partial()`). See [Need handlers](#need-handlers).
- **WORKFLOWS** - `run_full_cycle()`, `run_workflow()`, `run_workflow_loop()`,
  `side_quest()`, `ensure_setup()`.
  See [The workflow lifecycle](#the-workflow-lifecycle) above.
- **GUI** - the `tkinter` control panel. See [GUI internals](#gui-internals).

## Need-icon detection

`detect_need_icons()` scans a region of the screen for the round need icons:

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
3. Find circles with a Hough transform (`NEED_ICON_MIN_RADIUS` to
   `NEED_ICON_MAX_RADIUS`, at least `NEED_ICON_MIN_DISTANCE` apart) on the
   blurred grayscale strip. Icons are found by their round outline, not
   their color, so event badges overlapping an icon, differently colored
   icons and busy backgrounds (grass, scenery) don't affect detection.

Each detected icon is cropped out of the full screenshot at a fixed
radius (`ICON_CROP_RADIUS` + `ICON_EXTRACT_PADDING` px of margin) and handed
to `find_matching_need()`.

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

`icon_signature()` then resizes the result to `ICON_COMPARE_SIZE`, scales it
to 0-1, and keeps only the **left half** (`MATCH_ONLY_LEFT_HALF`) so an
event badge overlapping an icon's top right doesn't affect the match.
`compare_signatures()` scores two signatures as `1 - mean squared
difference`.

A detected center can be a pixel or two off, which matters at this
resolution, so `icon_variants()` cuts the live icon out at every offset
within `ICON_SHIFT_TOLERANCE` px and each saved need is scored by its
best-aligned variant.

`find_matching_need()` compares the new icon against every saved `.png` in
`needs/` and keeps the best score. A best score below
`ICON_MATCH_THRESHOLD` (0.93) means "not confident this is anything we've
seen" and falls through to `prompt_rename_need()` instead.

## Waiting for a need to clear

`wait_until_need_gone()` - used after a button click (`click_need_button()`),
after `ChooseNeedHandler` dismisses its menu, and after
`TeleportWalkNeedHandler`'s movement sequence - polls
`detected_need_names()` (one fresh detect-and-match pass) every
`NEED_GONE_POLL_INTERVAL` until the target need is missing
`NEED_GONE_CONFIRMATIONS` times in a row (any sighting resets the count
back to zero), up to `NEED_GONE_MAX_WAIT` total.

Each individual check goes through `_need_cleared()`, which re-samples
once more, `NEED_GONE_FLICKER_RECHECK_DELAY` later, before a miss is
allowed to count: the icon can drop out of a single detection pass for a
frame (or against a momentarily busy background) without the need having
actually cleared, and a bare miss-streak alone isn't enough to tell that
apart from the real thing. This only guards against a one-frame flicker -
a detection failure caused by a *sustained* background change (lighting,
a different area of the map) behind the icon isn't something this
re-check can fix, since it'll fail again on the immediate retry too.

`_need_cleared()` is also what `_watch_need_gone()` uses - see
[Checking a need in parallel with movement](#checking-a-need-in-parallel-with-movement)
- so both the standalone wait and the background-thread version share the
exact same debounce logic.

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

Every click in the macro goes through **`hover_click()`** /
**`hover_move()`**: moves with SendInput (`pydirectinput`), nudges
`HOVER_NUDGE_PIXELS` and back so Roblox registers real mouse movement
(hover), then clicks. Roblox ignores `pyautogui`'s cursor warps as hover
movement, so this is what makes a click register reliably whether it's on
a UI button or into empty game-world space (there used to be a separate
`simple_click()` with no hover wiggle for the latter case, but it wasn't
actually needed - `hover_click()` works fine there too, so it was dropped).

`wait_interruptible(duration)` is the delay primitive nearly everything
else is built on - see [Stopping & focus safety](#stopping--focus-safety).

## Need handlers

Every need - including hungry/thirsty/dirty/potty/sleepy - is resolved by
a `NeedHandler` instance; there's no separate code path for any of them.
`NEED_HANDLER_CLASSES` (in `main.py`, near `process_needs()`) is the
factory: one dedicated class per need with real logic, plus
`ButtonNeedHandler` and `TeleportWalkNeedHandler` registered once per name
via `partial()` for the needs that only differ by a name/config
(`BUTTON_NAMES` and `TELEPORT_WALK_NEEDS` respectively).

Which needs actually run automatically is decided by one set,
`ENABLED_NEEDS` (`magic_numbers.py`, the first thing defined in that
file). A detected need that isn't in it is logged and skipped, not
resolved. Currently enabled: `hungry`, `thirsty`, `dirty`, `potty`,
`sleepy`, `catch`, `pet`, `ride`, `walk`, `choose`, plus the
teleport-walk needs `cafe`, `salon`, `sick`, `pizza` (called out with
their own inline comment in the set literal, since they leave the map and
run considerably longer than everything else). Implemented but not
enabled: `bored`, `beach`, `school`, `camping` (also teleport-walk needs).
The GUI's **Debug** tab has a button per handler that runs it directly
regardless of `ENABLED_NEEDS`.

Except for `catch` and `pet` - which always run their full fixed sequence
precisely, with no early exit - every other need either waits for its
icon to actually clear (`wait_until_need_gone()`) instead of guessing how
long that takes, or checks for that *while* still moving (see below),
rather than a flat sleep after the fact.

| Need | How it's handled |
|---|---|
| `hungry` / `thirsty` / `dirty` / `potty` / `sleepy` | `ButtonNeedHandler`: walk to the action buttons (`walk_to_buttons()`), refresh the button mapping (`refresh_button_mapping()`), click the matching one (`click_need_button()`), which then calls `wait_until_need_gone()` for that need name. |
| `catch` | `CatchNeedHandler`: open backpack → toys → squeaky toy → equip → close backpack → wait `CATCH_WAIT_AFTER_EQUIP` → scroll up + click empty space, `CATCH_THROW_COUNT` (3) times, `CATCH_EMOTE_DELAY` apart → unequip. Always runs this exact sequence - no `wait_until_need_gone()` involved. |
| `pet` | `PetNeedHandler`: click to focus the pet, then hold the mouse down and trace a circle of radius `PET_CIRCLE_RADIUS` around screen center for `PET_CIRCLE_DURATION`. Same as `catch` - always the full fixed duration. |
| `choose` | `ChooseNeedHandler`: focus the pet, find the exact-color button (`CHOOSE_BUTTON_COLOR`, since it has no distinguishing icon), hover to it slowly (`hover_click`) and click, then click screen-center to dismiss the menu, then `wait_until_need_gone("choose")`. |
| `ride` | `RideNeedHandler`: step back, mount (`e`), walk forward briefly, then backpack → vehicles → first vehicle → equip → close backpack, then `walk_alternating(("w", "s"), NEED_GONE_MAX_WAIT, need_name="ride")` - up to `NEED_GONE_MAX_WAIT` (60s), ending early the moment "ride" is confirmed cleared (see below). |
| `bored` / `beach` / `school` / `cafe` / `salon` / `pizza` / `camping` / `sick` | `TeleportWalkNeedHandler`, configured per need in `TELEPORT_WALK_NEEDS`: teleport to the nursery or dealership (`teleport_to()`), hold each `(key, seconds)` step with `hold_key()`, then `wait_until_need_gone()` (see [Waiting for a need to clear](#waiting-for-a-need-to-clear)). `process_needs()` respawns afterwards. An entry may also set `final_click`, clicked after the last hold (used by `sick`). |
| `walk` | `WalkNeedHandler`: `walk_alternating(("a", "d"), NEED_GONE_MAX_WAIT, need_name="walk")` - up to `NEED_GONE_MAX_WAIT` (60s), same early-exit as `ride` above. |

A handler only counts as resolved (and only then triggers a respawn) if
`handle()` returns `True`. `ButtonNeedHandler` returns `False` if
`refresh_button_mapping()` found no buttons at all, or if this specific
need's button wasn't among them; `ChooseNeedHandler` returns `False` if it
can't find its exact-color button on screen. Either way the need is
logged and skipped for this pass with no respawn, same as a disabled need
- `process_needs()` doesn't distinguish between the two.

### GUI-only actions

None of `lure_collect()`, `set_new_lure()`, `tree_collect()` or
`setup_game()` are need handlers - they're plain functions, not
registered anywhere in `NEED_HANDLER_CLASSES` or `ENABLED_NEEDS`. Each
has its own button in the GUI's **Functions** section (same pattern as
**Respawn**) for testing on its own, and three of the four are also
triggered automatically - see
[Persisted game config](#persisted-game-config) for `side_quest()` (which
calls `lure_collect()`/`tree_collect()`) and `ensure_setup()` (which calls
`setup_game()`).

- **`lure_collect()`** - holds `a` for `LURE_COLLECT_WALK_DURATION` (2s),
  presses `KEY_INTERACT` (`e`) to collect the current lure's rewards,
  waits `LURE_COLLECT_SETTLE_DELAY` (1s), presses `KEY_INTERACT` again,
  then calls `set_new_lure()`.
- **`set_new_lure()`** - clicks `LURE_NEW_POS_1` then `LURE_NEW_POS_2`,
  the two backpack clicks that place a fresh lure. Split out from
  `lure_collect()` specifically so this placement step can be tested and
  tuned on its own, without walking to the old lure first each time.
- **`tree_collect()`** - holds `d` for `TREE_COLLECT_WALK_DURATION` (2s),
  then `s` for `TREE_COLLECT_BACKWARD_DURATION` (1s) to line up with the
  tree, then presses `KEY_INTERACT`.
- **`setup_game()`** - respawns, clicks `SETUP_LOCK_HOUSE_POS` to lock the
  house, then opens the backpack and clicks through
  `SETUP_BACKPACK_SETTINGS_POS` → `SETUP_SORT_MENU_POS` →
  `SETUP_FAVORITES_POS` → `SETUP_CONFIRM_POS` to set its item filter to
  favorites only, then closes the backpack. Disabling trades isn't
  implemented yet.

### Checking a need in parallel with movement

`walk_alternating()` (used by `ride` and `walk`) takes an optional
`need_name`. When given, it starts a background thread
(`_watch_need_gone()`) that polls whether that need has cleared *while*
the character is still alternating back and forth, instead of only
checking once the full duration has elapsed - the main loop breaks out
early (via a shared `threading.Event`) the moment the background thread
confirms it, rather than always running the full `total_duration`. The
background thread uses the same miss-confirmation as
`wait_until_need_gone()` (`NEED_GONE_CONFIRMATIONS`, debounced by the
shared `_need_cleared()` helper - see
[Waiting for a need to clear](#waiting-for-a-need-to-clear)), just
interleaved with the movement instead of run after it.

This is the one place in the macro with real thread concurrency (every
other background activity is the GUI's single `run_async()` worker
thread). A second `threading.Event` tells the watcher to stop the instant
the main loop exits for *any* reason - the duration elapsed, the need was
confirmed cleared, or an exception (including `StopRequested`/
`FocusLost`) is propagating out - via a `try`/`finally` around the
movement loop, so the watcher thread is never left running past the
function call that started it. The watcher itself must never let
`StopRequested`/`FocusLost` escape uncaught: only the main thread's
`check_running()` calls are allowed to unwind the workflow, so the
watcher instead catches both and just returns quietly, relying on the
main thread (which checks far more often, every `STOP_CHECK_INTERVAL`
inside `hold_key()`) to notice a stop or focus loss first and signal the
watcher to stop via the shared event. No input simulation
(`pydirectinput`) ever happens on the watcher thread - it only takes
screenshots and runs detection, each call independent and self-contained
(a fresh `mss.MSS()` per screenshot, no shared mutable state with the
main thread besides the two events), which is what makes running it
alongside the movement safe.

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

## Persisted game config

`GAME_CONFIG_PATH` (`debug/.config`, a JSON file) is the macro's one piece
of state that survives separate launches of the script - everything else
either lives in `needs/` (the learned reference icons) or is cheap to
redetect fresh, like `BUTTON_POSITIONS`: detected every single time the
character walks to the buttons, so there's nothing gained by saving it -
it's never trusted across a run anyway, and a stale save would just be
actively wrong if the screen resolution or Roblox's UI ever changed
between sessions.

`load_game_config()` returns these defaults merged with whatever's
actually on disk (so an old config missing a newer key still works), and
`save_game_config()` overwrites the whole file:

- **`money_collected`** - how much `tree_collect()` has yielded so far,
  in `TREE_HARVEST_YIELD` (16) increments. Only ever goes up - there's no
  code path that resets it, by design (see `side_quest()` below); reset
  it by hand by deleting `debug/.config`.
- **`lure_timer`** - the timestamp `lure_collect()` is next due. Defaults
  to "due immediately" (`time.time()`) the first time the config is
  created.
- **`setup_done`** - whether `setup_game()` has ever run (see
  `ensure_setup()` below).

Two small functions read and update this file every cycle or at startup:

- **`side_quest()`** - called right after `unscrew()`, every cycle of
  `run_full_cycle()`'s loop (so on the same cadence as the paycheck
  check). While `money_collected` is below `MONEY_COLLECTED_TARGET`
  (200), it calls `tree_collect()` and adds `TREE_HARVEST_YIELD`. Once
  `lure_timer` has passed, it calls `lure_collect()` and pushes
  `lure_timer` another `LURE_RECOLLECT_INTERVAL` (4 hours) into the
  future. Either action only updates its piece of the config if the
  underlying call actually returned `True`.
- **`ensure_setup()`** - called once at the top of `run_workflow()` and
  `run_workflow_loop()` (before anything else happens). Runs
  `setup_game()` and sets `setup_done` the first time ever; a no-op on
  every call after that.

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

Every tunable number, timing, screen position and color range lives in
`magic_numbers.py`, not `main.py` - it's the one file to edit for a moved
button, a slower computer, or a different monitor. `main.py` does `from
magic_numbers import *` and refers to everything there by bare name.
`ENABLED_NEEDS` is the first thing defined in it, since it's the setting
most likely to need editing. Everything else falls into one of these
groups (see the comments next to each constant in `magic_numbers.py` for
exact values and rationale):

| Group | Examples |
|---|---|
| Behavior flags | `ENABLED_NEEDS`, `FOCUS_WINDOW_ON_ACTION` |
| Timing | `RESPAWN_WAIT`, `WALK_TO_BUTTONS_DURATION`, `NEED_CHECK_RETRY_DELAY`, `LOOP_DELAY`, `STOP_CHECK_INTERVAL`, `NEED_GONE_*` |
| Screen positions | `CATCH_*_POS`, `EMPTY_POS`, `FOCUS_PET_POS`, `RIDE_*_POS`, `LURE_NEW_POS_*`, `SETUP_*_POS` |
| Side quest / setup | `TREE_HARVEST_YIELD`, `MONEY_COLLECTED_TARGET`, `LURE_RECOLLECT_INTERVAL`, `LURE_COLLECT_*`, `TREE_COLLECT_*` |
| Need-icon detection | `NEED_ICON_TOP_PERCENT`, `NEED_ICON_WIDTH_PERCENT`, `NEED_ICON_BLANK_*`, `NEED_ICON_MIN/MAX_RADIUS`, `NEED_ICON_HOUGH_*` |
| Need-icon matching | `ICON_MATCH_THRESHOLD`, `ICON_BW_THRESHOLD`, `ICON_CLAHE_*`, `ICON_COMPARE_SIZE`, `ICON_CROP_RADIUS`, `ICON_SHIFT_TOLERANCE`, `MATCH_ONLY_LEFT_HALF` |
| Button detection | `BUTTON_BAND_X/Y`, `BUTTON_MIN_AREA`, `BUTTON_MIN_CIRCULARITY`, `BUTTON_MAX_COUNT`, `BUTTON_NAMES`, `BUTTON_LOOSE_*_FACTOR`, `BUTTON_PURPLE_*_ITERATIONS`, `BUTTON_OUTLINE_PADDING` |
| Debug drawing | `DEBUG_NEED_MARKER_*`, `DEBUG_BUTTON_MARKER_*`, `DEBUG_BUTTON_LABEL_*` |
| Color ranges (HSV) | `PURPLE_RANGE`, `WHITE_RANGE` (button detection) |
| GUI | colors/fonts (`GUI_BG`, `GUI_FONT`, ...) and layout spacing (`GUI_OUTER_PADDING`, `GUI_ROW_SPACING`, ...) for the `tkinter` panel |

If detection isn't finding what you expect after changing screen
resolution or Roblox's UI, check `debug/debug_needs.png` and
`debug/debug_buttons.png` first - both are overwritten every detection
pass and show exactly what the macro is looking at - before tuning any of
these.
