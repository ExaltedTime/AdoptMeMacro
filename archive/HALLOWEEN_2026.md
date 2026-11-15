# Halloween 2026 (archived)

The Halloween event's minigames (Hauntlet 2, Ghost Gallery, Stitches' Spotlight, candy corn) are no longer part of `main.py`. Their code is `archive/Halloween2026.py` (its pictures are in `archive/Halloween2026_ref/`, its own keys are `KEYS_EVENT`), which imports `main` as `m` and uses its screen, input and detection helpers; its constants are in that file, its tests in `archive/tests/halloween2026/` and its crops in `archive/Halloween2026_ref/halloween/`, `archive/Halloween2026_ref/overlays/` and `tests/halloween*/`. Nothing in `main.py` calls it. The sections below are what ARCHITECTURE.md said while the event ran; hook names in them (`minigame_popup()` inside `unscrew()`, `play_minigames`, `exit_minigame()`) no longer exist in `main.py`.

## Popups and playing

**Minigame popups.** Halloween has minigames (`MINIGAME_LABELS`) that offer to teleport you -
"Ghost Gallery is starting soon! Teleport there now?", "Hauntlet 2 is
starting soon!..." and "Stitches' Spotlight is starting soon!..." - in the same popup layout.
`detect_minigame_popup()` recognises that layout by its two buttons (each piece of the Yes green looked at on its own, with the red beside *it*: a popup over the open backpack, whose buttons are green too, was missed when all the green on the screen was averaged - `tests/popups/` has real screenshots of popups and of other green buttons): neither
color is unique alone (Yes green = `GREEN_BUTTON_COLOR`, No red = the
Exit Home button), so it needs the Yes green (`GREEN_BUTTON_COLOR`, at
least `MINIGAME_POPUP_MIN_BUTTON_PIXELS` px) with the No red
(`MINIGAME_POPUP_NO_COLOR`) within `MINIGAME_POPUP_NO_MAX_DX` / `_DY` px to
its left, **under the popup's own "Teleport there now?" line** (a crop, `archive/Halloween2026_ref/popups/minigame_popup.png`, `POPUP_TEMPLATES["minigame_popup"]`; identical in the popups of all of them). The crop was added after a run in which 30 "popup dismissed (unknown)" lines, each a click on the popup's "do not show again" and No places, and 19 "can't spawn vehicle" windows came with a cascade of failed needs (choose 10 of 10, the buttons' needs 15 in a row): the dialogs taken for the popup were the macro's own teleport dialogs ("Teleport to the Nursery to adopt pets?", "Teleport to The Vehicle Dealership to see vehicles?": a green Yes and a red No, clicked by `teleport_to()` and "dismissed" by the in-task check in the middle of it), as the screenshots `tests/popups/teleport_*.png` show - the detector before the crop fired on both. The one dialog left out on purpose is the sick need's own "Ask Doctor Heart to heal your family?" dialog, which has the same Yes/No look but is the sick handler's to answer (its Yes is `SICK_FINAL_CLICK_POS`): `ref/popups/sick_dialog.png`, within its `POPUP_TEMPLATES` box, makes the detector ignore it. (Before that, the periodic in-task check (`UNSCREW_TASK_INTERVAL`) took it for a minigame popup and clicked its decline positions in the middle of the sick sequence - `unknown` popups clustered in the sick handler in the logs.) `identify_minigame()` then says which one it is: it matches the
first line of the title (`MINIGAME_TITLE_BOX`) against the crops in
`archive/Halloween2026_ref/halloween/` (`cv2.matchTemplate`, best score at least
`MINIGAME_TITLE_MATCH_THRESHOLD`); a minigame's name is its file name,
lower-cased (`ghost_gallery.png` → `ghost_gallery`, `hauntlet_2.png` → `hauntlet_2`), so a new one is just a
new crop - plus an entry in `MINIGAME_LABELS` and a player in
`MINIGAME_PLAYERS`.

Each minigame has a switch in the game config (`hauntlet_2_enabled`,
`ghost_gallery_enabled`, `stitches_spotlight_enabled`, all **off** by default; "Play ..." checkboxes on the
Settings tab). A minigame that is recognised but has no player in `MINIGAME_PLAYERS` gets "(no player yet)" on its checkbox, and its popup is declined like a switched-off one even if the switch is on. `minigame_popup()`:
- **switched off, not recognised, or with no player yet** - clicks `MINIGAME_POPUP_DONT_SHOW_POS`
  ("Do not show again this session") and `MINIGAME_POPUP_NO_POS`;
- **switched on, but a need is in its end wait** (`wait_until_need_gone()`, or `walk_alternating()` watching for its need; `end_wait()` marks it in `state.end_wait_need`) -
  the popup is left up for the need to clear and count as resolved, at most `MINIGAME_POPUP_END_WAIT` seconds from when it was first seen (`state.popup_held_since`):
  `minigame_popup()` returns `"waiting"` and `unscrew()` returns at once, before the paycheck check (the popup's green Yes would be taken for a paycheck's button);
- **switched on** - `play_minigame()` clicks Yes (`MINIGAME_POPUP_YES_POS`) with `accept_minigame_popup()`
  and plays it, as below. The click is checked: within `MINIGAME_TELEPORT_WAIT` seconds (looking every `MINIGAME_TELEPORT_POLL`) there
  must be the white screen of a teleport or a view that has changed (`screen_changed()`: the shrunk screen differs from the one at the click by
  more than `MINIGAME_TELEPORT_CHANGE` grey levels). If not and the popup is still up (`detect_minigame_popup()`) the click didn't take and Yes is clicked
  again, up to `MINIGAME_YES_TRIES` clicks; if the popup is gone and nothing teleported the start has failed at once - a logged failure and a respawn,
  no capture and no wait for a game that isn't there (run 6: Hauntlet 2's Yes clicked, the character still in the house 114 s later). Minigames are time-sensitive, so this also happens
  when the popup turns up while a handler is waiting in
  a handler's waits (see [Per-cycle checks while a task
  runs](ARCHITECTURE.md#need-handlers)): the minigame is played right
  then and the handler abandoned (`TaskInterrupted`). Every held key and the mouse button are released first - at the start of `play_minigame()`, since the handler may have been holding one (a walk, the pet swipe) when it was interrupted, and again as the interruption is raised. Where it left the
  character is unknown afterwards, and the next check sees what is still
  needed.

*Playing.* The macro's own window is sent to the back while a minigame is played and brought back after it, whichever way it ends (it covers part of the screen, and would be in
the recording and in the readers' way). No needs can be seen while a minigame runs, so every minigame ends on the
GAME OVER! screen instead - a red GAME OVER! banner over a green NICE! button,
found by exact color (`detect_minigame_finished()`, checked every
(it is looked for on every look at the screen), which is the same for all of them (the tests use real, lossless screenshots of it for Hauntlet 2 and Ghost Gallery, one with the next game's countdown over the banner: `tests/popups/finish_screen_*.png`, `game_over.png`, and the crops `tests/fixtures/finish_banner.png` and `finish_button.png`; the banner is about eight times and the button about three times the pixels the checks ask for, so no threshold needed moving). Every run ends on it, so reaching it is *finishing*, not winning (a real win, which gives a crypt key, shows the same screen; the macro can't tell them apart): what a run earned is the candy corn on the card. `save_minigame_result()` saves that card (`MINIGAME_RESULT_BOX` of the screen) as `runs/results/run<N>_<minigame>_<time>.png`, the newest `MAX_RESULT_SHOTS` kept, before NICE! is clicked. While it plays, `minigame_popup_checks()` runs `dismiss_stray_windows()` (and `detect_paycheck()` if it closed nothing) at most every `MINIGAME_POPUP_CHECK_INTERVAL`, including during the start waits (`minigame_wait()`) - the in-task `unscrew()` can't, since the minigame is played from it. Then NICE!
(`MINIGAME_FINISH_BUTTON_POS`) is clicked and `MINIGAME_FINISH_WAIT` waited
out (`click_nice_until_gone()`, which looks again and clicks up to
`MINIGAME_FINISH_CLICK_TRIES` times while the finish screen is still up, logging a
failure if it never closes), and the character respawns. If the finish screen hasn't shown after
`MINIGAME_MAX_DURATION` it gives up with a logged failure (and releases every
input). A minigame that ran to the GAME OVER! screen counts as progress for the no-progress rejoin (`state.last_progress`); one that failed - never started, left early, timed out - does not, or a popup every few minutes would keep the no-progress rejoin clock (`NO_PROGRESS_REJOIN_INTERVAL`) from ever running out. Either way it counts as a detection (no needs can be seen while it runs).

*Telling whether it is on.* Each minigame has an overlay along the top of the
screen while it runs (Hauntlet 2: the ROOM counter; Ghost Gallery: the TIME
LEFT / GHOST GALLERY / Score banner; Stitches' Spotlight: TIME LEFT / the title / the Candy Corn count), recognised by a crop in
`archive/Halloween2026_ref/overlays/<minigame>.png` within its box in `MINIGAME_OVERLAYS`
(`minigame_overlay_visible()`, at least `MINIGAME_OVERLAY_THRESHOLD`). A
`MinigameWatch` made by `play_minigame()` is fed every screenshot the player
takes (`minigame_look()`, `minigame_wait()`): once the overlay has been seen,
losing it for `MINIGAME_OVERLAY_LOST_TIMEOUT` means the minigame was left
(game over without the finish screen, kicked out) and it stops there; never
seeing it within `MINIGAME_OVERLAY_START_GRACE` of the start wait means it
never started. Either is a logged failure with that reason, instead of waiting
out `MINIGAME_MAX_DURATION`. The watch also looks for the "Disconnected" dialog (`detect_disconnect()`) in every screenshot: once seen, the minigame stops at once
(logged as a `ended by a disconnect` event, not a failure - the rejoin that follows logs that), where it used to go on for the rest of its time with the dialog up.
- **The start is the overlay, not a time.** Rounds start at fixed times of the hour (in the captured day Hauntlet 2 at about :03:07, :23:07 and :43:07, Ghost Gallery at :12:46, :32:46 and :52:46, a round every 20 minutes each) and the popup's Yes is clicked
  12-52 s before one, depending on what the macro was doing (a need's end wait holds it back by up to `MINIGAME_POPUP_END_WAIT`). A fixed wait from the click is therefore wrong most of the time: in Hauntlet 2's first room the timer is 30 s and crossing the hall takes about 20, and of 21 runs with a fixed 50 s wait the 11 whose Yes came 40 s or less before the round
  were thrown out of room 1 (the macro still waiting) and the 10 that came 43 s or more before it played on. So each player looks for its game's overlay at a short interval (`MINIGAME_START_LOOK_INTERVAL`, `wait_for_minigame_start()`) and acts the moment it shows; `MINIGAME_START_WAIT` is only the most it waits (the overlay came 50.5 s after the Yes in one run), after which it acts regardless.
- `play_hauntlet_2()` waits until the game's overlay shows (at most `MINIGAME_START_WAIT`), then goes through the rooms - reads the screen, picks a door
  and walks to it, room after room - until the minigame finishes or until the overlay says it's over. See
  [Playing Hauntlet 2](#playing-hauntlet-2).
- `play_ghost_gallery()` waits until the game's overlay shows (at most `MINIGAME_START_WAIT`), then finds the ghosts on screen, aims the
  mouse at one and holds the button on it, and on the boss in the boss phase, until the minigame finishes. See
  [Playing Ghost Gallery](#playing-ghost-gallery).
- `play_stitches_spotlight()` waits for the overlay, then holds the walking key and keeps the camera on the middle of the ring until the minigame finishes. See

## Readers and players

### Reading Hauntlet 2

`read_hauntlet_room()` (the HAUNTLET 2 READER section of `main.py`) returns the number on the ROOM panel
at the top of the screen, or `None` when it can't be read. It only reads: nothing here decides or presses a key.
The panel is flat UI that never moves or scales, so the digits are matched exactly: the white pixels
(`HAUNTLET_DIGIT_WHITE_LEVEL`) inside `HAUNTLET_ROOM_BOX` are split into connected pieces, each piece is cut
out, shrunk to `HAUNTLET_DIGIT_SIZE` and compared with the saved digit pictures in `archive/Halloween2026_ref/hauntlet_2/digits/`
(`0.png` ... `9.png`, cut from a real run) by mean difference. The best must be under
`HAUNTLET_DIGIT_MAX_DIFF`; on a 256-frame run (rooms 1-29, three layers of the hotel) every readable frame
matched at 0.0 and the two most alike digits are 43.7 apart. No panel (the white transition, the lobby, a
door close-up that hides it), no digit pieces, more than `HAUNTLET_ROOM_MAX_DIGITS`, or a piece that matches
none of the digits all give `None` rather than a guess. `archive/tests/halloween2026/test_hauntlet_2.py` runs a crop of each of the 29
rooms (`archive/tests/halloween2026/hauntlet_2/room_panels/`) back through the reader.

**Items.** `read_hauntlet_items()` returns the item slots along the bottom of the screen, left to right, as
`HauntletItem(name, count, pos)`. The slots are round white-ringed icons about 115 px apart, found as circles in
`HAUNTLET_SLOT_BAND` (`find_item_slots()`, Hough, radius `HAUNTLET_SLOT_RADIUS`; a slot that has just changed is a
little bigger). Each slot's `HAUNTLET_ITEM_CROP`-px square is matched by correlation, as a grey picture so the slot's
bright blue or dim teal background matters little, against the pictures in `archive/Halloween2026_ref/hauntlet_2/items/<name>/` (a folder per
item, several pictures each - bright, dim, slightly enlarged). The best must reach `HAUNTLET_ITEM_MATCH` or the
item is `unknown`. They were cut from the 382 slots of a 256-frame run: `phone`, `key`, `gold_key`, `cyan_potion`,
`heart_potion`, `red_potion`, plus `backpack` (the lobby's button, which sits where a slot would and is left out,
`HAUNTLET_NOT_ITEMS`); every slot of that run is read as the item it is (60 ms a frame). The wand was not in it, so
it has no pictures yet. A slot that matches none of them reads as `unknown`; nothing is saved. The count is the number
on the purple "x2" badge at a slot's top left (`read_item_count()`): the badge is the purple piece of the right size
(`HAUNTLET_BADGE_*`), its white pieces are the "x" and the digits, each compared with the glyphs in
`archive/Halloween2026_ref/hauntlet_2/counts/` (`x`, `1`, `2` - the only counts in the run, so no other digit is known yet: a glyph that
matches none gives `None`, not a guess).

**Hearts.** `read_hauntlet_hearts()` returns `HauntletHearts(red, black, width, pos)` for the row above the player's head.
Hearts are pink with a black outline and become solid black when lost. The pink ones are found by colour and shape
(`pink_heart_pieces()`; nothing above `HAUNTLET_HEART_MIN_Y`, which keeps the room panel's pink spot out), the row nearest
the middle of the screen is the player's, and the row is then followed outward one `HAUNTLET_HEART_PITCH` x a heart's
width at a time: a cell with a heart-shaped piece or mostly pink is red, a cell that is nearly all pure black (every
channel at most `HAUNTLET_HEART_BLACK_LEVEL` - a doorway or the player's clothes are not that black) is black, anything
else ends the row. A row shorter than `HAUNTLET_HEART_MIN` or longer than `HAUNTLET_HEART_MAX` isn't taken for hearts
(`None`). Across the 256 frames of the run the counts go 3/0, 2/1, 3/1 (a red heart picked up), 2/2, 1/3, 4/0 (a heart potion),
3/1, 2/2, 1/3 and no frame is wrong in between except where it gives `None`. What it does **not** do: find a row of
*only* black hearts (nothing pink to find it by - the run's last frames), see the cyan temporary heart (it ends the row),
or read a frame where the row is hidden (a door close-up, a transition) or where something pink sits next to it (one frame
in the run, a pink door, was declined as too long). One frame can still be wrong or missing, so whatever acts on the hearts should
look at a few frames in a row.

**The timer and the doors.** `hauntlet_timer_visible()` says whether the timer ("00:2x", left of the ROOM panel) is shown:
at least `HAUNTLET_TIMER_MIN_WHITE` white pixels in `HAUNTLET_TIMER_BOX` when it is, none when it isn't - it is hidden in a room's first moments and
while the player passes through a door, which is also when the screen still shows the *last* room's doors. `find_hauntlet_doors()`
finds the three doors of a room from the TVs above them: each TV shows its number (the players standing before that door)
in a light cyan (`HAUNTLET_TV_GLOW`) that no other pixel of the game has in that quantity (in a red room - room 12, three failures "no doors found" - the digits are 140-240 in red and 228-255 in green, so a range that stopped at 225 and 248 saw none of the three: `tests/hauntlet_2/doors/red_room_12_*`; the wider range finds the TVs of two more rooms in the 113 frames of a whole run and loses none, and the rule for the outer two with the middle one hidden takes any two blobs, not only neighbours, as stray glows now sit between); `find_door_tvs()` takes the pieces
of `HAUNTLET_TV_AREA` px (except those in `HAUNTLET_TV_HUD_BOX`, where the room panel and timer glow) and wants three of them in a row (the same height within
`HAUNTLET_TV_ROW_TOLERANCE`, evenly spaced within `HAUNTLET_TV_SPACING_TOLERANCE`), else `[]` - a fork's hall has no TVs, and neither do
the close-ups. **With the camera close** - which is how it looks once the macro stands still in a room - the middle TV is above the top of
the screen or behind the room panel, and only the outer two show; two TVs `HAUNTLET_TV_PAIR_GAP` apart (the real gap was about twice the neighbours') and centred on the
screen (`HAUNTLET_TV_PAIR_OFF_CENTER`; the player stands in front of the middle door) are taken for the outer two, the middle one halfway between. Off centre
they are also taken for the outer two when their gap is `HAUNTLET_TV_PAIR_RATIO` times the side of a number's blob or more (the gap between neighbours is a
fixed multiple of that side, half the outer two's): a balloon in front of the middle TV hid its glow in room 22. Two neighbours (level, `HAUNTLET_TV_SPACING` apart) with room for the third only off the screen's left or right edge give the third there, beyond the edge (`hauntlet_door_kind()` reads no kind for it: None): in room 2 and 3 of three macro-played runs the player stood against a wall with the camera looking along it, and "no doors found" was logged each time (`tests/hauntlet_2/doors/room3_third_tv_off_the_left_edge_*.png`).
Two neighbours (`HAUNTLET_TV_SPACING` apart, level, no lower than the panel) with the third where the room panel is (`HAUNTLET_TV_HUD_BOX`, whichever side puts it there) give that third: the panel hides its glow.
Two neighbours with the third off screen are 350-780 px apart but not centred, and their gap is too small for their size, so they don't pass. The spacing between TVs (330-410 px as the camera zooms) is the picture's scale, so the boxes under each
TV (`HAUNTLET_LOCK_LIGHT_BOX`, `HAUNTLET_DOOR_BOX`, measured at a spacing of `HAUNTLET_DOOR_SPACING`) are scaled with it.
`HauntletDoor(x, y, kind)` gives each door's `kind` from its pixels (`hauntlet_door_kind()`), the first that fits:

- `locked` - the padlock's red light above the door: a patch of pure red pixels (`HAUNTLET_LOCK_LIGHT_RED` is the red level they must pass), none on other doors. The
  dark red outline of the player's hearts, which can be under the door, is a darker red and doesn't count. The light is one small round patch
  (`HAUNTLET_LOCK_LIGHT_MIN` to `HAUNTLET_LOCK_LIGHT_MAX` px, `HAUNTLET_LOCK_LIGHT_ROUND`): a wall painted red beside a door (room 22) is bigger and long;
- `shadow` - a shadow door: dark smoke over the boards with **glowing eyes** (white-pink ovals, a few px each; `hauntlet_shadow_eyes()`). A near-white piece (`HAUNTLET_SHADOW_WHITE`, `HAUNTLET_SHADOW_AREA`) in `HAUNTLET_SHADOW_BOX` whose surrounding ring is dark (mean brightness at most `HAUNTLET_SHADOW_RING`: 46-49 for the eyes of the captured room 6, 73 and more for every other white thing in a door's box - screws, lights, the lock's glare). It is looked for before `dark`, since an opened shadow doorway is dark too. Before it, a shadow door read `boarded` (its smoke and boards looked like planks): in the four captures looked at, 84 frames with doors, only the shadow door's two frames read `shadow`. It is recognised only so far; nothing is done with it yet;
- `dark` - over `HAUNTLET_DARK_FRACTION` near-black pixels (44-74% on the doors that look so, at most 37% on the others);
- `mystery` - the pale grey "???" door, brightness 180-203 where every other door is under 164;
- `boarded` or `plain` - by the planks: the share of the door's pixels on a strong, near-horizontal edge
  (`HAUNTLET_PLANK_*`: the grey picture smoothed, Sobel, edge directions within `HAUNTLET_PLANK_ANGLE` degrees of horizontal). Plain doors
  are 0.03-0.13 (median 0.06), boarded ones 0.11-0.24 (median 0.18); the threshold, `HAUNTLET_PLANK_FRACTION`, is the best one over the
  doors of 25 rooms (the first and second layers separate best, the third a little worse), and picked in turn without one
  room of them it still gets 94% of that room's doors right. It was found by trying measures one after another - blob counts
  for the nails, edge density, edge directions, colour - and this was the one that held across the three layers' lighting.

All of it is judged against what was seen in one captured run, by eye: of all three doors of those 25 rooms, 63 of 75 come out as
labelled (`boarded` / `plain` from the planks are the least sure part: 3 of 22 plain doors read boarded; and
the third layer's spiked doors, 3 of them, come out `boarded`, there being no kind for them yet). The misses: dark doors with
fewer black pixels than the rule wants (4 of 10 read as boarded or plain), and a lock whose light is cut off by the shear of a side door
(2 of 14). It also can't see which door has the ghost behind it. The kinds are for the decision logic to use as far as they go.

### Playing Hauntlet 2

`play_hauntlet_2()` (once the overlay has shown - `MINIGAME_START_WAIT` at most - and a `zoom_out()` - `KEYS["zoom_out"]` held `MINIGAME_ZOOM_OUT_DURATION` seconds: with the camera close the TVs are cut off by the top of the
screen and the doors can't be found; again before each walk into a room whose doors weren't found; then, after `HAUNTLET_START_DELAY` seconds for the entrance hall's doors to open, a first step forward of `HAUNTLET_START_FORWARD`, none if that is 0) takes a screenshot every `HAUNTLET_LOOK_INTERVAL` and hands it to a
`HauntletRun`, which keeps the room number and when it changed (`read_hauntlet_room()`) and says through `look()` when it is time
to choose, returning the doors to choose from. The number changes as the player passes through a door, while the last
room's doors are still on screen (open, the camera following the player), so the doors are only trusted when

- `HAUNTLET_ROOM_SETTLE` seconds have gone by since the number changed,
- the timer shows (`hauntlet_timer_visible()`), and
- the row of TVs is where it was on the look before, within `HAUNTLET_TV_STABLE` px - the camera has stopped (it zooms
  and pans at the start of a room; the macro stands still, so it does) - or `HAUNTLET_DOORS_TIMEOUT` has gone by.

A room whose number is a multiple of `HAUNTLET_FORK_EVERY` (rooms 10 and 20 were forks in the captured run; that every such room is one is an assumption) with no row of TVs on two looks in a row is a fork: two doorways. **Room 1 is the entrance hall** ("Welcome to the Hauntlet
Hotel": one doorway straight ahead, no TVs, the timer already showing; in both captured runs the doorway leads into a room with three
doors that is still numbered 1), and any other room without TVs for `HAUNTLET_DOORS_TIMEOUT` is treated the same way: a single
doorway ahead, walked to by holding forward only (no strafe) until a row of TVs is on screen (`hauntlet_walk(until_doors=True)`) or the
number changes, after which the room is looked at at once. For a room other than 1, `no doors found in room N` is
logged once per run as a failure, which keeps a screenshot of what the TVs were not found in. A white screen (a ghost took a heart, or the player died: the game whites the screen out and brings the player back to the room's start, `detect_white_screen()`) starts the room's clock over (`HauntletRun.look()`), so a room entered long ago and then reset is not taken for one without doors; `archive/tests/halloween2026/hauntlet_2/white_after_a_ghost.png` is a real frame of it. After a walk the room is left alone for
`HAUNTLET_RETRY_WAIT` seconds and then chosen from again if its number hasn't changed - the retry for a walk that got stuck.

**The circles.** In front of every door there is a **purple circle** on the floor (a lilac ring, `find_door_circles()`: the ring-shaped pieces of `HAUNTLET_CIRCLE_HSV`, `HAUNTLET_CIRCLE_*`; badges and labels are smaller and don't count): **stepping into one picks that door**, and the player is then **held in place for a few moments**. `hauntlet_strafe()` therefore walks to the circle's middle, not to the TV above the door (in an angled room the two are far apart), when all three are seen. The circle the player stands on is mostly hidden: two circles seen and the player (the hearts) where the third would be is **standing in it** (`player_in_circle()`, `HAUNTLET_IN_CIRCLE`). `hauntlet_walk()` then notes the door as picked (`HauntletRun.picked`, until the room changes), does not take the still view for being stuck while the player stands in it - a step sideways would take the player out of the doorway (the lock only lasts `HAUNTLET_LOCK_WAIT` seconds, and the unsticking went on after it) - and gives the walk `HAUNTLET_CIRCLE_WAIT` seconds from the step in instead of `HAUNTLET_WALK_MAX`: in real runs the room changed 9-13 s after the step into the circle, and every walk had ended as one that "got nowhere" (and the next had stepped back) before that. A retry in a room with a door picked goes to that same door (`hauntlet_choose()` returns it at once, with no items used and no other door avoided): the choice was made when the circle was stepped in.

**A fork's doorways.** A fork has no TVs to find its doorways by. `find_fork_doorways()` finds their openings: each is dark inside (`HAUNTLET_OPENING_HSV`) and begins right under the wall's pale stripes (`HAUNTLET_WALL_HSV`), where everywhere else between the wall and the floor there is the gold wainscot, so in each column the first dark pixel with wall just above it is a column of an opening (`HAUNTLET_OPENING_*`); exactly two openings of the right width, the right distance apart, are the doorways. Seen at an angle, the far doorway can be lit inside and show only as a narrow piece of dark (`HAUNTLET_OPENING_PIECE`): one whole opening no wider than `HAUNTLET_OPENING_MAIN_MAX` with exactly one such piece `HAUNTLET_OPENING_PIECE_GAP` from it are taken as the pair, the piece's middle being a little off the doorway's (the aim is looked at again before each step). That only works with the camera near the wall: from the room's entrance at most one is seen (the other shows the next hall's gold panelling, not a dark inside). Over the three captured forks (rooms 10 and 20) it found both doorways in 19 of 47 frames - those from the wall; the others were from the entrance or with the player pushed about - and nowhere else but a frame of the lobby of the ordinary game, over 350 frames. So a fork is walked in two parts: `play_hauntlet_2()` holds forward until both doorways are seen (`hauntlet_walk(until_doorways=True)`, at most `HAUNTLET_WALK_MAX` seconds), then strafes by `hauntlet_strafe()` as in a room, the two doorways being taken for two door spacings apart (a room's outer doors) and the player's place the hearts; if they are never seen, what is left is the fixed strafe below.

**Getting stuck.** The room timer runs out after a while and the run is over, so a stuck player has to do something else, quickly. A walk that ended with the
number unchanged counts in `HauntletRun.stuck`; the next one (`hauntlet_walk()`) steps back first (`KEYS["back"]`, `HAUNTLET_BACKUP_DURATION`) and avoids the door that
got the player stuck when there is another (`choose_hauntlet_door(avoid=...)`). Within a walk, **a view that doesn't move is the sign of being caught on something**: the camera follows the player, so a walking
player makes the whole picture move (`hauntlet_view()`: the screen shrunk to grey, without the room panel and the item bar). In the captured run the mean change between two looks was 0.1-1.1 grey levels while the player stood against a door's
leaf for 14 s and 11-44 while it walked, so under `HAUNTLET_STILL_LEVEL` for `HAUNTLET_STILL_AFTER` seconds with forward held is stuck, and `hauntlet_unstick()` steps sideways (forward stays held, so it
slides along) with a jump: towards the door being walked to (its TV - in a fork its doorway, when both are seen - against where the player stands, `HAUNTLET_UNSTICK_MARGIN`), the other way at every try, and a random way if the
doors can't be seen; each step `HAUNTLET_UNSTICK_STEP` seconds longer than the last, up to `HAUNTLET_UNSTICK_MAX`. Where the player stands is read from the hearts above its head; if they can't be read it is the middle of the
screen (the camera follows the player; in that run's room 3 the player stood 350 px right of the middle door, the hearts unread, and the macro, taking it to be in front of the middle door, walked into the wall for 14 s). When the doors can't be found at all - the camera zoomed in too far, turned away (room 8 of a macro-played run was seen from an angle, and the macro held W for 25 s against
a wall until the timer ran out), or a TV hidden - the camera can be dragged round with the right button (`rotate_camera()`, `HAUNTLET_CAMERA_DRAG`) up to `HAUNTLET_SEARCH_TURNS` times, a look
after each, before walking ahead - but that is **off** (`HAUNTLET_SEARCH_TURNS` is zero): turning the camera changes where the keys take the player, and in a captured fork (room 10, two doorways under signs, "Power Station" and "Spider Den") the player stood in front of its doorway until the camera was turned, then against the wall for 20 s. (That turn was a bug of its own: a fork whose looks had started over after a walk, past `HAUNTLET_DOORS_TIMEOUT`, was taken for one doorway; a fork now waits for its two empty looks whatever the age.) A TV showing a "1" (the player stands before that door) is a thin stroke, much smaller than the "0"s of the others, hence the low end of
`HAUNTLET_TV_AREA`; the real run's row was missed for 25 s because of one.

`hauntlet_choose()` reads the hearts and the items off the same screenshot chooses the door, uses the items the choice calls for, and logs the lot:
`room 12, 3 hearts (1 lost), items: phone x1, key x1, doors: -, -, locked, going right, used key`.

*The door* (`choose_hauntlet_door()`) is a random one, with two exceptions: a locked door is only a choice when the **grey key** (`HAUNTLET_ITEM_KEY`; the gold key is not it)
is held, and the door the phone's arrow is over is left alone (both only while there is any other door to take). *The items* (`hauntlet_items_for()`), each only if held and in
this order: the grey key for a locked door; a **cyan potion** (a heart that lasts the room) for a boarded one; the **red potion** (it refills the hearts) when the red hearts are down to
`HAUNTLET_LOW_HEARTS` or fewer; a **heart potion** (one more heart) whenever there is one. *The phone* is used first, with a chance of `HAUNTLET_PHONE_CHANCE` at each choice (`HAUNTLET_PHONE_CHANCE_LOW` when the hearts are low - it is then that knowing where the ghost is matters most): it puts a
red arrow - a big red triangle with a ghost on it, `HAUNTLET_ARROW_*` - over the TV of the door with a ghost behind it, which then isn't chosen. `find_ghost_door()` finds the arrow (it
takes a moment to show, so it is looked for `HAUNTLET_ARROW_LOOKS` times) and says which door it belongs to; over the captured runs it fired on 9 frames, all of the hand-played run (the one that used the phone), and where the doors were
found (6 of them) always within a few percent of a door spacing of a door's TV; it fired on none of the ~700 frames of the macro-played runs, which used no phone. `hauntlet_use_item()` clicks an item's slot, as the capture shows a human doing, and waits
`HAUNTLET_ITEM_WAIT`; the slots close up when an item is used up, so it looks for the slot on a fresh screenshot each time. The items are used before the walk starts. Whether an item
worked is not looked at, and the gold key and the wand have no use yet. `hauntlet_strafe()` works out how far to go sideways (the key and the seconds), and `hauntlet_walk()` **aims the player in steps** before it holds `KEYS["forward"]` - never two keys at once. A door is only as wide as a part of the distance between two doors, so a player that is a little off its door stands against the wall beside it and W gets it nowhere. In the recordings of two runs (rooms 8 and 15) that took 10-30 s of a room's 30 s in the worst rooms: the player stood 0.3-0.4 door spacings off its door - below the old limit for steering, which was 0.4, and checked once a second - and the stuck check's sidestep is a guess. A strafe worked out in one go also ended short or long (in room 4 of the room 15 run 2.4 s of A moved the doors 20-30 % more than `HAUNTLET_STRAFE_DURATION` says, and the player ended against the left wall beside its door). So the walk begins with up to `HAUNTLET_AIM_STEPS` steps: a look at the doors (`hauntlet_steer()`), one hold of the key towards the chosen door - `HAUNTLET_STEER_GAIN` of the strafe, at most `HAUNTLET_STEER_MAX` seconds - and a new look, until the door is within `HAUNTLET_STEER_MIN` door spacings of the player. If the doors can't be seen at the first look (`hauntlet_steer()` gives nothing), the strafe worked out by the caller is held as it was (at most `HAUNTLET_STRAFE_MAX` spacings). Then W is held until the number changes (or `HAUNTLET_WALK_MAX` seconds), and then `HAUNTLET_WALK_PAST_DURATION` longer, and while it is held **the player is steered** the same way every `HAUNTLET_STEER_EVERY` seconds: with the three TVs in sight, a player `HAUNTLET_STEER_MIN` door spacings or more off its door - measured as the strafe is - lets go of W, holds the key for a step and walks on. (What the keys do to the camera is not known to be anything: in the recordings its heading stays the same and the view changes as the player moves; the player's place in a room carries over from the door it came through.) **The number changes while the
player is still in the doorway** (the camera close on the door, its TV filling the screen, the next room's doors out of sight); a human keeps walking
1.0-2.0 s more (median 1.4 s in the capture) and a macro that let go at once stood there for the rest of the run - which is how the
macro's own captures showed it. The
strafe is the distance from the player to the door's TV in *door spacings* (the distance between neighbouring TVs, which
also takes the camera's zoom out of it) times `HAUNTLET_STRAFE_DURATION` seconds per spacing - no strafe under `HAUNTLET_STRAFE_MIN`
spacings, never more than `HAUNTLET_STRAFE_MAX`. The player's place is measured, not assumed, because where a room is entered may
depend on the door it came through (a player who came in at the left door has further to go to the right one): it is the middle of the
row of hearts above the head (`HauntletHearts.center_x`), and where the hearts aren't seen, in front of the middle door. A fork's
two doorways, when they aren't located, are the left or the right key for one spacing, wherever the player stands (in a captured fork it stood nearer the left doorway). The seconds per spacing are from a human's run (the
capture's keys against its room numbers, from the middle: left door 1.0-1.1 s, right 0.9-1.3 s; W held 3-4.8 s in all); that a key held by the macro
carries the character as far as one pressed by hand is **not yet checked in the game**, and neither is the strafe in a fork's hall
(that its doorways are two door spacings apart is read from a human's run and the captured frames, not tried) - both are the first things to tune. The keys are always released,
also when the macro is stopped. When the run ends, `got as far as room N` goes in the run log.

### Playing Ghost Gallery

From a human's 162 s run (the capture of the keys, the mouse and 209 frames). **How the game works:** ghosts run about a hall and hide
behind the furniture; the player has a beam (the round button at the bottom) that goes from the character **to the mouse cursor** while the
left button is held, and a ghost held in it scores ("+2700", a combo counter "x3"...). The player is sometimes knocked down. About **1:08
before the end** (the game lasts 3 minutes) the ghosts and furniture are replaced by the **boss**, a huge cyan ghost with a segmented
health bar (a gold frame, `GHOST_BOSS_BAR_*`) along the top, and it is where most of the points are. At the end the screen goes white
and a "GAME OVER!" results screen with a NICE! button shows, which the existing `detect_minigame_finished()` already handles. In the run the human
held the button on a ghost for a second or less at a time (and on the boss for tens of seconds), the cursor staying within about 150 px of
the character while the camera followed it.

`find_ghost_blobs()` finds the ghosts and the boss as the connected pieces of their translucent cyan (`GHOST_COLOR`, OpenCV HSV (85, 140, 250)
under the human's cursor), at least `GHOST_MIN_AREA` px (ghosts 880-7200 px in the run, the boss 9500-41000), biggest first, leaving out the
HUD, the character's own glow and the bottom of the screen (`GHOST_IGNORE_BOXES`: a player's pet, in the macro-played run a cyan one - the same cyan as the ghosts to a few units of hue and brightness, so it can't be told apart by colour -
was aimed at for 20 s of a run, the camera being close, and a cyan costume was taken for a ghost for much longer) and, in the boss phase, the boss's health bar (its crest and its ghost icons are the
same cyan). **Ghosts from afar.** In a macro-played run's first two minutes the score stayed at 450: from the high camera the ghosts are small blue-cyan ovals (1200-3500 px) and `GHOST_COLOR`, taken from a close-up human run, stopped at saturation 210 - ghosts were found in 12 of 27 looks, most of them the character itself. `GHOST_COLOR_FAR` is looked for as well. Its first version (saturation to 240) was still too narrow: in a later run (a Ghost Gallery run whose score stayed 0 for its first 100 s) the ghosts' heads were hue 92-99 and saturation 244-254, and none was found in the first minute. Now it is the range `GHOST_COLOR_FAR` holds (hue 90-99 was seen, up to full saturation, bright). What looks alike and is outside it: the slime puddles (hue 83-85 here, saturation 255, not as bright), the glow of the haunted paintings (hue 100 and over), the ghosts' own purple trail (hue 100), The pet's cyan is inside it, see below. The dim boss of the shielded phase stays out by its value. Ghosts really are rare in view in that stretch (about a third of the looks have one), so the camera is turned **one way round** until one is seen (`GHOST_CAMERA_PERIOD`; the way is chosen at random and kept, as in the boss phase) and `GHOST_CAMERA_TILT` is left at no tilt: a random up-and-down in every turn made the pitch a random walk, and the view ended on the ceiling, the floor or a wall.

**Things that are not ghosts.** In the last 40 s of a macro-played run (the camera pulled in against a wall in the dark of the boss fight) the macro was stuck, aiming at and walking towards its own hoodie, its pet and its beam, and so never turned the camera to look for the boss. Three rules came out of that capture. (1) **The character's box follows the camera**: the glowing pink emblem on its backpack (`GHOST_EMBLEM_HSV`, at least `GHOST_EMBLEM_MIN` px in `GHOST_EMBLEM_BOX` - none is seen with the camera far away, 750-10000 px close up) gives the place and, by its size, how close the camera is, and `ghost_character_box()` ignores a box of `GHOST_SELF_SCALE` times that size (pet below included) on top of the small fixed one. (2) **In the shielded phase only small ghosts count** (at most `GHOST_SMALL_MAX` px: the beam's glow, which turns cyan where it ends, was 9500, the boss's fog 400000). (3) **In the boss phase a blob is the boss only if it is at least `GHOST_BOSS_MIN` px** (9500-41000 seen); a smaller one is a minion or a bit of fog and is aimed at like the ghosts are, the nearest. The top 150 px of the screen are not looked at, where bits of fog under the HUD were found. (4) **The pet is the same cyan as a ghost and flies about wherever the camera is** (in one run it was beside the character in some looks and 300-500 px away in others), so nothing of the screen is left out for it. Two references were tried and don't work on the frames at hand: the median hue, saturation and value of the pet's pieces against a ghost's (the nearest of 15 labelled examples told right 11 times with each left out in turn, and a margin that dropped no ghost dropped no pet either), and a picture of the pet matched at several sizes (0.4-0.65 on other frames of it, as much as on ghosts). What does tell them apart is the score: **a blob the beam scores nothing on, and neither does the furniture around it** (`GHOST_HIDE_TRIES` places, `GHOST_SPOT_TRIAL` each), **is skipped** for `GHOST_SKIP_TIME` seconds (`GhostFeedback.skip()`), and `follow_skipped()` moves the mark to the blob it has become (the nearest within `GHOST_STICK` px at each look), so that the pet stays skipped as it flies and a ghost elsewhere is not. A hiding ghost, which also scores nothing at first, gets its furniture tried before that. (5) **In the boss phase the beam on the biggest blob for `GHOST_BOSS_TRIAL` seconds without a score skips it too**: in that run the boss was in sight in 3 looks of 10, and in the others the biggest blob was a light shaft along a side or a pale sheet at the bottom (15900-33600 px) which was walked towards for seconds. The boss can be far away, so nothing is concluded from a blob's shape or size. (6) **The white "!"** over the furniture a ghost hides in (`find_ghost_alerts()`: a white bar, at least `GHOST_ALERT_MIN_HEIGHT` px high and `GHOST_ALERT_ASPECT` times as high as wide, with a white dot under it, at any distance - four seen, 17-25 px high; a candle is white and as tall and has no dot) is aimed at before any ghost, `GHOST_ALERT_BELOW` bar heights below the mark, where the furniture is (seen in the run's first 60 s, while the score was 0). A trial there that scores nothing goes the same way as a ghost's: the furniture around it is tried, and then it is skipped.

`ghost_boss_bar_visible()` is the boss phase: 13,000-17,000 gold pixels in `GHOST_BOSS_BAR_BOX` against none (at most 230) before it, right on
every frame of the run but the ones at the very start and end of the boss's bar. `choose_ghost_target()` picks where to aim: the biggest blob
in the boss phase, else the ghost already being followed (one within `GHOST_STICK` px of where it was, since they move and aiming at another one at each look
would lose them all), else the nearest the character (`GHOST_CHARACTER`). Against the human's own play, its choice was within 150 px of
where the human held the beam in 69 of 80 frames outside the boss phase and the boss was where the human held it in 12 of 13.

**The boss's shield.** The boss can't always be hurt: in three phases of the run (6-7 s each, at 1:10, 0:37 and 0:25 before the end, a few seconds after
each other) its bar shows two or three round ghost icons instead of its cyan health, the boss itself goes dim, and small ghosts fly in; catching them is
what gets the boss back. `ghost_boss_shielded()` is the bar's cyan fill (`GHOST_BOSS_FILL`, an HSV range) being gone: 500-20000 pixels of it in `GHOST_BOSS_BAR_BOX` when the
boss can be hurt, 0 in the icon phases (a sliver, 470-2000, in the last seconds of the boss's life, which is why the limit, `GHOST_BOSS_FILL_MIN`, isn't zero). The boss
is dim enough then to fall under `GHOST_COLOR`'s brightness, so `find_ghost_blobs()` finds only the small ghosts, and they are aimed at like any ghosts
(nearest the character). Replaying the run, the three phases come out at 110.4-115.8 s, 127.2-133.5 s and 139.8-147.1 s of the capture, with the boss
phase before, between and after them, as in the frames.

**Score feedback.** The score says whether what the beam is on is a ghost, and `GhostFeedback` reads it: the digits of the banner's score pill (`GHOST_SCORE_BOX`, found by the pill's colour, `GHOST_SCORE_PILL`) as a mask of their white pixels. Over the four captured runs two looks at an unchanged score differed by 0 px and at a changed one by 160 px or more (`GHOST_SCORE_CHANGE` is between), so a changed mask is a score; nothing is concluded while the pill isn't on screen (a loading screen, a transition). It is used before the boss only (in the boss phase the score rises all the time, and while the boss is shielded it legitimately doesn't):
(1) **A ghost in sight that the beam scores nothing on** for `GHOST_SPOT_TRIAL` seconds is taken to be hiding in furniture: the beam moves to furniture found in a ring of `GHOST_RING` px around it (`find_furniture(around=...)`, by the same hover outline), stays there for the trial while the player keeps walking towards the ghost, and a place that scored nothing is kept away from for `GHOST_BAD_SPOT_TIME` seconds (`GHOST_BAD_SPOT_RADIUS` px) when the next one is looked for; a score puts the beam back on the ghost at once, and another ghost starts a trial of its own. With no ghost in sight the furniture search is as it was (a new place every `GHOST_FURNITURE_PERIOD`, shorter than a trial).
(2) **No score at all for `GHOST_STALL` seconds is a stall**: the beam is let go of, a random key held `GHOST_STALL_WALK` seconds, a jump made and the camera turned by `GHOST_STALL_TURN` px of drag, and another stall period passes before it happens again. This is what a view blocked by a pillar and a beam held on bare floor have in common (in one captured run a big orange pillar covered part of the screen at 59 s, and at 75 s the view was clear with the beam on the floor; the score was 0 for the first 64 s and 450 for 36 s after that). **Not yet checked in the game:** the ring's size, that the walk and turn get the player out from behind a pillar, and how long a real gap between scores is.

`play_ghost_gallery()` waits until the overlay (TIME LEFT, the score) has been seen - the game starts well before `MINIGAME_START_WAIT` would be over - and then for
`GHOST_INTRO_WAIT` more, while the game's own intro camera (a look at the ceiling) runs: a zoom made during it is undone when the play camera takes over, as it was in both macro-played runs.
Then it **sets up the view**: `zoom_out()` (`KEYS["zoom_out"]` held `MINIGAME_ZOOM_OUT_DURATION` seconds; in a macro-played run the camera was right behind the character, whose
cyan hoodie filled the screen and was taken for a ghost, so that the beam was held on the character for the whole run) and `tilt_camera_down()` (the right button held while the mouse is
dragged down `GHOST_START_TILT` px, for a view from above). Then, every `GHOST_LOOK_INTERVAL`, it looks and works out the phase (before the boss, the boss can be hurt, the boss is shielded),
moves the mouse to the target with `pydirectinput.moveTo()` (not `hover_move()`: its three settling pauses are too slow for tracking) and holds the left button down while there is one.
**The beam is held while the mouse is moved around**, and let go of and pressed again every `GHOST_REGRAB_PERIOD` seconds (a player who is knocked down loses the beam and has to press afresh; the same release is made when the run ends or is stopped): the left button goes down at the first target and the mouse is moved with it down after that. **With nothing to aim at** before the boss it looks for furniture to hold the beam on, `find_furniture()`: furniture the beam can be held on is highlighted - a pale outline and a tint - when the mouse is near it (in a 1920x1080 screenshot the outline's pixels were 190-230 in every channel, against 0-200 pale pixels in a patch of plain scene), so the mouse is moved to up to `GHOST_FURNITURE_PROBES` random places in `GHOST_FURNITURE_BOX`, at least `GHOST_FURNITURE_MIN_DISTANCE` px from the
character (`far_from_character()`), with a look at each after `GHOST_OUTLINE_WAIT` seconds, and the first place where `furniture_outlined()` sees pixels turn pale (`GHOST_OUTLINE_WHITE`: at least `GHOST_OUTLINE_MIN` of them within `GHOST_OUTLINE_RADIUS` px of the mouse that weren't pale or beside a pale pixel before; the green beam has no pale pixels) is held for
`GHOST_FURNITURE_PERIOD` seconds; none showing it, the last place tried is held. The ghosts hide in the furniture and holding the beam on it is how to get them out. With nothing to aim at for `GHOST_CAMERA_PERIOD` seconds it **turns the camera**
(`rotate_camera()`; like `tilt_camera_down()` it first moves the mouse to the middle of the screen, as the drag moves the cursor by hundreds of px and a right button released over the window's border or title bar opens its menu and takes the game's focus): the right button held while the mouse is dragged sideways `GHOST_CAMERA_DRAG` px, a random way (and a little up or down, `GHOST_CAMERA_TILT`), in
`GHOST_CAMERA_DRAG_STEPS` moves over `GHOST_CAMERA_DRAG_DURATION` seconds, so as to find some. **In the boss phase, with the boss and the ghosts that shield it out of sight, the camera is turned at every look until one is in view**: `GHOST_BOSS_TURN_DRAG` px at a time, one way (chosen at random, kept until something is seen), and nothing else is done - it doesn't walk blindly or aim at furniture. **It walks**: first forward for `GHOST_START_FORWARD` seconds, out of where the game puts the player and into the middle of the hall. After that **it always walks towards what it aims at**, the ghost or the boss: the key towards it is held (`ghost_chase_key()`: `w` if it is above the character, `s` below, `a`/`d` to a side - along the axis it is farther off on) until it is within `GHOST_TOWARD_MARGIN` px of the character, the beam being short (a human's cursor stayed within about 150 px of the character; a macro that aimed from afar scored 750 in its first 100 s). Only with nothing in sight before the boss does it walk about, a step at a time: every `GHOST_MOVE_PERIOD` seconds a random `MOVE_KEYS` key is held for `GHOST_MOVE_STEP_MIN`-`GHOST_MOVE_STEP_MAX` seconds. Every `GHOST_JUMP_MIN`-`GHOST_JUMP_MAX` seconds it **jumps** (`KEYS["jump"]`).
The buttons and the walking key are always let go, also when the macro is stopped. The macro's own window is out of the way (sent to the back, see
`send_macro_window_to_back()`) for the whole of a minigame, as it covers part of the screen.
**Not yet checked in the game:** that `pydirectinput.moveRel(..., relative=True)` with the right button held turns Roblox's camera the way a hand does, the size of the drags and of
the start's tilt, where the furniture is (the box is a guess), and the walking's and jumping's use; nor whether the character's own colours (a cyan costume) are kept out of the ghost finder
once the camera is out (`GHOST_IGNORE_BOXES` has the character's place in a human's zoomed-out run). **What it does not do:** dodge, react to being knocked down, or choose between ghosts
by anything but distance (the human often picked the second or third nearest); it can't tell furniture from floor.

### Playing Stitches' Spotlight

First version, from two human-played rounds (a two-minute round; the overlay shows TIME LEFT, the title and the Candy Corn count). The player runs on a
ring walkway round a central drum (with a bunny on it that moves, so the drum is not a landmark to match); candy corn lies along the ring and on the platforms
at its sides, searchlights sweep it and tint what they cover red, and dark grey planks hide the player from the light. The count is never read.

`play_stitches_spotlight()` waits for the overlay (`MINIGAME_START_WAIT`), then for the ring's line to show (at most `STITCHES_INTRO_MAX`: the game's intro
camera looks at the sky first), tests which way the camera keys turn (`calibrate_ring_camera()`), then holds `KEYS["right"]` (a strafe, which round a middle
the camera looks at is a walk round the ring) until the minigame finishes. Every `STITCHES_CENTER_INTERVAL` it turns the camera towards the middle (`RingCamera`) and every
`STITCHES_BOOST_INTERVAL` it presses `KEYS["speed_boost"]` (the speed boost, "Press E for a Speed Boost!").

**Where the middle is** comes from the ring's own edge: a gold line (`STITCHES_LINE_*`: hue, saturation and value of its dim olive-gold, looked for in
`STITCHES_LINE_BOX`), fitted with a line (`stitches_ring_angle()`). A ground line across the camera's view runs straight across the screen, so when the camera
looks at the middle of the ring the line is level; a line that falls to the right means the middle is to the right.

**Turning the camera is a small controller (`RingCamera.decide()`), because a walk round the ring needs the camera turned all the time**, and the line is not
always in sight (in a searchlight's red it is not found, and with the camera turned away; on a round's frames it was found in about six of ten looks):
- *proportional:* a turn of up to `STITCHES_TURN_HOLD_MAX` seconds, longer the further the line is from level; within `STITCHES_LINE_DEADBAND` degrees none;
- *learned steady turn (`bias`):* while the line stays off, the share of the time the turn key is held grows (`STITCHES_TURN_BIAS_GAIN`, at most
  `STITCHES_TURN_BIAS_MAX`), which is the turn rate the orbit needs;
- *line out of sight:* the learned turn goes on, and after `STITCHES_LOST_AFTER` seconds the camera is swept round to find the line again;
- a turn shorter than `STITCHES_TURN_MIN_HOLD` is not made.

**Which key turns which way is tested, not assumed.** `calibrate_ring_camera()` turns with the right key for `STITCHES_CALIBRATE_HOLD` seconds and looks at the line
(it must move at least `STITCHES_CALIBRATE_MIN` degrees): turning right levels a line that falls to the right. If it moved the other way `RingCamera.swap_keys()`
swaps the keys (and the learned turn); if nothing clear shows, the keys stay as `KEYS["camera_left"]` and `KEYS["camera_right"]` has them and the run capture is marked. Each look logs the line's
angle, the turn and the learned `bias` at the debug level.

The red and the planks are not handled, and no candy corn is looked for: the magnet picks up what the walk passes.

Not yet known from play: whether D holds the character on the ring with these turns (the player has not been seen working in the game: a run capture of it
is the next thing to look at, with the debug lines of the log), how the round goes when the red is on the ring, and what the speed boost is worth.
The tests use frames of a round (`archive/tests/halloween2026/stitches_spotlight/`) and a made-up camera.

### Candy corn

What a minigame run earns is read, not guessed. **The HUD's candy corn total** (top right, red digits on a white flag - on screen in the lobby, in the world and on the GAME OVER! screen, not at home or in a minigame) is read by `read_candy_corn()`:
the red pixels of `CANDY_CORN_BOX` that make up pieces as tall as a digit (`CANDY_CORN_DIGIT_HEIGHT`; the commas are half that) are the digits, each shrunk to `CANDY_CORN_DIGIT_SIZE` and compared with the saved ones (`archive/Halloween2026_ref/candy_corn_digits/`, cut from six real screens
that between them have every digit; `CANDY_CORN_MAX_DIFF`). It returns None rather than a wrong number: no counter, a digit that matches none, digits further apart than `CANDY_CORN_PITCH` (one is missing) or a piece as tall as a digit but too wide
(something over the first or last digit, which would leave a number that looks right). The six screens are read right, a leave-one-out test of the digits (each read with templates cut from the other screens) 41 of 41, and over 553 captured frames it
read only totals that were plausible, in the lobby and game over frames. `read_candy_corn_settled()` reads until two looks half a second apart agree, as the total counts up for a moment after a run.

`play_minigame()` reads it in the lobby after the popup's Yes (the total before) and again on the GAME OVER! screen (after): **the candy corn a run earned is the difference** (`minigame_result()`; None if either wasn't read or the total fell). In the captured Ghost Gallery run
(score 46418) it rose by 2540, in a Hauntlet 2 run that reached room 10 by 2105. Each run's result - when, run number, minigame, how it ended, the totals, `earned`, and the room reached in Hauntlet 2 (`state.minigame_detail`, filled by `play_hauntlet_2()`) - is
appended as a line of `debug/runs/results/results.jsonl` (`record_minigame_result()`, which also logs `minigame result: <minigame>: N candy corn` in the run log), written into the recording's `info.json`, and used to decide which recordings to keep (see above).
The GAME OVER! card itself is also saved as a picture beside it (`save_minigame_result()`). A run's limit of candy corn differs by minigame - 5000 for Ghost Gallery, a good deal more for Hauntlet 2 (about 20000 by the player's account; the macro doesn't know it).

## Recording the minigame runs

**Minigame runs are recorded automatically.** `play_minigame()` starts a capture of its own after the teleport that the popup's Yes brought
(`start_run_capture()`) and stops it when the run is over, whichever way it ends - finished, left, timed out or stopped
(`finish_run_capture()`, which also writes `outcome` into `info.json` and zips the capture as above). It is the same recorder, so it
has the macro's own key presses and clicks and the frames on the same clock, but in `debug/minigame_captures/`
(`RUN_CAPTURES_DIR`, named `<minigame>_<date>_<time>`, then renamed by `finish_run_capture()` with what the run earned and how it ended: `_<N>cc` or `_cc-unread`, `_room<n>` for Hauntlet 2, `_failed` or `_failures`; `run_capture_suffix()`) so the hotkey's captures and these
never delete each other, and with a **budget of `RUN_CAPTURE_ZIPS` zips**, spread over the whole run: the
zips are counted as `zip_capture()` will make them (`plan_zip_parts()`, the events in the first). A lossless frame is 1-3 MB, so
the budget buys some tens of frames; when a run would need more zips, `InputCapture._keep_within_budget()` deletes
every other saved frame - never the newest, and the first stays - and from then on saves frames twice as far apart, so what is
left is spread evenly over the run however long it is. It is checked after every saved frame and once more when the capture
stops (the events have grown by then). The events of the deleted frames stay in `events.jsonl`, followed by a `frame_dropped`
line naming the file, so a reader can tell; `info.json` has `frames_dropped` and `max_zips`, and `stopped_because` is "the minigame
ended". The folders are big (the frames and then the same again as zips), hence **only some are kept**: after each run `prune_run_captures()` keeps, of each minigame, the
`RUN_CAPTURES_BEST` runs that earned the most candy corn (see [Candy corn](#candy-corn)), the `RUN_CAPTURES_WORST` that earned the least (a run whose total wasn't read counts as the worst), the latest, and the `RUN_CAPTURES_FAILED` latest that failed - a run that did not finish, or had a failure logged while it ran (`failures` in its `info.json`, `_failed` / `_failures` on its
folder's name) - going by the `earned`, `finished` and `failures` that `finish_run_capture()` wrote into each `info.json`.
**Frames of when things went wrong are kept and not thinned out.** `mark_run_capture(reason)` (`InputCapture.mark()`) writes a `mark` line with the reason into `events.jsonl`, saves a frame at once and then every look for
`RUN_CAPTURE_BURST` seconds, however far apart the thinning has put the others - and those frames are `protected`, which `_keep_within_budget()` leaves alone (at most `RUN_CAPTURE_PROTECTED` of them, or they would use up the zips).
It is called when a failure is logged (`log_failure()`), when a Hauntlet 2 walk gets nowhere, when the player is found not to be moving (`hauntlet_unstick()`) or a fork's doorways aren't seen, when a Ghost Gallery
beam on a ghost scores nothing or the score stalls, and when a stray window or the paycheck popup is closed mid-minigame. A run that did not finish also keeps the frames of its last `RUN_CAPTURE_TAIL` seconds
(`protect_tail()`), where the end of a run that went wrong is. Switch it off with `MINIGAME_CAPTURE`; it isn't started while the
hotkey's capture is running (two recorders would only get in each other's way), and a capture that can't be started or finished is a
message, never a reason not to play. If more frames per run are wanted the choice is more zips or lossy frames (which would no
longer be the exact pixels the readers are tuned on).
