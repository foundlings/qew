"""Releasing or starting an epic with many claimed descendants costs one read of the ledger, not one per descendant, and a
read never waits for a writer's lock.

Each test is a scenario; its docstring names the failure it prevents.
"""

import contextlib
import io
import os
import sys
import threading
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import cli, cmd_claim, filelock, ledger_config
from qew.ledger_store import Ledger
from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

OFF = "\n[replication]\nauto_forge_sync = false\nauto_push = false\nforge_concurrency = 1\n"
ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1", "QEW_ACTOR": "tester", "QEW_OFFLINE": "", "QEW_NO_FETCH": "1"}
TASKS = 6


class ReleaseCostWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub().start()
        self.addCleanup(self.fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\nselect = "kind:epic,issue"\n'
        self.world = World(mirrors, replication=OFF)
        self.addCleanup(self.world.close)
        slug = ledger_config.mirror_slug(ledger_config.load(self.world.code).mirrors[0])
        config = self.world.code / ".qew" / "config.toml"
        config.write_text(config.read_text() + f'\n[authority]\nprimary = "{slug}"\n', encoding="utf-8")
        self.epic = self.world.create("--kind", "epic", "--title", "Phase", "--quote", QUOTE)
        self.tasks = [self.world.create("--kind", "task", "--title", f"Task {n}", "--parent", self.epic, "--quote", QUOTE) for n in range(TASKS)]
        self.assertEqual(self.world.run("sync", "--all", env={k: v for k, v in ENV.items() if k.startswith("GITHUB") or k == "QEW_NO_GH_TOKEN"}).returncode, 0)

    def counted(self, *argv: str) -> tuple[int, int]:
        """(exit code, Ledger.snapshot calls) of one in-process qew run."""
        calls = [0]
        original = Ledger.snapshot

        def counting(ledger: Ledger):
            calls[0] += 1
            return original(ledger)

        previous = os.getcwd()
        os.chdir(self.world.code)
        try:
            with mock.patch.dict(os.environ, ENV), mock.patch.object(Ledger, "snapshot", counting), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = cli.main(list(argv))
        finally:
            os.chdir(previous)
        return code, calls[0]

    def test_starting_and_releasing_an_epic_read_the_ledger_a_constant_number_of_times(self) -> None:
        """Prevents `qew release ADF` holding the lock for minutes: a read of the whole ledger per claimed descendant."""
        started, reads_start = self.counted("start", self.epic)
        self.assertEqual(started, 0)
        released, reads_release = self.counted("release", self.epic, "--reason", "handing back")
        self.assertEqual(released, 0)
        markers = [c["body"] for issue in self.fake.issues.values() for c in issue["discussion"]]
        self.assertEqual(sum(1 for body in markers if "qew:release" in body), TASKS + 1, markers)
        self.assertLessEqual(reads_release, 4, f"release read the ledger {reads_release} times for {TASKS} tasks")
        self.assertLessEqual(reads_start, 4, f"start read the ledger {reads_start} times for {TASKS} tasks")


class DescendantsWalk(unittest.TestCase):
    def test_walking_a_wide_subtree_is_linear(self) -> None:
        """Prevents a quadratic walk: 20,000 descendants of one epic are found in a moment, each once, breadth first."""
        count = 20_000
        items = {"ROOT": types.SimpleNamespace(id="ROOT", parent="")}
        items.update({f"C{n}": types.SimpleNamespace(id=f"C{n}", parent="ROOT") for n in range(count)})
        items.update({f"G{n}": types.SimpleNamespace(id=f"G{n}", parent=f"C{n}") for n in range(count)})
        begun = time.monotonic()
        found = cmd_claim._descendants(types.SimpleNamespace(items=items), "ROOT")  # type: ignore[arg-type]
        self.assertEqual(len(found), 2 * count)
        self.assertEqual(len(set(found)), 2 * count)
        self.assertEqual(found[:2], ["C0", "C1"])
        self.assertLess(time.monotonic() - begun, 5.0)


class ReadsDoNotWaitWalk(unittest.TestCase):
    def test_a_read_goes_on_while_a_writer_holds_the_ledger_lock(self) -> None:
        """Prevents every agent's read stalling behind one long write: the read skips its refresh instead of waiting."""
        world = World(ledger_vcs="git", remote=True, replication="\n[replication]\nfetch_interval_s = 0\nauto_forge_sync = false\n")
        self.addCleanup(world.close)
        world.create("--title", "Item", "--quote", QUOTE)
        held = threading.Event()
        done = threading.Event()

        def writer() -> None:
            with filelock.hold(world.ledger / ".qew" / "lock", verb="release", wait_s=5):
                held.set()
                done.wait(30)

        thread = threading.Thread(target=writer)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(done.set)
        self.assertTrue(held.wait(5))
        begun = time.monotonic()
        result = world.run("list", timeout=25)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertLess(time.monotonic() - begun, 15.0)


if __name__ == "__main__":
    unittest.main()
