"""A crash between creating a forge issue and recording its mirror never leaves a twin, however many issues came after.

Adversary: the scoped item sync looks for an existing issue only among the newest 100, so an orphan pushed out of that
window is invisible and the next sync creates a second issue; pull then closes duplicates only above the mirrored one.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, UTC

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import ledger_config
from qew.forge import IssueSpec, marker_for
from qew.forge_pull import pull_target
from qew.ledger_store import Ledger
from qew.sync import Target, sync_target
from tests.fake_forgejo import TOKEN as FORGEJO_TOKEN, FakeForgejo
from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World
from tests.memory_forge import MemoryForge

ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}
QUIET = "\n[replication]\nauto_forge_sync = false\nauto_push = false\n"


def open_issues(fake: FakeGitHub, marker: str) -> list:
    return sorted(n for n, i in fake.issues.items() if marker in (i["body"] or "") and i["state"] == "open")


class CrashOrphanWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub().start()
        self.addCleanup(self.fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'
        self.w = World(mirrors, replication=QUIET)
        self.addCleanup(self.w.close)

    def crash_after_create(self) -> str:
        item = self.w.create("--title", "Crashes while syncing", "--quote", QUOTE)
        self.fake.fail_next(500, "/issues", after=True)
        self.w.run("sync", "--item", item, env=ENV)
        self.assertEqual(open_issues(self.fake, marker_for(item)), [1], "the create was applied and its response lost")
        return item

    def test_an_orphan_older_than_the_newest_hundred_is_adopted_not_duplicated(self) -> None:
        """Prevents a twin after a crash: the sync that follows a lost create finds the orphan whatever came after it."""
        item = self.crash_after_create()
        for n in range(150):
            self.fake.new_issue({"title": f"newer {n}", "body": "x"})
        result = self.w.run("sync", "--item", item, env=ENV)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(open_issues(self.fake, marker_for(item)), [1])
        self.assertEqual(self.w.run("show", item).stdout.count("#1"), 1)

    def test_a_clean_scoped_create_still_skips_the_lookup(self) -> None:
        """Prevents the fix costing every create a listing: with no intent left behind the scoped path stays create-first."""
        item = self.w.create("--title", "Plain", "--quote", QUOTE)
        before = len(self.fake.requests)
        self.assertEqual(self.w.run("sync", "--item", item, env=ENV).returncode, 0)
        made = self.fake.requests[before:]
        first_post = next(i for i, (m, p, _) in enumerate(made) if m == "POST" and p.endswith("/issues"))
        self.assertFalse([1 for m, p, _ in made[:first_post] if m == "GET" and "/issues?" in p])
        self.assertEqual(list((self.w.ledger / ".qew" / "state").rglob("creating.json")), [], "the intent is cleared once the mirror is written")


class MirrorWinsWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World('\n[[mirror]]\nforge = "forgejo"\nrepo = "https://git.example/o/r"\n', replication=QUIET)
        self.addCleanup(self.world.close)
        self.forge = MemoryForge("git.example")
        self.config = ledger_config.load(self.world.code)
        self.ledger = Ledger(self.world.ledger, "TST")
        mirror = self.config.mirrors[0]
        self.target = Target(mirror, ledger_config.mirror_slug(mirror), self.forge)

    def item(self) -> str:
        item = self.world.create("--title", "Original", "--quote", QUOTE)
        sync_target(self.ledger, self.target, self.config)
        return item

    def twin(self, item: str) -> int:
        return self.forge.create_issue(IssueSpec("Original", marker_for(item) + "\n\nbody", (), True)).number

    def later(self, seconds: int):
        return lambda: datetime.now(UTC) + timedelta(seconds=seconds)

    def test_a_lower_numbered_twin_of_a_live_item_is_closed_once_the_mirror_is_settled(self) -> None:
        """Prevents an orphan below the mirrored issue staying open for ever: the mirror decides, after a grace for merges."""
        item = self.item()
        orphan = 1
        mirrored = self.twin(item)  # number 2: a later create the ledger recorded after the crash
        record = self.ledger.load_mirrors(item)[0]
        slug = record.pop("_target")
        self.ledger.write_mirror(item, slug, {**record, "number": mirrored, "issue_url": self.forge.issues[mirrored].url})
        self.assertEqual(pull_target(self.ledger, self.target).duplicates, 0, "a fresh mirror may still be racing a merge")
        self.assertTrue(self.forge.issues[orphan].open)
        report = pull_target(self.ledger, self.target, clock=self.later(3600))
        self.assertEqual(report.duplicates, 1)
        self.assertFalse(self.forge.issues[orphan].open)
        self.assertNotIn(marker_for(item), self.forge.issues[orphan].body)
        self.assertTrue(self.forge.issues[mirrored].open)

    def test_an_item_without_a_mirror_adopts_its_lowest_marker_issue_and_closes_the_rest(self) -> None:
        """Prevents a create over an orphan: with no mirror, pull records the lowest marker issue and closes the others."""
        item = self.world.create("--title", "Original", "--quote", QUOTE)
        first, second = self.twin(item), self.twin(item)
        report = pull_target(self.ledger, self.target)
        self.assertEqual(self.ledger.load_mirrors(item)[0]["number"], first)
        self.assertEqual(report.duplicates, 1)
        self.assertFalse(self.forge.issues[second].open)
        self.assertTrue(self.forge.issues[first].open)
        sync_target(self.ledger, self.target, self.config)
        self.assertEqual(len(self.forge.issues), 2, "sync updates the adopted issue and creates nothing")


class OrphanOnOtherForgesWalk(unittest.TestCase):
    def test_forgejo_scoped_sync_adopts_an_old_orphan_without_a_twin(self) -> None:
        """Prevents the same twin on Forgejo: its marker search is server-side, so the orphan wins whatever its age."""
        fake = FakeForgejo().start()
        self.addCleanup(fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "forgejo"\nrepo = "{fake.repo_url}"\napi = "{fake.api}"\n'
        w = World(mirrors, replication=QUIET)
        self.addCleanup(w.close)
        env = {"FORGEJO_TOKEN": FORGEJO_TOKEN}
        item = w.create("--title", "Orphaned", "--quote", QUOTE)
        self.assertEqual(w.run("sync", "--item", item, env=env).returncode, 0)
        state = next((w.ledger / ".qew" / "state").rglob(f"{item}.json"))
        mirror = next((w.ledger / "mirrors" / item).glob("*.json"))
        mirror.unlink()
        state.unlink()
        self.assertEqual(w.run("sync", "--item", item, env=env).returncode, 0)
        self.assertEqual(len([i for i in fake.issues.values() if marker_for(item) in (i.get("body") or "")]), 1)


if __name__ == "__main__":
    unittest.main()
