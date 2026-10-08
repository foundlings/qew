"""Concurrent writers on one ledger checkout, and the [replication] config contract."""

import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import PROJECT_ROOT, World, git


class WriteLockWalk(unittest.TestCase):
    def test_parallel_writers_get_distinct_ids_and_one_commit_each(self) -> None:
        """Prevents interleaved commits, git index.lock failures and id collisions when several agents write at once."""
        w = World(ledger_vcs="git")
        self.addCleanup(w.close)
        environment = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT / "src"), "QEW_ACTOR": "tester",
                       "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e.invalid",
                       "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e.invalid"}
        processes = [
            subprocess.Popen([sys.executable, "-m", "qew.cli", "create", "--title", f"W{n}"], cwd=w.code, env=environment,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for n in range(6)
        ]
        results = [p.communicate() + (p.returncode,) for p in processes]
        self.assertEqual([r[2] for r in results], [0] * 6, [r[1] for r in results])
        ids = {r[0].split()[0] for r in results}
        self.assertEqual(len(ids), 6)
        subjects = w.ledger_log()
        self.assertEqual(sum(s.startswith("qew: create ") for s in subjects), 6)
        self.assertEqual(w.ledger_dirty(), [])


class ReplicationConfigWalk(unittest.TestCase):
    def config(self, text: str):
        w = World(replication=text)
        self.addCleanup(w.close)
        return w.run("list")

    def test_a_valid_table_loads(self) -> None:
        """Prevents a correct [replication] table being rejected."""
        result = self.config("\n[replication]\nauto_push = false\ntimeout_s = 2.5\nbranch = \"trunk\"\n")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unknown_keys_and_wrong_types_fail_loudly(self) -> None:
        """Prevents a typo such as auto_comit silently leaving a safety switch at its default."""
        unknown = self.config("\n[replication]\nauto_comit = false\n")
        self.assertEqual(unknown.returncode, 1)
        self.assertIn("auto_comit", unknown.stderr)
        typed = self.config("\n[replication]\nauto_commit = \"no\"\n")
        self.assertEqual(typed.returncode, 1)
        self.assertIn("auto_commit", typed.stderr)


if __name__ == "__main__":
    unittest.main()
