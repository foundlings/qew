"""Walks that keep forge-as-store administration out of qew and out of agents' hands.

Adversary: qew growing lookalikes of forge-side adopt/author/area/campaign
commands, and an agent editing labels, milestones or boards around the ledger.
"""

import json
import unittest

from tests.ledger_harness import World


class AdminBoundaryWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_help_lists_no_forge_as_store_commands(self) -> None:
        """The top-level command list never offers adopt, author, area or campaign."""
        text = self.world.run("--help").stdout
        commands = {line.split()[1] for line in text.splitlines() if line.startswith("  qew ")}
        self.assertTrue(commands, text)
        self.assertEqual(commands & {"adopt", "author", "area", "campaign"}, set())
        for name in ("adopt", "author", "area", "campaign"):
            self.assertNotEqual(self.world.run(name).returncode, 0)

    def refusal(self, command: str) -> str:
        payload = json.dumps({"tool_input": {"command": command}, "cwd": str(self.world.code)})
        result = self.world.run("hook", "pre-tool", "--agent", "claude", stdin=payload)
        return result.stdout + result.stderr

    def test_hook_refuses_forge_label_milestone_and_board_edits_with_qew_advice(self) -> None:
        """Each refusal names the qew command that does the job instead."""
        cases = {
            "gh label create triage": "qew sync",
            "gh api repos/o/r/milestones -f title=x": "qew sync",
            "gh project item-edit --id 1": "qew project ensure",
            "gh issue edit 3 --add-label x": "qew",
        }
        for command, advice in cases.items():
            out = self.refusal(command)
            self.assertIn("deny", out, command)
            self.assertIn(advice, out, command)


if __name__ == "__main__":
    unittest.main()
