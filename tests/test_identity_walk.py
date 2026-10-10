"""The actor is the configured VCS identity, never the operating-system login."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World, git

NO_ACTOR = {"QEW_ACTOR": "", "USER": "os-login-name", "GIT_AUTHOR_NAME": "887", "GIT_COMMITTER_NAME": "887"}


class IdentityWalk(unittest.TestCase):
    def test_events_and_commit_messages_carry_the_configured_name_not_the_os_login(self) -> None:
        """Prevents the OS login leaking into ledgers (events, claim owners, commit subjects) when no actor is exported."""
        w = World(ledger_vcs="git")
        self.addCleanup(w.close)
        git(w.code, "config", "user.name", "887")
        item = w.run("create", "--title", "Mine", "--quote", QUOTE, env=NO_ACTOR).stdout.split()[0]
        self.assertEqual(w.run("start", item, env=NO_ACTOR).returncode, 0)
        events = w.events(item)
        self.assertEqual({e["actor"] for e in events}, {"887"})
        self.assertEqual(events[-1]["owner"], "887")
        self.assertEqual(w.ledger_log()[0], f"qew: start {item} by 887")
        self.assertNotIn("os-login-name", "".join(p.read_text() for p in w.ledger.rglob("*.json")))

    def test_without_any_configured_identity_a_write_is_refused_and_names_the_setting(self) -> None:
        """Prevents guessing an identity: with no name configured anywhere, writes stop before touching the ledger."""
        w = World()
        self.addCleanup(w.close)
        with tempfile.TemporaryDirectory() as empty:
            env = {**NO_ACTOR, "HOME": empty, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "JJ_CONFIG": "/dev/null"}
            result = w.run("create", "--title", "Nameless", "--draft", env=env)
        self.assertEqual(result.returncode, 2)
        self.assertIn("user.name", result.stderr)
        self.assertFalse((w.ledger / "items").exists())


if __name__ == "__main__":
    unittest.main()
