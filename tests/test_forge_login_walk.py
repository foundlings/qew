"""The forge assignee is the human: `[forge] login` in the repo config names it, whichever agent actor claims or opens.

Each test is a scenario; its docstring names the failure it prevents. The agent actor stays in the ledger as the claimant.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.pr_harness import PrWorld

HUMAN = '\n[forge]\nlogin = "887"\n'


def assignee(world: PrWorld, number: int = 1) -> str:
    return world.forge.issues[number].assignee


def event_types(world: PrWorld, item: str) -> list[str]:
    return [e["type"] for e in world.events(item)]


class ForgeLoginWalk(unittest.TestCase):
    def test_a_claim_by_any_agent_assigns_the_issue_to_the_human_and_a_release_unassigns(self) -> None:
        """Prevents agents each needing an env var: elf5 claims, the issue is assigned to 887, the ledger still says elf5."""
        w = PrWorld(HUMAN, actor="elf5")
        self.addCleanup(w.close)
        item = w.item()
        self.assertEqual(assignee(w), "887")
        self.assertEqual(w.ledger.snapshot().folds[item].owner, "elf5")
        self.assertEqual(w.qew("release", item, "--reason", "handing back", actor="elf5").code, 0)
        w.sync()
        self.assertEqual(assignee(w), "")

    def test_the_forge_assignee_is_not_an_event_or_a_divergence(self) -> None:
        """Prevents the human assignee echoing back into the ledger: the next pull sees nothing to import or contest."""
        w = PrWorld(HUMAN, actor="elf5")
        self.addCleanup(w.close)
        item = w.item()
        from qew.forge_pull import pull_target
        from qew.sync import Target
        from qew import ledger_config
        mirror = w.config.mirrors[0]
        report = pull_target(w.ledger, Target(mirror, ledger_config.mirror_slug(mirror), w.forge))
        self.assertEqual((report.applied, report.divergences), (0, 0))
        self.assertNotIn("assigned", event_types(w, item))

    def test_a_pull_request_is_assigned_to_the_human_without_an_environment_variable(self) -> None:
        """Prevents 'not assigned to elf5': the opener login comes from the config, QEW_FORGE_LOGIN still overrides it."""
        w = PrWorld(HUMAN, actor="elf5")
        self.addCleanup(w.close)
        item = w.item()
        run = w.open(item)
        self.assertEqual(run.code, 0, run)
        self.assertEqual(w.forge.pull_specs[1].assignee, "887")
        self.assertNotIn("not assigned", run.err)
        other = w.item("Other")
        self.assertEqual(w.open(other, env={"QEW_FORGE_LOGIN": "octo"}).code, 0)
        self.assertEqual(w.forge.pull_specs[2].assignee, "octo")

    def test_a_per_actor_login_and_a_mirror_map_beat_the_default(self) -> None:
        """Prevents one login for everyone when a team needs several: [forge.logins] and the mirror's assignees map are exact."""
        w = PrWorld(HUMAN + '[forge.logins]\nelf3 = "alice"\n', actor="elf5", mirror_options='assignees = { elf4 = "bob" }\n')
        self.addCleanup(w.close)
        w.item("Five")
        w.item("Three", by="elf3")
        w.item("Four", by="elf4")
        self.assertEqual([assignee(w, n) for n in (1, 2, 3)], ["887", "alice", "bob"])

    def test_without_the_config_nothing_changes(self) -> None:
        """Prevents surprising existing setups: no [forge] means no issue assignee and the actor as the pull request login."""
        w = PrWorld(actor="elf5")
        self.addCleanup(w.close)
        item = w.item()
        self.assertEqual(assignee(w), "")
        self.assertEqual(w.open(item).code, 0)
        self.assertEqual(w.forge.pull_specs[1].assignee, "elf5")

    def test_a_malformed_forge_table_is_refused(self) -> None:
        """Prevents a typo silently disabling the login: unknown keys and a login with spaces fail loudly."""
        for config in ('\n[forge]\nlogin = "has space"\n', '\n[forge]\nloginn = "887"\n', '\n[forge]\nlogin = 5\n'):
            with self.subTest(config=config):
                with self.assertRaises(Exception):
                    PrWorld(config).close()


if __name__ == "__main__":
    unittest.main()
