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
button; Leave & rejoin, Respawn, Setup (asked for by the bot) and the Trading Hub trips are single actions run the same way): it respawns once up front (so the loop always starts from a known,
on-the-ground state; when the **Run Setup at the next loop** box is ticked Setup, which respawns by itself, takes the place of that respawn), then repeats `run_full_cycle()` forever, pausing
`LOOP_DELAY` seconds between cycles, until stopped.

**`run_full_cycle()`** is a tight retry loop:

```
while True:
    check_stop(); count the cycle
    check_quiet(); flush_idle_checks_if_due(); write_status()
    run_setup_if_requested()   # the Main tab's once-only box, before the cycle
    unscrew(); side_quest(); pet_pen(); pet_pen_swap_due(); pet_repick()
    if process_needs():   # resolved (or at least handled) ONE need
        return
    respawn_if_no_detection()   # nothing detected for NO_DETECTION_RESPAWN_INTERVAL
    wait NEED_CHECK_RETRY_DELAY seconds, then check again
```

The key word is *resolved*. `process_needs()` returns `True` only if it
actually did something - clicking a button or running a handler (or a handler
was cut short by a rejoin or a popup, which respawn the character
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
   well enough is skipped this pass (it is taught by the new-need key, see [Teaching a new need](#teaching-a-new-need)).
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
- **ICON PROCESSING** - `preprocess_icon()` / `icon_signature()` / `score_variants()`. See
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
  runs at the next loop when the GUI's box is ticked, from the bot's `/setup` and from the automatic recovery; `leave_and_rejoin()` from the GUI, the bot and the recovery
  - see the end of [Need handlers](#need-handlers).
- **NEED HANDLERS** - `NeedHandler` and one subclass per need (or per group
  of needs, via `partial()`). See [Need handlers](#need-handlers).
- **PER-CYCLE CHECKS** - `unscrew()` and what it runs: `rejoin_game()`
  (with `rejoin_reason()` / `detect_disconnect()`), `close_backpack_if_stuck()`
  / `close_backpack_if_open()` (with `detect_backpack_expanded()` /
  `detect_backpack_normal()`; the click sequences that open the backpack end
  with `close_backpack(step)`, which calls the latter - see
  [Click & input primitives](#click--input-primitives)),
  `dismiss_stray_windows()`,
  `detect_paycheck()`, `dismiss_notice()` (the popups of an event, at the top of a cycle only); the `task_unscrew()` / `task_unscrew_tick()` hook that
  runs them during a task; and `side_quest()`.
- **RUN RECORDING** - `RunRecorder` and `start_recording_if_requested()`. See
  [Recording a run](#recording-a-run).
- **INPUT CAPTURE** - `InputCapture`, `toggle_input_capture()`, `start_run_capture()`, `zip_capture()` and the key / mouse
  readers (`pressed_inputs()`, `cursor_position()`, `to_reference()`). See
  [Input capture](#input-capture).
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
`ROBLOX_RECT_TTL` seconds, and `focus_roblox()` forgets it. When Windows refuses
`activate()` (pygetwindow raises `PyGetWindowException`, often with "error code 0:
completed successfully": the bot's console had just opened over Roblox and `/inventory`
crashed on it), `force_foreground()` taps `KEYS["foreground_tap"]` (Alt) and activates
again, then minimizes and restores the window (`FOCUS_FORCE_TRIES` ways), and
`focus_roblox()` is False, logged, when none of them brings Roblox to the front - it does
not raise. On a 1920x1080
screen with Roblox maximized the rectangle is the whole screen, so nothing is
cropped or scaled; on another resolution or a smaller window everything
scales. Distances measured in pixels (icon radii, the `TRADES_ROW_PITCH`
spacing, ...) are reference pixels, so they scale too. Scaling assumes the same
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
to 0-1, and keeps only the **left half** so an
event badge overlapping an icon's top right doesn't affect the match.
`score_variants()` scores signatures as `1 - mean squared
difference`, over that disc only (`icon_match_mask()`), every variant of the detected icon against every saved icon at once (the squared difference as
`sum(L²) - 2·sum(L·S) + sum(S²)` over the disc's pixels, the middle term one matrix product: the 25 shifted variants against every saved need was most of a look at the need bar, 9 ms for an icon instead of 26 ms; the same scores to 1e-8 on every icon of the test frames). The saved icons are
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
seen" - the icon is then unknown and is skipped (the new-need key teaches it).

### Teaching a new need

`HOTKEYS["new_need"]` (`ú`, a key of its own on the keyboard layout, looked up like the screenshot key; only while Roblox is focused, and only while the Settings tab's **ú teaches a new need icon in Roblox** checkbox - `new_need_hotkey_enabled`, also `/option new_need_hotkey` - is on; off, the key can be typed in the game) looks at the need bar now (`teach_new_needs()`, on Tk's thread: `detect_need_icons(fresh=True)`, then `identify_icons()`) and asks the name of each icon on it that no saved need matches (`prompt_rename_need(on_ui_thread=True)`: the reference icon, in its black-and-white form from `preprocess_icon()`, is saved into `NEEDS_DIR` under the name typed - `unnamed_<n>` for a cancelled dialog or an empty name - and is recognised from the next look). It says in the log when there was no new icon. It replaced a flag that asked during a run.

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

**Choose, whatever the pet.** `ChooseNeedHandler` gets the pet's view up with `focus_pet_reliably()`: `focus_pet()` (the pixels that moved between two looks, then the name pill), and then the view is *waited for* (`PET_FOCUS_VIEW_WAIT`) - the first three chooses of a 2-hour run clicked the pet, looked once, found no button and failed, and the view came a few seconds later. When it does not come, the E key is pressed (the pet's own prompt, which opens its menu when the player is by it, whatever the pet; not when the view is up already: BACK is on E too), and the whole is tried `PET_FOCUS_TRIES` times. The name pill is `find_pet_bar()` (the bar with white at its right) or, when that finds none, `find_pet_pill()`: the pill as one blob of bright purple (the filled part of the age bar) and dark purple (the rest) with the white text closed over (`PET_PILL_*`), below the game's own buttons and outside the macro's window; the pet is centred under it. The old search found no pill on a pet whose bar was part filled, which is most of them. The button is then looked for for `CHOOSE_FIND_WAIT` seconds (it moves in with the view): its exact colour first, then the purple disc. Tests: `tests/test_choose.py` on the frames of that run.

**The game's update.** A white screen that shows blue letters at the bottom (`server_closing_visible()`, `SERVER_CLOSING_BOX`) is the game closing its servers for an update: `rejoin_reason()` waits `SERVER_UPDATE_REJOIN_AFTER` seconds from the first white look, not `WHITE_SCREEN_REJOIN_AFTER`, before the rejoin (the servers take minutes to come back).

**Doors and respawns.** A door, a respawn and a teleport each show the game's white screen and fade out of it, so `wait_for_white_to_pass()` waits for that: for the white screen to show (within `WHITE_LOOK`) and then to be gone for `WHITE_CLEAR_LOOKS` looks in a row (`WHITE_PASS_MAX` at most), then `WHITE_SETTLE`. When none shows it waits the fixed time the step had before (`RESPAWN_WAIT`, `TELEPORT_WAIT`, the door's 5 s in `PET_PEN_ROUTE`), the looks counted in it, so a transition without a white screen costs no more than it did. The nursery teleport comes as two loads (a white screen, a landing in a red room, a second white screen, the park), so `teleport_to()` waits for the first, walks back (`TELEPORT_BACK_DURATION`: it is the way out of the landing and what starts the second load) and then waits for the second (`TELEPORT_SECOND_WHITE_LOOK`, the fixed `TELEPORT_SETTLE_WAIT` when none shows). `respawn_character()`, `teleport_to()` and the pen's door (a `("door", name, seconds)` step of `PET_PEN_ROUTE`) use it. On the capture of the pen route the white screen was there for the respawn (4.9 to 6.3 s) and for the door (9.9 to 10.6 s). Not done: the hospital door of the sick need (walk until the white screen of going indoors shows), the Trading Hub trips and the rejoin's loading screens, which have their own waits.

**What a teleport looks like, and what is checked.** The frames of a capture of the teleport tests (`tests/teleport/`: the house before, the nursery and dealership dialogs, the first white screen, the red room, the second white screen, the park, the road landing, the hospital's lobby, the house idle) show the whole sequence, and the macro's own console in the frames gave each need's result (bored, beach, school, cafe, salon, pizza and camping cleared; sick failed on the prompt, see the sick need). In order: the backpack opens in the house, the category tab and the dialog ("Teleport to the Nursery / the Vehicle Dealership") come up, the Yes is clicked; a first white screen (the screen going white as the old place unloads), a landing in a red room with the pet in front of the camera (the nursery teleport lands there first), a second white screen, and the park or dealership street. The respawn is shorter: the dialog, one white screen, the house. In the house the game draws a red *Exit Home* button at the bottom left and nowhere else, and a white screen covers it, so `in_house()` (`HOUSE_EXIT_BOX`, `HOUSE_EXIT_HSV`, `HOUSE_EXIT_MIN_PIXELS`, `HOUSE_EXIT_MAX_PIXELS`: the red walls of the nursery's room fill the same box with about twice the red of the button, and were taken for the house by a first version) says where the player is. What is checked with these, each one a way of telling that something went wrong that the walk would not have shown:
- `wait_for_white_to_pass(what=...)` writes `<what>: no white screen showed` to the run log when none showed (the respawn, the teleport's two loads, the pen's door), then waits the fixed time it always did.
- `teleport_to()` looks for the dialog's Yes (`find_teleport_yes()`, `ref/popups/teleport_yes.png`, `TELEPORT_DIALOG_WAIT`) after the Get More tile, clicks the tile once more if it did not come up and clicks the Yes where it was found, not at a fixed pixel (the fixed `GENERAL_TELEPORT_POS_3` is where it was in the capture, and a test holds the two together). The category tabs and the tile are still fixed pixels; the tests check on the real dialog frames that a coloured button is under each.
- `teleport_to()` returns False, and the handler respawns and fails the need without walking blind from the house, when the dialog did not come up (`teleport: the dialog did not come up ...`), the Yes took nothing after `TELEPORT_YES_TRIES` clicks (`teleport: nothing happened after the dialog's Yes`) or when the player is still in the house after both loads (`teleport: still in the house after both loads`).
- The step back comes after the first white screen, in the landing, and is not just clearing a spot: it walks out of the landing (the nursery's red room, the dealership's street), and that is what starts the second white screen and the real place. A run in which the step back was moved after the second load (a version of `teleport_to()` that waited for it first) never got one: the player stood in the red room until the respawn, 39 teleports failed and none of the teleport needs resolved (`tests/teleport/red_room_stuck.png`). Do not move it.
- `respawn_character()` logs `respawn: not in the house afterwards` and the pen's door logs when the house is still on screen after it (a door that did not open).
A line of the first kind with a good result after it means the transition was too fast to be seen (the poll is `WHITE_POLL`); the same line with the need failing after it means the transition did not happen.

**The pulse.** The icon of a need that is being done pulses: its saturation swings towards white and back about every 2.5 s, deeper as it goes, until the need clears (the capture of dirty, thirsty and sleepy: it began 1 to 6 s after the click, and every other icon stayed constant; the match score of the paled icon was still 0.96, so matching is not disturbed). While it waits, `wait_until_need_gone()` looks at the icon every `PULSE_POLL` (`PulseWatch.look()`: a fresh circle search, the saturation inside the icon's circle, `icon_saturation()`) beside the checks above. A swing of `PULSE_MIN_SWING` between the highest and lowest look is a pulse (`PulseWatch.pulsing()`). For the needs in `PULSE_NEEDS` - the ones whose pulse has been seen - none within `PULSE_START_WAIT` seconds of the action means the game is not doing it: a failure is logged (`<need>: its icon has not pulsed ... - it is not being done`), `state.need_cleared` is False and the wait ends there, not at `NEED_GONE_MAX_WAIT`; the next cycle tries the need again like any other that is still showing. A need that times out says in its failure whether its icon pulsed and when (`its icon never pulsed`). The needs in `PULSE_NEEDS` are the button needs and the nursery ones whose pulse a 2-hour run showed (1 to 5.5 s after the action). For the other needs a pulse is only logged (`<need>: its icon pulsed Ns after the action`), which tells whether it is worth adding them. Tests: `tests/test_need_handlers.py` against the strips of `tests/pulse/`.

A need that was not cleared `NEED_NEVER_WORKED_FAILS` times in a run, and never was, is disabled for the rest of it (`process_needs()`): camping came every 30 minutes, waited its 75 s four times and never cleared in a 2-hour run. A rejoin does not give such a need (`state.hopeless`, `mark_hopeless()`) another chance until it has been disabled `NEED_HOPELESS_RETRY` seconds (`release_hopeless_needs()`, in the recovery: `given another chance after 1 h: ...` in the run log), and then only one: a single failure puts it back, as it has failed `NEED_NEVER_WORKED_FAILS` times already. Before that a bug of the macro's own cost a run every teleport need for the 2 hours that were left of it.

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

Popups and stray windows that turn up during the wait are handled
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

The needs that are a fixed sequence - `catch`, `pet` and `ride` - are **tables of steps** (`SequenceNeedHandler.steps`): each step is `(action, *arguments)`,
the arguments naming the `magic_numbers` constants (read when the step runs, so a retuned value is used) or a `KEYS` label, and `run_steps()` does them in
order (`STEP_ACTIONS`: `press`, `wait`, `sleep`, `tab`, `click`, `close_backpack`, `hold`, `walk`, `focus_pet`, `click_centre`, `equip_vehicle`, `swipe_down`, `say`
for a debug line, and `with_backpack` and `repeat`, which hold steps of their own). A step that returns False ends the need as not done. A new sequence
need is a new table, not new code; `tests/test_need_handlers.py` records what each does and checks that every name a table uses exists. The others stay code:
`ButtonNeedHandler` is three calls, and `choose`, `walk` and the teleport-walk needs decide as they go.

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

**Focusing the pet.** (Each click is followed by `FOCUS_PET_CLICK_SETTLE` and a `detect_pet_focus()` check; the clicks stop at the first one that focuses it, and `FOCUS_PET_MENU_WAIT` is only waited if none did. A pet that stands still moves nothing, and none of the blobs may be the pet: then `find_pet_bar()` looks for the pet's name bar over it - the purple part of the bar by its hue (`PET_BAR_HSV`), a wide short blob (`PET_BAR_ASPECT`, `PET_BAR_AREA`) with white at its right (`PET_BAR_WHITE_*`), whatever its words and its outline, which is not always there - and `focus_pet_by_bar()` clicks the places `PET_BAR_BELOW_STEPS` bar heights under it, at the join of the purple and the white, in turn until the pet is focused - how far under the bar the pet's body is depends on its pose: far for a standing pet, near for one lying close to the camera, whose bar also has less white beside it; the real frames are `tests/popups/pet_with_bar_not_moving.png`, where five attempts at `choose` failed, and `tests/popups/choose_pet_lying_in_house.png`, where the bar was rejected for its white.) `pet` and `choose` both start with `focus_pet()`, which first checks whether the pet is already focused (`detect_pet_focus()`: the BACK button's crop is on screen) and if so returns True with no clicks; otherwise it
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
| `choose` | `ChooseNeedHandler`: `focus_pet()` (see below), find the exact-color button (`CHOOSE_BUTTON_COLOR`, since it has no distinguishing icon), hover to it slowly (`hover_click`) and click, then click screen-center to dismiss the menu. It doesn't wait for the icon to clear (it's gone at once; the next check confirms). When no pixel has that exact colour (twice in run 34 the button was in plain sight under the focused pet), `find_choose_disc()` looks for it by its look: the biggest roundish blob of the purple-to-magenta hue `CHOOSE_DISC_HSV`, of `CHOOSE_DISC_AREA` px and at least `CHOOSE_DISC_ROUNDNESS` of its circle, so neither the white outline (not always there) nor the "?" (its text can change) matters; the button is about `CHOOSE_DISC_AREA` px (180 across); the 56 px "?" that `tests/popups/choose_pet_lying_in_house.png` has at the top is the choose need's icon in the need bar, not the button (a range that took it clicked the bar, and the real button was missed: run 19, six failures with "No purple disc found either"). No kept frame has the button in view, so `_choose()` now saves the focused view (`save_failure_report()`) when it finds nothing. A choose that failed backs out of the pet focus (`dismiss_stray_windows()`): left in it, the next need's walk to the buttons went nowhere. |
| `ride` | `RideNeedHandler`: step back, mount (`e`), walk forward briefly, then `equip_favorite_vehicle()` (backpack → vehicles → first vehicle → equip → close backpack), hold `r` (`KEYS["helicopter"]`) for `RIDE_R_HOLD_DURATION` (`take_off()`: held once, with a phase screen after it; a check that the view changed, with a second hold, was dropped: the helicopter is often already in the air before the key, so it flagged rides that went on to clear), then `walk_alternating(("w", "s"), NEED_GONE_MAX_WAIT, need_name="ride")` - up to `NEED_GONE_MAX_WAIT`, ending early the moment "ride" is confirmed cleared (see below). A ride that did not clear its need (`state.need_cleared` false) or could not be done is followed by `set_backpack_filter()` - the favourites filter of the backpack, as Setup sets it (looked at first, left alone when it says Favorites), and, only when the pet is not out (no name bar over the character), the Alicorn found by its picture in the pets tab (`equip_pet()`: its card's green Equip button found by `find_equip_button()`; a card that says Unequip is left alone), and the ride once more, once: with Setup not run, the first vehicle was a car and not the helicopter the keys are for. |
| `bored` / `beach` / `school` / `cafe` / `salon` / `pizza` / `camping` / `sick` | `TeleportWalkNeedHandler`, configured per need in `TELEPORT_WALK_NEEDS`: teleport to the nursery or dealership (`teleport_to()`), hold each `(key, seconds)` step with `hold_key()`, then `wait_until_need_gone()` (see [Waiting for a need to clear](#waiting-for-a-need-to-clear)). `process_needs()` respawns afterwards. An entry may also set `final_click`, the Yes of a dialog the need has to answer after the last hold (used by `sick`). After `SICK_PRE_CLICK_WAIT` (the fixed time, or the fade of the hospital door's white screen when one shows: `wait_for_white_to_pass()`) the handler looks for the doctor's *prompt* - the purple button over the player's head, a reference crop (a hand, `ref/popups/doctor_prompt.png`, `POPUP_TEMPLATES["doctor_prompt"]`; an E since the Lightning Dimension update, `doctor_prompt_e`, from the teleport-test capture, where the hand was not found and sick failed with the character standing at the desk; `find_doctor_prompt()` tries both) - for `SICK_PROMPT_LOOK` seconds (8 of 24 sick needs of a 10 hour run found no prompt behind the door's white screen with a shorter look; `look_until()`, a look every `SICK_LOOK_INTERVAL`), and clicks it where it is found. It does *not* press `e`: that is the key of the pet's own prompt, and in a recording of the macro it opened the pet's menu, the pet view backed out of, and the click on the Yes that followed hit nothing - while a hand clicking the prompt, by hand, brought up the dialog about 2.5 s later (`tests/sick/`: the real screenshots of the prompt, the dialog and the pet view). If the pet view is open when the handler starts it is backed out of first (`dismiss_stray_windows()`; it hides the prompt). Then it looks for the dialog (`detect_sick_dialog()`, `ref/popups/sick_dialog.png`) for `SICK_CONFIRM_WAIT` seconds; seen, it clicks the Yes (`final_click`). The need is given up at once - the character respawns, the handler returns False with no wait for the icon to clear and `process_needs()` logs the failure - when the prompt is not on screen (`sick: the doctor's prompt is not on screen`) or the dialog does not come up (`sick: no dialog after the prompt was clicked`). (Before this the handler pressed `e`, clicked Yes whether or not the dialog was recognised, and waited out the icon's 75 s: sick failed to clear 16 times in a 7-hour run, each costing about two minutes), `helicopter` (see below) and `max_wait` (how long to wait for the need to clear afterwards; default `NEED_GONE_MAX_WAIT`, 75s). |
| `walk` | `WalkNeedHandler`: `walk_alternating(("a", "d"), NEED_GONE_MAX_WAIT, need_name="walk")` - up to `NEED_GONE_MAX_WAIT`, same early-exit as `ride` above. |

**Helicopter.** A teleport-walk entry can fly instead of walk. `HELICOPTER_REQUIRED`
(`magic_numbers.py`) is the default for entries that don't
say; an entry overrides it with `helicopter=True`/`False`. When on, after
`teleport_to()` (the dialog's Yes is clicked up to `TELEPORT_YES_TRIES` times until the view changes or the screen goes white, like a repeated click on a button that did not take; a Yes that never takes is logged) the handler holds `w` for `HELICOPTER_FORWARD_DURATION`,
calls `equip_favorite_vehicle()` (the same backpack sequence `ride` uses; the
first vehicle is the favorite once `setup_game()` has filtered the backpack),
holds `r` for `HELICOPTER_HOLD_DURATION`, runs the entry's steps as
usual, then presses space (`KEYS["jump"]`) on arrival, before any `final_click`.
`camping` uses it (`helicopter=True`: forward, equip, hold, then the route's right and back steps, then space); the other entries walk. (The Halloween routes of `bored` and `beach` flew too; they are in `scripts/Halloween2026.py`.)

**Routes.** The teleport-walk steps live in one table, `TELEPORT_WALK_NEEDS` (what `TeleportWalkNeedHandler` and the handler registry read). The nursery's routes (`bored`, `beach`, `school`, `camping`, `sick`) are the ones from before Halloween moved it: the nursery is back where it was, and the event's own routes (and `helicopter`) are kept in `scripts/Halloween2026.py`, which the macro does not import. In a 10 hour run every route cleared its need (camping 21 times, salon 15, bored 14, school 12, beach 11, cafe 11, pizza 11, sick 16 of 24 attempts, the sick failures being the prompt behind the hospital door's white screen); the routes were tuned from a person's walk and are timed, so the pause between steps (`TELEPORT_WALK_STEP_GAP`) is not to be shortened.

Every cycle, `unscrew()` runs `rejoin_game()` first (nothing else works
while disconnected), then `close_backpack_if_stuck()` (the backpack, either
form; not during setup, which keeps it open for ~8s on purpose and closes it itself), then
`dismiss_stray_windows()`, then
the paycheck check. The stray windows go before the
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

**Stray windows.** The first two windows seen open on their own, covering
the screen (more are listed below): the Trading Hub (how it got opened isn't known; a misclicking
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

One more entry is the pet's **question card** (`choose_card`: "Can <pet> have the ability to jump on people's heads?" with a Choose One list, Cancel and Submit; the crop is its red Cancel, cut from `tests/popups/choose_card_screen.png`, and Cancel closes it; it stood over the game for 13 minutes of a run, in which the button needs found no buttons; `only_when_unwanted`, so the choose need, whose click is on that card, is not interrupted).

**Notices.** The popups of an event (the Admin Abuse one, on Saturdays: "An Adopt Me admin gave you: <item>" with an Awesome! button, "TRADE RECEIPT - YOU RECEIVED: <item>" with a Thanks!) change with every event, so there is no crop of them and no fixture: `dismiss_notice()` closes any popup that has a single wide green button (`GREEN_BUTTON_COLOR`, `NOTICE_BUTTON_SIZE`, at least `NOTICE_BUTTON_ASPECT` wide for its height, `NOTICE_BUTTON_FILL` of its box green, `NOTICE_JOIN` to grow it over confetti) in the middle of the screen (`NOTICE_BOX`) and no red button beside it (`NOTICE_RED_MIN`: a No or a Cancel makes the card a question, not a notice, which is left alone), by clicking that button (`find_notice_button()`). It runs in `unscrew()` at the top of a cycle only, after the stray windows and the paycheck (whose CASH OUT is a green button too), and never while a handler has the backpack, the pet's view or the Task Board open on purpose. A button that was clicked `NOTICE_MAX_CLICKS` times within `NOTICE_CLICK_WINDOW` seconds and is still there is not a notice: it is left alone for `NOTICE_PAUSE` seconds (`notice: ... left alone`). Each one closed is a run log line (`notice: closed one by its green button at ...`), and the first in `NOTICE_REPORT_INTERVAL` seconds saves a failure report, so that the screenshot shows what it was. On the frames of a 3.6 hour run it found all 55 frames of the admin's gift and 12 of the trade receipt's (the rest of that one's frames were its fade), and nothing in the other 656 frames, backpack and teleport dialogs included (the backpack's green plus tile and tab icons, joined, are 3.0 wide for their height, hence the aspect). Without it a run lost 19 minutes to the gift (no need could be done, then a rejoin for "no need resolved for 20 minutes") and 4.6 to the receipt (over the teleport dialog, the ride's backpack, Setup's pets tab).

Two more entries are the **character menu** (`character_menu.png`: the five
buttons Profile / Emotions / Dances / Actions / Activities; a click in the
middle of the screen dismisses it) and the **pet focus view** (`pet_focus.png`:
the camera on the pet with a BACK button; `close_pos` is that button). Only
`choose` and `pet` want the pet focused, so `pet_focus` is flagged
`only_when_unwanted`: `dismiss_stray_windows()` skips it while
`wanting_pet_focus()` is active (those two handlers), and clicks BACK in every
other case - including mid-task, e.g. a throw click that focused the pet.

`profile` is the player's own profile page (`ref/popups/profile.png`, the toolbar of Stickers / Preview / paint / save; `close_pos` is its red X), which a stray click on the character or the character menu's Profile opens.
`trade_board` is the Trading Hub's board of offers (`ref/popups/trade_board.png`, the "TRADING HUB" title with a magnifier on a pink board, over the offers made on the player's listings; `close_pos` is its red X; the real screenshot is `tests/popups/trade_board.png`): it came up over the world and stopped `/trades`. `trade_screen()` knows it as `"board"` and `open_trade_listings()` closes it first, whatever is asked, since the phone cannot be used under it; nothing wants it open yet, so there is no switch like the pet focus's.

`trade_request` and `trade_request_expired` are the cards that come up while trading is not set to No One: "<name> sent you a trade request" with Decline and Accept, and "Trade request from <name> has expired!" with Okay (real screenshots: `tests/popups/trade_request.png` and `trade_request_expired.png`). The words change with the name, so the request is known by its red Decline button (`ref/popups/trade_request.png`: the label tells it from the teleport dialogs' No) and the expired one by the first line of its card (`trade_request_expired.png`); they are closed by a button found by its colour and size (`find_dialog_button()`, `DIALOG_BUTTONS`: the entry's `button` key instead of a `close_pos`, since the card's height follows its text): Decline - Accept is never pressed - or Okay. The log line says that trading is not set to No One.

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
was seen after a rejoin). So `respawn_character()` looks for it
`RESPAWN_CONFIRM_LOOK_DELAY` after the keys and clicks Respawn if it's there;
the in-task checks would also catch it. Each time one is closed it is a
`stray window closed: respawn_confirm` line in the run log, so how often it
happens can be counted.

**Per-cycle checks while a task runs.** Waiting for the top of the next cycle
would leave a popup or a stray window in the way for the
whole of a handler (a minute or two). So
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
exempt in its own way (`unscrew(interruptible=False)`). The paycheck check is skipped
while the backpack is still open. If a disconnect made
it rejoin, `TaskInterrupted` is raised: the handler is abandoned (a rejoin
respawns the character; for the popup abort below,
`task_unscrew_tick()` respawns it before raising), `process_needs()` logs `<need> interrupted by` `a rejoin` / `a popup`, doesn't count it as resolved or as a failed attempt
(except the popup: that does count as an attempt, so a need a popup keeps
spoiling is disabled like any stuck need), and the next
check decides from the screen what's still needed. During setup (`interruptible=False`) nothing is
abandoned, since setup's clicks toggle things and can't be left halfway.

### Popup templates

Everything recognised by a crop in `ref/popups/` is looked up the same
way, by `popup_found(img, name)` / `popup_visible(img, name)`: the crop is matched
(`cv2.matchTemplate`, `TM_CCOEFF_NORMED`) within a box and counts as found when the score reaches
a threshold - the crop's centre is returned too, for the BACK button's click. The look is made in grey first (about five times faster) and the colour search only follows when the grey one scored `POPUP_GRAY_GATE`: most looks find nothing, and `dismiss_stray_windows()` makes a dozen of them a cycle (94 ms -> 40 ms for a cycle's checks on a real frame). The crops themselves are re-listed from their folder at most every `TEMPLATE_RECHECK_INTERVAL` (`load_templates()`). Each name's box and
threshold are in `POPUP_TEMPLATES` (`paycheck`, `sick_dialog`, `doctor_prompt`, `backpack_normal`, `backpack_back`) or, for
a stray window (and `pet_focus`), in `STRAY_WINDOWS` with `STRAY_WINDOW_MATCH_THRESHOLD`. The
detectors (`detect_paycheck()`, `detect_sick_dialog()`, `detect_pet_focus()`, the title check in
`detect_backpack_normal()`, the BACK button in `close_backpack_if_open()`) are that lookup plus
whatever they do with the answer, and `dismiss_stray_windows()` walks `STRAY_WINDOWS` through it. A
new crop is a file in `ref/popups/` plus an entry in one of the two tables. A name with no crop file is
never found.

**Backpack tabs.** The backpack remembers its last category tab. `select_backpack_tab()` only clicks the vehicles / toys tab when `is_backpack_tab_selected()` doesn't see the orange tile: a click on a selected tab does nothing. What expands the backpack is the orange button next to its settings button (`INVENTORY_EXPAND_POS`, used by the inventory tour), which is also where `PAYCHECK_DISMISS_POS_2` once landed.

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
white alone isn't enough, since the white is also any bright scenery. The frame
is also looked for at the two sides of the panel (at least
`BACKPACK_NORMAL_MIN_SIDE_PIXELS` in each of the `BACKPACK_NORMAL_SIDE_WIDTH`
columns at its left and right): the big "Friend" pill shown in the middle of the
screen with the cafe's name banner has the same purple and white, and was taken
for the backpack (`tests/popups/cafe_banner_friend_pill.png`). It is also
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

The Halloween minigames were moved out of `main.py` (see [Halloween 2026](HALLOWEEN_2026.md)).

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
   the clock (`state.white_since`) restarts whenever the screen isn't a *loading* white screen: `loading_white_screen()` is `detect_white_screen()` with nothing of the world on it (`WHITE_BARE_BOX`, at most `WHITE_BARE_MAX_SATURATED` saturated pixels, the update's blue letters not counted). The pizza need's walk ends on a big white plaza, white to `detect_white_screen()` but with the pet's name pill, the E button and the character on it (about 8000 saturated pixels, against 1450 to 2600 on a loading screen), and 30 seconds of it were taken for a loading screen and rejoined in the middle of the need (run 2, `tests/teleport/white_plaza_pizza.png`); the same goes for a white screen with a popup on it (22000 and more).
   (Needs stay visible on the white screen, so nothing else notices it.) It is the game's own doing and nothing the macro can help, so a rejoin that works is not a failure: the run log gets `white screen: ... - rejoining` and no screenshot, and the report counts them.
3. *Stuck* - `DISABLED_NEEDS_REJOIN_THRESHOLD` needs have been disabled as
   stuck, or one need that worked in the run has stayed disabled for `STUCK_NEED_REJOIN_AFTER` (`state.disabled_at`; run 4's ride was disabled after 26 minutes of tries and worked after the next rejoin). A need that never worked (`state.hopeless`) is not what a rejoin is for.
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
`state.last_detection` (set whenever a need is detected, and when the loop starts, or the game is rejoined), and again from each respawn, so it repeats at most once per interval.
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
part of any cycle: it runs when the **Run Setup at the next loop** box is ticked (`setup_next_loop`, taken by `run_setup_if_requested()` at the top of the loop's next pass and cleared), from the bot's `/setup` and from the automatic recovery. The
lure and tree actions are only reachable through `side_quest()`.

- **`pet_pen()`** - once per cycle after `side_quest()` (the game config's `pet_pen_enabled`, a checkbox of the Settings tab and the `pet_pen` switch of `/option`): when the Task Board's sign at the left edge of the screen (found by the colour and size of its wood, joined together where a green check and a chick are drawn over it, and by its title cloud `task_board_title.png` where the colours behind it are wood-coloured too, as at the school and the campfire: `find_task_board_button()`; found in 97% of the 1268 frames of two runs, the rest being white screens, popups and the like; clicked where it is) shows its red alert badge (`task_board_badge()`, looked for beside the sign, at most every `PET_PEN_BADGE_INTERVAL`; the badge is there for every alert) or `PET_PEN_CHECK_INTERVAL` has passed since the last look (the badge was not on in the capture of the Pet Pen notice), `collect_pet_pen()` opens the board and looks for the "Pet Pen Ready" card (`POPUP_TEMPLATES["pet_pen_ready"]`). With it: the card is clicked, `PET_PEN_ANIMATION_WAIT` waited, and the green NICE! (`DIALOG_BUTTONS["nice"]`) of the age-up screen clicked until it is gone. Without it the board is closed again. On the age-up screen `note_pet_friend()` looks for the Friend pill (`POPUP_TEMPLATES["pet_friend"]`, the purple pill with a gold edge and a heart count that a pet shows when it has grown as far as it does) and says in the run log which of the four places it is in; `pet_pen_swap_due()` switches it out. While it runs `state.task_board_wanted` stops `dismiss_stray_windows()` closing the board. It runs inside `run_side_action()`, so a rejoin or the stop key ends it. Tests: `tests/test_pet_pen.py` against the frames of `tests/pet_pen/`.
- **`look_at_daily_tasks()`** - optional (the game config's `daily_tasks_enabled`, the Settings tab's **Look at the Task Board's daily tasks**, the `daily_tasks` switch of `/option`; default off; it rides on the Pet Pen's look, so it needs `pet_pen_enabled`). When `collect_pet_pen()` has the Task Board open and there is no Pet Pen card, and `DAILY_TASKS_INTERVAL` has passed since `daily_tasks_looked`, it clicks the board's Tasks tab (`DAILY_TASKS_TAB_POS`), reads how far each daily task is from its progress bars (`read_daily_tasks()`: the rows of `DAILY_TASKS_BOX` that are grey (`DAILY_BAR_GREY`) or green, bands of `DAILY_BAR_BAND_HEIGHT` rows, the green share of each), writes `daily tasks: 0%, 40%` to the run log (or `the Tasks tab was not showing`) and saves a screenshot to `SCREENSHOTS_DIR`, so that the tasks of each day are on file. The tasks reset at `DAILY_TASKS_RESET_HOUR` (4 p.m. local time: the board's own timer said "expires in 00:30" at 15:29): the first look after it is due at once (`daily_tasks_due()`), the rest `DAILY_TASKS_INTERVAL` apart. A task not seen before is told by its words (`daily_task_crops()`: the name and what to do above each bar, `DAILY_TASK_TEXT_*`, without the progress digits or the reward) against the crops of those seen (`DAILY_TASKS_DIR`, `debug/daily_tasks/task_<n>.png`, matched at `DAILY_TASK_MATCH`): `keep_new_daily_tasks()` saves its crop, logs `daily tasks: N new task(s) seen` and puts the whole board into the run's moments as a frame labelled `new daily task` (when a moments capture runs). Refresh debug empties the folder, so the tasks are new again afterwards. A done task's reward is claimed too: when the board shows its green Claim All button (`daily_claim_all`, `tests/pet_pen/board_daily_claim.png`), it is clicked (`daily tasks: Claim All clicked`). The pet for a task is picked through the backpack's FILTERS panel, whose Effect rows (Regular, Neon, Mega Neon, No Potion, Rideable, Flyable) and Rarity rows (Common ... Legendary) are found like the Age rows (`filter_checkbox()`: the crops `ref/popups/filter_effect_*.png` and `filter_rarity_*.png`, each only in its own column, `FILTER_COLUMN_X`, because "Rare" is also the end of "Ultra Rare"): `equip_task_pet("neon")` leaves only Neon and Mega Neon ticked (`task_pet_rows()`, `set_filter_rows()`: each row found, clicked only when it differs and looked at again), `"common"` only Common, confirms, and equips the first pet of the narrowed list; `restore_pet_filters()` ticks every row again and picks Setup's pet (`repick_pet()`). The buttons that ran each by hand are archived in `scripts/archive/gui_buttons.md`. Whether the game's filter then lists only such pets is not known (a neon pet's Effect may also be No Potion or Flyable)
. **It is automatic** (the one setting, default off, now reads *Do the daily tasks that need a neon or a common pet*): `look_at_daily_tasks()` returns the pet the open tasks call for (`task_pet_wanted()`: the first task whose bar is not full (`DAILY_TASK_DONE_SHARE`) and whose text has the bold word Neon (`daily_neon`) or Common (`daily_common`), told by `daily_task_kinds()`; other tasks are left alone), and `collect_pet_pen()` closes the board and hands that to `apply_task_pet()`: a different kind than the one out is equipped through the filters (the rows of the kind that was out put back first), the kind is kept in the config (`task_pet`) and `pet_repick()` leaves the pet alone while it is out, the tasks are looked at every `DAILY_TASKS_ACTIVE_INTERVAL` meanwhile, and when no open task needs it any more `restore_pet_filters()` puts the filters and Setup's pet back. A kind that cannot be had (no such pet in the list, a step that did not take) is given up on until the next reset (`task_pet_failed`) and the filters put back. The level-up of the growing pet pauses while a task pet is out. It is not seen in the game yet. The tasks are "Complete N needs on a Common / Neon pet" and the like, which the macro's needs advance by themselves; choosing a pet for a task is not built (it needs a backpack card of a common and of a neon pet). The fixture is `tests/pet_pen/board_daily_tasks.png`.
- **`pet_pen_swap_due()`** - once per cycle after `pet_pen()`: `switch_pen_pets()` also every `PET_PEN_SWAP_INTERVAL` seconds since the last try (`pet_pen_swap_tried`, which Setup sets too), whether or not a pet was seen to grow up, so one that grew and was not noticed is replaced within the half hour. Between those, when the age-up screen showed a pet that reached Friend (`note_pet_friend()` sets the game config's `pet_pen_swap`) or any pets aged up in a collect (it is set after every one, so that all the full-grown pets are taken out) and `PET_PEN_SWAP_RETRY` seconds have passed since the last try, `switch_pen_pets()` runs as a side action and the flag is cleared once it came through. It respawns, walks to the pen (`PET_PEN_ROUTE`: out of the house by the door's E, along the fence, E at the pen - what a person did in a capture) and waits for the pen's screen (`pet_pen_title`). Each place whose pet carries the "Full grown!" sign (`full_grown_slots()`, the crop in each of the four `PET_PEN_SLOTS`) has its red X clicked (the others move up, so the pen is looked at again each time); then for each pet taken out `put_a_pet_in_the_pen()` clicks the green Add Pet tile, waits for the small backpack that opens over the pen and clicks the first pet that fits (`choose_pen_pet()`): in the grid of `PET_GRID_X` x `PET_GRID_Y` a cell is one of `pet_cell_state()`: `plus` (the green Add tile), `empty`, `locked` (grey, in a trade listing: corners darker than `PET_CELL_LOCKED_MAX_V`), `pen` (an orange frame with a little house, `PET_CELL_ORANGE_MIN`: it is in the pen already), `equipped` (a green frame, `PET_CELL_GREEN_BORDER_MIN`, when stronger than any orange: a gold pet's background is orange too) or `pet`; only `pet` is picked. While none fits the list is scrolled a notch at a time (the wheel on its scroll bar) until a scroll changes nothing; then no pet is put in, the backpack is closed again and the place stays free. Before a pet is picked `ensure_pen_age_filter()` looks at the backpack's Age filter, which the game keeps from one opening to the next: the filter button of the small backpack (`PET_PEN_FILTER_BUTTON_POS`) opens the same FILTERS panel as the big backpack's, in the same place, so Setup's steps are used (`untick_egg_and_full_grown()` looks at the Egg and Full Grown boxes first and clicks only a ticked one; Confirm, `SETUP_CONFIRM_POS`, only when something was changed, and the panel is opened again to see it took; an untouched panel is cancelled). The Full Grown row has a second crop for this panel (`AGE_ALTERNATES`, `age_full_grown_pen`). Tests: `tests/test_pet_pen.py` on the frames of that capture.
- **`replace_temporary_pet()`** - once per cycle after `pet_repick()`: the temporary pet the game gives every Tuesday and puts out has a pill reading "Temporary" (`pet_temporary`, `tests/pet_pen/temporary_pet_out.png`) and the pet is under it, where the click under the name pill misses (the pet need failed 5 times in a row and was disabled, then Setup found no pet out). When the pill is seen (with `level_growing_pet` on and no daily task's pet out; at most every `TEMPORARY_PET_RETRY`) `pet_outgrown` is set and `repick_pet()` run, so the pick skips the pet that is out and equips the first pet of the filtered list in its place. It was found in 17 of the frames of one run and in none of 700 others.
- **`pet_repick()`** - once per cycle after `pet_pen()`, with the game config's `level_growing_pet` on: every `PET_REPICK_INTERVAL` seconds `repick_pet()` runs the pet part of Setup again (`set_backpack_filter()`: the filters are looked at and left, the first pet of the list equipped) and respawns. A pet that has grown up is not in the filtered list any more, so the first one is another. The game says when the pet that is out has grown up - "Yay! Your pet is full grown! Add a new pet to continue earning XP.", a stray window (`pet_full_grown_notice`, closed by its Okay) - and `dismiss_stray_windows()` then makes the pick due at once (`pet_repicked` set to 0); the interval is only the fallback. The same function closes the age-up screen when it stays over the game (`age_up_screen`: its green NICE!, not while the pen has the screen open): in a 10-hour run it stood there for 20 minutes with the need bar hidden, nothing was detected and the macro waited for the 20-minute rejoin.
- **`leave_and_rejoin()`** - the bot's `/rejoin` (no button any more; archived in `scripts/archive/gui_buttons.md`), and what
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
     saved or the **Rejoin the private server** checkbox is off
     (`use_private_server`; `rejoin_server()` decides). A saved link that
     doesn't parse aborts instead of silently joining a public server.
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
  need handler, so popups and stray windows are handled and a
  rejoin (`TaskInterrupted`) just means it's tried again next cycle.
- **`set_new_lure()`** - clicks `LURE_NEW_POS_1` then `LURE_NEW_POS_2`
  (`UI_SETTLE` after each - they used to follow the key press with no wait), the two clicks that place a fresh lure. Split out from
  `lure_collect()` specifically so this placement step can be tested and
  tuned on its own, without walking to the old lure first each time.
- **`tree_collect()`** - holds `d` for `TREE_COLLECT_WALK_DURATION`,
  then `s` for `TREE_COLLECT_BACKWARD_DURATION` to line up with the
  tree, then presses `KEYS["interact"]` and respawns (like `lure_collect()`).
- **`setup_game()`** - respawns once (with the pen's check on that is the pen's own: its walk starts with a respawn and `check_pet_pen()` ends with one back in the house, and a third in front of them was three in a row for nothing at the start of every Setup), checks the pen (`check_pet_pen()`: `switch_pen_pets()` as for a pet that showed Friend, none switched if none is full grown, then a respawn back in the house; not a failed Setup when it cannot, only a line in the log), then three steps, each **looked at first, left alone when it says what it should, clicked when it does not, and looked at again after** (crops of the states in `POPUP_TEMPLATES`, real frames in `tests/setup/`; a button or window is waited for `SETUP_UI_WAIT` seconds, a label that is probably right already `SETUP_LOOK_WAIT`, looking every `SETUP_POLL`): `lock_house()` (the top button's label: `house_unlock` means locked already, `house_lock` is clicked at `SETUP_LOCK_HOUSE_POS` and `house_unlock` must follow); `set_backpack_filter()` (backpack key, `SETUP_BACKPACK_SETTINGS_POS`, the `filter_panel` that must come up; the sort button saying `filter_sort_favorites` is left alone, else `SETUP_SORT_MENU_POS` and Favorites clicked where its text `filter_option_favorites` is, the button must say Favorites; with `level_growing_pet` on (the Settings tab's **Setup levels a pet that still grows** checkbox, `/option level_growing_pet`, default on) the backpack's pets tab is made sure of first (`ensure_pets_tab()`: it opens on the tab it was last left on, a vehicle's after a ride, and the Age rows are the pets tab's; the tab is clicked when it is not selected and looked at after, and when it cannot be selected the Age boxes are left alone and the step fails with `setup: the backpack's pets tab could not be selected`), then the panel is scrolled to its Age rows (the wheel over its scroll bar, `FILTERS_SCROLL_POS`, a notch at a time, up to `FILTERS_SCROLL_TRIES`) and the Egg and Full Grown boxes are unticked where they are ticked, found by the text crops `age_egg` / `age_full_grown` (`AGE_LABELS`) with the box `AGE_CHECKBOX_DX` left of where the text starts and a tick told by its purple pixels (`AGE_TICK_HSV`), each looked at after its click - so that what the backpack lists is the pets that are neither eggs nor full grown, the ones that still grow; a panel with nothing to change is Cancelled (`SETUP_FILTER_CANCEL_POS`), one with changes is confirmed (`SETUP_CONFIRM_POS`) and opened again to see that the sort and both boxes took, then Cancelled; with the option on the first pet of the pets tab that can be picked (`choose_list_pet()`: not the green Add tile, not a grey one in a trade listing, not one with an orange frame - it is in the pen - and an equipped one with a green frame anywhere in view means the pet that is out is *kept*: the list is filtered to pets that still grow, so it showing there says that this one still does, and no other is switched in, `pet_cell_state()`; the list is scrolled while there is none, and when a scroll changes nothing the pet that is out is kept) is then equipped by `equip_pet()`: its card's green Equip button is found by colour, a card that says Unequip is left alone, and the card must say Unequip after - so a pet that has grown up leaves the list and the next one is equipped at the next Setup; with the option off the pet is left, and the Alicorn is equipped only when no pet is out); and `disable_trades()` (the gear `SETUP_TRADES_SETTINGS_POS` and `settings_title`, the menu `SETUP_TRADES_MENU_POS` and Interaction found by its text unless `settings_page_interaction` is up, the Trading setting `trading_no_one` left alone or `SETUP_TRADES_SETTING_POS` and No One found by its text, which must then be on the setting, and the settings closed at `SETUP_TRADES_CLOSE_POS`). A step that did not take is said in the log (`setup: ...`), the others still run, and the run ends with a failure and its screenshot (`log_failure()`) and `setup_game()` returns False; `setup_or_fail()` - what `/setup` and `/sloop` run - raises, so that the answer says it did not take and the loop of `/sloop` does not start. It first closes any stray window (up to `SETUP_STRAY_TRIES`; see [Stray The FILTERS panel stays scrolled where it was left (the Age rows, after Setup or the pen), so `scroll_filters_to_top()` brings its Sort By button into view before the sort is looked at or clicked - and again before the panel that was confirmed is looked at once more (a 2-hour run's Setups all failed with "Favorites was not in the sort menu": the click had gone to the Age rows).
windows](#need-handlers)) and runs inside `task_unscrew()`.
- **`travel_to_trade_hub()`** and **`leave_trade_hub()`** - the Trading Hub trips of the Discord bot's `/trade_hub` and `/leave_trade_hub` (see [Discord bot](#discord-bot)): the clicks of a human's run (`TRADE_HUB_*`; `click_steps()` does a list of them with `UI_SETTLE` after each), the first opening the backpack with `KEYS["backpack"]`, equipping the Trading Hub Phone from the Toys and using it, the second GO HOME and REGULAR SERVER. They are not part of the loop.
- **`in_trading_hub()` / `leave_hub_if_in()`** - the Trading Hub island is told by the text under its GO HOME button (`POPUP_TEMPLATES["go_home"]`: the text alone, since what is around the button is the island); `leave_hub_if_in()` (the first step of `/sloop`) does nothing out of it, else runs `leave_trade_hub()` and waits up to `HUB_LEAVE_WAIT` for the text to be gone and then up to `HUB_LOAD_WAIT` for the regular server to have loaded - the purple backpack button (`POPUP_TEMPLATES["backpack_button"]`) is back, which a loading screen does not show, so Setup does not start under it (each logged, False, if it does not happen).
- **`inventory_tour()`** - the bot's `/inventory` (see [Discord bot](#discord-bot)): opens the backpack (checking with `detect_backpack_open()` that it did), selects the pets tab (`INVENTORY_PETS_TAB_POS`) and clicks the orange button left of the settings one in the backpack's header (`INVENTORY_EXPAND_POS`; clicking the selected tab again does nothing) to **expand** the backpack (`INVENTORY_EXPAND_SETTLE` later `detect_backpack_expanded()` must see it, else the tour ends with "the backpack did not expand"; then `INVENTORY_LOAD_WAIT` seconds for its items to load, since the first picture had blank cells), then scrolls down it a step at a time (`scroll_down()`: a step is as many notches of the mouse wheel as move the list `INVENTORY_STEP_PIXELS` - 300 notches went from the first rows of the pets to the end of the list in one step, so how far a notch goes is measured on the first step, which is `INVENTORY_PROBE_CLICKS` notch(es): `scroll_shift()` looks for the band of the first picture at `INVENTORY_SHIFT_BAND` in the strip at the left and the one at the right of the next (the better match, at least `INVENTORY_SHIFT_MATCH`: a tooltip over the pointer hides one), and where it went is how far the list moved; when it is not found, `INVENTORY_SCROLL_CLICKS` notches are a step - over `INVENTORY_SCROLL_POS`, on the list's scroll bar at its right edge, since over an item its description opens and covers the items round it (and the pictures), that the mouse is first moved to by `hover_move()`, with the nudge that makes Roblox see the pointer there (a plain move did not: the wheel turned nothing), sent `INVENTORY_SCROLL_CHUNK` at a time with `INVENTORY_SCROLL_PAUSE` between, since a game may take one huge turn of the wheel for a small one, each notch `WHEEL_NOTCH` units because `pyautogui.scroll()` passes the number on as the Windows wheel amount, in which one notch is 120; then `INVENTORY_SCROLL_SETTLE`), saving a picture of the panel (`INVENTORY_PANEL_BOX`) as `INVENTORY_DIR/001.png`, `002.png` ... after emptying the folder. A step of `INVENTORY_SCROLL_CLICKS` notches moves the list a long way; the pictures show what is in view after each, so pets between two of them are not seen if a step is longer than the panel. It ends when a step moves nothing - a picture that differs from the last by less than `INVENTORY_SAME_LEVEL` grey levels (`same_picture()`) - or at `INVENTORY_MAX_PICTURES`, closes the backpack and writes `INVENTORY_DONE_PATH` (`{"pictures": N, "ended": why}`) however it ended, a stop, a backpack that did not open or expand or a Roblox that could not be focused included, so that the bot is not left waiting. Not part of the loop. The macro's own window is sent to the back for the tour and brought back however it ends (`send_macro_window_to_back()`), since it is always on top and was in the first picture. **First run in the game:** the backpack opened and expanded and the first picture was good, but the wheel moved nothing (it was given 1/120 of a notch a step), so the tour ended after one picture; that is fixed and not yet seen working. The picture box now takes the right side of the screen, as the first one cut off the right-hand column.
- **`trades_tour()` / `decline_offer()`** - the bot's `/trades` and `/decline`: the offers on the player's two trade listings (`TRADES_LISTINGS`), from the Trading Hub Phone (it needs no Trading Hub; the phone is equipped from the backpack's Toys tab when `POPUP_TEMPLATES["trade_phone_hotbar"]` is not in the hotbar, `equip_trade_phone()`: a payday popup (`detect_paycheck()`) is dismissed first, as it covers the backpack's header and takes the click on the search box - in captures the letters were then typed into the game, "e" used the world - the phone is found first by its picture anywhere in the Toys tab's grid (`POPUP_TEMPLATES["phone_item"]`) and clicked where it is - in the unfiltered list it is behind the badge, in the second slot - and only if it is not seen is the search used: `focus_search_box()` types only the first letter of `TRADES_SEARCH_TEXT` and goes on once the box's placeholder (`POPUP_TEMPLATES["search_placeholder"]`) is gone, else the box is clicked again (`TRADES_EQUIP_TRIES`; `tests/trades/payday_over_backpack.png`, `search_unfocused.png`); `TRADES_SEARCH_TEXT` is typed in the Toys tab's search box (`TRADES_SEARCH_POS`; `type_keys()` presses the keys `TYPE_KEY_DELAY` apart with `state.typing` set, which the hotkey watcher respects, since "phone" holds the stop key), the list follows the typed text about two seconds late (a capture showed it: the first item clicked sooner was the badge, which was still first), so the first item is clicked only once the phone's picture is in the first slot (`wait_for_phone_first()`: `POPUP_TEMPLATES["phone_icon"]`, up to `TRADES_SEARCH_MAX_WAIT` seconds; the frames are `tests/trades/toys_filtered.png` and `toys_unfiltered.png`), and the card that comes up must be the phone's (`POPUP_TEMPLATES["phone_card"]`, the "Trading Hub Phone" name; if it is not - the search did not filter and the badge was first, which is what was equipped - the box is emptied and the search made again, `TRADES_EQUIP_TRIES` times, and nothing is equipped), Equip is clicked where that card is (`TRADES_EQUIP_OFFSET` from its name), the search is cleared with backspaces and a click at `TRADES_UNFOCUS_POS` takes the focus out of the box, and the backpack is closed with its key - the slot it was first clicked by was the badge's, as the Special row's order changed with the favourites filter; a click in the world, `TRADE_HUB_USE_POS`, uses it). Every screen is known from a crop (`POPUP_TEMPLATES`: `trade_phone_home`, `trade_menu`, `my_listings`, `listing_1_of_2` / `listing_2_of_2`, `offers_title`, `decline_button`, `decline_dialog`; `trade_screen()` returns the first that is seen of dialog, board, detail, offers, listings, menu, phone (outside the Trading Hub the listings' title is "TRADING HUB", the menu's, with the same "LISTING 1/2": a menu title with a listing number is the listings; `tests/popups/trading_hub.png` is that window) - the ones further in first, because the earlier screens are under them, dimmed), and a click on one is only made when the screen it is for is seen: `open_trade_listings()` gets to MY LISTINGS from wherever the phone is (an "Are you sure?" that is up is cancelled with `EXIT_STAY_POS`, never answered), `select_listing()` goes to the other listing with the arrow (`TRADES_NEXT_POS`) and `open_offers()` presses OFFERS (`TRADES_OFFERS_POS`). The offers list shows a row per offer, a few rows at a time: `offers_tour()` scrolls it a step at a time with the mouse on the list's scroll bar (`TRADES_SCROLL_POS`: over a row the item's description pops up and covers the rows) (`TRADES_SCROLL_CLICKS` notch per step: about 95 px in the game, under the `TRADES_ROW_PITCH` of a row, so that no offer is skipped; the picture is taken once the list has stopped gliding, `grab_still()`, up to `TRADES_SCROLL_MAX_WAIT`), finds the whole rows by their red DECLINE buttons, `TRADES_DECLINE_DY` below the place of the row (`visible_offers()`: `TRADES_BUTTON_*`; an "Exact Match" offer has a green row, in which its green ACCEPT button is lost - `tests/trades/offers_exact_match.png`), numbers them by their place in the list (`OfferList`: a step moved the list by where the first row of the picture before is among the rows of the picture after, modulo the pitch, and an offer's number is its row counted from the first seen; offers that look alike - the same eggs on both sides - were all numbered 1 when a row was known again by its picture) and keeps a picture of the panel (`TRADES_PANEL_BOX`) with `#<n>` written by each row (`TRADES_DIR/L<listing>_<n>.png`; a step that showed no new offer keeps none) and the pictures of the rows (`TRADES_ROWS_DIR`). It ends when a step moves nothing (`TRADES_SAME_LEVEL`) or after `TRADES_MAX_STEPS`; `TRADES_DONE_PATH` says what each listing gave, and the bot sends the pictures. `decline_offer()` takes a listing and an offer number (`decline_row()` does the clicks: the row, DECLINE, Yes; `decline_all()` is the same for every offer of a listing, the offer being `TRADES_ALL_OFFERS`): the offer is found in the list by its place (`OfferList`, the same as `/trades` numbered it) and must look like the saved picture of its row (`row_is()`, at least `TRADES_ROW_MATCH`: a list that changed since `/trades` is not declined from: an offer that is not found is not touched, and the answer says so), its row is clicked (`TRADES_ROW_CLICK_X`) to show it on its own, DECLINE (`TRADES_DETAIL_DECLINE_POS`) is pressed once `decline_button` is seen, and Yes (`TRADES_DECLINE_YES_POS`) once the dialog is seen; ACCEPT is never clicked. The offer as it was is kept (`TRADES_DECLINED_PATH`) and `TRADES_DECLINE_DONE_PATH` says how it ended. A listings window or an offers list that does not come up, and a phone that cannot be opened, are failures with a screenshot (`log_failure()`, naming the screen that was seen: `debug/failures/`), so that what the macro was stuck on is on file. The frames the crops and positions come from are in `tests/trades/` (only the phone's screen and the hotbar kept). Not tried in the game.
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
`TaskInterrupted` is the same mechanism for a different cause: a
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
`finally` block as a last line of defense. Every key the macro holds is pressed with `key_down()` and let go of with `key_up()`, which keep the keys that are down in `HELD_KEYS` (`tools/check_constants.py` fails on a bare `pydirectinput.keyDown`), so `release_all_inputs()` lets go of exactly those, and of the fixed list `cfg.HELD_KEYS` as well (the movement, helicopter, zoom, jump, respawn and camera-turn keys), also when the exit comes in the middle of a hold; a key that raises when let go of (the fail-safe) does not stop the others. The releases skip PyDirectInput's fail-safe check (`unchecked()`: the wrapped call that `functools.wraps` keeps), because with the cursor in a corner - the fail-safe's sign - every checked call raises and what the macro holds would stay held after the very stop that corner asked for; the fail-safe itself stays on for everything else, and the cursor is not moved. A stop releases everything **at once** (`stop()` calls `release_all_inputs()` itself, from the thread that asked, without waiting for the worker to notice), and `key_down()` and `mouse_down()` refuse (`StopRequested`) to press anything once the stop flag is set, so the worker cannot press one more key in the gap. It releases a mouse button only if `pressed_inputs()` says it is down: a button-up with the button not pressed is a click to the window under the mouse, and a right one over Roblox's border or title bar opens the window menu and takes the focus (seen where a handler was interrupted with the mouse wherever it left it).

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
- **`keep_moments`** - on/off (default on): whether the loop captures the run's moments, see [The moments of a run](#the-moments-of-a-run).
- **`level_growing_pet`** - on/off (default on): Setup filters the eggs and full-grown pets out of the backpack and equips the first pet left, and `pet_repick()` does the same again every `PET_REPICK_INTERVAL` (`pet_repicked`: when it last did, a timestamp). `set_backpack_filter()` stamps `pet_repicked` whenever it runs with the option on, whether or not every step took (it used to be stamped at the end of a Setup that took, so a Setup that failed on one step was followed at once by the repick, and the backpack's steps ran twice). The run log shows each pass: `setup: begins` / `setup: ends, ...` and `backpack: filters and pet looked at`, so a doubled pass can be told from a Setup that ran twice.
- **`pet_outgrown`** - set by the "Yay! Your pet is full grown!" notice (`pet_full_grown_notice`, together with `pet_repicked` back to 0), cleared by the next pick: that pick does not keep the equipped pet even when its green frame shows in the filtered list (in run 6 the pick after the notice found it there and kept it: the list's age filter and the game's "full grown" are not the same thing, the pet's pill reading "Friend" being the sign).
- **`pet_pen_enabled`** - on/off (default on) for `pet_pen()` (`pet_pen_looked`: when it last opened the Task Board, a timestamp). `pet_pen_swap` is the flag that the pen's full-grown pets are to be taken out (set after a collect that aged pets up and by a Friend pill; cleared when `switch_pen_pets()` came through) and `pet_pen_swap_tried` when that was last tried (a timestamp). Both are in [Side actions](#side-actions).
- **`money_tree_enabled`** and **`lure_enabled`** - on/off switches (default on) for the two halves of
  `side_quest()`: the money tree and the lure. They are checkboxes on the Settings tab, and can be flipped while a
  workflow runs. A config file from before the split has one `side_quest_enabled`, which is carried over to both once, when the file is read.
- **`setup_next_loop`** - the **Run Setup at the next loop (once)** checkbox on the Main tab (default off), which replaces the Setup button. `run_setup_if_requested()` takes it at the
  top of each pass of the loop, in place of the first respawn (Setup respawns by itself, and the loop's own would be one more): it clears the switch, unticks the box and runs `setup_game()`, so a loop that is running takes it at its next cycle and a loop that is
  not takes it when it starts. A Setup that did not take is a line in the run log and the loop goes on. The bot's `/setup` and `/option setup_next_loop` do the same two things from outside.
- **`resume_on_focus_loss`** - the **Resume after focus loss** checkbox (default
  off); see [Stopping & focus safety](#stopping--focus-safety).
- **`needs_enabled`** - `{need: on or off}`, the switches next to the Test buttons on the Debug tab. A need that isn't in it
  follows `ENABLED_NEEDS`, so by default they are all on; **Reset Config** puts them back.
- **`record_next_run`** - the **Record the next run (low quality video)**
  checkbox on the Debug tab's Options subtab (default off); see [Recording a run](#recording-a-run). Cleared, and
  the checkbox unticked, when the run that it was for starts.
- **`blackout_hotkey_enabled`** - the **H blanks the screen in Roblox (off: type it)** checkbox on the Settings tab (default on; the label names
  `HOTKEYS["blackout"]`). Off, the key does nothing here and can be typed in the game, and a blank screen is taken away when it is turned off; see
  [Blanking the screen](#blanking-the-screen).
- **`private_server_link`** - the private server link `leave_and_rejoin()`
  joins, typed into the GUI's entry box (saved when you press Enter or click
  away). It lives here rather than in `magic_numbers.py` because it's a key
  to your server and the source is in a git repo. **Reset Config** keeps it.
- **`use_private_server`** - the **Rejoin the private server (off: a public
  one)** checkbox on the Settings tab, above the link (default on). Off, a
  rejoin goes to a public server and the link stays saved. **Reset Config**
  puts it back on.
- **`new_need_hotkey_enabled`** - the **Ú teaches a new need icon in Roblox (off: type it)** checkbox on the Settings tab (default on; the label names
  `HOTKEYS["new_need"]`). Off, the key does nothing here and can be typed in the game; see [Teaching a new need](#teaching-a-new-need).

During a run, this file is read and updated by:

- **`side_quest()`** - called right after `unscrew()`, every cycle of
  `run_full_cycle()`'s loop (so on the same cadence as the paycheck
  check); first applies the daily money reset (`reset_money_if_due()`), then does
  nothing for a half whose switch (`money_tree_enabled`, `lure_enabled`) is off. While
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
config", the defaults: an empty private server link).
`save_game_config()` writes a temporary file and moves it into place, so a
crash partway can't leave a truncated config either. `side_quest()`
deliberately doesn't hold a loaded config across a long
action and save it afterwards - that could silently undo a checkbox
clicked in the meantime.

## GUI internals

`AdoptMeGUI.create_ui()` builds the whole panel. A few things worth
knowing if you're modifying it:

- **The three tabs.** *Main*: the loop and stop buttons, the **Run Setup at the next loop (once)** checkbox (Setup is no longer a button: see `setup_next_loop` under [Persisted game config](#persisted-game-config)). The Trading Hub trips have no button either; they are the bot's `/trade_hub` and `/leave_trade_hub`. *Settings* (`build_settings_tab()`), in three groups under headings (`add_section()`): **The loop** (the money tree, the lure, resume after focus loss, the Pet Pen, Setup levelling a growing pet), **Rejoin** (private server or public, and the link), **Keys** (the two hotkey switches, with a line saying what the stop, capture and screenshot keys do); the debugging options are not here. *Debug* (`build_debug_tab()`), two subtabs: *Options* (`build_debug_options()`: record the next run, capture the moments, show the debug lines, **Refresh debug**, **Reset Config**) and *Needs* (`build_needs_tab()`: a switch and a Test button for each need, and the test of every teleport handler). Every checkbox of the Main, Settings and Debug tabs is a persisted switch made by `add_config_checkbox()`, and is also an `option` of the bot's `/option`.

- **`run_async(func)`** is how every long-running action is started: it
  disables every button in `self.action_buttons`, runs `func` on a
  background daemon thread (so Tkinter's main loop never blocks), and
  *always* re-enables them in a `finally` block - normal finish, a
  `StopRequested`/`FocusLost`, or an unexpected exception all take the
  same path back to a usable UI. A `TaskInterrupted` (a `setup_game()` run on its own, the bot's `/setup`, cut short by a disconnect, whose rejoin ran setup itself) is
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
- **`run_on_ui_thread(func, timeout)`** is how the worker thread touches Tk
  (which is only safe from its own thread): it queues `func`, waits until it
  has run or `timeout` seconds pass (`None` = as long as it takes, for a
  dialog such as the new-need name prompt) and returns what it returned.
- **`watch_stop_hotkey()`** starts a thread that watches two hotkeys (holding one doesn't retrigger; `hotkey_fires()` decides):
  `HOTKEYS["stop"]` stops a running macro, in any window (it
  does nothing while nothing runs), so you can take over the computer without
  reaching for the GUI, `HOTKEYS["capture"]` starts or stops an
  [input capture](#input-capture), and `HOTKEYS["screenshot"]` saves a picture of the whole screen (`save_screenshot()`: `grab_full_screen()`, at the monitor's own size, with nothing cut out or scaled, so a pixel read off it is a pixel of the screen; the macro's own window is in it if it is up, the blank screen's black window is not) size) as `SCREENSHOTS_DIR/screenshot_<date>_<time>.png`, both only while Roblox is the focused window. The action is handed to Tk's thread. A hotkey is a letter or digit, or a character that is a key of its own on the keyboard layout in use (`hotkey_vk()`: `VkKeyScanW`; `"ô"` on a layout that has it so); one the layout has only with Shift, AltGr or a dead key, or not at all, is never down and said once (`hotkey_down()`).
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

### The moments of a run

While the loop runs, `start_moments()` (called by `run_workflow_loop()`; the game config's `keep_moments`, a checkbox of the Debug tab's Options subtab and the `keep_moments` switch of `/option`) records the game into `MOMENTS_DIR/run<N>_<time>/`: an `InputCapture` with no inputs (`inputs` False), looking every `MOMENTS_FRAME_INTERVAL` seconds and kept to `MOMENTS_ZIPS` zips however long the run goes - when it would need more, every other frame is deleted and the frames are saved twice as far apart, so what is left is spread over the whole run (`reserve_bytes` keeps room in the first zip for the text files below). It is not started while the hotkey's capture runs, and that capture's start ends it (`finish_moments()`).

**Screens of the phases of a need.** While a need runs, `phase_shot(label)` keeps the screen in memory at its phases (a teleport's landing after the first load and after the step back and the second load, the screen before each step of a teleport walk and after the last, after the helicopter key, the doctor's desk before the prompt is looked for, the pen when it opened, after the wait for its places, after each X, before it is closed; at most `PHASE_SHOTS_MAX`). A need that could not be resolved or was not cleared has them written into the capture (`phase_commit()`: `frame_p<n>_<seconds>.png`, the frame's line in `events.jsonl` carrying a `label` such as `school was not cleared: before step 2: a 10.0s`, a `phase screens kept: ...` log line), a pen switch always, and one execution in `PHASE_KEEP_GOOD_EVERY` that went well, so that a failing walk can be set beside a good one (where it started, which way the camera faced, where it ended). They are protected frames, within `MOMENTS_PROTECTED`. Only with a moments capture running.

What the macro was doing is with it, so that the zips can be sent on their own:

- **Every frame's line in `events.jsonl`** carries `moments_context()`: `activity` (`state.activity`, set by `task_unscrew(what=...)` - `need handler: <need>`, `side action: <name>`, `setup` - and by the rejoin; "waiting for needs" between them), `detected` (the needs last seen), `cycles`, `resolved`, `failures`, `disabled` and `task`.
- **`log_run_event()` hands every line it writes to `note_moment()`**, which puts it into `events.jsonl` (a `log` line, on the clock of the frames). A line that starts with one of `MOMENTS_MARK_PREFIXES` - a failure, a scheduled rejoin, a white screen, a recovery, a disabled need, a quiet period - is also a moment: `InputCapture.mark()` saves a frame at once and every `CAPTURE_FRAME_INTERVAL` for `RUN_CAPTURE_BURST` seconds, and those frames are never thinned out (at most `MOMENTS_PROTECTED` of them; the zips hold about twice that), at most one mark every `MOMENTS_MARK_GAP` seconds. The moments are listed in `state.moments_notes`.
- **`output.log`** (`MomentsLog`, a handler of the macro's logger): everything the macro prints, the debug lines too, in two rotating files of `MOMENTS_LOG_MB` / 2, so that the end of a long run is there.

At the run's end `finish_moments()` deletes the folder when the run lasted less than `MOMENTS_MIN_RUN`. Otherwise it keeps the frames of the last `MOMENTS_TAIL` seconds too (`protect_tail()`, where a run that went wrong shows why), writes the files of `write_moments_files()` - `README.txt` (what the macro is, what every file is, how to read it, the list of moments), `run_log.txt` (the run's own log, at most `MOMENTS_RUN_LOG_MB`), `status.json`, `config.json` (the game config but for the private server link, the enabled needs, the counts), `failures.txt` (the `info.txt` of each failure folder of the run) and `failure_<folder>.jpg` (their screenshots, at most `MOMENTS_FAILURE_SHOTS`, spread over the run), and the commit and the moments in `info.json` - and zips it (`zip_capture_in_background()`: the first part holds `README.txt`, `info.json`, `events.jsonl` and the other text files, deflated; the frames are stored). The folders of the last `MOMENTS_KEEP` runs are kept. `/pull moments` fetches them; they are for improving the macro and fixing what failed.

### Input capture

Press `HOTKEYS["capture"]` while Roblox is the focused window to start recording and again to stop. It is for working out
how something is done by hand - a walk to a need's place, say - and gives the macro's author lossless
frames and the inputs that went with them. It is separate from **Record the next run** (a low-quality
video of a macro run): it works whether or not the macro is running, and the macro's own key presses
and clicks are recorded too, since `GetAsyncKeyState` sees them like any others.

Each capture is a folder in `debug/captures/` (`CAPTURES_DIR`, git-ignored, emptied by **Refresh debug**;
the newest `MAX_CAPTURES` are kept) named `capture_<date>_<time>`. **Getting one to someone else:** when a
capture stops, `zip_capture()` (on a thread of its own) also splits it into zips of at most `CAPTURE_ZIP_MB`
(chat attachments are limited to 30 MB) in the capture's `zips/` folder (made in `zips.partial/` and moved into place once all are made, so a `/pull` never takes half of them; one that comes while they are made is told the capture is still being zipped and copies nothing):
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
window goes with the process (`close()` takes it away first). The hotkey blanks the screen while the macro is doing anything - the loop or an action such as Setup (`blackout_by_hotkey()`; not when nothing runs), and shows a blank screen again at any time; it works at all only while the Settings tab's checkbox has it on (`blackout_hotkey_enabled`), and the `/blank` command does not ask either; the cursor is hidden over it.
The control window is blanked with the rest of the screen: while the screen is blank the black window is put back at the top of the always-on-top windows every
`BLACKOUT_RAISE_INTERVAL_MS` (`Blackout.keep_on_top()`, `SetWindowPos` with `HWND_TOPMOST` and no activation, the handles passed as pointer-sized values), and at once when the control window is brought back (`bring_to_front()`), because the control window coming back up from the
taskbar lands above it and Windows decides when, so a single raise is either too soon or too late.

**With a virtual display installed** (the Virtual Display Driver, a software monitor; `virtual_display_present()` looks through the display adapters for a name with one of `VIRTUAL_DISPLAY_NAMES` in it - those names are not read off a real PC), `Blackout.show()` first tries `switch_display()` (`BLACKOUT_VIRTUAL_DISPLAY`): it runs Windows' own `DisplaySwitch.exe /external` (Win+P's Second screen only; `display_switch()` runs only that and `/internal`, with no argument from anywhere else), so the PC's own panel is off - which no black window can make it - and the game is on the virtual display, where the macro's screenshots and clicks work as before (they are of the primary display, which is now the virtual one). `BLACKOUT_DISPLAY_SETTLE` seconds later it looks that Roblox is there and shows a picture (`display_switch_worked()`: not one flat colour, `BLACKOUT_FLAT_LEVEL`); if not it switches back (`/internal`) and the black window is used instead. Showing the screen again (`hide()`) runs `/internal`. While the screen is blank by either way the PC is asked not to sleep or turn the display off (`keep_awake()`, `SetThreadExecutionState`, undone with the blank). That the panel is off is kept in the game config (`display_switched`, written before the switch), so that a macro that dies with it off puts it back at its next start (`Blackout.recover_display()`). `/blank` says which it did ("blanked (display off)"). The switch takes the focus from Roblox for a moment, which used to stop a running macro (a lost focus is a stop): `state.display_switching` is up during it and `check_focus()` waits for it to end (`DISPLAY_SWITCH_MAX_WAIT`) before it looks at the focus, and `refocus_roblox_after_switch()` brings Roblox back to the front afterwards, so that the loop goes on and the keys reach the game. The blackout key shows a blank screen again from any window (it blanks only while Roblox is focused, as before), since with the panel off the focus may be anywhere. Win+P pressed by hand is not known to the macro and still counts as a lost focus, unless **Resume after focus loss** is on. The driver is installed by hand (see the project's page); nothing here installs it, and nothing from Discord can.

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
| Behavior flags | `ENABLED_NEEDS`, `HOTKEYS["stop"]`, `BACKPACK_*`, `SCREEN_CHANGE_SIZE`, `REJOIN_*`, `HELICOPTER_REQUIRED` |
| Window / coordinates | `REFERENCE_WIDTH`, `REFERENCE_HEIGHT`, `REFERENCE_CENTER_X/Y`, `ROBLOX_RECT_TTL` |
| Key bindings | `KEYS` (every key the macro presses, by what it is for: walking, jump, backpack, mount, interact, speed boost, zoom, helicopter, camera turn), `KEY_SEQUENCES` (respawn, leaving the game), `HOTKEYS` (stop, capture, blackout), and the derived `MOVE_KEYS` and `HELD_KEYS`; `STOP_HOTKEY_POLL_INTERVAL` |
| Timing | `WATCH_POLL_INTERVAL`, `NEED_WATCH_JOIN_TIMEOUT`, `RESPAWN_WAIT`, `WALK_TO_BUTTONS_DURATION`, `NEED_CHECK_RETRY_DELAY`, `LOOP_DELAY`, `STOP_CHECK_INTERVAL`, `STOP_CLICK_GRACE`, `NEED_GONE_*` |
| Stuck needs | `NEED_STUCK_CHECKS`, `NEED_NEVER_WORKED_FAILS`, `NEED_HOPELESS_RETRY`, `STUCK_NEED_REJOIN_AFTER` |
| Pet focusing | `FOCUS_PET_REGION_TOP_PERCENT`, `FOCUS_PET_FRAME_GAP`, `FOCUS_PET_DIFF_THRESHOLD`, `FOCUS_PET_MERGE_KERNEL`, `FOCUS_PET_MIN_AREA` |
| Screen positions | `CATCH_*_POS`, `EMPTY_POS`, `RIDE_*_POS`, `LURE_NEW_POS_*`, `SETUP_*_POS` |
| Side quest / setup | `TREE_HARVEST_YIELD`, `MONEY_COLLECTED_TARGET`, `TREE_CHECK_INTERVAL`, `MONEY_RESET_HOUR`, `LURE_RECOLLECT_INTERVAL`, `LURE_COLLECT_*`, `TREE_COLLECT_*` |
| Need-icon detection | `NEED_ICON_TOP_PERCENT`, `NEED_ICON_WIDTH_PERCENT`, `NEED_ICON_BLANK_*`, `NEED_ICON_MIN/MAX_RADIUS`, `NEED_ICON_HOUGH_*`, `NEED_STRIP_*` (when the circle search is skipped) |
| Need-icon matching | `ICON_MATCH_THRESHOLD`, `ICON_BW_THRESHOLD`, `ICON_CLAHE_*`, `ICON_COMPARE_SIZE`, `ICON_MATCH_RADIUS_FRACTION`, `ICON_CROP_RADIUS`, `ICON_SHIFT_TOLERANCE` |
| Button detection | `BUTTON_BAND_X/Y`, `BUTTON_MIN_AREA`, `BUTTON_MIN_CIRCULARITY`, `BUTTON_MAX_COUNT`, `BUTTON_NAMES`, `BUTTON_LOOSE_*_FACTOR`, `BUTTON_PURPLE_*_ITERATIONS`, `BUTTON_OUTLINE_PADDING` |
| Discord bot | `PULL_SOURCES`, `BOT_MAX_PULL_MB`, `BOT_PULL_CONFIRM_MB`, `BOT_PULL_CONFIRM_TIMEOUT`, `COMMANDS_DIR`, `COMMANDS`, `COMMAND_POLL_MS`, `COMMAND_MAX_AGE`, `REJOIN_STOP_POLL_MS`, `REJOIN_STOP_WAIT_MS`, `START_LOOP_DELAY_MS`, `BOT_COMMAND_ACK_WAIT`, `BOT_COMMAND_ACK_POLL`, `BOT_ACK_KEEP`, `BOT_START_CONFIRM`, `BOT_RVIEW_*`, `BOT_GIT_TIMEOUT`, `BOT_PIP_TIMEOUT`, `NEED_NAMES`, `OPTION_SWITCHES`, `SHOW_DEBUG_LINES_OPTION`, `TRADE_HUB_*`, `EXIT_*`, `BOT_BLANK_WAIT`, `RESUME_LOOP_AFTER`, `BOT_ACTION_WAIT`, `BOT_ACTION_POLL`, `INVENTORY_*`, `TRADES_*`, `BOT_TRADES_WAIT`, `BOT_TRADES_POLL`, `BOT_INVENTORY_WAIT`, `BOT_INVENTORY_POLL`, `BOT_FILES_PER_MESSAGE`, `BOT_CHANNEL_TRIES`, `BOT_CHANNEL_RETRY_WAIT`, `BOT_WATCH_INTERVAL`, `BOT_ALERT_GAP`, `BOT_REPORT_WAIT`, `BOT_MAX_UPLOAD_MB` |
| Debug logging | `OUTPUT_LOG_PATH`, `RUNS_DIR`, `FAILURE_DIR`, `STATUS_PATH`, `OUTPUT_LOG_MAX_BYTES`, `MAX_FAILURE_FOLDERS`, `MAX_REPORTS`, `CONSOLE_MAX_LINES`, `QUIET_LOG_INTERVAL`, `RESOURCE_LOG_INTERVAL` |
| Recovery | `DISABLED_NEEDS_REJOIN_THRESHOLD`, `NO_PROGRESS_REJOIN_INTERVAL`, `NO_DETECTION_RESPAWN_INTERVAL`, `DISCONNECT_*`, `WHITE_SCREEN_*`, `REJOIN_*`, `ROBLOX_*`, `FOCUS_RESUME_*`, `PAYCHECK_DISMISS_POS_*` |
| Input capture | `HOTKEYS["capture"]`, `HOTKEYS["screenshot"]`, `SCREENSHOTS_DIR`, `CAPTURES_DIR`, `RUN_CAPTURE_BURST`, `RUN_CAPTURE_PROTECTED`, `CAPTURE_FRAME_INTERVAL`, `CAPTURE_CHANGE_LEVEL`, `CAPTURE_CHANGE_FRACTION`, `CAPTURE_MAX_SKIP`, `CAPTURE_POLL_INTERVAL`, `CAPTURE_MOVE_INTERVAL`, `CAPTURE_MAX_MINUTES`, `CAPTURE_MIN_FREE_MB`, `MAX_CAPTURES`, `CAPTURE_ZIP_MB` |
| Backpack tabs | `TAB_HALF_SIZE`, `TAB_SELECTED_*` (a category tab that is orange is already selected) |
| Blanking the screen | `BLACKOUT_*` |
| Popup crops | `STRAY_WINDOWS`, `STRAY_WINDOW_MATCH_THRESHOLD`, `POPUP_TEMPLATES` (box and threshold of the paycheck, sick-dialog, doctor-prompt, teleport-Yes and backpack crops), `NOTICE_*` (the notice checker) |
| Transitions | `WHITE_*` (`WHITE_POLL`, `WHITE_LOOK`, `WHITE_BARE_*`, ...), `HOUSE_EXIT_*`, `TELEPORT_*` (`TELEPORT_DIALOG_*`, `TELEPORT_SECOND_WHITE_LOOK`, ...), `SERVER_CLOSING_*` |
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
`run_counter.txt`, `status.json`), **`runs/`** (`RUNS_DIR`: what belongs to one run - its own log, `run<N>.txt`, and its chart, `run<N>.png`, side by side) `recordings/` for the
videos, `captures/` for the input captures (see [Input capture](#input-capture)), the pictures of the last inventory tour (`inventory/`), those of the screenshot key (`screenshots/`), the commands the Discord bot leaves (`commands/`, with the macro's answers) and the macro's error output when the bot started it (`MACRO_STDERR_PATH`, `logs/macro_stderr.log`; **Refresh debug** empties it instead of deleting it, since the macro keeps it open and Windows will not delete an open file - `empty_logs_folder()`), and the game config (`.config`) in `debug/` itself. The Debug tab's
Debug tab's Options subtab's **Refresh debug** button (`refresh_debug()`, after a confirmation, not while a
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
  `generate_run_report()` in `main.py` with OpenCV (no extra dependency). It is **not** drawn when a run stops: the bot's `/report` asks for it (the `report` command: `report_from_outside()`
  draws the newest run of the log, or the one named, with `write_run_report()`); the oldest reports beyond `MAX_REPORTS` are deleted then (`keep_newest_files()`, pngs only) - the logs they are drawn from, `run_log.txt` and the runs' own `run<N>.txt`, are kept, so one can be drawn again. Five panels: needs resolved per unit of time (the unit is picked
  from `BUCKET_STEPS` so the run fills about `TARGET_BUCKETS` bars, stacked by
  basic / teleport / other need, with failures and rejoins marked); needs
  detected per check; average seconds per resolved need with how many there
  were; counts of failures by kind and other events (a rejoin after a white screen is an event, `white screens (rejoined)`, not a failure); and the script's own CPU and memory use over time, each with the whole PC's use as a second line (CPU on the same scale; memory in % on its own axis, labelled down the right edge). A need disabled as stuck is drawn in the first panel as a grey bar from when it was disabled until the next rejoin (which clears them), named above it. `parse_run()` reads
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

`scripts/` holds the programs that are not part of `main.py`: the Discord bot and the archived Halloween event (`Halloween2026.py`, see [Halloween 2026](HALLOWEEN_2026.md)). The bot is a separate program: a Discord bot that answers questions about the macro from anywhere (a phone, say). It does the read-only commands, alerts, pulling things out of the debug folder into OneDrive on request, starting and stopping the macro, and updating it. It runs **on the PC the macro runs on**, in the same logged-in desktop session, because it reads the
macro's files and grabs its screen; it connects out to Discord, so no port needs opening, and the Discord app itself is only on whatever device is used to type
the commands. It is started by hand, next to the macro: `python scripts/discord_bot.py [path to bot_config.json]` (the old `bot/discord_bot.py` is a stub that runs it, so that a bot started with the old command - and the restart `/update` makes - still comes up; delete it once the new command is used).

- **`scripts/bot_lib.py`** holds every decision that needs neither Discord nor Windows, as plain functions (`tests/test_bot.py` runs them anywhere, in CI too):
  `load_config()` (a `ConfigError` saying what is wrong), `is_allowed()`, `status_text()`, `tail_lines()`, `newest_report()`, `looks_blank()`,
  `main_py_in()`. The paths it reads are the macro's own (`STATUS_PATH`, `RUN_LOG_PATH`, `RUNS_DIR` of `magic_numbers.py`), so a moved file moves for both.
- **`scripts/discord_bot.py`** is the glue that can't be tested away from Discord (discord.py, the screen grab, the process check): the commands below, registered
  for the one guild if `guild_id` is set (instant) or globally (up to an hour to show).
- **`bot_config.json`** (untracked, and there is no example file: this list is where the keys are written down; the file is JSON, paths with forward slashes): `token`, `owner_id`, `channel_id`, optionally `guild_id`, `stale_status_minutes` `launch_macro` (default true: when the bot starts it starts the macro's window, without its loop, if no macro is running - `launch_macro_at_start()`; nothing when the processes cannot be listed or one is already running, as after the restart of `/update`; `/start` or `/loop` starts the loop; false leaves the macro to be started by hand).
  (how long `status.json` may go without an update before `/status` says it is not updating), `log_lines_max` (what `/log` is capped at), `onedrive_dir` (the folder inside the synced OneDrive folder that `/pull` copies into; leave it out and `/pull` says there is nowhere to copy to), `heartbeat_hours` (0: no heartbeat), `repo_dir` and `python` (what `/start`, `/loop`, the automatic restart and `/update` launch `main.py` with; the bot's own folder and Python when left out), `stop_timeout_seconds`, `auto_restart` (default off), `auto_restart_max_per_hour`, `update_remote` and `update_branch` (what `/update` pulls: `origin` and `main`).
- **Who is obeyed:** only `owner_id`, only in `channel_id`; anyone else's commands get no answer at all (`OwnerOnlyTree.interaction_check()`). The commands are a
  fixed list whose arguments are a number (`/log`), a choice from a fixed list (`/need`, `/option`, `/test`, `/pull`'s folder), a yes (`/reset_config`), or, for `/server_link`, a link typed into a box (not shown in the channel) that only `parse_server_link()` in the macro takes (anything else is dropped, and the link is not said back) or, for `/pull`, an `item` that is only used if it is exactly the name of something in that folder - nothing is run, no path is read from a message. The bot is created private ("Public Bot" off) and the token
  stays out of the repository (`.gitignore`).

| Command | What it posts |
| --- | --- |
| `/status` | `status.json` as text - whether it is still being updated (flagged when not), whether `main.py` is running (psutil if installed, else PowerShell or `pgrep`), the run, resolved and failed per need, disabled needs, the last resolved need and failure, rejoins, CPU and memory |
| `/screenshot` | the Roblox window (the whole monitor if there is none) as a PNG, with a note when it looks blank - a locked or sleeping PC |
| `/rview [seconds] [until_done]` | a rolling view: the Roblox window as a JPEG (`grab_roblox_jpeg()`, `BOT_RVIEW_WIDTH` wide, `BOT_RVIEW_JPEG_QUALITY`) every `BOT_RVIEW_INTERVAL` seconds for `BOT_RVIEW_SECONDS` seconds (or the `seconds` asked for, at most `BOT_RVIEW_MAX_SECONDS`; with `until_done`, until the macro's window is no longer busy - `busy` in status.json, which `_set_running_ui()` writes whenever the loop or an action starts or ends, read by `bot_lib.macro_busy()` - at most `BOT_RVIEW_MAX_SECONDS` or the `seconds` given, and an idle macro ends it at once only after it was seen busy or `BOT_RVIEW_START_GRACE` seconds have passed: `bot_lib.rview_finished()`), each replacing the last in one message (the message is edited, so the channel is not flooded); one at a time, and a failure stops it with a message |
| `/log [lines]` | the last lines of `run_log.txt`, as a file when they don't fit a message |
| `/report [run]` | the macro draws the chart of the run (the newest in its log unless one is named; the `report` command, answered when it is drawn, which does not need the loop to stop) and the bot sends it |
| `/start` | starts the macro with its window up and no loop going (`python main.py`), unless it is running; waits up to `BOT_START_CONFIRM` seconds for the process |
| `/loop` | starts the macro's loop: launches `python main.py --loop` if no `main.py` process is running (in a process group of its own, so Ctrl+C in the bot's console does not reach it; it shares that console on purpose, so what it prints can be watched there, and closing the console closes it); if one is and its status says the loop is going, says so and asks nothing; if the status says nothing runs (a window with no loop), leaves it the `start` command; then waits up to `BOT_START_CONFIRM` seconds for a `status.json` that is new and does not say that nothing runs (`loop_started()`) and says whether the loop showed |
| `/stop` | asks the macro to stop its loop (the `stop` command, the [STOP] button); its window stays open |
| `/exit` | asks the macro to exit (the `exit` command) and waits up to `stop_timeout_seconds` for its process to end; if it hasn't, kills it and releases the keys and mouse buttons from the bot |
| `/rejoin` | the macro leaves the game and rejoins it (its loop is stopped first; `/loop` again afterwards); the channel is told when it ends |
| `/setup` | the macro runs Setup; a loop that was running is stopped first and starts again once Setup is done (`RESUME_LOOP_AFTER`); the channel is told when it ends |
| `/blank` | blanks the screen with the black window or shows it again (`on` True or False; left out, it switches), whatever the hotkey's checkbox says; the macro is started if it is not running; the macro answers in the `.done` file of the command (`blanked`, `shown` or `not blanked`, `Blackout.show()` having refused: see the log), which the bot waits for up to `BOT_BLANK_WAIT` and says in words |
| `/sloop` | the macro leaves the Trading Hub if the player is in it (`leave_hub_if_in()`), runs Setup and then its loop (`leave_hub_and_setup()`; a macro that is not running is started first; a loop that runs is stopped first; a hub that cannot be left ends it as an error, with no Setup and no loop); the bot takes it that the loop is wanted, as for `/loop` |
| `/trade_hub` | the macro travels to the Trading Hub (`travel_to_trade_hub()`: the Trading Hub Phone from the backpack's Toys, used, TRAVEL TO HUB, Yes; the loop is stopped first). The clicks are the positions of a human's run (`TRADE_HUB_*`) |
| `/capture` | starts the input capture (what the capture hotkey does: screenshots and input into `CAPTURES_DIR`, zipped when it ends) on the PC, or ends the one that runs - it is a toggle, so a second `/capture` ends it - while another command goes on, to see what it does; the macro answers `started`, `stopped` or `failed: why` (`capture` command, `capture_command()`; `capture_text()` says it) and nothing else is stopped or queued; `/pull captures` takes what it made. The command may carry a `value` (the bot's debug option): true only starts one (`running` if one runs already), false only ends one (`none running` if there is none). **The `debug` option** (Discord has no `-d` flags: it is a True / False option of the command) of `/rejoin`, `/setup`, `/sloop`, `/trade_hub`, `/leave_trade_hub`, `/test`, `/inventory`, `/trades` and `/decline` starts a capture and a rolling view (a message of its own, edited once a second, at most `BOT_RVIEW_MAX_SECONDS`) before the command goes to the macro and ends both when the command's action ends (`DebugSession`: the end is the `.done` the macro writes, `announce_when_done()`'s `on_end`, or the end of the tour or decline the bot waits for), saying how the capture ended; `/sloop` ends with its Setup, as its `.done` does, not with the loop that then goes on |
| `/trades` | the macro opens the Trading Hub Phone and photographs the offers of the two trade listings (`trades_tour()`); the bot sends the pictures, the offers numbered, with what each listing gave (`collect_trades()`, `bot_lib.trades_text()`); stops the loop first |
| `/decline <listing> <offer>` | with `*` as the offer the macro declines every offer of the listing (`decline_all()`: the first offer in sight is declined, `decline_row()`, and the list waited for to have the next one at its top, `wait_list_settled()`, until none is left; at most `TRADES_MAX_OFFERS`; the answer says how many); with a number the macro declines that offer (`decline_offer()`: `decline` command, queued as `decline:<listing>:<offer>`), after looking for it in the list by its saved picture; the bot says how it ended and sends the offer as it was (`decline_offer()` of the bot, `bot_lib.decline_text()`); `listing` is 1 or 2 and `offer` 1 to `TRADES_MAX_OFFERS` |
| `/inventory` | the macro expands the backpack (its header's expand button) on its pets tab, scrolls down it and the bot sends the pictures it took, ten to a message, in order (`inventory_tour()`; the loop is stopped first and stays stopped - `/loop` again afterwards). The bot clears the earlier pictures, leaves the command, and waits up to `BOT_INVENTORY_WAIT` seconds for the macro's end marker (`collect_inventory()`), then says how many pictures and why the tour ended |
| `/recenter` | the macro closes what is open - the trade phone's screens (`close_trade_phone()`; the board of offers; an "Are you sure?" is cancelled, never answered) and the stray windows (`dismiss_stray_windows()`, up to `RECENTER_CLOSE_TRIES`) - respawns the character (`respawn_character()`) and closes what is left (`recenter()`); the loop is stopped first |
| `/leave_trade_hub` | the macro leaves the Trading Hub for a regular server (`leave_trade_hub()`: GO HOME, REGULAR SERVER); its loop is stopped, as for `/trade_hub` |
| `/refresh_debug` | the macro empties its debug folder (the game config stays), as the Refresh debug button does without asking; refused while its loop runs (the status says `stopped` when it does not: `loop_idle()`) |
| `/update` | pulls `update_remote`/`update_branch` (main) and restarts the macro - and the bot, if its own code changed - see [Update and rollback](#update-and-rollback) |
| `/restart` | stops the macro as `/exit` does and starts it again as it was (with its loop only if the loop was going), then restarts the bot itself, as `/update` does but with no change of code; one at a time with `/update` and `/rollback`; a macro that was not running is not started |
| `/rollback` | goes back to the commit the PC was on before the last `/update`, and restarts the macro the same way |
| `/need <need> <on>` | switches a need on or off, as the Debug tab's checkbox does (the command goes through a command file; a macro that is not running is started first - see below); says what the game config shows a moment later |
| `/option <option> <on>` | the other switches (every checkbox of the Main and Settings tabs and the Debug tab's Options subtab): `setup_next_loop`, `money_tree`, `lure`, `resume_on_focus_loss`, `use_private_server`, `record_next_run`, `blackout_hotkey`, `new_need_hotkey`, `level_growing_pet`, `pet_pen`, `keep_moments` and `show_debug_lines` (the output's debug lines, which is only a checkbox and so is not confirmed) |
| `/test <handler>` | what the Debug tab's Test button of a need does (`test_need_handler()`), or `teleports` for its "Test every teleport handler" (`TEST_ALL_TELEPORTS`); the macro's loop is stopped first and not started again, and the channel is told how it ended (`test` command, queued as `test:<handler>` in `OUTSIDE_ACTIONS`) |
| `/reset_config <sure>` | Reset Config (`reset_game_config()`: totals, timers and switches back to their defaults, the private server link kept), only with `sure` true and not while the macro runs anything (`reset_config` command: `.done` says `given up` if it did) |
| `/server_link` | opens a box (`LinkBox`, a Discord modal) for the private server link, so that the link is not a message or an argument shown in the channel; it is saved as the box in the Settings tab does; the macro drops a link `parse_server_link()` does not know (only 32 ASCII letters and digits are taken as the code) and nothing says the link back (`server_link` command) |
| `/options` | which options and needs are on, read from the game config (`debug/.config`, with the defaults for what is missing) |
| `/pull <folder> [item]` | copies something out of the debug folder into OneDrive - see below; nothing is copied to OneDrive unless this is used |

**What the bot posts by itself.** `watch()` looks at the macro's files every `BOT_WATCH_INTERVAL` seconds (`watch_once()`; a look that fails is logged and the watching goes on) and keeps what it has dealt with in `bot_state.json` (`BotState`: where it got to in `run_log.txt`, whether the status is stale, the last heartbeat), so a restart neither posts things twice nor misses what came while it was down. On its first start, what exists already is taken as dealt with, so old failures, charts and captures are not posted.

- **A failure:** a new `FAILURE:` line in `run_log.txt` (`parse_failure()`) is posted with its reason and its screenshot (`failure_screenshot_path()`: only a file inside `debug/`, not whatever path a line names, and none on another drive; not posted when bigger than `BOT_MAX_UPLOAD_MB`). At most one post per `BOT_ALERT_GAP` seconds (`RateLimiter`): the next post says how many more there were, and when no later failure comes, a post of its own says it once the gap has passed (`flush()`). A log that is shorter than the offset was deleted or started again (**Refresh debug**), and is read from its start (`read_new_lines()`; a line still being written waits for the next look).
- **The macro stops updating:** once, when `status.json` has not been updated for `stale_status_minutes` (the macro is stuck, or the PC asleep or locked; not when it was stopped on purpose - [STOP], the window's X, `/stop`, `/exit` - or is open with no loop going, which `mark_stopped()` writes into the status as `stopped`, and a new run clears), and once when it updates again (`stale_alert()`).
- **A heartbeat:** the `/status` text every `heartbeat_hours`, so silence means trouble; off by default.

**What leaves the PC, and what is kept from leaving.** Only the owner in the one channel is obeyed, and the bot's messages ping nobody (`AllowedMentions.none()`). What the bot posts or copies: the macro's status and log lines; a failure's `screen.png` (`failure_screenshot_path()`: only a `.png` inside `debug/failures/`, never the config, and not a link); `/screenshot` (the Roblox window); `/pull` (whole folders of `debug/` into OneDrive, which is a cloud). Pictures are of the game window only (`grab_roblox_native()` for a failure's: none when there is no window, never the rest of the monitor), but the game shows other players' names and chat, and the captures and moments (`debug/captures/`, `debug/moments/`) hold frames of it. The private server link is a key to the server: it is in `debug/.config` only (`PULL_SOURCES` has no entry that holds it; the moments' `config.json` leaves it out), is never logged, is entered in a box rather than a command argument, and `parse_server_link()` takes only 32 ASCII letters and digits of it into the `roblox://` address that is opened. `bot_config.json` (the token) and `bot_state.json` are git-ignored and the token is never printed or logged.

**Pulling things into OneDrive (`/pull`).** Nothing is copied to OneDrive by itself (it was, once: a finished capture's zips; that was taken out). `/pull` takes a `folder` from the fixed list `PULL_SOURCES` - `failures`, `logs`, `runs`, `captures`, `recordings`, `moments` (the runs' moments, see [The moments of a run](#the-moments-of-a-run)), `screenshots` (the pictures of the screenshot key); the game config (`debug/.config`, which holds the private server link) is not in it - and optionally an `item`: left out, the newest entry of the folder; `*`, the whole folder; or an entry picked from the autocomplete (newest first). An item is only accepted if it is exactly the name of an entry in that folder (`resolve_pull()`: `..`, a slash or a drive letter get nowhere). It copies the files into `onedrive_dir/<folder>/` (`pull_files()`, `copy_pull()`) and says how many files and megabytes; the OneDrive app on the PC uploads them. A capture is copied as its zips alone (the first holds `info.json` and `events.jsonl`) - not its frames, which are the same again as the zips; with `*`, the zips of every capture in the folder. A pull of more than `BOT_MAX_PULL_MB` copies nothing. One of more than `BOT_PULL_CONFIRM_MB` first says its size (in GB from 1024 MB) and file count with a **Copy it** and a **Cancel** button (`ConfirmView`, which only the owner in the channel can press); without a yes within `BOT_PULL_CONFIRM_TIMEOUT` seconds nothing is copied. The size is in the answer as well. An existing copy of the same file is replaced. A copy that fails half-way removes the files it made (`copy_pull()`), and the paths are given in the form that has no `MAX_PATH` limit on Windows (`long_path()`). The buttons come off the message when nobody pressed one in time (`ConfirmView.on_timeout()`). The autocomplete's listing skips an entry that vanished.

### Remote control

The commands that act on the macro (`/start`, `/loop`, `/stop`, `/exit`, `/rejoin`, `/setup`, `/refresh_debug`, `/trade_hub`, `/leave_trade_hub`, `/inventory`, `/recenter`, `/sloop`, `/blank` (`blackout`), `/trades`, `/decline`, the bot's own `focus` after an update, and `/need`, `/option`, `/test`, `/reset_config` and `/server_link`) need two small things in `main.py`, and no more of it:

- **`python main.py --loop`** starts the loop once the window is up (`start_remote_control(loop=True)`, `START_LOOP_DELAY_MS` later), as if the loop button was pressed.
- **Command files.** `poll_commands()` (a `root.after` timer, every `COMMAND_POLL_MS`, so on Tk's thread) reads the oldest file in `COMMANDS_DIR` (`debug/commands/<time>-<id>.json`, `{"command": ..., "time": ...}`; `read_command_with_id()`), one per poll, which the bot wrote (`bot_lib.write_command()`, a file of its own, all at once - two commands sent close together are both kept and done in order). A file is removed whenever it is read, and the macro answers in `<time>-<id>.ack`: `taken`, or `dropped: <why>` (`acknowledge_command()`). The bot waits up to `BOT_COMMAND_ACK_WAIT` for that answer (`send_command()`) before it says it asked: a dropped command is told with its reason, and one the macro did not look at in time (its window stuck) is withdrawn (`withdraw_command()`), so that it cannot run later, and said so. Answers nobody read are removed after `BOT_ACK_KEEP`. An action that is queued (below) is acknowledged when the command is taken, not when the action ends; one that is given up later is only in the log. A command that is not one of `COMMANDS`, or is older than `COMMAND_MAX_AGE` seconds, is dropped - one left from before the macro started cannot close it. `start` starts the loop (nothing if it runs), `stop` is the [STOP] button, **`exit`** is what the window's X button does (`close()`): ask for the stop (which already lets go of everything), close the capture, write the status, wait up to `CLOSE_WORKER_WAIT` for the running work to end, release every key and the mouse, take the blackout away and close the window, which ends the process, and **`set`** changes a switch (`set_switch()`): the command carries `kind` (`need` or `option`), `name` and a true or false `value`, and is only read when the switch exists (`NEED_NAMES`; `OPTION_SWITCHES` and `SHOW_DEBUG_LINES_OPTION`) - the switch is persisted as its checkbox does it, and the checkbox in the window follows. **`rejoin`** is the Leave & rejoin button, **`setup`** runs Setup at once (the window's box runs it at the next loop instead), and **`trade_hub`** and **`leave_trade_hub`** are the Trading Hub trips, which have no button in the window any more (`act_from_outside()`, `OUTSIDE_ACTIONS`) are **queued** and done one after the other in the order asked for (`drain_pending()`, looking every `REJOIN_STOP_POLL_MS`): a running loop is stopped first (once) and waited for up to `REJOIN_STOP_WAIT_MS`, after which the action is given up and the next one tried; another action that runs is waited for as long as it takes; a `stop` command stops what runs and leaves the queue alone. The loop is not started again afterwards, `/loop` does that. **`refresh_debug`** is the Refresh debug button (`refresh_debug_from_outside()`), done only when nothing is running, and the status file is written again after it. The next poll is scheduled before a command is done, and a command that raises is logged and does not stop the polling. Commands left from before are all dropped, and answered so, when the macro starts (`discard_commands()`). The list is fixed; the bot takes no argument that is run or opened. **The end of an action:** an action that was queued (`rejoin`, `setup`, the Trading Hub trips, `sloop`) writes `<time>-<id>.done` when it ends (`finish_command()`, from `_finish_running()`): the status the buttons show in lower case (`done`, `stopped`, `stopped: roblox not focused`, `error`), or `given up: the running loop did not stop`. The bot waits for that in the background (`announce_when_done()`, up to `BOT_ACTION_WAIT`, looking every `BOT_ACTION_POLL`) and says in the channel how it ended (`action_end_text()`), so an action's end is not left to be guessed. **The loop after Setup:** a loop that was running and was stopped for a `setup` goes on once Setup has ended as `done` and nothing else is queued (`resume_loop`; the actions that do this are `RESUME_LOOP_AFTER`); a Setup that was stopped, failed or given up does not bring it back, and nor do the other actions (a rejoin or a trip leaves it to `/loop`). `sloop` is a `setup` with that set whether or not a loop was running.

`/exit` uses `exit` and not `stop`, so that the process ending is the proof that the loop ended and the inputs were released; only if the process outlasts `stop_timeout_seconds` does the bot kill it (`kill_macro()`: `taskkill /F /T`, which takes its child processes too, `SIGKILL` elsewhere) and release the keys itself (`release_inputs()`: the keys of `cfg.HELD_KEYS`, and a mouse button only if it is down). The macro's process is found by its command line (`main.py`, not the bot; `macro_pids()`).

**Commands that need the macro start it.** `/rejoin`, `/setup`, `/sloop`, `/trade_hub`, `/leave_trade_hub`, `/refresh_debug`, `/inventory`, `/recenter`, `/trades`, `/decline`, `/capture`, `/blank`, `/need`, `/option`, `/test`, `/reset_config` and `/server_link` do not answer that the macro is not running: `ensure_macro()` starts it with its window and no loop (`start_window()`: `open_macro()`, then up to `BOT_START_CONFIRM` seconds for the window to write its status, which shows that it reads commands; the crash check does not look meanwhile, `launching()`), says "Started the macro first." and goes on, with the bot taking it that the window is expected (`desired_state` `open`; `/sloop` then `running`). `/stop` and `/exit` still say that nothing runs: starting a macro to stop it is no use. `/loop` and `/start` always launched it.

**Slow commands and the process list.** An interaction can be followed up for 15 minutes only; a longer `/update` or `/pull` answers in the channel itself (`say()`, `followup_needs_the_channel()`: when the interaction has expired, or Discord says the token is refused or unknown). The macro's process is found by its command line, `main.py` as a whole file name (`MAIN_PY`: not `test_main.py` or `main.pyc`), not the bot's. A listing of the processes that fails is not "the macro is not running": it is tried `BOT_PROCESS_LIST_TRIES` times, a second apart, and then said in the channel (`running_pids()`, `bot_lib.Said`) with nothing stopped, started, updated or killed on a guess; a stop whose final listing fails says it cannot tell whether the macro ended.

**Posting.** One post that fails does not end a look at the macro's files (`attempt()`): the rest of the look goes on. A failure's picture that is gone or refused is left out and the text says so (`post()`); a chart, the stale-status alert and the heartbeat are only recorded as done once they were sent, so they are tried again at the next look. A failure line whose text cannot be sent at all is lost (logged).

**Startup.** The bot's channel is fetched by `find_channel()`: from the cache, else with `BOT_CHANNEL_TRIES` tries and a wait that starts at `BOT_CHANNEL_RETRY_WAIT` and doubles; a `NotFound` or `Forbidden` (wrong `channel_id`, missing permission) is logged once with that hint and not retried, and the watching is then off while the commands still work. The watcher task is kept and a done-callback logs it if it ends by an exception. The slash commands are registered once, on the first `on_ready` (it comes again after every reconnect); if that fails the error is logged and posted in the channel, since no command shows up otherwise.

**Long messages.** Discord refuses a message over its limit, and with it the whole command, so every text the bot sends goes through `bot_lib.clip_message()` (`say()` for a command's answer): whole lines from the start, then "... (N more lines)", a code block kept closed; the lines of the update plan's git log are cut to `MAX_LOG_LINE`. A command that raises is logged and its kind of error said in the channel (`OwnerOnlyTree.on_error()`, `bot_lib.error_text()`), where it would otherwise stay on "thinking..." for ever; a `/screenshot` too big as a PNG is sent as a JPEG. A failed `/update` or `/rollback` is logged, says so in the channel where it still can, and lets go of the lock.

**What the bot keeps in mind.** `/loop` sets `desired_state` in `bot_state.json` to `running`, `/start` and `/stop` to `open` (the window, no loop), `/exit` to `stopped`; a restart by the bot (after a crash or an update) starts the loop only for `running`. While `/update` or `/rollback` has the macro stopped (`UPDATING`, `crash_check_due()`), or `/start` or `/loop` is still starting it (`LAUNCHING`), the process is not looked for, so the stop is neither reported nor, with `auto_restart` on, undone halfway, and a process that is not there yet is no crash. A macro whose process is gone while it should be running is a crash, posted once (`crash_check()`) and again when it is back; a macro never started with `/start` or `/loop` (started by hand, say) is never reported as crashed, and one closed with `/exit` is not. With `auto_restart` on, the bot restarts it by itself, at most `auto_restart_max_per_hour` times an hour (`restart_allowed()`; a restart is counted once the macro has been launched, so a launch that fails - said in the channel - does not use one up); off by default, since a macro that stopped because Roblox lost focus usually means the PC is in use. The watcher keeps `state.charts` to the charts that are there, and says once when the run log cannot be read (`log_unreadable`), again when it can. A macro that is running but not writing `status.json` is the stale-status alert, not a crash. Whatever the macro prints on its error output goes to `debug/logs/macro_stderr.log`.

### Update and rollback

`/update` takes whatever has been merged into `main` and runs it, from Discord, without a trip to the PC. It takes no argument: the remote and branch are the config's `update_remote` and `update_branch` (`origin` and `main`), and no other branch can be named in a message.

1. **The plan, before anything is changed** (`make_plan()` over a `GitRunner`, so the tests run it against real repositories made in a temporary folder): `git fetch` of that branch (the only thing it changes; the target is `FETCH_HEAD`, what was just fetched, whatever the remote's fetch refspec makes of the branch's name), then `update_plan()` decides from the commits. Same commit: "already up to date". **Local changes to tracked files** (`git status --porcelain --untracked-files=no`; untracked files and the git-ignored `debug/` don't count): refused, naming them - nothing is ever discarded. The PC **ahead** of the branch, or the histories **diverged**: refused. Only a fast-forward goes ahead, and the plan lists the commits it brings (`git log --oneline`) and how many lines changed in how many Python files (`python_changes_text()`, from `git diff --numstat`). `/update` then waits for the owner to press **Update** (`BOT_UPDATE_CONFIRM_TIMEOUT`; no answer or Cancel changes nothing); `/rollback` is the way back and does not ask.
2. **`apply_commit()`:** the macro is closed as `/exit` does it (cooperatively, killed only if it will not end), `git merge --ff-only` moves the PC to the fetched commit, and the files that changed are listed (`git diff --name-only`). The commit the PC was on is kept in `bot_state.json` (`previous_commit`) as soon as the move has been made - not before, so a move that fails keeps the rollback target there was - and a failure after it (the packages, the start) can still be rolled back.
3. If `requirements.txt` changed, `pip install -r requirements.txt` runs with the configured Python (`BOT_PIP_TIMEOUT`; the file is UTF-16, which pip reads); if it fails the macro is **not** started and `/rollback` is offered.
4. The macro is started again (as `/loop` does, waiting up to `BOT_START_CONFIRM` seconds for its status) **if it was running**, with its loop only if the loop was going (a window opened by `/start` or stopped by `/stop` comes back without one); one that was stopped stays stopped. If the loop does not show, the end of `macro_stderr.log` is posted with the `/rollback` offer.
5. **The bot restarts itself** when its own code changed - one of its own files in `scripts/` (`BOT_FILES`), `magic_numbers.py` (which it reads) or `requirements.txt` (`needs_bot_restart()`) - because Python does not reload a running program: first `import bot.discord_bot` is tried in the new code (`BOT_IMPORT_TIMEOUT`), and if that fails the bot stays on the old code and offers `/rollback`; otherwise it posts that, closes the Discord connection, and `main()` starts the same command again (`os.execv`; on Windows a new console window, since `execv` there leaves the terminal confused). The macro-side files alone (`main.py`, the references) need no bot restart.

After the macro has been started again and its window has written its status (`wait_for_window()`: a command written before that is dropped, which was what an update's `focus` met), `/update` and `/rollback` leave it the `focus` command, which brings the Roblox window to the front (`focus_roblox()`, no click; it does not stop a running loop), since the macro's own window comes up on top; a macro that did not look started, or that was not running, is not asked. `/update` and `/rollback` end with "**Update finished.**" or "**Rollback finished.**" when every step was done (`apply_commit()` is True), and the bot that starts again after them (or after `/restart`) says it is online (`online_text()`: the commit it runs and whether the macro is running its loop, open with no loop or not running) once its commands are registered, so the end of a restart is seen too. `/rollback` goes back to `previous_commit` with `git reset --hard`, with the same refusals (local changes to tracked files; no earlier commit; a commit that is no longer in this copy), then steps 2 to 5. It undoes one `/update`; a later `/update` pulls `main` again. The tests run both against real throwaway repositories: a clean update, an untracked file, a changed tracked file, ahead, diverged, a bad branch, and the way back.

`/restart` is the same without the git: the macro is stopped as `/exit` does it and started again as it was (`start_again()`: with its loop only if the loop was going, else its window alone), a macro that was not running is not started, and the bot then restarts itself (`restart_all()`, `RESTART_BOT`), the new bot finding the macro running. It takes the same lock as `/update` and `/rollback` and pauses the crash check the same way (`UPDATING`).

Nothing starts by itself with the PC: the bot is only ever started by hand, and the only time it starts itself is this restart after an update.

## Automated checks

`.github/workflows/check.yml` runs on every push and pull request (GitHub
Actions, a temporary Linux machine): `pyflakes` on `main.py`,
`magic_numbers.py`, `tools`, `bot` and `tests` (syntax errors,
undefined names, unused imports and local variables, duplicate definitions), then
`python tools/check_constants.py`, which fails on a `cfg.NAME` in `main.py` or `scripts/`
that `magic_numbers.py` doesn't define, a constant nothing uses, a function of
`main.py` or `scripts/` that nothing but tests calls, an UPPER_CASE name the docs write in backticks that no code file has (`docs/review.md` is left out: it records what was found), and a test that asks for `tmp_path`,
`monkeypatch`, `caplog` or `capsys` and never uses it; then `mypy main.py scripts/bot_lib.py` (configured in
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
`pip install pytest-xdist` to do the same). The checks that run on fake time over real screenshots take most of the time, and are
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
- `test_discord_glue.py` - the bot's `watch_once()` run against a fake channel and the macro's files in a temporary folder (skipped when discord.py is not installed; CI installs it): the unreadable run log, forgotten charts, what an automatic restart starts (and that a launch that fails is not counted), a macro being started is no crash, held failures, the buttons at a timeout, `/loop` against a loop that is going, the launch flags and the rollback target. The command handlers inside `build()` need a Discord connection and are not run; their decisions are in `bot_lib` and `test_bot.py`.
- `test_bot.py` - the Discord bot's logic (`scripts/bot_lib.py`): the config, who is obeyed, the messages made from the files `write_status()` writes, and the alert, `/pull` and control logic (failure lines, log offsets, rate limiting, stale status, what can be pulled and how, the command file, crash and restart decisions, update and rollback against real git repositories);
- `test_transitions.py` - the white-screen waits, the teleport's checks (the Yes that took nothing, the house still on screen, the walk after both loads) and, on the frames of `tests/teleport/` and `tests/needs_end/` (where each teleport need ends, from a 10 hour run), what each moment of a teleport looks like and that no detector fires where a need ends (`detect_white_screen()`, `loading_white_screen()`, `in_house()`, the notice checker, the Task Board's sign).
- `test_state.py` - the output log's levels, rotation and reopening, the console handler, stuck-need disabling, `process_needs()` (resolved, unresolvable and not-cleared cases), the sick handler on real screenshots (`tests/sick/`: the prompt, the dialog, the pet view), `RunStats`, `status.json`, the
  failure folders, the quiet and idle-respawn checks (including that a respawn
  leaves the no-progress rejoin clock alone), stopping, and the open `output.log`;
- `test_detection.py` - the need-strip capture and its cache, and the
  popups and stray windows against the real crops in `ref/` (including that every `POPUP_TEMPLATES` entry has a crop that fits its box);
- `test_icon_screenshots.py` - every real screenshot in `tests/icon_detection/<folder>/` (named `<scene>__<needs>.png`, the needs being what the test expects) (the need bar over dark, bright, busy and white scenes) run through the circle search and the matching; each must be named for the needs on its bar, so a screenshot added without a label fails;
- `test_loop.py` - the whole workflow loop run for a moment with the game stubbed out (idle respawns, the resource monitor starting and stopping, the report being written);
- `test_capture.py` - the input capture against a made-up keyboard, mouse and screen (events, both coordinate systems, click frames, skipping unchanged frames, the summary, the zips, stopping by itself, the hotkey toggle and pruning, the frame budget, its thinning, the zip);
- `test_need_handlers.py` - the catch, pet and ride needs as the game sees them (the order of keys, clicks and waits, with everything that touches the game recorded), that every constant and key their step tables name exists, and the inventory tour (its pictures, the end of the list, the limit, a backpack that does not open, a stop; the marker every time);
- `test_moments.py` - the capture of a run's moments (what it keeps, marks and writes, the zips' text files) and the `report` command;
- `test_setup.py` - Setup's steps against fakes of the backpack and the real crops of its screens;
- `test_trades.py` - the trade phone, the offer lists, `/trades` and `/decline`, and recentering;
- `test_pet_pen.py` - the Task Board's badge, the Pet Pen card and the NICE! button on real frames, and collecting the pen;
- `test_features.py` - the daily money reset, the run-every-need sequence, and the like;
- `test_config.py` - the real need tables in `magic_numbers.py`, without pinning any value: every entry is well formed (known keys, on-screen positions, movement keys, a `final_click` is on screen), has a handler, and the handler carries out exactly what the entry says (run over every need in the tables). The handler tests elsewhere use made-up entries; this is what keeps the real ones honest;
- `test_report.py` - the run-report parser against the exact log wording the
  macro writes (a round trip through `log_run_event()`), and the chart itself.
- `tests/halloween2026/` - the archived event's code (`scripts/Halloween2026.py`): its readers and players against crops of real runs, on a fake clock, keyboard and mouse, with its own fixtures in `event_fixtures.py`;
- `test_tools.py` - `tools/check_constants.py` passes on the repo and finds what it is for.

Fixtures are synthetic images or the lossless crops in `ref/`; compressed
screenshots (`.webp`) don't match exact colours, so they aren't used.

**Screenshots are named for what they show**, not by the number the screenshot tool or a capture gave them, and where a test reads something off a
picture the name says it, so a new file is checked against its own label: `tests/icon_detection/<folder>/<scene>__<needs>.png` (the needs on the bar,
left to right, joined by hyphens); the event's pictures are named the same way, see [Halloween 2026](HALLOWEEN_2026.md).

**Tests don't pin tuned values.** `magic_numbers.py` is edited constantly (timings,
routes, intervals), so a test must pass for any sensible
tuning: it states what it is about itself (the daily-reset tests set
`MONEY_RESET_HOUR` to 17; the teleport-walk and sick tests give a made-up need
an entry of its own, so they don't depend on the real table) or reads the constant (`cfg.QUIET_LOG_INTERVAL`,
not "5 minutes"). Waits are tested on a fake clock (`fake_clock`), not by timing
them, and the loop test runs until it has seen what it checks instead of for a
fixed time, so a slow machine only makes it slower.
`python tools/run_summary.py debug/run_log.txt` (or a moments folder or zip, `--run N` for one run of a log with several) prints a run as text: the needs resolved with their mean times, the failures by kind (the numbers of a kind folded together), the rejoins, notices, setup problems, teleport checks, needs disabled and the three longest stretches without a need resolved; the first look at a run sent for analysis. (`tests/test_tools.py` runs it on a small log.)

`python tools/retune_check.py` runs the suite against changed copies of the repo
and names any test that breaks - run it when a change could depend on tuned
values, not on every commit (about 30 runs, about 15 minutes with the 4 workers each uses; `python tools/retune_check.py timings_x20` runs just that one).
The variants: every timing and walk step scaled by 0.05, 0.5, 1.7 and 20; other
intervals, hours and counts changed; counts at their lowest and highest; the
event flag off; needs removed from or added to the table; button names reordered;
helicopter for every need; thresholds nudged up and down, colour tolerances
widened, search boxes and click positions moved; other key bindings; behaviour
two other time zones; a
different hash seed; run from another working directory; a `debug/` folder with a
real config and logs in it; extra files in `ref/`; the tests in reverse and in
shuffled order; and each test file run on its own. The one test file that is *meant* to depend
on recognition settings, `test_icon_screenshots.py`, is skipped by the
variant that changes those settings on purpose (`ICON_SHIFT_TOLERANCE` to 0).
