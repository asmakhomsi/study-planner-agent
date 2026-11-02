"""Small calendar helpers shared by the tools and the checker.

All times are *floating* local times (naive datetimes, no time zone), which is what
busy.ics uses. An all-day event blocks the whole day (00:00 to 24:00).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path

import icalendar
import recurring_ical_events

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _to_naive(value) -> datetime:
    """icalendar gives date (all-day) or datetime (maybe tz-aware); return a naive datetime."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    raise TypeError(f"Unexpected date value: {value!r}")


def expand_busy(ics_path: str | Path, start: datetime, end: datetime) -> list[dict]:
    """Every busy interval between start and end, with weekly recurrences (RRULE) expanded."""
    cal = icalendar.Calendar.from_ical(Path(ics_path).read_bytes())
    out = []
    for ev in recurring_ical_events.of(cal).between(start, end):
        s, e = ev.get("DTSTART").dt, ev.get("DTEND").dt
        all_day = not isinstance(s, datetime)
        out.append({"start": _to_naive(s), "end": _to_naive(e),
                    "summary": str(ev.get("SUMMARY", "busy")), "all_day": all_day})
    return sorted(out, key=lambda b: b["start"])


def overlaps(a_start, a_end, b_start, b_end) -> bool:
    """Half-open intervals [a_start, a_end) and [b_start, b_end) overlap."""
    return a_start < b_end and b_start < a_end


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M")
