"""Item and event IDs.

Item IDs come in three modes chosen per kind in `qew-ledger.toml`: `time`
(default, merge-safe), `letters` (A..Z, AA..) and `counter` (integers, one
writer). Imported items keep the IDs they already have. Event IDs are always
time-ordered: they decide the fold order, so they are monotonic per process.
"""

import os
import re
import threading
import time
from collections.abc import Iterable

from qew.errors import QewError

MODES = ("time", "letters", "counter")
# 35 characters: digits and lowercase letters without `l`, which reads as `1`.
_ALPHABET = "0123456789abcdefghijkmnopqrstuvwxyz"
_EPOCH = 1_577_836_800  # 2020-01-01T00:00:00Z
_TIME_WIDTH = 7
_LOCK = threading.Lock()
_last = (0, 0)
_TIME_ID = re.compile(r"[0-9a-km-z]{9}")
_VALID = re.compile(r"[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*")


def is_time_id(value: str) -> bool:
    return bool(_TIME_ID.fullmatch(value))


def valid_id(value: str) -> bool:
    return bool(_VALID.fullmatch(value))


def _encode(number: int, width: int) -> str:
    characters = []
    for _ in range(width):
        number, digit = divmod(number, 35)
        characters.append(_ALPHABET[digit])
    return "".join(reversed(characters))


def _random_pair() -> int:
    return int.from_bytes(os.urandom(2), "big") % (35 * 35)


def _decode(value: str) -> int:
    return sum(_ALPHABET.index(c) * 35 ** i for i, c in enumerate(reversed(value)))


def event_id(after: str = "") -> str:
    """Seconds since 2020 in base 35 (7 characters) plus 2 more: a time-mode ID.

    It is always greater than `after` and than this process's last one. The pair counts up
    inside a second, so IDs and an item's events keep their order even when separate commands
    write them within the same second. Two branches minting in the same second collide at
    about 1/1225; `qew check` refuses duplicates.
    """
    global _last
    with _LOCK:
        seconds = int(time.time()) - _EPOCH
        pair = _random_pair() % 600
        floor = max(_last[0] * 1225 + _last[1], _decode(after) if after else -1)
        if seconds * 1225 + pair <= floor:
            seconds, pair = divmod(floor + 1, 1225)
        _last = (seconds, pair)
    return _encode(seconds, _TIME_WIDTH) + _encode(pair, 2)


def letters_after(value: str | None) -> str:
    """A, B .. Z, AA, AB .. ZZ, AAA .. (the spreadsheet column after `value`)."""
    if not value:
        return "A"
    digits = list(value)
    index = len(digits) - 1
    while index >= 0:
        if digits[index] != "Z":
            digits[index] = chr(ord(digits[index]) + 1)
            return "".join(digits)
        digits[index] = "A"
        index -= 1
    return "A" * (len(value) + 1)


def next_id(mode: str, existing: Iterable[str]) -> str:
    """The next free ID of `mode`, given every ID already in the ledger."""
    taken = set(existing)
    if mode == "time":
        # Greater than every time id already here: ordered locally, and unique even within a second.
        newest = max((i for i in taken if _TIME_ID.fullmatch(i)), default="")
        return event_id(newest)
    if mode == "letters":
        letters = [i for i in taken if re.fullmatch(r"[A-Z]+", i)]
        return letters_after(max(letters, key=lambda i: (len(i), i)) if letters else None)
    if mode == "counter":
        numbers = [int(i) for i in taken if i.isdigit()]
        return str(max(numbers, default=0) + 1)
    raise QewError(f"unknown id_mode `{mode}`; use one of {', '.join(MODES)}", 1)


def sort_key(item_id: str) -> tuple[int, str]:
    """Shorter first, then lexical: right for fixed-width time IDs, letters and counters alike."""
    return (len(item_id), item_id)


def parse_modes(value: object, kinds: Iterable[str]) -> dict[str, str]:
    """`id_mode = "time"` for every kind, or a table with one mode per kind (others default to time)."""
    modes = {kind: "time" for kind in kinds}
    if value is None:
        return modes
    if isinstance(value, str):
        value = {kind: value for kind in modes}
    if not isinstance(value, dict):
        raise QewError("qew-ledger.toml: id_mode is a mode name or a table of kind = mode", 1)
    for kind, mode in value.items():
        if kind not in modes or mode not in MODES:
            raise QewError(f"qew-ledger.toml: id_mode.{kind} = {mode!r} is not a kind with one of {', '.join(MODES)}", 1)
        modes[kind] = mode
    return modes
