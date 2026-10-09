# Releases

- No GitHub workflows: there is no CI and no release automation on the forge. Verification is local:
  `python3 -m unittest discover -s tests` must pass before any push or release.
- A release is cut locally from a clean checkout whose package version equals the tag `vVERSION`:
  build the wheel and source archive with the pinned tools in `requirements/build.txt`, run
  `scripts/release_metadata.py` (SPDX 2.3 SBOM and `SHA256SUMS`; it refuses outputs inside the
  distribution directory and non-regular files), then publish with `scripts/upload_release.py`.
- Qew has no Python runtime dependencies; build tools are release inputs only.
- `scripts/release.py check|build --tag vVERSION --source-sha SHA` is the local release script. Before anything is
  built it refuses (exit 2) unless the source is a full commit id (a tag or branch name is never a source), the package
  version equals the tag, `HEAD` is that commit and the working tree is clean (messages contain `differs`). `build`
  then runs the pinned build and `scripts/release_metadata.py` into `--out`. `tests/test_release_check_walk.py` pins it.
- Decision: release verification is local, not a forge workflow. The workflow-only checks of a forge-hosted flow (the
  checkout uses the event commit, never a movable tag) live in `scripts/release.py`, which takes the commit id itself.
