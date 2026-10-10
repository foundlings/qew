"""Comments sync both ways with the forge issue: pushed once, edits and deletes follow, nothing is duplicated or looped.

Each test is a scenario; its docstring names the failure it prevents. A pushed comment carries a marker with its ledger
id, so a rerun, another machine or an echo of our own comment is recognised and never imported or pushed twice.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import ledger_config
from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

OFF = "\n[replication]\nauto_forge_sync = false\nauto_push = false\nforge_concurrency = 1\n"
ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}


class CommentMirrorWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub().start()
        self.addCleanup(self.fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'
        self.world = World(mirrors, replication=OFF)
        self.addCleanup(self.world.close)
        self.slug = ledger_config.mirror_slug(ledger_config.load(self.world.code).mirrors[0])
        self.item = self.world.create("--kind", "issue", "--title", "Talk", "--quote", QUOTE)

    def sync(self) -> None:
        result = self.world.run("sync", env=ENV)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def forge_comments(self) -> list:
        return next(iter(self.fake.issues.values()))["discussion"]

    def post(self, text: str, *extra: str, actor: str = "alice") -> str:
        result = self.world.run("comment", self.item, "--body", text, *extra, env={"QEW_ACTOR": actor})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout.split()[-1]

    def test_a_ledger_comment_is_pushed_once_with_its_marker_and_author(self) -> None:
        """Prevents ledger comments never reaching the forge, or reaching it again on every sync."""
        comment = self.post("hello from the ledger")
        self.sync()
        self.sync()
        pushed = self.forge_comments()
        self.assertEqual(len(pushed), 1)
        self.assertIn(f"<!-- qew-comment:{comment} -->", pushed[0]["body"])
        self.assertIn("hello from the ledger", pushed[0]["body"])
        self.assertIn("alice", pushed[0]["body"])

    def test_our_own_pushed_comment_is_not_imported_back_but_a_human_comment_is(self) -> None:
        """Prevents an echo loop: the pushed comment stays one ledger comment; a human's forge comment arrives once."""
        self.post("ledger side")
        self.sync()
        issue_number = next(iter(self.fake.issues))
        self.fake.add_comment(issue_number, "dana", "a human replies on GitHub")
        self.sync()
        self.sync()
        shown = self.world.run("show", self.item).stdout
        self.assertEqual(shown.count("ledger side"), 1)
        self.assertEqual(shown.count("a human replies on GitHub"), 1)
        self.assertEqual(len(self.forge_comments()), 2)

    def test_a_ledger_edit_updates_the_same_forge_comment_and_a_ledger_delete_removes_it(self) -> None:
        """Prevents duplicate forge comments after an edit, or a deleted ledger comment lingering on the forge."""
        comment = self.post("first words")
        self.sync()
        forge_id = self.forge_comments()[0]["id"]
        self.assertEqual(self.world.run("comment", self.item, "--edit", comment, "--body", "second words", env={"QEW_ACTOR": "alice"}).returncode, 0)
        self.sync()
        self.assertEqual(len(self.forge_comments()), 1)
        self.assertEqual(self.forge_comments()[0]["id"], forge_id)
        self.assertIn("second words", self.forge_comments()[0]["body"])
        self.assertEqual(self.world.run("comment", self.item, "--delete", comment, env={"QEW_ACTOR": "alice"}).returncode, 0)
        self.sync()
        self.assertEqual(self.forge_comments(), [])

    def test_a_forge_edit_and_a_forge_delete_of_our_comment_come_back_as_events(self) -> None:
        """Prevents the two sides drifting: an edit or delete made on GitHub becomes a comment_edited / comment_deleted event."""
        kept = self.post("keep me")
        gone = self.post("remove me")
        self.sync()
        comments = self.forge_comments()
        edited = next(c for c in comments if "keep me" in c["body"])
        edited["body"] = edited["body"].replace("keep me", "kept, now edited on GitHub")
        removed = next(c for c in comments if "remove me" in c["body"])
        comments.remove(removed)
        self.sync()
        shown = self.world.run("show", self.item).stdout
        self.assertIn("kept, now edited on GitHub", shown)
        self.assertNotIn("remove me", shown)
        events = self.world.events(self.item)
        self.assertEqual([e.get("provenance") for e in events if e["type"] in ("comment_edited", "comment_deleted")], ["forge", "forge"])
        self.assertEqual(self.world.run("check").returncode, 0)
        self.sync()
        self.assertEqual(len(self.forge_comments()), 1)
        self.assertTrue(kept and gone)

    def test_a_reply_reads_as_a_reply_on_the_forge_and_a_second_machine_adopts_instead_of_duplicating(self) -> None:
        """Prevents lost threading on GitHub and duplicates from another checkout: the marker finds the pushed comment."""
        parent = self.post("question?", actor="alice")
        self.post("answer.", "--reply-to", parent, actor="bob")
        self.sync()
        bodies = [c["body"] for c in self.forge_comments()]
        answer = next(b for b in bodies if "answer." in b)
        self.assertIn("In reply to", answer)
        self.assertIn("alice", answer)
        state = self.world.ledger / ".qew" / "state" / self.slug / f"{self.item}.json"
        state.unlink()  # a fresh checkout knows nothing of what was pushed
        self.sync()
        self.assertEqual(len(self.forge_comments()), 2)


if __name__ == "__main__":
    unittest.main()
