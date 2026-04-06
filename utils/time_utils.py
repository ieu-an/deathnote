"""
Time helpers for ping windows using US Eastern time (handles EDT/EST via zoneinfo).
"""

from __future__ import annotations

import re
from datetime import datetime, time
from typing import Optional
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
# Optional seconds so values like 09:00:00 (from exports or manual DB edits) still parse.
_HHMM = re.compile(r"^\s*(\d{1,2}):(\d{2})(?::\d{2})?\s*$")


def parse_hhmm(value: str) -> Optional[time]:
    """Parse 'HH:MM' or 'HH:MM:SS' (24h) into a time object, or None if invalid."""
    m = _HHMM.match((value or "").strip())
    if not m:
        return None
    h, mm = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mm <= 59):
        return None
    return time(hour=h, minute=mm)


def now_eastern() -> datetime:
    """Current local time in America/New_York."""
    return datetime.now(tz=EASTERN)


def is_within_ping_window(
    start: str,
    end: str,
    *,
    at: Optional[datetime] = None,
) -> bool:
    """
    Return True if ``at`` (default: now in Eastern) falls between start/end.

    Times are compared in Eastern. Supports overnight windows when start > end
    (e.g. 22:00–06:00).
    """
    ts = parse_hhmm((start or "").strip())
    te = parse_hhmm((end or "").strip())
    if ts is None or te is None:
        return False

    dt = at or now_eastern()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=EASTERN)
    else:
        dt = dt.astimezone(EASTERN)

    t = dt.time()
    ts_naive = time(ts.hour, ts.minute)
    te_naive = time(te.hour, te.minute)

    if ts_naive <= te_naive:
        return ts_naive <= t <= te_naive
    # Overnight: active from start through midnight, or from midnight through end
    return t >= ts_naive or t <= te_naive


def format_eastern_timestamp(dt: datetime) -> str:
    """Human-readable Eastern timestamp for embed footers."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=EASTERN)
    else:
        dt = dt.astimezone(EASTERN)
    return dt.strftime("%Y-%m-%d %H:%M:%S %Z")
