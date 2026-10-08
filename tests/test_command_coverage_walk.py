"""Every qew command and every long flag is exercised by at least one walk (the command inventory, as a gate)."""

import argparse
import os
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import cli

TESTS = Path(__file__).resolve().parent


def inventory(parser, path=()):
    found = {}
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                flags = {f for a in sub._actions for f in a.option_strings if f.startswith("--") and f != "--help"}
                found[path + (name,)] = flags
                found.update(inventory(sub, path + (name,)))
    return found


class CommandCoverageWalk(unittest.TestCase):
    def test_every_command_and_flag_appears_in_a_walk(self) -> None:
        """Prevents a command or flag shipping with no walk at all: the inventory fails naming what is unexercised."""
        sources = "\n".join(p.read_text(encoding="utf-8") for p in TESTS.glob("test_*.py") if p.name != Path(__file__).name)
        missing = []
        for path, flags in sorted(inventory(cli._parser()).items()):
            pattern = r"\s*,\s*".join(f'["\']{re.escape(word)}["\']' for word in path)
            if not re.search(pattern, sources):
                missing.append(" ".join(path))
            for flag in sorted(flags):
                if f'"{flag}"' not in sources and f"'{flag}'" not in sources and f"{flag} " not in sources:
                    missing.append(f"{' '.join(path)} {flag}")
        self.assertEqual(missing, [], "unexercised commands and flags")


if __name__ == "__main__":
    unittest.main()
