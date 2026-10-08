"""UTC instants: the --since argument and GitHub timestamps."""

from datetime import datetime, timezone
import re

from qew.errors import QewError

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_INSTANT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def parse_since(text: str) -> datetime:
    """An ISO 8601 UTC instant (2026-10-07T09:00:00Z) or a date (midnight UTC)."""
    try:
        if _INSTANT.fullmatch(text):
            return datetime.strptime(text, FORMAT).replace(tzinfo=timezone.utc)
        if _DATE.fullmatch(text):
            return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    raise QewError("--since must be a UTC timestamp like 2026-10-07T09:00:00Z or a date like 2026-10-07", 2)


def parse_github_time(text: object, context: str) -> datetime:
    if isinstance(text, str) and _INSTANT.fullmatch(text):
        try:
            return datetime.strptime(text, FORMAT).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    raise QewError(f"indeterminate: invalid timestamp in {context}", 4)


def format_instant(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime(FORMAT)
