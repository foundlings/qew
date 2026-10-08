"""Ten agents on one machine, one ledger checkout and one code repo, with writers killed mid-session."""

import json
import os
import random
import re
import signal
import subprocess
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import PROJECT_ROOT, QUOTE, World, git, jj, jj_available

AGENTS = 10
KILLS = 4
GOOD = {"start": {0, 5}}  # a repeated start is exit 5: already yours, nothing written
SUBJECT = re.compile(r"^(qew: [a-z-]+( [A-Za-z0-9]+| \d+ items)? by \S+|init ledger|.*merge.*)$", re.IGNORECASE)


class Fleet:
    """Runs qew processes for every agent and kills a few of them mid-flight."""

    def __init__(self, world: World) -> None:
        self.world = world
        self.running = {}
        self.mutex = threading.Lock()
        self.log = []
        self.environment = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT / "src"), "QEW_UNDER_TEST": "1",
                            "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e.invalid",
                            "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e.invalid"}

    def qew(self, agent: int, *arguments: str):
        environment = {**self.environment, "QEW_ACTOR": f"agent{agent}"}
        process = subprocess.Popen([sys.executable, "-m", "qew.cli", *arguments], cwd=self.world.code, env=environment,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        with self.mutex:
            self.running[process.pid] = process
        out, err = process.communicate()
        with self.mutex:
            self.running.pop(process.pid, None)
            self.log.append((agent, arguments[0], process.returncode, err))
        return process.returncode, out, err

    def killer(self, stop: threading.Event) -> None:
        generator = random.Random(7)
        killed = 0
        while killed < KILLS and not stop.is_set():
            time.sleep(generator.uniform(0.4, 1.2))
            with self.mutex:
                victims = [p for p in self.running.values() if p.poll() is None]
            if victims:
                generator.choice(victims).send_signal(signal.SIGKILL)
                killed += 1


def agent_script(fleet: Fleet, agent: int, head: str, results: dict) -> None:
    key = f"agent-{agent}"
    title = f"Work of agent {agent}"
    steps = [
        ("create", ["create", "--title", title, "--key", key, "--quote", QUOTE]),
        ("create", ["create", "--title", title, "--key", key, "--quote", QUOTE]),  # an eager retry of the same intent
    ]
    item = None
    for name, arguments in steps:
        code, out, err = retry(fleet, agent, arguments)
        if code == 0:
            item = out.split()[0]
    if item is None:
        results[agent] = "create never succeeded"
        return
    for name, arguments in (
        ("start", ["start", item]),
        ("evidence", ["evidence", item, "--change", head]),
        ("comment", ["comment", item, "--body", f"done by {agent}", "--key", f"c-{agent}"]),
        ("evidence", ["evidence", item, "--change", head]),
        ("complete", ["complete", item]),
    ):
        code, out, err = retry(fleet, agent, arguments)
        if code not in GOOD.get(name, {0}):
            results[agent] = f"{name} ended with {code}: {err[-200:]}"
            return
    results[agent] = item


def retry(fleet: Fleet, agent: int, arguments: list, attempts: int = 6):
    code, out, err = None, "", ""
    for _ in range(attempts):
        code, out, err = fleet.qew(agent, *arguments)
        if code is not None and code >= 0 and not (code == 3 and "locked" in err):
            break  # a finished answer; a killed (negative) or lock-timed-out attempt is retried
    return code, out, err


class TenAgentsWalk(unittest.TestCase):
    def run_fleet(self, vcs: str) -> None:
        world = World(ledger_vcs=vcs, remote=True)
        self.addCleanup(world.close)
        head = git(world.code, "rev-parse", "HEAD")
        fleet = Fleet(world)
        results = {}
        stop = threading.Event()
        killer = threading.Thread(target=fleet.killer, args=(stop,))
        agents = [threading.Thread(target=agent_script, args=(fleet, n, head, results)) for n in range(AGENTS)]
        killer.start()
        started = time.monotonic()
        for thread in agents:
            thread.start()
        for thread in agents:
            thread.join(timeout=240)
        stop.set()
        killer.join()
        elapsed = time.monotonic() - started
        self.assertEqual(len(results), AGENTS, f"agents still running after {elapsed:.0f}s")
        failures = {agent: outcome for agent, outcome in results.items() if not re.fullmatch(r"[0-9a-z]{9}", str(outcome))}
        self.assertEqual(failures, {})
        for agent, code, err in [(a, c, e) for a, _, c, e in fleet.log if c is not None]:
            self.assertNotIn("Traceback", err, f"agent {agent}")
        settle = world.run("create", "--title", "Settle", "--quote", QUOTE)  # a fresh writer proceeds at once
        self.assertEqual(settle.returncode, 0, settle.stderr)
        self.assertEqual(world.run("check").returncode, 0, world.run("check").stdout)
        items = sorted(path.stem for path in (world.ledger / "items").glob("*.md"))
        self.assertEqual(len(items), AGENTS + 1, "one item per key, even after kill and retry")
        self.assertEqual(len(set(items)), len(items))
        listing = [json.loads(line) for line in world.run("list", "--json").stdout.splitlines()]
        states = sorted(row["state"] for row in listing)
        self.assertEqual(states, ["approved"] + ["completed"] * AGENTS)
        for item in items:
            kinds = [event["type"] for event in world.events(item)]
            self.assertLessEqual(kinds.count("completed"), 1)
            self.assertLessEqual(kinds.count("evidence"), 1)
            self.assertLessEqual(kinds.count("commented"), 1)
        self.assertEqual(world.ledger_dirty(), [])
        subjects = world.ledger_log()
        self.assertEqual([s for s in subjects if not SUBJECT.match(s)], [])
        self.assertIn("lock", "".join(os.listdir(world.ledger / ".qew")))  # lock files remain, and blocked nobody
        remote_head = git(world.base, "--git-dir", str(world.remote), "rev-parse", "main")
        self.assertEqual(git(world.ledger, "rev-parse", "main"), remote_head, "the remote converged")

    def test_ten_agents_on_a_git_ledger_with_killed_writers_converge(self) -> None:
        """Prevents stale locks, duplicate items, tracebacks, dirty checkouts and a diverged remote under ten parallel agents."""
        self.run_fleet("git")

    @unittest.skipUnless(jj_available(), "jj is not installed")
    def test_ten_agents_on_a_jj_ledger_with_killed_writers_converge(self) -> None:
        """Prevents the same failures on a colocated jj ledger."""
        self.run_fleet("jj")


if __name__ == "__main__":
    unittest.main()
