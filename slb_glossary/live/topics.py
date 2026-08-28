"""API for fetching and matching glossary topics (disciplines)."""

import asyncio
import logging
import time

from patchright.async_api import Page
from patchright.async_api import TimeoutError as PWTimeoutError

from slb_glossary.live.browser import Session
from slb_glossary.live.parsers import (
    FACET_EXPAND_SELECTOR,
    FACET_HEADER_SELECTOR,
    get_element_text,
    get_facet_topics,
    get_glossary_size,
)
from slb_glossary.retries import DEFAULT_RETRY_POLICY, RetryPolicy
from slb_glossary.retries import retry as retry_func

logger = logging.getLogger(__name__)


__all__ = ["fetch_topics", "refresh_topics"]


async def fetch_topics(
    page: Page,
    *,
    base_url: str,
    settle_delay: float = 8000,
    retry: RetryPolicy = DEFAULT_RETRY_POLICY,
) -> tuple[dict[str, int], int]:
    """
    Load `base_url` and read the glossary's topic list and total term count.

    :param page: The page to load the glossary search screen on.
    :param base_url: Base search URL for the target glossary language, as
        returned by `slb_glossary.urls.get_glossary_base_url`.
    :param settle_delay: Milliseconds to wait after the facet panel first renders
        and after expanding it, giving the site's search widget time to
        finish populating both.
    :param retry: Policy for retrying the page load if the facet panel
        renders empty.
    :return: A `(topics, size)` pair: a mapping of topic name to term count,
        and the total number of terms in the glossary.
    """
    started_at = time.monotonic()
    logger.info("Loading glossary topics from %s", base_url)

    async def get_facet_header() -> str:
        await page.goto(base_url, wait_until="domcontentloaded")
        return await get_element_text(page, FACET_HEADER_SELECTOR, timeout=settle_delay)

    header_text = await retry_func(get_facet_header, policy=retry, until=bool)
    if not header_text:
        logger.warning(
            "Topics did not load after %d attempts (%.3fs)",
            retry.attempts,
            time.monotonic() - started_at,
        )
        return {}, 0

    # await asyncio.sleep(settle_delay / 1000)

    expand_button = page.locator(FACET_EXPAND_SELECTOR).first
    if await expand_button.count():
        try:
            expand_started_at = time.monotonic()
            await expand_button.scroll_into_view_if_needed(timeout=2000)
            await expand_button.click(timeout=2000, delay=300)
            logger.debug("Expanded full topic list in %.3fs", time.monotonic() - expand_started_at)
        except PWTimeoutError:
            logger.warning("Could not expand the full topic list", exc_info=True)
        else:
            await asyncio.sleep(settle_delay / 1000)

    topics = await get_facet_topics(page)
    size = await get_glossary_size(page)
    logger.info(
        "Loaded %d topics; glossary has %d terms (%.3fs)",
        len(topics),
        size,
        time.monotonic() - started_at,
    )
    return topics, size


async def refresh_topics(session: Session) -> Session:
    """
    Reload `session.topics` and `session.size` from the glossary site.

    :param session: The session to refresh.
    :return: `session`, with `topics` and `size` updated in place.
    """
    started_at = time.monotonic()
    logger.debug("Refreshing topics for session on %s", session.base_url)

    base_page_free = (
        session.base_page is not None
        and not session.base_page.is_closed()
        and not session.base_page_in_use
    )
    if base_page_free:
        # Reuse the session's base page when it's free as it's already
        # warmed up (see `Session.base_page`'s docstring for why that
        # matters), and this navigates it to the same search screen
        # `fetch_topics` always loads anyway, so nothing about a fresh
        # page would help here.
        page = session.base_page
        owns_page = False
        session.base_page_in_use = True
    else:
        # `base_page` is either unavailable (closed, or this session was
        # never initialized with one) or already checked out by another
        # call. Either way, get a dedicated page rather than racing that
        # call over `base_page`'s navigation.
        page = await session.new_page()
        owns_page = True

    try:
        assert page is not None
        topics, size = await fetch_topics(
            page,
            base_url=session.base_url,
            retry=session.retry,
            settle_delay=session.settle_timeout,
        )
    finally:
        if owns_page and page is not None:
            await page.close()
        else:
            session.base_page_in_use = False

    session.topics = topics
    session.size = size
    logger.debug("Refreshed topics in %.3fs", time.monotonic() - started_at)
    return session
