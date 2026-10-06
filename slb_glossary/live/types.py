import asyncio
import dataclasses
import enum
import logging
import time
import typing

from patchright.async_api import Browser, BrowserContext, Page, Playwright

from slb_glossary.constants import constants
from slb_glossary.errors import BrowserError, NetworkError, PagePoolTimeoutError
from slb_glossary.retries import RetryPolicy
from slb_glossary.types import Language
from slb_glossary.utils import safe_close, shielded_close

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
        if not self.page.is_closed() and await shielded_close(self.page.close(), "page"):
            logger.debug(f"Closed page via {type(self).__name__} context manager")


class _PoolDefault(enum.Enum):
    """Marks "use the pool's own `acquire_timeout`" for `Pages.get`'s `timeout`."""

    TOKEN = enum.auto()


POOL_DEFAULT = _PoolDefault.TOKEN


def _default_acquire_timeout() -> float | None:
    return constants.page_acquire_timeout or None


@dataclasses.dataclass(slots=True, kw_only=True)
class Pages:
    """
    A bounded pool of browser pages opened on a single `BrowserContext`.

    Callers that each want an independently-owned page should acquire
    one with `get()` instead of sharing (and racing over) a single page.
    `max_size` caps how many pages can be open on `context` at once;
    once that many are checked out, further `get()` calls wait for one to close,
    for at most `acquire_timeout`.

    Accounting is driven by the page's own `close` event rather than by
    callers remembering to release the page, so a page closed any way
    (via `PageHandle`, a direct `page.close()`, or the context tearing it
    down) always frees its slot.

    A task should never wait for a page while it is holding another one it isn't using
    yet. Several tasks doing that can each end up holding part of what they need, with
    nothing left for anyone to finish (a deadlock). `acquire_timeout` turns that into a
    `PagePoolTimeoutError` instead of a hang.
    """

    context: BrowserContext
    max_size: int
    acquire_timeout: float | None = dataclasses.field(default_factory=_default_acquire_timeout)
    """
    Milliseconds `get()` waits for a free page before raising `PagePoolTimeoutError`.
    `None` (or `0`) waits forever. Defaults to `constants.page_acquire_timeout`.
    """
    _pages: set[Page] = dataclasses.field(init=False, repr=False, default_factory=set)
    _semaphore: asyncio.Semaphore = dataclasses.field(init=False, repr=False)
    _closed: bool = dataclasses.field(init=False, repr=False, default=False)

    def __post_init__(self) -> None:
        if self.max_size < 1:
            raise ValueError("`max_size` must be at least 1")
        if self.acquire_timeout is not None and self.acquire_timeout <= 0:
            self.acquire_timeout = None

        self._semaphore = asyncio.Semaphore(self.max_size)
        logger.debug(
            "Opened page pool (max_size=%d, acquire_timeout=%s)",
            self.max_size,
            self.acquire_timeout,
        )

    @property
    def size(self) -> int:
        """Number of pages currently checked out of this pool."""
        return len(self._pages)

    @property
    def free(self) -> int:
        """How many more pages could be opened right now without waiting."""
        return max(self.max_size - self.size, 0)

    def describe(self) -> str:
        """What each checked-out page is showing, for diagnosing a stuck pool."""
        urls = [page.url for page in self._pages]
        blank = sum(1 for url in urls if url in ("", "about:blank"))
        text = f"{self.size}/{self.max_size} pages in use"
        if urls:
            text += ": " + ", ".join(url[:60] or "about:blank" for url in urls)
        if blank:
            text += (
                f". {blank} still on about:blank, which means they were opened but never "
                "used (something is holding pages while waiting for another page)"
            )
        return text

    async def _acquire_slot(self, timeout: float | None) -> None:
        """
        Take a slot from the semaphore, waiting at most `timeout` milliseconds.

        Done explicitly instead of with `asyncio.wait_for`, which on older Pythons can
        lose a slot that was granted at the instant it timed out or was cancelled.
        """
        if timeout is None:
            await self._semaphore.acquire()
            return

        acquire = asyncio.ensure_future(self._semaphore.acquire())
        try:
            done, _ = await asyncio.wait({acquire}, timeout=timeout / 1000)
        except BaseException:
            await self._abandon(acquire)
            raise
        if acquire in done:
            return
        await self._abandon(acquire)
        raise PagePoolTimeoutError(
            f"Timed out after {timeout / 1000:g}s waiting for a free page ({self.describe()}). "
            "This usually means more pages are wanted at once than `max_pages` allows "
            "(lower `concurrency` or raise `max_pages`), or a page was opened and never "
            "closed. Raise or disable `page_acquire_timeout` if the work is simply slow."
        )

    async def _abandon(self, acquire: asyncio.Future[typing.Any]) -> None:
        """Stop waiting on `acquire`, handing the slot back if it was granted meanwhile."""
        acquire.cancel()
        try:
            await acquire
        except asyncio.CancelledError:
            return
        self._semaphore.release()

    async def get(self, *, timeout: float | _PoolDefault | None = POOL_DEFAULT) -> PageHandle:
        """
        Acquire a new page on `context`.

        Waits if `max_size` pages are already open, until one of them closes.

        :param timeout: Milliseconds to wait for a free page. `None` waits forever.
            Defaults to this pool's `acquire_timeout`.
        :returns: A `PageHandle` wrapping the new page.
        :raises BrowserError: If this pool has already been closed.
        :raises PagePoolTimeoutError: If no page freed up within the timeout.
        """
        if self._closed:
            raise BrowserError(
                f"Cannot open a new page: this `{type(self).__name__}` pool is closed"
            )

        wait = self.acquire_timeout if isinstance(timeout, _PoolDefault) else timeout
        if wait is not None and wait <= 0:
            wait = None

        started_at = time.monotonic()
        if self._semaphore.locked():
            logger.debug(
                "Page pool is at capacity (%d/%d); waiting for a slot to free up",
                self.size,
                self.max_size,
            )

        await self._acquire_slot(wait)
        try:
            page = await self.context.new_page()
        except BaseException:
            # Includes cancellation: without this the slot would be lost for good.
            self._semaphore.release()
            raise

        released = False

        def discard_and_release(*_: typing.Any) -> None:
            nonlocal released
            if released:
                return
            released = True
            self._pages.discard(page)
            self._semaphore.release()
            logger.debug("Closed pool page (pool size now %d/%d)", self.size, self.max_size)

        page.on("close", discard_and_release)
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
        open_pages = [page for page in self._pages if not page.is_closed()]
        results = await asyncio.gather(
            *(shielded_close(page.close(), "pool page") for page in open_pages)
        )
        failed = results.count(False)
        logger.debug(
            "Closed page pool (%d page(s) still open, %d failed to close) in %.3fs",
            len(open_pages),
            failed,
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

    settle_timeout: float = 3000
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

    page_acquire_timeout: float | None = dataclasses.field(
        default_factory=_default_acquire_timeout
    )
    """
    Milliseconds `new_page()` waits for a free page (see `max_pages`) before raising
    `PagePoolTimeoutError`, instead of waiting forever. 
    
    `None` or `0` waits forever. Defaults to `constants.page_acquire_timeout`.
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

    The glossary site blocks requests that do not originate from a page
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
        self.pages = Pages(
            context=self.context,
            max_size=self.max_pages,
            acquire_timeout=self.page_acquire_timeout,
        )

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
            immediately instead, e.g. if you do not expect to need a
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
            logger.exception("An error occurred while initializing session")
            raise NetworkError(f"Could not reach the glossary at {self.base_url}") from exc
        finally:
            if not hold_page:
                await shielded_close(page.close(), "page")

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
        await safe_close(self.pages.close(), "page pool")
        await safe_close(self.context.close(), "browser context")
        logger.info("Closed session for %s in %.3fs", self.base_url, time.monotonic() - started_at)
