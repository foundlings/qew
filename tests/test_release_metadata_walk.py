"""Release metadata command walks."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMMAND = PROJECT_ROOT / "scripts" / "release_metadata.py"


class ReleaseMetadataWalk(unittest.TestCase):
    def test_release_artifacts_get_stable_hashes_and_an_spdx_sbom(self) -> None:
        """Published bytes and the dependency claim must be independently checkable."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            distribution = temporary / "dist"
            distribution.mkdir()
            wheel = distribution / "qew-0.1.0-py3-none-any.whl"
            source = distribution / "qew-0.1.0.tar.gz"
            wheel.write_bytes(b"wheel bytes")
            source.write_bytes(b"source bytes")
            sbom = temporary / "qew.spdx.json"
            checksums = temporary / "SHA256SUMS"

            result = self._run(distribution, sbom, checksums)

            self.assertEqual(result.returncode, 0, result.stderr)
            document = json.loads(sbom.read_text(encoding="utf-8"))
            self.assertEqual(document["spdxVersion"], "SPDX-2.3")
            self.assertEqual(document["name"], "qew-0.1.0")
            self.assertEqual(document["creationInfo"]["created"], "2026-09-28T07:00:00Z")
            package = document["packages"][0]
            self.assertEqual(package["name"], "qew")
            self.assertEqual(package["versionInfo"], "0.1.0")
            self.assertEqual(package["licenseDeclared"], "Apache-2.0")
            self.assertEqual(package["externalRefs"][0]["referenceLocator"], "pkg:pypi/qew@0.1.0")
            self.assertEqual(document["relationships"][0]["relationshipType"], "DESCRIBES")

            expected = []
            for artifact in (wheel, source, sbom):
                digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
                expected.append(f"{digest}  {artifact.name}")
            self.assertEqual(checksums.read_text(encoding="utf-8").splitlines(), expected)

    def test_symlinked_release_artifact_is_refused(self) -> None:
        """Metadata must describe bytes owned by the release directory, not an indirection."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            distribution = temporary / "dist"
            distribution.mkdir()
            target = temporary / "mutable.whl"
            target.write_bytes(b"mutable")
            (distribution / "qew-0.1.0.whl").symlink_to(target)

            result = self._run(
                distribution,
                temporary / "qew.spdx.json",
                temporary / "SHA256SUMS",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("regular files", result.stderr)

    def test_checksums_inside_the_distribution_directory_are_refused(self) -> None:
        """A checksums file in dist would be uploaded as a duplicate asset."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            distribution = temporary / "dist"
            distribution.mkdir()
            (distribution / "qew-0.1.0.whl").write_bytes(b"wheel bytes")
            checksums = distribution / "SHA256SUMS"

            result = self._run(distribution, temporary / "qew.spdx.json", checksums)

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("--checksums must be outside --dist", result.stderr)
            self.assertFalse(checksums.exists())

    def _run(self, distribution: Path, sbom: Path, checksums: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                sys.executable,
                str(COMMAND),
                "--dist",
                str(distribution),
                "--sbom",
                str(sbom),
                "--checksums",
                str(checksums),
                "--version",
                "0.1.0",
                "--source-sha",
                "0123456789abcdef0123456789abcdef01234567",
                "--created",
                "2026-09-28T09:00:00+02:00",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )


if __name__ == "__main__":
    unittest.main()
