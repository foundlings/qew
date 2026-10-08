#!/usr/bin/env python3
"""Upload Qew release files through GitHub CLI."""

import argparse
import os
import subprocess
import sys
from urllib.parse import urlsplit


def github_host(environment: dict) -> str:
    server_url = environment.get("GITHUB_SERVER_URL", "")
    parsed = urlsplit(server_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("GITHUB_SERVER_URL must be an HTTPS server origin")
    if parsed.port is None:
        return parsed.hostname
    return f"{parsed.hostname}:{parsed.port}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("artifacts", nargs="+")
    arguments = parser.parse_args()
    environment = os.environ.copy()
    try:
        environment["GH_HOST"] = github_host(environment)
        result = subprocess.run(
            [
                "gh",
                "release",
                "upload",
                arguments.tag,
                *arguments.artifacts,
                "--repo",
                arguments.repo,
                "--clobber",
            ],
            env=environment,
            check=False,
        )
    except (OSError, ValueError) as error:
        print(f"release upload error: {error}", file=sys.stderr)
        return 2
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
