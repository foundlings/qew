"""A large attachment reaches the commit and the remote on a jj ledger: jj refuses to snapshot a new file over its
`snapshot.max-new-file-size` (1 MiB by default), so qew's own jj commands raise that limit above the 100 MiB attachment limit, a stray
refused file is committed by the next write, and `qew check` says when an attachment file is not in the committed tree.

Each test is a scenario; its docstring names the failure it prevents.
"""

import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World, git, jj, jj_available

THREE_MIB = 3 * 1024 * 1024


def remote_files(world: World) -> list[str]:
    return git(world.base, "--git-dir", str(world.remote), "ls-tree", "-r", "--name-only", "main").splitlines()


@unittest.skipUnless(jj_available(), "jj is not installed")
class BigAttachmentJjWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(ledger_vcs="jj", remote=True)
        self.addCleanup(self.world.close)
        self.item = self.world.create("--title", "Needs a file", "--quote", QUOTE)

    def big(self, name: str = "big.bin") -> str:
        path = self.world.base / name
        path.write_bytes(os.urandom(THREE_MIB))
        return str(path)

    def attachments(self) -> list[str]:
        return [f for f in remote_files(self.world) if "/attachments/" in f]

    def test_a_three_mib_attachment_is_committed_and_pushed(self) -> None:
        """Prevents an attachment written to disk and never pushed: jj's 1 MiB default must not stop a 3 MiB file."""
        out = self.world.run("item", "attach", self.item, self.big())
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(len(self.attachments()), 1, remote_files(self.world))
        self.assertEqual(self.world.run("check").returncode, 0)
        self.assertNotIn("Refused to snapshot", jj(self.world.ledger, "st"))

    def test_a_file_jj_already_refused_is_committed_by_the_next_write(self) -> None:
        """Prevents a stranded file staying stranded: the recovery of the next write snapshots it with the raised limit."""
        stray = self.world.home("items", self.item) / "attachments" / "stray.bin"
        stray.parent.mkdir(parents=True, exist_ok=True)
        stray.write_bytes(os.urandom(THREE_MIB))
        self.assertIn("Refused to snapshot", subprocess.run(["jj", "st"], cwd=self.world.ledger, capture_output=True, text=True).stderr)
        self.assertEqual(self.world.run("create", "--title", "Any write", "--quote", QUOTE).returncode, 0)
        self.assertEqual(self.attachments(), [f"items/{self.item}/attachments/stray.bin"])


class UncommittedAttachmentWalk(unittest.TestCase):
    def test_check_names_an_attachment_file_that_is_not_in_the_committed_tree(self) -> None:
        """Prevents silent loss: a blob on disk that was never committed is a problem `qew check` reports, with the way out."""
        world = World(ledger_vcs="git", remote=True)
        self.addCleanup(world.close)
        item = world.create("--title", "Needs a file", "--quote", QUOTE)
        source = world.base / "note.txt"
        source.write_text("hello\n")
        self.assertEqual(world.run("item", "attach", item, str(source)).returncode, 0)
        self.assertEqual(world.run("check").returncode, 0)
        blob = next(p for p in (world.home("items", item) / "attachments").iterdir())
        git(world.ledger, "rm", "-q", "--cached", str(blob.relative_to(world.ledger)))
        git(world.ledger, "commit", "-q", "-m", "lose the blob")
        result = world.run("check")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("not committed", result.stdout + result.stderr)
        self.assertIn(blob.name, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
