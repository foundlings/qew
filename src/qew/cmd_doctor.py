"""`qew doctor [--fix]`: check the environment this ledger runs in and say what to change."""

import argparse

from qew import fsmonitor, ledger_config, ledger_layout
from qew.errors import QewError


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    doctor = commands.add_parser("doctor", help="check the environment (watchman for a jj ledger); --fix applies what qew can")
    doctor.add_argument("--fix", action="store_true", help="enable watchman for this ledger's jj repository when it is installed (exit 4 when it is not)")


def run(arguments: argparse.Namespace) -> bool:
    if arguments.command != "doctor":
        return False
    config = ledger_config.load()
    if arguments.fix:
        fsmonitor.enable(config.ledger_path)
        if fsmonitor.state(config.ledger_path) is fsmonitor.State.ENABLED:
            try:
                crawled = fsmonitor.recrawls(config.ledger_path)
            except QewError:
                crawled = 0  # a watchman that does not answer is named by the report below; there is nothing to reset
            if crawled:
                fsmonitor.reset(config.ledger_path)
                print(f"reset the watchman watch of {config.ledger_path}; the next jj snapshot scans the tree once")
    found = fsmonitor.state(config.ledger_path)
    problem = fsmonitor.warning(config.ledger_path)
    print(problem or f"ok: {found.value}" if found is not fsmonitor.State.NOT_JJ else "ok: git ledger, nothing to check")
    if found is fsmonitor.State.ENABLED:
        try:
            count = fsmonitor.recrawls(config.ledger_path)
        except QewError as error:
            print(f"warning: {error}; jj snapshots of this ledger may be waiting on it")
        else:
            if count:
                print(f"warning: watchman has re-crawled this ledger's watch {count} times because the system dropped file events; "
                      "each crawl makes every jj snapshot wait, so a commit can take a minute. Run `qew doctor --fix`")
    layout = ledger_layout.notice(ledger_config.ledger_schema(config.ledger_path))
    if layout:
        print(f"layout: {layout}")
    return True
