"""`qew status`: replication state in a few fixed lines (see docs/specs/ledger.md, Replication)."""

import json
import time
from typing import Optional

from qew import instants, ledger_config, ledger_conflicts, pull_cursor, syncworker
from qew.cmd_items import ledger_for
from qew.cmd_sync import mirrors_with_boards, pending_by_target
from qew.ledger_config import Config
from qew.ledger_repo import LedgerRepo

SHOWN_DIVERGENCES = 10


def register(commands) -> None:
    status = commands.add_parser("status", help="replication state: uncommitted, unpushed, pending per target")
    status.add_argument("--json", action="store_true", help="machine-readable output")


def run(arguments) -> bool:
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


def last_sync(ledger, slug: str, item_ids) -> Optional[str]:
    stamps = [m.get("synced_at", "") for item_id in item_ids for m in ledger.load_mirrors(item_id) if m["_target"] == slug]
    return max(stamps, default="") or None


def pull_state(ledger, slug: str) -> dict:
    """When the target was last pulled, when it last saw the complete issue set, and how the next pull will list."""
    cursor = pull_cursor.parse(ledger.read_cursor(slug))
    if cursor is None:
        return {"last_pull": None, "last_full": None, "next_pull": "full"}
    return {"last_pull": instants.format_instant(cursor.pulled_at), "last_full": instants.format_instant(cursor.full_at),
            "next_pull": pull_cursor.next_mode(cursor, instants.now())}


def _contested(item_id: str, claim, holder: str) -> dict:
    """A second effective claim in a merged ledger: the holder by event order against the contender."""
    return {"id": claim.id, "item": item_id, "target": "ledger", "field": "owner", "forge": claim.get("owner"), "ledger": holder}


def repos_on(mirrors, slug: str) -> bool:
    """Whether the target pushes the code and ledger repositories too (git_url / ledger_git_url set)."""
    mirror = next(m for m in mirrors if ledger_config.mirror_slug(m) == slug)
    return mirror.options.get("git_url") is not None or mirror.options.get("ledger_git_url") is not None


def collect(config: Config) -> dict:
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
        },
        "targets": [
            {"target": slug, "pending": len(rows), "last_sync": last_sync(ledger, slug, ids), "repos": repos_on(mirrors, slug),
             **pull_state(ledger, slug)}
            for slug, rows in pending.items()
        ],
        "divergences": divergences,
        "conflicts": len(ledger_conflicts.collect(config, snapshot)),
        "sync": {"retry_after": max(0, int(syncworker.retry_at(config) - time.time()))},
    }


def render(data: dict) -> str:
    ledger = data["ledger"]
    lines = [f"authority primary={data['authority']['primary']}"]
    lines += [f"ledger uncommitted={ledger['uncommitted']} unpushed={ledger['unpushed']} mode={ledger['mode']} remote={ledger['remote']}"]
    for target in data["targets"]:
        lines.append(f"target {target['target']} pending={target['pending']} synced={target['last_sync'] or 'never'}"
                     f" repos={'on' if target['repos'] else 'off'}"
                     f" pulled={target['last_pull'] or 'never'} next_pull={target['next_pull']} full_set={target['last_full'] or 'never'}")
    if data["sync"]["retry_after"]:
        lines.append(f"sync retry_after={data['sync']['retry_after']}")
    lines.append(f"divergences={len(data['divergences'])}")
    if data["conflicts"]:
        lines.append(f"conflicts={data['conflicts']}: run `qew conflicts`, then `qew resolve REF --take SIDE --reason TEXT`")
    shown = data["divergences"][:SHOWN_DIVERGENCES]
    for d in shown:
        lines.append(f"divergence {d['id']} {d['item']} {d['field']} forge={d['forge']} ledger={d['ledger']}")
    hidden = len(data["divergences"]) - len(shown)
    if hidden > 0:
        lines.append(f"... {hidden} more (qew status --json lists all)")
    return "\n".join(lines)
