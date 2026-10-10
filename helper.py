"""Debug helper: turns one run of run_log.txt into a PNG report.

The log is parsed by parse_run() (pure text, no imports from the macro) and
drawn with OpenCV by draw_report(), so there's no dependency beyond what the
macro already has. generate_run_report() does both and writes the file.

The report has four panels:
  1. needs resolved per unit of time - the unit is picked so the run fills
     about TARGET_BUCKETS bars - stacked by basic / teleport / other need,
     with failures (red) and rejoins (blue) marked along the bottom;
  2. needs detected per check, over time;
  3. average seconds per resolved need, with how many there were;
  4. counts of failures by kind, popups, minigames and the like.
"""

import os
import re
import time
from collections import Counter, defaultdict

import cv2
import numpy as np

LINE_RE = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\] \[RUN ([^\]]+)\] (.*)$")
RESOLVED_RE = re.compile(r"^resolved: (\S+) \((\d+)s\)$")
DETECTED_RE = re.compile(r"^detected: (.*)$")
NONE_MATCHED_RE = re.compile(r"^none matched")
MINIGAME_RE = re.compile(r"^minigame (started|finished): (.*?)(?: \(\d+s\))?$")

TARGET_BUCKETS = 40
# Nice bucket lengths (seconds), shortest first.
BUCKET_STEPS = [10, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 14400]

# BGR
COLOR_BASIC = (200, 140, 40)
COLOR_TELEPORT = (60, 160, 60)
COLOR_OTHER = (160, 100, 190)
COLOR_FAILURE = (60, 60, 220)
COLOR_REJOIN = (220, 120, 40)
COLOR_TEXT = (40, 40, 40)
COLOR_GRID = (225, 225, 225)
COLOR_BG = (255, 255, 255)
COLOR_LINE = (150, 150, 150)
COLOR_MEAN = (40, 40, 200)
FONT = cv2.FONT_HERSHEY_SIMPLEX


def parse_ts(text):
    return time.mktime(time.strptime(text, "%Y-%m-%d %H:%M:%S"))


def parse_run(log_path, run):
    """Everything the report needs from run `run` (a number or its text) of
    the log at `log_path`, as a dict, or None if the run isn't in it:
      start, end - epoch seconds of the first and last line;
      resolved - [(t, need, seconds)]; detected - [(t, matched, icons)] per check;
      failures - [(t, text)]; rejoins - [t]; events - Counter of everything else."""
    run = str(run)
    lines = []
    if not os.path.exists(log_path):
        return None
    with open(log_path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            m = LINE_RE.match(raw.rstrip("\n"))
            if m and m.group(2) == run:
                lines.append((parse_ts(m.group(1)), m.group(3)))
    if not lines:
        return None
    data = {"start": lines[0][0], "end": lines[-1][0], "resolved": [], "detected": [],
            "failures": [], "rejoins": [], "events": Counter()}
    for t, text in lines:
        if m := RESOLVED_RE.match(text):
            data["resolved"].append((t, m.group(1), int(m.group(2))))
        elif m := DETECTED_RE.match(text):
            body = m.group(1)
            if NONE_MATCHED_RE.match(body):
                icons = re.search(r"\((\d+) icon", body)
                data["detected"].append((t, 0, int(icons.group(1)) if icons else 0))
            else:
                names = [n for n in body.split(", ") if n]
                data["detected"].append((t, len(names), len(names)))
        elif text.startswith("FAILURE: "):
            data["failures"].append((t, text[len("FAILURE: "):]))
        elif text.startswith("scheduled rejoin") or text.startswith("recovery complete"):
            data["rejoins"].append(t)
            data["events"]["rejoins"] += 1
        elif text.startswith("paycheck popup"):
            data["events"]["paycheck popups"] += 1
        elif text.startswith("stray window closed"):
            data["events"]["stray windows closed"] += 1
        elif text.startswith("backpack was left open"):
            data["events"]["backpacks closed"] += 1
        elif text.startswith("minigame popup dismissed"):
            data["events"]["minigame popups declined"] += 1
        elif m := MINIGAME_RE.match(text):
            if m.group(1) == "finished":
                data["events"]["minigames won"] += 1
        elif " interrupted by " in text:
            data["events"]["needs interrupted"] += 1
    return data


def failure_kind(text):
    """A failure's kind: its first two words, digits dropped."""
    words = re.sub(r"[\d.]+s?", "", text).split()
    return " ".join(words[:2]).lower() or "failure"


def pick_bucket(duration):
    """The shortest of BUCKET_STEPS that makes at most TARGET_BUCKETS bars."""
    for step in BUCKET_STEPS:
        if duration / step <= TARGET_BUCKETS:
            return step
    return BUCKET_STEPS[-1]


def unit_label(seconds):
    if seconds % 3600 == 0:
        return f"{seconds // 3600} h"
    if seconds % 60 == 0:
        return f"{seconds // 60} min"
    return f"{seconds} s"


def duration_label(seconds):
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m:02d}m" if h else (f"{m}m {s:02d}s" if m else f"{s}s")


def need_group(need, basic, teleport):
    if need in basic:
        return "basic"
    if need in teleport:
        return "teleport"
    return "other"


GROUP_COLORS = {"basic": COLOR_BASIC, "teleport": COLOR_TELEPORT, "other": COLOR_OTHER}


# --- drawing ---------------------------------------------------------------

def text(img, s, x, y, scale=0.5, color=COLOR_TEXT, thick=1, anchor="left"):
    (w, h), _ = cv2.getTextSize(s, FONT, scale, thick)
    if anchor == "right":
        x -= w
    elif anchor == "center":
        x -= w // 2
    cv2.putText(img, s, (int(x), int(y)), FONT, scale, color, thick, cv2.LINE_AA)


def nice_max(value):
    """Round `value` up to a tidy axis maximum (1, 2, 5 x 10^n)."""
    if value <= 0:
        return 1
    exp = 10 ** int(np.floor(np.log10(value)))
    for mult in (1, 2, 5, 10):
        if value <= mult * exp:
            return mult * exp
    return 10 * exp


class Panel:
    """A plot area on `img`: maps data (x in 0..x_max, y in 0..y_max) to pixels
    and draws the frame, grid and tick labels."""

    def __init__(self, img, box, title, x_max, y_max, x_ticks=None, y_label=""):
        self.img, self.box, self.x_max, self.y_max = img, box, x_max, y_max
        left, top, right, bottom = box
        self.plot = (left + 55, top + 30, right - 15, bottom - 38)
        text(img, title, left, top + 14, 0.6, thick=2)
        pl, pt, pr, pb = self.plot
        cv2.rectangle(img, (pl, pt), (pr, pb), COLOR_LINE, 1)
        y_step = max(1, nice_max(y_max) // 5) if y_max >= 5 else (1 if y_max > 1 else 0.5)
        value = 0
        while value <= y_max + 1e-9:
            y = self.y(value)
            cv2.line(img, (pl, y), (pr, y), COLOR_GRID, 1)
            text(img, f"{value:g}", pl - 6, y + 4, 0.4, anchor="right")
            value += y_step
        for x_value, label in (x_ticks or []):
            x = self.x(x_value)
            cv2.line(img, (x, pt), (x, pb), COLOR_GRID, 1)
            text(img, label, x, pb + 16, 0.4, anchor="center")
        if y_label:
            text(img, y_label, left, pb + 32, 0.45)

    def x(self, value):
        pl, _, pr, _ = self.plot
        return int(pl + (pr - pl) * value / self.x_max)

    def y(self, value):
        _, pt, _, pb = self.plot
        return int(pb - (pb - pt) * value / self.y_max)


def time_ticks(duration, unit=1, max_ticks=8):
    """Axis ticks at whole multiples of `unit` seconds (a bucket length, so
    ticks fall on bar edges) - few enough to stay readable, on a nice step
    from BUCKET_STEPS."""
    nice = next((st for st in BUCKET_STEPS if duration / st <= max_ticks), BUCKET_STEPS[-1])
    step = unit * max(1, int(np.ceil(nice / unit)))
    return [(t, duration_label(t)) for t in range(0, int(duration) + 1, step)]


def draw_report(data, run, basic, teleport):
    """The report image (BGR numpy array) for parse_run()'s `data`."""
    width, height = 1600, 1100
    img = np.full((height, width, 3), COLOR_BG, np.uint8)
    duration = max(1.0, data["end"] - data["start"])
    start = data["start"]
    started = time.strftime("%Y-%m-%d %H:%M", time.localtime(start))
    total = len(data["resolved"])
    text(img, f"Run {run} - {started}, {duration_label(duration)}: {total} needs resolved, "
              f"{len(data['failures'])} failures, {len(data['rejoins'])} rejoins",
         20, 30, 0.8, thick=2)

    # 1. resolved per bucket, stacked
    step = pick_bucket(duration)
    n_buckets = max(1, int(np.ceil(duration / step)))
    stacks = [defaultdict(int) for _ in range(n_buckets)]
    for t, need, _ in data["resolved"]:
        stacks[min(n_buckets - 1, int((t - start) // step))][need_group(need, basic, teleport)] += 1
    peak = max([sum(s.values()) for s in stacks] + [1])
    y_max = nice_max(peak)
    panel = Panel(img, (20, 50, width - 20, 560), f"Needs resolved per {unit_label(step)}",
                  n_buckets * step, y_max, time_ticks(n_buckets * step, step))
    for i, s in enumerate(stacks):
        base = 0
        for group in ("basic", "teleport", "other"):
            if s[group]:
                x0, x1 = panel.x(i * step) + 1, panel.x((i + 1) * step) - 1
                cv2.rectangle(img, (x0, panel.y(base + s[group])), (x1, panel.y(base)), GROUP_COLORS[group], -1)
                base += s[group]
    pl, pt, pr, pb = panel.plot
    for t, _ in data["failures"]:
        x = panel.x(t - start)
        cv2.line(img, (x, pb), (x, pb - 14), COLOR_FAILURE, 2)
    for t in data["rejoins"]:
        x = panel.x(t - start)
        cv2.line(img, (x, pt), (x, pb), COLOR_REJOIN, 1)
    legend_x = pl + 10
    for label, color in (("basic", COLOR_BASIC), ("teleport", COLOR_TELEPORT), ("other", COLOR_OTHER),
                         ("failure", COLOR_FAILURE), ("rejoin", COLOR_REJOIN)):
        cv2.rectangle(img, (legend_x, pt + 8), (legend_x + 12, pt + 20), color, -1)
        text(img, label, legend_x + 18, pt + 19, 0.45)
        legend_x += 95

    # 2. detected per check
    checks = data["detected"]
    det_max = nice_max(max([c[1] for c in checks] + [1]))
    panel2 = Panel(img, (20, 570, 800, 1080), "Needs detected per check", duration, det_max,
                   time_ticks(duration))
    if checks:
        points = np.array([[panel2.x(t - start), panel2.y(matched)] for t, matched, _ in checks], np.int32)
        cv2.polylines(img, [points], False, COLOR_LINE, 1, cv2.LINE_AA)
        # mean per bucket
        for i in range(n_buckets):
            vals = [m for t, m, _ in checks if int((t - start) // step) == i]
            if vals:
                cv2.line(img, (panel2.x(i * step), panel2.y(np.mean(vals))),
                         (panel2.x((i + 1) * step), panel2.y(np.mean(vals))), COLOR_MEAN, 3)
        none = sum(1 for c in checks if c[1] == 0)
        note = f"grey: each of {len(checks)} checks; red: mean per {unit_label(step)}; {none} matched nothing"
        text(img, note, panel2.plot[0] + 190, panel2.plot[1] - 16, 0.4)

    # 3. average seconds per need
    per_need = defaultdict(list)
    for _, need, seconds in data["resolved"]:
        per_need[need].append(seconds)
    rows = sorted(per_need.items(), key=lambda kv: -np.mean(kv[1]))
    left, top, right, bottom = 820, 570, width - 20, 810
    text(img, "Average seconds per resolved need (n)", left, top + 14, 0.6, thick=2)
    if rows:
        row_h = min(26, (bottom - top - 30) // max(1, len(rows)))
        longest = max(np.mean(v) for _, v in rows) or 1
        for i, (need, secs) in enumerate(rows):
            y = top + 34 + i * row_h
            bar = int((right - left - 270) * np.mean(secs) / longest)
            cv2.rectangle(img, (left + 90, y), (left + 90 + max(2, bar), y + row_h - 6),
                          GROUP_COLORS[need_group(need, basic, teleport)], -1)
            text(img, need, left, y + row_h - 8, 0.45)
            text(img, f"{np.mean(secs):.0f}s (n={len(secs)})", left + 98 + bar, y + row_h - 8, 0.45)
    else:
        text(img, "nothing resolved", left, top + 44, 0.5)

    # 4. counts
    left, top = 820, 830
    text(img, "Failures and events", left, top, 0.6, thick=2)
    y = top + 24
    kinds = Counter(failure_kind(t) for _, t in data["failures"])
    for kind, count in kinds.most_common(5):
        text(img, f"{count:>4}  failure: {kind}", left, y, 0.5, COLOR_FAILURE)
        y += 22
    for label, count in data["events"].most_common():
        if y > 1085:
            break
        text(img, f"{count:>4}  {label}", left, y, 0.5)
        y += 22
    return img


def latest_run(log_path):
    """The number (as text) of the last numbered run in the log, or None."""
    last = None
    if os.path.exists(log_path):
        with open(log_path, encoding="utf-8", errors="replace") as f:
            for raw in f:
                m = LINE_RE.match(raw.rstrip("\n"))
                if m and m.group(2).isdigit():
                    last = m.group(2)
    return last


def generate_run_report(log_path, run, out_dir, basic=(), teleport=()):
    """Write run `run`'s report to <out_dir>/run<run>.png and return its path,
    or None if the run isn't in the log. Never raises: a report problem must
    not take a run down with it (returns None, having printed why)."""
    try:
        data = parse_run(log_path, run)
        if data is None:
            return None
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"run{run}.png")
        cv2.imwrite(path, draw_report(data, run, set(basic), set(teleport)))
        return path
    except Exception as error:
        print(f"[!] couldn't write the run report: {error}")
        return None
