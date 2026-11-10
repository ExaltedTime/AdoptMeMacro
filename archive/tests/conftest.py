"""For the archived event's tests (see ../README.md): when they are brought back, merge this file into the macro's tests/conftest.py. The event's `world` fixture (event_fixtures.py) was the macro's too; the macro's now has no event parts."""
import os

pytest_plugins = ["halloween2026.event_fixtures"]

def pytest_collection_modifyitems(items):
    """The tests of the event (archive/tests/halloween2026) all start from a fresh event state."""
    for item in items:
        if os.sep + "halloween2026" + os.sep in str(item.fspath):
            item.fixturenames.insert(0, "fresh_event_state")
