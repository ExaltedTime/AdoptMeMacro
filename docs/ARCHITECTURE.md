# Architecture

Detailed technical walkthrough of `main.py`. This is for understanding or
modifying the code - if you just want to run the macro, see the main
[README](../README.md) instead.

Almost everything lives in `main.py` (plus `helper.py`, the run-report chart - see [Debugging an unattended run](#debugging-an-unattended-run)), organized top to bottom as a sequence
of `# === SECTION ===` blocks; this doc follows that same order. The one
exception is `magic_numbers.py`, which holds every tunable constant (see
[Configuration reference](#configuration-reference)) - `main.py` pulls
them all in with `from magic_numbers import *` and refers to them by bare
name everywhere else.

## The workflow lifecycle

The only thing that runs is **`run_workflow_loop()`** (the GUI's loop
button): it respawns once up front (so the loop always starts from a known,
on-the-ground state), then repeats `run_full_cycle()` forever, pausing
`LOOP_DELAY` seconds between cycles, until stopped.

**`run_full_cycle()`** is a tight retry loop:

```
while True:
    unscrew(); side_quest()
    if process_needs():   # resolved ONE need
        return
    wait NEED_CHECK_RETRY_DELAY seconds, then check again
```

The key word is *resolved*. `process_needs()` returns `True` only if it
actually did something - clicking a button or running a special handler -
and it resolves **one** need per call. The loop then starts a new cycle, so
the popup checks, the side quest and a fresh detection all run again before
the next need: what to do next is always decided from what's on screen
*now*, not from a list taken before the previous need (which can take a
minute or two) was handled. Finding icons that don't lead to any action
(see below) returns `False`, so the cycle keeps waiting and re-checking
rather than treating a no-op as progress. While nothing is on screen the
console shows one `Waiting for a need... 1m 23s` line that is overwritten
every second instead of a line per check.

### `process_needs()`, step by step

1. Focus Roblox and take a screenshot (`focus_roblox_click()`).
2. Detect every need icon currently on screen (`detect_need_icons()`).
   Nothing found → return `False` immediately (silently).
3. For each detected icon, match it against the saved reference icons in
   `needs/` (`find_matching_need()`). An icon that doesn't match anything
   well enough triggers `prompt_rename_need()` (asks you, in a dialog, to
   name and save it - only when `SAVE_NEW_NEEDS` is on) and is otherwise skipped this pass - it isn't
   "resolved," it's just been taught for next time.
4. Every icon that *did* match is collected into an ordered list of need
   names, in the order they were detected, and printed and logged
   (`detected: ...`; icons on screen but none recognised are logged once,
   not on every check). The list is also handed to `record_detection()` -
   on every check, including ones that find nothing - which is what catches
   [stuck needs](#stuck-needs).
5. The list is then worked through in order until one need is resolved. For
   each `need_name`, `get_need_handler(need_name)` looks it up in
   `NEED_HANDLER_CLASSES` and returns an instance, or `None` if that need
   isn't in `ENABLED_NEEDS` - a `None` result is logged and skipped without
   doing anything. Every need is handled the same way from here: calling
   `.handle()` on whatever handler came back (see
   [Need handlers](#need-handlers)).
   - If `handle()` returns `True`, the character respawns (always done by
     `process_needs()`, never by the handler itself) and `process_needs()`
     returns `True` - the remaining needs in the list are dropped; they
     will be detected again, along with anything new, by the next check.
   - If it returns `False` (the need wasn't actually resolved this pass -
     e.g. no buttons detected, or a UI element `ChooseNeedHandler` needed
     wasn't found), the need is logged and skipped with no respawn, and the
     next one in the list is tried.
6. `process_needs()` returns `False` if nothing was resolved (e.g. every
   match turned out to be disabled, or every handler that ran returned
   `False`), which sends `run_full_cycle()` back to waiting and
   re-checking instead of treating a no-op as progress.

Respawning after every individual need keeps `walk_to_buttons()` reliable:
it always starts from the same known respawn point, instead of compounding
an extra walk from wherever the previous need left the character standing.

## Code structure, top to bottom

- **`StopRequested` / `FocusLost` / `check_stop()` / `check_focus()` /
  `check_running()`** - see [Stopping & focus safety](#stopping--focus-safety).
- **WINDOW FOCUS & SCREEN CAPTURE** - `focus_roblox()` / `focus_roblox_click()`
  bring Roblox to the front; `is_roblox_focused()` checks whether it still
  is; `roblox_windows()` lists the open Roblox windows; `grab_screen()`
  takes a screenshot via `mss`; `exact_color_mask()` / `find_exact_color()`
  find things by their exact pixel color rather than their shape (the
  choose need's button, the popups and the Play button all work this way).
- **STATE** - this module's own mutable state: `STOP_FLAG`;
  `BUTTON_POSITIONS`, the in-memory cache of the last-detected action
  button positions (never persisted to disk - see
  [Persisted game config](#persisted-game-config)); and
  `ATTEMPT_STREAKS` / `ATTEMPTED_LAST` / `DISABLED_THIS_RUN`, which back
  [Stuck needs](#stuck-needs). Every fixed
  config/tuning value lives in `magic_numbers.py` instead - see
  [Configuration reference](#configuration-reference).
- **LOGGING** - `next_run_number()` / `log_run_event()`, plus the
  unattended-run tools `log_output()`, `save_failure_screenshot()`,
  `log_failure()` and `write_status()` - see
  [Debugging an unattended run](#debugging-an-unattended-run).
- **GAME CONFIG** - `load_game_config()`/`save_game_config()`: the state
  that *does* persist across separate launches of the script (unlike
  `BUTTON_POSITIONS` above) - see [Persisted game config](#persisted-game-config).
- **ICON PROCESSING** - `preprocess_icon()` / `icon_signature()` / `compare_signatures()`. See
  [Need-icon matching](#need-icon-matching).
- **NEED ICON DETECTION** - `detect_need_icons()`, `find_matching_need()`,
  `prompt_rename_need()`. See [Need-icon detection](#need-icon-detection).
- **CLICKING** - `hover_click()`, `hover_move()`, `wait_interruptible()`,
  `wait_stoppable()`, `release_all_inputs()`. See
  [Click & input primitives](#click--input-primitives).
- **BUTTON DETECTION** - `detect_buttons()`, `refresh_button_mapping()`,
  `click_need_button()`. See [Button detection](#button-detection).
- **MOVEMENT** - `respawn_character()`, `walk_to_buttons()`,
  `walk_alternating()` (shared by the walk need, a/d, and the ride need,
  w/s - same pattern, different keys and duration passed in).
- **SIDE ACTIONS** - `lure_collect()`, `set_new_lure()`, `tree_collect()`,
  `setup_game()`, `disable_trades()`, `leave_and_rejoin()`: plain callable
  actions, not tied to any detected need/icon. `lure_collect()`/
  `tree_collect()` are run automatically by `side_quest()`; `setup_game()`
  and `leave_and_rejoin()` run from the GUI and from the automatic recovery
  - see the end of [Need handlers](#need-handlers).
- **NEED HANDLERS** - `NeedHandler` and one subclass per need (or per group
  of needs, via `partial()`). See [Need handlers](#need-handlers).
- **PER-CYCLE CHECKS** - `unscrew()` and what it runs: `rejoin_game()`
  (with `rejoin_reason()` / `detect_disconnect()`), `close_backpack_if_stuck()`
  / `close_backpack_if_open()` (with `detect_backpack_expanded()` /
  `detect_backpack_normal()`),
  `dismiss_stray_windows()`, `minigame_popup()`,
  `detect_paycheck()`; the `task_unscrew()` / `task_unscrew_tick()` hook that
  runs them during a task; and `side_quest()`.
- **WORKFLOWS** - `run_full_cycle()` and `run_workflow_loop()`. See
  [The workflow lifecycle](#the-workflow-lifecycle) above.
- **GUI** - the `tkinter` control panel. See [GUI internals](#gui-internals).

## Window & coordinates

Every position, pixel size and screen region in `magic_numbers.py` was
measured with the Roblox window maximized on a 1920x1080 screen
(`REFERENCE_WIDTH` x `REFERENCE_HEIGHT`). The macro works entirely in that
"reference" space and converts at exactly two points, so nothing else needs to
know the real window size:

- **`grab_screen()`** takes a screenshot of the whole monitor
  (`grab_full_screen()`), crops it to the Roblox window (`roblox_rect()`) and
  scales it to the reference size. Every detection - icons, buttons, popups,
  the Play button, the pet - and every position they return is therefore in
  reference space.
- **`to_screen(x, y)`** maps a reference-space position onto the real window.
  `hover_move()` (so every click), the pet's mouse circle and the focus click
  all go through it.

`roblox_rect()` reads the Roblox window's position and size (`roblox_window()`
prefers the window titled exactly "Roblox", so a browser tab that mentions it
isn't picked) and clips it to the monitor, which trims the few pixels of
invisible border a maximized window reports. The whole monitor is used when
there's no usable window (not running, minimized). It's remembered for
`ROBLOX_RECT_TTL` seconds, and `focus_roblox()` forgets it. On a 1920x1080
screen with Roblox maximized the rectangle is the whole screen, so nothing is
cropped or scaled; on another resolution or a smaller window everything
scales. Distances measured in pixels (icon radii, the `MINIGAME_POPUP_NO_MAX_*`
offsets, ...) are reference pixels, so they scale too. Scaling assumes the same
aspect ratio and a window that fills its area - a non-maximized window's title
bar and borders are counted as part of it, so expect small errors there.
`save_failure_screenshot()` is the one thing that captures the whole screen
instead, to show what else was on it.

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

`find_matching_need()` compares the new icon against the signature of every
saved `.png` in
`needs/` (`need_signatures()` computes those once and recomputes them only
when a file in `needs/` is added, changed or removed, so a check doesn't
re-read the folder) - including subfolders, so related icons can be grouped (the
seasonal `diving` and `puddle` icons live in `needs/weather/`); a need's
name is just its file name, wherever it sits - and keeps the best score. A best score below
`ICON_MATCH_THRESHOLD` (0.93) means "not confident this is anything we've
seen" and falls through to `prompt_rename_need()` instead.

## Waiting for a need to clear

`wait_until_need_gone()` - used after a button click (`click_need_button()`)
and after `TeleportWalkNeedHandler`'s movement sequence (`choose` doesn't
wait: it clears at once, and the next check sees that) - polls
`scan_needs()` (one fresh detect-and-match pass) every
`NEED_GONE_POLL_INTERVAL` until the target need is missing
`NEED_GONE_CONFIRMATIONS` times in a row (any sighting resets the count
back to zero), up to `NEED_GONE_MAX_WAIT` total.

Each individual check goes through `_need_cleared()`, which re-samples
once more, `NEED_GONE_FLICKER_RECHECK_DELAY` later, before a miss is
allowed to count: the icon can drop out of a single detection pass for a
frame (or against a momentarily busy background) without the need having
actually cleared, and a bare miss-streak alone isn't enough to tell that
apart from the real thing. This only guards against a one-frame flicker -
a detection failure caused by a *sustained* background change behind the
icon isn't something this re-check can fix, since it'll fail again on the
immediate retry too. The one such case that is handled: the Pizza Party's
background is near-white, which the icons can't be recognised against, so
the pizza need looked "gone" a few seconds in - the handler returned early,
the character respawned with the pizza still unserved, and it had to run
twice. `need_bar_readable()` spots it (more than
`NEED_BAR_MAX_BRIGHT_FRACTION` of the icon strip brighter than
`NEED_BAR_BRIGHT_LEVEL`); while the icons are unreadable `_need_cleared()`
returns `None`, which counts as neither a miss nor a sighting, so the wait
runs its full `NEED_GONE_MAX_WAIT` - and ending it isn't logged as a failure.
Popups, stray windows and minigames that turn up during the wait are handled
by the `unscrew()` checks that `wait_interruptible()` runs during a task - see
[Per-cycle checks while a task runs](#per-cycle-checks-while-a-task-runs).

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
teleport-walk needs `cafe`, `salon`, `sick`, `pizza`, `school`, `beach`,
`camping`, `bored` (called out with their own inline comment in the set
literal, since they leave the map and run considerably longer than
everything else).
The GUI's **Debug** tab has a button per handler that runs it directly
regardless of `ENABLED_NEEDS`.

**Focusing the pet.** (Each click is followed by `FOCUS_PET_CLICK_SETTLE` and a `pet_focused()` check; the clicks stop at the first one that focuses it, and `FOCUS_PET_MENU_WAIT` is only waited if none did.) `pet` and `choose` both start with `focus_pet()`, which first checks whether the pet is already focused (`pet_focused()`: the BACK button's crop is on screen) and if so returns True with no clicks; otherwise it
has no hardcoded position: it takes two screenshots of the bottom of the
screen (below `FOCUS_PET_REGION_TOP_PERCENT`) `FOCUS_PET_FRAME_GAP` apart,
and `moving_blobs()` diffs them - leaving out the macro's own window
(`macro_window_reference_rect()`, plus `FOCUS_PET_IGNORE_MARGIN`), whose console
scrolls and flickers and used to be picked up as a huge "moving" blob. Pixels that changed by more than
`FOCUS_PET_DIFF_THRESHOLD` are closed together (`FOCUS_PET_MERGE_KERNEL`,
since an up/down bob only changes the pet's top and bottom edges), blobs
under `FOCUS_PET_MIN_AREA` are dropped as noise, and `focus_pet()` clicks
the center of every remaining blob, largest first. It returns `False` (and
the handler skips the need) if nothing moved. It prints what it saw (how many
blobs, each one's center, size and area) and writes
`debug/debug_focus_pet.png` - the second frame with the changed pixels in
red and a numbered box round every blob (the ignored area in blue) - plus
`debug/debug_focus_pet_after.png`, the screen after the clicks and the menu
wait, so a click that lands on the wrong thing (another player, a UI
element) can be seen.

Except for `catch` and `pet` - which always run their full fixed sequence
precisely, with no early exit - every other need either waits for its
icon to actually clear (`wait_until_need_gone()`) instead of guessing how
long that takes, or checks for that *while* still moving (see below),
rather than a flat sleep after the fact.

| Need | How it's handled |
|---|---|
| `hungry` / `thirsty` / `dirty` / `potty` / `sleepy` | `ButtonNeedHandler`: walk to the action buttons (`walk_to_buttons()`), refresh the button mapping (`refresh_button_mapping()`), click the matching one (`click_need_button()`), which then calls `wait_until_need_gone()` for that need name. |
| `catch` | `CatchNeedHandler`: open backpack → toys → squeaky toy → equip → close backpack → wait `CATCH_WAIT_AFTER_EQUIP` → hold zoom-in → click empty space every `CATCH_THROW_INTERVAL` (0.5s) until the "catch" icon is confirmed gone (`click_until_need_gone()`, the same watcher thread `walk_alternating()` uses; at most `CATCH_MAX_WAIT`) → unequip. |
| `pet` | `PetNeedHandler`: `focus_pet()` (see below), then hold the mouse down and swipe once down from `PET_SWIPE_START_OFFSET` above screen center to `PET_SWIPE_END_OFFSET` below it over `PET_SWIPE_DURATION` (8s). Same as `catch` - always the full fixed duration. |
| `choose` | `ChooseNeedHandler`: `focus_pet()` (see below), find the exact-color button (`CHOOSE_BUTTON_COLOR`, since it has no distinguishing icon), hover to it slowly (`hover_click`) and click, then click screen-center to dismiss the menu. It doesn't wait for the icon to clear (it's gone at once; the next check confirms). |
| `ride` | `RideNeedHandler`: step back, mount (`e`), walk forward briefly, then `equip_favorite_vehicle()` (backpack → vehicles → first vehicle → equip → close backpack), hold `r` (`KEY_HELICOPTER`) for `RIDE_R_HOLD_DURATION` (1s), then `walk_alternating(("w", "s"), NEED_GONE_MAX_WAIT, need_name="ride")` - up to `NEED_GONE_MAX_WAIT` (60s), ending early the moment "ride" is confirmed cleared (see below). |
| `bored` / `beach` / `school` / `cafe` / `salon` / `pizza` / `camping` / `sick` | `TeleportWalkNeedHandler`, configured per need in `TELEPORT_WALK_NEEDS`: teleport to the nursery or dealership (`teleport_to()`), hold each `(key, seconds)` step with `hold_key()`, then `wait_until_need_gone()` (see [Waiting for a need to clear](#waiting-for-a-need-to-clear)). `process_needs()` respawns afterwards. An entry may also set `final_click`, clicked after the last hold (used by `sick`), and `helicopter` (see below). Which table is used depends on `HALLOWEEN` - see below. |
| `walk` | `WalkNeedHandler`: `walk_alternating(("a", "d"), NEED_GONE_MAX_WAIT, need_name="walk")` - up to `NEED_GONE_MAX_WAIT` (60s), same early-exit as `ride` above. |

**Helicopter.** A teleport-walk entry can fly instead of walk. `HELICOPTER_REQUIRED`
(`magic_numbers.py`, default `False`) is the default for entries that don't
say; an entry overrides it with `helicopter=True`/`False`. When on, after
`teleport_to()` the handler holds `w` for `HELICOPTER_FORWARD_DURATION` (1s),
calls `equip_favorite_vehicle()` (the same backpack sequence `ride` uses; the
first vehicle is the favorite once `setup_game()` has filtered the backpack),
holds `r` for `HELICOPTER_HOLD_DURATION` (4s), runs the entry's steps as
usual, then presses space (`KEY_JUMP`) on arrival, before any `final_click`.
Only the Halloween `bored`, `beach` and `camping` entries use it.

**Halloween.** `HALLOWEEN` (`magic_numbers.py`, next to `ENABLED_NEEDS`) is
a plain boolean. The teleport-walk steps live in two tables,
`TELEPORT_WALK_NEEDS_NORMAL` and `TELEPORT_WALK_NEEDS_HALLOWEEN`;
`TELEPORT_WALK_NEEDS` - the one `TeleportWalkNeedHandler` and the handler
registry actually read - is the normal table with the Halloween entries laid
over it while the flag is on. The Halloween table covers `bored`, `beach`,
`school`, `camping` (Halloween moves the nursery, so each has its own steps
there; `bored`, `beach` and `camping` also fly by helicopter) and `sick`, which *only* exists there - with `HALLOWEEN` off it
has no steps and isn't a need at all. While the flag is on, `unscrew()` also
calls `minigame_popup()`, which plays or dismisses the minigame popups
(see below).

Every cycle, `unscrew()` runs `rejoin_game()` first (nothing else works
while disconnected), then `close_backpack_if_stuck()` (the backpack, either
form), then
`dismiss_stray_windows()`, then `minigame_popup()` if `HALLOWEEN` is on, then
the paycheck check. The stray windows and the minigame popup go before the
paycheck check because their green buttons (Okay, Yes) are the same green as
the paycheck's CASH OUT button, so `detect_paycheck()` would mistake them for
one. For the same reason
`detect_paycheck()` matches the crop of the popup's bank-card header
(`ref/popups/paycheck.png`, within `PAYCHECK_BOX`) instead of counting green:
it used to count green pixels, and the green tiles of an open backpack passed
that, so it "dismissed" a paycheck every cycle - and its second dismiss click,
`PAYCHECK_DISMISS_POS_2` (948, 627), lands on the little orange box next to the
backpack's settings button, which expands the backpack.
`rejoin_game()` is the automatic recovery - see
[Automatic recovery](#stuck-needs).

**Stray windows.** Two windows have been seen open on their own, covering
the screen: the Trading Hub (how it got opened isn't known; a misclicking
`choose` is the suspect - see `debug/debug_focus_pet.png`; it throws up a "Go
to the Trading Hub to edit listings!" popup on top) and the Star Rewards, which
opens once a day, at no known moment. The Star Rewards hides the need icons;
the Trading Hub doesn't, but needs can't be resolved with it in the way. Run
308's two 20-minute stalls (a `paycheck popup dismissed` line every cycle) were
these windows - the macro's paycheck clicks don't close them, and their green
buttons look like CASH OUT.
`dismiss_stray_windows()` recognises each by a crop of its title
in `ref/popups/` (`trading_hub.png`, `star_rewards.png`; matched within the
`box` in `STRAY_WINDOWS`, at least `STRAY_WINDOW_MATCH_THRESHOLD`) and clicks
its `close_pos`; for the Trading Hub it first clicks the popup's green Okay
(`STRAY_OKAY_*`) if that's showing. A new window is a new crop plus an entry in
`STRAY_WINDOWS`. `setup_game()` also closes any before it starts, since an open
one would take its clicks.

Two more entries are the **character menu** (`character_menu.png`: the five
buttons Profile / Emotions / Dances / Actions / Activities; a click in the
middle of the screen dismisses it) and the **pet focus view** (`pet_focus.png`:
the camera on the pet with a BACK button; `close_pos` is that button). Only
`choose` and `pet` want the pet focused, so `pet_focus` is flagged
`only_when_unwanted`: `dismiss_stray_windows()` skips it while
`wanting_pet_focus()` is active (those two handlers), and clicks BACK in every
other case - including mid-task, e.g. a throw click that focused the pet.

A third entry, `respawn_confirm`, is the "Are you sure you want to respawn your
character?" dialog (`ref/popups/respawn_confirm.png`; its `close_pos` is the
Respawn button). `respawn_character()` presses esc, r and enter within a
fraction of a second, and if the Enter lands before the dialog exists the
dialog stays open and blocks everything after it (a screenshot of exactly that
was seen after a minigame). So `respawn_character()` looks for it
`RESPAWN_CONFIRM_LOOK_DELAY` after the keys and clicks Respawn if it's there;
the in-task checks would also catch it. Each time one is closed it is a
`stray window closed: respawn_confirm` line in the run log, so how often it
happens can be counted.

**Per-cycle checks while a task runs.** Waiting for the top of the next cycle
would leave a popup, a stray window or an offered minigame in the way for the
whole of a handler (a minute or two), and minigames are time-sensitive. So
`process_needs()` runs each handler - and `setup_game()` its steps - inside
`task_unscrew()`, and during it every `wait_interruptible()` also runs
`unscrew(in_task=True)` every `UNSCREW_TASK_INTERVAL` (only on the thread that
started the task, and never from inside `unscrew()` itself). It's the same
`unscrew()`, with two differences: the rejoin check only acts on a disconnect
(`rejoin_game(kinds=("disconnect",))`; the scheduled and stalled rejoins wait
for the top of a cycle), and the backpack is only closed once it has been seen
open on `BACKPACK_CLOSE_CONFIRMATIONS` (2) checks in a row - a handler opens the
normal backpack for a few seconds on purpose, so one sighting could be a
handler in the middle of its steps, but two `UNSCREW_TASK_INTERVAL` apart is a
backpack open for longer than any handler needs. The paycheck check is skipped
while the backpack is still open. If a minigame is played, or a disconnect made
it rejoin, `TaskInterrupted` is raised: the handler is abandoned (both respawn
the character), `process_needs()` logs `<need> interrupted by a minigame` /
`a rejoin`, doesn't count it as resolved or as a failed attempt, and the next
check decides from the screen what's still needed. During setup a minigame is
only declined (No, without "do not show again"), since setup's clicks toggle
things and can't be abandoned halfway.

**Backpack tabs.** The backpack remembers its last category tab, and clicking a tab that is already selected (orange background) expands the backpack. `select_backpack_tab()` therefore only clicks the vehicles / toys tab when `backpack_tab_selected()` doesn't see the orange tile.

**The BACK button.** The expanded backpack has a BACK button at the top
(`ref/popups/backpack_back.png`, found within `BACKPACK_BACK_BOX`) that closes
it entirely, where the backpack key only shrinks it to the normal form first.
`close_backpack_if_open()` clicks it when it sees the expanded form, and
falls back to the key presses if it isn't found. The pet focus view has a BACK
button too, but a different one: its `pet_focus` crop is searched for only in
the top-left box, away from the expanded backpack's.

**Closing a backpack left open.** The backpack has two forms and both are
detected. The *expanded* backpack (`detect_backpack_expanded()`) is recognised by
its purple header bar (`BACKPACK_HEADER_COLOR`) filling at least
`BACKPACK_HEADER_MIN_FRACTION` of `BACKPACK_HEADER_BOX`; it should never be
open during a task. The *normal* backpack (`detect_backpack_normal()`) - the
small panel at the bottom of the screen that handlers open for a few seconds -
is recognised by its purple frame (`BACKPACK_NORMAL_COLOR`, at least
`BACKPACK_NORMAL_MIN_PIXELS` within `BACKPACK_NORMAL_PANEL_BOX`) together with
the white of its item grid (`BACKPACK_NORMAL_WHITE_*`); the frame alone or the
white alone isn't enough, since the white is also any bright scenery. The normal
backpack's detection is calibrated on a single screenshot (`ref/test` is for
more). `detect_backpack_open()` is either. `close_backpack_if_open()` presses
`KEY_BACKPACK` - but only while Roblox has the focus, so the key can't go to
another window - looks again, and presses once more if it's still open (the
expanded backpack takes two presses: the first shrinks it to the normal one, the
second closes that). If it's still open after the second press the key isn't
doing what's expected, so a failure is logged and it stops rather than pressing
again, since another press could just reopen it. `close_backpack_if_stuck()`
decides *when*: at once at the top of a cycle, and mid-task only on the second
sighting in a row (see above).

**Minigame popups.** Halloween has two minigames that offer to teleport you -
"Ghost Gallery is starting soon! Teleport there now?" and "Hauntlet 2 is
starting soon!..." - in the same popup layout.
`detect_minigame_popup()` recognises that layout by its two buttons: neither
color is unique alone (Yes green = `PAYCHECK_CASHOUT_COLOR`, No red = the
Exit Home button), so it needs the Yes green (`MINIGAME_POPUP_YES_COLOR`, at
least `MINIGAME_POPUP_MIN_BUTTON_PIXELS` px) with the No red
(`MINIGAME_POPUP_NO_COLOR`) within `MINIGAME_POPUP_NO_MAX_DX` / `_DY` px to
its left. `identify_minigame()` then says which one it is: it matches the
first line of the title (`MINIGAME_TITLE_BOX`) against the crops in
`ref/halloween/` (`cv2.matchTemplate`, best score at least
`MINIGAME_TITLE_MATCH_THRESHOLD`); a minigame's name is its file name,
lower-cased (`Ghost_gallery.png` → `ghost_gallery`), so a new one is just a
new crop - plus an entry in `MINIGAME_LABELS` and a player in
`MINIGAME_PLAYERS`.

Each minigame has a switch in the game config (`hauntlet_enabled`,
`ghost_gallery_enabled`, both **off** by default; "Play ..." checkboxes on the
Options tab). `minigame_popup()`:
- **switched off, or not recognised** - clicks `MINIGAME_POPUP_DONT_SHOW_POS`
  ("Do not show again this session") and `MINIGAME_POPUP_NO_POS`;
- **switched on** - `play_minigame()` clicks Yes (`MINIGAME_POPUP_YES_POS`)
  and plays it, as below. Minigames are time-sensitive, so this also happens
  when the popup turns up while a handler is waiting in
  a handler's waits (see [Per-cycle checks while a task
  runs](#per-cycle-checks-while-a-task-runs)): the minigame is played right
  then and the handler abandoned (`TaskInterrupted`). Every held key and the mouse button are released first - at the start of `play_minigame()`, since the handler may have been holding one (a walk, the pet swipe) when it was interrupted, and again as the interruption is raised. Where it left the
  character is unknown afterwards, and the next check sees what is still
  needed.

*Playing.* No needs can be seen while a minigame runs, so both end on the
victory screen instead - a red GAME OVER! banner over a green NICE! button,
found by exact color (`detect_minigame_victory()`, checked every
`MINIGAME_VICTORY_CHECK_INTERVAL`), which is the same for both. While it plays, `minigame_popup_checks()` runs `dismiss_stray_windows()` and `detect_paycheck()` every `MINIGAME_POPUP_CHECK_INTERVAL` (4s), including during the start waits (`minigame_wait()`) - the in-task `unscrew()` can't, since the minigame is played from it. Then NICE!
(`MINIGAME_VICTORY_BUTTON_POS`) is clicked, `MINIGAME_FINISH_WAIT` is waited
out, and the character respawns. If the victory screen hasn't shown after
`MINIGAME_MAX_DURATION` it gives up with a logged failure (and releases every
input). A minigame counts as progress for the no-progress rejoin.
- `play_hauntlet()` waits `HAUNTLET_START_WAIT` (50 s), then holds
  `HAUNTLET_FORWARD_KEY` until victory.
- `play_ghost_gallery()` waits `GHOST_GALLERY_START_WAIT`, then holds the mouse
  in `GHOST_GALLERY_HOLD`-second holds (`GHOST_GALLERY_HOLD_GAP` apart) while
  running in random `MOVE_KEYS` directions with a jump, each for a random time
  between `GHOST_GALLERY_STEP_MIN` and `_MAX`, until victory.

A handler only counts as resolved (and only then triggers a respawn) if
`handle()` returns `True`. `ButtonNeedHandler` returns `False` if
`refresh_button_mapping()` found no buttons at all, or if this specific
need's button wasn't among them; `ChooseNeedHandler` returns `False` if it
can't find its exact-color button on screen. Either way the need is
logged and skipped for this pass with no respawn, same as a disabled need
- `process_needs()` doesn't distinguish between the two.

### Stuck needs

Needs are resolved one per check, so a need can sit through several checks
just waiting its turn - that isn't being stuck. What is: `record_detection()`
compares each check with the needs the *previous pass attempted*
(`ATTEMPTED_LAST`). An attempted need still on screen has its streak in
`ATTEMPT_STREAKS` raised; one that's gone has it dropped. When an *enabled*
need's streak reaches `NEED_STUCK_CHECKS` (5) - it's supposed to be getting
resolved, yet it never goes away - it's added to `DISABLED_THIS_RUN` and
`get_need_handler()` returns `None` for it from then on, same as a need that
isn't in `ENABLED_NEEDS`. A single check without it, even one that detects
nothing at all, breaks the streak.

`DISABLED_THIS_RUN` lasts for the life of the process only - it isn't saved
to the game config, so relaunching the script gives every need a fresh
chance - while the streaks are also reset (`reset_need_tracking()`) at the
start of every workflow run, so a streak never spans a stop and restart.

**Automatic recovery.** Every cycle `unscrew()` calls `rejoin_game()`, which
asks `rejoin_reason()` whether the game needs rejoining. In order:

1. *Disconnect* - Roblox's crash window (`ROBLOX_CRASH_WINDOW_TITLE`) is open;
   no Roblox window exists; or the Disconnected dialog is showing
   (`detect_disconnect()`: at least `DISCONNECT_PANEL_MIN_FRACTION` of
   `DISCONNECT_PANEL_BOX` is the dialog's grey, `DISCONNECT_PANEL_COLOR` - the
   game behind it is blurred, so nothing else fills a box like that).
2. *Stuck* - `DISABLED_NEEDS_REJOIN_THRESHOLD` (4) needs have been disabled as
   stuck.
3. *Stalled* - no need has been resolved for `NO_PROGRESS_REJOIN_INTERVAL`
   (20 minutes; `LAST_PROGRESS`, set when the loop starts, whenever a need is
   resolved and by every successful `leave_and_rejoin()`): the game is up but
   nothing is working, whatever the cause.
4. *Scheduled* - it has been `REJOIN_INTERVAL` (an hour) since the last
   rejoin (`LAST_REJOIN`, set when the loop starts and by every successful
   `leave_and_rejoin()`): a refresh of a client that has been up a long time.

If so, it runs `leave_and_rejoin()` (see [Side actions](#side-actions)) -
without the clean esc/l/enter leave for a disconnect, since there's no game
to leave - then `setup_game()` (the rejoin resets the settings it sets), then
clears `DISABLED_THIS_RUN` and the streaks so every need gets a fresh
chance, and the workflow carries on. A disconnect, stuck or stalled recovery counts as
a failure: `log_failure()` saves a screenshot first, which shows the dialog
and its error code. A scheduled rejoin isn't one: it's a line in the run log,
counted in `scheduled_rejoins`. If the rejoin fails the cause is still
there, so the next cycle tries again. The check only runs once per cycle, so
a disconnect in the middle of a long handler is noticed when that handler
finishes.

### Side actions

None of `lure_collect()`, `set_new_lure()`, `tree_collect()` or
`setup_game()` are need handlers - they're plain functions, not
registered anywhere in `NEED_HANDLER_CLASSES` or `ENABLED_NEEDS`.
`side_quest()` runs `lure_collect()`/`tree_collect()` automatically - see
[Persisted game config](#persisted-game-config). `setup_game()` is *not*
part of any cycle or loop: it only runs when you press the GUI's **Setup**
button and from the automatic recovery, and nothing tracks whether it has
run. The
lure and tree actions are only reachable through `side_quest()`.

- **`leave_and_rejoin()`** - the **Leave & rejoin** button, and what
  `rejoin_game()` calls to recover. Modelled on how Natro Macro (a Bee
  Swarm Simulator macro) reconnects: close the game properly, relaunch it
  through a deeplink, and wait in stages by looking at the screen rather
  than for a fixed time. Up to `REJOIN_MAX_ATTEMPTS` (3) times:
  1. `close_roblox()` - if `clean_leave` (it's off when the game is gone or
     showing the Disconnected dialog, where Enter could press Reconnect) and
     the Roblox window is at least `REJOIN_MIN_LEAVE_HEIGHT` tall (the L
     shortcut needs it) press
     `REJOIN_LEAVE_KEYS` (esc, l, enter), then `taskkill` every process in
     `ROBLOX_PROCESS_NAMES` and wait `REJOIN_CLOSE_WAIT` (relaunching sooner
     gives Roblox error 264).
  2. `run_deeplink()` - `roblox://navigation/share_links?code=...&type=Server`
     for the private server link saved in the GUI (`parse_server_link()` also
     understands the older `privateServerLinkCode=` form), or
     `roblox://placeID=ROBLOX_PLACE_ID` for a public server if no link is
     saved. A saved link that doesn't parse aborts instead of silently
     joining a public server.
  3. Wait up to `REJOIN_WINDOW_TIMEOUT` for a Roblox window to exist, then up
     to `REJOIN_LOAD_TIMEOUT` for the game's green Play button
     (`REJOIN_PLAY_COLOR`) to show up around `REJOIN_JOIN_POS`
     (`play_button_visible()`).
  4. Wait `REJOIN_PLAY_SETTLE`, click it, wait `REJOIN_AFTER_JOIN_WAIT` (15s),
     respawn and return `True`. A stage that times out starts the next attempt;
     after the last one it returns `False`.

  All of this waits with `wait_stoppable()`, which only [STOP] interrupts.
  `wait_interruptible()` also stops the run when Roblox loses focus, which is
  what used to kill the rejoin as soon as the game closed - there is no
  focused Roblox window while it restarts.
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
  favorites only, then closes the backpack and calls `disable_trades()`. It
first closes any stray window (up to `SETUP_STRAY_TRIES`; see [Stray
windows](#per-cycle-checks)) and runs inside `task_unscrew()`.
- **`disable_trades()`** - sends the macro window to the back
  (`send_macro_window_to_back()`; the always-on-top panel covers the settings
  gear), clicks the six `SETUP_TRADES_*` positions (settings → settings menu →
  interaction tab → trading setting → "no one" → close) and brings the
  window back in a `finally`, so a stop mid-way can't leave it buried. The
  worker thread can't touch Tk directly, so `MACRO_WINDOW` (the GUI) runs
  the two window changes on Tk's thread via `run_on_ui_thread()`.

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
deliberate - the moment you click **[LOOP]** in the Python
GUI, *that* window has focus, not Roblox. `process_needs()` is what brings
Roblox to the front a moment later; requiring focus before that first
attempt would make the buttons non-functional. Every checkpoint after that
first one does require focus.

Both exceptions unwind via ordinary Python exception propagation, all the
way up to `run_async()`'s worker thread, which reports `[STOPPED]` or
`[STOPPED: Roblox not focused]` respectively - unless **Resume after focus
loss** is ticked, in which case `run_workflow_loop()` catches `FocusLost`
itself (below). Anywhere a key or the mouse
button is held down (walking, petting) wraps the hold in `try/finally` so
it's always released on the way up, regardless of which exception caused
the unwind. `release_all_inputs()` runs once more in `run_async`'s
`finally` block as a last line of defense.

**Resuming after focus loss.** The GUI's **Resume after focus loss** checkbox
(on the Options tab; the `resume_on_focus_loss` config switch, off by default - stopping is the
right thing when you're using the computer yourself) makes the loop carry on
instead of ending. When `FocusLost` reaches `run_workflow_loop()` with the
switch on, it releases every held input, saves a failure screenshot (which
shows what took the focus), waits `FOCUS_RESUME_DELAY`, takes Roblox back with
`focus_roblox_click()`, respawns (a half-finished handler leaves the character
somewhere unknown) and starts the next cycle. After `FOCUS_RESUME_MAX_IN_A_ROW`
(5) focus losses with no need resolved in between it gives up and stops as
usual, so it can't fight you for the window forever. The switch is read at the
moment of each loss, so it can be flipped while the loop runs. Resumes are
counted in `focus_resumes` in `status.json`.

**The stop hotkey.** Pressing `STOP_HOTKEY` (`p`) in *any* window stops a running
macro, so you can take over the computer without reaching for the GUI (which
also has a hint label saying so). `AdoptMeGUI.watch_stop_hotkey()` runs a small
daemon thread that polls the key every `STOP_HOTKEY_POLL_INTERVAL` through
`hotkey_down()` (Windows' `GetAsyncKeyState`, so it needs no extra package and
works while another window has the focus). Only the key going *down* counts, so
holding it doesn't retrigger, and it does nothing while nothing is running. It
hands the stop to Tk's thread, which does exactly what the [STOP] button does
(sets `STOP_FLAG`, so the run unwinds at the next `check_running()`, within
`STOP_CHECK_INTERVAL`, releasing any held key on the way) and logs "stopped with
the P key". It stops the run for good - **Resume after focus loss** only catches
`FocusLost`, not a stop. Because it's global, typing a `p` anywhere (including in
the private server link box) while the macro is running stops it too.

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
  code path that lowers it, by design (see `side_quest()` below); the
  GUI's **Reset Config** button puts it back to 0.
- **`tree_timer`** / **`lure_timer`** - the timestamps `tree_collect()` /
  `lure_collect()` are next due. Both default to "due immediately"
  (`time.time()`) when the config is fresh.
- **`side_quest_enabled`** - on/off switch (default on) for
  `side_quest()`. It's a checkbox in the GUI, and can be flipped while a
  workflow runs.
- **`resume_on_focus_loss`** - the **Resume after focus loss** checkbox (default
  off); see [Stopping & focus safety](#stopping--focus-safety).
- **`record_next_run`** - the **Record the next run (low quality video)**
  checkbox (default off); see [Recording a run](#recording-a-run). Cleared, and
  the checkbox unticked, when the run that it was for starts.
- **`private_server_link`** - the private server link `leave_and_rejoin()`
  joins, typed into the GUI's entry box (saved when you press Enter or click
  away). It lives here rather than in `magic_numbers.py` because it's a key
  to your server and the source is in a git repo. **Reset Config** keeps it.

During a run, this file is read and updated by:

- **`side_quest()`** - called right after `unscrew()`, every cycle of
  `run_full_cycle()`'s loop (so on the same cadence as the paycheck
  check); does nothing if `side_quest_enabled` is off. While
  `money_collected` is below `MONEY_COLLECTED_TARGET` (200) *and*
  `tree_timer` has passed, it calls `tree_collect()`, adds
  `TREE_HARVEST_YIELD`, and pushes `tree_timer` `TREE_CHECK_INTERVAL`
  (10 minutes) out. Once `lure_timer` has passed, it calls
  `lure_collect()` and pushes `lure_timer` `LURE_RECOLLECT_INTERVAL` (4
  hours) out. Each action only updates the config if the call actually
  returned `True`, and does so immediately - so stopping partway through
  doesn't lose progress already made.

The GUI (Tk's main thread) and the workflow (`run_async`'s worker thread)
both write this file, so every change goes through
`update_game_config(mutate)` - a load-modify-save under a lock - or
`reset_game_config()` (deletes the file, so the next load is all defaults;
the GUI then refreshes its checkboxes to match). `side_quest()`
deliberately doesn't hold a loaded config across a long
action and save it afterwards - that could silently undo a checkbox
clicked in the meantime.

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
- **The main row** (loop/stop) gives both buttons their own
  fixed-pixel-**height** `Frame` container (`pack_propagate(False)`),
  rather than relying on `Button`'s own `width`/`height` (character
  units) - those scale inconsistently across different font sizes, and an
  unconstrained button's natural height grows with its font, which is
  what broke the row's alignment the first two times a font size changed.
  The stop container fixes both width and height (making it a square); the
  loop container fixes only height and otherwise expands
  (`fill=tk.X, expand=True`) to fill the remaining width. Fixing height on
  both individually, rather than letting the loop one inherit it from the
  stop one, is what keeps the row aligned regardless of any future font
  size change to either.
- Every button in that row is created with `bd=0, highlightthickness=0` to
  flatten Tk's default border/focus-ring rendering.
- The **Main** tab has the loop/stop row and the Respawn, Setup and Leave &
  rejoin buttons; the **Options** tab holds the persisted switches (Run side
  quest, Resume after focus loss), the private server link and Reset Config;
  the **Debug** tab has a button per need handler.
- **`DebugCapture`** redirects `sys.stdout` into the on-screen console
  (`self.debug_text`) for the lifetime of the GUI, so every `print()`
  anywhere in the macro shows up there automatically (`write()` only logs
  and queues - from any thread - and the Tk thread puts the queue on the
  widget every `CONSOLE_DRAIN_INTERVAL_MS`, since Tk isn't thread-safe), each line starting with
  the time (`[HH:MM:SS]`). A print starting with `\r` is a status line: it
  replaces the previous status line instead of adding one, and goes into
  `output.log` only the first time (this is the "Waiting for a need..."
  counter).

### Recording a run

Tick **Record the next run (low quality video)** on the Options tab (the
`record_next_run` config switch) and the next time the loop starts it records
the Roblox window to `debug/recordings/run<N>_<time>.mp4` - then clears the
switch, so it's one run only. `start_recording_if_requested()` starts a
`RunRecorder`, which writes `RECORD_WIDTH` x `RECORD_HEIGHT` (640x360) frames at
`RECORD_FPS` (4) from a background thread, each stamped with the time so the
video lines up with `output.log` and `run_log.txt`; a slow grab is covered by
repeating the last frame, so playback is real time. It stops when the loop
ends or after `RECORD_MAX_MINUTES` (480, 8 hours), and only the newest `MAX_RECORDINGS` videos
are kept. It's MP4 (`mp4v`) where OpenCV can write one, otherwise MJPG in an
`.avi`; `recording started` / `recording stopped` (with the size) go in the run
log. The picture is the Roblox window as the macro sees it, so the macro's own
window shows up in it if it's on top.

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
| Behavior flags | `ENABLED_NEEDS`, `STOP_HOTKEY`, `BACKPACK_*`, `HALLOWEEN`, `MINIGAME_*`, `HAUNTLET_*`, `GHOST_GALLERY_*`, `REJOIN_*`, `HELICOPTER_REQUIRED`, `FOCUS_WINDOW_ON_ACTION` |
| Window / coordinates | `REFERENCE_WIDTH`, `REFERENCE_HEIGHT`, `REFERENCE_CENTER_X/Y`, `ROBLOX_RECT_TTL` |
| Timing | `RESPAWN_WAIT`, `WALK_TO_BUTTONS_DURATION`, `NEED_CHECK_RETRY_DELAY`, `LOOP_DELAY`, `STOP_CHECK_INTERVAL`, `NEED_GONE_*` |
| Stuck needs | `NEED_STUCK_CHECKS` |
| Icon readability | `NEED_BAR_BRIGHT_LEVEL`, `NEED_BAR_MAX_BRIGHT_FRACTION` |
| Pet focusing | `FOCUS_PET_REGION_TOP_PERCENT`, `FOCUS_PET_FRAME_GAP`, `FOCUS_PET_DIFF_THRESHOLD`, `FOCUS_PET_MERGE_KERNEL`, `FOCUS_PET_MIN_AREA` |
| Screen positions | `CATCH_*_POS`, `EMPTY_POS`, `RIDE_*_POS`, `LURE_NEW_POS_*`, `SETUP_*_POS` |
| Side quest / setup | `TREE_HARVEST_YIELD`, `MONEY_COLLECTED_TARGET`, `TREE_CHECK_INTERVAL`, `LURE_RECOLLECT_INTERVAL`, `LURE_COLLECT_*`, `TREE_COLLECT_*` |
| Need-icon detection | `NEED_ICON_TOP_PERCENT`, `NEED_ICON_WIDTH_PERCENT`, `NEED_ICON_BLANK_*`, `NEED_ICON_MIN/MAX_RADIUS`, `NEED_ICON_HOUGH_*` |
| Need-icon matching | `ICON_MATCH_THRESHOLD`, `ICON_BW_THRESHOLD`, `ICON_CLAHE_*`, `ICON_COMPARE_SIZE`, `ICON_CROP_RADIUS`, `ICON_SHIFT_TOLERANCE`, `MATCH_ONLY_LEFT_HALF` |
| Button detection | `BUTTON_BAND_X/Y`, `BUTTON_MIN_AREA`, `BUTTON_MIN_CIRCULARITY`, `BUTTON_MAX_COUNT`, `BUTTON_NAMES`, `BUTTON_LOOSE_*_FACTOR`, `BUTTON_PURPLE_*_ITERATIONS`, `BUTTON_OUTLINE_PADDING` |
| Debug logging | `OUTPUT_LOG_PATH`, `FAILURE_DIR`, `STATUS_PATH`, `OUTPUT_LOG_MAX_BYTES`, `MAX_FAILURE_SCREENSHOTS` |
| Recovery | `DISABLED_NEEDS_REJOIN_THRESHOLD`, `NO_PROGRESS_REJOIN_INTERVAL`, `DISCONNECT_*`, `REJOIN_*`, `ROBLOX_*`, `FOCUS_RESUME_*`, `PAYCHECK_BOX`, `PAYCHECK_MATCH_THRESHOLD` |
| Debug drawing | `DEBUG_NEED_MARKER_*`, `DEBUG_BUTTON_MARKER_*`, `DEBUG_BUTTON_LABEL_*` |
| Color ranges (HSV) | `PURPLE_RANGE`, `WHITE_RANGE` (button detection) |
| GUI | colors/fonts (`GUI_BG`, `GUI_FONT`, ...) and layout spacing (`GUI_OUTER_PADDING`, `GUI_ROW_SPACING`, ...) for the `tkinter` panel |

## Debugging an unattended run

Everything below is in `debug/` (git-ignored) so a run that broke while you
were away can be read back afterwards:

- **`output.log`** - everything the macro prints (the same as the GUI
  console), one timestamped line each, appended to across runs. `log_output()`
  is called by `DebugCapture`, so this only exists while the GUI is up. Past
  `OUTPUT_LOG_MAX_BYTES` (5 MB) it moves to `output.log.old`, replacing the
  previous one.
- **`run_log.txt`** - the short version: one tagged line per notable event
  (needs detected, resolved with how long they took, popups dismissed,
  recoveries, stops, failures). Start here to see roughly what happened.
- **`reports/run<N>.png`** - a chart of one run, drawn from `run_log.txt` by
  `helper.py` with OpenCV (no extra dependency). Written automatically when a
  run stops (`write_run_report()`, in the `finally` of `run_workflow_loop()`)
  and on demand by the Debug tab's "Report on the last run" button, which also
  opens it. Four panels: needs resolved per unit of time (the unit is picked
  from `BUCKET_STEPS` so the run fills about `TARGET_BUCKETS` bars, stacked by
  basic / teleport / other need, with failures and rejoins marked); needs
  detected per check; average seconds per resolved need with how many there
  were; and counts of failures by kind and other events. `parse_run()` reads
  the log by the exact line wording `log_run_event()` writes, so a wording
  change there needs the matching regex in `helper.py`. It never raises -
  a report problem can't take a run down.
- **`failures/`** - `log_failure(reason)` saves a screenshot of the whole
  screen named with the time, run number and reason, and also prints the
  reason, writes it to `run_log.txt` and counts it in the status file. It's
  called when a handler can't resolve a need, a need is still showing after
  `NEED_GONE_MAX_WAIT`, a need is disabled as stuck, a recovery starts or
  fails, Roblox loses focus and stops the run, and when the run crashes (the
  full traceback is in `output.log`). It never raises, since it runs while
  something else is already wrong. The oldest screenshots beyond
  `MAX_FAILURE_SCREENSHOTS` (40) are deleted.
- **`status.json`** - rewritten every cycle and on every failure/resolved
  need (`write_status()`): the run number and start time, time of the last
  update (if that's old, the macro is stuck or dead), cycle count, the needs
  last detected, how many of each need were resolved or failed, the needs
  currently disabled, the number of recoveries, disconnects, scheduled
  rejoins and focus resumes, and the last resolved need
  and last failure. `RUN_STATS` holds the counters in memory.
- **`debug_needs.png`** / **`debug_buttons.png`** - see below.
- **`recordings/`** - run videos, see [Recording a run](#recording-a-run).
- **`debug_focus_pet.png`** / **`debug_focus_pet_after.png`** - what
  `focus_pet()` saw and what the screen looked like after its clicks.

A run that *stops* (a crash, or Roblox losing focus with **Resume after focus
loss** off) ends the loop and stays stopped - deliberately, since losing focus
usually means the computer is being used for something else. The logs say
why: the failure screenshot is of the whole screen, so it shows what took the
focus.

If detection isn't finding what you expect after changing screen
resolution or Roblox's UI, check `debug/debug_needs.png` and
`debug/debug_buttons.png` first - both are overwritten every detection
pass and show exactly what the macro is looking at - before tuning any of
these.
