"""Qew relies on typing: the type checker is part of the tests, and it rejects untyped code.

Each test is a scenario; its docstring names the failure it prevents. The checker is mypy --strict on Python 3.15,
run by scripts/typecheck.py from a pinned developer environment (`python3 scripts/typecheck.py --setup`).
"""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "typecheck.py"


def check(*paths: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *paths], cwd=ROOT, capture_output=True, text=True, timeout=900, check=False)


class TypecheckWalk(unittest.TestCase):
    def source(self, text: str) -> str:
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: [p.unlink() for p in directory.iterdir()] or directory.rmdir())
        path = directory / "sample.py"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def test_a_function_without_annotations_is_an_error(self) -> None:
        """Prevents untyped code slipping in: a missing annotation is an error, not a warning."""
        result = check(self.source("def add(a, b):\n    return a + b\n"))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("no-untyped-def", result.stdout)

    def test_an_implicit_any_in_a_signature_is_an_error(self) -> None:
        """Prevents a bare `list` or `dict` (implicit Any) passing as typed."""
        result = check(self.source("def first(items: list) -> int:\n    return 0\n"))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("type-arg", result.stdout)

    def test_an_annotated_function_passes(self) -> None:
        """Prevents the check rejecting correct, typed code."""
        result = check(self.source("def add(a: int, b: int) -> int:\n    return a + b\n"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_old_typing_spellings_are_errors(self) -> None:
        """Prevents Optional, Union, List and Dict creeping back: Python 3.15 spells them `X | None` and `list[X]`."""
        result = check(self.source("from typing import Optional\n\n\ndef first(items: list[int]) -> Optional[int]:\n    return None\n"))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("UP045", result.stdout)
        result = check(self.source("from typing import List\n\n\ndef first(items: List[int]) -> int:\n    return 0\n"))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("UP006", result.stdout)

    def test_an_override_without_the_decorator_is_an_error(self) -> None:
        """Prevents a backend method silently overriding (or failing to override) the Forge interface."""
        text = "class Base:\n    def go(self) -> int:\n        return 1\n\n\nclass Child(Base):\n    def go(self) -> int:\n        return 2\n"
        result = check(self.source(text))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("explicit-override", result.stdout)
        fixed = text.replace("class Child(Base):\n    def go", "class Child(Base):\n    @override\n    def go").replace("class Base:", "from typing import override\n\n\nclass Base:", 1)
        self.assertEqual(check(self.source(fixed)).returncode, 0)

    def test_modern_typing_passes(self) -> None:
        """Prevents the modern spellings being rejected."""
        result = check(self.source("def first(items: list[int], default: int | None = None) -> int | None:\n    return default\n"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_whole_package_is_clean(self) -> None:
        """Prevents a type error landing in src/qew: the package passes mypy --strict with no findings."""
        result = check()
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr)


if __name__ == "__main__":
    unittest.main()
