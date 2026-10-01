#!/usr/bin/env python3
"""One-off helper: strip the legacy 'need_' prefix from reference icons in
needs/, now that main.py expects them named after the need directly
(e.g. beach.png instead of need_beach.png)."""

import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
NEEDS_DIR = os.path.join(SCRIPT_DIR, "needs")
PREFIX = "need_"

def main():
    renamed = 0
    for filename in sorted(os.listdir(NEEDS_DIR)):
        if not filename.endswith(".png") or not filename.startswith(PREFIX):
            continue

        new_name = filename[len(PREFIX):]
        old_path = os.path.join(NEEDS_DIR, filename)
        new_path = os.path.join(NEEDS_DIR, new_name)

        if os.path.exists(new_path):
            print(f"[!] SKIP: {new_name} already exists, not overwriting")
            continue

        os.rename(old_path, new_path)
        print(f"[debug] {filename} -> {new_name}")
        renamed += 1

    print(f"\nDone - renamed {renamed} file(s).")

if __name__ == "__main__":
    main()
