# Review

What an earlier review of the code, docs and tests found, and what has become of each finding, read in the code on 2026-11-15; then what the repository review of that day found. This file is a list to pick from, not a record of work: each line says where the matter stands. Nothing here was run against the real game, Discord or Windows by the review itself (CI runs the tests on Windows and Linux; they stub the input and window libraries).

How the status of an old finding was decided: the code it named was read again (function names, the lines that were the problem). A finding marked **Fixed** has its fix in the code and, where one is cheap to write, a test. **Obsolete** means the code or file it was about is gone. **Not re-checked** means it was not read again.

Contents: [Earlier findings](#earlier-findings) · [Repository review of 2026-11-15](#repository-review-of-2026-11-15) · [Ideas still open](#ideas-still-open)

## Earlier findings

### Macro (`main.py`)

| # | Finding | Status |
|---|---|---|
| M1 | An exception in `poll_commands()` ended remote control for good | Fixed: it re-arms itself first, and a failing command is logged (`The command from outside (...) failed`) |
| M2 | Overlapping outside actions aborted each other, and `stop` did not cancel a pending one | Fixed: one ordered list (`pending`) and one chain (`drain_pending()`) |
| M3 | `release_all_inputs()` did not release every key the macro holds | Fixed: it releases `HELD_KEYS` (every key pressed through `key_down()`) and `cfg.HELD_KEYS`, and guards the mouse part |
| M4 | (uncertain) the fail-safe could leave keys held when the cursor is in a corner | Fixed in design: the releases skip the fail-safe check (`unchecked()`), the fail-safe stays on for everything else. Unseen in the game |
| M5 | The hotkey thread could die silently | Fixed: each look is guarded, the failure logged once, the watching goes on |
| M6 | `close()` stopped the capture before it set `STOP_EVENT` | Fixed: the stop is asked for first |
| M7 | A stop could be lost at the start of a run (a second `STOP_EVENT.clear()`) | Fixed: `run_async()` clears it once, before the thread starts |
| M8, M9 | The minigame players repeated their start wait; `MinigameWatch` matched every overlay | Obsolete: the event code is archived (see ARCHITECTURE, "Archive") |
| M10 | A watcher thread died on any error; `roblox_rect()` read a global that another thread can set to None | Fixed: the watcher catches `Exception` per look, and the rectangle is copied to a local first |
| M11 | `write_status()` shared a temporary file between threads | Fixed: `STATUS_LOCK` |
| M13 | `read_command()` dropped stale commands without a log line | Fixed: a warning says why |
| M14 | Smaller races (the UI reset after a finished worker; `refresh_debug` with a live capture) | Not re-checked |

### Discord bot (`scripts/discord_bot.py`, `scripts/bot_lib.py`)

| # | Finding | Status |
|---|---|---|
| B1 | `/update` could fail before applying when its plan text passed 2000 characters | Fixed: `bot_lib.clip_message()` for every message, `say()` for a command's answer |
| B2 | A false crash alert during `/update`, and a relaunch in the middle of it | Fixed: `UPDATING` and `LAUNCHING` pause the crash check |
| B3 | Long operations outlived the interaction token | Fixed: `say()` posts in the channel itself once the interaction has expired (`followup_needs_the_channel()`) |
| B4 | A command that raised left "thinking..." for ever | Fixed: `OwnerOnlyTree.on_error()` says what failed |
| B5 | A failed process listing was read as "not running" | Fixed: `current_macro_pids()` is None when unknown, `running_pids()` retries and says so, and nothing is stopped or started on a guess |
| B6 | Run charts were lost after `/refresh_debug` | Obsolete: the bot no longer posts charts by name (`/report` draws one on request) |
| B7 | A read error of the log re-posted old failures | Fixed: the offset is kept on an error (a test covers it) |
| B8 | State advanced before a post had succeeded | Fixed: each post goes through `attempt()`, and the stale alert is kept as it was when it could not be sent |
| B9 | Restarts always started the loop | Fixed in design: `desired_state` has `open` (a window, no loop), `running` and `stopped`, and the worker's end calls `mark_stopped()`. Not every ending re-read |
| B10 | The command file held one command and was never confirmed | Fixed: one file per command and an acknowledgement file (`take_ack()`) the bot waits for |
| B11 | A bot update whose code does not import lost remote control | Fixed: the new code is imported first, and `/rollback` exists |

### Docs, tests and tools

The first review's docs findings (a split table, names that did not exist, values written where the constant should be named, stale descriptions) were fixed when they were found or concerned files that have since been rewritten or removed (`docs/planned/Discord.md`, the `bot/` folder, the minigame sections). `tools/check_constants.py` now guards the repeated causes: a name in the docs that no code file has, an unused constant, a function only tests call, a test parameter it never uses. Docs that repeat tuned values are still a house rule that is kept by hand. Not every item of the tests list was read again: the hard-coded `RUN_CAPTURES_*` prune tests, the unused fixture `tests/ghost_gallery/pet_close_78.png` (it went with the archive) and the misnamed tests.

## Repository review of 2026-11-15

Found by reading the repository (layout, size, CI, docs, hygiene), not the game logic. Done the same day:

- **Done:** CI installs `requirements.txt` (the pinned packages users get), runs on Python 3.12 and 3.13 on Linux and a Windows job, mypy covers `main.py`, `magic_numbers.py`, `scripts/` and `tools/` and warns about unused ignores; `# type: ignore` went from 36 to 1 (`windll()`, `functools.partial` for the Tk callbacks, a client class for the bot, explicit kernels for OpenCV, `postable()` for the bot's channels); no `print` left in `main.py`; GitHub's actions are at checkout v5 and setup-python v6; `requirements.txt` is UTF-8; `.gitattributes` pins LF; `tests/icon_detection/halloween1` is `busy_scenes`; a stale `HALLOWEEN` flag left three test comments.
- **The Windows job found real differences:** two bot tests assumed Linux (a path left as it is, a text file's byte count). They were fixed. A pinned `mss` that can return no PNG made mypy fail where an unpinned install had hidden it.

Still open:

| Area | Finding |
|---|---|
| Size | `tests/` is 128 MB (162 PNGs, 60 over 1 MB). Lossless recompression saves about a fifth (12 sampled files: 10.1 MB to 8.0 MB); cropping fixtures to the region a test reads saves more. `archive/` is another 82 MB; removing a folder does not shrink the git history |
| Dependencies | The pins are exact and nothing updates them. A Dependabot configuration for pip and for the actions would open update PRs that CI then tests |
| Docs | 32 lines of `ARCHITECTURE.md` are over 1,000 characters (the longest 6,042) and 5 items of `PLANNED.md`: accurate, hard to read and to review |
| Release basics | No version number, changelog, contributing guide, `SECURITY.md` (the bot runs with the user's Discord token) or issue templates; the README is a few lines |
| Code | `main.py` is one file of about 450 functions; `magic_numbers.py` has about 590 constants; 46 `except Exception` blocks (many are deliberate "never raises" helpers, some log and some do not); 1 `# type: ignore` left (the bot's `setup_hook` assignment) |
| Users | Single-user assumptions (the account, the one resolution, hotkeys on one keyboard layout) are the subject of `ROADMAP.md` |

## Ideas still open

Collected from earlier work; none is built.

**Robustness**
- A Discord `/failures` listing with a screenshot for each failure, so a failed run can be looked at without the PC.
- Replace fixed-pixel clicks with on-screen finds where the layout can change (done for a few buttons: ARCHITECTURE, "Fixed clicks that look first"; the rest is in `PLANNED.md`).
- Navigate to the hospital by finding its door instead of timed key holds (`PLANNED.md`).
- A heartbeat that says what the macro is doing now (the current need), not only that it is updating.
- Per-need statistics over several days (a trend chart would show whether a change helped).

**Bot**
- Trying the commands from a phone and against the real PC: a no-op update, one that changes `requirements.txt`, one that makes `main.py` fail on start, to prove the failed-start report and `/rollback`.
- A dry run of `/update` that posts what would change without applying it, and a confirm button (as `/pull` has) before it applies.
- Deleting old copies in OneDrive by age or size (the bot deletes nothing there).
- A `/log` filter (only failures, or one need) once the log gets long.

**Process**
- A check that scans `ARCHITECTURE.md` for numbers next to constant names would keep the house rule (docs name constants, not values) honest.
