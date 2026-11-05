"""What Halloween 2026 added to the macro, kept here now that the event is over (see docs/ARCHITECTURE.md#halloween-2026).

This file is not imported by the macro. The routes below are the ones the event's moved nursery needed: the macro's own table (`magic_numbers.TELEPORT_WALK_NEEDS`) has the
ones from before, which the nursery is back to."""
import magic_numbers as cfg

KEYS = cfg.KEYS
SICK_FINAL_CLICK_POS = cfg.SICK_FINAL_CLICK_POS
TELEPORT_PETS_TAB_POS = cfg.TELEPORT_PETS_TAB_POS

# Used instead of the entry of the same name in TELEPORT_WALK_NEEDS while the event ran. "sick" is the event's own need (Doctor Heart's dialog).
TELEPORT_WALK_NEEDS_HALLOWEEN = {
    "bored":   dict(teleport_pos=TELEPORT_PETS_TAB_POS,     steps=((KEYS["forward"], 9),), helicopter=True),
    "beach":   dict(teleport_pos=TELEPORT_PETS_TAB_POS,     steps=((KEYS["left"], 20.0),), helicopter=True),
    "school":  dict(teleport_pos=TELEPORT_PETS_TAB_POS,     steps=((KEYS["forward"], 3), (KEYS["left"], 6))),
    "camping": dict(teleport_pos=TELEPORT_PETS_TAB_POS,     steps=((KEYS["back"], 20),), helicopter=True),
    "sick":    dict(teleport_pos=TELEPORT_PETS_TAB_POS,     steps=((KEYS["forward"], 2.5), (KEYS["right"], 4.0), (KEYS["forward"], 1.2)),
                    final_click=SICK_FINAL_CLICK_POS),
}
