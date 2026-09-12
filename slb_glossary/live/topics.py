"""API for fetching and matching glossary topics (disciplines)."""

import asyncio
import logging
import time

from patchright.async_api import Locator, Page
from patchright.async_api import TimeoutError as PWTimeoutError

from slb_glossary.live.browser import Session
from slb_glossary.live.parsers import (
    FACET_HEADER_SELECTOR,
    FACET_MORE_SELECTOR,
    get_element_text,
    get_facet_topics,
    get_glossary_size,
)
from slb_glossary.retries import DEFAULT_RETRY_POLICY, RetryPolicy
from slb_glossary.retries import retry as retry_func
from slb_glossary.utils import safe_close

logger = logging.getLogger(__name__)


__all__ = ["fetch_topics", "refresh_topics"]


COOKIE_SDK_BUTTON_SELECTOR = "#onetrust-banner-sdk #onetrust-button-group .banner-actions-container #onetrust-reject-all-handler"


async def resolve_cookie_modal(page: Page, *, settle_delay: float | None = None) -> None:
    """
    Attends to the cookie consent modal, either accepting or rejecting it.

    :param page: The page containing the cookie consent modal. Usually the base url page.
    :param settle_delay: Milliseconds to wait after the consent modal first renders
        to ensure that its buttons are clickable.
    """
    if settle_delay:
        await asyncio.sleep(settle_delay / 1000)
    started_at = time.monotonic()
    cookie_button = page.locator(COOKIE_SDK_BUTTON_SELECTOR).first
    if await cookie_button.count():
        await cookie_button.scroll_into_view_if_needed(timeout=settle_delay)
        await cookie_button.click(
            timeout=settle_delay, delay=settle_delay * 0.1 if settle_delay else None
        )
        logger.debug("Resolve cookied consent modal in %.3fs", time.monotonic() - started_at)
        return
    logger.debug("Found no cookie consent modal in %.3fs", time.monotonic() - started_at)
    return None


async def is_facet_expanded(more_button: Locator, *, timeout: float | None = None) -> bool:
    return await more_button.evaluate(
        """
        (element) => {
            collapseButton = element.parentElement.querySelector('.coveo-facet-less');
            if (collapseButton == null || collapseButton.disabled){
                return false;
            };
            return collapseButton.classList.contains('coveo-active');
        };
        """,
        timeout=timeout,
    )


async def fetch_topics(
    page: Page,
    *,
    base_url: str,
    settle_delay: float = 3000,
    retry: RetryPolicy = DEFAULT_RETRY_POLICY,
) -> tuple[dict[str, int], int]:
    """
    Load `base_url` and read the glossary's topic list and total term count.

    Also, resolves the cookie consent modal that pops up when the base url is loaded
    for the first time (which is everytime in our case).

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

    # Sorta like a element readiness for interaction delay. Unlike the element load/settle delay
    readiness_delay = settle_delay / 2 if settle_delay >= 2000 else settle_delay

    async def get_facet_header() -> str:
        await page.goto(base_url, wait_until="domcontentloaded")
        await resolve_cookie_modal(page, settle_delay=readiness_delay)
        return await get_element_text(page, FACET_HEADER_SELECTOR, timeout=settle_delay)

    header_text = await retry_func(get_facet_header, policy=retry, until=bool)
    if not header_text:
        logger.warning(
            "Topics did not load after %d attempts (%.3fs)",
            retry.attempts,
            time.monotonic() - started_at,
        )
        return {}, 0

    more_button = page.locator(FACET_MORE_SELECTOR).first
    if await more_button.count():
        try:
            expand_started_at = time.monotonic()
            await more_button.scroll_into_view_if_needed(timeout=readiness_delay)
            await more_button.click(timeout=readiness_delay, delay=readiness_delay * 0.1)
        except PWTimeoutError:
            logger.warning("Could not expand the full topic list", exc_info=True)
        else:
            logger.debug(
                "Waiting for topics list to expand for %.3fs maximum", settle_delay / 1000
            )
            delay = min(300, settle_delay) / 1000
            waited = 0
            while waited < settle_delay:
                if await is_facet_expanded(more_button, timeout=readiness_delay):
                    break
                await asyncio.sleep(delay)
                waited += delay
            logger.debug("Expanded full topic list in %.3fs", time.monotonic() - expand_started_at)

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
            await safe_close(page.close(), "page")
        else:
            session.base_page_in_use = False

    session.topics = topics
    session.size = size
    logger.debug("Refreshed topics in %.3fs", time.monotonic() - started_at)
    return session
