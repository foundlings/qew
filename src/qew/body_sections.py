"""A body composed from headed sections: where, problem or goal, fix or approach, done when."""

import argparse

from qew.errors import QewError

SECTIONS = (("where", "Where"), ("problem", "Problem"), ("goal", "Goal"), ("fix", "Fix"),
            ("approach", "Approach"), ("done_when", "Done when"))


def compose(arguments: argparse.Namespace) -> str | None:
    """The sectioned body the structured flags describe, or None when none was given."""
    given = {key: getattr(arguments, key) for key, _ in SECTIONS if getattr(arguments, key, None) is not None}
    if not given:
        return None
    for left, right in (("problem", "goal"), ("fix", "approach")):
        if left in given and right in given:
            raise QewError(f"--{left} and --{right} frame the same thing; use one", 2)
    return "\n\n".join(f"## {title}\n\n{given[key].strip()}" for key, title in SECTIONS if key in given) + "\n"
