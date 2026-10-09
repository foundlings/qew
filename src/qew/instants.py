"""UTC instants: `YYYY-MM-DDTHH:MM:SSZ`, or a date meaning midnight UTC. Nothing else is accepted."""

from datetime import datetime, timedelta, UTC
import re
from collections.abc import Iterable

from qew.errors import QewError

_INSTANT = re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})Z")
_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def parse_instant(text: str) -> datetime | None:
    """The instant `text` spells, or None when it is not exactly `YYYY-MM-DDTHH:MM:SSZ` (an impossible date is None)."""
    found = _INSTANT.fullmatch(text)
    return _build(found.groups()) if found else None


def parse_date_or_instant(text: str) -> datetime | None:
    found = _DATE.fullmatch(text)
    return _build((*found.groups(), "0", "0", "0")) if found else parse_instant(text)


def require_window_start(text: str) -> datetime:
    start = parse_date_or_instant(text)
    if start is None:
        raise QewError(f"`{text}` is not a UTC instant: use YYYY-MM-DDTHH:MM:SSZ or a date YYYY-MM-DD (midnight UTC)", 2)
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
