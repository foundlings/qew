"""The ledger remote: best-effort fetch/merge before a command and push after a write.

Each test is a scenario; its docstring names the failure it prevents.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import World, git, jj_available


def remote_subjects(world: World) -> list:
    return git(world.base, "--git-dir", str(world.remote), "log", "--format=%s", "main").splitlines()


class LedgerRemoteWalk(unittest.TestCase):
    vcs = "git"

    def setUp(self) -> None:
        every_time = "\n[replication]\nfetch_interval_s = 0\n"
        self.a = World(ledger_vcs=self.vcs, remote=True, replication=every_time)
        self.addCleanup(self.a.close)
        self.b = self.a.clone("b", replication=every_time)

    def test_a_write_is_pushed_to_the_remote(self) -> None:
        """Prevents work staying on one machine: the commit reaches the remote with the write."""
        item = self.a.create("--title", "Shared")
        self.assertEqual(remote_subjects(self.a)[0], f"qew: create {item} by tester")

    def test_another_agent_sees_the_work_on_its_next_command(self) -> None:
        """Prevents agent B working from a stale ledger: any command fetches and merges first."""
        item = self.a.create("--title", "From A")
        self.assertIn(item, self.b.run("list").stdout)

    def test_two_agents_writing_independently_converge_without_conflicts(self) -> None:
        """Prevents lost or conflicting work when agents write while apart: one file per item and event merges cleanly."""
        offline = {"QEW_OFFLINE": "1"}
        first = self.a.run("create", "--title", "A offline", env=offline).stdout.split()[0]
        second = self.b.run("create", "--title", "B offline", env=offline).stdout.split()[0]
        self.assertEqual(remote_subjects(self.a)[0], "init ledger")
        for agent in (self.a, self.b, self.a):  # reconnect: A pushes, B merges and pushes, A merges
            self.assertEqual(agent.run("ready").returncode, 0)
        listed_b = self.b.run("list").stdout
        listed_a = self.a.run("list").stdout
        self.assertIn(first, listed_b)
        self.assertIn(second, listed_a)
        self.assertEqual(listed_a, listed_b)
        self.assertEqual(self.a.run("check").returncode, 0)

    def test_an_unreachable_remote_never_fails_a_write_and_the_next_command_catches_up(self) -> None:
        """Prevents a network outage blocking work: the commit stays local and is pushed once the remote returns."""
        hidden = self.a.remote.with_name("hidden.git")
        self.a.remote.rename(hidden)
        item = self.a.run("create", "--title", "While down")
        self.assertEqual(item.returncode, 0, item.stderr)
        hidden.rename(self.a.remote)
        self.a.run("create", "--title", "Back up")
        self.assertEqual([s.split()[1] for s in remote_subjects(self.a)[:2]], ["create", "create"])
        self.assertEqual(len(remote_subjects(self.a)), 3)

    def test_qew_offline_makes_no_remote_traffic_but_still_commits(self) -> None:
        """Prevents QEW_OFFLINE being ignored: the remote stays untouched while the local commit is made."""
        self.a.run("create", "--title", "Quiet", env={"QEW_OFFLINE": "1"})
        self.assertEqual(remote_subjects(self.a), ["init ledger"])
        self.assertEqual(self.a.ledger_log()[0].split()[1], "create")

    def test_a_real_text_conflict_is_reported_loudly_and_never_auto_resolved(self) -> None:
        """Prevents silent corruption: a hand edit of the same item file on both sides stops writes with the files named."""
        item = self.a.create("--title", "Contested")
        self.b.run("list")
        path_a = self.a.ledger / "items" / f"{item}" / "item.md"
        path_b = self.b.ledger / "items" / f"{item}" / "item.md"
        for path, text in ((path_a, "from A"), (path_b, "from B")):
            path.write_text(path.read_text().replace("---\n", f"---\n{text}\n", 1))
        git(self.a.ledger, "commit", "-qam", "hand edit A")
        git(self.a.ledger, "push", "-q", "origin", "main")
        git(self.b.ledger, "commit", "-qam", "hand edit B")
        result = self.b.run("create", "--title", "Blocked by conflict")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("conflict", result.stderr)
        self.assertIn(item, result.stderr)
        self.assertEqual(self.b.ledger_dirty(), [])


class MirrorConflictWalk(unittest.TestCase):
    def test_conflicts_confined_to_mirror_files_resolve_to_the_remote_copy_and_other_conflicts_stay_loud(self) -> None:
        """Prevents one machine's stale sync bookkeeping in an old-format mirror file wedging every writer (seen live)."""
        every = "\n[replication]\nfetch_interval_s = 0\nauto_forge_sync = false\n"
        a = World(ledger_vcs="git", remote=True, replication=every)
        self.addCleanup(a.close)
        b = a.clone("b", replication=every)
        item = a.create("--title", "Mirrored")
        b.run("list")
        for world, stamp in ((a, "A"), (b, "B")):
            directory = world.ledger / "mirrors" / item
            directory.mkdir(parents=True)
            (directory / "t.json").write_text('{"forge": "forgejo", "url": "u", "kind": "issue", "number": 1, "synced_at": "%s"}\n' % stamp)
            git(world.ledger, "add", "-A")
            git(world.ledger, "commit", "-qm", f"old-format mirror from {stamp}")
        git(a.ledger, "push", "-q", "origin", "main")
        result = b.run("create", "--title", "After the clash")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"synced_at": "A"', (b.ledger / "mirrors" / item / "t.json").read_text())
        self.assertEqual(b.ledger_dirty(), [])
        self.assertEqual(b.run("check").returncode, 0)

    def test_a_long_conflict_report_names_a_few_files_and_the_count(self) -> None:
        """Prevents a 60-file conflict flooding the terminal: the message stays short."""
        from qew.ledger_repo import conflict_message

        text = conflict_message("main@origin", [f"items/{n}/item.md" for n in range(60)], "/ledger")
        self.assertLess(len(text), 600)
        self.assertIn("60 files", text)


@unittest.skipUnless(jj_available(), "jj is not installed")
class LedgerRemoteJujutsuWalk(LedgerRemoteWalk):
    vcs = "jj"

    def test_a_real_text_conflict_is_reported_loudly_and_never_auto_resolved(self) -> None:
        self.skipTest("conflict construction uses git commits; covered by the git walk")


if __name__ == "__main__":
    unittest.main()
