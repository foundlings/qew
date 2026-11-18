"""The ledger-only preflight of a schema 9 ledger: the pull requests' link declarations, judged together as one union.

What each rule protects: a `closes` item must be ready to be completed by this landing (claimed by the merger, its needs and every
descendant finished or closed by the same union), no other open pull request may close or leave it (two finishes, or a finish under
another's unfinished slice), and a member without a declaration blocks the union. `remains` and `related` are never gated.
"""

from qew import ledger_graph, pr_links, pr_record
from qew.ledger_event import State
from qew.ledger_store import Snapshot
from qew.pr_links import Declaration
from qew.pr_preflight import Finding, Unioned, Verdict
from qew.pr_record import CLOSED_STATES, PrRecord


def _open_declarations(snapshot: Snapshot, skip: set[int]) -> dict[int, tuple[str, Declaration]]:
    """The declaration of every recorded pull request that is not finished and not one of `skip`, by number."""
    found: dict[int, tuple[str, Declaration]] = {}
    ledger = snapshot.ledger
    for host, mirror in pr_record.entries(ledger):
        number = mirror.get("number")
        if mirror.get("state", "open") in ("merged", "closed") or number in skip:
            continue
        found[number] = (host, pr_links.load(ledger, mirror["_target"]))
    return found


def _descendants(snapshot: Snapshot, root: str) -> list[str]:
    kids = ledger_graph.children(snapshot)
    found: list[str] = []
    seen = {root}
    queue = list(kids.get(root, []))
    while queue:
        item_id = queue.pop(0)
        if item_id in seen:
            continue
        seen.add(item_id)
        found.append(item_id)
        queue.extend(kids.get(item_id, []))
    return found


def judge(snapshot: Snapshot, records: list[PrRecord], verdict: Verdict) -> None:
    ledger = snapshot.ledger
    numbers = {record.number for record in records}
    declared = {record.number: pr_links.load(ledger, record.mirror_file) for record in records}
    undeclared = [number for number, declaration in declared.items() if not declaration.declared]
    for number in undeclared:
        verdict.refusals.append(Finding(
            "has no link declaration: a pull request must say what it does to ledger items",
            f"qew pr link {number} --closes ITEM...  (or --remains / --related, or --no-item REASON); a migrated pull request: qew pr migrate {number}",
            "", number, 2))
    closing = {item_id: number for number, declaration in declared.items() for item_id in declaration.items("closes")}
    blocked: dict[str, str] = {}
    for item_id, number in closing.items():
        if item_id not in snapshot.items:
            continue
        state = snapshot.folds[item_id].state
        reasons: list[str] = []
        if state in CLOSED_STATES:
            reasons.append(f"is already {state.value if state else 'finished'}; it cannot be closed again")
            fix = f"qew pr link {number} --related {item_id}  (or: qew pr unlink {number} {item_id})"
        elif state == State.APPROVED:
            reasons.append("is not claimed, so landing cannot complete it")
            fix = f"qew start {item_id}"
        else:
            fix = ""
        unmet = [need for need in snapshot.items[item_id].needs
                 if need in snapshot.folds and snapshot.folds[need].state not in CLOSED_STATES and need not in closing]
        if unmet and state not in CLOSED_STATES:
            reasons.append(f"needs {' '.join(unmet)}, which is not finished and not closed by this union")
            fix = fix or f"qew pr link {number} --closes {' '.join(unmet)}  (or finish them first)"
        open_below = [d for d in _descendants(snapshot, item_id) if snapshot.folds[d].state not in CLOSED_STATES and d not in closing]
        if open_below and state not in CLOSED_STATES:
            shown = " ".join(open_below[:8]) + (f" (and {len(open_below) - 8} more)" if len(open_below) > 8 else "")
            reasons.append(f"has unfinished descendants {shown}, not closed by this union")
            fix = fix or f"qew pr link {number} --closes {' '.join(open_below[:8])}  (or `qew pr link {number} --remains {item_id}` for a partial slice)"
        for reason in reasons:
            verdict.refusals.append(Finding(f"{item_id} {reason}", fix, item_id, number, 2))
        if reasons:
            blocked[item_id] = "; ".join(reasons)
    others = _open_declarations(snapshot, numbers)
    for item_id, number in closing.items():
        for other, (_, declaration) in sorted(others.items()):
            role = declaration.roles.get(item_id)
            if role in ("closes", "remains"):
                verdict.refusals.append(Finding(
                    f"{item_id} is also {'closed' if role == 'closes' else 'left open'} by open pull request #{other}: "
                    f"{'two pull requests would both finish it' if role == 'closes' else 'closing it here would finish it under that unfinished slice'}",
                    f"merge them together (`qew pr merge {number} {other}`), or `qew pr link {number} --remains {item_id}`, or finish #{other} first",
                    item_id, number, 2))
    verdict.union = Unioned(**{
        "closes": [{"item": item_id, "pr": number} for item_id, number in sorted(closing.items())],
        "remains": [{"item": item_id, "pr": number, "why": "declared remains"} for number, d in sorted(declared.items()) for item_id in d.items("remains")]
        + [{"item": item_id, "pr": closing[item_id], "why": f"declared closes but blocked: {why}"} for item_id, why in sorted(blocked.items())],
        "related": [{"item": item_id, "pr": number} for number, d in sorted(declared.items()) for item_id in d.items("related")],
        "no_item": [{"pr": number, "reason": d.no_item} for number, d in sorted(declared.items()) if d.no_item is not None],
        "undeclared": undeclared,
    })


def summary(verdict: Verdict, numbers: list[int]) -> list[str]:
    """What the union closes, leaves and relates to, per member pull request, for the text output."""
    union = verdict.union
    if union is None:
        return []
    lines = [f"union {' '.join('#' + str(n) for n in numbers)}: closes {len(union['closes'])}, remains {len(union['remains'])}, related {len(union['related'])}"]
    for kind in ("closes", "remains", "related"):
        for row in union[kind]:
            why = f" ({row['why']})" if "why" in row else ""
            lines.append(f"  {kind} `{row['item']}` (pr #{row['pr']}){why}")
    lines += [f"  no item: pr #{row['pr']}: {row['reason']}" for row in union["no_item"]]
    lines += [f"  undeclared: pr #{number}" for number in union["undeclared"]]
    return lines
