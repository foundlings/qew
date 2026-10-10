"""Walks of the VCS evidence hook: `Refs:` trailers, `qew hook vcs-check`, the git pre-push hook, the jj guard.

Adversary: a push that delivers a cited ledger item whose evidence was never recorded, a lookalike
(body text, wrong case, indented) read as a citation, a typo'd id that silently cites nothing, and a
hook that can be switched off without anyone seeing it in the diff.
"""

import json
import os
import stat
import subprocess
import unittest

from tests.ledger_harness import PROJECT_ROOT, QUOTE, World, git, jj
from tests.test_hook_v1_walk import feed

ZERO = "0" * 40


def push_line(sha: str) -> str:
    return f"refs/heads/main {sha} refs/heads/main {ZERO}\n"


class VcsHookCase(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)
        self.item = self.world.create("--title", "Work", "--quote", QUOTE)
        self.assertEqual(self.world.run("start", self.item).returncode, 0)

    def cited_commit(self, item: str = None) -> str:
        return self.world.commit(f"work\n\nRefs: {item or self.item}")

    def check(self, *arguments: str, stdin: str = None):
        return self.world.run("hook", "vcs-check", *arguments, stdin=stdin)


class TrailerWalk(VcsHookCase):
    def test_known_and_unknown_ids_are_reported_and_lookalikes_are_ignored(self) -> None:
        """A typo'd id must not silently cite nothing; body text and odd casing must not cite anything."""
        message = self.world.base / "message.txt"
        message.write_text(f"subject\n\nRefs: {self.item} in prose is not a trailer\n\nRefs: {self.item}, TYPO99\n", encoding="utf-8")
        result = self.check("--message-file", str(message))
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(result.stdout.splitlines(), [f"known {self.item}", "unknown TYPO99"])
        for lookalike in (
            f"subject\n\nrefs: {self.item}\n", f"subject\n\n  Refs: {self.item}\n", f"subject\n\nRefs {self.item}\n",
            f"subject\n\nNot-Refs: {self.item}\n", f"subject\n\nRefs: #12, https://example.invalid/x;rm\n",
            f"Refs: {self.item}\n\nsubject text after\n",
        ):
            with self.subTest(message=lookalike):
                message.write_text(lookalike, encoding="utf-8")
                result = self.check("--message-file", str(message))
                self.assertEqual((result.returncode, result.stdout), (0, ""), result.stderr)


class PrePushWalk(VcsHookCase):
    def test_cited_id_with_its_commit_recorded_as_evidence_passes(self) -> None:
        """Golden: evidence recorded for the pushed commit lets the push through."""
        sha = self.cited_commit()
        self.assertEqual(self.world.run("evidence", self.item, "--change", sha).returncode, 0)
        result = self.check("--pre-push", stdin=push_line(sha))
        self.assertEqual((result.returncode, result.stderr), (0, ""))

    def test_cited_id_without_evidence_is_refused_with_the_exact_command(self) -> None:
        """A push may not deliver a cited item that has no evidence for that commit."""
        sha = self.cited_commit()
        result = self.check("--pre-push", stdin=push_line(sha))
        self.assertEqual(result.returncode, 2)
        self.assertIn(f"qew evidence {self.item} --change {sha}", result.stderr)

    def test_evidence_for_a_different_commit_does_not_cover_this_one(self) -> None:
        """Old evidence must not bless a later commit citing the same item."""
        first = self.cited_commit()
        self.world.run("evidence", self.item, "--change", first)
        second = self.cited_commit()
        result = self.check("--pre-push", stdin=push_line(second))
        self.assertEqual(result.returncode, 2)
        self.assertIn(second, result.stderr)
        self.assertNotIn(first, result.stderr)

    def test_a_cited_unknown_id_refuses_the_push(self) -> None:
        """A typo'd citation is reported by the hook, not discovered after delivery."""
        sha = self.world.commit("work\n\nRefs: TYPO99")
        result = self.check("--pre-push", stdin=push_line(sha))
        self.assertEqual(result.returncode, 2)
        self.assertIn("TYPO99", result.stderr)

    def test_commits_without_citations_and_branch_deletions_pass(self) -> None:
        """Uncited work and deleting a branch are not this hook's business."""
        sha = self.world.commit("plain work")
        self.assertEqual(self.check("--pre-push", stdin=push_line(sha)).returncode, 0)
        deletion = f"(delete) {ZERO} refs/heads/old {sha}\n"
        self.assertEqual(self.check("--pre-push", stdin=deletion).returncode, 0)


class InstalledGitHookWalk(VcsHookCase):
    def setUp(self) -> None:
        super().setUp()
        self.remote = self.world.base / "origin.git"
        git(self.world.base, "init", "-q", "--bare", "-b", "main", str(self.remote))
        git(self.world.code, "remote", "add", "origin", str(self.remote))
        bin_dir = self.world.base / "bin"
        bin_dir.mkdir()
        wrapper = bin_dir / "qew"
        wrapper.write_text(f"#!/bin/sh\nexec {os.sys.executable} -m qew \"$@\"\n")
        wrapper.chmod(0o755)
        self.env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "PYTHONPATH": str(PROJECT_ROOT / "src"),
                    "QEW_ACTOR": "tester", "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e.invalid",
                    "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e.invalid"}

    def push(self) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "push", "origin", "main"], cwd=self.world.code, env=self.env,
                              capture_output=True, text=True, check=False, timeout=60)

    def test_installed_hook_blocks_the_real_push_until_evidence_exists_and_removal_shows_in_the_diff(self) -> None:
        """The hook is bypassable only by removing a tracked file, which a diff shows."""
        installed = self.world.run("hook", "install", "--git")
        self.assertEqual(installed.stdout, "installed git hook\n", installed.stderr)
        script = self.world.code / ".githooks" / "pre-push"
        self.assertTrue(script.stat().st_mode & stat.S_IXUSR)
        self.assertEqual(git(self.world.code, "config", "core.hooksPath"), ".githooks")
        self.assertEqual(self.world.run("hook", "install", "--git").stdout, "unchanged git hook\n")
        git(self.world.code, "add", ".githooks")
        git(self.world.code, "commit", "-q", "-m", "install qew pre-push hook")
        sha = self.cited_commit()
        blocked = self.push()
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn(f"qew evidence {self.item} --change {sha}", blocked.stderr)
        self.world.run("evidence", self.item, "--change", sha)
        self.assertEqual(self.push().returncode, 0)
        removed = self.world.run("hook", "remove", "--git")
        self.assertEqual(removed.stdout, "removed git hook\n")
        self.assertFalse(script.exists())
        self.assertNotEqual(subprocess.run(["git", "config", "core.hooksPath"], cwd=self.world.code,
                                           capture_output=True).returncode, 0)
        self.assertIn("D .githooks/pre-push", git(self.world.code, "status", "--porcelain"))

    def test_install_refuses_a_foreign_hooks_path_or_an_unowned_script(self) -> None:
        """Never take over a hook setup that is not Qew's."""
        git(self.world.code, "config", "core.hooksPath", "tools/hooks")
        refused = self.world.run("hook", "install", "--git")
        self.assertEqual(refused.returncode, 2)
        self.assertIn("core.hooksPath", refused.stderr)
        git(self.world.code, "config", "--unset", "core.hooksPath")
        (self.world.code / ".githooks").mkdir()
        (self.world.code / ".githooks" / "pre-push").write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
        refused = self.world.run("hook", "install", "--git")
        self.assertEqual(refused.returncode, 2)
        self.assertEqual((self.world.code / ".githooks" / "pre-push").read_text(encoding="utf-8"), "#!/bin/sh\necho mine\n")

    def test_git_hook_needs_a_git_checkout(self) -> None:
        """A jj-only checkout has no git hooks to install."""
        (self.world.code / ".git").rename(self.world.base / "moved.git")
        (self.world.code / ".jj").mkdir()
        refused = self.world.run("hook", "install", "--git")
        self.assertEqual(refused.returncode, 2)
        self.assertIn("git checkout", refused.stderr)


class JjWalk(VcsHookCase):
    def setUp(self) -> None:
        super().setUp()
        jj(self.world.code, "git", "init", "--colocate")

    def commit(self, message: str) -> str:
        jj(self.world.code, "commit", "-m", message)
        return jj(self.world.code, "log", "--no-graph", "-r", "@-", "-T", "change_id.short(12)")

    def test_vcs_check_reads_jj_revisions_and_matches_evidence_by_change_id(self) -> None:
        """Scripts can check jj changes without git hooks."""
        change = self.commit(f"work\n\nRefs: {self.item}")
        refused = self.check("--revisions", "@-")
        self.assertEqual(refused.returncode, 2)
        self.assertIn(f"qew evidence {self.item} --change {change}", refused.stderr)
        self.assertEqual(self.world.run("evidence", self.item).returncode, 0)
        self.assertEqual(self.check("--revisions", "@-").returncode, 0)

    def test_the_agent_guard_blocks_jj_git_push_of_an_unevidenced_citation(self) -> None:
        """jj has no pre-push hook, so the PreToolUse guard stands in for it."""
        change = self.commit(f"work\n\nRefs: {self.item}")
        jj(self.world.code, "bookmark", "set", "main", "-r", "@-")
        for command in ("jj git push --bookmark main", "jj git push", "cd . && jj git push -b main"):
            with self.subTest(command=command):
                result = feed(self.world, "pre-tool", "claude", command, cwd=str(self.world.code))
                reason = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
                self.assertIn(f"qew evidence {self.item} --change {change}", reason)
        self.world.run("evidence", self.item)
        for command in ("jj git push --bookmark main", "jj git push"):
            with self.subTest(command=command, recorded=True):
                self.assertEqual(feed(self.world, "pre-tool", "claude", command, cwd=str(self.world.code)).stdout, "")

    def test_the_guard_stays_silent_when_jj_cannot_answer(self) -> None:
        """A broken jj must not block every push; the hook fails open here and says nothing."""
        result = feed(self.world, "pre-tool", "claude", "jj git push --bookmark nosuch", cwd=str(self.world.code))
        self.assertEqual((result.returncode, result.stdout), (0, ""))


if __name__ == "__main__":
    unittest.main()
