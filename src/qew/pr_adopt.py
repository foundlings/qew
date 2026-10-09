"""Adopting a pull request qew opened whose ledger record was never written (a crash after the forge accepted it).

Only an unrecorded pull request whose body carries this item's marker line is ever adopted: a stranger's pull
request, or another item's, on the same head is refused, never taken over. A pull request the ledger already
records for another item is that item's business and is left to the forge's own duplicate rule.
"""

import re

from qew.errors import QewError
from qew.forge import MARKER_PREFIX, Forge, RemotePullRequest, Unsupported, marker_alone, marker_for
from qew.ledger_store import Ledger

_MARKER = re.compile(rf"^<!-- {re.escape(MARKER_PREFIX)}(\S+) -->$", re.MULTILINE)


def find_adoptable(ledger: Ledger, target: str, forge: Forge, head: str, base: str, item_id: str) -> RemotePullRequest | None:
    """The open pull request on `head` that is this item's, or None when the head is free; refuses any other one.

    A forge that cannot list pull requests by head (`Unsupported`) has nothing to adopt: the open proceeds as before.
    """
    try:
        found = forge.find_open_pull_requests(head)
    except Unsupported:
        return None
    recorded = {m["number"] for host in ledger.item_ids() if host != item_id for m in ledger.load_mirrors(host)
                if m.get("kind") == "pr" and m["_target"].startswith(f"{target}-pr-") and isinstance(m.get("number"), int)}
    mine: list[RemotePullRequest] = []
    for pull in found:
        if pull.number in recorded:
            continue
        if marker_alone(pull.body, marker_for(item_id)):
            mine.append(pull)
            continue
        owner = _MARKER.search(pull.body)
        whose = f"belongs to {owner.group(1)}; open it for that item to adopt it" if owner else "carries no qew marker; close it on the forge first"
        raise QewError(f"an open pull request #{pull.number} already uses head {head} and {whose}", 2)
    if len(mine) > 1:
        raise QewError(f"{len(mine)} open pull requests ({', '.join('#' + str(p.number) for p in mine)}) already use head {head} for {item_id}; close all but one on the forge", 2)
    if mine and mine[0].base_branch != base:
        raise QewError(f"pull request #{mine[0].number} on head {head} targets {mine[0].base_branch}, not {base}; pass --base {mine[0].base_branch} to adopt it", 2)
    return mine[0] if mine else None
