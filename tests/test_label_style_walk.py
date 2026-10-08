"""Managed labels carry colours and priorities read P0..P4: on every backend, created and existing, no churn.

Adversary: neutral grey labels on the board, `priority:2` instead of `P2`, a recolour of a label a human owns
(`area:x`, `P5`), restyle calls on every sync, and a legacy `priority:N` label flooding divergences or surviving
the relabel.
"""

import json
import os
import sys
import unittest
from dataclasses import replace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_github import TOKEN as GITHUB_TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World
from tests.test_label_creation_walk import SPEC, adapters

QUIET = "\n[replication]\nauto_forge_sync = false\n"
PALETTE = {"P0": ("d73a4a", "Critical priority"), "P1": ("fb8500", "High priority"), "P2": ("fbca04", "Normal priority"),
           "P3": ("2da44e", "Low priority"), "P4": ("8b949e", "Backlog priority")}


def restyles(backend) -> int:
    if backend.name == "github":
        return len(backend.fake.calls("PATCH", "/labels/"))
    if backend.name == "forgejo":
        return len([1 for method, path, _ in backend.fake.requests if method == "PATCH" and "/labels/" in path])
    return len(backend.fake.requests("PUT", "/labels/"))


class LabelStyleBackendWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.backends = adapters(self)

    def test_created_managed_labels_get_the_palette_and_a_users_label_stays_neutral(self) -> None:
        """Prevents grey managed labels on the board: priorities take the Priority field colours, kind labels differ, area:x stays neutral."""
        for b in self.backends:
            labels = ("area:x", "task", "epic", *PALETTE, "qew:approved", "status:Ready")
            b.forge.create_issue(replace(SPEC, labels=labels))
            for name, expected in PALETTE.items():
                self.assertEqual(b.stored(name), expected, f"{b.name} {name}")
            colours = {n: b.stored(n)[0] for n in ("task", "epic", "qew:approved", "status:Ready")}
            self.assertEqual(len(set(colours.values())), 4, f"{b.name} {colours}")
            self.assertNotIn("ededed", colours.values(), b.name)
            self.assertEqual(b.stored("area:x"), ("ededed", "Managed by qew"), b.name)

    def test_existing_neutral_managed_labels_are_recoloured_once_and_a_humans_labels_are_not(self) -> None:
        """Prevents old grey labels staying grey (the board in production) and a human's P5/area label being repainted."""
        for b in self.backends:
            for name in ("P1", "task"):
                b.seed(name, "ededed", "Managed by qew")
            b.seed("P5", "123456", "mine")
            b.seed("area:x", "ff0000", "Tuned by the team")
            b.forge.create_issue(replace(SPEC, labels=("P1", "task", "P5", "area:x")))
            self.assertEqual(b.stored("P1"), PALETTE["P1"], b.name)
            self.assertNotEqual(b.stored("task")[0], "ededed", b.name)
            self.assertEqual(b.stored("P5"), ("123456", "mine"), b.name)
            self.assertEqual(b.stored("area:x"), ("ff0000", "Tuned by the team"), b.name)
            first = restyles(b)
            self.assertGreaterEqual(first, 2, b.name)
            b.forge.create_issue(replace(SPEC, title="U", labels=("P1", "task")))
            self.assertEqual(restyles(b), first, b.name)


class LabelStyleCommandWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub().start()
        self.addCleanup(self.fake.stop)
        mirror = f'\n[[mirror]]\nforge = "github"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'
        self.w = World(mirror, replication=QUIET)
        self.addCleanup(self.w.close)
        self.env = {"GITHUB_TOKEN": GITHUB_TOKEN, "QEW_NO_GH_TOKEN": "1"}

    def labels(self, number: int = 1) -> set:
        return set(self.fake.issues[number]["labels"])

    def test_sync_applies_p_labels_with_colours_and_no_priority_prefix(self) -> None:
        """Prevents `priority:2` on the issue and a grey P2 label: golden sync of a priority-2 and a priority-0 item."""
        self.w.create("--title", "Normal", "--priority", "2", "--quote", QUOTE)
        self.w.create("--title", "Urgent", "--priority", "0", "--quote", QUOTE)
        self.assertEqual(self.w.run("sync", env=self.env).returncode, 0)
        carried = self.labels(1) | self.labels(2)
        self.assertTrue({"P2", "P0"} <= carried, carried)
        self.assertFalse([name for name in carried if name.startswith("priority:")], carried)
        self.assertEqual(self.fake.label_store["P0"]["color"], "d73a4a")
        self.assertEqual(self.fake.label_store["P2"]["color"], "fbca04")

    def test_a_legacy_priority_label_migrates_without_divergence_and_prune_removes_its_definition(self) -> None:
        """Prevents a flood of label divergences after the rename and a stale `priority:2` label surviving."""
        self.w.create("--title", "Old", "--priority", "2", "--quote", QUOTE)
        self.assertEqual(self.w.run("sync", env=self.env).returncode, 0)
        issue = self.fake.issues[1]
        issue["labels"] = ["priority:2" if name == "P2" else name for name in issue["labels"]]
        self.fake.label_store["priority:2"] = {"color": "ededed", "description": "Managed by qew"}
        self.assertEqual(self.w.run("sync", env=self.env).returncode, 0)
        status = json.loads(self.w.run("status", "--json", env=self.env).stdout)
        self.assertEqual(status["divergences"], [])
        self.assertIn("P2", self.labels())
        self.assertNotIn("priority:2", self.labels())
        self.assertEqual(self.w.run("sync", "--prune-labels", env=self.env).returncode, 0)
        self.assertNotIn("priority:2", self.fake.label_store)

    def test_a_humans_p5_label_is_a_user_label_and_p2_is_never_one(self) -> None:
        """Prevents P0-P4 being imported as a user label (or a divergence) while P5 is still the human's to add."""
        item = self.w.create("--title", "Triage", "--priority", "2", "--quote", QUOTE)
        self.assertEqual(self.w.run("sync", env=self.env).returncode, 0)
        self.fake.issues[1]["labels"] = [*self.fake.issues[1]["labels"], "P5"]
        self.assertEqual(self.w.run("sync", env=self.env).returncode, 0)
        self.assertIn("P5", self.w.run("show", item).stdout)
        status = json.loads(self.w.run("status", "--json", env=self.env).stdout)
        self.assertEqual(status["divergences"], [])
        self.assertNotIn("P2", next(l for l in self.w.run("show", item).stdout.splitlines() if l.startswith("labels")))
        refused = self.w.run("create", "--title", "x", "--label", "P2", "--quote", QUOTE)
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)


if __name__ == "__main__":
    unittest.main()
