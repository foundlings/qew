#!/usr/bin/env python3
"""Build deterministic checksums and an SPDX SBOM for Qew releases."""

import argparse
from datetime import datetime, UTC
import hashlib
import json
from pathlib import Path
import re
import sys
import uuid


SOURCE_SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
VERSION = re.compile(r"[0-9]+(?:\.[0-9]+)*(?:[A-Za-z0-9.+-]*)")
REPOSITORY = "https://github.com/887/qew"


class MetadataError(ValueError):
    """The release inputs cannot be represented safely."""


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", required=True, type=Path)
    parser.add_argument("--sbom", required=True, type=Path)
    parser.add_argument("--checksums", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--created", required=True)
    return parser.parse_args()


def regular_artifacts(distribution: Path) -> list[Path]:
    if not distribution.is_dir() or distribution.is_symlink():
        raise MetadataError("--dist must be a real directory")
    artifacts = sorted(distribution.iterdir(), key=lambda path: path.name)
    if not artifacts:
        raise MetadataError("--dist must contain at least one release artifact")
    if any(path.is_symlink() or not path.is_file() for path in artifacts):
        raise MetadataError("release artifacts must be regular files, never links or directories")
    return artifacts


def normalized_created(raw: str) -> str:
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise MetadataError("--created must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise MetadataError("--created must include a timezone")
    return parsed.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def spdx_document(version: str, source_sha: str, created: str) -> dict:
    package_id = "SPDXRef-Package-qew"
    namespace_seed = f"{REPOSITORY}/releases/{version}/{source_sha}"
    namespace_id = uuid.uuid5(uuid.NAMESPACE_URL, namespace_seed)
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"qew-{version}",
        "documentNamespace": f"{REPOSITORY}/spdx/{namespace_id}",
        "creationInfo": {
            "created": created,
            "creators": ["Organization: example-org", "Tool: qew-release-metadata"],
        },
        "packages": [
            {
                "name": "qew",
                "SPDXID": package_id,
                "versionInfo": version,
                "downloadLocation": f"{REPOSITORY}/releases/tag/v{version}",
                "filesAnalyzed": False,
                "licenseConcluded": "Apache-2.0",
                "licenseDeclared": "Apache-2.0",
                "copyrightText": "NOASSERTION",
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceType": "purl",
                        "referenceLocator": f"pkg:pypi/qew@{version}",
                    }
                ],
            }
        ],
        "relationships": [
            {
                "spdxElementId": "SPDXRef-DOCUMENT",
                "relationshipType": "DESCRIBES",
                "relatedSpdxElement": package_id,
            }
        ],
    }


def write_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_checksums(path: Path, artifacts: list[Path]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{sha256(artifact)}  {artifact.name}" for artifact in artifacts]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    arguments = parse_arguments()
    try:
        if not VERSION.fullmatch(arguments.version):
            raise MetadataError("--version is not a supported package version")
        if not SOURCE_SHA.fullmatch(arguments.source_sha):
            raise MetadataError("--source-sha must be a 40- or 64-character lowercase hex digest")
        artifacts = regular_artifacts(arguments.dist)
        for option, output in (("--sbom", arguments.sbom), ("--checksums", arguments.checksums)):
            if output.parent.resolve() == arguments.dist.resolve():
                raise MetadataError(f"{option} must be outside --dist")
        created = normalized_created(arguments.created)
        write_json(arguments.sbom, spdx_document(arguments.version, arguments.source_sha, created))
        write_checksums(arguments.checksums, [*artifacts, arguments.sbom])
    except (MetadataError, OSError) as error:
        print(f"release metadata error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
