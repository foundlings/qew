#!/usr/bin/env python3
"""Run the test suite with one worker process per test module, in parallel; `--changed` runs only what a change can affect.

    python3 scripts/gate.py [-j N] [--timeout SECONDS] [--changed [--base REV]] [--list] [MODULE_SUBSTRING ...]

Every walk builds its own temporary repositories, so modules are independent; the serial
`python3 -m unittest discover -s tests` stays valid and gives the same verdict, only slower.
Prints a summary and the failing tests of any module that fails; exit status 1 on any failure.

The full run (no `--changed`) is the release gate. It also records, per module, which `src/qew` files its processes ran
(`QEW_FILE_MAP`, see `qew.filemap`) and how long it took, in `.gate-cache/`. `--changed` takes the files that differ from
`--base` (default `main`; jj or git, uncommitted work included) and runs

  - a changed test module, and every module that imports a changed test helper;
  - every module whose recorded run touched a changed `src/qew` file (a new file: the modules that ran its importers);
  - every module whose source names any other changed file (docs, scripts, config, fixtures a walk reads);
  - the type check of the changed `src/qew` files and everything that imports them, in parallel with the tests (the whole
    type check when its own configuration changed).

Without a recorded map `--changed` runs everything and records it. Slow modules start first, so the longest one does not
begin last; modules run on every core.
"""

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
CACHE = Path(os.environ.get("QEW_GATE_CACHE") or ROOT / ".gate-cache")
FILE_MAP = CACHE / "files.json"
TIMES = CACHE / "times.json"
TYPECHECK_INPUTS = ("pyproject.toml", "requirements/typecheck.txt", "scripts/typecheck.py")


# -- what changed ---------------------------------------------------------------------------------------------------------------

def changed_files(base: str) -> list[str]:
    """Repository-relative paths that differ from `base`, uncommitted work included."""
    if (ROOT / ".jj").is_dir():
        done = subprocess.run(["jj", "--no-pager", "diff", "--from", base, "--name-only"], cwd=ROOT, capture_output=True, text=True, check=False)
        if done.returncode != 0:
            raise SystemExit(f"gate: jj could not diff against {base}: {done.stderr.strip()}")
        return sorted({line.strip() for line in done.stdout.splitlines() if line.strip()})
    tracked = subprocess.run(["git", "diff", "--name-only", base], cwd=ROOT, capture_output=True, text=True, check=False)
    if tracked.returncode != 0:
        raise SystemExit(f"gate: git could not diff against {base}: {tracked.stderr.strip()}")
    fresh = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True, check=False)
    return sorted({line.strip() for line in (tracked.stdout + fresh.stdout).splitlines() if line.strip()})


# -- import graphs (static, lazy imports included) ------------------------------------------------------------------------------

def imports_of(path: Path, package: str, known: set[str]) -> set[str]:
    """The modules of `package` (its file stems, among `known`) that the file at `path` imports anywhere, even inside a function."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.module == package:
                found.update(alias.name for alias in node.names if alias.name in known)
            elif node.module.startswith(package + "."):
                found.add(node.module.split(".")[1])
        elif isinstance(node, ast.Import):
            found.update(alias.name.split(".")[1] for alias in node.names if alias.name.startswith(package + "."))
    return {name for name in found if name in known}


def graph(directory: Path, package: str) -> dict[str, set[str]]:
    stems = {path.stem for path in directory.glob("*.py")}
    return {path.stem: imports_of(path, package, stems) for path in sorted(directory.glob("*.py"))}


def dependents(seeds: set[str], deps: dict[str, set[str]]) -> set[str]:
    """`seeds` and every module that imports one of them, directly or through others."""
    reverse: dict[str, set[str]] = {}
    for module, imported in deps.items():
        for name in imported:
            reverse.setdefault(name, set()).add(module)
    found, queue = set(seeds), list(seeds)
    while queue:
        for importer in reverse.get(queue.pop(), ()):
            if importer not in found:
                found.add(importer)
                queue.append(importer)
    return found


# -- choosing the modules -------------------------------------------------------------------------------------------------------

def select(changed: list[str], trace: dict[str, list[str]] | None, modules: list[str], texts: dict[str, str],
           src_deps: dict[str, set[str]], test_deps: dict[str, set[str]]) -> list[str] | None:
    """The test modules a change can affect, or None when that cannot be said (no recorded map for a changed source file)."""
    picked: set[str] = set()
    unmapped: list[str] = []
    for path in changed:
        stem = Path(path).stem
        if path.startswith("src/qew/") and path.endswith(".py"):
            if trace is None:
                return None
            hit = {module for module, files in trace.items() if path in files}
            if hit:
                picked |= hit
            else:
                unmapped.append(stem)
        elif path.startswith("tests/") and path.endswith(".py") and "/" not in path[len("tests/"):]:
            picked |= {module for module in dependents({stem}, test_deps) if module.startswith("test_")}
        else:
            picked |= {module for module in modules if path in texts[module] or Path(path).name in texts[module]}
    for stem in unmapped:  # a source file no module ran: the modules that ran a file importing it stand for it
        importers = dependents({stem}, src_deps) - {stem}
        files = {f"src/qew/{name}.py" for name in importers}
        hit = {module for module, ran in (trace or {}).items() if files & set(ran)}
        if not hit:
            return None
        picked |= hit
    return [module for module in modules if module in picked]


def typecheck_scope(changed: list[str], src_deps: dict[str, set[str]]) -> list[str] | None:
    """The paths to type-check: [] for none, None for everything (the checker's own configuration changed)."""
    if any(path in TYPECHECK_INPUTS for path in changed):
        return None
    sources = {Path(path).stem for path in changed if path.startswith("src/qew/") and path.endswith(".py")}
    paths = sorted(f"src/qew/{name}.py" for name in dependents(sources, src_deps) if (ROOT / "src" / "qew" / f"{name}.py").is_file())
    return paths + sorted(path for path in changed if path.startswith("scripts/") and path.endswith(".py") and (ROOT / path).is_file())


# -- running ----------------------------------------------------------------------------------------------------------------------

def run_module(name: str, timeout: float, trace_dir: Path):
    started = time.monotonic()
    trace_dir.mkdir(parents=True, exist_ok=True)
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "QEW_UNDER_TEST": "1", "QEW_FILE_MAP": str(trace_dir)}
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


def files_run(trace_dir: Path) -> list[str]:
    """The `src/qew` files any process of the module ran (one list per process, written when it exits)."""
    found: set[str] = set()
    for part in trace_dir.glob("*.txt") if trace_dir.is_dir() else []:
        found.update(line for line in part.read_text(encoding="utf-8").splitlines() if line)
    return sorted(found)


def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def start_typecheck(scope: list[str] | None) -> subprocess.Popen[str]:
    return subprocess.Popen([sys.executable, str(ROOT / "scripts" / "typecheck.py"), *(scope or [])], cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 4)
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--changed", action="store_true", help="only the modules and type checks the change from --base can affect")
    parser.add_argument("--base", default="main", help="with --changed: the revision to compare with (default main)")
    parser.add_argument("--changed-files", nargs="*", default=None, metavar="FILE", help="like --changed, for exactly these repository-relative files")
    parser.add_argument("--list", action="store_true", help="with --changed: print what would run and stop")
    parser.add_argument("filters", nargs="*")
    arguments = parser.parse_args()
    modules = sorted(p.stem for p in (ROOT / "tests").glob("test_*.py"))
    if arguments.filters:
        modules = [m for m in modules if any(f in m for f in arguments.filters)]
    trace = load(FILE_MAP) or None
    scope: list[str] | None = []
    if arguments.changed or arguments.changed_files is not None:
        files = sorted(arguments.changed_files) if arguments.changed_files is not None else changed_files(arguments.base)
        if not files:
            print(f"gate: nothing differs from {arguments.base}; nothing to run")
            return 0
        texts = {m: (ROOT / "tests" / f"{m}.py").read_text(encoding="utf-8") for m in modules}
        picked = select(files, trace, modules, texts, graph(ROOT / "src" / "qew", "qew"), graph(ROOT / "tests", "tests"))
        scope = typecheck_scope(files, graph(ROOT / "src" / "qew", "qew"))
        if picked is None:
            print("gate: no recorded map for the changed source files; running every module (this run records it)")
        else:
            modules = picked
        print(f"gate: {len(files)} changed files -> {len(modules)} modules"
              + ("" if scope == [] else ", type check of " + ("everything" if scope is None else f"{len(scope)} files")))
        if arguments.list:
            print("\n".join(modules))
            if scope:
                print("typecheck: " + " ".join(scope))
            return 0
    elif arguments.list:
        raise SystemExit("gate: --list needs --changed or --changed-files")
    previous = load(TIMES)
    modules = sorted(modules, key=lambda m: -previous.get(m, 120.0))  # slowest first: the longest module never starts last
    changed = arguments.changed or arguments.changed_files is not None
    checker = start_typecheck(scope) if changed and scope != [] else None
    jobs = max(1, arguments.jobs - (1 if checker else 0))  # the type checker is one busy process beside the pool
    started = time.monotonic()
    trace_root = CACHE / "trace"
    shutil.rmtree(trace_root, ignore_errors=True)
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        results = list(pool.map(lambda name: run_module(name, arguments.timeout, trace_root / name), modules))
    ran_map = load(FILE_MAP)
    seen = {name: files_run(trace_root / name) for name in modules}
    ran_map.update({name: files for name, files in seen.items() if files})  # a module that left no trace keeps its old entry
    CACHE.mkdir(exist_ok=True)
    FILE_MAP.write_text(json.dumps(ran_map, sort_keys=True), encoding="utf-8")
    TIMES.write_text(json.dumps({**previous, **{r[0]: round(r[3], 1) for r in results}}, sort_keys=True), encoding="utf-8")
    shutil.rmtree(trace_root, ignore_errors=True)
    failed = [r for r in results if r[1] != 0]
    for name, code, _, seconds, output in failed:
        print(f"=== {name} (exit {code}, {seconds:.0f}s)")
        lines = [l for l in output.splitlines() if l.startswith(("FAIL:", "ERROR:", "TIMEOUT"))]
        causes = [l[:400] for l in output.splitlines() if re.match(r"^[A-Za-z_.]*(Error|Exception|Timeout)\b", l)]
        print(output[-3000:] if os.environ.get("QEW_GATE_VERBOSE") else "\n".join(lines[:15] + causes[:5]) or output[-1500:])
    checked_ok = True
    if checker is not None:
        report = checker.communicate()[0]
        checked_ok = checker.returncode == 0
        print("typecheck: " + ("ok" if checked_ok else "FAILED\n" + report[-3000:]))
    slow = sorted(results, key=lambda r: -r[3])[:5]
    print("slowest: " + ", ".join(f"{r[0]} {r[3]:.0f}s" for r in slow))
    total = sum(r[2] for r in results)
    print(f"{len(modules)} modules, {total} tests, {len(failed)} failing modules, {time.monotonic() - started:.0f}s")
    return 1 if failed or not checked_ok else 0


if __name__ == "__main__":
    raise SystemExit(main())
