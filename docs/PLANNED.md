# Add next

- [x] Add a 'setup' function (done: setup_game() does the favorites filter, the house lock and disabling trades, and is run on demand only, not by the cycle)
- [ ] Add further debug functionality to pinpoint when things go wrong
- [ ] Add functionality to rejoin if disconnected, and do that if too many needs(4) are disabled (leave_and_rejoin() exists as a GUI-only function; detecting a disconnect and calling it automatically is still to do)
- [ ] Start using the helicopter to get to places (the optional helicopter step exists for teleport-walk needs and is on for Halloween bored/beach/camping; the timings still need tuning and the other destinations could use it)
- [ ] Pet focusing for choose and pet sometimes breaks
- [ ] Add an auto ghost gallery function if the halloween toggle is on. And also a toggle for whether you want ghost gallery to run (GHOST_GALLERY_PLAY_MINIGAME is the toggle; the disable branch of ghost_gallery() works, the minigame branch is comments only so far)
      
# Expansion

- [ ] Trade helper
- [ ] Auto-pen/neon maker
