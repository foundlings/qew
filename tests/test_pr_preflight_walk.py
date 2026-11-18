"""`qew pr merge N [M ...] --check --ledger-only`: the fast, forge-free preflight a harness runs before a long gate.

Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.pr_harness import PrWorld


class NoForge:
    """A forge that fails the test on any use: the preflight must work with none reachable."""

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"the ledger-only preflight touched the forge ({name})")


class PreflightWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        self.root = self.w.item("Root", kind="epic")
        self.assertEqual(self.w.open(self.root).code, 0)

    def check(self, *numbers: str, json_out: bool = False, extra: tuple[str, ...] = ()):
        argv = ["pr", "merge", *numbers, "--check", "--ledger-only", *extra, *(["--json"] if json_out else [])]
        return self.w.qew(*argv, forge=NoForge())  # type: ignore[arg-type]

    def ledger_files(self) -> dict[str, str]:
        base = self.w.world.ledger
        return {str(p.relative_to(base)): p.read_text() for p in sorted(base.rglob("*.json")) + sorted(base.rglob("*.md")) if ".jj" not in p.parts and ".git" not in p.parts}

    def second_pull_covering(self, item: str) -> None:
        """PR 2 for another item whose record also covers `item`."""
        other = self.w.item("Other")
        self.w.head = "other"
        self.assertEqual(self.w.open(other).code, 0)
        self.assertEqual(self.w.qew("pr", "check", "2", "--add", item).code, 0)

    def test_a_clean_pull_request_passes_with_no_forge_and_writes_nothing(self) -> None:
        """Prevents the hook's first step needing a forge, a token or a network, or changing the ledger it only judges."""
        before = self.ledger_files()
        run = self.check("1")
        self.assertEqual(run.code, 0, run)
        self.assertIn("ledger-only", run.out)
        self.assertEqual(self.ledger_files(), before)
        self.assertTrue(json.loads(self.check("1", json_out=True).out)["mergeable"])

    def test_an_uncovered_child_is_refused_with_the_fix_command_in_text_and_json(self) -> None:
        """Prevents a 50 minute gate running for a merge that would complete a parent with open children, and a refusal that names no fix."""
        child = self.w.item("Child", parent=self.root, claim=False)
        run = self.check("1")
        self.assertEqual(run.code, 2, run)
        self.assertIn(child, run.err)
        self.assertIn(f"qew pr check 1 --add {child}", run.err)
        row = json.loads(self.check("1", json_out=True).out)
        self.assertFalse(row["mergeable"])
        refusal = row["refusals"][0]
        self.assertEqual((refusal["pr"], refusal["item"], refusal["exit_code"]), (1, self.root, 2))
        self.assertIn(f"qew pr check 1 --add {child}", refusal["fix"])
        self.assertIn("message", refusal)
        self.assertIn("schema_version", row)

    def test_every_refusal_is_listed_not_only_the_first(self) -> None:
        """Prevents fixing one refusal per gate run."""
        self.w.item("Child", parent=self.root, claim=False)
        self.second_pull_covering(self.root)
        listed = json.loads(self.check("1", json_out=True).out)["refusals"]
        self.assertGreaterEqual(len(listed), 2, listed)

    def test_an_item_another_open_pull_request_covers_is_a_conflict(self) -> None:
        """Prevents two open pull requests both finishing one item, the second merge finding it already done."""
        self.second_pull_covering(self.root)
        run = self.check("1")
        self.assertEqual(run.code, 2, run)
        self.assertIn("#2", run.err)

    def test_members_of_one_union_do_not_conflict_with_each_other(self) -> None:
        """Prevents the union's own members, each listing the shared item, refusing the union they form."""
        self.second_pull_covering(self.root)
        run = self.check("1", "2")
        self.assertNotIn("covers", run.err.replace("covers only", ""), run)
        self.assertNotIn("also covered", run.err, run)

    def test_ledger_only_without_check_is_refused(self) -> None:
        """Prevents a merge being attempted by a flag that promises no forge use."""
        run = self.w.qew("pr", "merge", "1", "--ledger-only", forge=NoForge())  # type: ignore[arg-type]
        self.assertEqual(run.code, 2, run)
        self.assertIn("--check", run.err)
        self.assertEqual(self.w.forge.pulls[1].state, "open")

    def test_an_unknown_pull_request_is_refused_not_guessed(self) -> None:
        """Prevents a typo number passing the preflight."""
        run = self.check("99")
        self.assertNotEqual(run.code, 0, run)


if __name__ == "__main__":
    unittest.main()
