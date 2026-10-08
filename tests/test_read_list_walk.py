"""Walks of the list-shaped reads: the ready bound, `list` filters and work states, `blocked` explanations.

Adversary: an unbounded ready list swallowing an agent's context, a filter that hides real work
(case) or matches a lookalike label (Unicode folding), stale ownership, an ambiguous or buried stuck
item, and a blocked view that guesses at needs it cannot see.
"""

import json
import unittest

from tests.ledger_harness import QUOTE, World


def rows(result) -> list:
    return [json.loads(line) for line in result.stdout.splitlines()]


def ids(result) -> list:
    return [line.split()[0] for line in result.stdout.splitlines()]


class ReadyLimitWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)
        specs = json.dumps([{"kind": "issue", "title": f"Work {n:02}"} for n in range(31)])
        created = [line.split()[0] for line in self.world.run("create", "--from-file", "-", stdin=specs).stdout.splitlines()]
        self.world.approve(*created)

    def test_default_bound_reports_truncation_and_zero_returns_everything(self) -> None:
        """31 ready items print 30 with total 31 and a stderr warning; --limit 0 prints all 31."""
        result = self.world.run("ready", "--json")
        body = json.loads(result.stdout)
        self.assertEqual((len(body["items"]), body["total"], body["truncated"]), (30, 31, True))
        self.assertIn("truncated", result.stderr)
        self.assertEqual(len(self.world.run("ready").stdout.splitlines()), 30)
        everything = json.loads(self.world.run("ready", "--limit", "0", "--json").stdout)
        self.assertEqual((len(everything["items"]), everything["truncated"]), (31, False))
        self.assertEqual(self.world.run("ready", "--limit", "0").stderr, "")

    def test_a_nonsense_bound_is_a_usage_error_before_the_ledger_is_read(self) -> None:
        """A negative or non-integer bound exits 2 even when the ledger itself is unreadable (exit 1)."""
        item = next(self.world.ledger.joinpath("items").glob("*.md"))
        item.write_text(item.read_text().replace("\n---\n", "\nstatus: x\n---\n", 1))
        for bound in ("-1", "abc"):
            result = self.world.run("ready", "--limit", bound)
            self.assertEqual((result.returncode, result.stdout), (2, ""), bound)


class ListWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def make(self, title: str, *extra: str, actor: str = "tester", approve: bool = True) -> str:
        result = self.world.run("create", "--title", title, *extra, env={"QEW_ACTOR": actor})
        self.assertEqual(result.returncode, 0, result.stderr)
        item = result.stdout.split()[0]
        if approve:
            self.world.approve(item)
        return item

    def test_repeated_labels_must_all_match_and_names_fold_ascii_case_only(self) -> None:
        """Two --label flags AND together; AREA:CLI finds area:cli; a Kelvin-sign lookalike never matches k8s."""
        both = self.make("both", "--label", "area:cli", "--label", "phase:one")
        self.make("one", "--label", "area:cli")
        plain = self.make("plain", "--label", "k8s")
        lookalike = self.make("lookalike", "--label", "K8s")
        self.assertEqual(ids(self.world.run("list", "--label", "AREA:CLI", "--label", "phase:ONE")), [both])
        self.assertEqual(ids(self.world.run("list", "--label", "K8S")), [plain])
        self.assertEqual(ids(self.world.run("list", "--label", "K8s")), [lookalike])
        self.assertEqual(self.world.run("list", "--label", "nothing").stdout, "")
        self.assertEqual(self.world.run("list", "--label", "nothing").returncode, 0)

    def test_owner_follows_claims_and_release_and_author_follows_the_creating_event(self) -> None:
        """--owner shows exactly what alice holds now; --author is the creator, not a label."""
        mine = self.make("mine", actor="alice")
        theirs = self.make("theirs", actor="bob")
        self.assertEqual(self.world.run("start", mine, env={"QEW_ACTOR": "alice"}).returncode, 0)
        self.assertEqual(ids(self.world.run("list", "--owner", "ALICE")), [mine])
        self.assertEqual(ids(self.world.run("list", "--author", "alice")), [mine])
        self.assertEqual(ids(self.world.run("list", "--author", "bob")), [theirs])
        self.world.run("release", mine, "--reason", "done for now", env={"QEW_ACTOR": "alice"})
        self.assertEqual(self.world.run("list", "--owner", "alice").stdout, "")

    def test_parent_filter_limit_and_sorted_labels(self) -> None:
        """--parent scopes to one epic, --limit bounds with a warning, JSON labels come out sorted."""
        epic = self.make("epic", "--kind", "epic")
        child = self.make("child", "--parent", epic, "--label", "zeta", "--label", "alpha")
        self.make("other")
        self.assertEqual(ids(self.world.run("list", "--parent", epic.upper())), [child])
        self.assertEqual(rows(self.world.run("list", "--parent", epic, "--json"))[0]["labels"], ["alpha", "zeta"])
        limited = self.world.run("list", "--limit", "1")
        self.assertEqual(len(limited.stdout.splitlines()), 1)
        self.assertIn("truncated", limited.stderr)

    def test_work_states_order_actionable_work_first(self) -> None:
        """ready, blocked, claimed, plan, then drafts awaiting a decision, then closed history."""
        w = self.world
        closed = self.make("closed")
        w.run("close", closed, "--reason", "not_planned")
        draft = self.make("draft", approve=False)
        epic = self.make("epic", "--kind", "epic")
        claimed = self.make("claimed")
        w.run("start", claimed)
        gate = self.make("gate")
        blocked = self.make("blocked", "--needs", gate)
        ready = gate
        listed = rows(w.run("list", "--json"))
        self.assertEqual([r["id"] for r in listed], [ready, blocked, claimed, epic, draft, closed])
        self.assertEqual([r["work_state"] for r in listed], ["ready", "blocked", "claimed", "plan", "needs_decision", "closed"])
        self.assertEqual(ids(w.run("list", "--work-state", "plan")), [epic])

    def test_claimed_and_blocked_reports_one_documented_state(self) -> None:
        """A claimed item with an unmet need is `blocked` (the more urgent class), exactly one value."""
        w = self.world
        gate = self.make("gate")
        stuck = self.make("stuck")
        w.run("start", stuck)
        item = w.ledger / "items" / f"{stuck}.md"
        item.write_text(item.read_text().replace("needs: \n", f"needs: {gate}\n", 1))
        row = next(r for r in rows(w.run("list", "--json")) if r["id"] == stuck)
        self.assertEqual((row["state"], row["work_state"]), ("claimed", "blocked"))


class BlockedWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def make(self, title: str, *extra: str, approve: bool = True) -> str:
        item = self.world.create("--title", title, *extra)
        if approve:
            self.world.approve(item)
        return item

    def finish(self, item: str) -> None:
        w = self.world
        self.assertEqual(w.run("start", item).returncode, 0)
        w.run("evidence", item, "--change", w.commit("work"))
        self.assertEqual(w.run("complete", item).returncode, 0)

    def test_only_unmet_needs_are_explained_with_state_and_title(self) -> None:
        """An open and a completed need: blocked_by lists the open one as an object with state and title."""
        w = self.world
        done, open_need = self.make("done need"), self.make("open need")
        self.finish(done)
        item = self.make("waiting", "--needs", done, open_need)
        body = json.loads(w.run("blocked", "--json").stdout)
        self.assertEqual([r["id"] for r in body["items"]], [item])
        self.assertEqual(body["items"][0]["blocked_by"], [{"id": open_need, "state": "approved", "title": "open need"}])
        self.assertTrue(w.run("blocked").stdout.strip().endswith(f"<- {open_need}"))

    def test_closed_and_dangling_needs_still_block_and_are_labelled(self) -> None:
        """A need closed as duplicate is `closed`; a need that does not exist is `missing`."""
        w = self.world
        closed = self.make("closed need")
        w.run("close", closed, "--reason", "not_planned")
        waiting = self.make("waiting", "--needs", closed)
        path = w.ledger / "items" / f"{waiting}.md"
        path.write_text(path.read_text().replace(f"needs: {closed}\n", f"needs: {closed} zzzzzzzzz\n", 1))
        entry = json.loads(w.run("blocked", "--json").stdout)["items"][0]
        self.assertEqual(entry["blocked_by"], [{"id": closed, "state": "closed", "title": "closed need"}, {"id": "zzzzzzzzz", "state": "missing"}])
        self.assertNotIn(waiting, w.run("ready").stdout)

    def test_drafts_never_appear_and_claimed_work_shows_its_owner(self) -> None:
        """A draft with an open need is in neither blocked nor ready; claimed stuck work keeps its owner."""
        w = self.world
        gate = self.make("gate")
        draft = self.make("draft", "--needs", gate, approve=False)
        stuck = self.make("stuck")
        w.run("start", stuck)
        item = w.ledger / "items" / f"{stuck}.md"
        item.write_text(item.read_text().replace("needs: \n", f"needs: {gate}\n", 1))
        blocked = w.run("blocked").stdout
        self.assertNotIn(draft, blocked)
        self.assertNotIn(draft, w.run("ready").stdout)
        self.assertIn(f"{stuck} claimed", blocked)
        self.assertIn("@tester", blocked)


if __name__ == "__main__":
    unittest.main()
