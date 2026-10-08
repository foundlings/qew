"""Pull forge-side changes into the ledger (skeleton)."""

from dataclasses import dataclass, field
from typing import List


@dataclass
class PullReport:
    slug: str
    applied: int = 0
    divergences: int = 0
    new_items: int = 0
    comments: int = 0
    skipped: List[str] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)


def pull_target(ledger, target) -> PullReport:
    return PullReport(target.slug)
