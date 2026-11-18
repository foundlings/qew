"""The ledger-only preflight on a schema 9 ledger judges the link declaration: what each pull request closes, leaves and relates to.

Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.pr_harness import PrWorld
from tests.test_pr_preflight_walk import NoForge


class DeclaredPreflightWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld(schema="9")
        self.addCleanup(self.w.close)
        self.count = 0

    def pr(self, item: str, *flags: str) -> int:
        """Open the next pull request (its own head branch) with the given declaration; returns its number."""
        self.count += 1
        self.w.head = f"branch-{self.count}"
        run = self.w.open(item, *flags)
        self.assertEqual(run.code, 0, run)
        return self.count

    def check(self, *numbers: int, json_out: bool = False):
        argv = ["pr", "merge", *map(str, numbers), "--check", "--ledger-only", *(["--json"] if json_out else [])]
        return self.w.qew(*argv, forge=NoForge())  # type: ignore[arg-type]

    def refusals(self, *numbers: int) -> list[dict]:
        return json.loads(self.check(*numbers, json_out=True).out)["refusals"]

    def test_a_declared_clean_pull_request_passes(self) -> None:
        """Prevents the declaration rules refusing the plain case."""
        item = self.w.item("Work")
        number = self.pr(item, "--closes", item)
        run = self.check(number)
        self.assertEqual(run.code, 0, run)

    def test_a_remains_only_pull_request_passes_even_over_an_epic_with_open_children(self) -> None:
        """Prevents a partial slice being judged as the epic's finish."""
        epic = self.w.item("Epic", kind="epic")
        self.w.item("Child", parent=epic, claim=False)
        number = self.pr(epic, "--remains", epic)
        self.assertEqual(self.check(number).code, 0)

    def test_a_pull_request_with_no_declaration_is_refused_with_the_command(self) -> None:
        """Prevents an undeclared pull request (a migrated or unlinked one) reaching a merge."""
        item = self.w.item("Work")
        number = self.pr(item, "--closes", item)
        self.w.qew("pr", "unlink", str(number), item)
        found = self.refusals(number)
        self.assertEqual(len(found), 1, found)
        self.assertIn("no link declaration", found[0]["message"])
        self.assertIn(f"qew pr link {number}", found[0]["fix"])

    def test_closing_an_unclaimed_item_is_refused_naming_start(self) -> None:
        """Prevents a merge whose landing would find the item unclaimed and leave it open."""
        item = self.w.item("Work")
        other = self.w.item("Other", claim=False)
        number = self.pr(item, "--closes", item, other)
        found = self.refusals(number)
        self.assertTrue(any(f["item"] == other and f"qew start {other}" in f["fix"] for f in found), found)

    def test_closing_an_item_that_needs_unfinished_work_is_refused_unless_that_work_is_closed_too(self) -> None:
        """Prevents completing an item before what it needs, in one PR or a union."""
        base = self.w.item("Base")
        top = self.w.item("Top")
        self.assertEqual(self.w.qew("block", top, "--on", base).code, 0)
        number = self.pr(top, "--closes", top)
        found = self.refusals(number)
        self.assertTrue(any(f["item"] == top and base in f["message"] for f in found), found)
        both = self.pr(base, "--closes", base)
        self.assertEqual(self.check(number, both).code, 0)

    def test_closing_an_epic_checks_every_descendant_not_only_the_children(self) -> None:
        """Prevents completing an epic whose grandchild is still open."""
        epic = self.w.item("Epic", kind="epic")
        child = self.w.item("Child", kind="issue", parent=epic, claim=False)
        grand = self.w.item("Grand", kind="task", parent=child, claim=False)
        number = self.pr(epic, "--closes", epic)
        found = self.refusals(number)
        self.assertTrue(any(grand in f["message"] for f in found), found)

    def test_another_open_pull_request_closing_or_leaving_the_item_is_a_conflict_outside_the_union(self) -> None:
        """Prevents two pull requests both finishing one item, or one finishing it under another's partial work."""
        item = self.w.item("Work")
        first = self.pr(item, "--closes", item)
        second = self.pr(item, "--closes", item)
        self.assertTrue(any("#2" in f["message"] for f in self.refusals(first)))
        self.assertEqual(self.check(first, second).code, 0)  # the same union: one landing
        third = self.pr(item, "--remains", item)
        self.assertTrue(any("#3" in f["message"] for f in self.refusals(first)))
        self.assertEqual(self.check(third).code, 0)  # a partial slice is not blocked by the closer

    def test_closing_an_already_completed_item_is_refused(self) -> None:
        """Prevents a stale closes of work another pull request finished."""
        item = self.w.item("Work")
        number = self.pr(item, "--closes", item)
        self.assertEqual(self.w.qew("evidence", item, "--change", self.w.commit("w")).code, 0)
        self.assertEqual(self.w.qew("complete", item).code, 0)
        found = self.refusals(number)
        self.assertTrue(any(f["item"] == item and "completed" in f["message"] for f in found), found)

    def test_a_union_names_what_it_closes_leaves_and_relates_by_member(self) -> None:
        """Prevents a union merging with nobody able to say which pull request brings which item, or what stays open."""
        a, b, c = self.w.item("A"), self.w.item("B", claim=False), self.w.item("C", claim=False)
        first = self.pr(a, "--closes", a, "--related", c)
        second = self.pr(b, "--remains", b)
        shown = json.loads(self.check(first, second, json_out=True).out)["union"]
        self.assertEqual(shown["closes"], [{"item": a, "pr": first}])
        self.assertEqual(shown["remains"][0]["item"], b)
        self.assertEqual(shown["related"], [{"item": c, "pr": first}])
        self.assertEqual(shown["undeclared"], [])
        text = self.check(first, second).out
        self.assertIn("closes", text)
        self.assertIn(f"#{first}", text)

    def test_an_undeclared_union_member_blocks_the_union_and_is_listed(self) -> None:
        """Prevents one member without a declaration riding along in a union."""
        a, b = self.w.item("A"), self.w.item("B")
        first = self.pr(a, "--closes", a)
        second = self.pr(b, "--closes", b)
        self.w.qew("pr", "unlink", str(second), b)
        row = json.loads(self.check(first, second, json_out=True).out)
        self.assertFalse(row["mergeable"])
        self.assertEqual(row["union"]["undeclared"], [second])

    def test_the_preflight_writes_nothing(self) -> None:
        """Prevents the judge changing what it judges."""
        item = self.w.item("Work")
        number = self.pr(item, "--closes", item)
        base = self.w.world.ledger
        before = {str(p): p.read_text() for p in sorted(base.rglob("*.json")) if ".git" not in p.parts and ".jj" not in p.parts}
        self.check(number)
        after = {str(p): p.read_text() for p in sorted(base.rglob("*.json")) if ".git" not in p.parts and ".jj" not in p.parts}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
