"""Locate the code repo's `.qew/config.toml` and the ledger it names."""

from dataclasses import dataclass
import os
from pathlib import Path
import tomllib
from typing import Dict, List, Optional, Tuple

from qew.errors import QewError

LEDGER_FILE = "qew-ledger.toml"
ALIASES = {"gitea": "forgejo"}
CONFIG = Path(".qew") / "config.toml"


@dataclass(frozen=True)
class Mirror:
    forge: str
    repo: str
    project: Optional[str]
    select: str
    options: Dict[str, object]


@dataclass(frozen=True)
class Replication:
    """How a write replicates itself; see docs/specs/ledger.md, Replication."""

    auto_commit: bool = True
    auto_push: bool = True
    auto_forge_sync: bool = True
    background_sync: bool = True
    timeout_s: float = 5.0
    fetch_interval_s: float = 2.0
    branch: str = "main"

    @property
    def offline(self) -> bool:
        return os.environ.get("QEW_OFFLINE", "") not in ("", "0")


_REPLICATION_KEYS = {
    "auto_commit": bool, "auto_push": bool, "auto_forge_sync": bool, "background_sync": bool,
    "timeout_s": (int, float), "fetch_interval_s": (int, float), "branch": str,
}


def _replication(section: object) -> Replication:
    if section is None:
        return Replication()
    if not isinstance(section, dict):
        raise QewError("[replication] must be a table", 1)
    for key, value in section.items():
        kind = _REPLICATION_KEYS.get(key)
        if kind is None:
            raise QewError(f"[replication] has an unknown key `{key}`", 1)
        if not isinstance(value, kind) or (kind is bool and not isinstance(value, bool)) or isinstance(value, bool) != (kind is bool):
            raise QewError(f"[replication] {key} has the wrong type", 1)
    return Replication(**section)


@dataclass(frozen=True)
class Config:
    root: Path
    ledger_path: Path
    prefix: str
    target: str
    mirrors: Tuple[Mirror, ...]
    replication: Replication = Replication()


def find_root(start: Optional[Path] = None) -> Optional[Path]:
    here = (start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        if (directory / CONFIG).is_file():
            return directory
    return None


def load(start: Optional[Path] = None) -> Config:
    """The code repo config, or a config built from `QEW_LEDGER` / a ledger cwd."""
    root = find_root(start)
    if root is None:
        ledger = _ledger_from_environment(start)
        if ledger is None:
            raise QewError("no .qew/config.toml found; run `qew init`", 2)
        return Config(ledger, ledger, _ledger_prefix(ledger), "main", ())
    try:
        data = tomllib.loads((root / CONFIG).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise QewError(f"{root / CONFIG}: {error}", 1) from None
    section = data.get("ledger")
    if not isinstance(section, dict) or not isinstance(section.get("path"), str):
        raise QewError(f"{root / CONFIG}: [ledger] path is required", 1)
    override = os.environ.get("QEW_LEDGER")
    ledger_path = Path(override) if override else (root / section["path"])
    prefix = section.get("prefix") or _ledger_prefix(ledger_path.resolve())
    code = data.get("code", {})
    mirrors = tuple(_mirror(entry) for entry in data.get("mirror", []))
    return Config(root, ledger_path.resolve(), str(prefix), str(code.get("target", "main")), mirrors,
                  _replication(data.get("replication")))


def _mirror(entry: object) -> Mirror:
    if not isinstance(entry, dict) or not isinstance(entry.get("forge"), str) or not isinstance(entry.get("repo"), str):
        raise QewError("each [[mirror]] needs string `forge` and `repo`", 1)
    known = {"forge", "repo", "project", "select"}
    project = entry.get("project")
    return Mirror(
        forge=ALIASES.get(entry["forge"], entry["forge"]),
        repo=entry["repo"].rstrip("/"),
        project=None if project is None else str(project),
        select=str(entry.get("select", "")),
        options={key: value for key, value in entry.items() if key not in known},
    )


def _ledger_from_environment(start: Optional[Path]) -> Optional[Path]:
    override = os.environ.get("QEW_LEDGER")
    if override:
        return Path(override).resolve()
    here = (start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        if (directory / LEDGER_FILE).is_file():
            return directory
    return None


def _ledger_prefix(ledger: Path) -> str:
    """The ledger's optional display prefix; item IDs do not carry it."""
    try:
        data = tomllib.loads((ledger / LEDGER_FILE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ""
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise QewError(f"{ledger / LEDGER_FILE}: {error}", 1) from None
    prefix = data.get("prefix", "")
    if not isinstance(prefix, str):
        raise QewError(f"{ledger / LEDGER_FILE}: prefix must be a string", 1)
    return prefix


def mirror_slug(mirror: Mirror) -> str:
    """The `<forge>-<host>-<repo-slug>` stem of a mirror file."""
    without_scheme = mirror.repo.split("://", 1)[-1]
    host, _, path = without_scheme.partition("/")
    slug = "".join(c if c.isalnum() or c in ".-" else "-" for c in path)
    return f"{mirror.forge}-{host}-{slug}"
