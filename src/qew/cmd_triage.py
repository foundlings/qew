"""`qew triage`: the explicit read of unapproved text, with what each item still lacks.

Titles and bodies are printed as they are (control characters replaced for a terminal) and annotated with
text findings; nothing is rewritten. Triage is the only list-shaped read that prints draft bodies.
"""

import argparse
from typing import List

from qew import cmd_items, cmd_read, ledger_check, ledger_config, ledger_graph, read_filter, read_view, readfmt, text_findings, tomllib_section
from qew.ledger_config import CONFIG, Config
from qew.ledger_event import State
from qew.ledger_store import Snapshot


def register(commands) -> None:
    triage = commands.add_parser("triage", help="review drafts and items missing planning fields, with bodies and findings")
    triage.add_argument("--search", nargs="+", metavar="TERM", help="only items whose title or body holds every term")
    triage.add_argument("--json", action="store_true", help="machine-readable output")


def run(arguments: argparse.Namespace) -> bool:
    if arguments.command != "triage":
        return False
    config = ledger_config.load()
    snapshot = ledger_check.read_snapshot(cmd_items.ledger_for(config))
    groups = label_groups(config)
    found = []
    for item_id in ledger_graph.ordered(snapshot, list(snapshot.items)):
        if snapshot.folds[item_id].state in (State.COMPLETED, State.CLOSED):
            continue
        missing = missing_fields(snapshot, item_id, groups)
        if snapshot.folds[item_id].state != State.DRAFT and not missing:
            continue
        if arguments.search and not matches(snapshot, item_id, arguments.search):
            continue
        found.append((item_id, missing))
    emit(snapshot, found, arguments.json)
    return True


def label_groups(config: Config) -> List[str]:
    """Label prefixes of the `[triage] label_groups` list in the code repo config (none = no label check)."""
    section = tomllib_section.read(config.root / CONFIG, "triage", {"label_groups": list})
    return list(section.get("label_groups", []))


def missing_fields(snapshot: Snapshot, item_id: str, groups: List[str]) -> List[str]:
    item = snapshot.items[item_id]
    missing = []
    if not item.body.strip():
        missing.append("body")
    if item.kind.value == "task" and not item.parent:
        missing.append("parent")
    for prefix in groups:
        if not any(read_filter.fold(label).startswith(read_filter.fold(prefix)) for label in item.labels):
            missing.append(f"label:{prefix}")
    return sorted(missing)


def matches(snapshot: Snapshot, item_id: str, terms: List[str]) -> bool:
    item = snapshot.items[item_id]
    haystack = read_filter.fold(f"{item.title}\n{item.body}")
    return all(read_filter.fold(term) in haystack for term in terms)


def emit(snapshot: Snapshot, found: List, as_json: bool) -> None:
    rows = []
    for item_id, missing in found:
        item = snapshot.items[item_id]
        row = read_view.row_data(snapshot, item_id)
        row.pop("text_findings", None)
        row["title"] = item.title
        row["body"] = item.body.rstrip("\n")
        row["missing"] = missing
        findings = [f for f in (text_findings.sparse("title", item.title), text_findings.sparse("body", item.body)) if f]
        if findings:
            row["text_findings"] = findings
        rows.append(row)
    if as_json:
        for line in readfmt.ndjson(rows):
            print(line)
        return
    for row in rows:
        flags = [f"missing: {', '.join(row['missing'])}"] if row["missing"] else []
        note = text_findings.note(row.get("text_findings", []))
        head = read_view.plain(f"{row['id']} {row['state']} p{row['priority']} {row['kind']} {row['title']}")
        print(" ".join([head, *(f"[{flag}]" for flag in flags), *([note] if note else [])]))
        for line in read_view.plain(str(row["body"])).split("\n") if row["body"] else []:
            print(f"  {line}")
