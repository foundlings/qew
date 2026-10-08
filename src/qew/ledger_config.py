"""Locate the code repo's `.qew/config.toml` and the ledger it names."""

from dataclasses import dataclass
import os
from pathlib import Path
import re
import tomllib
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit

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
    sync_retry_s: float = 10.0
    sync_budget_s: float = 20.0
    branch: str = "main"

    @property
    def offline(self) -> bool:
        return os.environ.get("QEW_OFFLINE", "") not in ("", "0")


_REPLICATION_KEYS = {
    "auto_commit": bool, "auto_push": bool, "auto_forge_sync": bool, "background_sync": bool,
    "timeout_s": (int, float), "fetch_interval_s": (int, float), "sync_retry_s": (int, float), "sync_budget_s": (int, float), "branch": str,
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


def _authority(section: object, mirrors: Tuple["Mirror", ...]) -> str:
    """The primary forge target's slug, or `none`; a unique fragment of a slug is accepted."""
    if section is None:
        return "none"
    if not isinstance(section, dict):
        raise QewError("[authority] must be a table", 1)
    for key in section:
        if key != "primary":
            raise QewError(f"[authority] has an unknown key `{key}`", 1)
    primary = section.get("primary", "none")
    if not isinstance(primary, str) or not primary:
        raise QewError("[authority] primary must be a target name or \"none\"", 1)
    if primary == "none":
        return "none"
    slugs = [mirror_slug(m) for m in mirrors]
    found = [s for s in slugs if s == primary] or [s for s in slugs if primary in s]
    if len(found) != 1:
        raise QewError(f"[authority] primary `{primary}` matches {len(found)} mirror targets (have: {', '.join(slugs) or 'none'})", 1)
    return found[0]


@dataclass(frozen=True)
class Config:
    root: Path
    ledger_path: Path
    prefix: str
    target: str
    mirrors: Tuple[Mirror, ...]
    replication: Replication = Replication()
    authority: str = "none"  # slug of the primary forge target, or "none": the ledger remote arbitrates
    max_active_claims: Optional[int] = None  # per-actor cap on claimed items; None = no cap


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
                  _replication(data.get("replication")), _authority(data.get("authority"), mirrors),
                  _claim_cap(data.get("claims")))


def _claim_cap(section: object) -> Optional[int]:
    """`[claims] max_active_claims`: a positive integer, or absent for no cap; anything else is exit 2."""
    if section is None:
        return None
    if not isinstance(section, dict) or set(section) - {"max_active_claims"}:
        raise QewError("[claims] holds only `max_active_claims`", 2)
    cap = section.get("max_active_claims")
    if cap is None:
        return None
    if type(cap) is not int or cap < 1:
        raise QewError("[claims] max_active_claims must be a positive integer", 2)
    return cap


_SEGMENT = re.compile(r"[A-Za-z0-9_.-]+")
_URL_OPTIONS = ("api", "graphql")


def _checked_url(value: object, name: str, forge: str, repository: bool) -> str:
    """A mirror URL that is safe to send a token to: http(s), no credentials, query or fragment.

    A repository URL also names `owner/repo` (GitLab may nest groups) in the forge character set.
    """
    where = f"[[mirror]] {name}"
    if not isinstance(value, str) or re.search(r"[\s\x00-\x1f\x7f]", value):
        raise QewError(f"{where} must be a URL without whitespace", 2)
    try:
        parts = urlsplit(value)
        parts.port  # noqa: B018 - a malformed port raises here
    except ValueError:
        raise QewError(f"{where} `{value}` is not a valid URL", 2) from None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise QewError(f"{where} `{value}` must be an http or https URL with a host", 2)
    if "@" in parts.netloc or parts.query or parts.fragment or "?" in value or "#" in value:
        raise QewError(f"{where} `{value}` must not carry credentials, a query or a fragment", 2)
    if repository:
        segments = parts.path.strip("/").split("/")
        shallow = len(segments) == 2
        if not (shallow or (forge == "gitlab" and len(segments) > 2)) or not all(
            _SEGMENT.fullmatch(segment) and segment not in (".", "..") for segment in segments
        ):
            raise QewError(f"{where} `{value}` must name `owner/repo` with letters, digits, `_`, `.` and `-`", 2)
    return value


def _mirror(entry: object) -> Mirror:
    if not isinstance(entry, dict) or not isinstance(entry.get("forge"), str) or not isinstance(entry.get("repo"), str):
        raise QewError("each [[mirror]] needs string `forge` and `repo`", 1)
    known = {"forge", "repo", "project", "select"}
    project = entry.get("project")
    forge = ALIASES.get(entry["forge"], entry["forge"])
    for name in _URL_OPTIONS:
        if name in entry:
            _checked_url(entry[name], name, forge, repository=False)
    timeout = entry.get("timeout")
    if "timeout" in entry and (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0):
        raise QewError("[[mirror]] timeout must be a positive number of seconds", 2)
    return Mirror(
        forge=forge,
        repo=_checked_url(entry["repo"], "repo", forge, repository=True).rstrip("/"),
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
