"""The guard against an unticked Progress box: what `pr ready`, `pr union` and `pr merge` say, and what an override must carry.

The text names each box (id and title), why it matters, and the commands that fix it, so the failure surfaces at the author.
"""

from qew import pr_body, pr_checklist, untrusted_text
from qew.errors import QewError
from qew.ledger_store import Ledger

DISPLAY_SCHEMA = 9  # from this schema the checklist is a display of an agent's own steps: it gates nothing and covers no item


def gates(ledger: Ledger) -> bool:
    """Whether unticked boxes still refuse a ready or a merge (a schema 8 ledger keeps the 6.x rules)."""
    return ledger.schema < DISPLAY_SCHEMA


IGNORED_NOTE = "qew: --allow-unticked is accepted and ignored: on this ledger unticked Progress boxes gate nothing (the link declaration decides); drop the flag"


def unticked(body: str) -> list[pr_checklist.Box]:
    return [box for box in pr_checklist.boxes(body) if not box.ticked]


def explain(number: int, boxes: list[pr_checklist.Box], override: bool = False) -> str:
    """The refusal for pull request `number`: the reason, each unticked box, and the fix commands for it."""
    lines = [f"pr #{number} has unticked checklist items; a merge of this PR is refused until every box is ticked or dropped:"]
    for box in boxes:
        shown = untrusted_text.show(box.text).text
        lines += [f"  {box.address} {shown}",
                  f"    qew pr check {number} {box.address}   (once its work is on the branch)",
                  f"    qew pr check {number} --drop {box.address} --reason TEXT   (when this PR does not finish it)"]
    lines.append(f"  qew pr close {number}   (when it only covers a shared parent it cannot finish)")
    if override:
        lines.append("  or, knowingly: --allow-unticked --reason TEXT (recorded on the items and in the PR body)")
    return "\n".join(lines)


def refuse(number: int, body: str, override: bool = False) -> None:
    boxes = unticked(body)
    if boxes:
        raise QewError(explain(number, boxes, override), 2)


def reason_for(allow: bool, reason: str | None) -> str:
    """The override's reason, cleaned to one line; `--allow-unticked` needs one and a reason means nothing without it. Exit 2."""
    text = " ".join((reason or "").split())
    if allow and not text:
        raise QewError("--allow-unticked needs --reason TEXT: say why the box stays unticked", 2)
    if text and not allow:
        raise QewError("--reason with no --allow-unticked: nothing to explain", 2)
    if allow:
        pr_body.refuse_closing_references(text, "the override reason")
        if "<!--" in text or "-->" in text:
            raise QewError("the override reason carries a comment marker", 2)
    return text


def override_line(who: str, when: str, boxes: list[pr_checklist.Box], reason: str) -> str:
    return f"Unticked override: {who} on {when} let this through with unticked checklist items {' '.join(b.address for b in boxes)}: {reason}"
