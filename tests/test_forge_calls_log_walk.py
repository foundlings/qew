"""Where the forge calls go: every qew command logs how many core REST and GraphQL calls it made, and `qew doctor` adds them up by verb.

Adversary: the shared token's hour of core calls used up with nobody able to say how much of it was qew's or which verb spent it.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

OFF = "\n[replication]\nauto_forge_sync = false\nauto_push = false\nforge_concurrency = 1\ndivergences = \"report\"\n"
ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}


class ForgeCallsLogWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub().start()
        self.addCleanup(self.fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'
        self.world = World(mirrors, replication=OFF)
        self.addCleanup(self.world.close)
        self.log = self.world.ledger / ".qew" / "forge-calls.log"

    def lines(self) -> list[dict]:
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()] if self.log.exists() else []

    def test_a_command_that_calls_the_forge_logs_its_calls_by_resource_and_verb(self) -> None:
        """Prevents the forge budget being unaccountable: the push's core calls are on record under its verb."""
        self.world.create("--title", "Only", "--quote", QUOTE)
        before = len(self.fake.requests)
        self.assertEqual(self.world.run("push", env=ENV).returncode, 0)
        made = len(self.fake.requests) - before
        found = [line for line in self.lines() if line["verb"] == "push"]
        self.assertEqual(len(found), 1, self.lines())
        self.assertEqual(sum(found[0]["calls"].values()), made)
        self.assertGreater(found[0]["calls"].get("core", 0), 0)

    def test_a_command_that_calls_no_forge_writes_no_line(self) -> None:
        """Prevents the log filling with every local read."""
        self.world.create("--title", "Only", "--quote", QUOTE)
        self.world.run("list")
        self.assertEqual(self.lines(), [])

    def test_doctor_adds_the_last_hour_up_by_verb(self) -> None:
        """Prevents the question `who spent the hour` having no answer: doctor names the totals and the verbs."""
        self.world.create("--title", "Only", "--quote", QUOTE)
        self.world.run("push", env=ENV)
        out = self.world.run("doctor").stdout
        self.assertIn("forge calls by qew in the last hour", out)
        self.assertIn("push", out)


if __name__ == "__main__":
    unittest.main()
