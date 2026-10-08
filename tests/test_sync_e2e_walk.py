"""`qew sync`, `qew pr` and `qew project ensure` through the CLI, real HTTP, a local Forgejo fake.

Adversary: a down forge blocking offline work, a mirror file recording work the forge never
accepted, a second sync creating duplicates, a pull request merged at a head nobody checked.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_forgejo import TOKEN, FakeForgejo
from tests.ledger_harness import QUOTE, World, git


def mirrors(api: str, repo: str, extra: str = "") -> str:
    return f'\n[[mirror]]\nforge = "forgejo"\nrepo = "{repo}"\napi = "{api}"\n{extra}'


class SyncE2eWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeForgejo().start()
        self.addCleanup(self.fake.stop)
        self.world = World(mirrors(self.fake.api, self.fake.repo_url))
        self.addCleanup(self.world.close)
        self.env = {"FORGEJO_TOKEN": TOKEN}

    def sync(self, *extra: str):
        return self.world.run("sync", *extra, env=self.env)

    def test_sync_creates_once_updates_on_change_and_never_duplicates(self) -> None:
        w = self.world
        item = w.create("--kind", "issue", "--title", "Ship it")
        first = self.sync()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(len(self.fake.issues), 1)
        self.assertEqual(len(list((w.ledger / "mirrors" / item).glob("*.json"))), 1)
        self.assertEqual(self.sync().returncode, 0)
        self.assertEqual(len(self.fake.issues), 1)
        w.run("set", item, "--title", "Ship it now")
        self.sync()
        self.assertEqual(self.fake.issues[1]["title"], "Ship it now")
        self.assertIn("P2", self.fake.issue_labels(1))
        self.assertNotIn("qew:draft", self.fake.issue_labels(1))

    def test_a_down_forge_fails_loudly_leaves_no_mirror_and_never_blocks_offline_work(self) -> None:
        w = self.world
        (w.code / ".qew" / "config.toml").write_text(
            '[ledger]\npath = "../ledger"\n' + mirrors("http://127.0.0.1:9/api/v1", "http://127.0.0.1:9/o/r")
        )
        item = w.create("--kind", "issue", "--title", "Offline", "--quote", QUOTE)
        result = self.sync()
        self.assertEqual(result.returncode, 1)
        self.assertIn("failed", result.stdout)
        self.assertFalse((w.ledger / "mirrors").exists())
        self.assertIn(item, w.run("ready").stdout)
        self.assertEqual(w.run("start", item).returncode, 0)

    def test_a_missing_token_is_an_auth_failure_not_a_crash(self) -> None:
        w = self.world
        w.create("--kind", "issue", "--title", "No token")
        result = w.run("sync", env={"FORGEJO_TOKEN": "", "GITEA_TOKEN": ""})
        self.assertEqual(result.returncode, 1)
        self.assertIn("token", result.stdout + result.stderr)

    def test_project_ensure_says_unsupported_for_a_forge_without_boards(self) -> None:
        result = self.world.run("project", "ensure", env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("boards unsupported", result.stdout)

    def test_pr_open_ready_merge_walk_merges_only_the_checked_head(self) -> None:
        w = self.world
        item = w.create("--kind", "issue", "--title", "Deliver", "--quote", QUOTE)
        w.run("start", item)
        self.assertEqual(self.sync().returncode, 0)
        git(w.code, "checkout", "-q", "-b", "feature")
        sha = w.commit("feature work")
        self.fake.set_head("feature", sha)
        opened = w.run("pr", "open", item, "--head", "feature", env=self.env)
        self.assertEqual(opened.returncode, 0, opened.stderr)
        number = opened.stdout.split("pr #")[1].split()[0]
        merge = w.run("pr", "merge", number, env=self.env)
        self.assertEqual(merge.returncode, 2, "a draft pull request must not merge")
        self.assertEqual(w.run("pr", "ready", number, env=self.env).returncode, 0)
        self.assertEqual(w.run("evidence", item, "--change", sha).returncode, 0)
        merged = w.run("pr", "merge", number, env=self.env)
        self.assertEqual(merged.returncode, 0, merged.stderr)
        self.assertIn("qew complete", merged.stdout)
        self.assertNotIn("closes", self.fake.pulls[int(number)]["title"].lower())


if __name__ == "__main__":
    unittest.main()
