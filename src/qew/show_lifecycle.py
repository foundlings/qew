"""The lifecycle facts `qew show` adds to an item: who holds it, how it finished, what it links to."""


from qew import untrusted_text
from qew.jsontypes import JsonObject
from qew.ledger_event import Event
from qew.ledger_item import Kind
from qew.ledger_store import Snapshot


def _safe(text: str) -> str:
    """Agent-written free text on one line, withheld when it hides characters a reader cannot see."""
    hidden = untrusted_text.findings(text)
    return f"[text withheld: {', '.join(hidden)}]" if hidden else " ".join(text.split())


def closed_links(snapshot: Snapshot, item_id: str) -> dict[str, list[str]]:
    """Closed items that name this one as the original (`duplicate_of`) or as their replacement (`superseded_by`)."""
    found: dict[str, list[str]] = {"duplicate_of": [], "superseded_by": []}
    for other, folded in snapshot.folds.items():
        if folded.closure is not None:
            for key in found:
                if folded.closure.get(key) == item_id:
                    found[key].append(other)
    return {key: sorted(ids) for key, ids in found.items()}


def _completion_line(event: Event) -> str:
    parts = [_safe(event.get("reason") or "no reason given")]
    if event.get("commits"):
        parts.append(f"landed {' '.join(event.get('commits'))} on {event.get('branch')}")
    if event.get("no_commit"):
        parts.append(f"no code: {_safe(event.get('no_commit'))}")
    if event.get("prs"):
        parts.append("PR " + " ".join(f"#{number}" for number in event.get("prs")))
    if event.get("taken_over_from"):
        parts.append(f"taken over from {event.get('taken_over_from')} by {event.actor}")
    if event.get("children_finished"):
        parts.append(f"{event.get('children_finished')} children finished")
    return " | ".join(parts)


def _closure_line(event: Event) -> str:
    text: str = event.get("reason")
    for key, word in (("duplicate_of", "of"), ("superseded_by", "by")):
        if event.get(key):
            text += f" {word} {event.get(key)}"
    return text + (f" ({_safe(event.get('note'))})" if event.get("note") else "")


def extras(snapshot: Snapshot, item_id: str) -> tuple[JsonObject, list[str]]:
    """(machine fields, text lines) for one item; the machine form always carries `claim` and `holders`."""
    folded = snapshot.folds[item_id]
    holders = snapshot.derived[item_id][1]
    links = closed_links(snapshot, item_id)
    data: JsonObject = {"claim": folded.claim, "holders": list(holders)}
    lines: list[str] = []
    events = sorted(snapshot.events[item_id], key=lambda event: event.id)
    origin = snapshot.items[item_id].discovered_from
    if origin:
        data["discovered_from"] = origin
        lines.append(f"discovered-from: {origin}")
    found = sorted(other.id for other in snapshot.items.values() if other.discovered_from == item_id)
    if found:
        data["discovered"] = found
        lines.append(f"discovered: {' '.join(found)}")
    if snapshot.items[item_id].kind == Kind.EPIC and holders:
        lines.append(f"holders: {' '.join(holders)}")
    if folded.claim and folded.claim.get("lane"):
        parts = [f"lane {folded.claim['lane']}", f"kind {folded.claim['kind']}"]
        parts += [f"workspace {folded.claim['workspace']}"] if folded.claim.get("workspace") else []
        lines.append(f"claim: {', '.join(parts)}")
    if folded.claim and folded.claim.get("needs_on"):
        lines.append(f"staged: started on {folded.claim['needs_on']} before {folded.claim['staged_needs']} completed")
    if folded.completion is not None:
        data["completion"] = folded.completion.data
        lines.append(f"completed: {_completion_line(folded.completion)}")
    if folded.closure is not None:
        data["closure"] = folded.closure.data
        lines.append(f"closed: {_closure_line(folded.closure)}")
    for key, label in (("duplicate_of", "closed-duplicates"), ("superseded_by", "supersedes")):
        if links[key]:
            data[label.replace("-", "_")] = links[key]
            lines.append(f"{label}: {' '.join(links[key])}")
    return data, lines
