"""Whether a cited change resolves in this checkout and landed on the configured target (an ancestor of it)."""

from collections.abc import Callable

from qew import code_vcs
from qew.code_vcs import Landing
from qew.errors import QewError
from qew.ledger_config import Config

Resolver = Callable[[str, str], Landing]  # (change id, vcs kind or "" for this checkout's) -> LANDED, UNKNOWN (not in this checkout) or NOT_ANCESTOR


def resolver(config: Config) -> Resolver | None:
    """The resolver for this checkout, or None where there is no code repository to ask."""
    try:
        checkout = code_vcs.detect(config.root)
    except QewError:
        return None

    def resolve(change: str, vcs: str) -> Landing:
        kind = vcs or checkout
        if kind != checkout or not code_vcs.valid_change(kind, change):
            return Landing.UNKNOWN  # another VCS or a shape this checkout cannot look up: unverifiable here, not wrong
        try:
            where, _commit = code_vcs.landing(config.root, kind, change, config.target)
        except QewError:
            return Landing.UNKNOWN
        return where

    return resolve
