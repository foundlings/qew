"""`qew hook`: install agent hooks and answer the agents' hook events."""

import argparse
import json
from pathlib import Path
import sys

from qew import (
    claude_hook, cmd_items, codex_hook, inbox, ledger_check, ledger_conflicts, prime, cursor_hook, git_hook, hook_guard, hook_merge, hook_words, ledger_config,
    opencode_hook, vcs_check,
)
from qew.errors import QewError
from qew.ledger_event import State
from qew.ledger_store import actor

AGENTS = hook_words.AGENTS
TARGETS = AGENTS + ("git",)
EVENTS = ("session-start", "pre-tool", "post-tool")

_ADAPTERS = {
    "claude": claude_hook,
    "codex": codex_hook,
    "cursor": cursor_hook,
    "opencode": opencode_hook,
    "git": git_hook,
}
_PAYLOAD_LIMIT = hook_words.PAYLOAD_LIMIT
_REFUSAL_EXIT = 2
_SCHEMA_VERSION = 1
_TRUST_LINE = "review and trust it with /hooks before it will run"


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    hook = commands.add_parser("hook", help="install agent hooks; answer agent hook events")
    actions = hook.add_subparsers(dest="hook_command", required=True)
    for name, text in (("install", "wire Qew into agent configuration"), ("remove", "remove Qew's agent hook entries")):
        sub = actions.add_parser(name, help=text)
        if name == "install":
            sub.add_argument("--skip-pre-tool", action="store_true",
                             help="with --claude only: leave PreToolUse alone (the repo runs `qew hook pre-tool` from its own gate script)")
        for agent in AGENTS:
            sub.add_argument(f"--{agent}", action="store_true", help=f"only {agent}")
        sub.add_argument("--git", action="store_true", help="the tracked git pre-push hook that checks Refs: citations (never part of the default)")
        sub.add_argument("--json", action="store_true", help="machine-readable result (schema_version 1)")
    for event in EVENTS:
        sub = actions.add_parser(event, help=f"answer the agent's {event} event")
        sub.add_argument("--agent", choices=AGENTS, required=True, help="calling agent")
    check = actions.add_parser("vcs-check", help="check that commits citing ledger ids have evidence")
    source = check.add_mutually_exclusive_group(required=True)
    source.add_argument("--pre-push", action="store_true", help="read git pre-push lines on stdin")
    source.add_argument("--revisions", help="jj revset of the changes to check")
    source.add_argument("--message-file", help="report the ledger ids a commit message cites")


def run(arguments: argparse.Namespace) -> bool:
    if arguments.command != "hook":
        return False
    if arguments.hook_command in ("install", "remove"):
        print(_configure(arguments))
        return True
    if arguments.hook_command == "vcs-check":
        return _vcs_check(arguments)
    # The one deliberate leniency in qew: a hook must never break the agent's
    # session, so an unexpected internal failure while answering a hook event
    # prints nothing and exits 0. A deliberate refusal is not a failure; it is
    # the (text, exit code) pair returned below. Do not copy this elsewhere.
    try:
        text, code = _answer(arguments.hook_command, arguments.agent, sys.stdin.read(_PAYLOAD_LIMIT))
    except Exception:
        return True
    if text:
        print(text)
    if code:
        sys.stdout.flush()
        raise SystemExit(code)
    return True


# -- install and remove

def _configure(arguments: argparse.Namespace) -> str:
    chosen = [target for target in TARGETS if getattr(arguments, target)] or list(AGENTS)
    root = _checkout_root()
    installing = arguments.hook_command == "install"
    skip_pre_tool = installing and arguments.skip_pre_tool
    if skip_pre_tool and chosen != ["claude"]:
        raise QewError("--skip-pre-tool applies to the Claude adapter only: pass --claude and no other agent", 2)
    results = []
    for agent in chosen:
        adapter = _ADAPTERS[agent]
        changed = claude_hook.install(root, pre_tool=False) if skip_pre_tool else adapter.install(root) if installing else adapter.remove(root)
        action = ("installed" if installing else "removed") if changed else "unchanged"
        result: dict[str, str | bool] = {"agent": agent, "action": action}
        if agent == "codex" and installing:
            result["trust_required"] = True
        results.append(result)
    if arguments.json:
        body = results[0] if len(results) == 1 else {"agents": results}
        return json.dumps({"schema_version": _SCHEMA_VERSION, **body})
    lines = [f"{result['action']} {result['agent']} hook" for result in results]
    if installing and "codex" in chosen:
        lines.append(_TRUST_LINE)
    return "\n".join(lines)


def _checkout_root() -> Path:
    here = Path.cwd().resolve()
    for directory in (here, *here.parents):
        if (directory / ".git").exists() or (directory / ".jj").exists():
            return directory
    raise QewError("not inside a Git or jj repository", 2)


# -- vcs-check

def _vcs_check(arguments: argparse.Namespace) -> bool:
    config = ledger_config.load()
    snapshot = cmd_items.ledger_for(config).snapshot()
    if arguments.message_file:
        try:
            message = Path(arguments.message_file).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise QewError(f"cannot read {arguments.message_file}: {error}", 2) from None
        lines, unknown = vcs_check.message_ids(snapshot, message)
        if lines:
            print("\n".join(lines))
        if unknown:
            raise SystemExit(_REFUSAL_EXIT)
        return True
    if arguments.pre_push:
        commits = vcs_check.git_pushed(config.root, sys.stdin.read(_PAYLOAD_LIMIT))
    else:
        commits = vcs_check.jj_revisions(config.root, arguments.revisions)
    lines, refusals = vcs_check.report(snapshot, commits)
    if lines:
        print("\n".join(lines))
    if refusals:
        print("\n".join(f"qew: push refused: {refusal}" for refusal in refusals), file=sys.stderr)
        raise SystemExit(_REFUSAL_EXIT)
    return True


_PUSH_CHECK_SECONDS = 5.0  # under the callers' own cap (foundlings: 10 s), so a stuck jj yields a reason, not a timeout

# -- hook events

def _answer(event: str, agent: str, raw: str) -> tuple[str, int]:
    command, cwd = _payload(raw)
    if event == "session-start":
        return _render_context(agent, "SessionStart", _prime(cwd)), 0
    if event == "pre-tool":
        return _pre_tool(agent, command, cwd)
    notes = [_push_reminder(command, cwd), hook_merge.reminder(command, cwd)] if command else []
    return _render_context(agent, "PostToolUse", "\n".join(note for note in notes if note) or None), 0


def _payload(raw: str) -> tuple[str | None, Path | None]:
    try:
        payload = json.loads(raw)
    except ValueError:
        return None, None
    if not isinstance(payload, dict):
        return None, None
    cwd = payload.get("cwd")
    return (
        hook_words.command_of(raw),
        Path(cwd) if isinstance(cwd, str) and Path(cwd).is_dir() else None,
    )


def _pre_tool(agent: str, command: str | None, cwd: Path | None) -> tuple[str, int]:
    refusals = hook_guard.refusals(command) if command and ledger_config.find_root(cwd) else []
    unevidenced, undecided = _unevidenced_jj_pushes(command, cwd) if command and not refusals else ([], [])
    if not refusals and not unevidenced:
        if undecided:  # the check ran out of time: allow, and say so rather than answer nothing
            return _render_context(agent, "PreToolUse", "\n".join(undecided)) or json.dumps({"permission": "allow"}), 0
        return (json.dumps({"permission": "allow"}) if agent == "cursor" else ""), 0
    reason = "\n".join([refusal.reason() for refusal in refusals] + unevidenced)
    if agent in ("claude", "codex"):
        decision = {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}
        return json.dumps({"hookSpecificOutput": decision}), 0
    if agent == "cursor":
        return json.dumps({"permission": "deny", "user_message": reason, "agent_message": reason}), 0
    return reason, _REFUSAL_EXIT


def _unevidenced_jj_pushes(command: str, cwd: Path | None) -> tuple[list[str], list[str]]:
    """jj has no pre-push hook: refuse `jj git push` of changes that cite unevidenced items.

    Read-only and bounded (`_PUSH_CHECK_SECONDS`, no working-copy snapshot, no ledger lock): returns (refusals, could-not-decide notes)."""
    revsets = hook_guard.jj_push_revsets(command)
    if not revsets or cwd is None or ledger_config.find_root(cwd) is None:
        return [], []
    config = ledger_config.load(cwd)
    snapshot = cmd_items.ledger_for(config).snapshot()
    reasons: list[str] = []
    undecided: list[str] = []
    for revset in revsets:
        problems = vcs_check.jj_push_problems(snapshot, config.root, revset, _PUSH_CHECK_SECONDS)
        if problems is None:
            undecided.append(f"qew could not decide whether `{revset}` cites unevidenced items (jj did not answer within {_PUSH_CHECK_SECONDS:g}s); the push is allowed unchecked.")
        for problem in problems or []:
            reasons.append(f"Blocked `jj git push`: {problem}.")
    return reasons, undecided


def _render_context(agent: str, event: str, text: str | None) -> str:
    if not text:
        return ""
    if agent in ("claude", "codex"):
        return json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}})
    if agent == "cursor":
        return json.dumps({"additional_context": text}) if event == "SessionStart" else ""
    return text


def _prime(cwd: Path | None) -> str | None:
    if ledger_config.find_root(cwd) is None:
        return None
    config = ledger_config.load(cwd)
    snapshot = ledger_check.read_snapshot(cmd_items.ledger_for(config))
    text = prime.build(snapshot, prime.HOOK, len(ledger_conflicts.collect(config, snapshot)))
    try:
        login = inbox.require_login(actor())
    except QewError:
        return text  # an actor that is no login has no inbox
    line = inbox.hook_line(snapshot, login, prime.HOOK.limit - len(text.encode("utf-8")) - 1)
    return f"{text}\n{line}" if line else text


def _push_reminder(command: str, cwd: Path | None) -> str | None:
    if not hook_guard.pushes(command) or ledger_config.find_root(cwd) is None:
        return None
    snapshot = cmd_items.ledger_for(ledger_config.load(cwd)).snapshot()
    me = actor()
    pending = [
        snapshot.short(item_id)
        for item_id, folded in sorted(snapshot.folds.items())
        if folded.state == State.CLAIMED and folded.owner == me and not folded.evidence
    ]
    if not pending:
        return None
    shown = ", ".join(pending[:3]) + (f" (+{len(pending) - 3} more)" if len(pending) > 3 else "")
    return f"qew: pushed while {shown} is claimed without evidence; run `qew evidence ID`, then `qew complete ID`."
