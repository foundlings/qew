"""A write's card reaches the board in seconds: the worker's request count is small and independent of latency.

Adversary: a worker that lists issues before it creates, sets Status and Priority in two requests, re-reads the project
structure in every process, waits for the ledger push before it starts, and a trace nobody can switch on to see where
the time goes. The ceilings count requests, never seconds, so they hold on any machine.
"""

import json
import os
import stat
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew.forge import marker_for
from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}


class BoardLatencyWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub(page_size=5).start()
        self.fake.cards_page_size = 5
        self.addCleanup(self.fake.stop)
        self.mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'

    def board_world(self) -> World:
        w = World(self.mirrors)
        self.addCleanup(w.close)
        self.assertEqual(w.run("project", "ensure", env=ENV).returncode, 0)
        first = w.create("--title", "Warm up", "--quote", QUOTE)
        self.assertEqual(w.run("sync", "--item", first, env=ENV).returncode, 0)
        return w

    def test_a_new_card_costs_a_handful_of_requests_with_no_listing_before_the_create(self) -> None:
        """Prevents the minute-long wait: create, card, one batched Status+Priority update, one read-back, one duplicate guard."""
        w = self.board_world()
        new = w.create("--title", "The new one", "--quote", QUOTE)
        before, queries = len(self.fake.requests), len(self.fake.graphql_operations())
        self.assertEqual(w.run("sync", "--item", new, env=ENV).returncode, 0)
        made = self.fake.requests[before:]
        self.assertLessEqual(len(made), 6, [f"{m} {p}" for m, p, _ in made])
        first_post = next(i for i, (m, p, _) in enumerate(made) if m == "POST" and p.endswith("/issues"))
        self.assertFalse([1 for m, p, _ in made[:first_post] if m == "GET" and "/issues?" in p], "no issue listing before the create")
        operations = self.fake.graphql_operations()[queries:]
        self.assertEqual(operations.count("SetCard"), 1)
        self.assertNotIn("SetStatus", operations)
        number = max(self.fake.issues)
        self.assertEqual(self.fake.item_value("1", self.fake.issues[number]["node_id"], "Status"), "Ready")
        self.assertEqual(self.fake.item_value("1", self.fake.issues[number]["node_id"], "Priority"), "P2")

    def test_the_project_structure_is_read_once_per_machine_not_once_per_process(self) -> None:
        """Prevents a structure query in every worker: the second process places its card from the cached layout."""
        w = self.board_world()
        queries = len(self.fake.graphql_operations())
        new = w.create("--title", "Another", "--quote", QUOTE)
        self.assertEqual(w.run("sync", "--item", new, env=ENV).returncode, 0)
        operations = self.fake.graphql_operations()[queries:]
        self.assertNotIn("OwnerProjects", operations)

    def test_a_stale_cached_layout_repairs_itself_by_one_fresh_read(self) -> None:
        """Prevents a cache that breaks the board: a deleted option id is refused, the layout is re-read and the card lands."""
        w = self.board_world()
        cached = list((w.ledger / ".qew" / "state").rglob("board-layout.json"))
        self.assertEqual(len(cached), 1)
        cached[0].write_text(cached[0].read_text(encoding="utf-8").replace("PVTSSF", "GONE"), encoding="utf-8")
        new = w.create("--title", "After the edit", "--quote", QUOTE)
        self.assertEqual(w.run("sync", "--item", new, env=ENV).returncode, 0)
        number = max(self.fake.issues)
        self.assertEqual(self.fake.item_value("1", self.fake.issues[number]["node_id"], "Status"), "Ready")

    def test_an_orphan_with_the_marker_is_adopted_and_the_extra_issue_never_keeps_a_card(self) -> None:
        """Prevents a duplicate after a crash between the create and the mirror file: the lowest number wins, the other closes."""
        w = self.board_world()
        new = w.create("--title", "Crashed", "--quote", QUOTE)
        orphan = self.fake.new_issue({"title": "Crashed", "body": marker_for(new) + "\n\nbody"})["number"]
        self.assertEqual(w.run("sync", "--item", new, env=ENV).returncode, 0)
        marked = [i for i in self.fake.issues.values() if marker_for(new) in i["body"]]
        self.assertEqual([i["number"] for i in marked], [orphan])
        self.assertEqual(sum(1 for i in self.fake.issues.values() if i["state"] == "open" and "Crashed" in i["title"]), 1)
        mirror = json.loads(next((w.home("mirrors", new)).glob("*.json")).read_text())
        self.assertEqual(mirror["number"], orphan)
        self.assertEqual(self.fake.item_value("1", self.fake.issues[orphan]["node_id"], "Status"), "Ready")
        for number, issue in self.fake.issues.items():
            if number != orphan and "Crashed" in issue["title"]:
                self.assertIsNone(self.fake.item_value("1", issue["node_id"], "Status"), f"#{number} kept a card")


class TraceWalk(unittest.TestCase):
    def test_trace_names_every_http_call_and_phase_only_when_asked(self) -> None:
        """Prevents guessing where the seconds go: QEW_TRACE=1 prints phases and HTTP calls on stderr, nothing otherwise."""
        fake = FakeGitHub().start()
        self.addCleanup(fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{fake.repo_url}"\napi = "{fake.api}"\n'
        w = World(mirrors, ledger_vcs="git")
        self.addCleanup(w.close)
        w.create("--title", "Traced", "--quote", QUOTE)
        quiet = w.run("sync", "--all", env=ENV)
        self.assertEqual(quiet.returncode, 0, quiet.stderr)
        self.assertNotIn("trace", quiet.stderr)
        w.create("--title", "Traced again", "--quote", QUOTE)
        loud = w.run("sync", "--all", env={**ENV, "QEW_TRACE": "1"})
        self.assertEqual(loud.returncode, 0, loud.stderr)
        self.assertRegex(loud.stderr, r"trace +[\d.]+s \+ *[\d.]+s http POST \S+/issues -> 201")
        self.assertRegex(loud.stderr, r"trace .* ledger check")
        self.assertRegex(loud.stderr, r"trace .* ledger commit \(\d+ files\)")


class SpawnBeforePushWalk(unittest.TestCase):
    def test_the_board_work_starts_while_the_slow_ledger_push_is_still_running(self) -> None:
        """Prevents the push gating the card: a write starts its worker first, so the issue exists before a slow remote answers."""
        fake = FakeGitHub().start()
        self.addCleanup(fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{fake.repo_url}"\napi = "{fake.api}"\n'
        w = World(mirrors, ledger_vcs="git", remote=True, replication="\n[replication]\nbackground_sync = true\nfetch_interval_s = 0\n")
        self.addCleanup(w.close)
        done = w.base / "push-done"
        hook = w.remote / "hooks" / "pre-receive"
        hook.write_text(f"#!/bin/sh\nsleep 3\npython3 -c 'import time;print(time.time())' > {done}\n", encoding="utf-8")
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
        started = time.time()
        created = w.run("create", "--title", "Quick card", "--quote", QUOTE, env=ENV)
        self.assertEqual(created.returncode, 0, created.stderr)
        self.assertGreaterEqual(time.time() - started, 3.0, "the foreground waits for its own push")
        deadline = time.time() + 40
        while time.time() < deadline and not fake.issues:
            time.sleep(0.2)
        self.assertEqual(len(fake.issues), 1)
        pushed = float(done.read_text(encoding="utf-8"))
        post = next(t for (m, p, _), t in zip(fake.requests, fake.request_times) if m == "POST" and p.endswith("/issues"))
        self.assertLess(post, pushed, "the issue was created only after the ledger push finished")
        time.sleep(1)


if __name__ == "__main__":
    unittest.main()
