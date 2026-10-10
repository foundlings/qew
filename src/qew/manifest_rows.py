"""Read a JSON Lines manifest: one object per line, each naming an `item`, checked line by line.

Shared by the bulk commands (`qew set --manifest`, `qew evidence --manifest`): every line is parsed and its item resolved
before anything is written, and an error names the line.
"""

from collections.abc import Sequence
from pathlib import Path

import json

from qew.errors import QewError
from qew.jsontypes import JsonObject
from qew.ledger_store import Ledger


def read(ledger: Ledger, manifest: Path, known: Sequence[str], allowed: set[str]) -> list[tuple[str, JsonObject, str]]:
    """(item id, the row without `item`, "line N") for every line; unknown fields, bad JSON or an unknown item refuse all."""
    try:
        text = manifest.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise QewError(f"cannot read {manifest}: {error}", 2) from None
    rows: list[tuple[str, JsonObject, str]] = []
    seen: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        where = f"line {number}"
        try:
            raw = json.loads(line)
        except ValueError as error:
            raise QewError(f"{where}: invalid JSON ({error})", 2) from None
        if not isinstance(raw, dict) or not isinstance(raw.get("item"), str):
            raise QewError(f"{where}: a row is an object with an `item`", 2)
        unknown = sorted(set(raw) - allowed - {"item"})
        if unknown:
            raise QewError(f"{where}: unknown field `{unknown[0]}` (fields: item, {', '.join(sorted(allowed))})", 2)
        try:
            item_id = ledger.resolve(raw["item"], list(known))
        except QewError as error:
            raise QewError(f"{where}: {error}", error.exit_code) from None
        if item_id in seen:
            raise QewError(f"{where}: {item_id} is already named on {seen[item_id]}", 2)
        seen[item_id] = where
        rows.append((item_id, {k: v for k, v in raw.items() if k != "item"}, where))
    if not rows:
        raise QewError(f"{manifest}: no rows", 2)
    return rows
