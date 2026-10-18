"""Locate the code repo's `.qew/config.toml` and the ledger it names."""

from dataclasses import dataclass, replace
from enum import Enum
from contextlib import contextmanager
import os
import re
from pathlib import Path
from collections.abc import Iterator
import re
import tomllib
from typing import cast
from collections.abc import Iterable
from urllib.parse import urlsplit

from qew import __version__
from qew.errors import QewError

LEDGER_FILE = "qew-ledger.toml"
ALIASES = {"gitea": "forgejo"}
CONFIG = Path(".qew") / "config.toml"


class OfflineClaims(Enum):
    """What `qew start` does when the arbiter of claims (primary forge or ledger remote) cannot be reached."""
    REFUSE = "refuse"  # the default: no claim without the arbiter
    ALLOW = "allow"  # fall back to a provisional claim that the next sync settles


@dataclass(frozen=True)
class Mirror:
    forge: str
    repo: str
    project: str | None
    select: str
    options: dict[str, object]
    read_only: bool = False  # a one-way mirror (ledger to forge): pull never imports its edits
    mode: str = "repository"  # "repository": qew owns the whole tracker; "overlay": a shared tracker, qew marks its issues


@dataclass(frozen=True)
class Replication:
    """How a write replicates itself; see docs/specs/ledger.md, Replication."""

    auto_commit: bool = True
    auto_push: bool = True
    auto_forge_sync: bool = True
    background_sync: bool = True
    timeout_s: float = 5.0
    fetch_interval_s: float = 10.0
    sync_retry_s: float = 10.0
    sync_budget_s: float = 20.0
    forge_concurrency: int = 4  # forge writes in flight at once during a sync (1: one at a time)
    divergences: str = "ledger"  # "ledger": sync undoes forge-side changes itself (the ledger is the truth); "report": they wait for accept or reject
    sync_limit: int = 300  # pending writes an unscoped `qew sync` makes before it asks for --all or a scope (0: no limit)
    forge_failure_limit: int = 5  # consecutive items failing on a 5xx or a lost connection before a sync stops the target
    branch: str = "main"

    @property
    def offline(self) -> bool:
        return os.environ.get("QEW_OFFLINE", "") not in ("", "0")


_REPLICATION_KEYS: dict[str, type | tuple[type, ...]] = {
    "auto_commit": bool, "auto_push": bool, "auto_forge_sync": bool, "background_sync": bool,
    "timeout_s": (int, float), "fetch_interval_s": (int, float), "sync_retry_s": (int, float), "sync_budget_s": (int, float), "forge_concurrency": int, "forge_failure_limit": int, "sync_limit": int, "divergences": str, "branch": str,
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
    if "forge_concurrency" in section and not 1 <= section["forge_concurrency"] <= 32:
        raise QewError("[replication] forge_concurrency must be between 1 and 32", 1)
    if "divergences" in section and section["divergences"] not in ("ledger", "report"):
        raise QewError('[replication] divergences must be "ledger" or "report"', 1)
    if "sync_limit" in section and not 0 <= section["sync_limit"] <= 1_000_000:
        raise QewError("[replication] sync_limit must be between 0 and 1000000", 1)
    if "forge_failure_limit" in section and not 1 <= section["forge_failure_limit"] <= 1000:
        raise QewError("[replication] forge_failure_limit must be between 1 and 1000", 1)
    return Replication(**section)


def _login(value: object, where: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 100 or any(c.isspace() or not c.isprintable() for c in value):
        raise QewError(f"{where} must be one forge login (no spaces)", 1)
    return value


def _forge(section: object) -> tuple[str, dict[str, str]]:
    """`[forge] login = "887"` (the human every agent acts for) and `[forge.logins] elf3 = "alice"` (an actor with its own login)."""
    if section is None:
        return "", {}
    if not isinstance(section, dict):
        raise QewError("[forge] must be a table", 1)
    for key in section:
        if key not in ("login", "logins"):
            raise QewError(f"[forge] has an unknown key `{key}`", 1)
    login = _login(section["login"], "[forge] login") if "login" in section else ""
    table = section.get("logins", {})
    if not isinstance(table, dict):
        raise QewError("[forge.logins] must be a table of actor = login", 1)
    return login, {str(actor): _login(who, f"[forge.logins] {actor}") for actor, who in table.items()}


def _with_logins(mirror: Mirror, login: str, logins: dict[str, str]) -> Mirror:
    """The mirror's `assignees` map (claim owner -> forge login) gets the repo-level defaults under it: the mirror's own entries
    win, then `[forge.logins]`, then `[forge] login` for every other actor (the wildcard `*`)."""
    if not login and not logins:
        return mirror
    own = mirror.options.get("assignees", {})
    merged = {**({"*": login} if login else {}), **logins, **(own if isinstance(own, dict) else {})}
    return replace(mirror, options={**mirror.options, "assignees": merged})


def _authority(section: object, mirrors: tuple[Mirror, ...]) -> str:
    """The primary forge target's slug, or `none`; a unique fragment of a slug is accepted."""
    if section is None:
        return "none"
    if not isinstance(section, dict):
        raise QewError("[authority] must be a table", 1)
    for key in section:
        if key not in ("primary", "offline_claims"):
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


def _offline_claims(section: object) -> OfflineClaims:
    """`[authority] offline_claims = "allow" | "refuse"` (default refuse)."""
    value = section.get("offline_claims", "refuse") if isinstance(section, dict) else "refuse"
    try:
        return OfflineClaims(value)
    except ValueError:
        raise QewError('[authority] offline_claims must be "allow" or "refuse"', 1) from None


@dataclass(frozen=True)
class Config:
    root: Path
    ledger_path: Path
    prefix: str
    target: str
    mirrors: tuple[Mirror, ...]
    replication: Replication = Replication()
    authority: str = "none"  # slug of the primary forge target, or "none": the ledger remote arbitrates
    max_active_claims: int | None = None  # per-actor cap on claimed items; None = no cap
    offline_claims: OfflineClaims = OfflineClaims.REFUSE


def find_root(start: Path | None = None) -> Path | None:
    here = (start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        if (directory / CONFIG).is_file():
            return directory
    return None


def load(start: Path | None = None) -> Config:
    """The code repo config, or a config built from `QEW_LEDGER` / a ledger cwd."""
    root = find_root(start)
    if root is None:
        ledger = _ledger_from_environment(start)
        if ledger is None:
            raise QewError("no .qew/config.toml found; run `qew init`", 2)
        return Config(ledger, ledger, _ledger_prefix(ledger), "main", ())
    if (root / CONFIG).is_symlink() or (root / CONFIG).parent.is_symlink():
        raise QewError(f"{root / CONFIG} is a symlink; refusing to read configuration through it", 2)
    try:
        data = tomllib.loads((root / CONFIG).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise QewError(f"{root / CONFIG}: {error}", 1) from None
    _require_minimum_version(data.get("qew"))
    section = data.get("ledger")
    if not isinstance(section, dict) or not isinstance(section.get("path"), str):
        raise QewError(f"{root / CONFIG}: [ledger] path is required", 1)
    override = os.environ.get("QEW_LEDGER")
    ledger_path = Path(override) if override else (root / section["path"])
    if override and data.get("mirror") and ledger_path.resolve() != (root / section["path"]).resolve():
        raise QewError(f"QEW_LEDGER names {ledger_path.resolve()} but the mirror targets in {root / CONFIG} belong to the ledger "
                       f"{(root / section['path']).resolve()}; run from that ledger's own code repo (or from the ledger itself)", 2)
    _refuse_real_ledger_under_test(ledger_path)
    ledger_prefix = _ledger_prefix(ledger_path.resolve())
    prefix = section.get("prefix") or ledger_prefix
    code = data.get("code", {})
    forge_login, forge_logins = _forge(data.get("forge"))
    mirrors = _distinct(_with_logins(_mirror(entry), forge_login, forge_logins) for entry in data.get("mirror", []))
    return Config(root, ledger_path.resolve(), str(prefix), str(code.get("target", "main")), mirrors,
                  _replication(data.get("replication")), _authority(data.get("authority"), mirrors),
                  _claim_cap(data.get("claims")), _offline_claims(data.get("authority")))


def _refuse_real_ledger_under_test(ledger_path: Path) -> None:
    """With QEW_UNDER_TEST set (the harness, scripts/gate.py) only a ledger inside the temp directory may be used."""
    if not os.environ.get("QEW_UNDER_TEST"):
        return
    import tempfile

    temporary = Path(os.path.realpath(tempfile.gettempdir()))
    resolved = Path(os.path.realpath(ledger_path))
    if temporary != resolved and temporary not in resolved.parents:
        raise QewError(f"under test qew only uses ledgers inside the temporary directory, not {ledger_path}", 2)


def _claim_cap(section: object) -> int | None:
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
_URL_OPTIONS = ("api", "graphql", "ledger_web_url")


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


_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _require_minimum_version(section: object) -> None:
    """`[qew] minimum_version` refuses every command of an older qew; additions elsewhere are ignored."""
    if section is None:
        return
    if not isinstance(section, dict):
        raise QewError("[qew] must be a table", 2)
    minimum = section.get("minimum_version")
    if minimum is None:
        return
    wanted = _VERSION.match(minimum) if isinstance(minimum, str) else None
    if wanted is None:
        raise QewError(f"[qew] minimum_version must look like 1.2.3, not {minimum!r}", 2)
    running = tuple(int(n) for n in cast(re.Match[str], _VERSION.match(__version__)).groups())
    if running < tuple(int(n) for n in wanted.groups()):
        raise QewError(
            f"this repository needs qew {minimum} or newer; this is {__version__}; upgrade with scripts/setup_user.py", 2)


def _distinct(mirrors: Iterable[Mirror]) -> tuple[Mirror, ...]:
    """One mirror per target: URL spellings that differ in case or `.git` are the same repository."""
    seen: dict[str, Mirror] = {}
    for mirror in mirrors:
        seen.setdefault(mirror_slug(mirror), mirror)
    return tuple(seen.values())


def _project_number(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise QewError(f"project must be a positive board number, got {value!r}", 2)
    return str(value)


def _mirror(entry: object) -> Mirror:
    if not isinstance(entry, dict) or not isinstance(entry.get("forge"), str) or not isinstance(entry.get("repo"), str):
        raise QewError("each [[mirror]] needs string `forge` and `repo`", 1)
    known = {"forge", "repo", "project", "select", "read_only", "mode"}
    mode = entry.get("mode", "repository")
    if mode not in ("repository", "overlay"):
        raise QewError("[[mirror]] mode must be \"repository\" or \"overlay\"", 2)
    read_only = entry.get("read_only", False)
    if not isinstance(read_only, bool):
        raise QewError("[[mirror]] read_only must be true or false", 2)
    project = _project_number(entry.get("project"))
    title = entry.get("project_title")
    if title is not None and (not isinstance(title, str) or not title.strip()):
        raise QewError("mirror `project_title` must not be empty", 2)
    named: str = entry["forge"]
    forge = ALIASES.get(named, named)
    for name in _URL_OPTIONS:
        if name in entry:
            _checked_url(entry[name], name, forge, repository=False)
    timeout = entry.get("timeout")
    if "timeout" in entry and (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0):
        raise QewError("[[mirror]] timeout must be a positive number of seconds", 2)
    for name in ("create_per_minute", "create_per_hour"):
        if name in entry and (isinstance(entry[name], bool) or not isinstance(entry[name], int) or entry[name] < 0):
            raise QewError(f"[[mirror]] {name} must be a whole number of creates, 0 for no limit", 2)
    return Mirror(
        forge=forge,
        repo=_checked_url(entry["repo"], "repo", forge, repository=True).rstrip("/"),
        project=project,
        select=str(entry.get("select", "")),
        options={key: value for key, value in entry.items() if key not in known},
        read_only=read_only,
        mode=mode,
    )


def _ledger_from_environment(start: Path | None) -> Path | None:
    override = os.environ.get("QEW_LEDGER")
    if override:
        return Path(override).resolve()
    here = (start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        if (directory / LEDGER_FILE).is_file():
            return directory
    return None


SCHEMA_VERSIONS = (1, 2, 3, 4, 5, 6, 7)  # this qew works on the latest layout only; `qew migrate-layout` moves any older one straight to 7
WORKABLE_SCHEMA = 4  # a schema 4 ledger works unchanged: 5 only adds the optional `fields` header key, which waits for the migration
CURRENT_SCHEMA = 7  # items live at items/<ID>/item.md (3), completed items carry their proof in the header (4), items carry named fields (5), a git ledger has core.untrackedCache set (6), the forge-synced baselines are committed under baselines/ (7); 1 and 2 kept items/<ID>.md
_old_layout_allowed = False


@contextmanager
def old_layout_allowed(allowed: bool) -> Iterator[None]:
    """Let `load` accept a ledger of an older layout (only `qew migrate-layout` does, to migrate it)."""
    global _old_layout_allowed
    previous, _old_layout_allowed = _old_layout_allowed, allowed
    try:
        yield
    finally:
        _old_layout_allowed = previous


def ledger_schema(ledger: Path) -> int:
    """The ledger's declared schema_version (1 when the file or the key is missing)."""
    try:
        data = tomllib.loads((ledger / LEDGER_FILE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return 1
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise QewError(f"{ledger / LEDGER_FILE}: {error}", 1) from None
    version = data.get("schema_version", 1)
    return version if type(version) is int else 1


def _ledger_prefix(ledger: Path) -> str:
    """The ledger's optional display prefix; item IDs do not carry it."""
    try:
        data = tomllib.loads((ledger / LEDGER_FILE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ""
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise QewError(f"{ledger / LEDGER_FILE}: {error}", 1) from None
    version = data.get("schema_version", 1)
    if version not in SCHEMA_VERSIONS or isinstance(version, bool):
        raise QewError(f"{ledger / LEDGER_FILE}: schema_version {version!r} is not supported by this qew "
                       f"({', '.join(map(str, SCHEMA_VERSIONS))}); upgrade qew", 2)
    if version < WORKABLE_SCHEMA and not _old_layout_allowed:
        raise QewError(f"{ledger / LEDGER_FILE}: schema_version {version} is an older layout; run `qew migrate-layout` once to move "
                       f"it straight to schema_version {CURRENT_SCHEMA} (each item in items/ID/item.md, completed items with their evidence in the header)", 2)
    prefix = data.get("prefix", "")
    if not isinstance(prefix, str):
        raise QewError(f"{ledger / LEDGER_FILE}: prefix must be a string", 1)
    return prefix


def is_read_only(config: Config, mirror: Mirror) -> bool:
    """One-way targets: marked `read_only`, or any target but the primary once a primary is set."""
    slug = mirror_slug(mirror)
    if slug == config.authority:
        return False
    return mirror.read_only or config.authority != "none"


def validated_mirror(entry: dict[str, object]) -> Mirror:
    """The mirror a `[[mirror]]` table would define, refusing what the loader refuses."""
    return _mirror(entry)


def mirror_slug(mirror: Mirror) -> str:
    """The `<forge>-<host>-<repo-slug>` stem of a mirror file."""
    without_scheme = mirror.repo.split("://", 1)[-1].casefold()
    host, _, path = without_scheme.partition("/")
    path = path.rstrip("/").removesuffix(".git")
    slug = "".join(c if c.isalnum() or c in ".-" else "-" for c in path)
    return f"{mirror.forge}-{host}-{slug}"
