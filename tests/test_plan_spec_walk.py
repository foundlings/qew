"""The plan-file spec must stay true: its example is parsed and applied by the real validator.

Adversary: a spec whose example rots (renamed field, changed rule) while the document still reads fine.
"""

import json
from pathlib import Path
import re
import unittest

from tests.ledger_harness import World

SPECS = Path(__file__).resolve().parents[1] / "docs" / "specs"


def example(name: str, fence: str) -> str:
    text = (SPECS / name).read_text(encoding="utf-8")
    match = re.search(rf"```{fence}\n(.*?)```", text, re.DOTALL)
    assert match, f"{name} has no ```{fence} block"
    return match.group(1)


class PlanSpecWalk(unittest.TestCase):
    def test_the_plan_file_example_in_the_spec_validates_and_dry_runs_in_the_real_command(self) -> None:
        """A spec example the validator refuses teaches authors a plan that never applies."""
        world = World()
        self.addCleanup(world.close)
        plan = example("plan-files.md", "jsonl")
        result = world.run("create", "--from-file", "-", "--dry-run", "--json", stdin=plan)
        self.assertEqual(result.returncode, 0, result.stderr)
        operations = json.loads(result.stdout)["operations"]
        self.assertGreaterEqual(len([op for op in operations if op["op"] == "create"]), 3)
        applied = world.run("create", "--from-file", "-", stdin=plan)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual(world.run("check").returncode, 0)

    def test_the_drafts_spec_names_every_draft_command_and_the_rules_that_have_no_flag(self) -> None:
        """The no-force-flag and human-quote rules live only in prose; losing them from the spec loses the contract."""
        text = (SPECS / "drafts.md").read_text(encoding="utf-8")
        for fragment in ("qew draft approve", "qew draft revert", "qew draft list", "qew release", "no force flag", "Name: 'their words'"):
            self.assertIn(fragment, text)


if __name__ == "__main__":
    unittest.main()
