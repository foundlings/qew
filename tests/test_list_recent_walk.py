"""`qew list --recent [N]` and `--since`: the newest items first by creation time, with the same filters as `list`.

Each test is a scenario; its docstring names the failure it prevents (seen live: checking whether an item was just created
needed a keyword search, because `list` returned every item in priority order).
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World


class ListRecentWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)
        self.ids = [self.world.create("--kind", "task" if number % 2 else "issue", "--title", f"Item {number}", "--quote", QUOTE) for number in range(5)]

    def listed(self, *flags: str) -> list[str]:
        result = self.world.run("list", *flags, "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return [json.loads(line)["id"] for line in result.stdout.splitlines() if line.strip()]

    def backdate(self, item: str, created: str) -> None:
        path = self.world.ledger / "items" / item / "item.md"
        lines = path.read_text().splitlines()
        path.write_text("\n".join(f"created: {created}" if line.startswith("created:") else line for line in lines) + "\n")

    def test_recent_lists_the_newest_items_first_and_stops_at_n(self) -> None:
        """Prevents a recency view that returns priority order: the last N created come first, newest first."""
        self.assertEqual(self.listed("--recent", "3"), list(reversed(self.ids))[:3])

    def test_recent_without_a_number_shows_up_to_a_hundred_and_says_nothing_about_a_cut(self) -> None:
        """Prevents a surprise default: a bare --recent is 100, and a short ledger shows everything newest first."""
        result = self.world.run("list", "--recent", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([json.loads(line)["id"] for line in result.stdout.splitlines()], list(reversed(self.ids)))
        self.assertNotIn("truncated", result.stderr)

    def test_recent_follows_creation_time_not_the_id(self) -> None:
        """Prevents ordering by id alone: an item whose created header is older sorts after newer ones."""
        self.backdate(self.ids[-1], "2020-01-01T00:00:00Z")
        self.assertEqual(self.listed("--recent", "5")[-1], self.ids[-1])

    def test_recent_takes_the_list_filters(self) -> None:
        """Prevents a recency view that ignores --kind: the filter applies before the newest N are taken."""
        tasks = [i for number, i in enumerate(self.ids) if number % 2]
        self.assertEqual(self.listed("--recent", "5", "--kind", "task"), list(reversed(tasks)))

    def test_since_keeps_items_created_at_or_after_a_date_an_instant_or_an_age(self) -> None:
        """Prevents a --since that is ignored: an old item drops out for a date, an instant and an age, and stays for an older start."""
        old = self.ids[0]
        self.backdate(old, "2020-01-01T00:00:00Z")
        for since in ("2021-01-01", "2021-01-01T00:00:00Z", "1d"):
            with self.subTest(since=since):
                self.assertNotIn(old, self.listed("--since", since))
                self.assertEqual(len(self.listed("--since", since)), 4)
        self.assertIn(old, self.listed("--since", "2019-01-01"))

    def test_a_bad_since_is_refused_with_exit_2(self) -> None:
        """Prevents a typo listing everything: a start that is no date, instant or age is refused naming the forms."""
        result = self.world.run("list", "--since", "yesterday-ish")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("2h", result.stderr)


if __name__ == "__main__":
    unittest.main()
