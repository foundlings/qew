"""Hidden-text scan: advisory findings for one string, from a vocabulary pinned in this module.

No dependency, no ledger or forge coupling, and no read of the host's Unicode database, so findings are
identical on every machine. Text is never normalised, hidden or rejected here; callers annotate it.

Finding kinds (sorted): `bidi_control`, `confusable`, `unicode_tags`, `zero_width`. Legitimate emoji
are exempt: joiner sequences between two emoji bases, variation selectors and skin tones on emoji
bases, keycaps on [0-9#*], and tag sequences spelling a recommended subdivision flag.
"""

import hashlib

UNICODE_RELEASE = "16.0"  # the reviewed release the ranges below were taken from

ZERO_WIDTH = frozenset((0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF))
BIDI_CONTROL = frozenset((0x061C, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A)))
TAG_FIRST, TAG_LAST = 0xE0000, 0xE007F
TAG_CANCEL = 0xE007F
JOINER = 0x200D
KEYCAP = 0x20E3
BLACK_FLAG = 0x1F3F4
SUBDIVISION_FLAGS = ("gbeng", "gbsct", "gbwls")
VARIATION_SELECTORS = (0xFE00, 0xFE0F)
SKIN_TONES = (0x1F3FB, 0x1F3FF)
# Characters that read as ASCII letters or digits: fullwidth, mathematical alphanumerics, Roman numerals, ligatures.
CONFUSABLE_RANGES = ((0xFF10, 0xFF19), (0xFF21, 0xFF3A), (0xFF41, 0xFF5A), (0x1D400, 0x1D7FF), (0x2160, 0x217F), (0xFB00, 0xFB06))
# Greek and Cyrillic only count beside ASCII letters or digits.
LOOKALIKE_SCRIPTS = ((0x0370, 0x03FF), (0x1F00, 0x1FFF), (0x0400, 0x052F), (0x1C80, 0x1C8F), (0x2DE0, 0x2DFF), (0xA640, 0xA69F))
EMOJI_BASES = (
    (0x00A9, 0x00A9), (0x00AE, 0x00AE), (0x203C, 0x203C), (0x2049, 0x2049), (0x2122, 0x2122), (0x2139, 0x2139),
    (0x2194, 0x21AA), (0x231A, 0x23FF), (0x24C2, 0x24C2), (0x25AA, 0x25FE), (0x2600, 0x27BF), (0x2934, 0x2935),
    (0x2B05, 0x2B55), (0x3030, 0x3030), (0x303D, 0x303D), (0x3297, 0x3299),
    (0x1F000, 0x1F3FA), (0x1F400, 0x1FAFF),
)

KINDS = ("bidi_control", "confusable", "unicode_tags", "zero_width")
_DESCRIPTIONS = {
    "bidi_control": "bidirectional control characters",
    "confusable": "confusable characters",
    "unicode_tags": "Unicode tag characters",
    "zero_width": "zero-width characters",
}


def describe(kind: str) -> str:
    return f"contains {_DESCRIPTIONS[kind]}"


def vocabulary_digest() -> str:
    """SHA-256 of the pinned tables, so an edit to the vocabulary is a visible, reviewed change."""
    tables = (sorted(ZERO_WIDTH), sorted(BIDI_CONTROL), TAG_FIRST, TAG_LAST, SUBDIVISION_FLAGS, VARIATION_SELECTORS,
              SKIN_TONES, CONFUSABLE_RANGES, LOOKALIKE_SCRIPTS, EMOJI_BASES, UNICODE_RELEASE)
    return hashlib.sha256(repr(tables).encode("ascii")).hexdigest()


PINNED_DIGEST = "1e667408aa0f732f3e32dff29ae31e1b69a0978d7b153196644ac1ad9d199acb"


def covers(code: int) -> bool:
    """True for characters this vocabulary already classifies (zero-width, bidi, tags): others need no second opinion."""
    return code in ZERO_WIDTH or code in BIDI_CONTROL or TAG_FIRST <= code <= TAG_LAST


def _within(code: int, ranges: tuple[tuple[int, int], ...]) -> bool:
    return any(first <= code <= last for first, last in ranges)


def _is_emoji_base(code: int) -> bool:
    return _within(code, EMOJI_BASES) and not SKIN_TONES[0] <= code <= SKIN_TONES[1]


def _is_modifier(code: int) -> bool:
    return SKIN_TONES[0] <= code <= SKIN_TONES[1] or VARIATION_SELECTORS[0] <= code <= VARIATION_SELECTORS[1]


def _joins_two_emoji(codes: list[int], index: int) -> bool:
    before = index - 1
    while before >= 0 and _is_modifier(codes[before]):
        before -= 1
    return before >= 0 and _is_emoji_base(codes[before]) and index + 1 < len(codes) and _is_emoji_base(codes[index + 1])


def _valid_variation(codes: list[int], index: int) -> bool:
    if index == 0:
        return False
    previous = codes[index - 1]
    if _is_emoji_base(previous) or SKIN_TONES[0] <= previous <= SKIN_TONES[1]:
        return True
    keycap_base = previous in (0x23, 0x2A) or 0x30 <= previous <= 0x39
    return keycap_base and index + 1 < len(codes) and codes[index + 1] == KEYCAP


def _is_subdivision_flag(codes: list[int], start: int, end: int) -> bool:
    """The tag run codes[start:end] directly after a black flag, spelling a recommended flag and cancelled."""
    if start == 0 or codes[start - 1] != BLACK_FLAG or codes[end - 1] != TAG_CANCEL:
        return False
    spelled = "".join(chr(code - TAG_FIRST) for code in codes[start:end - 1])
    return spelled in SUBDIVISION_FLAGS


def scan(text: str) -> tuple[str, ...]:
    """Sorted finding kinds in `text`; empty when it holds nothing hidden."""
    codes = [ord(char) for char in text]
    has_ascii = any(code < 0x80 and chr(code).isalnum() for code in codes)
    found = set()
    index = 0
    while index < len(codes):
        code = codes[index]
        if TAG_FIRST <= code <= TAG_LAST:
            end = index
            while end < len(codes) and TAG_FIRST <= codes[end] <= TAG_LAST:
                end += 1
            if not _is_subdivision_flag(codes, index, end):
                found.add("unicode_tags")
            index = end
            continue
        if code in BIDI_CONTROL:
            found.add("bidi_control")
        elif code == JOINER:
            if not _joins_two_emoji(codes, index):
                found.add("zero_width")
        elif code in ZERO_WIDTH:
            found.add("zero_width")
        elif VARIATION_SELECTORS[0] <= code <= VARIATION_SELECTORS[1]:
            if not _valid_variation(codes, index):
                found.add("zero_width")
        elif _within(code, CONFUSABLE_RANGES) or (has_ascii and _within(code, LOOKALIKE_SCRIPTS)):
            found.add("confusable")
        index += 1
    return tuple(sorted(found))
