# Planned: Discord remote control

Status: **not started** - this is the plan, written from reading `main.py` and `docs/ARCHITECTURE.md`.

## Goal

Run the macro on the home PC and, from anywhere (a phone, say):

1. **See** how it is doing: alive or stuck, what it resolved, what failed, a live screenshot.
2. **Be told** when something breaks (this is the "Notify somewhere when something breaks" item in `PLANNED.md`).
3. **Control** it: start the loop, stop it, restart it.
4. **Update** it: stop the macro, `git pull` the changes made in a Claude session, start it again.
5. **Get the minigame capture zips** off the PC without going home, via OneDrive, so they can be forwarded to Claude.

Claude (in a cloud session) cannot see Discord or the PC. The loop is always: the macro posts a report and links
-> you forward the relevant zips/logs to Claude -> Claude pushes a fix to a branch -> you type `/update` in Discord.

## Why a bot and not a webhook

A Discord *webhook* can only post messages out; it cannot receive commands. Two-way control needs a **bot** (the
`discord.py` library). The bot connects *outward* to Discord, so no port forwarding or public IP is needed. A
webhook is still fine for the one-way "something broke" posts if the bot is not wanted at first (Phase 1 can use
either; the plan below uses the bot throughout so there is only one thing to set up).

## What already exists and is reused

The macro already writes everything the bot needs to report, so the bot reads files and **does not need to be
wired into the macro's internals** for status and alerts:

| Need | Where it already is |
| --- | --- |
| Is it alive / what is it doing | `debug/logs/status.json`, rewritten every cycle by `write_status()`; its `updated` time going stale means stuck or dead. Has counters, `last_failure`, `disabled_needs`, `quiet_minutes`, CPU/memory |
| What happened | `debug/logs/run_log.txt` (one tagged line per event, `FAILURE: ...` for failures), `debug/logs/runs/run<N>.txt` (one run), `debug/logs/output.log` |
| What broke, with a picture | `debug/images/failures/<time>_<run>_<reason>/` (`screen.png`, `need_icons.png`, `info.txt`, `state.json`), made by `log_failure()` |
| Run chart | `debug/images/reports/run<N>.png`, written when a run stops (`write_run_report()`) |
| Minigame captures | `debug/minigame_captures/<minigame>_<date>_<time>/`, each with a `zips/` folder (`capture_..._part01.zip`, ...) made on a thread by `zip_capture_in_background()`. At most `RUN_CAPTURE_ZIPS` (5) zips of `CAPTURE_ZIP_MB` (25 MB) per run; `info.json` has the `outcome` |
| Stopping safely | `STOP_EVENT` (cooperative; the worker releases held keys itself - see "Stopping & focus safety") |
| Starting | the GUI's loop button -> `run_async(run_workflow_loop)` |
| Zip splitting | `tools/split_zip.py` |

What does **not** exist and has to be added: a way to start/stop the loop from outside the GUI, a `--loop`
start-up flag, and the bot itself.

## Design

### Two processes: a supervisor/bot, and the macro

```
Discord  <--outbound-->  bot.py (supervisor)  --launches/kills-->  main.py (the macro, GUI + loop)
                              |   reads                               |  writes
                              +---- debug/logs/status.json, run_log.txt, failures/, minigame_captures/ <----+
                              +---- writes debug/commands.json (stop / start requests) ---> polled by main.py
```

Reasons for a separate process rather than a thread inside `main.py`:

- **Update needs it.** Python cannot replace the code it is running; something outside has to stop the macro,
  `git pull`, and start it again. The same process keeps running the bot while the macro is down, so you can still
  talk to it when the macro has crashed or is mid-update.
- **A crashed or frozen macro doesn't take the bot down**, which is when you want the bot most.
- `main.py` is already 5,000+ lines; the bot stays out of it. Coupling is limited to two small hooks (below).

### The two hooks needed in `main.py`

1. **`python main.py --loop`**: after the GUI is up, start `run_async(run_workflow_loop)` as if the loop button was
   pressed (through `root.after`, so it runs on Tk's thread). Used by the bot to start and restart the macro.
2. **A command file poll.** The GUI already has a thread for the hotkeys (`watch_stop_hotkey()`); add a check there
   (or a `root.after` timer) for `debug/commands.json` containing `{"command": "stop"}` or `"start"`: it is handed
   to Tk's thread, deleted once read, and does exactly what the STOP / loop buttons do (`self.stop()` /
   `self.run_async(run_workflow_loop)`). That keeps the **cooperative stop**: the worker unwinds through its
   `try/finally` blocks and releases held keys and mouse buttons. Killing the process is only the fallback
   (see "Stop" below).

### Windows specifics that shape the design

- The macro clicks and types into Roblox, so both the macro and the bot's screenshot code must run in the
  **logged-in desktop session**. A Windows service or a scheduled task set to "run whether user is logged on or
  not" cannot see or click the game. Auto-start with a shortcut in the Startup folder (or a task "run only when
  user is logged on"), with the PC set to log in automatically if it should come back after a power cut.
- The PC must not sleep or lock: a locked screen shows nothing to the macro or to the screenshot command.
- Screenshots: reuse `grab_full_screen()` / `grab_screen()` from `main.py` by importing, or take a plain `mss`
  grab in the bot (no import of the 5,000-line module and no Tk). The bot should do the latter.

## Security (decide these before writing code)

The bot can move the mouse of your PC and run `git pull` and Python, so a stolen token or an open channel is
effectively remote control of the PC.

- **Only obey one Discord user ID** (`owner_id` in the config) in **one private channel**. Ignore everything else
  silently. Use slash commands with `default_member_permissions`/an `owner_id` check, not text prefixes.
- **A fixed list of commands. No `/run <anything>`, no shell, no file-path arguments.** Arguments are limited to
  choices from a fixed list (for example the minigame names) and numbers.
- **The token and ids live in an untracked file** (`bot_config.json` or `.env`, added to `.gitignore`), with a
  committed `bot_config.example.json` showing the keys. Never print the token. If it ever leaks, reset it in the
  Discord developer portal.
- **Create the bot as private** ("Public Bot" off in the developer portal) and invite it only to a private server
  of yours, with just the permissions it needs (send messages, attach files, use slash commands).
- **`/update` only does a fast-forward pull of one configured branch from the configured remote** (below), and
  refuses if there are local changes. Whoever can push to that branch can change what runs on the PC, so keep
  the branch protected to your own account.
- Screenshots can show other things on screen (chat, tabs, notifications). Only the Roblox window is captured, not
  the whole monitor, by default; a full-screen option can be a separate command.

## Commands

| Command | What it does |
| --- | --- |
| `/status` | Reads `status.json`: alive (age of `updated`), run number, cycles, resolved / failed per need, disabled needs, last failure, quiet minutes, CPU/memory, and whether `main.py` is running (process check). A stale `updated` is flagged |
| `/screenshot` | Grabs the Roblox window (or the screen with an option) and posts it as a PNG |
| `/log [lines]` | The last N lines of `run_log.txt` (capped, as a file attachment if long) |
| `/report` | The newest `reports/run<N>.png` |
| `/start` | Launches `main.py --loop` if it is not running |
| `/stop` | Asks the macro to stop (command file); if the loop hasn't stopped within a timeout, kills the process and releases held keys (see below) |
| `/restart` | `/stop` then `/start` |
| `/update [branch]` | Stop, `git pull --ff-only`, reinstall requirements if they changed, start. Posts what changed (the `git log` of the pulled commits) |
| `/rollback` | Go back to the commit that was running before the last `/update` (kept in `bot_state.json`), then start |
| `/captures` | Lists recent minigame capture zips, with sizes and whether they are in OneDrive yet |
| `/capture-link <name>` | Posts the OneDrive link for one capture (see below) |

Automatic posts (no command needed):

- **A failure**: a new line starting `FAILURE:` in `run_log.txt` or a new `failures/` folder -> post the reason and
  the folder's `screen.png`. Rate-limited (for example one per minute, then "N more failures") so a loop of
  failures doesn't spam the channel.
- **Stopped / dead**: `status.json` not updated for N minutes while the process is running, or the process gone
  while it should be running (a crash, "Roblox lost focus" stopping the run) -> alert, and optionally an
  automatic restart (config switch, off by default, with a limit of a few per hour).
- **A run ended**: post the run report chart.
- **A minigame capture finished** and was copied to OneDrive -> post the name, outcome, size and link.
- **A heartbeat** (config: every N hours, or off) with the short status so silence means trouble.

## Capture zips -> OneDrive

The simplest approach needs no Microsoft API:

1. Set `onedrive_dir` in the bot config to a folder **inside the PC's synced OneDrive folder**, for example
   `C:\Users\<you>\OneDrive\AdoptMeCaptures`. The OneDrive desktop client uploads whatever is copied there.
2. The bot watches `debug/minigame_captures/*/zips/`. A zip counts as ready when the capture's `info.json` has an
   `outcome` **and** the zip sizes have stopped changing for a few seconds (`zip_capture_in_background()` writes
   them on a thread, so they can appear before they are complete).
3. Copy them to `onedrive_dir/<capture folder name>/` (`shutil.copy2`; the files are already split to 25 MB parts,
   so one capture is a handful of parts). Remember which captures were copied in `bot_state.json`.
4. Post the capture name, outcome and part count in Discord, plus a link. For the link, share the **whole
   `AdoptMeCaptures` folder once** by hand (OneDrive -> Share) and post that single link with the name of the
   subfolder. A per-file link through the Microsoft Graph `createLink` call would need an Azure app registration
   and token handling - leave that as an optional later step.
5. Housekeeping: delete OneDrive copies older than N days or beyond N MB (config), since the free quota is small.
   The macro already keeps only the newest `MAX_RUN_CAPTURES` on the PC.

Getting them to Claude: **a OneDrive link can't be opened from a cloud session**. Download the parts (or just
the ones wanted) and attach them to the chat, in order (see the capture notes in `ARCHITECTURE.md` -
"Getting one to someone else"). Discord's own upload limit for a bot is around 10 MB without server boosts, so it
is not a substitute for OneDrive for the 25 MB parts, but `/screenshot`, reports and failure images fit.

## `/update` in detail

The safe order, because the macro is holding keys and the working tree must be clean:

1. Refuse if a minigame capture or a need handler is mid-way? Not needed - a graceful stop already copes with
   being interrupted anywhere - but tell the user what was running.
2. **Stop the macro** (command file; wait up to `stop_timeout` seconds for the process to exit).
3. `git status --porcelain`: if there are local changes to tracked files, **abort** and say so (never discard
   them). `debug/` is git-ignored so it doesn't count.
4. `git fetch origin <branch>`; if `git merge-base --is-ancestor HEAD origin/<branch>` fails (history diverged),
   abort and say so. Otherwise `git pull --ff-only origin <branch>`.
5. Record the old commit hash (for `/rollback`) and post `git log --oneline old..new`.
6. If `requirements.txt` changed in the pull, run `python -m pip install -r requirements.txt` (with the same
   Python that runs the bot; `sys.executable`). Note: `requirements.txt` in this repo is UTF-16 encoded - pip
   reads it fine, but any script that edits it must keep the encoding.
7. Start `main.py --loop` again and confirm `status.json` starts updating; post "running commit <hash>".
8. On a failed start (the process exits within a few seconds, or `status.json` never updates), post the
   traceback tail from `output.log` and offer `/rollback`.

Which branch? Add `update_branch` to the config (default the repo's main branch). To try a Claude-made change
first, `/update claude/<branch-name>` could pull a named branch - this needs a decision (see below), because it
moves the PC onto a branch that hasn't been reviewed or merged.

## Stop details (what must not go wrong)

- **Normal stop**: write the stop command; the macro's cooperative stop releases keys/buttons in its `finally`
  blocks (see "Stopping & focus safety"). Wait for the process to exit (`stop_timeout`, for example 30 s - a
  75 s walk is interrupted within a check or two, not at its end).
- **Fallback kill**: if it doesn't exit, terminate the process, then **release everything** from the bot
  (key-up for the movement keys and `shift`/`space`/`e`..., mouse button up), because a killed process can leave
  a key held down in the game. `release_all_inputs()` in `main.py` shows which; the bot needs its own small copy
  (`ctypes` `SendInput`/`keybd_event`) since it doesn't import `main.py`.
- A stopped macro on purpose (`/stop`) must not be reported as a crash or auto-restarted: the bot keeps a
  `desired_state` (running / stopped) in `bot_state.json`.

## Phased steps

Each phase is shippable and testable on its own; later phases build on earlier ones.

### Phase 0 - Discord side (by hand, ~10 minutes)

1. discord.com/developers -> New Application -> Bot. Turn **Public Bot** off. Copy the token.
2. Create a private server (or a private channel in an existing one) for the macro.
3. Invite the bot with the `bot` and `applications.commands` scopes and permissions: View Channel, Send Messages,
   Attach Files, Embed Links.
4. Turn on Developer Mode in Discord, and copy your **user ID**, the **channel ID** and the **server ID**.
5. Put them in `bot_config.json` (untracked; the example file is added in Phase 1).

### Phase 1 - Read-only bot (no change to `main.py`)

1. Add `discord.py` to `requirements.txt` (and pin the version like the others).
2. Add `bot/` (or `discord_bot.py` plus a small `bot_lib.py` for the testable parts - see Phase 6) with:
   config loading (`bot_config.json`, validated, with a clear error for a missing key), the owner/channel check,
   and the commands `/status`, `/screenshot`, `/log`, `/report`.
3. `.gitignore`: `bot_config.json`, `bot_state.json`. Commit `bot_config.example.json`.
4. Run it by hand next to the macro and try the commands from the phone.
5. Add the process check (is `python main.py` running?) so `/status` can say "macro not running".

### Phase 2 - Alerts

1. A watcher task in the bot (every ~10 s): new `FAILURE:` lines in `run_log.txt` (remember the file offset;
   handle the log being deleted by **Refresh debug** or rotated), new `failures/` folders, a stale `status.json`,
   a finished run report.
2. Post with the failure's `screen.png`; rate-limit; heartbeat option.
3. Test by forcing a failure (the Debug tab's handler tests, or stopping Roblox) and watching the post arrive.

### Phase 3 - OneDrive capture zips

1. `onedrive_dir` in the config; the watcher from the "Capture zips -> OneDrive" section above; `/captures` and
   `/capture-link`.
2. Test with a short minigame capture (or **Record** with the `m` hotkey, which makes the same zips) and check the
   files appear in OneDrive and the post arrives.
3. Housekeeping of old copies.

### Phase 4 - Control (`main.py` hooks + start/stop)

1. In `main.py`: the `--loop` argument (parse with `sys.argv`, no new dependency) and the command-file poll in the
   GUI. Keep the code small; add the new constants (`COMMANDS_PATH`, poll interval) to `magic_numbers.py` and the
   description to `ARCHITECTURE.md`.
2. In the bot: launch/track the process (`subprocess.Popen`, working directory the repo, same Python), `/start`,
   `/stop` (cooperative then the fallback kill + input release), `/restart`, `desired_state`.
3. Test: start, stop mid-walk and check no key stays held (try holding nothing and watch the game), kill the
   macro's process by hand and check the alert and the optional auto-restart.

### Phase 5 - Update and rollback

1. `/update` and `/rollback` as above, with the dirty-tree and diverged-history refusals.
2. Test on a throwaway branch: a no-op update, an update that changes `requirements.txt`, an update that makes
   `main.py` fail on start (to prove the failed-start report and `/rollback` work).
3. Start-up: a shortcut in the Windows Startup folder that runs the bot (which in turn starts the macro if
   `desired_state` is running).

### Phase 6 - Tests, CI and docs

The CI (`.github/workflows/check.yml`) runs `pyflakes`, `tools/check_constants.py`, `mypy main.py` and
`pytest` on Linux, and can't run the macro or Discord. So:

1. Keep the logic that can be tested without Discord or Windows in plain functions: the owner/channel check,
   config validation, parsing new lines from the log (offset handling, truncation), choosing ready capture zips
   (size stable, `outcome` present), rate limiting, the git-update decision (clean tree / fast-forward / diverged)
   given command outputs, and building the status text from a `status.json`. Add `tests/test_bot.py`.
2. Add the new files to the `pyflakes` line in `check.yml` (and to `mypy.ini`/the mypy step if wanted;
   `discord.py` ships types).
3. Document it: a section in `ARCHITECTURE.md` (commands, the two hooks, config keys, security rules), and move
   the Natro "notify somewhere when something breaks" item to done. (`README.md` and `LINKS.md` are the owner's.)

## Config keys (`bot_config.json`)

```
{
  "token": "...",
  "owner_id": 123456789012345678,
  "channel_id": 123456789012345678,
  "guild_id": 123456789012345678,
  "repo_dir": "C:\\path\\to\\AdoptMeMacro",
  "python": "C:\\path\\to\\.venv\\Scripts\\python.exe",
  "update_remote": "origin",
  "update_branch": "master",
  "onedrive_dir": "C:\\Users\\you\\OneDrive\\AdoptMeCaptures",
  "onedrive_share_link": "https://1drv.ms/...",
  "onedrive_keep_days": 14,
  "stop_timeout_seconds": 30,
  "stale_status_minutes": 5,
  "heartbeat_hours": 0,
  "auto_restart": false,
  "auto_restart_max_per_hour": 3
}
```

## Decisions needed before building

1. **Branch for `/update`**: only the main branch (safer: changes are merged first), or also a named
   `claude/...` branch (faster to try a fix, but runs unreviewed code)? Suggestion: main only by default, with
   the named branch behind an explicit `/update branch:<name>` and a confirmation button.
2. **Auto-restart** when the macro dies: on or off by default? Suggestion: off, since a crash caused by Roblox
   losing focus usually means the PC is in use (the macro stops on purpose then - see "Stopping & focus safety").
3. **Screenshot scope**: Roblox window only (suggested) or the whole monitor.
4. **OneDrive link style**: one shared folder link (suggested, no setup) or per-file links via the Graph API.
5. **Where the bot runs**: always started with the PC (Startup shortcut, suggested) or started by hand.

## Risks and limits

- **Roblox / Adopt Me rules**: macros may be against the game's terms; this plan doesn't change that, it only
  makes the macro easier to leave running unattended.
- **Discord rate limits and the 10 MB upload cap**: images and charts are fine; the capture zips are not, hence
  OneDrive.
- **No live channel to Claude**: nothing here lets Claude read Discord. The bot's output is for you; forward what
  matters.
- **Windows desktop session**: if the PC locks, logs off or sleeps, screenshots fail and the macro can't click.
  The bot should say so (`/screenshot` returning a black or failed grab) rather than post a blank image.
- **A flaky home connection**: `discord.py` reconnects by itself; the watcher must remember its log offset in
  `bot_state.json` so alerts missed while offline are posted afterwards (once, summarised).
