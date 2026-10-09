"""Ledger identity and lineage: `ledger_id` and `forked_from` in `qew-ledger.toml`.

A fork is a new ledger identity. Ledgers with different identities never merge (a fork is not merged back into
its origin, nor the origin into the fork); a ledger without an id only merges with one without an id.
"""

import tomllib

from qew.errors import QewError


def parse(text: str, where: str = "qew-ledger.toml") -> tuple[str, str]:
    """(ledger_id, forked_from), each empty when absent."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise QewError(f"{where}: {error}", 1) from None
    found = []
    for key in ("ledger_id", "forked_from"):
        value = data.get(key, "")
        if not isinstance(value, str):
            raise QewError(f"{where}: {key} must be a string", 1)
        found.append(value)
    return found[0], found[1]


def refuse_foreign_merge(local_text: str, remote_text: str, remote: str) -> None:
    """Raise when the remote ledger is a different identity than this one."""
    local_id, local_from = parse(local_text)
    remote_id, remote_from = parse(remote_text, f"{remote}:qew-ledger.toml")
    if local_id == remote_id:
        return
    lineage = remote_from or local_from
    raise QewError(
        f"refusing to merge {remote}: it is a different ledger (ledger_id `{remote_id or '-'}`, this one `{local_id or '-'}`"
        f"{', forked_from ' + lineage if lineage else ''}); a fork is never merged back into its origin",
        1,
    )
