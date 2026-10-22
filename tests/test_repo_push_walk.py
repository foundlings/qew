"""Sync pushes the code repo and the ledger repo to every forge target, so any forge can become primary.

Each test is a scenario; its docstring names the failure it prevents.
"""

import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_forgejo import TOKEN, FakeForgejo
from tests.ledger_harness import QUOTE, World, git

ENV = {"FORGEJO_TOKEN": TOKEN}
QUIET = "\n[replication]\nauto_forge_sync = false\nauto_push = false\n"


def head(repo: str, ref: str = "refs/heads/main") -> str:
    """The commit a repo holds at ref, or empty when it holds none."""
    done = subprocess.run(["git", "-C", repo, "rev-parse", "--verify", "-q", ref], capture_output=True, text=True)
    return done.stdout.strip()


class RepoPushWalk(unittest.TestCase):
    def target(self, fake: FakeForgejo, code_url: str = "", ledger_url: str = "") -> str:
        urls = (f'git_url = "{code_url}"\n' if code_url else "") + (f'ledger_git_url = "{ledger_url}"\n' if ledger_url else "")
        return f'\n[[mirror]]\nforge = "forgejo"\nrepo = "{fake.repo_url}"\napi = "{fake.api}"\n{urls}'

    def setUp(self) -> None:
        self.fakes = [FakeForgejo().start(), FakeForgejo().start()]
        for fake in self.fakes:
            self.addCleanup(fake.stop)

    def bare(self, world: World, name: str) -> str:
        path = world.base / f"{name}.git"
        git(world.base, "init", "-q", "--bare", "-b", "main", str(path))
        return str(path)

    def test_both_repos_reach_a_bare_remote_per_target(self) -> None:
        """Prevents a forge that cannot take over: after sync every target holds the code and the ledger at their heads."""
        world = World("", ledger_vcs="git", replication=QUIET)
        self.addCleanup(world.close)
        code_urls = [self.bare(world, f"code{i}") for i in range(2)]
        ledger_urls = [self.bare(world, f"ledger{i}") for i in range(2)]
        mirrors = "".join(self.target(f, c, l) for f, c, l in zip(self.fakes, code_urls, ledger_urls))
        config = world.code / ".qew" / "config.toml"
        config.write_text(config.read_text().replace(QUIET, "") + mirrors + QUIET)
        world.create("--title", "Work", "--quote", QUOTE)
        result = world.run("sync", "--all", env=ENV)
        self.assertEqual(result.returncode, 0, result.stderr)
        for code_url, ledger_url in zip(code_urls, ledger_urls):
            self.assertEqual(head(code_url), git(world.code, "rev-parse", "main"))
            self.assertEqual(head(ledger_url), git(world.ledger, "rev-parse", "main"))
        names = git(world.code, "remote").split()
        self.assertEqual(len([n for n in names if n.startswith("qew-")]), 2)

    def test_an_unreachable_target_is_reported_and_does_not_block_the_others(self) -> None:
        """Prevents one dead forge starving the rest: the reachable target still receives both repos."""
        world = World("", ledger_vcs="git", replication=QUIET)
        self.addCleanup(world.close)
        good_code, good_ledger = self.bare(world, "good-code"), self.bare(world, "good-ledger")
        dead = str(world.base / "missing" / "gone.git")
        mirrors = self.target(self.fakes[0], dead, dead) + self.target(self.fakes[1], good_code, good_ledger)
        config = world.code / ".qew" / "config.toml"
        config.write_text(config.read_text().replace(QUIET, "") + mirrors + QUIET)
        world.create("--title", "Work", "--quote", QUOTE)
        result = world.run("sync", "--all", env=ENV)
        self.assertEqual(head(good_code), git(world.code, "rev-parse", "main"))
        self.assertEqual(head(good_ledger), git(world.ledger, "rev-parse", "main"))
        output = result.stdout + result.stderr
        self.assertIn(f"{self.fakes[0].repo_url.split('//')[1].replace('/', '-')}", output)
        self.assertIn("failed", output)

    def test_a_target_without_a_git_url_is_quiet_in_sync_named_by_status_and_no_remote_is_guessed(self) -> None:
        """Prevents guessing a push URL from the API repo, and a hint repeating on every sync: sync is quiet, status says repos=off."""
        world = World(self.target(self.fakes[0]), ledger_vcs="git", replication=QUIET)
        self.addCleanup(world.close)
        world.create("--title", "Work", "--quote", QUOTE)
        result = world.run("sync", "--all", env=ENV)
        self.assertNotIn("git_url", result.stdout + result.stderr)
        self.assertIn("repos=off", world.run("status", env=ENV).stdout)
        self.assertEqual([n for n in git(world.code, "remote").split() if n.startswith("qew-")], [])
        self.assertEqual([n for n in git(world.ledger, "remote").split() if n.startswith("qew-")], [])


if __name__ == "__main__":
    unittest.main()
