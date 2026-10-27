# Important
Claude, you are not allowed to touch this file, the README.md or LINKS.md, and only the Claude section of PLANNED.md

# Checks
Don't run tools/retune_check.py unless the user asks for it - it takes far too long (about 15 minutes for its ~30 variants).
Before each push run pyflakes, pytest, `mypy main.py bot/bot_lib.py` and tools/check_constants.py. All must come out clean: mypy reports no errors, and a count that is merely the same as before is a failure to fix, not a baseline.

# Writing
I don't need rhetorical questions in my docs
