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

    def __init__(self, mirrors: str = "", ledger_vcs: Optional[str] = None, remote: bool = False,
                 replication: str = "") -> None:
        self._temporary = tempfile.TemporaryDirectory()
        base = Path(self._temporary.name)
        self.base = base
        if not replication:
            # Walks that do not test replication must not spawn detached workers against fake URLs.
            replication = "\n[replication]\nauto_forge_sync = false\n"
        self.ledger_vcs = ledger_vcs
        self.remote = base / "remote.git"
        self.code = base / "code"
        self.ledger = base / "ledger"
        self.code.mkdir()
        self.ledger.mkdir()
        git(self.code, "init", "-q", "-b", "main")
        git(self.code, "commit", "-q", "--allow-empty", "-m", "root")
        (self.code / ".qew").mkdir()
        (self.code / ".qew" / "config.toml").write_text(
            '[ledger]\npath = "../ledger"\nprefix = "TST"\n' + mirrors + replication, encoding="utf-8"
        )
        (self.ledger / "qew-ledger.toml").write_text(
            'name = "test"\nprefix = "TST"\nschema_version = 1\n', encoding="utf-8"
        )
        if ledger_vcs:
            self._init_ledger(ledger_vcs, remote)

    def _init_ledger(self, vcs: str, remote: bool) -> None:
        git(self.ledger, "init", "-q", "-b", "main")
        git(self.ledger, "add", "-A")
        git(self.ledger, "commit", "-q", "-m", "init ledger")
        if remote:
            git(self.base, "init", "-q", "--bare", "-b", "main", str(self.remote))
            git(self.ledger, "remote", "add", "origin", str(self.remote))
            git(self.ledger, "push", "-q", "-u", "origin", "main")
        if vcs == "jj":
            jj(self.ledger, "git", "init", "--colocate")
            if remote:
                jj(self.ledger, "bookmark", "track", "main", "--remote", "origin")

    def ledger_log(self, path: Optional[Path] = None) -> list:
        """Commit subjects of the ledger checkout, newest first, up to its first commit."""
        out = git(path or self.ledger, "log", "--format=%s", "main")
        return out.splitlines()

    def ledger_dirty(self, path: Optional[Path] = None) -> list:
        if self.ledger_vcs == "jj":
            out = jj(path or self.ledger, "diff", "--summary")
            return [("?? " if line.startswith("A ") else line[:2] + " ") + line[2:] for line in out.splitlines()]
        out = git(path or self.ledger, "status", "--porcelain", "--untracked-files=all")
        return [line for line in out.splitlines() if ".qew/" not in line]

    def clone(self, name: str, mirrors: str = "", replication: str = "") -> "World":
        """A second agent: its own code repo and ledger clone of the shared remote."""
        if not replication:
            replication = "\n[replication]\nauto_forge_sync = false\n"
        other = World.__new__(World)
        other._temporary = self._temporary
        other.base = self.base
        other.remote = self.remote
        other.ledger_vcs = self.ledger_vcs
        other._owner = False
        other.code = self.base / f"code-{name}"
        other.ledger = self.base / f"ledger-{name}"
        other.code.mkdir()
        git(self.base, "clone", "-q", str(self.remote), str(other.ledger))
        git(other.code, "init", "-q", "-b", "main")
        git(other.code, "commit", "-q", "--allow-empty", "-m", "root")
        (other.code / ".qew").mkdir()
        (other.code / ".qew" / "config.toml").write_text(
            f'[ledger]\npath = "../ledger-{name}"\nprefix = "TST"\n' + mirrors + replication, encoding="utf-8"
        )
        if self.ledger_vcs == "jj":
            jj(other.ledger, "git", "init", "--colocate")
            jj(other.ledger, "bookmark", "track", "main", "--remote", "origin")
        return other

    def close(self) -> None:
        if getattr(self, "_owner", True):
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
