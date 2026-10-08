"""The read commands over items: show, list, ready, blocked, graph."""

import argparse
import sys
from typing import List, Optional

from qew import blocked_chains, ledger_author, graph_render, show_lifecycle, untrusted_text, cmd_items, read_filter, ledger_check, ledger_config, ledger_graph, read_view, readfmt
from qew.errors import QewError
from qew.ledger_config import Config
from qew.ledger_event import State
from qew.ledger_ids import sort_key
from qew.ledger_store import Snapshot


READY_LIMIT = 30


def _limit(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"`{value}` is not a whole number") from None
    if number < 0:
        raise argparse.ArgumentTypeError("a limit is zero (all) or more")
    return number


def register(commands) -> None:
    show = commands.add_parser("show", help="show one item with derived edges and evidence")
    show.add_argument("item", help="item id or alias")
    show.add_argument("--json", action="store_true", help="machine-readable output")

    list_ = commands.add_parser("list", help="list items, actionable work first")
    list_.add_argument("--kind", choices=["epic", "issue", "task"], help="only this kind")
    list_.add_argument("--state", choices=["draft", "approved", "claimed", "completed", "closed"], help="only this state")
    list_.add_argument("--work-state", choices=ledger_graph.WORK_STATES, help="only this derived work state")
    list_.add_argument("--label", action="append", default=[], help="only items carrying this label (repeat: every label)")
    list_.add_argument("--owner", help="only items this person currently holds")
    list_.add_argument("--author", help="only items this person created")
    list_.add_argument("--parent", help="only children of this item")
    list_.add_argument("--limit", type=_limit, default=0, help="at most N items (0 = all)")
    list_.add_argument("--json", action="store_true", help="machine-readable output")

    ready = commands.add_parser("ready", help="approved, unclaimed work with every need completed")
    ready.add_argument("--limit", type=_limit, default=READY_LIMIT, help=f"at most N items, default {READY_LIMIT} (0 = all)")
    ready.add_argument("--json", action="store_true", help="machine-readable output")
    blocked = commands.add_parser("blocked", help="open work with unmet needs")
    blocked.add_argument("--json", action="store_true", help="machine-readable output")
    blocked.add_argument("--chains", action="store_true", help="show each item's longest unmet chain down to its root blocker")
    graph = commands.add_parser("graph", help="print dependency and parent edges as text, dot or mermaid")
    graph.add_argument("--json", action="store_true", help="machine-readable output")
    graph.add_argument("--format", choices=["text", "dot", "mermaid"], help="drawing syntax (default text); not with --json")


def run(arguments: argparse.Namespace) -> bool:
    command = arguments.command
    if command not in ("show", "list", "ready", "blocked", "graph"):
        return False
    config = ledger_config.load()
    snapshot = snapshot_for(config)
    if command == "show":
        show(config, snapshot, arguments.item, arguments.json)
    elif command == "list":
        list_items(snapshot, arguments, arguments.json)
    elif command == "ready":
        ready(snapshot, arguments.limit, arguments.json)
    elif command == "blocked":
        blocked(snapshot, arguments.json, arguments.chains)
    else:
        graph(snapshot, arguments.json, arguments.format)
    return True


def snapshot_for(config: Config) -> Snapshot:
    return ledger_check.read_snapshot(cmd_items.ledger_for(config))


def print_rows(snapshot: Snapshot, ids: List[str], as_json: bool, work_state: bool = False) -> None:
    if as_json:
        for line in readfmt.ndjson(read_view.rows(snapshot, ids, work_state)):
            print(line)
    else:
        for item_id in ids:
            print(read_view.text_row(snapshot, item_id))


def _bounded(ids: List[str], limit: int, what: str) -> List[str]:
    """The first `limit` ids (0 = all); a cut is announced on stderr."""
    if limit and len(ids) > limit:
        print(f"qew: {what} truncated: showing {limit} of {len(ids)} (--limit 0 shows all)", file=sys.stderr)
        return ids[:limit]
    return ids


def list_items(snapshot: Snapshot, arguments: argparse.Namespace, as_json: bool) -> None:
    ids = sorted(
        snapshot.items,
        key=lambda i: (ledger_graph.WORK_STATES.index(ledger_graph.work_state(snapshot, i)), snapshot.items[i].priority, sort_key(i)),
    )
    if arguments.kind:
        ids = [i for i in ids if snapshot.items[i].kind.value == arguments.kind]
    if arguments.state:
        ids = [i for i in ids if (snapshot.derived[i][0] or State.DRAFT).value == arguments.state]
    if arguments.work_state:
        ids = [i for i in ids if ledger_graph.work_state(snapshot, i) == arguments.work_state]
    if arguments.label:
        ids = [i for i in ids if read_filter.has_all_labels(snapshot.items[i].labels, arguments.label)]
    if arguments.owner:
        ids = [i for i in ids if read_filter.fold(snapshot.folds[i].owner) == read_filter.fold(arguments.owner)]
    if arguments.author:
        ids = [i for i in ids if read_filter.fold(read_filter.author(snapshot, i)) == read_filter.fold(arguments.author)]
    if arguments.parent:
        parent = snapshot.ledger.resolve(arguments.parent, list(snapshot.items))
        ids = [i for i in ids if snapshot.items[i].parent == parent]
    print_rows(snapshot, _bounded(ids, arguments.limit, "list"), as_json, work_state=True)


def ready(snapshot: Snapshot, limit: int, as_json: bool) -> None:
    ids = ledger_graph.ready(snapshot)
    shown = _bounded(ids, limit, "ready")
    if as_json:
        print(readfmt.envelope(read_view.rows(snapshot, shown), len(ids), len(shown) < len(ids)))
    else:
        print_rows(snapshot, shown, False)


def blocked(snapshot: Snapshot, as_json: bool, with_chains: bool = False) -> None:
    ids = ledger_graph.blocked(snapshot)
    found = blocked_chains.chains(snapshot, ids) if with_chains else {}
    if as_json:
        rows = [
            {**read_view.row_data(snapshot, i), "blocked_by": [read_view.related(snapshot, b) for b in ledger_graph.blockers(snapshot, i)]}
            for i in ids
        ]
        for row in rows:
            if row["id"] in found:
                chain = found[row["id"]]
                row["chain"] = chain
                row["root"] = {"id": chain[-1], "state": blocked_chains.root_state(snapshot, chain[-1])}
        print(readfmt.envelope(rows, len(rows), False))
        return
    for item_id in ids:
        print(f"{read_view.text_row(snapshot, item_id)} <- {' '.join(ledger_graph.blockers(snapshot, item_id))}")
        if item_id in found:
            chain = found[item_id]
            print(f"  chain: {' -> '.join(chain)} [root {blocked_chains.root_state(snapshot, chain[-1])}]")


def graph(snapshot: Snapshot, as_json: bool, form: Optional[str] = None) -> None:
    if as_json and form:
        raise QewError("ask for --json and --format separately, not both", 2)
    if as_json:
        for line in readfmt.ndjson({"item": a, kind: b} for a, kind, b in graph_render.edges(snapshot)):
            print(line)
        return
    print(graph_render.RENDERERS[form or "text"](snapshot), end="")


def show(config: Config, snapshot: Snapshot, reference: str, as_json: bool) -> None:
    ledger = snapshot.ledger
    item_id = ledger.resolve(reference, list(snapshot.items))
    item = snapshot.items[item_id]
    folded = snapshot.folds[item_id]
    kids = ledger_graph.children(snapshot).get(item_id, [])
    blocks = ledger_graph.blocks(snapshot).get(item_id, [])
    mirrors = [{"target": m["_target"], "number": m.get("number"), "url": m.get("url")} for m in ledger.load_mirrors(item_id)]
    evidence = [event.data for event in folded.evidence]
    data = read_view.row_data(snapshot, item_id)
    data["author"] = ledger_author.author(snapshot, item_id) or "unknown"
    for key, value in (("children", kids), ("blocks", blocks), ("evidence", evidence), ("mirrors", mirrors), ("body", item.body)):
        if value:
            data[key] = value
    extra, lines = show_lifecycle.extras(snapshot, item_id)
    data.update(extra)
    if as_json:
        print(readfmt.document(data))
        return
    print(f"id: {item_id}")
    print(f"kind: {item.kind.value}")
    print(f"title: {untrusted_text.show(item.title).text}")
    print(f"state: {data['state']}")
    if folded.owner:
        print(f"owner: {folded.owner}")
    print(f"priority: {item.priority}")
    print(f"author: {data['author']}")
    for key, values in (("parent", [item.parent] if item.parent else []), ("needs", item.needs), ("children", kids), ("blocks", blocks), ("labels", item.labels)):
        if values:
            print(f"{key}: {' '.join(values)}")
    for event in folded.evidence:
        print(f"evidence: {event.get('vcs')} {' '.join(event.get('change_ids'))}")
    for mirror in mirrors:
        print(f"mirror: {mirror['target']} #{mirror['number']}")
    for line in lines:
        print(line)
    if item.body.strip():
        print("---")
        print(item.body.rstrip())
