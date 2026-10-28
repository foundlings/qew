"""`qew pr merge A B C`: pull requests whose heads form one union land through the merge gate once.

One of the pull requests (the lead) has a head that contains every other head. Every member is gated first, the lead is
merged, and each other member must then read as merged on the forge before its mirror record says so.
"""

import argparse
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from qew import branch_cleanup, code_vcs, pr_block, pr_record, replication, untrusted_text
from qew.cmd_pr import (
    MERGE_RECHECK_SECONDS, SCHEMA_VERSION, Build, MergeOptions, _merge_options, branch_line, forge_errors, hand_off, load,
    merge_check, merge_pull_request,
)
from qew.errors import QewError
from qew.ledger_config import Config
from qew.forge import RemotePullRequest


@dataclass
class BatchMerged:
    lead: int
    members: list[int]
    lines: list[str] = field(default_factory=list)
    unmerged: list[int] = field(default_factory=list)

    def render(self, as_json: bool) -> str:
        if as_json:
            return json.dumps({"lead": self.lead, "merged": [n for n in self.members if n not in self.unmerged],
                               "unmerged": self.unmerged, "schema_version": SCHEMA_VERSION}, sort_keys=True)
        return "\n".join(self.lines)


def run(config: Config, arguments: argparse.Namespace, options: MergeOptions) -> bool:
    numbers = list(dict.fromkeys(arguments.number))
    if options.auto:
        raise QewError("--auto does not combine with several pull requests: a batch merges now or not at all", 2)
    lead = _lead(config, _heads(config, numbers, arguments.target, None, _takeover(options)))  # a follower inside the lead's head never merges alone
    refusals = [error for number in numbers for error in merge_check(config, number, arguments.target, options, contained=number != lead)]
    if refusals or options.check:
        if refusals:
            raise QewError("; ".join(str(r) for r in refusals), refusals[0].exit_code)
        print(f"pr {', '.join('#' + str(n) for n in numbers)}: qew merge preflight passed (ledger and forge refusals only); nothing was merged")
        return True
    result = merge_batch(config, numbers, arguments.target, options)
    print(result.render(arguments.json))
    if result.unmerged:
        raise QewError(f"pr #{result.lead} merged, but pr {', '.join('#' + str(n) for n in result.unmerged)} did not read as merged on the forge; "
                       "its record was left alone (check the forge, then `qew pr sync` or merge it alone)", 3)
    return True


def _takeover(options: MergeOptions) -> str:
    """The reason that takes over another actor's claim: only with --other-author, as in a single merge."""
    reason = _merge_options(options)
    return reason if options.other_author else ""


def _heads(config: Config, numbers: list[int], target: str | None, build: Build | None, reason: str) -> dict[int, RemotePullRequest]:
    pulls: dict[int, RemotePullRequest] = {}
    for number in numbers:
        loaded = load(config, number, target, build, reason, merging=True)
        with forge_errors(loaded.record.target):
            pulls[number] = loaded.forge.read_pull_request(number)
    return pulls


def _lead(config: Config, pulls: dict[int, RemotePullRequest]) -> int:
    vcs = code_vcs.detect(config.root)
    for number, pull in pulls.items():
        if all(code_vcs.is_ancestor(config.root, vcs, other.head_sha, pull.head_sha) for n, other in pulls.items() if n != number):
            return number
    branches = [untrusted_text.show(p.head_branch).text for p in pulls.values()]
    first = branches[0]
    recipe = (f"jj git fetch; jj new {' '.join(b + '@origin' for b in branches)}; jj bookmark set {first} -r @; jj git push --bookmark {first}"
              if vcs == "jj" else
              f"git fetch; git checkout {first}; git merge {' '.join('origin/' + b for b in branches[1:])}; git push origin {first}")
    raise QewError(f"no head contains the others, so pr {', '.join('#' + str(n) for n in pulls)} are not one union "
                   f"(or a head is not fetched here yet). Join {', '.join(branches)} into one head: `{recipe}`, then run this again", 2)


def merge_batch(config: Config, numbers: list[int], target: str | None, options: MergeOptions, build: Build | None = None,
                sleep: Callable[[float], None] | None = None) -> BatchMerged:
    reason = _takeover(options)
    pulls = _heads(config, numbers, target, build, reason)
    lead = _lead(config, pulls)
    merged = merge_pull_request(config, lead, target, options, build, sleep)
    result = BatchMerged(lead, numbers, [*merged.lines])
    for number in numbers:
        if number == lead:
            continue
        loaded = load(config, number, target, build, reason, merging=True)
        forge, slug = loaded.forge, loaded.record.target
        with forge_errors(slug):
            pull = forge.read_pull_request(number)
            if pull.state != "merged":
                (sleep or time.sleep)(MERGE_RECHECK_SECONDS)
                pull = forge.read_pull_request(number)
        if pull.state != "merged":
            result.unmerged.append(number)
            result.lines.append(f"pr #{number}: still {pull.state} on the forge although #{lead} contains its head")
            continue
        with replication.section("pr-merge"):
            pr_record.update_mirror(loaded.snapshot.ledger, loaded.record, draft=False, state="merged")
        cleanup = branch_cleanup.delete_merged_head(forge, pull.head_branch, {pull.base_branch, config.target})
        branch = untrusted_text.show(cleanup.name).text
        with replication.section("pr-merge"):
            released = pr_block.release_dependents(config, number, build)
        result.lines += [f"pr #{number} merged with #{lead} at {pull.head_sha[:12]}", *branch_line(cleanup, branch), *hand_off(loaded, number), *released]
    return result
