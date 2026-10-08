"""Walks of `sync`: idempotence, per-target independence, no partial mirror files.

Adversary: a duplicate issue after a crash between the forge accepting a create
and the mirror file being written, one dead forge blocking another, a mirror
file recording work the forge never accepted, and a dependency or board call
the forge cannot do being faked.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import ledger_config
from qew.forge import COLUMNS
from qew.ledger_store import Ledger
from qew.sync import Target, sync_target
from tests.ledger_harness import World
from tests.memory_forge import MemoryForge

MIRRORS = """
[[mirror]]
forge = "forgejo"
repo = "https://git.example/o/r"
[[mirror]]
forge = "gitlab"
repo = "https://lab.example/g/r"
select = "kind:epic"
"""


def targets(world: World, forges):
    config = ledger_config.load(world.code)
    return config, [Target(m, ledger_config.mirror_slug(m), f) for m, f in zip(config.mirrors, forges)]


class SyncWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(MIRRORS)
        self.addCleanup(self.world.close)
        self.a = MemoryForge("git.example")
        self.b = MemoryForge("lab.example")

    def ledger(self) -> Ledger:
        return Ledger(self.world.ledger, "TST")

    def sync(self, *names):
        config, all_targets = targets(self.world, [self.a, self.b])
        chosen = [t for t in all_targets if not names or t.slug in names]
        return [sync_target(self.ledger(), t, config) for t in chosen]

    def test_golden_walk_mirrors_per_target_and_a_second_sync_is_a_no_op(self) -> None:
        w = self.world
        epic = w.create("--kind", "epic", "--title", "Epic")
        issue = w.create("--kind", "issue", "--title", "Issue", "--parent", epic)
        reports = self.sync()
        self.assertEqual([r.created for r in reports], [2, 1], "select = kind:epic keeps the issue off the second target")
        mirror = json.loads((w.ledger / "mirrors" / issue / "forgejo-git.example-o-r.json").read_text())
        self.assertEqual((mirror["forge"], mirror["kind"], mirror["number"]), ("forgejo", "issue", 2))
        self.assertTrue((w.ledger / "mirrors" / epic / "gitlab-lab.example-g-r.json").exists())
        self.assertFalse((w.ledger / "mirrors" / issue / "gitlab-lab.example-g-r.json").exists())
        before = (self.a.count("create"), self.a.count("update"), self.b.count("create"))
        again = self.sync()
        self.assertEqual([(r.created, r.updated) for r in again], [(0, 0), (0, 0)])
        self.assertEqual((self.a.count("create"), self.a.count("update"), self.b.count("create")), before)

    def test_select_values_may_list_alternatives_with_commas(self) -> None:
        w = self.world
        w.create("--kind", "epic", "--title", "E")
        w.create("--kind", "issue", "--title", "I")
        w.create("--kind", "task", "--title", "T")
        config, all_targets = targets(w, [self.a, self.b])
        mirror = all_targets[0].mirror
        from dataclasses import replace

        narrowed = Target(replace(mirror, select="kind:epic,issue"), all_targets[0].slug, self.a)
        report = sync_target(self.ledger(), narrowed, config)
        self.assertEqual(report.created, 2)
        self.assertEqual(sorted(i.title for i in self.a.issues.values()), ["E", "I"])

    def test_a_header_change_updates_the_mirror_once(self) -> None:
        w = self.world
        item = w.create("--kind", "epic", "--title", "Old")
        self.sync()
        w.run("set", item, "--title", "New")
        report = self.sync("forgejo-git.example-o-r")[0]
        self.assertEqual(report.updated, 1)
        self.assertEqual(self.a.issues[1].title, "New")
        self.assertEqual(self.sync("forgejo-git.example-o-r")[0].updated, 0)

    def test_state_maps_to_open_closed_labels_and_columns(self) -> None:
        w = self.world
        item = w.create("--kind", "epic", "--title", "E")
        self.sync("forgejo-git.example-o-r")
        self.assertIn("qew:draft", self.a.issues[1].labels)
        self.assertTrue(self.a.issues[1].open)
        w.approve(item)
        w.run("start", item)
        child = w.create("--kind", "issue", "--title", "C", "--parent", item, "--quote", "Alexander: 'please track this work in the Qew ledger now'")
        w.run("start", child)
        w.run("evidence", child, "--change", w.commit())
        w.run("complete", child)
        w.run("complete", item)
        self.sync("forgejo-git.example-o-r")
        self.assertFalse(self.a.issues[1].open)
        self.assertIn("qew:completed", self.a.issues[1].labels)

    def test_repository_mode_adds_no_managed_label_and_overlay_mode_does(self) -> None:
        """Prevents label noise in a tracker qew owns outright (repository mode, the default) while an overlay on a shared
        tracker still marks its issues so views can filter out human ones."""
        from dataclasses import replace

        w = self.world
        w.create("--kind", "epic", "--title", "E")
        config, all_targets = targets(w, [self.a, self.b])
        plain = all_targets[0]
        self.assertEqual(plain.mirror.mode, "repository")
        sync_target(self.ledger(), plain, config)
        self.assertNotIn("qew:managed", self.a.issues[1].labels)
        overlay = Target(replace(plain.mirror, mode="overlay"), plain.slug, plain.forge)
        sync_target(self.ledger(), overlay, config)
        self.assertIn("qew:managed", self.a.issues[1].labels)
        sync_target(self.ledger(), plain, config)
        self.assertNotIn("qew:managed", self.a.issues[1].labels)

    def test_mode_must_be_repository_or_overlay(self) -> None:
        """Prevents a typo silently choosing a mode."""
        w = self.world
        path = w.code / ".qew" / "config.toml"
        path.write_text(path.read_text().replace('repo = "https://git.example/o/r"', 'repo = "https://git.example/o/r"\nmode = "shared"'))
        result = w.run("status")
        self.assertEqual(result.returncode, 2)
        self.assertIn("mode", result.stderr)

    def test_forges_without_boards_carry_the_status_as_a_label(self) -> None:
        """Prevents a Forgejo repository from showing no sign of the seven-status model."""
        w = self.world
        item = w.create("--kind", "issue", "--title", "I", "--quote", "Alexander: 'please track this work in the Qew ledger now'")
        self.sync("forgejo-git.example-o-r")
        self.assertIn("status:Ready", self.a.issues[1].labels)
        w.run("start", item)
        self.sync("forgejo-git.example-o-r")
        self.assertIn("status:In progress", self.a.issues[1].labels)
        self.assertNotIn("status:Ready", self.a.issues[1].labels)

    def test_a_dead_forge_does_not_block_another_and_leaves_no_mirror_file(self) -> None:
        w = self.world
        item = w.create("--kind", "epic", "--title", "E")
        self.a.down = True
        reports = self.sync()
        self.assertTrue(reports[0].failures)
        self.assertEqual(reports[1].created, 1)
        self.assertFalse((w.ledger / "mirrors" / item / "forgejo-git.example-o-r.json").exists())
        self.a.down = False
        self.assertEqual(self.sync("forgejo-git.example-o-r")[0].created, 1)

    def test_a_crash_after_create_adopts_the_existing_issue_instead_of_duplicating(self) -> None:
        w = self.world
        w.create("--kind", "epic", "--title", "E")
        self.sync("forgejo-git.example-o-r")
        for path in (w.ledger / "mirrors").rglob("forgejo-*.json"):
            path.unlink()
        report = self.sync("forgejo-git.example-o-r")[0]
        self.assertEqual((report.created, len(self.a.issues)), (0, 1))
        self.assertEqual(report.adopted, 1)

    def test_a_concurrent_creator_leaves_exactly_one_open_issue_and_the_lowest_number_wins(self) -> None:
        """Prevents duplicate issues when two checkouts sync one new item at once (seen live): the later one closes itself."""
        w = self.world
        item = w.create("--kind", "epic", "--title", "Raced")
        from qew.forge import IssueSpec, marker_for

        self.a.create_issue(IssueSpec("Raced", marker_for(item) + "\n\nbody", (), True))  # the other checkout's issue, number 1
        self.a.hide_next_find = True  # our first lookup misses it
        report = self.sync("forgejo-git.example-o-r")[0]
        self.assertEqual(report.failures, [])
        open_with_marker = [i for i in self.a.issues.values() if i.open and marker_for(item) in i.body]
        self.assertEqual([i.number for i in open_with_marker], [1])
        duplicate = self.a.issues[2]
        self.assertFalse(duplicate.open)
        self.assertNotIn(marker_for(item), duplicate.body)
        mirror = json.loads(next((w.ledger / "mirrors" / item).glob("*.json")).read_text())
        self.assertEqual(mirror["number"], 1)

    def test_a_rejected_item_is_reported_and_the_rest_still_sync(self) -> None:
        w = self.world
        w.create("--kind", "epic", "--title", "bad one")
        w.create("--kind", "epic", "--title", "good one")
        self.a.reject_title = "bad"
        report = self.sync("forgejo-git.example-o-r")[0]
        self.assertEqual((report.created, len(report.failures)), (1, 1))

    def test_dependencies_are_linked_when_supported_and_skipped_loudly_when_not(self) -> None:
        w = self.world
        first = w.create("--kind", "epic", "--title", "First")
        w.create("--kind", "epic", "--title", "Second", "--needs", first)
        self.sync()
        self.assertEqual(self.a.links, [(2, 1)])
        self.b.supports_links = False
        report = self.sync("gitlab-lab.example-g-r")[0]
        self.assertEqual(report.failures, [])

    def test_boards_get_the_column_for_the_state_and_unsupported_boards_are_skipped(self) -> None:
        w = self.world
        (w.code / ".qew" / "config.toml").write_text(
            (w.code / ".qew" / "config.toml").read_text().replace('repo = "https://git.example/o/r"', 'repo = "https://git.example/o/r"\nproject = 1')
        )
        item = w.create("--kind", "issue", "--title", "E", "--quote", "Alexander: 'please track this work in the Qew ledger now'")
        self.sync("forgejo-git.example-o-r")
        self.assertEqual(self.a.columns[1], "Ready")
        self.assertEqual(COLUMNS[2], "Ready")
        w.run("start", item)
        self.sync("forgejo-git.example-o-r")
        self.assertEqual(self.a.columns[1], "In progress")
        self.a.supports_boards = False
        report = self.sync("forgejo-git.example-o-r")[0]
        self.assertEqual(report.failures, [])


if __name__ == "__main__":
    unittest.main()
