"""A batch must leave the primary forge exactly as it found it unless it commits: a dry run, or a file that fails, posts no claim that stays.

Adversary: a rolled-back `start` row whose claim comment stays on the primary, so the item is ready in the ledger and unclaimable.
Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_forgejo import TOKEN, FakeForgejo
from tests.ledger_harness import QUOTE, World

ENV = {"FORGEJO_TOKEN": TOKEN}
QUIET = "\n[replication]\nauto_forge_sync = false\nauto_push = false\n"


class BatchPrimaryWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeForgejo().start()
        self.addCleanup(self.fake.stop)
        self.slug = f"forgejo-{self.fake.repo_url.split('//')[1].replace('/', '-')}"
        mirrors = f'\n[[mirror]]\nforge = "forgejo"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'
        self.a = World(mirrors, replication=QUIET + f'\n[authority]\nprimary = "{self.slug}"\n')
        self.addCleanup(self.a.close)
        self.one = self.a.create("--title", "One", "--quote", QUOTE)
        self.two = self.a.create("--title", "Two", "--quote", QUOTE)
        self.a.run("sync", "--all", env=ENV)  # the fake answers 409 for a label the first issue just made; the second pass files the rest
        synced = self.a.run("sync", "--all", env=ENV)
        self.assertEqual(synced.returncode, 0, synced.stdout + synced.stderr)
        self.b = self.second_checkout()
        self.manifest = self.a.base / "rows.jsonl"

    def second_checkout(self) -> World:
        other = World.__new__(World)
        other._owner = False
        other._temporary = self.a._temporary
        other.base = self.a.base
        other.ledger_vcs = None
        other.code = self.a.base / "code-b"
        other.ledger = self.a.base / "ledger-b"
        shutil.copytree(self.a.code, other.code)
        shutil.copytree(self.a.ledger, other.ledger)
        config = other.code / ".qew" / "config.toml"
        config.write_text(config.read_text().replace('"../ledger"', '"../ledger-b"'))
        return other

    def rows(self, *rows: object) -> None:
        self.manifest.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    def other_can_start(self, item: str) -> bool:
        return self.b.run("start", item, env={**ENV, "QEW_ACTOR": "second"}).returncode == 0

    def test_a_dry_run_posts_no_claim_on_the_primary(self) -> None:
        """Prevents the stuck items: a dry run of start rows left claims on the forge that nothing in the ledger holds."""
        self.rows({"op": "start", "item": self.one}, {"op": "start", "item": self.two})
        dry = self.a.run("batch", "--manifest", str(self.manifest), "--dry-run", env=ENV)
        self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
        self.assertIn("would apply 2 rows", dry.stdout)
        self.assertTrue(self.other_can_start(self.one))

    def test_a_dry_run_still_refuses_an_item_someone_else_holds(self) -> None:
        """Prevents a dry run promising a claim the real run loses: it reads the primary, it only never writes it."""
        self.assertTrue(self.other_can_start(self.one))
        self.rows({"op": "start", "item": self.one})
        dry = self.a.run("batch", "--manifest", str(self.manifest), "--dry-run", env=ENV)
        self.assertEqual(dry.returncode, 2, dry.stdout)
        self.assertIn("already claimed by second on the primary", dry.stderr)

    def test_a_failing_file_withdraws_the_claims_its_earlier_rows_took(self) -> None:
        """Prevents a rolled-back file leaving claims behind: row 1 claims on the forge, row 2 fails, the forge is free again."""
        self.rows({"op": "start", "item": self.one}, {"op": "complete", "item": self.two, "reason": "never claimed"})
        failed = self.a.run("batch", "--manifest", str(self.manifest), env=ENV)
        self.assertEqual(failed.returncode, 2, failed.stdout)
        self.assertIn("line 2", failed.stderr)
        self.assertTrue(self.other_can_start(self.one))

    def test_release_force_clears_a_primary_claim_the_ledger_does_not_hold(self) -> None:
        """Prevents an item stuck ready in the ledger and unclaimable: --force clears the forge's claim, plain release still refuses."""
        self.assertEqual(self.a.run("start", self.one, env=ENV).returncode, 0)
        plain = self.b.run("release", self.one, "--reason", "stale", env=ENV)
        self.assertEqual(plain.returncode, 2)
        self.assertIn("not claimed", plain.stderr)
        self.assertFalse(self.other_can_start(self.one))
        cleared = self.b.run("release", self.one, "--reason", "stale claim from a dry run", "--force", env=ENV)
        self.assertEqual(cleared.returncode, 0, cleared.stdout + cleared.stderr)
        self.assertIn("primary", cleared.stdout)
        self.assertTrue(self.other_can_start(self.one))
        nothing = self.b.run("release", self.two, "--reason", "nothing there", "--force", env=ENV)
        self.assertEqual(nothing.returncode, 2)


if __name__ == "__main__":
    unittest.main()
