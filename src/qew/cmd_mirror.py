"""`qew mirror add|remove|list`: edit the `[[mirror]]` targets in `.qew/config.toml`."""

import argparse
import json

from qew import config_edit, forge_registry, ledger_config
from qew.cmd_sync import choose
from qew.errors import QewError
from qew.ledger_config import Config, Mirror


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    mirror = commands.add_parser("mirror", help="mirror targets: add | remove | list")
    sub = mirror.add_subparsers(dest="mirror_command", required=True)
    add = sub.add_parser("add", help="add a forge repository as a mirror target")
    add.add_argument("forge", help="github, forgejo, gitea or gitlab")
    add.add_argument("repo", help="repository URL, https://HOST/OWNER/REPO")
    add.add_argument("--read-only", action="store_true", help="a one-way mirror: edits made on the forge are never imported")
    add.add_argument("--api", help="API root for a self-hosted forge")
    remove = sub.add_parser("remove", help="drop a mirror target from the config (its mirror files stay)")
    remove.add_argument("target", help="target slug or a unique part of it")
    listing = sub.add_parser("list", help="list the mirror targets and their role")
    listing.add_argument("--json", action="store_true", help="machine-readable output")


def run(arguments: argparse.Namespace) -> bool:
    if arguments.command != "mirror":
        return False
    config = ledger_config.load()
    if arguments.mirror_command == "list":
        print(list_mirrors(config, arguments.json))
    elif arguments.mirror_command == "add":
        print(add(config, arguments.forge, arguments.repo, arguments.read_only, arguments.api))
    else:
        print(remove(config, arguments.target))
    return True


def role(config: Config, mirror: Mirror) -> str:
    slug = ledger_config.mirror_slug(mirror)
    if slug == config.authority:
        return "primary"
    return "read-only" if ledger_config.is_read_only(config, mirror) else "two-way"


def list_mirrors(config: Config, as_json: bool) -> str:
    rows = [(ledger_config.mirror_slug(m), role(config, m), m.forge, m.repo) for m in config.mirrors]
    if as_json:
        return json.dumps([{"target": s, "role": r, "forge": f, "repo": u} for s, r, f, u in rows])
    return "\n".join(f"{slug} {kind} {forge} {repo}" for slug, kind, forge, repo in rows) or "no mirror targets"


def _need_config_file(config: Config) -> None:
    if not (config.root / ledger_config.CONFIG).is_file():
        raise QewError("mirror targets live in the code repo's .qew/config.toml; run this from the code repo", 2)


def add(config: Config, forge: str, repo: str, read_only: bool, api: str | None = None) -> str:
    _need_config_file(config)
    canonical = forge_registry.canonical(forge)
    if canonical not in ("github", "forgejo", "gitlab"):
        raise QewError(f"unknown forge `{forge}`; known: github, forgejo, gitea, gitlab", 2)
    entry: dict[str, object] = {"forge": canonical, "repo": repo}
    if api:
        entry["api"] = api
    if read_only:
        entry["read_only"] = True
    created = ledger_config.validated_mirror(entry)
    slug = ledger_config.mirror_slug(created)
    if slug in [ledger_config.mirror_slug(m) for m in config.mirrors]:
        raise QewError(f"`{slug}` is already a mirror target", 2)
    lines = [(key, json.dumps(value) if isinstance(value, str) else "true") for key, value in entry.items()]
    config_edit.update(config.root, lambda text: config_edit.append_mirror(text, lines))
    return f"added {slug}" + (" (read-only)" if read_only else "")


def remove(config: Config, wanted: str) -> str:
    _need_config_file(config)
    (found,) = choose(list(config.mirrors), wanted)
    slug = ledger_config.mirror_slug(found)
    if slug == config.authority:
        raise QewError(f"`{slug}` is the primary forge; migrate with `qew forge migrate --to TARGET` first", 2)
    index = [ledger_config.mirror_slug(m) for m in config.mirrors].index(slug)
    config_edit.update(config.root, lambda text: config_edit.remove_mirror(text, index))
    return f"removed {slug}"
