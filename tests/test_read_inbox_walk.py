"""Walks of `qew inbox`: replies, drafts waiting for a human and assigned work, from the ledger alone.

Adversary: a person reading whole threads, own or bot or machine comments flooding the inbox, a wrong
window silently dropping replies, a login smuggling a query qualifier, terminal escapes in an excerpt,
and a partial or tail-sliced scan hiding a reply.
"""

from datetime import datetime, timedelta, UTC
import json
import unittest

from tests.ledger_harness import World

SINCE = "2026-10-01T00:00:00Z"
IN = "2026-10-03T12:00:00Z"


def iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


class InboxBase(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)
        self.serial = 0

    def make(self, title: str, actor: str = "alice", approve: bool = True, *extra: str) -> str:
        result = self.world.run("create", "--title", title, *extra, "--draft", env={"QEW_ACTOR": actor})
        self.assertEqual(result.returncode, 0, result.stderr)
        item = result.stdout.split()[0]
        if approve:
            self.world.approve(item)
        return item

    def comment(self, item: str, author: str, at: str, text: str = "hello", actor: str = "") -> None:
        self.serial += 1
        path = self.world.home("events", item) / f"Z{self.serial:06d}.json"
        path.write_text(json.dumps({"type": "commented", "at": at, "actor": actor or author, "author": author, "text": text}))

    def inbox(self, *arguments: str, login: str = "alice"):
        return self.world.run("inbox", "--login", login, "--since", SINCE, *arguments)

    def rows(self, *arguments: str, login: str = "alice") -> list:
        result = self.inbox("--json", *arguments, login=login)
        self.assertEqual(result.returncode, 0, result.stderr)
        return [json.loads(line) for line in result.stdout.splitlines()]


class InboxSectionsWalk(InboxBase):
    def test_replies_waiting_and_assigned_come_in_three_sections_with_cut_excerpts(self) -> None:
        """Replies by others, a waiting draft and two claims: fixed headings with counts, one-line 100-character excerpts."""
        mine = self.make("My issue")
        self.comment(mine, "bob", IN, "x" * 150 + "\nsecond line is never shown")
        self.comment(mine, "carol", "2026-10-04T08:00:00Z", "short reply")
        draft = self.make("Waiting draft", approve=False)
        held = [self.make(f"Held {n}") for n in range(2)]
        for item in held:
            self.assertEqual(self.world.run("start", item, env={"QEW_ACTOR": "alice"}).returncode, 0)
        text = self.inbox().stdout
        lines = text.splitlines()
        self.assertEqual(lines[0], f"replies (2) since {SINCE}")
        self.assertEqual(lines[1], f"  {mine} @bob {IN}: " + "x" * 99 + "…")
        self.assertEqual(lines[2], f"  {mine} @carol 2026-10-04T08:00:00Z: short reply")
        self.assertEqual(lines[3], "waiting for a human (1)")
        self.assertTrue(lines[4].startswith(f"  {draft} draft "))
        self.assertEqual(lines[5], "assigned (2)")
        self.assertNotIn("second line", text)
        by_section = {}
        for row in self.rows():
            by_section.setdefault(row["section"], []).append(row)
            self.assertEqual(row["schema_version"], 1)
        self.assertEqual({k: len(v) for k, v in by_section.items()}, {"replies": 2, "waiting": 1, "assigned": 2})
        self.assertEqual(by_section["replies"][0]["excerpt"], "x" * 99 + "…")

    def test_count_is_one_line_and_omits_the_list_without_replies(self) -> None:
        """The session-hook line names reply items with their last author; no replies, no list."""
        mine = self.make("My issue")
        self.make("Waiting", approve=False)
        self.assertEqual(self.inbox("--count").stdout.strip(), f"qew inbox (alice): 0 issue(s) with replies since {SINCE}; 1 waiting for a human; 0 assigned.")
        self.comment(mine, "bob", IN)
        self.comment(mine, "carol", "2026-10-04T08:00:00Z")
        self.assertEqual(
            self.inbox("--count").stdout.strip(),
            f"qew inbox (alice): 1 issue(s) with replies since {SINCE}: {mine} (carol); 1 waiting for a human; 0 assigned.",
        )

    def test_the_session_start_hook_carries_the_count_line_within_its_bound(self) -> None:
        """Replies for the configured actor reach the hook context without breaking its 2000-byte bound."""
        mine = self.make("Mine", "tester")
        now = datetime.now(UTC)
        self.comment(mine, "bob", iso(now - timedelta(hours=1)))
        for n in range(30):
            self.make(f"Long title {n} " + "word " * 40, "tester")
        result = self.world.run("hook", "session-start", "--agent", "claude", stdin="{}")
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("qew inbox (tester): 1 issue(s) with replies", context)
        self.assertLessEqual(len(context.encode("utf-8")), 2000)


class ReplyFilterWalk(InboxBase):
    def test_own_bot_marker_and_old_comments_are_not_replies_but_the_window_start_is(self) -> None:
        """Own comments (any case), bots, claim markers and a comment one second early yield nothing; one exactly at the start counts."""
        mine = self.make("My issue")
        self.comment(mine, "Alice", IN)
        self.comment(mine, "ci[bot]", IN)
        self.comment(mine, "dependabot[BOT]", IN)
        self.comment(mine, "bob", IN, "<!-- qew:claim bob -->")
        self.comment(mine, "bob", "2026-09-30T23:59:59Z")
        self.assertEqual([r for r in self.rows() if r["section"] == "replies"], [])
        self.comment(mine, "bob", SINCE, "at the start")
        (reply,) = [r for r in self.rows() if r["section"] == "replies"]
        self.assertEqual((reply["author"], reply["at"], reply["excerpt"]), ("bob", SINCE, "at the start"))

    def test_escape_sequences_and_newlines_never_reach_the_terminal(self) -> None:
        """Control characters in an excerpt print as spaces."""
        mine = self.make("My issue")
        self.comment(mine, "bob", IN, "red\x1b[31mALERT\x07 end\nnext line")
        text = self.inbox().stdout
        self.assertNotIn("\x1b", text)
        self.assertNotIn("\x07", text)
        self.assertIn("red [31mALERT  end", text)
        self.assertNotIn("next line", text)


class InboxArgumentWalk(InboxBase):
    def test_malformed_since_values_are_usage_errors_before_any_read(self) -> None:
        """Five wrong windows exit 2 with nothing printed, even over an unreadable ledger."""
        self.make("Fine")
        (self.world.home("items", "BROKEN") / "item.md").parent.mkdir(exist_ok=True)
        (self.world.home("items", "BROKEN") / "item.md").write_text("not a header")
        for value in ("yesterday", "2026-13-40", "2026-10-07T10:00:00+02:00", "2026-10-07 10:00:00", ""):
            result = self.world.run("inbox", "--since", value)
            self.assertEqual((result.returncode, result.stdout), (2, ""), value)

    def test_a_date_is_midnight_utc_and_the_header_says_so(self) -> None:
        """No timezone ambiguity."""
        self.make("Fine")
        self.assertIn("since 2026-10-07T00:00:00Z", self.world.run("inbox", "--since", "2026-10-07", "--count").stdout)

    def test_defaults_are_the_last_seven_days_for_the_configured_actor(self) -> None:
        """No flags: tester's inbox, 6 days of replies in, 8 days out."""
        mine = self.make("Mine", "tester")
        now = datetime.now(UTC)
        self.comment(mine, "bob", iso(now - timedelta(days=6)), "recent")
        self.comment(mine, "bob", iso(now - timedelta(days=8)), "stale")
        out = self.world.run("inbox").stdout
        self.assertIn("recent", out)
        self.assertNotIn("stale", out)

    def test_a_login_cannot_carry_a_query_qualifier(self) -> None:
        """Whitespace, colons and quotes are refused before any read."""
        for login in ("alice label:secret", "a:b", "x'y", "a b"):
            result = self.world.run("inbox", "--login", login)
            self.assertEqual((result.returncode, result.stdout), (2, ""), login)


class InboxIntegrityWalk(InboxBase):
    def test_corrupt_data_fails_with_empty_stdout(self) -> None:
        """A bad comment time, a comment without text, an item without a title and a truncated event file are refused."""
        mine = self.make("My issue")
        events = self.world.home("events", mine)
        broken = {
            "time": json.dumps({"type": "commented", "at": "soon", "actor": "bob", "author": "bob", "text": "x"}),
            "text": json.dumps({"type": "commented", "at": IN, "actor": "bob", "author": "bob"}),
            "truncated": '{"type": "commented", "at": "' ,
        }
        for name, content in broken.items():
            (events / "Z999999.json").write_text(content)
            result = self.inbox()
            self.assertEqual((result.returncode, result.stdout), (1, ""), name)
        (events / "Z999999.json").unlink()
        path = self.world.home("items", f"{mine}") / "item.md"
        original = path.read_text()
        path.write_text("\n".join(line for line in original.split("\n") if not line.startswith("title:")))
        result = self.inbox()
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        path.write_text(original)
        self.assertEqual(self.inbox().returncode, 0)

    def test_the_one_reply_among_two_thousand_events_is_found(self) -> None:
        """No tail slice: an item with 2000 events and a single in-window reply in the middle."""
        mine = self.make("Busy issue")
        for n in range(2000):
            at = IN if n == 1500 else "2026-01-01T00:00:00Z"
            self.comment(mine, "bob", at, f"note {n}")
        (reply,) = [r for r in self.rows() if r["section"] == "replies"]
        self.assertEqual(reply["excerpt"], "note 1500")


if __name__ == "__main__":
    unittest.main()
