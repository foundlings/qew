"""What the forge issue shows for attachments: links into the ledger repository, never uploaded bytes.

Each test is a scenario; its docstring names the failure it prevents. The section sits in the generated footer, so a
pull never imports it as the item's body.
"""

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

OFF = "\n[replication]\nauto_forge_sync = false\nauto_push = false\nforge_concurrency = 1\n"
ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}
WEB = "https://github.com/acme/widgets-ledger"


class AttachmentMirrorWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub().start()
        self.addCleanup(self.fake.stop)

    def rig(self, options: str = f'ledger_web_url = "{WEB}"\n') -> tuple[World, str, Path]:
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n{options}'
        world = World(mirrors, replication=OFF)
        self.addCleanup(world.close)
        item = world.create("--kind", "issue", "--title", "With files", "--body", "The real body.", "--quote", QUOTE)
        source = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(source, ignore_errors=True))
        return world, item, source

    def body(self) -> str:
        return next(iter(self.fake.issues.values()))["body"]

    def test_attached_files_are_listed_with_links_into_the_ledger_repository(self) -> None:
        """Prevents invisible attachments or uploaded binaries: the issue lists name, size and a link to the stored file."""
        world, item, source = self.rig()
        data = b"\x89PNG" + bytes(range(200))
        (source / "d.png").write_bytes(data)
        self.assertEqual(world.run("item", "attach", item, str(source / "d.png"), "--as", "design/d.png").returncode, 0)
        self.assertEqual(world.run("sync", env=ENV).returncode, 0)
        digest = hashlib.sha256(data).hexdigest()
        body = self.body()
        self.assertIn("Attachments", body)
        self.assertIn("design/d.png", body)
        self.assertIn(f"{WEB}/blob/main/items/{item}/attachments/{digest}.png", body)
        self.assertNotIn("\x89PNG", body)

    def test_detaching_removes_the_section_and_a_forge_edit_of_it_is_not_imported(self) -> None:
        """Prevents the footer leaking into the item: forge text is untrusted, and the generated section is never the body."""
        world, item, source = self.rig()
        (source / "a.txt").write_bytes(b"a")
        world.run("item", "attach", item, str(source / "a.txt"))
        world.run("sync", env=ENV)
        self.assertIn("a.txt", self.body())
        issue = next(iter(self.fake.issues.values()))
        issue["body"] = issue["body"].replace("a.txt", "a.txt EDITED BY A HUMAN")
        world.run("sync", env=ENV)
        self.assertNotIn("EDITED", world.run("show", item).stdout)
        self.assertIn("divergences=0", world.run("status").stdout)
        self.assertEqual(world.run("item", "detach", item, "a.txt").returncode, 0)
        world.run("sync", env=ENV)
        self.assertNotIn("Attachments", self.body())

    def test_without_a_web_address_the_names_are_listed_without_links(self) -> None:
        """Prevents broken links: with no ledger web address known the section still names the files, plainly."""
        world, item, source = self.rig(options="")
        (source / "b.txt").write_bytes(b"b")
        world.run("item", "attach", item, str(source / "b.txt"))
        world.run("sync", env=ENV)
        body = self.body()
        self.assertIn("Attachments", body)
        self.assertIn("b.txt", body)
        self.assertNotIn("](http", body)


if __name__ == "__main__":
    unittest.main()
