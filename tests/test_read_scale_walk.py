"""Walks of the graph reads on adversarial shapes: layered diamonds, long chains, a big ledger, a hidden cycle.

Adversary: path enumeration (2^40 paths in a diamond stack), a recursive search overflowing on a long
chain, a cycle hidden in a large acyclic graph, and the no-index decision regressing into a slow read.
"""

import json
import time
import unittest

from tests.ledger_harness import World


def load(world: World, specs: list) -> list:
    result = world.run("create", "--from-file", "-", stdin=json.dumps(specs))
    assert result.returncode == 0, result.stderr
    return [line.split()[0] for line in result.stdout.splitlines()]


class ScaleWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def timed(self, *arguments: str):
        start = time.monotonic()
        result = self.world.run(*arguments)
        return result, time.monotonic() - start

    def assert_only_depth_findings(self, result) -> None:
        """`check` may refuse a chain deeper than its depth limit, naming only that invariant (no crash, no cycle)."""
        self.assertIn(result.returncode, (0, 1))
        for line in result.stdout.splitlines():
            self.assertTrue(line.startswith(("ok", "graph invariants", "advisory")) or "needs chain" in line, line)

    def test_forty_diamond_layers_validate_without_enumerating_paths(self) -> None:
        """2^40 paths must not be walked: check, ready and blocked answer in seconds and report no cycle."""
        specs = [{"ref": "l0a", "title": "top"}]
        previous = ["l0a"]
        for layer in range(1, 41):
            refs = [f"l{layer}a", f"l{layer}b"]
            specs += [{"ref": ref, "title": ref, "needs": [f"@{p}" for p in previous]} for ref in refs]
            previous = refs
        ids = load(self.world, specs)
        self.world.approve(*ids)
        for command in (("check",), ("ready",), ("blocked",)):
            result, seconds = self.timed(*command)
            self.assertLess(seconds, 5, command)
            self.assertNotIn("cycle", result.stdout)
            if command == ("check",):
                self.assert_only_depth_findings(result)
            else:
                self.assertEqual(result.returncode, 0, (command, result.stderr))

    def test_a_three_thousand_long_chain_does_not_overflow_the_stack(self) -> None:
        """No recursion error in check, ready or blocked; check may only report the documented depth line."""
        specs = [{"ref": "c0", "title": "c0"}]
        specs += [{"ref": f"c{n}", "title": f"c{n}", "needs": [f"@c{n - 1}"]} for n in range(1, 3000)]
        self.world.approve(*load(self.world, specs))
        for command in (("check",), ("ready",), ("blocked",)):
            result, seconds = self.timed(*command)
            self.assertNotIn("Traceback", result.stderr, command)
            self.assertNotIn("recursion", (result.stdout + result.stderr).lower(), command)
            self.assertLess(seconds, 30, command)
            if command != ("check",):
                self.assertEqual(result.returncode, 0, command)
        self.assert_only_depth_findings(self.world.run("check"))

    def test_a_cycle_inside_a_large_acyclic_graph_is_still_reported_with_its_path(self) -> None:
        """Scale must not hide a two-node cycle: it is named in the check output and the JSON report."""
        specs = [{"ref": f"n{n}", "title": f"n{n}", "needs": [f"@n{n - 1}"] if n else []} for n in range(400)]
        ids = load(self.world, specs)
        a, b = ids[100], ids[101]
        for item, other in ((a, b), (b, a)):
            path = self.world.ledger / "items" / f"{item}.md"
            lines = path.read_text().split("\n")
            path.write_text("\n".join(f"needs: {other}" if line.startswith("needs:") else line for line in lines))
        result = self.world.run("check", "--json")
        self.assertEqual(result.returncode, 1)
        cycles = json.loads(result.stdout)["cycles"]
        self.assertTrue(any(set(cycle) == {a, b} for cycle in cycles), cycles)

    def test_five_thousand_items_answer_ready_within_a_time_budget(self) -> None:
        """The no-index decision holds: a 5000-item ledger is read from files fast enough."""
        (self.world.ledger / "items").mkdir()
        for n in range(5000):
            item_id = f"w{n:08d}"
            header = f"id: {item_id}\nkind: issue\ntitle: Work {n}\nparent: \nneeds: \nlabels: \nowner: \npriority: 2\ncreated: 2026-01-01T00:00:00Z\n---\n"
            (self.world.ledger / "items" / f"{item_id}.md").write_text(header)
            events = self.world.ledger / "events" / item_id
            events.mkdir(parents=True)
            (events / "00000.json").write_text('{"type":"approved","at":"2026-01-01T00:00:00Z","actor":"x","quote":"Alexander: \'ok go\'"}')
        result, seconds = self.timed("ready", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual((body["total"], len(body["items"])), (5000, 30))
        self.assertLess(seconds, 20)


if __name__ == "__main__":
    unittest.main()
