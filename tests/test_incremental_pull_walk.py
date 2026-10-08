"""Incremental pull: the forge is asked only for what changed since the last pull, and deletions are still noticed.

Each test is a scenario; its docstring names the failure it prevents. Two listing paths are walked: the
`since` fallback against the in-memory forge, and conditional ETag pages against the GitHub fake.
"""

from datetime import datetime, timedelta, timezone
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import instants, ledger_config, pull_cursor
from qew.cmd_status import collect, render
from qew.forge import ForgeUnavailable
from qew.forge_github import GitHub
from qew.forge_http import urllib_transport
from qew.forge_pull import pull_target
from qew.ledger_config import Mirror
from qew.ledger_store import Ledger
from qew.sync import Target, sync_target
from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World
from tests.memory_forge import MemoryForge

OFF = "\n[replication]\nauto_forge_sync = false\nauto_push = false\n"
START = datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc)


class Clock:
    """One settable instant, shared by the pull and the forge unless a test skews the forge."""

    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> None:
        self.now += timedelta(**delta)


class Rig(unittest.TestCase):
    forge_text = '\n[[mirror]]\nforge = "forgejo"\nrepo = "https://git.example/o/r"\n'

    def start(self, forge, mirrors: str = "") -> None:
        self.world = World(mirrors or self.forge_text, replication=OFF)
        self.addCleanup(self.world.close)
        self.clock = Clock()
        self.forge = forge
        self.config = ledger_config.load(self.world.code)
        self.ledger = Ledger(self.world.ledger, "TST")
        mirror = self.config.mirrors[0]
        self.target = Target(mirror, ledger_config.mirror_slug(mirror), forge)
        self.cursor_path = self.world.ledger / ".qew" / "state" / self.target.slug / "cursor.json"

    def add_item(self, title: str) -> str:
        item = self.world.create("--kind", "issue", "--title", title, "--quote", QUOTE)
        sync_target(self.ledger, self.target, self.config)
        return item

    def pull(self, full: bool = False):
        return pull_target(self.ledger, self.target, full=full, clock=self.clock)

    def exists_divergences(self, item: str) -> int:
        return sum(1 for e in self.world.events(item) if e["type"] == "forge_divergence" and e["field"] == "exists")


class SincePullWalk(Rig):
    def setUp(self) -> None:
        memory = MemoryForge("git.example")
        self.start(memory)
        memory.clock = self.clock
        self.item = self.add_item("Original")
        self.clock.advance(minutes=10)
        self.assertEqual(self.pull().mode, "full")

    def listed_last(self):
        return self.forge.listed[-1]

    def test_a_second_pull_with_no_forge_change_makes_one_request_and_imports_nothing(self) -> None:
        """Prevents every sync re-listing and re-merging the whole forge when nothing changed there."""
        before = self.forge.list_requests
        self.clock.advance(minutes=10)
        report = self.pull()
        self.assertEqual((self.forge.list_requests - before, report.mode), (1, "incremental"))
        self.assertEqual((report.applied, report.new_items, report.comments, report.divergences, report.failures), (0, 0, 0, 0, []))
        self.assertEqual(self.listed_last(), [])

    def test_an_edit_after_the_cursor_arrives_once_and_one_before_it_does_not_repeat(self) -> None:
        """Prevents a human edit being missed, or being listed again by every later pull."""
        self.clock.advance(minutes=10)
        self.forge.edit(1, title="Edited by a human")
        self.clock.advance(minutes=10)
        self.assertEqual(self.pull().applied, 1)
        self.assertEqual(self.listed_last(), [1])
        self.clock.advance(minutes=10)
        report = self.pull()
        self.assertEqual((report.applied, self.listed_last()), (0, []))

    def test_a_forge_outage_mid_pull_leaves_the_cursor_so_the_next_pull_rereads_the_window(self) -> None:
        """Prevents a failed pull advancing the cursor and the edits it never merged being lost for good."""
        self.clock.advance(minutes=10)
        self.forge.comment_as_human(1, "please look")
        self.forge.edit(1, comments=1)
        kept = self.cursor_path.read_text()
        self.clock.advance(minutes=10)
        self.forge.raise_on["comments"] = ForgeUnavailable("down")
        failed = self.pull()
        self.assertTrue(failed.failures)
        self.assertEqual(self.cursor_path.read_text(), kept)
        del self.forge.raise_on["comments"]
        self.clock.advance(minutes=1)
        report = self.pull()
        self.assertEqual((report.comments, report.failures), (1, []))
        self.assertNotEqual(self.cursor_path.read_text(), kept)

    def test_a_deletion_is_noticed_only_by_a_full_listing_forced_or_daily(self) -> None:
        """Prevents an incremental pull reading an unchanged issue's absence as a deletion, or deletions never being noticed."""
        second = self.add_item("Second")
        self.clock.advance(minutes=10)
        self.pull()
        self.forge.delete_issue(1)
        self.clock.advance(minutes=10)
        self.assertEqual((self.pull().divergences, self.exists_divergences(self.item)), (0, 0))
        self.assertEqual((self.pull(full=True).divergences, self.exists_divergences(self.item)), (1, 1))
        self.forge.delete_issue(2)
        self.clock.advance(hours=25)
        report = self.pull()
        self.assertEqual((report.mode, self.exists_divergences(second)), ("full", 1))

    def test_a_forge_clock_running_behind_never_hides_an_edit(self) -> None:
        """Prevents clock skew between this machine and the forge from dropping an edit stamped before the cursor."""
        self.clock.advance(minutes=10)
        started = self.clock.now
        self.pull()
        self.forge.clock = lambda: started - timedelta(seconds=90)  # the forge is 90s behind and stamps the edit early
        self.forge.edit(1, title="Edited during the skew")
        self.clock.advance(minutes=1)
        self.assertEqual(self.pull().applied, 1)

    def test_status_shows_the_last_pull_and_whether_the_next_pull_is_full(self) -> None:
        """Prevents an operator guessing whether the next sync will list everything."""
        data = collect(self.config)["targets"][0]
        self.assertEqual(data["last_pull"], instants.format_instant(self.cursor_stamp("pulled_at")))
        self.assertEqual(data["next_pull"], "full" if pull_cursor.full_due(pull_cursor.parse(json.loads(self.cursor_path.read_text())), instants.now()) else "incremental")
        self.assertIn("pulled=2026-10-01T10:10:00Z", render(collect(self.config)))
        self.cursor_path.unlink()
        self.assertEqual(collect(self.config)["targets"][0]["next_pull"], "full")

    def cursor_stamp(self, key: str) -> datetime:
        return instants.parse_instant(json.loads(self.cursor_path.read_text())[key])


class ConditionalPullWalk(Rig):
    def setUp(self) -> None:
        self.github = FakeGitHub(page_size=2).start()
        self.addCleanup(self.github.stop)
        backend = GitHub(Mirror("github", self.github.repo_url, None, "", {"api": self.github.api}), urllib_transport(),
                         {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"})
        self.start(backend, f'\n[[mirror]]\nforge = "github"\nrepo = "{self.github.repo_url}"\napi = "{self.github.api}"\n')
        self.items = [self.add_item(f"Item {n}") for n in range(1, 6)]  # numbers 1-5: three pages of two
        self.assertEqual(self.pull().mode, "conditional")

    def list_requests(self):
        return [r for r in self.github.requests if r[0] == "GET" and "/issues?" in r[1]]

    def test_an_unchanged_forge_costs_one_304_per_page_and_imports_nothing(self) -> None:
        """Prevents a sync re-downloading every issue page when no page changed."""
        report = self.pull()
        self.assertEqual((report.pages_fetched, report.pages_reused, report.mode), (0, 3, "conditional"))
        self.assertEqual((report.applied, report.new_items, report.comments, report.divergences), (0, 0, 0, 0))

    def test_an_edit_on_page_two_refetches_only_page_two(self) -> None:
        """Prevents a single edit costing a full download, or an edit hidden behind a stale cached page."""
        self.github.issues[3]["title"] = "Retitled by a human"
        self.github.touch(self.github.issues[3])
        report = self.pull()
        self.assertEqual((report.pages_fetched, report.pages_reused, report.applied), (1, 2, 1))

    def test_deleting_an_issue_on_page_one_is_noticed_and_recorded_once(self) -> None:
        """Prevents a deletion going unnoticed until a full listing: later pages shift, are refetched, and the issue is gone."""
        self.github.delete_issue(1)
        report = self.pull()
        self.assertEqual((report.divergences, self.exists_divergences(self.items[0])), (1, 1))
        self.assertEqual((report.pages_fetched, report.pages_reused), (2, 0))
        self.assertEqual(self.pull().divergences, 0)

    def test_a_failed_page_request_keeps_the_cache_and_the_cursor(self) -> None:
        """Prevents a half-fetched listing replacing the cache or advancing the cursor."""
        pages = self.world.ledger / ".qew" / "state" / self.target.slug / "pages"
        cache = {p.name: p.read_text() for p in pages.glob("*.json")}
        cursor = self.cursor_path.read_text()
        self.github.issues[1]["title"] = "Changed"
        self.github.touch(self.github.issues[1])
        self.github.fail_next(503, "/issues?", skip=1)
        self.clock.advance(minutes=5)
        self.assertTrue(self.pull().failures)
        self.assertEqual({p.name: p.read_text() for p in pages.glob("*.json")}, cache)
        self.assertEqual(self.cursor_path.read_text(), cursor)
        self.assertEqual(self.pull().applied, 1)

    def test_a_forge_without_etags_falls_back_to_a_since_listing(self) -> None:
        """Prevents a forge that sends no ETag being pulled in full on every sync."""
        self.github.etags = False
        self.clock.advance(minutes=10)
        full = self.pull()
        self.assertEqual(full.mode, "incremental")
        self.assertTrue(any("since=" in r[1] for r in self.list_requests()))

    def test_sync_full_ignores_the_cached_pages(self) -> None:
        """Prevents `--full` trusting a cache the operator doubts."""
        report = self.pull(full=True)
        self.assertEqual((report.pages_fetched, report.pages_reused, report.mode), (3, 0, "full"))


class SyncFullCommandWalk(unittest.TestCase):
    def test_sync_full_command_rereads_an_edit_the_cursor_would_not_list(self) -> None:
        """Prevents `qew sync --full` being a flag that changes nothing: it must pull an edit a conditional pull cannot see."""
        fake = FakeGitHub().start()
        self.addCleanup(fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{fake.repo_url}"\napi = "{fake.api}"\n'
        world = World(mirrors, replication=OFF)
        self.addCleanup(world.close)
        env = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}
        world.create("--kind", "issue", "--title", "Original", "--quote", QUOTE)
        world.run("sync", env=env)
        fake.issues[1]["title"] = "Retitled by a human"  # a human edit the forge's modification time does not show
        fake.etags = False
        fake.issues[1]["updated_at"] = "2000-01-01T00:00:00Z"  # older than any cursor, so a since listing skips it
        quiet = world.run("sync", env=env).stdout
        self.assertNotIn("applied=1", quiet)
        loud = world.run("sync", "--full", env=env).stdout
        self.assertIn("applied=1", loud)


if __name__ == "__main__":
    unittest.main()
