"""An isolated code repo plus ledger repo, driven through `python -m qew.cli`."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


import json
import os
from pathlib import Path
import subprocess
import time
import sys
import tempfile
from collections.abc import Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if os.environ.get("QEW_TEST_SCHEMA") == "8":  # run the walks on the sharded layout: a Ledger built by hand declares it too (the product always reads the file)
    from qew import ledger_store as _ledger_store
    _defaults = list(_ledger_store.Ledger.__init__.__defaults__)  # type: ignore[misc]
    _defaults[_defaults.index(5)] = 8
    _ledger_store.Ledger.__init__.__defaults__ = tuple(_defaults)  # type: ignore[misc]
os.environ.setdefault("QEW_UNDER_TEST", "1")  # qew then refuses any ledger outside the temp directory (see ledger_config)
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


def baseline_files(ledger: Path, item: str) -> list[Path]:
    """The committed sync baselines of one item (one live file per target), oldest first."""
    return sorted((ledger / "baselines" / item).glob("*/*.json"), key=lambda path: path.stem)


def baseline_path(ledger: Path, item: str) -> Path:
    """The newest committed sync baseline of one item."""
    return baseline_files(ledger, item)[-1]


class World:
    """A git code repo on `main` with `.qew/config.toml` naming a sibling ledger."""

    def __init__(self, mirrors: str = "", ledger_vcs: str | None = None, remote: bool = False,
                 replication: str = "", schema: str = "") -> None:
        self._temporary = tempfile.TemporaryDirectory()
        base = Path(self._temporary.name)
        self.base = base
        if not replication:
            # Walks that do not test replication must not spawn detached workers against fake URLs.
            replication = "\n[replication]\nauto_forge_sync = false\nforge_concurrency = 1\n"
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
            f'name = "test"\nprefix = "TST"\nschema_version = {schema or os.environ.get("QEW_TEST_SCHEMA", "7")}\n', encoding="utf-8"
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

    def ledger_log(self, path: Path | None = None) -> list:
        """Commit subjects of the ledger checkout, newest first, up to its first commit."""
        out = git(path or self.ledger, "log", "--format=%s", "main")
        return out.splitlines()

    def ledger_dirty(self, path: Path | None = None) -> list:
        if self.ledger_vcs == "jj":
            out = jj(path or self.ledger, "diff", "--summary")
            return [("?? " if line.startswith("A ") else line[:2] + " ") + line[2:] for line in out.splitlines()]
        out = git(path or self.ledger, "status", "--porcelain", "--untracked-files=all")
        return [line for line in out.splitlines() if ".qew/" not in line]

    def clone(self, name: str, mirrors: str = "", replication: str = "") -> World:
        """A second agent: its own code repo and ledger clone of the shared remote."""
        if not replication:
            replication = "\n[replication]\nauto_forge_sync = false\nforge_concurrency = 1\n"
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
            for attempt in range(5):  # a detached sync worker may still be writing into the tree
                try:
                    self._temporary.cleanup()
                    return
                except OSError:
                    if attempt == 4:
                        raise
                    time.sleep(0.5)

    def commit(self, message: str = "work") -> str:
        git(self.code, "commit", "-q", "--allow-empty", "-m", message)
        return git(self.code, "rev-parse", "HEAD")

    def run(
        self,
        *arguments: str,
        stdin: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float = 60,
    ) -> subprocess.CompletedProcess:
        environment = {**os.environ, **_GIT_ENV, "PYTHONPATH": str(PROJECT_ROOT / "src")}
        environment["QEW_ACTOR"] = "tester"
        environment.setdefault("QEW_RETRY_BASE_S", "0.01")  # a forge that is down must not make a walk wait out real backoff
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
            timeout=timeout,
        )

    def create(self, *arguments: str) -> str:
        """Create an item and return its full ID."""
        result = self.run("create", *arguments)
        assert result.returncode == 0, result.stderr
        return result.stdout.split()[0]

    def approve(self, *ids: str) -> None:
        result = self.run("draft", "approve", *ids, "--quote", QUOTE)
        assert result.returncode == 0, result.stderr

    def sharded(self) -> bool:
        """Whether the ledger declares the sharded layout (schema 8): events/<last two characters of the id>/<id>/."""
        return "schema_version = 8" in (self.ledger / "qew-ledger.toml").read_text(encoding="utf-8")

    def home(self, kind: str, item_id: str, ledger: Path | None = None) -> Path:
        """The directory of one item under `items`, `events`, `mirrors` or `baselines`, in whichever layout the ledger declares."""
        root = (ledger or self.ledger) / kind
        return root / f"_{item_id[-2:].lower()}" / item_id if self.sharded() else root / item_id

    def events(self, item_id: str) -> list:
        directory = self.home("events", item_id)
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
