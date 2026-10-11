"""The evidence push hook checks new work only, and reads only the leading ids of a `Refs:` trailer.

Each test is a scenario; its docstring names the failure it prevents. An integrator pushes a branch built on other actors' commits
that are already published on their own pull request branches: those are not theirs to evidence, and prose after the ids is prose.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import git, jj, jj_available
from tests.test_hook_v1_walk import feed
from tests.test_vcs_hook_walk import VcsHookCase


class RefsTrailerParsingWalk(VcsHookCase):
    def message(self, text: str):
        path = self.world.base / "message.txt"
        path.write_text(text, encoding="utf-8")
        return self.check("--message-file", str(path))

    def test_prose_after_the_ids_is_not_read_as_ids(self) -> None:
        """Prevents 'cites Generated/only/through, which is not a ledger item': a sentence after the id ends the citation."""
        prose = "Generated only through c2_ade_conversion_inventory.py and its checks."
        for line in (f"Refs: {self.item}. {prose}", f"Refs: {self.item}; {prose}", f"Refs: {self.item}: {prose}"):
            with self.subTest(line=line):
                result = self.message(f"work\n\n{line}\n")
                self.assertEqual(result.stdout.splitlines()[:1], [f"known {self.item}"], result.stdout + result.stderr)
                for word in ("Generated", "only", "through"):
                    self.assertNotIn(f"unknown {word}", result.stdout)

    def test_a_list_of_ids_still_cites_every_id_and_a_typo_is_still_unknown(self) -> None:
        """Prevents the fix hiding real citations: comma and space separated ids, then a typo, are all still read."""
        other = self.world.create("--title", "Other", "--quote", "Alexander: 'please track this work in the Qew ledger now'")
        result = self.message(f"work\n\nRefs: {self.item}, {other} TYPO99\n")
        self.assertEqual(result.stdout.splitlines(), [f"known {self.item}", f"known {other}", "unknown TYPO99"])


@unittest.skipUnless(jj_available(), "jj is not installed")
class PublishedCommitsWalk(VcsHookCase):
    def setUp(self) -> None:
        super().setUp()
        jj(self.world.code, "git", "init", "--colocate")
        self.remote = self.world.base / "published.git"
        git(self.world.base, "init", "-q", "--bare", "-b", "main", str(self.remote))
        jj(self.world.code, "git", "remote", "add", "origin", str(self.remote))

    def commit(self, message: str) -> str:
        jj(self.world.code, "commit", "-m", message)
        return jj(self.world.code, "log", "--no-graph", "-r", "@-", "-T", "change_id.short(12)")

    def guard(self, command: str) -> str:
        result = feed(self.world, "pre-tool", "claude", command, cwd=str(self.world.code))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"] if result.stdout else ""

    def test_commits_already_on_a_remote_branch_are_not_checked_again(self) -> None:
        """Prevents blocking the integrator: another actor's published commit needs no evidence from them, only the new work does."""
        published = self.commit(f"work\n\nRefs: {self.item}")  # an elf's commit, unevidenced, already on its own pull request branch
        jj(self.world.code, "bookmark", "create", "elf-branch", "-r", "@-")
        jj(self.world.code, "git", "push", "--bookmark", "elf-branch")
        mine = self.commit(f"integration\n\nRefs: {self.item}")
        jj(self.world.code, "bookmark", "create", "integrator", "-r", "@-")
        self.assertIn(f"--change {mine}", self.guard("jj git push --bookmark integrator"), "new work still needs its evidence")
        self.assertEqual(self.world.run("evidence", self.item, "--change", mine).returncode, 0)
        reason = self.guard("jj git push --bookmark integrator")
        self.assertEqual(reason, "", f"the published commit {published} must not be demanded: {reason}")
