"""`qew sync` scope flags and the size refusal, through the CLI over real HTTP against a local Forgejo fake.

Adversary: a first sync of a big ledger that writes everything at once and runs into forge limits, a filter that
quietly syncs more or less than it names, and a refusal that hides the number or the way out.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_forgejo import TOKEN, FakeForgejo
from tests.ledger_harness import World

REPLICATION = "\n[replication]\nauto_forge_sync = false\nforge_concurrency = 1\n"


class SyncScopeWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeForgejo().start()
        self.addCleanup(self.fake.stop)
        mirror = f'\n[[mirror]]\nforge = "forgejo"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'
        self.world = World(mirror, replication=REPLICATION)
        self.addCleanup(self.world.close)
        w = self.world
        self.epic = w.create("--kind", "epic", "--title", "Epic", "--draft")
        self.kids = [w.create("--kind", "issue", "--title", f"Kid {n}", "--parent", self.epic, "--draft") for n in (1, 2)]
        self.loose = [w.create("--kind", "issue", "--title", f"Loose {n}", "--draft") for n in (1, 2)]

    def lower_limit(self) -> None:
        """Three writes are the most one unscoped sync may make here."""
        path = self.world.code / ".qew" / "config.toml"
        path.write_text(path.read_text().replace("forge_concurrency = 1\n", "forge_concurrency = 1\nsync_limit = 3\n"))

    def sync(self, *extra: str):
        return self.world.run("sync", *extra, env={"FORGEJO_TOKEN": TOKEN})

    def titles(self) -> list[str]:
        return sorted(issue["title"] for issue in self.fake.issues.values())

    def test_a_large_unscoped_sync_is_refused_naming_the_count_and_the_way_out(self) -> None:
        """Prevents the all-at-once explosion: five writes against a limit of three stop before any forge call."""
        self.lower_limit()
        result = self.sync()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        for word in ("5", "--all", "--limit", "--tree", "sync_limit"):
            self.assertIn(word, result.stderr)
        self.assertEqual(self.fake.issues, {})

    def test_all_writes_everything(self) -> None:
        """Prevents a refusal with no way past it: --all is the explicit yes."""
        self.lower_limit()
        result = self.sync("--all")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.fake.issues), 5)

    def test_tree_syncs_the_item_and_everything_below_it_only(self) -> None:
        """Prevents a tree filter that leaks: the epic and its two children are written, the loose issues are not."""
        result = self.sync("--tree", self.epic)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.titles(), ["Epic", "Kid 1", "Kid 2"])

    def test_limit_writes_the_first_n_parents_first_and_a_rerun_continues(self) -> None:
        """Prevents a limit that strands children without their parent, or repeats work on the next run."""
        self.assertEqual(self.sync("--limit", "2").returncode, 0)
        self.assertEqual(len(self.fake.issues), 2)
        self.assertIn("Epic", self.titles())
        self.assertEqual(self.sync("--limit", "2").returncode, 0)
        self.assertEqual(len(self.fake.issues), 4)

    def test_a_limit_above_sync_limit_is_the_users_explicit_choice(self) -> None:
        """Prevents the guard blocking a deliberate --limit that is larger than the default threshold."""
        self.lower_limit()
        self.assertEqual(self.sync("--limit", "4").returncode, 0)
        self.assertEqual(len(self.fake.issues), 4)

    def test_since_a_future_date_matches_nothing_and_says_so(self) -> None:
        """Prevents a silent empty run: no match is stated, nothing is written."""
        result = self.sync("--since", "2999-01-01")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("no item matches", result.stdout)
        self.assertEqual(self.fake.issues, {})

    def test_since_a_past_date_matches_recent_work(self) -> None:
        """Prevents a window that drops items changed inside it."""
        self.assertEqual(self.sync("--since", "2000-01-01", "--limit", "5").returncode, 0)
        self.assertEqual(len(self.fake.issues), 5)

    def test_author_and_owner_narrow_to_the_person(self) -> None:
        """Prevents a person filter that ignores the person: another author matches nothing, the real one matches all."""
        author = json.loads(self.world.run("show", self.epic, "--json").stdout)["author"]
        other = self.sync("--author", "nobody-at-all")
        self.assertIn("no item matches", other.stdout)
        self.assertEqual(self.fake.issues, {})
        self.assertEqual(self.sync("--author", author, "--limit", "5").returncode, 0)
        self.assertEqual(len(self.fake.issues), 5)
        self.assertIn("no item matches", self.sync("--owner", "nobody-at-all").stdout)

    def test_dry_run_with_a_scope_lists_only_that_scope_and_is_never_refused(self) -> None:
        """Prevents a plan that shows more than the real run would write, or a refused preview."""
        self.lower_limit()
        scoped = self.world.run("sync", "--dry-run", "--tree", self.epic)
        self.assertEqual(scoped.returncode, 0, scoped.stderr)
        self.assertEqual(sum("create" in line for line in scoped.stdout.splitlines()), 3)
        everything = self.world.run("sync", "--dry-run")
        self.assertEqual(everything.returncode, 0, everything.stderr)
        self.assertEqual(sum("create" in line for line in everything.stdout.splitlines()), 5)
        self.assertIn("sync_limit", everything.stdout)

    def test_a_bad_since_is_a_usage_error(self) -> None:
        """Prevents a typo in the window from meaning 'everything'."""
        result = self.sync("--since", "last tuesday")
        self.assertEqual(result.returncode, 2)
        self.assertIn("not a UTC instant", result.stderr)

    def test_item_is_exempt_from_the_size_refusal(self) -> None:
        """Prevents the guard stopping a named item: the background pass syncs queued ids and must keep working."""
        self.lower_limit()
        self.assertEqual(self.sync("--item", self.loose[0], "--item", self.loose[1], "--item", self.kids[0], "--item", self.kids[1]).returncode, 0)
        self.assertEqual(len(self.fake.issues), 4)


if __name__ == "__main__":
    unittest.main()
