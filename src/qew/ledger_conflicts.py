"""Every open sync conflict, as data an agent can list and settle (`qew conflicts`, `qew resolve`).

Three kinds need a decision: a ledger merge conflict (two checkouts changed the same ledger file), a forge
divergence (the forge changed something the ledger did not) and a claim contest (two effective claims).
The rest settle themselves and are not listed: a rejected push is retried after a fetch, a duplicate forge
issue is closed by the pull, a lost claim is final, mirror-only merge conflicts take the remote copy.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple

from qew import ledger_item
from qew.errors import QewError
from qew.ledger_config import Config
from qew.ledger_repo import MAX_DISCARDED_CHARS, LedgerRepo, MergeConflict, OWNED
from qew.ledger_store import Snapshot

LEDGER_REFERENCE = "ledger"  # the reference that names the ledger merge conflict
SHOWN_CHARS = 200


class ConflictKind(str, Enum):
    LEDGER_MERGE = "ledger_merge"
    DIVERGENCE = "divergence"
    CLAIM_CONTEST = "claim_contest"


class Side(str, Enum):
    OURS = "ours"
    THEIRS = "theirs"
    FORGE = "forge"
    LEDGER = "ledger"


SIDES: Dict[ConflictKind, Tuple[Side, ...]] = {
    ConflictKind.LEDGER_MERGE: (Side.OURS, Side.THEIRS),
    ConflictKind.DIVERGENCE: (Side.FORGE, Side.LEDGER),
    ConflictKind.CLAIM_CONTEST: (Side.FORGE, Side.LEDGER),
}


@dataclass(frozen=True)
class Difference:
    file: str
    key: str
    ours: str
    theirs: str


@dataclass(frozen=True)
class Conflict:
    kind: ConflictKind
    id: str  # the reference `qew resolve` takes
    item: str
    target: str
    field: str
    ledger: str
    forge: str
    files: Tuple[str, ...] = ()
    differences: Tuple[Difference, ...] = ()
    resolvable: bool = True

    def commands(self) -> List[str]:
        if not self.resolvable:
            return []
        return [f"qew resolve {self.id} --take {side.value} --reason TEXT" for side in SIDES[self.kind]]

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value, "id": self.id, "item": self.item, "target": self.target, "field": self.field,
            "ledger": self.ledger, "forge": self.forge, "files": list(self.files), "resolvable": self.resolvable,
            "differences": [{"file": d.file, "key": d.key, "ours": d.ours, "theirs": d.theirs} for d in self.differences],
            "resolve": self.commands(),
        }

    def render(self) -> str:
        if self.kind == ConflictKind.LEDGER_MERGE:
            lines = [f"{self.kind.value} {self.id}: {', '.join(self.files)}"]
            lines += [f"  {d.file} {d.key}: ours={d.ours!r} theirs={d.theirs!r}" for d in self.differences]
            if not self.resolvable:
                lines.append("  not settled by qew: a file outside the ledger's own directories conflicts; resolve it by hand")
        else:
            lines = [f"{self.kind.value} {self.id} {self.item} {self.field}: forge={self.forge} ledger={self.ledger}"]
        lines += [f"  {command}" for command in self.commands()]
        return "\n".join(lines)


def divergences(snapshot: Snapshot) -> List[Conflict]:
    found = []
    for item_id in sorted(snapshot.items):
        fold = snapshot.folds[item_id]
        for event in fold.divergences:
            if event.type == "claimed":  # a second effective claim: the holder by event order against the contender
                found.append(Conflict(ConflictKind.CLAIM_CONTEST, event.id, item_id, "ledger", "owner",
                                      fold.owner or "", event.get("owner") or ""))
            else:
                found.append(Conflict(ConflictKind.DIVERGENCE, event.id, item_id, event.get("target") or "",
                                      event.get("field") or "", str(event.get("ledger") or ""), str(event.get("forge") or "")))
    return found


def _clip(text: Optional[str]) -> str:
    return "(absent)" if text is None else text[:SHOWN_CHARS]


def _differences(repo: LedgerRepo, pending: MergeConflict) -> Tuple[Difference, ...]:
    found: List[Difference] = []
    for relative in pending.files:
        ours, theirs = repo.file_at(pending.ours, relative), repo.file_at(pending.theirs, relative)
        try:
            if ours is None or theirs is None or not relative.startswith("items/"):
                raise QewError("not an item file", 1)
            left, right = ledger_item.parse_item(ours, relative), ledger_item.parse_item(theirs, relative)
        except QewError:
            found.append(Difference(relative, "(file)", _clip(ours), _clip(theirs)))
            continue
        before, after = left.header(), right.header()
        found += [Difference(relative, key, before[key], after[key]) for key in before if before[key] != after[key]]
        if left.body != right.body:
            found.append(Difference(relative, "body", _clip(left.body), _clip(right.body)))
    return tuple(found)


def merge_conflict(config: Config) -> Optional[Conflict]:
    repo = LedgerRepo(config.ledger_path, config.replication.branch)
    pending = repo.pending_conflict()
    if pending is None:
        return None
    owned = all(relative.split("/")[0] in OWNED for relative in pending.files)
    item = next((relative[len("items/"):-len(".md")] for relative in pending.files
                 if relative.startswith("items/") and relative.endswith(".md")), "")
    return Conflict(ConflictKind.LEDGER_MERGE, LEDGER_REFERENCE, item, "ledger", "files", "", "", tuple(pending.files),
                    _differences(repo, pending), owned)


def collect(config: Config, snapshot: Snapshot) -> List[Conflict]:
    """The ledger merge conflict first (it blocks every write), then divergences and contests."""
    merge = merge_conflict(config)
    return ([merge] if merge else []) + divergences(snapshot)


def clipped_discard(text: Optional[str]) -> Optional[str]:
    """The side that was not kept, bounded; the full text stays in the merge's other parent commit."""
    if text is None or len(text) <= MAX_DISCARDED_CHARS:
        return text
    return text[:MAX_DISCARDED_CHARS] + "\n(truncated; the full text is in the merge's other parent commit)"
