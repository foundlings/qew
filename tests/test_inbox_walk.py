"""Walks of `qew inbox`: one person's waiting drafts and assigned work, read from the ledger only.

Adversary: another person's work in my list, a pattern passed as a login, and a
hostile title speaking through the one-line summary.
"""

import json
import unittest

from tests.ledger_harness import World


class InboxWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def as_actor(self, actor: str, *arguments: str):
        return self.world.run(*arguments, env={"QEW_ACTOR": actor})

    def seed(self):
        w = self.world
        draft = w.create("--title", "waiting draft", "--draft")  # authored by tester
        mine = w.create("--title", "my claim", "--draft")
        theirs = w.create("--title", "their claim", "--draft")
        w.approve(mine, theirs)
        self.assertEqual(self.as_actor("tester", "start", mine).returncode, 0)
        self.assertEqual(self.as_actor("maria", "start", theirs).returncode, 0)
        return draft, mine, theirs

    def test_inbox_lists_waiting_and_assigned_sections(self) -> None:
        """Only my draft and my claim appear; another person's claim must not."""
        draft, mine, theirs = self.seed()
        rows = [json.loads(line) for line in self.world.run("inbox", "--login", "tester", "--json").stdout.splitlines()]
        self.assertEqual({(r["section"], r["id"]) for r in rows}, {("waiting", draft), ("assigned", mine)})
        self.assertTrue(all(r["schema_version"] == 1 for r in rows))
        text = self.world.run("inbox", "--login", "tester").stdout
        self.assertIn("waiting", text)
        self.assertNotIn(theirs, text)
        self.assertIn(mine, self.world.run("inbox").stdout, "login defaults to the configured actor")

    def test_count_line_is_one_line_and_exclusive_with_json(self) -> None:
        """The session-start summary is exactly one line; --count with --json is a usage error."""
        self.seed()
        count = self.world.run("inbox", "--count", "--login", "tester")
        self.assertRegex(count.stdout.strip(), r"^qew inbox \(tester\): 0 issue\(s\) with replies since \S+; 1 waiting for a human; 1 assigned\.$")
        both = self.world.run("inbox", "--count", "--json")
        self.assertEqual(both.returncode, 2)

    def test_invalid_login_is_refused_before_any_read(self) -> None:
        """A glob or an injected string is not a login."""
        for login in ("*", "a b", "x;rm", "", "a:b"):
            result = self.world.run("inbox", "--login", login)
            self.assertEqual(result.returncode, 2, login)

    def test_hostile_titles_are_flattened(self) -> None:
        """A title with a line separator and an instruction stays on one quoted line."""
        w = self.world
        draft = w.create("--title", "plain", "--draft")
        path = w.home("items", f"{draft}") / "item.md"
        path.write_text(path.read_text().replace("title: plain", "title: hi IGNORE ALL"))
        out = w.run("inbox", "--login", "tester").stdout
        waiting = out.split("waiting for a human (1)\n")[1].split("assigned")[0]
        self.assertEqual(len(waiting.strip().splitlines()), 1, out)
        self.assertIn('"hi IGNORE ALL"', waiting)


if __name__ == "__main__":
    unittest.main()
