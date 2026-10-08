"""Release upload boundary command walks."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMMAND = PROJECT_ROOT / "scripts" / "upload_release.py"


class ReleaseUploadWalk(unittest.TestCase):
    def test_enterprise_host_and_fixed_upload_arguments_reach_gh(self) -> None:
        """The workflow token must be sent to its own enterprise host."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            calls = temporary / "calls.json"
            fake_gh = temporary / "gh"
            fake_gh.write_text(
                "#!" + sys.executable + "\n"
                "import json, os, pathlib, sys\n"
                "pathlib.Path(os.environ['CALLS']).write_text(json.dumps({"
                "'argv': sys.argv[1:], 'host': os.environ.get('GH_HOST')}))\n"
                "raise SystemExit(0 if os.environ.get('GH_HOST') == 'ghe.example.com' else 19)\n",
                encoding="utf-8",
            )
            fake_gh.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "CALLS": str(calls),
                    "GITHUB_SERVER_URL": "https://ghe.example.com",
                    "PATH": str(temporary) + os.pathsep + environment["PATH"],
                }
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(COMMAND),
                    "--tag",
                    "v1.0.0",
                    "--repo",
                    "887/qew",
                    "dist/qew.whl",
                    "release/SHA256SUMS",
                ],
                cwd=PROJECT_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            captured = json.loads(calls.read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            captured,
            {
                "argv": [
                    "release",
                    "upload",
                    "v1.0.0",
                    "dist/qew.whl",
                    "release/SHA256SUMS",
                    "--repo",
                    "887/qew",
                    "--clobber",
                ],
                "host": "ghe.example.com",
            },
        )

    def test_server_port_reaches_gh_host(self) -> None:
        """An enterprise server on a non-default port must not lose its port."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            calls = temporary / "calls.json"
            fake_gh = temporary / "gh"
            fake_gh.write_text(
                "#!" + sys.executable + "\n"
                "import json, os, pathlib\n"
                "pathlib.Path(os.environ['CALLS']).write_text("
                "json.dumps({'host': os.environ.get('GH_HOST')}))\n",
                encoding="utf-8",
            )
            fake_gh.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "CALLS": str(calls),
                    "GITHUB_SERVER_URL": "https://ghes.example:8443",
                    "PATH": str(temporary) + os.pathsep + environment["PATH"],
                }
            )
            result = subprocess.run(
                [sys.executable, str(COMMAND), "--tag", "v1.0.0", "--repo", "o/r", "dist/qew.whl"],
                cwd=PROJECT_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            captured = json.loads(calls.read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(captured["host"], "ghes.example:8443")


if __name__ == "__main__":
    unittest.main()
