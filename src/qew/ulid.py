"""ULIDs: 26 sortable Crockford base32 characters (48-bit milliseconds, 80 random bits)."""

import os
import threading
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_LOCK = threading.Lock()
_last = (0, 0)


def new_ulid() -> str:
    """A ULID that sorts after every earlier one made by this process."""
    global _last
    with _LOCK:
        millis = time.time_ns() // 1_000_000
        random = int.from_bytes(os.urandom(10), "big")
        last_millis, last_random = _last
        if millis <= last_millis:
            millis, random = last_millis, last_random + 1
            if random >= 1 << 80:
                millis, random = millis + 1, int.from_bytes(os.urandom(10), "big")
        _last = (millis, random)
    return _encode(millis, 10) + _encode(random, 16)


def is_ulid(value: str) -> bool:
    return len(value) == 26 and all(character in _ALPHABET for character in value)


def _encode(number: int, length: int) -> str:
    characters = []
    for _ in range(length):
        characters.append(_ALPHABET[number & 31])
        number >>= 5
    return "".join(reversed(characters))
