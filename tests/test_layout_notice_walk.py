"""A ledger still on the flat layout says so where agents already look, so they tell their user to migrate.

Adversary: a ledger that quietly slows with its size because nobody knew `qew migrate-layout` existed, a notice that nags a
ledger already migrated, one that tells an agent to migrate before every agent can read the new layout, and one that costs a
session more bytes than the prime text may spend.
"""

import json
import unittest

from tests.ledger_harness import World


class LayoutNoticeWalk(unittest.TestCase):
    def flat(self) -> World:
        world = World(schema="7")
        self.addCleanup(world.close)
        world.create("--title", "something")
        return world

    def test_a_flat_ledger_says_so_in_prime_status_and_doctor_with_the_command_and_the_version_to_wait_for(self) -> None:
        """Prevents a silently slowing ledger: prime (the session-start text), status and doctor all name the migration."""
        world = self.flat()
        for command in (("prime",), ("status",), ("doctor",)):
            text = world.run(*command).stdout
            self.assertIn("qew migrate-layout", text, command)
            self.assertIn("6.46.0", text, command)
            self.assertIn("schema 7", text, command)
        self.assertLessEqual(len(world.run("prime").stdout.encode("utf-8")), 4096)

    def test_status_json_carries_the_layout_for_agents(self) -> None:
        """Prevents a machine reader missing it: layout.schema, layout.current and the notice text."""
        layout = json.loads(self.flat().run("status", "--json").stdout)["layout"]
        self.assertEqual((layout["schema"], layout["current"]), (7, 8))
        self.assertIn("migrate-layout", layout["notice"])

    def test_a_sharded_ledger_says_nothing(self) -> None:
        """Prevents nagging a migrated ledger: no notice in prime, status text, doctor or the status JSON."""
        world = World(schema="8")
        self.addCleanup(world.close)
        world.create("--title", "something")
        for command in (("prime",), ("status",), ("doctor",)):
            self.assertNotIn("migrate-layout", world.run(*command).stdout, command)
        self.assertIsNone(json.loads(world.run("status", "--json").stdout)["layout"]["notice"])

    def test_the_session_start_hook_text_stays_inside_its_bound_with_the_notice(self) -> None:
        """Prevents the hook overflowing: the notice shares the 2000-byte session-start budget."""
        world = self.flat()
        out = world.run("hook", "session-start", "--agent", "claude", stdin="{}")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("migrate-layout", out.stdout)
        self.assertLess(len(out.stdout.encode("utf-8")), 4000)


if __name__ == "__main__":
    unittest.main()
