"""`QEW_FILE_MAP=DIR`: record which `src/qew` files a process ran, for `scripts/gate.py --changed`.

A development aid only: it changes nothing a command does and does nothing unless the variable is set. Each code object is
reported once (the monitor then disables itself for it), so the cost is one callback per function a process ever calls. At exit
the process writes the repository-relative paths of the `src/qew` files it ran to `DIR/<pid>.txt`.
"""

import atexit
import os
from pathlib import Path
import sys

TOOL = 4  # sys.monitoring tool ids 0 to 5 are free to claim; 0 is the debugger, 1 coverage, 2 the profiler, 5 the optimizer


def start(directory: str) -> None:
    package = str(Path(__file__).resolve().parent)
    root = Path(package).parent.parent
    ran: set[str] = set()
    monitoring = sys.monitoring

    def on_start(code: object, offset: int) -> object:
        name = getattr(code, "co_filename", "")
        if name.startswith(package):
            ran.add(name)
        return monitoring.DISABLE

    def write() -> None:
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{os.getpid()}.txt").write_text("\n".join(sorted(str(Path(name).relative_to(root)) for name in ran)) + "\n", encoding="utf-8")

    monitoring.use_tool_id(TOOL, "qew-filemap")
    monitoring.register_callback(TOOL, monitoring.events.PY_START, on_start)
    monitoring.set_events(TOOL, monitoring.events.PY_START)
    atexit.register(write)
