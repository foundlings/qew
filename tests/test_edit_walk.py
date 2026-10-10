"""Walks of body authoring: `--edit` through VISUAL/EDITOR and `--body-file` safety.

Adversary: an editor failure turning into an empty or stale body, a shell-run
editor string, a body read through a symlink, and temp files left behind.
"""

from pathlib import Path
import stat
import unittest

from tests.ledger_harness import World


def snapshot_of(world: World) -> dict:
    return {str(p.relative_to(world.ledger)): p.read_bytes() for p in world.ledger.rglob("*")
            if p.is_file() and ".git" not in p.parts and ".qew" not in p.parts}


class EditWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)
        self.scripts = self.world.base / "scripts"
        self.scripts.mkdir()

    def editor(self, body: str, status: int = 0) -> str:
        script = self.scripts / f"editor{len(list(self.scripts.iterdir()))}.sh"
        script.write_text(f'#!/bin/sh\nprintf %s "$QEW_TEST_BODY" > "$1"\necho "$1" >> "{self.scripts}/paths"\nexit {status}\n')
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        self.body = body
        return str(script)

    def run_edit(self, editor: str, *arguments: str, visual: bool = True):
        env = {"VISUAL": editor if visual else "", "EDITOR": "" if visual else editor, "QEW_TEST_BODY": getattr(self, "body", "")}
        return self.world.run(*arguments, env=env)

    def test_edit_uses_the_editor_output_as_the_body_for_create_and_set(self) -> None:
        """The editor's file becomes the body; VISUAL wins and EDITOR is the fallback; the temp dir is removed."""
        created = self.run_edit(self.editor("from the editor\n"), "create", "--title", "T", "--edit")
        self.assertEqual(created.returncode, 0, created.stderr)
        item = created.stdout.split()[0]
        self.assertIn("from the editor", (self.world.ledger / "items" / f"{item}.md").read_text())
        changed = self.run_edit(self.editor("second body\n"), "set", item, "--edit", visual=False)
        self.assertEqual(changed.returncode, 0, changed.stderr)
        self.assertIn("second body", (self.world.ledger / "items" / f"{item}.md").read_text())
        for path in (self.scripts / "paths").read_text().split():
            self.assertFalse(Path(path).parent.exists(), "temp dir must be removed")

    def test_missing_or_blank_editor_is_refused(self) -> None:
        """No VISUAL and no EDITOR (or only blanks): exit 2 naming both, ledger untouched."""
        before = snapshot_of(self.world)
        result = self.world.run("create", "--title", "T", "--edit", env={"VISUAL": " ", "EDITOR": ""})
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("VISUAL", result.stderr)
        self.assertIn("EDITOR", result.stderr)
        self.assertEqual(snapshot_of(self.world), before)

    def test_failing_editor_empty_result_or_unparseable_command_writes_nothing(self) -> None:
        """A non-zero editor exit, a blank body and a broken quote each refuse before any write."""
        before = snapshot_of(self.world)
        for editor in (self.editor("text", status=1), self.editor("   \n"), "'unterminated"):
            result = self.run_edit(editor, "create", "--title", "T", "--edit")
            self.assertEqual(result.returncode, 2, editor)
        self.assertEqual(snapshot_of(self.world), before)

    def test_the_editor_command_is_not_run_through_a_shell(self) -> None:
        """Shell metacharacters in EDITOR must not execute; the command is split, not interpreted."""
        marker = self.world.base / "pwned"
        result = self.run_edit(f"true; touch {marker}", "create", "--title", "T", "--edit")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(marker.exists())

    def test_body_file_symlink_and_empty_file_are_refused(self) -> None:
        """A link could point at any file; an empty body file is a mistake, not a body."""
        real = self.world.base / "secret.txt"
        real.write_text("secret")
        link = self.world.base / "link.txt"
        link.symlink_to(real)
        empty = self.world.base / "empty.txt"
        empty.write_text("")
        before = snapshot_of(self.world)
        for path in (link, empty):
            result = self.world.run("create", "--title", "T", "--body-file", str(path))
            self.assertEqual(result.returncode, 2, path)
        self.assertEqual(snapshot_of(self.world), before)

    def test_edit_cannot_be_combined_with_another_body_source(self) -> None:
        """Two body sources are ambiguous."""
        result = self.run_edit(self.editor("x"), "create", "--title", "T", "--edit", "--body", "y")
        self.assertEqual(result.returncode, 2)

    def test_set_with_no_change_writes_nothing_and_a_closed_item_needs_a_flag(self) -> None:
        """`set` with no change appends no event; editing closed work is explicit."""
        item = self.world.create("--title", "T")
        before = snapshot_of(self.world)
        self.assertIn("unchanged", self.world.run("set", item, "--title", "T").stdout)
        self.assertEqual(snapshot_of(self.world), before)
        self.assertEqual(self.world.run("close", item, "--reason", "not_planned").returncode, 0)
        refused = self.world.run("set", item, "--title", "New")
        self.assertEqual(refused.returncode, 2)
        self.assertEqual(self.world.run("set", item, "--title", "New", "--closed").returncode, 0)


if __name__ == "__main__":
    unittest.main()
