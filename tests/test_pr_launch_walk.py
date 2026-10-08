"""How the pull request commands behave when the forge is unreachable, hung or tokenless.

Adversary: a hook or agent hanging forever on a dead forge, an anonymous request, a traceback instead of
one line, and a token helper that hangs.
"""

import os
from pathlib import Path
import socket
import stat
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import forge_http
from qew.forge import ForgeAuth
from qew.ledger_config import Mirror
from tests.fake_forgejo import FakeForgejo
from tests.pr_harness import PrWorld


class HungServer:
    """Accepts connections and never answers."""

    def __init__(self) -> None:
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.held = []
        self.thread = threading.Thread(target=self._accept, daemon=True)
        self.thread.start()

    @property
    def api(self) -> str:
        return f"http://127.0.0.1:{self.sock.getsockname()[1]}/api/v1"

    def _accept(self) -> None:
        while True:
            try:
                self.held.append(self.sock.accept()[0])
            except OSError:
                return

    def close(self) -> None:
        self.sock.close()
        for held in self.held:
            held.close()


class LaunchWalk(unittest.TestCase):
    def open_in_subprocess(self, world: PrWorld, item: str, env=None):
        return world.world.run("pr", "open", item, "--head", "feature", "--base", "main", env=env)

    def test_a_hung_forge_ends_in_one_timed_out_line_and_leaves_no_record(self) -> None:
        """Prevents a hook or agent hanging forever."""
        hung = HungServer()
        self.addCleanup(hung.close)
        world = PrWorld(mirror_options=f'api = "{hung.api}"\ntimeout = 1\n')
        self.addCleanup(world.close)
        item = world.item()
        result = self.open_in_subprocess(world, item, env={"FORGEJO_TOKEN": "t"})
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(len(result.stderr.strip().splitlines()), 1)
        self.assertIn("timed out", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(world.mirror_files(item), [])

    def test_no_token_names_the_variables_and_sends_no_request(self) -> None:
        """Prevents anonymous requests."""
        fake = FakeForgejo().start()
        self.addCleanup(fake.stop)
        world = PrWorld(mirror_options=f'api = "{fake.api}"\n')
        self.addCleanup(world.close)
        item = world.item()
        result = self.open_in_subprocess(world, item, env={"FORGEJO_TOKEN": "", "GITEA_TOKEN": ""})
        self.assertEqual(result.returncode, 1)
        self.assertIn("FORGEJO_TOKEN", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(fake.requests, [])

    def test_a_missing_or_hung_gh_helper_falls_back_to_a_missing_token_error(self) -> None:
        """Prevents a hang in token resolution."""
        mirror = Mirror("github", "https://github.com/o/r", None, "", {})
        with tempfile.TemporaryDirectory() as empty, mock.patch.dict(os.environ, {"PATH": empty}):
            with self.assertRaises(ForgeAuth):
                forge_http.resolve_token(mirror, {})
        with tempfile.TemporaryDirectory() as directory:
            helper = Path(directory) / "gh"
            helper.write_text("#!/bin/sh\nsleep 5\n")
            helper.chmod(helper.stat().st_mode | stat.S_IXUSR)
            with mock.patch.dict(os.environ, {"PATH": directory}), mock.patch.object(forge_http, "TOKEN_HELPER_SECONDS", 0.3):
                with self.assertRaises(ForgeAuth):
                    forge_http.resolve_token(mirror, {})


if __name__ == "__main__":
    unittest.main()
