"""Command walks for discovering Qew from its own help.

Adversary: a command exists that `qew --help` never mentions, so a user without
prior knowledge cannot find it; a command group (drafts, pull requests) whose
subcommands are invisible; and a new command added later with no group.
"""

import argparse
import os
import re
import sys
import unittest

from tests.command_harness import run_qew

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

GROUPS = ("Read", "Work", "Drafts", "Pull requests", "Plans and graph", "Board and repository", "Hooks")


def top_level_commands(help_text: str) -> list:
    match = re.search(r"\{([a-z,-]+)\}", help_text)
    return match.group(1).split(",")


class HelpWalk(unittest.TestCase):
    def test_every_command_is_listed_under_a_workflow_group(self) -> None:
        result, _ = run_qew([], ["--help"])

        self.assertEqual(result.returncode, 0, result.stderr)
        text = result.stdout
        self.assertIn("Commands by workflow", text)
        listing = text[text.index("Commands by workflow") :]
        for group in GROUPS:
            self.assertIn(group, listing)
        for command in top_level_commands(text):
            self.assertRegex(listing, rf"(?m)^\s+qew {command}\b", f"{command} is in no group")

    def test_help_points_at_per_command_help_and_the_drafts_flow(self) -> None:
        result, _ = run_qew([], ["--help"])

        self.assertIn("qew COMMAND --help", result.stdout)
        self.assertIn("qew draft approve", result.stdout)
        self.assertIn("qew draft revert", result.stdout)

    def test_subcommand_groups_list_their_subcommands(self) -> None:
        for command, names in (
            ("draft", ("list", "approve", "revert")),
            ("pr", ("ready", "check", "merge")),
            ("plan", ("create", "status", "complete")),
        ):
            with self.subTest(command=command):
                result, _ = run_qew([], [command, "--help"])

                self.assertEqual(result.returncode, 0, result.stderr)
                listed = result.stdout.split("positional arguments:")[1].split("options:")[0]
                for name in names:
                    self.assertRegex(listed, rf"(?m)^\s{{4}}{name}\s+\S", f"{command} {name} has no help line")

    def test_every_top_level_command_has_a_help_line(self) -> None:
        result, _ = run_qew([], ["--help"])

        listed = result.stdout.split("positional arguments:")[1].split("options:")[0]
        for command in top_level_commands(result.stdout):
            self.assertRegex(listed, rf"(?m)^\s{{4}}{command}\s+\S", f"{command} has no help line")


def undocumented(parser: argparse.ArgumentParser, path: tuple = ()) -> list:
    found = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                found.extend(undocumented(sub, (*path, name)))
        elif not action.help and action.dest not in ("help", "command") and action.option_strings != ["--version"]:
            shown = ", ".join(action.option_strings) or action.dest
            found.append(f"qew {' '.join(path)} {shown}".replace("  ", " "))
    return found


class HelpTextWalk(unittest.TestCase):
    def test_every_option_and_argument_has_a_help_line(self) -> None:
        # Adversary: a flag that --help shows with no description, so the user
        # has to read the source to learn what `create --where` or `pr --testing` means.
        from qew.__main__ import _parser

        self.assertEqual(undocumented(_parser()), [])


def flags(parser: argparse.ArgumentParser, path: tuple = ()) -> list:
    found = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                found.extend(flags(sub, (*path, name)))
        else:
            found.extend(
                (f"qew {' '.join(path)}".strip(), flag)
                for flag in action.option_strings
                if flag.startswith("--") and flag not in ("--help", "--version")
            )
    return found


class DocumentedFlagsWalk(unittest.TestCase):
    def test_every_flag_is_named_in_the_readme_and_the_command_spec(self) -> None:
        # Adversary: a flag that exists and --help describes, but the README and
        # the spec never mention, so a reader of either does not know it is there.
        from qew.__main__ import _parser

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        readme = open(os.path.join(root, "README.md"), encoding="utf-8").read()
        spec = open(os.path.join(root, "docs", "specs", "commands.md"), encoding="utf-8").read()
        missing = sorted(
            {
                f"{where}: {command} {flag}"
                for command, flag in flags(_parser())
                for where, text in (("README.md", readme), ("docs/specs/commands.md", spec))
                if flag not in text
            }
        )

        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
