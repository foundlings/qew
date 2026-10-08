"""`qew hook`: install agent hooks and answer the agents' hook events."""

import argparse
import json
from pathlib import Path
import sys
from typing import List, Optional, Tuple

from qew import (
    claude_hook, cmd_items, codex_hook, cursor_hook, hook_guard, ledger_config, ledger_graph,
    opencode_hook,
)
from qew.errors import QewError
from qew.ledger_event import State
from qew.ledger_store import actor

AGENTS = ("claude", "codex", "cursor", "opencode")
EVENTS = ("session-start", "pre-tool", "post-tool")

_ADAPTERS = {
    "claude": claude_hook,
    "codex": codex_hook,
    "cursor": cursor_hook,
    "opencode": opencode_hook,
}
_PAYLOAD_LIMIT = 1_000_000
_PRIME_LIMIT = 2000
_PRIME_READY = 10
_ROW_LIMIT = 110
_PRIME_COMMANDS = (
    "Commands: qew ready | qew start ID | qew evidence ID | qew complete ID | qew show ID | qew draft list. "
    "Claim before coding; never change forge issues or merge pull requests around qew."
)
_REFUSAL_EXIT = 2


def register(commands) -> None:
    hook = commands.add_parser("hook", help="install agent hooks; answer agent hook events")
    actions = hook.add_subparsers(dest="hook_command", required=True)
    for name, text in (("install", "wire Qew into agent configuration"), ("remove", "remove Qew's agent hook entries")):
        sub = actions.add_parser(name, help=text)
        for agent in AGENTS:
            sub.add_argument(f"--{agent}", action="store_true", help=f"only {agent}")
    for event in EVENTS:
        sub = actions.add_parser(event, help=f"answer the agent's {event} event")
        sub.add_argument("--agent", choices=AGENTS, required=True, help="calling agent")


def run(arguments: argparse.Namespace) -> bool:
    if arguments.command != "hook":
        return False
    if arguments.hook_command in ("install", "remove"):
        print(_configure(arguments))
        return True
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
    chosen = [agent for agent in AGENTS if getattr(arguments, agent)] or list(AGENTS)
    root = _checkout_root()
    installing = arguments.hook_command == "install"
    lines = []
    for agent in chosen:
        adapter = _ADAPTERS[agent]
        changed = adapter.install(root) if installing else adapter.remove(root)
        action = ("installed" if installing else "removed") if changed else "unchanged"
        lines.append(f"{action} {agent} hook")
    if installing and "codex" in chosen:
        lines.append("review and trust it with /hooks before it will run")
    return "\n".join(lines)


def _checkout_root() -> Path:
    here = Path.cwd().resolve()
    for directory in (here, *here.parents):
        if (directory / ".git").exists() or (directory / ".jj").exists():
            return directory
    raise QewError("not inside a Git or jj repository", 2)


# -- hook events

def _answer(event: str, agent: str, raw: str) -> Tuple[str, int]:
    command, cwd = _payload(raw)
    if event == "session-start":
        return _render_context(agent, "SessionStart", _prime(cwd)), 0
    if event == "pre-tool":
        return _pre_tool(agent, command, cwd)
    return _render_context(agent, "PostToolUse", _push_reminder(command, cwd) if command else None), 0


def _payload(raw: str) -> Tuple[Optional[str], Optional[Path]]:
    try:
        payload = json.loads(raw)
    except ValueError:
        return None, None
    if not isinstance(payload, dict):
        return None, None
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else payload.get("command")
    cwd = payload.get("cwd")
    return (
        command if isinstance(command, str) else None,
        Path(cwd) if isinstance(cwd, str) and Path(cwd).is_dir() else None,
    )


def _pre_tool(agent: str, command: Optional[str], cwd: Optional[Path]) -> Tuple[str, int]:
    refusals = hook_guard.refusals(command) if command and ledger_config.find_root(cwd) else []
    if not refusals:
        return (json.dumps({"permission": "allow"}) if agent == "cursor" else ""), 0
    reason = "\n".join(refusal.reason() for refusal in refusals)
    if agent in ("claude", "codex"):
        decision = {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}
        return json.dumps({"hookSpecificOutput": decision}), 0
    if agent == "cursor":
        return json.dumps({"permission": "deny", "user_message": reason, "agent_message": reason}), 0
    return reason, _REFUSAL_EXIT


def _render_context(agent: str, event: str, text: Optional[str]) -> str:
    if not text:
        return ""
    if agent in ("claude", "codex"):
        return json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}})
    if agent == "cursor":
        return json.dumps({"additional_context": text}) if event == "SessionStart" else ""
    return text


def _prime(cwd: Optional[Path]) -> Optional[str]:
    if ledger_config.find_root(cwd) is None:
        return None
    snapshot = cmd_items.ledger_for(ledger_config.load(cwd)).snapshot()
    ready = ledger_graph.ready(snapshot)
    drafts = [i for i in snapshot.items if (snapshot.folds[i].state or State.DRAFT) == State.DRAFT]
    header = f"Qew ledger: {len(ready)} ready, {len(ledger_graph.blocked(snapshot))} blocked, {len(drafts)} draft."
    budget = _PRIME_LIMIT - len((header + "\n" + _PRIME_COMMANDS + "\nReady, by priority:\n").encode("utf-8")) - 40
    rows: List[str] = []
    for item_id in ready[:_PRIME_READY]:
        row = cmd_items.row(snapshot, item_id)
        row = row if len(row) <= _ROW_LIMIT else row[: _ROW_LIMIT - 3] + "..."
        budget -= len(row.encode("utf-8")) + 1
        if budget < 0:
            break
        rows.append(row)
    lines = [header]
    if rows:
        lines.append("Ready, by priority:")
        lines.extend(rows)
    if len(ready) > len(rows):
        lines.append(f"(+{len(ready) - len(rows)} more: qew ready)")
    lines.append(_PRIME_COMMANDS)
    return "\n".join(lines)


def _push_reminder(command: str, cwd: Optional[Path]) -> Optional[str]:
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
