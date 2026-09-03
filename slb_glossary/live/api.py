"""Search engine API for the SLB glossary."""

import asyncio
import logging
import math
import time
import typing
from collections.abc import Collection

from patchright.async_api import Page

from slb_glossary.errors import NetworkError, ParsingError, SessionNotInitializedError
from slb_glossary.live.browser import Session
from slb_glossary.live.parsers import (
    TermBlock,
    get_result_links,
    get_results_header_text,
    get_term_detail_blocks,
    get_term_images,
    get_term_name,
    get_total_term_count,
    resolve_grammatical_label,
)
from slb_glossary.live.topics import fetch_topics
from slb_glossary.live.urls import build_pager_query, build_search_url
from slb_glossary.retries import retry
from slb_glossary.types import RelatedTerm, SearchResult
from slb_glossary.utils import as_async_iterator, get_topic_match, log_timed_yields, split_exclude

logger = logging.getLogger(__name__)


__all__ = [
    "ensure_initialized",
    "get_results_from_url",
    "get_results_from_urls",
    "get_terms_on",
    "get_terms_urls",
    "search",
]


RELATED_KEYWORDS = ("related term", "see related", "synonyms", "alternate form")


async def ensure_initialized(session: Session, auto_initialize: bool = True) -> None:
    """
    Initialize `session` if it isn't already, or raise if it can't be.

    Every function here that actually touches the live site (as opposed
    to just reading something already loaded, like `session.language`)
    calls this first, so a session opened lazily via `open_session(..., initialize=False)`
    only pays to load `topics`/`size` at the point something genuinely
    needs them, not up front.

    :param session: The session to ensure is initialized.
    :param auto_initialize: If `True` (the default) and `session` isn't
        initialized yet, initialize it now (`session.initialize()`)
        before returning. If `False`, an uninitialized `session` raises
        instead of being initialized automatically. Use this where
        silently opening a page and fetching topics on the caller's
        behalf would be surprising, and you'd rather the caller call
        `session.initialize()` explicitly first.
    :raises SessionNotInitializedError: If `session` isn't initialized
        and `auto_initialize` is `False`.
    :raises NetworkError: If `auto_initialize` is `True` and the glossary
        site could not be reached while initializing.
    """
    if session.initialized:
        return
    if not auto_initialize:
        raise SessionNotInitializedError(
            "Session is not initialized and `auto_initialize=False`. Call "
            "`session.initialize()` first, open it with "
            "`open_session(..., initialize=True)`, or pass `auto_initialize=True` "
            "to let this call initialize it lazily instead."
        )
    await session.initialize()


async def goto(
    session: Session, url: str, *, page: Page | None = None, timeout: float | None = None
) -> Page:
    async def navigate() -> Page:
        nonlocal page
        if page is None:
            page = await session.new_page()
        await page.goto(url, timeout=timeout, wait_until="domcontentloaded")
        return page

    return await retry(navigate, policy=session.retry, raise_exception=True)  # type: ignore[return-value]


def _find_related_links(blocks: typing.Sequence[TermBlock]) -> tuple[RelatedTerm, ...]:
    """
    Return the related-term links from a definition section's blocks.

    :param blocks: A definition section's `TermBlock`s, as returned
        by `slb_glossary.parsers.get_term_detail_blocks`.
    :return: The related terms found, in the order they're linked. Empty
        if no block in the block links to any related terms.
    """
    for block in blocks:
        text_lower = block.text.lower()
        if block.links and any(keyword in text_lower for keyword in RELATED_KEYWORDS):
            return block.links

    # Fall back to any block with links at all, in case the site's
    # wording of the "related terms" lead-in ever changes.
    for block in blocks:
        if block.links:
            return block.links
    return ()


async def _wait_for_settle(
    session: Session,
    url: str,
    *,
    page: Page | None = None,
    previous_links: typing.Sequence[str],
    previous_header: str,
) -> tuple[list[str], str]:
    """
    Load `url` and wait until the results panel differs from the given baseline.

    The glossary is a mostly single-page application. Navigating between search
    filters changes only the URL fragment, so a fresh `page.goto` can
    resolve before the site's JavaScript has actually re-rendered the
    results panel. This polls the rendered result links and results header
    until at least one of them differs from the caller's baseline, or
    until `session.settle_timeout` elapses. Whichever comes first.

    :param session: The session to load `url` on.
    :param url: The search URL to load.
    :param previous_links: Result links rendered on the page *before* this
        navigation. Always pass the page's actual current state here, even
        for the first search of a session. The glossary auto-runs an
        unfiltered query as soon as the search screen loads, so there is
        always something real to diff against. An empty sequence here
        means "nothing rendered yet", which skips the wait entirely and
        risks reading a stale, pre-filter panel.
    :param previous_header: Results header text rendered before this
        navigation. This is a second, independent signal that the panel actually
        updated, so a coincidental match on `previous_links` alone (e.g.
        the same top result happens to rank first for two different
        queries) doesn't return before the panel has really changed.
    :return: The `(links, header_text)` pair read once the panel changed,
        or the last values read if `session.settle_timeout` elapsed first
        without any observed change.
    """
    goto_started_at = time.monotonic()
    current_page = await goto(session, url, page=page)
    logger.debug("Loaded %s in %.3fs", url, time.monotonic() - goto_started_at)

    settle_started_at = time.monotonic()
    settle_timeout = session.settle_timeout / 1000
    poll_interval = session.poll_interval / 1000
    deadline = settle_started_at + settle_timeout
    previous_links = list(previous_links)
    polls = 0
    while True:
        current_links = await get_result_links(current_page)
        current_header = await get_results_header_text(current_page)
        if current_links != previous_links or current_header != previous_header:
            logger.debug(
                "Results panel settled after %.3fs (%d poll(s)) for %s",
                time.monotonic() - settle_started_at,
                polls,
                url,
            )
            return current_links, current_header

        if time.monotonic() >= deadline:
            logger.debug(
                "Results panel did not change within %.2fs of loading %s", settle_timeout, url
            )
            return current_links, current_header
        polls += 1
        await asyncio.sleep(poll_interval)


async def get_terms_urls(
    session: Session,
    *,
    query: str | None = None,
    topic: str | None = None,
    start_letter: str | None = None,
    limit: int | None = None,
    exclude: Collection[str] | None = None,
    auto_initialize: bool = True,
) -> typing.AsyncIterator[str]:
    """
    Yield term detail page URLs matching the given filters.

    Pages through the glossary site's results one tab at a time, only
    loading the next tab once the caller asks for another URL.

    :param session: An open glossary session.
    :param query: A free-text search query.
    :param topic: Restrict results to this topic, or several
        comma-separated topics, e.g. `"Well completions,Perforating"`. Need
        not be an exact match; the closest topic(s) in `session.topics` are
        used. See `slb_glossary.utils.get_topic_match`.
    :param start_letter: Restrict results to terms starting with this letter.
    :param limit: Maximum number of URLs to yield. Yields every matching
        URL if `None`. An excluded URL (see `exclude`) doesn't count
        against this: `limit` is a count of what's actually yielded.
    :param exclude: URLs and/or term names to skip over instead of
        yielding, e.g. ones already stored locally, so a sync doesn't pay
        to re-fetch them. An entry is treated as a URL if it starts with
        `"http://"`/`"https://"`, and as a term name otherwise - see
        `slb_glossary.utils.split_exclude`. Note that a term name in
        `exclude` has no effect *here*: this only ever sees each term's
        URL, not its name (that's only known once the detail page itself
        is fetched), so a term-name exclusion only takes effect once
        this URL stream is fed into `get_results_from_url`/
        `get_results_from_urls`, which do know each page's term name.
        Membership is checked once per URL seen, so pass a `set`/`frozenset`
        for that to stay cheap; some other `AbstractSet` works too, just
        possibly slower depending on what it is. `None` (the default)
        excludes nothing.
    :yield: Term detail page URLs, in the order the glossary site returns
        them, `exclude`d ones skipped.
    :param auto_initialize: If `session` isn't initialized yet, initialize
        it automatically (the default) or raise. See `ensure_initialized`.
    :raises ValueError: If `limit` is given and is less than 1.
    :raises SessionNotInitializedError: If `session` isn't initialized and
        `auto_initialize` is `False`.
    """
    await ensure_initialized(session, auto_initialize)
    if limit is not None and limit < 1:
        raise ValueError("`limit` must be greater than 0")
    if not topic and not (query or start_letter):
        return

    started_at = time.monotonic()
    topic_match = get_topic_match(session.topics, topic=topic) if topic else None
    excluded, _ = split_exclude(exclude)
    logger.debug(
        "Iterating term URLs: query=%r topic=%r start_letter=%r limit=%r exclude=%d entr(ies)",
        query,
        topic,
        start_letter,
        limit,
        len(excluded) if excluded else 0,
    )

    yielded = 0
    skipped = 0
    tab = 1
    max_tabs: int | None = None

    base_page_free = (
        session.base_page is not None
        and not session.base_page.is_closed()
        and not session.base_page_in_use
    )
    if base_page_free:
        # Reuse the session's base page when it's free: it's already
        # warmed up (see `Session.base_page`'s docstring for why that
        # matters), and staying warmed up isn't a one-time thing, so
        # there's no reason to throw it away after a single use.
        page = session.base_page
        owns_page = False
        session.base_page_in_use = True
    else:
        # `base_page` is either unavailable (closed, or this session was
        # never initialized with one) or already checked out by a
        # concurrent `get_terms_urls` call - either way, get a dedicated
        # page of our own and pay the one-time warm-up cost ourselves
        # rather than racing another call over `base_page`'s navigation.
        page = await session.new_page()
        owns_page = True
        await fetch_topics(page, base_url=session.base_url)
    try:
        assert page is not None
        # The glossary auto-runs an unfiltered query as soon as the search
        # screen loads (that's what populates the facet panel), so the page
        # always has *some* results-panel state to diff a filtered search
        # against, so we read it now rather than starting from an empty baseline.
        # An empty baseline previously meant "nothing to wait for", so the
        # very first search of every session read that pre-filter panel
        # before the site's JS had applied the query. Which will look exactly
        # like every search returning the same (default) results.
        previous_links = await get_result_links(page)
        previous_header = await get_results_header_text(page)

        while True:
            pager_query = build_pager_query(tab_number=tab, terms_per_tab=session.terms_per_tab)
            url = build_search_url(
                base_url=session.base_url,
                topic=topic_match,
                query=query,
                start_letter=start_letter,
                pager_query=pager_query,
            )
            links, header_text = await _wait_for_settle(
                session,
                url=url,
                page=page,
                previous_links=previous_links,
                previous_header=previous_header,
            )

            if not links:
                logger.debug("No result links on tab %d, stopping", tab)
                return

            if not header_text:
                logger.debug("No results header on tab %d, stopping", tab)
                return

            total_terms = await get_total_term_count(page)
            if total_terms is None:
                logger.debug("Could not read a total term count on tab %d, stopping", tab)
                return

            if max_tabs is None:
                max_tabs = math.ceil(total_terms / session.terms_per_tab)
                logger.debug("Search matched %d terms across %d tabs", total_terms, max_tabs)

            tab_started_at = time.monotonic()
            skipped_this_tab = 0
            for href in links:
                if excluded and href in excluded:
                    skipped += 1
                    skipped_this_tab += 1
                    continue

                yield href
                yielded += 1
                if limit is not None and yielded >= limit:
                    return

            logger.debug(
                "Yielded %d url(s) (skipped %d excluded) from tab %d/%d in %.3fs",
                len(links) - skipped_this_tab,
                skipped_this_tab,
                tab,
                max_tabs,
                time.monotonic() - tab_started_at,
            )

            previous_links = links
            previous_header = header_text
            if tab >= max_tabs:
                return
            tab += 1
    finally:
        if owns_page and page is not None:
            await page.close()
        else:
            session.base_page_in_use = False
        elapsed = time.monotonic() - started_at
        logger.debug(
            "`get_terms_urls` done: %d url(s) yielded, %d skipped (excluded), "
            "across %d tab(s) in %.3fs (avg %.3fs/url)",
            yielded,
            skipped,
            tab,
            elapsed,
            elapsed / yielded if yielded else 0.0,
        )


async def get_results_from_url(
    session: Session,
    url: str,
    *,
    topic: str | None = None,
    page: Page | None = None,
    exclude: Collection[str] | None = None,
    auto_initialize: bool = True,
) -> typing.AsyncIterator[SearchResult]:
    """
    Load a term detail page and lazily yield each definition found on it.

    A term can carry several definitions, one per topic it appears under,
    and a single definition can itself be filed under several topics at
    once (shown on the page as a bracketed, comma-separated list, e.g.
    `"1. n. [Drilling, Shale Gas]"`). This yields one `SearchResult` per
    definition *per topic it's filed under*, so a definition listed under
    two topics yields two otherwise-identical results, one per topic
    consistent with topic being part of a locally stored term's identity.

    :param session: An open glossary session.
    :param url: A term detail page URL, as yielded by `get_terms_urls`.
    :param topic: If a definition's source topic matches this topic
        (or one of several comma-separated topics), that resolved topic
        name is used for its `SearchResult.topic` instead of the raw
        topic text parsed off the page (canonicalizing minor
        formatting differences between the two). Every other topic
        listed alongside it, and every other definition on the page,
        is still yielded regardless - this only affects how one
        matching topic entry is labeled, not which definitions/topics
        are yielded at all.
    :param page: A page to navigate to `url` on. When given, it's assumed
        to be owned by the caller (e.g. a worker page reused across
        several calls) and is left open when this generator finishes. When
        omitted, a page is checked out from `session` for this call alone
        and closed before returning.
    :param exclude: URLs and/or term names to skip. If `url` itself
        matches an excluded URL, this returns immediately without
        navigating anywhere at all, e.g. a term already stored locally
        that a sync doesn't need to re-fetch. A term-name exclusion can
        only be checked once the page's term name is actually known,
        so it's checked right after that, before any `SearchResult` is
        yielded. The page load itself still happens in that case, since
        there's no way to know the term name without fetching it. See
        `slb_glossary.utils.split_exclude` for how an entry is told apart
        as a URL vs. a term name. Pass a `set`/`frozenset` to keep the URL
        check cheap. `None` (the default) excludes nothing.
    :yield: One `SearchResult` per definition-topic pairing found on the
        page. Every result from the same definition (one appearing under
        several topics) shares the same `term`/`definition`/
        `grammatical_label`/`image`/`image_caption`/`related`, differing
        only in `topic`. Each definition's `image`/`image_caption` reflect
        *that definition's own* section, independently of any other
        section on the page, and is `None` only when that particular
        section has no illustrative image, even if a sibling section
        does. `related` is empty when that section has no related-term links.
    :param auto_initialize: If `session` isn't initialized yet, initialize
        it automatically (the default) or raise. See `ensure_initialized`.
    :raises SessionNotInitializedError: If `session` isn't initialized and
        `auto_initialize` is `False`.
    :raises ParsingError: If the page loaded but its structure didn't
        match what this parser expects, e.g. no term name heading, or no
        definition sections. This almost always means the glossary's
        markup changed rather than this particular term genuinely having
        nothing to show.
    """
    excluded_urls, excluded_names = split_exclude(exclude)
    if excluded_urls and url in excluded_urls:
        logger.debug("Skipping excluded url %r", url)
        return

    await ensure_initialized(session, auto_initialize)
    resolved_topic = get_topic_match(session.topics, topic) if topic else None

    started_at = time.monotonic()
    owns_page = page is None
    current_page = await goto(session, url, page=page)
    try:
        term_name = await get_term_name(current_page)
        detail_sections = await get_term_detail_blocks(current_page)

        if excluded_names and " ".join(term_name.strip().lower().split()) in excluded_names:
            logger.debug("Skipping excluded term %r at %s", term_name, url)
            return

        # One illustrative image per definition section. A term with
        # several definitions can have a different image (or none)
        # per section. Indices line up with `detail_sections` since
        # both come from the same repeated DOM wrapper.
        section_images = await get_term_images(current_page)

        yielded = 0
        for index, section_blocks in enumerate(detail_sections):
            if len(section_blocks) < 2:
                continue

            summary_line = section_blocks[0].text
            definition = (
                section_blocks[2].text
                if len(section_blocks) > 2 and section_blocks[1].text == ""
                else section_blocks[1].text
            )
            related = _find_related_links(section_blocks) or None

            summary_words = summary_line.split()
            label_abbreviation = summary_words[1] if len(summary_words) > 1 else ""
            grammatical_label = resolve_grammatical_label(session.language, label_abbreviation)

            bracket_text = summary_line.split(".")[-1].strip().removeprefix("[").removesuffix("]")
            raw_topics = [name.strip() for name in bracket_text.split(",") if name.strip()]
            section_topics: list[str | None] = [
                resolved_topic
                if resolved_topic and resolved_topic.lower() in name.lower()
                else name
                for name in raw_topics
            ] or [resolved_topic if resolved_topic else None]
            # A section can list the same topic more than once in principle,
            # so we keep first-seen order rather than an unordered `set`.
            section_topics = list(dict.fromkeys(section_topics))

            section_image = section_images[index] if index < len(section_images) else None
            image_url, image_caption = (
                (section_image.url, section_image.caption)
                if section_image is not None
                else (None, None)
            )

            for section_topic in section_topics:
                yielded += 1
                yield SearchResult(
                    term=term_name,
                    definition=definition,
                    grammatical_label=grammatical_label,
                    topic=section_topic,
                    url=url,
                    image=image_url,
                    image_caption=image_caption,
                    related=related,
                    language=session.language.value,
                )

        logger.debug(
            "Fetched %r: %d definition(s) from %s in %.3fs",
            term_name,
            yielded,
            url,
            time.monotonic() - started_at,
        )
    finally:
        if owns_page:
            await current_page.close()


async def get_results_from_urls(
    session: Session,
    urls: typing.Iterable[str] | typing.AsyncIterable[str],
    *,
    topic: str | None = None,
    concurrency: int = 1,
    first_only: bool = False,
    exclude: Collection[str] | None = None,
    auto_initialize: bool = True,
) -> typing.AsyncIterator[SearchResult]:
    """
    Fetch term detail pages for `urls` and yield their definitions.

    With `concurrency` > 1, `concurrency` worker pages are opened on
    `session` (via `session.new_page()`, so they share cookies/auth/stealth
    patches with the rest of the session) so several term pages can be
    fetched in parallel. Each worker reuses its own page across every URL
    it handles rather than opening a fresh one per URL. Results are still
    yielded one at a time as they become available, not collected into
    batches, though not necessarily in the same order as `urls` when
    running concurrently.

    :param session: An open glossary session. `session.max_pages` must be
        large enough to cover `concurrency` worker pages, plus one more if
        `urls` is a still-paging `get_terms_urls` generator holding its
        own page open at the same time.
    :param urls: Term detail page URLs to fetch, e.g. from `get_terms_urls`.
        May be a plain iterable or an async iterable (so a still-paging
        `get_terms_urls` generator can be passed straight through).
    :param topic: Passed through to `get_results_from_url` for each URL.
    :param concurrency: Number of term detail pages to fetch in parallel.
        `1` (the default) fetches sequentially on a single page.
    :param first_only: If `True`, yield only the first definition found on
        each page rather than every definition on it.
    :param exclude: URLs and/or term names to skip. A URL match is
        checked once per URL in `urls`, before it's ever queued for a
        worker, so pass a `set`/`frozenset` to keep that cheap. A
        term-name match can only be checked once each page's term name
        is known, so it's applied inside `get_results_from_url` itself
        for each URL that does get fetched. See that function's own
        `exclude` parameter, and `slb_glossary.utils.split_exclude` for
        how an entry is told apart as a URL vs. a term name. `None` (the
        default) excludes nothing.
    :yield: `SearchResult`s as they're fetched, `exclude`d URLs/terms skipped.
    :param auto_initialize: If `session` isn't initialized yet, initialize
        it automatically (the default) or raise. See `ensure_initialized`.
    :raises ValueError: If `concurrency` is less than 1.
    :raises SessionNotInitializedError: If `session` isn't initialized and
        `auto_initialize` is `False`.
    :raises ParsingError: With `concurrency=1`, if a page's structure
        didn't match what the parser expects (see
        `get_results_from_url`). With `concurrency` > 1, a single URL's
        `ParsingError`/`NetworkError` is logged and skipped instead
        (this function's fetch is best-effort per URL there), but any
        other, unexpected exception still propagates rather than being
        swallowed.
    """
    await ensure_initialized(session, auto_initialize)
    if concurrency < 1:
        raise ValueError("`concurrency` must be at least 1")

    excluded_urls, _ = split_exclude(exclude)
    started_at = time.monotonic()
    yielded = 0
    skipped = 0

    async def filtered_urls() -> typing.AsyncIterator[str]:
        nonlocal skipped
        async for url in as_async_iterator(urls):
            if excluded_urls and url in excluded_urls:
                skipped += 1
                continue
            yield url

    url_iter = filtered_urls()

    if concurrency == 1:
        page = await session.new_page()
        try:
            async for url in url_iter:
                async for result in get_results_from_url(
                    session,
                    url,
                    topic=topic,
                    page=page,
                    exclude=exclude,
                    auto_initialize=auto_initialize,
                ):
                    yielded += 1
                    yield result
                    if first_only:
                        break
        finally:
            await page.close()
            elapsed = time.monotonic() - started_at
            logger.debug(
                "`get_results_from_urls` (sequential) done: %d result(s), %d skipped "
                "(excluded), in %.3fs (avg %.3fs/result)",
                yielded,
                skipped,
                elapsed,
                elapsed / yielded if yielded else 0.0,
            )
        return

    # Every worker gets its own page on the session's context, reused
    # across every URL it handles, so workers never race over a shared page.
    worker_pages = [await session.new_page() for _ in range(concurrency)]
    logger.debug("Fetching with %d concurrent worker(s)", len(worker_pages))

    url_queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=concurrency * 2)
    result_queue: asyncio.Queue[SearchResult | BaseException | None] = asyncio.Queue()

    async def produce() -> None:
        async for url in url_iter:
            await url_queue.put(url)
        for _ in worker_pages:
            await url_queue.put(None)  # one stop signal per worker

    async def consume(worker_page: Page) -> None:
        while True:
            url = await url_queue.get()
            if url is None:
                break

            try:
                async for result in get_results_from_url(
                    session,
                    url,
                    topic=topic,
                    page=worker_page,
                    exclude=exclude,
                    auto_initialize=auto_initialize,
                ):
                    await result_queue.put(result)
                    if first_only:
                        break
            except (NetworkError, ParsingError) as exc:
                # Expected, page-specific failure modes for a best-effort
                # bulk fetch. So we log with enough context to diagnose an
                # upstream change, but not a full page dump, and move on
                # to the next URL rather than aborting the whole batch
                # over one bad page.
                logger.warning("Failed to fetch %s: %s", url, exc)
            except Exception as exc:
                # Anything else is unexpected. Route it to the main
                # generator loop below instead of letting it vanish into
                # this worker task, which `asyncio.gather(...,
                # return_exceptions=True)` in the `finally` block would
                # otherwise discard unseen.
                logger.exception("Unexpected error fetching %s", url)
                await result_queue.put(exc)
                break
        await result_queue.put(None)  # this worker is done

    producer_task = asyncio.create_task(produce())
    worker_tasks = [asyncio.create_task(consume(worker_page)) for worker_page in worker_pages]

    try:
        total_worker_tasks = len(worker_tasks)
        finished_workers = 0
        while finished_workers < total_worker_tasks:
            item = await result_queue.get()
            if item is None:
                finished_workers += 1
                continue
            if isinstance(item, BaseException):
                raise item
            yielded += 1
            yield item
    finally:
        producer_task.cancel()
        for task in worker_tasks:
            task.cancel()

        await asyncio.gather(producer_task, *worker_tasks, return_exceptions=True)
        for worker_page in worker_pages:
            await worker_page.close()

        elapsed = time.monotonic() - started_at
        logger.debug(
            "`get_results_from_urls` (concurrency=%d) done: %d result(s), %d skipped "
            "(excluded), in %.3fs (avg %.3fs/result)",
            concurrency,
            yielded,
            skipped,
            elapsed,
            elapsed / yielded if yielded else 0.0,
        )


async def search(
    session: Session,
    query: str,
    *,
    topic: str | None = None,
    start_letter: str | None = None,
    limit: int | None = 3,
    concurrency: int = 1,
    first_only: bool = False,
    exclude: Collection[str] | None = None,
    auto_initialize: bool = True,
) -> typing.AsyncIterator[SearchResult]:
    """
    Search the glossary for `query` and yield matching definitions.

    A matched term can carry several definitions (one per topic), so more
    than `limit` results may be yielded; `limit` bounds the number of terms
    looked up, not the number of definitions returned.

    :param session: An open glossary session.
    :param query: The search query.
    :param topic: Restrict results to this topic, or several
        comma-separated topics. See `get_terms_urls` for matching rules.
    :param start_letter: Restrict results to terms starting with this letter.
    :param limit: Maximum number of terms to look up. Looks up every
        matching term if `None`. Defaults to `3`.
    :param concurrency: Number of term detail pages to fetch in parallel.
        See `get_results_from_urls`. Defaults to `1` (sequential).
    :param first_only: If `True`, yield only the first definition found on
        each page rather than every definition on it.
    :param exclude: Term URLs and/or term names to skip over, e.g. ones
        already stored locally. See `get_terms_urls`/`get_results_from_urls`.
        `None` (the default) excludes nothing.
    :param auto_initialize: If `session` isn't initialized yet, initialize
        it automatically (the default) or raise. See `ensure_initialized`.
    :yield: `SearchResult`s for the matched terms. In sequential order
        (`concurrency=1`) these are most-relevant-first; with higher
        concurrency, results may arrive out of relevance order.
    :raises SessionNotInitializedError: If `session` isn't initialized and
        `auto_initialize` is `False`.
    """
    await ensure_initialized(session, auto_initialize)
    logger.info("Searching glossary for %r (limit=%r, concurrency=%r)", query, limit, concurrency)
    started_at = time.monotonic()
    urls = get_terms_urls(
        session,
        query=query,
        topic=topic,
        start_letter=start_letter,
        limit=limit,
        exclude=exclude,
        auto_initialize=auto_initialize,
    )
    count = 0
    async for result in log_timed_yields(
        get_results_from_urls(
            session,
            urls,
            topic=topic,
            concurrency=concurrency,
            first_only=first_only,
            exclude=exclude,
            auto_initialize=auto_initialize,
        ),
        logger=logger,
        label=f"search({query!r})",
    ):
        count += 1
        yield result

    elapsed = time.monotonic() - started_at
    logger.info(
        "Search for %r yielded %d result(s) in %.3fs (avg %.3fs/result)",
        query,
        count,
        elapsed,
        elapsed / count if count else 0.0,
    )


async def get_terms_on(
    session: Session,
    topic: str,
    *,
    start_letter: str | None = None,
    limit: int | None = None,
    concurrency: int = 1,
    first_only: bool = False,
    exclude: Collection[str] | None = None,
    auto_initialize: bool = True,
) -> typing.AsyncIterator[SearchResult]:
    """
    Yield the definition of every term filed under `topic`.

    :param session: An open glossary session.
    :param topic: The topic to look up terms for. Need not be an exact
        match; see `get_terms_urls` for matching rules.
    :param start_letter: Restrict results to terms starting with this letter.
    :param limit: Maximum number of terms to yield. Yields every term filed
        under `topic` if `None`.
    :param concurrency: Number of term detail pages to fetch in parallel.
        See `get_results_from_urls`. Defaults to `1` (sequential).
    :param first_only: If `True`, yield only the first definition found on
        each page rather than every definition on it.
    :param exclude: Term URLs and/or term names to skip over, e.g. ones
        already stored locally. See `get_terms_urls`/`get_results_from_urls`.
        `None` (the default) excludes nothing.
    :param auto_initialize: If `session` isn't initialized yet, initialize
        it automatically (the default) or raise. See `ensure_initialized`.
    :yield: One `SearchResult` per term filed under `topic`.
    :raises SessionNotInitializedError: If `session` isn't initialized and
        `auto_initialize` is `False`.
    """
    await ensure_initialized(session, auto_initialize)
    logger.info(
        "Fetching terms under topic %r (start_letter=%r, limit=%r, concurrency=%r)",
        topic,
        start_letter,
        limit,
        concurrency,
    )
    started_at = time.monotonic()
    urls = get_terms_urls(
        session,
        topic=topic,
        start_letter=start_letter,
        limit=limit,
        exclude=exclude,
        auto_initialize=auto_initialize,
    )
    count = 0
    async for result in log_timed_yields(
        get_results_from_urls(
            session,
            urls,
            topic=topic,
            concurrency=concurrency,
            first_only=first_only,
            exclude=exclude,
            auto_initialize=auto_initialize,
        ),
        logger=logger,
        label=f"get_terms_on({topic!r})",
    ):
        count += 1
        yield result

    elapsed = time.monotonic() - started_at
    logger.info(
        "Fetched %d term(s) under topic %r in %.3fs (avg %.3fs/term)",
        count,
        topic,
        elapsed,
        elapsed / count if count else 0.0,
    )
