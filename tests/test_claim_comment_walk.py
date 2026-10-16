"""Claim and release comments on the primary forge show a visible line, never an empty card.

Adversary: a comment that is only an HTML comment renders on GitHub as "No description provided.", flooding an
issue with blank cards; the marker must stay machine-readable while a human sees who claimed or released what.
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import ledger_config
from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

OFF = "\n[replication]\nauto_forge_sync = false\nauto_push = false\nforge_concurrency = 1\n"
ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}


def visible(body: str) -> str:
    return re.sub(r"<!--.*?-->", "", body, flags=re.S).strip()


class ClaimCommentWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub().start()
        self.addCleanup(self.fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'
        self.world = World(mirrors, replication=OFF)
        self.addCleanup(self.world.close)
        slug = ledger_config.mirror_slug(ledger_config.load(self.world.code).mirrors[0])
        config = self.world.code / ".qew" / "config.toml"
        config.write_text(config.read_text() + f'\n[authority]\nprimary = "{slug}"\n', encoding="utf-8")
        self.item = self.world.create("--kind", "issue", "--title", "Work", "--quote", QUOTE)
        self.assertEqual(self.world.run("sync", env=ENV).returncode, 0)

    def comments(self) -> list[str]:
        return [c["body"] for issue in self.fake.issues.values() for c in issue["discussion"]]

    def test_claim_and_release_comments_carry_visible_text_and_keep_the_marker(self) -> None:
        """Prevents blank 'No description provided.' cards: each marker comment names the owner, item and action in plain text."""
        self.assertEqual(self.world.run("start", self.item, env=ENV).returncode, 0)
        self.assertEqual(self.world.run("release", self.item, "--reason", "pause", env=ENV).returncode, 0)
        claim, release = [b for b in self.comments() if "<!-- qew:" in b]
        self.assertIn(f"<!-- qew:claim {self.item} tester", claim)
        self.assertIn(f"<!-- qew:release {self.item} -->", release)
        self.assertIn("tester", visible(claim))
        self.assertIn(self.item, visible(claim))
        self.assertIn(self.item, visible(release))

    def test_a_rival_is_still_refused_while_the_claim_carries_visible_text(self) -> None:
        """Prevents the visible line breaking arbitration: the marker alone decides the holder."""
        self.assertEqual(self.world.run("start", self.item, env=ENV).returncode, 0)
        rival = self.world.run("start", self.item, env={**ENV, "QEW_ACTOR": "rival"})
        self.assertNotEqual(rival.returncode, 0, rival.stdout + rival.stderr)

    def test_a_pull_keeps_arbitration_markers_out_of_the_ledger_but_imports_a_human_comment(self) -> None:
        """Prevents claim and release markers cluttering `qew show` as discussion while a real comment still comes in."""
        self.assertEqual(self.world.run("start", self.item, env=ENV).returncode, 0)
        self.assertEqual(self.world.run("release", self.item, "--reason", "pause", env=ENV).returncode, 0)
        self.fake.add_comment(1, "bob", "a human remark")
        self.assertEqual(self.world.run("sync", env=ENV).returncode, 0)
        texts = [e["text"] for e in self.world.events(self.item) if e["type"] == "commented"]
        self.assertEqual(texts, ["a human remark"])


if __name__ == "__main__":
    unittest.main()
