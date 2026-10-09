"""Ten agents on one machine, one ledger checkout and one code repo, with chosen writers killed mid-session."""

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import PROJECT_ROOT, QUOTE, World, git

AGENTS = 10
KILL_AFTER_S = 0.2
# (agent, verb) pairs whose FIRST attempt is killed 0.2 s after it starts: a fixed choice, not a random one.
DOOMED = {(1, "create"), (3, "start"), (5, "evidence"), (7, "complete"), (9, "comment")}
GOOD = {"start": {0, 5}}  # a repeated start is exit 5: already yours, nothing written
# a writer killed between writing and committing leaves files the next writer commits as `qew: recover N files`
SUBJECT = re.compile(r"^(qew: [a-z-]+( [A-Za-z0-9]+| \d+ items)? by \S+|qew: recover \d+ files?|init ledger|.*merge.*)$", re.IGNORECASE)


class Fleet:
    """Runs qew processes for every agent; the first attempt of the DOOMED (agent, verb) pairs is killed mid-flight."""

    def __init__(self, world: World) -> None:
        self.world = world
        self.mutex = threading.Lock()
        self.log = []
        self.seen = set()
        self.environment = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT / "src"), "QEW_UNDER_TEST": "1",
                            "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e.invalid",
                            "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e.invalid"}

    def qew(self, agent: int, *arguments: str):
        environment = {**self.environment, "QEW_ACTOR": f"agent{agent}"}
        process = subprocess.Popen([sys.executable, "-m", "qew.cli", *arguments], cwd=self.world.code, env=environment,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        with self.mutex:
            first = (agent, arguments[0]) not in self.seen
            self.seen.add((agent, arguments[0]))
        timer = None
        if first and (agent, arguments[0]) in DOOMED:
            timer = threading.Timer(KILL_AFTER_S, lambda: process.poll() is None and process.send_signal(signal.SIGKILL))
            timer.start()
        out, err = process.communicate()
        if timer is not None:
            timer.cancel()
        with self.mutex:
            self.log.append((agent, arguments[0], process.returncode, err))
        return process.returncode, out, err


def retry(fleet: Fleet, agent: int, arguments: list, attempts: int = 6):
    code, out, err = None, "", ""
    for _ in range(attempts):
        code, out, err = fleet.qew(agent, *arguments)
        if code is not None and code >= 0 and not (code == 3 and "locked" in err):
            break  # a finished answer; a killed (negative) or lock-timed-out attempt is retried
    return code, out, err


def agent_script(fleet: Fleet, agent: int, head: str, results: dict) -> None:
    key = f"agent-{agent}"
    title = f"Work of agent {agent}"
    item = None
    for _ in range(2):  # the second create is an eager retry of the same intent
        code, out, err = retry(fleet, agent, ["create", "--title", title, "--key", key, "--quote", QUOTE])
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


def run_fleet(test, vcs: str) -> None:
    world = World(ledger_vcs=vcs, remote=True)
    test.addCleanup(world.close)
    head = git(world.code, "rev-parse", "HEAD")
    fleet = Fleet(world)
    results = {}
    agents = [threading.Thread(target=agent_script, args=(fleet, n, head, results)) for n in range(AGENTS)]
    started = time.monotonic()
    for thread in agents:
        thread.start()
    for thread in agents:
        thread.join(timeout=240)
    elapsed = time.monotonic() - started
    test.assertEqual(len(results), AGENTS, f"agents still running after {elapsed:.0f}s")
    failures = {agent: outcome for agent, outcome in results.items() if not re.fullmatch(r"[0-9a-z]{9}", str(outcome))}
    test.assertEqual(failures, {})
    killed = [(agent, verb) for agent, verb, code, _ in fleet.log if code is not None and code < 0]
    test.assertGreaterEqual(len(killed), 3, f"too few writers were killed mid-flight: {killed}")
    for agent, _, code, err in fleet.log:
        test.assertNotIn("Traceback", err, f"agent {agent}")
    settle = world.run("create", "--title", "Settle", "--quote", QUOTE)  # a fresh writer proceeds at once
    test.assertEqual(settle.returncode, 0, settle.stderr)
    check = world.run("check")
    test.assertEqual(check.returncode, 0, check.stdout)
    items = sorted(path.stem for path in (world.ledger / "items").glob("*.md"))
    test.assertEqual(len(items), AGENTS + 1, "one item per key, even after kill and retry")
    test.assertEqual(len(set(items)), len(items))
    listing = [json.loads(line) for line in world.run("list", "--json").stdout.splitlines()]
    test.assertEqual(sorted(row["state"] for row in listing), ["approved"] + ["completed"] * AGENTS)
    for item in items:
        kinds = [event["type"] for event in world.events(item)]
        test.assertLessEqual(kinds.count("completed"), 1)
        test.assertLessEqual(kinds.count("evidence"), 1)
        test.assertLessEqual(kinds.count("commented"), 1)
    test.assertEqual(world.ledger_dirty(), [])
    test.assertEqual([s for s in world.ledger_log() if not SUBJECT.match(s)], [])
    test.assertTrue(any("lock" in name for name in os.listdir(world.ledger / ".qew")), "lock files stay and block nobody")
    remote_head = git(world.base, "--git-dir", str(world.remote), "rev-parse", "main")
    test.assertEqual(git(world.ledger, "rev-parse", "main"), remote_head, "the remote converged")
