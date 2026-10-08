# Installation

- Requirements: Python 3.11+, git and/or jj for the code repo. Qew has no Python runtime dependencies.
  Forge tokens come from the environment (`GITHUB_TOKEN`, `FORGEJO_TOKEN`/`GITEA_TOKEN`, `GITLAB_TOKEN`, or
  the mirror's `token_env`); for github.com `gh auth token` is a fallback.
- Linux and macOS: `scripts/setup_user.py` installs the checkout into a virtual environment under
  `$XDG_DATA_HOME/qew/venv` (default `~/.local/share`) and symlinks its `qew` entry point into
  `~/.local/bin`.
- The script replaces only its own symlink and refuses any other file at that path. Other systems are
  refused (exit 2); failed environment or install commands exit 3.
