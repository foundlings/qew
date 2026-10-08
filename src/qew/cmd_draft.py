"""Drafts: work not yet released by a human's quoted words."""

from typing import List

from qew import ledger_graph
from qew.cmd_items import emit, ledger_for
from qew.errors import QewError
from qew.ledger_config import Config
from qew.ledger_event import State, transition_error
from qew.requester import require_quote


def list_drafts(config: Config, as_json: bool) -> None:
    snapshot = ledger_for(config).snapshot()
    ids = [i for i in ledger_graph.ordered(snapshot, list(snapshot.items)) if snapshot.folds[i].state == State.DRAFT]
    emit(snapshot, ids, as_json)


def _descendants(snapshot, roots: List[str]) -> List[str]:
    kids = ledger_graph.children(snapshot)
    found: List[str] = []
    stack = list(roots)
    while stack:
        item_id = stack.pop(0)
        if item_id not in found:
            found.append(item_id)
            stack.extend(kids.get(item_id, []))
    return found


def approve(config: Config, references: List[str], tree: bool, quote: str) -> List[str]:
    verified = require_quote(quote)
    ledger = ledger_for(config)
    snapshot = ledger.snapshot()
    ids = [ledger.resolve(reference, list(snapshot.items)) for reference in references]
    if tree:
        ids = _descendants(snapshot, ids)
    targets = [i for i in ids if snapshot.folds[i].state == State.DRAFT] if tree else ids
    for item_id in targets:
        problem = transition_error(snapshot.folds[item_id].state, "approved")
        if problem:
            raise QewError(f"{snapshot.short(item_id)}: {problem}", 2)
    for item_id in targets:
        ledger.append_event(item_id, "approved", quote=verified)
    return [f"{snapshot.short(i)} approved" for i in targets]


def revert(config: Config, references: List[str], reason: str, quote: str) -> List[str]:
    verified = require_quote(quote)
    ledger = ledger_for(config)
    snapshot = ledger.snapshot()
    ids = [ledger.resolve(reference, list(snapshot.items)) for reference in references]
    for item_id in ids:
        problem = transition_error(snapshot.folds[item_id].state, "reverted")
        if problem:
            raise QewError(f"{snapshot.short(item_id)}: {problem}", 2)
    for item_id in ids:
        ledger.append_event(item_id, "reverted", reason=reason, quote=verified)
    return [f"{snapshot.short(i)} draft" for i in ids]
