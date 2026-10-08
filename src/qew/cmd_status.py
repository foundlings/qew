"""`qew status`: replication state in a few fixed lines (see docs/specs/ledger.md, Replication)."""

import json
from typing import Optional

from qew import ledger_config
from qew.cmd_items import ledger_for
from qew.cmd_sync import mirrors_with_boards, pending_by_target
from qew.ledger_config import Config
from qew.ledger_repo import LedgerRepo


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


def _contested(item_id: str, claim, holder: str) -> dict:
    """A second effective claim in a merged ledger: the holder by event order against the contender."""
    return {"id": claim.id, "item": item_id, "target": "ledger", "field": "owner", "forge": claim.get("owner"), "ledger": holder}


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
            {"target": slug, "pending": len(rows), "last_sync": last_sync(ledger, slug, ids)}
            for slug, rows in pending.items()
        ],
        "divergences": divergences,
    }


def render(data: dict) -> str:
    ledger = data["ledger"]
    lines = [f"authority primary={data['authority']['primary']}"]
    lines += [f"ledger uncommitted={ledger['uncommitted']} unpushed={ledger['unpushed']} mode={ledger['mode']} remote={ledger['remote']}"]
    for target in data["targets"]:
        lines.append(f"target {target['target']} pending={target['pending']} synced={target['last_sync'] or 'never'}")
    lines.append(f"divergences={len(data['divergences'])}")
    for d in data["divergences"]:
        lines.append(f"divergence {d['id']} {d['item']} {d['field']} forge={d['forge']} ledger={d['ledger']}")
    return "\n".join(lines)
