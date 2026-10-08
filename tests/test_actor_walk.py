"""Walks of the actor written into events: untrusted environment text must be a safe token.

Adversary: a malformed identity (spaces, commas, newlines) reaching event files and later forge labels
or rendered output, and a header label posing as the author or the claimant.
"""

import json
import unittest

from tests.ledger_harness import World
from tests.plan_support import snapshot


class ActorWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_an_actor_with_a_space_or_comma_is_refused_before_anything_is_written(self) -> None:
        """A login that cannot be a label must not reach event files, where it would be mirrored to forges."""
        w = self.world
        item = w.create("--title", "Work", "--quote", "Alexander: 'please track this work in the Qew ledger now'")
        before = snapshot(w)
        for bad in ("bad login,x", "two words", "a,b"):
            for command in (("create", "--title", "Other"), ("start", item)):
                result = w.run(*command, env={"QEW_ACTOR": bad})
                self.assertEqual(result.returncode, 3, (bad, command))
                self.assertIn("QEW_ACTOR", result.stderr)
        self.assertEqual(snapshot(w), before)

    def test_a_newline_in_the_actor_is_refused(self) -> None:
        """A newline would forge extra lines in rendered output and commit messages."""
        w = self.world
        result = w.run("create", "--title", "Other", env={"QEW_ACTOR": "mallory\nsecond line"})
        self.assertEqual(result.returncode, 3)
        self.assertEqual(snapshot(w), {})

    def test_check_flags_a_planted_event_whose_actor_is_not_a_token(self) -> None:
        """A hand-merged event with an unsafe actor must be found, not trusted."""
        w = self.world
        item = w.create("--title", "Work")
        planted = {"type": "comment", "at": "2026-01-01T00:00:00Z", "actor": "evil, admin", "body": "x"}
        (w.ledger / "events" / item / ("z" * 26 + ".json")).write_text(json.dumps(planted))
        result = w.run("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("actor", result.stdout)

    def test_a_header_label_never_names_the_claimant(self) -> None:
        """Claiming is by event: a label that looks like an identity must change nothing."""
        w = self.world
        item = w.create("--title", "Work", "--quote", "Alexander: 'please track this work in the Qew ledger now'")
        path = w.ledger / "items" / f"{item}.md"
        path.write_text(path.read_text().replace("labels: \n", "labels: author:mallory\n", 1))
        self.assertEqual(w.run("start", item).returncode, 0)
        self.assertEqual(w.events(item)[-1]["owner"], "tester")
        checked = w.run("check")
        self.assertEqual(checked.returncode, 1)
        self.assertIn("author:mallory", checked.stdout)


if __name__ == "__main__":
    unittest.main()
