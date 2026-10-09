"""Item attachments: files and subdirectories owned by an item, named by events, merged without conflicts.

Each test is a scenario; its docstring names the failure it prevents. Bytes are immutable files named by their
content hash under items/ID/attachments/; events map relative paths to hashes (last event by id wins).
"""

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World

EVERY = "\n[replication]\nfetch_interval_s = 0\nauto_forge_sync = false\nforge_concurrency = 1\n"
OFFLINE = {"QEW_OFFLINE": "1"}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class AttachmentsWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(ledger_vcs="git")
        self.addCleanup(self.world.close)
        self.item = self.world.create("--kind", "issue", "--title", "Has files", "--quote", QUOTE)
        self.source = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.source, ignore_errors=True))

    def file(self, name: str, data: bytes) -> Path:
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def attach(self, *arguments: str):
        return self.world.run("attach", self.item, *arguments)

    def assertRefused(self, result, label: object = "") -> None:
        """A refusal of the attach command itself (exit 2), not the CLI not knowing the command."""
        self.assertEqual(result.returncode, 2, (label, result.stdout, result.stderr))
        self.assertNotIn("invalid choice", result.stderr)

    def show(self) -> str:
        return self.world.run("show", self.item).stdout

    def test_an_attached_file_is_stored_by_hash_listed_and_exported_byte_for_byte(self) -> None:
        """Prevents lost or altered bytes: the file lands content-addressed, show names it, export restores it exactly."""
        data = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 40  # binary, not valid text
        result = self.attach(str(self.file("diagram.png", data)), "--as", "design/diagram.png")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        blob = self.world.ledger / "items" / self.item / "attachments" / f"{sha(data)}.png"
        self.assertEqual(blob.read_bytes(), data)
        listing = self.show()
        self.assertIn("design/diagram.png", listing)
        self.assertIn(sha(data)[:12], listing)
        target = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(target, ignore_errors=True))
        self.assertEqual(self.world.run("export", self.item, str(target)).returncode, 0)
        self.assertEqual((target / "design" / "diagram.png").read_bytes(), data)
        self.assertEqual(self.world.run("check").returncode, 0)

    def test_a_directory_keeps_its_subdirectories_in_one_commit(self) -> None:
        """Prevents a flat copy: attaching a tree keeps relative paths, and the whole attach is one ledger commit."""
        self.file("data/a.json", b'{"a": 1}')
        self.file("data/deep/b.toml", b"b = 2\n")
        self.file("notes.md", b"# notes\n")
        before = len(self.world.ledger_log())
        result = self.attach(str(self.source), "--as", "phase")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        listing = self.show()
        for name in ("phase/data/a.json", "phase/data/deep/b.toml", "phase/notes.md"):
            self.assertIn(name, listing)
        self.assertEqual(len(self.world.ledger_log()) - before, 1)

    def test_a_replacement_wins_a_rerun_is_a_no_op_and_old_bytes_stay_for_history(self) -> None:
        """Prevents rewriting history: same bytes again adds nothing, new bytes win, the old blob stays on disk."""
        first, second = b"first", b"second"
        self.attach(str(self.file("f.txt", first)), "--as", "f.txt")
        again = self.attach(str(self.file("f.txt", first)), "--as", "f.txt")
        self.assertIn("unchanged", again.stdout)
        self.assertEqual(sum(1 for e in self.world.events(self.item) if e["type"] == "attached"), 1)
        self.attach(str(self.file("f.txt", second)), "--as", "f.txt")
        listing = self.show()
        self.assertIn(sha(second)[:12], listing)
        self.assertNotIn(sha(first)[:12], listing)
        self.assertTrue((self.world.ledger / "items" / self.item / "attachments" / f"{sha(first)}.txt").exists())

    def test_detach_removes_a_name_or_a_whole_directory_but_keeps_the_bytes(self) -> None:
        """Prevents orphaned names: detaching a directory name detaches everything under it; bytes stay for history."""
        self.file("d/x.txt", b"x")
        self.file("d/y.txt", b"y")
        self.attach(str(self.source), "--as", "all")
        self.assertEqual(self.world.run("detach", self.item, "all/d/x.txt").returncode, 0)
        self.assertNotIn("all/d/x.txt", self.show())
        self.assertIn("all/d/y.txt", self.show())
        self.assertEqual(self.world.run("detach", self.item, "all").returncode, 0)
        self.assertNotIn("all/d", self.show())
        self.assertTrue((self.world.ledger / "items" / self.item / "attachments" / f"{sha(b'x')}.txt").exists())
        self.assertEqual(self.world.run("check").returncode, 0)

    def test_unsafe_names_a_missing_source_and_an_oversize_file_are_refused_with_nothing_written(self) -> None:
        """Prevents path escapes and half-written attaches: bad names, missing sources and over-limit files write nothing."""
        good = self.file("ok.txt", b"ok")
        for bad in ("../escape", "/abs/path", "a//b", "a/./b", ".", "", "a\\b", "x" * 300):
            self.assertRefused(self.attach(str(good), "--as", bad), bad)
        self.assertRefused(self.attach(str(self.source / "missing.txt")))
        big = self.file("big.bin", b"0" * 2048)
        limited = self.world.run("attach", self.item, str(big), env={"QEW_ATTACH_MAX_BYTES": "1024"})
        self.assertRefused(limited)
        self.assertEqual([e for e in self.world.events(self.item) if e["type"] == "attached"], [])
        self.assertFalse((self.world.ledger / "items" / self.item / "attachments").exists())

    def test_a_file_and_a_directory_cannot_share_a_name(self) -> None:
        """Prevents an unexportable tree: `a` as a file and `a/b` as a file cannot both exist."""
        self.attach(str(self.file("one.txt", b"1")), "--as", "a")
        self.assertRefused(self.attach(str(self.file("two.txt", b"2")), "--as", "a/b"))

    def test_check_fails_on_a_missing_blob_corrupt_bytes_and_a_dangling_reference(self) -> None:
        """Prevents silent damage: a deleted blob, flipped bytes or a body link to nothing fail qew check, naming it."""
        data = b"payload"
        self.attach(str(self.file("p.txt", data)), "--as", "p.txt")
        blob = self.world.ledger / "items" / self.item / "attachments" / f"{sha(data)}.txt"
        blob.write_bytes(b"tampered")
        corrupt = self.world.run("check")
        self.assertEqual(corrupt.returncode, 1, corrupt.stdout)
        self.assertIn("hash", corrupt.stdout)
        blob.unlink()
        missing = self.world.run("check")
        self.assertEqual(missing.returncode, 1, missing.stdout)
        self.assertIn("missing", missing.stdout)
        blob.write_bytes(data)
        self.assertEqual(self.world.run("check").returncode, 0)
        self.world.run("set", self.item, "--body", "see [plan](attachment:nope/plan.md)")
        dangling = self.world.run("check")
        self.assertEqual(dangling.returncode, 1, dangling.stdout)
        self.assertIn("nope/plan.md", dangling.stdout)

    def test_a_body_reference_to_an_attached_name_passes_check(self) -> None:
        """Prevents false alarms: a body link to an attached name is valid."""
        self.assertEqual(self.attach(str(self.file("plan.md", b"plan")), "--as", "docs/plan.md").returncode, 0)
        self.world.run("set", self.item, "--body", "see [plan](attachment:docs/plan.md)")
        self.assertEqual(self.world.run("check").returncode, 0)


class AttachmentsMergeWalk(unittest.TestCase):
    def test_two_machines_attaching_different_bytes_under_one_name_merge_without_a_conflict(self) -> None:
        """Prevents git conflicts on attachments: bytes are named by hash and events are separate files, so the merge is
        clean and every checkout shows the same winner (the later event)."""
        a = World(ledger_vcs="git", remote=True, replication=EVERY)
        self.addCleanup(a.close)
        b = a.clone("b", replication=EVERY)
        item = a.create("--kind", "issue", "--title", "Shared", "--quote", QUOTE)
        b.run("list")
        source = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(source, ignore_errors=True))
        (source / "mine.txt").write_bytes(b"from a")
        (source / "yours.txt").write_bytes(b"from b")
        self.assertEqual(a.run("attach", item, str(source / "mine.txt"), "--as", "same.txt", env=OFFLINE).returncode, 0)
        self.assertEqual(b.run("attach", item, str(source / "yours.txt"), "--as", "same.txt", env=OFFLINE).returncode, 0)
        for world in (a, b, a, b):
            self.assertEqual(world.run("ready").returncode, 0)
        shown = [world.run("show", item).stdout for world in (a, b)]
        winners = {sha(b"from a")[:12] in text for text in shown} | {sha(b"from b")[:12] in text for text in shown}
        self.assertEqual(len({sha(b"from a")[:12] in text for text in shown}), 1, "both checkouts must pick the same winner")
        self.assertTrue(winners)
        for world in (a, b):
            self.assertEqual(world.run("check").returncode, 0)
            self.assertEqual(world.ledger_dirty(), [])


if __name__ == "__main__":
    unittest.main()
