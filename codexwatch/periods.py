"""Turn "today", "7d", "month" or "2026-10" into a time range on the local calendar."""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta

HELP = ("today, yesterday, 5h, 24h, 7d, 30d, NNd, NNh, week, month, year, all, "
        "YYYY, YYYY-MM or YYYY-MM-DD")


def _midnight(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def parse(period: str | None, now: float | None = None) -> tuple[float | None, float | None, str]:
    """(since, until, label). None means unbounded on that side."""
    now = now or time.time()
    p = (period or "all").strip().lower()
    today = _midnight(datetime.fromtimestamp(now))
    if p == "all":
        return None, None, "all time"
    if p == "today":
        return today.timestamp(), None, "today"
    if p == "yesterday":
        y = today - timedelta(days=1)
        return y.timestamp(), today.timestamp(), "yesterday"
    if p == "week":
        start = today - timedelta(days=today.weekday())
        return start.timestamp(), None, "this week"
    if p == "month":
        return today.replace(day=1).timestamp(), None, "this month"
    if p == "year":
        return today.replace(month=1, day=1).timestamp(), None, "this year"
    m = re.fullmatch(r"(\d+)\s*([hdw])", p)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        secs = {"h": 3600, "d": 86400, "w": 7 * 86400}[unit] * n
        if unit == "h":
            return now - secs, None, f"last {n} hour{'s' if n != 1 else ''}"
        # Whole local days: "7d" is today and the six days before it.
        start = today - timedelta(days=(n * (7 if unit == "w" else 1)) - 1)
        return start.timestamp(), None, f"last {n} {'week' if unit == 'w' else 'day'}{'s' if n != 1 else ''}"
    m = re.fullmatch(r"(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", p)
    if m:
        y, mo, d = int(m.group(1)), m.group(2), m.group(3)
        if d:
            start = datetime(y, int(mo), int(d))
            return start.timestamp(), (start + timedelta(days=1)).timestamp(), f"{start.day} {start:%b %Y}"
        if mo:
            start = datetime(y, int(mo), 1)
            end = datetime(y + (start.month == 12), start.month % 12 + 1, 1)
            return start.timestamp(), end.timestamp(), start.strftime("%B %Y")
        return datetime(y, 1, 1).timestamp(), datetime(y + 1, 1, 1).timestamp(), str(y)
    raise ValueError(f"unknown period '{period}'. Use one of: {HELP}")


def parse_date(s: str | None, end: bool = False) -> float | None:
    """A --since/--until value: YYYY-MM-DD (local midnight) or YYYYMMDD."""
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(s, fmt)
            if end and fmt in ("%Y-%m-%d", "%Y%m%d"):
                dt += timedelta(days=1)
            return dt.timestamp()
        except ValueError:
            continue
    raise ValueError(f"unknown date '{s}'; use YYYY-MM-DD")


def day_key(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
