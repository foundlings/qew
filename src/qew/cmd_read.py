"""The read commands over items: show, list, ready, blocked, graph."""

import argparse
from typing import List, Optional

from qew import graph_render, show_history, untrusted_text, cmd_items, ledger_check, ledger_config, ledger_graph, read_view, readfmt
from qew.errors import QewError
from qew.ledger_config import Config
from qew.ledger_event import State
from qew.ledger_store import Snapshot


def register(commands) -> None:
    show = commands.add_parser("show", help="show one item with derived edges and evidence")
    show.add_argument("item", help="item id or alias")
    show.add_argument("--json", action="store_true", help="machine-readable output")

    list_ = commands.add_parser("list", help="list items")
    list_.add_argument("--kind", choices=["epic", "issue", "task"], help="only this kind")
    list_.add_argument("--state", choices=["draft", "approved", "claimed", "completed", "closed"], help="only this state")
    list_.add_argument("--label", help="only items carrying this label")
    list_.add_argument("--json", action="store_true", help="machine-readable output")

    ready = commands.add_parser("ready", help="approved, unclaimed work with every need completed")
    ready.add_argument("--json", action="store_true", help="machine-readable output")
    blocked = commands.add_parser("blocked", help="open work with unmet needs")
    blocked.add_argument("--json", action="store_true", help="machine-readable output")
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
        list_items(snapshot, arguments.kind, arguments.state, arguments.label, arguments.json)
    elif command == "ready":
        ready(snapshot, arguments.json)
    elif command == "blocked":
        blocked(snapshot, arguments.json)
    else:
        graph(snapshot, arguments.json, arguments.format)
    return True


def snapshot_for(config: Config) -> Snapshot:
    return ledger_check.read_snapshot(cmd_items.ledger_for(config))


def print_rows(snapshot: Snapshot, ids: List[str], as_json: bool) -> None:
    if as_json:
        for line in readfmt.ndjson(read_view.rows(snapshot, ids)):
            print(line)
    else:
        for item_id in ids:
            print(read_view.text_row(snapshot, item_id))


def list_items(snapshot: Snapshot, kind, state, label, as_json: bool) -> None:
    ids = ledger_graph.ordered(snapshot, list(snapshot.items))
    if kind:
        ids = [i for i in ids if snapshot.items[i].kind.value == kind]
    if state:
        ids = [i for i in ids if (snapshot.folds[i].state or State.DRAFT).value == state]
    if label:
        ids = [i for i in ids if label in snapshot.items[i].labels]
    print_rows(snapshot, ids, as_json)


def ready(snapshot: Snapshot, as_json: bool) -> None:
    ids = ledger_graph.ready(snapshot)
    if as_json:
        print(readfmt.envelope(read_view.rows(snapshot, ids), len(ids), False))
    else:
        print_rows(snapshot, ids, False)


def blocked(snapshot: Snapshot, as_json: bool) -> None:
    ids = ledger_graph.blocked(snapshot)
    if as_json:
        rows = [{**read_view.row_data(snapshot, i), "blocked_by": ledger_graph.blockers(snapshot, i)} for i in ids]
        print(readfmt.envelope(rows, len(rows), False))
        return
    for item_id in ids:
        print(f"{read_view.text_row(snapshot, item_id)} <- {' '.join(ledger_graph.blockers(snapshot, item_id))}")


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
    for key, value in (("children", kids), ("blocks", blocks), ("evidence", evidence), ("mirrors", mirrors), ("body", item.body),
                       ("approvals", show_history.approvals(snapshot.events[item_id])),
                       ("comments", show_history.comments(snapshot.events[item_id]))):
        if value:
            data[key] = value
    if as_json:
        print(readfmt.document(data))
        return
    print(f"id: {item_id}")
    print(f"kind: {item.kind.value}")
    print(f"title: {untrusted_text.show(item.title).text}")
    print(f"state: {data['state']}")
    for line in show_history.release_lines(snapshot.events[item_id]):
        print(line)
    if folded.owner:
        print(f"owner: {folded.owner}")
    print(f"priority: {item.priority}")
    for key, values in (("parent", [item.parent] if item.parent else []), ("needs", item.needs), ("children", kids), ("blocks", blocks), ("labels", item.labels)):
        if values:
            print(f"{key}: {' '.join(values)}")
    for event in folded.evidence:
        print(f"evidence: {event.get('vcs')} {' '.join(event.get('change_ids'))}")
    for mirror in mirrors:
        print(f"mirror: {mirror['target']} #{mirror['number']}")
    for line in show_history.comment_lines(snapshot.events[item_id]):
        print(line)
    if item.body.strip():
        print("---")
        print(item.body.rstrip())
