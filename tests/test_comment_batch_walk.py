"""Bulk comments for importers: a manifest and comments inside create --from-file plans.

Each test is a scenario; its docstring names the failure it prevents. One command is one ledger commit, the original
author and time are kept, a key makes a rerun a no-op, and a refusal anywhere writes nothing anywhere.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World


class CommentBatchWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(ledger_vcs="git")
        self.addCleanup(self.world.close)
        self.items = [self.world.create("--kind", "issue", "--title", f"Item {n}", "--quote", QUOTE) for n in range(3)]
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))

    def manifest(self, rows: list) -> Path:
        path = self.root / "comments.jsonl"
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        return path

    def commented(self, item: str) -> list:
        return [e for e in self.world.events(item) if e["type"] == "commented"]

    def assertRefused(self, result) -> None:
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        for vague in ("unrecognized arguments", "the following arguments are required", "unknown field", "give exactly one"):
            self.assertNotIn(vague, result.stderr)

    def test_a_manifest_adds_threaded_comments_with_their_original_author_and_time_in_one_commit(self) -> None:
        """Prevents a flat, re-authored import: authors and times survive, replies point at their key, one commit, rerun no-op."""
        rows = [
            {"item": self.items[0], "key": "c1", "text": "the question", "author": "dana", "at": "2025-03-01T09:00:00Z"},
            {"item": self.items[0], "key": "c2", "text": "the answer", "author": "erik", "at": "2025-03-01T10:00:00Z", "reply_to": "c1"},
            {"item": self.items[1], "key": "c3", "text": "another item", "author": "dana", "at": "2025-03-02T09:00:00Z"},
        ]
        manifest = self.manifest(rows)
        before = len(self.world.ledger_log())
        result = self.world.run("comment", "--manifest", str(manifest))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.world.ledger_log()) - before, 1)
        shown = self.world.run("show", self.items[0]).stdout
        self.assertIn("@dana 2025-03-01T09:00:00Z", shown)
        self.assertIn("@erik 2025-03-01T10:00:00Z", shown)
        data = json.loads(self.world.run("show", self.items[0], "--json").stdout)
        by_text = {c["text"]: c for c in data["comments"]}
        self.assertEqual(by_text["the answer"]["reply_to"], by_text["the question"]["id"])
        again = self.world.run("comment", "--manifest", str(manifest))
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("unchanged", again.stdout)
        self.assertEqual(len(self.commented(self.items[0])), 2)
        self.assertEqual(len(self.world.ledger_log()) - before, 1)
        self.assertEqual(self.world.run("check").returncode, 0)

    def test_one_bad_row_refuses_the_whole_manifest_and_names_its_line(self) -> None:
        """Prevents a half-applied import: an unknown item, a missing key or a reply to nothing writes nothing at all."""
        good = {"item": self.items[0], "key": "ok", "text": "fine"}
        for bad, needle in (
            ({"item": "no-such-item", "key": "k", "text": "x"}, "line 2"),
            ({"item": self.items[1], "text": "no key"}, "line 2"),
            ({"item": self.items[1], "key": "r", "text": "x", "reply_to": "ghost"}, "line 2"),
            ({"item": self.items[1], "key": "t", "text": "x", "at": "yesterday"}, "line 2"),
        ):
            result = self.world.run("comment", "--manifest", str(self.manifest([good, bad])))
            self.assertRefused(result)
            self.assertIn(needle, result.stderr)
        self.assertEqual(sum(len(self.commented(i)) for i in self.items), 0)

    def test_a_key_reused_with_different_text_is_refused(self) -> None:
        """Prevents silent overwrite on a rerun: the same key with other text is an error, as for a single comment."""
        self.assertEqual(self.world.run("comment", "--manifest", str(self.manifest([{"item": self.items[0], "key": "k", "text": "one"}]))).returncode, 0)
        changed = self.world.run("comment", "--manifest", str(self.manifest([{"item": self.items[0], "key": "k", "text": "two"}])))
        self.assertRefused(changed)
        self.assertEqual(len(self.commented(self.items[0])), 1)

    def test_a_create_plan_can_carry_comments_for_its_new_items(self) -> None:
        """Prevents a second pass for the importer: items and their discussion arrive together, in one commit."""
        plan = self.root / "plan.jsonl"
        plan.write_text(json.dumps({"kind": "issue", "title": "With talk", "comments": [
            {"key": "a", "text": "first", "author": "dana", "at": "2025-01-01T00:00:00Z"},
            {"key": "b", "text": "second", "author": "erik", "at": "2025-01-02T00:00:00Z", "reply_to": "a"},
        ]}) + "\n")
        before = len(self.world.ledger_log())
        result = self.world.run("create", "--from-file", str(plan), "--quote", QUOTE)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.world.ledger_log()) - before, 1)
        created = result.stdout.split()[0]
        self.assertIn("@erik 2025-01-02T00:00:00Z", self.world.run("show", created).stdout)
        self.assertEqual(self.world.run("check").returncode, 0)

    def test_a_plan_with_a_bad_comment_creates_no_item_at_all(self) -> None:
        """Prevents orphan items: a comment replying to nothing refuses the whole plan before any write."""
        plan = self.root / "bad.jsonl"
        plan.write_text(
            json.dumps({"kind": "issue", "title": "Fine"}) + "\n"
            + json.dumps({"kind": "issue", "title": "Broken", "comments": [{"key": "x", "text": "t", "reply_to": "ghost"}]}) + "\n"
        )
        before = len(self.world.run("list").stdout.splitlines())
        result = self.world.run("create", "--from-file", str(plan), "--quote", QUOTE)
        self.assertRefused(result)
        self.assertEqual(len(self.world.run("list").stdout.splitlines()), before)


if __name__ == "__main__":
    unittest.main()
