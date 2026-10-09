"""A command that never talks to a forge must not load the HTTP stack: every qew process pays for its imports.

Each test is a scenario; its docstring names the failure it prevents. Agent hooks and the test suite start `qew`
thousands of times, so start-up cost is felt directly.
"""

import json
import os
import subprocess
import sys
import unittest

from tests.ledger_harness import PROJECT_ROOT, QUOTE, World

HEAVY = ("urllib.request", "http.client", "ssl", "qew.forge_http", "qew.forge_github", "qew.forge_forgejo", "qew.forge_gitlab",
         "qew.forge_registry")
PROBE = (
    "import json, sys\n"
    "from qew import cli\n"
    "try:\n"
    "    code = cli.main(sys.argv[1:])\n"
    "except SystemExit as stop:\n"
    "    code = stop.code or 0\n"
    "print(json.dumps({'code': code, 'loaded': sorted(m for m in sys.modules if m in %r)}), file=sys.stderr)\n" % (HEAVY,)
)


class StartupWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def loaded_by(self, *arguments: str) -> list[str]:
        environment = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT / "src"), "QEW_ACTOR": "tester"}
        done = subprocess.run([sys.executable, "-c", PROBE, *arguments], cwd=self.world.code, env=environment,
                              capture_output=True, text=True, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        report = json.loads(done.stderr.strip().splitlines()[-1])
        return report["loaded"]

    def test_version_and_help_load_no_forge_or_http_code(self) -> None:
        """Prevents `qew --version` and `qew --help` paying for urllib, http.client and every forge backend."""
        self.assertEqual(self.loaded_by("--version"), [])

    def test_a_ledger_only_command_loads_no_forge_or_http_code(self) -> None:
        """Prevents `qew list` and `qew create` (what hooks and agents run constantly) paying for the HTTP stack."""
        self.world.create("--kind", "issue", "--title", "One", "--quote", QUOTE)
        self.assertEqual(self.loaded_by("list"), [])
        self.assertEqual(self.loaded_by("create", "--title", "Two"), [])


if __name__ == "__main__":
    unittest.main()
