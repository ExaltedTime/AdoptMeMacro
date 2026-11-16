## What this changes

<!-- One or two sentences, then a short list if it touches several areas. -->

## Why

<!-- The run, issue or failure this comes from (a run number and what the log or frames showed), or "cleanup". -->

## How it was checked

- [ ] `pyflakes main.py magic_numbers.py tools scripts tests` is clean
- [ ] `python tools/check_constants.py` is clean
- [ ] `mypy main.py magic_numbers.py scripts tools` reports no errors
- [ ] `python -m pytest -q -n auto` passes
- [ ] CI is green (Linux 3.12 and 3.13, Windows)
- [ ] Tested on frames from a real run: <!-- fixture names under tests/ -->

## Seen in the game

<!-- Mark one. -->

- [ ] Yes: <!-- which run, what it showed -->
- [ ] No. This is unverified in the game. <!-- What to look at in the next run -->

## Docs

- [ ] `docs/ARCHITECTURE.md` describes the change (constants are named, not their values)
- [ ] `docs/PLANNED.md` has an item for anything left open or unverified

## Fixtures

- [ ] New screenshots are PNG files, not re-encoded JPEG or WebP (a lossy fixture changes what the detectors score)

## Anything else for the reviewer

<!-- Optional. -->
