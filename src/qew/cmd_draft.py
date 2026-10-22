"""Drafts: work not yet released by a human's quoted words, and the release and withdrawal of it."""

from collections import deque
import json
from collections.abc import Sequence

from qew import cmd_read, ledger_graph, pr_record, read_view
from qew.cmd_items import ledger_for
from qew.errors import QewError
from qew.ledger_config import Config
from qew.ledger_event import State, fold
from qew.ledger_store import Ledger, Snapshot
from qew.requester import require_quote

TREE_LIMIT = 1000  # items one `--tree` may visit; a runaway or cyclic tree is refused, never walked forever


def list_drafts(config: Config, parent: str | None, as_json: bool) -> None:
    ledger = ledger_for(config)
    snapshot = cmd_read.snapshot_for(config)
    ids = [i for i in ledger_graph.ordered(snapshot, list(snapshot.items)) if snapshot.folds[i].state == State.DRAFT]
    if parent:
        parent_id = ledger.resolve(parent, list(snapshot.items))
        ids = [i for i in ids if snapshot.items[i].parent == parent_id]
    ids = read_view.parent_first(snapshot, ids)
    if as_json:
        cmd_read.print_rows(snapshot, ids, True)
        return
    for item_id in ids:
        owner = snapshot.items[item_id].parent
        print(f"{read_view.text_row(snapshot, item_id)}{f' (under {owner})' if owner else ''}")


def _roots(ledger: Ledger, snapshot: Snapshot, references: Sequence[str]) -> list[str]:
    """Resolved references, each item once, in the order given (an alias and its full id are one target)."""
    roots: list[str] = []
    for reference in references:
        item_id = ledger.resolve(reference, list(snapshot.items))
        if item_id not in roots:
            roots.append(item_id)
    return roots


def _visit(snapshot: Snapshot, roots: list[str]) -> list[str]:
    """The roots and everything below them by `parent` edges (never `needs`), each item once."""
    kids = ledger_graph.children(snapshot)
    order: list[str] = []
    seen = set()
    queue = deque(roots)
    while queue:
        item_id = queue.popleft()
        if item_id in seen:
            continue
        seen.add(item_id)
        order.append(item_id)
        if len(order) > TREE_LIMIT:
            raise QewError(f"--tree would visit more than {TREE_LIMIT} items; name a smaller subtree", 2)
        queue.extend(kids.get(item_id, []))
    return order


def _state(snapshot: Snapshot, item_id: str) -> State:
    state = snapshot.folds[item_id].state
    if state is None:
        raise QewError(f"{item_id} has no events; run `qew check`", 1)
    return state


def _refuse_closed_roots(snapshot: Snapshot, roots: list[str]) -> None:
    for item_id in roots:
        if _state(snapshot, item_id) == State.CLOSED:
            raise QewError(f"{item_id} is closed; reopen it first, nothing was changed", 2)


def _approved_with(snapshot: Snapshot, item_id: str, quote: str) -> bool:
    """True when the item was released by exactly these words: a retried approval, not a second release."""
    quotes = [e.get("quote") for e in snapshot.events[item_id] if e.type == "approved"]
    return bool(quotes) and quotes[-1] == quote


def _apply(ledger: Ledger, targets: list[str], event_type: str, expected: State, past: str, **data: str) -> None:
    """Append one event per target and read each back; on a failure say exactly what was and was not moved."""
    done: list[str] = []
    for item_id in targets:
        try:
            ledger.append_event(item_id, event_type, **data)
            state = fold(ledger.load_events(item_id)).state
        except QewError as error:
            raise _partial(past, done, targets, str(error)) from None
        if state != expected:
            shown = state.value if state else "no state"
            raise _partial(past, done, targets, f"{item_id} folds to {shown} after `{event_type}`, not {expected.value}")
        done.append(item_id)


def _partial(past: str, done: list[str], targets: list[str], why: str) -> QewError:
    left = [i for i in targets if i not in done]
    return QewError(
        f"{why}; {past}: {' '.join(done) or 'none'}; not {past}: {' '.join(left)}; "
        "rerun the same command to finish, and run `qew push` to refresh the mirrors",
        3,
    )


def _render(kind: str, done: list[str], skipped: list[tuple[str, str]], word: str, as_json: bool) -> list[str]:
    if as_json:
        return [json.dumps({kind: done, "skipped": [item_id for item_id, _ in skipped]})]
    return [f"{i} {word}" for i in done] + [f"{i} skipped ({why})" for i, why in skipped]


def approve(config: Config, references: list[str], tree: bool, quote: str, as_json: bool = False) -> list[str]:
    verified = require_quote(quote)
    ledger = ledger_for(config)
    snapshot = ledger.snapshot()
    roots = _roots(ledger, snapshot, references)
    _refuse_closed_roots(snapshot, roots)
    visited = _visit(snapshot, roots) if tree else roots
    targets = [i for i in visited if _state(snapshot, i) == State.DRAFT]
    skipped = [(i, f"already {_state(snapshot, i).value}") for i in visited if i not in targets]
    if not targets and all(_approved_with(snapshot, i, verified) for i in roots):
        return _render("approved", [], [(i, "unchanged: already approved with these words") for i in roots], "approved", as_json) \
            if as_json else [f"{i} unchanged (already approved)" for i in roots]
    if not targets:
        named = " ".join(roots)
        raise QewError(
            f"nothing to approve: {named} is not a draft" + (" and nothing below it is" if tree else ""), 2
        )
    _apply(ledger, targets, "approved", State.APPROVED, "approved", quote=verified)
    return _render("approved", targets, skipped, "approved", as_json)


def revert(
    config: Config, references: list[str], tree: bool, reason: str, quote: str, as_json: bool = False
) -> list[str]:
    verified = require_quote(quote)
    if not reason or not reason.strip():
        raise QewError("--reason must say why the release is withdrawn", 2)
    ledger = ledger_for(config)
    snapshot = ledger.snapshot()
    roots = _roots(ledger, snapshot, references)
    _refuse_closed_roots(snapshot, roots)
    visited = _visit(snapshot, roots) if tree else roots
    for item_id in visited:
        _refuse_claimed(snapshot, item_id)
    for item_id in roots:
        if _state(snapshot, item_id) == State.COMPLETED:
            raise QewError(f"{item_id} is completed; only approved work can return to draft, nothing was changed", 2)
    targets = [i for i in visited if _state(snapshot, i) == State.APPROVED]
    skipped = [(i, f"already {_state(snapshot, i).value}") for i in visited if i not in targets]
    if not targets:
        raise QewError(f"nothing to revert: {' '.join(roots)} is a draft" + (" and so is everything below it" if tree else ""), 2)
    for item_id in targets:
        _refuse_in_review(ledger, item_id)
    _apply(ledger, targets, "reverted", State.DRAFT, "reverted", reason=reason.strip(), quote=verified)
    return _render("reverted", targets, skipped, "draft", as_json)


def _refuse_claimed(snapshot: Snapshot, item_id: str) -> None:
    folded = snapshot.folds[item_id]
    if folded.state == State.CLAIMED:
        raise QewError(
            f"{item_id} is claimed by {folded.owner}; release it first with qew release {item_id}, nothing was changed", 3
        )


def _refuse_in_review(ledger: Ledger, item_id: str) -> None:
    """Approved work with a pull request that is not a draft is under review: pulling it would orphan the review."""
    for record in ledger.load_mirrors(item_id):
        if record.get("kind") == "pr" and record.get("draft") is not True:
            raise QewError(f"{item_id} has an open pull request; close or merge it first, nothing was changed", 2)
    covering = pr_record.covering_open(ledger, item_id)
    if covering:
        raise QewError(
            f"{item_id} has an open pull request; close or merge it first, nothing was changed (pull request #{covering[0]})", 2
        )
