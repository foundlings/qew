"""Build a ledger in one command from `create --from-file` specs, for walks that need exact ids."""

import json

from tests.ledger_harness import QUOTE, World


def load(world: World, *specs: dict, quote: bool = True) -> None:
    arguments = ["create", "--from-file", "-", *(["--quote", QUOTE] if quote else [])]
    result = world.run(*arguments, stdin=json.dumps(list(specs)))
    assert result.returncode == 0, result.stderr


def spec(item_id: str, title: str = "", kind: str = "issue", **fields) -> dict:
    return {"id": item_id, "kind": kind, "title": title or f"Work {item_id}", **fields}
