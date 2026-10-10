"""The id mode of base-35 time strings is called `base35-time`; the old name `time` is refused loudly.

Each test is a scenario; its docstring names the failure it prevents.
"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World


class IdModeNameWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)
        self.config: Path = self.world.ledger / "qew-ledger.toml"

    def use(self, mode_line: str) -> None:
        self.config.write_text(self.config.read_text().rstrip("\n") + "\n" + mode_line + "\n", encoding="utf-8")

    def test_the_new_name_makes_base35_time_ids(self) -> None:
        """Prevents the renamed mode being unknown: `id_mode = "base35-time"` creates nine character time ids."""
        self.use('id_mode = "base35-time"')
        result = self.world.run("create", "--kind", "issue", "--title", "One", "--quote", QUOTE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout.split()[0], r"^[0-9a-km-z]{9}$")

    def test_the_old_name_is_refused_and_names_the_new_one(self) -> None:
        """Prevents a silent accept of the retired name: a ledger still saying `time` fails and says what to write."""
        self.use('id_mode = "time"')
        result = self.world.run("list")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("base35-time", result.stderr)

    def test_migrate_ids_takes_the_new_name_only(self) -> None:
        """Prevents `qew migrate-ids --to time` lingering: the target mode is spelled base35-time."""
        self.assertEqual(self.world.run("migrate-ids", "--to", "base35-time", "--dry-run").returncode, 0)
        old = self.world.run("migrate-ids", "--to", "time", "--dry-run")
        self.assertEqual(old.returncode, 2, old.stdout)

    def test_init_writes_the_new_name_into_the_template(self) -> None:
        """Prevents a new ledger starting without an explicit mode, or with the retired name."""
        self.config.unlink()
        result = self.world.run("init")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        text = self.config.read_text()
        self.assertIn('issue = "base35-time"', text)
        self.assertNotIn('"time"', text)


if __name__ == "__main__":
    unittest.main()
