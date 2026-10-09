"""Offline claims: an explicit override lets a claim be taken while the arbiter is unreachable, and sync settles it later.

Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_forgejo import TOKEN, FakeForgejo
from tests.ledger_harness import QUOTE, World

ENV = {"FORGEJO_TOKEN": TOKEN}
QUIET = "\n[replication]\nauto_forge_sync = false\nauto_push = false\n"


def types(world: World, item: str) -> list:
    return [e["type"] for e in world.events(item)]


class OfflineClaimWalk(unittest.TestCase):
    ALLOW = ""

    def setUp(self) -> None:
        self.fake = FakeForgejo().start()
        self.addCleanup(self.fake.stop)
        self.slug = f"forgejo-{self.fake.repo_url.split('//')[1].replace('/', '-')}"
        mirrors = f'\n[[mirror]]\nforge = "forgejo"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'
        self.config = QUIET + f'\n[authority]\nprimary = "{self.slug}"\n{self.ALLOW}'
        self.a = World(mirrors, replication=self.config)
        self.addCleanup(self.a.close)
        self.item = self.a.create("--title", "Private work", "--quote", QUOTE)
        self.assertEqual(self.a.run("sync", env=ENV).returncode, 0)
        self.b = self.second_checkout()

    def second_checkout(self) -> World:
        other = World.__new__(World)
        other._owner = False
        other._temporary = self.a._temporary
        other.base = self.a.base
        other.ledger_vcs = None
        other.code = self.a.base / "code-b"
        other.ledger = self.a.base / "ledger-b"
        shutil.copytree(self.a.code, other.code)
        shutil.copytree(self.a.ledger, other.ledger)
        config = other.code / ".qew" / "config.toml"
        config.write_text(config.read_text().replace('"../ledger"', '"../ledger-b"'))
        return other

    def comments(self) -> list:
        return [c["body"] for issue in self.fake.issues.values() for c in issue.get("discussion", [])]


class OverrideFlagWalk(OfflineClaimWalk):
    def test_without_the_override_an_unreachable_primary_still_refuses(self) -> None:
        """Prevents the override becoming the default: a plain start with the primary down writes nothing."""
        self.fake.down = True
        result = self.a.run("start", self.item, env=ENV)
        self.assertEqual(result.returncode, 3)
        self.assertNotIn("claimed", types(self.a, self.item))

    def test_start_offline_takes_a_provisional_claim_and_the_next_sync_confirms_it(self) -> None:
        """Prevents offline work being lost or unreconciled: the claim is marked provisional, works offline, and sync reserves it."""
        self.fake.down = True
        result = self.a.run("start", self.item, "--offline", "--json", env=ENV)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["provisional"])
        self.assertEqual(types(self.a, self.item)[-1], "claimed")
        self.assertTrue(self.a.events(self.item)[-1]["provisional"])
        status = self.a.run("status", env=ENV).stdout
        self.assertIn("provisional", status)
        self.assertIn(self.item, status)
        self.fake.down = False
        synced = self.a.run("sync", env=ENV)
        self.assertEqual(synced.returncode, 0, synced.stderr)
        self.assertEqual(types(self.a, self.item)[-2:], ["claimed", "claim_confirmed"])
        self.assertTrue(any(f"qew:claim {self.item} tester" in body for body in self.comments()))
        self.assertNotIn("provisional", self.a.run("status", env=ENV).stdout)

    def test_a_claim_taken_while_apart_loses_to_the_holder_on_the_primary_and_sync_says_so(self) -> None:
        """Prevents a silent double claim: the provisional claim is lost, the event names the winner, sync exits 3 naming item and holder."""
        self.fake.down = True
        self.assertEqual(self.a.run("start", self.item, "--offline", env=ENV).returncode, 0)
        self.fake.down = False
        won = self.b.run("start", self.item, env={**ENV, "QEW_ACTOR": "second"})
        self.assertEqual(won.returncode, 0, won.stderr)
        synced = self.a.run("sync", env=ENV)
        self.assertEqual(synced.returncode, 3)
        self.assertIn(self.item, synced.stderr)
        self.assertIn("second", synced.stderr)
        lost = [e for e in self.a.events(self.item) if e["type"] == "claim_lost"]
        self.assertEqual(len(lost), 1)
        self.assertEqual(lost[0]["winner"], "second")
        shown = self.a.run("show", self.item, "--json", env=ENV)
        self.assertEqual(json.loads(shown.stdout)["state"], "approved")
        status = self.a.run("status", env=ENV).stdout
        self.assertIn(f"claim {self.item} lost winner=second", status)
        again = self.a.run("sync", env=ENV)
        self.assertEqual(again.returncode, 0, again.stderr)

    def test_an_item_the_primary_has_never_seen_can_be_claimed_offline_and_confirms_after_its_first_sync(self) -> None:
        """Prevents offline-created work being unclaimable: no issue yet is fine with the override, and the first sync reserves it."""
        fresh = self.a.create("--title", "Created offline", "--quote", QUOTE)
        self.assertEqual(self.a.run("start", fresh, env=ENV).returncode, 2)
        self.assertEqual(self.a.run("start", fresh, "--offline", env=ENV).returncode, 0)
        self.assertEqual(self.a.run("sync", env=ENV).returncode, 0)
        self.assertEqual(types(self.a, fresh)[-2:], ["claimed", "claim_confirmed"])

    def test_completing_offline_work_is_allowed_and_the_completion_survives_a_lost_claim(self) -> None:
        """Prevents finished private work being thrown away: the completion records the provisional claim and stands; sync still reports the loss."""
        self.fake.down = True
        self.assertEqual(self.a.run("start", self.item, "--offline", env=ENV).returncode, 0)
        done = self.a.run("complete", self.item, "--no-commit", "private repo, nothing to land", env=ENV)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("provisional_claim", self.a.events(self.item)[-1])
        self.fake.down = False
        self.assertEqual(self.b.run("start", self.item, env={**ENV, "QEW_ACTOR": "second"}).returncode, 0)
        synced = self.a.run("sync", env=ENV)
        self.assertEqual(synced.returncode, 3)
        self.assertEqual(self.a.run("check", env=ENV).returncode, 0)
        self.assertEqual(json.loads(self.a.run("show", self.item, "--json", env=ENV).stdout)["state"], "completed")


class ConfigAllowWalk(OfflineClaimWalk):
    ALLOW = 'offline_claims = "allow"\n'

    def test_the_config_makes_a_plain_start_fall_back_when_the_primary_is_unreachable(self) -> None:
        """Prevents retyping the flag all day on a private repo: offline_claims = allow falls back by itself, and only when unreachable."""
        online = self.a.run("start", self.item, env=ENV)
        self.assertEqual(online.returncode, 0, online.stderr)
        self.assertEqual(types(self.a, self.item)[-2:], ["claimed", "claim_confirmed"])
        other = self.a.create("--title", "Second", "--quote", QUOTE)
        self.assertEqual(self.a.run("sync", env=ENV).returncode, 0)
        self.fake.down = True
        offline = self.a.run("start", other, env=ENV)
        self.assertEqual(offline.returncode, 0, offline.stderr)
        self.assertTrue(self.a.events(other)[-1]["provisional"])


class LedgerRemoteOfflineWalk(unittest.TestCase):
    def setUp(self) -> None:
        every_time = "\n[replication]\nfetch_interval_s = 0\nauto_forge_sync = false\n"
        self.a = World(ledger_vcs="git", remote=True, replication=every_time)
        self.addCleanup(self.a.close)
        self.b = self.a.clone("b", replication=every_time)
        self.item = self.a.create("--title", "Shared", "--quote", QUOTE)
        self.b.run("list")

    def test_the_ledger_remote_settles_an_offline_claim_when_it_returns(self) -> None:
        """Prevents the remote-arbitrated mode lacking the override: refused without it, provisional with it, confirmed or lost on sync."""
        offline = {"QEW_OFFLINE": "1"}
        self.assertEqual(self.a.run("start", self.item, env=offline).returncode, 3)
        self.assertEqual(self.a.run("start", self.item, "--offline", env=offline).returncode, 0)
        self.assertEqual(self.b.run("start", self.item, env={"QEW_ACTOR": "second"}).returncode, 0)
        synced = self.a.run("sync")
        self.assertEqual(synced.returncode, 3)
        self.assertIn(self.item, synced.stderr)
        self.assertIn("second", synced.stderr)
        self.assertEqual([e["type"] for e in self.a.events(self.item)].count("claim_lost"), 1)
        self.assertEqual(self.a.run("check").returncode, 0)

    def test_a_rival_claim_already_on_the_remote_wins_even_before_its_confirmation_arrives(self) -> None:
        """Prevents two holders of one item: the other checkout's claim is pushed before its claim_confirmed follows,
        and a provisional claim older than nothing on the remote must still lose to it."""
        from qew import ledger_store
        self.assertEqual(self.a.run("start", self.item, "--offline", env={"QEW_OFFLINE": "1"}).returncode, 0)
        rival = ledger_store.Ledger(self.a.ledger)
        rival.append_event(self.item, "claimed", owner="second", actor="second")  # pushed claim, confirmation not yet arrived
        synced = self.a.run("sync")
        self.assertEqual(synced.returncode, 3, synced.stdout + synced.stderr)
        self.assertEqual([e["type"] for e in self.a.events(self.item)].count("claim_lost"), 1)

    def test_an_uncontested_offline_claim_is_confirmed_by_the_next_sync(self) -> None:
        """Prevents a provisional claim lingering for ever when nobody else wanted the item."""
        self.assertEqual(self.a.run("start", self.item, "--offline", env={"QEW_OFFLINE": "1"}).returncode, 0)
        self.assertEqual(self.a.run("sync").returncode, 0)
        self.assertEqual(self.a.events(self.item)[-1]["type"], "claim_confirmed")


if __name__ == "__main__":
    unittest.main()
