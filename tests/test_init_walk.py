"""Command walks for idempotent repository bootstrap."""

import json
import unittest

from tests.command_harness import run_qew


LABELS = [
    ("P0", "B60205", "Critical priority"),
    ("P1", "D93F0B", "High priority"),
    ("P2", "FBCA04", "Normal priority"),
    ("P3", "0E8A16", "Low priority"),
    ("P4", "C5DEF5", "Backlog priority"),
    ("bug", "D73A4A", "Defect work package"),
    ("enhancement", "A2EEEF", "Capability work package"),
    ("task", "5319E7", "Implementation work package"),
    ("epic", "3E4B9E", "Parent plan issue"),
    ("qew:approved", "1D76DB", "Approved for routine agent context"),
    ("qew:managed", "0E8A16", "Issue belongs to the Qew work graph"),
    ("qew:draft", "C2E0C6", "Filed for discussion; not workable until approved"),
    ("needs:approval", "D93F0B", "Waiting on a human's authorization before work starts"),
    ("in-progress", "0052CC", "Claimed work in progress"),
]


class InitWalk(unittest.TestCase):
    def test_refusal_walk_requires_an_explicit_mode(self) -> None:
        result, calls = run_qew(
            [],
            ["init", "--repo", "acme/widgets"],
            uninitialized=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("--mode overlay or --mode repository", result.stderr)
        self.assertEqual(calls, [])

    def test_golden_walk_is_idempotent_and_prints_security_guidance(self) -> None:
        result, calls = run_qew(
            [pages(label_page(LABELS))],
            ["init", "--mode", "repository", "--repo", "acme/widgets", "--json"],
            uninitialized=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["changed"], [])
        self.assertEqual(output["repository"], "acme/widgets")
        self.assertEqual(output["schema_version"], 1)
        self.assertEqual(output["mode"], "repository")
        self.assertIn("untrusted", output["guidance"])
        self.assertIn("qew:approved", output["guidance"])
        self.assertEqual(len(calls), 1)

    def test_init_links_a_project_and_later_runs_preserve_it(self) -> None:
        # Adversary: re-running init without --project silently unlinks the board.
        linked, _ = run_qew(
            [pages(label_page(LABELS))],
            ["init", "--mode", "repository", "--project", "31", "--repo", "acme/widgets", "--json"],
            uninitialized=True,
        )
        rerun, _ = run_qew(
            [pages(label_page(LABELS))],
            ["init", "--mode", "repository", "--repo", "acme/widgets", "--json"],
            project=31,
        )

        self.assertEqual(linked.returncode, 0, linked.stderr)
        self.assertEqual(
            linked.config,
            {"mode": "repository", "project": 31, "schema_version": 1},
        )
        self.assertEqual(json.loads(linked.stdout)["project"], 31)
        self.assertEqual(rerun.returncode, 0, rerun.stderr)
        self.assertEqual(rerun.config["project"], 31)

    def test_seam_walk_repairs_only_missing_or_drifted_qew_labels(self) -> None:
        before = [label for label in LABELS if label[0] != "P4"]
        before = [
            (name, "FFFFFF", "wrong") if name == "task" else (name, color, description)
            for name, color, description in before
        ]
        result, calls = run_qew(
            [
                pages(label_page(before)),
                response(""),
                response(""),
                pages(label_page(LABELS)),
            ],
            ["init", "--mode", "repository", "--repo", "acme/widgets", "--json"],
            uninitialized=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["changed"], ["P4", "task"])
        self.assertEqual(len(calls), 4)
        writes = [call for call in calls if call[:2] == ["label", "create"]]
        self.assertEqual(len(writes), 2)
        self.assertTrue(all("--force" in call for call in writes))
        self.assertTrue(all("unrelated" not in call for call in writes))

    def test_init_creates_the_needs_approval_gate_label(self) -> None:
        # Adversary: a repository bootstrapped by Qew lacks the label every
        # default create applies, so gh refuses every create.
        before = [label for label in LABELS if label[0] != "needs:approval"]
        result, calls = run_qew(
            [pages(label_page(before)), response(""), pages(label_page(LABELS))],
            ["init", "--mode", "repository", "--repo", "acme/widgets", "--json"],
            uninitialized=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["changed"], ["needs:approval"])
        writes = [call for call in calls if call[:2] == ["label", "create"]]
        self.assertEqual(len(writes), 1)
        self.assertIn("needs:approval", writes[0])

    def test_refusal_walk_rejects_incomplete_label_inventory(self) -> None:
        result, calls = run_qew(
            [pages(label_page(LABELS, has_next=True, cursor="next"))],
            ["init", "--mode", "repository", "--repo", "acme/widgets", "--json"],
            uninitialized=True,
        )

        self.assertEqual(result.returncode, 4)
        self.assertEqual(result.stdout, "")
        self.assertIn("incomplete pagination", result.stderr)
        self.assertEqual(len(calls), 1)

    def test_overlay_preserves_existing_label_definitions(self) -> None:
        existing = [
            ("task", "1D76DB", "Actionable work item / ticket"),
            ("bug", "D73A4A", "Something isn't working"),
            ("enhancement", "A2EEEF", "New feature or request"),
            ("EPIC", "F16825", ""),
        ]
        created = [
            label
            for label in LABELS
            if label[0] not in {"task", "bug", "enhancement", "epic"}
        ]
        result, calls = run_qew(
            [
                pages(label_page(existing)),
                *[response("") for _ in created],
                pages(label_page([*existing, *created])),
            ],
            ["init", "--mode", "overlay", "--repo", "acme/widgets", "--json"],
            uninitialized=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["mode"], "overlay")
        writes = [call for call in calls if call[:2] == ["label", "create"]]
        self.assertEqual(len(writes), len(created))
        self.assertTrue(all("--force" not in call for call in writes))
        self.assertTrue(all("Actionable work item / ticket" not in call for call in writes))


def response(stdout: str) -> dict:
    return {"stdout": stdout}


def pages(*payloads: dict) -> dict:
    return response(json.dumps(list(payloads)))


def label_page(labels: list, *, has_next: bool = False, cursor=None) -> dict:
    return {
        "data": {
            "repository": {
                "labels": {
                    "nodes": [
                        {"name": name, "color": color, "description": description}
                        for name, color, description in labels
                    ],
                    "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
                }
            }
        }
    }


if __name__ == "__main__":
    unittest.main()
