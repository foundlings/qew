"""A code repo, a ledger with a synced item and an in-memory forge, driven through `qew.cli.main` in process."""

import contextlib
import io
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import cli, cmd_claim, cmd_items, ledger_config
from qew.ledger_store import Ledger
from qew.sync import Target, sync_target
from tests.ledger_harness import QUOTE, World, git
from tests.memory_forge import MemoryForge

MIRRORS = '\n[[mirror]]\nforge = "forgejo"\nrepo = "https://git.example/o/r"\n'


class Run:
    """One qew invocation: exit code, stdout, stderr."""

    def __init__(self, code: int, out: str, err: str) -> None:
        self.code, self.out, self.err = code, out, err

    def __repr__(self) -> str:
        return f"Run(code={self.code}, out={self.out!r}, err={self.err!r})"


class PrWorld:
    """`actor` acts through the in-memory forge; `as_actor` switches who is calling."""

    def __init__(self, extra_config: str = "", actor: str = "tester", mirror_options: str = "") -> None:
        mirrors = MIRRORS.replace('/o/r"\n', '/o/r"\n' + mirror_options, 1) if mirror_options else MIRRORS
        self.world = World(mirrors + extra_config)
        self.forge = MemoryForge("git.example")
        self.config = ledger_config.load(self.world.code)
        self.ledger = Ledger(self.world.ledger, "TST")
        self.actor = actor
        self.head = "feature"

    def close(self) -> None:
        self.world.close()

    # -- items -------------------------------------------------------------------
    def item(self, title: str = "Work", kind: str = "issue", claim: bool = True, draft: bool = False,
             parent: str | None = None, body: str = "", sync: bool = True, by: str | None = None) -> str:
        specs = [{"kind": kind, "title": title, "parent": parent or "", "body": body}]
        with self._env(by):
            created = cmd_items.create(self.config, specs, None if draft else QUOTE, False)
            item_id = created[0].split()[0]
            if claim and not draft:
                cmd_claim.start(self.config, item_id)
        if sync:
            self.sync()
        return item_id

    def sync(self) -> None:
        mirror = self.config.mirrors[0]
        sync_target(self.ledger, Target(mirror, ledger_config.mirror_slug(mirror), self.forge), self.config)

    def approve(self, *ids: str, tree: bool = False) -> Run:
        return self.qew("draft", "approve", *ids, *(["--tree"] if tree else []), "--quote", QUOTE)

    def events(self, item_id: str) -> list:
        return self.world.events(item_id)

    def mirror_files(self, item_id: str) -> list[str]:
        directory = self.world.ledger / "mirrors" / item_id
        return sorted(p.name for p in directory.glob("*-pr-*.json")) if directory.is_dir() else []

    # -- running -----------------------------------------------------------------
    @contextlib.contextmanager
    def _env(self, actor: str | None = None):
        previous = os.getcwd()
        os.chdir(self.world.code)
        try:
            with mock.patch.dict(os.environ, {"QEW_ACTOR": actor or self.actor, "QEW_OFFLINE": "1", "QEW_NO_FETCH": "1"}):
                yield
        finally:
            os.chdir(previous)

    def qew(self, *argv: str, actor: str | None = None, forge: MemoryForge | None = None, sleep=None,
            env: dict | None = None) -> Run:
        """Run `qew ARGV` in process against the in-memory forge."""
        out, err = io.StringIO(), io.StringIO()
        used = forge or self.forge
        patches = [mock.patch("qew.forge_registry.build", lambda mirror: used)]
        if sleep is not None:
            patches.append(mock.patch("time.sleep", sleep))
        if env:
            patches.append(mock.patch.dict(os.environ, env))
        with self._env(actor), contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    code = cli.main(list(argv))
                except SystemExit as exit_:  # argparse usage errors
                    code = exit_.code if isinstance(exit_.code, int) else 2
        return Run(code, out.getvalue(), err.getvalue())

    def open(self, item_id: str, *extra: str, **kwargs) -> Run:
        return self.qew("pr", "open", item_id, "--head", self.head, "--base", "main", *extra, **kwargs)

    def commit(self, message: str = "work") -> str:
        return self.world.commit(message)


__all__ = ["PrWorld", "Run", "git"]
