# Installation

- Requirements: Python 3.15+, git and/or jj for the code repo. Qew has no Python runtime dependencies.
  Forge tokens come from the environment (`GITHUB_TOKEN`, `FORGEJO_TOKEN`/`GITEA_TOKEN`, `GITLAB_TOKEN`, or
  the mirror's `token_env`); for github.com `gh auth token` is a fallback.
- Linux and macOS: `sh scripts/install.sh` finds a Python 3.15+ (macOS ships an older one; Homebrew locations are tried) and runs
  `scripts/setup_user.py`, which installs the checkout into a virtual environment under
  `$XDG_DATA_HOME/qew/venv` (default `~/.local/share`) and symlinks its `qew` entry point into
  `~/.local/bin`.
- The script replaces only its own symlink and refuses any other file at that path. Other systems are
  refused (exit 2); failed environment or install commands exit 3.
- The package builds a real wheel (`qew-VERSION-py3-none-any.whl`, no runtime requirements) whose `qew` console script is
  `qew.cli:main`; `tests/test_distribution_walk.py` builds it with the pinned tools, installs it into a clean venv and runs it
  outside the checkout (skipped when `setuptools` is not installed).

## Identity

- Every event actor, claim owner and ledger commit subject comes from `QEW_ACTOR`, else `git config user.name`, else
  `jj config get user.name`; qew never uses the operating-system login and refuses writes when no name is configured.
- `scripts/rewrite-identity.sh [--dry-run] [--push] OLD NEW REPO...` removes one identity from a repository's whole history
  (authors, committers, messages, file revisions, via `git-filter-repo`) and puts another in, re-adding `origin` and
  re-initialising a colocated jj repo. `--push` force-pushes all refs; collaborators must re-clone afterwards.
- Developers: `python3 scripts/typecheck.py` runs `mypy --strict` on `src/qew` from a pinned environment in `.venv-typecheck/` (requirements/typecheck.txt; created on first run, needs network once). It is part of the test suite and needs a Python 3.15 interpreter on PATH.
