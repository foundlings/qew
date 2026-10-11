"""`instants.parse_instant` reads exactly `YYYY-MM-DDTHH:MM:SSZ` (ASCII digits, a real date and time) and nothing else.

Each test is a scenario; its docstring names the failure it prevents. The parser runs once per event (142,000 on foundlings) and
uses the C `datetime.fromisoformat`; this pins it to the strictness of the one it replaced.
"""

import itertools
import os
import sys
import unittest
from datetime import datetime, UTC

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import instants


def reference(text: str) -> datetime | None:
    """The previous implementation, kept for the comparison (ASCII digits only, as documented)."""
    import re

    found = re.fullmatch(r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})Z", text)
    if not found:
        return None
    try:
        year, month, day, hour, minute, second = (int(part) for part in found.groups())
        return datetime(year, month, day, hour, minute, second, tzinfo=UTC)
    except ValueError:
        return None


class InstantsWalk(unittest.TestCase):
    def test_it_agrees_with_the_strict_reference_on_edges_and_junk(self) -> None:
        """Prevents a looser parser: hour 24, minute or second 60, month 13, 30 February and a leap day must all agree."""
        texts = ["2026-10-11T09:25:58Z", "2024-02-29T00:00:00Z", "2026-02-29T00:00:00Z", "2026-02-30T00:00:00Z", "2026-13-01T00:00:00Z",
                 "2026-00-10T00:00:00Z", "2026-10-00T00:00:00Z", "2026-10-32T00:00:00Z", "2026-10-11T24:00:00Z", "2026-10-11T23:59:59Z",
                 "2026-10-11T23:60:00Z", "2026-10-11T23:59:60Z", "2026-10-11T09:25:58", "2026-10-11 09:25:58Z", "2026-10-11T09:25:58z",
                 " 2026-10-11T09:25:58Z", "2026-10-11T09:25:58Z ", "2026-10-11T09:25:58+00:00", "2026-10-11", "", "x", "0000-01-01T00:00:00Z",
                 "9999-12-31T23:59:59Z"]
        texts += [f"2026-{m:02d}-{d:02d}T{h:02d}:{n:02d}:{s:02d}Z" for m, d, h, n, s in itertools.product((1, 2, 12, 13), (1, 28, 29, 30, 31, 32), (0, 23, 24), (0, 59, 60), (0, 59, 60))]
        for text in texts:
            with self.subTest(text=text):
                self.assertEqual(instants.parse_instant(text), reference(text))

    def test_non_ascii_digits_are_refused(self) -> None:
        """Prevents lookalike digits passing as a time: only ASCII digits spell an instant."""
        self.assertIsNone(instants.parse_instant("٢٠٢٦-10-11T09:25:58Z"))


if __name__ == "__main__":
    unittest.main()
