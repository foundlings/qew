"""`qew ledger fork`: a new ledger identity that keeps the work and leaves the authority behind.

Items and their events are carried over under the same ids. Mirrors, boards, the claim in force on open work and
unresolved divergences are not: the fork starts with nobody holding anything and no forge attached.
"""

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path

from qew import ledger_config, ledger_lineage
from qew.errors import QewError
from qew.ledger_aliases import ITEM_FILE
from qew.ledger_config import LEDGER_FILE, Config
from qew.ledger_event import Event, State, fold, parse_event
from qew.ledger_repo import LedgerRepo
from qew.repo_push import url_problem

_TARGET = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*")
_IDENTITY_LINES = re.compile(r"^(ledger_id|forked_from)\s*=.*\n", re.M)
PUSH_TIMEOUT_S = 120


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    ledger = commands.add_parser("ledger", help="ledger identity: fork")
    sub = ledger.add_subparsers(dest="ledger_command", required=True)
    fork = sub.add_parser("fork", help="push a copy with a new identity: same items, no mirrors, claims or divergences")
    fork.add_argument("--to", required=True, help="git remote (URL or path) of the new, empty ledger repository")
    fork.add_argument("--primary", help="the fresh forge target the fork will use as primary (printed as config)")


def run(arguments: argparse.Namespace) -> bool:
    if arguments.command != "ledger":
        return False
    print(fork(ledger_config.load(), arguments.to, arguments.primary))
    return True


def _git(path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(path), *arguments], capture_output=True, text=True, timeout=PUSH_TIMEOUT_S,
                          env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}, check=False)


def _origin_commit(config: Config) -> str:
    repo = LedgerRepo(config.ledger_path, config.replication.branch)
    if repo.vcs is None:
        raise QewError("the ledger is not a git or jj repository; a fork needs a commit to record", 2)
    for ref in (f"refs/heads/{config.replication.branch}", "HEAD"):
        found = _git(config.ledger_path, "rev-parse", "--verify", "-q", ref)
        if found.returncode == 0:
            return found.stdout.strip()
    raise QewError("the ledger has no commit yet; a fork needs one to record", 2)


def _carried(events: list[Event]) -> list[Event]:
    """The events a fork keeps: everything but the claim in force on open work and unresolved divergences."""
    folded = fold(events)
    dropped = {e.id for e in folded.divergences}
    if folded.state == State.CLAIMED:
        lost = {e.get("ref") for e in events if e.type == "claim_lost"}
        reset = max((e.id for e in events if e.type in ("released", "approved", "reverted", "reopened")), default="")
        claims = {e.id for e in events if e.type == "claimed" and e.id > reset and e.id not in lost}
        dropped |= claims | {e.id for e in events if e.type in ("claim_confirmed", "claim_lost") and e.get("ref") in claims}
    kept = [e for e in events if e.id not in dropped]
    if fold(kept).errors:
        raise QewError(f"cannot fork: the history of an item does not fold without its claim ({fold(kept).errors[0]})", 1)
    return kept


def _write_copy(config: Config, target: Path, ledger_id: str, forked_from: str) -> None:
    source = config.ledger_path
    (target / "items").mkdir()
    for path in sorted(p / ITEM_FILE for p in (source / "items").iterdir() if (p / ITEM_FILE).is_file()):
        (target / "items" / path.parent.name).mkdir()
        shutil.copyfile(path, target / "items" / path.parent.name / ITEM_FILE)
        events = []
        directory = source / "events" / path.parent.name
        for event_path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
            events.append((parse_event(event_path.stem, event_path.read_text(encoding="utf-8"), str(event_path)), event_path))
        keep = {e.id for e in _carried([e for e, _ in events])}
        (target / "events" / path.parent.name).mkdir(parents=True)
        for event, event_path in events:
            if event.id in keep:
                shutil.copyfile(event_path, target / "events" / path.parent.name / event_path.name)
    text = _IDENTITY_LINES.sub("", (source / LEDGER_FILE).read_text(encoding="utf-8"))
    header = f'ledger_id = {json.dumps(ledger_id)}\nforked_from = {json.dumps(forked_from)}\n'
    (target / LEDGER_FILE).write_text(header + text, encoding="utf-8")


def fork(config: Config, remote: str, primary: str | None) -> str:
    problem = url_problem(remote)
    if problem:
        raise QewError(f"--to {problem}", 2)
    if primary is not None and not _TARGET.fullmatch(primary):
        raise QewError("--primary names a target slug: letters, digits, `.`, `-`, `_` and `:`", 2)
    old_id, _ = ledger_lineage.parse((config.ledger_path / LEDGER_FILE).read_text(encoding="utf-8"))
    origin = old_id or _name(config)
    commit = _origin_commit(config)
    forked_from = f"{origin}@{commit}"
    new_id = secrets.token_hex(8)
    with tempfile.TemporaryDirectory() as temporary:
        copy = Path(temporary)
        (copy / "events").mkdir()
        _write_copy(config, copy, new_id, forked_from)
        identity = [] if _git(copy, "config", "user.name").returncode == 0 else ["-c", "user.name=qew", "-c", "user.email=qew@localhost.invalid"]
        steps = [("init", "-q", "-b", "main"), ("add", "-A"), (*identity, "commit", "-q", "-m", f"qew: fork of {forked_from}"),
                 ("push", "-q", remote, "main:main")]
        for step in steps:
            done = _git(copy, *step)
            if done.returncode != 0:
                raise QewError(f"fork failed at `git {step[0]}`: {done.stderr.strip()[:300]}", 3)
    lines = [f"forked {forked_from} as {new_id}, pushed to {remote}"]
    if primary:
        lines += ["in the fork's code repo config:", "[authority]", f'primary = "{primary}"']
    return "\n".join(lines)


def _name(config: Config) -> str:
    import tomllib

    data = tomllib.loads((config.ledger_path / LEDGER_FILE).read_text(encoding="utf-8"))
    name = data.get("name")
    if not isinstance(name, str) or not name:
        raise QewError(f"{LEDGER_FILE}: the origin has neither ledger_id nor name to record as lineage", 1)
    return name
