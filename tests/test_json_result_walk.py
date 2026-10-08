"""Versioned `--json` results of the write commands.

Adversary: agents guessing where the flag goes, parsing prose lines instead of an object, and JSON mode
hiding a failure.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import World
from tests.lifecycle_support import approved, started


class JsonResultWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def parse(self, *arguments: str, env=None) -> dict:
        result = self.world.run(*arguments, env=env)
        self.assertEqual(result.returncode, 0, (arguments, result.stderr))
        return json.loads(result.stdout)

    def test_the_flag_before_or_after_the_command_gives_the_same_versioned_object(self) -> None:
        """Prevents agents guessing the flag position."""
        w = self.world
        first, second = approved(w, title="One"), approved(w, title="Two")
        after = self.parse("start", first, "--json")
        before = self.parse("--json", "start", second)
        self.assertEqual(after.pop("id"), first)
        self.assertEqual(before.pop("id"), second)
        self.assertEqual(after, before)
        self.assertEqual((after["schema_version"], after["state"]), (1, "started"))

    def test_start_json_carries_the_lane_fields_when_given(self) -> None:
        """Prevents a loop losing which lane it started."""
        item = approved(self.world)
        result = self.parse("start", item, "--lane", "lane-a", "--kind", "token", "--json")
        self.assertEqual((result["lane"], result["kind"]), ("lane-a", "token"))

    def test_create_json_for_a_batch_is_an_array_in_input_order(self) -> None:
        """Prevents parsing stdout lines."""
        batch = json.dumps([{"title": "First"}, {"title": "Second"}])
        result = self.world.run("create", "--from-file", "-", "--json", stdin=batch)
        self.assertTrue(result.stdout.lstrip().startswith("["), result.stdout)
        rows = json.loads(result.stdout)
        self.assertEqual([r["state"] for r in rows], ["draft", "draft"])
        self.assertTrue(all(r["schema_version"] == 1 for r in rows))
        titles = [json.loads(self.world.run("show", r["id"], "--json").stdout)["title"] for r in rows]
        self.assertEqual(titles, ["First", "Second"])

    def test_every_other_write_command_pins_its_object_shape(self) -> None:
        """Prevents shape drift: one table of the keys each command promises."""
        w = self.world
        dep = approved(w, title="Dep")
        item = started(w, title="Item")
        w.run("evidence", item, "--change", w.commit("work"))
        shapes = {
            "set": self.parse("set", item, "--priority", "1", "--json"),
            "block": self.parse("block", item, "--on", dep, "--json"),
            "unblock": self.parse("unblock", item, "--from", dep, "--json"),
            "release": self.parse("release", item, "--reason", "pause", "--json"),
        }
        self.assertEqual(shapes["set"]["changed"], ["priority"])
        self.assertEqual((shapes["block"]["state"], shapes["unblock"]["state"], shapes["release"]["state"]),
                         ("blocked", "unblocked", "released"))
        w.run("start", item)
        done = self.parse("complete", item, "--json")
        self.assertEqual((done["id"], done["state"]), (item, "completed"))
        closed = self.parse("close", dep, "--reason", "not_planned", "--json")
        self.assertEqual(closed["state"], "closed")
        reopened = self.parse("reopen", dep, "--reason", "again", "--json")
        self.assertEqual(reopened["state"], "reopened")

    def test_a_failure_keeps_the_plain_message_and_exit_code_in_json_mode(self) -> None:
        """Prevents JSON mode hiding failures."""
        result = self.world.run("start", "nosuchitem", "--json")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertTrue(result.stderr.startswith("qew: "))


if __name__ == "__main__":
    unittest.main()
