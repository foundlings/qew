"""Item and event IDs.

Item IDs come in three modes chosen per kind in `qew-ledger.toml`: `base35-time`
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

MODES = ("base35-time", "letters", "counter")
DEFAULT_MODES = {"epic": "letters", "issue": "base35-time", "task": "base35-time"}  # phases read A, B .. AA; the rest merge-safe
# 35 characters: digits and lowercase letters without `l`, which reads as `1`.
_ALPHABET = "0123456789abcdefghijkmnopqrstuvwxyz"
_EPOCH = 1_577_836_800  # 2020-01-01T00:00:00Z
_TIME_WIDTH = 7
_LOCK = threading.Lock()
_last = (0, 0)
_TIME_ID = re.compile(r"[0-9a-km-z]{9}")
_VALID = re.compile(r"[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*")


def is_base35_time_id(value: str) -> bool:
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
    """Seconds since 2020 in base 35 (7 characters) plus 2 more: a base35-time ID.

    It is always greater than `after` and than this process's last one. The pair counts up
    inside a second, so IDs and an item's events keep their order even when separate commands
    write them within the same second. Two branches minting in the same second collide at
    about 1/600 (1/300 after the same predecessor); `qew check` refuses duplicates.
    """
    global _last
    with _LOCK:
        seconds = int(time.time()) - _EPOCH
        pair = _random_pair() % 600
        floor = max(_last[0] * 1225 + _last[1], _decode(after) if after else -1)
        if seconds * 1225 + pair <= floor:
            # Not +1: two machines after the same event in the same second would pick the same successor, and the
            # event file would conflict in git. A random step keeps the order and makes that a 1 in 300 chance.
            room = 35 ** (_TIME_WIDTH + 2) - 1 - floor  # ids near the top of the 9 character space cannot jump far
            if room < 1:
                raise QewError("no event id is left after the newest one", 1)
            seconds, pair = divmod(floor + 1 + _random_pair() % min(300, room), 1225)
        _last = (seconds, pair)
    return _encode(seconds, _TIME_WIDTH) + _encode(pair, 2)


LETTER_JUMP = 3 * 26 * 26  # ranks: a gap wider than three blocks of two-letter ids ends the main run; what lies beyond is an outlier


def letter_rank(value: str) -> int:
    """A=1 .. Z=26, AA=27 .. ZZ=702, AAA=703 ..: the position in the letter sequence."""
    rank = 0
    for character in value:
        rank = rank * 26 + (ord(character) - 64)
    return rank


def letters_head(ids: Iterable[str]) -> str | None:
    """The last id of the main run of letter ids: from the lowest id on, each next id is at most `LETTER_JUMP` ranks away (ADF to PID is about 10,000).
    Hand-named outliers far past the run (PIE after ADF) are not the sequence and do not move its head."""
    ranked = sorted((letter_rank(i), i) for i in ids if re.fullmatch(r"[A-Z]+", i))
    if not ranked:
        return None
    head = ranked[0]
    for rank, name in ranked[1:]:
        if rank - head[0] > LETTER_JUMP:
            break
        head = (rank, name)
    return head[1]


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
    if mode == "base35-time":
        # Greater than every base35-time id already here: ordered locally, and unique even within a second.
        newest = max((i for i in taken if _TIME_ID.fullmatch(i)), default="")
        return event_id(newest)
    if mode == "letters":
        candidate = letters_after(letters_head(taken))
        while candidate in taken:  # an outlier sits exactly on the next letter
            candidate = letters_after(candidate)
        return candidate
    if mode == "counter":
        numbers = [int(i) for i in taken if i.isdigit()]
        return str(max(numbers, default=0) + 1)
    raise QewError(f"unknown id_mode `{mode}`; use one of {', '.join(MODES)}", 1)


def sort_key(item_id: str) -> tuple[int, str]:
    """Shorter first, then lexical: right for fixed-width time IDs, letters and counters alike."""
    return (len(item_id), item_id)


def parse_modes(value: object, kinds: Iterable[str]) -> dict[str, str]:
    """`id_mode = "base35-time"` for every kind, or a table with one mode per kind (a kind left out gets its default:
    letters for epics, base35-time for issues and tasks)."""
    modes = {kind: DEFAULT_MODES.get(kind, "base35-time") for kind in kinds}
    if value is None:
        return modes
    if isinstance(value, str):
        value = {kind: value for kind in modes}
    if not isinstance(value, dict):
        raise QewError("qew-ledger.toml: id_mode is a mode name or a table of kind = mode", 1)
    for kind, mode in value.items():
        if mode == "time":
            raise QewError(f"qew-ledger.toml: id_mode.{kind} = 'time' was renamed 'base35-time'; change it there", 1)
        if kind not in modes or mode not in MODES:
            raise QewError(f"qew-ledger.toml: id_mode.{kind} = {mode!r} is not a kind with one of {', '.join(MODES)}", 1)
        modes[kind] = mode
    return modes
