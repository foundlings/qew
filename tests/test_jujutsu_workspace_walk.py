"""Command walks for gh-backed commands run from a Jujutsu workspace."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Like real gh in a jj workspace: with no git context, repository inference fails.
FAKE_GH = f"""#!{sys.executable}
import json, os, sys
calls = os.environ["QEW_FAKE_GH_CALLS"]
with open(calls, "a") as handle:
    handle.write(json.dumps({{"argv": sys.argv[1:], "git_dir": os.environ.get("GIT_DIR")}}) + "\\n")
if sys.argv[1:3] == ["repo", "view"]:
    git_dir = os.environ.get("GIT_DIR")
    if not git_dir or not os.path.isdir(git_dir):
        print("failed to run git: fatal: not a git repository", file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps({{"nameWithOwner": "acme/widgets"}}))
    raise SystemExit(0)
print("stop after repo view", file=sys.stderr)
raise SystemExit(77)
"""


class JujutsuWorkspaceWalk(unittest.TestCase):
    def test_workspace_resolves_the_repository_through_the_main_git_store(self) -> None:
        result, calls = self._run("workspace")

        self.assertNotIn("not a git repository", result.stderr)
        self.assertIn("stop after repo view", result.stderr)
        self.assertTrue(calls[0]["git_dir"].endswith("main/.git"), calls[0])

    def test_unresolvable_store_names_what_could_not_be_resolved(self) -> None:
        result, calls = self._run("broken")

        self.assertEqual(result.returncode, 3)
        self.assertIn("cannot resolve the git store of this jj workspace", result.stderr)
        self.assertEqual(calls, [])

    def test_plain_git_checkout_leaves_gh_to_its_own_inference(self) -> None:
        result, calls = self._run("plain")

        self.assertEqual(result.returncode, 3)
        self.assertIsNone(calls[0]["git_dir"])

    def _run(self, layout: str):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            fake_gh = root / "gh"
            fake_gh.write_text(FAKE_GH, encoding="utf-8")
            fake_gh.chmod(0o755)
            main = root / "main"
            (main / ".git").mkdir(parents=True)
            store = main / ".jj" / "repo" / "store"
            store.mkdir(parents=True)
            (store / "git_target").write_text("../../../.git", encoding="utf-8")
            if layout == "plain":
                checkout = main
                (main / ".jj").rename(root / "unused")
            else:
                checkout = root / "lane"
                (checkout / ".jj").mkdir(parents=True)
                pointer = "../../main/.jj/repo" if layout == "workspace" else "../../gone/.jj/repo"
                (checkout / ".jj" / "repo").write_text(pointer, encoding="utf-8")
            (checkout / ".qew").mkdir(exist_ok=True)
            (checkout / ".qew" / "config.json").write_text(
                '{"mode": "repository", "schema_version": 1}\n', encoding="utf-8"
            )
            environment = os.environ.copy()
            environment["PATH"] = str(root) + os.pathsep + environment["PATH"]
            environment["PYTHONPATH"] = str(PROJECT_ROOT / "src")
            environment["QEW_FAKE_GH_CALLS"] = str(root / "calls.jsonl")
            environment.pop("GIT_DIR", None)
            result = subprocess.run(
                [sys.executable, "-m", "qew", "ready"],
                cwd=checkout,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            calls_file = root / "calls.jsonl"
            calls = (
                [json.loads(line) for line in calls_file.read_text().splitlines()]
                if calls_file.exists()
                else []
            )
            return result, calls


if __name__ == "__main__":
    unittest.main()
