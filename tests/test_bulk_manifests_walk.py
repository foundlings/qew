"""`qew set --manifest` and `qew evidence --manifest`: many items changed in one commit, every row checked first.

Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World


class ManifestWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(ledger_vcs="git")
        self.addCleanup(self.world.close)
        self.items = [self.world.create("--kind", "task", "--title", f"Item {n}") for n in range(3)]
        self.path = self.world.base / "manifest.jsonl"

    def manifest(self, *rows: object) -> None:
        self.path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    def show(self, item: str) -> dict:
        return json.loads(self.world.run("show", item, "--json").stdout)

    def commits(self) -> int:
        return int(subprocess.run(["git", "-C", str(self.world.ledger), "rev-list", "--count", "HEAD"], capture_output=True, text=True).stdout)

    def events(self, item: str, kind: str) -> list:
        return [e for e in self.world.events(item) if e["type"] == kind]

    def test_set_manifest_changes_labels_priority_and_gates_in_one_commit(self) -> None:
        """Prevents thousands of single `qew set` commits: labels, priority and a gate with its reason land in one commit."""
        one, two, three = self.items
        self.manifest({"item": one, "add_label": "area:billing", "priority": 1},
                      {"item": two, "add_label": ["gated", "area:ops"], "reason": "needs the live Stripe key"},
                      {"item": three, "add_label": "area:billing", "remove_label": "area:old"})
        before = self.commits()
        result = self.world.run("set", "--manifest", str(self.path))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("changed 3", result.stdout)
        self.assertEqual(self.commits(), before + 1)
        self.assertIn("area:billing", self.show(one)["labels"])
        self.assertEqual(self.show(one)["priority"], 1)
        self.assertIn("gated", self.show(two)["labels"])
        self.assertIn("gated: needs the live Stripe key", self.world.run("show", two).stdout)
        self.assertNotIn(two, self.world.run("ready").stdout)
        self.assertEqual(self.world.run("check").returncode, 0)

    def test_set_manifest_dry_run_writes_nothing_and_a_rerun_is_unchanged(self) -> None:
        """Prevents surprises and duplicates: --dry-run changes nothing, and a second run adds no event or commit."""
        one = self.items[0]
        self.manifest({"item": one, "add_label": "area:billing"})
        before = self.commits()
        preview = self.world.run("set", "--manifest", str(self.path), "--dry-run")
        self.assertEqual(preview.returncode, 0, preview.stdout + preview.stderr)
        self.assertIn("would change 1", preview.stdout)
        self.assertEqual((self.commits(), self.show(one)["labels"]), (before, []))
        self.assertEqual(self.world.run("set", "--manifest", str(self.path)).returncode, 0)
        done = self.commits()
        again = self.world.run("set", "--manifest", str(self.path))
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("unchanged 1", again.stdout)
        self.assertEqual(self.commits(), done)

    def test_one_bad_set_row_refuses_the_whole_manifest(self) -> None:
        """Prevents a half-applied bulk edit: unknown item, duplicate, unknown field, gate without reason, finished item."""
        one, two, _ = self.items
        finished = self.world.create("--kind", "task", "--title", "Done work")
        self.assertEqual(self.world.run("close", finished, "--reason", "not_planned").returncode, 0)
        before = self.commits()
        for rows, needle in (([{"item": one, "add_label": "a:b"}, {"item": "nope", "add_label": "a:b"}], "line 2"),
                             ([{"item": one, "add_label": "a:b"}, {"item": one, "priority": 2}], "line 2"),
                             ([{"item": one, "title": "x"}], "title"),
                             ([{"item": one, "add_label": "a:b"}, {"item": two, "add_label": "gated"}], "reason"),
                             ([{"item": finished, "add_label": "a:b"}], finished[:8]),
                             ([{"item": one, "priority": 10}], "priority")):
            with self.subTest(needle=needle):
                self.manifest(*rows)
                refused = self.world.run("set", "--manifest", str(self.path))
                self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
                self.assertIn(needle, refused.stderr)
                self.assertEqual((self.commits(), self.show(one)["labels"]), (before, []))
        self.assertEqual(self.world.run("set", self.items[0], "--manifest", str(self.path)).returncode, 2)

    def test_evidence_manifest_records_change_ids_in_one_commit_and_a_rerun_is_unchanged(self) -> None:
        """Prevents one `qew evidence` call per item: rows carry change ids, one commit, a rerun adds nothing."""
        one, two, _ = self.items
        a, b = self.world.commit(), self.world.commit()
        self.manifest({"item": one, "change_ids": [a]}, {"item": two, "change_ids": [a, b]})
        before = self.commits()
        result = self.world.run("evidence", "--manifest", str(self.path))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("recorded 2", result.stdout)
        self.assertEqual(self.commits(), before + 1)
        self.assertEqual([e["change_ids"] for e in self.events(two, "evidence")], [[a, b]])
        done = self.commits()
        again = self.world.run("evidence", "--manifest", str(self.path))
        self.assertIn("unchanged 2", again.stdout)
        self.assertEqual(self.commits(), done)
        self.assertEqual(self.world.run("check").returncode, 0)

    def test_evidence_manifest_refuses_bad_rows_whole(self) -> None:
        """Prevents partial evidence: a bad change id, a foreign claim, a draft or a finished item refuses every row."""
        one, two, three = self.items
        draft = self.world.create("--kind", "task", "--title", "Idea", "--draft")
        self.assertEqual(self.world.run("start", two, env={"QEW_ACTOR": "someone-else"}).returncode, 0)
        good = self.world.commit()
        before = self.commits()
        for rows, code, needle in (([{"item": one, "change_ids": [good]}, {"item": three, "change_ids": ["not a change!"]}], 2, "line 2"),
                                   ([{"item": two, "change_ids": [good]}], 3, two[:8]),
                                   ([{"item": draft, "change_ids": [good]}], 2, draft[:8]),
                                   ([{"item": one}], 2, "change_ids"),
                                   ([{"item": one, "change_ids": [good], "extra": 1}], 2, "extra")):
            with self.subTest(needle=needle):
                self.manifest(*rows)
                refused = self.world.run("evidence", "--manifest", str(self.path))
                self.assertEqual(refused.returncode, code, refused.stdout + refused.stderr)
                self.assertIn(needle, refused.stderr)
                self.assertEqual(self.commits(), before)

    def test_evidence_manifest_recovers_ids_for_historical_completions_only(self) -> None:
        """Prevents lost proof staying lost: a historical completion gets real ids in its header; stated absences are refused."""
        one, two, three = self.items
        for item, kind in ((one, "historical"), (two, "no-code")):
            self.assertEqual(self.world.run("complete", "--manifest", self.write_complete(item, kind), "--quote", "Alexander: 'done per the old board'").returncode, 0)
        sha = self.world.commit("the real work")
        self.manifest({"item": one, "change_ids": [sha]})
        result = self.world.run("evidence", "--manifest", str(self.path))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        text = (self.world.ledger / "items" / one / "item.md").read_text()
        self.assertIn(f"evidence: {sha}", text)
        self.assertNotIn("evidence_absent", text)
        self.assertIn("unchanged 1", self.world.run("evidence", "--manifest", str(self.path)).stdout)
        three_done = self.world.run("complete", "--manifest", self.write_complete(three, "historical"), "--quote", "Alexander: 'done per the old board'")
        self.assertEqual(three_done.returncode, 0, three_done.stdout + three_done.stderr)
        self.manifest({"item": three, "change_ids": ["0123456789abcdef0123456789abcdef01234567"]})
        unknown = self.world.run("evidence", "--manifest", str(self.path))
        self.assertEqual(unknown.returncode, 2, unknown.stdout + unknown.stderr)
        self.assertIn("does not resolve", unknown.stderr)
        self.assertIn("historical", (self.world.ledger / "items" / three / "item.md").read_text())
        self.manifest({"item": two, "change_ids": [sha]})
        refused = self.world.run("evidence", "--manifest", str(self.path))
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn("no-code", refused.stderr)
        self.assertEqual(self.world.run("check").returncode, 0)

    def write_complete(self, item: str, kind: str) -> str:
        path = self.world.base / f"complete-{kind}.jsonl"
        path.write_text(json.dumps({"item": item, "absent": kind, "reason": "recorded earlier"}) + "\n", encoding="utf-8")
        return str(path)


if __name__ == "__main__":
    unittest.main()
