"""`qew pr title N TEXT`: each pull request of one item can carry its own title, and sync keeps it.

Adversary: several pull requests of one item all reading the item's title with no way to tell them apart, a custom title that
the next `pr sync` writes back over, a block suffix lost or doubled on a custom title, and a title the forge would take as a
multi-line or empty string.
"""

import json
import unittest

from tests.pr_harness import PrWorld

CUSTOM = "ADU.3 group 50: revenue"


class PrTitleWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        self.item = self.w.item("ADU.3 group 50")
        self.w.head = "first-branch"
        self.assertEqual(self.w.open(self.item).code, 0)
        self.w.head = "second-branch"
        second = self.w.open(self.item)
        self.assertEqual(second.code, 0, second)
        self.assertEqual(self.w.forge.pull_specs[2].title, "ADU.3 group 50")

    def titled(self, number: int) -> str:
        return self.w.forge.read_pull_request(number).title

    def test_a_title_is_set_on_the_forge_and_recorded_and_the_other_pull_request_keeps_the_items(self) -> None:
        """Prevents two pull requests of one item looking identical: #2 gets its own title, #1 keeps the item's."""
        done = self.w.qew("pr", "title", "2", CUSTOM)
        self.assertEqual(done.code, 0, done)
        self.assertEqual(self.titled(2), CUSTOM)
        self.assertEqual(self.titled(1), "ADU.3 group 50")
        recorded = json.loads((self.w.world.home("mirrors", self.item) / self.w.mirror_files(self.item)[1]).read_text())
        self.assertEqual(recorded["pr_title"], CUSTOM)

    def test_sync_keeps_the_custom_title_and_still_follows_the_item_for_the_others(self) -> None:
        """Prevents sync writing the item's title back over a title set on purpose, even after the item is renamed."""
        self.w.qew("pr", "title", "2", CUSTOM)
        self.assertEqual(self.w.qew("set", self.item, "--title", "ADU.3 group 50 renamed").code, 0)
        self.assertEqual(self.w.qew("pr", "sync", "2").code, 0)
        self.assertEqual(self.w.qew("pr", "sync", "1").code, 0)
        self.assertEqual(self.titled(2), CUSTOM)
        self.assertEqual(self.titled(1), "ADU.3 group 50 renamed")

    def test_a_blocked_suffix_goes_on_the_custom_title_and_comes_off_again(self) -> None:
        """Prevents the suffix replacing or doubling on a custom title."""
        self.w.qew("pr", "title", "2", CUSTOM)
        self.assertEqual(self.w.qew("pr", "block", "2", "--by", "1").code, 0)
        self.assertEqual(self.titled(2), f"{CUSTOM} [blocked by: #1]")
        self.assertEqual(self.w.qew("pr", "title", "2", "ADU.3 group 50: other").code, 0)
        self.assertEqual(self.titled(2), "ADU.3 group 50: other [blocked by: #1]")
        self.assertEqual(self.w.qew("pr", "unblock", "2").code, 0)
        self.assertEqual(self.titled(2), "ADU.3 group 50: other")

    def test_empty_multiline_and_overlong_titles_and_unknown_pull_requests_are_refused(self) -> None:
        """Prevents a title the forge would mangle: nothing is sent, exit 2."""
        sent = len(self.w.forge.title_writes)
        for bad in ("", "   ", "two\\nlines", "x" * 257):
            with self.subTest(text=bad[:20]):
                self.assertEqual(self.w.qew("pr", "title", "2", bad.replace("\\n", "\n")).code, 2)
        self.assertEqual(self.w.qew("pr", "title", "99", CUSTOM).code, self.w.qew("pr", "sync", "99").code)  # an unrecorded pull request, as for every pr verb
        self.assertEqual(len(self.w.forge.title_writes), sent)

    def test_json_output_names_the_number_and_the_title(self) -> None:
        """Prevents agents scraping prose."""
        done = self.w.qew("pr", "title", "2", CUSTOM, "--json")
        self.assertEqual(json.loads(done.out), {"number": 2, "title": CUSTOM, "changed": True, "schema_version": 1})
        again = self.w.qew("pr", "title", "2", CUSTOM, "--json")
        self.assertFalse(json.loads(again.out)["changed"])


if __name__ == "__main__":
    unittest.main()
