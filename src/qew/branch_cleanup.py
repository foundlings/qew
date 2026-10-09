"""Which branches may go: the rules shared by `qew pr merge` (its own head) and `qew branch prune` (the backlog).

A branch is never deleted when it is the base, the configured target branch, the forge's default or a protected
branch, or the head of any open pull request. Branch names are forge text: they are only ever compared and passed
back to the forge, never evaluated.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Collection, List

from qew.forge import Forge, ForgeError, RemoteBranch, Unsupported


class Fate(Enum):
    DELETED = "deleted"
    KEPT = "kept"  # a rule says it must stay
    FAILED = "failed"  # the forge could not delete it; the merge still stands


@dataclass(frozen=True)
class HeadFate:
    name: str
    fate: Fate
    note: str = ""  # why it was kept or what the forge said


def delete_merged_head(forge: Forge, head: str, never: Collection[str]) -> HeadFate:
    """Delete the head branch of a pull request that just merged, unless a rule or another open pull request keeps it.

    `never` holds the base and the configured target branch. A forge failure is returned, not raised: the merge
    already happened and cleanup must not turn it into an error.
    """
    if head in never:
        return HeadFate(head, Fate.KEPT, "it is the base or the target branch")
    try:
        if head in forge.list_open_pull_heads():
            return HeadFate(head, Fate.KEPT, "another open pull request uses it")
        forge.delete_branch(head)
    except Unsupported:
        return HeadFate(head, Fate.FAILED, "this forge cannot delete branches")
    except ForgeError as error:
        return HeadFate(head, Fate.FAILED, error.detail)
    return HeadFate(head, Fate.DELETED)


def removable(forge: Forge, base: str, target: str) -> List[str]:
    """Names of the branches whose tip `base` already holds and that nothing needs, in listing order.

    Raises the forge's error untouched when it cannot answer: an unanswered question never counts as merged.
    """
    in_use = set(forge.list_open_pull_heads())
    found: List[str] = []
    for branch in forge.list_branches():
        if _kept(branch, base, target, in_use):
            continue
        if forge.branch_is_merged(branch.name, base):
            found.append(branch.name)
    return found


def _kept(branch: RemoteBranch, base: str, target: str, in_use: Collection[str]) -> bool:
    return branch.default or branch.protected or branch.name in (base, target) or branch.name in in_use
