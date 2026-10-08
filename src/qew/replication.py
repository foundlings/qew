"""A write session: lock the ledger checkout, record the files a command writes, commit them as one unit.

Every ledger-writing command runs inside `session`. If the command fails before finishing its work the
files it wrote are removed or restored (atomic); if the commit fails the same happens and the command
fails. Forge-facing commands keep their partial work instead, because a mirror file records something the
forge already accepted.
"""

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import time
from typing import Iterator, List, Optional, Set

from qew import claim_authority, ledger_config, ledger_store
from qew.errors import QewError
from qew.ledger_repo import LedgerRepo

LOCK_WAIT_SECONDS = 60.0
CHECKPOINT_EVERY = 25
# Commands that keep partial work when they fail midway (see module docstring).
KEEP_PARTIAL = {"sync", "board", "import", "restore", "approve", "revert", "land"}


def verb_for(arguments) -> Optional[str]:
    """The commit verb of a ledger-writing command, or None for reads and non-ledger commands."""
    command = arguments.command
    if command in ("create", "set", "start", "release", "evidence", "complete", "close", "reopen", "sync",
                   "import", "restore", "accept", "reject", "comment"):
        return command
    if command == "draft" and arguments.draft_command in ("approve", "revert"):
        return arguments.draft_command
    if command == "project" and arguments.project_command == "ensure":
        return "board"
    if command == "board" and arguments.board_command in ("create", "set"):
        return "view"
    if command == "pr" and arguments.pr_command in ("open", "ready", "merge"):
        return f"pr-{arguments.pr_command}"
    if command == "land":
        return "land"
    return None


def _item_ids(relative_paths: List[str]) -> List[str]:
    ids: Set[str] = set()
    for path in relative_paths:
        parts = path.split("/")
        if parts[0] in ("items", "events", "mirrors", "proposals") and len(parts) > 1:
            ids.add(parts[1][:-3] if parts[1].endswith(".md") else parts[1])
    return sorted(ids)


def message(verb: str, relative_paths: List[str]) -> str:
    ids = _item_ids(relative_paths)
    who = ledger_store.actor()
    if len(ids) == 1:
        return f"qew: {verb} {ids[0]} by {who}"
    if ids:
        return f"qew: {verb} {len(ids)} items by {who}"
    return f"qew: {verb} by {who}"


@contextmanager
def _lock(ledger_path: Path) -> Iterator[None]:
    cache = ledger_path / ".qew"
    cache.mkdir(exist_ok=True)
    handle = open(cache / "lock", "a+")
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    try:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise QewError(f"the ledger {ledger_path} is locked by another qew process", 3) from None
                time.sleep(0.05)
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        yield
    finally:
        handle.close()


_ACTIVE = None  # (config, repo, verb) of the running sync session, for checkpoints


@contextmanager
def session(arguments) -> Iterator[None]:
    verb = verb_for(arguments)
    if verb is None:
        _pull_for_read(arguments)
        yield
        return
    try:
        config = ledger_config.load()
    except QewError:
        yield  # no config yet: the command itself reports it
        return
    repo = LedgerRepo(config.ledger_path, config.replication.branch)
    if not (config.ledger_path / ledger_config.LEDGER_FILE).is_file():
        yield
        return
    rep = config.replication
    committing = repo.vcs is not None and rep.auto_commit
    replicating = committing and not rep.offline and rep.auto_push and not os.environ.get("QEW_NO_FETCH")
    if verb == "sync":
        yield from _sync_session(config, repo, committing, replicating)
        return
    with _lock(config.ledger_path):
        repo.exclude_cache()
        if committing:
            _recover(repo)
        if replicating:
            _note(config, repo.fetch_merge(rep.timeout_s))
            (config.ledger_path / ".qew" / "last-fetch").touch()
        ledger_store.journal_start()
        try:
            yield
        except BaseException:
            claim_authority.take_pending()
            entries = ledger_store.journal_take()
            if verb in KEEP_PARTIAL:
                if committing and entries:
                    _commit(repo, config, verb, entries, rollback_on_failure=False)
                    if replicating:
                        repo.push(rep.timeout_s)
            else:
                ledger_store.journal_rollback(entries)
            raise
        entries = ledger_store.journal_take()
        pending = claim_authority.take_pending()
        if committing and entries:
            _commit(repo, config, verb, entries, rollback_on_failure=True)
        if replicating and pending:
            _settle_claim(config, repo, pending)
        push_after = replicating and not pending
    if push_after:
        # Outside the write lock: a slow remote must not queue every other writer behind this one.
        _note(config, repo.push(rep.timeout_s))
    if entries and verb != "board":
        _after_write(config)


def _write_claim_event(config, repo: LedgerRepo, ledger_path, item_id: str, event_type: str, **data) -> None:
    from qew.ledger_store import open_ledger

    ledger = open_ledger(config.ledger_path, config.prefix)
    ledger_store.journal_start()
    try:
        ledger.append_event(item_id, event_type, **data)
    finally:
        entries = ledger_store.journal_take()
    _commit(repo, config, event_type.replace("_", "-"), entries, rollback_on_failure=True)
    repo.push(config.replication.timeout_s)


def _settle_claim(config, repo: LedgerRepo, pending) -> None:
    """The claim is committed locally; the ledger remote decides whether it counts (first push to land wins)."""
    from qew.ledger_event import State, fold
    from qew.ledger_store import open_ledger

    item_id, claim_id = pending
    rep = config.replication
    ledger = open_ledger(config.ledger_path, config.prefix)
    for _ in range(3):
        outcome = repo.push_strict(rep.timeout_s)
        _note(config, "ok" if outcome == "ok" else "offline" if outcome == "offline" else "ok")
        if outcome == "ok":
            _write_claim_event(config, repo, None, item_id, "claim_confirmed", ref=claim_id)
            return
        if outcome == "offline":
            _write_claim_event(config, repo, None, item_id, "claim_lost", ref=claim_id, winner="(authority unreachable)")
            raise QewError(f"cannot claim {item_id}: the ledger remote became unreachable, so the claim is not held", 3)
        others = fold([e for e in ledger.load_events(item_id) if e.id != claim_id])
        if others.state == State.CLAIMED:
            _write_claim_event(config, repo, None, item_id, "claim_lost", ref=claim_id, winner=others.owner)
            raise QewError(f"cannot claim {item_id}: lost the claim race to {others.owner}", 2)
    raise QewError(f"cannot claim {item_id}: the ledger remote kept rejecting the push", 3)


def _sync_session(config, repo: LedgerRepo, committing: bool, replicating: bool) -> Iterator[None]:
    """Sync takes the lock only to commit: forge calls can be slow and must never block other writers."""
    global _ACTIVE
    rep = config.replication
    with _lock(config.ledger_path):
        repo.exclude_cache()
        if committing:
            _recover(repo)
        if replicating:
            repo.fetch_merge(rep.timeout_s)
    ledger_store.journal_start()
    _ACTIVE = (config, repo, committing, replicating)
    try:
        yield
    finally:
        _ACTIVE = None
        entries = ledger_store.journal_take()
        if committing and entries:
            with _lock(config.ledger_path):
                _commit(repo, config, "sync", entries, rollback_on_failure=False)
                if replicating:
                    repo.push(rep.timeout_s)


def checkpoint() -> None:
    """Commit the mirror files a running sync has written so far, so a kill loses at most a few."""
    if _ACTIVE is None:
        return
    config, repo, committing, replicating = _ACTIVE
    entries = ledger_store.journal_take()
    ledger_store.journal_start()
    if committing and len(entries) >= CHECKPOINT_EVERY:
        with _lock(config.ledger_path):
            _commit(repo, config, "sync", entries, rollback_on_failure=False)
    else:
        ledger_store.JOURNAL.extend(entries)


def _after_write(config) -> None:
    """Opportunistic forge sync of the pending items: detached and silent, or inline within a budget."""
    rep = config.replication
    if not config.mirrors or not rep.auto_forge_sync or rep.offline or os.environ.get("QEW_BACKGROUND"):
        return
    from qew import syncworker

    if rep.background_sync:
        print(syncworker.queue(config))
        return
    import argparse
    from qew import cmd_sync

    arguments = argparse.Namespace(command="sync", target=None, item=None, dry_run=False)
    try:
        with session(arguments):
            cmd_sync.sync_command(config, None, None, False, budget_s=rep.sync_budget_s, tick=checkpoint)
    except QewError:
        syncworker.mark(config, True)  # failures stay pending; the next command retries


def _note(config, result: str) -> None:
    """Remember the last remote attempt so `qew status` can show an outage without a network call."""
    if result in ("ok", "offline"):
        (config.ledger_path / ".qew").mkdir(exist_ok=True)
        (config.ledger_path / ".qew" / "remote-state").write_text(result, encoding="utf-8")


def _pull_for_read(arguments) -> None:
    """Reads see other agents' work: fetch and merge the ledger remote, at most every fetch_interval_s."""
    if arguments.command in ("init", "hook", "status"):
        return
    try:
        config = ledger_config.load()
    except QewError:
        return
    rep = config.replication
    repo = LedgerRepo(config.ledger_path, rep.branch)
    if repo.vcs is None or rep.offline or not rep.auto_push or not (config.ledger_path / ledger_config.LEDGER_FILE).is_file():
        return
    stamp = config.ledger_path / ".qew" / "last-fetch"
    if stamp.exists() and time.time() - stamp.stat().st_mtime < rep.fetch_interval_s:
        return
    with _lock(config.ledger_path):
        repo.exclude_cache()
        _recover(repo) if rep.auto_commit else None
        fetched = repo.fetch_merge(rep.timeout_s)
        _note(config, fetched)
        if fetched == "ok":
            _note(config, repo.push(rep.timeout_s))  # catch up commits made while the remote was unreachable
        stamp.touch()
    if config.mirrors and rep.auto_forge_sync and not rep.offline:
        from qew import syncworker

        syncworker.retry_if_pending(config)


def _relative(config, entries) -> List[str]:
    root = config.ledger_path
    return sorted({str(path.relative_to(root)) for path, _ in entries if path.is_relative_to(root)})


def _commit(repo: LedgerRepo, config, verb: str, entries, rollback_on_failure: bool) -> None:
    paths = _relative(config, entries)
    try:
        repo.commit(paths, message(verb, paths))
    except QewError as error:
        if rollback_on_failure:
            ledger_store.journal_rollback(entries)
            raise QewError(f"ledger commit failed, the write was rolled back: {error}", 1) from None
        raise


def _recover(repo: LedgerRepo) -> None:
    """Commit qew-owned files a crashed command left behind, before this command writes."""
    stray = repo.dirty_owned()
    if stray:
        repo.commit(stray, f"qew: recover {len(stray)} files")
