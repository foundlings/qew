"""Forge support priorities are documented and held to: GitHub and Forgejo/Gitea first, GitLab as it is.

Each test is a scenario; its docstring names the failure it prevents.
"""

import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

ROOT = Path(__file__).resolve().parents[1]
# The walks of the authority features (claims, mirrors, links, repos, fork, contests): first-class forges only.
AUTHORITY_WALKS = ("test_claim_authority_walk", "test_mirror_walk", "test_header_links_walk", "test_repo_push_walk",
                   "test_ledger_fork_walk", "test_contested_walk", "test_authority_walk")


class ForgePriorityWalk(unittest.TestCase):
    def test_the_forge_spec_states_the_priority(self) -> None:
        """Prevents guessing where effort goes: forges.md names the first-class forges and says GitLab is not extended."""
        text = (ROOT / "docs" / "specs" / "forges.md").read_text(encoding="utf-8")
        section = re.search(r"## Support priority\n(.*?)(\n## |\Z)", text, re.S)
        self.assertIsNotNone(section, "forges.md has no `## Support priority` section")
        body = section.group(1)
        for needle in ("GitHub", "Forgejo", "Gitea", "first-class", "GitLab", "not extended"):
            self.assertIn(needle, body)

    def test_authority_features_are_not_tested_against_gitlab(self) -> None:
        """Prevents silent scope creep: no authority walk drives the GitLab fake, so GitLab keeps only its existing tests."""
        for name in AUTHORITY_WALKS:
            path = ROOT / "tests" / f"{name}.py"
            self.assertTrue(path.is_file(), f"{name} is listed as an authority walk but does not exist")
            self.assertNotIn("fake_gitlab", path.read_text(encoding="utf-8"), name)


if __name__ == "__main__":
    unittest.main()
