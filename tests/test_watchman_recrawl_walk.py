"""`qew doctor` finds a watchman watch that keeps re-crawling, and a slow ledger commit points at it.

Seen live: macOS dropped file events, watchman re-crawled the watch (99 times on one ledger) and every jj snapshot waited for the
crawl, so a two-file ledger commit took two minutes. `watchman watch-del` then `watch-project` brought a snapshot from 60 s to 0.3 s.
Adversary: a ledger left on that slow path with nothing to say so, a fix that resets the wrong watch, and a hint printed on every
write. The watchman here is a script: the walks control what it answers.
"""

import shutil
import stat
import subprocess
import unittest
from pathlib import Path

from tests.ledger_harness import World

SCRIPT = """#!/bin/sh
echo "$@" >> "$WATCHMAN_LOG"
case "$1" in
  watch-del) touch "$WATCHMAN_FIXED"; echo '{"version": "t"}' ;;
  watch-project)
    if [ -n "$WATCHMAN_DOWN" ]; then echo "watchman is down" >&2; exit 1; fi
    if [ -f "$WATCHMAN_FIXED" ] || [ -n "$WATCHMAN_QUIET" ]; then echo '{"version": "t", "watch": "p"}';
    else printf '%s\\n' '{"version": "t", "watch": "p", "warning": "Recrawled this watch 7 times, most recently because:\\nMustScanSubDirs UserDropped"}'; fi ;;
  *) echo '{"version": "t"}' ;;
esac
"""


class WatchmanRecrawlWalk(unittest.TestCase):
    def jj_world(self, replication: str = "") -> tuple[World, dict[str, str]]:
        world = World(ledger_vcs="jj", replication=replication, schema="8")
        self.addCleanup(world.close)
        directory = world.base / "pathdir"
        directory.mkdir()
        for name in ("jj", "git"):
            (directory / name).symlink_to(shutil.which(name) or name)
        script = directory / "watchman"
        script.write_text(SCRIPT)
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        subprocess.run(["jj", "--ignore-working-copy", "config", "set", "--repo", "fsmonitor.backend", "watchman"], cwd=world.ledger, check=True,
                       capture_output=True)
        self.log = world.base / "watchman.log"
        return world, {"PATH": str(directory), "WATCHMAN_LOG": str(self.log), "WATCHMAN_FIXED": str(world.base / "fixed")}

    def test_doctor_names_a_watch_that_re_crawled_and_the_fix(self) -> None:
        """Prevents a ledger stuck on a 60 s snapshot with nothing to say so: the count and the command are in the output."""
        world, env = self.jj_world()
        done = world.run("doctor", env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("re-crawled", done.stdout)
        self.assertIn("7 times", done.stdout)
        self.assertIn("qew doctor --fix", done.stdout)

    def test_fix_resets_this_ledgers_watch_and_nothing_else(self) -> None:
        """Prevents resetting the wrong watch: watch-del then watch-project of exactly this ledger, after which doctor is quiet."""
        world, env = self.jj_world()
        fixed = world.run("doctor", "--fix", env=env)
        self.assertEqual(fixed.returncode, 0, fixed.stdout + fixed.stderr)
        self.assertIn("reset", fixed.stdout)
        calls = self.log.read_text().splitlines()
        deleted = [index for index, call in enumerate(calls) if call == f"watch-del {world.ledger.resolve()}" or call == f"watch-del {world.ledger}"]
        self.assertTrue(deleted, calls)
        self.assertTrue(any(call.startswith("watch-project") for call in calls[deleted[0] + 1:]), calls)
        self.assertTrue(all(str(world.ledger.resolve()) in call or str(world.ledger) in call for call in calls if call.split()[0] in ("watch-del",)), calls)
        self.assertNotIn("re-crawled", world.run("doctor", env=env).stdout)

    def test_a_quiet_watch_adds_nothing_and_a_dead_watchman_is_named(self) -> None:
        """Prevents noise on a healthy ledger, and silence when watchman does not answer at all."""
        world, env = self.jj_world()
        quiet = world.run("doctor", env={**env, "WATCHMAN_QUIET": "1"})
        self.assertEqual(quiet.stdout.strip(), "ok: watchman is enabled")
        down = world.run("doctor", env={**env, "WATCHMAN_DOWN": "1"})
        self.assertIn("did not answer", down.stdout)

    def test_a_slow_ledger_commit_points_at_doctor_once_a_day(self) -> None:
        """Prevents a silent two-minute commit: one stderr hint naming the time and `qew doctor`, not repeated on the next write."""
        world, env = self.jj_world("\n[replication]\nauto_forge_sync = false\nforge_concurrency = 1\nslow_commit_s = 0.000001\n")
        first = world.run("create", "--title", "slow", env=env)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("took", first.stderr)
        self.assertIn("qew doctor", first.stderr)
        second = world.run("create", "--title", "slower", env=env)
        self.assertNotIn("qew doctor", second.stderr)

    def test_a_git_ledger_never_gets_the_hint(self) -> None:
        """Prevents a watchman hint on a ledger that does not use jj."""
        world = World(ledger_vcs="git", schema="8", replication="\n[replication]\nauto_forge_sync = false\nforge_concurrency = 1\nslow_commit_s = 0.000001\n")
        self.addCleanup(world.close)
        made = world.run("create", "--title", "git")
        self.assertNotIn("qew doctor", made.stderr)


if __name__ == "__main__":
    unittest.main()
