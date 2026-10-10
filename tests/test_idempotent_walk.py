"""A retried command (after a crash, a timeout or an agent's own retry) never duplicates anything."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World

OTHER_QUOTE = "Alexander: 'a different release that is not a replay'"


def types(world: World, item: str) -> list:
    return [event["type"] for event in world.events(item)]


class IdempotentCreateWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = World(ledger_vcs="git")
        self.addCleanup(self.w.close)

    def test_the_same_key_returns_the_same_item_with_the_same_line(self) -> None:
        """Prevents a retried `create` filing the item twice: a repeat with the key returns the first item, byte for byte."""
        first = self.w.run("create", "--title", "Parse headers", "--key", "task-17", "--quote", QUOTE)
        again = self.w.run("create", "--title", "Parse headers", "--key", "task-17", "--quote", QUOTE)
        self.assertEqual((first.returncode, again.returncode), (0, 0), first.stderr + again.stderr)
        self.assertEqual(first.stdout, again.stdout)
        ids = [path.parent.name for path in (self.w.ledger / "items").glob("*/item.md")]
        self.assertEqual(len(ids), 1)
        self.assertEqual(types(self.w, ids[0]), ["approved"])

    def test_the_same_key_with_another_title_is_refused_naming_the_first_item(self) -> None:
        """Prevents a key silently pointing at different work: a changed title under a used key is refused (exit 2)."""
        first = self.w.run("create", "--title", "Parse headers", "--key", "task-17", "--quote", QUOTE)
        item = first.stdout.split()[0]
        other = self.w.run("create", "--title", "Something else", "--key", "task-17", "--quote", QUOTE)
        self.assertEqual(other.returncode, 2)
        self.assertIn(item, other.stderr)
        self.assertIn("task-17", other.stderr)
        self.assertEqual(len(list((self.w.ledger / "items").glob("*/item.md"))), 1)

    def test_different_keys_and_no_key_stay_independent(self) -> None:
        """Prevents the key colliding unrelated items: another key, or none, files a new item."""
        for args in (("--key", "a"), ("--key", "b"), ()):
            self.assertEqual(self.w.run("create", "--title", "Same title", *args, "--quote", QUOTE).returncode, 0)
        self.assertEqual(len(list((self.w.ledger / "items").glob("*/item.md"))), 3)

    def test_a_key_must_be_one_word_and_cannot_come_with_a_plan_file(self) -> None:
        """Prevents ambiguous keys: whitespace, a leading @ and --from-file (which has its own refs) are refused."""
        for key in ("two words", "@ref", ""):
            self.assertEqual(self.w.run("create", "--title", "x", "--key", key, "--quote", QUOTE).returncode, 2, key)
        plan = self.w.base / "plan.jsonl"
        plan.write_text('{"title": "A"}\n', encoding="utf-8")
        refused = self.w.run("create", "--from-file", str(plan), "--key", "k", "--quote", QUOTE)
        self.assertEqual(refused.returncode, 2)

    def test_a_keyed_create_is_replayable_as_a_draft_too(self) -> None:
        """Prevents drafts being exempt: the replay of a keyed draft returns the same draft."""
        one = self.w.run("create", "--title", "Idea", "--key", "idea-1", "--draft")
        two = self.w.run("create", "--title", "Idea", "--key", "idea-1", "--draft")
        self.assertEqual((one.returncode, two.returncode, one.stdout), (0, 0, two.stdout))
        self.assertEqual(len(list((self.w.ledger / "items").glob("*/item.md"))), 1)


class IdempotentVerbWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = World(ledger_vcs="git")
        self.addCleanup(self.w.close)
        self.item = self.w.create("--title", "Work", "--quote", QUOTE)

    def test_evidence_for_the_same_change_is_recorded_once(self) -> None:
        """Prevents a retried `evidence` appending a second identical fact."""
        self.w.run("start", self.item)
        change = self.w.commit("the work")
        first = self.w.run("evidence", self.item, "--change", change)
        again = self.w.run("evidence", self.item, "--change", change)
        self.assertEqual((first.returncode, again.returncode), (0, 0), again.stderr)
        self.assertIn("unchanged", again.stdout)
        self.assertEqual(types(self.w, self.item).count("evidence"), 1)
        newer = self.w.commit("more work")
        self.assertEqual(self.w.run("evidence", self.item, "--change", newer).returncode, 0)
        self.assertEqual(types(self.w, self.item).count("evidence"), 2)

    def test_a_keyed_comment_is_posted_once_and_a_reused_key_with_other_text_is_refused(self) -> None:
        """Prevents a retried `comment` posting twice; unkeyed comments still append every time."""
        one = self.w.run("comment", self.item, "--body", "hello", "--key", "c1")
        two = self.w.run("comment", self.item, "--body", "hello", "--key", "c1")
        self.assertEqual((one.returncode, two.returncode), (0, 0), two.stderr)
        self.assertIn("unchanged", two.stdout)
        self.assertEqual(types(self.w, self.item).count("commented"), 1)
        clash = self.w.run("comment", self.item, "--body", "different", "--key", "c1")
        self.assertEqual(clash.returncode, 2)
        self.w.run("comment", self.item, "--body", "plain")
        self.w.run("comment", self.item, "--body", "plain")
        self.assertEqual(types(self.w, self.item).count("commented"), 3)

    def test_approving_with_the_same_quote_again_is_unchanged_but_another_quote_is_refused(self) -> None:
        """Prevents a retried approval erroring after it had worked; a different release of live work stays a loud refusal."""
        draft = self.w.create("--title", "Idea", "--draft")
        self.assertEqual(self.w.run("draft", "approve", draft, "--quote", QUOTE).returncode, 0)
        again = self.w.run("draft", "approve", draft, "--quote", QUOTE)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("unchanged", again.stdout)
        self.assertEqual(types(self.w, draft), ["drafted", "approved"])
        other = self.w.run("draft", "approve", draft, "--quote", OTHER_QUOTE)
        self.assertEqual(other.returncode, 2)
        self.assertIn("nothing to approve", other.stderr)

    def test_release_and_close_repeat_as_unchanged_by_the_same_actor(self) -> None:
        """Prevents a retried `release` or `close` failing after it had worked."""
        self.w.run("start", self.item)
        self.assertEqual(self.w.run("release", self.item, "--reason", "stuck").returncode, 0)
        again = self.w.run("release", self.item, "--reason", "stuck")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("unchanged", again.stdout)
        self.assertEqual(types(self.w, self.item).count("released"), 1)
        self.assertEqual(self.w.run("close", self.item, "--reason", "not_planned").returncode, 0)
        closed = self.w.run("close", self.item, "--reason", "not_planned")
        self.assertEqual(closed.returncode, 0, closed.stderr)
        self.assertIn("unchanged", closed.stdout)
        self.assertEqual(types(self.w, self.item).count("closed"), 1)
        reason = self.w.run("close", self.item, "--reason", "superseded", "--of", self.item)
        self.assertEqual(reason.returncode, 2)

    def test_start_and_complete_repeat_with_their_documented_idempotent_answers(self) -> None:
        """Prevents the contract drifting: a repeat `start` is exit 5 (nothing written), a repeat `complete` exit 0 unchanged."""
        self.assertEqual(self.w.run("start", self.item).returncode, 0)
        self.assertEqual(self.w.run("start", self.item).returncode, 5)
        self.w.run("evidence", self.item, "--change", self.w.commit("x"))
        self.assertEqual(self.w.run("complete", self.item).returncode, 0)
        again = self.w.run("complete", self.item)
        self.assertEqual(again.returncode, 0)
        self.assertIn("unchanged", again.stdout)
        self.assertEqual(types(self.w, self.item).count("completed"), 1)

    def test_json_replays_carry_the_same_ids(self) -> None:
        """Prevents machine callers seeing a different shape on replay."""
        one = json.loads(self.w.run("create", "--title", "J", "--key", "j1", "--quote", QUOTE, "--json").stdout)
        two = json.loads(self.w.run("create", "--title", "J", "--key", "j1", "--quote", QUOTE, "--json").stdout)
        self.assertEqual(one["created"], two["created"])


if __name__ == "__main__":
    unittest.main()
