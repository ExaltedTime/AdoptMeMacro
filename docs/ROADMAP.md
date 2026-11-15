# Roadmap: from one person's macro to something others can run

The goal is a macro that a stranger can download, set up in under half an hour on their own PC and account, and keep running through the game's updates, with a community that reports problems in a form that can be fixed. The model is Natro Macro (a Bee Swarm Simulator macro with a large user base). What follows is what that takes, in order, with what is done by whom and how each step is known to be finished.

This file is a plan, not a record of work done. Items name constants and functions as they are today; sizes are rough (S: a day or two, M: about a week, L: weeks). Nothing here has been tried on a second PC: finding out what breaks there is the first real work (phase 1).

## Where the project is

What is in place and worth keeping:

- **It reads the screen and presses keys.** No memory access, no injection (`pydirectinput`, `pyautogui`, `mss`). This is the line that keeps it a macro and not an exploit, and it stays.
- **It recovers.** Rejoins on a timer and on failure, respawns, closes stray windows and notices, disables a need that keeps failing and gives it another chance.
- **It can be diagnosed from outside.** The moments capture (`start_moments()`, `write_moments_files()`, `zip_capture()`) leaves a zip with frames, the run log, the failures and a README that explains itself; `tools/run_summary.py` reads it.
- **It is tested on real frames.** About 700 tests, CI on every push (pyflakes, `tools/check_constants.py`, mypy, pytest), fixtures from real runs.
- **It has a GUI, a Guide tab and a Discord bot** (start, stop, status, update with rollback, pulling files out of the debug folder).
- **It is documented** (`docs/ARCHITECTURE.md`; AI-written, which the README says).

What stops anyone else from using it:

| Area | State today |
|---|---|
| Account | Setup equips the Alicorn when no pet is out, assumes the helicopter is the favourite vehicle, a locked house of one layout, and a backpack sorted by favourites. The pets, the house and the vehicles are the author's. |
| Screen | Positions are in a 1920 x 1080 reference space (`REFERENCE_WIDTH`, `REFERENCE_HEIGHT`) scaled to the Roblox window. Other sizes were tried on fixtures only (1600 x 900 worked except one crop), never in the game. |
| Crops | The need icons (`ref/needs`, 19) and the popup crops (`ref/popups`, about 90) were cut from one screen. A different render (the real window is scaled and softer than a sharp screenshot) already cost a feature once (the filter rows, run 5). |
| Keys | `HOTKEYS` has two keys that exist only on some keyboard layouts (`ú`, `ô`) and is edited in code. |
| Settings | About 590 constants in `magic_numbers.py`; the GUI has switches for a handful of them. `GUI_GEOMETRY` is a position on the author's monitor. |
| Install | The README is a few lines ("a lot of adjustments"). `requirements.txt` is UTF-16, pins everything and installs `discord.py` for people who do not want the bot. No Python version is stated (CI uses 3.12). |
| Releases | None. No version number a user can quote, no changelog, no installer, no update from the GUI. |
| Support | No issue templates, no FAQ, no place to ask. |
| Code | `main.py` is one file of about 7,300 lines; contributors would have to read it all. |
| Risk | Roblox's and the game publisher's view of macros is not stated anywhere; the capture zips contain other players' names and chat. |

## Principles

1. **Never make the working setup worse.** Every change here is made so that the author's PC behaves as before unless a setting says otherwise (the same rule `click_dialog_button()` follows: the measured position wins unless the button is found elsewhere).
2. **Say what is unseen.** A step that has not run in the game is written down as unverified (see `docs/PLANNED.md`) and stays so until a run shows it.
3. **A failure must leave something to fix it by.** A new user who hits a problem sends one zip; the zip is enough. That is already true for the author's runs and has to stay true for others', without leaking what is not theirs to leak.
4. **Fail early and say why.** A wrong window size, scale or backpack is a message at the start, not 20 failed needs.
5. **Small steps that can be tested.** Each item below says how it is shown to work.

## Phase 0: portable, honest and installable on paper (about 2-3 weeks of work)

Nothing here needs another person. It removes what is plainly the author's.

**0.1 Settings in a file, not in code (M).**
Today: the game config (`load_game_config()`, `update_game_config()`, JSON in the debug folder) holds switches and timers; everything else is a constant.
Plan: a `settings.json` (git-ignored, created with defaults on first start, validated on load: unknown keys reported, wrong types fall back with a warning) for the things a user must be able to change: hotkeys, GUI position and size, which needs are on (`ENABLED_NEEDS`), rejoin (private server or public), capture on or off and its limits, the pet and vehicle choices from 0.2. `magic_numbers.py` stays the home of calibration constants and is not meant to be edited.
Done when: a user can change every item above without opening a `.py` file; a test loads a broken file and gets defaults plus a message; `tools/check_constants.py` knows the new names.

**0.2 Take the author's account out of Setup (M).**
Today: `setup_game()` and `equip_pet()` fall back to the Alicorn; `equip_favorite_vehicle()` takes the first vehicle of the favourites; the house is locked by `lock_house()` with a fixed position.
Plan: settings for "the pet to keep out when none is" (a name looked up in the pets tab, or "the first of the filtered list", which is already what `level_growing_pet` does), "the vehicle for the ride need" (first of the favourites, or by name), and "lock the house in Setup" on or off. A step whose thing is missing logs why and is skipped instead of failing Setup.
Done when: Setup runs on the author's account with the defaults exactly as before (test on the existing fixtures) and a test with no Alicorn in the list ends with a clear line, not a failure.

**0.3 Hotkeys that exist on every keyboard (done in part).**
Done: the keys are settings (the Settings tab's Hotkeys subtab, the game config's `hotkeys`), and every default in `HOTKEYS` is blank, so no key is taken from the user until they choose one; a key that is not on the layout is refused with the reason (`check_hotkey()`) and `hotkey_down()` warns once about a saved one that is not.
Left: with no stop key the first run has no keyboard stop. The first-run wizard (3.1) should ask for one, and the README should say the red button and the mouse in a corner of the screen stop the macro.

**0.4 Requirements and Python (S).**
Plan: `requirements.txt` in UTF-8, split into `requirements.txt` (the macro) and `requirements-bot.txt` (`discord.py` and what only the bot needs), a stated Python version (the one CI runs) and a note that Windows is required (the input and window code is Windows-only; the tests stub it so CI runs on Linux). Ranges instead of exact pins where a pin is not needed, so a user with another Python is not blocked.
Done when: a fresh virtual environment on Windows installs the macro without the bot and `python main.py` opens the GUI.

**0.5 A README a stranger can follow (S).**
Not written by Claude into `README.md` without the owner's say-so (the file is the owner's). Draft it as `docs/README_DRAFT.md` and let the owner move it: what the macro does and does not do, requirements (Windows, Python, Roblox in a window at a stated size, the game's UI settings), install, first run, what the Guide tab says, where to ask, the risk note from 5.1, the licence.
Done when: someone who has not seen the project gets from the page to a running loop in the game, and the author watches and writes down every place they stop.

## Phase 1: find what breaks on another PC (about 2 weeks, mostly waiting on testers)

This phase decides the rest. Everything is a guess until it runs somewhere else.

**1.1 Recruit 5-10 testers with different setups (owner).** Different monitors and scales (100%, 125%, 150%), resolutions (1366 x 768, 1600 x 900, 1920 x 1080, 2560 x 1440), windowed and fullscreen Roblox, graphics quality levels, accounts with different pets, houses, vehicles and a different avatar. Ask for Windows 10 and 11 both.

**1.2 A preflight that tests the environment (M).**
Plan: `preflight()` run at the first loop and from a Debug button, reporting each check as a line the user can read: Roblox found and in front; window size and which reference scale that is; Windows display scale (DPI awareness is already handled in `roblox_rect()`, so this checks it did); the need bar found; the backpack openable and its tabs found by crop; the house's Exit Home button; the macro's own window not over the game. A check that fails says what to change ("Roblox is 1280 x 720; use 1600 x 900 or larger"). Optional: save one annotated screenshot.
Done when: it runs on the author's PC with every check green, on the fixtures (including the 1600 x 900 renders) with the expected result, and a deliberately wrong frame fails the right check.

**1.3 A diagnostics zip made for strangers (S-M).**
Today: the moments zip holds full frames: other players' names, chat and the author's own screen.
Plan: a "Make a bug report" button that zips the last run's log, config (without the private server link, already left out), preflight result and a few frames, with the chat area and the name pills blurred (the regions are known: the chat box, `find_pet_pill()`-style pills, the name cards), and a note listing exactly what is inside. The long-run moments capture stays for the author and is off by default for others (a setting from 0.1).
Done when: a test shows the regions of a real frame are blurred in the output and a README in the zip says what it holds.

**1.4 Crops that survive another render (M).**
Today: the need icons and popup crops are matched at the reference scale from one sharp or one real-window source; a second render can score under a threshold (run 5).
Plan: for each crop, record the score on every fixture render available (the sharp screenshot, the real window, the 1600 x 900 fixtures) in a table (`tools/` script); where a crop falls near its threshold, add a second crop from the other render the way the filter rows have `_soft` crops, or match on a blurred, clipped copy. Ask the testers' preflight for the scores of their screens (1.2) so the table fills with real renders.
Done when: the table exists, every crop scores clear of its threshold on every render in it, and a new tester's score below the threshold is a line in the preflight, not a silent miss.

**1.5 Triage list (owner and Claude).** After a week of tester runs: every failure sorted into "their setup" (a preflight check or a setting), "a crop or position that does not travel" (1.4) and "the game does something else for them" (new work). This list orders phases 2 and 3.

## Phase 2: packaging and updates (about 2 weeks)

**2.1 One way to run it that is not "install Python" (M).**
Options, in the order worth trying: (a) a release zip with an embedded Python and a launcher (`.bat`), no installer and no antivirus trouble; (b) a PyInstaller build, which users like and some antivirus products flag. Start with (a); try (b) only if testers ask, and check it against a few products before shipping it.
Done when: a Windows machine with no Python runs the zip's launcher to the GUI.

**2.2 Versions and releases (S).**
A version string in one place (it can come from the tag; `macro_version()` shows the commit today), shown in the GUI title and the status, GitHub releases cut from tags with a changelog written for users (what changed, what to re-check), and the known issues listed in the release notes.

**2.3 Updates from the GUI (M).**
Today: the bot's `/update` pulls `main` with a plan, a restart check and `/rollback` (see ARCHITECTURE, "Update and rollback"). A user without the bot has nothing.
Plan: on start, one request to the releases page for the newest tag (off by a setting; no personal data sent), a line in the GUI when there is a newer one with the changelog link, and the release zip replaced by hand or by a small updater that keeps `settings.json` and the debug folder. No automatic update while a run goes on.
Done when: with a faked newer tag the GUI says so; the updater keeps the user files on a test folder.

**2.4 Game updates: a fast path (S-M).** Adopt Me changes its UI and events. A patch note in the releases ("works with game version X as of date Y") and a `tools/` script that checks the crops against a fresh capture and prints which ones no longer match, so that a fix after a game update takes an hour, not a day.

## Phase 3: onboarding that works without the author (about 2 weeks)

**3.1 A first-run wizard (M).** The GUI's first start walks through: the requirements, the preflight (1.2), the pet and vehicle settings (0.2), the hotkeys, whether to turn the Discord bot on, and a dry run (one need, watched). It writes `settings.json` and is skipped afterwards, with a button to run it again.

**3.2 The Guide tab as the manual (S).** The Guide tab already lists the keys and switches. Extend it with the setup requirements, what each preflight message means, the common failures and their cause (from the triage list, 1.5), and where to report a problem.

**3.3 Honest error messages (S-M).** Review the run log lines users will read: the ones that name a constant or a function (`FAILURE: ... handler could not resolve it`) get a second line with a cause and an action when the cause is known (for example, "the Task Board did not open; the macro window may be covering its sign"). The technical line stays for reports.

**3.4 A bot guide (S).** `docs/` has the bot's reference; add a short "set up the Discord bot" page (the token, the owner id, the channel, what to keep private) with the security notes in front, since users will run it with their own token.

## Phase 4: community and support (ongoing, owner-led; the docs and templates by Claude)

- **A place to ask.** A Discord server (or GitHub Discussions to begin with) with channels for help, bug reports, suggestions and announcements. One person answering in the first weeks decides whether people stay.
- **Issue templates** that ask for the version, the preflight output, the Windows and monitor facts, and the bug-report zip from 1.3. A reply template for "send the zip".
- **A FAQ and a short video** (install, first run, the three failures everyone hits). The video is the owner's; the script and the FAQ text can be drafted.
- **Contributing guide.** How to run the tests and CI checks locally, how to add a need or a popup crop (the existing pattern: a crop in `ref/popups`, an entry in `POPUP_TEMPLATES`, a fixture, a test), the rules this repository already follows (docs name constants and not their values, no rhetorical questions in docs, `tools/check_constants.py` clean).
- **Credit and tone.** A thanks list in the releases for the people whose zips fixed something. This is cheap and it is what keeps testers.

## Phase 5: code health for contributors (in parallel from phase 2, about a month of small steps)

- **Split `main.py`.** Along the seams that already exist in its sections (window and coordinates, detection, input primitives, needs and handlers, setup, pet pen and daily tasks, GUI, moments and capture, loop). `tests/conftest.py` already loads `main` as one module; moving a section means keeping `main.<name>` working (re-exports) until the tests are moved, one section at a time, each a pull request that changes no behaviour.
- **Calibration data out of the code path.** `magic_numbers.py` has 590 names; group them by area into several modules or tables, keep `cfg.NAME` working, and keep `tools/check_constants.py` as the guard.
- **A type check on more of the code.** mypy runs on `main.py` and `scripts/bot_lib.py`; widen it as sections are split.
- **Archive stays archive.** `archive/` holds what is not used and is not tested; a change that wants something from it moves it out first.

## Phase 6: risk, legal and trust (before any public announcement)

Decisions for the owner, with what each needs:

1. **The risk note.** The macro is input-only and does not touch the game's memory. That is a fact about the design, not a promise about how the game's operator treats it. Read the current Roblox terms and the game's rules on automation yourself, decide what to say (at least: use at your own risk, bans are possible, no support for rule-breaking uses, an alt account suggestion), and put it first in the README and in the first-run wizard. The wording is not something to take from this file.
2. **Names and logos.** A project name that says it is a fan tool (not "Adopt Me ..." as if official); no game art in the repository beyond the crops and fixtures that tests need (the crops are small and used for matching; whether to ship them is a decision to make with the risk note in front of you).
3. **Privacy.** What the macro stores (`debug/`, the captures, the config), what leaves the PC (nothing by itself; the bot posts only to the user's own Discord; the optional update check), stated in one page. Captures contain other players' names and chat: blurred in the bug report (1.3), a warning on the long capture.
4. **The Discord bot's keys.** The token and owner id in `bot_config.json` (git-ignored), the fixed command list and the single owner already keep it narrow; the setup page (3.4) repeats what a user must never do (share the token, give the bot a channel others can use).
5. **Licence.** The repository is under the AGPL. That is a valid choice and it makes forks share their changes; it also puts some contributors off and means a hosted fork has to publish its source. Keep it or change it deliberately, before there are contributors whose agreement a change would need.
6. **Antivirus and SmartScreen.** Input-sending programs and unsigned executables get flagged. Ship the zip-with-Python form first (2.1), and say in the README what to expect. A code-signing certificate is a cost to weigh only if an `.exe` is later wanted.

## Order and dependencies

```
Phase 0 (portable) -> Phase 1 (find what breaks) -> triage (1.5)
                                   |                     |
                        Phase 2 (packaging)    Phase 3 (onboarding)
                                   \\                    /
                                 Phase 6 decisions (before announcing)
                                           |
                                 Phase 4 (community)
Phase 5 (code health) runs alongside from phase 2.
```

Phase 0 and the owner's recruiting for phase 1 can start together. Phase 6 is decided during phase 1-2 and must be done before anything is announced publicly.

## Who does what

| Claude | Owner |
|---|---|
| Phase 0 (settings, account assumptions, hotkeys, requirements, README draft) | Decides defaults and the wording of the risk note |
| Preflight, bug-report zip, crop score table (phase 1) | Recruits and talks to testers; runs the long game sessions only the author's account allows |
| Packaging scripts, version and update check, changelog drafts (phase 2) | Cuts releases, tests the zip on a clean PC |
| Wizard, Guide text, error messages, bot guide (phase 3) | Reads them as a stranger would |
| Issue and reply templates, FAQ and contributing text (phase 4) | Runs the server, answers people, makes the video |
| The `main.py` split and calibration grouping (phase 5) | Reviews and merges |

## What "good enough to announce" means

- A tester on an unfamiliar PC reaches a running loop from the README alone, in under half an hour, with the preflight green or with a message that says what to change.
- Across the testers' runs, the share of needs resolved is close to the author's (the run summary's resolved and failure counts are the measure; set the number from the testers' actual runs, not in advance).
- A bug report is one zip, it holds nothing that identifies other players, and the triage turns it into a fix without a second round of questions.
- A game update that breaks a crop is found by the script in 2.4 and fixed in a release within a few days.
- The risk note, the privacy page and the licence decision are written.

## What is not in this plan

- **Anything that reads or changes the game's memory, or injects code.** The project's line is input and screen only.
- **A hosted service.** The macro runs on the user's PC; the bot runs on the user's PC with their token.
- **Other platforms.** Windows only, as the input and window handling are.
- **A promise about scale.** Natro Macro's reach came from years, several maintainers and a very large game audience. This plan makes the project usable and supportable by strangers; how many there are is not something it can set.
