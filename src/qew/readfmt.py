"""The machine form of read commands: compact, sorted-key JSON, versioned.

Bounded answers are one envelope object; open-ended lists are one object per line (NDJSON), each
row carrying `schema_version`. Shapes are listed in docs/specs/commands.md.
"""

import json
from collections.abc import Iterable

from qew.jsontypes import JsonObject

SCHEMA_VERSION = 1


def dumps(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def envelope(items: list[JsonObject], total: int, truncated: bool) -> str:
    return dumps({"items": items, "total": total, "truncated": truncated, "schema_version": SCHEMA_VERSION})


def document(value: JsonObject) -> str:
    return dumps({**value, "schema_version": SCHEMA_VERSION})


def ndjson(rows: Iterable[JsonObject]) -> list[str]:
    return [dumps({**row, "schema_version": SCHEMA_VERSION}) for row in rows]
