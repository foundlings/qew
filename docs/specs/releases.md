# Releases

- Publishing a GitHub release for `vVERSION` builds that exact tag. The tag must
  equal Qew's package version or the job refuses to publish artifacts.
- The job checks out the release event commit (`GITHUB_SHA`, not the movable
  tag name) and fails if the checked out HEAD differs from it, so the build and
  the SBOM source describe the same commit.
- The release contains the wheel, source archive, SPDX 2.3 SBOM, and
  `SHA256SUMS` covering both distributions and the SBOM. The metadata generator
  refuses SBOM and checksum outputs inside the distribution directory.
- GitHub artifact attestations bind the distributions to the workflow and
  source revision. A second attestation binds the SPDX SBOM.
- Release actions are pinned by commit digest. The metadata generator uses only
  Python's standard library and refuses non-regular distribution files.
- Qew has no Python runtime dependencies. Build tools are release inputs, not
  runtime components, and remain visible in the workflow and provenance.
- Pull requests and main run the complete behavioral suite on Python 3.10 and
  3.12. Dependabot proposes cooled updates for action and Python build pins.
