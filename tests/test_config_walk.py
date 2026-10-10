"""Walks of the config contract: version gate, tolerance of additions, strict values, target identity.

Adversary: an old qew silently misreading a newer repository, a future field
breaking every older qew, a title standing in for a board number, and one
board duplicated by a URL spelling.
"""

import tomllib
import unittest

from qew import __version__
from tests.ledger_harness import PROJECT_ROOT, World

NOSYNC = "\n[replication]\nauto_forge_sync = false\n"
MIRROR = '[[mirror]]\nforge = "forgejo"\nrepo = "https://git.example/o/r"\n'


def newer_than_running() -> str:
    major, minor, patch = (int(n) for n in __version__.split("."))
    return f"{major + 1}.{minor}.{patch}"


class ConfigWalk(unittest.TestCase):
    def world(self, text: str) -> World:
        world = World(text, replication=NOSYNC)
        self.addCleanup(world.close)
        return world

    def test_unknown_config_fields_are_ignored(self) -> None:
        """A future top-level table and a future mirror option must not break an older qew."""
        w = self.world('[future]\nkey = 1\n[[mirror]]\nforge = "forgejo"\nrepo = "https://git.example/o/r"\nfuture_option = "x"\n')
        w.create("--title", "T")
        self.assertEqual(w.run("sync", "--dry-run").returncode, 0)

    def test_older_qew_than_the_minimum_is_refused_before_any_call(self) -> None:
        """Every command refuses, naming both versions and the upgrade script, before reading the ledger."""
        w = self.world(f'[qew]\nminimum_version = "{newer_than_running()}"\n' + MIRROR)
        for command in (("ready",), ("sync",), ("check",), ("list",)):
            result = w.run(*command)
            self.assertEqual(result.returncode, 2, command)
            self.assertIn(newer_than_running(), result.stderr)
            self.assertIn(__version__, result.stderr)
            self.assertIn("scripts/setup_user.py", result.stderr)

    def test_current_and_lower_minimums_are_accepted(self) -> None:
        """The boundary: equal to the running version and an older one both pass."""
        for minimum in (__version__, "0.0.1"):
            w = self.world(f'[qew]\nminimum_version = "{minimum}"\n')
            self.assertEqual(w.run("ready").returncode, 0, minimum)

    def test_malformed_minimum_versions_are_refused(self) -> None:
        """Only MAJOR.MINOR.PATCH digits are a version; anything else is refused before any call."""
        for bad in ('"1.x"', '"1.2"', '"v1.2.3"', '"1.2.3-rc1"', "3"):
            w = self.world(f"[qew]\nminimum_version = {bad}\n")
            result = w.run("ready")
            self.assertEqual(result.returncode, 2, bad)
            self.assertIn("minimum_version", result.stderr)

    def test_unsupported_ledger_schema_version_is_refused_and_a_missing_one_reads_as_1(self) -> None:
        """A newer ledger must not be misread; an absent schema_version is version 1."""
        w = self.world("")
        meta = w.ledger / "qew-ledger.toml"
        meta.write_text('name = "test"\nschema_version = 3\n')
        result = w.run("ready")
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("schema_version", result.stderr)
        meta.write_text('name = "test"\n')
        self.assertEqual(w.run("ready").returncode, 0)
        self.assertEqual(w.run("check").returncode, 0)

    def test_a_symlinked_config_is_refused(self) -> None:
        """Config read through a link could be pointed anywhere."""
        w = self.world("")
        real = w.code / "elsewhere.toml"
        config = w.code / ".qew" / "config.toml"
        real.write_text(config.read_text())
        config.unlink()
        config.symlink_to(real)
        result = w.run("ready")
        self.assertEqual(result.returncode, 2)
        self.assertIn("symlink", result.stderr)

    def test_tracked_repository_config_links_its_own_board(self) -> None:
        """Qew's own checked-in config pins its board and a minimum version no newer than this build."""
        data = tomllib.loads((PROJECT_ROOT / ".qew" / "config.toml").read_text())
        mirror = next(m for m in data["mirror"] if m["repo"].endswith("/foundlings/qew"))
        self.assertIsInstance(mirror["project"], int)
        minimum = data["qew"]["minimum_version"]
        self.assertRegex(minimum, r"^\d+\.\d+\.\d+$")
        self.assertLessEqual(tuple(map(int, minimum.split("."))), tuple(map(int, __version__.split("."))))


class ProjectNumberWalk(unittest.TestCase):
    def test_non_positive_or_mistyped_project_is_refused_at_load(self) -> None:
        """A title, a numeric string, zero, a negative, a boolean or a float is not a board number."""
        for bad in ('"Roadmap"', '"7"', "0", "-3", "true", "7.5"):
            w = World(f'[[mirror]]\nforge = "github"\nrepo = "https://github.com/o/r"\nproject = {bad}\n')
            self.addCleanup(w.close)
            result = w.run("ready")
            self.assertEqual(result.returncode, 2, bad)
            self.assertIn("project must be a positive board number", result.stderr)

    def test_empty_project_title_is_refused(self) -> None:
        """A blank title would create an unnamed board."""
        w = World('[[mirror]]\nforge = "github"\nrepo = "https://github.com/o/r"\nproject_title = ""\n')
        self.addCleanup(w.close)
        result = w.run("ready")
        self.assertEqual(result.returncode, 2)
        self.assertIn("project_title", result.stderr)

    def test_a_positive_number_is_accepted(self) -> None:
        w = World('[[mirror]]\nforge = "github"\nrepo = "https://github.com/o/r"\nproject = 7\n')
        self.addCleanup(w.close)
        self.assertEqual(w.run("ready").returncode, 0)


class TargetIdentityWalk(unittest.TestCase):
    def test_case_and_git_suffix_variants_are_one_target(self) -> None:
        """`Acme/Widgets.git` and `acme/widgets` are one repository: one mirror per item, never two boards."""
        w = World('[[mirror]]\nforge = "forgejo"\nrepo = "https://git.example/Acme/Widgets.git"\n'
                  '[[mirror]]\nforge = "forgejo"\nrepo = "https://GIT.example/acme/widgets"\n', replication=NOSYNC)
        self.addCleanup(w.close)
        w.create("--title", "T")
        lines = [l for l in w.run("sync", "--dry-run").stdout.splitlines() if l.startswith("create")]
        self.assertEqual(len(lines), 1, lines)


if __name__ == "__main__":
    unittest.main()
