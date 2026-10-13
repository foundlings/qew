"""`qew doctor`: environment checks that make big ledgers fast. A jj ledger wants watchman, or every commit stats every file.

Adversary: a large jj ledger left on the slow path with nobody told; a fix that edits the wrong repository; a git ledger nagged
about a tool it does not use.
Each test is a scenario; its docstring names the failure it prevents.
"""

import os
import shutil
import stat
import subprocess
import unittest
from pathlib import Path

from tests.ledger_harness import World


def tools(base: Path, *names: str) -> dict[str, str]:
    """A PATH holding only these tools (symlinks), so a walk controls whether watchman is found."""
    directory = base / "pathdir"
    directory.mkdir(exist_ok=True)
    for name in names:
        link = directory / name
        if not link.exists():
            link.symlink_to(shutil.which(name) or name)
    return {"PATH": str(directory)}


def fake_watchman(base: Path) -> None:
    script = base / "pathdir" / "watchman"
    script.write_text("#!/bin/sh\nexit 1\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)


def configured(ledger: Path) -> str:
    result = subprocess.run(["jj", "--ignore-working-copy", "config", "get", "fsmonitor.backend"], cwd=ledger, capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() != "none" else ""  # jj's default is `none`


class DoctorWalk(unittest.TestCase):
    def test_a_jj_ledger_without_watchman_is_warned_on_doctor_and_init(self) -> None:
        """Prevents a big jj ledger staying on the slow snapshot path unnoticed: both commands name the tool and the install line."""
        w = World(ledger_vcs="jj")
        self.addCleanup(w.close)
        env = tools(w.base, "jj", "git")
        doctor = w.run("doctor", env=env)
        self.assertEqual(doctor.returncode, 0, doctor.stderr)
        self.assertIn("warning", doctor.stdout)
        self.assertIn("brew install watchman", doctor.stdout)
        init = w.run("init", env=env)
        self.assertEqual(init.returncode, 0, init.stderr)
        self.assertIn("brew install watchman", init.stderr)

    def test_fix_turns_watchman_on_for_this_ledger_only(self) -> None:
        """Prevents a fix that edits the user's global jj config or enables a monitor that is not installed."""
        w = World(ledger_vcs="jj")
        self.addCleanup(w.close)
        env = tools(w.base, "jj", "git")
        refused = w.run("doctor", "--fix", env=env)
        self.assertEqual(refused.returncode, 4, refused.stdout)
        self.assertEqual(configured(w.ledger), "")
        fake_watchman(w.base)
        before = w.run("doctor", env=env)
        self.assertIn("not enabled", before.stdout)
        fixed = w.run("doctor", "--fix", env=env)
        self.assertEqual(fixed.returncode, 0, fixed.stdout + fixed.stderr)
        self.assertEqual(configured(w.ledger), "watchman")
        self.assertIn("ok", w.run("doctor", env=env).stdout)
        self.assertNotIn("warning", w.run("init", env=env).stderr)

    def test_a_git_ledger_has_nothing_to_warn_about(self) -> None:
        """Prevents nagging a git ledger about a tool only jj uses."""
        w = World(ledger_vcs="git")
        self.addCleanup(w.close)
        env = tools(w.base, "jj", "git")
        doctor = w.run("doctor", env=env)
        self.assertEqual(doctor.returncode, 0, doctor.stderr)
        self.assertNotIn("warning", doctor.stdout)
        self.assertNotIn("watchman", w.run("init", env=env).stderr)


if __name__ == "__main__":
    unittest.main()
