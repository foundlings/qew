"""`qew pr merge N [M ...] --check --ledger-only`: every bookkeeping refusal a merge would raise, from the ledger alone.

No forge is built or called, so it works offline and without a token; it writes nothing. A harness runs it before a long
gate. The several numbers are one union: they never conflict with each other. Every refusal is collected with the command
that fixes it.
"""

import json
from dataclasses import asdict, dataclass, field
from typing import TypedDict

from qew import code_vcs, pr_gate, pr_record, remote_options
from qew.cmd_items import ledger_for
from qew.errors import QewError
from qew.ledger_config import Config
from qew.ledger_event import State
from qew.ledger_item import Kind
from qew.ledger_store import Snapshot, actor
from qew.mirror_types import MirrorRecord
from qew.pr_links import LINKS_SCHEMA
from qew.pr_record import PrRecord

SCHEMA_VERSION = 1


class Unioned(TypedDict):
    """What the pull requests being merged together close, leave open and relate to; rows are `{item, pr}` plus `why` or `reason`."""

    closes: list[dict[str, object]]
    remains: list[dict[str, object]]
    related: list[dict[str, object]]
    no_item: list[dict[str, object]]
    undeclared: list[int]


@dataclass(frozen=True)
class Finding:
    message: str
    fix: str
    item: str
    pr: int
    exit_code: int

    def line(self) -> str:
        return f"pr #{self.pr}: {self.message}" + (f" (fix: {self.fix})" if self.fix else "")


@dataclass
class Verdict:
    refusals: list[Finding] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    union: Unioned | None = None  # what the pull requests close, leave and relate to (schema 9 only)

    def render_json(self) -> str:
        body: dict[str, object] = {"mergeable": not self.refusals, "refusals": [asdict(r) for r in self.refusals],
                                   "warnings": self.warnings, "schema_version": SCHEMA_VERSION}
        if self.union is not None:
            body["union"] = self.union
        return json.dumps(body, sort_keys=True)


def check(config: Config, numbers: list[int], target: str | None, reason: str = "") -> Verdict:
    """The verdict for these pull requests merged together."""
    ledger = ledger_for(config)
    snapshot = ledger.snapshot()
    verdict = Verdict()
    records: list[PrRecord] = []
    for number in numbers:
        try:
            pr_record.positive_number(number)
            records.append(pr_record.find(ledger, number, target))
        except QewError as error:
            verdict.refusals.append(Finding(str(error), "", "", number, error.exit_code))
    declared = ledger.schema >= LINKS_SCHEMA
    states: dict[str, State] = {}
    for record in records:
        states |= _one(config, snapshot, record, set(numbers), reason, verdict, declared)
    if declared:
        from qew import pr_preflight_declared  # judges with this module's Verdict

        pr_preflight_declared.judge(snapshot, records, verdict)
    else:
        _children(snapshot, records, states, verdict)
    return verdict


def cover_command(snapshot: Snapshot, number: int, items: list[str]) -> str:
    """The command that puts `items` under pull request `number`: a declaration on schema 9, a checklist entry before."""
    if snapshot.ledger.schema >= LINKS_SCHEMA:
        return f"qew pr link {number} --closes {' '.join(items)}"
    return f"qew pr check {number} --add {' '.join(items)}"


def _children(snapshot: Snapshot, records: list[PrRecord], states: dict[str, State], verdict: Verdict) -> None:
    """Claimed covered items with open children no member covers: the union is judged as one pull request."""
    if not records:
        return
    first = records[0]
    covered = tuple(dict.fromkeys(i for r in records for i in r.items if i in states))  # a member refused earlier has no states to judge
    closing = tuple(dict.fromkeys(i for r in records for i in (r.items if r.closing is None else r.closing) if i in states))
    union = PrRecord(first.host, first.number, first.mirror_file, first.target, first.opener, covered, closing=closing)
    owner = {item_id: record.number for record in reversed(records) for item_id in record.items}
    for item_id, blocking in pr_gate.open_children(snapshot, union, {i: s for i, s in states.items() if i in union.items}).items():
        verdict.refusals.append(Finding(
            f"cannot merge half-done work: {item_id} has open children {' '.join(blocking)}",
            f"{cover_command(snapshot, owner[item_id], blocking)}  (cover them), or finish them first, or declare `qew pr link {owner[item_id]} --remains {item_id}` if this is a partial slice" if snapshot.ledger.schema >= LINKS_SCHEMA
            else f"{cover_command(snapshot, owner[item_id], blocking)}  (cover them), or finish them first", item_id, owner[item_id], 2))


def _one(config: Config, snapshot: Snapshot, record: PrRecord, union: set[int], reason: str, verdict: Verdict, declared: bool = False) -> dict[str, State]:
    number = record.number
    mirror: MirrorRecord | dict[str, object] = pr_record.mirror_of(snapshot.ledger, record) or {}
    if mirror.get("state", "open") in ("merged", "closed"):
        verdict.refusals.append(Finding(f"is {mirror['state']}; it cannot merge", "", "", number, 2))
        return {}
    try:
        states = pr_record.known_states(snapshot, record)
        pr_record.require_owner(snapshot, record, states, actor(), reason, merging=True)
    except QewError as error:
        verdict.refusals.append(Finding(str(error), "", "", number, error.exit_code))
        return {}
    held = pr_record.drafts(states)
    if held:
        verdict.refusals.append(Finding(f"is held: {' '.join(held)} is a draft, so it cannot merge until a human releases it", f"qew draft approve {' '.join(held)} --quote ...", held[0], number, 2))
    for item_id in () if declared else record.items:
        others = [n for n in _covering(snapshot, item_id) if n not in union]
        if others and states[item_id] not in pr_record.CLOSED_STATES:
            verdict.refusals.append(Finding(
                f"{item_id} is also covered by open pull request {' '.join('#' + str(n) for n in others)}; two pull requests would both finish it",
                f"qew pr check {number} --drop {item_id} --reason TEXT, or merge them together (`qew pr merge {number} {' '.join(map(str, others))}`)",
                item_id, number, 2))
    _evidence(config, snapshot, record, mirror.get("head"), states, verdict)
    return states


def _covering(snapshot: Snapshot, item_id: str) -> list[int]:
    """Numbers of the recorded pull requests that are not finished (drafts included) and cover `item_id`."""
    found: list[int] = []
    for host, mirror in pr_record.entries(snapshot.ledger):
        if mirror.get("state", "open") in ("merged", "closed"):
            continue
        number = mirror.get("number")
        if item_id in mirror.get("items", [host]) and isinstance(number, int):
            found.append(number)
    return sorted(set(found))


def _evidence(config: Config, snapshot: Snapshot, record: PrRecord, head: object, states: dict[str, State], verdict: Verdict) -> None:
    """Delivered items need evidence covering the head; the head is read from the local checkout, never the forge."""
    delivering = [i for i in pr_gate.delivering(states, record) if snapshot.items[i].kind != Kind.EPIC]
    if not delivering:
        return
    remote = remote_options.selected()
    resolved = code_vcs.run_git(config.root, "rev-parse", "--verify", "--quiet", f"refs/remotes/{remote}/{head}^{{commit}}") if isinstance(head, str) and head else None
    sha = resolved.stdout.strip() if resolved is not None and resolved.returncode == 0 else ""
    if not sha:
        verdict.warnings.append(f"pr #{record.number}: head {head} is not fetched in this checkout; evidence on the head was not checked")
        return
    for item_id in delivering:
        try:
            covered = pr_gate.evidence_covers(config, snapshot.folds[item_id].evidence, sha)
        except QewError as error:
            verdict.refusals.append(Finding(str(error), "", item_id, record.number, error.exit_code))
            continue
        if not covered:
            verdict.refusals.append(Finding(f"head {sha[:12]} is not covered by recorded evidence of {item_id}",
                                            f"qew evidence {item_id} --change CHANGE", item_id, record.number, 2))
