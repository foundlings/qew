"""`qew sync --prune-labels`: delete managed-prefix label definitions nothing carries or wants.

Adversary: deleting a label a closed issue or a human still carries, deleting a label outside qew's
prefixes, deleting on a read-only mirror, deleting during a dry run, and wedging because the label is gone.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_forgejo import TOKEN as FORGEJO_TOKEN, FakeForgejo
from tests.fake_github import TOKEN as GITHUB_TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

QUIET = "\n[replication]\nauto_forge_sync = false\n"


class GitHubSide:
    forge, token_env, token = "github", "GITHUB_TOKEN", GITHUB_TOKEN

    def __init__(self) -> None:
        self.fake = FakeGitHub().start()

    def seed(self, name: str) -> None:
        self.fake.label_store[name] = {"color": "ededed", "description": ""}

    def carry(self, name: str, state: str = "closed", body: str = "") -> None:
        self.seed(name)
        issue = self.fake.new_issue({"title": "Foreign", "body": body, "labels": [name]})
        issue["state"] = state

    def labels(self) -> set:
        return set(self.fake.label_store)


class ForgejoSide:
    forge, token_env, token = "forgejo", "FORGEJO_TOKEN", FORGEJO_TOKEN

    def __init__(self) -> None:
        self.fake = FakeForgejo().start()

    def seed(self, name: str) -> None:
        self.fake._label_id(name)

    def carry(self, name: str, state: str = "closed", body: str = "") -> None:
        number = self.fake._index()
        self.fake.issues[number] = {"title": "Foreign", "body": body, "state": state, "label_ids": [self.fake._label_id(name)],
                                    "assignee": "", "discussion": []}
        self.fake.touch(number)

    def labels(self) -> set:
        return set(self.fake.labels.values())


class PruneLabelsWalk:
    side_class = None
    read_only = ""

    def setUp(self) -> None:
        self.side = self.side_class()
        self.addCleanup(self.side.fake.stop)
        mirror = (f'\n[[mirror]]\nforge = "{self.side.forge}"\nrepo = "{self.side.fake.repo_url}"\n'
                  f'api = "{self.side.fake.api}"\n{self.read_only}')
        self.w = World(mirror, replication=QUIET)
        self.addCleanup(self.w.close)
        self.env = {self.side.token_env: self.side.token, "QEW_NO_GH_TOKEN": "1"}

    def seed_stale(self) -> None:
        for name in ("author:laragana", "qew:managed", "status:old", "area:keep"):
            self.side.seed(name)
        self.side.carry("author:legacy")  # a closed issue still carries it: not ours to delete

    def test_unused_managed_labels_are_deleted_and_everything_else_stays(self) -> None:
        """Prevents a tidy-up eating labels in use or outside qew's prefixes; a dry run changes nothing."""
        self.w.create("--title", "Work", "--quote", QUOTE)
        self.assertEqual(self.w.run("sync", env=self.env).returncode, 0)
        self.seed_stale()
        before = self.side.labels()
        dry = self.w.run("sync", "--prune-labels", "--dry-run", env=self.env)
        self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
        for name in ("author:laragana", "qew:managed", "status:old"):
            self.assertIn(name, dry.stdout)
        self.assertNotIn("area:keep", dry.stdout)
        self.assertNotIn("author:legacy", dry.stdout)
        self.assertEqual(self.side.labels(), before)
        done = self.w.run("sync", "--prune-labels", env=self.env)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(before - self.side.labels(), {"author:laragana", "qew:managed", "status:old"})
        self.assertTrue({"area:keep", "author:legacy"} <= self.side.labels())
        again = self.w.run("sync", "--prune-labels", env=self.env)
        self.assertEqual(again.returncode, 0)
        self.assertIn("no unused", again.stdout)

    def test_a_closed_duplicate_twin_does_not_keep_a_label_alive(self) -> None:
        """Prevents a dead twin (closed, marker replaced by qew-duplicate) pinning a label forever; an open twin still does."""
        self.side.carry("qew:managed", "closed", "<!-- qew-duplicate -->\n\nbody")
        self.side.carry("author:pinned", "open", "<!-- qew-duplicate -->\n\nbody")
        self.assertEqual(self.w.run("sync", "--prune-labels", env=self.env).returncode, 0)
        self.assertNotIn("qew:managed", self.side.labels())
        self.assertIn("author:pinned", self.side.labels())

    def test_a_label_the_ledger_still_wants_is_kept(self) -> None:
        """Prevents deleting a label sync would recreate: the live label set of every item is wanted."""
        item = self.w.create("--title", "Work", "--quote", QUOTE)
        self.assertEqual(self.w.run("sync", env=self.env).returncode, 0)
        wanted = {name for name in self.side.labels() if name.startswith("kind:")}
        self.assertTrue(wanted, item)
        self.assertEqual(self.w.run("sync", "--prune-labels", env=self.env).returncode, 0)
        self.assertTrue(wanted <= self.side.labels())


class GitLabBackends(unittest.TestCase):
    """The same three calls on the backends the command walks do not reach (GitLab, and the pull-request side)."""

    def test_gitlab_lists_uses_and_deletes_labels(self) -> None:
        """Prevents a GitLab prune deleting a label a merge request or issue carries, or mis-deleting by name."""
        from qew import forge_http
        from qew.forge_gitlab import GitLab
        from qew.ledger_config import Mirror
        from tests.fake_gitlab import TOKEN, FakeGitLab

        fake = FakeGitLab().start()
        self.addCleanup(fake.stop)
        project = fake.project("group/repo")
        for number, name in enumerate(("author:old", "qew:used", "kind:mr"), start=900):
            project.labels.append({"id": number, "name": name, "color": "#428BCA", "description": ""})
        project.issues.append({"id": 1, "iid": 1, "state": "closed", "title": "t", "description": "", "labels": ["qew:used"],
                               "assignees": [], "notes": []})
        project.merge_requests.append({"id": 2, "iid": 1, "state": "merged", "labels": ["kind:mr"]})
        forge = GitLab(Mirror("gitlab", f"{fake.host}/group/repo", None, "", {"api": fake.api}),
                       forge_http.urllib_transport(timeout=5), {"GITLAB_TOKEN": TOKEN})
        self.assertEqual(forge.list_repo_labels(), ["author:old", "kind:mr", "qew:used"])
        self.assertEqual(forge.labels_in_use(), {"qew:used", "kind:mr"})
        forge.delete_label("author:old")
        forge.delete_label("author:old")  # already gone: not an error
        self.assertEqual(forge.list_repo_labels(), ["kind:mr", "qew:used"])


class GitHubPrune(PruneLabelsWalk, unittest.TestCase):
    side_class = GitHubSide


class ForgejoPrune(PruneLabelsWalk, unittest.TestCase):
    side_class = ForgejoSide


class ReadOnlyPrune(PruneLabelsWalk, unittest.TestCase):
    side_class = GitHubSide
    read_only = "read_only = true\n"

    def test_unused_managed_labels_are_deleted_and_everything_else_stays(self) -> None:
        """Prevents pruning a mirror we do not own: a read-only target is refused and keeps every label."""
        self.seed_stale()
        before = self.side.labels()
        result = self.w.run("sync", "--prune-labels", env=self.env)
        self.assertEqual(result.returncode, 2)
        self.assertIn("read-only", result.stdout + result.stderr)
        self.assertEqual(self.side.labels(), before)

    def test_a_closed_duplicate_twin_does_not_keep_a_label_alive(self) -> None:
        """Prevents read-only mirrors being pruned through a twin-labelled issue: still refused."""
        self.assertEqual(self.w.run("sync", "--prune-labels", env=self.env).returncode, 2)

    def test_a_label_the_ledger_still_wants_is_kept(self) -> None:
        """Prevents the read-only refusal depending on item state: it also refuses an empty ledger run."""
        self.assertEqual(self.w.run("sync", "--prune-labels", env=self.env).returncode, 2)


if __name__ == "__main__":
    unittest.main()
