"""Command walks for the tracked repository config contract."""

import json
import sys
import unittest

from tests.command_harness import PROJECT_ROOT, run_qew
from tests.test_ready_walk import graph_response

sys.path.insert(0, str(PROJECT_ROOT / "src"))
from qew import __version__  # noqa: E402

BASE = {"mode": "repository", "schema_version": 1}


def ready(config: dict):
    return run_qew(
        [{"stdout": json.dumps(graph_response())}],
        ["ready", "--repo", "acme/widgets", "--json"],
        config=config,
    )


class ConfigWalk(unittest.TestCase):
    def test_unknown_config_fields_are_ignored(self) -> None:
        # Behavior: an additive field must not break a Qew that predates it.
        # Adversary: every new field makes every older Qew fail on every command.
        result, _ = ready({**BASE, "future_field": {"anything": [1, 2]}})

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_qew_older_than_the_required_minimum_is_refused_before_any_call(self) -> None:
        # Adversary: an old Qew silently ignores a field the repository depends on.
        result, calls = ready({**BASE, "minimum_version": "99.0.0"})

        self.assertEqual(result.returncode, 2)
        self.assertIn("requires Qew 99.0.0 or newer", result.stderr)
        self.assertIn(f"this is Qew {__version__}", result.stderr)
        self.assertIn("scripts/setup_user.py", result.stderr)
        self.assertEqual(calls, [])

    def test_the_current_and_older_minimums_are_accepted(self) -> None:
        for minimum in (__version__, "0.0.1"):
            with self.subTest(minimum=minimum):
                result, _ = ready({**BASE, "minimum_version": minimum})
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_malformed_minimum_version_is_refused(self) -> None:
        for minimum in ("1.x", "1.2", "v1.2.3", "1.2.3-rc1", 7):
            with self.subTest(minimum=minimum):
                result, calls = ready({**BASE, "minimum_version": minimum})
                self.assertEqual(result.returncode, 2)
                self.assertIn("minimum_version must be MAJOR.MINOR.PATCH", result.stderr)
                self.assertEqual(calls, [])

    def test_init_preserves_fields_it_does_not_own(self) -> None:
        # Adversary: re-running init drops a newer Qew's field, or the minimum
        # version a repository depends on, without a word.
        from tests.test_init_walk import LABELS, label_page, pages

        result, _ = run_qew(
            [pages(label_page(LABELS))],
            ["init", "--mode", "repository", "--repo", "acme/widgets", "--json"],
            config={**BASE, "minimum_version": "0.0.1", "future_field": True},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.config, {**BASE, "minimum_version": "0.0.1", "future_field": True})

    def test_mode_and_schema_version_stay_strict(self) -> None:
        for config in ({"mode": "repository", "schema_version": 2}, {"mode": "weird", "schema_version": 1}):
            with self.subTest(config=config):
                result, calls = ready(config)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
