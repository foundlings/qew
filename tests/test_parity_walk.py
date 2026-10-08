"""docs/specs/parity.md is true: every qew command and flag it names exists."""

import argparse
import os
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import cli

ROOT = Path(__file__).resolve().parents[1]
SPAN = re.compile(r"`(qew [^`]+)`")


def commands(parser, path=()):
    found = {}
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                flags = {f for a in sub._actions for f in a.option_strings}
                found[path + (name,)] = flags
                found.update(commands(sub, path + (name,)))
    return found


class ParityWalk(unittest.TestCase):
    def test_every_qew_command_and_flag_named_in_the_parity_table_exists(self) -> None:
        """Prevents the parity table promising a replacement that does not exist (or was renamed)."""
        known = commands(cli._parser())
        text = (ROOT / "docs" / "specs" / "parity.md").read_text(encoding="utf-8")
        spans = SPAN.findall(text)
        self.assertGreater(len(spans), 30)
        for span in spans:
            words = [w for w in span.split()[1:] if not w.startswith("--")]
            flags = [w for w in span.split() if w.startswith("--")]
            path = tuple(words[: next((i for i, w in enumerate(words) if not re.fullmatch(r"[a-z][a-z-]*", w)), len(words))])
            while path and path not in known:
                path = path[:-1]
            self.assertTrue(path, f"`{span}` names no qew command")
            for flag in flags:
                self.assertIn(flag, known[path], f"`{span}`: {flag} is not a flag of qew {' '.join(path)}")

    def test_no_reference_name_appears_in_the_document(self) -> None:
        """Prevents the reference tool's name leaking into qew's documents."""
        text = (ROOT / "docs" / "specs" / "parity.md").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"(?i)(?<![a-z])kew(?![a-z])", text))


if __name__ == "__main__":
    unittest.main()
