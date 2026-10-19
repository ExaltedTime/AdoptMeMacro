# Review

A review of the code, the docs and the tests, with recommendations and ideas collected from earlier work on this project. Nothing in it has been
fixed yet; it is a list to pick from. Each finding was read in the code by the review that found it, and a few were checked again by hand (the
unused `MINIGAME_VICTORY_CHECK_INTERVAL`, the blank line in the Discord command table, the unguarded `poll_commands`, the two `STOP_EVENT.clear()`
calls, the key list of `release_all_inputs()`). Findings marked **(uncertain)** depend on library behaviour or timing that could not be run.
Line numbers are those at the time of the review and drift.

The review ran the suite (562 tests), `tools/check_constants.py` and pyflakes; all passed. Nothing was run against the real game, Discord or Windows.

`docs/planned/Discord.md` has been removed (the bot is built and described in `ARCHITECTURE.md`); findings below that mention it are about the file as it was.

**Done since (docs, tools and tests pass):** all of the [Docs](#docs) findings except the ones noted as left (the macro-side comment fixes in
`magic_numbers.py` were made with them), and everything under [Tests and tools](#tests-and-tools) except the test seam for the Discord glue (an idea, not a defect). The unused `MINIGAME_VICTORY_CHECK_INTERVAL` and `minigame_won()` were removed, and `tools/check_constants.py` now also
checks `bot/`, reports unused constants and uncalled functions, names unknown UPPER_CASE names in the docs, and flags unused `tmp_path`, `monkeypatch`, `caplog`
and `capsys` test parameters (unused imports and local variables are pyflakes's, which now also covers `tools`). Also done (dead and duplicate code): M8 (`wait_for_minigame_start()` replaces the three start waits and ends on a disconnect), the unused `hearts` parameter of `choose_hauntlet_door()`, the unused `began` attribute and `config` parameter, the repeated command handlers of the Discord bot (`ask_macro()`), and the repeated colour-count code (`color_pixels()`, `color_fraction()`). Fixed from "Fix first": M1 (`poll_commands()` keeps polling), M3 (held keys are tracked in `HELD_KEYS`, so all are released; the fail-safe worry M4 is not addressed beyond each release being tried separately), M7 (the lost stop), B1 (every message is clipped to fit) and B2 (no crash alert during `/update`). Not changed, and still open: the macro findings
M1 to M14 and the bot findings B1 to B12 (code), and the docs' values that are measurements rather than constants.

Contents: [Fix first](#fix-first) · [Macro](#macro-mainpy) · [Discord bot](#discord-bot) · [Docs](#docs) · [Tests and tools](#tests-and-tools) ·
[Ideas and open questions](#ideas-and-open-questions)

## Fix first

The findings most likely to bite, in the order to take them.

1. **Remote control dies for good after one exception** (`poll_commands`). See [M1](#m1).
2. **`/update` can fail before doing anything** when the plan text is over Discord's 2000 characters, and the owner sees only "thinking...". See [B1](#b1).
3. **The watcher posts a false crash alert during `/update`, and with `auto_restart` can relaunch the macro in the middle of it.** See [B2](#b2).
4. **Held keys are not all released** by `release_all_inputs()` (the arrow keys of Stitches' Spotlight, space, the respawn keys). See [M3](#m3).
5. **A stop can be lost when a run starts** (a second `STOP_EVENT.clear()`). See [M7](#m7).
6. **A `/pull` or `/update` that takes more than 15 minutes cannot answer any more** (the interaction token expires). See [B3](#b3).
7. **Commands that raise leave Discord showing "thinking..." for ever** (no error answer). See [B4](#b4).
8. **`current_macro_pids()` treats a failed process listing as "not running"**, so `/stop` and `/update` can do the wrong thing. See [B5](#b5).

## Macro (`main.py`)

### High and medium

<a id="m1"></a>
**M1. An exception in `poll_commands()` ends remote control permanently.** The method re-arms itself with `self.root.after(...)` as its last line, outside any
`try`; only the `exit` branch returns early. Anything raised by `set_switch()`, `refresh_debug_from_outside()` or `start_loop_from_outside()` stops the polling.
Realistic causes: `save_game_config()` has an unguarded fallback `open(..., "w")` that raises `PermissionError` while `game_config.json` is locked, and
`refresh_debug()` calls `os.listdir()` unguarded. Scenario: the bot sends `set`, the file is locked, Tk prints a traceback, the process stays up and the status
looks healthy, and `/stop`, `/exit` and `/loop` never arrive again. Fix: re-arm first, or `try/except Exception` with the `after` call in a `finally`, logging
the exception.

<a id="m2"></a>
**M2. Overlapping outside actions abort each other, and `stop` does not cancel a pending one.** `act_from_outside()` starts an independent retry chain per call, and
all chains share `self.rejoin_waits`, which `poll_commands()` resets on every command. `/rejoin` then `/setup` while the loop runs leaves two chains; when the loop
ends, the first runs, and the second chain's next tick sees `is_running` and calls `stop()`, aborting the rejoin half-way. A later `stop` or `exit` does not remove a
chain, so the action still starts after the user said stop. Fix: one `self.pending_action` (None or a command name); a new action replaces it, `stop` clears it, one
`after` chain serves it.

<a id="m3"></a>
**M3. `release_all_inputs()` does not cover every key the macro holds.** Its list is `MOVE_KEYS`, `KEY_HELICOPTER`, `KEY_ZOOM_IN` and `KEY_ZOOM_OUT`. Missing:
`STITCHES_TURN_KEYS` (left and right arrows), the jump key, and the respawn keys. This net matters most in `close()`, where the daemon worker dies with the process:
an exit that arrives during a Stitches' Spotlight camera turn can leave the arrow key down in Roblox. Also `held = pressed_inputs()` is outside any `try`; if it raised,
the worker's `finally` would not reach `self.is_running = False`, the UI would stay disabled and every remote command would be refused as "Already running".
Fix: build the list from every `KEY_*` constant plus `STITCHES_TURN_KEYS` and `RESPAWN_KEYS`, and guard the mouse part.

<a id="m4"></a>
**M4. (uncertain) The PyAutoGUI/PyDirectInput fail-safe may leave keys held.** The fail-safe stays on (a user decision, documented as accepted). No code moves the
mouse to (0,0), and every camera drag starts at screen centre, so it only fires when the user flings the cursor to a corner. What could not be checked is the cleanup
path: if `pydirectinput.keyUp()`/`mouseUp()` also run the fail-safe check (believed true for PyDirectInput 1.0.4), then with the cursor at the corner every release in
`release_all_inputs()` raises `FailSafeException`, which the bare `except Exception: pass` swallows, and the held keys stay down. Options that keep the fail-safe on for
everything else: release with `ctypes.windll.user32.SendInput`/`keybd_event` directly in the safety net, or move the cursor off the corner with `SetCursorPos` first.
Also `hold_key()` and `respawn_character()` call `keyDown()` before their `try/finally`; moving `keyDown` inside the `try` (and having `respawn_character()` use
`hold_key()`) closes that gap.

<a id="m5"></a>
**M5. The emergency-stop hotkey thread can die silently.** `watch_stop_hotkey()` catches only `RuntimeError` and `tk.TclError`, and one thread serves both the STOP and
CAPTURE hotkeys. `hotkey_fires()` calls `is_roblox_focused()` for the capture hotkey, which uses pygetwindow and can raise on unusual windows. One such exception ends
the thread, and STOP is dead for the session with no log line. Fix: catch `Exception` per iteration, log it, carry on; return only when Tk is gone.

<a id="m6"></a>
**M6. `close()` stops the capture before it sets `STOP_EVENT`.** `capture.stop()` can join threads for several seconds, during which the worker keeps clicking and
holding keys in the live game; after the stop `close()` sleeps briefly and releases inputs without joining the worker. Fix: set `STOP_EVENT` first; keep the worker
`Thread` handle in `run_async()` and `join(timeout)` it before releasing inputs.

<a id="m7"></a>
**M7. A stop can be lost at the start of a run.** `run_workflow_loop()` calls `STOP_EVENT.clear()` on the worker thread, after `run_async()` already cleared it on the
Tk thread. A stop that lands between the two (a bot `/loop` followed at once by `/stop`) is cleared, and the loop runs until stopped again. Fix: delete the redundant
clear in `run_workflow_loop()`.

### Low and medium

- **M8. The three minigame players repeat the start wait, and none leaves it on a disconnect.** The `waited_until`/`minigame_wait` block appears in
  `play_hauntlet_2()`, `play_ghost_gallery()` and `play_stitches_spotlight()`; none checks `watch.problem`, so a disconnect in the lobby idles for the whole
  `MINIGAME_START_WAIT`. Extract `wait_for_minigame_start(watch, interval)` that returns on `watch.problem`.
- **M9. `MinigameWatch.update()` matches every overlay** although only `self.name` matters; `detect_minigame_overlay()` template-matches all of them on every look
  (the Ghost Gallery loop looks very often). Matching only the game's own crop is cheaper and rules out cross-matches.
- **M10. Watcher threads and the rect cache.** `_watch_need_gone()` catches only `StopRequested` and `FocusLost`; any other error (a cv2 error from
  `_need_cleared()`) kills the thread and the early-exit and the "still showing" failure check stop working. `roblox_rect()` tests `_ROBLOX_RECT` then indexes it;
  `focus_roblox()` can set it to None in between on the worker thread, giving a `TypeError` in the watcher, recorder or capture threads. Copy to a local first.
- **M11. `write_status()` uses a fixed `status.json.tmp` and no lock.** `close()` and `mark_stopped()` run on the Tk thread while the worker unwinds and also calls
  `write_status()`; two writers can interleave on the temp file and corrupt `status.json`, and a failed `os.replace` is swallowed. Add a lock or a unique temp name.
- **M12. `travel_to_trade_hub()` is not idempotent.** It presses the backpack key blindly; if the backpack is already open that closes it and the fixed clicks land
  in the world. It runs no `dismiss_stray_windows()` first, and neither trip checks that the hub loaded. Use `close_backpack_if_open()` first and confirm by a look at the
  screen afterwards. If the Trading Hub Phone is already equipped the sequence also does something unintended (the recorded run started from an unequipped phone).
- **M13. `read_command()` drops stale, unknown and malformed commands without a log line.** A bot host whose clock is off by more than `COMMAND_MAX_AGE` gets every
  command ignored with nothing in the log. Add a `logger.warning`. A command written between the read and the `os.remove` is also lost (tiny window).
- **M14. Smaller races.** The worker's `finally` sets `is_running = False` before queueing the UI reset on Tk; an `act_from_outside()` retry in between gets its UI reset
  to "Ready" with the buttons enabled. `refresh_debug_from_outside()` checks only `is_running`; a live `InputCapture` can still write into the folder being deleted.

### Dead code, duplication, size

- `MINIGAME_VICTORY_CHECK_INTERVAL` is unused (victory is checked on every look); its comment in `magic_numbers.py` and the docs still say it sets an interval.
  `tools/check_constants.py` only finds undefined names, not unused constants; adding that check would have caught it.
- `minigame_won()` is never called (only the docs mention it). The `hearts` parameter of `choose_hauntlet_door()` is unused ("for the decision logic to come").
- `HAUNTLET_SEARCH_TURNS` is deliberately zero, which makes the camera-search branch, `HauntletRun.turns` and `HAUNTLET_CAMERA_DRAG` inert as shipped. Fine as a kill
  switch; say so in its comment.
- `play_ghost_gallery()` is about 160 lines with six closure-captured state variables; a `GhostPlayer` class (as `HauntletRun` and `GhostFeedback` were done) would
  make it testable in parts. `hauntlet_walk()` is about 90 lines.
- Inline literals remain where constants belong: the 96x54 and 8/45 in `hauntlet_view()`, the 80x45 in `screen_changed()`, the thresholds in `find_ghost_alerts()`,
  Ghost Gallery's hard-coded `"w"` and its borrowed `HAUNTLET_LEFT/RIGHT/BACK_KEY`. `TARGET_BUCKETS`, `BUCKET_STEPS` and `COLOR_*` live in `main.py`, against the
  statement in the docs that every tunable is in `magic_numbers.py`.
- Naming: `detect_minigame_victory()` and `minigame_popup_checks()` say "victory" but the screen is the GAME OVER screen for any outcome; the players' docstrings
  say "True if won".

### Stale comments

- `MinigameWatch` says it is fed by `minigame_won()`; it is fed by `minigame_look()` and `minigame_wait()`.
- `magic_numbers.py`: "both end on the victory screen" and "both minigames wait/hold" (three minigames); the overlay comment omits Stitches' Spotlight; the
  Hauntlet 2 comment "then W alone until the room number changes" predates steering and unsticking; the "Record the next run" comment points to the Options tab, it is
  on Debug > Other.

## Discord bot

No exploitable security hole was found. The owner-and-channel gate covers commands, the `/pull` autocomplete and the confirm buttons; `/pull` accepts only the exact
name of an entry of a folder in `PULL_SOURCES` (which does not include `debug/.config`) or `*`; the token and `bot_config.json` are never printed; `/update` fetches
only the configured remote and branch and merges a SHA. Residual: a symlink inside a pulled folder would be followed (very low), and `Config`'s dataclass repr would
include the token if anything ever logged it (nothing does; a `field(repr=False)` on `token` removes the risk).

### High

<a id="b1"></a>
**B1. `/update` can fail before applying anything.** The plan is posted as a code block with up to 15 `git log --oneline` lines. With this repository's long commit
subjects that exceeds 2000 characters at about 14 commits, `followup.send()` raises `HTTPException`, nothing is applied (the send is before the state save), and
`change_code()` has no handler for it. Fix: truncate each line and the total, or send long text as a file; use one helper for every long message.

<a id="b2"></a>
**B2. False crash alert during `/update` and `/rollback`; possible relaunch mid-update.** `apply_commit()` stops the macro without changing `desired_state`, which stays
`running`; `watch_once()` does not look at the update lock. During the stop, git and pip the watcher sees no process, posts "process is gone", and with `auto_restart` on
launches the macro while git is moving files or pip is running (`start_macro()` then sees a process and only sends a `start` command to a macro still starting).
Fix: skip the crash check while the update lock is held, or set `desired_state` to `stopped` for the duration.

<a id="b3"></a>
**B3. Long operations outlive the 15-minute interaction token.** `/update` can spend a stop wait, several git calls, `BOT_PIP_TIMEOUT` and a start wait; a 10 GB
`/pull` copy is the same. Once the token is old, `followup.send()` fails, `apply_commit()` aborts (only `RuntimeError` is caught), the macro is left stopped, and
`RESTART_BOT` is never set. Fix: after a few minutes post through `channel.send()`, and wrap `apply_commit()` and `start_macro()` in `except Exception`.

<a id="b4"></a>
**B4. A command that raises leaves "thinking..." for ever.** Every command defers, but `OwnerOnlyTree.on_error()` only logs. Examples: `launch_macro()` raises if
`debug/logs` is missing; `write_command()` can raise `PermissionError` on Windows if the macro has the file open at that instant **(uncertain)**; `remember()`'s
`save_state()` can raise; `/screenshot` can exceed Discord's upload limit on a large monitor (the send is outside the try); `/pull` and `/status` text can pass
2000 characters (a long `{item!r}`, a long failure reason). Fix: in `on_error()`, send a truncated "Command failed: ..." follow-up when the interaction was deferred;
check the PNG size before sending.

<a id="b5"></a>
**B5. Process detection is loose, and a failed listing is read as "not running".** `main_py_in()` matches the substring `main.py`, so `test_main.py`, an editor or any tool
with it on its command line counts as the macro; match the argv basename. `current_macro_pids()` does `list_processes() or []`: when the listing fails (None, for
instance a PowerShell timeout when psutil is absent) `stop_macro()` reports "was not running" and does nothing, the wait loop can report "stopped" while the macro runs,
and `/restart` can launch a second macro. Treat None as unknown: retry, or abort with a message.

### Logic and state

- **B6. Run charts are lost after `/refresh_debug`.** `state.charts` holds basenames and is never pruned; refresh restarts run numbers at 1, so a new `run1.png` counts
  as already posted. Prune `state.charts` to the files that exist, or key by name plus modification time.
- **B7. A transient read error re-posts old failures.** `read_new_lines()` returns `([], 0)` on any `OSError` (an antivirus or OneDrive lock); the next good read starts
  at offset 0 and re-posts the oldest failure. Return `[], offset` on `OSError`.
- **B8. State advances before the post succeeds.** `log_offset`, `charts` and `stale` change before the send; if the send raises (over 2000 characters, a Discord 5xx, a
  screenshot deleted by a refresh between `isfile` and `getsize`) the failure, chart or alert is lost, the rest of the tick (crash check, heartbeat) is skipped, and
  later failures in the batch are lost too. Wrap each post individually and truncate the text.
- **B9. Restarts always start the loop.** After `/start` (window only) or a local close, `desired_state` is still `running`, so an auto-restart relaunches
  `main.py --loop`; `/update` on a macro whose loop was stopped (`/stop`) also starts the loop. With `auto_restart` on, the window cannot be closed locally without `/exit`.
  `/refresh_debug` is refused as "loop is running" when `status.json` is missing or the loop ended in an error, because `mark_stopped()` is not called on the error and
  focus-lost paths; and the bot says it asked while the macro ignores the command with a warning if a non-loop action runs. Fix: have the worker's `finally` call
  `mark_stopped()` for every ending, and keep whether the loop was running (not only the process) in what `/update` and the auto-restart use.
- **B10. The command file holds one command and is never confirmed.** A second `write_command()` within one poll overwrites the first (`/rejoin` then `/loop`);
  a command older than `COMMAND_MAX_AGE` is dropped silently although the bot said "Asked the macro..."; `act_from_outside()` gives up after its wait while the bot said
  the loop was stopped. Options: a small queue (a list in the file, or one file per command), and an acknowledgement the macro writes (for example into `status.json`)
  that the bot waits for before answering.
- **B11. A bot update with code that does not import loses remote control.** The new process dies at once; on Windows its console closes and nothing is posted.
  Run `py_compile` or `python -c "import bot.discord_bot"` before closing the client, and roll back when it fails.
- **B12. `/loop` can report success falsely.** `started_since()` has one-second resolution and compares `status.updated`; with a loop already running any recent status
  counts as "Its loop is running" even if the `start` command was ignored.

### Lower

- `watch()` calls `fetch_channel()` outside any `try`; a `Forbidden`, `NotFound` or network error ends the task silently and alerts stop. The task reference is not
  kept. `on_ready()` runs `tree.sync()` on every reconnect with no error handling (the 100-character description failure was exactly this: no command registered).
- `restart_allowed()` counts a restart before `launch_macro()`, so a failed launch uses one up. `/loop` and `/start` call `remember("running")` before launching, so
  the watcher can post a brief false crash alert.
- `failure_screenshot_path()`: `os.path.commonpath` raises `ValueError` for a path on another drive on Windows (not reachable from normal macro output).
- `/screenshot` falls back to the whole monitor when there is no Roblox window and can post other applications and notifications; a DPI difference between pygetwindow
  and mss is possible **(uncertain)**.
- `launch_macro()` has no creation flags, so the macro shares the bot's console and Ctrl+C or closing the console can kill it; `taskkill` without `/T` kills only one PID.
- `/pull`: destination paths can pass `MAX_PATH`; a copy that fails half-way leaves partial files (a retry overwrites them); `ConfirmView` has no `on_timeout`, so the
  buttons stay visible after the timeout; the autocomplete lists and calls `getmtime` on the event loop and can raise if a file vanishes.
- `/update`: `previous_commit` is overwritten with HEAD before the move succeeds, so a failed update loses an earlier rollback target; `git fetch <remote> <branch>`
  then `rev-parse refs/remotes/...` depends on the remote's fetch refspec **(uncertain)**; `FETCH_HEAD` is safer.
- Failure text is posted with default allowed mentions; set `allowed_mentions` to none on the client.
- The `/leave_trade_hub` reply does not say the loop is stopped (the macro stops it). `RateLimiter` drops held failures and only reports them when another comes.

## Docs

### False or broken

1. **`ARCHITECTURE.md` Discord command table is split by a blank line** (before `/start`), so `/start` to `/pull` render as plain pipe text. Delete the line.
2. **Names that do not exist:** `HAUNTLET_LEARN_LIMIT` and `HAUNTLET_ROOM_TIMEOUT` in the constants table; `MAX_RUN_CAPTURES` in `planned/Discord.md`.
3. **`MINIGAME_VICTORY_CHECK_INTERVAL`** is described as an interval in the docs and in `magic_numbers.py`, and is not used (see above).
4. **Stale detail:** the timer's white-pixel range (the code names `HAUNTLET_TIMER_MIN_WHITE`), the TV-lock "red pixel" range and its colour (it is a threshold pair now),
   `GHOST_BOSS_FILL` described as a BGR colour (it is an HSV range).
5. **`status.json` field list** omits `stopped` (read by `/status`, `loop_idle()` and the stale alert) and the `failures` total.
6. **"First four phases"** against five phases built (`planned/Discord.md` says five).
7. **CI description** does not match `.github/workflows/check.yml` (pyflakes also runs on `bot`; mypy runs `main.py bot/bot_lib.py`); `planned/Discord.md` repeats it.
8. **Tests section** omits `tests/test_candy_corn.py` and `tests/test_stitches_spotlight.py`.
9. **Links:** `ref/test` does not exist; `main.py` points to `ARCHITECTURE.md#popup-templates`, which is a bold paragraph, not a heading (make it a heading or fix the link).
10. **"The only arguments are numbers"** in the bot section is wrong: `/pull item` is free text and `/need`, `/option` take choice strings.
11. **`planned/Discord.md` is stale throughout:** "only update and rollback still to do"; `/stop` described as `exit`; `/start` as `--loop`; `/update [branch]`; the command
    table lacks `/loop /exit /rejoin /setup /trade_hub /leave_trade_hub /refresh_debug /need /option /options /pull /results`; a "Notify" item that no longer exists in
    `PLANNED.md`; `bot.py` for `bot/discord_bot.py`; an example config in Phase 1 against "none is committed"; sample config keys `update_branch: master` and
    `onedrive_keep_days` (not a key); `stop_timeout` for `stop_timeout_seconds`; "Decisions needed" that are decided. (It was only edited when the user asked to
    update the docs, so it is left for a request.)
12. **`BotState` description** says it holds "captures done"; it has no such field. **The `desired_state` text** omits `/loop`. **`discord_bot.py`'s module docstring**
    still says "Read-only (Phase 1)". The remote-control opening still says `/start`... "need two small things in `main.py`, and no more of it" although more commands
    follow. **`repo_dir` and `python`** are used by `/loop`, the auto-restart and `/update` too, not only `/start`.

### Values written in the docs (house rule: name the constant)

`NEED_GONE_MAX_WAIT` (in several places), `NO_PROGRESS_REJOIN_INTERVAL`, `REJOIN_INTERVAL`, `NO_DETECTION_RESPAWN_INTERVAL`, `UNSCREW_TASK_INTERVAL`, `FOCUS_RESUME_MAX_IN_A_ROW`, `STOP_HOTKEY` and `CAPTURE_HOTKEY`, `TREE_CHECK_INTERVAL`, `MONEY_RESET_HOUR`, `CAPTURE_FRAME_INTERVAL`, `HELICOPTER_REQUIRED`, the Stitches' Spotlight keys (`KEY_STITCHES_WALK`, `STITCHES_TURN_KEYS`), `HAUNTLET_SEARCH_TURNS`, `GHOST_CAMERA_TILT`, `GHOST_COLOR_FAR`, `HAUNTLET_DARK_FRACTION`, `HAUNTLET_PLANK_FRACTION`, `HAUNTLET_PLANK_ANGLE`, the TV light range (`HAUNTLET_TV_GLOW`), `RUN_CAPTURE_ZIPS` and `CAPTURE_ZIP_MB` in `planned/Discord.md`. Measured facts ("63 of 75 doors right") are observations and can stay.
These also make the docs wrong the moment a value is retuned.

### Incomplete or inconsistent

- "Two minigames" or "both" where there are three (`MINIGAME_LABELS`); the overlay list omits Stitches' Spotlight.
- The persisted-config list omits `hauntlet_2_enabled`, `ghost_gallery_enabled` and `stitches_spotlight_enabled`.
- The Discord constants row omits `BOT_GIT_TIMEOUT` (documented nowhere), `BOT_PIP_TIMEOUT`, `NEED_NAMES`, `OPTION_SWITCHES`, `SHOW_DEBUG_LINES_OPTION`, `TRADE_HUB_*`.
  There is no group for the `KEY_*` constants; `KEY_MOUNT`, `KEY_ZOOM_IN`, `WATCH_POLL_INTERVAL`, `TAB_SELECTED_*` and `NEED_WATCH_JOIN_TIMEOUT` are named nowhere.
- Side actions omits `travel_to_trade_hub()`, `leave_trade_hub()` and `click_steps()` (they appear only under Discord). The debug layout omits `captures/`,
  `minigame_captures/`, `commands.json` and `logs/macro_stderr.log`.
- Run capture naming: `<minigame>_<date>_<time>` is incomplete (the suffix from `run_capture_suffix()`); a sentence about which runs are kept lost a comma.
- The `ButtonNeedHandler` row repeats "without waiting for the icon" and contradicts itself. "`run_workflow_loop()` is the only thing that runs" is no longer true.
- Fork doorway counts differ between `ARCHITECTURE.md` and `PLANNED.md`; `retune_check.py` is "a few minutes" in one place and about 15 in `CLAUDE.md`;
  `PLANNED.md` says the sick dialog is up "after `e`" while the handler clicks the doctor's prompt; "the table of `STITCHES_TURN_KEYS`" (it is a tuple); the door
  fixture naming rule has exceptions (`fork_hall__no_doors`, `phone_arrow`, `room1_close`, `thin_one`).

## Tests and tools

- **Tests that pin tuned values** (confirmed by retuning): five prune tests in `tests/test_capture.py` hard-code `RUN_CAPTURES_BEST` and `RUN_CAPTURES_WORST` as 2
  (fix: monkeypatch them at the top, as the failed count already is); `test_the_camera_is_turned_towards_the_side_the_line_says_and_not_when_it_is_straight`
  hard-codes angles inside the deadband (derive them from `STITCHES_LINE_DEADBAND`); `tests/test_config.py` asserts `MINIGAME_START_WAIT >= 60` (delete, or make
  relational). The docs say this file pins no value.
- **`tools/retune_check.py` has no variant** for `RUN_CAPTURES_*` or `STITCHES_LINE_DEADBAND`, which is why the above was missed. Add them. (Do not run it unasked: it
  takes about 15 minutes.)
- **`tools/check_constants.py` scans only `main.py`**, so a misspelt `cfg.NAME` in `bot/*.py` goes unnoticed (the bot uses many `cfg.BOT_*`). Extend it to `bot/` and add
  an unused-constant report.
- **Possible flake:** `tests/test_capture.py::test_a_long_stretch_without_a_frame_forces_one` failed once under `-n 4` and passed on rerun (real threads and
  wall-clock waits). The macro review saw the same test fail once in a run of the suite. Inject a clock or widen the margin.
- **Unused fixture:** `tests/ghost_gallery/pet_close_78.png`.
- **Weak, stale or misnamed:** `len(commands) >= 20` in the slash-command test (compare with the registered commands instead); `test_the_new_config_keys_are_read_and_checked`
  covers two keys (rename); the `# --- control (Phase 4) ---` header; the garbled name `test_no_line_is_gone_by_where_it_is_not_seen`; `test_constants_check_passes_on_the_repo`
  sits in the run-report parser tests and duplicates the CI step; unused `tmp_path` and `monkeypatch` parameters in several tests.
- **Untested by design, worth a test seam:** the Discord glue (`bot/discord_bot.py`: `ConfirmView`, `change_code()`, `watch_once()`, the command handlers) has no tests
  because it needs a Discord client; a thin fake client, or moving more decisions into `bot_lib.py`, would cover the update and crash-alert logic in B2 and B3.

## Ideas and open questions

Collected from earlier work; none of these is built.

**Stitches' Spotlight (first version, never run in the game)**
- Check that the left and right arrow keys turn the camera the way `STITCHES_TURN_KEYS` assumes, that holding D keeps the character on the ring, and how the ring line
  is found when the room is red. The player does not handle the searchlight or look for candy off the ring.
- Roblox's own camera: the line fit works on the olive-gold ring edge; in red light it is not found and the camera is left alone. A second cue (the dark spiked ring,
  or the horizon) would cover those frames. A camera that drifts for long is the likely failure.
- The `E` key is a speed boost by the game's message ("Press E for a Speed Boost!"); the human used it about ten times. It is not used.
- The earlier design (walk to the nearest candy corn, step away from red, stand still behind planks) was replaced by the ring walk; its findings are still true
  and would be the next step: planks hide the player and break now and then, red covers the area around the character when lit, the candy count never has to be read.
- A fixture of the central bunny cannot be matched (it moves); the ring edge was chosen for that reason.

**Hauntlet 2 and Ghost Gallery** (from `PLANNED.md`): the Hauntlet walk, door kinds (the third layer's spiked doors read as boarded), the decision logic (risk-based door
choice, hearts and items), fork rooms, and Ghost Gallery's pet and boss handling are all unconfirmed in the game. Hearts and items have unread cases (a row of only black
hearts, the cyan temporary heart, the wand, counts above two).

**Robustness ideas**
- Captures of failed runs are kept; a Discord `/failures` listing with a per-failure screenshot would let a failed run be looked at without the PC.
- Replace fixed-pixel clicks with on-screen finds where the layout can change (the backpack, the vehicle and toy items, the Equip button, the Trading Hub trips).
- Confirm teleport steps by comparing the screen with what is expected after each step.
- Navigate to the hospital by finding its door instead of timed key holds.
- Make clicks and timings less hard-coded and tune them (`PLANNED.md`, "Add next").
- A heartbeat that says what the macro is doing now (the current need or minigame), not only that it is updating.
- Per-minigame statistics over several days (best, worst, average are in `/results`; a trend chart would show whether a change helped).

**Bot ideas**
- Left over from the removed `docs/planned/Discord.md`: deleting old copies in OneDrive by age or size (the bot deletes nothing there); a full-screen screenshot as a
  separate command; trying the commands from a phone; trying alerts, `/pull`, start/stop and `/update` and `/rollback` against the real PC (a no-op update, one that changes
  `requirements.txt`, one that makes `main.py` fail on start, to prove the failed-start report and `/rollback`).
- A command that lists the commands and their state (`/help` is Discord's own list, but a short status of what is possible right now would help).
- Acknowledgement of remote commands by the macro (B10) would let every command report what really happened.
- A dry run of `/update` that posts what would change without applying it; `/update` already posts the plan, a confirm button (as `/pull` has) before applying would
  make it a two-step action.
- Posting a failure screenshot inside the thread of the alert, and deleting the screenshot after a successful post, to keep the debug folder small.
- A `/log` filter (only failures, or one need) once the log gets long.

**Process**
- The docs repeat tuned values in many places; a check that scans `ARCHITECTURE.md` for numbers next to constant names would keep that rule honest.
- `ARCHITECTURE.md` is long (about 1700 lines); splitting it (macro, minigames, bot, tests) would make it easier to keep accurate, with the same anchors kept as short
  redirects.
- `main.py` is about 5900 lines; the minigame players and the Discord command handling are the natural first modules to split out, with the tests following the split.
