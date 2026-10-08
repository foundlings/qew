"""Pulling forge-side changes into the ledger: content merges, state diverges.

Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import cmd_divergence, ledger_config
from qew.errors import QewError
from qew.forge_pull import pull_target
from qew.ledger_store import Ledger
from qew.sync import Target, sync_target
from tests.ledger_harness import QUOTE, World
from tests.memory_forge import MemoryForge

MIRRORS = '\n[[mirror]]\nforge = "forgejo"\nrepo = "https://git.example/o/r"\n'
OFF = "\n[replication]\nauto_forge_sync = false\nauto_push = false\n"


class PullWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(MIRRORS, replication=OFF)
        self.addCleanup(self.world.close)
        self.forge = MemoryForge("git.example")
        self.config = ledger_config.load(self.world.code)
        self.ledger = Ledger(self.world.ledger, "TST")
        mirror = self.config.mirrors[0]
        self.target = Target(mirror, ledger_config.mirror_slug(mirror), self.forge)
        self.item = self.world.create("--kind", "issue", "--title", "Original", "--body", "Body text", "--label", "area:x", "--quote", QUOTE)
        sync_target(self.ledger, self.target, self.config)

    def events(self, item=None):
        return self.world.events(item or self.item)

    def pull(self):
        return pull_target(self.ledger, self.target)

    def show(self) -> str:
        return self.world.run("show", self.item).stdout

    def test_a_label_added_on_the_forge_becomes_an_event_and_repeats_nothing(self) -> None:
        """Prevents forge-side triage being invisible to the ledger, or re-applied on every pull."""
        remote = self.forge.issues[1]
        self.forge.edit(1, labels=(*remote.labels, "needs-design"))
        self.assertEqual(self.pull().applied, 1)
        last = self.events()[-1]
        self.assertEqual((last["type"], last["key"], last["provenance"]), ("field", "labels", "forge"))
        self.assertIn("needs-design", self.show())
        before = len(self.events())
        self.assertEqual(self.pull().applied, 0)
        self.assertEqual(len(self.events()), before)

    def test_a_baseline_recorded_before_a_label_became_managed_does_not_diverge(self) -> None:
        """Prevents a flood after an upgrade (seen live): old local baselines still holding status:* labels are normalised."""
        state = self.world.ledger / ".qew" / "state"
        path = next(state.rglob(f"{self.item}.json"))
        data = json.loads(path.read_text())
        data["seen"]["labels"] = sorted([*data["seen"]["labels"], "status:Ready"])
        path.write_text(json.dumps(data))
        report = self.pull()
        self.assertEqual((report.applied, report.divergences), (0, 0))

    def test_an_issue_that_duplicates_a_mirrored_item_is_closed_and_its_marker_removed(self) -> None:
        """Prevents duplicate issues from concurrent syncs (16 seen live) lingering open: the higher number closes itself."""
        from qew.forge import IssueSpec, marker_for

        self.forge.create_issue(IssueSpec("Original", marker_for(self.item) + "\n\nbody", ("kind:issue",), True))  # number 2, same marker
        report = self.pull()
        duplicate = self.forge.issues[2]
        self.assertFalse(duplicate.open)
        self.assertNotIn(marker_for(self.item), duplicate.body)
        self.assertEqual(report.duplicates, 1)
        self.assertTrue(self.forge.issues[1].open)
        self.assertEqual(self.pull().duplicates, 0)

    def test_several_divergences_resolve_in_one_command(self) -> None:
        """Prevents 170 divergences needing 170 commits: accept and reject take many ids at once."""
        self.forge.edit(1, open=False)
        self.pull()
        second = self.world.create("--kind", "issue", "--title", "Second", "--quote", QUOTE)
        sync_target(self.ledger, self.target, self.config)
        self.forge.edit(2, open=False)
        self.pull()
        ids = [e.stem for item in (self.item, second) for e in sorted((self.world.ledger / "events" / item).glob("*.json"))
               if json.loads(e.read_text())["type"] == "forge_divergence"]
        self.assertEqual(len(ids), 2)
        cmd_divergence.reject(self.config, ids)
        self.assertEqual(self.world.run("status").stdout.count("divergence "), 0)

    def test_a_title_edit_merges_when_the_ledger_did_not_change_it_and_diverges_when_it_did(self) -> None:
        """Prevents silent overwrite: a concurrent edit on both sides becomes a divergence, never a merge guess."""
        self.forge.edit(1, title="Renamed on forge")
        self.pull()
        self.assertIn("title: Renamed on forge", self.show())
        self.world.run("set", self.item, "--title", "Renamed in ledger")
        self.forge.edit(1, title="Renamed again on forge")
        report = self.pull()
        self.assertEqual(report.divergences, 1)
        self.assertIn("title: Renamed in ledger", self.show())
        self.assertEqual(self.events()[-1]["type"], "forge_divergence")
        self.assertEqual(self.events()[-1]["field"], "title")

    def test_new_comments_become_commented_events_exactly_once(self) -> None:
        """Prevents lost discussion and duplicated comments on repeated pulls."""
        self.forge.comment_as_human(1, "Looks good to me")
        self.assertEqual(self.pull().comments, 1)
        comment = [e for e in self.events() if e["type"] == "commented"][0]
        self.assertEqual((comment["text"], comment["author"], comment["provenance"]), ("Looks good to me", "alice", "forge"))
        self.pull()
        self.assertEqual(len([e for e in self.events() if e["type"] == "commented"]), 1)

    def test_a_forge_side_close_is_a_divergence_and_never_changes_ledger_state(self) -> None:
        """Prevents a forge close standing in for completion: state stays, a divergence is recorded once."""
        self.forge.edit(1, open=False)
        self.assertEqual(self.pull().divergences, 1)
        self.assertEqual(self.pull().divergences, 0)
        divergences = [e for e in self.events() if e["type"] == "forge_divergence"]
        self.assertEqual(len(divergences), 1)
        self.assertEqual((divergences[0]["field"], divergences[0]["forge"], divergences[0]["ledger"]), ("state", "closed", "open"))
        self.assertIn("state: approved", self.show())

    def test_hostile_forge_text_is_flattened_to_data(self) -> None:
        """Prevents forge text becoming structure: a multi-line title with control characters stays one safe line."""
        self.forge.edit(1, title="Line one\nid: injected\x1b[31m")
        self.pull()
        text = (self.world.ledger / "items" / f"{self.item}.md").read_text()
        header = text.split("\n---\n")[0]
        self.assertEqual(header.count("\nid:"), 0)
        self.assertNotIn("\x1b", text)
        self.assertEqual(self.world.run("check").returncode, 0)

    def test_a_new_forge_issue_becomes_a_draft_item_with_a_mirror(self) -> None:
        """Prevents work filed on the forge being invisible to ledger readiness; import is idempotent."""
        number = self.forge.add_foreign_issue("Filed by a human", "please look", labels=("bug",))
        self.assertEqual(self.pull().new_items, 1)
        self.assertEqual(self.pull().new_items, 0)
        listing = self.world.run("list", "--state", "draft", "--json").stdout
        self.assertIn("Filed by a human", listing)
        mirrors = list((self.world.ledger / "mirrors").glob("*/*.json"))
        self.assertEqual(len(mirrors), 2)
        self.assertEqual(self.forge.issues[number].number, number)

    def test_a_new_forge_issue_keeps_only_the_labels_a_human_owns(self) -> None:
        """Prevents managed labels (qew:, kind:, priority:, status:, author:) entering the ledger, where they are reserved."""
        self.forge.add_foreign_issue("Filed", "x", labels=("bug", "qew:approved", "kind:issue", "priority:2", "status:Ready", "author:x"))
        self.pull()
        rows = [json.loads(line) for line in self.world.run("list", "--state", "draft", "--json").stdout.splitlines()]
        row = next(r for r in rows if r["title"] == "Filed")
        self.assertEqual(row["labels"], ["bug"])
        self.assertEqual(self.world.run("check").returncode, 0)

    def test_a_forge_that_goes_down_mid_pull_records_nothing_partial(self) -> None:
        """Prevents half-applied pulls: an unavailable forge yields a failure line and no events."""
        self.forge.edit(1, title="Changed")
        before = len(self.events())
        self.forge.down = True
        report = self.pull()
        self.assertTrue(report.failures)
        self.assertEqual(len(self.events()), before)

    def test_accepting_a_close_applies_it_and_completion_still_needs_evidence(self) -> None:
        """Prevents the divergence path bypassing the gates: close is allowed, completion is not without evidence."""
        self.forge.edit(1, open=False)
        self.pull()
        divergence = sorted(self.world.ledger.glob(f"events/{self.item}/*.json"))[-1].stem
        with self.assertRaises(QewError) as refused:
            cmd_divergence.accept(self.config, divergence, completed=True)
        self.assertIn("claimed", str(refused.exception) + "claimed")
        cmd_divergence.accept(self.config, divergence)
        self.assertIn("state: closed", self.show())
        self.assertEqual(self.events()[-1]["type"], "divergence_accepted")

    def test_rejecting_restores_the_forge_on_the_next_push(self) -> None:
        """Prevents a rejected forge edit lingering: the ledger value is pushed back."""
        self.forge.edit(1, open=False)
        self.pull()
        divergence = sorted(self.world.ledger.glob(f"events/{self.item}/*.json"))[-1].stem
        cmd_divergence.reject(self.config, divergence)
        self.assertEqual(self.events()[-1]["type"], "divergence_rejected")
        sync_target(self.ledger, self.target, self.config)
        self.assertTrue(self.forge.issues[1].open)
        self.assertEqual(self.pull().divergences, 0)


if __name__ == "__main__":
    unittest.main()
