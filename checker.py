"""Independent checker for a study plan: the six rules of Track A.

It does NOT trust the agent. It re-reads deadlines.csv with its own parser, expands busy.ics itself,
and checks plan.ics (+ the shortfall list in plan.json) against the preferences.

    python checker.py runs/messy/plan.ics --scenario messy
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import icalendar

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from planner.calendar_utils import WEEKDAYS, expand_busy, overlaps  # noqa: E402

DEFAULT_ESTIMATES = {"quiz": 2, "problem set": 4, "essay": 6, "project": 10, "lab report": 3}
FALLBACK_ESTIMATE = 3
EPS = timedelta(seconds=1)


# ------------------------------------------------------------------ reference data
def task_key(course: str, task: str) -> str:
    return f"{course.strip()}|{task.strip()}".lower()


def parse_due_reference(raw: str, now: datetime) -> datetime:
    """The checker's own date parser (kept separate from the agent's code on purpose)."""
    s = raw.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt if fmt.endswith("%M") else dt.replace(hour=23, minute=59)
        except ValueError:
            pass
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)          # US style: month/day/year
    if m:
        return datetime(int(m[3]), int(m[1]), int(m[2]), 23, 59)
    m = re.fullmatch(r"([A-Za-z]{3})[a-z]* (\d{1,2})(?: (\d{1,2})(?::(\d{2}))?\s*(am|pm))?", s, re.I)
    if m:
        month = datetime.strptime(m[1][:3].title(), "%b").month
        hour, minute = 23, 59
        if m[3]:
            hour, minute = int(m[3]) % 12 + (12 if m[5].lower() == "pm" else 0), int(m[4] or 0)
        dt = datetime(now.year, month, int(m[2]), hour, minute)
        return dt if dt > now - timedelta(days=180) else dt.replace(year=now.year + 1)
    raise ValueError(f"checker cannot parse due date {raw!r}")


def reference_tasks(csv_path: Path, now: datetime) -> tuple[dict, list]:
    """Return ({task_key: {due, est}}, [past-due task keys]) after de-duplication."""
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    tasks: dict[str, dict] = {}
    for r in rows:
        key = task_key(r["course"], r["task"])
        est_raw = (r.get("est_hours") or "").strip()
        if key in tasks and not est_raw:
            continue                                   # duplicate without estimate: keep the other one
        if est_raw:
            est = float(est_raw)
        else:
            name = r["task"].lower()
            est = next((h for k, h in DEFAULT_ESTIMATES.items() if k in name), FALLBACK_ESTIMATE)
        tasks[key] = {"due": parse_due_reference(r["due"], now), "est": est,
                      "title": f"{r['course'].strip()} · {r['task'].strip()}"}
    past = [k for k, t in tasks.items() if t["due"] <= now]
    return {k: t for k, t in tasks.items() if k not in past}, past


def read_plan_ics(path: Path) -> list[dict]:
    cal = icalendar.Calendar.from_ical(path.read_bytes())
    blocks = []
    for ev in cal.walk("VEVENT"):
        for field in ("UID", "DTSTAMP", "DTSTART", "DTEND", "SUMMARY"):
            if ev.get(field) is None:
                raise ValueError(f"event without {field}: {ev.get('SUMMARY')}")
        blocks.append({"start": ev.get("DTSTART").dt.replace(tzinfo=None),
                       "end": ev.get("DTEND").dt.replace(tzinfo=None),
                       "title": str(ev.get("SUMMARY")),
                       "task_key": str(ev.get("X-STUDYPLAN-TASK", "")).lower()})
    return sorted(blocks, key=lambda b: b["start"])


# ------------------------------------------------------------------ the six rules
def check_plan(blocks: list[dict], busy: list[dict], prefs: dict, tasks: dict | None = None,
               past_due: list | None = None, shortfall_keys: set | None = None,
               ics_ok: tuple[bool, str] = (True, "not checked")) -> list[dict]:
    """Run the six rules. Blocks have datetime 'start'/'end' and a 'task_key'. Returns one dict per rule."""
    results = []

    def rule(n, name, problems):
        results.append({"rule": n, "name": name, "ok": not problems, "problems": problems[:10]})

    # 1. no block overlaps anything in busy.ics
    p = [f"{b['title']} {b['start']:%a %d %b %H:%M} overlaps '{x['summary']}'"
         for b in blocks for x in busy if overlaps(b["start"], b["end"], x["start"], x["end"])]
    rule(1, "No overlap with busy.ics", p)

    # 2. inside the working window, never on a blocked weekday
    blocked = set(prefs.get("blocked_weekdays", ["Sun"]))
    p = []
    for b in blocks:
        day_start = b["start"].replace(hour=prefs["day_start_hour"], minute=0, second=0)
        day_end = b["start"].replace(hour=prefs["day_end_hour"], minute=0, second=0)
        if b["start"] < day_start or b["end"] > day_end or b["end"].date() != b["start"].date():
            p.append(f"{b['title']} {b['start']:%a %d %b %H:%M}-{b['end']:%H:%M} outside "
                     f"{prefs['day_start_hour']}:00-{prefs['day_end_hour']}:00")
        if WEEKDAYS[b["start"].weekday()] in blocked:
            p.append(f"{b['title']} on a blocked day ({b['start']:%a %d %b})")
    rule(2, f"Inside {prefs['day_start_hour']}:00-{prefs['day_end_hour']}:00, not on {', '.join(sorted(blocked))}", p)

    # 3. blocks of min..max hours, at most max_hours_per_day per day
    p, per_day = [], {}
    for b in blocks:
        h = (b["end"] - b["start"]).total_seconds() / 3600
        if not prefs["min_block_hours"] - 1e-9 <= h <= prefs["max_block_hours"] + 1e-9:
            p.append(f"{b['title']} {b['start']:%d %b %H:%M} lasts {h:g} h")
        per_day[b["start"].date()] = per_day.get(b["start"].date(), 0) + h
    p += [f"{d:%a %d %b}: {h:g} h scheduled" for d, h in per_day.items() if h > prefs["max_hours_per_day"] + 1e-9]
    rule(3, f"Blocks {prefs['min_block_hours']}-{prefs['max_block_hours']} h, max {prefs['max_hours_per_day']} h/day", p)

    # 4. each task finished >= N hours before its deadline
    if tasks is not None:
        buffer = timedelta(hours=prefs["finish_hours_before_due"])
        p = []
        for b in blocks:
            t = tasks.get(b["task_key"])
            if t is None:
                p.append(f"block '{b['title']}' belongs to no open task (past due or unknown)")
            elif b["end"] > t["due"] - buffer + EPS:
                p.append(f"{b['title']} ends {b['end']:%d %b %H:%M}, limit {t['due'] - buffer:%d %b %H:%M}")
        rule(4, f"Done >= {prefs['finish_hours_before_due']} h before each deadline", p)

    # 5. hours == estimate, or the task is flagged as a shortfall; past-due tasks not scheduled
    if tasks is not None:
        shortfall_keys = shortfall_keys or set()
        done = {}
        for b in blocks:
            done[b["task_key"]] = done.get(b["task_key"], 0) + (b["end"] - b["start"]).total_seconds() / 3600
        p = []
        for k, t in tasks.items():
            h = done.get(k, 0)
            if abs(h - t["est"]) > 1e-6 and k not in shortfall_keys:
                p.append(f"{t['title']}: {h:g} h scheduled, estimate {t['est']:g} h, not flagged as shortfall")
            if h > t["est"] + 1e-6:
                p.append(f"{t['title']}: {h:g} h scheduled, more than the estimate {t['est']:g} h")
        p += [f"past-due task {k} was scheduled" for k in (past_due or []) if k in done]
        rule(5, "Hours = estimate, or flagged as shortfall", p)

    # 6. blocks don't overlap each other; plan.ics parses
    p = [f"{a['title']} {a['start']:%d %b %H:%M} overlaps {b['title']}"
         for i, a in enumerate(blocks) for b in blocks[i + 1:] if overlaps(a["start"], a["end"], b["start"], b["end"])]
    if not ics_ok[0]:
        p.append(f"plan.ics problem: {ics_ok[1]}")
    rule(6, "No overlaps between blocks; plan.ics valid", p)
    return results


def print_report(results: list[dict]) -> bool:
    for r in results:
        print(f"  {'PASS' if r['ok'] else 'FAIL'}  rule {r['rule']}: {r['name']}")
        for prob in r["problems"]:
            print(f"          - {prob}")
    ok = all(r["ok"] for r in results)
    print(f"  => {'ALL RULES PASS' if ok else 'SOME RULES FAIL'}")
    return ok


def check_files(plan_ics: Path, scenario: str) -> tuple[bool, list[dict]]:
    """Check a plan.ics written for one scenario. Returns (all_ok, results)."""
    cfg = json.loads((ROOT / "data" / "scenarios.json").read_text(encoding="utf-8"))
    prefs = {**cfg["base_prefs"], "blocked_weekdays": cfg["scenarios"][scenario]["final_blocked_weekdays"]}
    now = datetime.fromisoformat(prefs["now"])
    tasks, past = reference_tasks(ROOT / "data" / "scenarios" / scenario / "deadlines.csv", now)
    try:
        blocks, ics_ok = read_plan_ics(plan_ics), (True, "ok")
    except Exception as e:                                  # unreadable calendar fails rule 6
        blocks, ics_ok = [], (False, str(e))
    horizon = max(t["due"] for t in tasks.values()) + timedelta(days=1)
    busy = expand_busy(ROOT / "data" / "busy.ics", now, horizon)
    plan_json = plan_ics.with_suffix(".json")
    shortfall = set()
    if plan_json.exists():
        shortfall = {s["task_key"].lower() for s in json.loads(plan_json.read_text(encoding="utf-8")).get("shortfall", [])}
    results = check_plan(blocks, busy, prefs, tasks, past, shortfall, ics_ok)
    return all(r["ok"] for r in results), results


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan_ics", type=Path)
    ap.add_argument("--scenario", required=True)
    a = ap.parse_args()
    print(f"Checking {a.plan_ics} for scenario '{a.scenario}'")
    ok, res = check_files(a.plan_ics, a.scenario)
    print_report(res)
    sys.exit(0 if ok else 1)
