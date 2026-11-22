"""Where the forge calls go: one line per command in `.qew/forge-calls.log`, added up by `qew doctor`.

Kept apart from `forge_http` so a command that never calls a forge does not import any HTTP code to be accounted: at exit this looks
at `forge_http` only if the command loaded it.
"""

import atexit
import json
import os
import sys
import time
from pathlib import Path

CALLS_LOG = "forge-calls.log"
CALLS_KEEP_S = 2 * 3600.0
CALLS_TRIM_BYTES = 256 * 1024
_verb: list[str] = []


def account(verb: str) -> None:
    """Log this process's forge calls under `verb` when it exits (once, and only when it made any)."""
    if not _verb:
        atexit.register(_write_account)
    _verb[:] = [verb]


def _ledger_store() -> Path | None:
    from qew import ledger_config
    from qew.errors import QewError

    try:
        return ledger_config.load().ledger_path / ".qew"
    except QewError:
        return None


def _write_account() -> None:
    http = sys.modules.get("qew.forge_http")
    if http is None or not _verb:
        return
    calls = {name: watch.calls for name, watch in http._WATCHES.items() if watch.calls}
    store = _ledger_store() if calls else None
    if store is None or not store.is_dir():
        return
    path = store / CALLS_LOG
    line = json.dumps({"at": round(time.time(), 1), "pid": os.getpid(), "verb": _verb[0], "calls": calls}, separators=(",", ":")) + "\n"
    try:
        if path.exists() and path.stat().st_size > CALLS_TRIM_BYTES:
            recent = [row for row in path.read_text(encoding="utf-8").splitlines(keepends=True) if _fresh(row)]
            path.write_text("".join(recent), encoding="utf-8")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line)  # one short append: concurrent commands do not interleave inside a line
    except OSError:
        return


def _fresh(row: str, within: float = CALLS_KEEP_S) -> bool:
    try:
        return float(json.loads(row)["at"]) > time.time() - within
    except (ValueError, KeyError, TypeError):
        return False


def calls_in_the_last_hour(store: Path) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    """(calls by resource, calls by verb and resource) of the commands logged in the last hour."""
    totals: dict[str, int] = {}
    by_verb: dict[str, dict[str, int]] = {}
    try:
        rows = (store / CALLS_LOG).read_text(encoding="utf-8").splitlines()
    except OSError:
        return totals, by_verb
    for row in rows:
        if not _fresh(row, 3600.0):
            continue
        entry = json.loads(row)
        for resource, count in entry["calls"].items():
            totals[resource] = totals.get(resource, 0) + int(count)
            verb = by_verb.setdefault(str(entry["verb"]), {})
            verb[resource] = verb.get(resource, 0) + int(count)
    return totals, by_verb
