"""Extra assignees of an item: `assigned` and `unassigned` events naming forge logins.

The claim keeps exactly one owner; these are the other people the forge issue lists as assigned. Two machines assigning
different logins write different event files, so they merge without a conflict.
"""

from qew.errors import QewError
from qew.ledger_event import Event, valid_actor


def assignees_after(events: list[Event]) -> tuple[str, ...]:
    """The logins currently assigned, in the order they were first assigned."""
    found: list[str] = []
    for event in sorted((e for e in events if e.type in ("assigned", "unassigned")), key=lambda e: e.id):
        login = str(event.get("login"))
        if event.type == "assigned" and login not in found:
            found.append(login)
        elif event.type == "unassigned" and login in found:
            found.remove(login)
    return tuple(found)


def checked_login(login: str) -> str:
    if not valid_actor(login):
        raise QewError(f"`{login}` is not a login: one word without spaces or commas", 2)
    return login
