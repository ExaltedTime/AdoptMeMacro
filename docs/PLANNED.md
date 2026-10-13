# Add next

- [ ] Pet focusing for choose and pet sometimes breaks
- [ ] Add logic to the minigames
- [ ] Tune the timings and make the clicks not hard-coded
      
# Ideas from Natro Macro

- [ ] Notify somewhere when something breaks (Natro posts disconnects, errors and screenshots to a Discord webhook), so an unattended failure doesn't wait for you to look

# Expansion

- [ ] Trade helper
- [ ] Auto-pen/neon maker

# Claude

Suggestions from reading through the code, none of them started. Roughly in order of payoff within each group.

## Code

- [ ] The teleport and walk handlers are already table-driven; the button handlers, `catch` and `pet` could be too (a list of steps instead of code), which also makes the timing tuning item easier
- [ ] Use the `logging` module instead of `print("[debug] ...")`, so levels and the console/file split come for free and the on-screen console can hide debug lines
- [ ] Hard-coded click positions (backpack categories, settings, setup) break on a different layout or UI update; where something has a distinctive look, find it (colour or template match) instead, as the Play button and popups already do

## Reliability

- [ ] Hard-coded click positions that could be found on screen instead (each is a fixed pixel, so a different backpack layout, a new item row or a UI update breaks it): the vehicle / toy / pet items and Equip button in `equip_favorite_vehicle()` and `CatchNeedHandler` (`RIDE_FIRST_VEHICLE_POS`, `RIDE_EQUIP_POS`, `CATCH_SQUEAKY_TOY_POS`, `CATCH_EQUIP_POS`, `CATCH_UNEQUIP_POS`), the teleport clicks (`GENERAL_TELEPORT_POS_*`, `TELEPORT_*_TAB_POS`), the lure clicks (`LURE_NEW_POS_*`) and the setup menus (`SETUP_*_POS`). Each would be a `ref/` crop of the button plus `template_score()` to find it (the same way the stray windows work), falling back to the fixed pixel. The Equip button in particular moves with the item panel
- [ ] The dismiss clicks in the paycheck (`PAYCHECK_DISMISS_POS_*`), minigame popup (`MINIGAME_POPUP_*_POS`), Okay and rejoin Play buttons could be the centre of the matched crop instead of a fixed pixel, so they follow the popup if it ever moves
- [ ] Roblox itself closed twice in run 328 (00:37 and 03:35, "Roblox lost focus" then "Roblox isn't running", both a few minutes after a sick need started) with no recording to show why. Find the cause: keep a rolling recording or the last few screenshots to save when Roblox disappears, log Roblox's memory use / the Windows event log entry at that point, and check whether it only happens during sick or at the half-hour minigame time
- [ ] Confirm the teleport steps really ran by checking against screenshots: after each teleport, backpack click and walk, compare the screen with what's expected (the backpack panel is open, the view changed after the teleport, the sick need's "Ask Doctor Heart" dialog - `sick_dialog` - is up after `e`) and retry or log a failure when it isn't. Run 3 had a dealership need (17:51) whose sequence never teleported and a sick need (17:09) that never cleared, and nothing in the log says which step was skipped
- [ ] Button detection: in run 1 a potty click at 01:31 did nothing - the icon was still there when the 75s wait ended (FAILURE at 01:31:39), the handler still returned True, and the next check showed potty again (resolved normally at 01:39). Find out why the click missed (`detect_buttons()` mapping the wrong circle, a button that moved, the click landing before the button was ready) - check what `refresh_button_mapping()` found on that attempt, and consider confirming the mapped buttons against the need's name before clicking. A lead: the buttons are named only by their order left to right (`refresh_button_mapping()`), so one missed button (a pet in front of it, a hover colour) shifts every name after it and the wrong button gets clicked - the run log now says `button scan found N of 5` when that happens, so the next occurrence shows whether it was this
- [ ] Button click that does nothing because the pet is too far away: run 1's `sleepy` at 19:58 (FAILURE `sleepy still showing after 75s`, `not cleared: sleepy`) was this - the game answers a button click with "only babies, pets, and dolls can use this" when the pet isn't close enough. Check for that message (a crop of it, matched the way the popups are) right after a button click and treat it as a failed click at once instead of waiting out the 75s: log it, and either walk back to the buttons and click again or fail the need. It also tells the click apart from the other ways a button click can do nothing (the wrong button mapped - see above)

## Hauntlet 2

- [ ] Check the walk in the game and tune it: whether a key held by the macro (`HAUNTLET_STRAFE_DURATION`, `HAUNTLET_WALK_MAX`) carries the character through the side doors as a human's keys did, and how to walk a fork's two doorways (rooms 10 and 20 had them at different distances, so one strafe time probably won't do for both). `HAUNTLET_FORK_EVERY` assumes every tenth room is a fork; room 30 would show whether that holds
- [ ] Tell plain doors from boarded ones (`hauntlet_door_kind()` finds locked, "???" and dark doors only): one captured run was too little for a rule that holds in all three layers - a few more captures of rooms, labelled by hand, or saved door pictures matched the way the items are. Also whether the dark door with the glowing eyes is the one with the ghost behind it (the phone's red arrow would show it)
- [ ] The decision logic (`choose_hauntlet_door()`): hearts and items are already read and logged on each choice; risk-based picking, using the cyan potion on a high-risk door and the key on a lock, is what is left
- [ ] Hearts and items left out by the readers: a row of only black hearts, the cyan temporary heart, the wand's picture and the counts above 2
