"""Comments are first-class: stable ids, replies, edits and deletes as events, validated by qew check.

Each test is a scenario; its docstring names the failure it prevents. Edits and deletes are events (history kept, the last
event by id wins), so two machines never write the same file.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World

EVERY = "\n[replication]\nfetch_interval_s = 0\nauto_forge_sync = false\nforge_concurrency = 1\n"
OFFLINE = {"QEW_OFFLINE": "1"}


def posted(result) -> str:
    """The comment id `qew comment` prints: `commented on ITEM as ID`."""
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.split()[-1]


class CommentThreadsWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(ledger_vcs="git")
        self.addCleanup(self.world.close)
        self.item = self.world.create("--kind", "issue", "--title", "Discussed", "--quote", QUOTE)

    def comment(self, *arguments: str, env: dict | None = None):
        return self.world.run("comment", self.item, *arguments, env=env)

    def show(self) -> str:
        return self.world.run("show", self.item).stdout

    def test_a_reply_is_threaded_under_its_parent_and_carries_its_id(self) -> None:
        """Prevents a flat pile of comments: the reply names its parent and shows indented beneath it."""
        first = posted(self.comment("--body", "the question"))
        second = posted(self.comment("--body", "the answer", "--reply-to", first))
        self.assertNotEqual(first, second)
        lines = self.show().splitlines()
        question = next(i for i, line in enumerate(lines) if "the question" in line)
        answer = next(i for i, line in enumerate(lines) if "the answer" in line)
        self.assertGreater(answer, question)
        indent = lambda text: len(text) - len(text.lstrip())  # noqa: E731
        self.assertGreater(indent(lines[answer]), indent(lines[question]))
        data = json.loads(self.world.run("show", self.item, "--json").stdout)
        by_id = {c["id"]: c for c in data["comments"]}
        self.assertEqual(by_id[second]["reply_to"], first)

    def test_a_reply_to_nothing_is_refused(self) -> None:
        """Prevents dangling threads: --reply-to must name a comment of this item."""
        refused = self.comment("--body", "orphan", "--reply-to", "nope")
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertNotIn("unrecognized", refused.stderr)

    def test_an_edit_replaces_the_text_and_keeps_the_history(self) -> None:
        """Prevents rewriting history: the shown text is the last edit, marked edited, and the original event stays."""
        first = posted(self.comment("--body", "first draft"))
        edited = self.comment("--edit", first, "--body", "second draft")
        self.assertEqual(edited.returncode, 0, edited.stdout + edited.stderr)
        listing = self.show()
        self.assertIn("second draft", listing)
        self.assertIn("(edited)", listing)
        self.assertNotIn("first draft", listing)
        types = [e["type"] for e in self.world.events(self.item)]
        self.assertEqual((types.count("commented"), types.count("comment_edited")), (1, 1))
        self.assertEqual(self.world.run("check").returncode, 0)

    def test_only_the_author_may_edit_or_delete_unless_forced(self) -> None:
        """Prevents rewriting someone else's words by accident: another actor needs --force, which is recorded."""
        first = posted(self.comment("--body", "alice says", env={"QEW_ACTOR": "alice"}))
        refused = self.comment("--edit", first, "--body", "bob rewrites", env={"QEW_ACTOR": "bob"})
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertNotIn("unrecognized", refused.stderr)
        self.assertIn("alice says", self.show())
        forced = self.comment("--edit", first, "--body", "bob rewrites", "--force", env={"QEW_ACTOR": "bob"})
        self.assertEqual(forced.returncode, 0, forced.stdout + forced.stderr)
        edit = next(e for e in self.world.events(self.item) if e["type"] == "comment_edited")
        self.assertTrue(edit.get("forced"))
        self.assertEqual(self.world.run("check").returncode, 0)

    def test_a_delete_hides_the_text_but_keeps_the_event(self) -> None:
        """Prevents losing history: the listing shows a placeholder, the commented event and the delete are both on disk."""
        first = posted(self.comment("--body", "regrettable words"))
        reply = posted(self.comment("--body", "a reply stays", "--reply-to", first))
        deleted = self.comment("--delete", first)
        self.assertEqual(deleted.returncode, 0, deleted.stdout + deleted.stderr)
        listing = self.show()
        self.assertNotIn("regrettable words", listing)
        self.assertIn("[deleted]", listing)
        self.assertIn("a reply stays", listing)
        self.assertIn(reply[:6], listing)
        self.assertEqual([e["type"] for e in self.world.events(self.item)].count("comment_deleted"), 1)

    def test_check_refuses_dangling_references_and_unauthorised_edits(self) -> None:
        """Prevents a hand-edited ledger passing: an edit or reply naming nothing, or a link to an unattached name, fails."""
        posted(self.comment("--body", "see [plan](attachment:docs/plan.md)"))
        failing = self.world.run("check")
        self.assertEqual(failing.returncode, 1, failing.stdout)
        self.assertIn("docs/plan.md", failing.stdout)
        source = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(source, ignore_errors=True))
        (source / "plan.md").write_bytes(b"plan")
        self.assertEqual(self.world.run("item", "attach", self.item, str(source / "plan.md"), "--as", "docs/plan.md").returncode, 0)
        self.assertEqual(self.world.run("check").returncode, 0, self.world.run("check").stdout)
        directory = self.world.home("events", self.item)
        from qew.ledger_ids import event_id
        newest = sorted(directory.glob("*.json"))[-1].stem
        stray = event_id(newest)
        (directory / f"{stray}.json").write_text(json.dumps({"type": "comment_edited", "at": "2026-01-01T00:00:00Z", "actor": "x", "ref": "nothing", "text": "t"}))
        bad = self.world.run("check")
        self.assertEqual(bad.returncode, 1, bad.stdout)
        self.assertIn("nothing", bad.stdout)
        (directory / f"{stray}.json").unlink()
        reply = event_id(stray)
        (directory / f"{reply}.json").write_text(json.dumps({"type": "commented", "at": "2026-01-01T00:00:00Z", "actor": "x", "text": "r", "reply_to": "ghost"}))
        orphan = self.world.run("check")
        self.assertEqual(orphan.returncode, 1, orphan.stdout)
        self.assertIn("ghost", orphan.stdout)


class CommentMergeWalk(unittest.TestCase):
    def test_two_machines_editing_one_comment_merge_without_a_conflict(self) -> None:
        """Prevents git conflicts on comments: each edit is its own event file; every checkout derives the same winner."""
        a = World(ledger_vcs="git", remote=True, replication=EVERY)
        self.addCleanup(a.close)
        b = a.clone("b", replication=EVERY)
        item = a.create("--kind", "issue", "--title", "Shared", "--quote", QUOTE)
        b.run("list")
        first = posted(a.run("comment", item, "--body", "original"))
        b.run("list")
        self.assertEqual(a.run("comment", item, "--edit", first, "--body", "from a", env=OFFLINE).returncode, 0)
        self.assertEqual(b.run("comment", item, "--edit", first, "--body", "from b", env=OFFLINE).returncode, 0)
        for world in (a, b, a, b):
            settled = world.run("ready")
            self.assertEqual(settled.returncode, 0, settled.stdout + settled.stderr)
        shown = [world.run("show", item).stdout for world in (a, b)]
        self.assertEqual(("from a" in shown[0]), ("from a" in shown[1]))
        self.assertEqual(("from b" in shown[0]), ("from b" in shown[1]))
        for world in (a, b):
            self.assertEqual(world.run("check").returncode, 0)


if __name__ == "__main__":
    unittest.main()
