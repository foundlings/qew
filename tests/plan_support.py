"""Shared helpers for the plan-file, draft and graph walks."""

import json
from typing import Dict, List

from tests.ledger_harness import World


def snapshot(world: World) -> Dict[str, bytes]:
    """Every file of the ledger (cache excluded), to prove a refused command changed nothing."""
    return {
        str(path.relative_to(world.ledger)): path.read_bytes()
        for path in sorted(world.ledger.rglob("*"))
        if path.is_file() and ".qew" not in path.parts
    }


def jsonl(*entries: dict) -> str:
    return "\n".join(json.dumps(entry) for entry in entries) + "\n"


def listing(world: World) -> List[dict]:
    return json.loads(world.run("list", "--json").stdout)
