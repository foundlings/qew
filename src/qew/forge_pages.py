"""Conditional listing of the issue pages: ask each page with its cached ETag, reuse it on 304.

The pages are ordered by creation and numbered from 1, so the union of the pages is the complete current
issue set on every call and an issue missing from it was deleted. A page's ETag changes whenever any issue
on it is created, edited, commented, deleted or shifted onto it, so the cost scales with the changed pages.
"""

from collections.abc import Callable, Mapping

from qew.forge import CachedPage, ForgeRejected, PageListing, RemoteIssue, Unsupported
from qew.forge_http import MAX_PAGES, NotModified, header

DEFAULT_PAGE_SIZE = 100

# page number, ETag to send ("" for none) -> NotModified, or (parsed issues, raw entry count, response headers, more)
Fetch = Callable[[int, str], NotModified | tuple[tuple[RemoteIssue, ...], int, Mapping[str, str], bool]]


def list_pages(fetch: Fetch, cached: Mapping[int, CachedPage], page_size: int = DEFAULT_PAGE_SIZE) -> PageListing:
    pages = []
    fetched = reused = 0
    for number in range(1, MAX_PAGES + 1):
        prior = cached.get(number)
        answer = fetch(number, prior.etag if prior else "")
        if isinstance(answer, NotModified):
            if prior is None:
                raise ForgeRejected(304, f"issue page {number} answered 304 though no copy of it was sent")
            page = prior
            reused += 1
            more = prior.more or _probe(prior, cached, page_size)
        else:
            issues, raw, headers, more = answer
            etag = header(headers, "ETag")
            if not etag:
                raise Unsupported("conditional_listing")
            page = CachedPage(etag, issues, more, raw)
            fetched += 1
        pages.append(page)
        if not more:
            return PageListing(tuple(pages), fetched, reused)
    raise ForgeRejected(200, f"more than {MAX_PAGES} issue pages; refusing a truncated listing")


def _probe(page: CachedPage, cached: Mapping[int, CachedPage], page_size: int) -> bool:
    """A reused last page that is as full as the others may have gained a successor its ETag cannot show."""
    size = max((p.raw for p in cached.values() if p.more), default=page_size)
    return page.raw >= size
