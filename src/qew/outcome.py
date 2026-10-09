"""Command results: one terse human line, or a versioned JSON object for agent loops."""

import argparse
import json

SCHEMA_VERSION = 1


def wants_json(arguments: argparse.Namespace) -> bool:
    """`--json` placed before the command (`json_first`) or after it (`json`)."""
    return bool(getattr(arguments, "json", False) or getattr(arguments, "json_first", False))


def render(arguments: argparse.Namespace, human: str, **data: object) -> str:
    """The text to print: `human`, or `{"schema_version": 1, ...data}` when JSON was asked for."""
    if wants_json(arguments):
        return json.dumps({"schema_version": SCHEMA_VERSION, **data})
    return human
