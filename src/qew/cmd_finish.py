"""Finish work: `evidence`, `complete`, `close`, `reopen`.

`complete` is the only way to reach the completed state for claimed work: the caller holds the claim, every
child is finished, and (except for epics) the delivering code is an ancestor of the target branch. Work that
shipped without a claim is closed with `close --delivered-by`, under the same landing proof.
"""

import argparse
import json
import sys
from pathlib import Path

from qew import claim_reconcile, code_vcs, complete_batch, evidence_batch, evidence_resolve, ledger_graph, manifest_rows, outcome
from qew.cmd_claim import holding, holds_ancestor
from qew.cmd_items import ledger_for
from qew.code_vcs import Landing
from qew.errors import QewError
from qew.jsontypes import JsonObject
from qew.ledger_config import Config
from qew.ledger_event import CLOSE_REASONS, State
from qew.ledger_item import Kind, absence_problem
from qew.ledger_store import Ledger, Snapshot, actor
from qew.requester import require_quote

RELEASED_SHOWN = 5  # work a completion released, named in its one line
_FINISHED_WORK = "finished work: qew complete ITEM, or qew close ITEM --delivered-by SHA --landed-on BRANCH"


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    evidence = commands.add_parser("evidence", help="record the code changes that deliver an item")
    evidence.add_argument("item", nargs="?", help="item id or alias (not with --manifest)")
    evidence.add_argument("--manifest", metavar="FILE", help="instead of ITEM: a JSON Lines file of {item, change_ids} rows, recorded in one commit")
    evidence.add_argument("--dry-run", action="store_true", help="with --manifest: say what would be recorded and write nothing")
    evidence.add_argument("--change", action="append", default=[], help="change id (jj) or commit (git), repeatable; default is the current one")
    evidence.add_argument("--vcs", choices=["jj", "git"], help="override the detected VCS")
    evidence.add_argument("--on-behalf-of", metavar="ACTOR", help="record the evidence for a claim ACTOR holds (the integrator citing an elf's change); both actors are recorded, the claim does not move")
    complete = commands.add_parser("complete", help="finish claimed work once the gate passes")
    complete.add_argument("item", nargs="?", help="item id or alias (not with --manifest)")
    complete.add_argument("--manifest", metavar="FILE", help="instead of ITEM: a JSON Lines file of {item, reason} rows, completed in one commit on a human's --quote")
    complete.add_argument("--quote", help="with --manifest: a human's words, \"Name: 'their words'\"; recorded on every completed event")
    complete.add_argument("--dry-run", action="store_true", help="with --manifest: say what would be completed and write nothing")
    complete.add_argument("--reason", help="why the work is done (recorded on the completed event)")
    complete.add_argument("--take-over", action="store_true", help="finish work another actor holds (needs --reason)")
    complete.add_argument("--no-commit", metavar="WHY", help="complete without code, for work delivered outside the repository; WHY is recorded as the absence `no-code`")
    complete.add_argument("--absent", metavar="KIND", help="complete without code for a stated reason (with --reason): no-code, historical or superseded-by:ITEM")
    complete.add_argument("--landed-on", metavar="BRANCH", help="branch the evidence must have landed on (default: the configured target)")
    complete.add_argument("--json", action="store_true", help="machine-readable output")
    close = commands.add_parser("close", help="close work that will not be completed")
    close.add_argument("item", help="item id or alias")
    close.add_argument("--reason", help="not_planned, duplicate (with --of) or superseded (with --by)")
    close.add_argument("--of", metavar="ITEM", help="with --reason duplicate: the original item")
    close.add_argument("--by", metavar="ITEM", help="with --reason superseded: the replacement item")
    close.add_argument("--note", help="free-text rationale, recorded on the closed event")
    close.add_argument("--delivered-by", metavar="SHA", help="close work already delivered by a landed commit nobody claimed (needs --landed-on)")
    close.add_argument("--landed-on", metavar="BRANCH", help="with --delivered-by: the branch the commit landed on")
    close.add_argument("--json", action="store_true", help="machine-readable output")
    reopen = commands.add_parser("reopen", help="reopen completed or closed work")
    reopen.add_argument("item", nargs="?", help="item id or alias (not with --manifest)")
    reopen.add_argument("--manifest", metavar="FILE", help="instead of ITEM: a JSON Lines file of {item, reason} rows, reopened in one commit")
    reopen.add_argument("--dry-run", action="store_true", help="with --manifest: say what would change and write nothing")
    reopen.add_argument("--reason", help="why the work is required again (needed unless the item is already open)")
    reopen.add_argument("--json", action="store_true", help="machine-readable output")


def run(arguments: argparse.Namespace) -> bool:
    if arguments.command not in ("evidence", "complete", "close", "reopen"):
        return False
    from qew import ledger_config

    config = ledger_config.load()
    if arguments.command == "reopen" and arguments.manifest:
        if arguments.item or arguments.reason:
            raise QewError("--manifest replaces ITEM and --reason: each row carries its own reason", 2)
        print(reopen_manifest(config, Path(arguments.manifest), arguments.dry_run))
        return True
    if arguments.command == "reopen" and (not arguments.item or arguments.dry_run):
        raise QewError("give ITEM, or --manifest FILE (--dry-run belongs to --manifest)", 2)
    if arguments.command == "evidence" and arguments.manifest:
        if arguments.item or arguments.change or arguments.vcs or arguments.on_behalf_of:
            raise QewError("--manifest replaces ITEM and the single-item options: give the manifest alone", 2)
        print(evidence_manifest(config, Path(arguments.manifest), arguments.dry_run))
        return True
    if arguments.command == "evidence":
        if not arguments.item:
            raise QewError("give ITEM, or --manifest FILE", 2)
        if arguments.dry_run:
            raise QewError("--dry-run belongs to --manifest", 2)
        print(evidence(config, arguments.item, arguments.change, arguments.vcs, arguments.on_behalf_of))
        return True
    if arguments.command == "complete" and arguments.manifest:
        if arguments.item or arguments.take_over or arguments.no_commit or arguments.reason or arguments.landed_on:
            raise QewError("--manifest replaces ITEM and the single-item options: give the manifest with --quote alone", 2)
        print(complete_manifest(config, Path(arguments.manifest), arguments.quote, arguments.dry_run))
        return True
    if arguments.command == "complete":
        if not arguments.item:
            raise QewError("give ITEM, or --manifest FILE with --quote", 2)
        if arguments.quote or arguments.dry_run:
            raise QewError("--quote and --dry-run belong to --manifest", 2)
        result = complete(config, arguments.item, arguments.reason, arguments.take_over, arguments.no_commit, arguments.landed_on, arguments.absent)
    elif arguments.command == "close":
        result = close(config, arguments.item, arguments.reason, arguments.of, arguments.by, arguments.note,
                       arguments.delivered_by, arguments.landed_on)
    else:
        result = reopen(config, arguments.item, arguments.reason)
    warning = result.pop("warning", None)
    print(outcome.render(arguments, str(result.pop("line")), **result))
    if warning:
        print(f"warning: {warning}", file=sys.stderr)
    return True


_STATE_WHY: dict[State | None, str] = {
    State.DRAFT: "it is a draft; only a human's quoted words release it (qew draft approve)",
    State.APPROVED: "it is approved and unclaimed",
    State.COMPLETED: "it is completed",
    State.CLOSED: "it is closed (qew reopen)",
}


def _load(config: Config, reference: str) -> tuple[Ledger, Snapshot, str]:
    ledger = ledger_for(config)
    snapshot = ledger.snapshot()
    return ledger, snapshot, ledger.resolve(reference, list(snapshot.items))


def _refuse(snapshot: Snapshot, item_id: str, verb: str, needs: str) -> QewError:
    state = snapshot.folds[item_id].state
    return QewError(f"cannot {verb} {item_id}: {_STATE_WHY.get(state, 'its state is unknown')} (needs {needs})", 2)


def _text(name: str, value: str | None) -> str | None:
    """A free-text option: absent stays absent, present must say something."""
    if value is None:
        return None
    if not value.strip():
        raise QewError(f"--{name} must not be blank", 2)
    return value.strip()


def _branch(name: str) -> str:
    if not name or not code_vcs.valid_branch(name):
        raise QewError(f"`{name}` is not a usable branch name (no leading `-`, no `..`, no spaces)", 2)
    return name


def complete_manifest(config: Config, manifest: Path, quote: str | None, dry_run: bool) -> str:
    """Complete the manifest's items in one write on a human's quote; checked whole first, a rerun is unchanged."""
    verified = require_quote(quote)
    ledger = ledger_for(config)
    try:
        text = manifest.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise QewError(f"cannot read {manifest}: {error}", 2) from None
    snapshot = ledger.snapshot()
    rows: list[complete_batch.Row] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        where = f"line {number}"
        try:
            raw = json.loads(line)
        except ValueError as error:
            raise QewError(f"{where}: invalid JSON ({error})", 2) from None
        if not isinstance(raw, dict) or not isinstance(raw.get("item"), str):
            raise QewError(f"{where}: a row is an object with an `item` (and optional reason)", 2)
        try:
            item_id = ledger.resolve(raw["item"], list(snapshot.items))
        except QewError as error:
            raise QewError(f"{where}: {error}", error.exit_code) from None
        rows.append(complete_batch.row({k: v for k, v in raw.items() if k != "item"}, item_id, where))
    batch = complete_batch.plan(snapshot, rows, ledger.evidence_strict)
    if not dry_run:
        complete_batch.apply(ledger, batch, verified)
    return complete_batch.summary(batch, "would complete" if dry_run else "completed")


def evidence_manifest(config: Config, manifest: Path, dry_run: bool) -> str:
    """Record evidence for many items in one write; checked whole first (see evidence_batch)."""
    ledger = ledger_for(config)
    snapshot = ledger.snapshot()
    rows = manifest_rows.read(ledger, manifest, list(snapshot.items), evidence_batch.ROW_KEYS)
    vcs = code_vcs.detect(config.root)
    batch = evidence_batch.plan(snapshot, rows, vcs, code_vcs.repository_url(config.root), evidence_resolve.resolver(config))
    if not dry_run:
        evidence_batch.apply(ledger, batch)
    return evidence_batch.summary(batch, "would record" if dry_run else "recorded")


def evidence(config: Config, reference: str, changes: list[str], vcs: str | None, on_behalf_of: str | None = None) -> str:
    ledger, snapshot, item_id = _load(config, reference)
    if snapshot.folds[item_id].state not in (State.APPROVED, State.CLAIMED):
        raise _refuse(snapshot, item_id, "record evidence for", "an approved or claimed item")
    folded = snapshot.folds[item_id]
    if on_behalf_of is not None:
        _require_holder(snapshot, item_id, on_behalf_of)
    elif folded.state == State.CLAIMED and folded.owner != actor() and not holds_ancestor(snapshot, item_id, actor()):
        raise QewError(f"cannot record evidence for {item_id}: it is claimed by {holding(snapshot, item_id)}; "
                       "evidence needs the claim or the claim of its epic (or --on-behalf-of the holder)", 3)
    kind = vcs or code_vcs.detect(config.root)
    cited = changes or [code_vcs.current_change(config.root, kind)]
    for change in cited:
        if not code_vcs.valid_change(kind, change):
            raise QewError(f"`{change}` is not a {kind} change id", 2)
    recorded = {c for e in snapshot.events[item_id] if e.type == "evidence" and e.get("vcs") == kind
                for c in e.get("change_ids", [])}
    if set(cited) <= recorded:  # a retry of evidence that already landed
        return f"{item_id} evidence {kind} {' '.join(cited)} (unchanged)"
    named = {} if on_behalf_of is None else {"on_behalf_of": on_behalf_of}
    ledger.append_event(
        item_id, "evidence", vcs=kind, change_ids=cited, repo=code_vcs.repository_url(config.root), **named
    )
    return f"{item_id} evidence {kind} {' '.join(cited)}" + ("" if on_behalf_of is None else f" (for {on_behalf_of})")


def _require_holder(snapshot: Snapshot, item_id: str, holder: str) -> None:
    """`--on-behalf-of` is for a claim somebody else really holds: the named actor holds the item (or its epic); never oneself."""
    if holder == actor():
        raise QewError(f"--on-behalf-of {holder} is you: record the evidence plainly", 2)
    folded = snapshot.folds[item_id]
    if not ((folded.state == State.CLAIMED and folded.owner == holder) or holds_ancestor(snapshot, item_id, holder)):
        held = f"claimed by {holding(snapshot, item_id)}" if folded.state == State.CLAIMED else "not claimed by anyone"
        raise QewError(f"cannot record evidence for {item_id} on behalf of {holder}: {holder} does not hold it ({held})", 3)


def _holder_check(snapshot: Snapshot, item_id: str, take_over: bool) -> str | None:  # "completed" when already done
    """Complete only work the caller holds; returns the previous holder when it is a take-over."""
    folded = snapshot.folds[item_id]
    if folded.state == State.COMPLETED:
        return "completed"
    if folded.state == State.APPROVED:
        if take_over:
            raise QewError(f"nothing to take over: {item_id} is unclaimed", 2)
        raise QewError(f"cannot complete {item_id}: it is unclaimed; claim it first with `qew start {item_id}`", 2)
    if folded.state != State.CLAIMED:
        raise _refuse(snapshot, item_id, "complete", "a claimed item")
    if folded.owner == actor() or holds_ancestor(snapshot, item_id, actor()):
        if take_over:
            raise QewError(f"nothing to take over: you already hold {item_id}", 2)
        return None
    if not take_over:
        raise QewError(
            f"cannot complete {item_id}: it is claimed by {holding(snapshot, item_id)}; "
            f"to finish it anyway: qew complete {item_id} --take-over --reason TEXT", 3)
    return folded.owner


def _absence(ledger: Ledger, absent: str | None, reason: str | None, no_commit: str | None) -> tuple[str, str]:
    """(kind, reason) of the typed absence of evidence: --no-commit WHY is `no-code`, --absent KIND[:ITEM] needs --reason."""
    if no_commit is not None and absent is not None:
        raise QewError("--no-commit WHY is the absence no-code: give it or --absent KIND, not both", 2)
    if no_commit is not None:
        return "no-code", no_commit
    assert absent is not None
    why = (reason or "").strip()
    if not why:
        raise QewError("--absent needs --reason TEXT: why there is no code to cite", 2)
    kind = absent.strip()
    problem = absence_problem(f"{kind}: {why}")
    if problem:
        raise QewError(f"--absent {kind}: {problem}", 2)
    if kind.startswith("superseded-by:"):
        other = kind[len("superseded-by:"):]
        if other not in ledger.item_ids():
            raise QewError(f"--absent {kind}: no item {other} in this ledger", 2)
    return kind, why


def _refuse_self_citation(config: Config, ledger: Ledger, item_id: str, cited: list[str]) -> None:
    """When the ledger lives inside the code repository, the change being made now cannot be its own proof."""
    try:
        inside = ledger.path.resolve().is_relative_to(config.root.resolve())
    except OSError:
        return
    if not inside:
        return
    here = code_vcs.current_change(config.root, code_vcs.detect(config.root))
    if here in cited:
        raise QewError(f"cannot complete {item_id}: evidence {here} is the change that records this completion; cite the work itself", 2)


def complete(config: Config, reference: str, reason: str | None = None, take_over: bool = False,
             no_commit: str | None = None, landed_on: str | None = None, absent: str | None = None) -> dict[str, object]:
    reason = _text("reason", reason)
    no_commit = _text("no-commit", no_commit)
    branch = _branch(landed_on if landed_on is not None else config.target)
    if take_over and reason is None:
        raise QewError("--take-over needs --reason TEXT: say why the claim is finished by you", 2)
    ledger, snapshot, item_id = _load(config, reference)
    item = snapshot.items[item_id]
    if snapshot.folds[item_id].state == State.DRAFT and item.kind == Kind.EPIC:
        raise QewError(f"cannot complete {item_id}: it is a draft plan; release it with qew draft approve {item_id} --tree --quote Q first", 2)
    previous = _holder_check(snapshot, item_id, take_over)
    if previous == "completed":  # a repeat is a quiet no-op: no second event
        return {"line": f"{item_id} completed (unchanged)", "id": item_id, "state": "unchanged"}
    unmet = ledger_graph.blockers(snapshot, item_id)
    if unmet:
        raise QewError(f"cannot complete {item_id}: it needs {' '.join(unmet)} completed first", 2)
    if ledger_graph.GATED_LABEL in item.labels:
        raise QewError(ledger_graph.gated_refusal(item_id), 2)
    kids = ledger_graph.children(snapshot).get(item_id, [])
    open_kids = [k for k in kids if snapshot.folds[k].state not in (State.COMPLETED, State.CLOSED)]
    if open_kids:
        count = f"{len(open_kids)} open child{'ren' if len(open_kids) != 1 else ''}"
        raise QewError(f"cannot complete {item_id}: {count}, unfinished children {' '.join(open_kids)}", 2)
    data: JsonObject = {}
    commits: list[str] = []
    if item.kind == Kind.EPIC:
        if no_commit is not None:
            raise QewError(f"{item_id} is an epic: it needs children, not code, so --no-commit does not apply", 2)
        if not kids:
            raise QewError(f"cannot complete {item_id}: an epic with no children has no completed work; close it", 2)
        if absent is not None:
            raise QewError(f"{item_id} is an epic: its evidence is its children, so --absent does not apply", 2)
        data["absent"], data["absent_reason"] = "children", f"all {len(kids)} children completed"
    elif no_commit is not None or absent is not None:
        if snapshot.folds[item_id].evidence:
            raise QewError(f"{item_id} has evidence recorded: complete it on that proof, or drop --no-commit and --absent", 2)
        data["absent"], data["absent_reason"] = _absence(ledger, absent, reason, no_commit)
    else:
        cited, commits = _landed_commits(config, snapshot, item_id, branch)
        _refuse_self_citation(config, ledger, item_id, cited)
        data["evidence"], data["commits"], data["branch"] = cited, commits, branch
    prs = _merged_pull_requests(ledger, item_id)
    if prs:
        data["prs"] = prs
    if reason is not None:
        data["reason"] = reason
    if previous is not None:
        data["taken_over_from"] = previous
    provisional = claim_reconcile.provisional_claim_of(snapshot, item_id, actor())
    if provisional is not None:
        data["provisional_claim"] = provisional  # sync settles the claim; the completion stands whatever the arbiter says
    ledger.append_event(item_id, "completed", **data)
    released = sorted(set(ledger_graph.ready(ledger.snapshot())) - set(ledger_graph.ready(snapshot)))
    line = f"{item_id} completed" + (f"; ready: {' '.join(released[:RELEASED_SHOWN])}" if released else "")
    result: dict[str, object] = {"line": line, "id": item_id, "state": "completed", "released": released}
    result.update(_finish_parent(ledger, item_id))
    return result


def _landed_commits(config: Config, snapshot: Snapshot, item_id: str, target: str) -> tuple[list[str], list[str]]:
    """(the change ids as cited, their full commits) of the first evidence event whose changes all landed on `target`,
    else a precise refusal."""
    events = snapshot.folds[item_id].evidence
    if not events:
        raise QewError(f"cannot complete {item_id}: no evidence recorded (qew evidence {item_id} --change ID)", 2)
    checkout = code_vcs.detect(config.root)
    unknown: list[str] = []
    unlanded: list[str] = []
    for event in events:
        kind = event.get("vcs")
        if kind != checkout:
            raise QewError(f"evidence uses {kind} but this checkout is {checkout}", 3)
        full: list[str] = []
        for change in event.get("change_ids"):
            if not code_vcs.valid_change(kind, change):
                raise QewError(f"cannot complete {item_id}: evidence `{change}` is not a valid {kind} change id", 2)
            where, commit = code_vcs.landing(config.root, kind, change, target)
            if where == Landing.LANDED:
                full.append(commit)
            elif where == Landing.UNKNOWN:
                unknown.append(change)
            else:
                unlanded.append(change)
        if len(full) == len(event.get("change_ids")):
            return list(event.get("change_ids")), full
    problems = []
    if unknown:
        problems.append(f"evidence {' '.join(dict.fromkeys(unknown))} is unknown to this checkout (typo, or not fetched here yet)")
    if unlanded:
        problems.append(f"evidence {' '.join(dict.fromkeys(unlanded))} is not an ancestor of {target}")
    raise QewError(f"cannot complete {item_id}: {'; '.join(problems)}", 2)


def _merged_pull_requests(ledger: Ledger, item_id: str) -> list[int]:
    """Numbers of the item's pull requests recorded as merged; unreadable records are indeterminate (exit 4)."""
    try:
        records = ledger.load_mirrors(item_id)
    except QewError as error:
        raise QewError(f"indeterminate: the pull request records of {item_id} cannot be read ({error}); nothing was written", 4) from None
    numbers = []
    for record in records:
        if record.get("kind") != "pr" or record.get("state") != "merged":
            continue
        number = record.get("number")
        if type(number) is not int or number < 1:
            raise QewError(f"indeterminate: {record['_target']} has no valid pull request number; nothing was written", 4)
        numbers.append(number)
    return sorted(set(numbers))


def _finish_parent(ledger: Ledger, child_id: str) -> dict[str, object]:
    """Complete the epic above `child_id` when its last open child just finished, then the epic above that, and so on.

    The child's own event is already written; a failure here is only a warning naming the explicit command.
    """
    finished: list[str] = []
    current = child_id
    try:
        while True:
            snapshot = ledger.snapshot()
            parent_id = snapshot.items[current].parent
            parent = snapshot.items.get(parent_id)
            if parent is None or parent.kind != Kind.EPIC or snapshot.folds[parent_id].state != State.APPROVED:  # an epic someone holds is finished by its holder
                break
            kids = ledger_graph.children(snapshot)[parent_id]
            states = [snapshot.folds[k].state for k in kids]
            if any(s not in (State.COMPLETED, State.CLOSED) for s in states) or State.COMPLETED not in states:
                break
            ledger.append_event(parent_id, "completed", children_finished=len(kids), reason=f"all {len(kids)} children finished")
            finished.append(parent_id)
            current = parent_id
    except QewError as error:
        return {"warning": f"{current} is done, but its epic was not completed automatically ({error}); run `qew complete {snapshot.items[current].parent}`",
                **({"epic_completed": finished[0]} if finished else {})}
    return {"epic_completed": finished[0], **({"epics_completed": finished} if len(finished) > 1 else {})} if finished else {}


def close(config: Config, reference: str, reason: str | None = None, of: str | None = None,
          by: str | None = None, note: str | None = None, delivered_by: str | None = None,
          landed_on: str | None = None) -> dict[str, object]:
    note = _text("note", note)
    if delivered_by is not None or (landed_on is not None and reason is None):
        return _close_delivered(config, reference, reason, of, by, note, delivered_by, landed_on)
    if landed_on is not None:
        raise QewError("--landed-on belongs to --delivered-by", 2)
    if reason not in CLOSE_REASONS:
        raise QewError(f"close needs --reason {'|'.join(CLOSE_REASONS)} ({_FINISHED_WORK})", 2)
    ledger, snapshot, item_id = _load(config, reference)
    link = _disposition(ledger, snapshot, item_id, reason, of, by)
    if snapshot.folds[item_id].state == State.CLOSED:
        last = [e for e in snapshot.events[item_id] if e.type == "closed"][-1:]
        if last and last[0].get("reason") == reason and all(last[0].get(k) == v for k, v in link.items()):
            return {"line": f"{item_id} closed ({reason}) (unchanged)", "id": item_id, "state": "closed"}  # a retry
        raise QewError(f"{item_id} is already closed", 2)
    data: JsonObject = {"reason": reason, **link}
    if note is not None:
        data["note"] = note
    ledger.append_event(item_id, "closed", **data)
    result: dict[str, object] = {"line": f"{item_id} closed ({reason})", "id": item_id, "state": "closed"}
    result.update(_finish_parent(ledger, item_id))
    return result


def _disposition(ledger: Ledger, snapshot: Snapshot, item_id: str, reason: str, of: str | None, by: str | None) -> dict[str, str]:
    """The linked item a duplicate or superseded close names; any other pairing is refused."""
    wanted = {"duplicate": ("of", of, by), "superseded": ("by", by, of)}.get(reason)
    field = {"of": "duplicate_of", "by": "superseded_by"}
    if wanted is None:
        if of is not None or by is not None:
            raise QewError(f"--reason {reason} names no other item: drop --of and --by", 2)
        return {}
    key, value, stray = wanted
    if stray is not None:
        raise QewError(f"--reason {reason} takes --{key}, not --{'by' if key == 'of' else 'of'}", 2)
    if value is None:
        raise QewError(f"--reason {reason} needs --{key} ITEM naming the {'original' if key == 'of' else 'replacement'}", 2)
    other = ledger.resolve(value, list(snapshot.items))
    if other == item_id:
        raise QewError(f"{item_id} cannot be {'a duplicate of' if key == 'of' else 'superseded by'} itself", 2)
    return {field[key]: other}


def _close_delivered(config: Config, reference: str, reason: str | None, of: str | None, by: str | None,
                     note: str | None, delivered_by: str | None, landed_on: str | None) -> dict[str, object]:
    if delivered_by is None or landed_on is None:
        raise QewError("--delivered-by SHA and --landed-on BRANCH go together: half a proof proves nothing", 2)
    if reason is not None or of is not None or by is not None or note is not None:
        raise QewError("--delivered-by completes the item; it takes no --reason, --of, --by or --note", 2)
    branch = _branch(landed_on)
    kind = code_vcs.detect(config.root)
    if not code_vcs.valid_change(kind, delivered_by):
        raise QewError(f"`{delivered_by}` is not a {kind} change id", 2)
    ledger, snapshot, item_id = _load(config, reference)
    folded = snapshot.folds[item_id]
    if folded.state == State.CLAIMED and folded.owner != actor():
        raise QewError(f"{item_id} is claimed by {holding(snapshot, item_id)}; finish it with `qew complete {item_id} --take-over --reason TEXT`", 3)
    if folded.state not in (State.APPROVED, State.CLAIMED):
        raise _refuse(snapshot, item_id, "close as delivered", "an approved or claimed item")
    where, commit = code_vcs.landing(config.root, kind, delivered_by, branch)
    if where != Landing.LANDED:
        raise QewError(f"cannot close {item_id}: {delivered_by} is {where.value} {branch}" if where == Landing.NOT_ANCESTOR
                       else f"cannot close {item_id}: {delivered_by} is {where.value}", 2)
    ledger.append_event(item_id, "evidence", vcs=kind, change_ids=[commit], repo=code_vcs.repository_url(config.root))
    ledger.append_event(item_id, "completed", delivered_by=[commit], commits=[commit], branch=branch)
    result: dict[str, object] = {"line": f"{item_id} completed (delivered by {commit[:12]} on {branch})", "id": item_id, "state": "completed"}
    result.update(_finish_parent(ledger, item_id))
    return result


def reopen_manifest(config: Config, manifest: Path, dry_run: bool) -> str:
    """Reopen the manifest's finished items in one write; checked whole first, a rerun is unchanged."""
    from qew import manifest_rows, reopen_batch

    ledger = ledger_for(config)
    snapshot = ledger.snapshot()
    batch = reopen_batch.plan(snapshot, manifest_rows.read(ledger, manifest, list(snapshot.items), reopen_batch.ROW_KEYS))
    if not dry_run:
        reopen_batch.apply(ledger, batch)
    return reopen_batch.summary(batch, "would reopen" if dry_run else "reopened")


def reopen(config: Config, reference: str, reason: str | None = None) -> dict[str, object]:
    reason = _text("reason", reason)
    ledger, snapshot, item_id = _load(config, reference)
    state = snapshot.folds[item_id].state
    if state in (State.DRAFT, State.APPROVED, State.CLAIMED):
        return {"line": f"unchanged {item_id} (already open)", "id": item_id, "state": "unchanged"}
    if reason is None:
        raise QewError(f"reopen needs --reason TEXT: say why {item_id} is required again", 2)
    ledger.append_event(item_id, "reopened", reason=reason)
    return {"line": f"{item_id} reopened", "id": item_id, "state": "reopened"}
