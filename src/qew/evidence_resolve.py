"""Whether a cited change resolves in this checkout and landed on the configured target (an ancestor of it).

Many ids are resolved together: `prepare` looks all of them up in a few read-only VCS calls (never one snapshotting call per
id, which took hours on a checkout with tens of thousands of files), and the lookups then answer from that.
"""

from collections.abc import Iterable

from qew import code_vcs
from qew.code_vcs import Landing
from qew.errors import QewError
from qew.ledger_config import Config


class Resolver:
    """(change id, vcs kind or "" for this checkout's) -> LANDED, UNKNOWN (not in this checkout) or NOT_ANCESTOR."""

    def __init__(self, config: Config, checkout: str) -> None:
        self._config = config
        self._checkout = checkout
        self._known: dict[str, Landing] = {}

    def _usable(self, change: str, vcs: str) -> bool:
        return (vcs or self._checkout) == self._checkout and code_vcs.valid_change(self._checkout, change)

    def prepare(self, pairs: Iterable[tuple[str, str]]) -> None:
        """Resolve every usable id not yet known in one batched lookup."""
        todo = list(dict.fromkeys(change for change, vcs in pairs if self._usable(change, vcs) and change not in self._known))
        if not todo:
            return
        try:
            found = code_vcs.landing_many(self._config.root, self._checkout, todo, self._config.target)
        except QewError:
            found = {change: (Landing.UNKNOWN, "") for change in todo}
        self._known.update({change: where for change, (where, _commit) in found.items()})

    def __call__(self, change: str, vcs: str) -> Landing:
        if not self._usable(change, vcs):
            return Landing.UNKNOWN  # another VCS or a shape this checkout cannot look up: unverifiable here, not wrong
        if change not in self._known:
            self.prepare([(change, vcs)])
        return self._known.get(change, Landing.UNKNOWN)


def resolver(config: Config) -> Resolver | None:
    """The resolver for this checkout, or None where there is no code repository to ask."""
    try:
        return Resolver(config, code_vcs.detect(config.root))
    except QewError:
        return None
