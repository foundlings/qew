"""Tests can never touch a real ledger: under the harness qew refuses ledgers outside the temp directory."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import PROJECT_ROOT, World


class RealLedgerGuardWalk(unittest.TestCase):
    def test_a_ledger_outside_the_temp_directory_is_refused_while_under_test(self) -> None:
        """Prevents a test (or an agent's experiment run with the test flag) writing into a real ledger checkout."""
        w = World()
        self.addCleanup(w.close)
        real = Path.home() / "qew-guard-fixture-ledger"
        try:
            real.mkdir()
            (real / "qew-ledger.toml").write_text('name = "x"\nschema_version = 1\n')
            config = w.code / ".qew" / "config.toml"
            config.write_text('[ledger]\npath = "%s"\n' % real)
            result = w.run("create", "--title", "Must not land")
            self.assertEqual(result.returncode, 2)
            self.assertIn("temporary", result.stderr)
            self.assertFalse((real / "items").exists())
        finally:
            for child in sorted(real.rglob("*"), reverse=True):
                child.unlink() if child.is_file() else child.rmdir()
            real.rmdir()

    def test_without_the_test_flag_any_ledger_is_allowed(self) -> None:
        """Prevents the guard leaking into real use: only QEW_UNDER_TEST turns it on."""
        w = World()
        self.addCleanup(w.close)
        outside = Path(os.path.realpath(tempfile.gettempdir()))
        self.assertTrue(str(w.ledger.resolve()).startswith(str(outside)))
        result = w.run("list", env={"QEW_UNDER_TEST": ""})
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
