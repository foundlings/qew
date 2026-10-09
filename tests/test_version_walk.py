"""Walk of `qew --version`.

Adversary: users unable to identify the installed build, and a version flag that needs a repository,
a ledger, credentials or the network.
"""

import unittest

from tests.ledger_harness import World
from tests.test_hook_v1_walk import outside
from qew import __version__  # noqa: E402


class VersionWalk(unittest.TestCase):
    def test_version_prints_the_package_version_outside_any_repository(self) -> None:
        """The flag must work with no repository and touch no ledger or forge."""
        result = outside("--version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"^qew \d+\.\d+\.\d+\n$")
        self.assertEqual(result.stdout, f"qew {__version__}\n")
        self.assertEqual(result.stderr, "")

    def test_version_ignores_a_broken_ledger_config(self) -> None:
        """A checkout with an unreadable ledger must still report its build."""
        world = World()
        self.addCleanup(world.close)
        (world.code / ".qew" / "config.toml").write_text("not [valid toml", encoding="utf-8")
        result = world.run("--version")
        self.assertEqual((result.returncode, result.stdout), (0, f"qew {__version__}\n"), result.stderr)


if __name__ == "__main__":
    unittest.main()
