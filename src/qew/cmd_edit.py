"""File and edit items: `create`, `set`, `comment`, `block`, `unblock`."""

import json
import os
import sys
from typing import Dict, List, Optional

from qew import code_text, editor, graph_rules, ledger_config, outcome
from qew.cmd_items import apply_set, create_items, ledger_for
from qew.errors import QewError
from qew.ledger_config import Config
from qew.ledger_event import MAX_COMMENT_BYTES, State
from qew.ledger_store import Snapshot

_SECTIONS = (("where", "Where"), ("problem", "Problem"), ("goal", "Goal"), ("fix", "Fix"),
             ("approach", "Approach"), ("done_when", "Done when"))
_FINISHED = (State.COMPLETED, State.CLOSED)


def register(commands) -> None:
    create = commands.add_parser("create", help="file an item (a draft unless --quote releases it)")
    create.add_argument("--kind", choices=["epic", "issue", "task"], default="issue", help="item kind")
    create.add_argument("--title", help="short imperative title")
    create.add_argument("--parent", help="parent item")
    create.add_argument("--needs", nargs="*", default=[], help="items this one is blocked by")
    create.add_argument("--label", action="append", default=[], help="label, repeatable")
    create.add_argument("--priority", type=int, default=2, help="lower is sooner")
    create.add_argument("--body", help="Markdown body")
    create.add_argument("--body-file", help="read the body from a file, or - for stdin")
    create.add_argument("--where", help="structured body: where the work happens")
    create.add_argument("--problem", help="structured body: the problem (not with --goal)")
    create.add_argument("--goal", help="structured body: the goal (not with --problem)")
    create.add_argument("--fix", help="structured body: the fix (not with --approach)")
    create.add_argument("--approach", help="structured body: the approach (not with --fix)")
    create.add_argument("--done-when", help="structured body: what done looks like")
    create.add_argument("--edit", action="store_true", help="write the body in $VISUAL or $EDITOR")
    create.add_argument("--draft", action="store_true", help="file a draft explicitly (refused together with --quote)")
    create.add_argument("--discovered-from", metavar="ITEM", help="the item this one was found while working on (not a dependency)")
    create.add_argument("--quote", help="a human's words, \"Name: 'their words'\"; releases the item")
    create.add_argument("--from-file", help="JSON object or array of items, or - for stdin")
    create.add_argument("--import", dest="import_events", action="store_true", help="with --from-file: keep each item's own events (imported history)")
    create.add_argument("--json", action="store_true", help="machine-readable output")

    set_ = commands.add_parser("set", help="change an item's header fields")
    set_.add_argument("items", nargs="+", help="item ids or aliases")
    set_.add_argument("--title", help="new title")
    set_.add_argument("--parent", help="new parent")
    set_.add_argument("--no-parent", action="store_true", help="remove the parent")
    set_.add_argument("--kind", choices=["epic", "issue", "task"], help="new kind")
    set_.add_argument("--needs", nargs="*", help="replace the needs list")
    set_.add_argument("--labels", nargs="*", help="replace the labels")
    set_.add_argument("--add-label", action="append", default=[], help="add a label, repeatable")
    set_.add_argument("--remove-label", action="append", default=[], help="remove a label, repeatable")
    set_.add_argument("--priority", type=int, help="new priority")
    set_.add_argument("--body", help="replace the body")
    set_.add_argument("--body-file", help="replace the body with a file's text")
    set_.add_argument("--edit", action="store_true", help="rewrite the body in $VISUAL or $EDITOR")
    set_.add_argument("--closed", action="store_true", help="allow editing completed or closed work")
    set_.add_argument("--json", action="store_true", help="machine-readable output")

    comment = commands.add_parser("comment", help="add an append-only comment to an item")
    comment.add_argument("item", help="item id or alias")
    comment.add_argument("--body", help="the comment text")
    comment.add_argument("--body-file", help="read the comment from a file")
    comment.add_argument("--json", action="store_true", help="machine-readable output")

    block = commands.add_parser("block", help="make an item wait for another (adds one needs edge)")
    block.add_argument("item", help="item id or alias")
    block.add_argument("--on", required=True, metavar="ITEM", help="the item it must wait for")
    block.add_argument("--closed", action="store_true", help="allow editing completed or closed work")
    block.add_argument("--json", action="store_true", help="machine-readable output")
    unblock = commands.add_parser("unblock", help="remove one needs edge")
    unblock.add_argument("item", help="item id or alias")
    unblock.add_argument("--from", dest="source", required=True, metavar="ITEM", help="the item it no longer waits for")
    unblock.add_argument("--closed", action="store_true", help="allow editing completed or closed work")
    unblock.add_argument("--json", action="store_true", help="machine-readable output")


def run(arguments) -> bool:
    command = arguments.command
    if command not in ("create", "set", "comment", "block", "unblock"):
        return False
    config = ledger_config.load()
    if command == "create":
        _create(config, arguments)
    elif command == "set":
        _set(config, arguments)
    elif command == "comment":
        result = comment(config, arguments.item, arguments.body, arguments.body_file)
        print(outcome.render(arguments, result.pop("line"), **result))
    else:
        if command == "block":
            result = edge(config, arguments.item, arguments.on, True, arguments.closed)
        else:
            result = edge(config, arguments.item, arguments.source, False, arguments.closed)
        print(outcome.render(arguments, result.pop("line"), **result))
    return True


def read_text(path: str) -> str:
    """A file's text; stdin for `-`; a symlink is refused so a body never follows a link out of the tree."""
    if path == "-":
        return sys.stdin.read()
    if os.path.islink(path):
        raise QewError(f"{path} is a symlink; name the real file", 2)
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeError) as error:
        raise QewError(f"cannot read {path}: {error}", 2) from None


def _warn(text: str) -> None:
    for line in code_text.warnings(text):
        print(line, file=sys.stderr)


def _structured(arguments) -> Optional[str]:
    given = {key: getattr(arguments, key) for key, _ in _SECTIONS if getattr(arguments, key) is not None}
    if not given:
        return None
    for left, right in (("problem", "goal"), ("fix", "approach")):
        if left in given and right in given:
            raise QewError(f"--{left} and --{right} frame the same thing; use one", 2)
    return "\n\n".join(f"## {title}\n\n{given[key].strip()}" for key, title in _SECTIONS if key in given) + "\n"


def _body(arguments) -> str:
    structured = _structured(arguments)
    sources = [name for name, used in (("--body", arguments.body is not None), ("--body-file", bool(arguments.body_file)),
                                       ("--edit", arguments.edit), ("the structured flags", structured is not None)) if used]
    if len(sources) > 1:
        raise QewError(f"{' and '.join(sources)} each give the body; use one", 2)
    if structured is not None:
        return structured
    if arguments.edit:
        return editor.edit_body()
    if arguments.body_file:
        return editor.read_body_file(arguments.body_file) if arguments.body_file != "-" else sys.stdin.read()
    return arguments.body or ""


def _specs(arguments) -> List[dict]:
    if arguments.from_file:
        try:
            value = json.loads(read_text(arguments.from_file))
        except ValueError as error:
            raise QewError(f"--from-file is not valid JSON: {error}", 2) from None
        specs = value if isinstance(value, list) else [value]
        if not all(isinstance(spec, dict) for spec in specs):
            raise QewError("--from-file holds an object or an array of objects", 2)
        return specs
    if not arguments.title:
        raise QewError("--title is required", 2)
    return [{
        "kind": arguments.kind, "title": arguments.title, "parent": arguments.parent, "needs": arguments.needs,
        "labels": arguments.label, "priority": arguments.priority, "body": _body(arguments),
        "discovered_from": arguments.discovered_from,
    }]


def _create(config: Config, arguments) -> None:
    if arguments.draft and arguments.quote is not None:
        raise QewError("--draft files a draft and --quote releases the item: use one", 2)
    specs = _specs(arguments)
    rows = create_items(config, specs, arguments.quote, arguments.import_events)
    for spec in specs:
        _warn(str(spec.get("body", "")))
    if outcome.wants_json(arguments):
        print(json.dumps([{"schema_version": outcome.SCHEMA_VERSION, "id": i, "state": s} for i, s, _ in rows]))
    else:
        print("\n".join(f"{i} {s} {t}" for i, s, t in rows))


def _set(config: Config, arguments) -> None:
    if arguments.no_parent and arguments.parent is not None:
        raise QewError("--no-parent and --parent contradict each other", 2)
    if arguments.labels is not None and (arguments.add_label or arguments.remove_label):
        raise QewError("--labels replaces the set; --add-label and --remove-label edit it: use one way", 2)
    changes: Dict[str, str] = {}
    for key in ("title", "parent", "priority", "kind"):
        value = getattr(arguments, key)
        if value is not None:
            changes[key] = str(value)
    if arguments.no_parent:
        changes["parent"] = ""
    for key in ("needs", "labels"):
        value = getattr(arguments, key)
        if value is not None:
            changes[key] = " ".join(value)
    if sum([arguments.body is not None, bool(arguments.body_file), arguments.edit]) > 1:
        raise QewError("use only one of --body, --body-file and --edit", 2)
    body = editor.edit_body() if arguments.edit else (editor.read_body_file(arguments.body_file) if arguments.body_file else arguments.body)
    lines, changed = apply_set(config, arguments.items, changes, body, arguments.closed, arguments.add_label, arguments.remove_label)
    if body is not None:
        _warn(body)
    print(outcome.render(arguments, "\n".join(lines), ids=list(dict.fromkeys(arguments.items)), changed=changed))


def comment(config: Config, reference: str, body: Optional[str], body_file: Optional[str]) -> Dict[str, object]:
    if (body is None) == (not body_file):
        raise QewError("give the comment as exactly one of --body and --body-file", 2)
    text = body if body is not None else editor.read_body_file(body_file)
    if not text.strip():
        raise QewError("a comment must say something", 2)
    if len(text.encode("utf-8")) > MAX_COMMENT_BYTES:
        raise QewError(f"a comment is at most {MAX_COMMENT_BYTES} bytes", 2)
    ledger = ledger_for(config)
    item_id = ledger.resolve(reference, ledger.item_ids())
    ledger.append_event(item_id, "comment", text=text)
    _warn(text)
    return {"line": f"{item_id} commented", "id": item_id, "state": "commented"}


def edge(config: Config, reference: str, other: str, add: bool, closed: bool) -> Dict[str, object]:
    ledger = ledger_for(config)
    snapshot: Snapshot = ledger.snapshot()
    item_id = ledger.resolve(reference, list(snapshot.items))
    need = ledger.resolve(other, list(snapshot.items))
    state = snapshot.folds[item_id].state
    if state in _FINISHED and not closed:
        raise QewError(f"{item_id} is {state.value}; pass --closed to change its edges", 2)
    item = snapshot.items[item_id]
    word = ("blocked", "unblocked")[not add]
    if (need in item.needs) == add:
        return {"line": f"unchanged {item_id}", "id": item_id, "state": "unchanged", "need": need}
    needs = (*item.needs, need) if add else tuple(n for n in item.needs if n != need)
    if add:
        graph_rules.validate_edges({**snapshot.items, item_id: _with_needs(item, needs)}, [item_id])
    ledger.append_event(item_id, "edge", op="add" if add else "remove", need=need,
                        old=" ".join(item.needs), new=" ".join(needs))
    return {"line": f"{item_id} {word} {'on' if add else 'from'} {need}", "id": item_id, "state": word, "need": need}


def _with_needs(item, needs):
    from dataclasses import replace

    return replace(item, needs=tuple(needs))
