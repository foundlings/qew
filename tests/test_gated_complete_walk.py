"""A gated item cannot be completed, and finished work reopens in bulk.

Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World


class GatedCompleteWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(ledger_vcs="git")
        self.addCleanup(self.world.close)
        self.path = self.world.base / "manifest.jsonl"

    def gated(self, title: str = "Needs a key") -> str:
        item = self.world.create("--kind", "task", "--title", title)
        self.assertEqual(self.world.run("set", item, "--add-label", "gated", "--reason", "waits on the live Stripe key").returncode, 0)
        return item

    def manifest(self, *rows: object) -> str:
        self.path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        return str(self.path)

    def commits(self) -> int:
        return int(subprocess.run(["git", "-C", str(self.world.ledger), "rev-list", "--count", "HEAD"], capture_output=True, text=True).stdout)

    def test_complete_refuses_a_gated_item_until_the_gate_is_lifted_with_a_reason(self) -> None:
        """Prevents finishing work that waits on something outside the repository: the refusal names the label and the way out."""
        item = self.gated()
        self.assertEqual(self.world.run("start", item).returncode, 0)
        out = self.world.run("complete", item, "--no-commit", "no code needed")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("gated", out.stderr)
        self.assertIn("--remove-label gated", out.stderr)
        lifted = self.world.run("set", item, "--remove-label", "gated", "--reason", "the key arrived")
        self.assertEqual(lifted.returncode, 0, lifted.stdout + lifted.stderr)
        self.assertIn("the key arrived", self.world.run("show", item).stdout)
        self.assertEqual(self.world.run("complete", item, "--no-commit", "no code needed").returncode, 0)

    def test_complete_manifest_refuses_the_whole_file_when_one_row_is_gated(self) -> None:
        """Prevents the bulk path bypassing the gate: one gated row refuses every row, naming it, and nothing is completed."""
        free = self.world.create("--kind", "task", "--title", "Free work")
        stuck = self.gated()
        before = self.commits()
        out = self.world.run("complete", "--manifest", self.manifest({"item": free, "absent": "historical", "reason": "old board"},
                                                                       {"item": stuck, "absent": "historical", "reason": "old board"}),
                             "--quote", "Alexander: 'done per the old board'")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn(stuck[:8], out.stderr)
        self.assertIn("gated", out.stderr)
        self.assertEqual(self.commits(), before)

    def test_check_reports_a_completed_gated_item_as_an_error(self) -> None:
        """Prevents history that contradicts the gate going unseen: completed and gated together fails `qew check`."""
        item = self.world.create("--kind", "task", "--title", "Done work")
        self.assertEqual(self.world.run("start", item).returncode, 0)
        self.assertEqual(self.world.run("complete", item, "--no-commit", "no code needed").returncode, 0)
        self.assertEqual(self.world.run("set", item, "--closed", "--add-label", "gated", "--reason", "waits").returncode, 0)
        out = self.world.run("check")
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("gated", out.stdout + out.stderr)


class ReopenManifestWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(ledger_vcs="git")
        self.addCleanup(self.world.close)
        self.items = [self.world.create("--kind", "task", "--title", f"Closed {n}") for n in range(3)]
        for item in self.items:
            self.assertEqual(self.world.run("close", item, "--reason", "not_planned").returncode, 0)
        self.path = self.world.base / "reopen.jsonl"

    def manifest(self, *rows: object) -> str:
        self.path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        return str(self.path)

    def commits(self) -> int:
        return int(subprocess.run(["git", "-C", str(self.world.ledger), "rev-list", "--count", "HEAD"], capture_output=True, text=True).stdout)

    def state(self, item: str) -> str:
        return json.loads(self.world.run("show", item, "--json").stdout)["state"]

    def test_reopen_manifest_reopens_many_in_one_commit_and_a_rerun_changes_nothing(self) -> None:
        """Prevents two hours of single reopens: one manifest reopens every row in one commit, each with its reason."""
        path = self.manifest(*({"item": item, "reason": "gated work was never done"} for item in self.items))
        before = self.commits()
        preview = self.world.run("reopen", "--manifest", path, "--dry-run")
        self.assertEqual(preview.returncode, 0, preview.stdout + preview.stderr)
        self.assertIn("would reopen 3", preview.stdout)
        self.assertEqual(self.commits(), before)
        done = self.world.run("reopen", "--manifest", path)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("reopened 3", done.stdout)
        self.assertEqual(self.commits(), before + 1)
        self.assertTrue(all(self.state(item) == "approved" for item in self.items))
        again = self.world.run("reopen", "--manifest", path)
        self.assertIn("unchanged 3", again.stdout)
        self.assertEqual(self.commits(), before + 1)
        self.assertEqual(self.world.run("check").returncode, 0)

    def test_a_bad_row_refuses_every_row(self) -> None:
        """Prevents a half-applied bulk reopen: a missing reason, an unknown item or a duplicate changes nothing."""
        for rows in ([{"item": self.items[0], "reason": "ok"}, {"item": self.items[1]}],
                     [{"item": self.items[0], "reason": "ok"}, {"item": "nope", "reason": "x"}],
                     [{"item": self.items[0], "reason": "ok"}, {"item": self.items[0], "reason": "again"}]):
            with self.subTest(rows=rows):
                before = self.commits()
                out = self.world.run("reopen", "--manifest", self.manifest(*rows))
                self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
                self.assertEqual(self.commits(), before)
                self.assertEqual(self.state(self.items[0]), "closed")


if __name__ == "__main__":
    unittest.main()
