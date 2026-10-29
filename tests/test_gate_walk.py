"""`scripts/gate.py --changed`: a change runs the test modules it can affect and the type check of the files it touched.

Adversary: a change that skips a test which exercises the changed file (a wrongly small selection), a gate that cannot say what
a change affects and runs nothing instead of everything, a type check of the whole tree for a one-line doc edit, and a record of
which files a module ran that is empty because the monitor never ran. Each test names the failure it prevents.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.ledger_harness import World

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "scripts" / "gate.py"


class GateChangedWalk(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.cache = Path(temporary.name)

    def record(self, mapping: dict[str, list[str]]) -> None:
        (self.cache / "files.json").write_text(json.dumps(mapping), encoding="utf-8")

    def plan(self, *files: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(GATE), "--changed-files", *files, "--list"], cwd=ROOT, capture_output=True, text=True,
                              env={"PATH": "/usr/bin:/bin:/opt/homebrew/bin", "QEW_GATE_CACHE": str(self.cache), "HOME": str(self.cache)}, check=False)

    def selected(self, result: subprocess.CompletedProcess[str]) -> list[str]:
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return [line for line in result.stdout.splitlines() if line.startswith("test_")]

    def test_a_changed_source_file_selects_exactly_the_modules_that_ran_it(self) -> None:
        """Prevents a change skipping its tests, and an unrelated module running: only the module whose recorded run touched the file."""
        self.record({"test_progress_walk": ["src/qew/cmd_progress.py"], "test_help_walk": ["src/qew/cli.py"]})
        self.assertEqual(self.selected(self.plan("src/qew/cmd_progress.py")), ["test_progress_walk"])

    def test_a_changed_test_module_runs_itself_and_a_changed_helper_runs_every_module_that_imports_it(self) -> None:
        """Prevents a test edit going unrun, and a harness edit going unchecked by the walks built on it."""
        self.record({"test_help_walk": ["src/qew/cli.py"]})
        self.assertEqual(self.selected(self.plan("tests/test_progress_walk.py")), ["test_progress_walk"])
        helper = self.selected(self.plan("tests/pr_harness.py"))
        self.assertIn("test_pr_open_walk", helper)
        self.assertIn("test_pr_merge_walk", helper)
        self.assertNotIn("test_progress_walk", helper)

    def test_a_changed_doc_or_script_selects_the_modules_whose_source_names_it(self) -> None:
        """Prevents spec and fixture edits escaping the walks that read them: the module text naming the path is the link."""
        self.record({"test_help_walk": ["src/qew/cli.py"]})
        self.assertIn("test_help_walk", self.selected(self.plan("docs/specs/commands.md")))
        self.assertIn("test_typecheck_walk", self.selected(self.plan("scripts/typecheck.py")))

    def test_a_source_file_no_module_ran_falls_back_to_the_modules_that_ran_what_imports_it_else_everything(self) -> None:
        """Prevents running nothing for a file the record has never seen: its importers stand for it, and with no clue it is all."""
        self.record({"test_help_walk": ["src/qew/cli.py"]})
        unseen = self.plan("src/qew/ledger_layout.py")  # imported by files no recorded module ran
        self.assertIn("running every module", unseen.stdout)
        self.assertGreater(len(self.selected(unseen)), 100)

    def test_no_recorded_map_runs_everything_and_nothing_changed_runs_nothing(self) -> None:
        """Prevents a first use selecting nothing: without a map every module is chosen; an empty change says so and stops."""
        first = self.plan("src/qew/cmd_progress.py")
        self.assertIn("running every module", first.stdout)
        self.assertGreater(len(self.selected(first)), 100)
        none = self.plan()
        self.assertEqual(none.returncode, 0)
        self.assertIn("nothing differs", none.stdout)

    def test_the_type_check_covers_the_changed_files_and_what_imports_them_not_the_tree(self) -> None:
        """Prevents a type check of every file for a one-file change, and one that misses a caller broken by a signature change."""
        self.record({"test_help_walk": ["src/qew/cli.py"]})
        scoped = self.plan("src/qew/ledger_layout.py").stdout
        line = next(line for line in scoped.splitlines() if line.startswith("typecheck:"))
        files = line.split()[1:]
        self.assertIn("src/qew/ledger_layout.py", files)
        self.assertIn("src/qew/ledger_store.py", files)  # it imports ledger_layout
        self.assertLess(len(files), len(list((ROOT / "src" / "qew").glob("*.py"))))
        self.assertNotIn("typecheck:", self.plan("docs/specs/commands.md").stdout)
        self.assertIn("type check of everything", self.plan("pyproject.toml").stdout)

    def test_a_process_records_the_source_files_it_ran(self) -> None:
        """Prevents an empty map: with QEW_FILE_MAP set a qew command leaves the list of src/qew files it ran."""
        world = World()
        self.addCleanup(world.close)
        world.create("--title", "x")
        trace = self.cache / "trace"
        result = world.run("list", env={"QEW_FILE_MAP": str(trace)})
        self.assertEqual(result.returncode, 0, result.stderr)
        ran = {line for part in trace.glob("*.txt") for line in part.read_text().splitlines()}
        self.assertIn("src/qew/cli.py", ran)
        self.assertIn("src/qew/ledger_store.py", ran)

    def test_scripts_are_linted_but_not_type_checked_strictly_when_named(self) -> None:
        """Prevents the scoped check failing on scripts the default check never held to mypy --strict."""
        done = subprocess.run([sys.executable, str(ROOT / "scripts" / "typecheck.py"), "scripts/measure_reads.py"], cwd=ROOT,
                              capture_output=True, text=True, check=False)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()
