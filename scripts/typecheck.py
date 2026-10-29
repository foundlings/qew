#!/usr/bin/env python3
"""Type-check Qew with mypy --strict and enforce modern typing with ruff, on Python 3.15; exit status 1 on any finding.

    python3 scripts/typecheck.py [--setup] [PATH ...]

The checker is a developer tool in `.venv-typecheck/` (pinned in requirements/typecheck.txt), never a runtime
dependency. `--setup` only creates or refreshes that environment. Without PATH it checks `src/qew` (mypy) and
`src/qew` plus `scripts` (ruff: built-in generics, `X | None`, no deprecated typing imports, no unused imports).
A missing checker or interpreter is an error, never a skipped check.
"""

import argparse
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv-typecheck"
REQUIREMENTS = ROOT / "requirements" / "typecheck.txt"
STAMP = VENV / ".requirements"


def python315() -> str:
    candidates = ["/opt/homebrew/bin/python3.15", "/usr/local/bin/python3.15", sys.executable, shutil.which("python3.15") or ""]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            found = subprocess.run([candidate, "-c", "import sys; print(sys.version_info[:2] == (3, 15))"],
                                   capture_output=True, text=True, check=False).stdout.strip()
        except OSError:
            continue
        if found == "True":
            return candidate
    raise SystemExit("typecheck: Python 3.15 not found; install python@3.15 (`brew install python@3.15`)")


def ensure_environment() -> Path:
    wanted = REQUIREMENTS.read_text(encoding="utf-8")
    interpreter = VENV / "bin" / "python"
    if not interpreter.exists():
        subprocess.run([python315(), "-m", "venv", str(VENV)], check=True)
    if not STAMP.exists() or STAMP.read_text(encoding="utf-8") != wanted:
        subprocess.run([str(interpreter), "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                        "-r", str(REQUIREMENTS)], check=True)
        STAMP.write_text(wanted, encoding="utf-8")
    return interpreter


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--setup", action="store_true", help="only create or refresh the checker environment")
    parser.add_argument("--config", default=str(ROOT / "pyproject.toml"), help="mypy configuration file")
    parser.add_argument("paths", nargs="*", help="files or directories to check (default src/qew)")
    arguments = parser.parse_args()
    interpreter = ensure_environment()
    if arguments.setup:
        return 0
    # The scripts are linted, never held to mypy --strict (a default run does not): a named script goes to ruff alone.
    checked = [path for path in arguments.paths if not (ROOT / path).resolve().is_relative_to(ROOT / "scripts")]
    if arguments.paths and not checked:
        mypy = subprocess.CompletedProcess([], 0)
    else:
        mypy = subprocess.run([str(interpreter), "-m", "mypy", "--config-file", arguments.config, *checked], cwd=ROOT, check=False)
    ruff_paths = arguments.paths or ["src/qew", "scripts"]
    ruff = subprocess.run([str(VENV / "bin" / "ruff"), "check", "--config", arguments.config, "--no-cache", *ruff_paths], cwd=ROOT, check=False)
    return 1 if mypy.returncode or ruff.returncode else 0


if __name__ == "__main__":
    sys.exit(main())
