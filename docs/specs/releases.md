# Releases

- No GitHub workflows: there is no CI and no release automation on the forge. Verification is local:
  `python3 scripts/gate.py` (mypy strict and ruff, then every walk in parallel; `python3 -m unittest discover -s tests` is the
  serial equivalent) must pass before any push or release.
- Every release updates the specs: before tagging, reread `docs/specs/` and the README for what the release changed (commands, flags,
  defaults, schema, requirements, the release steps below) and fix whatever disagrees. A release with a stale spec is not done.
- The steps of a release, all local and all through qew where qew has the command:
  1. The work is finished red then green (`qew create`, `qew start`, RED and GREEN changes) and the gate passes.
  2. `qew pr open ITEM --head BRANCH`, `qew pr ready N`, `qew pr merge N`, then `qew complete ITEM` once the changes are on main.
  3. A release commit on main sets `__version__` in `src/qew/__init__.py` and `minimum_version` in `.qew/config.toml` to the new
     version, titled `Release X: summary; minimum_version X`. A change of ledger schema also migrates qew's own ledger
     (`qew migrate-layout`) and says so in the notes.
  4. `python3 scripts/setup_user.py` installs it for this user (the other lanes run the installed `qew`; see installation.md).
  5. The tag `vX` points at the release commit; push it and publish the notes with `gh release create vX`.
  6. Whoever runs a ledger on qew is told the version (a changed `minimum_version` stops older installs).
- Optional release artifacts, cut from a clean checkout whose package version equals the tag `vVERSION`:
  build the wheel and source archive with the pinned tools in `requirements/build.txt`, run
  `scripts/release_metadata.py` (SPDX 2.3 SBOM and `SHA256SUMS`; it refuses outputs inside the
  distribution directory and non-regular files), then publish with `scripts/upload_release.py`.
- Qew has no third-party runtime packages and needs the Python 3.15+ interpreter; build tools are release inputs only.
- `scripts/release.py check|build --tag vVERSION --source-sha SHA` is the local release script. Before anything is
  built it refuses (exit 2) unless the source is a full commit id (a tag or branch name is never a source), the package
  version equals the tag, `HEAD` is that commit and the working tree is clean (messages contain `differs`). `build`
  then runs the pinned build and `scripts/release_metadata.py` into `--out`. `tests/test_release_check_walk.py` pins it.
- Decision: release verification is local, not a forge workflow. The workflow-only checks of a forge-hosted flow (the
  checkout uses the event commit, never a movable tag) live in `scripts/release.py`, which takes the commit id itself.
