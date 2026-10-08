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


class CheckReportWalk(unittest.TestCase):
    """`qew check --json`: one stable machine report."""

    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def report(self, expected_code: int) -> dict:
        result = self.world.run("check", "--json")
        self.assertEqual(result.returncode, expected_code, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def set_needs(self, item: str, needs: str) -> None:
        path = self.world.ledger / "items" / f"{item}.md"
        lines = [f"needs: {needs}" if line.startswith("needs:") else line for line in path.read_text().split("\n")]
        path.write_text("\n".join(lines))

    def test_valid_ledger_reports_valid_true_and_exit_0(self) -> None:
        """A clean ledger gives empty lists and a schema_version, so callers can test one field."""
        self.world.create("--title", "A")
        data = self.report(0)
        self.assertEqual((data["valid"], data["cycles"], data["contradictions"], data["text_findings"]), (True, [], [], []))
        self.assertEqual(data["schema_version"], 1)

    def test_cycles_are_reported_once_in_canonical_rotation(self) -> None:
        """A cycle entered through a tail must print rotated to its smallest id and closed, so diffs are stable."""
        w = self.world
        tail = w.create("--title", "tail")
        a = w.create("--title", "a")
        b = w.create("--title", "b")
        c = w.create("--title", "c")
        self.set_needs(a, b)
        self.set_needs(b, c)
        self.set_needs(c, a)
        self.set_needs(tail, c)
        data = self.report(1)
        self.assertFalse(data["valid"])
        self.assertEqual(data["cycles"], [[a, b, c, a]])

    def test_open_work_under_a_completed_epic_is_a_contradiction(self) -> None:
        """A plan marked done that still has open work is reported by id, and is advisory (the check stays valid)."""
        w = self.world
        epic = w.create("--kind", "epic", "--title", "E")
        kid = w.create("--title", "K", "--parent", epic)
        w.approve(epic, kid)
        w.run("start", epic)
        w.run("start", kid)
        w.run("evidence", kid, "--change", w.commit())
        w.run("complete", kid)
        w.run("complete", epic)
        late = w.create("--title", "late", "--parent", epic)
        data = self.report(0)
        self.assertEqual(data["contradictions"], [{"item": late, "kind": "open_child_of_completed_epic"}])
