# Adopt Me Macro

A Python desktop macro that watches the Roblox game **Adopt Me** for pet-care
"need" icons (hunger, thirst, etc.) and automatically performs the action
that satisfies each one, controlled from a small `tkinter` GUI.

Everything lives in a single file: `main.py`.

## Documentation

- **This README** - what the project is, how to install and run it, and
  how to use the GUI.
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** - a detailed technical
  walkthrough of how the code works: the workflow lifecycle, the need
  detection and matching algorithms, button detection, every need handler,
  the stop/focus-safety mechanism, and the GUI internals.
- **[FEATURES.md](FEATURES.md)** - a checklist of what's implemented vs.
  still planned.

## How it works, in one paragraph

The macro takes a screenshot, looks at a strip along the top of the screen for
colored circular icons, and compares each one (using shape/contrast, not
raw color) against a folder of reference icons it has seen before
(`needs/`). Once it recognizes a need, it hands it off to whatever satisfies
it - walk to the action buttons and click one, open the backpack and throw
a toy, pet the animal, etc. This repeats in a loop until you press Stop.

## Requirements

- Windows (the automation relies on clicking into an actual Roblox window;
  it won't do anything useful on a headless machine)
- Python 3.9+
- Roblox running with Adopt Me open

Install dependencies:

```bash
pip install numpy opencv-python mss pyautogui pydirectinput pygetwindow
```

## Running it

```bash
python main.py
```

This opens the control panel, pinned to the top-right of your screen. Put
your Roblox window somewhere it won't be covered by the panel.

## The GUI

The top row has three short buttons side by side: two identically-sized
squares on the left and right, and a wide button filling the center.

| Button | What it does |
|---|---|
| 🔄 with a small **1** overlaid (left square, green) | Waits for a need to appear (checking every few seconds), then handles it, once. |
| 🔄 (center, wide, blue) | Same as the button above, but repeats continuously until stopped - respawning once up front, then again every time a need is resolved. |
| ■ (right square, red) | Signals whatever is currently running to stop as soon as it safely can. Always clickable, even mid-action. |
| **Respawn** (purple) | Runs just the respawn sequence (Esc, R, Enter) on its own. |
| **[TEST] Catch / Pet / Ride / Choose** | Runs that one need handler directly, bypassing icon detection - useful for tuning a handler without waiting for its icon to appear naturally. |

The scrolling **Output** panel at the bottom mirrors everything printed to
the console, so you can watch what the macro is doing/deciding in real time.

If the macro loses track of what it's looking at (a workflow stops itself
because Roblox lost focus, or it flags something as a "new need" that it
shouldn't), see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) - it explains
the detection and safety mechanisms in detail.

## The `needs/` folder

Reference icons the macro has learned, saved as high-contrast black & white
PNGs (e.g. `need_hungry.png`). This *is* the macro's memory of what each need
looks like - delete a file here and it will ask you to re-name that icon
the next time it sees it. It's the only persistent state this macro has;
there is no config file.

## Debug

Every detection pass writes screenshots into a `debug/` folder next to
`main.py`, so you can see exactly what the macro is looking at:

- `debug_needs.png` - the top-left region scanned for need icons, with a
  circle drawn around every one detected.
- `debug_buttons.png` - the full screen with a numbered circle on every
  detected action button.

Both are overwritten on every pass and are pure debugging output - safe to
delete anytime, and the first place to look whenever detection isn't
finding what you expect (e.g. tune `PURPLE_RANGE` / `BUTTON_BAND_X` /
`BUTTON_BAND_Y` if `debug_buttons.png` shows no or wrong markers - see
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for what each constant does).
