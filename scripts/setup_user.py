#!/usr/bin/env python3
"""Install Qew for the current Linux or macOS user and link it into ~/.local/bin."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

SOURCE = Path(__file__).resolve().parents[1]


class SetupError(Exception):
    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


def data_home(home: Path) -> Path:
    configured = os.environ.get("XDG_DATA_HOME", "")
    if configured and Path(configured).is_absolute():
        return Path(configured)
    return home / ".local" / "share"


def check_link(link: Path, target: Path) -> bool:
    """Return whether the link already points at Qew; refuse anything foreign."""
    if link.is_symlink() and Path(os.readlink(link)) == target:
        return True
    if link.exists() or link.is_symlink():
        raise SetupError(f"refusing to replace {link}: it is not the Qew link")
    return False


def on_path(directory: Path) -> bool:
    entries = [Path(entry) for entry in os.environ.get("PATH", "").split(os.pathsep) if entry]
    return directory in entries


def run(command: list) -> None:
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise SetupError(f"command failed: {' '.join(map(str, command))}", 3)


def install() -> None:
    if not (sys.platform.startswith("linux") or sys.platform == "darwin"):
        raise SetupError("this script supports only Linux and macOS")
    if sys.version_info < (3, 11):
        raise SetupError("Qew requires Python 3.11 or newer")
    home = Path.home()
    if not home.is_absolute():
        raise SetupError("HOME must be an absolute path")
    venv = data_home(home) / "qew" / "venv"
    executable = venv / "bin" / "qew"
    bin_dir = home / ".local" / "bin"
    link = bin_dir / "qew"
    linked = check_link(link, executable)
    run([sys.executable, "-m", "venv", str(venv)])
    run(
        [
            str(venv / "bin" / "python"),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--force-reinstall",
            "--no-deps",
            str(SOURCE),
        ]
    )
    if not linked:
        bin_dir.mkdir(parents=True, exist_ok=True)
        link.symlink_to(executable)
    print(f"installed qew: {link} -> {executable}")
    if not on_path(bin_dir):
        print(f"warning: {bin_dir} is not on PATH; add it to your shell profile", file=sys.stderr)


def main() -> int:
    try:
        install()
    except SetupError as error:
        print(f"setup error: {error}", file=sys.stderr)
        return error.code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
