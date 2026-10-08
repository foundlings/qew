"""Tracked repository policy walk."""

import json
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RepositoryConfigWalk(unittest.TestCase):
    def test_qew_links_its_own_board_so_its_mutations_move_cards(self) -> None:
        """Without the link, Qew's own claims and completions leave board 31 stale; the minimum version keeps an older Qew from misreading the numeric link."""
        config = json.loads((PROJECT_ROOT / ".qew" / "config.json").read_text(encoding="utf-8"))

        self.assertEqual(
            config,
            {"minimum_version": "2.10.0", "mode": "repository", "project": 31, "schema_version": 1},
        )


if __name__ == "__main__":
    unittest.main()
