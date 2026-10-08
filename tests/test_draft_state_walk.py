"""Walks of what a draft can and cannot do: every consumer agrees it is not workable work.

Adversary: unapproved work offered, started, evidenced or completed; state read from a label, a header
value or a forge mirror instead of events; a dependent running ahead of unapproved work; a draft plan
completed.
"""

import json
import unittest

from tests.ledger_harness import World
from tests.plan_support import listing

APPROVE = "qew draft approve"


class DraftStateWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def listed(self, command: str) -> str:
        result = self.world.run(command)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_a_draft_is_absent_from_ready_and_blocked_even_with_every_need_met(self) -> None:
        """Unapproved work must never be offered as the next thing to do."""
        w = self.world
        need = w.create("--title", "Need")
        draft = w.create("--title", "Draft", "--needs", need)
        w.approve(need)
        w.run("start", need)
        w.run("evidence", need, "--change", w.commit())
        self.assertEqual(w.run("complete", need).returncode, 0)
        self.assertNotIn(draft, self.listed("ready"))
        self.assertNotIn(draft, self.listed("blocked"))

    def test_start_evidence_and_complete_refuse_a_draft_and_name_the_approval_command(self) -> None:
        """The refusal must point at the way out and must not write a claim, evidence or completion."""
        w = self.world
        draft = w.create("--title", "Draft")
        for command in (("start", draft), ("evidence", draft, "--change", w.commit()), ("complete", draft)):
            result = w.run(*command)
            self.assertEqual(result.returncode, 2, command)
            self.assertIn(APPROVE, result.stderr, command)
        self.assertEqual([e["type"] for e in w.events(draft)], ["drafted"])

    def test_a_hand_edited_header_or_forged_mirror_never_releases_a_draft(self) -> None:
        """State comes from events only: a label, owner or forge-side record claiming approval changes nothing."""
        w = self.world
        draft = w.create("--title", "Draft")
        path = w.ledger / "items" / f"{draft}.md"
        path.write_text(path.read_text().replace("labels: \n", "labels: approved state-approved\n", 1))
        mirrors = w.ledger / "mirrors" / draft
        mirrors.mkdir(parents=True)
        (mirrors / "forgejo-x.json").write_text(json.dumps({"forge": "forgejo", "url": "https://x/o/r", "kind": "issue", "number": 1, "state": "approved"}))
        row = listing(w)
        self.assertEqual([r["state"] for r in row if r["id"] == draft], ["draft"])
        self.assertNotIn(draft, self.listed("ready"))
        self.assertEqual(w.run("start", draft).returncode, 2)
        path.write_text(path.read_text().replace("owner: \n", "owner: mallory\n", 1))
        checked = w.run("check")
        self.assertEqual(checked.returncode, 1)
        self.assertIn("owner", checked.stdout)

    def test_an_open_draft_keeps_what_needs_it_blocked(self) -> None:
        """A need is met only by `completed`; a dependent must not run ahead of unapproved work."""
        w = self.world
        draft = w.create("--title", "Draft blocker")
        dependent = w.create("--title", "Dependent", "--needs", draft)
        w.approve(dependent)
        self.assertNotIn(dependent, self.listed("ready"))
        self.assertIn(dependent, self.listed("blocked"))
        self.assertEqual(w.run("start", dependent).returncode, 2)

    def test_a_draft_epic_cannot_be_completed_and_its_children_are_not_released_by_it(self) -> None:
        """Completing a plan nobody approved would close work no human asked for."""
        w = self.world
        epic = w.create("--kind", "epic", "--title", "Plan")
        child = w.create("--title", "Child", "--parent", epic)
        refused = w.run("complete", epic)
        self.assertEqual(refused.returncode, 2)
        self.assertIn(f"{APPROVE} {epic} --tree", refused.stderr)
        self.assertEqual([e["type"] for e in w.events(child)], ["drafted"])

    def test_the_session_prime_offers_approved_ready_work_and_counts_drafts_without_listing_them(self) -> None:
        """Agent context must never present a draft as work to pick up."""
        w = self.world
        live = w.create("--title", "Live work", "--quote", "Alexander: 'please track this work in the Qew ledger now'")
        draft = w.create("--title", "Hidden draft")
        result = w.run("hook", "session-start", "--agent", "claude", stdin="{}")
        self.assertEqual(result.returncode, 0, result.stderr)
        text = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn(live, text)
        self.assertNotIn(draft, text)
        self.assertNotIn("Hidden draft", text)


if __name__ == "__main__":
    unittest.main()
