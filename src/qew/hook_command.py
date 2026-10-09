"""The one shell string an agent runs for a qew hook event.

A single `command` string works on every agent version (no exec-form `args`). When `qew` is not on
PATH the hook skips with a one-line warning on stdout instead of failing: a missing tool must not
break every shell call. Real refusals still block, because `exec` hands qew's own exit status through.
"""

_SKIP = '{"systemMessage":"qew is not on PATH; qew hooks skipped"}'


def guarded(event: str, agent: str) -> str:
    return f"command -v qew >/dev/null 2>&1 || {{ echo '{_SKIP}'; exit 0; }}; exec qew hook {event} --agent {agent}"
