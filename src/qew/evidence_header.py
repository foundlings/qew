"""What a `completed` event says about proof, as the item header shows it: change ids, or a typed absence.

Every path that completes work goes through `Ledger.append_event`, which asks this module for the header values, so no
completion can leave the item without its proof or its stated lack of one. Events stay the history; the header is what a
reader of the item sees.
"""

from qew.jsontypes import JsonObject

IMPORTED_REASON = "imported without resolvable evidence"
Header = tuple[tuple[str, ...], str]  # (change ids, absence `KIND: reason`)


def one_line(text: str) -> str:
    return " ".join(text.split())


def header_values(data: JsonObject) -> Header | None:
    """(evidence, evidence_absent) the completed event's data stands for, or None when it says nothing about proof."""
    cited = data.get("evidence")
    if isinstance(cited, list) and cited:
        return tuple(str(c) for c in cited), ""
    absent = data.get("absent")
    if isinstance(absent, str) and absent:
        return (), f"{absent}: {one_line(str(data.get('absent_reason', '')))}"
    if data.get("no_commit"):
        return (), f"no-code: {one_line(str(data['no_commit']))}"
    if data.get("children_finished") is not None:
        return (), f"children: {one_line(str(data.get('reason') or 'all children finished'))}"
    delivered = data.get("delivered_by")
    if isinstance(delivered, list) and delivered:
        return tuple(str(c) for c in delivered), ""
    if data.get("provenance") in ("import", "forge-import"):
        return (), f"historical: {one_line(str(data.get('reason') or IMPORTED_REASON))}"
    return None
