"""Finish work: `evidence`, `complete`, `close`, `reopen`.

`complete` is the only way to reach the completed state for claimed work: the caller holds the claim, every
child is finished, and (except for epics) the delivering code is an ancestor of the target branch. Work that
shipped without a claim is closed with `close --delivered-by`, under the same landing proof.
"""

import sys
from typing import Dict, List, Optional

from qew import code_vcs, ledger_graph, outcome
from qew.cmd_claim import holding
from qew.cmd_items import ledger_for
from qew.code_vcs import Landing
from qew.errors import QewError
from qew.ledger_config import Config
from qew.ledger_event import CLOSE_REASONS, State
from qew.ledger_item import Kind
from qew.ledger_store import Ledger, Snapshot, actor

_FINISHED_WORK = "finished work: qew complete ITEM, or qew close ITEM --delivered-by SHA --landed-on BRANCH"


def register(commands) -> None:
    evidence = commands.add_parser("evidence", help="record the code changes that deliver an item")
    evidence.add_argument("item", help="item id or alias")
    evidence.add_argument("--change", action="append", default=[], help="change id (jj) or commit (git), repeatable; default is the current one")
    evidence.add_argument("--vcs", choices=["jj", "git"], help="override the detected VCS")
    complete = commands.add_parser("complete", help="finish claimed work once the gate passes")
    complete.add_argument("item", help="item id or alias")
    complete.add_argument("--reason", help="why the work is done (recorded on the completed event)")
    complete.add_argument("--take-over", action="store_true", help="finish work another actor holds (needs --reason)")
    complete.add_argument("--no-commit", metavar="WHY", help="complete without code, for work delivered outside the repository; WHY is recorded")
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
    reopen.add_argument("item", help="item id or alias")
    reopen.add_argument("--reason", help="why the work is required again (needed unless the item is already open)")
    reopen.add_argument("--json", action="store_true", help="machine-readable output")


def run(arguments) -> bool:
    if arguments.command not in ("evidence", "complete", "close", "reopen"):
        return False
    from qew import ledger_config

    config = ledger_config.load()
    if arguments.command == "evidence":
        print(evidence(config, arguments.item, arguments.change, arguments.vcs))
        return True
    if arguments.command == "complete":
        result = complete(config, arguments.item, arguments.reason, arguments.take_over, arguments.no_commit, arguments.landed_on)
    elif arguments.command == "close":
        result = close(config, arguments.item, arguments.reason, arguments.of, arguments.by, arguments.note,
                       arguments.delivered_by, arguments.landed_on)
    else:
        result = reopen(config, arguments.item, arguments.reason)
    warning = result.pop("warning", None)
    print(outcome.render(arguments, result.pop("line"), **result))
    if warning:
        print(f"warning: {warning}", file=sys.stderr)
    return True


_STATE_WHY = {
    State.DRAFT: "it is a draft; only a human's quoted words release it (qew draft approve)",
    State.APPROVED: "it is approved and unclaimed",
    State.COMPLETED: "it is completed",
    State.CLOSED: "it is closed (qew reopen)",
}


def _load(config: Config, reference: str):
    ledger = ledger_for(config)
    snapshot = ledger.snapshot()
    return ledger, snapshot, ledger.resolve(reference, list(snapshot.items))


def _refuse(snapshot: Snapshot, item_id: str, verb: str, needs: str) -> QewError:
    state = snapshot.folds[item_id].state
    return QewError(f"cannot {verb} {item_id}: {_STATE_WHY.get(state, 'its state is unknown')} (needs {needs})", 2)


def _text(name: str, value: Optional[str]) -> Optional[str]:
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


def evidence(config: Config, reference: str, changes: List[str], vcs: Optional[str]) -> str:
    ledger, snapshot, item_id = _load(config, reference)
    if snapshot.folds[item_id].state not in (State.APPROVED, State.CLAIMED):
        raise _refuse(snapshot, item_id, "record evidence for", "an approved or claimed item")
    kind = vcs or code_vcs.detect(config.root)
    cited = changes or [code_vcs.current_change(config.root, kind)]
    for change in cited:
        if not code_vcs.valid_change(kind, change):
            raise QewError(f"`{change}` is not a {kind} change id", 2)
    ledger.append_event(
        item_id, "evidence", vcs=kind, change_ids=cited, repo=code_vcs.repository_url(config.root)
    )
    return f"{item_id} evidence {kind} {' '.join(cited)}"


def _holder_check(snapshot: Snapshot, item_id: str, take_over: bool) -> Optional[str]:
    """Complete only work the caller holds; returns the previous holder when it is a take-over."""
    folded = snapshot.folds[item_id]
    if folded.state == State.COMPLETED:
        raise QewError(f"cannot complete {item_id}: it is already completed", 2)
    if folded.state == State.APPROVED:
        if take_over:
            raise QewError(f"nothing to take over: {item_id} is unclaimed", 2)
        raise QewError(f"cannot complete {item_id}: it is unclaimed; claim it first with `qew start {item_id}`", 2)
    if folded.state != State.CLAIMED:
        raise _refuse(snapshot, item_id, "complete", "a claimed item")
    if folded.owner == actor():
        if take_over:
            raise QewError(f"nothing to take over: you already hold {item_id}", 2)
        return None
    if not take_over:
        raise QewError(
            f"cannot complete {item_id}: it is claimed by {holding(snapshot, item_id)}; "
            f"to finish it anyway: qew complete {item_id} --take-over --reason TEXT", 3)
    return folded.owner


def complete(config: Config, reference: str, reason: Optional[str] = None, take_over: bool = False,
             no_commit: Optional[str] = None, landed_on: Optional[str] = None) -> Dict[str, object]:
    reason = _text("reason", reason)
    no_commit = _text("no-commit", no_commit)
    branch = _branch(landed_on if landed_on is not None else config.target)
    if take_over and reason is None:
        raise QewError("--take-over needs --reason TEXT: say why the claim is finished by you", 2)
    ledger, snapshot, item_id = _load(config, reference)
    item = snapshot.items[item_id]
    previous = _holder_check(snapshot, item_id, take_over)
    unmet = ledger_graph.blockers(snapshot, item_id)
    if unmet:
        raise QewError(f"cannot complete {item_id}: it needs {' '.join(unmet)} completed first", 2)
    kids = ledger_graph.children(snapshot).get(item_id, [])
    open_kids = [k for k in kids if snapshot.folds[k].state not in (State.COMPLETED, State.CLOSED)]
    if open_kids:
        raise QewError(f"cannot complete {item_id}: unfinished children {' '.join(open_kids)}", 2)
    data: Dict[str, object] = {}
    commits: List[str] = []
    if item.kind == Kind.EPIC:
        if no_commit is not None:
            raise QewError(f"{item_id} is an epic: it needs children, not code, so --no-commit does not apply", 2)
        if not kids:
            raise QewError(f"cannot complete {item_id}: an epic with no children has nothing to prove; close it", 2)
    elif no_commit is not None:
        if snapshot.folds[item_id].evidence:
            raise QewError(f"{item_id} has evidence recorded: complete it on that proof, or drop --no-commit", 2)
        data["no_commit"] = no_commit
    else:
        commits = _landed_commits(config, snapshot, item_id, branch)
        data["commits"], data["branch"] = commits, branch
    prs = _merged_pull_requests(ledger, item_id)
    if prs:
        data["prs"] = prs
    if reason is not None:
        data["reason"] = reason
    if previous is not None:
        data["taken_over_from"] = previous
    ledger.append_event(item_id, "completed", **data)
    result: Dict[str, object] = {"line": f"{item_id} completed", "id": item_id, "state": "completed"}
    result.update(_finish_parent(ledger, item_id))
    return result


def _landed_commits(config: Config, snapshot: Snapshot, item_id: str, target: str) -> List[str]:
    """Full ids of the first evidence event whose changes all landed on `target`, else a precise refusal."""
    events = snapshot.folds[item_id].evidence
    if not events:
        raise QewError(f"cannot complete {item_id}: no evidence recorded (qew evidence {item_id} --change ID)", 2)
    checkout = code_vcs.detect(config.root)
    unknown: List[str] = []
    unlanded: List[str] = []
    for event in events:
        kind = event.get("vcs")
        if kind != checkout:
            raise QewError(f"evidence uses {kind} but this checkout is {checkout}", 3)
        full: List[str] = []
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
            return full
    problems = []
    if unknown:
        problems.append(f"evidence {' '.join(dict.fromkeys(unknown))} is unknown to this checkout (typo, or not fetched here yet)")
    if unlanded:
        problems.append(f"evidence {' '.join(dict.fromkeys(unlanded))} is not an ancestor of {target}")
    raise QewError(f"cannot complete {item_id}: {'; '.join(problems)}", 2)


def _merged_pull_requests(ledger: Ledger, item_id: str) -> List[int]:
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


def _finish_parent(ledger: Ledger, child_id: str) -> Dict[str, object]:
    """Complete the epic above `child_id` when its last open child just finished.

    The child's own event is already written; a failure here is only a warning naming the explicit command.
    """
    try:
        snapshot = ledger.snapshot()
        parent_id = snapshot.items[child_id].parent
        parent = snapshot.items.get(parent_id)
        if parent is None or parent.kind != Kind.EPIC or snapshot.folds[parent_id].state not in (State.APPROVED, State.CLAIMED):
            return {}
        kids = ledger_graph.children(snapshot)[parent_id]
        states = [snapshot.folds[k].state for k in kids]
        if any(s not in (State.COMPLETED, State.CLOSED) for s in states) or State.COMPLETED not in states:
            return {}
        ledger.append_event(parent_id, "completed", children_finished=len(kids), reason=f"all {len(kids)} children finished")
        return {"epic_completed": parent_id}
    except QewError as error:
        return {"warning": f"{child_id} is done, but its epic was not completed automatically ({error}); run `qew complete {snapshot.items[child_id].parent}`"}


def close(config: Config, reference: str, reason: Optional[str] = None, of: Optional[str] = None,
          by: Optional[str] = None, note: Optional[str] = None, delivered_by: Optional[str] = None,
          landed_on: Optional[str] = None) -> Dict[str, object]:
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
        raise QewError(f"{item_id} is already closed", 2)
    data: Dict[str, object] = {"reason": reason, **link}
    if note is not None:
        data["note"] = note
    ledger.append_event(item_id, "closed", **data)
    result: Dict[str, object] = {"line": f"{item_id} closed ({reason})", "id": item_id, "state": "closed"}
    result.update(_finish_parent(ledger, item_id))
    return result


def _disposition(ledger: Ledger, snapshot: Snapshot, item_id: str, reason: str, of: Optional[str], by: Optional[str]) -> Dict[str, str]:
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


def _close_delivered(config: Config, reference: str, reason: Optional[str], of: Optional[str], by: Optional[str],
                     note: Optional[str], delivered_by: Optional[str], landed_on: Optional[str]) -> Dict[str, object]:
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
    result: Dict[str, object] = {"line": f"{item_id} completed (delivered by {commit[:12]} on {branch})", "id": item_id, "state": "completed"}
    result.update(_finish_parent(ledger, item_id))
    return result


def reopen(config: Config, reference: str, reason: Optional[str] = None) -> Dict[str, object]:
    reason = _text("reason", reason)
    ledger, snapshot, item_id = _load(config, reference)
    state = snapshot.folds[item_id].state
    if state in (State.DRAFT, State.APPROVED, State.CLAIMED):
        return {"line": f"unchanged {item_id} (already open)", "id": item_id, "state": "unchanged"}
    if reason is None:
        raise QewError(f"reopen needs --reason TEXT: say why {item_id} is required again", 2)
    ledger.append_event(item_id, "reopened", reason=reason)
    return {"line": f"{item_id} reopened", "id": item_id, "state": "reopened"}
