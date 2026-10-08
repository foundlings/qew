"""Walks of the read contract: one machine shape per read command, refusal of an unreadable ledger,
what `ready` holds back, and the pinned row order.

Adversary: a consumer parsing half an answer, a field added to a bare array, a claimed or unapproved
item offered as ready work, and low-priority work listed first.
"""

import json
from pathlib import Path
import re
import unittest

from tests.ledger_harness import QUOTE, World

ROOT = Path(__file__).resolve().parents[1]
READS = (("list",), ("ready",), ("blocked",), ("graph",), ("draft", "list"))


def lines(result) -> list:
    return [json.loads(line) for line in result.stdout.splitlines()]


def spec_shapes() -> dict:
    """`shape NAME: required keys | optional keys` lines of docs/specs/commands.md."""
    shapes = {}
    for line in (ROOT / "docs/specs/commands.md").read_text(encoding="utf-8").splitlines():
        found = re.fullmatch(r"shape (\w+): ([a-z_ ]*?)( \| ([a-z_ ]*))?", line.strip())
        if found:
            shapes[found.group(1)] = (set(found.group(2).split()), set((found.group(4) or "").split()))
    return shapes


class ContractWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def ready_item(self, title: str, *extra: str) -> str:
        item = self.world.create("--title", title, *extra)
        self.world.approve(item)
        return item

    def test_list_prints_one_versioned_object_per_line(self) -> None:
        """A consumer reading line by line gets a row per item; a new field cannot break a bare array."""
        a, b = self.ready_item("A"), self.ready_item("B")
        rows = lines(self.world.run("list", "--json"))
        self.assertEqual({row["id"] for row in rows}, {a, b})
        self.assertTrue(all(row["schema_version"] == 1 for row in rows))

    def test_ready_is_an_envelope_with_sorted_keys_and_is_byte_stable(self) -> None:
        """Two runs on one ledger are byte-identical and carry total and truncated."""
        self.ready_item("A")
        self.ready_item("B", "--label", "zeta", "--label", "alpha")
        first = self.world.run("ready", "--json").stdout
        self.assertEqual(first, self.world.run("ready", "--json").stdout)
        body = json.loads(first)
        self.assertEqual((body["total"], body["truncated"], body["schema_version"]), (2, False, 1))
        self.assertEqual(len(body["items"]), 2)
        self.assertEqual(first.strip(), json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        self.assertIn(["alpha", "zeta"], [row["labels"] for row in body["items"]])

    def test_every_read_refuses_a_corrupt_item_among_good_ones(self) -> None:
        """One bad file makes each read exit 1 with nothing on stdout and the file named; a repair restores it."""
        ids = [self.ready_item(f"Item {n}") for n in range(10)]
        bad = self.world.ledger / "items" / f"{ids[4]}.md"
        good = bad.read_text()
        bad.write_text(good.replace("\n---\n", "\nstatus: done\n---\n", 1))
        for command in (*READS, ("show", ids[0])):
            result = self.world.run(*command)
            self.assertEqual((result.returncode, result.stdout), (1, ""), command)
            self.assertIn(ids[4], result.stderr, command)
        bad.write_text(good)
        for command in READS:
            self.assertEqual(self.world.run(*command).returncode, 0, command)

    def test_every_read_refuses_history_that_belongs_to_no_item(self) -> None:
        """An event directory for a missing item is never ignored."""
        self.ready_item("A")
        orphan = self.world.ledger / "events" / "ghost"
        orphan.mkdir()
        (orphan / "0000000000.json").write_text('{"type":"drafted","at":"2026-01-01T00:00:00Z","actor":"x"}')
        for command in READS:
            result = self.world.run(*command)
            self.assertEqual((result.returncode, result.stdout), (1, ""), command)
            self.assertIn("ghost", result.stderr, command)

    def test_spec_shapes_match_what_the_commands_emit(self) -> None:
        """A key a command emits that the spec lacks, or a required key it omits, fails here."""
        shapes = spec_shapes()
        parent = self.world.create("--kind", "epic", "--title", "Plan")
        need = self.ready_item("Need", "--parent", parent, "--label", "area:x")
        blocked = self.ready_item("Blocked", "--needs", need)
        self.world.run("start", need)
        ready = json.loads(self.world.run("ready", "--json").stdout)
        blocked_body = json.loads(self.world.run("blocked", "--json").stdout)
        self.assertTrue(ready["items"] and blocked_body["items"])
        probes = {
            "row": [*lines(self.world.run("list", "--json")), *ready["items"]],
            "envelope": [ready, blocked_body],
            "blocked_row": blocked_body["items"],
            "edge": lines(self.world.run("graph", "--json")),
            "show": [json.loads(self.world.run("show", blocked, "--json").stdout)],
        }
        for name, objects in probes.items():
            self.assertIn(name, shapes, f"commands.md lacks `shape {name}:`")
            required, optional = shapes[name]
            for emitted in objects:
                self.assertLessEqual(set(emitted), required | optional, f"{name} emits unlisted keys")
                self.assertLessEqual(required, set(emitted), f"{name} omits required keys")


class ReadyMatrixWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def make(self, title: str, *extra: str, approve: bool = True) -> str:
        item = self.world.create("--title", title, *extra)
        if approve:
            self.world.approve(item)
        return item

    def test_only_startable_work_is_ready_in_priority_order(self) -> None:
        """A dispatcher never sees claimed, unapproved, reverted, blocked, epic, completed or closed work."""
        w = self.world
        late = self.make("late", "--priority", "3")
        early = self.make("early", "--priority", "0")
        claimed = self.make("claimed")
        w.run("start", claimed)
        draft = self.make("draft with approved label", "--label", "approved", approve=False)
        reverted = self.make("reverted")
        self.assertEqual(w.run("draft", "revert", reverted, "--reason", "no", "--quote", QUOTE).returncode, 0)
        epic = self.make("epic")
        w.run("create", "--title", "child of epic", "--parent", epic)
        open_need = self.make("open need")
        claimed_need = self.make("claimed need")
        w.run("start", claimed_need)
        draft_need = self.make("draft need", approve=False)
        closed_need = self.make("closed need")
        self.assertEqual(w.run("close", closed_need, "--reason", "not_planned").returncode, 0)
        dangling_source = self.make("dangling")
        path = w.ledger / "items" / f"{dangling_source}.md"
        path.write_text(path.read_text().replace("needs: \n", "needs: zzzzzzzzz\n", 1))
        held = [self.make(f"held by {n}", "--needs", need)
                for n, need in enumerate((open_need, claimed_need, draft_need, closed_need))]
        rows = json.loads(w.run("ready", "--json").stdout)["items"]
        self.assertEqual([row["id"] for row in rows], [early, open_need, late])

    def test_completing_the_only_open_need_releases_the_dependent_on_the_next_run(self) -> None:
        """Readiness is derived each run, never cached."""
        w = self.world
        need = self.make("need")
        dependent = self.make("dependent", "--needs", need)
        self.assertNotIn(dependent, w.run("ready").stdout)
        w.run("start", need)
        sha = w.commit("work")
        w.run("evidence", need, "--change", sha)
        self.assertEqual(w.run("complete", need).returncode, 0)
        self.assertIn(dependent, w.run("ready").stdout)


class OrderWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_priority_decides_the_order_in_ready_list_and_blocked(self) -> None:
        """Items created low-priority first still come out p0 first, the same way in all three reads."""
        w = self.world
        low = w.create("--title", "low", "--priority", "3")
        high = w.create("--title", "high", "--priority", "0")
        w.approve(low, high)
        self.assertEqual([r.split()[0] for r in w.run("ready").stdout.splitlines()], [high, low])
        self.assertEqual([r.split()[0] for r in w.run("list").stdout.splitlines()], [high, low])
        gate = w.create("--title", "gate", "--priority", "0")
        later = w.create("--title", "later", "--priority", "3", "--needs", gate)
        sooner = w.create("--title", "sooner", "--priority", "0", "--needs", gate)
        w.approve(gate, later, sooner)
        self.assertEqual([r.split()[0] for r in w.run("blocked").stdout.splitlines()], [sooner, later])

    def test_counter_ids_sort_numerically(self) -> None:
        """2 comes before 10; a lexicographic order would not."""
        w = self.world
        path = w.ledger / "qew-ledger.toml"
        path.write_text(path.read_text() + 'id_mode = "counter"\n')
        ids = [w.create("--title", f"T{n}") for n in range(10)]
        w.approve(*ids)
        listed = [row.split()[0] for row in w.run("ready").stdout.splitlines()]
        self.assertEqual(listed, sorted(ids, key=int))
        self.assertLess(listed.index("2"), listed.index("10"))


if __name__ == "__main__":
    unittest.main()
