"""Ask qew for the next free id: letters run in strict order, outliers are ignored, a plan cannot jump the sequence.

Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World

DRAFTED = [{"type": "drafted", "at": "2026-01-01T00:00:00Z", "actor": "import"}]


class NextIdWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(ledger_vcs="git")
        self.addCleanup(self.world.close)
        config = self.world.ledger / "qew-ledger.toml"
        config.write_text(config.read_text().rstrip("\n") + '\n[id_mode]\nepic = "letters"\nissue = "base35-time"\ntask = "counter"\n')

    def epics(self, *names: str) -> None:
        plan = "\n".join(json.dumps({"kind": "epic", "title": f"Phase {n}", "id": n, "events": DRAFTED}) for n in names)
        result = self.world.run("create", "--from-file", "-", "--import", stdin=plan)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def next_ids(self, *arguments: str) -> list[str]:
        result = self.world.run("next-id", *arguments)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout.split()

    def test_the_next_letter_follows_the_sequence_and_ignores_hand_named_outliers(self) -> None:
        """Prevents PIF after ADF: PID and PIE sit far past the main run (AAA..ADF) and are not the sequence."""
        self.epics("A", "B", "ZZ", "AAA", "AAB", "AAD", "ADF", "PID", "PIE")
        self.assertEqual(self.next_ids("--kind", "epic"), ["ADG"])
        self.assertEqual(self.next_ids("--kind", "epic", "--count", "3"), ["ADG", "ADH", "ADI"])
        created = self.world.run("create", "--kind", "epic", "--title", "Next phase", "--quote", QUOTE)
        self.assertEqual(created.stdout.split()[0], "ADG")

    def test_after_zzz_comes_aaaa_and_an_empty_ledger_starts_at_a(self) -> None:
        """Prevents the end of a length breaking the sequence: ZZZ is followed by AAAA, nothing yet is A."""
        self.assertEqual(self.next_ids("--kind", "epic"), ["A"])
        self.epics("ZZY", "ZZZ")
        self.assertEqual(self.next_ids("--kind", "epic"), ["AAAA"])

    def test_other_modes_print_their_next_ids_in_order(self) -> None:
        """Prevents the command only knowing letters: base35-time ids are fresh and increasing, counters count up."""
        times = self.next_ids("--kind", "issue", "--count", "3")
        self.assertEqual(len(set(times)), 3)
        self.assertEqual(times, sorted(times))
        self.assertTrue(all(len(t) == 9 for t in times))
        self.assertEqual(self.next_ids("--kind", "task"), ["1"])

    def test_a_plan_cannot_name_a_letter_that_is_not_the_next_one(self) -> None:
        """Prevents mnemonic epic ids: outside --import a letters kind accepts exactly the next letter, and says which."""
        self.epics("A", "B")
        wrong = self.world.run("create", "--from-file", "-", stdin=json.dumps({"kind": "epic", "title": "Mnemonic", "id": "PIE", "quote": QUOTE}))
        self.assertEqual(wrong.returncode, 2, wrong.stdout + wrong.stderr)
        self.assertIn("C", wrong.stderr)
        self.assertNotIn("PIE", self.world.run("list").stdout)
        right = self.world.run("create", "--from-file", "-", stdin=json.dumps({"kind": "epic", "title": "In order", "id": "C", "quote": QUOTE}))
        self.assertEqual(right.returncode, 0, right.stdout + right.stderr)


if __name__ == "__main__":
    unittest.main()
