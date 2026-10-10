"""Pull request reviews as comments on the item the pull request covers.

A review and an inline review comment each become one `commented` event (author, original time, provenance forge) stored once
by remote reference. An open pull request is read on every pull; a merged or closed one is read to the end once and then
left alone (`reviews_read` in the local mirror state). Forge text is untrusted data and only ever stored as text.
"""

from typing import TYPE_CHECKING

from qew import instants
from qew.comments import comments_after
from qew.forge import ForgeRejected, RemoteReview, RemoteReviewComment, Unsupported
from qew.forge_text import clean_body, clean_line
from qew.ledger_store import Ledger, Snapshot
from qew.mirror_types import MirrorRecord, updated

if TYPE_CHECKING:
    from qew.sync import Target


def pull(ledger: Ledger, snapshot: Snapshot, target: Target, prs: list[tuple[str, MirrorRecord]]) -> int:
    """Import the reviews of every tracked pull request that still needs reading; returns how many comments were added."""
    added = 0
    for host, record in prs:
        if record.get("reviews_read"):
            continue
        number = record["number"]
        covered = next((i for i in record.get("items", []) if i in snapshot.items), host)
        known = {c.remote_ref for c in comments_after(snapshot.events[covered]) if c.remote_ref}
        try:
            reviews = target.forge.list_reviews(number)
            inline = target.forge.list_review_comments(number)
        except Unsupported:
            return added
        except ForgeRejected as error:
            if error.status != 404:
                raise
            # the forge no longer has this pull request: nothing to read, and nothing to ask about again
            ledger.write_mirror(host, record["_target"], updated(record, reviews_read=True))
            continue
        for review in reviews:
            added += _add(ledger, covered, target, known, f"{target.slug}#pr{number}/review/{review.id}", review.submitted,
                          review.author, _review_text(review))
        for comment in inline:
            added += _add(ledger, covered, target, known, f"{target.slug}#pr{number}/inline/{comment.id}", comment.created,
                          comment.author, _inline_text(comment))
        if record.get("state") in ("merged", "closed"):
            ledger.write_mirror(host, record["_target"], updated(record, reviews_read=True))
    return added


def _review_text(review: RemoteReview) -> str:
    body = clean_body(review.body).strip()
    return f"Review {review.state}" + (f"\n\n{body}" if body else "")


def _inline_text(comment: RemoteReviewComment) -> str:
    where = f"{clean_line(comment.path)}:{comment.line}" if comment.line else clean_line(comment.path)
    return f"Review comment on {where}\n\n{clean_body(comment.body).strip()}"


def _add(ledger: Ledger, item_id: str, target: Target, known: set[str], ref: str, when: str, author: str, text: str) -> int:
    if ref in known:
        return 0
    known.add(ref)
    stamp = when if instants.parse_instant(when) is not None else ""
    ledger.append_event(item_id, "commented", at=stamp, text=text, author=clean_line(author), remote_ref=ref, provenance="forge")
    return 1
