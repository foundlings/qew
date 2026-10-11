"""`qew evidence ID --change X --on-behalf-of ACTOR` lets another actor (the integrator) record evidence for a claim ACTOR holds. It records
both actors; it is refused unless ACTOR really holds the claim; claims themselves stay as they are.

Each test is a scenario; its docstring names the failure it prevents.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import World
from tests.lifecycle_support import approved, started

BOB = {"QEW_ACTOR": "bob"}


class EvidenceBehalfWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = World()
        self.addCleanup(self.w.close)
        self.item = started(self.w)  # claimed by tester
        self.sha = self.w.commit("the work")

    def evidence_events(self) -> list[dict]:
        return [e for e in self.w.events(self.item) if e["type"] == "evidence"]

    def test_the_integrator_records_evidence_for_the_claim_holder_and_both_are_named(self) -> None:
        """Prevents a round trip with the elf for every cited item, without losing who recorded it: both actors are in the event."""
        plain = self.w.run("evidence", self.item, "--change", self.sha[:10], env=BOB)
        self.assertEqual(plain.returncode, 3, plain.stderr)
        self.assertEqual(self.evidence_events(), [])
        run = self.w.run("evidence", self.item, "--change", self.sha[:10], "--on-behalf-of", "tester", env=BOB)
        self.assertEqual(run.returncode, 0, run.stderr)
        (event,) = self.evidence_events()
        self.assertEqual((event["actor"], event["on_behalf_of"]), ("bob", "tester"))
        again = self.w.run("evidence", self.item, "--change", self.sha[:10], "--on-behalf-of", "tester", env=BOB)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(len(self.evidence_events()), 1)

    def test_it_is_refused_unless_the_named_actor_holds_the_claim(self) -> None:
        """Prevents evidence in the name of someone who is not working the item, or of no one: nothing is recorded."""
        stranger = self.w.run("evidence", self.item, "--change", self.sha[:10], "--on-behalf-of", "carol", env=BOB)
        self.assertEqual(stranger.returncode, 3, stranger.stderr)
        self.assertIn("carol", stranger.stderr)
        unclaimed = approved(self.w, "Nobody holds it")
        refused = self.w.run("evidence", unclaimed, "--change", self.sha[:10], "--on-behalf-of", "tester", env=BOB)
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(self.evidence_events(), [])
        self.assertEqual([e for e in self.w.events(unclaimed) if e["type"] == "evidence"], [])

    def test_the_holder_names_themselves_is_a_mistake_and_the_claim_stays_theirs(self) -> None:
        """Prevents claims changing hands: naming yourself is exit 2, and the other actor still cannot complete the item."""
        self_named = self.w.run("evidence", self.item, "--change", self.sha[:10], "--on-behalf-of", "tester")
        self.assertEqual(self_named.returncode, 2, self_named.stderr)
        self.assertEqual(self.w.run("evidence", self.item, "--change", self.sha[:10], "--on-behalf-of", "tester", env=BOB).returncode, 0)
        done = self.w.run("complete", self.item, env=BOB)
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(self.w.run("check").returncode, 0)


if __name__ == "__main__":
    unittest.main()
