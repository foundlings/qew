"""`qew hook`: install agent hooks and answer the agents' hook events."""

import argparse

from qew.errors import QewError

AGENTS = ("claude", "codex", "cursor", "opencode")
EVENTS = ("session-start", "pre-tool", "post-tool")


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
    raise QewError("qew hook: not implemented", 1)
