"""The quota check before a merge or a union: a merge the token cannot finish is refused up front, before anything is pushed or written.

The core REST limit is one hour of calls shared by everything that uses the token. A union joined, gated for a quarter of an hour
and then refused by a 403 on the merge wastes the gate; `GET /rate_limit` costs nothing, so the figure is read first and the reset
time named.
"""

import time
from datetime import UTC, datetime

from qew.errors import QewError
from qew.forge import Forge


def needed_for_merge(count: int) -> int:
    """About how many calls of the forge's limited resource merging `count` pull requests spends: the merge, the head branch deletions and
    the label and draft writes; the reads go through GraphQL, which has a budget of its own."""
    return 10 + 4 * count


def refuse_if_short(forge: Forge, needed: int, doing: str, untouched: str) -> int | None:
    """Raise (exit 4) when the forge has fewer calls left than `needed`; otherwise the figure that is left, None when the forge reports none."""
    budget = forge.budget()
    if budget is None:
        return None
    if budget.remaining < needed:
        minutes = max(int((budget.reset - time.time()) / 60) + 1, 1)
        at = datetime.fromtimestamp(budget.reset, UTC).strftime("%H:%MZ")
        raise QewError(f"{doing} needs about {needed} {budget.resource} calls and the forge has {budget.remaining} {budget.resource} calls left "
                       f"(the token's hour resets at {at}, in about {minutes} min); {untouched}", 4)
    return budget.remaining
