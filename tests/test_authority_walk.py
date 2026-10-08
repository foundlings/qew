"""Authority configuration and the claim events that arbitration writes.

Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World

MIRRORS = '\n[[mirror]]\nforge = "forgejo"\nrepo = "https://git.example/o/r"\n'
SLUG = "forgejo-git.example-o-r"
QUIET = "\n[replication]\nauto_forge_sync = false\n"


def write_event(world: World, item: str, name: str, **data) -> None:
    body = {"at": "2026-01-01T00:00:00Z", "actor": "x", **data}
    (world.ledger / "events" / item / f"{name}.json").write_text(json.dumps(body))


class AuthorityConfigWalk(unittest.TestCase):
    def status(self, authority: str, mirrors: str = MIRRORS):
        world = World(mirrors, replication=QUIET + authority)
        self.addCleanup(world.close)
        return world.run("status")

    def test_the_default_is_no_primary_and_status_says_so(self) -> None:
        """Prevents an unconfigured ledger silently claiming a forge authority."""
        result = self.status("")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("authority primary=none", result.stdout)

    def test_a_named_target_is_accepted_by_slug_or_unique_part(self) -> None:
        """Prevents typing the long slug being required: a unique fragment resolves to the target."""
        for value in (SLUG, "git.example"):
            result = self.status(f'\n[authority]\nprimary = "{value}"\n')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"authority primary={SLUG}", result.stdout)

    def test_unknown_targets_and_wrong_types_fail_loudly(self) -> None:
        """Prevents a typo leaving claims unarbitrated: the config names what is wrong."""
        unknown = self.status('\n[authority]\nprimary = "nowhere"\n')
        self.assertEqual(unknown.returncode, 1)
        self.assertIn("nowhere", unknown.stderr)
        typed = self.status("\n[authority]\nprimary = 3\n")
        self.assertEqual(typed.returncode, 1)
        stray = self.status('\n[authority]\nprimery = "none"\n')
        self.assertEqual(stray.returncode, 1)
        self.assertIn("primery", stray.stderr)
        no_targets = self.status('\n[authority]\nprimary = "git.example"\n', mirrors="")
        self.assertEqual(no_targets.returncode, 1)


class ClaimEventsWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)
        self.item = self.world.create("--title", "Contested", "--quote", QUOTE)

    def test_a_lost_claim_is_ignored_by_the_fold(self) -> None:
        """Prevents two owners: the claim that lost arbitration leaves the item unclaimed."""
        w = self.world
        w.run("start", self.item)
        claimed = w.events(self.item)[-1]
        claim_id = sorted((w.ledger / "events" / self.item).glob("*.json"))[-1].stem
        w.run("release", self.item)
        write_event(w, self.item, "ZZZZZZZZA", type="claimed", owner="loser")
        write_event(w, self.item, "ZZZZZZZZB", type="claim_lost", ref="ZZZZZZZZA", winner="tester")
        shown = w.run("show", self.item).stdout
        self.assertIn("state: approved", shown)
        self.assertNotIn("owner: loser", shown)
        self.assertEqual(w.run("check").returncode, 0)

    def test_a_confirmed_claim_still_owns_the_item(self) -> None:
        """Prevents confirmation events hiding the owner."""
        w = self.world
        w.run("start", self.item)
        claim = sorted((w.ledger / "events" / self.item).glob("*.json"))[-1].stem
        write_event(w, self.item, "ZZZZZZZZC", type="claim_confirmed", ref=claim)
        self.assertIn("state: claimed", w.run("show", self.item).stdout)
        self.assertEqual(w.run("check").returncode, 0)

    def test_check_refuses_claim_events_that_name_nothing(self) -> None:
        """Prevents a dangling claim_lost silently cancelling nothing (or the wrong thing)."""
        w = self.world
        write_event(w, self.item, "ZZZZZZZZD", type="claim_lost", ref="missing", winner="x")
        result = w.run("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("claim_lost", result.stdout)


if __name__ == "__main__":
    unittest.main()
