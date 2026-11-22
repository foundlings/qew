"""`qew pr retarget N --base BRANCH`: point a pull request at another base branch and record it.

A stacked pull request (based on a lane branch, not main) cannot join a union with main-based ones. Moving its base is safe when the
head still holds everything the old base brought: the head must contain the merge base of the old and the new base branch, or the
pull request would stop showing what it replaces. A finished pull request is never touched.
"""

from dataclasses import dataclass
import json
from collections.abc import Callable

from qew import code_vcs, pr_record, remote_options, replication, untrusted_text
from qew.cmd_pr import SCHEMA_VERSION, Build, apply_and_verify, forge_errors, load, refuse_finished
from qew.errors import QewError
from qew.ledger_config import Config
from qew.pr_verify import Outcome


@dataclass(frozen=True)
class Retargeted:
    number: int
    base: str
    changed: bool

    def render(self, as_json: bool) -> str:
        if as_json:
            return json.dumps({"number": self.number, "base": self.base, "changed": self.changed, "schema_version": SCHEMA_VERSION}, sort_keys=True)
        return f"pr #{self.number} retargeted to {self.base}" if self.changed else f"pr #{self.number} already based on {self.base}"


def _resolve(config: Config, remote: str, branch: str) -> str:
    """The tip of `branch` as the code remote last showed it, else the local branch; exit 2 when neither exists."""
    for name in (f"{remote}/{branch}", branch):
        found = code_vcs.run_git(config.root, "rev-parse", "--verify", "--quiet", f"{name}^{{commit}}")
        if found.returncode == 0:
            return found.stdout.strip()
    raise QewError(f"branch {untrusted_text.show(branch).text} is not in this checkout: fetch it first (`git fetch {remote}`), then run this again", 2)


def _refuse_lost_history(config: Config, number: int, head: str, old: str, new: str) -> None:
    remote = remote_options.selected()
    old_tip, new_tip = _resolve(config, remote, old), _resolve(config, remote, new)
    common = code_vcs.run_git(config.root, "merge-base", old_tip, new_tip)
    if common.returncode != 0 or not common.stdout.strip():
        raise QewError(f"{old} and {new} share no merge base; pr #{number} cannot be retargeted between unrelated branches", 2)
    held = code_vcs.run_git(config.root, "merge-base", "--is-ancestor", common.stdout.strip(), head).returncode == 0
    if not held:
        raise QewError(f"pr #{number}'s head does not contain the merge base of {old} and {new}; retargeting would drop what it was built on, "
                       "settle the branch first", 2)


def retarget(config: Config, number: int, base: str, target: str | None, build: Build | None = None,
             sleep: Callable[[float], None] | None = None, on_behalf_of: str | None = None) -> Retargeted:
    if not code_vcs.valid_branch(base):
        raise QewError(f"{untrusted_text.show(base).text!r} is not a branch name", 2)
    loaded = load(config, number, target, build, on_behalf_of=on_behalf_of)
    forge = loaded.forge
    changed = False
    with forge_errors(loaded.record.target):
        before = forge.read_pull_request(number)
        refuse_finished(number, before, "be retargeted")
        if before.base_branch != base:
            _refuse_lost_history(config, number, before.head_sha, before.base_branch, base)
            outcome = apply_and_verify(
                before,
                lambda: forge.update_pull_request_base(number, base),
                lambda: forge.read_pull_request(number),
                lambda pull: pull.base_branch == base,
                sleep=sleep,
            )
            if outcome == Outcome.NOT_APPLIED:
                raise QewError(f"pr #{number} still has base {before.base_branch} after the forge accepted the change; nothing is known to have changed", 4)
            changed = True
    if loaded.record.base != base:
        with replication.section("pr-retarget"):
            pr_record.update_mirror(loaded.snapshot.ledger, loaded.record, base=base)
            pr_record.note(loaded.snapshot.ledger, loaded.record, f"pull request #{number} retargeted to {base}")
    return Retargeted(number, base, changed)
