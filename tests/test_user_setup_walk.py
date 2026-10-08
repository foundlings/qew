"""User setup script command walks."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMMAND = PROJECT_ROOT / "scripts" / "setup_user.py"
SUPPORTED = sys.platform.startswith("linux") or sys.platform == "darwin"


def run_setup(home: Path, path_entries: list) -> subprocess.CompletedProcess:
    environment = os.environ.copy()
    environment["HOME"] = str(home)
    environment.pop("XDG_DATA_HOME", None)
    environment["PATH"] = os.pathsep.join([*map(str, path_entries), environment["PATH"]])
    return subprocess.run(
        [sys.executable, str(COMMAND)],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


@unittest.skipUnless(SUPPORTED, "the setup script installs only on Linux and macOS")
class UserSetupWalk(unittest.TestCase):
    def test_setup_links_a_working_qew_into_local_bin_and_is_idempotent(self) -> None:
        """A user must get a runnable qew on PATH, and a rerun must not break it."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            home = Path(temporary_directory)
            local_bin = home / ".local" / "bin"

            first = run_setup(home, [local_bin])
            link = local_bin / "qew"
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertTrue(link.is_symlink())
            smoke = subprocess.run(
                [str(link), "--help"], capture_output=True, text=True, check=False
            )
            self.assertEqual(smoke.returncode, 0, smoke.stderr)
            self.assertNotIn("not on PATH", first.stdout + first.stderr)

            second = run_setup(home, [local_bin])
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertTrue(link.is_symlink())

    def test_setup_warns_when_local_bin_is_not_on_path(self) -> None:
        """A silent install that cannot be run would look like a failed install."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            home = Path(temporary_directory)

            result = run_setup(home, [])

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("not on PATH", result.stdout + result.stderr)

    def test_setup_refuses_to_replace_a_file_it_does_not_own(self) -> None:
        """An unrelated qew executable must never be overwritten."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            home = Path(temporary_directory)
            local_bin = home / ".local" / "bin"
            local_bin.mkdir(parents=True)
            foreign = local_bin / "qew"
            foreign.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")

            result = run_setup(home, [local_bin])

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("refusing to replace", result.stderr)
            self.assertEqual(foreign.read_text(encoding="utf-8"), "#!/bin/sh\necho mine\n")
            self.assertFalse(foreign.is_symlink())


if __name__ == "__main__":
    unittest.main()
