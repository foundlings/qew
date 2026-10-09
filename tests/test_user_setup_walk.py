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


@unittest.skipUnless(SUPPORTED, "the install script runs only on Linux and macOS")
class InstallShellWalk(unittest.TestCase):
    def test_the_shell_front_door_installs_a_working_qew_and_refuses_when_no_python_is_new_enough(self) -> None:
        """Prevents install.sh picking an old system Python or failing silently: it installs, or exits 3 saying why."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            home = Path(temporary_directory)
            environment = {**os.environ, "HOME": str(home), "PATH": f"{home / '.local' / 'bin'}{os.pathsep}{os.environ['PATH']}"}
            environment.pop("XDG_DATA_HOME", None)
            script = PROJECT_ROOT / "scripts" / "install.sh"
            done = subprocess.run(["sh", str(script)], cwd=PROJECT_ROOT, env=environment, capture_output=True, text=True, check=False)
            self.assertEqual(done.returncode, 0, done.stderr)
            smoke = subprocess.run([str(home / ".local" / "bin" / "qew"), "--help"], capture_output=True, text=True, check=False)
            self.assertEqual(smoke.returncode, 0, smoke.stderr)
            bare = {**environment, "PATH": "/nonexistent"}
            refused = subprocess.run(["/bin/sh", str(script)], cwd=PROJECT_ROOT, env=bare, capture_output=True, text=True, check=False)
            if not any(Path(p).exists() and os.access(p, os.X_OK) for p in ("/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3")):
                self.assertEqual(refused.returncode, 3)
                self.assertIn("Python 3.15", refused.stderr)


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

    def test_a_symlink_pointing_elsewhere_is_refused_untouched(self) -> None:
        """A user-managed link to another qew must survive untouched."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            home = Path(temporary_directory)
            local_bin = home / ".local" / "bin"
            local_bin.mkdir(parents=True)
            elsewhere = home / "other-qew"
            elsewhere.write_text("#!/bin/sh\n", encoding="utf-8")
            link = local_bin / "qew"
            link.symlink_to(elsewhere)
            refused = run_setup(home, [local_bin])
            self.assertEqual(refused.returncode, 2, refused.stderr)
            self.assertEqual(os.readlink(link), str(elsewhere))

    def test_a_failing_install_exits_three_and_leaves_no_half_link(self) -> None:
        """A broken pip must not leave a link to an executable that does not exist."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            home = Path(temporary_directory)
            fake = home / "fakebin"
            fake.mkdir()
            result = subprocess.run(
                [sys.executable, "-c",
                 "import runpy, subprocess, sys\n"
                 "real = subprocess.run\n"
                 "def fake(command, **kw):\n"
                 "    if 'pip' in command:\n"
                 "        return subprocess.CompletedProcess(command, 1)\n"
                 "    return real(command, **kw)\n"
                 "subprocess.run = fake\n"
                 f"sys.argv = [{str(COMMAND)!r}]\n"
                 f"runpy.run_path({str(COMMAND)!r}, run_name='__main__')\n"],
                cwd=PROJECT_ROOT, env={**os.environ, "HOME": str(home)}, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertFalse((home / ".local" / "bin" / "qew").exists())
            self.assertFalse((home / ".local" / "bin" / "qew").is_symlink())


if __name__ == "__main__":
    unittest.main()
