"""`qew start X --needs-on BRANCH`: check that every unmet need of X is already evidenced on a collection branch.

Work stacked on a branch that merges to main later cannot wait for its needs to complete, since they complete only with that
merge. The check is the same as completion's, against the named branch instead of the target: some evidence event of the need
whose changes are all ancestors of the branch tip. Completing X still requires its needs completed.
"""

from qew import code_vcs
from qew.code_vcs import Landing
from qew.errors import QewError
from qew.ledger_config import Config
from qew.ledger_store import Snapshot


def verify(config: Config, snapshot: Snapshot, item_id: str, branch: str, unmet: list[str]) -> None:
    if not code_vcs.valid_branch(branch):
        raise QewError(f"--needs-on `{branch}` is not a usable branch name", 2)
    kind = code_vcs.detect(config.root)
    missing: list[str] = []
    for need in unmet:
        events = snapshot.folds[need].evidence if need in snapshot.items else []
        if not any(_on_branch(config, kind, event, branch) for event in events if event.get("vcs") == kind):
            missing.append(need)
    if missing:
        raise QewError(f"cannot start {item_id} --needs-on {branch}: {' '.join(missing)} has no evidence that is an ancestor of {branch} "
                       f"(record it with qew evidence NEED --change CHANGE, or wait for it to complete)", 2)


def _on_branch(config: Config, kind: str, event: object, branch: str) -> bool:
    changes = event.get("change_ids")  # type: ignore[attr-defined]
    return bool(changes) and all(code_vcs.landing(config.root, kind, change, branch)[0] == Landing.LANDED for change in changes)
