"""Installed-distribution smoke walk for Qew's supported Python floor."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tests.command_harness import REQUESTER_QUOTE


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DistributionWalk(unittest.TestCase):
    def test_wheel_installs_qew_executable_outside_checkout(self) -> None:
        """The declared package must contain Qew, not an empty UNKNOWN wheel."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            wheelhouse = temporary / "wheelhouse"
            wheelhouse.mkdir()
            build = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "wheel",
                    "--disable-pip-version-check",
                    "--no-build-isolation",
                    "--no-deps",
                    "--wheel-dir",
                    str(wheelhouse),
                    str(PROJECT_ROOT),
                ],
                cwd=temporary,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            wheels = list(wheelhouse.glob("qew-*.whl"))
            self.assertEqual(len(wheels), 1, build.stdout)

            environment = os.environ.copy()
            environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
            venv = temporary / "venv"
            create = subprocess.run(
                [sys.executable, "-m", "venv", str(venv)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(create.returncode, 0, create.stderr)
            pip = venv / "bin" / "pip"
            install = subprocess.run(
                [str(pip), "install", "--no-deps", str(wheels[0])],
                env=environment,
                cwd=temporary,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(install.returncode, 0, install.stderr)

            metadata = subprocess.run(
                [
                    str(venv / "bin" / "python"),
                    "-c",
                    "import importlib.metadata as m, json; "
                    "print(json.dumps({'version': m.version('qew'), 'requires': m.requires('qew')}))",
                ],
                env=environment,
                cwd=temporary,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(metadata.returncode, 0, metadata.stderr)
            self.assertEqual(
                json.loads(metadata.stdout),
                {"version": "2.10.0", "requires": None},
            )

            smoke = subprocess.run(
                [str(venv / "bin" / "qew"), "--help"],
                env=environment,
                cwd=temporary,
                capture_output=True,
                text=True,
                check=False,
            )
            # The shipped v1 plan predates requester quotes; dry-run it as a
            # caller would re-apply it today, with each item naming its requester.
            plan = temporary / "qew-v1-quoted.jsonl"
            plan.write_text(
                "".join(
                    json.dumps({**json.loads(line), "quote": REQUESTER_QUOTE}) + "\n"
                    for line in (PROJECT_ROOT / "docs" / "plans" / "qew-v1.jsonl")
                    .read_text(encoding="utf-8")
                    .splitlines()
                    if line.strip()
                ),
                encoding="utf-8",
            )
            self_host = subprocess.run(
                [
                    str(venv / "bin" / "qew"),
                    "apply",
                    str(plan),
                    "--repo",
                    "acme/widgets",
                    "--dry-run",
                    "--json",
                ],
                env=environment,
                cwd=temporary,
                capture_output=True,
                text=True,
                check=False,
            )
            linked_plan = temporary / "linked-plan.jsonl"
            linked_plan.symlink_to(plan)
            refusal = subprocess.run(
                [
                    str(venv / "bin" / "qew"),
                    "apply",
                    str(linked_plan),
                    "--repo",
                    "acme/widgets",
                    "--dry-run",
                ],
                env=environment,
                cwd=temporary,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(smoke.returncode, 0, smoke.stderr)
        self.assertIn("usage: qew", smoke.stdout)
        self.assertEqual(self_host.returncode, 0, self_host.stderr)
        operations = json.loads(self_host.stdout)["operations"]
        created = [operation["id"] for operation in operations if operation["action"] == "create"]
        self.assertEqual(len(created), len(set(created)))
        self.assertIn("self-host-release", created)
        self.assertEqual(refusal.returncode, 2)
        self.assertIn("plan file must not be a symlink", refusal.stderr)


if __name__ == "__main__":
    unittest.main()
