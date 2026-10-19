# Add next

- [ ] Add logic to the minigames
- [ ] Tune the timings and make the clicks not hard-coded
      
# Expansion

- [ ] Trade helper
- [ ] Auto-pen/neon maker

# Claude

Suggestions from reading through the code, none of them started. Roughly in order of payoff within each group.

## Code

- [ ] The teleport and walk handlers are already table-driven; the button handlers, `catch` and `pet` could be too (a list of steps instead of code), which also makes the timing tuning item easier

## Reliability

- [ ] Ghost Gallery in the game: whether the score-feedback skip and the "!" marks keep the macro off the pet and the fog, and how the boss phase does; none of it was seen in a run after the changes.
- [ ] Find on screen the clicks that are fixed pixels now, so a different backpack layout, a new item row or a UI update doesn't break them: the vehicle / toy / pet items and Equip button in `equip_favorite_vehicle()` and `CatchNeedHandler` (`RIDE_FIRST_VEHICLE_POS`, `RIDE_EQUIP_POS`, `CATCH_SQUEAKY_TOY_POS`, `CATCH_EQUIP_POS`, `CATCH_UNEQUIP_POS`; the Equip button moves with the item panel), the teleport clicks (`GENERAL_TELEPORT_POS_*`, `TELEPORT_*_TAB_POS`), the lure clicks (`LURE_NEW_POS_*`), the setup menus (`SETUP_*_POS`), backpack categories and settings. Where something has a distinctive look, a `ref/` crop of it plus `template_score()` finds it, as the stray windows and the Play button already do, with the fixed pixel as the fallback. The dismiss clicks of the paycheck (`PAYCHECK_DISMISS_POS_*`), the minigame popup (`MINIGAME_POPUP_*_POS`), Okay and the rejoin Play button can be the centre of the matched crop, so they follow the popup if it moves
- [ ] Confirm the teleport steps really ran by checking against screenshots: after each teleport, backpack click and walk, compare the screen with what's expected (the backpack panel is open, the view changed after the teleport, the sick need's "Ask Doctor Heart" dialog - `sick_dialog` - is up after the doctor's prompt is clicked) and retry or log a failure when it isn't. Run 3 had a dealership need (17:51) whose sequence never teleported and a sick need (17:09) that never cleared, and nothing in the log says which step was skipped
- [ ] Navigate to the hospital (sick) by centering the camera on its door and moving forward, instead of timed key holds: find the door on screen, turn the camera until it is in the middle, walk forward until the white screen of going indoors shows (an earlier attempt that stepped towards the door's light and panes was removed; it fired on shop windows in the plaza)

## Stitches' Spotlight

- [ ] Check the first player in the game (`play_stitches_spotlight()`): that the left and right arrow keys turn the camera towards the middle as `STITCHES_TURN_KEYS` assumes, that D then keeps the character on the ring, and how the ring line is found when the room is red. Then the searchlight (hide behind planks) and the candy corn off the ring. The key `E` (a speed boost, by the game's message) is not used.

## Hauntlet 2


- [ ] Check the walk in the game and tune it: whether a key held by the macro (`HAUNTLET_STRAFE_DURATION` per door spacing, `HAUNTLET_WALK_MAX`) carries the character through the side doors as a human's keys did, and how to walk a fork's two doorways (rooms 10 and 20 had them at different distances, so one strafe time probably won't do for both). `HAUNTLET_FORK_EVERY` assumes every tenth room is a fork; room 30 would show whether that holds
- [ ] Door kinds (`hauntlet_door_kind()`): the third layer's spiked doors read as boarded and some dark doors and side-door locks are missed (63 of 75 doors right in one run). More captures, labelled by hand, would show where the thresholds are off; and whether the dark door with the glowing eyes is the one with the ghost behind it (the phone's red arrow would show it)
- [ ] The Hauntlet 2 decision logic is the item rules plus a random door so far (`choose_hauntlet_door()`, `hauntlet_items_for()`): risk-based picking - the ghost door by kind (boarded, dark, mystery), how many hearts to risk - is what is left; so is checking that an item was used (the slot's count goes down), and a use for the gold key and the wand
- [ ] Fork rooms (10 and 20): both doorways are found in 19 of 47 frames of three captures (one whole opening plus a narrow piece of the lit one); the rest show one doorway, from the entrance, and the walk to it is untested in the game. The signs over the doorways (cream boards with an item picture) and the circles in them could find a doorway that is not dark inside.
- [ ] Hearts and items left out by the readers: a row of only black hearts, the cyan temporary heart, the wand's picture and the counts above 2
