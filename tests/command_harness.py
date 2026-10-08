"""Process-level fake GitHub CLI for command walks."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import List, Mapping, Optional, Sequence, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Every created item names its human requester (see test_requester_quote_walk).
REQUESTER_QUOTE = "Alexander: 'please track this work in the Qew graph'"

PROJECT_HINT = (
    "hint: GitHub Projects are derived visibility; refresh when useful with: "
    "qew project ensure --title TITLE\n"
)


def project_reconciliation() -> dict:
    return {
        "command": "qew project ensure --title TITLE",
        "reason": "GitHub Projects are derived visibility and may now be stale.",
    }


def make_checkout(
    parent: Path,
    mode: Optional[str] = "repository",
    project: Optional[object] = None,
    vcs_marker: str = ".git",
    config: Optional[dict] = None,
) -> Path:
    """Create an isolated checkout so no walk reads this repository's real policy.

    A mode of None leaves the checkout uninitialized, without a Qew config.
    """
    root = parent / "repository"
    root.mkdir()
    (root / vcs_marker).mkdir()
    if mode is not None:
        if config is None:
            config = {"mode": mode, "schema_version": 1}
            if project is not None:
                config["project"] = project
        path = root / ".qew" / "config.json"
        path.parent.mkdir()
        path.write_text(json.dumps(config) + "\n", encoding="utf-8")
    return root


def run_qew(
    responses: list,
    arguments: Sequence[str],
    environment_overrides: Optional[Mapping[str, str]] = None,
    repository_mode: Optional[str] = None,
    uninitialized: bool = False,
    vcs_marker: str = ".git",
    timeout: Optional[float] = None,
    project: Optional[object] = None,
    config: Optional[dict] = None,
    stdin: Optional[str] = None,
    remote: Optional[str] = None,
) -> Tuple[subprocess.CompletedProcess, List[list]]:
    with tempfile.TemporaryDirectory() as temporary_directory:
        temporary = Path(temporary_directory)
        fake_gh = temporary / "gh"
        state_file = temporary / "state"
        calls_file = temporary / "calls.jsonl"
        fake_gh.write_text(_fake_gh_program(), encoding="utf-8")
        fake_gh.chmod(0o755)
        environment = os.environ.copy()
        responses_file = temporary / "responses.json"
        responses_file.write_text(json.dumps(responses), encoding="utf-8")
        environment["QEW_FAKE_GH_RESPONSES"] = str(responses_file)
        environment["QEW_FAKE_GH_STATE"] = str(state_file)
        environment["QEW_FAKE_GH_CALLS"] = str(calls_file)
        environment["PATH"] = temporary_directory + os.pathsep + environment["PATH"]
        environment["PYTHONPATH"] = str(PROJECT_ROOT / "src")
        if environment_overrides is not None:
            environment.update(environment_overrides)
        working_directory = make_checkout(
            temporary,
            None if uninitialized else repository_mode or "repository",
            project,
            vcs_marker,
            config,
        )
        if remote is not None:
            for command in (["git", "init", "-q"], ["git", "remote", "add", "origin", remote]):
                subprocess.run(command, cwd=working_directory, check=True, capture_output=True)
        config_path = working_directory / ".qew" / "config.json"
        result = subprocess.run(
            [sys.executable, "-m", "qew", *arguments],
            cwd=working_directory,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            input=stdin,
        )
        calls = []
        if calls_file.exists():
            calls = [json.loads(line) for line in calls_file.read_text().splitlines()]
        # The temporary checkout is removed on return, so expose the written config.
        result.config = (
            json.loads(config_path.read_text(encoding="utf-8"))
            if config_path.exists()
            else None
        )
        return result, calls


def _fake_gh_program() -> str:
    return f"""#!{sys.executable}
import json
import os
from pathlib import Path
import re
import sys

state_path = Path(os.environ["QEW_FAKE_GH_STATE"])
calls_path = Path(os.environ["QEW_FAKE_GH_CALLS"])
responses = json.loads(Path(os.environ["QEW_FAKE_GH_RESPONSES"]).read_text())
index = int(state_path.read_text() if state_path.exists() else "0")
with calls_path.open("a", encoding="utf-8") as calls:
    calls.write(json.dumps(sys.argv[1:]) + "\\n")
if index >= len(responses):
    print("unexpected gh call", file=sys.stderr)
    raise SystemExit(99)
response = responses[index]
if response.get("project_query"):
    # Answer like GitHub: return only the issue fields the query selects.
    query = next(
        argument.split("=", 1)[1]
        for argument in sys.argv[1:]
        if argument.startswith("query=")
    )
    selected = set(re.findall(r"[A-Za-z_]+", query))
    def project(value):
        if isinstance(value, dict) and "number" in value:
            return {{key: child for key, child in value.items() if key in selected}}
        if isinstance(value, dict):
            return {{key: project(child) for key, child in value.items()}}
        if isinstance(value, list):
            return [project(child) for child in value]
        return value
    response = {{"stdout": json.dumps(project(json.loads(response["stdout"])))}}
state_path.write_text(str(index + 1), encoding="utf-8")
sys.stdout.write(response.get("stdout", ""))
sys.stderr.write(response.get("stderr", ""))
raise SystemExit(response.get("exit_code", 0))
"""


def author_responses(login: str = "octocat", label_exists: bool = True) -> list:
    """Answer the viewer and author-label lookups Qew makes before its first create."""
    label = {"name": f"author:{login}"} if label_exists else None
    responses = [
        {"stdout": json.dumps({"data": {"viewer": {"login": login}}})},
        {"stdout": json.dumps({"data": {"repository": {"label": label}}})},
    ]
    if not label_exists:
        responses.append({"stdout": ""})
    return responses
