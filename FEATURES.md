# Features

A running checklist of what's implemented vs. still planned. This is meant
to be edited freely as things change - check items off, reword them, add
new ones, delete ones that turn out not to matter.

## Implemented

- [x] Need-icon detection (color-independent, shape/contrast matching)
- [x] Interactive learning of new need icons (prompted by console, saved to `needs/`)
- [x] Hungry / thirsty / dirty / potty / sleepy - walk to the action buttons and click
- [x] Catch - backpack → equip squeaky toy → scroll up + throw x3 → unequip
- [x] Ride - mount a vehicle → walk back and forth
- [x] Walk - walk left-right for a while
- [x] Pet - focus the pet, hold and circle the mouse (implemented, off by default - `PET_ENABLED`)
- [x] Choose - find and click the exact-color choice button (implemented, off by default - `CHOOSE_ENABLED`)
- [x] Automated respawn, including once up front when starting the continuous loop
- [x] Single-cycle run (**[START]**) and continuous loop (**[LOOP]**)
- [x] Cooperative stop via **[STOP]** (`StopRequested`)
- [x] Auto-stop if Roblox loses window focus mid-workflow (`FocusLost`)
- [x] Debug screenshots for need/button detection (`debug/`)
- [x] Tkinter control panel with live console output
- [x] Manual `[TEST]` triggers for Catch / Pet / Ride / Choose
- [x] Manual Respawn trigger

## Planned / ideas

- [ ] Turn on `PET_ENABLED` once the pet handler's been tuned enough to trust in automatic processing
- [ ] Turn on `CHOOSE_ENABLED` once the choose handler's been tuned enough to trust in automatic processing
- [ ] A "task board" / pen-check flow (an earlier draft had placeholder coordinates for this - `TASK_BOARD_POS`/`READY_POS` - that were removed as unused; revisit if this is still wanted)
- [ ] Anything else you're planning - add it here
