"""Editing and filing items: `set` options, authorship, `create --draft`, comments, discovery, bodies.

Adversary: an unaudited edit, a corrupted header, forged authorship, a draft silently approved, junk or
instructions smuggled in through a comment or body, and a lost discussion.
"""

import json
import os
import stat
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World
from tests.lifecycle_support import approved, event_types, tree


class SetOptionsWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_one_set_changes_title_body_priority_kind_labels_and_parent_with_one_event_each(self) -> None:
        """Prevents edits that are not audited: every changed key leaves its own field event."""
        w = self.world
        old_epic = w.create("--kind", "epic", "--title", "Old")
        new_epic = w.create("--kind", "epic", "--title", "New")
        item = w.create("--kind", "issue", "--title", "Item", "--parent", old_epic, "--label", "alpha", "--label", "beta")
        body = w.base / "body.md"
        body.write_text("New body\n")
        result = w.run("set", item, "--title", "Renamed", "--body-file", str(body), "--priority", "1", "--kind", "task",
                       "--add-label", "gamma", "--remove-label", "alpha", "--parent", new_epic)
        self.assertEqual(result.returncode, 0, result.stderr)
        shown = json.loads(w.run("show", item, "--json").stdout)
        self.assertEqual((shown["title"], shown["priority"], shown["kind"], shown["parent"], shown["body"].strip()),
                         ("Renamed", 1, "task", new_epic, "New body"))
        self.assertEqual(sorted(shown["labels"]), ["beta", "gamma"])
        keys = sorted(e["key"] for e in w.events(item) if e["type"] == "field")
        self.assertEqual(keys, ["body", "kind", "labels", "parent", "priority", "title"])
        again = w.run("set", item, "--title", "Renamed", "--priority", "1")
        self.assertIn("unchanged", again.stdout)

    def test_no_parent_removes_the_parent(self) -> None:
        """Prevents an item that can never leave its plan."""
        w = self.world
        epic = w.create("--kind", "epic", "--title", "E")
        item = w.create("--kind", "issue", "--title", "I", "--parent", epic)
        self.assertEqual(w.run("set", item, "--no-parent").returncode, 0)
        self.assertEqual(json.loads(w.run("show", item, "--json").stdout)["parent"], "")

    def test_values_that_would_corrupt_the_header_are_refused_and_nothing_changes(self) -> None:
        """Prevents corrupting the fixed header format: blank or multi-line titles, an empty or symlinked body file."""
        w = self.world
        item = w.create("--kind", "issue", "--title", "Item", "--body", "keep me")
        empty = w.base / "empty.md"
        empty.write_text("  \n")
        real = w.base / "real.md"
        real.write_text("text\n")
        link = w.base / "link.md"
        link.symlink_to(real)
        before = tree(w)
        for arguments in (["--title", "  "], ["--title", "one\ntwo"], ["--body-file", str(empty)], ["--body-file", str(link)],
                          ["--add-label", "qew:approved"], ["--add-label", "has space"], ["--no-parent", "--parent", item]):
            result = w.run("set", item, *arguments)
            self.assertEqual(result.returncode, 2, (arguments, result.stderr))
        self.assertEqual(tree(w), before)


class AuthorshipWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_an_actor_that_cannot_form_a_label_is_refused_at_the_first_write(self) -> None:
        """Prevents malformed text spliced into a mirror label name."""
        w = self.world
        before = tree(w)
        for actor in ("bad login,x", "two words", "a,b"):
            result = w.run("create", "--title", "Work", env={"QEW_ACTOR": actor})
            self.assertEqual(result.returncode, 2, (actor, result.stderr))
        self.assertEqual(tree(w), before)

    def test_show_prints_the_author_from_the_first_event_not_the_claimant(self) -> None:
        """Prevents the author being read as the claim."""
        w = self.world
        result = w.run("create", "--title", "Work", "--quote", QUOTE, env={"QEW_ACTOR": "alice"})
        item = result.stdout.split()[0]
        w.run("start", item, env={"QEW_ACTOR": "bob"})
        shown = w.run("show", item).stdout
        self.assertIn("author: alice", shown)
        self.assertIn("owner: bob", shown)
        self.assertEqual(json.loads(w.run("show", item, "--json").stdout)["author"], "alice")


class DraftFlagWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_create_draft_files_a_draft_and_never_an_approval(self) -> None:
        """Prevents a draft treated as live work."""
        w = self.world
        epic = w.create("--kind", "epic", "--title", "Plan", "--draft")
        child = w.create("--title", "Step", "--draft", "--parent", epic)
        self.assertEqual(event_types(w, epic), ["drafted"])
        self.assertEqual(event_types(w, child), ["drafted"])
        self.assertEqual(w.run("ready").stdout, "")
        self.assertEqual(w.run("start", child).returncode, 2)

    def test_draft_together_with_a_quote_is_a_usage_error_naming_both_flags(self) -> None:
        """Prevents contradictory flags silently approving."""
        w = self.world
        before = tree(w)
        result = w.run("create", "--title", "Both", "--draft", "--quote", QUOTE)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("--draft", result.stderr)
        self.assertIn("--quote", result.stderr)
        self.assertEqual(tree(w), before)

    def test_a_negative_priority_is_a_usage_error_not_an_invalid_ledger(self) -> None:
        """Prevents a typo reading as a corrupt ledger (exit 1) instead of a refusal."""
        w = self.world
        before = tree(w)
        self.assertEqual(w.run("create", "--title", "X", "--priority", "-3").returncode, 2)
        self.assertEqual(tree(w), before)


class CommentWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_a_comment_on_a_draft_is_listed_by_show_with_actor_and_time(self) -> None:
        """Prevents draft discussion getting lost in chat."""
        w = self.world
        item = w.create("--title", "Maybe", "--draft")
        result = w.run("comment", item, "--body", "Why not start with the parser?", env={"QEW_ACTOR": "alice"})
        self.assertEqual(result.returncode, 0, result.stderr)
        shown = w.run("show", item).stdout
        self.assertIn("alice", shown)
        self.assertIn("Why not start with the parser?", shown)
        comments = json.loads(w.run("show", item, "--json").stdout).get("comments", [])
        self.assertEqual([(c["actor"], c["text"]) for c in comments], [("alice", "Why not start with the parser?")])

    def test_invalid_comment_input_is_refused_without_an_event(self) -> None:
        """Prevents junk and path tricks: neither or both sources, blank, symlinked, oversize."""
        w = self.world
        item = w.create("--title", "Maybe")
        real = w.base / "c.md"
        real.write_text("text")
        link = w.base / "l.md"
        link.symlink_to(real)
        before = tree(w)
        for arguments in ([], ["--body", "x", "--body-file", str(real)], ["--body", "  "], ["--body-file", str(link)],
                          ["--body", "x" * 20_001]):
            self.assertEqual(w.run("comment", item, *arguments).returncode, 2, arguments[:1])
        self.assertEqual(tree(w), before)

    def test_text_that_looks_like_a_command_or_an_event_is_stored_verbatim_and_never_run(self) -> None:
        """Prevents treating a comment as an instruction."""
        w = self.world
        item = w.create("--title", "Maybe")
        marker = w.base / "marker"
        text = f'{{"type": "completed"}} $(touch {marker}) `touch {marker}`'
        self.assertEqual(w.run("comment", item, "--body", text).returncode, 0)
        self.assertFalse(marker.exists())
        self.assertEqual(w.events(item)[-1]["text"], text)
        self.assertIn("state: draft", w.run("show", item).stdout)
        self.assertEqual(w.run("check").returncode, 0)

    def test_two_branches_each_adding_a_comment_keep_both(self) -> None:
        """Prevents lost discussion: each comment is its own event file."""
        every_time = "\n[replication]\nfetch_interval_s = 0\n"
        a = World(ledger_vcs="git", remote=True, replication=every_time)
        self.addCleanup(a.close)
        b = a.clone("b", replication=every_time)
        item = a.create("--title", "Shared")
        b.run("list")
        offline = {"QEW_OFFLINE": "1"}
        a.run("comment", item, "--body", "from a", env=offline)
        b.run("comment", item, "--body", "from b", env=offline)
        for agent in (a, b, a):
            agent.run("ready")
        texts = sorted(c["text"] for c in json.loads(a.run("show", item, "--json").stdout).get("comments", []))
        self.assertEqual(texts, ["from a", "from b"])


class DiscoveredFromWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_an_item_remembers_what_it_was_discovered_from_without_blocking_on_it(self) -> None:
        """Prevents provenance lost in free text, and the origin being mistaken for a dependency."""
        w = self.world
        origin = approved(w, title="Origin")
        found = w.create("--title", "Found", "--discovered-from", origin, "--quote", QUOTE)
        self.assertIn(f"discovered-from: {origin}", w.run("show", found).stdout)
        self.assertIn(f"discovered: {found}", w.run("show", origin).stdout)
        self.assertIn(found, w.run("ready").stdout)

    def test_an_unknown_origin_is_refused_before_any_file(self) -> None:
        """Prevents a dangling provenance link."""
        w = self.world
        before = tree(w)
        self.assertEqual(w.run("create", "--title", "X", "--discovered-from", "nosuchitem").returncode, 2)
        self.assertEqual(tree(w), before)


class BodyWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_structured_flags_compose_a_sectioned_body(self) -> None:
        """Prevents free-form bodies that omit what done looks like."""
        w = self.world
        item = w.create("--title", "Parse", "--where", "src/parse.py", "--problem", "headers are dropped",
                        "--fix", "keep them", "--done-when", "a header test passes")
        body = json.loads(w.run("show", item, "--json").stdout)["body"]
        for heading, text in (("Where", "src/parse.py"), ("Problem", "headers are dropped"), ("Fix", "keep them"),
                              ("Done when", "a header test passes")):
            self.assertRegex(body, rf"## {heading}\n+{text}")

    def test_ambiguous_framing_or_a_body_beside_structured_flags_writes_nothing(self) -> None:
        """Prevents a body that silently drops one of the inputs."""
        w = self.world
        before = tree(w)
        for arguments in (["--problem", "p", "--goal", "g"], ["--fix", "f", "--approach", "a"],
                          ["--body", "free", "--where", "x"], ["--body-file", "-", "--done-when", "x"]):
            self.assertEqual(w.run("create", "--title", "X", *arguments, stdin="text").returncode, 2, arguments)
        self.assertEqual(tree(w), before)

    def test_the_editor_runs_as_argv_never_through_a_shell(self) -> None:
        """Prevents shell injection through EDITOR."""
        w = self.world
        script = w.base / "editor.py"
        script.write_text("import sys\nopen(sys.argv[-1], 'w').write('edited body\\n')\n")
        marker = w.base / "marker"
        editor = f"python3 {script} ; touch {marker}"
        result = w.run("create", "--title", "Edited", "--edit", env={"EDITOR": editor})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists())
        item = result.stdout.split()[0]
        self.assertEqual(json.loads(w.run("show", item, "--json").stdout)["body"].strip(), "edited body")


class CodeShapedTextWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_unmarked_commands_flags_and_paths_warn_once_each_and_the_body_is_untouched(self) -> None:
        """Prevents later renderers or agents mistaking prose for commands, without altering the body."""
        w = self.world
        text = "Run qew ready --limit 5 then open ./plans/work.jsonl"
        result = w.run("create", "--title", "Warn", "--body", text)
        self.assertEqual(result.returncode, 0, result.stderr)
        for token in ("qew ready", "--limit", "./plans/work.jsonl"):
            self.assertEqual(result.stderr.count(f"warning: unmarked code-shaped text: {token}"), 1, result.stderr)
        item = result.stdout.split()[0]
        self.assertEqual(json.loads(w.run("show", item, "--json").stdout)["body"].strip(), text)

    def test_marked_text_does_not_warn_and_comments_and_set_warn_too(self) -> None:
        """Prevents noise that trains people to ignore the hint."""
        w = self.world
        marked = "Run `qew ready --limit 5` and\n```\n./plans/work.jsonl\n```\n"
        quiet = w.run("create", "--title", "Quiet", "--body", marked)
        self.assertNotIn("warning", quiet.stderr)
        item = quiet.stdout.split()[0]
        self.assertIn("unmarked code-shaped text", w.run("comment", item, "--body", "try --force now").stderr)
        self.assertIn("unmarked code-shaped text", w.run("set", item, "--body", "see ./a/b.py").stderr)


if __name__ == "__main__":
    unittest.main()
