"""Pull request reviews as comments on the item the pull request covers.

A review and an inline review comment each become one `commented` event (author, original time, provenance forge) stored once
by remote reference. A pull request is read when the forge's cheap stamp (state and last change, `pull_stamps`) shows it changed since
its reviews were last read (`reviews_seen`), at most `QEW_REVIEW_READS` (150) a pass; a merged or closed one, by the record or by the
forge, is read to the end once and then left alone (`reviews_read` in the local mirror state). Forge text is untrusted data and only ever stored as text.
"""

import os
from typing import TYPE_CHECKING

from qew import instants
from qew.comments import comments_after
from qew.forge import ForgeRejected, RemoteReview, RemoteReviewComment, Unsupported
from qew.forge_text import clean_body, clean_line
from qew.ledger_store import Ledger, Snapshot
from qew.mirror_types import MirrorRecord, updated

if TYPE_CHECKING:
    from qew.sync import Target


READS_PER_PASS = 150  # pull requests whose reviews one pass reads (env QEW_REVIEW_READS); the rest wait for the next pass


def reads_per_pass() -> int:
    raw = os.environ.get("QEW_REVIEW_READS", "")
    return int(raw) if raw.strip().isdigit() else READS_PER_PASS


def pull(ledger: Ledger, snapshot: Snapshot, target: Target, prs: list[tuple[str, MirrorRecord]]) -> int:
    """Import the reviews of the tracked pull requests that changed; returns how many comments were added.

    A pull request costs two forge calls to read, so a pass asks the forge once, cheaply (`pull_stamps`: one query for a hundred), which
    are open and when each last changed, and reads only those that changed since their reviews were last read (`reviews_seen`), at most
    `reads_per_pass()` a pass (the rest wait for the next). One the forge says is finished, whatever the ledger's own `state` still
    says, is read to the end once and marked read; one the forge no longer has is marked read without a call. A forge that cannot give
    stamps is read pull request by pull request under the same cap."""
    pending = [(host, record) for host, record in prs if not record.get("reviews_read")]
    stamps = target.forge.pull_stamps([record["number"] for _, record in pending]) if pending else None
    added = 0
    budget = reads_per_pass()
    for host, record in pending:
        number = record["number"]
        stamp = None if stamps is None else stamps.get(number)
        if stamps is not None and stamp is None:
            ledger.write_mirror(host, record["_target"], updated(record, reviews_read=True))  # the forge no longer has it: nothing to read
            continue
        if stamp is not None and stamp.open and record.get("reviews_seen") == stamp.updated:
            continue  # unchanged since its reviews were last read
        if budget <= 0:
            break
        budget -= 1
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
        finished = record.get("state") in ("merged", "closed") or (stamp is not None and not stamp.open)
        if finished:
            ledger.write_mirror(host, record["_target"], updated(record, reviews_read=True))
        elif stamp is not None:
            ledger.write_mirror(host, record["_target"], updated(record, reviews_seen=stamp.updated))
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
