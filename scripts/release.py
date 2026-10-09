#!/usr/bin/env python3
"""Local release steps for Qew: verify the source checkout, then build and attest.

There is no forge CI; the checks that a workflow would make run here, before any artifact exists.
"""

import argparse
from pathlib import Path
import re
import subprocess
import sys

SCRIPTS = Path(__file__).resolve().parent
FULL_SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
VERSION_LINE = re.compile(r'__version__\s*=\s*"([^"]+)"')


class ReleaseError(Exception):
    pass


def git(root: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(["git", "-C", str(root), *arguments], capture_output=True, text=True, check=False)
    except OSError as error:
        raise ReleaseError(f"cannot run git: {error}") from error
    if result.returncode != 0:
        raise ReleaseError(f"git {' '.join(arguments)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def package_version(root: Path) -> str:
    try:
        text = (root / "src" / "qew" / "__init__.py").read_text(encoding="utf-8")
    except OSError as error:
        raise ReleaseError(f"cannot read the package version: {error}") from error
    match = VERSION_LINE.search(text)
    if match is None:
        raise ReleaseError("src/qew/__init__.py has no __version__")
    return match.group(1)


def check(root: Path, tag: str, source_sha: str) -> str:
    """Refuse unless the checkout is exactly the recorded commit, clean, at the tag's version. Returns the version."""
    if not FULL_SHA.fullmatch(source_sha):
        raise ReleaseError("--source-sha must be a full commit id (lowercase hex), never a tag or branch name")
    version = package_version(root)
    if tag != f"v{version}":
        raise ReleaseError(f"package version {version} does not match tag {tag}")
    head = git(root, "rev-parse", "HEAD")
    if head != source_sha:
        raise ReleaseError(f"the checkout differs from the source commit: HEAD is {head}, expected {source_sha}")
    if git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ReleaseError("the checkout differs from the source commit: the working tree has uncommitted changes")
    return version


def build(root: Path, out: Path, version: str, source_sha: str, created: str) -> None:
    dist = out / "dist"
    for command in (
        [sys.executable, "-m", "build", "--no-isolation", "--outdir", str(dist), str(root)],
        [sys.executable, str(SCRIPTS / "release_metadata.py"), "--dist", str(dist), "--sbom", str(out / "qew.spdx.json"),
         "--checksums", str(out / "SHA256SUMS"), "--version", version, "--source-sha", source_sha, "--created", created],
    ):
        if subprocess.run(command, check=False).returncode != 0:
            raise ReleaseError(f"step failed: {' '.join(command[:3])}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("step", choices=["check", "build"], help="check the source, or check then build and attest")
    parser.add_argument("--root", type=Path, default=SCRIPTS.parent, help="source checkout")
    parser.add_argument("--tag", required=True, help="release tag vVERSION")
    parser.add_argument("--source-sha", required=True, help="full commit id being released")
    parser.add_argument("--out", type=Path, help="build: directory for dist, the SBOM and SHA256SUMS")
    parser.add_argument("--created", help="build: ISO-8601 timestamp with a timezone for the SBOM")
    arguments = parser.parse_args()
    try:
        version = check(arguments.root, arguments.tag, arguments.source_sha)
        if arguments.step == "build":
            if arguments.out is None or arguments.created is None:
                raise ReleaseError("build needs --out and --created")
            build(arguments.root, arguments.out, version, arguments.source_sha, arguments.created)
    except ReleaseError as error:
        print(f"release error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
