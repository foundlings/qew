"""Who may hold an item: one authority decides, never timestamps.

With a primary forge the claim is a reservation there: qew posts a claim comment and re-reads the issue's
comments; the earliest claim comment still in force wins (the forge orders comments, not clocks). With
primary `none` the ledger remote arbitrates and `replication` settles the push (see `settle`).
"""

from enum import Enum
import re
import secrets
from typing import List, Optional, Tuple

from qew import forge_registry, ledger_config
from qew.errors import QewError
from qew.forge import ForgeError, RemoteComment
from qew.ledger_config import Config, OfflineClaims
from qew.ledger_repo import LedgerRepo
from qew.ledger_store import Ledger

_MARK = re.compile(r"<!-- qew:(claim|release) ([A-Za-z0-9._-]+)(?: (\S+) (\S+))? -->")
PENDING: List[Tuple[str, str]] = []  # (item id, claimed event id) awaiting the ledger remote's verdict; one command may claim several


def claim_comment(item_id: str, owner: str, nonce: str) -> str:
    return f"<!-- qew:claim {item_id} {owner} {nonce} -->"


def release_comment(item_id: str) -> str:
    return f"<!-- qew:release {item_id} -->"


def holder(comments: List[RemoteComment], item_id: str) -> Optional[Tuple[str, str]]:
    """(owner, nonce) of the claim in force after replaying claim and release comments in order."""
    current: Optional[Tuple[str, str]] = None
    for comment in comments:
        found = _MARK.search(comment.body)
        if not found or found.group(2) != item_id:
            continue
        if found.group(1) == "claim" and current is None and found.group(3):
            current = (found.group(3), found.group(4))
        elif found.group(1) == "release":
            current = None
    return current


def _primary_forge(config: Config, ledger: Ledger, item_id: str):
    mirror = next((m for m in config.mirrors if ledger_config.mirror_slug(m) == config.authority), None)
    record = next((r for r in ledger.load_mirrors(item_id)
                   if r["_target"] == config.authority and r.get("kind") == "issue"), None)
    if mirror is None or record is None:
        raise QewError(f"cannot claim {item_id}: the primary forge has no issue for it yet; run `qew sync` first", 2)
    try:
        return forge_registry.build(mirror), record["number"]
    except ForgeError as error:
        raise QewError(f"cannot claim {item_id}: the primary forge is unavailable ({error.detail})", 3) from None


class ClaimMode(Enum):
    """How a claim is taken: through the arbiter, or by the explicit offline override (`start --offline`)."""
    ARBITRATED = "arbitrated"
    OFFLINE = "offline"


class Claimed(Enum):
    """What `claim` achieved: a claim the arbiter confirmed, or a provisional one that sync settles later."""
    CONFIRMED = "confirmed"
    PROVISIONAL = "provisional"


def _provisional(ledger: Ledger, item_id: str, owner: str, lane: dict) -> Claimed:
    ledger.append_event(item_id, "claimed", owner=owner, provisional=True, **lane)
    return Claimed.PROVISIONAL


def claim(config: Config, ledger: Ledger, item_id: str, owner: str, mode: ClaimMode = ClaimMode.ARBITRATED,
          **lane: str) -> Claimed:
    """Write the claim, or refuse. The caller has already checked the item is approved and unblocked.

    `lane` carries the optional lane fields (lane, workspace, kind) of the `claimed` event. `ClaimMode.OFFLINE`
    skips the arbiter and writes a provisional claim; `[authority] offline_claims = "allow"` does the same only
    when the arbiter cannot be reached. `claim_reconcile` settles every provisional claim at the next sync.
    """
    if mode is ClaimMode.OFFLINE:
        return _provisional(ledger, item_id, owner, lane)
    if config.authority != "none":
        return _claim_on_primary(config, ledger, item_id, owner, lane)
    repo = LedgerRepo(config.ledger_path, config.replication.branch)
    rep = config.replication
    if repo.vcs is None or not repo.has_remote() or not rep.auto_push:
        ledger.append_event(item_id, "claimed", owner=owner, **lane)  # single writer: nothing to arbitrate, nothing to confirm
        return Claimed.CONFIRMED
    marker = config.ledger_path / ".qew" / "remote-state"
    if rep.offline or (marker.exists() and marker.read_text(encoding="utf-8").strip() == "offline"):
        if config.offline_claims is OfflineClaims.ALLOW:
            return _provisional(ledger, item_id, owner, lane)
        why = _remote_error(config)
        raise QewError(f"cannot claim {item_id}: the ledger remote arbitrates claims and is unreachable"
                       f"{f' ({why})' if why else ''} "
                       f"(`qew start {item_id} --offline` takes a provisional claim that sync settles)", 3)
    event = ledger.append_event(item_id, "claimed", owner=owner, **lane)
    PENDING.append((item_id, event.id))
    return Claimed.CONFIRMED


def _remote_error(config: Config) -> str:
    try:
        return (config.ledger_path / ".qew" / "remote-error").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _confirmed(ledger: Ledger, item_id: str, owner: str, lane: dict) -> None:
    event = ledger.append_event(item_id, "claimed", owner=owner, **lane)
    ledger.append_event(item_id, "claim_confirmed", ref=event.id)


def _claim_on_primary(config: Config, ledger: Ledger, item_id: str, owner: str, lane: dict) -> Claimed:
    allowed = config.offline_claims is OfflineClaims.ALLOW
    try:
        forge, number = _primary_forge(config, ledger, item_id)
    except QewError:
        if allowed:  # no issue there yet, or the forge cannot be built: work on, sync settles it
            return _provisional(ledger, item_id, owner, lane)
        raise
    nonce = secrets.token_hex(4)
    try:
        forge.create_comment(number, claim_comment(item_id, owner, nonce))
        winner = holder(forge.list_comments(number), item_id)
    except ForgeError as error:
        if allowed:
            return _provisional(ledger, item_id, owner, lane)
        raise QewError(f"cannot claim {item_id}: claims need the primary forge online ({error.detail}); "
                       f"`qew start {item_id} --offline` takes a provisional claim that sync settles", 3) from None
    if winner is None or winner[1] != nonce:
        who = winner[0] if winner else "someone else"
        raise QewError(f"cannot claim {item_id}: already claimed by {who} on the primary", 2)
    _confirmed(ledger, item_id, owner, lane)
    return Claimed.CONFIRMED


def release(config: Config, ledger: Ledger, item_id: str) -> None:
    """Free the reservation on the primary before the ledger forgets it; no-op without a primary."""
    if config.authority == "none":
        return
    forge, number = _primary_forge(config, ledger, item_id)
    try:
        forge.create_comment(number, release_comment(item_id))
    except ForgeError as error:
        raise QewError(f"cannot release {item_id}: the primary forge is unreachable ({error.detail})", 3) from None


def take_pending() -> List[Tuple[str, str]]:
    pending = list(PENDING)
    PENDING.clear()
    return pending
