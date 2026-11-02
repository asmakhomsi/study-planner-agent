# Study-plan computation, written to run inside run_python's sandbox.
# In offline mode the scripted LLM sends exactly this code (with PARAMS filled in);
# with DeepSeek the model writes its own version. Reads deadlines.csv and busy.ics from
# the working directory and prints ONE JSON object on the last line.
import csv
import json
import math
import re
from datetime import datetime, time, timedelta

import icalendar
import recurring_ical_events
from dateutil import parser as dparser

PARAMS = __PARAMS__

NOW = datetime.fromisoformat(PARAMS["now"])
DAY_START, DAY_END = PARAMS["day_start_hour"], PARAMS["day_end_hour"]
MAX_PER_DAY, MIN_BLOCK, MAX_BLOCK = PARAMS["max_hours_per_day"], PARAMS["min_block_hours"], PARAMS["max_block_hours"]
BUFFER = timedelta(hours=PARAMS["finish_hours_before_due"])
BREAK = timedelta(minutes=PARAMS.get("break_minutes", 30))
BLOCKED = set(PARAMS["blocked_weekdays"])
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DEFAULTS = {"quiz": 2, "problem set": 4, "essay": 6, "project": 10, "lab report": 3}
assumptions = []


# --- parse_due ---
def parse_due(raw):
    """Accept ISO, US month/day/year and 'Nov 12 5pm'; state every assumption made."""
    s = raw.strip()
    has_time = bool(re.search(r"\d:\d\d|\d\s*(am|pm)\b", s, re.I))
    has_year = bool(re.search(r"\b\d{4}\b", s))
    default = datetime(NOW.year, 1, 1, 0, 0) if has_time else datetime(NOW.year, 1, 1, 23, 59)
    dt = dparser.parse(s, dayfirst=False, default=default)
    if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", s):
        assumptions.append(f"'{s}' read as month/day/year (US format)")
    if not has_time:
        assumptions.append(f"'{s}' has no time: assumed due at 23:59")
    if not has_year:
        if dt < NOW - timedelta(days=180):
            dt = dt.replace(year=dt.year + 1)
        assumptions.append(f"'{s}' has no year: assumed {dt.year}")
    return dt
# --- end parse_due ---


# 1. Read, de-duplicate, fill missing estimates, drop past-due tasks
rows = list(csv.DictReader(open("deadlines.csv", encoding="utf-8")))
groups = {}                                   # same course + task = duplicate rows
for r in rows:
    groups.setdefault(f"{r['course'].strip()}|{r['task'].strip()}".lower(), []).append(r)
tasks = {}
for key, same in groups.items():
    r = next((x for x in same if (x.get("est_hours") or "").strip()), same[0])   # prefer a row with an estimate
    if len(same) > 1:
        assumptions.append(f"{r['course']} {r['task']} appears {len(same)} times: kept one row (the one with an estimate)")
    est_raw = (r.get("est_hours") or "").strip()
    if est_raw:
        est = float(est_raw)
    else:
        kind = next((k for k in DEFAULTS if k in r["task"].lower()), None)
        est = DEFAULTS.get(kind, 3)
        assumptions.append(f"{r['course']} {r['task']}: no estimate, used default {est:g} h "
                           f"({'type ' + kind if kind else 'unknown type'})")
    tasks[key] = {"task_key": key, "title": f"{r['course'].strip()} · {r['task'].strip()}",
                  "due": parse_due(r["due"]), "est": est}

skipped = [{"task_key": t["task_key"], "title": t["title"], "due": t["due"].isoformat(timespec="minutes")}
           for t in tasks.values() if t["due"] <= NOW]
open_tasks = sorted((t for t in tasks.values() if t["due"] > NOW), key=lambda t: t["due"])   # earliest deadline first

# 2. Busy intervals (weekly recurrences expanded, all-day events block the day)
horizon = max([t["due"] for t in open_tasks], default=NOW) + timedelta(days=1)
cal = icalendar.Calendar.from_ical(open("busy.ics", "rb").read())
busy = []
for ev in recurring_ical_events.of(cal).between(NOW, horizon):
    s, e = ev.get("DTSTART").dt, ev.get("DTEND").dt
    if not isinstance(s, datetime):
        s, e = datetime.combine(s, time.min), datetime.combine(e, time.min)
    busy.append((s.replace(tzinfo=None), e.replace(tzinfo=None)))
busy.sort()

# 3. Free slots = working window minus busy intervals, day by day (blocked weekdays skipped)
def round_up_half_hour(dt):
    dt = dt.replace(second=0, microsecond=0)
    extra = (30 - dt.minute % 30) % 30
    return dt + timedelta(minutes=extra)

free = []          # list of [start, end], chronological
day = NOW.date()
while datetime.combine(day, time.min) < horizon:
    if WEEKDAYS[day.weekday()] not in BLOCKED:
        start = max(datetime.combine(day, time(DAY_START)), round_up_half_hour(NOW))
        end = datetime.combine(day, time(DAY_END))
        cursor = start
        for bs, be in busy:
            if be <= cursor or bs >= end:
                continue
            if bs > cursor:
                free.append([cursor, min(bs, end)])
            cursor = max(cursor, be)
        if cursor < end:
            free.append([cursor, end])
    day += timedelta(days=1)

# 4. Greedy earliest-deadline-first: fill 1-2 h blocks, respect the daily cap and the 24 h buffer
used_per_day = {}
blocks, shortfall = [], []
hours = lambda a, b: (b - a).total_seconds() / 3600
for t in open_tasks:
    remaining, limit = t["est"], t["due"] - BUFFER
    for seg in free:
        while remaining > 1e-9 and seg[0] < min(seg[1], limit):
            d = seg[0].date()
            avail = min(hours(seg[0], min(seg[1], limit)), MAX_PER_DAY - used_per_day.get(d, 0))
            avail = math.floor(avail * 2 + 1e-9) / 2       # half-hour grid, never more than what is free
            length = min(MAX_BLOCK, remaining, avail)
            if 0 < remaining - length < MIN_BLOCK:          # never leave a piece shorter than a block
                length = remaining - MIN_BLOCK if remaining - MIN_BLOCK >= MIN_BLOCK else (
                    remaining if remaining <= min(MAX_BLOCK, avail) else 0)
            if length < MIN_BLOCK - 1e-9:
                break                                       # this segment (or day) cannot take a block
            b_start, b_end = seg[0], seg[0] + timedelta(hours=length)
            blocks.append({"title": t["title"], "task_key": t["task_key"],
                           "start": b_start.isoformat(timespec="minutes"), "end": b_end.isoformat(timespec="minutes")})
            used_per_day[d] = used_per_day.get(d, 0) + length
            remaining -= length
            seg[0] = b_end + BREAK                          # short break before the next block
        if remaining <= 1e-9:
            break
    if remaining > 1e-9:
        shortfall.append({"task_key": t["task_key"], "title": t["title"], "needed": t["est"],
                          "scheduled": t["est"] - remaining, "missing": remaining,
                          "due": t["due"].isoformat(timespec="minutes")})

blocks.sort(key=lambda b: b["start"])
assumptions = list(dict.fromkeys(assumptions))   # drop repeats, keep order
print(f"{len(open_tasks)} open tasks, {len(blocks)} blocks, {len(shortfall)} shortfall, {len(skipped)} past due")
print(json.dumps({"blocks": blocks, "shortfall": shortfall, "skipped_past_due": skipped,
                  "assumptions": assumptions,
                  "tasks": [{"task_key": t["task_key"], "est": t["est"], "due": t["due"].isoformat(timespec="minutes")}
                            for t in open_tasks]}, ensure_ascii=False))
