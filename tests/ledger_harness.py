"""An isolated code repo plus ledger repo, driven through `python -m qew.cli`."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Mapping, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
QUOTE = "Alexander: 'please track this work in the Qew ledger now'"

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}


def git(cwd: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        env={**os.environ, **_GIT_ENV},
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


class World:
    """A git code repo on `main` with `.qew/config.toml` naming a sibling ledger."""

    def __init__(self, mirrors: str = "") -> None:
        self._temporary = tempfile.TemporaryDirectory()
        base = Path(self._temporary.name)
        self.code = base / "code"
        self.ledger = base / "ledger"
        self.code.mkdir()
        self.ledger.mkdir()
        git(self.code, "init", "-q", "-b", "main")
        git(self.code, "commit", "-q", "--allow-empty", "-m", "root")
        (self.code / ".qew").mkdir()
        (self.code / ".qew" / "config.toml").write_text(
            '[ledger]\npath = "../ledger"\nprefix = "TST"\n' + mirrors, encoding="utf-8"
        )
        (self.ledger / "qew-ledger.toml").write_text(
            'name = "test"\nprefix = "TST"\nschema_version = 1\n', encoding="utf-8"
        )

    def close(self) -> None:
        self._temporary.cleanup()

    def commit(self, message: str = "work") -> str:
        git(self.code, "commit", "-q", "--allow-empty", "-m", message)
        return git(self.code, "rev-parse", "HEAD")

    def run(
        self,
        *arguments: str,
        stdin: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
    ) -> subprocess.CompletedProcess:
        environment = {**os.environ, **_GIT_ENV, "PYTHONPATH": str(PROJECT_ROOT / "src")}
        environment["QEW_ACTOR"] = "tester"
        if env:
            environment.update(env)
        return subprocess.run(
            [sys.executable, "-m", "qew.cli", *arguments],
            cwd=self.code,
            env=environment,
            capture_output=True,
            text=True,
            input=stdin,
            check=False,
            timeout=60,
        )

    def create(self, *arguments: str) -> str:
        """Create an item and return its full ID."""
        result = self.run("create", *arguments)
        assert result.returncode == 0, result.stderr
        return result.stdout.split()[0]

    def approve(self, *ids: str) -> None:
        result = self.run("draft", "approve", *ids, "--quote", QUOTE)
        assert result.returncode == 0, result.stderr

    def events(self, item_id: str) -> list:
        directory = self.ledger / "events" / item_id
        return [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(directory.glob("*.json"))
        ]


def jj_available() -> bool:
    return subprocess.run(["which", "jj"], capture_output=True).returncode == 0


def jj(cwd: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["jj", *arguments],
        cwd=cwd,
        env={**os.environ, "JJ_USER": "Test", "JJ_EMAIL": "test@example.invalid"},
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()
