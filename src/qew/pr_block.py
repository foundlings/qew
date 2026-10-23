"""`qew pr block N --by M` and `qew pr unblock N`: a pull request that waits on others says so.

The forge shows it twice: the label `blocked` (never an id) and the title suffix ` [blocked by: #M, #M2]`. The blockers
are kept in the pull request's mirror record, so `pr sync` rebuilds the suffix and the merge of a blocker clears it.
"""

import re
from collections.abc import Callable

from qew import pr_record
from qew.cmd_pr import Build, forge_errors, load, refuse_finished
from qew.errors import QewError
from qew.forge import Forge
from qew.ledger_config import Config
from qew.ledger_store import actor
from qew.pr_verify import Outcome, apply_and_verify

LABEL = "blocked"
_SUFFIX = re.compile(r" \[blocked by: #\d+(?:, #\d+)*\]$")


def suffix(blockers: tuple[int, ...] | list[int]) -> str:
    return f" [blocked by: {', '.join(f'#{n}' for n in blockers)}]" if blockers else ""


def bare(title: str) -> str:
    return _SUFFIX.sub("", title)


def _show(forge: Forge, number: int, blockers: tuple[int, ...], sleep: Callable[[float], None] | None) -> bool:
    """Make the forge's title and label match `blockers`, reading each change back; True when something changed."""
    before = forge.read_pull_request(number)
    title = bare(before.title) + suffix(blockers)
    labels = tuple(dict.fromkeys([*(label for label in before.labels if label != LABEL), *([LABEL] if blockers else [])]))
    changed = False
    if title != before.title:
        outcome = apply_and_verify(before, lambda: forge.update_pull_request_title(number, title),
                                   lambda: forge.read_pull_request(number), lambda pull: pull.title == title, sleep=sleep)
        if outcome == Outcome.NOT_APPLIED:
            raise QewError(f"pr #{number} still has its old title after the forge accepted the change; nothing is known to have changed", 4)
        changed = True
    if set(labels) != set(before.labels):
        outcome = apply_and_verify(before, lambda: forge.set_pull_request_labels(number, labels),
                                   lambda: forge.read_pull_request(number), lambda pull: set(pull.labels) == set(labels), sleep=sleep)
        if outcome == Outcome.NOT_APPLIED:
            raise QewError(f"pr #{number} does not show the `{LABEL}` label change after the forge accepted it; nothing is known to have changed", 4)
        changed = True
    return changed


def _set(config: Config, number: int, change: Callable[[tuple[int, ...]], tuple[int, ...]], note: str, target: str | None,
         build: Build | None, on_behalf_of: str | None, owned: bool, sleep: Callable[[float], None] | None) -> str:
    """Apply `change` to the recorded blockers, make the forge show the result, then record it."""
    loaded = load(config, number, target, build, owned=owned, on_behalf_of=on_behalf_of)
    blockers = change(loaded.record.blockers)
    changed = False
    with forge_errors(loaded.record.target):
        pull = loaded.forge.read_pull_request(number)
        if owned or pull.state == "open":  # a blocker's merge only records for a pull request that finished meanwhile
            refuse_finished(number, pull, "be marked blocked or unblocked")
            changed = _show(loaded.forge, number, blockers, sleep)
    if blockers != loaded.record.blockers:
        pr_record.update_mirror(loaded.snapshot.ledger, loaded.record, blocked_by=list(blockers))
        loaded.snapshot.ledger.append_event(loaded.record.host, "commented", text=f"pull request #{number} {note}", author=actor())
        changed = True
    state = f"blocked by {', '.join(f'#{n}' for n in blockers)}" if blockers else "not blocked"
    return f"pr #{number} {state}" + ("" if changed else " (unchanged)")


def _numbers(by: list[int]) -> list[int]:
    for n in by:
        pr_record.positive_number(n)
    return list(dict.fromkeys(by))


def block(config: Config, number: int, by: list[int], target: str | None, build: Build | None = None,
          on_behalf_of: str | None = None, sleep: Callable[[float], None] | None = None) -> str:
    """Add blockers: the label `blocked` and the title suffix; the pull request must be open and the caller's to edit."""
    added = _numbers(by)
    if not added:
        raise QewError("name the blocking pull request with --by NUMBER", 2)
    if number in added:
        raise QewError(f"pr #{number} cannot be blocked by itself", 2)
    return _set(config, number, lambda current: tuple(dict.fromkeys([*current, *added])),
                f"is blocked by {' '.join(f'#{n}' for n in added)}", target, build, on_behalf_of, True, sleep)


def unblock(config: Config, number: int, by: list[int], target: str | None, build: Build | None = None,
            on_behalf_of: str | None = None, sleep: Callable[[float], None] | None = None) -> str:
    """Remove the named blockers, or every one when none is named; the label and suffix go with the last."""
    removed = _numbers(by)
    return _set(config, number, lambda current: tuple(n for n in current if removed and n not in removed),
                f"is no longer blocked by {' '.join(f'#{n}' for n in removed) or 'anyone'}", target, build, on_behalf_of, True, sleep)


def release_dependents(config: Config, merged: int, build: Build | None = None) -> list[str]:
    """After pull request `merged` landed: take it out of every blocked pull request that waits on it. One line per pull request;
    a pull request that cannot be edited is named, never an error (the merge already happened)."""
    from qew.cmd_items import ledger_for

    lines: list[str] = []
    for record in pr_record.waiting_on(ledger_for(config), merged):
        try:
            remaining = tuple(n for n in record.blockers if n != merged)
            _set(config, record.number, lambda current: tuple(n for n in current if n != merged),
                 f"is no longer blocked by #{merged} (it merged)", record.target, build, None, False, None)
        except QewError as error:
            lines.append(f"pr #{record.number} still lists #{merged} as a blocker: {error}")
            continue
        lines.append(f"unblocked pr #{record.number}" + (f" (still blocked by {', '.join(f'#{n}' for n in remaining)})" if remaining else ""))
    return lines
