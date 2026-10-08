"""Walk of the post-tool completion hint after an agent merges a pull request.

Adversary: a hint driven by data in command text (quotes, branch names, PR 0), a hint for a pull request
that is not merged or a forge that cannot be read, an automatic completion, and taking another actor's
claim without saying so.
"""

import os
import unittest
from unittest import mock

from tests.ledger_harness import QUOTE, World  # first: puts src on sys.path
from qew import cmd_pr, hook_merge, ledger_config
from qew.ledger_store import Ledger
from qew.sync import Target, sync_target
from tests.memory_forge import MemoryForge
from tests.test_hook_v1_walk import feed

MIRRORS = '\n[[mirror]]\nforge = "forgejo"\nrepo = "https://git.example/o/r"\n'


class MergeHintWalk(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.dict(os.environ, {"QEW_ACTOR": "tester"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.world = World(MIRRORS)
        self.addCleanup(self.world.close)
        self.forge = MemoryForge("git.example")
        self.config = ledger_config.load(self.world.code)
        self.item = self.world.create("--title", "Work", "--quote", QUOTE)
        self.world.run("start", self.item)
        ledger = Ledger(self.world.ledger, "TST")
        mirror = self.config.mirrors[0]
        sync_target(ledger, Target(mirror, ledger_config.mirror_slug(mirror), self.forge), self.config)
        cmd_pr.open_pull_request(self.config, self.item, None, "feature", "main", True, self.build)

    def build(self, mirror):
        return self.forge

    def merge(self) -> None:
        cmd_pr.merge_pull_request(self.config, 1, None, self.build)

    def hint(self, command: str):
        return hook_merge.reminder(command, self.world.code, self.build)

    def test_a_merge_the_agent_ran_gets_one_line_naming_complete_and_nothing_is_completed(self) -> None:
        """Merged-but-open items must not go stale; completion stays an explicit act."""
        self.world.run("evidence", self.item, "--change", self.world.commit("work"))
        self.merge()
        text = self.hint("qew pr merge 1")
        self.assertEqual(len(text.splitlines()), 1)
        self.assertIn(f"qew complete {self.item}", text)
        self.assertIn("#1", text)
        self.assertEqual(self.hint("cd sub && qew pr merge 1 --target git.example"), text)
        self.assertIn("claimed", self.world.run("show", self.item).stdout)

    def test_an_item_without_evidence_is_told_to_record_it_first(self) -> None:
        """The hint must not send an agent into a refused completion."""
        self.merge()
        text = self.hint("qew pr merge 1")
        self.assertIn(f"qew evidence {self.item}", text)
        self.assertIn(f"qew complete {self.item}", text)

    def test_command_text_that_only_mentions_a_merge_is_silent(self) -> None:
        """Quotes, PR 0, a branch name or other commands are data, never a trigger."""
        self.merge()
        for command in ('echo "qew pr merge 1"', "qew pr merge 0", "qew pr merge feature", "qew pr merge", "qew pr ready 1",
                        "qew pr merge -1", "gh pr merge 1", "qew pr merge 99", ""):
            with self.subTest(command=command):
                self.assertIsNone(self.hint(command))

    def test_an_open_pull_request_or_an_unreadable_forge_is_silent(self) -> None:
        """No false completion hint: only a merged state counts."""
        self.assertIsNone(self.hint("qew pr merge 1"))

        def broken(mirror):
            raise OSError("forge down")

        self.merge()
        self.assertIsNone(hook_merge.reminder("qew pr merge 1", self.world.code, broken))

    def test_another_actors_claim_is_named_not_taken(self) -> None:
        """The hint must not complete or take over work someone else holds."""
        other = self.world.create("--title", "Theirs", "--quote", QUOTE)
        self.world.run("start", other, env={"QEW_ACTOR": "someone-else"})
        ledger = Ledger(self.world.ledger, "TST")
        mirror = self.config.mirrors[0]
        sync_target(ledger, Target(mirror, ledger_config.mirror_slug(mirror), self.forge), self.config)
        cmd_pr.open_pull_request(self.config, other, None, "theirs", "main", True, self.build)
        cmd_pr.merge_pull_request(self.config, 2, None, self.build)
        text = self.hint("qew pr merge 2")
        self.assertIn("someone-else", text)
        self.assertIn("--take-over", text)
        self.assertNotIn(f"`qew complete {other}`", text)

    def test_through_the_hook_command_an_unreachable_forge_stays_silent(self) -> None:
        """The real hook has no forge in this world; it must print nothing and exit 0."""
        result = feed(self.world, "post-tool", "claude", "qew pr merge 1")
        self.assertEqual((result.returncode, result.stdout), (0, ""))
        self.assertEqual(feed(self.world, "post-tool", "cursor", "qew pr merge 1").stdout, "")


if __name__ == "__main__":
    unittest.main()
