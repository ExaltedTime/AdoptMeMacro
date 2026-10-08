# Add next

- [ ] Add a 'setup' function to select the favorite filter in the backpack, disable trades and lock the house (setup_game() does the favorites filter and the house lock, and is run on demand only, not by the cycle; disabling trades is still to do)
- [ ] Add disabling the ghost gallery popup to unscrew if the halloween toggle is on (unscrew() now calls a ghost_gallery() stub when HALLOWEEN is on; the disable branch is comments only so far)
- [ ] Add further debug functionality to pinpoint when things go wrong
- [ ] Add functionality to rejoin if disconnected, and do that if too many needs(4) are disabled
- [ ] Start using the helicopter to get to places (the optional helicopter step exists for teleport-walk needs and is on for Halloween bored/beach/camping; the timings still need tuning and the other destinations could use it)
- [ ] Pet focusing for choose and pet sometimes breaks
- [ ] Add an auto ghost gallery function if the halloween toggle is on. And also a toggle for whether you want ghost gallery to run (GHOST_GALLERY_PLAY_MINIGAME is the toggle, ghost_gallery() the stub; the minigame branch is comments only so far)
      
# Expansion

- [ ] Trade helper
- [ ] Auto-pen/neon maker
