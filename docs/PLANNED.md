# Add next

- [ ] Fix the remaining teleport needs and separate out the seasonal needs (diving and puddle - their icons now live in needs/weather/, but they have no handlers yet). The HALLOWEEN toggle and a separate Halloween step list (TELEPORT_WALK_NEEDS_HALLOWEEN, which also holds sick) now exist; what's left is working out the real Halloween steps for bored/beach/school/camping/sick (currently placeholder copies of the normal steps for the first four)
- [ ] Add a 'setup' function to select the favorite filter in the backpack, disable trades and lock the house (setup_game() does the favorites filter and the house lock; disabling trades is still to do)
- [ ] Add disabling the ghost gallery popup to unscrew if the halloween toggle is on (unscrew() now calls a ghost_gallery() stub when HALLOWEEN is on; the disable branch is comments only so far)
- [ ] Add further debug functionality to pinpoint when things go wrong
- [ ] Add functionality to rejoin if disconnected
- [ ] Add auto-disabling needs if they aren't getting resolved, and auto-rejoin if too many are getting disabled
- [ ] Start using the helicopter to get to places
- [ ] Pet focusing for choose and pet sometimes breaks
- [ ] Add an auto ghost gallery function if the halloween toggle is on. And also a toggle for whether you want ghost gallery to run (GHOST_GALLERY_PLAY_MINIGAME is the toggle, ghost_gallery() the stub; the minigame branch is comments only so far)
- [ ] Update timings
- [ ] Update café steps

# Expansion

- [ ] Trade helper
- [ ] Auto-pen/neon maker
