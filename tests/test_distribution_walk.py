"""Walk of the built distribution: a real wheel installs into a clean venv and runs outside the checkout.

Adversary: an empty or dependency-bearing package, an entry point that only works from the source
tree, a version that differs from what `qew --version` prints, and a bulk-import input that is a
symlink to somewhere the caller did not name.
"""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

from tests.ledger_harness import PROJECT_ROOT, QUOTE, World, git
from qew import __version__  # noqa: E402

BUILD_INPUTS = ("src", "setup.cfg", "setup.py", "pyproject.toml", "README.md", "LICENSE")


def run(argv, cwd, env=None):
    return subprocess.run(argv, cwd=cwd, env=env or {**os.environ, "PYTHONPATH": ""}, capture_output=True,
                          text=True, check=False, timeout=300)


@unittest.skipIf(importlib.util.find_spec("setuptools") is None, "setuptools is not installed (pinned in requirements/build.txt)")
class WheelWalk(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        base = Path(cls.temporary.name)
        source = base / "source"
        source.mkdir()
        for name in BUILD_INPUTS:
            target = source / name
            if (PROJECT_ROOT / name).is_dir():
                shutil.copytree(PROJECT_ROOT / name, target, ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
            else:
                shutil.copy(PROJECT_ROOT / name, target)
        built = run([sys.executable, "-m", "pip", "wheel", "--no-build-isolation", "--no-deps", "--no-index",
                     "-q", "-w", str(base / "dist"), "."], source)
        assert built.returncode == 0, built.stderr
        cls.wheel = next((base / "dist").glob("*.whl"))
        venv = base / "venv"
        assert run([sys.executable, "-m", "venv", str(venv)], base).returncode == 0
        installed = run([sys.executable, "-m", "pip", "--python", str(venv / "bin" / "python"), "install",
                         "--no-deps", "--no-index", "-q", str(cls.wheel)], base)
        assert installed.returncode == 0, installed.stderr
        cls.venv = venv
        cls.work = base / "work"
        cls.work.mkdir()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_the_wheel_is_real_named_and_free_of_runtime_requirements(self) -> None:
        """Publishing an empty UNKNOWN wheel or one with dependencies must be impossible."""
        self.assertEqual(self.wheel.name, f"qew-{__version__}-py3-none-any.whl")
        with zipfile.ZipFile(self.wheel) as archive:
            names = archive.namelist()
            self.assertIn("qew/cli.py", names)
            metadata = archive.read(f"qew-{__version__}.dist-info/METADATA").decode("utf-8")
            entry_points = archive.read(f"qew-{__version__}.dist-info/entry_points.txt").decode("utf-8")
        self.assertIn("Name: qew\n", metadata)
        self.assertNotIn("Requires-Dist", metadata)
        self.assertIn("qew = qew.cli:main", entry_points)

    def test_the_installed_executable_runs_help_version_and_a_ledger_golden_path_outside_the_checkout(self) -> None:
        """The package must not depend on the source tree being importable."""
        qew = str(self.venv / "bin" / "qew")
        env = {**os.environ, "PYTHONPATH": "", "QEW_ACTOR": "tester", "QEW_OFFLINE": "1"}
        repo = self.work / "repo"
        repo.mkdir()
        git(repo, "init", "-q", "-b", "main")
        self.assertIn("Commands by workflow", run([qew, "--help"], repo, env).stdout)
        self.assertEqual(run([qew, "--version"], repo, env).stdout, f"qew {__version__}\n")
        self.assertEqual(run([qew, "init", "--ledger", "../ledger"], repo, env).returncode, 0)
        created = run([qew, "create", "--title", "Installed", "--quote", QUOTE], repo, env)
        self.assertEqual(created.returncode, 0, created.stderr)
        shown = run([qew, "show", created.stdout.split()[0]], repo, env)
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn("Installed", shown.stdout)
        imported = run([sys.executable, "-I", "-c", "import qew, sys; print(qew.__file__)"], repo,
                       {**env, "PYTHONPATH": str(self.venv / "lib")})
        self.assertNotIn(str(PROJECT_ROOT), imported.stdout)


class BulkInputWalk(unittest.TestCase):
    def test_a_symlinked_bulk_input_file_is_refused(self) -> None:
        """A planted link must not make `create --from-file` read a file the caller did not name."""
        world = World()
        self.addCleanup(world.close)
        real = world.base / "real.json"
        real.write_text(json.dumps([{"title": "From link"}]), encoding="utf-8")
        link = world.base / "specs.json"
        link.symlink_to(real)
        refused = world.run("create", "--from-file", str(link), "--quote", QUOTE)
        self.assertEqual(refused.returncode, 2)
        self.assertIn("symlink", refused.stderr)
        self.assertEqual(world.run("list").stdout, "")
        self.assertEqual(world.run("create", "--from-file", str(real), "--quote", QUOTE).returncode, 0)
        body_link = world.base / "body.md"
        body_link.symlink_to(world.base / "real.json")
        self.assertEqual(world.run("create", "--title", "x", "--body-file", str(body_link)).returncode, 2)


if __name__ == "__main__":
    unittest.main()
