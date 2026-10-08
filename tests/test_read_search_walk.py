"""Walks of `qew search`: offline search over approved work, never a way to read unapproved text.

Adversary: unapproved text surfacing through a routine read, a silently clipped answer, a dead end
on an empty result, and a search term reaching a shell.
"""

import json
import os
import unittest

from tests.ledger_harness import World


class SearchWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def make(self, title: str, *extra: str, approve: bool = True) -> str:
        item = self.world.create("--title", title, *extra)
        if approve:
            self.world.approve(item)
        return item

    def test_approved_matches_come_best_first_and_drafts_never_appear(self) -> None:
        """An exact title beats a word beats a substring; a matching draft stays out (triage owns it)."""
        sub = self.make("Reparse the parsers")
        word = self.make("Fix the parser crash")
        exact = self.make("parser")
        self.make("Parser draft", approve=False)
        listed = self.world.run("search", "parser").stdout.splitlines()
        self.assertEqual([line.split()[0] for line in listed], [exact, word, sub])
        body = json.loads(self.world.run("search", "parser", "--json").stdout)
        self.assertEqual((body["total"], body["truncated"], body["schema_version"]), (3, False, 1))

    def test_every_term_must_match_across_id_title_and_labels_ignoring_ascii_case(self) -> None:
        """Two terms narrow; a label counts; the item id counts."""
        both = self.make("Fix header parser", "--label", "area:Cli")
        self.make("Fix footer parser")
        self.assertEqual([l.split()[0] for l in self.world.run("search", "HEADER", "area:cli").stdout.splitlines()], [both])
        self.assertEqual([l.split()[0] for l in self.world.run("search", both[:6]).stdout.splitlines()][:1], [both])

    def test_a_capped_answer_says_so_and_an_empty_one_points_at_triage(self) -> None:
        """No silently clipped list, no dead end."""
        for n in range(32):
            self.make(f"common {n:02}")
        capped = self.world.run("search", "common")
        self.assertEqual(len(capped.stdout.splitlines()), 30)
        self.assertIn("search results capped", capped.stderr)
        self.assertEqual(len(self.world.run("search", "common", "--limit", "0").stdout.splitlines()), 32)
        empty = self.world.run("search", "nothing-like-this")
        self.assertEqual((empty.returncode, empty.stdout), (0, ""))
        self.assertIn("qew triage --search", empty.stderr)

    def test_terms_are_data_and_never_reach_a_shell(self) -> None:
        """Metacharacters, newlines and a leading dash are matched literally and execute nothing."""
        marker = self.world.base / "pwned"
        hit = self.make("weird $(touch pwned); `id` -rf")
        for term in (f"$(touch {marker})", "`id`", "a\nb"):
            self.assertEqual(self.world.run("search", "--", term).returncode, 0)
        self.assertFalse(marker.exists())
        self.assertEqual(self.world.run("search", "--", "-rf").stdout.split()[0], hit)
        self.assertEqual(self.world.run("search", "--", "$(touch").stdout.split()[0], hit)


if __name__ == "__main__":
    unittest.main()
