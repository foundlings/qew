"""`pr_gate.evidence_covers`: evidence the checkout cannot resolve must neither be called a missing head nor hide covering evidence.

Seen live: an item cited three changes; the first was an 8-letter prefix that two change ids share, so jj refused it as ambiguous,
and the refusal was reported as 'head is not in this checkout' although the head was there and the third change covered it.
The VCS is the seam: `code_vcs.is_ancestor` is replaced, since a real repository cannot be made to share a change id prefix.
"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from qew import cmd_finish, code_vcs, pr_gate
from qew.errors import QewError

AMBIGUOUS = "pvntuxyp"
GOOD = "urmwxouykqtz"
HEAD = "e95c50a56ba3"


class EvidenceCoversWalk(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        (Path(temporary.name) / ".jj").mkdir()
        self.config = SimpleNamespace(root=Path(temporary.name))

    @staticmethod
    def events(*changes: str) -> list[dict[str, object]]:
        return [{"vcs": "jj", "change_ids": [change]} for change in changes]

    @staticmethod
    def resolver(change: str, target: str) -> bool:
        if change == AMBIGUOUS:
            raise QewError(f"jj could not resolve `::({target}) & (change_id({change}))`: Change ID prefix `{change}` is ambiguous", 3)
        return change == GOOD

    def covers(self, *changes: str) -> bool:
        with mock.patch.object(code_vcs, "is_ancestor", side_effect=lambda _root, _kind, change, target: self.resolver(change, target)):
            return pr_gate.evidence_covers(self.config, self.events(*changes), HEAD)  # type: ignore[arg-type]

    def test_covering_evidence_after_an_ambiguous_change_still_passes(self) -> None:
        """Prevents the ambiguous first event aborting the check before the covering third event is read."""
        self.assertTrue(self.covers(AMBIGUOUS, "tlnlunox", GOOD))

    def test_only_unresolvable_evidence_is_named_not_blamed_on_the_head(self) -> None:
        """Prevents 'head is not in this checkout' for a head that is there: the refusal names the change and jj's reason."""
        with self.assertRaises(QewError) as caught:
            self.covers(AMBIGUOUS)
        message = str(caught.exception)
        self.assertIn(AMBIGUOUS, message)
        self.assertIn("ambiguous", message)
        self.assertNotIn("not in this checkout", message)

    def test_resolvable_but_uncovering_evidence_is_simply_not_covering(self) -> None:
        """Prevents a clean 'no' turning into an error."""
        self.assertFalse(self.covers("tlnlunox"))


class LandedCommitsWalk(unittest.TestCase):
    """`cmd_finish._landed_commits`: completing reads the same evidence, and must survive an unresolvable event the same way."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        (Path(temporary.name) / ".jj").mkdir()
        self.config = SimpleNamespace(root=Path(temporary.name))

    def landed(self, *changes: str) -> tuple[list[str], list[str]]:
        events = [{"vcs": "jj", "change_ids": [change]} for change in changes]
        snapshot = SimpleNamespace(folds={"I": SimpleNamespace(evidence=events)})

        def landing(_root: Path, _kind: str, change: str, _target: str) -> tuple[code_vcs.Landing, str]:
            if change == AMBIGUOUS:
                raise QewError(f"jj could not resolve `change_id({change})`: Change ID prefix `{change}` is ambiguous", 3)
            return code_vcs.Landing.LANDED, "f" * 40

        with mock.patch.object(code_vcs, "landing", side_effect=landing):
            return cmd_finish._landed_commits(self.config, snapshot, "I", "main")  # type: ignore[arg-type]

    def test_a_landed_event_after_an_ambiguous_one_completes(self) -> None:
        """Prevents one ambiguous short change id blocking completion although another recorded event landed."""
        self.assertEqual(self.landed(AMBIGUOUS, GOOD)[0], [GOOD])

    def test_only_an_ambiguous_event_is_named_with_the_vcs_reason(self) -> None:
        """Prevents a bare failure: the refusal names the change and says it is ambiguous."""
        with self.assertRaises(QewError) as caught:
            self.landed(AMBIGUOUS)
        self.assertIn(AMBIGUOUS, str(caught.exception))
        self.assertIn("ambiguous", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
