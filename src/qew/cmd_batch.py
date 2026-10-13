"""`qew batch --manifest FILE`: many mutations of different kinds under one lock, one snapshot and one commit.

The manifest is JSON Lines of `{op, item, ...}` rows. Every row is parsed and its item resolved before anything is written; the
rows then run in file order through the same functions as the single commands, so each sees the state the row before it left.
One failing row undoes the whole file (the write session rolls back), and the error names the line.
"""

import argparse
import json
from collections.abc import Callable
from pathlib import Path

from qew import cmd_claim, cmd_comment, cmd_finish, cmd_items, ledger_config, replication, snapshot_reuse
from qew.errors import QewError
from qew.jsontypes import JsonObject
from qew.ledger_config import Config
from qew.ledger_store import Ledger

type Kind = str  # a field type: text, list or flag
type Runner = Callable[[Config, Ledger, str, JsonObject], None]

TEXT, LIST, FLAG, WHOLE = "text", "list", "flag", "number"


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    batch = commands.add_parser("batch", help="apply many start, evidence, complete, close, reopen, set and comment rows in one commit")
    batch.add_argument("--manifest", required=True, metavar="FILE", help="a JSON Lines file of {op, item, ...} rows, applied in order, all or nothing")
    batch.add_argument("--dry-run", action="store_true", help="run every row against the ledger and undo it: say what would be applied, write nothing")


def run(arguments: argparse.Namespace) -> bool:
    if arguments.command != "batch":
        return False
    config = ledger_config.load()
    ledger = cmd_items.ledger_for(config)
    with snapshot_reuse.reusing():
        rows = _read(ledger, Path(arguments.manifest))
        for op, item_id, fields, line in rows:
            try:
                OPS[op][1](config, ledger, item_id, fields)
            except QewError as error:
                raise QewError(f"line {line}: {op} {item_id}: {error}", error.exit_code) from None
    if arguments.dry_run:
        replication.discard_writes()
    print(f"{'would apply' if arguments.dry_run else 'applied'} {len(rows)} rows")
    return True


def _read(ledger: Ledger, manifest: Path) -> list[tuple[str, str, JsonObject, int]]:
    """(op, item id, the row's other fields, line number) for every row; any bad row refuses the file."""
    try:
        text = manifest.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise QewError(f"cannot read {manifest}: {error}", 2) from None
    known = list(ledger.snapshot().items)
    rows: list[tuple[str, str, JsonObject, int]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        where = f"line {number}"
        try:
            raw = json.loads(line)
        except ValueError as error:
            raise QewError(f"{where}: invalid JSON ({error})", 2) from None
        if not isinstance(raw, dict) or not isinstance(raw.get("op"), str) or not isinstance(raw.get("item"), str):
            raise QewError(f"{where}: a row is an object with an `op` and an `item`", 2)
        op = raw["op"]
        if op not in OPS:
            raise QewError(f"{where}: unknown op `{op}` (ops: {', '.join(OPS)})", 2)
        allowed = OPS[op][0]
        fields: JsonObject = {key: value for key, value in raw.items() if key not in ("op", "item")}
        unknown = sorted(set(fields) - set(allowed))
        if unknown:
            raise QewError(f"{where}: {op} has no field `{unknown[0]}` (fields: {', '.join(allowed) or 'none'})", 2)
        for key, value in fields.items():
            _check(where, op, key, value, allowed[key])
        try:
            item_id = ledger.resolve(raw["item"], known)
        except QewError as error:
            raise QewError(f"{where}: {error}", error.exit_code) from None
        rows.append((op, item_id, fields, number))
    if not rows:
        raise QewError(f"{manifest}: no rows", 2)
    return rows


def _check(where: str, op: str, key: str, value: object, kind: Kind) -> None:
    good = {
        TEXT: isinstance(value, str),
        LIST: isinstance(value, list) and all(isinstance(each, str) for each in value),
        FLAG: isinstance(value, bool),
        WHOLE: isinstance(value, int) and not isinstance(value, bool),
    }[kind]
    if not good:
        raise QewError(f"{where}: {op} `{key}` must be {'a list of text' if kind == LIST else 'true or false' if kind == FLAG else 'a whole number' if kind == WHOLE else 'text'}", 2)


def _text(fields: JsonObject, key: str) -> str | None:
    value = fields.get(key)
    return value if isinstance(value, str) else None


def _list(fields: JsonObject, key: str) -> list[str]:
    value = fields.get(key)
    return [each for each in value if isinstance(each, str)] if isinstance(value, list) else []


def _flag(fields: JsonObject, key: str) -> bool:
    return fields.get(key) is True


def _start(config: Config, ledger: Ledger, item_id: str, fields: JsonObject) -> None:
    cmd_claim.start(config, item_id, False, None, _text(fields, "lane"), _text(fields, "workspace"), _text(fields, "kind"), _flag(fields, "offline"))


def _evidence(config: Config, ledger: Ledger, item_id: str, fields: JsonObject) -> None:
    changes = _list(fields, "change_ids")
    if not changes:
        raise QewError("evidence needs `change_ids`", 2)
    cmd_finish.evidence(config, item_id, changes, None, _text(fields, "on_behalf_of"))


def _complete(config: Config, ledger: Ledger, item_id: str, fields: JsonObject) -> None:
    cmd_finish.complete(config, item_id, _text(fields, "reason"), _flag(fields, "take_over"), _text(fields, "no_commit"),
                        _text(fields, "landed_on"), _text(fields, "absent"))


def _close(config: Config, ledger: Ledger, item_id: str, fields: JsonObject) -> None:
    cmd_finish.close(config, item_id, _text(fields, "reason"), _text(fields, "of"), _text(fields, "by"), _text(fields, "note"),
                     _text(fields, "delivered_by"), _text(fields, "landed_on"))


def _reopen(config: Config, ledger: Ledger, item_id: str, fields: JsonObject) -> None:
    cmd_finish.reopen(config, item_id, _text(fields, "reason"))


def _comment(config: Config, ledger: Ledger, item_id: str, fields: JsonObject) -> None:
    text = _text(fields, "text")
    if text is None or not text.strip():
        raise QewError("a comment needs `text`", 2)
    cmd_comment.post(ledger, item_id, text, _text(fields, "key"))


def _set(config: Config, ledger: Ledger, item_id: str, fields: JsonObject) -> None:
    changes: dict[str, str] = {key: str(fields[key]) for key in ("title", "parent", "priority", "kind") if key in fields}
    body = _text(fields, "body")
    cmd_items.set_fields(config, [item_id], changes, body, _flag(fields, "closed"), _list(fields, "add_label"), _list(fields, "remove_label"),
                         _text(fields, "reason"))


OPS: dict[str, tuple[dict[str, Kind], Runner]] = {
    "start": ({"lane": TEXT, "workspace": TEXT, "kind": TEXT, "offline": FLAG}, _start),
    "evidence": ({"change_ids": LIST, "on_behalf_of": TEXT}, _evidence),
    "complete": ({"reason": TEXT, "absent": TEXT, "no_commit": TEXT, "landed_on": TEXT, "take_over": FLAG}, _complete),
    "close": ({"reason": TEXT, "of": TEXT, "by": TEXT, "note": TEXT, "delivered_by": TEXT, "landed_on": TEXT}, _close),
    "reopen": ({"reason": TEXT}, _reopen),
    "comment": ({"text": TEXT, "key": TEXT}, _comment),
    "set": ({"title": TEXT, "parent": TEXT, "priority": WHOLE, "kind": TEXT, "add_label": LIST, "remove_label": LIST, "body": TEXT,
             "closed": FLAG, "reason": TEXT}, _set),
}
