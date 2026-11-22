"""Reading pull request reviews costs a bounded, shrinking number of forge calls per sync, never every pull request every pass.

Adversary: 1,106 tracked pull requests whose ledger record still says `open` (they finished on the forge outside qew) each read for
reviews and inline comments on every sync: 2,200 core calls a pass, more than the shared token's hour, so the sync that starts at every
quota reset burns the whole budget, is rate limited, and starts again at the next reset: the fleet starved every hour.
"""

import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import ledger_config
from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

OFF = "\n[replication]\nauto_forge_sync = false\nauto_push = false\nforge_concurrency = 1\n"
ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}


class ReviewBudgetWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub().start()
        self.addCleanup(self.fake.stop)
        self.fake.add_branch("main")
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'
        self.world = World(mirrors, replication=OFF)
        self.addCleanup(self.world.close)
        self.slug = ledger_config.mirror_slug(ledger_config.load(self.world.code).mirrors[0])
        self.item = self.world.create("--kind", "issue", "--title", "Reviewed work", "--quote", QUOTE)
        self.sync("--all")

    def sync(self, *flags: str, env: dict[str, str] | None = None) -> None:
        result = self.world.run("sync", *flags, env={**ENV, **(env or {})})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def pull(self, head: str, state: str = "open", recorded: str = "open") -> int:
        """A pull request on the forge in `state`, tracked in the ledger as `recorded`."""
        self.fake.add_branch(head)
        number = self.fake.new_pull({"title": head, "body": "b", "head": head, "base": "main"})["number"]
        self.fake.pulls[number]["state"] = state
        self.fake.issues[number]["state"] = state
        path = self.world.home("mirrors", self.item) / f"{self.slug}-pr-{number}.json"
        path.write_text(json.dumps({"forge": "github", "url": self.fake.repo_url, "kind": "pr", "number": number, "items": [self.item], "state": recorded}))
        return number

    def review_reads(self, number: int | None = None) -> int:
        return sum(1 for _m, path, _b in self.fake.requests if "/pulls/" in path and ("/reviews" in path or "/comments" in path)
                   and (number is None or f"/pulls/{number}/" in path))

    def test_a_pull_request_the_forge_shows_finished_is_read_once_even_when_the_record_says_open(self) -> None:
        """Prevents a stale `open` record being re-read for ever: the forge's closed or merged state ends the reads after one."""
        numbers = [self.pull(f"old-{n}", state="closed") for n in range(5)]
        self.sync("--full")
        first = self.review_reads()
        self.assertEqual(first, 2 * len(numbers))
        self.sync("--full")
        self.assertEqual(self.review_reads(), first, "a finished pull request was read again")

    def test_an_incremental_pass_reads_only_the_pull_requests_the_listing_shows_changed(self) -> None:
        """Prevents every pass reading every open pull request: nothing changed since the cursor, nothing is asked."""
        number = self.pull("live")
        self.sync("--full")
        self.assertEqual(self.review_reads(number), 2)
        self.sync("--all")
        self.assertEqual(self.review_reads(number), 2, "an unchanged open pull request was read by an incremental pass")
        time.sleep(1.1)  # the forge's timestamps have one-second resolution
        self.fake.add_review(number, "dana", "COMMENTED", "new thought")
        self.sync("--all")
        self.assertEqual(self.review_reads(number), 4)
        self.assertIn("new thought", self.world.run("show", self.item).stdout)

    def test_a_pass_reads_at_most_the_cap_and_the_next_pass_goes_on(self) -> None:
        """Prevents one pass spending an hour of calls: with a cap of 3 pull requests a pass, eight are done in three passes."""
        numbers = [self.pull(f"old-{n}", state="closed") for n in range(8)]
        cap = {"QEW_REVIEW_READS": "3"}
        seen = []
        for _ in range(4):
            before = self.review_reads()
            self.sync("--full", env=cap)
            seen.append(self.review_reads() - before)
        self.assertEqual(seen, [6, 6, 4, 0])
        self.assertEqual(self.review_reads(), 2 * len(numbers))


if __name__ == "__main__":
    unittest.main()
