"""Version flag command walk."""

import unittest

from tests.command_harness import run_qew


class VersionWalk(unittest.TestCase):
    def test_version_flag_prints_the_package_version_without_calling_github(self) -> None:
        """Users must identify the installed Qew without a command or network call."""
        result, calls = run_qew([], ["--version"])

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"^qew \d+\.\d+\.\d+\n$")
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
