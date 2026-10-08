"""Walks of the read-side trust spec and the token-efficiency measurement.

Adversary: a security spec that lies (omits a routine-read guarantee, or cites a walk that does not
exist) and a silent growth of a read command's output.
"""

import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "docs" / "specs" / "security.md"
SCRIPT = ROOT / "scripts" / "measure_reads.py"
SIZES = ROOT / "docs" / "specs" / "read-sizes.json"

GUARANTEES = (
    "untrusted_data", "routine_reads_bounded", "explicit_inspection", "prime_revalidates",
    "findings_are_annotations", "terms_are_data", "approval_needs_a_quote",
)


def tags(text: str) -> dict:
    """`[guarantee: ID | walk: FILE CLASS.test]` tags of the spec: id -> list of (file, class, test)."""
    found = {}
    for match in re.finditer(r"\[guarantee: (\w+)((?: \| walk: \S+ \S+)+)\]", text):
        walks = re.findall(r"walk: (\S+) (\w+)\.(\w+)", match.group(2))
        found[match.group(1)] = walks
    return found


class SecuritySpecWalk(unittest.TestCase):
    def test_every_routine_read_guarantee_is_stated_and_names_walks_that_exist(self) -> None:
        """The spec names each guarantee and each cited walk is a real test of a real class."""
        found = tags(SPEC.read_text(encoding="utf-8"))
        self.assertEqual(sorted(found), sorted(GUARANTEES), "security.md must tag exactly the named guarantees")
        for name, walks in found.items():
            self.assertTrue(walks, name)
            for file, klass, test in walks:
                source = (ROOT / file).read_text(encoding="utf-8")
                self.assertRegex(source, rf"class {klass}\(", f"{name}: {file} has no class {klass}")
                self.assertIn(f"def {test}(", source, f"{name}: {file} has no {test}")


class MeasurementWalk(unittest.TestCase):
    def run_script(self, *arguments: str):
        return subprocess.run([sys.executable, str(SCRIPT), *arguments], capture_output=True, text=True, cwd=ROOT, timeout=300, check=False)

    def test_the_recorded_sizes_hold_and_a_grown_command_fails_the_check(self) -> None:
        """--check passes on the checked-in sizes; lowering one recorded size makes it fail naming the command."""
        ok = self.run_script("--check")
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        for command in ("ready", "list", "prime", "show"):
            self.assertIn(command, ok.stdout)
        recorded = json.loads(SIZES.read_text(encoding="utf-8"))
        recorded["ready"]["compact_bytes"] -= 1
        with tempfile.TemporaryDirectory() as directory:
            lowered = Path(directory) / "sizes.json"
            lowered.write_text(json.dumps(recorded), encoding="utf-8")
            bad = self.run_script("--check", "--sizes", str(lowered))
        self.assertEqual(bad.returncode, 1, bad.stdout)
        self.assertIn("ready", bad.stdout + bad.stderr)

    def test_compact_output_is_smaller_than_the_raw_ledger_files(self) -> None:
        """The point of the contract: every measured command is cheaper than reading the files."""
        for name, sizes in json.loads(SIZES.read_text(encoding="utf-8")).items():
            self.assertLess(sizes["compact_bytes"], sizes["raw_bytes"], name)


if __name__ == "__main__":
    unittest.main()
