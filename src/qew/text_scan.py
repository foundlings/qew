"""Deterministic advisory findings for untrusted GitHub prose.

The code-point vocabulary is pinned to Unicode 17.0.0.  It is a deliberately
small, high-signal subset for ASCII-like confusables plus the emoji ranges
needed to exempt legitimate joiners, selectors, modifiers, and flag tags.

Reference snapshots:
- https://www.unicode.org/Public/security/17.0.0/confusables.txt
  SHA-256 091c7f82fc39ef208faf8f94d29c244de99254675e09de163160c810d13ef22a
- https://www.unicode.org/Public/emoji/17.0/emoji-test.txt
  SHA-256 1d8a944f88d7952f7ef7c5167fef3c67995bcae24543949710231b03a201acda

Findings are review hints.  Text is never normalized, hidden, or rejected.
"""

from typing import Dict, Iterator, Tuple


ZERO_WIDTH = frozenset({0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF})
BIDI_CONTROLS = frozenset(
    {
        0x061C,
        0x200E,
        0x200F,
        0x202A,
        0x202B,
        0x202C,
        0x202D,
        0x202E,
        0x2066,
        0x2067,
        0x2068,
        0x2069,
    }
)

# Mixed ASCII with Greek or Cyrillic is a high-signal prose warning.  The
# remaining ranges have compatibility forms that resemble ASCII directly.
MIXED_SCRIPT_CONFUSABLE_RANGES: Tuple[Tuple[int, int], ...] = (
    (0x0370, 0x03FF),
    (0x0400, 0x052F),
)
ASCII_CONFUSABLE_RANGES: Tuple[Tuple[int, int], ...] = (
    (0x2160, 0x217F),
    (0xFB00, 0xFB06),
    (0xFF10, 0xFF19),
    (0xFF21, 0xFF3A),
    (0xFF41, 0xFF5A),
    (0x1CCD6, 0x1CCF9),
    (0x1D400, 0x1D7FF),
)

# Pinned emoji-base ranges used only to recognize clusters whose otherwise
# suspicious formatting characters are part of the emoji itself.
EMOJI_BASE_RANGES: Tuple[Tuple[int, int], ...] = (
    (0x00A9, 0x00AE),
    (0x203C, 0x2049),
    (0x2122, 0x2139),
    (0x2194, 0x21FF),
    (0x2300, 0x23FF),
    (0x2460, 0x24FF),
    (0x25A0, 0x27BF),
    (0x2934, 0x2935),
    (0x2B05, 0x2B55),
    (0x3030, 0x303D),
    (0x3297, 0x3299),
    (0x1F000, 0x1FAFF),
)

VARIATION_SELECTORS = frozenset({0xFE0E, 0xFE0F})
KEYCAP = 0x20E3
ZWJ = 0x200D
BLACK_FLAG = 0x1F3F4
CANCEL_TAG = 0xE007F
# Recommended subdivision flags in emoji-test.txt 17.0: England, Scotland, Wales.
SUBDIVISION_FLAG_TAGS = frozenset({"gbeng", "gbsct", "gbwls"})


def text_findings(text: str) -> Dict[str, bool]:
    """Return stable, sparse findings for one field of untrusted text."""

    findings: Dict[str, bool] = {}
    contains_ascii = any(character.isascii() and character.isalnum() for character in text)
    for cluster in _clusters(text):
        if _is_recognized_emoji(cluster):
            continue
        for character in cluster:
            codepoint = ord(character)
            if codepoint in ZERO_WIDTH:
                findings["zero_width"] = True
            if codepoint in BIDI_CONTROLS:
                findings["bidi_control"] = True
            if 0xE0000 <= codepoint <= 0xE007F:
                findings["unicode_tags"] = True
            if _in_ranges(codepoint, ASCII_CONFUSABLE_RANGES) or (
                contains_ascii and _in_ranges(codepoint, MIXED_SCRIPT_CONFUSABLE_RANGES)
            ):
                findings["confusable"] = True
    return findings


def _clusters(text: str) -> Iterator[str]:
    """Yield the emoji-relevant cluster shapes Qew needs without a dependency."""

    index = 0
    while index < len(text):
        start = index
        index += 1
        index = _consume_extenders(text, index)
        while index < len(text) and ord(text[index]) == ZWJ and index + 1 < len(text):
            index += 2
            index = _consume_extenders(text, index)
        yield text[start:index]


def _consume_extenders(text: str, index: int) -> int:
    while index < len(text):
        codepoint = ord(text[index])
        if (
            codepoint in VARIATION_SELECTORS
            or codepoint == KEYCAP
            or 0x1F3FB <= codepoint <= 0x1F3FF
            or 0xE0020 <= codepoint <= 0xE007F
        ):
            index += 1
            continue
        break
    return index


def _is_recognized_emoji(cluster: str) -> bool:
    codepoints = [ord(character) for character in cluster]
    bases = [
        codepoint
        for codepoint in codepoints
        if codepoint not in VARIATION_SELECTORS
        and codepoint != KEYCAP
        and codepoint != ZWJ
        and not 0x1F3FB <= codepoint <= 0x1F3FF
        and not 0xE0020 <= codepoint <= 0xE007F
    ]
    if not bases:
        return False
    if KEYCAP in codepoints:
        return len(bases) == 1 and chr(bases[0]) in "#*0123456789"
    if any(0xE0020 <= codepoint <= 0xE007F for codepoint in codepoints):
        return bases == [BLACK_FLAG] and _is_subdivision_flag(codepoints[1:])
    if not all(_in_ranges(codepoint, EMOJI_BASE_RANGES) for codepoint in bases):
        return False
    if ZWJ in codepoints:
        return len(bases) >= 2
    return any(
        codepoint in VARIATION_SELECTORS or 0x1F3FB <= codepoint <= 0x1F3FF
        for codepoint in codepoints
    )


def _is_subdivision_flag(tail: list) -> bool:
    if not tail or tail[-1] != CANCEL_TAG:
        return False
    if not all(0xE0061 <= codepoint <= 0xE007A for codepoint in tail[:-1]):
        return False
    return "".join(chr(codepoint - 0xE0000) for codepoint in tail[:-1]) in SUBDIVISION_FLAG_TAGS


def _in_ranges(codepoint: int, ranges: Tuple[Tuple[int, int], ...]) -> bool:
    return any(start <= codepoint <= end for start, end in ranges)
