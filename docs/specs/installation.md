# Installation

- Requirements: Python 3.10+ and an authenticated GitHub CLI (`gh`). Qew has no
  Python runtime dependencies. macOS's system Python is older than 3.10, so run
  the script with a newer `python3` there.
- Linux and macOS: `scripts/setup_user.py` installs the checkout into a virtual
  environment under `$XDG_DATA_HOME/qew/venv` (default `~/.local/share`) and
  symlinks its `qew` entry point into `~/.local/bin`, the per-user executable
  directory of the XDG base directory convention and systemd file hierarchy.
- The script replaces only its own symlink and refuses any other file at that
  path before creating anything. It warns, without failing, when
  `~/.local/bin` is not on `PATH` or `gh` is missing.
- Other systems are refused (exit 2). Failed environment or install commands
  exit 3.
- Homebrew is not supported: its fetch sandbox cannot use the GitHub CLI
  credential helper for the private enterprise host.
