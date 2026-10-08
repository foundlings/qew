"""Release workflow source-commit walks.

Behavior: the release job builds the commit it records as the SBOM source.
Adversary: a tag that moves after publish makes the checkout differ from
GITHUB_SHA, so the build and the SBOM would describe different commits.
"""

from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest


WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml"
STEP_NAME = "Verify the checkout is the recorded source"


def step_run_script(text: str, name: str) -> str:
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == f"- name: {name}"), None)
    if start is None:
        raise AssertionError(f"release workflow has no step named {name!r}")
    run = next(i for i in range(start, len(lines)) if lines[i].strip() == "run: |")
    indent = len(lines[run + 1]) - len(lines[run + 1].lstrip())
    body = []
    for line in lines[run + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            break
        body.append(line[indent:])
    return "\n".join(body)


class ReleaseWorkflowWalk(unittest.TestCase):
    def test_checkout_that_differs_from_recorded_source_is_refused(self) -> None:
        """A moved tag must fail the job before a mismatched build is attested."""
        script = step_run_script(WORKFLOW.read_text(encoding="utf-8"), STEP_NAME)
        with tempfile.TemporaryDirectory() as temporary:
            self._git(temporary, "init", "-q")
            recorded = self._commit(temporary, "recorded")
            moved = self._commit(temporary, "moved")

            same = self._verify(script, temporary, moved)
            differs = self._verify(script, temporary, recorded)

        self.assertEqual(same.returncode, 0, same.stderr)
        self.assertNotEqual(differs.returncode, 0)
        self.assertIn("differs", differs.stderr)

    def test_checkout_uses_the_event_commit_not_the_movable_tag(self) -> None:
        """Checking out by tag name lets the tag move away from GITHUB_SHA."""
        text = WORKFLOW.read_text(encoding="utf-8")
        checkout = re.search(r"actions/checkout@.*?\n\n", text, re.S)
        self.assertIsNotNone(checkout)
        self.assertIn("ref: ${{ github.sha }}", checkout.group(0))
        self.assertNotIn("tag_name", checkout.group(0))

    def _verify(self, script: str, cwd: str, sha: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "GITHUB_SHA": sha}
        return subprocess.run(
            ["bash", "-e", "-c", script], cwd=cwd, env=env, capture_output=True, text=True, check=False
        )

    def _commit(self, cwd: str, message: str) -> str:
        self._git(cwd, "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-q", "--allow-empty", "-m", message)
        return self._git(cwd, "rev-parse", "HEAD").stdout.strip()

    def _git(self, cwd: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


if __name__ == "__main__":
    unittest.main()
