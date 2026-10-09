"""Walks of `qew hook install|remove` output: the versioned JSON object and the default all-agents run.

Adversary: automation that cannot tell whether a manual trust step remains, a rerun that is
not idempotent without anyone noticing, and a default invocation that skips an agent.
"""

import json
import unittest

from tests.ledger_harness import World
from tests.test_hook_install_walk import AGENTS, FILES, read


class MachineOutputWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_single_agent_json_says_whether_a_trust_step_remains(self) -> None:
        """Scripted setup must not guess whether Codex still needs `/hooks` review."""
        result = self.world.run("hook", "install", "--codex", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout),
                         {"schema_version": 1, "agent": "codex", "action": "installed", "trust_required": True})
        claude = json.loads(self.world.run("hook", "install", "--claude", "--json").stdout)
        self.assertEqual(claude, {"schema_version": 1, "agent": "claude", "action": "installed"})

    def test_all_agents_json_lists_every_agent_in_fixed_order_and_reruns_are_unchanged(self) -> None:
        """A non-idempotent rerun must be visible to automation."""
        self.world.run("hook", "install")
        rerun = json.loads(self.world.run("hook", "install", "--json").stdout)
        self.assertEqual(rerun["schema_version"], 1)
        self.assertEqual([a["agent"] for a in rerun["agents"]], list(AGENTS))
        self.assertEqual({a["action"] for a in rerun["agents"]}, {"unchanged"})
        removed = json.loads(self.world.run("hook", "remove", "--json").stdout)
        self.assertEqual({a["action"] for a in removed["agents"]}, {"removed"})
        self.assertNotIn("trust_required", removed["agents"][0])

    def test_human_output_keeps_one_line_per_agent_and_the_codex_trust_line(self) -> None:
        """The trust instruction must stay visible to a person."""
        out = self.world.run("hook", "install", "--codex").stdout
        self.assertEqual(out.splitlines(), ["installed codex hook", "review and trust it with /hooks before it will run"])


class InstallAllWalk(unittest.TestCase):
    def test_default_install_orders_agents_and_default_remove_undoes_all(self) -> None:
        """No agent may be silently skipped by the default invocation."""
        world = World()
        self.addCleanup(world.close)
        first = world.run("hook", "install")
        self.assertEqual(first.stdout.splitlines()[:4], [f"installed {a} hook" for a in AGENTS])
        for relative in FILES.values():
            self.assertTrue((world.code / relative).is_file(), relative)
        self.assertEqual(world.run("hook", "install").stdout.splitlines()[:4], [f"unchanged {a} hook" for a in AGENTS])
        removed = world.run("hook", "remove")
        self.assertEqual(removed.stdout.splitlines(), [f"removed {a} hook" for a in AGENTS])
        self.assertFalse((world.code / FILES["opencode"]).exists())
        self.assertEqual(read(world.code / FILES["claude"]), {})
        self.assertEqual(read(world.code / FILES["codex"]), {})
        self.assertEqual(read(world.code / FILES["cursor"]), {"version": 1})


if __name__ == "__main__":
    unittest.main()
