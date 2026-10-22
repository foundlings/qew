"""`qew status`: replication state in a few fixed lines (see docs/specs/ledger.md, Replication)."""

import argparse
import json
import time
from collections.abc import Sequence

from qew import claim_reconcile, instants, ledger_config, ledger_conflicts, pull_cursor, syncworker
from qew.pacing import PaceState
from qew.cmd_items import ledger_for
from qew.cmd_sync import mirrors_with_boards, pending_by_target
from qew.jsontypes import JsonObject
from qew.ledger_config import Config, Mirror
from qew.ledger_event import Event
from qew.ledger_repo import ROUTINE_CONFLICT, LedgerRepo
from qew.ledger_store import Ledger

SHOWN_DIVERGENCES = 10


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    status = commands.add_parser("status", help="replication state: uncommitted, unpushed, pending per target")
    status.add_argument("--json", action="store_true", help="machine-readable output")


def run(arguments: argparse.Namespace) -> bool:
    if arguments.command != "status":
        return False
    config = ledger_config.load()
    data = collect(config)
    print(json.dumps(data) if arguments.json else render(data))
    return True


def remote_state(config: Config) -> str:
    """The outcome of the last fetch or push attempt, `none` without a remote."""
    repo = LedgerRepo(config.ledger_path, config.replication.branch)
    if not repo.has_remote():
        return "none"
    marker = config.ledger_path / ".qew" / "remote-state"
    return marker.read_text(encoding="utf-8").strip() if marker.exists() else "ok"


def remote_error(config: Config) -> str | None:
    """Why the last fetch or push failed (a timeout names `timeout_s`), or None when it did not."""
    note = config.ledger_path / ".qew" / "remote-error"
    return note.read_text(encoding="utf-8").strip() or None if note.exists() and remote_state(config) == "offline" else None


def last_sync(ledger: Ledger, slug: str, item_ids: Sequence[str]) -> str | None:
    stamps = [m.get("synced_at", "") for item_id in item_ids for m in ledger.load_mirrors(item_id) if m["_target"] == slug]
    return max(stamps, default="") or None


def pull_state(ledger: Ledger, slug: str) -> JsonObject:
    """When the target was last pulled, when it last saw the complete issue set, and how the next pull will list."""
    cursor = pull_cursor.parse(ledger.read_cursor(slug))
    if cursor is None:
        return {"last_pull": None, "last_full": None, "next_pull": "full"}
    return {"last_pull": instants.format_instant(cursor.pulled_at), "last_full": instants.format_instant(cursor.full_at),
            "next_pull": pull_cursor.next_mode(cursor, instants.now())}


def _contested(item_id: str, claim: Event, holder: str) -> JsonObject:
    """A second effective claim in a merged ledger: the holder by event order against the contender."""
    return {"id": claim.id, "item": item_id, "target": "ledger", "field": "owner", "forge": claim.get("owner"), "ledger": holder}


def repos_on(mirrors: list[Mirror], slug: str) -> bool:
    """Whether the target pushes the code and ledger repositories too (git_url / ledger_git_url set)."""
    mirror = next(m for m in mirrors if ledger_config.mirror_slug(m) == slug)
    return mirror.options.get("git_url") is not None or mirror.options.get("ledger_git_url") is not None


def collect(config: Config) -> JsonObject:
    ledger = ledger_for(config)
    repo = LedgerRepo(config.ledger_path, config.replication.branch)
    mirrors = mirrors_with_boards(ledger, config)
    pending = pending_by_target(ledger, mirrors)
    ids = ledger.item_ids()
    snapshot = ledger.snapshot()
    divergences = [
        _contested(item_id, e, snapshot.folds[item_id].owner) if e.type == "claimed" else
        {"id": e.id, "item": item_id, "target": e.get("target"), "field": e.get("field"),
         "forge": e.get("forge"), "ledger": e.get("ledger")}
        for item_id in snapshot.items for e in snapshot.folds[item_id].divergences
    ]
    return {
        "authority": {"primary": config.authority},
        "ledger": {
            "uncommitted": len(repo.dirty_owned()),
            "unpushed": repo.unpushed(),
            "mode": "offline" if config.replication.offline else "online",
            "remote": remote_state(config),
            "remote_error": remote_error(config),
        },
        "targets": [
            {"target": slug, "pending": sum(1 for action, _id in rows if action != "baseline"),
             "unbaselined": sum(1 for action, _id in rows if action == "baseline"), "last_sync": last_sync(ledger, slug, ids), "repos": repos_on(mirrors, slug),
             "pace": _pace(ledger, slug), **pull_state(ledger, slug)}
            for slug, rows in pending.items()
        ],
        "claims": {
            "provisional": [{"item": item_id, "owner": event.get("owner")} for item_id, event in claim_reconcile.provisional_claims(snapshot)],
            "lost": [{"item": item_id, "winner": winner} for item_id, winner in claim_reconcile.lost_provisional_claims(snapshot)],
        },
        "divergences": divergences,
        "conflicts": len(ledger_conflicts.collect(config, snapshot)),
        "sync": {"retry_after": max(0, int(syncworker.retry_at(config) - time.time()))},
    }


def _pace(ledger: Ledger, slug: str) -> JsonObject | None:
    """The create pace a secondary-limit block taught for this target, or None."""
    window = PaceState(ledger.pace_dir(slug)).window()
    return None if window is None else {"limit": window[0], "per_seconds": int(window[1])}


def unbaselined(target: JsonObject) -> str:
    """` unbaselined=N` when this checkout has no sync state for N mirrored items: the first sync records it, writing nothing."""
    return f" unbaselined={target['unbaselined']}" if target["unbaselined"] else ""


def render(data: JsonObject) -> str:
    ledger = data["ledger"]
    lines = [f"authority primary={data['authority']['primary']}"]
    lines += [f"ledger uncommitted={ledger['uncommitted']} unpushed={ledger['unpushed']} mode={ledger['mode']} remote={ledger['remote']}"]
    if ledger["remote_error"]:
        lines.append(f"ledger remote error: {ledger['remote_error']}")
    for target in data["targets"]:
        lines.append(f"target {target['target']} pending={target['pending']}{unbaselined(target)} synced={target['last_sync'] or 'never'}"
                     f" repos={'on' if target['repos'] else 'off'}"
                     f" pulled={target['last_pull'] or 'never'} next_pull={target['next_pull']} full_set={target['last_full'] or 'never'}")
    for target in data["targets"]:
        if target["pace"]:
            lines.append(f"pace {target['target']} limit={target['pace']['limit']} per={target['pace']['per_seconds']}s"
                         f" (learned from a block; delete .qew/state/{target['target']}/pace.json to reset)")
    if data["sync"]["retry_after"]:
        lines.append(f"sync retry_after={data['sync']['retry_after']}")
    for claim in data["claims"]["provisional"]:
        lines.append(f"claim {claim['item']} provisional owner={claim['owner']} (offline; `qew push` settles it)")
    for claim in data["claims"]["lost"]:
        lines.append(f"claim {claim['item']} lost winner={claim['winner']} (offline claim dropped; talk to the holder)")
    lines.append(f"divergences={len(data['divergences'])}")
    if data["conflicts"]:
        lines.append(f"conflicts={data['conflicts']}: {ROUTINE_CONFLICT}")
    shown = data["divergences"][:SHOWN_DIVERGENCES]
    for d in shown:
        lines.append(f"divergence {d['id']} {d['item']} {d['field']} forge={d['forge']} ledger={d['ledger']}")
    hidden = len(data["divergences"]) - len(shown)
    if hidden > 0:
        lines.append(f"... {hidden} more (qew status --json lists all)")
    return "\n".join(lines)
