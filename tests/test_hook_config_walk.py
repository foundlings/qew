"""Behavioral walks for safe agent hook configuration editing."""

import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qew.errors import QewError
from qew.hook_config import (
    install_managed_file,
    remove_managed_file,
    update_json_config,
)


class HookConfigWalk(unittest.TestCase):
    def test_json_install_remove_is_structural_preserving_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / ".agent" / "settings.json"
            target.parent.mkdir()
            target.write_text(
                json.dumps({"theme": "dark", "hooks": {"other": ["keep"]}}),
                encoding="utf-8",
            )

            def install(value: dict) -> dict:
                hooks = value.setdefault("hooks", {})
                hooks["session"] = ["qew prime"]
                return value

            self.assertTrue(update_json_config(root, (".agent", "settings.json"), install))
            self.assertFalse(update_json_config(root, (".agent", "settings.json"), install))
            installed = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(installed["theme"], "dark")
            self.assertEqual(installed["hooks"]["other"], ["keep"])
            self.assertEqual(installed["hooks"]["session"], ["qew prime"])

            def remove(value: dict) -> dict:
                value["hooks"].pop("session", None)
                return value

            self.assertTrue(update_json_config(root, (".agent", "settings.json"), remove))
            self.assertFalse(update_json_config(root, (".agent", "settings.json"), remove))
            removed = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(removed, {"hooks": {"other": ["keep"]}, "theme": "dark"})

    def test_managed_file_install_remove_refuses_unowned_overwrite(self) -> None:
        marker = "// managed by qew"
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            relative = (".agent", "plugin.js")
            content = f"{marker}\nexport const hook = 'qew prime';\n"

            self.assertTrue(install_managed_file(root, relative, content, marker))
            self.assertFalse(install_managed_file(root, relative, content, marker))
            self.assertTrue(remove_managed_file(root, relative, marker))
            self.assertFalse(remove_managed_file(root, relative, marker))

            target = root.joinpath(*relative)
            target.write_text("unrelated plugin\n", encoding="utf-8")
            with self.assertRaisesRegex(QewError, "not managed by Qew"):
                install_managed_file(root, relative, content, marker)
            self.assertEqual(target.read_text(encoding="utf-8"), "unrelated plugin\n")

    def test_symlinked_directory_and_file_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            outside = root / "outside"
            outside.mkdir()
            (root / ".linked").symlink_to(outside, target_is_directory=True)

            with self.assertRaisesRegex(QewError, "symlink"):
                update_json_config(root, (".linked", "settings.json"), lambda value: value)

            safe = root / ".safe"
            safe.mkdir()
            outside_file = outside / "settings.json"
            outside_file.write_text('{"untouched":true}', encoding="utf-8")
            (safe / "settings.json").symlink_to(outside_file)
            with self.assertRaisesRegex(QewError, "symlink"):
                update_json_config(root, (".safe", "settings.json"), lambda value: value)
            self.assertEqual(outside_file.read_text(encoding="utf-8"), '{"untouched":true}')

    def test_failed_atomic_replace_leaves_original_configuration_intact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / ".agent" / "settings.json"
            target.parent.mkdir()
            original = b'{"theme":"dark"}\n'
            target.write_bytes(original)

            with patch("qew.hook_config.os.replace", side_effect=OSError("interrupted")):
                with self.assertRaisesRegex(QewError, "cannot replace"):
                    update_json_config(
                        root,
                        (".agent", "settings.json"),
                        lambda value: {**value, "hook": "qew prime"},
                    )

            self.assertEqual(target.read_bytes(), original)
            self.assertEqual(list(target.parent.glob(".settings.json.*.tmp")), [])

    def test_existing_file_modes_are_kept_and_new_paths_are_private(self) -> None:
        """Install must not loosen or tighten a user's config permissions."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / ".agent").mkdir()
            for mode in (0o644, 0o640):
                target = root / ".agent" / f"m{mode:o}.json"
                target.write_text("{}", encoding="utf-8")
                target.chmod(mode)
                self.assertTrue(update_json_config(root, (".agent", target.name), lambda v: {**v, "a": 1}))
                self.assertEqual(stat.S_IMODE(target.stat().st_mode), mode)
                self.assertFalse(update_json_config(root, (".agent", target.name), lambda v: {**v, "a": 1}))
                self.assertEqual(stat.S_IMODE(target.stat().st_mode), mode)
            update_json_config(root, (".fresh", "settings.json"), lambda v: {**v, "a": 1})
            self.assertEqual(stat.S_IMODE((root / ".fresh").stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((root / ".fresh" / "settings.json").stat().st_mode), 0o600)

    def test_a_symlinked_root_or_a_link_swapped_in_after_the_lookup_is_refused(self) -> None:
        """Never write through a planted link outside the checkout."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            base = Path(temporary_directory)
            outside = base / "outside"
            outside.mkdir()
            real = base / "real"
            real.mkdir()
            link = base / "link"
            link.symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(QewError, "symlink"):
                update_json_config(link, (".agent", "settings.json"), lambda v: {**v, "a": 1})
            self.assertEqual(list(real.iterdir()), [])
            real_mkdir = os.mkdir

            def swapping(path, mode=0o777, *, dir_fd=None):
                real_mkdir(path, mode, dir_fd=dir_fd)
                os.rmdir(path, dir_fd=dir_fd)
                os.symlink(str(outside), path, dir_fd=dir_fd)

            with patch("qew.hook_config.os.mkdir", side_effect=swapping):
                with self.assertRaisesRegex(QewError, "symlink"):
                    update_json_config(real, (".agent", "settings.json"), lambda v: {**v, "a": 1})
            self.assertEqual(list(outside.iterdir()), [])

    def test_remove_of_an_unowned_managed_file_is_a_no_op_not_a_delete(self) -> None:
        """A user's own file under Qew's name must survive remove."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / ".agent" / "plugin.js"
            target.parent.mkdir()
            target.write_text("// mine\n", encoding="utf-8")
            self.assertFalse(remove_managed_file(root, (".agent", "plugin.js"), "// managed by qew"))
            self.assertEqual(target.read_text(encoding="utf-8"), "// mine\n")

    def test_the_write_is_flushed_before_the_replace_and_the_directory_after(self) -> None:
        """Order matters: file fsync, atomic replace, then directory fsync, with no temp file left."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / ".agent").mkdir()
            events = []
            real_fsync, real_replace = os.fsync, os.replace

            def fsync(descriptor):
                events.append("dir" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file")
                return real_fsync(descriptor)

            def replace(*args, **kwargs):
                events.append("replace")
                return real_replace(*args, **kwargs)

            with patch("qew.hook_config.os.fsync", side_effect=fsync), patch("qew.hook_config.os.replace", side_effect=replace):
                update_json_config(root, (".agent", "settings.json"), lambda v: {**v, "a": 1})
            self.assertEqual(events, ["file", "replace", "dir"])
            self.assertEqual(sorted(p.name for p in (root / ".agent").iterdir()), ["settings.json"])


if __name__ == "__main__":
    unittest.main()
