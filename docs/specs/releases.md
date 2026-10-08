# Releases

- No GitHub workflows: there is no CI and no release automation on the forge. Verification is local:
  `python3 -m unittest discover -s tests` must pass before any push or release.
- A release is cut locally from a clean checkout whose package version equals the tag `vVERSION`:
  build the wheel and source archive with the pinned tools in `requirements/build.txt`, run
  `scripts/release_metadata.py` (SPDX 2.3 SBOM and `SHA256SUMS`; it refuses outputs inside the
  distribution directory and non-regular files), then publish with `scripts/upload_release.py`.
- Qew has no Python runtime dependencies; build tools are release inputs only.
- A single local release script that runs these steps is tracked as ledger work.
