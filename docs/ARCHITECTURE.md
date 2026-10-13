# Architecture

Detailed technical walkthrough of `main.py`. This is for understanding or
modifying the code - if you just want to run the macro, see the main
[README](../README.md) instead.

Almost everything lives in `main.py` (including the run-report chart - see [Debugging an unattended run](#debugging-an-unattended-run)), organized top to bottom as a sequence
of `# === SECTION ===` blocks; this doc follows that same order. The one
exception is `magic_numbers.py`, which holds every tunable constant (see
[Configuration reference](#configuration-reference)) - `main.py` does
`import magic_numbers as cfg` and refers to them as `cfg.NAME`
everywhere else (`tools/check_constants.py` fails on a `cfg.` name that
`magic_numbers.py` doesn't define - a typo pyflakes can't see). The docs below
still write constants by their bare name.

## The workflow lifecycle

The only thing that runs is **`run_workflow_loop()`** (the GUI's loop
button): it respawns once up front (so the loop always starts from a known,
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
   isn't in `ENABLED_NEEDS` or was disabled as stuck (see
   [Stuck needs](#stuck-needs)) - a `None` result is printed and skipped
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
  unattended-run tools `log_output()`, `save_failure_report()`,
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
`ICON_MATCH_THRESHOLD` (0.93) means "not confident this is anything we've
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
`NEED_GONE_POLL_INTERVAL` (3.5s) until a check finds the target need gone - one
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
   `BUTTON_MAX_COUNT` (5), then sort left-to-right.
6. `refresh_button_mapping()` zips that left-to-right order against
   `BUTTON_NAMES = ["hungry", "thirsty", "dirty", "potty", "sleepy"]` and
   stores the result in `state.button_positions`. This assumes the five buttons
   are always laid out in that fixed order on screen - if Roblox ever
   changes that order, this mapping (and only this mapping) would need
   updating. It clears the previous mapping first, even when detection fails,
   so a failed scan never leaves a stale position for `click_need_button()`;
   when fewer than five buttons are found it logs `button scan found N of 5`.

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

Which needs actually run automatically is decided by one set,
`ENABLED_NEEDS` (`magic_numbers.py`, the first thing defined in that
file). A detected need that isn't in it is logged and skipped, not
resolved. Currently enabled: `hungry`, `thirsty`, `dirty`, `potty`,
`sleepy`, `catch`, `pet`, `ride`, `walk`, `choose`, plus the
teleport-walk needs `cafe`, `salon`, `sick`, `pizza`, `school`, `beach`,
`camping`, `bored` (called out with their own inline comment in the set
literal, since they leave the map and run considerably longer than
everything else).
The GUI's **Debug** tab has a **Needs** subtab with a button per handler that
runs it directly regardless of `ENABLED_NEEDS`, and a button that runs every
handler once, one after another (`run_all_need_handlers()`: each with the
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
| `catch` | `CatchNeedHandler`: open backpack → toys → squeaky toy → equip → close backpack → wait `CATCH_WAIT_AFTER_EQUIP` → hold zoom-in → throw the toy by clicking empty space `CATCH_THROW_COUNT` (3) times, `CATCH_EMOTE_DELAY` (5s) after each → unequip. A fixed sequence: it doesn't wait for the icon (the next check sees whether it cleared). |
| `pet` | `PetNeedHandler`: `focus_pet()` (see below, with `PET_FOCUS_CLICK_DURATION`), click the screen centre, move to `PET_SWIPE_START_OFFSET` above it, then hold the mouse down and swipe once down to `PET_SWIPE_END_OFFSET` below it over `PET_SWIPE_DURATION` (8s), releasing the button in a `finally`. Same as `catch` - always the full fixed duration. |
| `choose` | `ChooseNeedHandler`: `focus_pet()` (see below), find the exact-color button (`CHOOSE_BUTTON_COLOR`, since it has no distinguishing icon), hover to it slowly (`hover_click`) and click, then click screen-center to dismiss the menu. It doesn't wait for the icon to clear (it's gone at once; the next check confirms). |
| `ride` | `RideNeedHandler`: step back, mount (`e`), walk forward briefly, then `equip_favorite_vehicle()` (backpack → vehicles → first vehicle → equip → close backpack), hold `r` (`KEY_HELICOPTER`) for `RIDE_R_HOLD_DURATION` (1.5s), then `walk_alternating(("w", "s"), NEED_GONE_MAX_WAIT, need_name="ride")` - up to `NEED_GONE_MAX_WAIT` (75s), ending early the moment "ride" is confirmed cleared (see below). |
| `bored` / `beach` / `school` / `cafe` / `salon` / `pizza` / `camping` / `sick` | `TeleportWalkNeedHandler`, configured per need in `TELEPORT_WALK_NEEDS`: teleport to the nursery or dealership (`teleport_to()`), hold each `(key, seconds)` step with `hold_key()`, then `wait_until_need_gone()` (see [Waiting for a need to clear](#waiting-for-a-need-to-clear)). `process_needs()` respawns afterwards. An entry may also set `final_click`, clicked after the last hold (used by `sick`: after pressing `e` and waiting `SICK_CONFIRM_WAIT` the handler looks for the doctor's dialog, `detect_sick_dialog()`. Recognised: it clicks Yes. Not recognised and the pet view is not open: it logs `sick: dialog not recognised after e (pet focused: False) - clicking Yes anyway` and clicks Yes all the same, and the wait for the icon says whether it worked (in run 2 the recognition failed on 27 of 27 real attempts while the plain click had worked in run 1, so it is not trusted to fail the need). Not recognised and the pet view *is* open - the prompt is then a hand button `e` doesn't press: it logs `sick: no dialog after e (pet focused: True) - clicking the prompt`, backs out of the pet view (`dismiss_stray_windows()`), clicks the prompt on screen (`prompt_pos`, `SICK_PROMPT_POS`) and looks once more. Only if the dialog still isn't up has the need failed: the character respawns and the handler returns False at once, with no wait for the icon to clear, and `process_needs()` logs the failure), `helicopter` (see below) and `max_wait` (how long to wait for the need to clear afterwards; default `NEED_GONE_MAX_WAIT`, 75s). `sick` waits `SICK_PRE_E_WAIT` after its last hold before pressing `e`. Which table is used depends on `HALLOWEEN` - see below. |
| `walk` | `WalkNeedHandler`: `walk_alternating(("a", "d"), NEED_GONE_MAX_WAIT, need_name="walk")` - up to `NEED_GONE_MAX_WAIT` (75s), same early-exit as `ride` above. |

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
form; not during setup, which keeps it open for ~8s on purpose and closes it itself), then
`dismiss_stray_windows()`, then, if `HALLOWEEN` is on, `close_minigame_win_screen()`
(whenever the minigame win screen is up - the red GAME OVER! banner and the green
NICE! button, `detect_minigame_victory()` - NICE! is clicked, the character
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
(`load_templates()` reads each folder's crops once and re-reads them only when a file is added, changed or removed.)
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
open on `BACKPACK_CLOSE_CONFIRMATIONS` (2) checks in a row - a handler opens the
normal backpack for a few seconds on purpose, so one sighting could be a
handler in the middle of its steps, but two `UNSCREW_TASK_INTERVAL` apart is a
backpack open for longer than any handler needs. The paycheck check is skipped
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

**Popup templates.** Everything recognised by a crop in `ref/popups/` is looked up the same
way, by `popup_found(img, name)` / `popup_visible(img, name)`: the crop is matched
(`cv2.matchTemplate`, `TM_CCOEFF_NORMED`) within a box and counts as found when the score reaches
a threshold - the crop's centre is returned too, for the BACK button's click. Each name's box and
threshold are in `POPUP_TEMPLATES` (`paycheck`, `sick_dialog`, `backpack_normal`, `backpack_back`) or, for
a stray window (and `pet_focus`), in `STRAY_WINDOWS` with `STRAY_WINDOW_MATCH_THRESHOLD`. The
detectors (`detect_paycheck()`, `detect_sick_dialog()`, `detect_pet_focus()`, the title check in
`detect_backpack_normal()`, the BACK button in `close_backpack_if_open()`) are that lookup plus
whatever they do with the answer, and `dismiss_stray_windows()` walks `STRAY_WINDOWS` through it. A
new crop is a file in `ref/popups/` plus an entry in one of the two tables. A name with no crop file is
never found. The minigame title (`identify_minigame()`) and overlay (`detect_minigame_overlay()`) crops
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
missed another (`ref/test` is for more). `detect_backpack_open()` is either. `close_backpack_if_open()` presses
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
color is unique alone (Yes green = `GREEN_BUTTON_COLOR`, No red = the
Exit Home button), so it needs the Yes green (`GREEN_BUTTON_COLOR`, at
least `MINIGAME_POPUP_MIN_BUTTON_PIXELS` px) with the No red
(`MINIGAME_POPUP_NO_COLOR`) within `MINIGAME_POPUP_NO_MAX_DX` / `_DY` px to
its left - except the sick need's own "Ask Doctor Heart to heal your family?" dialog, which has the same Yes/No look but is the sick handler's to answer (its Yes is `SICK_FINAL_CLICK_POS`): `ref/popups/sick_dialog.png`, within its `POPUP_TEMPLATES` box, makes the detector ignore it. (Before that, the every-4s in-task check took it for a minigame popup and clicked its decline positions in the middle of the sick sequence - `unknown` popups clustered in the sick handler in the logs.) `identify_minigame()` then says which one it is: it matches the
first line of the title (`MINIGAME_TITLE_BOX`) against the crops in
`ref/halloween/` (`cv2.matchTemplate`, best score at least
`MINIGAME_TITLE_MATCH_THRESHOLD`); a minigame's name is its file name,
lower-cased (`ghost_gallery.png` → `ghost_gallery`, `hauntlet_2.png` → `hauntlet_2`), so a new one is just a
new crop - plus an entry in `MINIGAME_LABELS` and a player in
`MINIGAME_PLAYERS`.

Each minigame has a switch in the game config (`hauntlet_2_enabled`,
`ghost_gallery_enabled`, both **off** by default; "Play ..." checkboxes on the
Options tab). `minigame_popup()`:
- **switched off, or not recognised** - clicks `MINIGAME_POPUP_DONT_SHOW_POS`
  ("Do not show again this session") and `MINIGAME_POPUP_NO_POS`;
- **switched on** - `play_minigame()` clicks Yes (`MINIGAME_POPUP_YES_POS`)
  and plays it, as below. Minigames are time-sensitive, so this also happens
  when the popup turns up while a handler is waiting in
  a handler's waits (see [Per-cycle checks while a task
  runs](#need-handlers)): the minigame is played right
  then and the handler abandoned (`TaskInterrupted`). Every held key and the mouse button are released first - at the start of `play_minigame()`, since the handler may have been holding one (a walk, the pet swipe) when it was interrupted, and again as the interruption is raised. Where it left the
  character is unknown afterwards, and the next check sees what is still
  needed.

*Playing.* No needs can be seen while a minigame runs, so both end on the
victory screen instead - a red GAME OVER! banner over a green NICE! button,
found by exact color (`detect_minigame_victory()`, checked every
`MINIGAME_VICTORY_CHECK_INTERVAL`), which is the same for both. While it plays, `minigame_popup_checks()` runs `dismiss_stray_windows()` (and `detect_paycheck()` if it closed nothing) at most every `MINIGAME_POPUP_CHECK_INTERVAL` (4s), including during the start waits (`minigame_wait()`) - the in-task `unscrew()` can't, since the minigame is played from it. Then NICE!
(`MINIGAME_VICTORY_BUTTON_POS`) is clicked and `MINIGAME_FINISH_WAIT` waited
out (`click_nice_until_gone()`, which looks again and clicks up to
`MINIGAME_VICTORY_CLICK_TRIES` times while the win screen is still up, logging a
failure if it never closes), and the character respawns. If the victory screen hasn't shown after
`MINIGAME_MAX_DURATION` it gives up with a logged failure (and releases every
input). A minigame that was won counts as progress for the no-progress rejoin (`state.last_progress`); one that failed - never started, left early, timed out - does not, or a popup every few minutes would keep the 20-minute rejoin clock from ever running out. Either way it counts as a detection (no needs can be seen while it runs).

*Telling whether it is on.* Each minigame has an overlay along the top of the
screen while it runs (Hauntlet 2: the ROOM counter; Ghost Gallery: the TIME
LEFT / GHOST GALLERY / Score banner), recognised by a crop in
`ref/overlays/<minigame>.png` within its box in `MINIGAME_OVERLAYS`
(`detect_minigame_overlay()`, at least `MINIGAME_OVERLAY_THRESHOLD`). A
`MinigameWatch` made by `play_minigame()` is fed every screenshot the player
takes (`minigame_look()`, `minigame_won()`, `minigame_wait()`): once the overlay has been seen,
losing it for `MINIGAME_OVERLAY_LOST_TIMEOUT` (10 s) means the minigame was left
(game over without the victory screen, kicked out) and it stops there; never
seeing it within `MINIGAME_OVERLAY_START_GRACE` (60 s) of the start wait means it
never started. Either is a logged failure with that reason, instead of waiting
out `MINIGAME_MAX_DURATION` (600 s).
- `play_hauntlet_2()` waits `MINIGAME_START_WAIT` (50 s), then goes through the rooms - reads the screen, picks a door
  and walks to it, room after room - until victory or until the overlay says it's over. See
  [Playing Hauntlet 2](#playing-hauntlet-2).
- `play_ghost_gallery()` waits `MINIGAME_START_WAIT`, then holds the mouse
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
(`state.attempted_last`). An attempted need still on screen has its streak in
`state.attempt_streaks` raised; one that's gone has it dropped. When an *enabled*
need's streak reaches `NEED_STUCK_CHECKS` (5) - it's supposed to be getting
resolved, yet it never goes away - it's added to `state.disabled_needs` and
`get_need_handler()` returns `None` for it from then on, same as a need that
isn't in `ENABLED_NEEDS`. A single check without it, even one that detects
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
   `detect_white_screen()`) for `WHITE_SCREEN_REJOIN_AFTER` (30s) in a row. The
   game shows a white screen for a moment on a respawn, teleport or change of
   location, so only a long one counts; a bright scene is nowhere near it (the
   real white transition in `tests/icon_detection/` is 93% white, every other
   screenshot under 10%). It is looked for wherever `rejoin_reason()` is
   asked, which is every cycle and every `UNSCREW_TASK_INTERVAL` mid-task, and
   the clock (`state.white_since`) restarts whenever the screen isn't white.
   (Needs stay visible on the white screen, so nothing else notices it.)
3. *Stuck* - `DISABLED_NEEDS_REJOIN_THRESHOLD` (4) needs have been disabled as
   stuck.
4. *Stalled* - no need has been resolved for `NO_PROGRESS_REJOIN_INTERVAL`
   (20 minutes; `state.last_progress`, set when the loop starts, whenever a need is
   resolved and by every successful `leave_and_rejoin()`): the game is up but
   nothing is working, whatever the cause.
5. *Scheduled* - it has been `REJOIN_INTERVAL` (two hours) since the last
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
need has been *detected* for `NO_DETECTION_RESPAWN_INTERVAL` (4 minutes) - it
may be stuck somewhere the icons don't show from. It counts from
`state.last_detection` (set whenever a need is detected, and when the loop starts, the game is rejoined or a minigame is
played), and again from each respawn, so it repeats at most once per interval.
It never touches `state.last_progress`, so the 20-minute stalled rejoin above
still comes exactly when it would have: in a long stretch with nothing
detected the character is respawned every 4 minutes and the game rejoined at
20. The log line is `no need detected for N min - respawning`.

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
     (`GREEN_BUTTON_COLOR`) to show up around `REJOIN_JOIN_POS`
     (`detect_play_button()`).
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
  waits `UI_SETTLE` for the placement panel, calls `set_new_lure()` (and gives
  up with False if that does), then respawns. `side_quest()` runs it - and
  `tree_collect()` - through `run_side_action()`, inside `task_unscrew()` like a
  need handler, so popups and stray windows are handled and a minigame or
  rejoin (`TaskInterrupted`) just means it's tried again next cycle.
- **`set_new_lure()`** - clicks `LURE_NEW_POS_1` then `LURE_NEW_POS_2`
  (`UI_SETTLE` after each - they used to follow the key press with no wait), the two clicks that place a fresh lure. Split out from
  `lure_collect()` specifically so this placement step can be tested and
  tuned on its own, without walking to the old lure first each time.
- **`tree_collect()`** - holds `d` for `TREE_COLLECT_WALK_DURATION` (2s),
  then `s` for `TREE_COLLECT_BACKWARD_DURATION` (1s) to line up with the
  tree, then presses `KEY_INTERACT` and respawns (like `lure_collect()`).
- **`setup_game()`** - respawns, clicks `SETUP_LOCK_HOUSE_POS` to lock the
  house, then opens the backpack and clicks through
  `SETUP_BACKPACK_SETTINGS_POS` → `SETUP_SORT_MENU_POS` →
  `SETUP_FAVORITES_POS` → `SETUP_CONFIRM_POS` to set its item filter to
  favorites only, then closes the backpack and calls `disable_trades()`. It
first closes any stray window (up to `SETUP_STRAY_TRIES`; see [Stray
windows](#need-handlers)) and runs inside `task_unscrew()`.
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
(sets `STOP_EVENT` (a `threading.Event`: the waits sleep on it, so a stop is noticed at once), so the run unwinds at the next `check_running()`, within
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
  in `TREE_HARVEST_YIELD` (16) increments. It goes up with each harvest and
  is put back to 0 every day at `MONEY_RESET_HOUR` (17:00, local time) by
  `reset_money_if_due()` (see `side_quest()` below), or by the Debug tab's
  **Reset Config** button.
- **`money_reset_boundary`** - the last daily reset moment already dealt with
  (a timestamp; `None` on a fresh or older config). `side_quest()` compares it
  with the most recent 17:00 (`last_money_reset_time()`): a later one means the
  total is reset and the new moment saved. The first time it is seen (`None`) it
  is only recorded, so updating doesn't wipe the day's total.
- **`tree_timer`** / **`lure_timer`** - the timestamps `tree_collect()` /
  `lure_collect()` are next due. Both default to "due immediately"
  (`time.time()`) when the config is fresh.
- **`side_quest_enabled`** - on/off switch (default on) for
  `side_quest()`. It's a checkbox in the GUI, and can be flipped while a
  workflow runs.
- **`resume_on_focus_loss`** - the **Resume after focus loss** checkbox (default
  off); see [Stopping & focus safety](#stopping--focus-safety).
- **`record_next_run`** - the **Record the next run (low quality video)**
  checkbox on the Debug tab's Other subtab (default off); see [Recording a run](#recording-a-run). Cleared, and
  the checkbox unticked, when the run that it was for starts.
- **`private_server_link`** - the private server link `leave_and_rejoin()`
  joins, typed into the GUI's entry box (saved when you press Enter or click
  away). It lives here rather than in `magic_numbers.py` because it's a key
  to your server and the source is in a git repo. **Reset Config** keeps it.

During a run, this file is read and updated by:

- **`side_quest()`** - called right after `unscrew()`, every cycle of
  `run_full_cycle()`'s loop (so on the same cadence as the paycheck
  check); first applies the daily money reset (`reset_money_if_due()`), then does
  nothing if `side_quest_enabled` is off. While
  `money_collected` is below `MONEY_COLLECTED_TARGET` (200) *and*
  `tree_timer` has passed, it calls `tree_collect()`, adds
  `TREE_HARVEST_YIELD`, and pushes `tree_timer` `TREE_CHECK_INTERVAL`
  (10 minutes) out. Once `lure_timer` has passed, it calls
  `lure_collect()` and pushes `lure_timer` `LURE_RECOLLECT_INTERVAL` (4
  hours) out. Each action only updates the config if the call actually
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
- The **Main** tab has the loop/stop row and the Respawn, Setup and Leave &
  rejoin buttons; the **Options** tab holds the persisted switches (Run side
  quest, Resume after focus loss, Play <minigame>) and the private server link;
  the **Debug** tab has two subtabs: **Needs** (a button that tests every need
  in turn, and one per need handler) and **Other** (Record the next run,
  Refresh debug, Reset Config).
- **`run_on_ui_thread(func, timeout)`** is how the worker thread touches Tk
  (which is only safe from its own thread): it queues `func`, waits until it
  has run or `timeout` seconds pass (`None` = as long as it takes, for a
  dialog such as the new-need name prompt) and returns what it returned.
- **`watch_stop_hotkey()`** starts a thread that watches two hotkeys in any
  window (holding one doesn't retrigger): `STOP_HOTKEY` stops a running macro (it
  does nothing while nothing runs), so you can take over the computer without
  reaching for the GUI, and `CAPTURE_HOTKEY` starts or stops an
  [input capture](#input-capture). The action is handed to Tk's thread.
- **`add_config_checkbox(key)`** makes a checkbox for a persisted switch. It is
  deliberately not in `action_buttons`: the switches stay usable while a
  workflow runs, which is safe because `update_game_config()` is locked.
- **`DebugCapture`** redirects `sys.stdout` into the on-screen console
  (`self.debug_text`) for the lifetime of the GUI, so every `print()`
  anywhere in the macro shows up there automatically (`write()` only logs
  and queues - from any thread - and the Tk thread puts the queue on the
  widget every `CONSOLE_DRAIN_INTERVAL_MS`, since Tk isn't thread-safe), each line starting with
  the time (`[HH:MM:SS]`). The widget keeps only its last `CONSOLE_MAX_LINES` lines (`trim()`) - it would otherwise hold everything printed, about 25 MB of memory per 100,000 lines - while `output.log` keeps all of it. A print starting with `\r` is a status line: it
  replaces the previous status line instead of adding one, and goes into
  `output.log` only the first time (this is the "Waiting for a need..."
  counter).

### Input capture

Press `CAPTURE_HOTKEY` (`m`) in any window to start recording and again to stop. It is for working out
how something is played by hand - the Hauntlet 2 rooms, say - and gives the macro's author lossless
frames and the inputs that went with them. It is separate from **Record the next run** (a low-quality
video of a macro run): it works whether or not the macro is running, and the macro's own key presses
and clicks are recorded too, since `GetAsyncKeyState` sees them like any others.

Each capture is a folder in `debug/captures/` (`CAPTURES_DIR`, git-ignored, emptied by **Refresh debug**;
the newest `MAX_CAPTURES` are kept) named `capture_<date>_<time>`. **Getting one to someone else:** when a
capture stops, `zip_capture()` (on a thread of its own) also splits it into zips of at most `CAPTURE_ZIP_MB`
(25, since chat attachments are limited to 30 MB) in the capture's `zips/` folder:
`capture_<time>_part01.zip`, `_part02.zip`, ... The frames are in time order, none twice; part 1 also holds
`events.jsonl` and `info.json`. They are stored, not compressed (PNGs don't shrink), so a 25 MB part is about
14 frames. Attach them all (send them in order); a zip arrives byte for byte, unlike a pasted image, which
is recompressed. For a zip that already exists (a capture zipped by hand, say) `python tools/split_zip.py big.zip` cuts it into
`big_part01.zip`, ... of at most 25 MB (or the number of MB given as a second argument), every file kept whole. The files themselves stay in the folder:

- `frame_<n>_<seconds>.png` - `grab_screen()` (the Roblox window at the reference size, the macro's own
  window included if it is on top), lossless. The screen is looked at every `CAPTURE_FRAME_INTERVAL`
  (0.5 s) and right after every mouse click, but a frame is only **saved if it differs from the last one
  saved**: at least `CAPTURE_CHANGE_FRACTION` (0.2%) of the picture, shrunk to 320x180, has moved by more
  than `CAPTURE_CHANGE_LEVEL` grey levels. A character walking or a door opening does that; a ticking
  timer or a blinking light doesn't. A click always gets a frame, and so does every `CAPTURE_MAX_SKIP` (3 s)
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

**Minigame runs are recorded automatically.** `play_minigame()` starts a capture of its own after taking the popup's Yes
(`start_run_capture()`) and stops it when the run is over, whichever way it ends - won, left, timed out or stopped
(`finish_run_capture()`, which also writes `outcome` into `info.json` and zips the capture as above). It is the same recorder, so it
has the macro's own key presses and clicks and the frames on the same clock, but in `debug/minigame_captures/`
(`RUN_CAPTURES_DIR`, the newest `MAX_RUN_CAPTURES` kept, named `<minigame>_<date>_<time>`) so the hotkey's captures and these
never delete each other, and with a **budget of `RUN_CAPTURE_ZIPS` zips** (5, so up to about 125 MB, spread over the whole run): the
zips are counted as `zip_capture()` will make them (`plan_zip_parts()`, the events in the first). A lossless frame is 1-3 MB, so
that is some 60-80 frames; when a run would need more zips, `InputCapture._keep_within_budget()` deletes
every other saved frame - never the newest, and the first stays - and from then on saves frames twice as far apart, so what is
left is spread evenly over the run however long it is. It is checked after every saved frame and once more when the capture
stops (the events have grown by then). The events of the deleted frames stay in `events.jsonl`, followed by a `frame_dropped`
line naming the file, so a reader can tell; `info.json` has `frames_dropped` and `max_zips`, and `stopped_because` is "the minigame
ended". The folders are big (the frames and then the same again as zips), hence `MAX_RUN_CAPTURES` is 5. Switch it off with `MINIGAME_CAPTURE`; it isn't started while the
hotkey's capture is running (two recorders would only get in each other's way), and a capture that can't be started or finished is a
message, never a reason not to play. If more frames per run are wanted the choice is more zips or lossy frames (which would no
longer be the exact pixels the readers are tuned on).

Keys and mouse buttons are read by polling `GetAsyncKeyState` every `CAPTURE_POLL_INTERVAL` (10 ms) -
the same call as the stop hotkey, so no extra package is needed - and the position with `GetCursorPos`.
The cost of polling is that **the mouse wheel isn't seen** (no async state to read) and a key pressed
and released between two polls is missed. A capture stops by itself after `CAPTURE_MAX_MINUTES` (15) or
when less than `CAPTURE_MIN_FREE_MB` of disk is free - a frame is roughly 1-3 MB; how many are saved depends on how much moves, but at worst two a second - and the next
press of the hotkey then tidies it up and starts a new one. Screenshots and input are written from two
threads of the capture's own; the hotkey is watched by the GUI's hotkey thread (with the stop hotkey) and
acted on in Tk's thread, so typing an `m` into the private server link box starts a capture too.

### Reading Hauntlet 2

`read_hauntlet_room()` (the HAUNTLET 2 READER section of `main.py`) returns the number on the ROOM panel
at the top of the screen, or `None` when it can't be read. It only reads: nothing here decides or presses a key.
The panel is flat UI that never moves or scales, so the digits are matched exactly: the white pixels
(`HAUNTLET_DIGIT_WHITE_LEVEL`) inside `HAUNTLET_ROOM_BOX` are split into connected pieces, each piece is cut
out, shrunk to `HAUNTLET_DIGIT_SIZE` and compared with the saved digit pictures in `ref/hauntlet_2/digits/`
(`0.png` ... `9.png`, cut from a real run) by mean difference. The best must be under
`HAUNTLET_DIGIT_MAX_DIFF` (20); on a 256-frame run (rooms 1-29, three layers of the hotel) every readable frame
matched at 0.0 and the two most alike digits are 43.7 apart. No panel (the white transition, the lobby, a
door close-up that hides it), no digit pieces, more than `HAUNTLET_ROOM_MAX_DIGITS`, or a piece that matches
none of the digits all give `None` rather than a guess. `tests/test_hauntlet_2.py` runs a crop of each of the 29
rooms (`tests/hauntlet_2/room_panels/`) back through the reader.

**Items.** `read_hauntlet_items()` returns the item slots along the bottom of the screen, left to right, as
`HauntletItem(name, count, pos)`. The slots are round white-ringed icons about 115 px apart, found as circles in
`HAUNTLET_SLOT_BAND` (`find_item_slots()`, Hough, radius `HAUNTLET_SLOT_RADIUS`; a slot that has just changed is a
little bigger). Each slot's `HAUNTLET_ITEM_CROP`-px square is matched by correlation, as a grey picture so the slot's
bright blue or dim teal background matters little, against the pictures in `ref/hauntlet_2/items/<name>/` (a folder per
item, several pictures each - bright, dim, slightly enlarged). The best must reach `HAUNTLET_ITEM_MATCH` (0.75) or the
item is `unknown`. They were cut from the 382 slots of a 256-frame run: `phone`, `key`, `gold_key`, `cyan_potion`,
`heart_potion`, `red_potion`, plus `backpack` (the lobby's button, which sits where a slot would and is left out,
`HAUNTLET_NOT_ITEMS`); every slot of that run is read as the item it is (60 ms a frame). The wand was not in it, so
it has no pictures yet. **Learning:** with `learn=True` an unknown slot is saved as `ref/hauntlet_2/items/unknown_<n>/1.png`
(`learn_hauntlet_item()`) and is known from then on; renaming the folder to what it is names it. The count is the number
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
950-1190 white pixels in `HAUNTLET_TIMER_BOX` when it is, none when it isn't - it is hidden in a room's first moments and
while the player passes through a door, which is also when the screen still shows the *last* room's doors. `find_hauntlet_doors()`
finds the three doors of a room from the TVs above them: each TV shows its number (the players standing before that door)
in a light cyan, `(206, 255, 255)`, that no other pixel of the game has in that quantity; `find_door_tvs()` takes the pieces
of `HAUNTLET_TV_AREA` px below `HAUNTLET_TV_MIN_Y` and wants three of them in a row (the same height within
`HAUNTLET_TV_ROW_TOLERANCE`, evenly spaced within `HAUNTLET_TV_SPACING_TOLERANCE`), else `[]` - a fork's hall has no TVs, and neither do
the close-ups. The spacing between TVs (330-410 px as the camera zooms) is the picture's scale, so the boxes under each
TV (`HAUNTLET_LOCK_LIGHT_BOX`, `HAUNTLET_DOOR_BOX`, measured at a spacing of `HAUNTLET_DOOR_SPACING`) are scaled with it.
`HauntletDoor(x, y, kind)` gives each door's `kind` from its pixels (`hauntlet_door_kind()`), the first that fits:

- `locked` - the padlock's red light above the door: 40-99 red pixels, none on other doors;
- `dark` - over 40% near-black pixels (44-74% on the doors that look so, at most 37% on the others);
- `mystery` - the pale grey "???" door, brightness 180-203 where every other door is under 164;
- `boarded` or `plain` - by the planks: the share of the door's pixels on a strong, near-horizontal edge
  (`HAUNTLET_PLANK_*`: the grey picture smoothed, Sobel, edge directions within 35 degrees of horizontal). Plain doors
  are 0.03-0.13 (median 0.06), boarded ones 0.11-0.24 (median 0.18); the threshold, 0.103, is the best one over the
  doors of 25 rooms (the first and second layers separate best, the third a little worse), and picked in turn without one
  room of them it still gets 94% of that room's doors right. It was found by trying measures one after another - blob counts
  for the nails, edge density, edge directions, colour - and this was the one that held across the three layers' lighting.

All of it is judged against what was seen in one captured run, by eye: of all three doors of those 25 rooms, 63 of 75 come out as
labelled (`boarded` / `plain` from the planks are the least sure part: 3 of 22 plain doors read boarded; and
the third layer's spiked doors, 3 of them, come out `boarded`, there being no kind for them yet). The misses: dark doors with
fewer black pixels than the rule wants (4 of 10 read as boarded or plain), and a lock whose light is cut off by the shear of a side door
(2 of 14). It also can't see which door has the ghost behind it. The kinds are for the decision logic to use as far as they go.

### Playing Hauntlet 2

`play_hauntlet_2()` (after `MINIGAME_START_WAIT`) takes a screenshot every `HAUNTLET_LOOK_INTERVAL` and hands it to a
`HauntletRun`, which keeps the room number and when it changed (`read_hauntlet_room()`) and says through `look()` when it is time
to choose, returning the doors to choose from. The number changes as the player passes through a door, while the last
room's doors are still on screen (open, the camera following the player), so the doors are only trusted when

- `HAUNTLET_ROOM_SETTLE` seconds have gone by since the number changed,
- the timer shows (`hauntlet_timer_visible()`), and
- the row of TVs is where it was on the look before, within `HAUNTLET_TV_STABLE` px - the camera has stopped (it zooms
  and pans at the start of a room; the macro stands still, so it does) - or `HAUNTLET_DOORS_TIMEOUT` has gone by.

A room whose number is a multiple of `HAUNTLET_FORK_EVERY` (10 and 20 in the run - it is an assumption that 30, 40 and so on
are forks too) with no row of TVs on two looks in a row is a fork: two doorways. **Room 1 is the entrance hall** ("Welcome to the Hauntlet
Hotel": one doorway straight ahead, no TVs, the timer already showing; in both captured runs the doorway leads into a room with three
doors that is still numbered 1), and any other room without TVs for `HAUNTLET_DOORS_TIMEOUT` is treated the same way: a single
doorway ahead, walked to by holding forward only (no strafe) until a row of TVs is on screen (`hauntlet_walk(until_doors=True)`) or the
number changes, after which the room is looked at at once. For a room other than 1, `no doors found in room N` is
logged once per run as a failure, which keeps a screenshot of what the TVs were not found in. Once a door was chosen the room is left alone for
`HAUNTLET_ROOM_TIMEOUT` seconds (the walk takes up to `HAUNTLET_WALK_MAX`); a room whose number didn't change by then is chosen from
again, which is the retry for a walk that got stuck.

`hauntlet_choose()` reads the hearts and the items off the same screenshot (new items are saved, at most
`HAUNTLET_LEARN_LIMIT` per play, so a misreading can't fill the folder), picks a door with `choose_hauntlet_door()` and an item to
use with `choose_hauntlet_item()`, and logs the lot: `room 12, 3 hearts (1 lost), items: phone x1, key x1, doors: -, -, locked, going middle`. **The choices are random for now**
(a locked door is only taken when a key is held, or when all are locked; with a chance of `HAUNTLET_ITEM_CHANCE` at each choice one of
the items, any of them, is used - `hauntlet_use_item()` clicks its slot, as the capture shows a human doing, and waits `HAUNTLET_ITEM_WAIT`, before the
walk starts); `choose_hauntlet_door(doors, hearts, items)` and `choose_hauntlet_item(items, hearts)` are where the risk-based logic goes. Whether
an item worked is not looked at. `hauntlet_strafe()` works out how far to go sideways and `hauntlet_walk()` then holds `HAUNTLET_2_FORWARD_KEY` - plus
`HAUNTLET_LEFT_KEY` or `HAUNTLET_RIGHT_KEY` for the strafe - until the number changes or `HAUNTLET_WALK_MAX` seconds. The
strafe is the distance from the player to the door's TV in *door spacings* (the distance between neighbouring TVs, which
also takes the camera's zoom out of it) times `HAUNTLET_STRAFE_DURATION` seconds per spacing - no strafe under `HAUNTLET_STRAFE_MIN`
spacings, never more than `HAUNTLET_STRAFE_MAX`. The player's place is measured, not assumed, because where a room is entered may
depend on the door it came through (a player who came in at the left door has further to go to the right one): it is the middle of the
row of hearts above the head (`HauntletHearts.center_x`), and where the hearts aren't seen, in front of the middle door. A fork's
two doorways aren't located, so each is the left or the right key for one spacing. The seconds per spacing are from a human's run (the
capture's keys against its room numbers, from the middle: left door 1.0-1.1 s, right 0.9-1.3 s; W held 3-4.8 s in all); that a key held by the macro
carries the character as far as one pressed by hand is **not yet checked in the game**, and neither is the strafe in a fork's hall
(its doorways were seen at other distances in rooms 10 and 20) - both are the first things to tune. The keys are always released,
also when the macro is stopped. When the run ends, `got as far as room N` goes in the run log.

### Recording a run

Tick **Record the next run (low quality video)** on the Debug tab's Other subtab (the
`record_next_run` config switch) and the next time the loop starts it records
the Roblox window to `debug/recordings/run<N>_<time>.mp4` - then clears the
switch, so it's one run only. `start_recording_if_requested()` starts a
`RunRecorder`, which writes `RECORD_WIDTH` x `RECORD_HEIGHT` (640x360) frames at
`RECORD_FPS` (4) from a background thread, each stamped with the time (white text on a solid black box in the top-left
corner, which stays readable over any scene at the cost of that corner) so the
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
button, a slower computer, or a different monitor. `main.py` does `import
magic_numbers as cfg` and refers to everything there as `cfg.NAME`.
`ENABLED_NEEDS` is the first thing defined in it, since it's the setting
most likely to need editing. Everything else falls into one of these
groups (see the comments next to each constant in `magic_numbers.py` for
exact values and rationale):

| Group | Examples |
|---|---|
| Behavior flags | `ENABLED_NEEDS`, `STOP_HOTKEY`, `BACKPACK_*`, `HALLOWEEN`, `MINIGAME_*`, `HAUNTLET_2_*`, `GHOST_GALLERY_*`, `REJOIN_*`, `HELICOPTER_REQUIRED`, `FOCUS_WINDOW_ON_ACTION` |
| Window / coordinates | `REFERENCE_WIDTH`, `REFERENCE_HEIGHT`, `REFERENCE_CENTER_X/Y`, `ROBLOX_RECT_TTL` |
| Timing | `RESPAWN_WAIT`, `WALK_TO_BUTTONS_DURATION`, `NEED_CHECK_RETRY_DELAY`, `LOOP_DELAY`, `STOP_CHECK_INTERVAL`, `STOP_CLICK_GRACE`, `NEED_GONE_*` |
| Stuck needs | `NEED_STUCK_CHECKS` |
| Pet focusing | `FOCUS_PET_REGION_TOP_PERCENT`, `FOCUS_PET_FRAME_GAP`, `FOCUS_PET_DIFF_THRESHOLD`, `FOCUS_PET_MERGE_KERNEL`, `FOCUS_PET_MIN_AREA` |
| Screen positions | `CATCH_*_POS`, `EMPTY_POS`, `RIDE_*_POS`, `LURE_NEW_POS_*`, `SETUP_*_POS` |
| Side quest / setup | `TREE_HARVEST_YIELD`, `MONEY_COLLECTED_TARGET`, `TREE_CHECK_INTERVAL`, `MONEY_RESET_HOUR`, `LURE_RECOLLECT_INTERVAL`, `LURE_COLLECT_*`, `TREE_COLLECT_*` |
| Need-icon detection | `NEED_ICON_TOP_PERCENT`, `NEED_ICON_WIDTH_PERCENT`, `NEED_ICON_BLANK_*`, `NEED_ICON_MIN/MAX_RADIUS`, `NEED_ICON_HOUGH_*`, `NEED_STRIP_*` (when the circle search is skipped) |
| Need-icon matching | `ICON_MATCH_THRESHOLD`, `ICON_BW_THRESHOLD`, `ICON_CLAHE_*`, `ICON_COMPARE_SIZE`, `ICON_MATCH_RADIUS_FRACTION`, `ICON_CROP_RADIUS`, `ICON_SHIFT_TOLERANCE`, `MATCH_ONLY_LEFT_HALF` |
| Button detection | `BUTTON_BAND_X/Y`, `BUTTON_MIN_AREA`, `BUTTON_MIN_CIRCULARITY`, `BUTTON_MAX_COUNT`, `BUTTON_NAMES`, `BUTTON_LOOSE_*_FACTOR`, `BUTTON_PURPLE_*_ITERATIONS`, `BUTTON_OUTLINE_PADDING` |
| Debug logging | `OUTPUT_LOG_PATH`, `RUN_LOGS_DIR`, `FAILURE_DIR`, `STATUS_PATH`, `OUTPUT_LOG_MAX_BYTES`, `MAX_FAILURE_FOLDERS`, `MAX_REPORTS`, `CONSOLE_MAX_LINES`, `QUIET_LOG_INTERVAL`, `RESOURCE_LOG_INTERVAL` |
| Recovery | `DISABLED_NEEDS_REJOIN_THRESHOLD`, `NO_PROGRESS_REJOIN_INTERVAL`, `NO_DETECTION_RESPAWN_INTERVAL`, `DISCONNECT_*`, `WHITE_SCREEN_*`, `REJOIN_*`, `ROBLOX_*`, `FOCUS_RESUME_*`, `PAYCHECK_DISMISS_POS_*` |
| Hauntlet 2 reader | `HAUNTLET_ROOM_BOX`, `HAUNTLET_DIGIT_*`, `HAUNTLET_ROOM_MAX_DIGITS`, `HAUNTLET_SLOT_*`, `HAUNTLET_ITEM_*`, `HAUNTLET_BADGE_*`, `HAUNTLET_COUNT_*`, `HAUNTLET_NOT_ITEMS`, `HAUNTLET_HEART_*`, `HAUNTLET_*_DIR` |
| Hauntlet 2 play | `HAUNTLET_TIMER_*`, `HAUNTLET_TV_*`, `HAUNTLET_DOOR_*`, `HAUNTLET_LOCK_LIGHT_*`, `HAUNTLET_MYSTERY_BRIGHTNESS`, `HAUNTLET_DARK_*`, `HAUNTLET_PLANK_*`, `HAUNTLET_LEFT_KEY`, `HAUNTLET_RIGHT_KEY`, `HAUNTLET_STRAFE_*`, `HAUNTLET_WALK_MAX`, `HAUNTLET_LOOK_INTERVAL`, `HAUNTLET_ROOM_SETTLE`, `HAUNTLET_ROOM_TIMEOUT`, `HAUNTLET_FORK_EVERY`, `HAUNTLET_DOORS_TIMEOUT`, `HAUNTLET_ITEM_CHANCE`, `HAUNTLET_ITEM_WAIT`, `HAUNTLET_KEY_ITEMS`, `HAUNTLET_LEARN_LIMIT` |
| Input capture | `CAPTURE_HOTKEY`, `CAPTURES_DIR`, `MINIGAME_CAPTURE`, `RUN_CAPTURES_DIR`, `RUN_CAPTURE_ZIPS`, `MAX_RUN_CAPTURES`, `CAPTURE_FRAME_INTERVAL`, `CAPTURE_CHANGE_LEVEL`, `CAPTURE_CHANGE_FRACTION`, `CAPTURE_MAX_SKIP`, `CAPTURE_POLL_INTERVAL`, `CAPTURE_MOVE_INTERVAL`, `CAPTURE_MAX_MINUTES`, `CAPTURE_MIN_FREE_MB`, `MAX_CAPTURES`, `CAPTURE_ZIP_MB` |
| Popup crops | `STRAY_WINDOWS`, `STRAY_WINDOW_MATCH_THRESHOLD`, `POPUP_TEMPLATES` (box and threshold of the paycheck, sick-dialog and backpack crops) |
| Debug drawing | `DEBUG_NEED_MARKER_*`, `DEBUG_BUTTON_MARKER_*`, `DEBUG_BUTTON_LABEL_*` |
| Color ranges (HSV) | `PURPLE_RANGE`, `WHITE_RANGE` (button detection) |
| GUI | colors/fonts (`GUI_BG`, `GUI_FONT`, ...) and layout spacing (`GUI_OUTER_PADDING`, `GUI_ROW_SPACING`, ...) for the `tkinter` panel |

## Debugging an unattended run

Everything below is in `debug/` (git-ignored) so a run that broke while you
were away can be read back afterwards. The folder has two subfolders -
`images/` (`IMAGES_DIR`: every png - the `debug_*.png` views, `failures/`,
`reports/`) and `logs/` (`LOGS_DIR`: every text and json file - `output.log`,
`run_log.txt`, `run_counter.txt`, `status.json`, and `runs/`, one `run<N>.txt` per run) - plus `recordings/` for the
videos, and the game config (`.config`) in `debug/` itself. An older layout is
moved into place at startup (`migrate_debug_layout()`). The Debug tab's
Other subtab's **Refresh debug** button (`refresh_debug()`, after a confirmation, not while a
run is going) empties all of it except `.config`; run numbers start from 1
again, since the counter goes with the logs.

- **`output.log`** - everything the macro prints (the same as the GUI
  console), one timestamped line each, appended to across runs. `log_output()`
  is called by `DebugCapture`, so this only exists while the GUI is up. The file stays open between writes (flushed after each one, so a crash loses nothing); `close_output_log()` closes it before **Refresh debug** deletes it, since Windows won't delete an open file. Past
  `OUTPUT_LOG_MAX_BYTES` (5 MB) it moves to `output.log.old`, replacing the
  previous one.
- **`run_log.txt`** - the short version: one tagged line per notable event
  (needs detected, resolved with how long they took, popups dismissed,
  recoveries, stops, failures). Start here to see roughly what happened.
  Every line is also written to its own run's file, `logs/runs/run<N>.txt`
  (`RUN_LOGS_DIR`, by `log_run_event()`), so one run can be read or shared on
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
- **`reports/run<N>.png`** - a chart of one run, drawn from `run_log.txt` by
  `generate_run_report()` in `main.py` with OpenCV (no extra dependency). Written automatically when a
  run stops (`write_run_report()`, in the `finally` of `run_workflow_loop()`)
  (so there is no button for it); the oldest reports beyond `MAX_REPORTS` (50) are deleted then (`keep_newest_files()`) - the logs they are drawn from are kept, so one can be drawn again. Five panels: needs resolved per unit of time (the unit is picked
  from `BUCKET_STEPS` so the run fills about `TARGET_BUCKETS` bars, stacked by
  basic / teleport / other need, with failures and rejoins marked); needs
  detected per check; average seconds per resolved need with how many there
  were; counts of failures by kind and other events; and the script's own CPU and memory use over time, each with the whole PC's use as a second line (CPU on the same scale; memory in % on its own axis, labelled down the right edge). A need disabled as stuck is drawn in the first panel as a grey bar from when it was disabled until the next rejoin (which clears them), named above it. `parse_run()` reads
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
  at that moment). It also prints the reason, writes it
  to `run_log.txt` and counts it in the status file. It's called when a handler
  can't resolve a need, a need is still showing after `NEED_GONE_MAX_WAIT`, a
  need is disabled as stuck, a recovery starts or fails, Roblox loses focus
  and stops the run, and when the run crashes (the full traceback is in
  `output.log`). It never raises, since it runs while something else is
  already wrong. The oldest folders beyond `MAX_FAILURE_FOLDERS` (100) are
  deleted.
- **`status.json`** - rewritten every cycle and on every failure/resolved
  need (`write_status()`): the run number and start time, time of the last
  update (if that's old, the macro is stuck or dead), cycle count, the needs
  last detected, how many of each need were resolved or failed, the needs
  currently disabled, the number of recoveries, disconnects, scheduled
  rejoins and focus resumes, how many whole minutes no need has been resolved
  for (`quiet_minutes`), the script's latest CPU and memory use, and the last
  resolved need and last failure. `run_stats` (a `RunStats` dataclass) holds the counters in memory.
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

## Automated checks

`.github/workflows/check.yml` runs on every push and pull request (GitHub
Actions, a temporary Linux machine): `pyflakes` on `main.py`,
`magic_numbers.py`, `tools/check_constants.py` and `tests` (syntax errors,
undefined names, unused imports, duplicate definitions), then
`python tools/check_constants.py`, which fails on a `cfg.NAME` in `main.py`
that `magic_numbers.py` doesn't define, then `mypy main.py` (configured in
`mypy.ini`: it also checks the bodies of functions without type hints, and
ignores the Windows-only libraries it has no stubs for), then
`python -m pytest -q`. Every top-level function and method in
`main.py` has type hints (a few small nested helpers don't), using the aliases `Image` (a BGR screenshot or crop),
`Point`, `Box` and `Color` defined under the imports. A few lines carry
`# type: ignore[...]` for what the stubs get wrong (Windows-only calls, tkinter,
OpenCV). It can't run the macro itself - that
needs Windows, a screen and Roblox.

### Tests

`tests/` holds the pytest tests (`python -m pytest -q`, also run by the Action).
`tests/conftest.py` stands in for the libraries that only work on Windows or
with a screen (`pyautogui`, `pydirectinput`, `mss`, `pygetwindow`), so
`main.py` imports anywhere, and its `macro` fixture gives each test a fresh
`State` and `RunStats`, redirects every file the macro writes (run log, status,
failure folders, output log, reports) into a temporary folder, and records
clicks instead of making them. They cover:

- `test_pure.py` - the small pure functions (server link parsing, durations,
  colour masks, axis rounding, failure kinds);
- `test_state.py` - stuck-need disabling, `process_needs()` (resolved, unresolvable and not-cleared cases), `RunStats`, `status.json`, the
  failure folders, the quiet and idle-respawn checks (including that a respawn
  leaves the 20-minute rejoin clock alone), stopping, and the open `output.log`;
- `test_detection.py` - the need-strip capture and its cache, and the
  popups and stray windows against the real crops in `ref/` (including that every `POPUP_TEMPLATES` entry has a crop that fits its box);
- `test_icon_screenshots.py` - every real screenshot in `tests/icon_detection/<folder>/` (the need bar over dark, bright, busy and white scenes) run through the circle search and the matching; each must be listed in `EXPECTED` with the needs on its bar, so a screenshot added without a label fails;
- `test_loop.py` - the whole workflow loop run for a moment with the game stubbed out (idle respawns, the resource monitor starting and stopping, the report being written);
- `test_capture.py` - the input capture against a made-up keyboard, mouse and screen (events, both coordinate systems, click frames, skipping unchanged frames, the summary, the zips, stopping by itself, the hotkey toggle and pruning, and the automatic recording of minigame runs: the frame budget, its thinning, the outcome, the zip);
- `test_hauntlet_2.py` - reading the Hauntlet 2 screen against crops of a real run;
- `test_hauntlet_2_play.py` - the timer and doors against crops of that run, then the room tracking, the choices (door, item), the walk and the whole loop with a fake clock and keyboard;
- `test_features.py` - the daily money reset, the run-every-need sequence, the win screen and the like;
- `test_config.py` - the real need tables in `magic_numbers.py`, without pinning any value: every entry is well formed (known keys, on-screen positions, movement keys, a `final_click` has its `prompt_pos`), has a handler, and the handler carries out exactly what the entry says (run over whichever needs exist, so `HALLOWEEN` off just drops `sick`). The handler tests elsewhere use made-up entries; this is what keeps the real ones honest;
- `test_report.py` - the run-report parser against the exact log wording the
  macro writes (a round trip through `log_run_event()`), the chart itself,
  and the constant check.

Fixtures are synthetic images or the lossless crops in `ref/`; compressed
screenshots (`.webp`) don't match exact colours, so they aren't used.

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
values, not on every commit (about 30 runs, a few minutes; `python tools/retune_check.py halloween_off timings_x20` runs just those).
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
