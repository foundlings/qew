"""Landing a merged pull request's link declaration on a schema 9 ledger.

One path serves the merge (`qew pr merge` lands as it merges), the repair (`qew land N`) and a merge seen late. The merge is already
attested by the forge, so nothing here refuses: an item that cannot be completed is recorded as `land_pending` with the reason, and
the next `qew land` completes it. Evidence is recorded per item from that item's own pull request, only for changes that are
ancestors of the target branch, never for a union's whole range.
"""

from dataclasses import dataclass, field
from typing import Literal

from qew import cmd_claim, cmd_finish, code_vcs, pr_links
from qew.cmd_items import ledger_for
from qew.errors import QewError
from qew.ledger_config import Config
from qew.ledger_event import State
from qew.ledger_item import Kind
from qew.ledger_store import Snapshot, actor
from qew.pr_gate import evidence_covers
from qew.pr_record import CLOSED_STATES, PrRecord

PendingKind = Literal["held", "open", "failed"]


@dataclass(frozen=True)
class Member:
    """One merged pull request: its record, its head and what the ledger says it does."""

    record: PrRecord
    head_sha: str
    declaration: pr_links.Declaration


@dataclass
class Pending:
    item: str
    pr: int
    why: str
    kind: PendingKind
    holder: str = ""


@dataclass
class Report:
    lines: list[str] = field(default_factory=list)
    completed: list[tuple[int, str, str]] = field(default_factory=list)  # (pull request, item, evidence shown)
    clean: list[str] = field(default_factory=list)  # items already finished
    pending: list[Pending] = field(default_factory=list)
    notes: list[tuple[int, str, str]] = field(default_factory=list)  # (pull request, item, role) of the remains/related notes written


def _depth(snapshot: Snapshot, item_id: str) -> int:
    depth, seen, cursor = 0, {item_id}, snapshot.items[item_id].parent
    while cursor and cursor in snapshot.items and cursor not in seen:
        seen.add(cursor)
        depth, cursor = depth + 1, snapshot.items[cursor].parent
    return depth


def _own(config: Config, member: Member, merge_sha: str) -> list[str]:
    """The pull request's own changes that are ancestors of the target branch (empty: none can be proven on it yet)."""
    vcs = code_vcs.detect(config.root)
    changes = code_vcs.own_changes(config.root, vcs, merge_sha, member.head_sha) if merge_sha else []
    changes = changes or [member.head_sha]
    try:
        stands = code_vcs.landing_many(config.root, vcs, changes, config.target)
    except QewError:
        return []
    return [change for change in changes if stands[change][0] == code_vcs.Landing.LANDED]


def _note(snapshot: Snapshot, member: Member, item_id: str, role: str, merge_sha: str) -> bool:
    marker = f"pull request #{member.record.number} merged"
    if any(e.type == "commented" and marker in str(e.get("text", "")) for e in snapshot.events.get(item_id, [])):
        return False
    meaning = "partial delivery; this item stays open" if role == "remains" else "related"
    snapshot.ledger.append_event(item_id, "commented", text=f"{marker} ({merge_sha[:12]}): {meaning}", author=actor())
    return True


def land(config: Config, members: list[Member], merge_sha: str, evidence_by_pr: dict[int, list[str]] | None = None,
         integrator: str | None = None) -> Report:
    """Complete every `closes` item of `members` that can be completed now, note the rest, record what is pending.

    `evidence_by_pr` is each pull request's own provable changes when the caller computed them before taking the write lock; the
    default computes them here. `integrator` (a reason) lands items another actor claimed, on the take-over path."""
    report = Report()
    ledger = ledger_for(config)
    own = evidence_by_pr if evidence_by_pr is not None else {m.record.number: _own(config, m, merge_sha) for m in members}
    first = ledger.snapshot()
    work = [(m, item_id) for m in members for item_id in m.declaration.items("closes") if item_id in first.items]
    work.sort(key=lambda pair: -_depth(first, pair[1]))  # children before the epics they finish
    for member, item_id in work:
        _land_one(config, member, item_id, own[member.record.number], integrator, report)
    snapshot = ledger.snapshot()
    for member in members:
        for role in pr_links.ROLES[1:]:
            for item_id in member.declaration.items(role):
                if item_id in snapshot.items and _note(snapshot, member, item_id, role, merge_sha):
                    report.notes.append((member.record.number, item_id, role))
    for member in members:
        pr_links.merged_seen(ledger, member.record.mirror_file, merge_sha, member.head_sha, "qew")
    for done in report.completed:
        pr_links.record_landed(ledger, _key(members, done[0]), done[1], [done[2]])
    for item_id in report.clean:
        owner = next(m for m in members if item_id in m.declaration.items("closes"))
        pr_links.record_landed(ledger, owner.record.mirror_file, item_id, [])
    for held in report.pending:
        pr_links.record_pending(ledger, _key(members, held.pr), held.item, held.why)
    _lines(members, report)
    return report


def _key(members: list[Member], number: int) -> str:
    return next(m.record.mirror_file for m in members if m.record.number == number)


def _land_one(config: Config, member: Member, item_id: str, changes: list[str], integrator: str | None, report: Report) -> None:
    number = member.record.number
    snapshot = ledger_for(config).snapshot()
    folded = snapshot.folds[item_id]
    if folded.state in CLOSED_STATES:
        report.clean.append(item_id)
        return
    fix = f"qew land {number}"
    if folded.state != State.CLAIMED:
        report.pending.append(Pending(item_id, number, f"is {folded.state.value if folded.state else 'unreadable'}, not claimed: `qew start {item_id}`, then `{fix}`", "open"))
        return
    mine = folded.owner == actor() or cmd_claim.holds_ancestor(snapshot, item_id, actor())
    if not mine and integrator is None:
        report.pending.append(Pending(item_id, number, f"is claimed by {folded.owner}: `{fix} --integrator --reason TEXT` if you merged it, or `qew complete {item_id} --take-over --reason TEXT`", "held", folded.owner or ""))
        return
    takeover = f"integrator landed pr #{number} (held by {folded.owner}): {integrator}" if not mine and integrator is not None else None
    if snapshot.items[item_id].kind != Kind.EPIC and not evidence_covers(config, folded.evidence, member.head_sha):
        if not changes:
            report.pending.append(Pending(item_id, number, f"has no evidence on {config.target}: none of this pull request's changes is an ancestor of it yet (fetch, then `{fix}`)", "open"))
            return
        try:
            cmd_finish.evidence(config, item_id, changes, None, folded.owner if takeover else None)
        except QewError as error:
            report.pending.append(Pending(item_id, number, f"evidence was not recorded: {error}", "failed"))
            return
    try:
        done = cmd_finish.complete(config, item_id, takeover, takeover is not None)
    except QewError as error:
        report.pending.append(Pending(item_id, number, str(error).removeprefix(f"cannot complete {item_id}: "), "failed"))
        return
    report.completed.append((number, item_id, " ".join(c[:12] for c in changes) or "children"))
    del done


def _lines(members: list[Member], report: Report) -> None:
    for member in members:
        number = member.record.number
        for _, item_id, shown in (c for c in report.completed if c[0] == number):
            report.lines.append(f"pr #{number}: completed `{item_id}` (evidence {shown})")
        for item_id in (i for i in report.clean if i in member.declaration.items("closes")):
            report.lines.append(f"pr #{number}: `{item_id}` already finished")
        for held in (p for p in report.pending if p.pr == number):
            report.lines.append(f"pr #{number}: left open `{held.item}`: {held.why}")
        for _, item_id, role in (n for n in report.notes if n[0] == number):
            report.lines.append(f"pr #{number}: `{item_id}` {role} (note recorded)")
    if report.pending:
        report.lines.append("repair with `qew land " + " ".join(str(n) for n in dict.fromkeys(p.pr for p in report.pending)) + "` once resolved")
