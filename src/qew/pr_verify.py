"""Verify a forge write by reading the pull request again, instead of trusting the call's answer."""

from enum import Enum
import time
from typing import Callable, Optional, Sequence

from qew.forge import RemotePullRequest


class Outcome(str, Enum):
    VERIFIED = "verified"  # the write was needed and a later read shows it
    UNCHANGED = "unchanged"  # the read already showed the wanted state; nothing was written
    NOT_APPLIED = "not_applied"  # the write was accepted yet every read still shows the old state


def apply_and_verify(
    before: RemotePullRequest,
    write: Callable[[], object],
    read: Callable[[], RemotePullRequest],
    applied: Callable[[RemotePullRequest], bool],
    delays: Sequence[float] = (),
    sleep: Optional[Callable[[float], None]] = None,
) -> Outcome:
    """Write unless `before` already satisfies `applied`; then read once, and once more after each delay."""
    if applied(before):
        return Outcome.UNCHANGED
    write()
    if applied(read()):
        return Outcome.VERIFIED
    for delay in delays:
        (sleep or time.sleep)(delay)
        if applied(read()):
            return Outcome.VERIFIED
    return Outcome.NOT_APPLIED
