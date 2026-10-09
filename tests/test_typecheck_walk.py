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

    def test_the_whole_package_is_clean(self) -> None:
        """Prevents a type error landing in src/qew: the package passes mypy --strict with no findings."""
        result = check()
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr)


if __name__ == "__main__":
    unittest.main()
