"""`qew pr union N N...`: the heads of several open pull requests become one commit, pushed as the first one's branch.

Merge commits only, never a squash, so every change id the evidence cites stays an ancestor. The build touches no working
copy: git plumbing makes the commit, and the push is forward-only.
"""

import argparse
from pathlib import Path

from qew import code_vcs, untrusted_text
from qew.cmd_pr import MergeOptions, _merge_options, forge_errors, load, refuse_finished
from qew.errors import QewError
from qew.forge import RemotePullRequest
from qew.ledger_config import Config

REMOTE = "origin"


def _git(root: Path, *argv: str) -> str:
    result = code_vcs.run_git(root, *argv)
    if result.returncode != 0:
        raise QewError(f"`git {' '.join(argv[:2])}` failed: {untrusted_text.show((result.stderr or result.stdout).strip()[:300]).text}", 3)
    return result.stdout.strip()


def _contains(root: Path, inner: str, outer: str) -> bool:
    return code_vcs.run_git(root, "merge-base", "--is-ancestor", inner, outer).returncode == 0


def _join(root: Path, accumulated: str, head: str, number: int) -> str:
    """One merge commit with both parents, or exit 2 naming the pull request and the files that conflict."""
    tree = code_vcs.run_git(root, "merge-tree", "--write-tree", "--name-only", accumulated, head)
    lines = tree.stdout.splitlines()
    if tree.returncode == 1:
        files = [line for line in lines[1:] if line][: lines.index("") - 1 if "" in lines else None]
        shown = ", ".join(untrusted_text.show(name).text for name in files[:5]) or "unknown files"
        raise QewError(f"pr #{number} conflicts with the heads joined so far: {shown}; nothing was pushed (settle it on the branches, then run this again)", 2)
    if tree.returncode != 0 or not lines:
        raise QewError(f"`git merge-tree` failed: {untrusted_text.show(tree.stderr.strip()[:300]).text}", 3)
    return _git(root, "commit-tree", lines[0], "-p", accumulated, "-p", head, "-m", f"Union of pull request heads (joins #{number})")


def union(config: Config, arguments: argparse.Namespace) -> str:
    numbers = list(dict.fromkeys(arguments.number))
    if len(numbers) < 2:
        raise QewError("a union needs at least two pull requests", 2)
    options = MergeOptions(other_author=arguments.other_author, reason=arguments.reason)
    reason = _merge_options(options)
    root = config.root
    loaded = load(config, numbers[0], arguments.target, None, reason if options.other_author else "", merging=True)
    pulls: dict[int, RemotePullRequest] = {}
    for number in numbers:
        member = loaded if number == numbers[0] else load(config, number, arguments.target, None, owned=False)  # only the lead's branch moves
        with forge_errors(member.record.target):
            pulls[number] = member.forge.read_pull_request(number)
        refuse_finished(number, pulls[number], "join a union")
    lead = pulls[numbers[0]]
    if len({p.base_branch for p in pulls.values()}) != 1:
        raise QewError("these pull requests do not share one base branch", 2)
    if not code_vcs.valid_branch(lead.head_branch):
        raise QewError(f"pr #{numbers[0]}: head branch `{untrusted_text.show(lead.head_branch).text}` is not a safe branch name", 2)
    _git(root, "fetch", REMOTE)
    for number, pull in pulls.items():
        if code_vcs.run_git(root, "rev-parse", "--verify", "--quiet", f"{pull.head_sha}^{{commit}}").returncode != 0:
            raise QewError(f"pr #{number}: head {pull.head_sha[:12]} is not in this checkout after fetching {REMOTE}", 4)
    for number, pull in pulls.items():
        if all(_contains(root, other.head_sha, pull.head_sha) for n, other in pulls.items() if n != number):
            return f"pr #{number} already contains the others; nothing to join (merge with `qew pr merge {' '.join(map(str, numbers))}`)"
    built = lead.head_sha
    for number in numbers[1:]:
        head = pulls[number].head_sha
        if _contains(root, head, built):
            continue
        built = head if _contains(root, built, head) else _join(root, built, head, number)
    members = " ".join(f"#{n}" for n in numbers)
    if arguments.dry_run:
        return f"union {built[:12]} of {members} builds without conflicts; dry run, nothing pushed"
    branch = lead.head_branch
    _git(root, "push", REMOTE, f"{built}:refs/heads/{branch}")
    remote = _git(root, "ls-remote", REMOTE, f"refs/heads/{branch}").split()
    if not remote or remote[0] != built:
        raise QewError(f"the push of {branch} was accepted but {REMOTE} does not show {built[:12]} there", 3)
    return (f"union {built[:12]} of {members} pushed as {untrusted_text.show(branch).text} (the head of pr #{numbers[0]}); "
            f"run the project checks on it, then `qew pr merge {' '.join(map(str, numbers))}`")
