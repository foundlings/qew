"""Behavioral walks for safe agent hook configuration editing."""

import json
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
