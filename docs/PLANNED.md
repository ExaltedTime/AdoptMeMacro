# Add next

- [ ] Add logic to the minigames
- [ ] Tune the timings and make the clicks not hard-coded
      
# Expansion

- [ ] Trade helper
- [ ] Auto-pen/neon maker

# Claude

Suggestions from reading through the code, none of them started. Roughly in order of payoff within each group.

## Reliability

- [ ] Ghost Gallery in the game: whether the score-feedback skip and the "!" marks keep the macro off the pet and the fog, and how the boss phase does; none of it was seen in a run after the changes.
- [ ] Find on screen the clicks that are fixed pixels now, so a different backpack layout, a new item row or a UI update doesn't break them: the vehicle / toy / pet items and Equip button in `equip_favorite_vehicle()` and `CatchNeedHandler` (`RIDE_FIRST_VEHICLE_POS`, `RIDE_EQUIP_POS`, `CATCH_SQUEAKY_TOY_POS`, `CATCH_EQUIP_POS`, `CATCH_UNEQUIP_POS`; the Equip button moves with the item panel), the teleport clicks (`GENERAL_TELEPORT_POS_*`, `TELEPORT_*_TAB_POS`), the lure clicks (`LURE_NEW_POS_*`), the setup menus (`SETUP_*_POS`), backpack categories and settings. Where something has a distinctive look, a `ref/` crop of it plus `template_score()` finds it, as the stray windows and the Play button already do, with the fixed pixel as the fallback. The dismiss clicks of the paycheck (`PAYCHECK_DISMISS_POS_*`), the minigame popup (`MINIGAME_POPUP_*_POS`), Okay and the rejoin Play button can be the centre of the matched crop, so they follow the popup if it moves
- [ ] Confirm the teleport steps really ran by checking against screenshots: after each teleport, backpack click and walk, compare the screen with what's expected (the backpack panel is open, the view changed after the teleport, the sick need's "Ask Doctor Heart" dialog - `sick_dialog` - is up after the doctor's prompt is clicked) and retry or log a failure when it isn't. Run 3 had a dealership need (17:51) whose sequence never teleported and a sick need (17:09) that never cleared, and nothing in the log says which step was skipped
- [ ] Navigate to the hospital (sick) by centering the camera on its door and moving forward, instead of timed key holds: find the door on screen, turn the camera until it is in the middle, walk forward until the white screen of going indoors shows (an earlier attempt that stepped towards the door's light and panes was removed; it fired on shop windows in the plaza)
- [ ] `/inventory` in the game (`inventory_tour()`): whether the wheel scrolls the pets tab now (the first run ended after one picture: it was given 1/120 of a notch a step; if it still does not, dragging the scroll bar would be the other way), whether the step measured on the first notch (`INVENTORY_STEP_PIXELS` of the list, so each picture overlaps the next; 300 notches a step skipped most of the list) comes out right in the game (the debug lines say how far a notch went), and `INVENTORY_SCROLL_POS` (an estimate); the pictures it sends are the first sight of it.
- [ ] Lossy screenshots still in the repo: `tests/popups/cafe_banner_friend_pill.png` (it reached the chat as a `.webp` and was converted to PNG, which does not bring the detail back; the backpack check's counts on it are from lossy pixels). Replace it with a lossless 1920x1080 screenshot of the same moment (`debug/failures/.../screen.png` has one) and recheck `BACKPACK_NORMAL_*` on it. Any other fixture that came from a chat image or a shrunk copy belongs here too; the ones from the capture zips (`tests/exit_hub/`, `tests/trades/`, `tests/hauntlet_2/doors/room3_*`) are lossless.
- [ ] `/sloop` in the game: leaving the Trading Hub (`leave_hub_if_in()`) and a minigame (`exit_minigame()`; the exit dialog, the Exit Minigame button and the GO HOME text are crops of real screenshots, but only of one minigame and of the hub island in two views - the other two minigames and the house's Exit Home button, which must not be taken for the red one, are unchecked).
- [ ] `/inventory` scrolling: the mouse is moved to `INVENTORY_SCROLL_POS` with `hover_move()` now (it was a plain move, so Roblox may not have seen the pointer there); if the list still does not move, the position may be off the list: an expanded backpack screenshot would give it.
- [ ] Hauntlet 2: three changes from the runs of rooms 8 and 9 - a third TV off the screen's edge is inferred, a player in a door's circle stands there for `HAUNTLET_CIRCLE_WAIT` instead of being unstuck out of it, and the normal backpack's colour check wants its frame at both sides; watch whether "no doors found" and "the walk got nowhere" marks stop, and whether the game-over rooms (6, 8, 9) came from the door choices (not looked into).

## Stitches' Spotlight

- [ ] Check the player in the game (`play_stitches_spotlight()`): send a run capture and the debug lines of its log. It never worked in the first version; the second (a controller that learns the steady turn, tests which arrow key turns which way and presses the speed boost) is untried. Still to do after that: the searchlight (hide behind planks) and the candy corn off the ring.

## Hauntlet 2


- [ ] Check the walk in the game and tune it: whether a key held by the macro (`HAUNTLET_STRAFE_DURATION` per door spacing, `HAUNTLET_WALK_MAX`) carries the character through the side doors as a human's keys did, and how to walk a fork's two doorways (rooms 10 and 20 had them at different distances, so one strafe time probably won't do for both). `HAUNTLET_FORK_EVERY` assumes every tenth room is a fork; room 30 would show whether that holds
- [ ] Door kinds (`hauntlet_door_kind()`): the third layer's spiked doors read as boarded and some dark doors and side-door locks are missed (63 of 75 doors right in one run). More captures, labelled by hand, would show where the thresholds are off; and whether the dark door with the glowing eyes is the one with the ghost behind it (the phone's red arrow would show it)
- [ ] The Hauntlet 2 decision logic is the item rules plus a random door so far (`choose_hauntlet_door()`, `hauntlet_items_for()`): risk-based picking - the ghost door by kind (boarded, dark, mystery), how many hearts to risk - is what is left; so is checking that an item was used (the slot's count goes down), and a use for the gold key and the wand
- [ ] Fork rooms (10 and 20): both doorways are found in 19 of 47 frames of three captures (one whole opening plus a narrow piece of the lit one); the rest show one doorway, from the entrance, and the walk to it is untested in the game. The signs over the doorways (cream boards with an item picture) and the circles in them could find a doorway that is not dark inside.
- [ ] Hearts and items left out by the readers: a row of only black hearts, the cyan temporary heart, the wand's picture and the counts above 2

## Trade listings

`/trades` and `/decline` are built (see ARCHITECTURE.md); what is left:

- [ ] `/trades` and `/decline` in the game: they are built from one human's run (60 frames, 19 clicks) and tested on those frames; the scroll step (`TRADES_SCROLL_CLICKS` notches) and what the list does at its end were never seen, nor a listing with no offers, nor a list of more than three offers.
- [ ] Find the Trading Hub Phone in the backpack by searching for it (the search box) instead of by its slot: the Special row's order changes with the favourites filter Setup turns on, so `TRADES_PHONE_SLOT_POS` is the second slot of that row and `TRADE_HUB_PHONE_POS` of `travel_to_trade_hub()` the first. The phone does not need the Trading Hub.
- [ ] Creating listings (POST A LISTING): not started; `/trades` and `/decline` only look at offers and decline them.

## Trade value helper

Read a trade window and say whether the trade is worth it. Not started; nothing is built.

- [ ] **Value source first.** Neither adoptmewfl.com nor elvebredd.com publishes a feed that was found (the former serves its value files scrambled and disallows `/api` in `robots.txt`; the latter did not answer a direct request). Ask the owners for a feed or for permission to read the calculator automatically. Until then a values table kept by hand (a file, one value per pet and flag combination) is the fallback, or the helper only shows what it read and the calculator is used by hand.
- [ ] **Read the trade window** from real screenshots (both sides, several pets, ordinary, neon and mega): the pets' pictures matched against a reference crop per pet (or the name label read, if the window shows one), and the age, fly, ride, neon and mega marks each with a detector of its own. Needs screenshots of a trade in progress; details hidden until a pet is hovered cannot be read.
- [ ] **Decide.** Sum each side from the values table and compare with a margin; the helper posts its verdict (and a screenshot) to Discord and does not accept or decline by itself at first, since a misread would cost real pets.
- [ ] **Optionally drive the calculator.** Type the pets into the site in a browser like a visitor and read its verdict off the screen; this needs a second window or monitor (the macro stops when Roblox loses focus), the pet names exactly as the site expects them, and the owners' agreement.

