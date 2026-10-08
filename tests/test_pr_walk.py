"""Walks of `qew pr`: open for an item, ready, and merge only a checked head.

Adversary: a closing keyword that lets the forge complete work around qew, a
draft, conflicted or red pull request merged, a head that moved after the check
being merged anyway, and a pull request qew never recorded merged by number.
"""

import json
import os
import sys
import unittest
from dataclasses import replace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import cmd_pr, cmd_sync, ledger_config
from qew.errors import QewError
from qew.ledger_store import Ledger
from qew.sync import Target, sync_target
from tests.ledger_harness import World
from tests.memory_forge import MemoryForge

MIRRORS = '\n[[mirror]]\nforge = "forgejo"\nrepo = "https://git.example/o/r"\n'


class PrWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(MIRRORS)
        self.addCleanup(self.world.close)
        self.forge = MemoryForge("git.example")
        self.config = ledger_config.load(self.world.code)
        self.ledger = Ledger(self.world.ledger, "TST")
        self.item = self.world.create("--kind", "issue", "--title", "Work", "--quote", "Alexander: 'please track this work in the Qew ledger now'")
        self.world.run("start", self.item)
        mirror = self.config.mirrors[0]
        sync_target(self.ledger, Target(mirror, ledger_config.mirror_slug(mirror), self.forge), self.config)

    def build(self, mirror):
        return self.forge

    def open(self, **kwargs):
        return cmd_pr.open_pull_request(self.config, self.item, None, "feature", "main", kwargs.get("ready", False), self.build)

    def test_open_records_a_pull_request_mirror_without_a_closing_keyword(self) -> None:
        line = self.open()
        spec = self.forge.pull_specs[1]
        self.assertTrue(spec.draft)
        self.assertEqual((spec.head, spec.base), ("feature", "main"))
        for keyword in ("closes", "fixes", "resolves"):
            self.assertNotIn(keyword, (spec.body + spec.title).lower())
        self.assertIn(self.item, spec.body)
        files = list((self.world.ledger / "mirrors" / self.item).glob("*pr*.json"))
        self.assertEqual(len(files), 1)
        self.assertEqual(json.loads(files[0].read_text())["kind"], "pr")
        self.assertIn("#1", line)

    def test_merge_refuses_draft_red_conflicted_and_unrecorded_pull_requests(self) -> None:
        self.open()
        for change, fragment in (
            ({}, "draft"),
            ({"draft": False, "checks": "failing"}, "checks"),
            ({"checks": "pending"}, "checks"),
            ({"checks": "passing", "mergeable": False}, "conflict"),
            ({"mergeable": None}, "conflict"),
        ):
            self.forge.pulls[1] = replace(self.forge.pulls[1], **change)
            with self.assertRaises(QewError) as caught:
                cmd_pr.merge_pull_request(self.config, 1, None, self.build)
            self.assertIn(fragment, str(caught.exception))
        self.assertEqual(self.forge.merged, [])
        with self.assertRaises(QewError) as unknown:
            cmd_pr.merge_pull_request(self.config, 99, None, self.build)
        self.assertIn("not recorded", str(unknown.exception))

    def test_merge_merges_exactly_the_checked_head_and_points_at_complete(self) -> None:
        self.open()
        cmd_pr.mark_ready(self.config, 1, None, self.build)
        line = cmd_pr.merge_pull_request(self.config, 1, None, self.build)
        self.assertEqual(self.forge.merged, [(1, "sha1")])
        self.assertIn("qew complete", line)

    def test_a_head_that_moved_after_the_check_is_refused_by_the_forge(self) -> None:
        self.open()
        cmd_pr.mark_ready(self.config, 1, None, self.build)
        real_read = self.forge.read_pull_request

        def moving(number):
            seen = real_read(number)
            self.forge.pulls[number] = replace(seen, head_sha="moved")
            return seen

        self.forge.read_pull_request = moving
        with self.assertRaises(Exception):
            cmd_pr.merge_pull_request(self.config, 1, None, self.build)
        self.assertEqual(self.forge.merged, [])


if __name__ == "__main__":
    unittest.main()
