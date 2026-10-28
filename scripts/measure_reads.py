#!/usr/bin/env python3
"""Measure what the compact read commands cost against reading the ledger files.

Builds a fixed fixture ledger, runs `ready`, `list`, `prime` and `show` in their machine form, and compares
the exact UTF-8 byte size of each answer with the raw bytes of the ledger files a reader would otherwise open.
A token proxy (words and punctuation marks) is printed beside the bytes.

  python3 scripts/measure_reads.py            print the table
  python3 scripts/measure_reads.py --record   write the sizes to docs/specs/read-sizes.json
  python3 scripts/measure_reads.py --check    fail (exit 1) when a command's answer grew past its recorded size
"""

import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from tests.ledger_harness import QUOTE, World  # noqa: E402

DEFAULT_SIZES = ROOT / "docs" / "specs" / "read-sizes.json"
COMMANDS = ("ready", "list", "prime", "show")


def tokens(text: str) -> int:
    """A stdlib token proxy: runs of word characters and single punctuation marks."""
    return len(re.findall(r"\w+|[^\w\s]", text))


def fixture(world: World):
    """A fixed ledger: an epic, twelve issues with bodies and labels, needs, claims, a draft.

    Returns the item to show and the items a reader opens to understand it (itself, its epic, its dependents).
    """
    epic = world.create("--kind", "epic", "--title", "Ship the importer", "--quote", QUOTE)
    first = None
    related = [epic]
    for n in range(12):
        extra = ["--label", "area:importer", "--label", f"phase:{n % 3}", "--priority", str(n % 4)]
        if n >= 6 and first:
            extra += ["--needs", first]
        body = f"Work item {n}: parse the input, validate each record, report errors with line numbers.\n" * 3
        item = world.create("--title", f"Importer step {n:02}: handle record kind {n}", "--parent", epic, "--body", body, "--quote", QUOTE, *extra)
        first = first or item
        if n >= 6:
            related.append(item)
    world.create("--title", "A draft idea", "--body", "Not approved yet.", "--draft")
    world.run("start", first)
    world.run("comment", first, "--body", "Started; the first parser pass is done.")
    return first, [first, *related]


def raw_bytes(world: World, only: list = None) -> int:
    """Bytes of the item and event files (all of them, or only those of the named items)."""
    files = [*(world.ledger / "items").glob("*/*/item.md"), *(world.ledger / "events").glob("*/*/*.json")]  # the sharded layout
    return sum(path.stat().st_size for path in files if only is None or path.parent.name in only)


def measure() -> dict:
    world = World(schema="8")  # the steady state: a flat ledger's prime carries a one-off migration notice
    try:
        shown, related = fixture(world)
        answers = {
            "ready": (world.run("ready", "--json").stdout, raw_bytes(world)),
            "list": (world.run("list", "--json").stdout, raw_bytes(world)),
            "prime": (world.run("prime", "--json").stdout, raw_bytes(world)),
            "show": (world.run("show", shown, "--json").stdout, raw_bytes(world, related)),
        }
    finally:
        world.close()
    return {name: {"raw_bytes": raw, "compact_bytes": len(text.encode("utf-8")), "compact_tokens": tokens(text)}
            for name, (text, raw) in answers.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="fail when a command's answer grew past its recorded size")
    parser.add_argument("--record", action="store_true", help="write the measured sizes to the sizes file")
    parser.add_argument("--sizes", default=str(DEFAULT_SIZES), help="the sizes file (default docs/specs/read-sizes.json)")
    arguments = parser.parse_args()
    measured = measure()
    print(f"{'command':8} {'raw bytes':>10} {'compact bytes':>14} {'tokens':>7} {'saved':>6}")
    for name in COMMANDS:
        m = measured[name]
        print(f"{name:8} {m['raw_bytes']:>10} {m['compact_bytes']:>14} {m['compact_tokens']:>7} {100 - 100 * m['compact_bytes'] // m['raw_bytes']:>5}%")
    path = Path(arguments.sizes)
    if arguments.record:
        path.write_text(json.dumps(measured, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return 0
    if arguments.check:
        recorded = json.loads(path.read_text(encoding="utf-8"))
        grown = [f"{n}: {measured[n]['compact_bytes']} bytes, recorded {recorded[n]['compact_bytes']}"
                 for n in COMMANDS if measured[n]["compact_bytes"] > recorded[n]["compact_bytes"]]
        if grown:
            print("read output grew: " + "; ".join(grown), file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
