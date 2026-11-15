# Archive

Code, tests, pictures and notes the macro does not use now. Nothing here is run, linted or checked: `pytest.ini` leaves it out of the tests, and the lint and constant checks do not look at it.

- `Halloween2026.py`, `Halloween2026_ref/`, `tests/halloween2026/`, `HALLOWEEN_2026.md`: the Halloween 2026 event (what it was, the hooks the macro had, how to bring it back). The tests' own fixtures and conftest are in `tests/`.
- `gui_buttons.md`: the Main tab's Functions buttons and the Debug tab's four red buttons, with the code that backed them.

To bring something back, move it to where the macro expects it (the event: `scripts/` and `tests/`) and put its hooks back.
