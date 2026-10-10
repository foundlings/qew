"""Comments, both ways, between a ledger item and its forge issue.

Pull runs first (as for everything else): a forge comment we pushed is recognised by its marker and never imported; a human's
comment becomes a `commented` event; an edit or delete of any linked comment becomes `comment_edited` / `comment_deleted`
(provenance forge). Push then writes what the ledger holds that the forge does not: new comments, edits, deletes. The link
between a forge comment and a ledger comment is local sync state (`cmap`); a checkout without it adopts by marker.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING

from qew import comment_mirror
from qew.forge_text import clean_body, clean_line
from qew.comments import comments_after
from qew.forge import Forge, Unsupported
from qew.ledger_store import Ledger, Snapshot
from qew.mirror_types import CommentLink, MirrorRecord, updated

if TYPE_CHECKING:
    from qew.forge_pull import PullReport
    from qew.sync import Target, TargetReport


def pull(ledger: Ledger, snapshot: Snapshot, target: Target, report: PullReport, item_id: str, number: int,
         record: MirrorRecord, seen_ids: list[str]) -> tuple[list[str], dict[str, CommentLink]]:
    """Read the issue's comments and reconcile them with the ledger's; returns every forge comment id seen and the new links."""
    forge_comments = target.forge.list_comments(number)
    ledger_comments = {c.id: c for c in comments_after(snapshot.events[item_id])}
    by_remote = {c.remote_ref: c.id for c in ledger_comments.values() if c.remote_ref}
    cmap: dict[str, CommentLink] = dict(record.get("cmap", {}))
    present = {c.id: c for c in forge_comments}
    ids = list(seen_ids)
    for comment in forge_comments:
        if comment.id not in ids:
            ids.append(comment.id)
        marker, text = comment_mirror.parse(comment.body)
        sha = comment_mirror.digest(comment.body)
        remote_ref = f"{target.slug}#{number}/{comment.id}"
        link = cmap.get(comment.id)
        if link is None:
            if marker is not None and marker in ledger_comments:  # ours, pushed by another checkout or forgotten here
                cmap[comment.id] = {"ledger": marker, "sha": sha}
            elif remote_ref in by_remote:  # imported before links existed
                cmap[comment.id] = {"ledger": by_remote[remote_ref], "sha": sha}
            else:
                event = ledger.append_event(item_id, "commented", text=clean_body(text if marker else comment.body),
                                            author=clean_line(comment.author), remote_ref=remote_ref, provenance="forge")
                cmap[comment.id] = {"ledger": event.id, "sha": sha}
                report.comments += 1
        elif link["sha"] != sha:
            current = ledger_comments.get(link["ledger"])
            fresh = clean_body(text if marker else comment.body)
            if current is not None and not current.deleted and current.text != fresh:
                ledger.append_event(item_id, "comment_edited", ref=link["ledger"], text=fresh, provenance="forge")
                report.applied += 1
            cmap[comment.id] = {"ledger": link["ledger"], "sha": sha}
    for forge_id, link in list(cmap.items()):
        if forge_id not in present:  # removed on the forge
            current = ledger_comments.get(link["ledger"])
            if current is not None and not current.deleted:
                ledger.append_event(item_id, "comment_deleted", ref=link["ledger"], provenance="forge")
                report.applied += 1
            del cmap[forge_id]
    return ids, cmap


def push(ledger: Ledger, snapshot: Snapshot, target: Target, report: TargetReport, item_id: str, record: MirrorRecord,
         pace: Callable[[], None]) -> MirrorRecord:
    """Create, update or delete the forge comments for the ledger's own comments; imported ones are the forge's already."""
    events = snapshot.events[item_id]
    comments = comments_after(events)
    cmap: dict[str, CommentLink] = dict(record.get("cmap", {}))
    ours = [c for c in comments if not c.remote_ref]
    by_ledger = {link["ledger"]: forge_id for forge_id, link in cmap.items()}
    by_id = {c.id: c for c in comments}
    wanted = {c.id: comment_mirror.render(c, by_id.get(c.reply_to)) for c in ours if not c.deleted}
    unmapped = [c for c in ours if not c.deleted and c.id not in by_ledger]
    stale = [c for c in ours if not c.deleted and c.id in by_ledger and cmap[by_ledger[c.id]]["sha"] != comment_mirror.digest(wanted[c.id])]
    removed = [c for c in ours if c.deleted and c.id in by_ledger]
    if not (unmapped or stale or removed):
        return record
    forge: Forge = target.forge
    number = record["number"]
    try:
        if unmapped:  # a rerun after a lost response, or another checkout, may have pushed these already
            for existing in forge.list_comments(number):
                marker, _text = comment_mirror.parse(existing.body)
                if marker in {c.id for c in unmapped} and existing.id not in cmap:
                    cmap[existing.id] = {"ledger": marker or "", "sha": comment_mirror.digest(existing.body)}
            by_ledger = {link["ledger"]: forge_id for forge_id, link in cmap.items()}
            unmapped = [c for c in unmapped if c.id not in by_ledger]
            stale = [c for c in ours if not c.deleted and c.id in by_ledger and cmap[by_ledger[c.id]]["sha"] != comment_mirror.digest(wanted[c.id])]
        for comment in unmapped:
            pace()
            made = forge.create_comment(number, wanted[comment.id])
            cmap[made.id] = {"ledger": comment.id, "sha": comment_mirror.digest(wanted[comment.id])}
        for comment in stale:
            forge_id = by_ledger[comment.id]
            forge.update_comment(number, forge_id, wanted[comment.id])
            cmap[forge_id] = {"ledger": comment.id, "sha": comment_mirror.digest(wanted[comment.id])}
        for comment in removed:
            forge_id = by_ledger[comment.id]
            forge.delete_comment(number, forge_id)
            del cmap[forge_id]
    except Unsupported as error:
        if error.capability not in report.skipped:
            report.skipped.append(error.capability)
    fresh = updated(record, cmap=cmap)
    ledger.write_mirror(item_id, target.slug, fresh)
    return fresh


