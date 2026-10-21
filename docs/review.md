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
and `capsys` test parameters (unused imports and local variables are pyflakes's, which now also covers `tools`). Also done (dead and duplicate code): M8 (`wait_for_minigame_start()` replaces the three start waits and ends on a disconnect), the unused `hearts` parameter of `choose_hauntlet_door()`, the unused `began` attribute and `config` parameter, the repeated command handlers of the Discord bot (`ask_macro()`), and the repeated colour-count code (`color_pixels()`, `color_fraction()`). Fixed from "Fix first": M1 (`poll_commands()` keeps polling), M3 (held keys are tracked in `HELD_KEYS`, so all are released; the fail-safe worry M4 is not addressed beyond each release being tried separately), M7 (the lost stop), B1 (every message is clipped to fit) and B2 (no crash alert during `/update`). Then fixed: M2 (a queue of outside actions), M4 (the safety net's releases skip the fail-safe check, without moving the cursor or switching the fail-safe off), M6 (`close()` asks for the stop first and joins the worker), M13 (a dropped command is logged). Also fixed: M9 (one overlay match per look), M11 (a lock on the status file; it is written by the worker as the run goes on and by Tk's thread when the window opens or closes), B4 (a command that raises is told to the channel; a too-big screenshot is sent as a JPEG; the log folder is made if missing); `/restart` was removed (it was `/exit` then `/loop`). Also done: the inline literals (the Hauntlet view and screen-change sizes, the Ghost "!" thresholds) are constants, the movement keys are one set (`KEY_FORWARD`, `KEY_LEFT`, `KEY_BACK`, `KEY_RIGHT`) instead of Hauntlet ones borrowed by Ghost Gallery, and the comments that said victory or won now say the minigame finished. Also: every key the macro presses is named in one place by what it is for (`KEYS`, `KEY_SEQUENCES`, `HOTKEYS` in `magic_numbers.py`; the walk tables use them too, and `tools/check_constants.py` fails on an unknown label or a key written as a letter), and the finish screen of a minigame is called that everywhere (the constants `MINIGAME_FINISH_*`, `detect_minigame_finished()`, the fixtures and the log lines), not victory or win. Then: B3 (an answer goes to the channel when the interaction has expired) and B5 (`main.py` matched as a whole file name; a failed process listing is retried and then said, never taken for "not running"). Then: M5 (the hotkey thread survives a failed look), M10 (the need watcher survives a failed look; the window rect cache is read once into a local), M14 (the flag that nothing runs goes down on Tk's thread with the buttons; Refresh debug is refused while a capture records). M12 is dropped, as are the `allowed_mentions` item and the idea of splitting `ARCHITECTURE.md` and `main.py`. Then, in the bot: B9 (`desired_state` is `open` after `/start` and `/stop`, so a restart or an update brings the macro back with its loop only if it had one), B6 (a chart that is gone is forgotten, so a run number used again after Refresh debug is posted), B7 (a run log that cannot be read is said once, and again when it can be; the place in it is kept), B11 (the bot restarts after an update only if `import bot.discord_bot` works, else it says so and offers `/rollback`), and `/update` asks first, with the commits and the lines changed in the Python files. The startup errors: the channel is fetched again after a hiccup (`find_channel()`), the watcher task is kept and logged if it ends, and `tree.sync()` runs once with its failure logged and posted. B8 (`attempt()`: a failed post does not end the look; charts, the stale alert and the heartbeat count as done only once sent; a gone picture is left out), B10 (one file per command in `COMMANDS_DIR`, read oldest first, and the macro answers each with `taken` or `dropped: why`, which the bot waits for and withdraws the command if none comes; a queued action that is given up later is still only in the log), and the `check_constants` scan of `bot/`, which was already there. Then B12 (`/loop` says the loop is running already, or waits for a status that is new and not idle), the lower bot items that left the macro's behaviour alone (listed under Lower below), and the `catch`, `pet` and `ride` needs as tables of steps. Not changed, and still open: the console item below, and the docs' values that are measurements rather than constants. M1 to M14 are all fixed or dropped (M4 only in part), as are B1 to B12; the entries below are kept as the record of what was found.

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
- **M13. `read_command()` drops stale, unknown and malformed commands without a log line.** A bot host whose clock is off by more than `COMMAND_MAX_AGE` gets every
  command ignored with nothing in the log. Add a `logger.warning`. A command written between the read and the `os.remove` is also lost (tiny window).
- **M14. Smaller races.** The worker's `finally` sets `is_running = False` before queueing the UI reset on Tk; an `act_from_outside()` retry in between gets its UI reset
  to "Ready" with the buttons enabled. `refresh_debug_from_outside()` checks only `is_running`; a live `InputCapture` can still write into the folder being deleted.

### Dead code, duplication, size

Checked against the code: the unused `MINIGAME_VICTORY_CHECK_INTERVAL`, `minigame_won()` and the `hearts` parameter are gone, the inline literals are constants, the movement keys are one set, `detect_minigame_victory()` is `detect_minigame_finished()`, and `HAUNTLET_SEARCH_TURNS`'s comment says it is a kill switch. Still there:
- `play_ghost_gallery()` is about 160 lines with several closure-captured state variables; a `GhostPlayer` class (as `HauntletRun` and `GhostFeedback` were done) would make it testable in parts. `hauntlet_walk()` is about 90 lines.
- `TARGET_BUCKETS`, `BUCKET_STEPS` and `COLOR_*` (the run report's chart) live in `main.py`; `ARCHITECTURE.md` names that as the one exception to "every tunable is in `magic_numbers.py`".

### Stale comments

None found in `main.py` or `magic_numbers.py` that the earlier review listed (the `MinigameWatch` feeding, "both" for three minigames, the overlay list, the Hauntlet 2 walk comment); the "Record the next run" comment now says the Options subtab.

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
### Lower

Fixed: a restart is counted once the macro has been launched, and `/start` and `/loop` pause the crash check while they launch it (`LAUNCHING`); a failure screenshot on another drive is none and no error; the macro is launched in a process group of its own and killed with its children (`taskkill /T`); `/pull` removes what a failed copy made, gives paths past `MAX_PATH` the long form, takes the buttons off at the timeout, and its autocomplete skips an entry that vanished; `/update` keeps `previous_commit` until the move has been made and takes the target from `FETCH_HEAD`; the `/leave_trade_hub` reply says the loop is stopped; failures held back by the rate limit are counted in a post of their own. Still there:
- The macro the bot launches shares the bot's console window, so closing that window (its X button) closes the macro as well; Ctrl+C no longer reaches it. Launching it with no console would stop that, but would take away the terminal question about a new need's name (`input()`), which is the macro's behaviour.

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

Checked against `ARCHITECTURE.md`: the lists the review found short (the persisted config, the Discord constants row, the side actions, the debug layout, the capture naming, the fixture naming exceptions) are complete now, and the "both" and "two minigames" wording says every minigame. Left:
- `PLANNED.md` says the sick dialog is up "after `e`" while the handler clicks the doctor's prompt (outside the Claude section, so not edited), and its fork doorway counts were not compared with `ARCHITECTURE.md` again.

## Tests and tools

- **Tests that pin tuned values** (confirmed by retuning): five prune tests in `tests/test_capture.py` hard-code `RUN_CAPTURES_BEST` and `RUN_CAPTURES_WORST` as 2
  (fix: monkeypatch them at the top, as the failed count already is); `test_the_camera_is_turned_towards_the_side_the_line_says_and_not_when_it_is_straight`
  hard-codes angles inside the deadband (derive them from `STITCHES_LINE_DEADBAND`); `tests/test_config.py` asserts `MINIGAME_START_WAIT >= 60` (delete, or make
  relational). The docs say this file pins no value.
- **`tools/retune_check.py` has no variant** for `RUN_CAPTURES_*` or `STITCHES_LINE_DEADBAND`, which is why the above was missed. Add them. (Do not run it unasked: it
  takes about 15 minutes.)
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
