"""Walks of the state machine and the gated `complete`.

Adversary: work marked done without proof, proof that is not on the target
branch, a parent finished over unfinished tasks, a claim on a draft or on
blocked work, and a forge-side close standing in for completion.
"""

import unittest

from tests.ledger_harness import World, git, jj, jj_available


def started(world: World, *arguments: str) -> str:
    item = world.create("--kind", "issue", "--title", "Work", *arguments)
    world.approve(item)
    assert world.run("start", item).returncode == 0
    return item


class FlowWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_golden_walk_claim_evidence_complete(self) -> None:
        w = self.world
        item = started(w)
        sha = w.commit("the work")
        self.assertEqual(w.run("evidence", item, "--change", sha).returncode, 0)
        done = w.run("complete", item)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual([e["type"] for e in w.events(item)], ["drafted", "approved", "claimed", "evidence", "completed"])
        self.assertEqual(w.events(item)[2]["owner"], "tester")
        self.assertIn("completed", w.run("show", item).stdout)
        self.assertEqual(w.run("ready").stdout, "")

    def test_complete_is_refused_without_evidence(self) -> None:
        w = self.world
        item = started(w)
        result = w.run("complete", item)
        self.assertEqual(result.returncode, 2)
        self.assertIn("evidence", result.stderr)
        self.assertNotIn("completed", [e["type"] for e in w.events(item)])

    def test_complete_is_refused_when_evidence_is_not_on_the_target_branch(self) -> None:
        w = self.world
        item = started(w)
        git(w.code, "checkout", "-q", "-b", "feature")
        sha = w.commit("unmerged work")
        git(w.code, "checkout", "-q", "main")
        w.run("evidence", item, "--change", sha)
        result = w.run("complete", item)
        self.assertEqual(result.returncode, 2)
        self.assertIn("not an ancestor of main", result.stderr)
        git(w.code, "merge", "-q", "--no-ff", "feature", "-m", "merge")
        self.assertEqual(w.run("complete", item).returncode, 0)

    def test_complete_is_refused_over_unfinished_child_tasks(self) -> None:
        w = self.world
        parent = started(w)
        task = w.create("--kind", "task", "--title", "Step", "--parent", parent)
        w.approve(task)
        sha = w.commit()
        w.run("evidence", parent, "--change", sha)
        result = w.run("complete", parent)
        self.assertEqual(result.returncode, 2)
        self.assertIn(task, result.stderr)
        w.run("start", task)
        w.run("evidence", task, "--change", sha)
        self.assertEqual(w.run("complete", task).returncode, 0)
        self.assertEqual(w.run("complete", parent).returncode, 0)

    def test_an_epic_completes_over_finished_children_without_its_own_evidence(self) -> None:
        w = self.world
        epic = w.create("--kind", "epic", "--title", "Epic")
        child = w.create("--kind", "issue", "--title", "Child", "--parent", epic)
        w.approve(epic, child)
        w.run("start", epic)
        self.assertNotEqual(w.run("complete", epic).returncode, 0)
        w.run("start", child)
        w.run("evidence", child, "--change", w.commit())
        w.run("complete", child)
        self.assertEqual(w.run("complete", epic).returncode, 0)

    def test_start_refuses_drafts_double_claims_and_blocked_work(self) -> None:
        w = self.world
        draft = w.create("--kind", "issue", "--title", "Draft")
        self.assertEqual(w.run("start", draft).returncode, 2)
        first = w.create("--kind", "issue", "--title", "First")
        second = w.create("--kind", "issue", "--title", "Second", "--needs", first)
        w.approve(first, second)
        blocked = w.run("start", second)
        self.assertEqual(blocked.returncode, 2)
        self.assertIn("needs", blocked.stderr)
        self.assertEqual(w.run("start", first).returncode, 0)
        again = w.run("start", first, env={"QEW_ACTOR": "someone-else"})
        self.assertEqual(again.returncode, 2)
        self.assertIn("claimed", again.stderr)

    def test_release_close_and_reopen(self) -> None:
        w = self.world
        item = started(w)
        self.assertEqual(w.run("release", item).returncode, 0)
        self.assertIn(item, w.run("ready").stdout)
        bad = w.run("close", item, "--reason", "because")
        self.assertNotEqual(bad.returncode, 0)
        self.assertEqual(w.run("close", item, "--reason", "not_planned").returncode, 0)
        self.assertEqual(w.run("ready").stdout, "")
        self.assertEqual(w.run("reopen", item).returncode, 0)
        self.assertIn(item, w.run("ready").stdout)

    def test_draft_revert_returns_unclaimed_work_to_draft_only(self) -> None:
        w = self.world
        item = w.create("--kind", "issue", "--title", "Maybe")
        w.approve(item)
        quote = "Alexander: 'hold this one back until we decide more'"
        self.assertEqual(w.run("draft", "revert", item, "--reason", "undecided", "--quote", quote).returncode, 0)
        self.assertEqual(w.run("ready").stdout, "")
        w.approve(item)
        w.run("start", item)
        refused = w.run("draft", "revert", item, "--reason", "late", "--quote", quote)
        self.assertEqual(refused.returncode, 2)

    def test_approve_without_a_human_quote_is_refused(self) -> None:
        w = self.world
        item = w.create("--kind", "issue", "--title", "Needs a human")
        result = w.run("draft", "approve", item, "--quote", "ok")
        self.assertEqual(result.returncode, 2)
        self.assertEqual([e["type"] for e in w.events(item)], ["drafted"])

    @unittest.skipUnless(jj_available(), "jj is not installed")
    def test_jujutsu_change_ids_resolve_against_the_target_bookmark(self) -> None:
        w = self.world
        (w.code / ".git" / "info" / "exclude").write_text(".qew/\n")
        jj(w.code, "git", "init", "--colocate")
        jj(w.code, "bookmark", "set", "main", "-r", "@-", "--allow-backwards")
        item = started(w)
        jj(w.code, "new", "-m", "unmerged work")
        change = jj(w.code, "log", "--no-graph", "-r", "@", "-T", "change_id.short(12)")
        jj(w.code, "new", "main")
        recorded = w.run("evidence", item, "--change", change)
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        self.assertEqual(w.events(item)[-1]["vcs"], "jj")
        self.assertEqual(w.run("complete", item).returncode, 2)
        jj(w.code, "bookmark", "set", "main", "-r", change)
        self.assertEqual(w.run("complete", item).returncode, 0)


if __name__ == "__main__":
    unittest.main()
