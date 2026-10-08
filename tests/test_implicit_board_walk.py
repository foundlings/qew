"""The board follows the ledger implicitly: item-scoped background sync, one-call write verbs.

Adversary: a write that re-reads the whole forge (cost grows with the ledger, so the card lands a minute later), ten quick
writes starting ten whole-target syncs, a human-filed issue that sits off the board or in Draft, and write verbs that print
banners which invite a second call.
"""

import fcntl
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}
BACKGROUND = "\n[replication]\nbackground_sync = true\n"


class ImplicitBoardWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub(page_size=5).start()
        self.fake.cards_page_size = 5
        self.addCleanup(self.fake.stop)
        self.mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'

    def world(self, replication: str = "") -> World:
        w = World(self.mirrors, replication=replication)
        self.addCleanup(w.close)
        return w

    def hold_worker_lock(self, w: World):
        (w.ledger / ".qew").mkdir(exist_ok=True)
        handle = open(w.ledger / ".qew" / "sync.lock", "a+")
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.addCleanup(handle.close)

    def column(self, number: int) -> str:
        return self.fake.item_value("1", self.fake.issues[number]["node_id"], "Status")

    def scoped_cost(self, existing: int):
        fake = FakeGitHub(page_size=5).start()
        fake.cards_page_size = 5
        self.addCleanup(fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{fake.repo_url}"\napi = "{fake.api}"\n'
        w = World(mirrors)
        self.addCleanup(w.close)
        for index in range(existing):
            w.create("--title", f"Existing {index}", "--quote", QUOTE)
        self.assertEqual(w.run("project", "ensure", env=ENV).returncode, 0)
        self.assertEqual(w.run("sync", env=ENV).returncode, 0)
        new = w.create("--title", "The new one", "--quote", QUOTE)
        before = len(fake.requests)
        result = w.run("sync", "--item", new, env=ENV)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        made = fake.requests[before:]
        number = max(fake.issues)
        self.assertEqual(fake.item_value("1", fake.issues[number]["node_id"], "Status"), "Ready")
        return len(made), [path for method, path, _ in made if method == "GET" and re.search(r"/issues\?", path)], \
            [op for op in fake.graphql_operations()[-len(made):] if op in ("BoardItems", "ProjectCards")]

    def test_a_scoped_sync_costs_the_same_for_a_small_and_a_large_ledger(self) -> None:
        """Prevents the card landing a minute after the write: the item sync never lists every issue or every card."""
        small, small_lists, small_boards = self.scoped_cost(3)
        large, large_lists, large_boards = self.scoped_cost(18)
        self.assertEqual((small_lists, small_boards, large_lists, large_boards), ([], [], [], []))
        self.assertEqual(small, large)
        self.assertLessEqual(large, 16)

    def test_quick_writes_queue_their_items_once_and_one_worker_run_syncs_them_all(self) -> None:
        """Prevents ten writes starting ten whole-target syncs: the queue is a union of item ids drained by one run."""
        w = self.world(BACKGROUND)
        self.assertEqual(w.run("project", "ensure", env=ENV).returncode, 0)
        self.hold_worker_lock(w)
        first = w.run("create", "--title", "First", "--quote", QUOTE, env=ENV).stdout.split()[0]
        second = w.run("create", "--title", "Second", "--quote", QUOTE, env=ENV).stdout.split()[0]
        self.assertEqual(w.run("set", first, "--title", "First again", env=ENV).returncode, 0)
        queued = (w.ledger / ".qew" / "sync.queue").read_text(encoding="utf-8").split()
        self.assertEqual(sorted(set(queued)), sorted([first, second]))
        self.assertEqual(len(self.fake.issues), 0)
        before = len(self.fake.requests)
        worker = w.run("sync", env={**ENV, "QEW_BACKGROUND": "1"})
        self.assertEqual(worker.returncode, 0, worker.stdout + worker.stderr)
        self.assertEqual(sorted(i["title"] for i in self.fake.issues.values()), ["First again", "Second"])
        self.assertEqual([self.column(n) for n in sorted(self.fake.issues)], ["Ready", "Ready"])
        self.assertFalse((w.ledger / ".qew" / "sync.queue").exists() and (w.ledger / ".qew" / "sync.queue").read_text().strip())
        lists = [p for m, p, _ in self.fake.requests[before:] if m == "GET" and re.search(r"/issues\?", p)]
        self.assertEqual(lists, [])

    def test_an_issue_filed_on_the_forge_gets_a_backlog_card_and_stays_a_non_startable_draft(self) -> None:
        """Prevents a human-filed issue sitting off the board or being moved to Draft: qew imports and places it in Backlog."""
        w = self.world()
        self.assertEqual(w.run("project", "ensure", env=ENV).returncode, 0)
        filed = self.fake.new_issue({"title": "Filed by a human", "body": "please look"})["number"]
        for _ in range(2):  # the second pass proves sync never fights its own placement
            self.assertEqual(w.run("sync", env=ENV).returncode, 0)
            self.assertEqual(self.column(filed), "Backlog")
        row = next(line for line in w.run("list", "--json").stdout.splitlines() if "Filed by a human" in line)
        self.assertIn('"state": "draft"', row)
        item = row.split('"id": "')[1].split('"')[0]
        self.assertNotEqual(w.run("start", item).returncode, 0)

    def test_write_verbs_answer_in_one_compact_line_and_name_what_the_agent_needs_next(self) -> None:
        """Prevents a second tool call: no banner, the claim names the title, completion names the work it released."""
        w = self.world(BACKGROUND)
        self.hold_worker_lock(w)
        need = w.run("create", "--title", "Parse the headers", "--quote", QUOTE, env=ENV)
        need_id = need.stdout.split()[0]
        after = w.create("--title", "Use the headers", "--needs", need_id, "--quote", QUOTE)
        started = w.run("start", need_id, env=ENV)
        w.run("evidence", need_id, "--change", w.commit(), env=ENV)
        done = w.run("complete", need_id, env=ENV)
        for result, ceiling in ((need, 80), (started, 120), (done, 100)):
            self.assertEqual(len(result.stdout.splitlines()), 1, result.stdout)
            self.assertNotIn("sync:", result.stdout)
            self.assertLessEqual(len(result.stdout.encode()), ceiling, result.stdout)
        self.assertEqual(started.stdout.strip(), f"{need_id} claimed by tester: Parse the headers")
        self.assertEqual(done.stdout.strip(), f"{need_id} completed; ready: {after}")


if __name__ == "__main__":
    unittest.main()
