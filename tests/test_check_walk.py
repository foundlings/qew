"""Walks of `qew check`: what makes a ledger invalid, and that it never writes.

Adversary: a hand-merged file that parses but is wrong (unknown key, a need that
cycles or dangles, an event the state machine forbids) slipping through to
readiness answers.
"""

import json
import unittest

from tests.ledger_harness import World


class CheckWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_a_clean_ledger_passes_and_check_writes_nothing(self) -> None:
        w = self.world
        a = w.create("--kind", "issue", "--title", "A")
        w.create("--kind", "issue", "--title", "B", "--needs", a)
        before = sorted(str(p) for p in w.ledger.rglob("*"))
        result = w.run("check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok", result.stdout)
        self.assertEqual(sorted(str(p) for p in w.ledger.rglob("*")), before)

    def _fails(self, fragment: str) -> None:
        result = self.world.run("check")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(fragment, result.stdout + result.stderr)

    def test_unknown_header_key_fails(self) -> None:
        w = self.world
        item = w.create("--kind", "issue", "--title", "A")
        path = w.ledger / "items" / f"{item}.md"
        path.write_text(path.read_text().replace("\n---\n", "\nstatus: done\n---\n", 1))
        self._fails("unknown header key")

    def test_a_dependency_cycle_fails(self) -> None:
        w = self.world
        a = w.create("--kind", "issue", "--title", "A")
        b = w.create("--kind", "issue", "--title", "B", "--needs", a)
        path = w.ledger / "items" / f"{a}.md"  # `set` refuses to write a cycle; a hand merge can still produce one
        path.write_text(path.read_text().replace("needs: \n", f"needs: {b}\n", 1))
        self._fails("cycle")

    def test_a_dangling_need_fails(self) -> None:
        w = self.world
        a = w.create("--kind", "issue", "--title", "A")
        path = w.ledger / "items" / f"{a}.md"
        path.write_text(path.read_text().replace("needs: \n", "needs: zzzzzzzzz\n", 1))
        self._fails("dangling")

    def test_an_illegal_transition_fails(self) -> None:
        w = self.world
        a = w.create("--kind", "issue", "--title", "A")
        event = w.ledger / "events" / a / "ZZZZZZZZZZZZZZZZZZZZZZZZZZ.json"
        event.write_text(json.dumps({"type": "completed", "at": "2026-01-01T00:00:00Z", "actor": "x"}))
        self._fails("illegal")

    def test_completed_work_without_evidence_fails(self) -> None:
        w = self.world
        a = w.create("--kind", "issue", "--title", "A", "--quote", "Alexander: 'please track this work in the Qew ledger now'")
        for kind, extra in (("claimed", {"owner": "x"}), ("completed", {})):
            name = {"claimed": "Y", "completed": "Z"}[kind] * 26
            (w.ledger / "events" / a / f"{name}.json").write_text(
                json.dumps({"type": kind, "at": "2026-01-01T00:00:00Z", "actor": "x", **extra})
            )
        self._fails("evidence")

    def test_a_wrong_prefix_and_a_malformed_event_fail(self) -> None:
        w = self.world
        a = w.create("--kind", "issue", "--title", "A")
        (w.ledger / "events" / a / "BAD.json").write_text("not json")
        self._fails("not valid JSON")


if __name__ == "__main__":
    unittest.main()
