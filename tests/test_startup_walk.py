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

HEAVY = ("urllib.request", "http.client", "ssl", "qew.forge_http", "qew.forge_github", "qew.forge_forgejo", "qew.forge_gitlab")
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
        done = subprocess.run([sys.executable, "-c", "import sys\nfrom qew import cli\ncli._parser(cli._wanted_modules(['--version']))\n"
                               "print(sorted(m for m in sys.modules if m.startswith('qew.cmd_')))"],
                              env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT / "src")}, capture_output=True, text=True, check=False)
        self.assertEqual(done.stdout.strip(), "['qew.cmd_draft', 'qew.cmd_items', 'qew.cmd_read']", done.stderr)

    def test_a_ledger_only_command_loads_no_forge_or_http_code(self) -> None:
        """Prevents `qew list` and `qew create` (what hooks and agents run constantly) paying for the HTTP stack."""
        self.world.create("--kind", "issue", "--title", "One", "--quote", QUOTE)
        self.assertEqual(self.loaded_by("list"), [])
        self.assertEqual(self.loaded_by("create", "--title", "Two"), [])


class CommandTableWalk(unittest.TestCase):
    def test_the_command_table_matches_what_the_modules_register(self) -> None:
        """Prevents a new command missing from command_modules.py: `qew NEWCOMMAND` would then list every command as unknown."""
        import argparse
        import importlib

        from qew import cli
        from qew.command_modules import COMMAND_MODULE, CORE_COMMANDS

        registered: dict[str, str] = {}
        for name in cli._MODULES:
            parser = argparse.ArgumentParser()
            commands = parser.add_subparsers(dest="command")
            importlib.import_module(f"qew.{name}").register(commands)
            registered.update({command: name for command in commands.choices})
        self.assertEqual(COMMAND_MODULE, registered)
        full = next(a for a in cli._parser()._actions if isinstance(a, argparse._SubParsersAction)).choices
        self.assertEqual(sorted(full), cli.top_level_commands())
        self.assertEqual(set(full) - set(registered), set(CORE_COMMANDS))

    def test_a_command_imports_only_its_own_module(self) -> None:
        """Prevents `qew list` paying for the 25 command modules it does not use."""
        code = ("import sys\nfrom qew import cli\ncli._parser(cli._wanted_modules(['list']))\n"
                "print(sorted(m for m in sys.modules if m.startswith('qew.cmd_')))")
        done = subprocess.run([sys.executable, "-c", code], env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT / "src")},
                              capture_output=True, text=True, check=False)
        self.assertEqual(done.stdout.strip(), "['qew.cmd_draft', 'qew.cmd_items', 'qew.cmd_read']", done.stderr)


if __name__ == "__main__":
    unittest.main()
