"""Walks of `qew migrate-ids --to base35-time`: letters, counter and imported ids become time ids.

Adversary: a reference left pointing at a vanished id, history lost or renumbered, a rerun that
migrates twice, a crash that leaves a ledger `qew check` refuses, another login's claim yanked
from under them, and a forge issue that carries the old marker being duplicated.
"""

import hashlib
import json
import re
import shutil
import unittest
from pathlib import Path
from unittest import mock

from tests.ledger_harness import QUOTE, World  # first: it puts src/ on the path
from tests.memory_forge import MemoryForge
from qew import id_migration, ledger_check, ledger_config, ledger_store
from qew.forge import marker_for
from qew.forge_pull import pull_target
from qew.ledger_import import import_target
from qew.ledger_store import open_ledger
from qew.sync import Target, sync_target

TIME = re.compile(r"^[0-9a-km-z]{9}$")
MIRRORS = '\n[[mirror]]\nforge = "forgejo"\nrepo = "https://git.example/o/r"\n'
OFF = "\n[replication]\nauto_forge_sync = false\nauto_push = false\n"


class Killed(BaseException):
    """Stands in for a kill: nothing in the command can catch or roll it back."""


def letters_world(**options) -> World:
    world = World(**options)
    path = world.ledger / "qew-ledger.toml"
    path.write_text(path.read_text() + 'id_mode = "letters"\n')
    return world


def tree(root: Path) -> dict:
    """Every ledger file (not the VCS or the local cache) with its content hash."""
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".git" not in path.parts and ".qew" not in path.parts
    }


class Fixture:
    """Epic A, issues B and C (C needs B, both under A); B is claimed, C has an edge event and a comment."""

    def __init__(self, world: World) -> None:
        self.w = world
        self.epic = world.create("--kind", "epic", "--title", "Epic", "--quote", QUOTE)
        self.first = world.create("--title", "First", "--parent", self.epic, "--quote", QUOTE)
        self.second = world.create("--title", "Second", "--parent", self.epic, "--quote", QUOTE)
        assert (self.epic, self.first, self.second) == ("A", "B", "C")
        world.run("set", self.second, "--needs", self.first)
        world.run("comment", self.second, "--body", "keep this")
        assert world.run("start", self.first).returncode == 0


class MigrateIdsWalk(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.template = letters_world(ledger_vcs="git")  # built once; every walk starts from a copy of its ledger
        Fixture(cls.template)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.template.close()

    def setUp(self) -> None:
        self.world = letters_world(ledger_vcs="git")
        self.addCleanup(self.world.close)
        shutil.rmtree(self.world.ledger)
        shutil.copytree(self.template.ledger, self.world.ledger)

    def ids(self) -> list:
        return sorted(path.stem for path in (self.world.ledger / "items").glob("*.md"))

    def show(self, reference: str) -> dict:
        result = self.world.run("show", reference, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_letters_become_time_ids_with_every_reference_and_event_kept(self) -> None:
        """Prevents a migration that drops a parent or needs edge, renumbers history or loses an event."""
        w = self.world
        names = {i: sorted(path.name for path in (w.ledger / "events" / i).glob("*.json")) for i in "ABC"}
        before = {i: [(e["type"], e["at"], e["actor"]) for e in w.events(i)] for i in "ABC"}
        result = w.run("migrate-ids", "--to", "base35-time")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.ids()), 3)
        self.assertTrue(all(TIME.match(i) for i in self.ids()), self.ids())
        self.assertEqual(w.run("check").returncode, 0, w.run("check").stdout)
        epic, first, second = (self.show(i) for i in "ABC")
        self.assertEqual((epic["title"], first["title"], second["title"]), ("Epic", "First", "Second"))
        self.assertEqual(first["parent"]["id"], epic["id"])
        self.assertEqual(second["parent"]["id"], epic["id"])
        self.assertEqual(second["needs"], [first["id"]])
        for old, now in zip("ABC", (epic["id"], first["id"], second["id"])):
            self.assertEqual(sorted(path.name for path in (w.ledger / "events" / now).glob("*.json")), names[old], f"event ids of {old}")
            self.assertEqual([(e["type"], e["at"], e["actor"]) for e in w.events(now)], before[old], f"events of {old}")
            self.assertFalse((w.ledger / "events" / old).exists())
        self.assertIn("aliases: B", (w.ledger / "items" / f"{first['id']}.md").read_text())

    def test_an_old_id_still_resolves_and_the_answer_names_the_new_id(self) -> None:
        """Prevents notes, commits and branches that cite the old id from going dead."""
        self.world.run("migrate-ids", "--to", "base35-time")
        new = self.show("B")["id"]
        self.assertRegex(new, TIME)
        shown = self.world.run("show", "B")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn(f"id: {new}", shown.stdout)
        self.assertEqual(self.world.run("comment", "B", "--body", "by old id").returncode, 0)
        self.assertEqual(len(self.ids()), 3)

    def test_the_migration_is_one_commit_and_a_rerun_changes_nothing(self) -> None:
        """Prevents a history of per-item commits, and a second run renaming or aliasing again."""
        w = self.world
        commits = len(w.ledger_log())
        self.assertEqual(w.run("migrate-ids", "--to", "base35-time").returncode, 0)
        self.assertEqual(len(w.ledger_log()), commits + 1)
        self.assertTrue(w.ledger_log()[0].startswith("qew: migrate-ids"), w.ledger_log()[0])
        settled = tree(w.ledger)
        again = w.run("migrate-ids", "--to", "base35-time")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("nothing to migrate", again.stdout)
        self.assertEqual(tree(w.ledger), settled)
        self.assertEqual(len(w.ledger_log()), commits + 1)

    def test_dry_run_names_the_items_and_writes_nothing(self) -> None:
        """Prevents a preview that already renamed files."""
        w = self.world
        settled = tree(w.ledger)
        result = w.run("migrate-ids", "--to", "base35-time", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        for old in "ABC":
            self.assertIn(old, result.stdout.split())
        self.assertEqual(tree(w.ledger), settled)
        self.assertEqual(self.ids(), ["A", "B", "C"])

    def test_an_item_claimed_by_someone_else_refuses_the_whole_migration(self) -> None:
        """Prevents rewriting the id under another login's active claim."""
        w = self.world
        w.run("release", "B", "--reason", "handoff")
        self.assertEqual(w.run("start", "B", env={"QEW_ACTOR": "other"}).returncode, 0)
        settled = tree(w.ledger)
        result = w.run("migrate-ids", "--to", "base35-time")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("other", result.stderr)
        self.assertEqual(tree(w.ledger), settled)

    def test_only_time_is_a_target(self) -> None:
        """Prevents a typo migrating the ledger to an id mode that does not exist."""
        result = self.world.run("migrate-ids", "--to", "letters")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("letters", result.stderr)
        self.assertEqual(self.ids(), ["A", "B", "C"])

    def test_a_kill_at_any_write_leaves_a_checkable_ledger_that_a_rerun_finishes(self) -> None:
        """Prevents a crash stranding half-renamed items, orphan events or dangling references."""
        w = self.world
        pristine = Path(w.base) / "pristine"
        shutil.copytree(w.ledger, pristine, ignore=shutil.ignore_patterns(".qew"))
        steps = 0
        for limit in range(1, 200):
            shutil.rmtree(w.ledger)
            shutil.copytree(pristine, w.ledger)
            calls = {"n": 0}
            real_write, real_discard = ledger_store._write, ledger_store.discard

            def counted(real):
                def wrapper(*args, **kwargs):
                    calls["n"] += 1
                    if calls["n"] > limit:
                        raise Killed()
                    return real(*args, **kwargs)
                return wrapper

            ledger = open_ledger(w.ledger, "TST")
            with mock.patch.object(ledger_store, "_write", counted(real_write)), \
                    mock.patch.object(ledger_store, "discard", counted(real_discard)):
                try:
                    id_migration.migrate(ledger, "tester")
                except Killed:
                    pass
                else:
                    steps = limit
                    break
            self.assertEqual(ledger_check.check(ledger), [], f"after {limit} writes")
            id_migration.migrate(ledger, "tester")
            self.assertTrue(all(TIME.match(i) for i in self.ids()), f"after {limit} writes: {self.ids()}")
            self.assertEqual(ledger_check.check(ledger), [], f"finished after {limit} writes")
        self.assertEqual(w.run("check").returncode, 0)
        self.assertEqual(self.show("C")["needs"], [self.show("B")["id"]])
        self.assertGreater(steps, 8, "the migration was never killed midway")

    def test_events_that_cite_ids_are_rewritten_and_mirror_numbers_are_kept(self) -> None:
        """Prevents an edge or close event, or a pull request mirror, naming an id that no longer exists."""
        w = self.world
        w.run("set", "C", "--needs")
        w.run("set", "C", "--needs", "B")
        mirror_dir = w.ledger / "mirrors" / "B"
        mirror_dir.mkdir(parents=True)
        record = {"forge": "forgejo", "url": "https://git.example/o/r", "kind": "pr", "number": 7, "items": ["B", "C"]}
        (mirror_dir / "forgejo-git.example-o-r-pr-7.json").write_text(json.dumps(record))
        self.assertEqual(w.run("migrate-ids", "--to", "base35-time").returncode, 0)
        first, second = self.show("B")["id"], self.show("C")["id"]
        moved = json.loads((w.ledger / "mirrors" / first / "forgejo-git.example-o-r-pr-7.json").read_text())
        self.assertEqual((moved["number"], moved["items"]), (7, [first, second]))
        self.assertFalse((w.ledger / "mirrors" / "B").exists())
        edges = [e for e in w.events(second) if e["type"] == "edge"]
        self.assertTrue(edges)
        self.assertTrue(all(e["need"] == first for e in edges if e["op"] == "add"), edges)
        self.assertEqual(w.run("check").returncode, 0)


class MigrateMirrorWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = letters_world(mirrors=MIRRORS, replication=OFF)
        self.addCleanup(self.world.close)
        self.forge = MemoryForge("git.example")
        self.config = ledger_config.load(self.world.code)
        self.ledger = open_ledger(self.world.ledger, "TST")
        mirror = self.config.mirrors[0]
        self.target = Target(mirror, ledger_config.mirror_slug(mirror), self.forge)
        self.item = self.world.create("--kind", "issue", "--title", "Mirrored", "--quote", QUOTE)
        sync_target(self.ledger, self.target, self.config)
        self.assertIn(marker_for("A"), self.forge.issues[1].body)
        self.assertEqual(self.world.run("migrate-ids", "--to", "base35-time").returncode, 0)
        self.new = next((self.world.ledger / "items").glob("*.md")).stem

    def test_the_mirror_follows_the_item_and_the_next_sync_rewrites_only_the_marker(self) -> None:
        """Prevents a second forge issue for a migrated item, or a forge number changing."""
        records = list((self.world.ledger / "mirrors" / self.new).glob("*.json"))
        self.assertEqual(len(records), 1)
        self.assertEqual(json.loads(records[0].read_text())["number"], 1)
        sync_target(open_ledger(self.world.ledger, "TST"), self.target, self.config)
        self.assertEqual(len(self.forge.issues), 1)
        self.assertIn(marker_for(self.new), self.forge.issues[1].body)
        self.assertNotIn(marker_for("A"), self.forge.issues[1].body)

    def test_an_issue_still_carrying_the_old_marker_is_adopted_not_duplicated(self) -> None:
        """Prevents the first sync after a migration (mirror file lost, forge body not yet updated) creating a twin."""
        shutil.rmtree(self.world.ledger / "mirrors")
        shutil.rmtree(self.world.ledger / ".qew" / "state", ignore_errors=True)
        sync_target(open_ledger(self.world.ledger, "TST"), self.target, self.config)
        self.assertEqual(len(self.forge.issues), 1)
        records = list((self.world.ledger / "mirrors" / self.new).glob("*.json"))
        self.assertEqual(json.loads(records[0].read_text())["number"], 1)
        self.assertIn(marker_for(self.new), self.forge.issues[1].body)

    def test_pull_and_import_treat_an_old_marker_as_the_same_item(self) -> None:
        """Prevents an import restoring the old id as a second item next to its renamed self."""
        shutil.rmtree(self.world.ledger / "mirrors")
        shutil.rmtree(self.world.ledger / ".qew" / "state", ignore_errors=True)
        ledger = open_ledger(self.world.ledger, "TST")
        pull_target(ledger, self.target)
        import_target(ledger, self.target)
        self.assertEqual([p.stem for p in (self.world.ledger / "items").glob("*.md")], [self.new])


if __name__ == "__main__":
    unittest.main()
