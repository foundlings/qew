"""Discoverability gates: help, README and the command spec agree on every command and flag.

Adversary: a command missing from `qew --help`, a flag with no help line, and a command or
flag that exists in code but is absent from the README or docs/specs/commands.md.
"""

import argparse
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import cli
from tests.ledger_harness import World

ROOT = Path(__file__).resolve().parents[1]
GROUPS = ("Read", "Work", "Drafts", "Pull requests", "Mirrors", "Setup")


def walk(parser, path=()):
    """Yield (command path, parser) for every subcommand, depth first."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            helps = {choice.dest: choice.help for choice in action._choices_actions}
            for name, sub in action.choices.items():
                yield path + (name,), sub, helps.get(name)
                yield from walk(sub, path + (name,))


class HelpWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = cli._parser()
        self.commands = list(walk(self.parser))

    def test_every_command_is_listed_under_a_workflow_group(self) -> None:
        world = World()
        self.addCleanup(world.close)
        text = world.run("--help").stdout
        self.assertIn("Commands by workflow", text)
        listing = text[text.index("Commands by workflow"):]
        for group in GROUPS:
            self.assertIn(group, listing)
        for path, _, _ in self.commands:
            if len(path) == 1:
                self.assertRegex(listing, rf"(?m)^\s+qew {path[0]}\b", f"{path[0]} is in no group")

    def test_every_command_and_flag_has_a_help_line(self) -> None:
        for path, sub, help_text in self.commands:
            self.assertTrue(help_text, f"qew {' '.join(path)} has no help line")
            for action in sub._actions:
                if action.option_strings and "-h" not in action.option_strings:
                    self.assertTrue(action.help, f"qew {' '.join(path)} {action.option_strings} has no help")

    def test_readme_and_command_spec_name_every_command_and_flag(self) -> None:
        for name in ("README.md", "docs/specs/commands.md"):
            text = (ROOT / name).read_text(encoding="utf-8")
            for path, sub, _ in self.commands:
                self.assertIn(f"qew {' '.join(path)}", text, f"{name} lacks `qew {' '.join(path)}`")
                for action in sub._actions:
                    for flag in action.option_strings:
                        if flag.startswith("--") and flag != "--help":
                            self.assertIn(flag, text, f"{name} lacks {flag} (qew {' '.join(path)})")

    def test_rendered_group_help_lists_every_subcommand_with_its_help_line(self) -> None:
        """A subcommand that is only in the parser, not in the rendered text, is invisible to users."""
        world = World()
        self.addCleanup(world.close)
        groups = [(path, sub) for path, sub, _ in self.commands
                  if any(isinstance(a, argparse._SubParsersAction) for a in sub._actions)]
        self.assertTrue({("draft",), ("pr",), ("hook",), ("project",)} <= {path for path, _ in groups})
        for path, sub in groups:
            rendered = " ".join(world.run(*path, "--help").stdout.split())
            for child_path, _, help_text in self.commands:
                if child_path[:-1] == path:
                    with self.subTest(command=" ".join(child_path)):
                        self.assertIn(f"{child_path[-1]} {' '.join(help_text.split())}", rendered)

    def test_top_level_help_points_at_command_help_and_the_drafts_flow(self) -> None:
        """A newcomer must find per-command help and how work is released."""
        world = World()
        self.addCleanup(world.close)
        text = " ".join(world.run("--help").stdout.split())
        self.assertIn("qew COMMAND --help", text)
        self.assertIn("qew draft list | approve | revert", text)
        self.assertIn("only a human's quoted words release work", text)

    def test_no_section_sign_appears_in_the_documents(self) -> None:
        for name in ("README.md", "CLAUDE.md", *(str(p.relative_to(ROOT)) for p in (ROOT / "docs").rglob("*.md"))):
            self.assertNotIn("§", (ROOT / name).read_text(encoding="utf-8"), name)


if __name__ == "__main__":
    unittest.main()
