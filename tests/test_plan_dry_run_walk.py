"""Walks of `create --from-file --dry-run`: review a plan without applying it.

Adversary: reviewing a plan by applying it, a dry run that passes what the real run refuses, and a draft
plan whose preview hides that every item will be a draft.
"""

import hashlib
import json
import unittest

from tests.ledger_harness import QUOTE, World
from tests.plan_support import jsonl, snapshot


class PlanDryRunWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_golden_dry_run_lists_the_operations_in_order_and_writes_nothing(self) -> None:
        """The preview must be exact enough to review and must leave the ledger byte-identical."""
        w = self.world
        existing = w.create("--title", "Existing")
        text = jsonl(
            {"ref": "e", "kind": "epic", "title": "Epic"},
            {"ref": "a", "kind": "issue", "title": "First", "parent": "@e", "needs": [existing]},
            {"ref": "b", "kind": "issue", "title": "Second", "parent": "@e", "needs": ["@a"]},
        )
        before = snapshot(w)
        result = w.run("create", "--from-file", "-", "--dry-run", "--json", stdin=text)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["plan_sha256"], hashlib.sha256(text.encode()).hexdigest())
        self.assertEqual(
            report["operations"],
            [
                {"op": "create", "ref": "e", "kind": "epic", "title": "Epic", "draft": True},
                {"op": "create", "ref": "a", "kind": "issue", "title": "First", "draft": True},
                {"op": "parent", "ref": "a", "parent": "e"},
                {"op": "need", "ref": "a", "needs": existing},
                {"op": "create", "ref": "b", "kind": "issue", "title": "Second", "draft": True},
                {"op": "parent", "ref": "b", "parent": "e"},
                {"op": "need", "ref": "b", "needs": "a"},
            ],
        )
        self.assertEqual(snapshot(w), before)
        plain = w.run("create", "--from-file", "-", "--dry-run", stdin=text)
        self.assertIn("create a issue 'First' draft", plain.stdout)
        self.assertIn("need b -> a", plain.stdout)

    def test_a_dry_run_shows_whether_each_item_would_be_a_draft(self) -> None:
        """Surprise releases must be visible in the preview: quoted entries are not drafts, `--draft` makes all drafts."""
        text = jsonl({"ref": "x", "title": "Quoted", "quote": QUOTE}, {"ref": "y", "title": "Plain"})
        released = json.loads(self.world.run("create", "--from-file", "-", "--dry-run", "--json", stdin=text).stdout)
        self.assertEqual([op["draft"] for op in released["operations"]], [False, True])
        drafts = json.loads(self.world.run("create", "--from-file", "-", "--dry-run", "--draft", "--json", stdin=text).stdout)
        self.assertEqual([op["draft"] for op in drafts["operations"]], [True, True])

    def test_a_dry_run_of_an_invalid_plan_fails_with_the_same_message_as_a_real_run(self) -> None:
        """A dry run that passes what the real run refuses defeats its purpose."""
        text = jsonl({"title": "Fine"}, {"title": "Typo", "need": []})
        dry = self.world.run("create", "--from-file", "-", "--dry-run", stdin=text)
        real = self.world.run("create", "--from-file", "-", stdin=text)
        self.assertEqual((dry.returncode, real.returncode), (2, 2))
        self.assertEqual(dry.stderr, real.stderr)

    def test_a_dry_run_after_a_partial_run_reports_what_it_would_adopt_without_changing_it(self) -> None:
        """The dry run must reflect rerun behaviour so the reviewer knows nothing will be duplicated."""
        w = self.world
        path = w.base / "plan.jsonl"
        path.write_text(jsonl({"ref": "a", "title": "A"}, {"ref": "b", "title": "B"}))
        self.assertEqual(w.run("create", "--from-file", str(path)).returncode, 0)
        before = snapshot(w)
        report = json.loads(w.run("create", "--from-file", str(path), "--dry-run", "--json").stdout)
        self.assertEqual([op["op"] for op in report["operations"]], ["adopt", "adopt"])
        self.assertEqual(snapshot(w), before)


if __name__ == "__main__":
    unittest.main()
