"""Command walks for gh launch failures.

Behavior: a missing or hung gh is reported as a Qew GitHub failure (exit 3)
with one stderr line.
Adversary: an absent gh binary or a gh that never returns must not surface a
Python traceback or hang Qew and the hooks that call it.
"""

from pathlib import Path
import sys
import tempfile
import unittest

from tests.command_harness import run_qew


class GhLaunchWalk(unittest.TestCase):
    def test_missing_gh_exits_three_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            result, _ = run_qew(
                [],
                ["ready", "--repo", "acme/widgets", "--json"],
                {"PATH": empty},
            )

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("gh", result.stderr)

    def test_hung_gh_times_out_with_exit_three(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            hung = Path(directory) / "gh"
            hung.write_text(
                f"#!{sys.executable}\nimport time\ntime.sleep(5)\n",
                encoding="utf-8",
            )
            hung.chmod(0o755)
            result, _ = run_qew(
                [],
                ["ready", "--repo", "acme/widgets", "--json"],
                {"PATH": directory, "QEW_GH_TIMEOUT_SECONDS": "0.5"},
            )

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("timed out", result.stderr)


if __name__ == "__main__":
    unittest.main()
