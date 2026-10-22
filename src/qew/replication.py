"""A write session: lock the ledger checkout, record the files a command writes, commit them as one unit.

Every ledger-writing command runs inside `session`. If the command fails before finishing its work the
files it wrote are removed or restored (atomic); if the commit fails the same happens and the command
fails. Forge-facing commands keep their partial work instead, because a mirror file records something the
forge already accepted.
"""

import argparse
import contextlib
from contextlib import contextmanager
from dataclasses import dataclass
import os
import sys
from pathlib import Path
import time
from typing import Any
from collections.abc import Callable, Iterable, Iterator

from qew import claim_authority, filelock, ledger_config, ledger_store, trace
from qew.errors import QewError
from qew.ledger_config import Config
from qew.ledger_repo import LedgerRepo, RepoLocked

type Entries = list[tuple[Path, str | None]]

LOCK_WAIT_SECONDS = 60.0
CHECKPOINT_EVERY = 25
# Commands that keep partial work when they fail midway (see module docstring).
KEEP_PARTIAL = {"sync", "board", "import", "restore", "approve", "revert", "land", "migrate-ids"}


_AFTER_SETTLE: list[Callable[[], None]] = []
_DISCARD = False


def discard_writes() -> None:
    """A dry run: the session undoes what the command wrote instead of committing it."""
    global _DISCARD
    _DISCARD = True
    claim_authority.DRY_RUN = True


def _withdraw(config: Config) -> None:
    """The command's ledger writes were undone: take back the claims it put on the primary forge, and say which it could not."""
    from qew.ledger_store import open_ledger

    if not claim_authority.POSTED:
        return
    failed = claim_authority.withdraw_posted(config, open_ledger(config.ledger_path, config.prefix))
    if failed:
        print(f"qew: warning: could not withdraw the primary forge's claim on {' '.join(failed)}; clear each with `qew release ID --force --reason TEXT`", file=sys.stderr)


def after_settle(callback: Callable[[], None]) -> None:
    """Run `callback` once the ledger remote has confirmed this command's claim (never when it was lost)."""
    _AFTER_SETTLE.append(callback)


def verb_for(arguments: argparse.Namespace) -> str | None:
    """The commit verb of a ledger-writing command, or None for reads and non-ledger commands."""
    command: str = arguments.command
    if command in ("create", "set", "start", "release", "evidence", "complete", "close", "reopen", "sync",
                   "import", "restore", "accept", "reject", "resolve", "comment", "block", "unblock"):
        return command
    if command == "draft" and arguments.draft_command in ("approve", "revert"):
        return str(arguments.draft_command)
    if command == "project" and arguments.project_command == "ensure":
        return "board"
    if command == "board" and arguments.board_command in ("create", "set"):
        return "view"
    if command == "forge":
        return "migrate"
    if command == "pr" and arguments.pr_command == "merge" and arguments.check:
        return None  # a check writes nothing
    if command == "pr" and arguments.pr_command in ("open", "ready", "close", "merge"):
        return f"pr-{arguments.pr_command}"
    if command == "item" and arguments.item_command in ("attach", "detach", "rename", "assign", "unassign") and not getattr(arguments, "dry_run", False):
        return str(arguments.item_command)
    if command == "pr" and arguments.pr_command == "check" and (arguments.add or arguments.drop):
        return "pr-check"  # these record a comment on the covered items; ticking and listing write only the forge
    if command == "land":
        return "land"
    if command == "batch":
        return "batch"
    if command == "mirror" and arguments.mirror_command == "rehome":
        return "rehome"
    if command == "correct-author" and not arguments.dry_run:
        return "correct-author"
    if command in ("migrate-ids", "migrate-layout", "repair-evidence") and not arguments.dry_run:
        return command
    return None


def _item_ids(relative_paths: list[str]) -> list[str]:
    ids: set[str] = set()
    for path in relative_paths:
        parts = path.split("/")
        if parts[0] in ("items", "events", "mirrors", "baselines", "proposals") and len(parts) > 1:
            ids.add(parts[1][:-3] if parts[1].endswith(".md") else parts[1])
    return sorted(ids)


def message(verb: str, relative_paths: list[str]) -> str:
    ids = [] if verb in ("migrate-ids", "migrate-layout") else _item_ids(relative_paths)  # a rename touches the old and the new id
    who = ledger_store.actor()
    if len(ids) == 1:
        return f"qew: {verb} {ids[0]} by {who}"
    if ids:
        return f"qew: {verb} {len(ids)} items by {who}"
    return f"qew: {verb} by {who}"


def _who() -> str:
    try:
        return ledger_store.actor()
    except QewError:
        return ""


WRITING = "writing"  # `.qew/writing`: present while a command holds the write lock and writes; a killed one leaves it behind


@contextmanager
def _lock(ledger_path: Path, name: str = "lock", verb: str = "", wait_s: float = LOCK_WAIT_SECONDS) -> Iterator[None]:
    """The write lock (`lock`), or the push lock (`push.lock`): held by a write while its worker starts and its push runs,
    so the worker commits its mirror files only after that push is done. A kernel lock (see filelock): a dead holder
    frees it, and the file left behind is inert."""
    marker = ledger_path / ".qew" / WRITING
    with filelock.hold(ledger_path / ".qew" / name, verb=verb, who=_who(), wait_s=wait_s,
                       what=f"the ledger {ledger_path}", polite=verb == "sync"):  # a sync lets waiting verbs go first
        writing = name == "lock" and verb != "read"
        if writing:
            marker.touch()
        try:
            yield
        finally:
            if writing:
                marker.unlink(missing_ok=True)


_ACTIVE: tuple[Config, LedgerRepo, bool, bool] | None = None  # (config, repo, verb) of the running sync session, for checkpoints


@contextmanager
def session(arguments: argparse.Namespace) -> Iterator[None]:
    verb = verb_for(arguments)
    _arm_miss_pull(arguments, held=verb is not None)
    if verb is None:
        _pull_for_read(arguments)
        yield
        return
    try:
        config = ledger_config.load()
    except QewError:
        yield  # no config yet: the command itself reports it
        return
    ledger_store.actor()  # refuse before any write when no identity is configured
    repo = LedgerRepo(config.ledger_path, config.replication.branch)
    if not (config.ledger_path / ledger_config.LEDGER_FILE).is_file():
        yield
        return
    rep = config.replication
    committing = repo.vcs is not None and rep.auto_commit
    replicating = committing and not rep.offline and rep.auto_push and not os.environ.get("QEW_NO_FETCH")
    if verb == "sync":
        yield from _sync_session(config, repo, committing, replicating, not getattr(arguments, "dry_run", False))
        return
    with _lock(config.ledger_path, "lock", verb):
        repo.exclude_cache()
        if committing:
            _recover(repo, deferred=True)
        if replicating and verb != "resolve":  # resolve settles a conflict the fetch would only re-raise
            _note(config, repo.fetch_merge(rep.timeout_s), repo.last_failure)
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
                _withdraw(config)
            raise
        entries = ledger_store.journal_take()
        pending = claim_authority.take_pending()
        if _DISCARD:
            ledger_store.journal_rollback(entries)
            return
        if committing and entries:
            try:
                _commit(repo, config, verb, entries, rollback_on_failure=True)
            except QewError:
                _withdraw(config)
                raise
        claim_authority.POSTED.clear()
        if replicating and pending:
            _settle_claim(config, repo, pending)
        confirmed, _AFTER_SETTLE[:] = list(_AFTER_SETTLE), []
        for callback in confirmed:
            callback()
        push_after = replicating and not pending
    touched = _item_ids(_relative(config, entries)) if entries and verb != "board" else []
    if touched and push_after and _worker_starts(config):
        # The board work is what the owner waits for: start its worker first, then push. The worker's own ledger commit
        # waits on the push lock, so the two never touch the checkout at once.
        with _lock(config.ledger_path, "push.lock", verb):
            _after_write(config, touched)
            _note(config, repo.push(rep.timeout_s), repo.last_failure)
        return
    if push_after:
        # Outside the write lock: a slow remote must not queue every other writer behind this one.
        _note(config, repo.push(rep.timeout_s), repo.last_failure)
    if touched:
        _after_write(config, touched)


def _write_claim_events(config: Config, repo: LedgerRepo, events: list[tuple[str, str, dict[str, Any]]]) -> None:
    """Record (item id, event type, data) claim events as one commit and one push, however many claims the command made."""
    from qew.ledger_store import open_ledger

    ledger = open_ledger(config.ledger_path, config.prefix)
    ledger_store.journal_start()
    try:
        for item_id, event_type, data in events:
            ledger.append_event(item_id, event_type, **data)
    finally:
        entries = ledger_store.journal_take()
    _commit(repo, config, events[0][1].replace("_", "-"), entries, rollback_on_failure=True)
    repo.push(config.replication.timeout_s)


def _settle_claim(config: Config, repo: LedgerRepo, pending: list[tuple[str, str]]) -> None:
    """The claims are committed locally; the ledger remote decides whether they count (first push to land wins).

    One command may claim several items (an epic and its descendants): they stand or fall together, so a loss
    is recorded for every claim of the command.
    """
    from qew.ledger_event import State, fold
    from qew.ledger_store import open_ledger

    rep = config.replication
    ledger = open_ledger(config.ledger_path, config.prefix)

    def lose(winner: str) -> None:
        _write_claim_events(config, repo, [(item_id, "claim_lost", {"ref": claim_id, "winner": winner}) for item_id, claim_id in pending])

    for _ in range(3):
        outcome = repo.push_strict(rep.timeout_s)
        _note(config, "ok" if outcome == "ok" else "offline" if outcome == "offline" else "ok", repo.last_failure)
        if outcome == "ok":
            _write_claim_events(config, repo, [(item_id, "claim_confirmed", {"ref": claim_id}) for item_id, claim_id in pending])
            return
        if outcome == "offline":
            lose("(authority unreachable)")
            raise QewError(f"cannot claim {pending[0][0]}: the ledger remote became unreachable, so the claim is not held", 3)
        for item_id, claim_id in pending:
            others = fold([e for e in ledger.load_events(item_id) if e.id != claim_id])
            if others.state == State.CLAIMED:
                lose(others.owner)
                raise QewError(f"cannot claim {item_id}: lost the claim race to {others.owner}", 2)
    raise QewError(f"cannot claim {pending[0][0]}: the ledger remote kept rejecting the push", 3)


def _sync_session(config: Config, repo: LedgerRepo, committing: bool, replicating: bool, push_repos: bool) -> Iterator[None]:
    """Sync takes the lock only to commit: forge calls can be slow and must never block other writers."""
    global _ACTIVE
    rep = config.replication
    with _lock(config.ledger_path, "lock", "sync"):
        repo.exclude_cache()
        if committing:
            _recover(repo, deferred=True)
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
            with _lock(config.ledger_path, "push.lock", "sync"), _lock(config.ledger_path, "lock", "sync"):
                _commit(repo, config, "sync", entries, rollback_on_failure=False)
                if replicating:
                    repo.push(rep.timeout_s)
        elif replicating and repo.unpushed():
            # every write was checkpointed (committed on the way, never pushed): nothing is left to commit, but commits are
            with _lock(config.ledger_path, "push.lock", "sync"):
                repo.push(rep.timeout_s)
        if push_repos:
            from qew import repo_push

            report = repo_push.push_everywhere(config)
            if report:
                print("\n".join(report))


def checkpoint() -> None:
    """Commit the mirror files a running sync has written so far, so a kill loses at most a few."""
    if _ACTIVE is None:
        return
    config, repo, committing, replicating = _ACTIVE
    entries = ledger_store.journal_take()
    ledger_store.journal_start()
    if committing and len(entries) >= CHECKPOINT_EVERY:
        with _lock(config.ledger_path, "push.lock", "sync"), _lock(config.ledger_path, "lock", "sync"):
            _commit(repo, config, "sync", entries, rollback_on_failure=False)
    else:
        journal = ledger_store.JOURNAL
        if journal is not None:
            journal.extend(entries)


def _worker_starts(config: Config) -> bool:
    """Whether `_after_write` will start a detached worker (so it can run before the push)."""
    rep = config.replication
    return bool(config.mirrors) and rep.auto_forge_sync and not rep.offline and rep.background_sync and not os.environ.get("QEW_BACKGROUND")


def _after_write(config: Config, item_ids: Iterable[str]) -> None:
    """Sync the items this write touched (and what their change moves): detached and silent, or inline within a budget."""
    rep = config.replication
    if not config.mirrors or not rep.auto_forge_sync or rep.offline or os.environ.get("QEW_BACKGROUND"):
        return
    from qew import syncworker

    if rep.background_sync:
        syncworker.queue(config, item_ids)
        return
    import argparse
    from qew import cmd_sync

    arguments = argparse.Namespace(command="sync", target=None, item=list(item_ids) or None, dry_run=False)
    try:
        with session(arguments):
            cmd_sync.sync_command(config, None, list(item_ids) or None, False, budget_s=rep.sync_budget_s, tick=checkpoint)
    except QewError:
        syncworker.mark(config, True)  # failures stay pending; the next command retries


def _note(config: Config, result: str, reason: str = "") -> None:
    """Remember the last remote attempt so `qew status` can show an outage without a network call."""
    if result in ("ok", "offline"):
        cache = config.ledger_path / ".qew"
        cache.mkdir(exist_ok=True)
        (cache / "remote-state").write_text(result, encoding="utf-8")
        if result == "offline" and reason:
            (cache / "remote-error").write_text(reason + "\n", encoding="utf-8")
            if "timed out" in reason:  # not an unreachable remote the user already knows about: say it, the work is saved
                print(f"qew: warning: {reason}; the ledger commit is saved and the next write or `qew sync` pushes it", file=sys.stderr)
        else:
            (cache / "remote-error").unlink(missing_ok=True)


@dataclass(frozen=True)
class Pulled:
    before: str
    after: str
    commits: int
    items: tuple[str, ...]


def pull(config: Config, held: bool = False, wait_s: float = LOCK_WAIT_SECONDS) -> Pulled:
    """Fetch and merge the ledger remote and nothing else: no forge call, no worker, no claim arbiter. `held` says the caller
    already holds the ledger lock (a writer). Raises QewError when the remote is unreachable or the merge conflicts."""
    rep = config.replication
    repo = LedgerRepo(config.ledger_path, rep.branch)
    if repo.vcs is None or not repo.has_remote():
        raise QewError("the ledger has no remote to pull from", 2)
    if rep.offline or os.environ.get("QEW_NO_FETCH"):
        raise QewError("offline mode is on (QEW_OFFLINE or [replication] offline); nothing was pulled", 3)
    with contextlib.nullcontext() if held else _lock(config.ledger_path, "lock", "read", wait_s=wait_s):
        repo.exclude_cache()
        crashed = config.ledger_path / ".qew" / WRITING
        if not held and rep.auto_commit and crashed.exists():
            _recover(repo)
            crashed.unlink(missing_ok=True)
        before = repo.tip()
        fetched = repo.fetch_merge(rep.timeout_s)
        _note(config, fetched, repo.last_failure)
        if fetched == "offline":
            raise QewError(f"the ledger remote is unreachable: {repo.last_failure or 'no answer'}; nothing was pulled", 3)
        (config.ledger_path / ".qew" / "last-fetch").touch()
        after = repo.tip()
        paths = repo.changed_between(before, after)
    return Pulled(before, after, repo.count_between(before, after), tuple(_item_ids(paths)))


_MISS_TRIED = False


def _arm_miss_pull(arguments: argparse.Namespace, held: bool) -> None:
    """Let a lookup that finds no item or pull request fetch the ledger remote once, whatever the fetch interval, and look again.
    Not for a command that has unsaved ledger writes (the fetch would merge into them), nor for an offline or read-only-replica ledger."""
    global _MISS_TRIED
    _MISS_TRIED = False
    ledger_store.MISS_HOOK = None
    if arguments.command in ("init", "hook", "status", "conflicts", "unlock", "pull", "sync") or os.environ.get("QEW_NO_FETCH"):
        return
    try:
        config = ledger_config.load()
    except QewError:
        return
    rep = config.replication
    if rep.offline or not rep.auto_push or not (config.ledger_path / ledger_config.LEDGER_FILE).is_file():
        return

    def on_miss() -> bool:
        global _MISS_TRIED
        if _MISS_TRIED or ledger_store.JOURNAL:
            return False
        _MISS_TRIED = True
        try:
            return bool(pull(config, held=held, wait_s=0).commits)
        except (QewError, filelock.LockTimeout):
            return False  # the lookup's own refusal is the answer; a failed pull has been noted for `qew status`

    ledger_store.MISS_HOOK = on_miss


def _pull_for_read(arguments: argparse.Namespace) -> None:
    """Reads see other agents' work: fetch and merge the ledger remote, at most every fetch_interval_s."""
    if arguments.command in ("init", "hook", "status", "conflicts", "unlock", "pull"):
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
    try:
        with _lock(config.ledger_path, "lock", "read", wait_s=0):  # a writer holds it: it fetched at its own start, so a read goes on with what is local
            repo.exclude_cache()
            crashed = config.ledger_path / ".qew" / WRITING
            if rep.auto_commit and crashed.exists():  # a killed writer left files behind; looking for them otherwise scans every file
                _recover(repo)
                crashed.unlink(missing_ok=True)
            fetched = repo.fetch_merge(rep.timeout_s)
            _note(config, fetched, repo.last_failure)
            if fetched == "ok" and repo.unpushed():
                _note(config, repo.push(rep.timeout_s), repo.last_failure)  # catch up commits made while the remote was unreachable
            stamp.touch()
    except filelock.LockTimeout:
        return
    if config.mirrors and rep.auto_forge_sync and not rep.offline:
        from qew import syncworker

        syncworker.retry_if_pending(config)


def _relative(config: Config, entries: Entries) -> list[str]:
    root = config.ledger_path
    return sorted({str(path.relative_to(root)) for path, _ in entries if path.is_relative_to(root)})


def _commit(repo: LedgerRepo, config: Config, verb: str, entries: Entries, rollback_on_failure: bool) -> None:
    paths = _relative(config, entries)
    try:
        with trace.phase(f"ledger commit ({len(paths)} files)"):
            repo.commit(paths, message(verb, paths))
    except QewError as error:
        if rollback_on_failure:
            ledger_store.journal_rollback(entries)
            raise QewError(f"ledger commit failed, the write was rolled back: {error}",
                           error.exit_code if isinstance(error, RepoLocked) else 1) from None
        raise


def _recover(repo: LedgerRepo, deferred: bool = False) -> None:
    """Commit qew-owned files a crashed command left behind, before this command writes. On a jj ledger a writing command
    (`deferred`) skips the scan: its own commit snapshots once and sweeps what it finds (`LedgerRepo.commit`)."""
    if deferred and repo.vcs == "jj":
        return
    stray = repo.dirty_owned()
    if stray:
        repo.commit(stray, f"qew: recover {len(stray)} files")
