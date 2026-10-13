"""The `qew` console script: answer a pre-tool hook event no guard rule can act on without importing the CLI, else run the CLI.

An agent runs `qew hook pre-tool` before every shell call; loading the command table, ledger and config code for a plain `ls` is the
whole cost of it. Anything unexpected here falls through to the full CLI, which decides.
"""

import io
import json
import sys
from collections.abc import Sequence

from qew import hook_words

_EVENT = ["hook", "pre-tool", "--agent"]


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) == 4 and arguments[:3] == _EVENT and arguments[3] in hook_words.AGENTS:
        try:
            raw = sys.stdin.read(hook_words.PAYLOAD_LIMIT)
        except (OSError, UnicodeError, ValueError):
            raw = ""
        if hook_words.inert(hook_words.command_of(raw)):
            if arguments[3] == "cursor":
                print(json.dumps({"permission": "allow"}))
            return 0
        sys.stdin = io.StringIO(raw)
    from qew.cli import main as run

    return run(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
