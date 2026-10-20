"""UTC instants: `YYYY-MM-DDTHH:MM:SSZ`, or a date meaning midnight UTC. Nothing else is accepted."""

from datetime import datetime, timedelta, UTC
import re
from collections.abc import Iterable

from qew.errors import QewError

_INSTANT = re.compile(r"\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):\d{2}:\d{2}Z", re.ASCII)  # hour 24 would roll to the next day in fromisoformat
_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def parse_instant(text: str) -> datetime | None:
    """The instant `text` spells, or None when it is not exactly `YYYY-MM-DDTHH:MM:SSZ` (an impossible date is None)."""
    if _INSTANT.fullmatch(text) is None:
        return None
    try:
        return datetime.fromisoformat(text)  # C-implemented; an impossible date raises
    except ValueError:
        return None


def parse_date_or_instant(text: str) -> datetime | None:
    found = _DATE.fullmatch(text)
    return _build((*found.groups(), "0", "0", "0")) if found else parse_instant(text)


def require_window_start(text: str) -> datetime:
    start = parse_date_or_instant(text)
    if start is None:
        raise QewError(f"`{text}` is not a UTC instant: use YYYY-MM-DDTHH:MM:SSZ or a date YYYY-MM-DD (midnight UTC)", 2)
    return start


_AGE = re.compile(r"(\d+)([smhdw])", re.ASCII)
_AGE_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def require_since(text: str, moment: datetime | None = None) -> datetime:
    """The start `text` names: a UTC instant, a date (midnight UTC) or an age back from now (`90m`, `2h`, `3d`, `1w`)."""
    age = _AGE.fullmatch(text)
    if age is not None:
        return (moment or now()) - timedelta(seconds=int(age.group(1)) * _AGE_UNITS[age.group(2)])
    start = parse_date_or_instant(text)
    if start is None:
        raise QewError(f"`{text}` is not a start: use YYYY-MM-DDTHH:MM:SSZ, a date YYYY-MM-DD (midnight UTC) or an age such as 90m, 2h, 3d, 1w", 2)
    return start


def format_instant(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def days_ago(days: int) -> datetime:
    return now() - timedelta(days=days)


def _build(parts: Iterable[str]) -> datetime | None:
    try:
        year, month, day, hour, minute, second = (int(part) for part in parts)
        return datetime(year, month, day, hour, minute, second, tzinfo=UTC)
    except ValueError:
        return None
