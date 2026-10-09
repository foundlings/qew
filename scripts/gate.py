#!/usr/bin/env python3
"""Run the whole test suite with one worker process per test module, in parallel.

    python3 scripts/gate.py [-j N] [--timeout SECONDS] [MODULE_SUBSTRING ...]

Every walk builds its own temporary repositories, so modules are independent; the serial
`python3 -m unittest discover -s tests` stays valid and gives the same verdict, only slower.
Prints a summary and the failing tests of any module that fails; exit status 1 on any failure.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def run_module(name: str, timeout: float):
    started = time.monotonic()
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "QEW_UNDER_TEST": "1"}
    try:
        done = subprocess.run(
            [sys.executable, "-m", "unittest", f"tests.{name}"],
            cwd=ROOT, env=environment, capture_output=True, text=True, timeout=timeout, check=False,
        )
        output, code = done.stdout + done.stderr, done.returncode
    except subprocess.TimeoutExpired as error:
        output = f"TIMEOUT after {timeout}s\n{(error.stderr or b'').decode('utf-8', 'replace')[-800:]}"
        code = 124
    ran = re.search(r"^Ran (\d+) tests?", output, re.MULTILINE)
    return name, code, int(ran.group(1)) if ran else 0, time.monotonic() - started, output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-j", "--jobs", type=int, default=min(8, os.cpu_count() or 4))
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("filters", nargs="*")
    arguments = parser.parse_args()
    modules = sorted(p.stem for p in (ROOT / "tests").glob("test_*.py"))
    if arguments.filters:
        modules = [m for m in modules if any(f in m for f in arguments.filters)]
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=arguments.jobs) as pool:
        results = list(pool.map(lambda name: run_module(name, arguments.timeout), modules))
    failed = [r for r in results if r[1] != 0]
    for name, code, _, seconds, output in failed:
        print(f"=== {name} (exit {code}, {seconds:.0f}s)")
        lines = [l for l in output.splitlines() if l.startswith(("FAIL:", "ERROR:", "TIMEOUT"))]
        causes = [l[:400] for l in output.splitlines() if re.match(r"^[A-Za-z_.]*(Error|Exception|Timeout)\b", l)]
        print(output[-3000:] if os.environ.get("QEW_GATE_VERBOSE") else "\n".join(lines[:15] + causes[:5]) or output[-1500:])
    slow = sorted(results, key=lambda r: -r[3])[:5]
    print("slowest: " + ", ".join(f"{r[0]} {r[3]:.0f}s" for r in slow))
    total = sum(r[2] for r in results)
    print(f"{len(modules)} modules, {total} tests, {len(failed)} failing modules, {time.monotonic() - started:.0f}s")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
