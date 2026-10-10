"""Recording a forge-side change the ledger had not made: one open `forge_divergence` event per field and forge value."""

from qew.ledger_store import Ledger, Snapshot


def already_open(snapshot: Snapshot, item_id: str, field_name: str, forge_value: str) -> bool:
    return any(e.get("field") == field_name and e.get("forge") == forge_value for e in snapshot.folds[item_id].divergences)


def record(ledger: Ledger, snapshot: Snapshot, slug: str, item_id: str, number: int, field_name: str, forge_value: str,
           ledger_value: str, close_reason: str = "") -> bool:
    """Append the divergence unless the same one is already open; True when one was appended."""
    if already_open(snapshot, item_id, field_name, forge_value):
        return False
    extra = {"forge_reason": close_reason} if close_reason else {}
    ledger.append_event(item_id, "forge_divergence", target=slug, field=field_name, forge=forge_value,
                        ledger=ledger_value, remote_ref=str(number), provenance="forge", **extra)
    return True
