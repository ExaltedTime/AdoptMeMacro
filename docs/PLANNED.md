# Add next

- [x] Add a 'setup' function (done: setup_game() does the favorites filter, the house lock and disabling trades, and is run on demand only, not by the cycle)
- [x] Add further debug functionality to pinpoint when things go wrong (output.log, failure screenshots, status.json - see the Debugging section of ARCHITECTURE.md)
- [ ] Add functionality to rejoin if disconnected (rejoin_game() already recovers - rejoin, setup, clear disabled needs - when 4 needs get disabled; detecting a disconnect as a second trigger is still to do, waiting on a screenshot of the dialog)
- [ ] Restart the loop automatically if it stops (a crash, or Roblox losing focus, currently ends it)
- [ ] Start using the helicopter to get to places (the optional helicopter step exists for teleport-walk needs and is on for Halloween bored/beach/camping; the timings still need tuning and the other destinations could use it)
- [ ] Pet focusing for choose and pet sometimes breaks
- [ ] Add an auto ghost gallery function if the halloween toggle is on. And also a toggle for whether you want ghost gallery to run (GHOST_GALLERY_PLAY_MINIGAME is the toggle; the disable branch of ghost_gallery() works, the minigame branch is comments only so far)
      
# Expansion

- [ ] Trade helper
- [ ] Auto-pen/neon maker
