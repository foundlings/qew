"""Canonical closed vocabulary and text findings."""

from dataclasses import dataclass
from enum import Enum
from typing import FrozenSet, List, Optional

from qew.errors import QewError
from qew.text_scan import text_findings


AUTHOR_LABEL_PREFIX = "author:"
APPROVAL_LABEL = "needs:approval"


class Priority(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"

    @property
    def rank(self) -> int:
        return list(Priority).index(self)


class WorkType(str, Enum):
    BUG = "bug"
    ENHANCEMENT = "enhancement"
    TASK = "task"


@dataclass(frozen=True)
class NormalizedLabels:
    priority: Optional[Priority]
    work_type: Optional[WorkType]
    priorities: FrozenSet[Priority]
    work_types: FrozenSet[WorkType]
    approved: bool
    epic: bool
    in_progress: bool
    needs_approval: bool
    managed: bool
    draft: bool = False

    @property
    def workable(self) -> bool:
        """Approved, typed as work (not an epic) and not held back as a draft."""
        return self.approved and not self.draft and not self.epic

    @property
    def triaged(self) -> bool:
        return len(self.priorities) == 1 and len(self.work_types) == 1


def _ascii_lower(value: str) -> str:
    # GitHub folds ASCII case only; Unicode casefolding would equate look-alikes
    # such as U+212A KELVIN SIGN with "k".
    return "".join(
        char.lower() if "A" <= char <= "Z" else char for char in value
    )


def ascii_fold(value: str) -> str:
    """GitHub's case folding for label names and logins (ASCII only)."""
    return _ascii_lower(value)


def reject_reserved_areas(areas: List[str]) -> None:
    """Refuse labels that carry Qew state (approval, priority, type, claim, author)."""
    reserved = {item.value.casefold() for item in (*Priority, *WorkType)}
    reserved |= {"epic", "in-progress", "needs:approval"}
    for area in areas:
        name = area.strip().casefold()
        if name in reserved or name.startswith(("qew:", "author:")):
            raise QewError(f"area label is reserved by Qew: {area}", 2)


def normalize_labels(labels: List[str]) -> NormalizedLabels:
    names = frozenset(_ascii_lower(label) for label in labels)
    priorities = frozenset(
        priority for priority in Priority if _ascii_lower(priority.value) in names
    )
    work_types = frozenset(
        work_type for work_type in WorkType if _ascii_lower(work_type.value) in names
    )
    priority = min(priorities, key=lambda item: item.rank) if priorities else None
    work_type = next((item for item in WorkType if item in work_types), None)
    return NormalizedLabels(
        priority=priority,
        work_type=work_type,
        priorities=priorities,
        work_types=work_types,
        approved="qew:approved" in names,
        epic="epic" in names,
        in_progress="in-progress" in names,
        needs_approval="needs:approval" in names,
        managed="qew:managed" in names,
        draft="qew:draft" in names,
    )
