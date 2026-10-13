"""Who may hold an item: one authority decides, never timestamps.

With a primary forge the claim is a reservation there: qew posts a claim comment and re-reads the issue's
comments; the earliest claim comment still in force wins (the forge orders comments, not clocks). With
primary `none` the ledger remote arbitrates and `replication` settles the push (see `settle`).
"""

from enum import Enum
import re
import secrets
from typing import Any

from qew import forge_registry, ledger_config, sync_plan
from qew.errors import QewError
from qew.forge import Forge, ForgeError, RemoteComment
from qew.ledger_config import Config, Mirror, OfflineClaims
from qew.ledger_repo import LedgerRepo
from qew.ledger_store import Ledger, Snapshot

_MARK = re.compile(r"<!-- qew:(claim|release) ([A-Za-z0-9._-]+)(?: (\S+) (\S+))? -->")
DRY_RUN = False  # a dry run reads the primary and never writes it
POSTED: list[str] = []  # items whose won claim this command put on the primary: withdrawn if the command's ledger writes are undone
PENDING: list[tuple[str, str]] = []  # (item id, claimed event id) awaiting the ledger remote's verdict; one command may claim several


def claim_comment(item_id: str, owner: str, nonce: str) -> str:
    return f"<!-- qew:claim {item_id} {owner} {nonce} -->"


def release_comment(item_id: str) -> str:
    return f"<!-- qew:release {item_id} -->"


def holder(comments: list[RemoteComment], item_id: str) -> tuple[str, str] | None:
    """(owner, nonce) of the claim in force after replaying claim and release comments in order."""
    current: tuple[str, str] | None = None
    for comment in comments:
        found = _MARK.search(comment.body)
        if not found or found.group(2) != item_id:
            continue
        if found.group(1) == "claim" and current is None and found.group(3):
            current = (found.group(3), found.group(4))
        elif found.group(1) == "release":
            current = None
    return current


def _issue_record(config: Config, ledger: Ledger, mirror: Mirror | None, item_id: str, snapshot: Snapshot | None = None) -> Any:
    """The primary's issue record for the item. An item its mirror does not select (a task when the mirror takes epics and
    issues) has no issue by design and is reserved on the nearest ancestor's: the claim markers name the item id, so one
    issue carries the markers of every descendant. An item the mirror does select but has not synced yet has no record."""
    own = next((r for r in ledger.load_mirrors(item_id) if r["_target"] == config.authority and r.get("kind") == "issue"), None)
    if own is not None or mirror is None:
        return own
    snapshot = snapshot or ledger.snapshot()  # a caller that already read the ledger passes it: a read is the slow part
    if sync_plan.selects(snapshot, item_id, mirror.select):
        return None
    seen: set[str] = {item_id}
    cursor = snapshot.items[item_id].parent
    while cursor and cursor not in seen and cursor in snapshot.items:
        seen.add(cursor)
        record = next((r for r in ledger.load_mirrors(cursor) if r["_target"] == config.authority and r.get("kind") == "issue"), None)
        if record is not None:
            return record
        cursor = snapshot.items[cursor].parent
    return None


def _primary_forge(config: Config, ledger: Ledger, item_id: str, snapshot: Snapshot | None = None) -> tuple[Forge, Any]:
    mirror = next((m for m in config.mirrors if ledger_config.mirror_slug(m) == config.authority), None)
    record = _issue_record(config, ledger, mirror, item_id, snapshot)
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


def _provisional(ledger: Ledger, item_id: str, owner: str, lane: dict[str, str]) -> Claimed:
    ledger.append_event(item_id, "claimed", owner=owner, provisional=True, **lane)
    return Claimed.PROVISIONAL


def claim(config: Config, ledger: Ledger, item_id: str, owner: str, mode: ClaimMode = ClaimMode.ARBITRATED,
          snapshot: Snapshot | None = None, **lane: str) -> Claimed:
    """Write the claim, or refuse. The caller has already checked the item is approved and unblocked.

    `lane` carries the optional lane fields (lane, workspace, kind) of the `claimed` event. `ClaimMode.OFFLINE`
    skips the arbiter and writes a provisional claim; `[authority] offline_claims = "allow"` does the same only
    when the arbiter cannot be reached. `claim_reconcile` settles every provisional claim at the next sync.
    """
    if mode is ClaimMode.OFFLINE:
        return _provisional(ledger, item_id, owner, lane)
    if config.authority != "none":
        return _claim_on_primary(config, ledger, item_id, owner, lane, snapshot)
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


def _confirmed(ledger: Ledger, item_id: str, owner: str, lane: dict[str, str]) -> None:
    event = ledger.append_event(item_id, "claimed", owner=owner, **lane)
    ledger.append_event(item_id, "claim_confirmed", ref=event.id)


def _claim_on_primary(config: Config, ledger: Ledger, item_id: str, owner: str, lane: dict[str, str],
                      snapshot: Snapshot | None = None) -> Claimed:
    allowed = config.offline_claims is OfflineClaims.ALLOW
    try:
        forge, number = _primary_forge(config, ledger, item_id, snapshot)
    except QewError:
        if allowed:  # no issue there yet, or the forge cannot be built: work on, sync settles it
            return _provisional(ledger, item_id, owner, lane)
        raise
    if DRY_RUN:
        try:
            held = holder(forge.list_comments(number), item_id)
        except ForgeError as error:
            raise QewError(f"cannot check {item_id} on the primary forge ({error.detail})", 3) from None
        if held is not None:
            raise QewError(f"cannot claim {item_id}: already claimed by {held[0]} on the primary", 2)
        _confirmed(ledger, item_id, owner, lane)
        return Claimed.CONFIRMED
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
    POSTED.append(item_id)
    _confirmed(ledger, item_id, owner, lane)
    return Claimed.CONFIRMED


def stale_holder(config: Config, ledger: Ledger, item_id: str, snapshot: Snapshot | None = None) -> str | None:
    """Who holds the item on the primary forge, or None (also without a primary). The ledger is not consulted."""
    if config.authority == "none":
        return None
    forge, number = _primary_forge(config, ledger, item_id, snapshot)
    try:
        held = holder(forge.list_comments(number), item_id)
    except ForgeError as error:
        raise QewError(f"cannot read {item_id} on the primary forge ({error.detail})", 3) from None
    return held[0] if held else None


def withdraw_posted(config: Config, ledger: Ledger) -> list[str]:
    """Release the claims this command won on the primary because its ledger writes are being undone; the items it could not."""
    failed: list[str] = []
    for item_id in list(POSTED):
        try:
            release(config, ledger, item_id)
        except QewError:
            failed.append(item_id)
    POSTED.clear()
    return failed


def release(config: Config, ledger: Ledger, item_id: str, snapshot: Snapshot | None = None) -> None:
    """Free the reservation on the primary before the ledger forgets it; no-op without a primary."""
    if config.authority == "none":
        return
    forge, number = _primary_forge(config, ledger, item_id, snapshot)
    try:
        forge.create_comment(number, release_comment(item_id))
    except ForgeError as error:
        raise QewError(f"cannot release {item_id}: the primary forge is unreachable ({error.detail})", 3) from None


def take_pending() -> list[tuple[str, str]]:
    pending = list(PENDING)
    PENDING.clear()
    return pending
