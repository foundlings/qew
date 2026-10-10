"""Helpers shared by the lifecycle walks: a byte snapshot of the ledger and a started item."""

from pathlib import Path

from tests.ledger_harness import World


def tree(world: World) -> dict[str, bytes]:
    """Every ledger file (not the VCS or cache) with its bytes, to prove a refusal wrote nothing."""
    root: Path = world.ledger
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".git" not in path.parts and ".qew" not in path.parts
    }


def approved(world: World, *arguments: str, title: str = "Work") -> str:
    item = world.create("--kind", "issue", "--title", title, *arguments, "--draft")
    world.approve(item)
    return item


def started(world: World, *arguments: str, title: str = "Work") -> str:
    item = approved(world, *arguments, title=title)
    result = world.run("start", item)
    assert result.returncode == 0, result.stderr
    return item


def event_types(world: World, item: str) -> list:
    return [event["type"] for event in world.events(item)]
