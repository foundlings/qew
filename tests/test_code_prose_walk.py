"""Walks of the unmarked-code warning on item bodies.

Adversary: instruction-looking commands and flags written as plain prose into a body that a later
agent reads, false alarms on code that is marked or on URLs, and an advisory that blocks the write.
"""

import unittest

from tests.ledger_harness import World


class CodeProseWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def create(self, body: str):
        return self.world.run("create", "--title", "Item", "--body", body)

    def warnings(self, result) -> list:
        prefix = "warning: unmarked code-shaped text: "
        return [line[len(prefix):] for line in result.stderr.splitlines() if line.startswith(prefix)]

    def test_unmarked_commands_flags_and_paths_warn_once_in_order_and_the_write_succeeds(self) -> None:
        """Plain `qew start X --force` warns for the command and the flag; repeats are not repeated; exit stays 0."""
        result = self.create("Then run qew start X --force and later qew start Y --force; edit /etc/hosts or ./run.sh and call gh pr merge.")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.warnings(result), ["qew start", "--force", "/etc/hosts", "./run.sh", "gh pr"])
        self.assertTrue(result.stdout.strip())

    def test_marked_code_warns_nothing(self) -> None:
        """Backticks, three-backtick fences and tilde fences (even indented) mark code."""
        body = "Run `qew start X --force` first.\n\n```sh\nqew complete X --force\n```\n\n  ~~~\ngit push --force\n  ~~~\n"
        self.assertEqual(self.warnings(self.create(body)), [])

    def test_an_unclosed_fence_hides_the_rest_and_urls_are_not_paths(self) -> None:
        """Content after an unclosed fence is code; a URL path glued to a colon is not reported; prose after is."""
        self.assertEqual(self.warnings(self.create("See https://example.com/a/b for details.\n```\nqew start X --force\n")), [])
        self.assertEqual(self.warnings(self.create("See https://example.com/a/b for details, then python3 -m http.server")), ["python3 -m"])

    def test_edits_and_bulk_files_warn_the_same_way(self) -> None:
        """`set --body` and `create --from-file` bodies are checked too."""
        item = self.world.create("--title", "Edited")
        result = self.world.run("set", item, "--body", "use --dangerous mode")
        self.assertEqual((result.returncode, self.warnings(result)), (0, ["--dangerous"]))
        bulk = self.world.run("create", "--from-file", "-", stdin='[{"title": "A", "body": "pipx install thing"}]')
        self.assertEqual((bulk.returncode, self.warnings(bulk)), (0, ["pipx install"]))


if __name__ == "__main__":
    unittest.main()
