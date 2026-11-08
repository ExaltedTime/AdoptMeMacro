"""A text summary of one run, from its run log (run_log.txt, or the moments folder / zip that has it): what was resolved and how long it took, what failed (by kind), the rejoins, the notices, the setup
problems, the teleport checks, the needs disabled and the longest stretches without a need resolved. The first look at a run that is sent for analysis, without reading 2000 lines.

    python tools/run_summary.py debug/run_log.txt [--run 4]
    python tools/run_summary.py path/to/moments_folder_or.zip
"""
import argparse
import collections
import datetime
import os
import re
import sys
import zipfile

LINE_RE = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\] \[RUN ([^\]]+)\] (.*)$")
RESOLVED_RE = re.compile(r"^resolved: (\S+) \((\d+)s\)$")
FAILURE_RE = re.compile(r"^FAILURE: (.*?)(?: \(screenshot: .*)?$")
GAP_TOP = 3


def read_lines(path: str) -> list[str]:
    """The lines of the run log at `path`: a file, a folder with run_log.txt in it, or a zip with one."""
    if os.path.isdir(path):
        path = os.path.join(path, "run_log.txt")
    if path.endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            name = next((n for n in z.namelist() if n.endswith("run_log.txt")), None)
            if name is None:
                raise SystemExit(f"no run_log.txt in {path}")
            return z.read(name).decode("utf-8", "replace").splitlines()
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read().splitlines()


def kind_of(text: str) -> str:
    """A failure's text without what changes between two of a kind (seconds, counts, the list of disabled needs)."""
    text = re.sub(r"\d+(\.\d+)?s\b", "Ns", text)
    text = re.sub(r"\(\d+ disabled now:[^)]*\)", "(N disabled)", text)
    text = re.sub(r"\d+ times", "N times", text)
    return text[:100]


def summarise(lines: list[str], run: str = "") -> str:
    parsed = [(datetime.datetime.fromisoformat(m.group(1)), m.group(2), m.group(3)) for m in map(LINE_RE.match, lines) if m]
    if run:
        parsed = [p for p in parsed if p[1] == run]
    if not parsed:
        return "no lines of a run in that log"
    start, end = parsed[0][0], parsed[-1][0]
    resolved: dict[str, list[int]] = collections.defaultdict(list)
    failures: collections.Counter = collections.Counter()
    notes: dict[str, collections.Counter] = {key: collections.Counter() for key in ("rejoin", "notice", "setup", "teleport", "disabled", "popup")}
    progress = [start]
    for when, _run, text in parsed:
        m = RESOLVED_RE.match(text)
        if m:
            resolved[m.group(1)].append(int(m.group(2)))
            progress.append(when)
            continue
        m = FAILURE_RE.match(text)
        if m:
            failures[kind_of(m.group(1))] += 1
            continue
        if re.search(r"rejoin|recovery complete|white screen:", text):
            notes["rejoin"][kind_of(text)] += 1
        elif text.startswith("notice:"):
            notes["notice"][kind_of(text)] += 1
        elif text.startswith("setup:") or text.startswith("backpack:"):
            if re.search(r"not|did not|could not|failed", text):
                notes["setup"][kind_of(text)] += 1
        elif text.startswith("teleport") or "no white screen" in text:
            notes["teleport"][kind_of(text)] += 1
        elif text.startswith("disabled:") or text.startswith("given another chance"):
            notes["disabled"][kind_of(text)] += 1
        elif text.startswith("stray window closed"):
            notes["popup"][kind_of(text)] += 1
    progress.append(end)
    out = [f"run {parsed[0][1]}: {start:%Y-%m-%d %H:%M} to {end:%H:%M}, {(end - start).total_seconds() / 3600:.1f} hours, {sum(len(v) for v in resolved.values())} needs resolved, {sum(failures.values())} failures", ""]
    out.append("resolved (count, mean seconds):")
    for need, times in sorted(resolved.items(), key=lambda kv: -len(kv[1])):
        out.append(f"  {need:10} {len(times):4}  {sum(times) / len(times):5.0f}s")
    out.append("")
    out.append("failures:")
    out.extend(f"  {count:4}  {kind}" for kind, count in failures.most_common())
    for title, counter in notes.items():
        if counter:
            out.append("")
            out.append(f"{title}:")
            out.extend(f"  {count:4}  {kind}" for kind, count in counter.most_common(12))
    gaps = sorted(((b - a).total_seconds(), a) for a, b in zip(progress, progress[1:]))[-GAP_TOP:]
    out.append("")
    out.append("longest stretches without a need resolved:")
    out.extend(f"  {seconds / 60:5.1f} min from {a:%H:%M:%S}" for seconds, a in reversed(gaps))
    return "\n".join(out)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path")
    parser.add_argument("--run", default="", help="only the lines of this run number")
    args = parser.parse_args(argv)
    print(summarise(read_lines(args.path), args.run))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
