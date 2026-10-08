"""Mirror approved epics as forge milestones (opt-in per target, `milestones = true`).

An epic becomes a milestone titled like it, described by the approving human's quote, due on its `due` header;
its approved child issues are assigned to it. Every step is idempotent and read back. An issue already in a
different milestone is never moved; a milestone closes only when its epic is completed and no open issue sits in it.
"""

from dataclasses import replace
from typing import Dict, List

from qew import sync_plan
from qew.forge import ForgeError, Milestone, MilestoneSpec, RemoteIssue, Unsupported
from qew.ledger_event import State
from qew.ledger_item import Kind


def enabled(options) -> bool:
    return options.get("milestones") is True


def description(snapshot, epic_id: str) -> str:
    approvals = [e for e in snapshot.events[epic_id] if e.type == "approved"]
    quote = approvals[-1].get("quote") if approvals else ""
    return f"Approved: {quote}" if quote else ""


def sync_milestones(ledger, snapshot, target, report, ids: List[str], numbers: Dict[str, int]) -> None:
    forge = target.forge
    try:
        issues = {issue.number: issue for issue in forge.list_issues()}
    except Unsupported:
        report.skipped.append("milestones")
        return
    for epic_id in ids:
        epic = snapshot.items[epic_id]
        state = snapshot.folds[epic_id].state
        if epic.kind != Kind.EPIC or state not in (State.APPROVED, State.CLAIMED, State.COMPLETED):
            continue
        try:
            _one(ledger, snapshot, target, report, epic_id, numbers, issues)
        except Unsupported:
            report.skipped.append("milestones")
            return
        except ForgeError as error:
            report.failures.append(f"{snapshot.short(epic_id)}: milestone: {error.detail}")


def _record(ledger, target, epic_id):
    return next((m for m in ledger.load_mirrors(epic_id) if m["_target"] == target.slug), None)


def _one(ledger, snapshot, target, report, epic_id: str, numbers, issues: Dict[int, RemoteIssue]) -> None:
    forge = target.forge
    epic = snapshot.items[epic_id]
    state = snapshot.folds[epic_id].state
    spec = MilestoneSpec(epic.title, description(snapshot, epic_id), epic.due)
    found = forge.find_milestone(spec.title)
    if found is None:
        if state == State.COMPLETED:
            return  # nothing was ever mirrored, so there is nothing to close
        found = forge.create_milestone(spec)
        report.milestones_created += 1
    elif (found.description, found.due) != (spec.description, spec.due):
        found = forge.update_milestone(found.number, spec)
        report.milestones_updated += 1
    else:
        report.milestones_unchanged += 1
    checked = forge.read_milestone(found.number)
    if (checked.description, checked.due) != (spec.description, spec.due):
        report.failures.append(f"{snapshot.short(epic_id)}: milestone {found.number} does not show what was written")
        return
    _assign(snapshot, target, report, epic_id, found, numbers, issues)
    _state(ledger, target, report, epic_id, state, checked, issues)


def _assign(snapshot, target, report, epic_id: str, milestone: Milestone, numbers, issues) -> None:
    for item in snapshot.items.values():
        if item.parent != epic_id or snapshot.folds[item.id].state not in (State.APPROVED, State.CLAIMED, State.COMPLETED):
            continue
        number = numbers.get(item.id)
        issue = issues.get(number) if number else None
        if issue is None or issue.milestone == milestone.number:
            continue
        if issue.milestone:
            report.failures.append(
                f"{snapshot.short(item.id)}: is in milestone {issue.milestone}, not moved to {milestone.number} ({milestone.title})")
            continue
        placed = target.forge.assign_milestone(number, milestone.number)
        if placed.milestone != milestone.number:
            report.failures.append(f"{snapshot.short(item.id)}: milestone {milestone.number} was not kept")
            continue
        issues[number] = replace(issue, milestone=milestone.number)


def _state(ledger, target, report, epic_id: str, state, milestone: Milestone, issues) -> None:
    forge = target.forge
    record = _record(ledger, target, epic_id)
    if state == State.COMPLETED:
        attached = [i for i in issues.values() if i.milestone == milestone.number and i.open]
        if milestone.open and not attached:
            closed = forge.set_milestone_open(milestone.number, False)
            if closed.open or forge.read_milestone(milestone.number).open:
                report.failures.append(f"milestone {milestone.number} did not close")
                return
            _remember(ledger, target, epic_id, record, closed=True)
        elif milestone.open:
            report.warnings.append(f"milestone {milestone.number} stays open: {len(attached)} open issue(s) are still in it")
    elif not milestone.open and record is not None and record.get("milestone_closed"):
        forge.set_milestone_open(milestone.number, True)  # the epic was reopened
        _remember(ledger, target, epic_id, record, closed=False)


def _remember(ledger, target, epic_id: str, record, closed: bool) -> None:
    if record is None:
        return
    clean = {k: v for k, v in record.items() if k != "_target"}
    ledger.write_mirror(epic_id, target.slug, {**clean, "milestone_closed": closed})
