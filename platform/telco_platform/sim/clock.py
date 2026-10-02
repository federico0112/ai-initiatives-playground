"""Simulated clock (UTC) and ISO 8601 helpers shared by the runner and adapters."""

import re
from datetime import datetime, timedelta, timezone

_DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


def parse_time(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError(f"time must carry a timezone: {value}")
    return dt.astimezone(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_duration(value: str) -> timedelta:
    m = _DURATION.match(value)
    if not m or value in ("P", "PT"):
        raise ValueError(f"unsupported ISO 8601 duration: {value}")
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return timedelta(days=d, hours=h, minutes=mi, seconds=s)


class SimClock:
    def __init__(self, start: str | datetime):
        self._now = parse_time(start)

    def now(self) -> datetime:
        return self._now

    def advance_to(self, t: str | datetime) -> datetime:
        t = parse_time(t)
        if t < self._now:
            raise ValueError(f"clock cannot go back from {iso(self._now)} to {iso(t)}")
        self._now = t
        return t

    def advance(self, delta: timedelta) -> datetime:
        return self.advance_to(self._now + delta)
