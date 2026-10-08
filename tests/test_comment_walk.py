"""Walks of `qew comment`: discussion that never changes state.

Adversary: needing a state change to discuss a draft, a body silently altered or dropped, and a frozen
audit trail once work is finished.
"""

import unittest

from tests.ledger_harness import World


class CommentWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_a_comment_on_a_draft_appends_an_event_and_leaves_it_a_draft(self) -> None:
        """Drafts exist to be discussed; discussing must not release or alter them."""
        w = self.world
        draft = w.create("--title", "Maybe")
        result = w.run("comment", draft, "--body", "Should this wait for the importer?")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"commented {draft}")
        self.assertEqual([e["type"] for e in w.events(draft)], ["drafted", "comment"])
        self.assertEqual(w.events(draft)[1]["body"], "Should this wait for the importer?")
        shown = w.run("show", draft).stdout
        self.assertIn("state: draft", shown)
        self.assertIn("comment: tester", shown)
        self.assertIn("Should this wait", shown)

    def test_body_file_and_stdin_store_the_text_verbatim(self) -> None:
        """A quietly altered body (trimmed, re-wrapped) would misquote the discussion."""
        w = self.world
        draft = w.create("--title", "Maybe")
        text = "  line one\n\n  line two with $(not run) and `ticks`\n"
        path = w.base / "note.txt"
        path.write_text(text)
        self.assertEqual(w.run("comment", draft, "--body-file", str(path)).returncode, 0)
        self.assertEqual(w.run("comment", draft, "--body-file", "-", stdin=text).returncode, 0)
        self.assertEqual([e["body"] for e in w.events(draft)[1:]], [text, text])

    def test_missing_blank_or_double_body_is_refused_with_nothing_written(self) -> None:
        """A comment that silently drops its body is worse than none."""
        w = self.world
        draft = w.create("--title", "Maybe")
        path = w.base / "note.txt"
        path.write_text("text")
        for arguments in ((), ("--body", "  "), ("--body", "a", "--body-file", str(path)), ("--body-file", "-")):
            result = w.run("comment", draft, *arguments, stdin="")
            self.assertEqual(result.returncode, 2, arguments)
        self.assertEqual([e["type"] for e in w.events(draft)], ["drafted"])

    def test_completed_and_closed_items_can_still_be_discussed_and_check_passes(self) -> None:
        """The audit trail must not freeze when the work is done."""
        w = self.world
        done = w.create("--title", "Done", "--quote", "Alexander: 'please track this work in the Qew ledger now'")
        w.run("start", done)
        w.run("evidence", done, "--change", w.commit())
        self.assertEqual(w.run("complete", done).returncode, 0)
        shut = w.create("--title", "Shut")
        w.run("close", shut, "--reason", "duplicate")
        for item in (done, shut):
            self.assertEqual(w.run("comment", item, "--body", "late note").returncode, 0)
        self.assertEqual(w.run("check").returncode, 0)


if __name__ == "__main__":
    unittest.main()
