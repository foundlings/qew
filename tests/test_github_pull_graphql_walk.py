"""Reading a pull request on GitHub costs one GraphQL query, not three REST calls, and says exactly what REST says.

Adversary: fifteen agents and two integrators spending the shared token's core REST limit to 0 on pull request reads (the pull, the
combined status, the check runs, several times a verb) while GraphQL still shows 4955 of 5000; and the cure answering differently.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from unittest import mock

from qew.forge import ForgeError, PullRequestSpec
from qew.forge_github import GitHub
from qew.forge_http import urllib_transport
from qew.ledger_config import Mirror
from tests.fake_github import GRAPHQL_PATH, TOKEN, FakeGitHub


class GraphqlPullWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub().start()
        self.addCleanup(self.fake.stop)
        self.github = GitHub(Mirror("github", self.fake.repo_url, None, "", {"api": self.fake.api}), urllib_transport(),
                             {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"})

    def open(self, head: str = "feature", draft: bool = False) -> int:
        return self.github.open_pull_request(PullRequestSpec(head, "main", f"Work {head}", "body text", draft, "")).number

    def rest(self, since: int) -> list[tuple]:
        return [r for r in self.fake.requests[since:] if not r[1].endswith(GRAPHQL_PATH)]

    def test_reading_a_pull_request_is_one_graphql_query_and_no_rest_call(self) -> None:
        """Prevents three REST calls per read (the pull, the combined status, the check runs)."""
        number = self.open()
        self.fake.set_check_run(self.fake.pulls[number]["head"]["sha"], "completed", "success")
        before = len(self.fake.requests)
        pull = self.github.read_pull_request(number)
        self.assertEqual(pull.checks, "passing")
        self.assertEqual(self.rest(before), [], self.rest(before))
        self.assertEqual(self.fake.graphql_operations()[-1:], ["ReadPull"])

    def matrix(self) -> list[int]:
        """Pull requests of every shape a verb meets."""
        numbers = []
        for name, draft in (("a", True), ("b", False), ("c", False), ("d", False), ("e", False), ("f", False), ("g", False)):
            numbers.append(self.open(name, draft))
        sha = lambda n: self.fake.pulls[n]["head"]["sha"]  # noqa: E731
        self.fake.set_check_run(sha(numbers[0]), "completed", "success")
        self.fake.pulls[numbers[1]]["mergeable"] = None
        self.fake.set_check_run(sha(numbers[1]), "completed", "failure")
        self.fake.set_status(sha(numbers[2]), "pending")
        self.fake.set_check_run(sha(numbers[2]), "queued", None)
        self.fake.pulls[numbers[3]].update(state="closed", merged=True, merge_sha="d" * 40)
        self.fake.set_status(sha(numbers[3]), "success")
        self.fake.pulls[numbers[4]]["state"] = "closed"
        self.fake.pulls[numbers[4]]["mergeable"] = False
        self.fake.issues[numbers[5]]["labels"] = ["blocked", "area:x"]
        self.fake.issues[numbers[5]]["assignee"] = "someone"
        self.fake.pulls[numbers[5]]["auto_merge"] = True
        self.fake.set_check_run(sha(numbers[5]), "completed", "neutral")
        self.fake.set_check_run(sha(numbers[5]), "completed", "skipped")
        self.fake.set_status(sha(numbers[6]), "error")
        return numbers

    def test_graphql_and_rest_say_the_same_about_every_shape_of_pull_request(self) -> None:
        """Prevents the cheaper read changing an answer: state, draft, mergeable, checks, labels, assignee, auto-merge, merge commit."""
        numbers = self.matrix()
        with mock.patch.dict(os.environ, {"QEW_NO_GRAPHQL": "1"}):
            expected = [self.github.read_pull_request(n) for n in numbers]
        got = [self.github.read_pull_request(n) for n in numbers]
        self.assertEqual(got, expected)
        self.assertEqual({p.checks for p in got}, {"passing", "failing", "pending", "none"})

    def test_a_graphql_failure_falls_back_to_rest_with_the_same_answer(self) -> None:
        """Prevents a GraphQL outage (or a server without it) stopping every pull request verb."""
        number = self.open()
        self.fake.set_check_run(self.fake.pulls[number]["head"]["sha"], "completed", "success")
        expected = self.github.read_pull_request(number)
        self.fake.graphql_pulls_broken = True
        before = len(self.fake.requests)
        self.assertEqual(self.github.read_pull_request(number), expected)
        self.assertTrue(self.rest(before))

    def test_more_than_a_hundred_check_contexts_are_read_through_rest(self) -> None:
        """Prevents a verdict from the first page of checks only: when GraphQL says more follow, the full REST read answers."""
        number = self.open()
        self.fake.set_check_run(self.fake.pulls[number]["head"]["sha"], "completed", "success")
        self.fake.contexts_overflow = True
        before = len(self.fake.requests)
        self.assertEqual(self.github.read_pull_request(number).checks, "passing")
        self.assertTrue(self.rest(before))

    def test_finding_the_open_pull_requests_of_a_head_is_one_query(self) -> None:
        """Prevents the list of open pull requests plus three REST calls for each one found."""
        number = self.open("topic")
        self.open("other")
        before = len(self.fake.requests)
        found = self.github.find_open_pull_requests("topic")
        self.assertEqual([p.number for p in found], [number])
        self.assertEqual(self.rest(before), [], self.rest(before))

    def test_marking_ready_reads_only_through_graphql(self) -> None:
        """Prevents the verbs' read-write-read sequence spending REST: the mutation and the reads are all GraphQL."""
        number = self.open(draft=True)
        before = len(self.fake.requests)
        pull = self.github.mark_pull_request_ready(number)
        self.assertFalse(pull.draft)
        self.assertEqual(self.rest(before), [], self.rest(before))

    def test_reading_many_pull_requests_is_one_query_per_twenty(self) -> None:
        """Prevents 36 pull requests costing 108 REST calls: a union is read in a few GraphQL queries, and equals the one-by-one reads."""
        numbers = [self.open(f"head-{n}") for n in range(45)]
        self.fake.set_check_run(self.fake.pulls[numbers[3]]["head"]["sha"], "completed", "failure")
        self.fake.pulls[numbers[7]]["mergeable"] = False
        with mock.patch.dict(os.environ, {"QEW_NO_GRAPHQL": "1"}):
            expected = {n: self.github.read_pull_request(n) for n in numbers}
        before = len(self.fake.requests)
        got = self.github.read_pull_requests(numbers)
        self.assertEqual(got, expected)
        self.assertEqual(self.rest(before), [], len(self.rest(before)))
        self.assertEqual(self.fake.graphql_operations()[-3:], ["ReadPulls"] * 3)

    def test_a_batch_read_falls_back_to_rest_and_a_missing_number_raises(self) -> None:
        """Prevents an outage of the batched read stopping a union, and a number that is not there being skipped."""
        numbers = [self.open(f"h{n}") for n in range(3)]
        expected = {n: self.github.read_pull_request(n) for n in numbers}
        self.fake.graphql_pulls_broken = True
        self.assertEqual(self.github.read_pull_requests(numbers), expected)
        self.fake.graphql_pulls_broken = False
        with self.assertRaises(ForgeError):
            self.github.read_pull_requests([*numbers, 999])


if __name__ == "__main__":
    unittest.main()
