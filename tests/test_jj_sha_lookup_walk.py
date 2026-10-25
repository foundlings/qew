"""Resolving a commit sha in jj: a hex sha is a commit id, a k-z string is a change id, and a failed lookup is an error.

Adversary: jj 0.46.0 refuses `change_id(<hex sha>)` as a revset parse error; the lookup read that failure as "not an ancestor",
so `qew pr merge A B C` said no head contained the others although the lead held every one (seen live on foundlings).
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import code_vcs
from qew.errors import QewError
from tests.ledger_harness import jj, jj_available


def commit_id(repo: Path, revset: str) -> str:
    return jj(repo, "--ignore-working-copy", "log", "--no-graph", "-r", revset, "-T", "commit_id").strip()


@unittest.skipUnless(jj_available(), "jj is not installed")
class JjShaLookupWalk(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name)
        jj(self.repo, "git", "init")
        jj(self.repo, "describe", "-m", "base")
        jj(self.repo, "bookmark", "create", "main", "-r", "@")
        jj(self.repo, "new", "-m", "lead")
        self.lead = commit_id(self.repo, "@")
        jj(self.repo, "new", "-m", "later")
        self.later = commit_id(self.repo, "@")
        jj(self.repo, "bookmark", "set", "main", "-r", self.lead, "--allow-backwards")

    def test_a_full_hex_sha_resolves_as_an_ancestor_of_the_lead(self) -> None:
        """Prevents the batch lead lookup failing on a forge head sha: the 40-hex sha is a commit id, never a change id."""
        base = commit_id(self.repo, "main-")
        self.assertEqual(len(base), 40)
        self.assertTrue(code_vcs.is_ancestor(self.repo, "jj", base, "main"))
        self.assertTrue(code_vcs.is_ancestor(self.repo, "jj", self.lead, "main"))
        self.assertFalse(code_vcs.is_ancestor(self.repo, "jj", self.later, "main"))

    def test_landing_of_a_hex_sha_names_where_it_stands(self) -> None:
        """Prevents a hex sha reading as unknown to this checkout under jj."""
        self.assertEqual(code_vcs.landing(self.repo, "jj", self.lead, "main")[0], code_vcs.Landing.LANDED)
        self.assertEqual(code_vcs.landing(self.repo, "jj", self.later, "main")[0], code_vcs.Landing.NOT_ANCESTOR)
        base = commit_id(self.repo, "main-")
        many = code_vcs.landing_many(self.repo, "jj", [self.lead, self.later, base], "main")
        self.assertEqual({change: where for change, (where, _) in many.items()},
                         {self.lead: code_vcs.Landing.LANDED, self.later: code_vcs.Landing.NOT_ANCESTOR, base: code_vcs.Landing.LANDED})

    def test_a_failed_revset_is_an_error_not_a_negative_answer(self) -> None:
        """Prevents a jj failure being reported as 'not an ancestor': the caller must see that the lookup itself failed."""
        real = code_vcs._run

        def failing(root: Path, argv: list[str]) -> "subprocess.CompletedProcess[str]":
            result = real(root, argv)
            if "log" in argv and any(a.startswith("::(") for a in argv):
                return subprocess.CompletedProcess(argv, 1, "", "Error: Failed to parse revset")
            return result

        with mock.patch.object(code_vcs, "_run", failing):
            with self.assertRaises(QewError) as caught:
                code_vcs.is_ancestor(self.repo, "jj", self.lead, "main")
        self.assertEqual(caught.exception.exit_code, 3)


if __name__ == "__main__":
    unittest.main()
