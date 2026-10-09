"""Walks of `qew pr open`: what is opened, recorded and refused before the forge hears of it.

Adversary: a pull request with no ledger record (or recorded before the forge accepted it), a closing
keyword that lets the forge complete work behind qew's back, a ready pull request over unapproved work,
and a pull request for work nobody (or somebody else) owns.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew.forge import ForgeAuth, ForgeRejected, ForgeUnavailable
from tests.pr_harness import PrWorld

CLOSING_TABLE = (
    "also fixes #16",
    "Resolves acme/other#7",
    "closed: #16",
    "Closes https://host/o/r/issues/9",
    "fixes o/r#16",
    "RESOLVED #3",
)


class OpenWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)

    def test_golden_open_records_a_draft_assigned_to_its_opener_in_one_mirror_file(self) -> None:
        """Prevents a pull request with no ledger record, or one recorded before the forge accepted it."""
        item = self.w.item("Parse headers")
        run = self.w.open(item)
        self.assertEqual(run.code, 0, run.err)
        self.assertEqual(run.out.strip(), f"{item} pr #1 https://git.example/o/r/pulls/1 (draft)")
        spec = self.w.forge.pull_specs[1]
        self.assertEqual((spec.draft, spec.assignee, spec.title, spec.head, spec.base), (True, "tester", "Parse headers", "feature", "main"))
        files = self.w.mirror_files(item)
        self.assertEqual(len(files), 1)
        record = json.loads((self.w.world.ledger / "mirrors" / item / files[0]).read_text())
        self.assertEqual((record["kind"], record["number"], record["opener"], record["items"]), ("pr", 1, "tester", [item]))

    def test_the_assignee_is_the_forge_login_when_it_differs_from_the_actor_and_a_refusal_is_a_note_not_a_failure(self) -> None:
        """Prevents a pull request that exists on the forge but not in the ledger because assigning it failed."""
        item = self.w.item()
        self.assertEqual(self.w.open(item, env={"QEW_FORGE_LOGIN": "octo"}).code, 0)
        self.assertEqual(self.w.forge.pull_specs[1].assignee, "octo")
        self.w.forge.assign_refused = True
        other = self.w.item("Other")
        run = self.w.open(other)
        self.assertEqual(run.code, 0, run)
        self.assertIn("not assigned to tester", run.err)
        self.assertIn("QEW_FORGE_LOGIN", run.err)
        self.assertEqual(len(self.w.mirror_files(other)), 1)

    def test_the_machine_flag_after_the_positional_prints_the_stable_object(self) -> None:
        """Prevents agents scraping human text."""
        item = self.w.item()
        run = self.w.open(item, "--json")
        self.assertEqual(
            json.loads(run.out),
            {"item": item, "number": 1, "url": "https://git.example/o/r/pulls/1", "draft": True, "schema_version": 1},
        )

    def test_a_forge_failure_leaves_no_mirror_and_one_stderr_line(self) -> None:
        """Prevents a phantom pull request record that later merges the wrong number."""
        item = self.w.item()
        for failure in (ForgeUnavailable("forge is down"), ForgeAuth("no token"), ForgeRejected(422, "no")):
            with self.subTest(failure=type(failure).__name__):
                self.w.forge.raise_on["open_pr"] = failure
                run = self.w.open(item)
                self.assertEqual(run.code, 1)
                self.assertEqual(len(run.err.strip().splitlines()), 1)
                self.assertNotIn("Traceback", run.err)
                self.assertEqual(self.w.mirror_files(item), [])

    def test_an_item_with_no_issue_mirror_is_refused_with_the_sync_hint(self) -> None:
        """Prevents a pull request opened against an arbitrary mirror."""
        item = self.w.item(sync=False)
        run = self.w.open(item)
        self.assertEqual(run.code, 2)
        self.assertIn("qew sync", run.err)
        self.assertEqual(self.w.forge.count("open_pr"), 0)

    def test_an_item_with_several_issue_mirrors_needs_a_target(self) -> None:
        item = self.w.item()
        existing = json.loads(next((self.w.world.ledger / "mirrors" / item).glob("*.json")).read_text())
        self.w.ledger.write_mirror(item, "forgejo-other.example-o-r", {**existing, "url": "https://other.example/o/r"})
        run = self.w.open(item)
        self.assertEqual(run.code, 2)
        self.assertIn("--target", run.err)
        self.assertEqual(self.w.forge.count("open_pr"), 0)


class BodyWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)

    def test_the_body_carries_the_item_sections_the_marker_and_delivers_once_each(self) -> None:
        """Prevents lost or duplicated provenance."""
        item = self.w.item(body="Goal: parse headers\n\nApproach: a table")
        self.w.open(item, "--why", "Because agents need it", "--testing", "golden walk")
        body = self.w.forge.pull_specs[1].body
        self.assertEqual(body.count(f"<!-- qew:{item} -->"), 1)
        self.assertEqual(body.count(f"Delivers {item} (issue #1)."), 1)
        for text in ("## What\n\nGoal: parse headers\n\nApproach: a table", "## Why\n\nBecause agents need it", "## Testing\n\ngolden walk"):
            self.assertEqual(body.count(text), 1, text)

    def test_a_closing_reference_anywhere_in_the_body_text_is_refused_before_the_forge_is_called(self) -> None:
        """Prevents the merge closing unrelated issues on the forge behind qew's back."""
        item = self.w.item()
        for text in CLOSING_TABLE:
            with self.subTest(text=text):
                run = self.w.open(item, "--what", text)
                self.assertEqual(run.code, 2, text)
                self.assertIn("closing reference", run.err)
        self.assertEqual(self.w.forge.count("open_pr"), 0)
        self.assertEqual(self.w.mirror_files(item), [])

    def test_a_closing_reference_in_the_item_body_is_refused_too(self) -> None:
        item = self.w.item(body="Notes\n\nThis also fixes #16 on the way.")
        run = self.w.open(item)
        self.assertEqual(run.code, 2)
        self.assertEqual(self.w.forge.count("open_pr"), 0)

    def test_text_that_only_mentions_a_flag_or_a_path_passes_byte_for_byte(self) -> None:
        """Prevents silent body mutation."""
        item = self.w.item()
        text = "run `qew pr open --ready`; see fixes in docs/closes.md and the resolved list"
        run = self.w.open(item, "--what", text)
        self.assertEqual(run.code, 0, run.err)
        self.assertIn(f"## What\n\n{text}", self.w.forge.pull_specs[1].body)

    def test_a_body_file_checklist_is_carried_verbatim_and_can_be_listed(self) -> None:
        """Prevents a checklist that cannot be ticked later."""
        item = self.w.item()
        checklist = "## Progress\n\n- [ ] RED test <!-- pr-progress:red -->\n- [ ] implementation <!-- pr-progress:green -->\n- [ ] docs <!-- pr-progress:docs -->\n"
        path = self.w.world.code / "progress.md"
        path.write_text(checklist)
        self.assertEqual(self.w.open(item, "--body-file", str(path)).code, 0)
        self.assertIn(checklist.strip(), self.w.forge.pull_specs[1].body)
        listed = self.w.qew("pr", "check", "1", "--list")
        self.assertEqual(listed.out.strip().splitlines(), ["[ ] red RED test", "[ ] green implementation", "[ ] docs docs"])

    def test_a_closing_reference_hidden_in_the_body_file_is_refused(self) -> None:
        item = self.w.item()
        path = self.w.world.code / "progress.md"
        path.write_text("- [ ] step <!-- pr-progress:a -->\nCloses #4\n")
        self.assertEqual(self.w.open(item, "--body-file", str(path)).code, 2)
        self.assertEqual(self.w.forge.count("open_pr"), 0)

    def test_an_unreadable_body_file_is_refused_before_any_forge_call(self) -> None:
        """Prevents a pull request opened with an empty body."""
        item = self.w.item()
        run = self.w.open(item, "--body-file", str(self.w.world.code / "missing.md"))
        self.assertEqual(run.code, 2)
        self.assertEqual(self.w.forge.count("open_pr"), 0)


class DraftAndOwnerWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)

    def test_a_draft_item_opens_a_draft_pull_request_without_any_claim(self) -> None:
        """Prevents forcing a human approval or a fake claim just to open planning work."""
        item = self.w.item(draft=True)
        run = self.w.open(item)
        self.assertEqual(run.code, 0, run.err)
        self.assertTrue(self.w.forge.pull_specs[1].draft)
        self.assertEqual(len(self.w.mirror_files(item)), 1)

    def test_ready_on_a_draft_item_is_refused_with_no_forge_call_and_no_mirror(self) -> None:
        """Prevents a plan pull request that looks reviewable while its issues are unapproved."""
        item = self.w.item(draft=True)
        run = self.w.open(item, "--ready")
        self.assertEqual(run.code, 2)
        self.assertIn("always a draft", run.err)
        self.assertEqual(self.w.forge.count("open_pr"), 0)
        self.assertEqual(self.w.mirror_files(item), [])

    def test_an_approved_claimed_item_opens_a_draft_by_default_and_ready_only_on_request(self) -> None:
        """Prevents accidental ready pull requests."""
        first, second = self.w.item("One"), self.w.item("Two")
        self.w.open(first)
        self.w.open(second, "--ready")
        self.assertEqual([self.w.forge.pull_specs[n].draft for n in (1, 2)], [True, False])

    def test_an_unclaimed_item_is_refused_with_the_start_hint(self) -> None:
        """Prevents pull requests for work nobody owns."""
        item = self.w.item(claim=False)
        run = self.w.open(item)
        self.assertEqual(run.code, 2)
        self.assertIn(f"qew start {item}", run.err)
        self.assertEqual(self.w.forge.count("open_pr"), 0)

    def test_an_item_claimed_by_someone_else_is_refused_naming_the_owner(self) -> None:
        """Prevents one lane hijacking another lane's pull request."""
        item = self.w.item(by="other")
        run = self.w.open(item)
        self.assertEqual(run.code, 2)
        self.assertIn("other", run.err)
        self.assertEqual(self.w.forge.count("open_pr"), 0)

    def test_a_closed_item_cannot_get_a_pull_request(self) -> None:
        item = self.w.item()
        self.w.qew("close", item, "--reason", "not_planned")
        self.assertEqual(self.w.open(item).code, 2)


class PlanEpicWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        self.epic = self.w.item("Plan", kind="epic", draft=True)
        self.one = self.w.item("One", draft=True, parent=self.epic)
        self.two = self.w.item("Two", draft=True, parent=self.epic)

    def test_one_pull_request_covers_the_epic_and_its_open_descendants(self) -> None:
        self.assertEqual(self.w.open(self.epic).code, 0)
        file = self.w.mirror_files(self.epic)[0]
        record = json.loads((self.w.world.ledger / "mirrors" / self.epic / file).read_text())
        self.assertEqual(sorted(record["items"]), sorted([self.epic, self.one, self.two]))

    def test_a_closed_child_is_not_covered(self) -> None:
        self.w.qew("close", self.two, "--reason", "not_planned")
        self.w.open(self.epic)
        file = self.w.mirror_files(self.epic)[0]
        record = json.loads((self.w.world.ledger / "mirrors" / self.epic / file).read_text())
        self.assertNotIn(self.two, record["items"])

    def test_the_epic_cannot_open_ready_while_a_child_is_a_draft(self) -> None:
        epic = self.w.item("Approved plan", kind="epic")
        self.w.item("Still draft", draft=True, parent=epic)
        run = self.w.open(epic, "--ready")
        self.assertEqual(run.code, 2)
        self.assertEqual(self.w.forge.count("open_pr"), 0)


if __name__ == "__main__":
    unittest.main()
