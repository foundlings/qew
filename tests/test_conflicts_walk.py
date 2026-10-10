"""Agents deal with sync conflicts by command: `qew conflicts` lists them, `qew resolve` settles each kind."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_forgejo import TOKEN, FakeForgejo
from tests.ledger_harness import QUOTE, World, git, jj_available

EVERY = "\n[replication]\nfetch_interval_s = 0\nauto_forge_sync = false\ndivergences = \"report\"\n"
OFFLINE = {"QEW_OFFLINE": "1"}


def listing(world: World) -> list:
    result = world.run("conflicts", "--json")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["conflicts"]


class LedgerMergeConflictWalk(unittest.TestCase):
    vcs = "git"

    def setUp(self) -> None:
        self.a = World(ledger_vcs=self.vcs, remote=True, replication=EVERY)
        self.addCleanup(self.a.close)
        self.b = self.a.clone("b", replication=EVERY)
        self.item = self.a.create("--title", "Original", "--quote", QUOTE)
        self.b.run("list")
        self.assertEqual(self.a.run("set", self.item, "--title", "Title from A").returncode, 0)
        self.assertEqual(self.b.run("set", self.item, "--title", "Title from B", env=OFFLINE).returncode, 0)
        blocked = self.b.run("list")  # the next online command meets the same-item conflict
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("qew conflicts", blocked.stderr)

    def title(self, world: World) -> str:
        return json.loads(world.run("show", self.item, "--json").stdout)["title"]

    def test_the_conflict_is_listed_with_both_sides_and_the_exact_resolve_commands(self) -> None:
        """Prevents an agent facing a wedged ledger with no way to see or settle it: both titles and the commands are given."""
        found = listing(self.b)
        self.assertEqual([c["kind"] for c in found], ["ledger_merge"])
        conflict = found[0]
        self.assertEqual(conflict["files"], [f"items/{self.item}/item.md"])
        self.assertEqual(conflict["item"], self.item)
        titles = [(d["key"], d["ours"], d["theirs"]) for d in conflict["differences"]]
        self.assertEqual(titles, [("title", "Title from B", "Title from A")])
        self.assertIn(f"qew resolve ledger --take theirs --reason TEXT", conflict["resolve"])
        self.assertIn(f"qew resolve ledger --take ours --reason TEXT", conflict["resolve"])
        human = self.b.run("conflicts")
        self.assertEqual(human.returncode, 0, human.stderr)
        self.assertIn("ledger_merge", human.stdout)
        self.assertIn("Title from A", human.stdout)

    def test_taking_theirs_keeps_the_remote_item_and_records_the_discarded_side(self) -> None:
        """Prevents a resolution losing work silently: the discarded local title is kept in a conflict_resolved event."""
        done = self.b.run("resolve", "ledger", "--take", "theirs", "--reason", "A edited first")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.title(self.b), "Title from A")
        recorded = [e for e in self.b.events(self.item) if e["type"] == "conflict_resolved"]
        self.assertEqual(len(recorded), 1)
        self.assertEqual((recorded[0]["took"], recorded[0]["reason"]), ("theirs", "A edited first"))
        self.assertIn("Title from B", recorded[0]["discarded"][f"items/{self.item}/item.md"])
        self.assertEqual(listing(self.b), [])
        self.assertEqual(self.b.ledger_dirty(), [])
        self.assertEqual(self.b.run("check").returncode, 0)
        self.assertEqual(self.a.run("list").returncode, 0)
        self.assertEqual(self.title(self.a), "Title from A")
        self.assertEqual(len([e for e in self.a.events(self.item) if e["type"] == "conflict_resolved"]), 1)

    def test_taking_ours_keeps_the_local_item_and_it_reaches_the_other_checkout(self) -> None:
        """Prevents the other side being left behind: after taking ours, the remote and the other checkout agree."""
        done = self.b.run("resolve", "ledger", "--take", "ours", "--reason", "B is the owner's wording")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.title(self.b), "Title from B")
        recorded = [e for e in self.b.events(self.item) if e["type"] == "conflict_resolved"][0]
        self.assertIn("Title from A", recorded["discarded"][f"items/{self.item}/item.md"])
        self.assertEqual(self.a.run("list").returncode, 0)
        self.assertEqual(self.title(self.a), "Title from B")

    def test_resolve_refuses_without_a_reason_with_the_wrong_side_and_when_nothing_conflicts(self) -> None:
        """Prevents a careless resolution: a reason is required, `forge` is not a ledger side, and a clean ledger has nothing to resolve."""
        self.assertEqual(self.b.run("resolve", "ledger", "--take", "ours").returncode, 2)
        wrong = self.b.run("resolve", "ledger", "--take", "forge", "--reason", "x")
        self.assertEqual(wrong.returncode, 2)
        self.assertIn("ours", wrong.stderr)
        self.assertEqual(self.b.run("resolve", "ledger", "--take", "ours", "--reason", "settle").returncode, 0)
        again = self.b.run("resolve", "ledger", "--take", "ours", "--reason", "again")
        self.assertEqual(again.returncode, 2)
        self.assertIn("no ledger merge conflict", again.stderr)


@unittest.skipUnless(jj_available(), "jj is not installed")
class LedgerMergeConflictJujutsuWalk(LedgerMergeConflictWalk):
    vcs = "jj"


class ForeignFileConflictWalk(unittest.TestCase):
    def test_a_conflict_in_a_file_qew_does_not_own_is_listed_but_never_resolved(self) -> None:
        """Prevents qew rewriting a human's file: resolve refuses and both sides of the foreign file stay as they were."""
        a = World(ledger_vcs="git", remote=True, replication=EVERY)
        self.addCleanup(a.close)
        b = a.clone("b", replication=EVERY)
        for world, text in ((a, "from A\n"), (b, "from B\n")):
            (world.ledger / "NOTES.txt").write_text(text, encoding="utf-8")
        git(a.ledger, "add", "-A")
        git(a.ledger, "commit", "-qm", "notes A")
        git(a.ledger, "push", "-q", "origin", "main")
        git(b.ledger, "add", "-A")
        git(b.ledger, "commit", "-qm", "notes B")
        self.assertNotEqual(b.run("list").returncode, 0)
        found = listing(b)
        self.assertEqual((found[0]["kind"], found[0]["files"], found[0]["resolvable"]), ("ledger_merge", ["NOTES.txt"], False))
        refused = b.run("resolve", "ledger", "--take", "theirs", "--reason", "try")
        self.assertEqual(refused.returncode, 2)
        self.assertIn("NOTES.txt", refused.stderr)
        self.assertEqual((b.ledger / "NOTES.txt").read_text(encoding="utf-8"), "from B\n")


class ForgeDivergenceWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeForgejo().start()
        self.addCleanup(self.fake.stop)
        mirror = f'\n[[mirror]]\nforge = "forgejo"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'
        self.w = World(mirror, replication=EVERY)
        self.addCleanup(self.w.close)
        self.env = {"FORGEJO_TOKEN": TOKEN}
        self.item = self.w.create("--title", "Mirrored", "--quote", QUOTE)
        self.assertEqual(self.w.run("sync", env=self.env).returncode, 0)
        self.fake.issues[next(iter(self.fake.issues))]["state"] = "closed"
        self.assertEqual(self.w.run("sync", env=self.env).returncode, 0)

    def test_a_forge_close_is_listed_with_both_values_and_both_resolutions(self) -> None:
        """Prevents a divergence being invisible to an agent: kind, item, forge and ledger values and the two commands are given."""
        found = listing(self.w)
        self.assertEqual(len(found), 1)
        conflict = found[0]
        self.assertEqual((conflict["kind"], conflict["item"], conflict["field"]), ("divergence", self.item, "state"))
        self.assertEqual((conflict["forge"], conflict["ledger"]), ("closed", "open"))
        self.assertEqual(sorted(conflict["resolve"]), sorted([
            f"qew resolve {conflict['id']} --take forge --reason TEXT",
            f"qew resolve {conflict['id']} --take ledger --reason TEXT"]))

    def test_taking_the_ledger_keeps_it_open_and_records_why(self) -> None:
        """Prevents a rejection without a trail: the divergence_rejected event carries the agent's reason and the ledger stays open."""
        done = self.w.run("resolve", self.item, "--take", "ledger", "--reason", "still in progress", env=self.env)
        self.assertEqual(done.returncode, 0, done.stderr)
        events = self.w.events(self.item)
        self.assertEqual([e["reason"] for e in events if e["type"] == "divergence_rejected"], ["still in progress"])
        self.assertEqual(listing(self.w), [])
        self.assertEqual(json.loads(self.w.run("show", self.item, "--json").stdout)["state"], "approved")

    def test_taking_the_forge_applies_the_close_and_records_why(self) -> None:
        """Prevents a forge change applying without a trail: the close lands as a forge-provenance event with the reason."""
        done = self.w.run("resolve", self.item, "--take", "forge", "--reason", "closed on purpose", env=self.env)
        self.assertEqual(done.returncode, 0, done.stderr)
        events = self.w.events(self.item)
        self.assertEqual([e["reason"] for e in events if e["type"] == "divergence_accepted"], ["closed on purpose"])
        self.assertEqual(json.loads(self.w.run("show", self.item, "--json").stdout)["state"], "closed")

    def test_resolve_refuses_an_unknown_reference_and_a_ledger_side_for_a_divergence(self) -> None:
        """Prevents guessing: an unknown reference and `ours` (a merge side) are refused with exit 2 and nothing is written."""
        before = len(self.w.events(self.item))
        self.assertEqual(self.w.run("resolve", "nope", "--take", "forge", "--reason", "x").returncode, 2)
        wrong = self.w.run("resolve", self.item, "--take", "ours", "--reason", "x")
        self.assertEqual(wrong.returncode, 2)
        self.assertEqual(len(self.w.events(self.item)), before)

    def test_status_and_the_session_hook_point_at_qew_conflicts(self) -> None:
        """Prevents an agent not knowing conflicts exist: status and the session-start context name the command."""
        self.assertIn("qew conflicts", self.w.run("status").stdout)
        self.assertEqual(json.loads(self.w.run("status", "--json").stdout)["conflicts"], 1)
        hook = self.w.run("hook", "session-start", "--agent", "claude", stdin="{}")
        self.assertIn("qew conflicts", hook.stdout)

    def test_accept_and_reject_keep_working(self) -> None:
        """Prevents the older verbs regressing: reject still settles the divergence."""
        self.assertEqual(self.w.run("reject", self.item, env=self.env).returncode, 0)
        self.assertEqual(listing(self.w), [])


if __name__ == "__main__":
    unittest.main()
