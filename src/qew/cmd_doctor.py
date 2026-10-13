"""`qew doctor [--fix]`: check the environment this ledger runs in and say what to change."""

import argparse

from qew import fsmonitor, ledger_config


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    doctor = commands.add_parser("doctor", help="check the environment (watchman for a jj ledger); --fix applies what qew can")
    doctor.add_argument("--fix", action="store_true", help="enable watchman for this ledger's jj repository when it is installed (exit 4 when it is not)")


def run(arguments: argparse.Namespace) -> bool:
    if arguments.command != "doctor":
        return False
    config = ledger_config.load()
    if arguments.fix:
        fsmonitor.enable(config.ledger_path)
    found = fsmonitor.state(config.ledger_path)
    problem = fsmonitor.warning(config.ledger_path)
    print(problem or f"ok: {found.value}" if found is not fsmonitor.State.NOT_JJ else "ok: git ledger, nothing to check")
    return True
