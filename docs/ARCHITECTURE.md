# Architecture

Detailed technical walkthrough of `main.py`. This is for understanding or
modifying the code - if you just want to run the macro, see the main
[README](../README.md) instead.

Almost everything lives in `main.py` (including the run-report chart - see [Debugging an unattended run](#debugging-an-unattended-run)), organized top to bottom as a sequence
of `# === SECTION ===` blocks; this doc follows that same order. The one
exception is `magic_numbers.py`, which holds the tunable constants (all but the colour-bucket tables of the run report, which are in `main.py`) (see
[Configuration reference](#configuration-reference)) - `main.py` does
`import magic_numbers as cfg` and refers to them as `cfg.NAME`
everywhere else (`tools/check_constants.py` fails on a `cfg.` name that
`magic_numbers.py` doesn't define - a typo pyflakes can't see). The docs below
still write constants by their bare name.

## The workflow lifecycle

The main thing that runs is **`run_workflow_loop()`** (the GUI's loop
button; Setup, Leave & rejoin, Respawn and the Trading Hub trips are single actions run the same way): it respawns once up front (so the loop always starts from a known,
on-the-ground state), then repeats `run_full_cycle()` forever, pausing
`LOOP_DELAY` seconds between cycles, until stopped.

**`run_full_cycle()`** is a tight retry loop:

```
while True:
    check_stop(); count the cycle
    check_quiet(); flush_idle_checks_if_due(); write_status()
    unscrew(); side_quest()
    if process_needs():   # resolved (or at least handled) ONE need
        return
    respawn_if_no_detection()   # nothing detected for NO_DETECTION_RESPAWN_INTERVAL
    wait NEED_CHECK_RETRY_DELAY seconds, then check again
```

The key word is *resolved*. `process_needs()` returns `True` only if it
actually did something - clicking a button or running a handler (or a handler
was cut short by a minigame, a rejoin or a popup, which respawn the character
too) - and it handles **one** need per call. The loop then starts a new cycle, so
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
   `ref/needs/` (`find_matching_need()`). An icon that doesn't match anything
   well enough triggers `prompt_rename_need()` (asks you, in a dialog, to
   name and save it - only when `SAVE_NEW_NEEDS` is on) and is otherwise skipped this pass - it isn't
   "resolved," it's just been taught for next time. The saved icon is the
   high-contrast black-and-white form (`preprocess_icon()`); cancelling the
   dialog or an empty name saves it as `unnamed_<idx>`. The dialog runs on the
   GUI's thread (`run_on_ui_thread()`, no timeout) since the worker has no
   console to read from; without a GUI it asks on the console.
4. Every icon that *did* match is collected into an ordered list of need
   names, in the order they were detected, and logged (`detected: ...`, and
   printed with each icon's score; icons on screen but none recognised are
   logged once, not on every check). A check that finds no need isn't logged line by line
   either: `note_idle_check()` counts them (including the not-recognised ones
   after the first) and `flush_idle_checks()` writes one `idle checks: N over
   Ss` line before the next detection, every `QUIET_LOG_INTERVAL`, and when the
   loop stops - the run report spreads those N empty checks over the S seconds,
   so "needs detected per check" counts every check, not only those that found
   something. The same goes for checks that find exactly the needs the last
   `detected:` line named with nothing handled since (disabled needs sitting
   on screen, say): `note_repeat_check()` counts them and the flush writes
   `same checks: N over Ss`, which `parse_run()` turns back into N more checks
   like the `detected:` line above it. Anything that changes the picture - a
   different set, an empty stretch, a handler running - logs `detected:`
   again (`state.last_detected_body`). The list is also handed to `record_detection()` -
   on every check, including ones that find nothing - which is what catches
   [stuck needs](#stuck-needs).
5. The list is then worked through in order until one need is resolved. For
   each `need_name`, `get_need_handler(need_name)` looks it up in
   `NEED_HANDLER_CLASSES` and returns an instance, or `None` if that need
   is switched off (`need_is_enabled()`) or was disabled as stuck (see
   [Stuck needs](#stuck-needs)) - a `None` result is logged and skipped
   without doing anything. Every need is handled the same way from here: calling
   `.handle()` on whatever handler came back (see
   [Need handlers](#need-handlers)).
   - If `handle()` returns `True`, the character respawns (always done by
     `process_needs()`, never by the handler itself) and `process_needs()`
     returns `True` - the remaining needs in the list are dropped; they
     will be detected again, along with anything new, by the next check.
   - If it returns `False` (the need wasn't actually resolved this pass -
     e.g. no buttons detected, or a UI element `ChooseNeedHandler` needed
     wasn't found), it is counted in `run_stats.failed`, logged as a failure
     (`<need> handler could not resolve it`) and skipped with no respawn, and
     the next one in the list is tried.
   - If it returns `True` but one of its waits ran out with the need still
     showing (`wait_until_need_gone()` and `walk_alternating()` set
     `state.need_cleared` to `False` and log the failure), the character is respawned
     (it was moved) but the need counts as *failed*, not resolved:
     `not cleared: <need> (Ns)` is logged, `last_progress` is left alone, and
     it is still an attempt, so a need that never clears is disabled by
     `record_detection()` in the end.
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
  is (a window titled exactly "Roblox"; a title that only contains it, a browser tab or the crash window, counts only if no
  window is titled exactly "Roblox" - the same choice `roblox_window()` makes, so keys never go to a browser tab); `roblox_windows()` lists the open Roblox windows; `grab_screen()`
  takes a screenshot via `mss`; `exact_color_mask()` / `find_exact_color()`
  find things by their exact pixel color rather than their shape (the
  choose need's button, the popups and the Play button all work this way).
- **STATE** - this module's own mutable state: `STOP_EVENT`;
  the `State` object, `state` (a dataclass holding all of this module's
  mutable state - the run number, `last_progress`, `last_rejoin`, the stuck-need
  tracking and the in-task check flags; `State.snapshot()` is saved with every
  failure), including `state.button_positions`, the in-memory cache of the last-detected action
  button positions (never persisted to disk - see
  [Persisted game config](#persisted-game-config)); and
  `state.attempt_streaks` / `state.attempted_last` / `state.disabled_needs`, which back
  [Stuck needs](#stuck-needs). Every fixed
  config/tuning value lives in `magic_numbers.py` instead - see
  [Configuration reference](#configuration-reference).
- **LOGGING** - `next_run_number()` / `log_run_event()`, plus the
  unattended-run tools (`logger` with its `OutputLog` handler, see below), `save_failure_report()`,
  `log_failure()` and `write_status()` - see
  [Debugging an unattended run](#debugging-an-unattended-run).
- **GAME CONFIG** - `load_game_config()`/`save_game_config()`: the state
  that *does* persist across separate launches of the script (unlike
  `state.button_positions` above) - see [Persisted game config](#persisted-game-config).
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
  `detect_backpack_normal()`; the click sequences that open the backpack end
  with `close_backpack(step)`, which calls the latter - see
  [Click & input primitives](#click--input-primitives)),
  `dismiss_stray_windows()`, `minigame_popup()`,
  `detect_paycheck()`; the `task_unscrew()` / `task_unscrew_tick()` hook that
  runs them during a task; and `side_quest()`.
- **RUN RECORDING** - `RunRecorder` and `start_recording_if_requested()`. See
  [Recording a run](#recording-a-run).
- **INPUT CAPTURE** - `InputCapture`, `toggle_input_capture()`, `start_run_capture()`, `zip_capture()` and the key / mouse
  readers (`pressed_inputs()`, `cursor_position()`, `to_reference()`). See
  [Input capture](#input-capture).
- **HAUNTLET 2 READER** - `read_hauntlet_room()`, `read_hauntlet_items()`, `read_hauntlet_hearts()`,
  `hauntlet_timer_visible()`, `find_hauntlet_doors()` and their pictures. See [Reading Hauntlet 2](#reading-hauntlet-2).
  (What is done with it - `HauntletRun`, `hauntlet_strafe()`, `hauntlet_walk()`, `play_hauntlet_2()` - is with the other minigame code under
  NEED HANDLERS; see [Playing Hauntlet 2](#playing-hauntlet-2).)
- **WORKFLOWS** - `run_full_cycle()` and `run_workflow_loop()` (plus
  `ResourceMonitor`, `check_quiet()` and `respawn_if_no_detection()`, which run
  inside the loop). See [The workflow lifecycle](#the-workflow-lifecycle) above.
- **RUN REPORT** - `parse_run()` / `draw_report()` / `generate_run_report()`,
  which chart a run from the log. See
  [Debugging an unattended run](#debugging-an-unattended-run).
- **GUI** - the `tkinter` control panel. See [GUI internals](#gui-internals).
- **MAIN** - starts the GUI. The top of the file, **EXTERNAL DEPENDENCIES**,
  holds the imports and the `Image` / `Point` / `Box` / `Color` aliases.

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
  `hover_move()` (so every click), the pet swipe and the focus click
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
`save_failure_report()` is the one thing that captures the whole screen
instead, to show what else was on it.

## Need-icon detection

`detect_need_icons()` scans a region of the screen for the round need icons. Only that region is captured (`grab_screen_region()`: the strip plus the margin the icon crops reach past it, about 13% of the screen), when the Roblox window is exactly the reference size - otherwise it falls back to cropping a full `grab_screen()`, which gives the same pixels:

1. Crop to the top `NEED_ICON_TOP_PERCENT` of screen height and the
   left `NEED_ICON_WIDTH_PERCENT` of screen width. The width crop
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
   The search is skipped while the strip (as cropped and blanked above) is
   practically unchanged since it last ran: when fewer than
   `NEED_STRIP_MAX_CHANGED_FRACTION` of its pixels moved by more than
   `NEED_STRIP_CHANGED_LEVEL`, the last result is reused (at most
   `NEED_STRIP_MAX_REUSES` times in a row, or `NEED_STRIP_MAX_EMPTY_REUSES` if
   that result found no icon, since a missed icon looks the same; a call with
   `fresh=True` always searches, and the debug image is still saved). An icon appearing or going changes far more than that. Only the
   strip is compared, never the whole screen.

Each detected icon is cropped out of the captured strip (plus margin) at a fixed
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

`icon_signature()` first blacks out everything outside the disc inside the
icon's ring (`ICON_MATCH_RADIUS_FRACTION`) - the square crop's corners show
whatever is behind the icon, and on a bright scene they used to wreck the
CLAHE and the threshold - then resizes the result to `ICON_COMPARE_SIZE`, scales it
to 0-1, and keeps only the **left half** (`MATCH_ONLY_LEFT_HALF`) so an
event badge overlapping an icon's top right doesn't affect the match.
`compare_signatures()` scores two signatures as `1 - mean squared
difference`, over that disc only (`icon_match_mask()`). The saved icons are
run through the same function when loaded, so none needed re-saving.

A detected center can be a pixel or two off, which matters at this
resolution, so `icon_variants()` cuts the live icon out at every offset
within `ICON_SHIFT_TOLERANCE` px and each saved need is scored by its
best-aligned variant.

`find_matching_need()` compares the new icon against the signature of every
saved `.png` in
`ref/needs/` (`need_signatures()` computes those once and recomputes them only
when a file in `ref/needs/` is added, changed or removed, so a check doesn't
re-read the folder) - including subfolders, so related icons can be grouped (the
seasonal `diving` and `puddle` icons live in `ref/needs/weather/`); a need's
name is just its file name, wherever it sits - and keeps the best score. A best score below
`ICON_MATCH_THRESHOLD` means "not confident this is anything we've
seen" - the icon is then unknown and, when `SAVE_NEW_NEEDS` is on, goes to
`prompt_rename_need()`; otherwise it is just skipped.

## Waiting for a need to clear

`wait_until_need_gone()` - used after a button click (`click_need_button()`)
and after `TeleportWalkNeedHandler`'s movement sequence (`choose` doesn't
wait: it clears at once, and the next check sees that; `walk` and `ride` do
the same check in parallel with their movement, see
[below](#checking-a-need-in-parallel-with-movement)) - polls
`scan_needs()` (one detect-and-match pass; the circle search may be skipped
while the strip is unchanged, except for the second look of each check, which is
always a real search) every
`NEED_GONE_POLL_INTERVAL` until a check finds the target need gone - one
check is enough; each check looks twice, as described below - up to
`NEED_GONE_MAX_WAIT` total, 75s - the one maximum for every need (the
teleport-walk needs time out by themselves in the game, and camping was seen
clearing ~66s after its walk) - or a teleport-walk entry's own `max_wait`.

Each individual check goes through `_need_cleared()`, which re-samples
once more, `NEED_GONE_FLICKER_RECHECK_DELAY` later, before a miss is
allowed to count: the icon can drop out of a single detection pass for a
frame (or against a momentarily busy background) without the need having
actually cleared. (There used to be a further three-misses-in-a-row
confirmation on top of this; it was removed to speed up resolving.) This only guards against a one-frame flicker -
a detection failure caused by a *sustained* background change behind the
icon isn't something this re-check could fix, since it would fail again on the
immediate retry too - which is why the matching itself has to work on any
background (see [Need-icon matching](#need-icon-matching): only the disc inside the
icon's ring is compared, checked against the real screenshots in
`tests/icon_detection/`, bright ones included). There used to be a fallback for
a near-white bar (the Pizza Party's) that judged an icon gone by how it changed
between looks; with the matching reliable on those backgrounds it was removed,
and so was the "bar unreadable" state: a wait that runs out is always a failure.

Popups, stray windows and minigames that turn up during the wait are handled
by the `unscrew()` checks that `wait_interruptible()` runs during a task - see
[Per-cycle checks while a task runs](#need-handlers).

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
   gives a "has a white outline" score per candidate (the count of those
   pixels within the contour's bounding box, grown by `BUTTON_OUTLINE_PADDING`).
4. Find purple contours, filtering out ones that are too small
   (`BUTTON_MIN_AREA * BUTTON_LOOSE_AREA_FACTOR`), too large
   (`BUTTON_MAX_AREA_FRACTION` of the band - a blob covering most of the
   band isn't a button), too small in radius (`BUTTON_MIN_RADIUS`), or not
   round enough (`BUTTON_MIN_CIRCULARITY * BUTTON_LOOSE_CIRCULARITY_FACTOR`).
   These thresholds are deliberately looser than the need-icon ones, since
   buttons are more variable in how cleanly they contour.
5. Prefer candidates with a white outline, then by size; take the top
   `BUTTON_MAX_COUNT`, then sort left-to-right.
6. `refresh_button_mapping()` zips that left-to-right order against
   `BUTTON_NAMES = ["hungry", "thirsty", "dirty", "potty", "sleepy"]` and
   stores the result in `state.button_positions`. This assumes the five buttons
   are always laid out in that fixed order on screen - if Roblox ever
   changes that order, this mapping (and only this mapping) would need
   updating. It clears the previous mapping first, even when detection fails,
   so a failed scan never leaves a stale position for `click_need_button()`.
   Anything but five buttons is a failed scan: it logs `button scan found N of 5`
   and scans once more after `BUTTON_SCAN_RETRY_WAIT` seconds. If that is not
   five either, the buttons found are named by where they are (`name_buttons_by_position()`: each within
   `BUTTON_SLOT_TOLERANCE` of its `BUTTON_SLOTS_X`, none two of them) - a button can be hidden by the
   character standing before it or by a beam of light across it, and naming by order would then shift
   every name after it (in a run, dirty went to the potty button for 40 minutes). The need of a hidden button
   finds no button mapped and fails; if a found one is near no slot, nothing is mapped and it returns `False`.

## Click & input primitives

Every click in the macro goes through **`hover_click()`** /
**`hover_move()`**: moves with SendInput (`pydirectinput`), nudges
`HOVER_NUDGE_PIXELS` and back so Roblox registers real mouse movement
(hover), then clicks. Roblox ignores `pyautogui`'s cursor warps as hover
movement, so this is what makes a click register reliably whether it's on
a UI button or into empty game-world space (there used to be a separate
`simple_click()` with no hover wiggle for the latter case, but it wasn't
actually needed - `hover_click()` works fine there too, so it was dropped).

(`hover_move()` uses SendInput rather than `pyautogui.moveTo()` because
`SetCursorPos`-style warps don't count as hover to Roblox, only real input
events do.)

`close_backpack(step)` ends a click sequence: it presses the backpack key and
checks the backpack really closed - a misplaced click earlier can have expanded
it, when the key only shrinks it - and closes it properly if not
(`close_backpack_if_open()`, which gives up and logs a failure after a second
press rather than risk reopening it, and does nothing unless Roblox has the
focus).

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

Which needs actually run automatically is decided by a switch per need, the one next to its
Test button on the GUI's Debug tab (see below), kept in the game config (`needs_enabled`, see
[Persisted game config](#persisted-game-config)). A switch starts **on**, unless the need is missing from the set
`ENABLED_NEEDS` (`magic_numbers.py`, the first thing defined in that file), which only decides where the
switches start: `need_is_enabled()` is the answer, `enabled_needs()` the set of them. A detected need that is off is
logged and skipped, not resolved. Enabled in `ENABLED_NEEDS`: `hungry`, `thirsty`, `dirty`, `potty`,
`sleepy`, `catch`, `pet`, `ride`, `walk`, `choose`, plus the
teleport-walk needs `cafe`, `salon`, `sick`, `pizza`, `school`, `beach`,
`camping`, `bored` (called out with their own inline comment in the set
literal, since they leave the map and run considerably longer than
everything else).
The GUI's **Debug** tab has a **Needs** subtab (its second) with, for each handler, a switch (whether the macro acts on the
need; usable while a run goes on, like the other config switches) and a small **Test** button that
runs the handler directly whether or not it is switched on, and a button that runs every
*teleport* handler once, one after another (`TELEPORT_HANDLERS`, `run_all_need_handlers()`: each with the
in-task checks on, the character respawned after each, a stop ends the
whole sequence, and a summary of how each went printed at the end).

**Focusing the pet.** (Each click is followed by `FOCUS_PET_CLICK_SETTLE` and a `detect_pet_focus()` check; the clicks stop at the first one that focuses it, and `FOCUS_PET_MENU_WAIT` is only waited if none did.) `pet` and `choose` both start with `focus_pet()`, which first checks whether the pet is already focused (`detect_pet_focus()`: the BACK button's crop is on screen) and if so returns True with no clicks; otherwise it
has no hardcoded position: it takes two screenshots of the bottom of the
screen (below `FOCUS_PET_REGION_TOP_PERCENT`) `FOCUS_PET_FRAME_GAP` apart,
and `moving_blobs()` diffs them - leaving out the macro's own window
(`macro_window_reference_rect()`, plus `FOCUS_PET_IGNORE_MARGIN`), whose console
scrolls and flickers and used to be picked up as a huge "moving" blob. Pixels that changed by more than
`FOCUS_PET_DIFF_THRESHOLD` are closed together (`FOCUS_PET_MERGE_KERNEL`,
since an up/down bob only changes the pet's top and bottom edges), blobs
under `FOCUS_PET_MIN_AREA` are dropped as noise, and `focus_pet()` clicks
the center of every remaining blob, largest first. It returns `False` (and
the handler skips the need) if nothing moved. It logs what it saw (how many
blobs, each one's center, size and area), so a click that lands on the
wrong thing (another player, a UI element) can be told from the log.

Except for `catch` and `pet` - which always run their full fixed sequence
precisely, with no early exit - every other need either waits for its
icon to actually clear (`wait_until_need_gone()`) instead of guessing how
long that takes, or checks for that *while* still moving (see below),
rather than a flat sleep after the fact.

| Need | How it's handled |
|---|---|
| `hungry` / `thirsty` / `dirty` / `potty` / `sleepy` | `ButtonNeedHandler`: walk to the action buttons (`walk_to_buttons()`), refresh the button mapping (`refresh_button_mapping()`), click the matching one (`click_need_button()`), which then calls `wait_until_need_gone()` for that need name. A click can be refused - the game answers "Only Pets, Babies, and Dolls can use this." even when all five buttons were found - and for `BUTTON_REJECT_LOOK` seconds after each click `only_pets_message_visible()` looks for that text (the white of its letters against `ref/popups/only_pets.png`, `ONLY_PETS_*`, so what is behind it doesn't matter). If it is there the handler waits `BUTTON_RETRY_WAIT` seconds (the pet may be on its way: in a run three clicks a few seconds apart were all refused) and clicks again, up to `BUTTON_CLICK_TRIES` clicks in all (a run event each time); a click that is refused again gives the need up (the handler returns False without waiting for the icon), and one that takes after a retry gets only `BUTTON_RETRY_CLEAR_WAIT` seconds for the icon to clear (a retried need timed out at the full wait). |
| `catch` | `CatchNeedHandler`: open backpack → toys → squeaky toy → equip → close backpack → wait `CATCH_WAIT_AFTER_EQUIP` → hold zoom-in → throw the toy by clicking empty space `CATCH_THROW_COUNT` times, `CATCH_EMOTE_DELAY` after each → unequip. A fixed sequence: it doesn't wait for the icon (the next check sees whether it cleared). |
| `pet` | `PetNeedHandler`: `focus_pet()` (see below, with `PET_FOCUS_CLICK_DURATION`), click the screen centre, move to `PET_SWIPE_START_OFFSET` above it, then hold the mouse down and swipe once down to `PET_SWIPE_END_OFFSET` below it over `PET_SWIPE_DURATION`, releasing the button in a `finally`. Same as `catch` - always the full fixed duration. |
| `choose` | `ChooseNeedHandler`: `focus_pet()` (see below), find the exact-color button (`CHOOSE_BUTTON_COLOR`, since it has no distinguishing icon), hover to it slowly (`hover_click`) and click, then click screen-center to dismiss the menu. It doesn't wait for the icon to clear (it's gone at once; the next check confirms). |
| `ride` | `RideNeedHandler`: step back, mount (`e`), walk forward briefly, then `equip_favorite_vehicle()` (backpack → vehicles → first vehicle → equip → close backpack), hold `r` (`KEYS["helicopter"]`) for `RIDE_R_HOLD_DURATION`, then `walk_alternating(("w", "s"), NEED_GONE_MAX_WAIT, need_name="ride")` - up to `NEED_GONE_MAX_WAIT`, ending early the moment "ride" is confirmed cleared (see below). |
| `bored` / `beach` / `school` / `cafe` / `salon` / `pizza` / `camping` / `sick` | `TeleportWalkNeedHandler`, configured per need in `TELEPORT_WALK_NEEDS`: teleport to the nursery or dealership (`teleport_to()`), hold each `(key, seconds)` step with `hold_key()`, then `wait_until_need_gone()` (see [Waiting for a need to clear](#waiting-for-a-need-to-clear)). `process_needs()` respawns afterwards. An entry may also set `final_click`, the Yes of a dialog the need has to answer after the last hold (used by `sick`). After `SICK_PRE_CLICK_WAIT` the handler looks for the doctor's *prompt* - the purple hand button over the player's head, a reference crop (`ref/popups/doctor_prompt.png`, `POPUP_TEMPLATES["doctor_prompt"]`, `find_doctor_prompt()`) - for `SICK_PROMPT_LOOK` seconds (`look_until()`, a look every `SICK_LOOK_INTERVAL`), and clicks it where it is found. It does *not* press `e`: that is the key of the pet's own prompt, and in a recording of the macro it opened the pet's menu, the pet view backed out of, and the click on the Yes that followed hit nothing - while a hand clicking the prompt, by hand, brought up the dialog about 2.5 s later (`tests/sick/`: the real screenshots of the prompt, the dialog and the pet view). If the pet view is open when the handler starts it is backed out of first (`dismiss_stray_windows()`; it hides the prompt). Then it looks for the dialog (`detect_sick_dialog()`, `ref/popups/sick_dialog.png`) for `SICK_CONFIRM_WAIT` seconds; seen, it clicks the Yes (`final_click`). The need is given up at once - the character respawns, the handler returns False with no wait for the icon to clear and `process_needs()` logs the failure - when the prompt is not on screen (`sick: the doctor's prompt is not on screen`) or the dialog does not come up (`sick: no dialog after the prompt was clicked`). (Before this the handler pressed `e`, clicked Yes whether or not the dialog was recognised, and waited out the icon's 75 s: sick failed to clear 16 times in a 7-hour run, each costing about two minutes), `helicopter` (see below) and `max_wait` (how long to wait for the need to clear afterwards; default `NEED_GONE_MAX_WAIT`, 75s). Which table is used depends on `HALLOWEEN` - see below. |
| `walk` | `WalkNeedHandler`: `walk_alternating(("a", "d"), NEED_GONE_MAX_WAIT, need_name="walk")` - up to `NEED_GONE_MAX_WAIT`, same early-exit as `ride` above. |

**Helicopter.** A teleport-walk entry can fly instead of walk. `HELICOPTER_REQUIRED`
(`magic_numbers.py`) is the default for entries that don't
say; an entry overrides it with `helicopter=True`/`False`. When on, after
`teleport_to()` (the dialog's Yes is clicked up to `TELEPORT_YES_TRIES` times until the view changes or the screen goes white, like a minigame popup's; a Yes that never takes is logged) the handler holds `w` for `HELICOPTER_FORWARD_DURATION`,
calls `equip_favorite_vehicle()` (the same backpack sequence `ride` uses; the
first vehicle is the favorite once `setup_game()` has filtered the backpack),
holds `r` for `HELICOPTER_HOLD_DURATION`, runs the entry's steps as
usual, then presses space (`KEYS["jump"]`) on arrival, before any `final_click`.
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
form; not during setup, which keeps it open for ~8s on purpose and closes it itself), then
`dismiss_stray_windows()`, then, if `HALLOWEEN` is on, `close_minigame_finish_screen()`
(whenever the minigame finish screen is up - the red GAME OVER! banner and the green
NICE! button, `detect_minigame_finished()` - NICE! is clicked, the character
respawned and the task abandoned, even if the macro wasn't playing that minigame;
in setup only the click is made) and `minigame_popup()`, then
the paycheck check. The stray windows and the minigame popup go before the
paycheck check because their green buttons (Okay, Yes) are the same green as
the paycheck's CASH OUT button, so `detect_paycheck()` would mistake them for
one. For the same reason
`detect_paycheck()` matches the crop of the popup's bank-card header
(`ref/popups/paycheck.png`, within its `POPUP_TEMPLATES` box) instead of counting green:
it used to count green pixels, and the green tiles of an open backpack passed
that, so it "dismissed" a paycheck every cycle - and its second dismiss click,
`PAYCHECK_DISMISS_POS_2`, lands on the little orange box next to the
backpack's settings button, which expands the backpack.
`rejoin_game()` is the automatic recovery - see
[Automatic recovery](#stuck-needs).

**Stray windows.** Two windows have been seen open on their own, covering
the screen: the Trading Hub (how it got opened isn't known; a misclicking
`choose` is the suspect; it throws up a "Go
to the Trading Hub to edit listings!" popup on top) and the Star Rewards, which
opens once a day, at no known moment. The Star Rewards hides the need icons;
the Trading Hub doesn't, but needs can't be resolved with it in the way. Run
308's two 20-minute stalls (a `paycheck popup dismissed` line every cycle) were
these windows - the macro's paycheck clicks don't close them, and their green
buttons look like CASH OUT.
(`load_templates()` reads each folder's crops once and re-reads them only when a file is added, changed or removed.)
`dismiss_stray_windows()` recognises each by a crop of its title
in `ref/popups/` (`trading_hub.png`, `star_rewards.png`, `star_rewards_claim.png`, `task_board.png`; matched within the
`box` in `STRAY_WINDOWS`, at least `STRAY_WINDOW_MATCH_THRESHOLD`) and clicks
its `close_pos`; for the Trading Hub it first clicks the popup's green Okay
(`STRAY_OKAY_*`) if that's showing. The Star Rewards has two views, each its own entry: the one that opens by itself once a day (`star_rewards_claim`) has only a green CLAIM! button and no X, so it is closed
by clicking CLAIM!; the calendar one that the star icon opens (`star_rewards`) is closed by its red X. Only the calendar one was handled at first, with the X clicked wherever the window
was - which closes nothing in the daily view, the one the macro meets (real screenshots of both are `tests/popups/star_rewards_claim.png` and `star_rewards.png`; the crops are cut from them). The **Task Board** (`task_board`: opened by a notification such as "Trading Hub: Your listing got an offer!", on its Alerts tab; its "TASK BOARD" plate is the same on every tab) is only closed, by its red X, for now. The real screenshots of the Trading Hub, the Task Board and both Star Rewards views are in `tests/popups/` - they are the Roblox screenshots (which lack the window's 23-row title bar) with 23 black rows added on top, to be the macro's frames, and the crops in `ref/popups/` are cut from them (the Trading Hub crop was already pixel-identical to the same place in a new screenshot, so it was kept). A new window is a new crop plus an entry in
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

`profile` is the player's own profile page (`ref/popups/profile.png`, the toolbar of Stickers / Preview / paint / save; `close_pos` is its red X), which a stray click on the character or the character menu's Profile opens.

`cant_spawn_vehicle` is the "You can't spawn a vehicle here." popup
(`ref/popups/cant_spawn_vehicle.png`; `close_pos` is its Okay button). It is
flagged `abort_task`: when it is closed while a need handler is running,
`unscrew(in_task=True)` returns `"aborted"` and `task_unscrew_tick()` raises
`TaskInterrupted("a popup")`, so the need is abandoned and run again from the
start on the next check (logged as "<need> interrupted by a popup"). A paycheck popup dismissed mid-task does the same: its clicks and the dismissal leave the backpack and the character in an unknown state (run 3, 17:51: it landed in the middle of `teleport_to()`, which carried on and walked the cafe route from the house). Setup never aborts this way (it can't be abandoned halfway), so the popup is just closed there.

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
`unscrew()`, with two differences: the rejoin check only acts on a disconnect or a
white screen (`rejoin_game(kinds=("disconnect", "white_screen"))`; the stuck, scheduled and stalled rejoins wait
for the top of a cycle), and the backpack is only closed once it has been seen
open on `BACKPACK_CLOSE_CONFIRMATIONS` checks in a row - so one sighting, which could be a
handler in the middle of its steps, isn't enough. That was not enough either: an equip sequence keeps the backpack open for about
6 s, longer than two checks apart, and in a run's log 10 of 13 rides had the check close the backpack in the middle of the
sequence (a plain "backpack was left open - closed it" 2-3 s before the "... after equipping the vehicle" of the sequence's own close) - given one
earlier sighting that wasn't real, the next check, inside the sequence, was the second; the key that ends the sequence then opened the backpack
again and it was closed a second time. (A simulation of the real handler and check on a fake clock gives the same pair of lines; what the
first, false sighting is is not known - the debug image of the first sighting that was kept for this is gone; the log has the line.)
So **a handler that opens the backpack on purpose says so**: `backpack_on_purpose()` (used by `equip_favorite_vehicle()`, the catch
need's toy sequence and `teleport_to()` up to the click that teleports) makes `close_backpack_if_stuck()` leave the backpack alone, forget any
sighting from before, and say whether it is open (so the paycheck check is still skipped). Setup, which keeps it open for ~8 s, is
exempt in its own way (`play_minigames=False`). The paycheck check is skipped
while the backpack is still open. If a minigame is played, or a disconnect made
it rejoin, `TaskInterrupted` is raised: the handler is abandoned (a minigame and
a rejoin both respawn the character; for the popup abort below,
`task_unscrew_tick()` respawns it before raising), `process_needs()` logs `<need> interrupted by a minigame` /
`a rejoin` / `a popup`, doesn't count it as resolved or as a failed attempt
(except the popup: that does count as an attempt, so a need a popup keeps
spoiling is disabled like any stuck need), and the next
check decides from the screen what's still needed. During setup a minigame is
only declined (No, without "do not show again"), since setup's clicks toggle
things and can't be abandoned halfway.

### Popup templates

Everything recognised by a crop in `ref/popups/` is looked up the same
way, by `popup_found(img, name)` / `popup_visible(img, name)`: the crop is matched
(`cv2.matchTemplate`, `TM_CCOEFF_NORMED`) within a box and counts as found when the score reaches
a threshold - the crop's centre is returned too, for the BACK button's click. Each name's box and
threshold are in `POPUP_TEMPLATES` (`paycheck`, `sick_dialog`, `doctor_prompt`, `backpack_normal`, `backpack_back`) or, for
a stray window (and `pet_focus`), in `STRAY_WINDOWS` with `STRAY_WINDOW_MATCH_THRESHOLD`. The
detectors (`detect_paycheck()`, `detect_sick_dialog()`, `detect_pet_focus()`, the title check in
`detect_backpack_normal()`, the BACK button in `close_backpack_if_open()`) are that lookup plus
whatever they do with the answer, and `dismiss_stray_windows()` walks `STRAY_WINDOWS` through it. A
new crop is a file in `ref/popups/` plus an entry in one of the two tables. A name with no crop file is
never found. The minigame title (`identify_minigame()`) and overlay (`minigame_overlay_visible()`) crops
live in their own folders and pick the best of several, so they keep their own constants.

**Backpack tabs.** The backpack remembers its last category tab, and clicking a tab that is already selected (orange background) expands the backpack. `select_backpack_tab()` therefore only clicks the vehicles / toys tab when `is_backpack_tab_selected()` doesn't see the orange tile.

**The BACK button.** The expanded backpack has a BACK button at the top
(`ref/popups/backpack_back.png`, found within its `POPUP_TEMPLATES["backpack_back"]` box) that closes
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
white alone isn't enough, since the white is also any bright scenery. It is also
recognised by a crop of its "BACKPACK" title (`ref/popups/backpack_normal.png`,
`POPUP_TEMPLATES["backpack_normal"]`), because the color check, tuned on one screenshot,
missed another (more crops of it can be added to `ref/popups/`). `detect_backpack_open()` is either. `close_backpack_if_open()` presses
`KEYS["backpack"]` - but only while Roblox has the focus, so the key can't go to
another window - looks again, and presses once more if it's still open (the
expanded backpack takes two presses: the first shrinks it to the normal one, the
second closes that). If it's still open after the second press the key isn't
doing what's expected, so a failure is logged and it stops rather than pressing
again, since another press could just reopen it. `close_backpack_if_stuck()`
decides *when*: at once at the top of a cycle, and mid-task only on the second
sighting in a row (see above).

**Minigame popups.** Halloween has minigames (`MINIGAME_LABELS`) that offer to teleport you -
"Ghost Gallery is starting soon! Teleport there now?", "Hauntlet 2 is
starting soon!..." and "Stitches' Spotlight is starting soon!..." - in the same popup layout.
`detect_minigame_popup()` recognises that layout by its two buttons (each piece of the Yes green looked at on its own, with the red beside *it*: a popup over the open backpack, whose buttons are green too, was missed when all the green on the screen was averaged - `tests/popups/` has real screenshots of popups and of other green buttons): neither
color is unique alone (Yes green = `GREEN_BUTTON_COLOR`, No red = the
Exit Home button), so it needs the Yes green (`GREEN_BUTTON_COLOR`, at
least `MINIGAME_POPUP_MIN_BUTTON_PIXELS` px) with the No red
(`MINIGAME_POPUP_NO_COLOR`) within `MINIGAME_POPUP_NO_MAX_DX` / `_DY` px to
its left, **under the popup's own "Teleport there now?" line** (a crop, `ref/popups/minigame_popup.png`, `POPUP_TEMPLATES["minigame_popup"]`; identical in the popups of all of them). The crop was added after a run in which 30 "popup dismissed (unknown)" lines, each a click on the popup's "do not show again" and No places, and 19 "can't spawn vehicle" windows came with a cascade of failed needs (choose 10 of 10, the buttons' needs 15 in a row): the dialogs taken for the popup were the macro's own teleport dialogs ("Teleport to the Nursery to adopt pets?", "Teleport to The Vehicle Dealership to see vehicles?": a green Yes and a red No, clicked by `teleport_to()` and "dismissed" by the in-task check in the middle of it), as the screenshots `tests/popups/teleport_*.png` show - the detector before the crop fired on both. The one dialog left out on purpose is the sick need's own "Ask Doctor Heart to heal your family?" dialog, which has the same Yes/No look but is the sick handler's to answer (its Yes is `SICK_FINAL_CLICK_POS`): `ref/popups/sick_dialog.png`, within its `POPUP_TEMPLATES` box, makes the detector ignore it. (Before that, the periodic in-task check (`UNSCREW_TASK_INTERVAL`) took it for a minigame popup and clicked its decline positions in the middle of the sick sequence - `unknown` popups clustered in the sick handler in the logs.) `identify_minigame()` then says which one it is: it matches the
first line of the title (`MINIGAME_TITLE_BOX`) against the crops in
`ref/halloween/` (`cv2.matchTemplate`, best score at least
`MINIGAME_TITLE_MATCH_THRESHOLD`); a minigame's name is its file name,
lower-cased (`ghost_gallery.png` → `ghost_gallery`, `hauntlet_2.png` → `hauntlet_2`), so a new one is just a
new crop - plus an entry in `MINIGAME_LABELS` and a player in
`MINIGAME_PLAYERS`.

Each minigame has a switch in the game config (`hauntlet_2_enabled`,
`ghost_gallery_enabled`, `stitches_spotlight_enabled`, all **off** by default; "Play ..." checkboxes on the
Settings tab). A minigame that is recognised but has no player in `MINIGAME_PLAYERS` gets "(no player yet)" on its checkbox, and its popup is declined like a switched-off one even if the switch is on. `minigame_popup()`:
- **switched off, not recognised, or with no player yet** - clicks `MINIGAME_POPUP_DONT_SHOW_POS`
  ("Do not show again this session") and `MINIGAME_POPUP_NO_POS`;
- **switched on, but a need is in its end wait** (`wait_until_need_gone()`, or `walk_alternating()` watching for its need; `end_wait()` marks it in `state.end_wait_need`) -
  the popup is left up for the need to clear and count as resolved, at most `MINIGAME_POPUP_END_WAIT` seconds from when it was first seen (`state.popup_held_since`):
  `minigame_popup()` returns `"waiting"` and `unscrew()` returns at once, before the paycheck check (the popup's green Yes would be taken for a paycheck's button);
- **switched on** - `play_minigame()` clicks Yes (`MINIGAME_POPUP_YES_POS`) with `accept_minigame_popup()`
  and plays it, as below. The click is checked: within `MINIGAME_TELEPORT_WAIT` seconds (looking every `MINIGAME_TELEPORT_POLL`) there
  must be the white screen of a teleport or a view that has changed (`screen_changed()`: the shrunk screen differs from the one at the click by
  more than `MINIGAME_TELEPORT_CHANGE` grey levels). If not and the popup is still up (`detect_minigame_popup()`) the click didn't take and Yes is clicked
  again, up to `MINIGAME_YES_TRIES` clicks; if the popup is gone and nothing teleported the start has failed at once - a logged failure and a respawn,
  no capture and no wait for a game that isn't there (run 6: Hauntlet 2's Yes clicked, the character still in the house 114 s later). Minigames are time-sensitive, so this also happens
  when the popup turns up while a handler is waiting in
  a handler's waits (see [Per-cycle checks while a task
  runs](#need-handlers)): the minigame is played right
  then and the handler abandoned (`TaskInterrupted`). Every held key and the mouse button are released first - at the start of `play_minigame()`, since the handler may have been holding one (a walk, the pet swipe) when it was interrupted, and again as the interruption is raised. Where it left the
  character is unknown afterwards, and the next check sees what is still
  needed.

*Playing.* The macro's own window is sent to the back while a minigame is played and brought back after it, whichever way it ends (it covers part of the screen, and would be in
the recording and in the readers' way). No needs can be seen while a minigame runs, so every minigame ends on the
GAME OVER! screen instead - a red GAME OVER! banner over a green NICE! button,
found by exact color (`detect_minigame_finished()`, checked every
(it is looked for on every look at the screen), which is the same for all of them (the tests use real, lossless screenshots of it for Hauntlet 2 and Ghost Gallery, one with the next game's countdown over the banner: `tests/popups/finish_screen_*.png`, `game_over.png`, and the crops `tests/fixtures/finish_banner.png` and `finish_button.png`; the banner is about eight times and the button about three times the pixels the checks ask for, so no threshold needed moving). Every run ends on it, so reaching it is *finishing*, not winning (a real win, which gives a crypt key, shows the same screen; the macro can't tell them apart): what a run earned is the candy corn on the card. `save_minigame_result()` saves that card (`MINIGAME_RESULT_BOX` of the screen) as `runs/results/run<N>_<minigame>_<time>.png`, the newest `MAX_RESULT_SHOTS` kept, before NICE! is clicked. While it plays, `minigame_popup_checks()` runs `dismiss_stray_windows()` (and `detect_paycheck()` if it closed nothing) at most every `MINIGAME_POPUP_CHECK_INTERVAL`, including during the start waits (`minigame_wait()`) - the in-task `unscrew()` can't, since the minigame is played from it. Then NICE!
(`MINIGAME_FINISH_BUTTON_POS`) is clicked and `MINIGAME_FINISH_WAIT` waited
out (`click_nice_until_gone()`, which looks again and clicks up to
`MINIGAME_FINISH_CLICK_TRIES` times while the finish screen is still up, logging a
failure if it never closes), and the character respawns. If the finish screen hasn't shown after
`MINIGAME_MAX_DURATION` it gives up with a logged failure (and releases every
input). A minigame that ran to the GAME OVER! screen counts as progress for the no-progress rejoin (`state.last_progress`); one that failed - never started, left early, timed out - does not, or a popup every few minutes would keep the no-progress rejoin clock (`NO_PROGRESS_REJOIN_INTERVAL`) from ever running out. Either way it counts as a detection (no needs can be seen while it runs).

*Telling whether it is on.* Each minigame has an overlay along the top of the
screen while it runs (Hauntlet 2: the ROOM counter; Ghost Gallery: the TIME
LEFT / GHOST GALLERY / Score banner; Stitches' Spotlight: TIME LEFT / the title / the Candy Corn count), recognised by a crop in
`ref/overlays/<minigame>.png` within its box in `MINIGAME_OVERLAYS`
(`minigame_overlay_visible()`, at least `MINIGAME_OVERLAY_THRESHOLD`). A
`MinigameWatch` made by `play_minigame()` is fed every screenshot the player
takes (`minigame_look()`, `minigame_wait()`): once the overlay has been seen,
losing it for `MINIGAME_OVERLAY_LOST_TIMEOUT` means the minigame was left
(game over without the finish screen, kicked out) and it stops there; never
seeing it within `MINIGAME_OVERLAY_START_GRACE` of the start wait means it
never started. Either is a logged failure with that reason, instead of waiting
out `MINIGAME_MAX_DURATION`. The watch also looks for the "Disconnected" dialog (`detect_disconnect()`) in every screenshot: once seen, the minigame stops at once
(logged as a `ended by a disconnect` event, not a failure - the rejoin that follows logs that), where it used to go on for the rest of its time with the dialog up.
- **The start is the overlay, not a time.** Rounds start at fixed times of the hour (in the captured day Hauntlet 2 at about :03:07, :23:07 and :43:07, Ghost Gallery at :12:46, :32:46 and :52:46, a round every 20 minutes each) and the popup's Yes is clicked
  12-52 s before one, depending on what the macro was doing (a need's end wait holds it back by up to `MINIGAME_POPUP_END_WAIT`). A fixed wait from the click is therefore wrong most of the time: in Hauntlet 2's first room the timer is 30 s and crossing the hall takes about 20, and of 21 runs with a fixed 50 s wait the 11 whose Yes came 40 s or less before the round
  were thrown out of room 1 (the macro still waiting) and the 10 that came 43 s or more before it played on. So each player looks for its game's overlay at a short interval (`MINIGAME_START_LOOK_INTERVAL`, `wait_for_minigame_start()`) and acts the moment it shows; `MINIGAME_START_WAIT` is only the most it waits (the overlay came 50.5 s after the Yes in one run), after which it acts regardless.
- `play_hauntlet_2()` waits until the game's overlay shows (at most `MINIGAME_START_WAIT`), then goes through the rooms - reads the screen, picks a door
  and walks to it, room after room - until the minigame finishes or until the overlay says it's over. See
  [Playing Hauntlet 2](#playing-hauntlet-2).
- `play_ghost_gallery()` waits until the game's overlay shows (at most `MINIGAME_START_WAIT`), then finds the ghosts on screen, aims the
  mouse at one and holds the button on it, and on the boss in the boss phase, until the minigame finishes. See
  [Playing Ghost Gallery](#playing-ghost-gallery).
- `play_stitches_spotlight()` waits for the overlay, then holds the walking key and keeps the camera on the middle of the ring until the minigame finishes. See
  [Playing Stitches' Spotlight](#playing-stitches-spotlight).

A handler only counts as resolved (and only then triggers a respawn) if
`handle()` returns `True`. `ButtonNeedHandler` returns `False` if
`refresh_button_mapping()` did not find all five buttons; `ChooseNeedHandler` returns `False` if it
can't find its exact-color button on screen. Either way the need is
logged and skipped for this pass with no respawn, same as a disabled need
- `process_needs()` doesn't distinguish between the two.

### Stuck needs

Needs are resolved one per check, so a need can sit through several checks
just waiting its turn - that isn't being stuck. What is: `record_detection()`
compares each check with the needs the *previous pass attempted*
(`state.attempted_last`). An attempted need still on screen has its streak in
`state.attempt_streaks` raised; one that's gone has it dropped. When an *enabled*
need's streak reaches `NEED_STUCK_CHECKS` - it's supposed to be getting
resolved, yet it never goes away - it's added to `state.disabled_needs` and
`get_need_handler()` returns `None` for it from then on, same as a need that
is switched off. A single check without it, even one that detects
nothing at all, breaks the streak.

`state.disabled_needs` lasts for the life of the process only - it isn't saved
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
2. *White screen* - at least `WHITE_SCREEN_MIN_FRACTION` of the screen has been
   completely white (every channel at or above `WHITE_SCREEN_LEVEL`,
   `detect_white_screen()`) for `WHITE_SCREEN_REJOIN_AFTER` in a row. The
   game shows a white screen for a moment on a respawn, teleport or change of
   location, so only a long one counts; a bright scene is nowhere near it (the
   real white transition in `tests/icon_detection/` is 93% white, every other
   screenshot under 10%). It is looked for wherever `rejoin_reason()` is
   asked, which is every cycle and every `UNSCREW_TASK_INTERVAL` mid-task, and
   the clock (`state.white_since`) restarts whenever the screen isn't white.
   (Needs stay visible on the white screen, so nothing else notices it.) It is the game's own doing and nothing the macro can help, so a rejoin that works is not a failure: the run log gets `white screen: ... - rejoining` and no screenshot, and the report counts them.
3. *Stuck* - `DISABLED_NEEDS_REJOIN_THRESHOLD` needs have been disabled as
   stuck.
4. *Stalled* - no need has been resolved for `NO_PROGRESS_REJOIN_INTERVAL`
   (`NO_PROGRESS_REJOIN_INTERVAL`; `state.last_progress`, set when the loop starts, whenever a need is
   resolved and by every successful `leave_and_rejoin()`): the game is up but
   nothing is working, whatever the cause.
5. *Scheduled* - it has been `REJOIN_INTERVAL` since the last
   rejoin (`state.last_rejoin`, set when the loop starts and by every successful
   `leave_and_rejoin()`): a refresh of a client that has been up a long time.

If one applies, `rejoin_game()` runs `leave_and_rejoin()` (see [Side actions](#side-actions)) -
without the clean esc/l/enter leave for a disconnect, since there's no game
to leave - then `setup_game()` (the rejoin resets the settings it sets), then
clears `state.disabled_needs` and the streaks so every need gets a fresh
chance, and the workflow carries on. (If the game is rejoined *again* while
setup runs - its in-task checks do that on a disconnect - the nested recovery has
already run setup, so the `TaskInterrupted` it raises is caught here and logged
as `setup interrupted by a rejoin`, rather than ending the run.) A disconnect, white-screen, stuck or stalled recovery counts as
a failure: `log_failure()` saves a screenshot first, which shows the dialog
and its error code. A scheduled rejoin isn't one: it's a line in the run log,
counted in `scheduled_rejoins`. Nor is Roblox not running when the run starts
(`State.run_start_pending`, cleared by the first top-of-cycle `rejoin_game()`):
that is where the run begins, logged as "Roblox isn't running at the start -
starting it" and not counted as a recovery or a failure. If the rejoin fails (`recovery: rejoin failed, will try again next cycle`, a logged failure) the cause is still
there, so the next cycle tries again. The top-of-cycle check looks for all
five reasons; during a handler only a disconnect or a white screen is acted on (see *Per-cycle
checks while a task runs* above), and the stuck, stalled and scheduled rejoins
wait for the next top of a cycle.

Separately from those, `respawn_if_no_detection()` (in `run_full_cycle()`, after
`process_needs()` found nothing to resolve) respawns the character when no
need has been *detected* for `NO_DETECTION_RESPAWN_INTERVAL` - it
may be stuck somewhere the icons don't show from. It counts from
`state.last_detection` (set whenever a need is detected, and when the loop starts, the game is rejoined or a minigame is
played), and again from each respawn, so it repeats at most once per interval.
It never touches `state.last_progress`, so the stalled rejoin above (`NO_PROGRESS_REJOIN_INTERVAL`)
still comes exactly when it would have: in a long stretch with nothing
detected the character is respawned every `NO_DETECTION_RESPAWN_INTERVAL` and the game rejoined at
`NO_PROGRESS_REJOIN_INTERVAL`. The log line is `no need detected for N min - respawning`.

### Side actions

None of `lure_collect()`, `set_new_lure()`, `tree_collect()` or
`setup_game()` are need handlers - they're plain functions, not
registered anywhere in `NEED_HANDLER_CLASSES` or switched on.
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
  than for a fixed time. Up to `REJOIN_MAX_ATTEMPTS` times:
  1. `close_roblox()` - if `clean_leave` (it's off when the game is gone or
     showing the Disconnected dialog, where Enter could press Reconnect) and
     the Roblox window is at least `REJOIN_MIN_LEAVE_HEIGHT` tall (the L
     shortcut needs it) press
     `KEY_SEQUENCES["leave_game"]` (esc, l, enter), then `taskkill` every process in
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
     (`GREEN_BUTTON_COLOR`) to show up around `REJOIN_JOIN_POS`
     (`detect_play_button()`).
  4. Wait `REJOIN_PLAY_SETTLE`, click it, wait `REJOIN_AFTER_JOIN_WAIT`,
     respawn and return `True`. A stage that times out starts the next attempt;
     after the last one it returns `False`.

  All of this waits with `wait_stoppable()`, which only [STOP] interrupts.
  `wait_interruptible()` also stops the run when Roblox loses focus, which is
  what used to kill the rejoin as soon as the game closed - there is no
  focused Roblox window while it restarts.
- **`lure_collect()`** - holds `a` for `LURE_COLLECT_WALK_DURATION`,
  presses `KEYS["interact"]` (`e`) to collect the current lure's rewards,
  waits `LURE_COLLECT_SETTLE_DELAY`, presses `KEYS["interact"]` again,
  waits `UI_SETTLE` for the placement panel, calls `set_new_lure()` (and gives
  up with False if that does), then respawns. `side_quest()` runs it - and
  `tree_collect()` - through `run_side_action()`, inside `task_unscrew()` like a
  need handler, so popups and stray windows are handled and a minigame or
  rejoin (`TaskInterrupted`) just means it's tried again next cycle.
- **`set_new_lure()`** - clicks `LURE_NEW_POS_1` then `LURE_NEW_POS_2`
  (`UI_SETTLE` after each - they used to follow the key press with no wait), the two clicks that place a fresh lure. Split out from
  `lure_collect()` specifically so this placement step can be tested and
  tuned on its own, without walking to the old lure first each time.
- **`tree_collect()`** - holds `d` for `TREE_COLLECT_WALK_DURATION`,
  then `s` for `TREE_COLLECT_BACKWARD_DURATION` to line up with the
  tree, then presses `KEYS["interact"]` and respawns (like `lure_collect()`).
- **`setup_game()`** - respawns, clicks `SETUP_LOCK_HOUSE_POS` to lock the
  house, then opens the backpack and clicks through
  `SETUP_BACKPACK_SETTINGS_POS` → `SETUP_SORT_MENU_POS` →
  `SETUP_FAVORITES_POS` → `SETUP_CONFIRM_POS` to set its item filter to
  favorites only, then closes the backpack and calls `disable_trades()`. It
first closes any stray window (up to `SETUP_STRAY_TRIES`; see [Stray
windows](#need-handlers)) and runs inside `task_unscrew()`.
- **`travel_to_trade_hub()`** and **`leave_trade_hub()`** - the Trading Hub trips of the Discord bot's `/trade_hub` and `/leave_trade_hub` (see [Discord bot](#discord-bot)): the clicks of a human's run (`TRADE_HUB_*`; `click_steps()` does a list of them with `UI_SETTLE` after each), the first opening the backpack with `KEYS["backpack"]`, equipping the Trading Hub Phone from the Toys and using it, the second GO HOME and REGULAR SERVER. They are not part of the loop.
- **`disable_trades()`** - sends the macro window to the back
  (`send_macro_window_to_back()`; the always-on-top panel covers the settings
  gear), clicks the six `SETUP_TRADES_*` positions (settings → settings menu →
  interaction tab → trading setting → "no one" → close) and brings the
  window back in a `finally`, so a stop mid-way can't leave it buried. The
  worker thread can't touch Tk directly, so `state.macro_window` (the GUI) runs
  the two window changes on Tk's thread via `run_on_ui_thread()`.

### Checking a need in parallel with movement

`walk_alternating()` (used by `ride` and `walk`) takes an optional
`need_name`. When given, it starts a background thread
(`_watch_need_gone()`) that checks every `NEED_GONE_POLL_INTERVAL` whether that need has cleared *while*
the character is still alternating back and forth, instead of only
checking once the full duration has elapsed - the main loop breaks out
early (via a shared `threading.Event`) the moment the background thread
finds it gone, rather than always running the full `total_duration`. The
background thread makes the same check as
`wait_until_need_gone()` (the shared `_need_cleared()` helper, which looks
twice - see
[Waiting for a need to clear](#waiting-for-a-need-to-clear)), just
interleaved with the movement instead of run after it.

This is the one place the macro scans the screen from two threads at once
(the other background threads - the GUI's `run_async()` worker, the stop-hotkey
poller, `ResourceMonitor` and `RunRecorder` - do their own, separate jobs). A second `threading.Event` tells the watcher to stop the instant
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
(a fresh `mss.MSS()` per screenshot, and no shared mutable state with the
main thread besides the two events and the need-strip cache, which
`detect_need_icons()` guards with a lock), which is what makes running it
alongside the movement safe.

## Stopping & focus safety

Two independent conditions can interrupt a running workflow, and both are
checked the same way:

- **`StopRequested`** - raised when `STOP_EVENT` is set (the user pressed
  **[STOP]**).
- **`FocusLost`** - raised when Roblox is no longer the focused window.
  Without this, a long-running action (a 75s walk, an 8s pet swipe, a
  wait between catch throws) would keep sending keypresses/clicks into
  whatever window the user actually switched to.
  Pressing the GUI's [STOP] button takes the focus from Roblox on
  mouse-down, a moment before the click sets `STOP_EVENT`, so a `FocusLost`
  raised right then waits up to `STOP_CLICK_GRACE` (`stop_pressed_soon()`) for
  the stop: if it comes, the run ends as an ordinary user stop rather than a
  "Roblox lost focus" failure.

**Why an exception and not killing the thread.** The stop is cooperative on
purpose. Python has no safe way to terminate a running thread from outside
(the closest trick, async-raising into it through `ctypes`, is explicitly
unsafe), and for a game macro that matters: a kill that landed while a
movement key or the mouse button was held would never release it, and it
would stay stuck down in the game. Instead `wait_interruptible()` and
`check_running()` raise `StopRequested` / `FocusLost` the moment they notice,
and ordinary exception propagation unwinds the stack up to `run_async()`'s
worker thread, which catches it. Every `try`/`finally` on the way (used
wherever a key or the mouse is held) still runs, so cleanup happens wherever
the stop lands, without each function checking a flag after every action.
`TaskInterrupted` is the same mechanism for a different cause: a minigame or a
rejoin took over the character, so the handler's waits raise it, the handler
is abandoned, and `process_needs()` catches it - the next check decides from
the screen what is still needed.

`wait_interruptible()` runs to a deadline, so the time the in-task checks take counts
towards the wait (a key held for a timed step is held for that long, not longer).

`check_running()` checks both (`check_stop()` then `check_focus()`) and is
the single checkpoint used everywhere: inside `wait_interruptible()` (so
essentially every delay in the macro is a checkpoint), and in any manual
loop that doesn't otherwise call it (e.g. `PetNeedHandler`'s swipe
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
`finally` block as a last line of defense. Every key the macro holds is pressed with `key_down()` and let go of with `key_up()`, which keep the keys that are down in `HELD_KEYS` (`tools/check_constants.py` fails on a bare `pydirectinput.keyDown`), so `release_all_inputs()` lets go of exactly those, and of the fixed list `cfg.HELD_KEYS` as well (the movement, helicopter, zoom, jump, respawn and camera-turn keys), also when the exit comes in the middle of a hold; a key that raises when let go of (the fail-safe) does not stop the others. The releases skip PyDirectInput's fail-safe check (`unchecked()`: the wrapped call that `functools.wraps` keeps), because with the cursor in a corner - the fail-safe's sign - every checked call raises and what the macro holds would stay held after the very stop that corner asked for; the fail-safe itself stays on for everything else, and the cursor is not moved. A stop releases everything **at once** (`stop()` calls `release_all_inputs()` itself, from the thread that asked, without waiting for the worker to notice), and `key_down()` and `mouse_down()` refuse (`StopRequested`) to press anything once the stop flag is set, so the worker cannot press one more key in the gap. It releases a mouse button only if `pressed_inputs()` says it is down: a button-up with the button not pressed is a click to the window under the mouse, and a right one over Roblox's border or title bar opens the window menu and takes the focus (seen at every minigame popup, whose `play_minigame()` releases everything first, the mouse being wherever the interrupted handler left it).

**Resuming after focus loss.** The GUI's **Resume after focus loss** checkbox
(on the Settings tab; the `resume_on_focus_loss` config switch, off by default - stopping is the
right thing when you're using the computer yourself) makes the loop carry on
instead of ending. When `FocusLost` reaches `run_workflow_loop()` with the
switch on, it releases every held input, saves a failure screenshot (which
shows what took the focus), waits `FOCUS_RESUME_DELAY`, takes Roblox back with
`focus_roblox_click()`, respawns (a half-finished handler leaves the character
somewhere unknown) and starts the next cycle. After `FOCUS_RESUME_MAX_IN_A_ROW`
`FOCUS_RESUME_MAX_IN_A_ROW` focus losses with no need resolved in between it gives up and stops as
usual, so it can't fight you for the window forever. The switch is read at the
moment of each loss, so it can be flipped while the loop runs. Resumes are
counted in `focus_resumes` in `status.json`.

**The stop hotkey.** Pressing `HOTKEYS["stop"]` in *any* window stops a running
macro, so you can take over the computer without reaching for the GUI (which
also has a hint label saying so). `AdoptMeGUI.watch_stop_hotkey()` runs a small
daemon thread that polls the key every `STOP_HOTKEY_POLL_INTERVAL` through
`hotkey_down()` (Windows' `GetAsyncKeyState`, so it needs no extra package and
works while another window has the focus). Only the key going *down* counts, so
holding it doesn't retrigger, and it does nothing while nothing is running. It
hands the stop to Tk's thread, which does exactly what the [STOP] button does
(sets `STOP_EVENT` (a `threading.Event`: the waits sleep on it, so a stop is noticed at once), so the run unwinds at the next `check_running()`, within
`STOP_CHECK_INTERVAL`, releasing any held key on the way) and logs "stopped with
the P key". It stops the run for good - **Resume after focus loss** only catches
`FocusLost`, not a stop. Because it's global, typing the stop key anywhere (including in
the private server link box) while the macro is running stops it too.

This is deliberately *not* solved with a second "killer" thread that
force-stops the running one from outside - Python has no safe way to do
that, and the unsafe tricks that exist (async-raising into another thread)
can land mid-action and leave a key stuck down in the actual game, which
is worse than the small delay this cooperative approach costs instead.

## Persisted game config

`GAME_CONFIG_PATH` (`debug/.config`, a JSON file) is the macro's one piece
of state that survives separate launches of the script - everything else
either lives in `ref/needs/` (the learned reference icons) or is cheap to
redetect fresh, like `state.button_positions`: detected every single time the
character walks to the buttons, so there's nothing gained by saving it -
it's never trusted across a run anyway, and a stale save would just be
actively wrong if the screen resolution or Roblox's UI ever changed
between sessions.

`load_game_config()` returns these defaults merged with whatever's
actually on disk (so an old config missing a newer key still works), and
`save_game_config()` overwrites the whole file:

- **`money_collected`** - how much `tree_collect()` has yielded so far,
  in `TREE_HARVEST_YIELD` increments. It goes up with each harvest and
  is put back to 0 every day at `MONEY_RESET_HOUR` by
  `reset_money_if_due()` (see `side_quest()` below), or by the Debug tab's
  **Reset Config** button.
- **`money_reset_boundary`** - the last daily reset moment already dealt with
  (a timestamp; `None` on a fresh or older config). `side_quest()` compares it
  with the most recent `MONEY_RESET_HOUR` o'clock (`last_money_reset_time()`): a later one means the
  total is reset and the new moment saved. The first time it is seen (`None`) it
  is only recorded, so updating doesn't wipe the day's total.
- **`tree_timer`** / **`lure_timer`** - the timestamps `tree_collect()` /
  `lure_collect()` are next due. Both default to "due immediately"
  (`time.time()`) when the config is fresh.
- **`side_quest_enabled`** - on/off switch (default on) for
  `side_quest()`. It's a checkbox in the GUI, and can be flipped while a
  workflow runs.
- **`hauntlet_2_enabled`**, **`ghost_gallery_enabled`**, **`stitches_spotlight_enabled`** - the **Play ...** checkboxes on the Settings tab (all off by default); see Minigame popups under Need handlers.
- **`resume_on_focus_loss`** - the **Resume after focus loss** checkbox (default
  off); see [Stopping & focus safety](#stopping--focus-safety).
- **`needs_enabled`** - `{need: on or off}`, the switches next to the Test buttons on the Debug tab's Needs subtab. A need that isn't in it
  follows `ENABLED_NEEDS`, so by default they are all on; **Reset Config** puts them back.
- **`record_next_run`** - the **Record the next run (low quality video)**
  checkbox on the Debug tab's Options subtab (default off); see [Recording a run](#recording-a-run). Cleared, and
  the checkbox unticked, when the run that it was for starts.
- **`blackout_hotkey_enabled`** - the **H blanks the screen in Roblox (off: type it)** checkbox on the Debug tab's Options subtab (default on; the label names
  `HOTKEYS["blackout"]`). Off, the key does nothing here and can be typed in the game, and a blank screen is taken away when it is turned off; see
  [Blanking the screen](#blanking-the-screen).
- **`private_server_link`** - the private server link `leave_and_rejoin()`
  joins, typed into the GUI's entry box (saved when you press Enter or click
  away). It lives here rather than in `magic_numbers.py` because it's a key
  to your server and the source is in a git repo. **Reset Config** keeps it.

During a run, this file is read and updated by:

- **`side_quest()`** - called right after `unscrew()`, every cycle of
  `run_full_cycle()`'s loop (so on the same cadence as the paycheck
  check); first applies the daily money reset (`reset_money_if_due()`), then does
  nothing if `side_quest_enabled` is off. While
  `money_collected` is below `MONEY_COLLECTED_TARGET` *and*
  `tree_timer` has passed, it calls `tree_collect()`, adds
  `TREE_HARVEST_YIELD`, and pushes `tree_timer` `TREE_CHECK_INTERVAL`
  (`TREE_CHECK_INTERVAL`) out. Once `lure_timer` has passed, it calls
  `lure_collect()` and pushes `lure_timer` `LURE_RECOLLECT_INTERVAL` out. Each action only updates the config if the call actually
  returned `True`, and does so immediately - so stopping partway through
  doesn't lose progress already made.

The GUI (Tk's main thread) and the workflow (`run_async`'s worker thread)
both read and write this file, so every access holds one lock
(`GAME_CONFIG_LOCK`): a change goes through
`update_game_config(mutate)` - a load-modify-save under it - or
`reset_game_config()` (deletes the file, so the next load is all defaults;
the GUI then refreshes its checkboxes to match), and `load_game_config()`
takes it too, so a reader can't see the file mid-write (which reads as "no
config", the defaults: an empty private server link, the minigames off).
`save_game_config()` writes a temporary file and moves it into place, so a
crash partway can't leave a truncated config either. `side_quest()`
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
  same path back to a usable UI. A `TaskInterrupted` (the Setup button's
  `setup_game()` cut short by a disconnect, whose rejoin ran setup itself) is
  logged and counts as done, not as a crash. `final_status` is bound before the `try`, so even a failing log write inside an `except` can't leave the buttons disabled. Closing the window (the X) stops the run and releases every key and the mouse first (`AdoptMeGUI.close()`), since the daemon worker would otherwise die with a key still held; the stop hotkey stops before it logs. "Refresh debug" skips an entry it can't delete (a video still open) instead of abandoning half the folder. `[STOP]` is deliberately excluded from
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
- The **Main** tab has the loop/stop row and the Respawn, Setup, Leave &
  rejoin and Trading Hub (go, leave) buttons; the **Settings** tab holds the persisted switches (Run side
  quest, Resume after focus loss, Play <minigame>) and the private server link;
  the **Debug** tab has two subtabs: **Options** (Record the next run, Show debug
  lines, Refresh debug, Reset Config) and **Needs** (a button that tests every need
  in turn, and one per need handler).
- **`run_on_ui_thread(func, timeout)`** is how the worker thread touches Tk
  (which is only safe from its own thread): it queues `func`, waits until it
  has run or `timeout` seconds pass (`None` = as long as it takes, for a
  dialog such as the new-need name prompt) and returns what it returned.
- **`watch_stop_hotkey()`** starts a thread that watches two hotkeys (holding one doesn't retrigger; `hotkey_fires()` decides):
  `HOTKEYS["stop"]` stops a running macro, in any window (it
  does nothing while nothing runs), so you can take over the computer without
  reaching for the GUI, and `HOTKEYS["capture"]` starts or stops an
  [input capture](#input-capture), only while Roblox is the focused window. The action is handed to Tk's thread.
- **`add_config_checkbox(key)`** makes a checkbox for a persisted switch. It is
  deliberately not in `action_buttons`: the switches stay usable while a
  workflow runs, which is safe because `update_game_config()` is locked.
- **`DebugCapture`** is a `logging.Handler` on `logger` for the lifetime of the GUI: every record logged anywhere in the macro shows up in
  the on-screen console (`self.debug_text`) - `emit()` only queues (from any thread) and the Tk thread puts the queue on the widget every
  `CONSOLE_DRAIN_INTERVAL_MS`, since Tk isn't thread-safe - each line starting with the time (`[HH:MM:SS]`), in the colour of its level
  (`GUI_CONSOLE_LEVEL_COLORS`: the debug lines are dimmer). The **Show debug lines in the output** checkbox on the Debug tab's Options subtab raises the handler's level to INFO to
  leave them out (`output.log` keeps them either way). The widget keeps only its last `CONSOLE_MAX_LINES` lines (`trim()`) - it would otherwise
  hold everything logged, about 25 MB of memory per 100,000 lines - while `output.log` keeps all of it. A record with `extra={"status": True}` is a
  status line: it replaces the previous status line instead of adding one, and goes into `output.log` only the first time (this is the
  "Waiting for a need..." counter).

### Input capture

Press `HOTKEYS["capture"]` while Roblox is the focused window to start recording and again to stop. It is for working out
how something is played by hand - the Hauntlet 2 rooms, say - and gives the macro's author lossless
frames and the inputs that went with them. It is separate from **Record the next run** (a low-quality
video of a macro run): it works whether or not the macro is running, and the macro's own key presses
and clicks are recorded too, since `GetAsyncKeyState` sees them like any others.

Each capture is a folder in `debug/captures/` (`CAPTURES_DIR`, git-ignored, emptied by **Refresh debug**;
the newest `MAX_CAPTURES` are kept) named `capture_<date>_<time>`. **Getting one to someone else:** when a
capture stops, `zip_capture()` (on a thread of its own) also splits it into zips of at most `CAPTURE_ZIP_MB`
(chat attachments are limited to 30 MB) in the capture's `zips/` folder:
`capture_<time>_part01.zip`, `_part02.zip`, ... The frames are in time order, none twice; part 1 also holds
`events.jsonl` and `info.json`. They are stored, not compressed (PNGs don't shrink), so a part holds only a
few frames. Attach them all (send them in order); a zip arrives byte for byte, unlike a pasted image, which
is recompressed. For a zip that already exists (a capture zipped by hand, say) `python tools/split_zip.py big.zip` cuts it into
`big_part01.zip`, ... of at most 25 MB (or the number of MB given as a second argument), every file kept whole. The files themselves stay in the folder:

- `frame_<n>_<seconds>.png` - `grab_screen()` (the Roblox window at the reference size, the macro's own
  window included if it is on top), lossless. The screen is looked at every `CAPTURE_FRAME_INTERVAL`
  (`CAPTURE_FRAME_INTERVAL`) and right after every mouse click, but a frame is only **saved if it differs from the last one
  saved**: at least `CAPTURE_CHANGE_FRACTION` of the picture, shrunk to 320x180, has moved by more
  than `CAPTURE_CHANGE_LEVEL` grey levels. A character walking or a door opening does that; a ticking
  timer or a blinking light doesn't. A click always gets a frame, and so does every `CAPTURE_MAX_SKIP`
  without one, so a gap in the numbers means nothing changed, not that the capture stalled. `<seconds>` is
  on the same clock as the events.
- `events.jsonl` - one JSON object per line, in time order, `t` being seconds since the start:
  `key_down` / `key_up` (`key`: `w`, `space`, `shift`, `f1`, ... - the names in `VK_NAMES`),
  `mouse_down` / `mouse_up` (`button`: `left`, `right`, `middle`, `x1`, `x2`, with the position),
  `move` (the position, written at most every `CAPTURE_MOVE_INTERVAL`) and `frame` (`file`).
  Positions are both `x`, `y` (screen pixels) and `rx`, `ry` (the reference space everything in
  `magic_numbers.py` uses, `to_reference()`). The capture hotkey itself is left out.
- `info.json` - when it started and stopped, the screen and Roblox window sizes, the frame (and skipped-frame) and event
  counts and why it stopped.

**Minigame runs are recorded automatically.** `play_minigame()` starts a capture of its own after the teleport that the popup's Yes brought
(`start_run_capture()`) and stops it when the run is over, whichever way it ends - finished, left, timed out or stopped
(`finish_run_capture()`, which also writes `outcome` into `info.json` and zips the capture as above). It is the same recorder, so it
has the macro's own key presses and clicks and the frames on the same clock, but in `debug/minigame_captures/`
(`RUN_CAPTURES_DIR`, named `<minigame>_<date>_<time>`, then renamed by `finish_run_capture()` with what the run earned and how it ended: `_<N>cc` or `_cc-unread`, `_room<n>` for Hauntlet 2, `_failed` or `_failures`; `run_capture_suffix()`) so the hotkey's captures and these
never delete each other, and with a **budget of `RUN_CAPTURE_ZIPS` zips**, spread over the whole run: the
zips are counted as `zip_capture()` will make them (`plan_zip_parts()`, the events in the first). A lossless frame is 1-3 MB, so
the budget buys some tens of frames; when a run would need more zips, `InputCapture._keep_within_budget()` deletes
every other saved frame - never the newest, and the first stays - and from then on saves frames twice as far apart, so what is
left is spread evenly over the run however long it is. It is checked after every saved frame and once more when the capture
stops (the events have grown by then). The events of the deleted frames stay in `events.jsonl`, followed by a `frame_dropped`
line naming the file, so a reader can tell; `info.json` has `frames_dropped` and `max_zips`, and `stopped_because` is "the minigame
ended". The folders are big (the frames and then the same again as zips), hence **only some are kept**: after each run `prune_run_captures()` keeps, of each minigame, the
`RUN_CAPTURES_BEST` runs that earned the most candy corn (see [Candy corn](#candy-corn)), the `RUN_CAPTURES_WORST` that earned the least (a run whose total wasn't read counts as the worst), the latest, and the `RUN_CAPTURES_FAILED` latest that failed - a run that did not finish, or had a failure logged while it ran (`failures` in its `info.json`, `_failed` / `_failures` on its
folder's name) - going by the `earned`, `finished` and `failures` that `finish_run_capture()` wrote into each `info.json`.
**Frames of when things went wrong are kept and not thinned out.** `mark_run_capture(reason)` (`InputCapture.mark()`) writes a `mark` line with the reason into `events.jsonl`, saves a frame at once and then every look for
`RUN_CAPTURE_BURST` seconds, however far apart the thinning has put the others - and those frames are `protected`, which `_keep_within_budget()` leaves alone (at most `RUN_CAPTURE_PROTECTED` of them, or they would use up the zips).
It is called when a failure is logged (`log_failure()`), when a Hauntlet 2 walk gets nowhere, when the player is found not to be moving (`hauntlet_unstick()`) or a fork's doorways aren't seen, when a Ghost Gallery
beam on a ghost scores nothing or the score stalls, and when a stray window or the paycheck popup is closed mid-minigame. A run that did not finish also keeps the frames of its last `RUN_CAPTURE_TAIL` seconds
(`protect_tail()`), where the end of a run that went wrong is. Switch it off with `MINIGAME_CAPTURE`; it isn't started while the
hotkey's capture is running (two recorders would only get in each other's way), and a capture that can't be started or finished is a
message, never a reason not to play. If more frames per run are wanted the choice is more zips or lossy frames (which would no
longer be the exact pixels the readers are tuned on).

Keys and mouse buttons are read by polling `GetAsyncKeyState` every `CAPTURE_POLL_INTERVAL` -
the same call as the stop hotkey, so no extra package is needed - and the position with `GetCursorPos`.
The cost of polling is that **the mouse wheel isn't seen** (no async state to read) and a key pressed
and released between two polls is missed. A capture stops by itself after `CAPTURE_MAX_MINUTES` or
when less than `CAPTURE_MIN_FREE_MB` of disk is free - a frame is roughly 1-3 MB; how many are saved depends on how much moves, but at worst two a second - and the next
press of the hotkey then tidies it up and starts a new one. Screenshots and input are written from two
threads of the capture's own; the hotkey is watched by the GUI's hotkey thread (with the stop hotkey) and
acted on in Tk's thread, so typing the capture key into the private server link box starts a capture too.

### Blanking the screen

Pressing `HOTKEYS["blackout"]` while Roblox is focused (the GUI's hotkey thread, acted on in Tk's thread like the capture key) blanks the screen with a black window
(`Blackout`), and pressing it again takes the window away. The window is full-screen and topmost, **click-through** (`WS_EX_TRANSPARENT`, so the macro's clicks
reach the game), **never takes the focus** (`WS_EX_NOACTIVATE`, so the macro's focus checks are unaffected) and **left out of screenshots**
(`SetWindowDisplayAffinity` with `WDA_EXCLUDEFROMCAPTURE`, Windows 10 version 2004 and later). So the macro's own screenshots, the recordings and captures
(which all use `grab_screen()`) and the Discord bot's `/screenshot` and `/rview` see the game, not the black window; a recorder of another program (OBS, a call)
would see the black.

It is only turned on after a test (`Blackout.show()`): the window is first shown white for `BLACKOUT_SETTLE` seconds and the screen is grabbed; if the grab is
`BLACKOUT_SEEN_RISE` grey levels brighter the screenshots see the window and it is taken away again with a warning, and a screen already brighter than
`BLACKOUT_TOO_BRIGHT` cannot be tested (`blackout_seen_by_capture()` is None) and is refused the same way (try again from a darker view). A Windows that refuses
the capture exclusion gets the same warning. Only then is the window made black. The test shows as a brief white flash. It does not dim the backlight, and the
window goes with the process (`close()` takes it away first). Typing the hotkey in Roblox is the only way to toggle it, while the Options subtab's checkbox has it on (`blackout_hotkey_enabled`); the cursor is hidden over it.
The control window is blanked with the rest of the screen: while the screen is blank the black window is put back at the top of the always-on-top windows every
`BLACKOUT_RAISE_INTERVAL_MS` (`Blackout.keep_on_top()`, `SetWindowPos` with `HWND_TOPMOST` and no activation), because the control window coming back up from the
taskbar lands above it and Windows decides when, so a single raise is either too soon or too late.

### Reading Hauntlet 2

`read_hauntlet_room()` (the HAUNTLET 2 READER section of `main.py`) returns the number on the ROOM panel
at the top of the screen, or `None` when it can't be read. It only reads: nothing here decides or presses a key.
The panel is flat UI that never moves or scales, so the digits are matched exactly: the white pixels
(`HAUNTLET_DIGIT_WHITE_LEVEL`) inside `HAUNTLET_ROOM_BOX` are split into connected pieces, each piece is cut
out, shrunk to `HAUNTLET_DIGIT_SIZE` and compared with the saved digit pictures in `ref/hauntlet_2/digits/`
(`0.png` ... `9.png`, cut from a real run) by mean difference. The best must be under
`HAUNTLET_DIGIT_MAX_DIFF`; on a 256-frame run (rooms 1-29, three layers of the hotel) every readable frame
matched at 0.0 and the two most alike digits are 43.7 apart. No panel (the white transition, the lobby, a
door close-up that hides it), no digit pieces, more than `HAUNTLET_ROOM_MAX_DIGITS`, or a piece that matches
none of the digits all give `None` rather than a guess. `tests/test_hauntlet_2.py` runs a crop of each of the 29
rooms (`tests/hauntlet_2/room_panels/`) back through the reader.

**Items.** `read_hauntlet_items()` returns the item slots along the bottom of the screen, left to right, as
`HauntletItem(name, count, pos)`. The slots are round white-ringed icons about 115 px apart, found as circles in
`HAUNTLET_SLOT_BAND` (`find_item_slots()`, Hough, radius `HAUNTLET_SLOT_RADIUS`; a slot that has just changed is a
little bigger). Each slot's `HAUNTLET_ITEM_CROP`-px square is matched by correlation, as a grey picture so the slot's
bright blue or dim teal background matters little, against the pictures in `ref/hauntlet_2/items/<name>/` (a folder per
item, several pictures each - bright, dim, slightly enlarged). The best must reach `HAUNTLET_ITEM_MATCH` or the
item is `unknown`. They were cut from the 382 slots of a 256-frame run: `phone`, `key`, `gold_key`, `cyan_potion`,
`heart_potion`, `red_potion`, plus `backpack` (the lobby's button, which sits where a slot would and is left out,
`HAUNTLET_NOT_ITEMS`); every slot of that run is read as the item it is (60 ms a frame). The wand was not in it, so
it has no pictures yet. A slot that matches none of them reads as `unknown`; nothing is saved. The count is the number
on the purple "x2" badge at a slot's top left (`read_item_count()`): the badge is the purple piece of the right size
(`HAUNTLET_BADGE_*`), its white pieces are the "x" and the digits, each compared with the glyphs in
`ref/hauntlet_2/counts/` (`x`, `1`, `2` - the only counts in the run, so no other digit is known yet: a glyph that
matches none gives `None`, not a guess).

**Hearts.** `read_hauntlet_hearts()` returns `HauntletHearts(red, black, width, pos)` for the row above the player's head.
Hearts are pink with a black outline and become solid black when lost. The pink ones are found by colour and shape
(`pink_heart_pieces()`; nothing above `HAUNTLET_HEART_MIN_Y`, which keeps the room panel's pink spot out), the row nearest
the middle of the screen is the player's, and the row is then followed outward one `HAUNTLET_HEART_PITCH` x a heart's
width at a time: a cell with a heart-shaped piece or mostly pink is red, a cell that is nearly all pure black (every
channel at most `HAUNTLET_HEART_BLACK_LEVEL` - a doorway or the player's clothes are not that black) is black, anything
else ends the row. A row shorter than `HAUNTLET_HEART_MIN` or longer than `HAUNTLET_HEART_MAX` isn't taken for hearts
(`None`). Across the 256 frames of the run the counts go 3/0, 2/1, 3/1 (a red heart picked up), 2/2, 1/3, 4/0 (a heart potion),
3/1, 2/2, 1/3 and no frame is wrong in between except where it gives `None`. What it does **not** do: find a row of
*only* black hearts (nothing pink to find it by - the run's last frames), see the cyan temporary heart (it ends the row),
or read a frame where the row is hidden (a door close-up, a transition) or where something pink sits next to it (one frame
in the run, a pink door, was declined as too long). One frame can still be wrong or missing, so whatever acts on the hearts should
look at a few frames in a row.

**The timer and the doors.** `hauntlet_timer_visible()` says whether the timer ("00:2x", left of the ROOM panel) is shown:
at least `HAUNTLET_TIMER_MIN_WHITE` white pixels in `HAUNTLET_TIMER_BOX` when it is, none when it isn't - it is hidden in a room's first moments and
while the player passes through a door, which is also when the screen still shows the *last* room's doors. `find_hauntlet_doors()`
finds the three doors of a room from the TVs above them: each TV shows its number (the players standing before that door)
in a light cyan (`HAUNTLET_TV_GLOW`) that no other pixel of the game has in that quantity; `find_door_tvs()` takes the pieces
of `HAUNTLET_TV_AREA` px (except those in `HAUNTLET_TV_HUD_BOX`, where the room panel and timer glow) and wants three of them in a row (the same height within
`HAUNTLET_TV_ROW_TOLERANCE`, evenly spaced within `HAUNTLET_TV_SPACING_TOLERANCE`), else `[]` - a fork's hall has no TVs, and neither do
the close-ups. **With the camera close** - which is how it looks once the macro stands still in a room - the middle TV is above the top of
the screen or behind the room panel, and only the outer two show; two TVs `HAUNTLET_TV_PAIR_GAP` apart (the real gap was about twice the neighbours') and centred on the
screen (`HAUNTLET_TV_PAIR_OFF_CENTER`; the player stands in front of the middle door) are taken for the outer two, the middle one halfway between. Off centre
they are also taken for the outer two when their gap is `HAUNTLET_TV_PAIR_RATIO` times the side of a number's blob or more (the gap between neighbours is a
fixed multiple of that side, half the outer two's): a balloon in front of the middle TV hid its glow in room 22.
Two neighbours (`HAUNTLET_TV_SPACING` apart, level, no lower than the panel) with the third where the room panel is (`HAUNTLET_TV_HUD_BOX`, whichever side puts it there) give that third: the panel hides its glow.
Two neighbours with the third off screen are 350-780 px apart but not centred, and their gap is too small for their size, so they don't pass. The spacing between TVs (330-410 px as the camera zooms) is the picture's scale, so the boxes under each
TV (`HAUNTLET_LOCK_LIGHT_BOX`, `HAUNTLET_DOOR_BOX`, measured at a spacing of `HAUNTLET_DOOR_SPACING`) are scaled with it.
`HauntletDoor(x, y, kind)` gives each door's `kind` from its pixels (`hauntlet_door_kind()`), the first that fits:

- `locked` - the padlock's red light above the door: a patch of pure red pixels (`HAUNTLET_LOCK_LIGHT_RED` is the red level they must pass), none on other doors. The
  dark red outline of the player's hearts, which can be under the door, is a darker red and doesn't count. The light is one small round patch
  (`HAUNTLET_LOCK_LIGHT_MIN` to `HAUNTLET_LOCK_LIGHT_MAX` px, `HAUNTLET_LOCK_LIGHT_ROUND`): a wall painted red beside a door (room 22) is bigger and long;
- `shadow` - a shadow door: dark smoke over the boards with **glowing eyes** (white-pink ovals, a few px each; `hauntlet_shadow_eyes()`). A near-white piece (`HAUNTLET_SHADOW_WHITE`, `HAUNTLET_SHADOW_AREA`) in `HAUNTLET_SHADOW_BOX` whose surrounding ring is dark (mean brightness at most `HAUNTLET_SHADOW_RING`: 46-49 for the eyes of the captured room 6, 73 and more for every other white thing in a door's box - screws, lights, the lock's glare). It is looked for before `dark`, since an opened shadow doorway is dark too. Before it, a shadow door read `boarded` (its smoke and boards looked like planks): in the four captures looked at, 84 frames with doors, only the shadow door's two frames read `shadow`. It is recognised only so far; nothing is done with it yet;
- `dark` - over `HAUNTLET_DARK_FRACTION` near-black pixels (44-74% on the doors that look so, at most 37% on the others);
- `mystery` - the pale grey "???" door, brightness 180-203 where every other door is under 164;
- `boarded` or `plain` - by the planks: the share of the door's pixels on a strong, near-horizontal edge
  (`HAUNTLET_PLANK_*`: the grey picture smoothed, Sobel, edge directions within `HAUNTLET_PLANK_ANGLE` degrees of horizontal). Plain doors
  are 0.03-0.13 (median 0.06), boarded ones 0.11-0.24 (median 0.18); the threshold, `HAUNTLET_PLANK_FRACTION`, is the best one over the
  doors of 25 rooms (the first and second layers separate best, the third a little worse), and picked in turn without one
  room of them it still gets 94% of that room's doors right. It was found by trying measures one after another - blob counts
  for the nails, edge density, edge directions, colour - and this was the one that held across the three layers' lighting.

All of it is judged against what was seen in one captured run, by eye: of all three doors of those 25 rooms, 63 of 75 come out as
labelled (`boarded` / `plain` from the planks are the least sure part: 3 of 22 plain doors read boarded; and
the third layer's spiked doors, 3 of them, come out `boarded`, there being no kind for them yet). The misses: dark doors with
fewer black pixels than the rule wants (4 of 10 read as boarded or plain), and a lock whose light is cut off by the shear of a side door
(2 of 14). It also can't see which door has the ghost behind it. The kinds are for the decision logic to use as far as they go.

### Playing Hauntlet 2

`play_hauntlet_2()` (once the overlay has shown - `MINIGAME_START_WAIT` at most - and a `zoom_out()` - `KEYS["zoom_out"]` held `MINIGAME_ZOOM_OUT_DURATION` seconds: with the camera close the TVs are cut off by the top of the
screen and the doors can't be found; again before each walk into a room whose doors weren't found; then, after `HAUNTLET_START_DELAY` seconds for the entrance hall's doors to open, a first step forward of `HAUNTLET_START_FORWARD`, none if that is 0) takes a screenshot every `HAUNTLET_LOOK_INTERVAL` and hands it to a
`HauntletRun`, which keeps the room number and when it changed (`read_hauntlet_room()`) and says through `look()` when it is time
to choose, returning the doors to choose from. The number changes as the player passes through a door, while the last
room's doors are still on screen (open, the camera following the player), so the doors are only trusted when

- `HAUNTLET_ROOM_SETTLE` seconds have gone by since the number changed,
- the timer shows (`hauntlet_timer_visible()`), and
- the row of TVs is where it was on the look before, within `HAUNTLET_TV_STABLE` px - the camera has stopped (it zooms
  and pans at the start of a room; the macro stands still, so it does) - or `HAUNTLET_DOORS_TIMEOUT` has gone by.

A room whose number is a multiple of `HAUNTLET_FORK_EVERY` (rooms 10 and 20 were forks in the captured run; that every such room is one is an assumption) with no row of TVs on two looks in a row is a fork: two doorways. **Room 1 is the entrance hall** ("Welcome to the Hauntlet
Hotel": one doorway straight ahead, no TVs, the timer already showing; in both captured runs the doorway leads into a room with three
doors that is still numbered 1), and any other room without TVs for `HAUNTLET_DOORS_TIMEOUT` is treated the same way: a single
doorway ahead, walked to by holding forward only (no strafe) until a row of TVs is on screen (`hauntlet_walk(until_doors=True)`) or the
number changes, after which the room is looked at at once. For a room other than 1, `no doors found in room N` is
logged once per run as a failure, which keeps a screenshot of what the TVs were not found in. A white screen (a ghost took a heart, or the player died: the game whites the screen out and brings the player back to the room's start, `detect_white_screen()`) starts the room's clock over (`HauntletRun.look()`), so a room entered long ago and then reset is not taken for one without doors; `tests/hauntlet_2/white_after_a_ghost.png` is a real frame of it. After a walk the room is left alone for
`HAUNTLET_RETRY_WAIT` seconds and then chosen from again if its number hasn't changed - the retry for a walk that got stuck.

**The circles.** In front of every door there is a **purple circle** on the floor (a lilac ring, `find_door_circles()`: the ring-shaped pieces of `HAUNTLET_CIRCLE_HSV`, `HAUNTLET_CIRCLE_*`; badges and labels are smaller and don't count): **stepping into one picks that door**, and the player is then **held in place for a few moments**. `hauntlet_strafe()` therefore walks to the circle's middle, not to the TV above the door (in an angled room the two are far apart), when all three are seen. The circle the player stands on is mostly hidden: two circles seen and the player (the hearts) where the third would be is **standing in it** (`player_in_circle()`, `HAUNTLET_IN_CIRCLE`). `hauntlet_walk()` then notes the door as picked (`HauntletRun.picked`, until the room changes), and for `HAUNTLET_LOCK_WAIT` seconds does not take the still view for being stuck - a step sideways after the lock would take the player out of the doorway. A retry in a room with a door picked goes to that same door (`hauntlet_choose()` returns it at once, with no items used and no other door avoided): the choice was made when the circle was stepped in.

**A fork's doorways.** A fork has no TVs to find its doorways by. `find_fork_doorways()` finds their openings: each is dark inside (`HAUNTLET_OPENING_HSV`) and begins right under the wall's pale stripes (`HAUNTLET_WALL_HSV`), where everywhere else between the wall and the floor there is the gold wainscot, so in each column the first dark pixel with wall just above it is a column of an opening (`HAUNTLET_OPENING_*`); exactly two openings of the right width, the right distance apart, are the doorways. Seen at an angle, the far doorway can be lit inside and show only as a narrow piece of dark (`HAUNTLET_OPENING_PIECE`): one whole opening no wider than `HAUNTLET_OPENING_MAIN_MAX` with exactly one such piece `HAUNTLET_OPENING_PIECE_GAP` from it are taken as the pair, the piece's middle being a little off the doorway's (the aim is looked at again before each step). That only works with the camera near the wall: from the room's entrance at most one is seen (the other shows the next hall's gold panelling, not a dark inside). Over the three captured forks (rooms 10 and 20) it found both doorways in 19 of 47 frames - those from the wall; the others were from the entrance or with the player pushed about - and nowhere else but a frame of the lobby of the ordinary game, over 350 frames. So a fork is walked in two parts: `play_hauntlet_2()` holds forward until both doorways are seen (`hauntlet_walk(until_doorways=True)`, at most `HAUNTLET_WALK_MAX` seconds), then strafes by `hauntlet_strafe()` as in a room, the two doorways being taken for two door spacings apart (a room's outer doors) and the player's place the hearts; if they are never seen, what is left is the fixed strafe below.

**Getting stuck.** The room timer runs out after a while and the run is over, so a stuck player has to do something else, quickly. A walk that ended with the
number unchanged counts in `HauntletRun.stuck`; the next one (`hauntlet_walk()`) steps back first (`KEYS["back"]`, `HAUNTLET_BACKUP_DURATION`) and avoids the door that
got the player stuck when there is another (`choose_hauntlet_door(avoid=...)`). Within a walk, **a view that doesn't move is the sign of being caught on something**: the camera follows the player, so a walking
player makes the whole picture move (`hauntlet_view()`: the screen shrunk to grey, without the room panel and the item bar). In the captured run the mean change between two looks was 0.1-1.1 grey levels while the player stood against a door's
leaf for 14 s and 11-44 while it walked, so under `HAUNTLET_STILL_LEVEL` for `HAUNTLET_STILL_AFTER` seconds with forward held is stuck, and `hauntlet_unstick()` steps sideways (forward stays held, so it
slides along) with a jump: towards the door being walked to (its TV - in a fork its doorway, when both are seen - against where the player stands, `HAUNTLET_UNSTICK_MARGIN`), the other way at every try, and a random way if the
doors can't be seen; each step `HAUNTLET_UNSTICK_STEP` seconds longer than the last, up to `HAUNTLET_UNSTICK_MAX`. Where the player stands is read from the hearts above its head; if they can't be read it is the middle of the
screen (the camera follows the player; in that run's room 3 the player stood 350 px right of the middle door, the hearts unread, and the macro, taking it to be in front of the middle door, walked into the wall for 14 s). When the doors can't be found at all - the camera zoomed in too far, turned away (room 8 of a macro-played run was seen from an angle, and the macro held W for 25 s against
a wall until the timer ran out), or a TV hidden - the camera can be dragged round with the right button (`rotate_camera()`, `HAUNTLET_CAMERA_DRAG`) up to `HAUNTLET_SEARCH_TURNS` times, a look
after each, before walking ahead - but that is **off** (`HAUNTLET_SEARCH_TURNS` is zero): turning the camera changes where the keys take the player, and in a captured fork (room 10, two doorways under signs, "Power Station" and "Spider Den") the player stood in front of its doorway until the camera was turned, then against the wall for 20 s. (That turn was a bug of its own: a fork whose looks had started over after a walk, past `HAUNTLET_DOORS_TIMEOUT`, was taken for one doorway; a fork now waits for its two empty looks whatever the age.) A TV showing a "1" (the player stands before that door) is a thin stroke, much smaller than the "0"s of the others, hence the low end of
`HAUNTLET_TV_AREA`; the real run's row was missed for 25 s because of one.

`hauntlet_choose()` reads the hearts and the items off the same screenshot chooses the door, uses the items the choice calls for, and logs the lot:
`room 12, 3 hearts (1 lost), items: phone x1, key x1, doors: -, -, locked, going right, used key`.

*The door* (`choose_hauntlet_door()`) is a random one, with two exceptions: a locked door is only a choice when the **grey key** (`HAUNTLET_ITEM_KEY`; the gold key is not it)
is held, and the door the phone's arrow is over is left alone (both only while there is any other door to take). *The items* (`hauntlet_items_for()`), each only if held and in
this order: the grey key for a locked door; a **cyan potion** (a heart that lasts the room) for a boarded one; the **red potion** (it refills the hearts) when the red hearts are down to
`HAUNTLET_LOW_HEARTS` or fewer; a **heart potion** (one more heart) whenever there is one. *The phone* is used first, with a chance of `HAUNTLET_PHONE_CHANCE` at each choice (`HAUNTLET_PHONE_CHANCE_LOW` when the hearts are low - it is then that knowing where the ghost is matters most): it puts a
red arrow - a big red triangle with a ghost on it, `HAUNTLET_ARROW_*` - over the TV of the door with a ghost behind it, which then isn't chosen. `find_ghost_door()` finds the arrow (it
takes a moment to show, so it is looked for `HAUNTLET_ARROW_LOOKS` times) and says which door it belongs to; over the captured runs it fired on 9 frames, all of the hand-played run (the one that used the phone), and where the doors were
found (6 of them) always within a few percent of a door spacing of a door's TV; it fired on none of the ~700 frames of the macro-played runs, which used no phone. `hauntlet_use_item()` clicks an item's slot, as the capture shows a human doing, and waits
`HAUNTLET_ITEM_WAIT`; the slots close up when an item is used up, so it looks for the slot on a fresh screenshot each time. The items are used before the walk starts. Whether an item
worked is not looked at, and the gold key and the wand have no use yet. `hauntlet_strafe()` works out how far to go sideways (the key and the seconds), and `hauntlet_walk()` **aims the player in steps** before it holds `KEYS["forward"]` - never two keys at once. A door is only as wide as a part of the distance between two doors, so a player that is a little off its door stands against the wall beside it and W gets it nowhere. In the recordings of two runs (rooms 8 and 15) that took 10-30 s of a room's 30 s in the worst rooms: the player stood 0.3-0.4 door spacings off its door - below the old limit for steering, which was 0.4, and checked once a second - and the stuck check's sidestep is a guess. A strafe worked out in one go also ended short or long (in room 4 of the room 15 run 2.4 s of A moved the doors 20-30 % more than `HAUNTLET_STRAFE_DURATION` says, and the player ended against the left wall beside its door). So the walk begins with up to `HAUNTLET_AIM_STEPS` steps: a look at the doors (`hauntlet_steer()`), one hold of the key towards the chosen door - `HAUNTLET_STEER_GAIN` of the strafe, at most `HAUNTLET_STEER_MAX` seconds - and a new look, until the door is within `HAUNTLET_STEER_MIN` door spacings of the player. If the doors can't be seen at the first look (`hauntlet_steer()` gives nothing), the strafe worked out by the caller is held as it was (at most `HAUNTLET_STRAFE_MAX` spacings). Then W is held until the number changes (or `HAUNTLET_WALK_MAX` seconds), and then `HAUNTLET_WALK_PAST_DURATION` longer, and while it is held **the player is steered** the same way every `HAUNTLET_STEER_EVERY` seconds: with the three TVs in sight, a player `HAUNTLET_STEER_MIN` door spacings or more off its door - measured as the strafe is - lets go of W, holds the key for a step and walks on. (What the keys do to the camera is not known to be anything: in the recordings its heading stays the same and the view changes as the player moves; the player's place in a room carries over from the door it came through.) **The number changes while the
player is still in the doorway** (the camera close on the door, its TV filling the screen, the next room's doors out of sight); a human keeps walking
1.0-2.0 s more (median 1.4 s in the capture) and a macro that let go at once stood there for the rest of the run - which is how the
macro's own captures showed it. The
strafe is the distance from the player to the door's TV in *door spacings* (the distance between neighbouring TVs, which
also takes the camera's zoom out of it) times `HAUNTLET_STRAFE_DURATION` seconds per spacing - no strafe under `HAUNTLET_STRAFE_MIN`
spacings, never more than `HAUNTLET_STRAFE_MAX`. The player's place is measured, not assumed, because where a room is entered may
depend on the door it came through (a player who came in at the left door has further to go to the right one): it is the middle of the
row of hearts above the head (`HauntletHearts.center_x`), and where the hearts aren't seen, in front of the middle door. A fork's
two doorways, when they aren't located, are the left or the right key for one spacing, wherever the player stands (in a captured fork it stood nearer the left doorway). The seconds per spacing are from a human's run (the
capture's keys against its room numbers, from the middle: left door 1.0-1.1 s, right 0.9-1.3 s; W held 3-4.8 s in all); that a key held by the macro
carries the character as far as one pressed by hand is **not yet checked in the game**, and neither is the strafe in a fork's hall
(that its doorways are two door spacings apart is read from a human's run and the captured frames, not tried) - both are the first things to tune. The keys are always released,
also when the macro is stopped. When the run ends, `got as far as room N` goes in the run log.

### Playing Ghost Gallery

From a human's 162 s run (the capture of the keys, the mouse and 209 frames). **How the game works:** ghosts run about a hall and hide
behind the furniture; the player has a beam (the round button at the bottom) that goes from the character **to the mouse cursor** while the
left button is held, and a ghost held in it scores ("+2700", a combo counter "x3"...). The player is sometimes knocked down. About **1:08
before the end** (the game lasts 3 minutes) the ghosts and furniture are replaced by the **boss**, a huge cyan ghost with a segmented
health bar (a gold frame, `GHOST_BOSS_BAR_*`) along the top, and it is where most of the points are. At the end the screen goes white
and a "GAME OVER!" results screen with a NICE! button shows, which the existing `detect_minigame_finished()` already handles. In the run the human
held the button on a ghost for a second or less at a time (and on the boss for tens of seconds), the cursor staying within about 150 px of
the character while the camera followed it.

`find_ghost_blobs()` finds the ghosts and the boss as the connected pieces of their translucent cyan (`GHOST_COLOR`, OpenCV HSV (85, 140, 250)
under the human's cursor), at least `GHOST_MIN_AREA` px (ghosts 880-7200 px in the run, the boss 9500-41000), biggest first, leaving out the
HUD, the character's own glow and the bottom of the screen (`GHOST_IGNORE_BOXES`: a player's pet, in the macro-played run a cyan one - the same cyan as the ghosts to a few units of hue and brightness, so it can't be told apart by colour -
was aimed at for 20 s of a run, the camera being close, and a cyan costume was taken for a ghost for much longer) and, in the boss phase, the boss's health bar (its crest and its ghost icons are the
same cyan). **Ghosts from afar.** In a macro-played run's first two minutes the score stayed at 450: from the high camera the ghosts are small blue-cyan ovals (1200-3500 px) and `GHOST_COLOR`, taken from a close-up human run, stopped at saturation 210 - ghosts were found in 12 of 27 looks, most of them the character itself. `GHOST_COLOR_FAR` is looked for as well. Its first version (saturation to 240) was still too narrow: in a later run (a Ghost Gallery run whose score stayed 0 for its first 100 s) the ghosts' heads were hue 92-99 and saturation 244-254, and none was found in the first minute. Now it is the range `GHOST_COLOR_FAR` holds (hue 90-99 was seen, up to full saturation, bright). What looks alike and is outside it: the slime puddles (hue 83-85 here, saturation 255, not as bright), the glow of the haunted paintings (hue 100 and over), the ghosts' own purple trail (hue 100), The pet's cyan is inside it, see below. The dim boss of the shielded phase stays out by its value. Ghosts really are rare in view in that stretch (about a third of the looks have one), so the camera is turned **one way round** until one is seen (`GHOST_CAMERA_PERIOD`; the way is chosen at random and kept, as in the boss phase) and `GHOST_CAMERA_TILT` is left at no tilt: a random up-and-down in every turn made the pitch a random walk, and the view ended on the ceiling, the floor or a wall.

**Things that are not ghosts.** In the last 40 s of a macro-played run (the camera pulled in against a wall in the dark of the boss fight) the macro was stuck, aiming at and walking towards its own hoodie, its pet and its beam, and so never turned the camera to look for the boss. Three rules came out of that capture. (1) **The character's box follows the camera**: the glowing pink emblem on its backpack (`GHOST_EMBLEM_HSV`, at least `GHOST_EMBLEM_MIN` px in `GHOST_EMBLEM_BOX` - none is seen with the camera far away, 750-10000 px close up) gives the place and, by its size, how close the camera is, and `ghost_character_box()` ignores a box of `GHOST_SELF_SCALE` times that size (pet below included) on top of the small fixed one. (2) **In the shielded phase only small ghosts count** (at most `GHOST_SMALL_MAX` px: the beam's glow, which turns cyan where it ends, was 9500, the boss's fog 400000). (3) **In the boss phase a blob is the boss only if it is at least `GHOST_BOSS_MIN` px** (9500-41000 seen); a smaller one is a minion or a bit of fog and is aimed at like the ghosts are, the nearest. The top 150 px of the screen are not looked at, where bits of fog under the HUD were found. (4) **The pet is the same cyan as a ghost and flies about wherever the camera is** (in one run it was beside the character in some looks and 300-500 px away in others), so nothing of the screen is left out for it. Two references were tried and don't work on the frames at hand: the median hue, saturation and value of the pet's pieces against a ghost's (the nearest of 15 labelled examples told right 11 times with each left out in turn, and a margin that dropped no ghost dropped no pet either), and a picture of the pet matched at several sizes (0.4-0.65 on other frames of it, as much as on ghosts). What does tell them apart is the score: **a blob the beam scores nothing on, and neither does the furniture around it** (`GHOST_HIDE_TRIES` places, `GHOST_SPOT_TRIAL` each), **is skipped** for `GHOST_SKIP_TIME` seconds (`GhostFeedback.skip()`), and `follow_skipped()` moves the mark to the blob it has become (the nearest within `GHOST_STICK` px at each look), so that the pet stays skipped as it flies and a ghost elsewhere is not. A hiding ghost, which also scores nothing at first, gets its furniture tried before that. (5) **In the boss phase the beam on the biggest blob for `GHOST_BOSS_TRIAL` seconds without a score skips it too**: in that run the boss was in sight in 3 looks of 10, and in the others the biggest blob was a light shaft along a side or a pale sheet at the bottom (15900-33600 px) which was walked towards for seconds. The boss can be far away, so nothing is concluded from a blob's shape or size. (6) **The white "!"** over the furniture a ghost hides in (`find_ghost_alerts()`: a white bar, at least `GHOST_ALERT_MIN_HEIGHT` px high and `GHOST_ALERT_ASPECT` times as high as wide, with a white dot under it, at any distance - four seen, 17-25 px high; a candle is white and as tall and has no dot) is aimed at before any ghost, `GHOST_ALERT_BELOW` bar heights below the mark, where the furniture is (seen in the run's first 60 s, while the score was 0). A trial there that scores nothing goes the same way as a ghost's: the furniture around it is tried, and then it is skipped.

`ghost_boss_bar_visible()` is the boss phase: 13,000-17,000 gold pixels in `GHOST_BOSS_BAR_BOX` against none (at most 230) before it, right on
every frame of the run but the ones at the very start and end of the boss's bar. `choose_ghost_target()` picks where to aim: the biggest blob
in the boss phase, else the ghost already being followed (one within `GHOST_STICK` px of where it was, since they move and aiming at another one at each look
would lose them all), else the nearest the character (`GHOST_CHARACTER`). Against the human's own play, its choice was within 150 px of
where the human held the beam in 69 of 80 frames outside the boss phase and the boss was where the human held it in 12 of 13.

**The boss's shield.** The boss can't always be hurt: in three phases of the run (6-7 s each, at 1:10, 0:37 and 0:25 before the end, a few seconds after
each other) its bar shows two or three round ghost icons instead of its cyan health, the boss itself goes dim, and small ghosts fly in; catching them is
what gets the boss back. `ghost_boss_shielded()` is the bar's cyan fill (`GHOST_BOSS_FILL`, an HSV range) being gone: 500-20000 pixels of it in `GHOST_BOSS_BAR_BOX` when the
boss can be hurt, 0 in the icon phases (a sliver, 470-2000, in the last seconds of the boss's life, which is why the limit, `GHOST_BOSS_FILL_MIN`, isn't zero). The boss
is dim enough then to fall under `GHOST_COLOR`'s brightness, so `find_ghost_blobs()` finds only the small ghosts, and they are aimed at like any ghosts
(nearest the character). Replaying the run, the three phases come out at 110.4-115.8 s, 127.2-133.5 s and 139.8-147.1 s of the capture, with the boss
phase before, between and after them, as in the frames.

**Score feedback.** The score says whether what the beam is on is a ghost, and `GhostFeedback` reads it: the digits of the banner's score pill (`GHOST_SCORE_BOX`, found by the pill's colour, `GHOST_SCORE_PILL`) as a mask of their white pixels. Over the four captured runs two looks at an unchanged score differed by 0 px and at a changed one by 160 px or more (`GHOST_SCORE_CHANGE` is between), so a changed mask is a score; nothing is concluded while the pill isn't on screen (a loading screen, a transition). It is used before the boss only (in the boss phase the score rises all the time, and while the boss is shielded it legitimately doesn't):
(1) **A ghost in sight that the beam scores nothing on** for `GHOST_SPOT_TRIAL` seconds is taken to be hiding in furniture: the beam moves to furniture found in a ring of `GHOST_RING` px around it (`find_furniture(around=...)`, by the same hover outline), stays there for the trial while the player keeps walking towards the ghost, and a place that scored nothing is kept away from for `GHOST_BAD_SPOT_TIME` seconds (`GHOST_BAD_SPOT_RADIUS` px) when the next one is looked for; a score puts the beam back on the ghost at once, and another ghost starts a trial of its own. With no ghost in sight the furniture search is as it was (a new place every `GHOST_FURNITURE_PERIOD`, shorter than a trial).
(2) **No score at all for `GHOST_STALL` seconds is a stall**: the beam is let go of, a random key held `GHOST_STALL_WALK` seconds, a jump made and the camera turned by `GHOST_STALL_TURN` px of drag, and another stall period passes before it happens again. This is what a view blocked by a pillar and a beam held on bare floor have in common (in one captured run a big orange pillar covered part of the screen at 59 s, and at 75 s the view was clear with the beam on the floor; the score was 0 for the first 64 s and 450 for 36 s after that). **Not yet checked in the game:** the ring's size, that the walk and turn get the player out from behind a pillar, and how long a real gap between scores is.

`play_ghost_gallery()` waits until the overlay (TIME LEFT, the score) has been seen - the game starts well before `MINIGAME_START_WAIT` would be over - and then for
`GHOST_INTRO_WAIT` more, while the game's own intro camera (a look at the ceiling) runs: a zoom made during it is undone when the play camera takes over, as it was in both macro-played runs.
Then it **sets up the view**: `zoom_out()` (`KEYS["zoom_out"]` held `MINIGAME_ZOOM_OUT_DURATION` seconds; in a macro-played run the camera was right behind the character, whose
cyan hoodie filled the screen and was taken for a ghost, so that the beam was held on the character for the whole run) and `tilt_camera_down()` (the right button held while the mouse is
dragged down `GHOST_START_TILT` px, for a view from above). Then, every `GHOST_LOOK_INTERVAL`, it looks and works out the phase (before the boss, the boss can be hurt, the boss is shielded),
moves the mouse to the target with `pydirectinput.moveTo()` (not `hover_move()`: its three settling pauses are too slow for tracking) and holds the left button down while there is one.
**The beam is held while the mouse is moved around**, and let go of and pressed again every `GHOST_REGRAB_PERIOD` seconds (a player who is knocked down loses the beam and has to press afresh; the same release is made when the run ends or is stopped): the left button goes down at the first target and the mouse is moved with it down after that. **With nothing to aim at** before the boss it looks for furniture to hold the beam on, `find_furniture()`: furniture the beam can be held on is highlighted - a pale outline and a tint - when the mouse is near it (in a 1920x1080 screenshot the outline's pixels were 190-230 in every channel, against 0-200 pale pixels in a patch of plain scene), so the mouse is moved to up to `GHOST_FURNITURE_PROBES` random places in `GHOST_FURNITURE_BOX`, at least `GHOST_FURNITURE_MIN_DISTANCE` px from the
character (`far_from_character()`), with a look at each after `GHOST_OUTLINE_WAIT` seconds, and the first place where `furniture_outlined()` sees pixels turn pale (`GHOST_OUTLINE_WHITE`: at least `GHOST_OUTLINE_MIN` of them within `GHOST_OUTLINE_RADIUS` px of the mouse that weren't pale or beside a pale pixel before; the green beam has no pale pixels) is held for
`GHOST_FURNITURE_PERIOD` seconds; none showing it, the last place tried is held. The ghosts hide in the furniture and holding the beam on it is how to get them out. With nothing to aim at for `GHOST_CAMERA_PERIOD` seconds it **turns the camera**
(`rotate_camera()`; like `tilt_camera_down()` it first moves the mouse to the middle of the screen, as the drag moves the cursor by hundreds of px and a right button released over the window's border or title bar opens its menu and takes the game's focus): the right button held while the mouse is dragged sideways `GHOST_CAMERA_DRAG` px, a random way (and a little up or down, `GHOST_CAMERA_TILT`), in
`GHOST_CAMERA_DRAG_STEPS` moves over `GHOST_CAMERA_DRAG_DURATION` seconds, so as to find some. **In the boss phase, with the boss and the ghosts that shield it out of sight, the camera is turned at every look until one is in view**: `GHOST_BOSS_TURN_DRAG` px at a time, one way (chosen at random, kept until something is seen), and nothing else is done - it doesn't walk blindly or aim at furniture. **It walks**: first forward for `GHOST_START_FORWARD` seconds, out of where the game puts the player and into the middle of the hall. After that **it always walks towards what it aims at**, the ghost or the boss: the key towards it is held (`ghost_chase_key()`: `w` if it is above the character, `s` below, `a`/`d` to a side - along the axis it is farther off on) until it is within `GHOST_TOWARD_MARGIN` px of the character, the beam being short (a human's cursor stayed within about 150 px of the character; a macro that aimed from afar scored 750 in its first 100 s). Only with nothing in sight before the boss does it walk about, a step at a time: every `GHOST_MOVE_PERIOD` seconds a random `MOVE_KEYS` key is held for `GHOST_MOVE_STEP_MIN`-`GHOST_MOVE_STEP_MAX` seconds. Every `GHOST_JUMP_MIN`-`GHOST_JUMP_MAX` seconds it **jumps** (`KEYS["jump"]`).
The buttons and the walking key are always let go, also when the macro is stopped. The macro's own window is out of the way (sent to the back, see
`send_macro_window_to_back()`) for the whole of a minigame, as it covers part of the screen.
**Not yet checked in the game:** that `pydirectinput.moveRel(..., relative=True)` with the right button held turns Roblox's camera the way a hand does, the size of the drags and of
the start's tilt, where the furniture is (the box is a guess), and the walking's and jumping's use; nor whether the character's own colours (a cyan costume) are kept out of the ghost finder
once the camera is out (`GHOST_IGNORE_BOXES` has the character's place in a human's zoomed-out run). **What it does not do:** dodge, react to being knocked down, or choose between ghosts
by anything but distance (the human often picked the second or third nearest); it can't tell furniture from floor.

### Playing Stitches' Spotlight

First version, from two human-played rounds (a two-minute round; the overlay shows TIME LEFT, the title and the Candy Corn count). The player runs on a
ring walkway round a central drum (with a bunny on it that moves, so the drum is not a landmark to match); candy corn lies along the ring and on the platforms
at its sides, searchlights sweep it and tint what they cover red, and dark grey planks hide the player from the light. The count is never read.

`play_stitches_spotlight()` waits for the overlay (`MINIGAME_START_WAIT`), then for the ring's line to show (at most `STITCHES_INTRO_MAX`: the game's intro
camera looks at the sky first), tests which way the camera keys turn (`calibrate_ring_camera()`), then holds `KEYS["right"]` (a strafe, which round a middle
the camera looks at is a walk round the ring) until the minigame finishes. Every `STITCHES_CENTER_INTERVAL` it turns the camera towards the middle (`RingCamera`) and every
`STITCHES_BOOST_INTERVAL` it presses `KEYS["speed_boost"]` (the speed boost, "Press E for a Speed Boost!").

**Where the middle is** comes from the ring's own edge: a gold line (`STITCHES_LINE_*`: hue, saturation and value of its dim olive-gold, looked for in
`STITCHES_LINE_BOX`), fitted with a line (`stitches_ring_angle()`). A ground line across the camera's view runs straight across the screen, so when the camera
looks at the middle of the ring the line is level; a line that falls to the right means the middle is to the right.

**Turning the camera is a small controller (`RingCamera.decide()`), because a walk round the ring needs the camera turned all the time**, and the line is not
always in sight (in a searchlight's red it is not found, and with the camera turned away; on a round's frames it was found in about six of ten looks):
- *proportional:* a turn of up to `STITCHES_TURN_HOLD_MAX` seconds, longer the further the line is from level; within `STITCHES_LINE_DEADBAND` degrees none;
- *learned steady turn (`bias`):* while the line stays off, the share of the time the turn key is held grows (`STITCHES_TURN_BIAS_GAIN`, at most
  `STITCHES_TURN_BIAS_MAX`), which is the turn rate the orbit needs;
- *line out of sight:* the learned turn goes on, and after `STITCHES_LOST_AFTER` seconds the camera is swept round to find the line again;
- a turn shorter than `STITCHES_TURN_MIN_HOLD` is not made.

**Which key turns which way is tested, not assumed.** `calibrate_ring_camera()` turns with the right key for `STITCHES_CALIBRATE_HOLD` seconds and looks at the line
(it must move at least `STITCHES_CALIBRATE_MIN` degrees): turning right levels a line that falls to the right. If it moved the other way `RingCamera.swap_keys()`
swaps the keys (and the learned turn); if nothing clear shows, the keys stay as `KEYS["camera_left"]` and `KEYS["camera_right"]` has them and the run capture is marked. Each look logs the line's
angle, the turn and the learned `bias` at the debug level.

The red and the planks are not handled, and no candy corn is looked for: the magnet picks up what the walk passes.

Not yet known from play: whether D holds the character on the ring with these turns (the player has not been seen working in the game: a run capture of it
is the next thing to look at, with the debug lines of the log), how the round goes when the red is on the ring, and what the speed boost is worth.
The tests use frames of a round (`tests/stitches_spotlight/`) and a made-up camera.

### Candy corn

What a minigame run earns is read, not guessed. **The HUD's candy corn total** (top right, red digits on a white flag - on screen in the lobby, in the world and on the GAME OVER! screen, not at home or in a minigame) is read by `read_candy_corn()`:
the red pixels of `CANDY_CORN_BOX` that make up pieces as tall as a digit (`CANDY_CORN_DIGIT_HEIGHT`; the commas are half that) are the digits, each shrunk to `CANDY_CORN_DIGIT_SIZE` and compared with the saved ones (`ref/candy_corn_digits/`, cut from six real screens
that between them have every digit; `CANDY_CORN_MAX_DIFF`). It returns None rather than a wrong number: no counter, a digit that matches none, digits further apart than `CANDY_CORN_PITCH` (one is missing) or a piece as tall as a digit but too wide
(something over the first or last digit, which would leave a number that looks right). The six screens are read right, a leave-one-out test of the digits (each read with templates cut from the other screens) 41 of 41, and over 553 captured frames it
read only totals that were plausible, in the lobby and game over frames. `read_candy_corn_settled()` reads until two looks half a second apart agree, as the total counts up for a moment after a run.

`play_minigame()` reads it in the lobby after the popup's Yes (the total before) and again on the GAME OVER! screen (after): **the candy corn a run earned is the difference** (`minigame_result()`; None if either wasn't read or the total fell). In the captured Ghost Gallery run
(score 46418) it rose by 2540, in a Hauntlet 2 run that reached room 10 by 2105. Each run's result - when, run number, minigame, how it ended, the totals, `earned`, and the room reached in Hauntlet 2 (`state.minigame_detail`, filled by `play_hauntlet_2()`) - is
appended as a line of `debug/runs/results/results.jsonl` (`record_minigame_result()`, which also logs `minigame result: <minigame>: N candy corn` in the run log), written into the recording's `info.json`, and used to decide which recordings to keep (see above).
The GAME OVER! card itself is also saved as a picture beside it (`save_minigame_result()`). A run's limit of candy corn differs by minigame - 5000 for Ghost Gallery, a good deal more for Hauntlet 2 (about 20000 by the player's account; the macro doesn't know it).

### Recording a run

Tick **Record the next run (low quality video)** on the Debug tab's Options subtab (the
`record_next_run` config switch) and the next time the loop starts it records
the Roblox window to `debug/recordings/run<N>_<time>.mp4` - then clears the
switch, so it's one run only. `start_recording_if_requested()` starts a
`RunRecorder`, which writes `RECORD_WIDTH` x `RECORD_HEIGHT` frames at
`RECORD_FPS` from a background thread, each stamped with the time (white text on a solid black box in the top-left
corner, which stays readable over any scene at the cost of that corner) so the
video lines up with `output.log` and `run_log.txt`; a slow grab is covered by
repeating the last frame, so playback is real time. It stops when the loop
ends or after `RECORD_MAX_MINUTES`, and only the newest `MAX_RECORDINGS` videos
are kept. It's MP4 (`mp4v`) where OpenCV can write one, otherwise MJPG in an
`.avi`; `recording started` / `recording stopped` (with the size) go in the run
log. The picture is the Roblox window as the macro sees it, so the macro's own
window shows up in it if it's on top.

## Configuration reference

Every tunable number, timing, screen position and color range lives in
`magic_numbers.py`, not `main.py` - it's the one file to edit for a moved
button, a slower computer, or a different monitor. `main.py` does `import
magic_numbers as cfg` and refers to everything there as `cfg.NAME`.
`ENABLED_NEEDS` is the first thing defined in it, since it's the setting
most likely to need editing. Everything else falls into one of these
groups (see the comments next to each constant in `magic_numbers.py` for
exact values and rationale):

| Group | Examples |
|---|---|
| Behavior flags | `ENABLED_NEEDS`, `HOTKEYS["stop"]`, `BACKPACK_*`, `HALLOWEEN`, `MINIGAME_*`, `SCREEN_CHANGE_SIZE`, `HAUNTLET_2_*`, `GHOST_*`, `REJOIN_*`, `HELICOPTER_REQUIRED`, `FOCUS_WINDOW_ON_ACTION` |
| Window / coordinates | `REFERENCE_WIDTH`, `REFERENCE_HEIGHT`, `REFERENCE_CENTER_X/Y`, `ROBLOX_RECT_TTL` |
| Key bindings | `KEYS` (every key the macro presses, by what it is for: walking, jump, backpack, mount, interact, speed boost, zoom, helicopter, camera turn), `KEY_SEQUENCES` (respawn, leaving the game), `HOTKEYS` (stop, capture, blackout), and the derived `MOVE_KEYS` and `HELD_KEYS`; `STOP_HOTKEY_POLL_INTERVAL` |
| Timing | `WATCH_POLL_INTERVAL`, `NEED_WATCH_JOIN_TIMEOUT`, `RESPAWN_WAIT`, `WALK_TO_BUTTONS_DURATION`, `NEED_CHECK_RETRY_DELAY`, `LOOP_DELAY`, `STOP_CHECK_INTERVAL`, `STOP_CLICK_GRACE`, `NEED_GONE_*` |
| Stuck needs | `NEED_STUCK_CHECKS` |
| Pet focusing | `FOCUS_PET_REGION_TOP_PERCENT`, `FOCUS_PET_FRAME_GAP`, `FOCUS_PET_DIFF_THRESHOLD`, `FOCUS_PET_MERGE_KERNEL`, `FOCUS_PET_MIN_AREA` |
| Screen positions | `CATCH_*_POS`, `EMPTY_POS`, `RIDE_*_POS`, `LURE_NEW_POS_*`, `SETUP_*_POS` |
| Side quest / setup | `TREE_HARVEST_YIELD`, `MONEY_COLLECTED_TARGET`, `TREE_CHECK_INTERVAL`, `MONEY_RESET_HOUR`, `LURE_RECOLLECT_INTERVAL`, `LURE_COLLECT_*`, `TREE_COLLECT_*` |
| Need-icon detection | `NEED_ICON_TOP_PERCENT`, `NEED_ICON_WIDTH_PERCENT`, `NEED_ICON_BLANK_*`, `NEED_ICON_MIN/MAX_RADIUS`, `NEED_ICON_HOUGH_*`, `NEED_STRIP_*` (when the circle search is skipped) |
| Need-icon matching | `ICON_MATCH_THRESHOLD`, `ICON_BW_THRESHOLD`, `ICON_CLAHE_*`, `ICON_COMPARE_SIZE`, `ICON_MATCH_RADIUS_FRACTION`, `ICON_CROP_RADIUS`, `ICON_SHIFT_TOLERANCE`, `MATCH_ONLY_LEFT_HALF` |
| Button detection | `BUTTON_BAND_X/Y`, `BUTTON_MIN_AREA`, `BUTTON_MIN_CIRCULARITY`, `BUTTON_MAX_COUNT`, `BUTTON_NAMES`, `BUTTON_LOOSE_*_FACTOR`, `BUTTON_PURPLE_*_ITERATIONS`, `BUTTON_OUTLINE_PADDING` |
| Discord bot | `PULL_SOURCES`, `BOT_MAX_PULL_MB`, `BOT_PULL_CONFIRM_MB`, `BOT_PULL_CONFIRM_TIMEOUT`, `COMMANDS_DIR`, `COMMANDS`, `COMMAND_POLL_MS`, `COMMAND_MAX_AGE`, `REJOIN_STOP_POLL_MS`, `REJOIN_STOP_WAIT_MS`, `START_LOOP_DELAY_MS`, `BOT_COMMAND_ACK_WAIT`, `BOT_COMMAND_ACK_POLL`, `BOT_ACK_KEEP`, `BOT_START_CONFIRM`, `BOT_RVIEW_*`, `BOT_GIT_TIMEOUT`, `BOT_PIP_TIMEOUT`, `NEED_NAMES`, `OPTION_SWITCHES`, `SHOW_DEBUG_LINES_OPTION`, `TRADE_HUB_*`, `BOT_CHANNEL_TRIES`, `BOT_CHANNEL_RETRY_WAIT`, `BOT_WATCH_INTERVAL`, `BOT_ALERT_GAP`, `BOT_FILE_SETTLE`, `BOT_MAX_UPLOAD_MB` |
| Debug logging | `OUTPUT_LOG_PATH`, `RUNS_DIR`, `FAILURE_DIR`, `STATUS_PATH`, `OUTPUT_LOG_MAX_BYTES`, `MAX_FAILURE_FOLDERS`, `MAX_REPORTS`, `CONSOLE_MAX_LINES`, `QUIET_LOG_INTERVAL`, `RESOURCE_LOG_INTERVAL` |
| Recovery | `DISABLED_NEEDS_REJOIN_THRESHOLD`, `NO_PROGRESS_REJOIN_INTERVAL`, `NO_DETECTION_RESPAWN_INTERVAL`, `DISCONNECT_*`, `WHITE_SCREEN_*`, `REJOIN_*`, `ROBLOX_*`, `FOCUS_RESUME_*`, `PAYCHECK_DISMISS_POS_*` |
| Hauntlet 2 reader | `HAUNTLET_ROOM_BOX`, `HAUNTLET_DIGIT_*`, `HAUNTLET_ROOM_MAX_DIGITS`, `HAUNTLET_SLOT_*`, `HAUNTLET_ITEM_*`, `HAUNTLET_BADGE_*`, `HAUNTLET_COUNT_*`, `HAUNTLET_NOT_ITEMS`, `HAUNTLET_HEART_*`, `HAUNTLET_*_DIR` |
| Stitches' Spotlight play | `STITCHES_*` |
| Ghost Gallery play | `GHOST_COLOR`, `GHOST_COLOR_FAR`, `GHOST_CLOSE`, `GHOST_MIN_AREA`, `GHOST_IGNORE_BOXES`, `GHOST_CHARACTER`, `GHOST_STICK`, `GHOST_BOSS_BAR_*`, `GHOST_BOSS_FILL*`, `GHOST_LOOK_INTERVAL`, `GHOST_FURNITURE_*`, `GHOST_CAMERA_*`, `GHOST_START_TILT`, `GHOST_MOVE_*`, `GHOST_START_FORWARD`, `GHOST_TOWARD_MARGIN`, `GHOST_BOSS_TURN_DRAG`, `GHOST_EMBLEM_*`, `GHOST_SELF_*`, `GHOST_SMALL_MAX`, `GHOST_BOSS_MIN`, `GHOST_REGRAB_PERIOD`, `GHOST_OUTLINE_*`, `GHOST_JUMP_*`, `GHOST_SCORE_*`, `GHOST_SPOT_TRIAL`, `GHOST_BAD_SPOT_*`, `GHOST_RING`, `GHOST_STALL*`, `GHOST_ALERT_*`, `GHOST_SKIP_*`, `GHOST_HIDE_TRIES`, `GHOST_BOSS_TRIAL`, `KEYS["zoom_out"]`, `MINIGAME_ZOOM_OUT_DURATION` |
| Hauntlet 2 play | `HAUNTLET_TIMER_*`, `HAUNTLET_TV_*`, `HAUNTLET_DOOR_*`, `HAUNTLET_LOCK_LIGHT_*`, `HAUNTLET_MYSTERY_BRIGHTNESS`, `HAUNTLET_SHADOW_*`, `HAUNTLET_CIRCLE_*`, `HAUNTLET_IN_CIRCLE`, `HAUNTLET_LOCK_WAIT`, `HAUNTLET_STILL_*`, `HAUNTLET_UNSTICK_*`, `HAUNTLET_START_*`, `HAUNTLET_DARK_*`, `HAUNTLET_VIEW_*`, `HAUNTLET_PLANK_*`, `KEYS["left"]`, `KEYS["right"]`, `HAUNTLET_STRAFE_*`, `HAUNTLET_WALK_MAX`, `HAUNTLET_LOOK_INTERVAL`, `HAUNTLET_ROOM_SETTLE`, `HAUNTLET_FORK_EVERY`, `HAUNTLET_DOORS_TIMEOUT`, `HAUNTLET_ITEM_*`, `HAUNTLET_LOW_HEARTS`, `HAUNTLET_PHONE_CHANCE`, `HAUNTLET_ARROW_*` |
| Candy corn | `CANDY_CORN_*`, `MINIGAME_RESULT_BOX`, `MAX_RESULT_SHOTS` |
| Input capture | `HOTKEYS["capture"]`, `CAPTURES_DIR`, `MINIGAME_CAPTURE`, `RUN_CAPTURES_DIR`, `RUN_CAPTURE_ZIPS`, `RUN_CAPTURES_BEST`, `RUN_CAPTURES_WORST`, `RUN_CAPTURES_FAILED`, `RUN_CAPTURE_BURST`, `RUN_CAPTURE_TAIL`, `RUN_CAPTURE_PROTECTED`, `CAPTURE_FRAME_INTERVAL`, `CAPTURE_CHANGE_LEVEL`, `CAPTURE_CHANGE_FRACTION`, `CAPTURE_MAX_SKIP`, `CAPTURE_POLL_INTERVAL`, `CAPTURE_MOVE_INTERVAL`, `CAPTURE_MAX_MINUTES`, `CAPTURE_MIN_FREE_MB`, `MAX_CAPTURES`, `CAPTURE_ZIP_MB` |
| Backpack tabs | `TAB_HALF_SIZE`, `TAB_SELECTED_*` (a category tab that is orange is already selected) |
| Blanking the screen | `BLACKOUT_*` |
| Popup crops | `STRAY_WINDOWS`, `STRAY_WINDOW_MATCH_THRESHOLD`, `POPUP_TEMPLATES` (box and threshold of the paycheck, sick-dialog, doctor-prompt and backpack crops) |
| Color ranges (HSV) | `PURPLE_RANGE`, `WHITE_RANGE` (button detection) |
| GUI | colors/fonts (`GUI_BG`, `GUI_FONT`, ...) and layout spacing (`GUI_OUTER_PADDING`, `GUI_ROW_SPACING`, ...) for the `tkinter` panel |

## Debugging an unattended run

**How the macro says things.** Two logs, for two readers. `logger` (Python's `logging`, one logger for the whole macro, `main.py`'s `logger`) is
for the person watching or debugging: the console and `output.log`. `log_run_event()` is the record the report is drawn from and its wording is
parsed (`parse_run()`), so it only gets the events the report knows. The levels mean what they usually do: **debug** is how the macro got somewhere
(positions, scores, what a look found), **info** what it is doing, **warning** something it couldn't do or that looks wrong but it carried on,
**error** a failure (`log_failure()` logs at this level) or a crash. A message is a sentence in sentence case - no `[debug]` or `[!]` tags (the level
says that), no capitals for emphasis, no exclamation marks, no blank lines. `print()` is only for a question put to someone at a terminal.

Everything below is in `debug/` (git-ignored) so a run that broke while you
were away can be read back afterwards. The folder has `failures/` (`FAILURE_DIR`), `logs/` (`LOGS_DIR`: every text and json file - `output.log`, `run_log.txt` (all runs, the whole log),
`run_counter.txt`, `status.json`), **`runs/`** (`RUNS_DIR`: what belongs to one run - its own log, `run<N>.txt`, and its chart, `run<N>.png`, side by side, and the GAME OVER! cards of the minigames in `runs/results/`) `recordings/` for the
videos, `captures/` and `minigame_captures/` for the input captures (see [Input capture](#input-capture)), the commands the Discord bot leaves (`commands/`, with the macro's answers) and the macro's error output when the bot started it (`logs/macro_stderr.log`), and the game config (`.config`) in `debug/` itself. An older layout is
moved into place at startup (`migrate_debug_layout()`, which also moves the old `logs/runs/` and `reports/` files into `runs/`, `images/failures/` up to `failures/`, and deletes the `debug_*.png` views and the `images/` folder). The Debug tab's
Options subtab's **Refresh debug** button (`refresh_debug()`, after a confirmation, not while a
run is going) empties all of it except `.config`; run numbers start from 1
again, since the counter goes with the logs.

- **`output.log`** - everything the macro logs (`logger`, the same lines as the GUI console, with the debug ones), one line each with its time
  and level, appended to across runs, whether or not the GUI is up. The `OutputLog` handler opens the file with the first line (flushed by the
  handler after each, so a crash loses nothing); `close_output_log()` closes it before **Refresh debug** deletes it, since Windows won't delete an
  open file. Past `OUTPUT_LOG_MAX_BYTES` it moves to `output.log.old`, replacing the previous one (a `RotatingFileHandler` does that).
- **`run_log.txt`** - the short version: one tagged line per notable event
  (needs detected, resolved with how long they took, popups dismissed,
  recoveries, stops, failures). Start here to see roughly what happened.
  Every line is also written to its own run's file, `runs/run<N>.txt`
  (`RUNS_DIR`, by `log_run_event()`), so one run can be read or shared on
  its own; lines logged before any run has started (`RUN None`) are only in
  the combined file, and the run report still reads the combined one.
  Two kinds of line are written on a timer while the loop runs:
  `quiet: no need resolved for N min` (every `QUIET_LOG_INTERVAL`, by
  `check_quiet()` at the top of each cycle, while nothing is being resolved -
  well before the `NO_PROGRESS_REJOIN_INTERVAL` rejoin) and
  `resources: cpu X% mem YMB pc P% pcmem M%` (every `RESOURCE_LOG_INTERVAL`,
  from the `ResourceMonitor` thread: the script's own CPU use as a percentage
  of the whole machine, its memory, and - when readable
  (`system_cpu_times()`, `system_memory_percent()`: `GetSystemTimes` /
  `GlobalMemoryStatusEx` on Windows, `/proc` elsewhere) - the whole PC's CPU
  and memory use; older logs lack the last two). A need switched off as stuck
  logs `disabled: <need>`. The run report charts the resources.
- **`runs/run<N>.png`** - a chart of one run, beside the run's own log, drawn from `run_log.txt` by
  `generate_run_report()` in `main.py` with OpenCV (no extra dependency). Written automatically when a
  run stops (`write_run_report()`, in the `finally` of `run_workflow_loop()`)
  (so there is no button for it); the oldest reports beyond `MAX_REPORTS` are deleted then (`keep_newest_files()`, pngs only) - the logs they are drawn from, `run_log.txt` and the runs' own `run<N>.txt`, are kept, so one can be drawn again. Five panels: needs resolved per unit of time (the unit is picked
  from `BUCKET_STEPS` so the run fills about `TARGET_BUCKETS` bars, stacked by
  basic / teleport / other need, with failures and rejoins marked); needs
  detected per check; average seconds per resolved need with how many there
  were; the best, worst and average candy corn of each minigame's runs (from the `minigame result:` lines) and counts of failures by kind and other events (a rejoin after a white screen is an event, `white screens (rejoined)`, not a failure); and the script's own CPU and memory use over time, each with the whole PC's use as a second line (CPU on the same scale; memory in % on its own axis, labelled down the right edge). A need disabled as stuck is drawn in the first panel as a grey bar from when it was disabled until the next rejoin (which clears them), named above it. `parse_run()` reads
  the log by the exact line wording `log_run_event()` writes, so a wording
  change there needs the matching regex in the RUN REPORT section of `main.py`. It never raises -
  a report problem can't take a run down.
- **`failures/`** - `log_failure(reason)` saves a folder named with the
  time, run number and reason (`save_failure_report()`): `screen.png` (the whole
  screen), `need_icons.png` (the strip the need icons are read from, so a
  misread icon can be seen at once; missing when Roblox couldn't be captured)
  and `info.txt` (the reason, the needs last detected, the disabled needs and
  how long ago a need was last resolved) and `state.json` (the whole `State`
  object and `run_stats` (a `RunStats` dataclass) as they were, for working out what the macro believed
  at that moment). It also logs the reason, writes it
  to `run_log.txt` and counts it in the status file. It's called when a handler
  can't resolve a need, a need is still showing after `NEED_GONE_MAX_WAIT`, a
  need is disabled as stuck, a recovery starts or fails, Roblox loses focus
  and stops the run, and when the run crashes (the full traceback is in
  `output.log`). It never raises, since it runs while something else is
  already wrong. The oldest folders beyond `MAX_FAILURE_FOLDERS` are
  deleted.
- **`status.json`** - rewritten every cycle and on every failure/resolved
  need (`write_status()`): the run number and start time, time of the last
  update (if that's old, the macro is stuck or dead), cycle count, the needs
  last detected, how many of each need were resolved or failed, the needs
  currently disabled, the number of recoveries, disconnects, scheduled
  rejoins and focus resumes, the failures in all, `stopped` (when the macro ended on purpose or opened with no loop going - `mark_stopped()`; the Discord bot does not take a status that stops updating then for a stuck macro, and a new run clears it), how many whole minutes no need has been resolved
  for (`quiet_minutes`), the script's latest CPU and memory use, and the last
  resolved need and last failure. `run_stats` (a `RunStats` dataclass) holds the counters in memory.
- **`recordings/`** - run videos, see [Recording a run](#recording-a-run).

A run that *stops* (a crash, or Roblox losing focus with **Resume after focus
loss** off) ends the loop and stays stopped - deliberately, since losing focus
usually means the computer is being used for something else. The logs say
why: the failure screenshot is of the whole screen, so it shows what took the
focus.

If detection isn't finding what you expect after changing screen
resolution or Roblox's UI, look at the screenshot of a failure (`failures/`) and the
`button scan found N of 5` line of the run log before tuning any of these.

## Discord bot

`bot/` is a separate program, not part of `main.py`: a Discord bot that answers questions about the macro from anywhere (a phone, say). It does the read-only commands, alerts, pulling things out of the debug folder into OneDrive on request, starting and stopping the macro, and updating it. It runs **on the PC the macro runs on**, in the same logged-in desktop session, because it reads the
macro's files and grabs its screen; it connects out to Discord, so no port needs opening, and the Discord app itself is only on whatever device is used to type
the commands. It is started by hand, next to the macro: `python bot/discord_bot.py [path to bot_config.json]`.

- **`bot/bot_lib.py`** holds every decision that needs neither Discord nor Windows, as plain functions (`tests/test_bot.py` runs them anywhere, in CI too):
  `load_config()` (a `ConfigError` saying what is wrong), `is_allowed()`, `status_text()`, `results_text()`, `tail_lines()`, `newest_report()`, `looks_blank()`,
  `main_py_in()`. The paths it reads are the macro's own (`STATUS_PATH`, `RUN_LOG_PATH`, `RUNS_DIR` of `magic_numbers.py`), so a moved file moves for both.
- **`bot/discord_bot.py`** is the glue that can't be tested away from Discord (discord.py, the screen grab, the process check): the commands below, registered
  for the one guild if `guild_id` is set (instant) or globally (up to an hour to show).
- **`bot_config.json`** (untracked, and there is no example file: this list is where the keys are written down; the file is JSON, paths with forward slashes): `token`, `owner_id`, `channel_id`, optionally `guild_id`, `stale_status_minutes`
  (how long `status.json` may go without an update before `/status` says it is not updating), `log_lines_max` (what `/log` is capped at), `onedrive_dir` (the folder inside the synced OneDrive folder that `/pull` copies into; leave it out and `/pull` says there is nowhere to copy to), `heartbeat_hours` (0: no heartbeat), `repo_dir` and `python` (what `/start`, `/loop`, the automatic restart and `/update` launch `main.py` with; the bot's own folder and Python when left out), `stop_timeout_seconds`, `auto_restart` (default off), `auto_restart_max_per_hour`, `update_remote` and `update_branch` (what `/update` pulls: `origin` and `main`).
- **Who is obeyed:** only `owner_id`, only in `channel_id`; anyone else's commands get no answer at all (`OwnerOnlyTree.interaction_check()`). The commands are a
  fixed list whose arguments are a number (`/log`), a choice from a fixed list (`/need`, `/option`, `/pull`'s folder) or, for `/pull`, an `item` that is only used if it is exactly the name of something in that folder - nothing is run, no path is read from a message. The bot is created private ("Public Bot" off) and the token
  stays out of the repository (`.gitignore`).

| Command | What it posts |
| --- | --- |
| `/status` | `status.json` as text - whether it is still being updated (flagged when not), whether `main.py` is running (psutil if installed, else PowerShell or `pgrep`), the run, resolved and failed per need, disabled needs, the last resolved need and failure, rejoins, CPU and memory |
| `/screenshot` | the Roblox window (the whole monitor if there is none) as a PNG, with a note when it looks blank - a locked or sleeping PC |
| `/rview` | a rolling view: the Roblox window as a JPEG (`grab_roblox_jpeg()`, `BOT_RVIEW_WIDTH` wide, `BOT_RVIEW_JPEG_QUALITY`) every `BOT_RVIEW_INTERVAL` seconds for `BOT_RVIEW_SECONDS` seconds, each replacing the last in one message (the message is edited, so the channel is not flooded); one at a time, and a failure stops it with a message |
| `/log [lines]` | the last lines of `run_log.txt`, as a file when they don't fit a message |
| `/report` | the newest run chart in `RUNS_DIR` |
| `/results` | each minigame's runs, best, worst and average candy corn, the deepest Hauntlet 2 room and the latest, from `runs/results/results.jsonl` |
| `/start` | starts the macro with its window up and no loop going (`python main.py`), unless it is running; waits up to `BOT_START_CONFIRM` seconds for the process |
| `/loop` | starts the macro's loop: launches `python main.py --loop` if no `main.py` process is running, else leaves it the `start` command; then waits up to `BOT_START_CONFIRM` seconds for `status.json` to be written and says whether the loop showed |
| `/stop` | asks the macro to stop its loop (the `stop` command, the [STOP] button); its window stays open |
| `/exit` | asks the macro to exit (the `exit` command) and waits up to `stop_timeout_seconds` for its process to end; if it hasn't, kills it and releases the keys and mouse buttons from the bot |
| `/rejoin` | the macro leaves the game and rejoins it (its loop is stopped first; `/loop` again afterwards) |
| `/setup` | the macro runs Setup (its loop is stopped first; `/loop` again afterwards) |
| `/trade_hub` | the macro travels to the Trading Hub (`travel_to_trade_hub()`: the Trading Hub Phone from the backpack's Toys, used, TRAVEL TO HUB, Yes; the loop is stopped first). The clicks are the positions of a human's run (`TRADE_HUB_*`) |
| `/leave_trade_hub` | the macro leaves the Trading Hub for a regular server (`leave_trade_hub()`: GO HOME, REGULAR SERVER) |
| `/refresh_debug` | the macro empties its debug folder (the game config stays), as the Refresh debug button does without asking; refused while its loop runs (the status says `stopped` when it does not: `loop_idle()`) |
| `/update` | pulls `update_remote`/`update_branch` (main) and restarts the macro - and the bot, if its own code changed - see [Update and rollback](#update-and-rollback) |
| `/rollback` | goes back to the commit the PC was on before the last `/update`, and restarts the macro the same way |
| `/need <need> <on>` | switches a need on or off, as the Debug tab's checkbox does (the macro must be running: the command goes through a command file); says what the game config shows a moment later |
| `/option <option> <on>` | the other switches: `side_quest`, `resume_on_focus_loss`, `record_next_run`, `play_<minigame>` and `show_debug_lines` (the output's debug lines, which is only a checkbox and so is not confirmed) |
| `/options` | which options and needs are on, read from the game config (`debug/.config`, with the defaults for what is missing) |
| `/captures` | the newest minigame captures on this PC, with their outcome, zips and size |
| `/pull <folder> [item]` | copies something out of the debug folder into OneDrive - see below; nothing is copied to OneDrive unless this is used |

**What the bot posts by itself.** `watch()` looks at the macro's files every `BOT_WATCH_INTERVAL` seconds (`watch_once()`; a look that fails is logged and the watching goes on) and keeps what it has dealt with in `bot_state.json` (`BotState`: where it got to in `run_log.txt`, the run charts done, whether the status is stale, the last heartbeat), so a restart neither posts things twice nor misses what came while it was down. On its first start, what exists already is taken as dealt with, so old failures, charts and captures are not posted.

- **A failure:** a new `FAILURE:` line in `run_log.txt` (`parse_failure()`) is posted with its reason and its screenshot (`failure_screenshot_path()`: only a file inside `debug/`, not whatever path a line names; not posted when bigger than `BOT_MAX_UPLOAD_MB`). At most one post per `BOT_ALERT_GAP` seconds (`RateLimiter`): the next post says how many more there were. A log that is shorter than the offset was deleted or started again (**Refresh debug**), and is read from its start (`read_new_lines()`; a line still being written waits for the next look).
- **The macro stops updating:** once, when `status.json` has not been updated for `stale_status_minutes` (the macro is stuck, or the PC asleep or locked; not when it was stopped on purpose - [STOP], the window's X, `/stop`, `/exit` - or is open with no loop going, which `mark_stopped()` writes into the status as `stopped`, and a new run clears), and once when it updates again (`stale_alert()`).
- **A run chart:** each new `run<N>.png` in `RUNS_DIR`, once it has been unchanged for `BOT_FILE_SETTLE` seconds.
- **A heartbeat:** the `/status` text every `heartbeat_hours`, so silence means trouble; off by default.

**Pulling things into OneDrive (`/pull`).** Nothing is copied to OneDrive by itself (it was, once: a finished capture's zips; that was taken out). `/pull` takes a `folder` from the fixed list `PULL_SOURCES` - `failures`, `logs`, `runs`, `minigame_captures`, `captures`, `recordings`; the game config (`debug/.config`, which holds the private server link) is not in it - and optionally an `item`: left out, the newest entry of the folder; `*`, the whole folder; or an entry picked from the autocomplete (newest first). An item is only accepted if it is exactly the name of an entry in that folder (`resolve_pull()`: `..`, a slash or a drive letter get nowhere). It copies the files into `onedrive_dir/<folder>/` (`pull_files()`, `copy_pull()`) and says how many files and megabytes; the OneDrive app on the PC uploads them. A minigame capture is copied as its zips alone (the first holds `info.json` and `events.jsonl`) - not its frames, which are the same again as the zips; with `*`, the zips of every capture in the folder. A pull of more than `BOT_MAX_PULL_MB` copies nothing. One of more than `BOT_PULL_CONFIRM_MB` first says its size (in GB from 1024 MB) and file count with a **Copy it** and a **Cancel** button (`ConfirmView`, which only the owner in the channel can press); without a yes within `BOT_PULL_CONFIRM_TIMEOUT` seconds nothing is copied. The size is in the answer as well. An existing copy of the same file is replaced.

### Remote control

The commands that act on the macro (`/start`, `/loop`, `/stop`, `/exit`, `/rejoin`, `/setup`, `/refresh_debug`, `/trade_hub`, `/leave_trade_hub`, and `/need` and `/option`) need two small things in `main.py`, and no more of it:

- **`python main.py --loop`** starts the loop once the window is up (`start_remote_control(loop=True)`, `START_LOOP_DELAY_MS` later), as if the loop button was pressed.
- **Command files.** `poll_commands()` (a `root.after` timer, every `COMMAND_POLL_MS`, so on Tk's thread) reads the oldest file in `COMMANDS_DIR` (`debug/commands/<time>-<id>.json`, `{"command": ..., "time": ...}`; `read_command()`), one per poll, which the bot wrote (`bot_lib.write_command()`, a file of its own, all at once - two commands sent close together are both kept and done in order). A file is removed whenever it is read, and the macro answers in `<time>-<id>.ack`: `taken`, or `dropped: <why>` (`acknowledge_command()`). The bot waits up to `BOT_COMMAND_ACK_WAIT` for that answer (`send_command()`) before it says it asked: a dropped command is told with its reason, and one the macro did not look at in time (its window stuck) is withdrawn (`withdraw_command()`), so that it cannot run later, and said so. Answers nobody read are removed after `BOT_ACK_KEEP`. An action that is queued (below) is acknowledged when the command is taken, not when the action ends; one that is given up later is only in the log. A command that is not one of `COMMANDS`, or is older than `COMMAND_MAX_AGE` seconds, is dropped - one left from before the macro started cannot close it. `start` starts the loop (nothing if it runs), `stop` is the [STOP] button, **`exit`** is what the window's X button does (`close()`): ask for the stop (which already lets go of everything), close the capture, write the status, wait up to `CLOSE_WORKER_WAIT` for the running work to end, release every key and the mouse, take the blackout away and close the window, which ends the process, and **`set`** changes a switch (`set_switch()`): the command carries `kind` (`need` or `option`), `name` and a true or false `value`, and is only read when the switch exists (`NEED_NAMES`; `OPTION_SWITCHES` and `SHOW_DEBUG_LINES_OPTION`) - the switch is persisted as its checkbox does it, and the checkbox in the window follows. **`rejoin`** and **`setup`** are the Leave & rejoin and Setup buttons, and **`trade_hub`** and **`leave_trade_hub`** the Trading Hub trips (`act_from_outside()`, `OUTSIDE_ACTIONS`) are **queued** and done one after the other in the order asked for (`drain_pending()`, looking every `REJOIN_STOP_POLL_MS`): a running loop is stopped first (once) and waited for up to `REJOIN_STOP_WAIT_MS`, after which the action is given up and the next one tried; another action that runs is waited for as long as it takes; a `stop` command stops what runs and leaves the queue alone. The loop is not started again afterwards, `/loop` does that. **`refresh_debug`** is the Refresh debug button (`refresh_debug_from_outside()`), done only when nothing is running, and the status file is written again after it. The next poll is scheduled before a command is done, and a command that raises is logged and does not stop the polling. Commands left from before are all dropped, and answered so, when the macro starts (`discard_commands()`). The list is fixed; the bot takes no argument that is run or opened.

`/exit` uses `exit` and not `stop`, so that the process ending is the proof that the loop ended and the inputs were released; only if the process outlasts `stop_timeout_seconds` does the bot kill it (`kill_macro()`: `taskkill /F`, `SIGKILL` elsewhere) and release the keys itself (`release_inputs()`: the keys of `cfg.HELD_KEYS`, and a mouse button only if it is down). The macro's process is found by its command line (`main.py`, not the bot; `macro_pids()`).

**Slow commands and the process list.** An interaction can be followed up for 15 minutes only; a longer `/update` or `/pull` answers in the channel itself (`say()`, `followup_needs_the_channel()`: when the interaction has expired, or Discord says the token is refused or unknown). The macro's process is found by its command line, `main.py` as a whole file name (`MAIN_PY`: not `test_main.py` or `main.pyc`), not the bot's. A listing of the processes that fails is not "the macro is not running": it is tried `BOT_PROCESS_LIST_TRIES` times, a second apart, and then said in the channel (`running_pids()`, `bot_lib.Said`) with nothing stopped, started, updated or killed on a guess; a stop whose final listing fails says it cannot tell whether the macro ended.

**Posting.** One post that fails does not end a look at the macro's files (`attempt()`): the rest of the look goes on. A failure's picture that is gone or refused is left out and the text says so (`post()`); a chart, the stale-status alert and the heartbeat are only recorded as done once they were sent, so they are tried again at the next look. A failure line whose text cannot be sent at all is lost (logged).

**Startup.** The bot's channel is fetched by `find_channel()`: from the cache, else with `BOT_CHANNEL_TRIES` tries and a wait that starts at `BOT_CHANNEL_RETRY_WAIT` and doubles; a `NotFound` or `Forbidden` (wrong `channel_id`, missing permission) is logged once with that hint and not retried, and the watching is then off while the commands still work. The watcher task is kept and a done-callback logs it if it ends by an exception. The slash commands are registered once, on the first `on_ready` (it comes again after every reconnect); if that fails the error is logged and posted in the channel, since no command shows up otherwise.

**Long messages.** Discord refuses a message over its limit, and with it the whole command, so every text the bot sends goes through `bot_lib.clip_message()` (`say()` for a command's answer): whole lines from the start, then "... (N more lines)", a code block kept closed; the lines of the update plan's git log are cut to `MAX_LOG_LINE`. A command that raises is logged and its kind of error said in the channel (`OwnerOnlyTree.on_error()`, `bot_lib.error_text()`), where it would otherwise stay on "thinking..." for ever; a `/screenshot` too big as a PNG is sent as a JPEG. A failed `/update` or `/rollback` is logged, says so in the channel where it still can, and lets go of the lock.

**What the bot keeps in mind.** `/loop` sets `desired_state` in `bot_state.json` to `running`, `/start` and `/stop` to `open` (the window, no loop), `/exit` to `stopped`; a restart by the bot (after a crash or an update) starts the loop only for `running`. While `/update` or `/rollback` has the macro stopped (`UPDATING`, `crash_check_due()`) the process is not looked for, so the stop is neither reported nor, with `auto_restart` on, undone halfway. A macro whose process is gone while it should be running is a crash, posted once (`crash_check()`) and again when it is back; a macro never started with `/start` or `/loop` (started by hand, say) is never reported as crashed, and one closed with `/exit` is not. With `auto_restart` on, the bot restarts it by itself, at most `auto_restart_max_per_hour` times an hour (`restart_allowed()`); off by default, since a macro that stopped because Roblox lost focus usually means the PC is in use. The watcher keeps `state.charts` to the charts that are there, and says once when the run log cannot be read (`log_unreadable`), again when it can. A macro that is running but not writing `status.json` is the stale-status alert, not a crash. Whatever the macro prints on its error output goes to `debug/logs/macro_stderr.log`.

### Update and rollback

`/update` takes whatever has been merged into `main` and runs it, from Discord, without a trip to the PC. It takes no argument: the remote and branch are the config's `update_remote` and `update_branch` (`origin` and `main`), and no other branch can be named in a message.

1. **The plan, before anything is changed** (`make_plan()` over a `GitRunner`, so the tests run it against real repositories made in a temporary folder): `git fetch` of that branch (the only thing it changes), then `update_plan()` decides from the commits. Same commit: "already up to date". **Local changes to tracked files** (`git status --porcelain --untracked-files=no`; untracked files and the git-ignored `debug/` don't count): refused, naming them - nothing is ever discarded. The PC **ahead** of the branch, or the histories **diverged**: refused. Only a fast-forward goes ahead, and the plan lists the commits it brings (`git log --oneline`) and how many lines changed in how many Python files (`python_changes_text()`, from `git diff --numstat`). `/update` then waits for the owner to press **Update** (`BOT_UPDATE_CONFIRM_TIMEOUT`; no answer or Cancel changes nothing); `/rollback` is the way back and does not ask.
2. **`apply_commit()`:** the macro is closed as `/exit` does it (cooperatively, killed only if it will not end), `git merge --ff-only` moves the PC to the fetched commit, and the files that changed are listed (`git diff --name-only`). The commit the PC was on is kept in `bot_state.json` (`previous_commit`) before any of this, so a failure halfway can still be rolled back.
3. If `requirements.txt` changed, `pip install -r requirements.txt` runs with the configured Python (`BOT_PIP_TIMEOUT`; the file is UTF-16, which pip reads); if it fails the macro is **not** started and `/rollback` is offered.
4. The macro is started again (as `/loop` does, waiting up to `BOT_START_CONFIRM` seconds for its status) **if it was running**, with its loop only if the loop was going (a window opened by `/start` or stopped by `/stop` comes back without one); one that was stopped stays stopped. If the loop does not show, the end of `macro_stderr.log` is posted with the `/rollback` offer.
5. **The bot restarts itself** when its own code changed - a file under `bot/`, `magic_numbers.py` (which it reads) or `requirements.txt` (`needs_bot_restart()`) - because Python does not reload a running program: first `import bot.discord_bot` is tried in the new code (`BOT_IMPORT_TIMEOUT`), and if that fails the bot stays on the old code and offers `/rollback`; otherwise it posts that, closes the Discord connection, and `main()` starts the same command again (`os.execv`; on Windows a new console window, since `execv` there leaves the terminal confused). The macro-side files alone (`main.py`, the references) need no bot restart.

`/rollback` goes back to `previous_commit` with `git reset --hard`, with the same refusals (local changes to tracked files; no earlier commit; a commit that is no longer in this copy), then steps 2 to 5. It undoes one `/update`; a later `/update` pulls `main` again. The tests run both against real throwaway repositories: a clean update, an untracked file, a changed tracked file, ahead, diverged, a bad branch, and the way back.

Nothing starts by itself with the PC: the bot is only ever started by hand, and the only time it starts itself is this restart after an update.

## Automated checks

`.github/workflows/check.yml` runs on every push and pull request (GitHub
Actions, a temporary Linux machine): `pyflakes` on `main.py`,
`magic_numbers.py`, `tools`, `bot` and `tests` (syntax errors,
undefined names, unused imports and local variables, duplicate definitions), then
`python tools/check_constants.py`, which fails on a `cfg.NAME` in `main.py` or `bot/`
that `magic_numbers.py` doesn't define, a constant nothing uses, a function of
`main.py` or `bot/` that nothing but tests calls, an UPPER_CASE name the docs write in backticks that no code file has (`docs/review.md` is left out: it records what was found), and a test that asks for `tmp_path`,
`monkeypatch`, `caplog` or `capsys` and never uses it; then `mypy main.py bot/bot_lib.py` (configured in
`mypy.ini`: it also checks the bodies of functions without type hints, and
ignores the Windows-only libraries it has no stubs for), then
`python -m pytest -q -n auto`. Every top-level function and method in
`main.py` has type hints (a few small nested helpers don't), using the aliases `Image` (a BGR screenshot or crop),
`Point`, `Box` and `Color` defined under the imports. A few lines carry
`# type: ignore[...]` for what the stubs get wrong (Windows-only calls, tkinter,
OpenCV). It can't run the macro itself - that
needs Windows, a screen and Roblox.

### Tests

`tests/` holds the pytest tests (`python -m pytest -q`, also run by the Action, there as `-n auto` with `pytest-xdist`, which spreads them over the machine's cores;
`pip install pytest-xdist` to do the same). The minigame players are tested by running them for minutes of fake time over real screenshots, so most of the time is
detection on the same frames again and again: the `world` fixture answers each of `MEMOIZED_DETECTORS` once per frame (`memoized_detectors()`) and makes the frames
read-only while they are remembered, so a test that changes one in place fails loudly (copy the frame first) instead of getting an old answer.
`tests/conftest.py` stands in for the libraries that only work on Windows or
with a screen (`pyautogui`, `pydirectinput`, `mss`, `pygetwindow`), so
`main.py` imports anywhere, and its `macro` fixture gives each test a fresh
`State` and `RunStats`, redirects every file the macro writes (run log, status,
failure folders, output log, reports) into a temporary folder, and records
clicks instead of making them. They cover:

- `test_pure.py` - the small pure functions (server link parsing, durations,
  colour masks, axis rounding, failure kinds);
- `test_discord_glue.py` - the bot's `watch_once()` run against a fake channel and the macro's files in a temporary folder (skipped when discord.py is not installed; CI installs it): the unreadable run log, forgotten charts, what an automatic restart starts. The command handlers inside `build()` need a Discord connection and are not run; their decisions are in `bot_lib` and `test_bot.py`.
- `test_bot.py` - the Discord bot's logic (`bot/bot_lib.py`): the config, who is obeyed, the messages made from the files `write_status()` and `record_minigame_result()` write, and the alert, `/pull` and control logic (failure lines, log offsets, rate limiting, stale status, what can be pulled and how, the command file, crash and restart decisions, update and rollback against real git repositories);
- `test_state.py` - the output log's levels, rotation and reopening, the console handler, stuck-need disabling, `process_needs()` (resolved, unresolvable and not-cleared cases), the sick handler on real screenshots (`tests/sick/`: the prompt, the dialog, the pet view), `RunStats`, `status.json`, the
  failure folders, the quiet and idle-respawn checks (including that a respawn
  leaves the no-progress rejoin clock alone), stopping, and the open `output.log`;
- `test_detection.py` - the need-strip capture and its cache, and the
  popups and stray windows against the real crops in `ref/` (including that every `POPUP_TEMPLATES` entry has a crop that fits its box);
- `test_icon_screenshots.py` - every real screenshot in `tests/icon_detection/<folder>/` (named `<scene>__<needs>.png`, the needs being what the test expects) (the need bar over dark, bright, busy and white scenes) run through the circle search and the matching; each must be named for the needs on its bar, so a screenshot added without a label fails;
- `test_loop.py` - the whole workflow loop run for a moment with the game stubbed out (idle respawns, the resource monitor starting and stopping, the report being written);
- `test_capture.py` - the input capture against a made-up keyboard, mouse and screen (events, both coordinate systems, click frames, skipping unchanged frames, the summary, the zips, stopping by itself, the hotkey toggle and pruning, and the automatic recording of minigame runs: the frame budget, its thinning, the outcome, the zip);
- `test_hauntlet_2.py` - reading the Hauntlet 2 screen against crops of a real run;
- `test_ghost_gallery.py` - ghost and boss finding against crops of a human's run, the aim and the player with a fake clock and mouse;
- `test_hauntlet_2_play.py` - the timer and doors against crops of that run, then the room tracking, the choices (door, item), the walk and the whole loop with a fake clock and keyboard;
- `test_features.py` - the daily money reset, the run-every-need sequence, the finish screen and the like;
- `test_config.py` - the real need tables in `magic_numbers.py`, without pinning any value: every entry is well formed (known keys, on-screen positions, movement keys, a `final_click` is on screen), has a handler, and the handler carries out exactly what the entry says (run over whichever needs exist, so `HALLOWEEN` off just drops `sick`). The handler tests elsewhere use made-up entries; this is what keeps the real ones honest;
- `test_report.py` - the run-report parser against the exact log wording the
  macro writes (a round trip through `log_run_event()`), and the chart itself.
- `test_candy_corn.py` - the candy corn total in the HUD, read off crops of real screens, and what a minigame run earned.
- `test_stitches_spotlight.py` - the ring's gold edge line and the camera turns it asks for, on frames of a real round, and the player's key handling.
- `test_tools.py` - `tools/check_constants.py` passes on the repo and finds what it is for.

Fixtures are synthetic images or the lossless crops in `ref/`; compressed
screenshots (`.webp`) don't match exact colours, so they aren't used.

**Screenshots are named for what they show**, not by the number the screenshot tool or a capture gave them, and where a test reads something off a
picture the name says it, so a new file is checked against its own label: `tests/icon_detection/<folder>/<scene>__<needs>.png` (the needs on the bar,
left to right, joined by hyphens), `tests/hauntlet_2/doors/<kind>-<kind>-<kind>[__note].png` (the three doors' kinds) (a few that are not three doors are named for the scene: `fork_hall__no_doors`, `phone_arrow`, `room1_close`, `thin_one`), `tests/hauntlet_2/item_bars/<item>[_x2]-...[__note].png`
(the slots), `tests/hauntlet_2/room_panels/<NN>.png` (the room number), `tests/candy_corn/hud_<total>.png`. The pictures of `tests/halloween/hauntlet_2/with_others/` (rooms
of a run with other players in them) are named `room<NN>_<what>.png`.

**Tests don't pin tuned values.** `magic_numbers.py` is edited constantly (timings,
routes, intervals, the `HALLOWEEN` flag), so a test must pass for any sensible
tuning: it states what it is about itself (the daily-reset tests set
`MONEY_RESET_HOUR` to 17; the teleport-walk and sick tests give a made-up need
an entry of its own, so they don't depend on the real table or on `HALLOWEEN`,
which decides whether `sick` exists) or reads the constant (`cfg.QUIET_LOG_INTERVAL`,
not "5 minutes"). Waits are tested on a fake clock (`fake_clock`), not by timing
them, and the loop test runs until it has seen what it checks instead of for a
fixed time, so a slow machine only makes it slower.
`python tools/retune_check.py` runs the suite against changed copies of the repo
and names any test that breaks - run it when a change could depend on tuned
values, not on every commit (about 30 runs, about 15 minutes with the 4 workers each uses; `python tools/retune_check.py halloween_off timings_x20` runs just those).
The variants: every timing and walk step scaled by 0.05, 0.5, 1.7 and 20; other
intervals, hours and counts changed; counts at their lowest and highest; the
event flag off; needs removed from or added to the table; button names reordered;
helicopter for every need; thresholds nudged up and down, colour tolerances
widened, search boxes and click positions moved; other key bindings; behaviour
flags flipped (`SAVE_NEW_NEEDS`, `MATCH_ONLY_LEFT_HALF`); two other time zones; a
different hash seed; run from another working directory; a `debug/` folder with a
real config and logs in it; extra files in `ref/`; the tests in reverse and in
shuffled order; and each test file run on its own. The one test file that is *meant* to depend
on recognition settings, `test_icon_screenshots.py`, is skipped by the two
variants that change those settings on purpose (`ICON_SHIFT_TOLERANCE` to 0,
`MATCH_ONLY_LEFT_HALF` off).
