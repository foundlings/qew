"""Push ledger state to one mirror target (skeleton)."""

from dataclasses import dataclass, field
from typing import List

from qew.forge import Forge
from qew.ledger_config import Config, Mirror
from qew.ledger_store import Ledger


@dataclass(frozen=True)
class Target:
    mirror: Mirror
    slug: str
    forge: Forge


@dataclass
class TargetReport:
    slug: str
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    adopted: int = 0
    skipped: List[str] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)


def sync_target(ledger: Ledger, target: Target, config: Config) -> TargetReport:
    return TargetReport(target.slug)
