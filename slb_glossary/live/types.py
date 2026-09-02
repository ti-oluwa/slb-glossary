import asyncio
import contextlib
import dataclasses
import enum
import logging
import time
import typing

from patchright.async_api import Browser, BrowserContext, Page, Playwright

from slb_glossary.errors import BrowserError, NetworkError
from slb_glossary.retries import RetryPolicy
from slb_glossary.types import Language

logger = logging.getLogger(__name__)


__all__ = [
    "BrowserType",
    "PageHandle",
    "Pages",
    "ResourceType",
    "Session",
]


class BrowserType(str, enum.Enum):  # type: ignore[name-defined]
    """Playwright browser families `open_session` can launch."""

    CHROMIUM = "chromium"
    FIREFOX = "firefox"
    WEBKIT = "webkit"


class ResourceType(enum.IntFlag):
    """Playwright request resource types used for resource blocking."""

    DOCUMENT = enum.auto()
    STYLESHEET = enum.auto()
    IMAGE = enum.auto()
    MEDIA = enum.auto()
    FONT = enum.auto()
    SCRIPT = enum.auto()
    TEXTTRACK = enum.auto()
    XHR = enum.auto()
    FETCH = enum.auto()
    EVENTSOURCE = enum.auto()
    WEBSOCKET = enum.auto()
    MANIFEST = enum.auto()
    OTHER = enum.auto()
    ALL = (
        DOCUMENT
        | STYLESHEET
        | IMAGE
        | MEDIA
        | FONT
        | SCRIPT
        | TEXTTRACK
        | XHR
        | FETCH
        | EVENTSOURCE
        | WEBSOCKET
        | MANIFEST
        | OTHER
    )


@dataclasses.dataclass(slots=True, kw_only=True)
class PageHandle:
    """
    A page acquired from a `Pages` pool.

    Use it directly (`handle.page`) and close it yourself when done, or
    use it as an async context manager to have it closed for you:

    ```python
    async with await pages.get() as page:
        await page.goto(url)
    ```

    Either way, the pool's slot for this page is freed automatically as
    soon as the underlying page closes, whether that happens via this
    handle, a direct `page.close()`, or the browser context closing the
    page out from under you.
    """

    page: Page

    async def __aenter__(self) -> Page:
        return self.page

    async def __aexit__(self, *exc_info: object) -> None:
        if not self.page.is_closed():
            with contextlib.suppress(Exception):
                await self.page.close()
                logger.debug(f"Closed page via {type(self).__name__} context manager")


@dataclasses.dataclass(slots=True, kw_only=True)
class Pages:
    """
    A bounded pool of browser pages opened on a single `BrowserContext`.

    Callers that each want an independently-owned page should acquire
    one with `get()` instead of sharing (and racing over) a single page.
    `max_size` caps how many pages can be open on `context`
    at once; once that many are checked out, further `get()` calls wait
    for one to close.

    Accounting is driven by the page's own `close` event rather than by
    callers remembering to "release" anything, so a page closed any way
    (via `PageHandle`, a direct `page.close()`, or the context tearing it
    down) always frees its slot exactly once.
    """

    context: BrowserContext
    max_size: int
    _pages: set[Page] = dataclasses.field(init=False, repr=False, default_factory=set)
    _semaphore: asyncio.Semaphore = dataclasses.field(init=False, repr=False)
    _closed: bool = dataclasses.field(init=False, repr=False, default=False)

    def __post_init__(self) -> None:
        if self.max_size < 1:
            raise ValueError("`max_size` must be at least 1")
        self._semaphore = asyncio.Semaphore(self.max_size)
        logger.debug("Opened page pool (max_size=%d)", self.max_size)

    @property
    def size(self) -> int:
        """Number of pages currently checked out of this pool."""
        return len(self._pages)

    async def get(self) -> PageHandle:
        """
        Acquire a new page on `context`.

        Blocks if `max_size` pages are already open, until one of them closes.

        :returns: A `PageHandle` wrapping the new page.
        :raises BrowserError: If this pool has already been closed.
        """
        if self._closed:
            raise BrowserError(
                f"Cannot open a new page: this `{type(self).__name__}` pool is closed"
            )

        started_at = time.monotonic()
        if self._semaphore.locked():
            logger.debug(
                "Page pool is at capacity (%d/%d); waiting for a slot to free up",
                self.size,
                self.max_size,
            )
        await self._semaphore.acquire()
        try:
            page = await self.context.new_page()
        except Exception:
            self._semaphore.release()
            raise

        released = False

        def _on_close(*_: typing.Any) -> None:
            nonlocal released
            if released:
                return
            released = True
            self._pages.discard(page)
            self._semaphore.release()
            logger.debug("Closed pool page (pool size now %d/%d)", self.size, self.max_size)

        page.on("close", _on_close)
        self._pages.add(page)
        logger.debug(
            "Opened pool page (pool size now %d/%d) in %.3fs",
            self.size,
            self.max_size,
            time.monotonic() - started_at,
        )
        return PageHandle(page=page)

    async def close(self) -> None:
        """
        Close every page still checked out of this pool.

        Safe to call more than once; later calls are no-ops. Once closed,
        this pool can no longer hand out pages via `get()`.
        """
        if self._closed:
            return
        self._closed = True
        started_at = time.monotonic()
        count = self.size
        for page in list(self._pages):
            if not page.is_closed():
                with contextlib.suppress(Exception):
                    await page.close()
        logger.debug(
            "Closed page pool (%d page(s) still open) in %.3fs",
            count,
            time.monotonic() - started_at,
        )


@dataclasses.dataclass(slots=True, kw_only=True)
class Session:
    """
    An open, ready-to-query browser session against the SLB glossary.

    Obtain one with `slb_glossary.browser.open_session` or `session`,
    then pass it to the search functions in `slb_glossary.live`.

    A session owns one browser context shared by however many pages are
    open at once (bounded by `max_pages`).

    Operations that need their own page check one out via `pages.get()`
    or `new_page()`, so a session is safe to drive concurrently as
    long as `max_pages` covers however many pages those operations need
    open at the same time.
    """

    playwright: Playwright
    """The running Playwright driver instance backing this session."""

    browser: Browser
    """The browser instance launched for this session."""

    context: BrowserContext
    """The browser context (cookies, cache, stealth patches) `pages` opens pages in."""

    language: Language
    """The glossary language this session searches."""

    base_url: str
    """Base search URL for `language`."""

    topics: dict[str, int]
    """Mapping of topic name to number of glossary terms filed under it."""

    size: int
    """Total number of terms in the glossary, as reported by the site."""

    browser_type: BrowserType = BrowserType.CHROMIUM  # type: ignore[assignment]
    """Playwright browser family this session launched."""

    terms_per_tab: int = 12
    """Number of results the glossary site returns per results page."""

    blocked_resources: frozenset[str] = dataclasses.field(default_factory=frozenset)
    """
    Request resource types dropped for this session, as the literal
    strings Playwright's own `Request.resource_type` produces (e.g.
    `"image"`), not `ResourceType` members. 
    
    Resolved once from whatever `open_session`'s `block` argument was 
    (a `bool`, a `ResourceType`, or an iterable of names) so that blocking 
    a request is a single `in` check against this frozenset on every intercepted 
    request, with no per-request enum conversion in that (hot) path.
    """

    retry: RetryPolicy = dataclasses.field(default_factory=RetryPolicy)
    """
    Policy used to retry page loads that render before their JavaScript
    search widget has finished populating.
    """

    timeout: float = 60_000
    """Milliseconds to wait for page/element load or lookups, and navigation before timeout"""

    settle_timeout: float = 8000
    """
    Milliseconds to wait for the results list to update after a search
    filter changes, since the glossary updates its results via JavaScript
    rather than a full page navigation.
    """

    poll_interval: float = 300
    """Milliseconds to wait between polls while waiting on `settle_timeout`."""

    max_pages: int = 6
    """
    Maximum number of browser pages this session will have open on `context` at once. 
    
    Each independent operation (the search/tab-paging page `get_terms_urls` 
    holds open, each concurrent worker page in `get_results_from_urls`) checks 
    out its own page via `new_page()` rather than sharing one, so this should 
    comfortably cover the highest `concurrency` you plan to call search functions 
    with, plus one for a `base_page` or `get_terms_urls` page running alongside it.
    """

    pages: Pages = dataclasses.field(init=False, repr=False)
    """
    The pool `new_page()` acquires pages from, bounded to `max_pages`.
    Built automatically from `context` and `max_pages`.
    """

    base_page: Page | None = None
    """
    The page `initialize()` used to load `topics`/`size`, held onto
    (unless `initialize(hold_page=False)` was used) so later calls can
    reuse an already-warmed-up page instead of opening and warming up
    their own each time.

    The glossary site blocks requests that don't originate from a page
    that's already been interacted with, so a brand-new page has to run a
    throwaway search first before it can be trusted with a real one.
    `base_page` has already paid that cost during `initialize()`, and
    stays warmed-up indefinitely. `slb_glossary.live.get_terms_urls`
    reuses it every time it's free, not just once, falling back to
    opening and warming up a fresh page only when `base_page` is
    unavailable (closed) or already checked out by a concurrent call
    (see `base_page_in_use`).
    """

    base_page_in_use: bool = dataclasses.field(default=False, repr=False)
    """
    Whether `base_page` is currently checked out by a `get_terms_urls`
    call. Sessions aren't meant to run several `get_terms_urls` calls at
    once (they'd each need to navigate `base_page` independently, racing
    over its single tab/state), so a concurrent call finding this `True`
    falls back to a dedicated page of its own for that call, rather than
    navigating `base_page` out from under the call already using it.
    """

    _initialized: bool = dataclasses.field(init=False, repr=False, default=False)

    def __post_init__(self) -> None:
        self.pages = Pages(context=self.context, max_size=self.max_pages)

    @property
    def initialized(self) -> bool:
        """
        Whether `topics`/`size` have been loaded from the glossary site yet.
        """
        return self._initialized

    async def initialize(self, hold_page: bool = True) -> None:
        """
        Load `topics`/`size` from the glossary site, if not already loaded.

        A no-op if `initialized` is already `True`. Safe to call more than
        once, e.g. defensively before a search function that requires it.
        `open_session(..., initialize=True)` (the default) calls this for
        you before returning the session; call it yourself only if you
        opened one with `initialize=False`.

        :param hold_page: If `True` (the default), the page opened to
            load `topics`/`size` is kept open afterward and stored on
            `base_page`, instead of being closed once this call finishes.
            See `base_page`'s own docstring for what that buys later
            callers, and its limits. Pass `False` to close the page
            immediately instead, e.g. if you don't expect to need a
            warmed-up page again soon and would rather not hold one open.
        :raises NetworkError: If the glossary site could not be reached.
        """
        if self.initialized:
            logger.debug("Session for %s is already initialized; no-op", self.base_url)
            return

        logger.info(
            "Initializing session for %s (language=%s, browser_type=%s)",
            self.base_url,
            self.language.value,
            self.browser_type,
        )
        started_at = time.monotonic()
        page = await self.new_page()
        if hold_page:
            # Hold the base page for use later on. call `Session.base_page.close()`
            # If you really do not need it anymore. But you honestly should leave it
            # if you have no reason to close it.
            self.base_page = page

        try:
            from slb_glossary.live.topics import fetch_topics

            topics, size = await fetch_topics(
                page,
                base_url=self.base_url,
                settle_delay=self.settle_timeout,
                retry=self.retry,
            )
            self.topics = topics
            self.size = size
            self._initialized = True
        except Exception as exc:
            raise NetworkError(f"Could not reach the glossary at {self.base_url}") from exc
        finally:
            if not hold_page:
                await page.close()

        logger.info(
            "Initialized session for %s: %d topic(s), %d term(s) total, in %.3fs",
            self.base_url,
            len(self.topics),
            self.size,
            time.monotonic() - started_at,
        )

    async def new_page(self) -> Page:
        """
        Check out a new page from `pages`, this session's page pool.

        The page counts against `max_pages` until it's closed (by you, or
        automatically when the session closes), at which point its slot
        frees up for another `new_page()` call. Prefer `pages.get()`
        directly if you want the `PageHandle` context-manager form instead
        of a bare `Page`.

        :returns: A new Playwright browser page.
        :raises BrowserError: If this session has already been closed.
        """
        handle = await self.pages.get()
        return handle.page

    async def close(self) -> None:
        """
        Close every page this session opened, then its browser context.

        Safe to call more than once; later calls are no-ops. Does not
        close `browser` or stop `playwright`. Use `close_session` (or
        the `session`/`session_from_config` context managers) for full
        teardown of a session opened with `open_session`.
        """
        started_at = time.monotonic()
        with contextlib.suppress(Exception):
            await self.pages.close()
        with contextlib.suppress(Exception):
            await self.context.close()
        logger.info("Closed session for %s in %.3fs", self.base_url, time.monotonic() - started_at)
