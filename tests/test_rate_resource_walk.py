"""The forge quota is two budgets: the core REST limit and the GraphQL limit. A pass that spent one must not be fooled by the other.

Adversary: the shared token's core REST limit at 0 while GraphQL still shows 4955: one GraphQL answer overwrites the accounting and
the background pass carries on spending REST calls it was meant to leave for merges (or stands down on a GraphQL number).
"""

import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import forge_http
from qew.forge import HttpRequest, HttpResponse

REST = "https://api.github.com/repos/o/r/pulls/1"
GRAPHQL = "https://api.github.com/graphql"


def answer(resource: str, remaining: int) -> HttpResponse:
    headers = {"x-ratelimit-resource": resource, "x-ratelimit-remaining": str(remaining), "x-ratelimit-reset": str(int(time.time()) + 3000)}
    return HttpResponse(200, headers, b"{}")


class RateResourceWalk(unittest.TestCase):
    def call(self, url: str, resource: str, remaining: int) -> None:
        forge_http._timed(lambda request: answer(resource, remaining), HttpRequest("POST" if url == GRAPHQL else "GET", url, {}))

    def test_a_graphql_answer_does_not_hide_an_exhausted_rest_limit_from_the_background_reserve(self) -> None:
        """Prevents the reserve being lifted by GraphQL's 4955 left: the next REST call in the background still stands down."""
        with mock.patch.dict(os.environ, {"QEW_BACKGROUND": "1"}), mock.patch.object(forge_http.WATCH, "remaining", None), \
                mock.patch.object(forge_http.WATCH, "reset", None):
            self.call(REST, "core", 100)
            self.call(GRAPHQL, "graphql", 4955)
            with self.assertRaises(forge_http.RateReserve):
                self.call(REST, "core", 100)

    def test_a_low_graphql_budget_does_not_stop_rest_calls_that_have_plenty(self) -> None:
        """Prevents a background pass standing down on the wrong budget: REST with 4000 left goes on while GraphQL is at 50."""
        with mock.patch.dict(os.environ, {"QEW_BACKGROUND": "1"}), mock.patch.object(forge_http.WATCH, "remaining", None), \
                mock.patch.object(forge_http.WATCH, "reset", None):
            self.call(REST, "core", 4000)
            self.call(GRAPHQL, "graphql", 50)
            self.call(REST, "core", 3999)  # not refused

    def test_the_core_figure_pr_migrate_reads_stays_the_core_figure(self) -> None:
        """Prevents `forge_http.WATCH.remaining` (the floor `pr migrate` stops at) turning into GraphQL's number."""
        with mock.patch.object(forge_http.WATCH, "remaining", None), mock.patch.object(forge_http.WATCH, "reset", None):
            self.call(REST, "core", 321)
            self.call(GRAPHQL, "graphql", 4955)
            self.assertEqual(forge_http.WATCH.remaining, 321)

    def test_a_rate_limit_refusal_names_when_the_hour_resets(self) -> None:
        """Prevents a bare `API rate limit exceeded` after a long gate: the message says which limit and the clock time it lifts."""
        reset = int(time.time()) + 1500
        refused = HttpResponse(403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(reset), "x-ratelimit-resource": "core"},
                               b'{"message": "API rate limit exceeded for user ID 1."}')
        with self.assertRaises(forge_http.ForgeUnavailable) as caught:
            forge_http._interpret(refused, (200,))
        text = str(caught.exception)
        self.assertIn("core limit", text)
        self.assertRegex(text, r"resets at \d\d:\d\dZ, in about \d+ min")


if __name__ == "__main__":
    unittest.main()
