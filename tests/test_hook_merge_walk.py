"""Walk of the post-tool completion hint after an agent merges a pull request.

Adversary: a hint driven by data in command text (quotes, branch names, PR 0), a hint for a pull request
that is not merged or a forge that cannot be read, an automatic completion, and taking another actor's
claim without saying so.
"""

import unittest

from tests.pr_harness import PrWorld
from qew import hook_merge


class MergeHintWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        self.item = self.w.item()
        self.assertEqual(self.w.open(self.item, "--ready").code, 0)
        self.w.forge.pulls[1] = self._pull(checks="passing", mergeable=True)

    def _pull(self, **changes):
        from dataclasses import replace
        return replace(self.w.forge.pulls[1], **changes)

    def build(self, mirror):
        return self.w.forge

    def hint(self, command: str, build=None):
        with self.w._env():
            return hook_merge.reminder(command, self.w.world.code, build or self.build)

    def test_a_merge_the_agent_ran_names_complete_and_land_and_completes_nothing(self) -> None:
        """Merged-but-open items must not go stale; completion stays an explicit act."""
        self.w.world.run("evidence", self.item, "--change", self.w.commit("work"))
        self.w.forge.pulls[1] = self._pull(state="merged")
        text = self.hint("qew pr merge 1")
        self.assertIn(f"qew complete {self.item}", text)
        self.assertIn("qew land 1", text)
        self.assertIn("#1", text)
        self.assertEqual(self.hint("cd sub && qew pr merge 1 --target git.example"), text)
        self.assertIn("claimed", self.w.world.run("show", self.item).stdout)

    def test_an_item_without_evidence_is_told_to_record_it_first(self) -> None:
        """The hint must not send an agent into a refused completion."""
        self.w.forge.pulls[1] = self._pull(state="merged")
        text = self.hint("qew pr merge 1")
        self.assertIn(f"qew evidence {self.item}", text)
        self.assertIn(f"qew complete {self.item}", text)

    def test_command_text_that_only_mentions_a_merge_is_silent(self) -> None:
        """Quotes, PR 0, a branch name or other commands are data, never a trigger."""
        self.w.forge.pulls[1] = self._pull(state="merged")
        for command in ('echo "qew pr merge 1"', "qew pr merge 0", "qew pr merge feature", "qew pr merge", "qew pr ready 1",
                        "qew pr merge -1", "gh pr merge 1", "qew pr merge 99", ""):
            with self.subTest(command=command):
                self.assertIsNone(self.hint(command))

    def test_an_open_pull_request_or_an_unreadable_forge_is_silent(self) -> None:
        """No false completion hint: only a merged state counts."""
        self.assertIsNone(self.hint("qew pr merge 1"))
        self.w.forge.pulls[1] = self._pull(state="merged")

        def broken(mirror):
            raise OSError("forge down")

        self.assertIsNone(self.hint("qew pr merge 1", broken))

    def test_another_actors_claim_is_named_not_taken(self) -> None:
        """The hint must not complete or take over work someone else holds."""
        other = self.w.item("Theirs", by="someone-else")
        self.assertEqual(self.w.open(other, "--ready", actor="someone-else").code, 0)
        self.w.forge.pulls[2] = self._pull(number=2, state="merged")
        text = self.hint("qew pr merge 2")
        self.assertIn("someone-else", text)
        self.assertIn("--take-over", text)
        self.assertNotIn(f"`qew complete {other}`", text)

    def test_through_the_hook_command_an_unreachable_forge_stays_silent(self) -> None:
        """The real hook has no forge in this world; it must print nothing and exit 0."""
        from tests.test_hook_v1_walk import feed
        result = feed(self.w.world, "post-tool", "claude", "qew pr merge 1")
        self.assertEqual((result.returncode, result.stdout), (0, ""))
        self.assertEqual(feed(self.w.world, "post-tool", "cursor", "qew pr merge 1").stdout, "")


if __name__ == "__main__":
    unittest.main()
