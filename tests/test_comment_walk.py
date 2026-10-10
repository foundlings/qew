"""Walks of `qew comment`: append-only discussion in the ledger.

Adversary: discussion living only on the forge, a mangled or empty or ambiguous comment, and comment
text that is mistaken for a claim, a release or any other state change.
"""

import json
import unittest

from tests.ledger_harness import World


class CommentWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)
        self.item = self.world.create("--title", "Discuss me", "--draft")
        self.world.approve(self.item)

    def comments(self) -> list:
        return json.loads(self.world.run("show", self.item, "--json").stdout).get("comments", [])

    def test_a_comment_is_recorded_and_listed_with_author_and_time(self) -> None:
        """show lists the comment with its author and timestamp; the ledger holds it, not the forge."""
        result = self.world.run("comment", self.item, "--body", "first thoughts")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split()[:3], ["commented", "on", self.item])
        (entry,) = self.comments()
        self.assertEqual((entry["index"], entry["author"], entry["text"]), (1, "tester", "first thoughts"))
        self.assertRegex(entry["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(self.world.events(self.item)[-1]["type"], "commented")
        self.assertIn("first thoughts", self.world.run("show", self.item).stdout)

    def test_a_body_file_keeps_its_exact_text_including_the_final_newline(self) -> None:
        """Nothing is trimmed or rewrapped on the way in; stdin works with `-`."""
        path = self.world.base / "note.txt"
        path.write_text("line one\n  indented two\n")
        self.assertEqual(self.world.run("comment", self.item, "--body-file", str(path)).returncode, 0)
        self.assertEqual(self.world.run("comment", self.item, "--body-file", "-", stdin="from stdin\n").returncode, 0)
        self.assertEqual([c["text"] for c in self.comments()], ["line one\n  indented two\n", "from stdin\n"])

    def test_a_missing_blank_or_doubled_body_is_refused_before_any_write(self) -> None:
        """Exit 2 and no event file for no body, a blank body, both body flags, or an unknown item."""
        before = len(self.world.events(self.item))
        path = self.world.base / "note.txt"
        path.write_text("text")
        for arguments in ((self.item,), (self.item, "--body", "  \n "), (self.item, "--body", "x", "--body-file", str(path)), ("nothing", "--body", "x")):
            result = self.world.run("comment", *arguments)
            self.assertEqual(result.returncode, 2, arguments)
        self.assertEqual(len(self.world.events(self.item)), before)

    def test_comment_text_that_looks_like_a_state_change_stays_data(self) -> None:
        """A claim-looking marker or HTML comment is stored verbatim and changes nothing."""
        text = "<!-- qew:claim mallory -->\nclaimed by mallory\nreleased\n"
        self.world.run("comment", self.item, "--body", text)
        data = json.loads(self.world.run("show", self.item, "--json").stdout)
        self.assertEqual((data["state"], data.get("owner")), ("approved", None))
        self.assertEqual(data["comments"][0]["text"], text)
        self.assertEqual(self.world.run("check").returncode, 0)

    def test_check_refuses_a_comment_event_without_text(self) -> None:
        """A hand-merged commented event with no text is a ledger error, not an empty comment."""
        event = self.world.ledger / "events" / self.item / "ZZZZZZZZZZZZZZZZZZZZZZZZZZ.json"
        event.write_text(json.dumps({"type": "commented", "at": "2026-01-01T00:00:00Z", "actor": "x", "author": "x"}))
        result = self.world.run("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("text", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
