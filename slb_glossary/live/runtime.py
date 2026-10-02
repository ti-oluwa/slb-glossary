"""
Session and database lifecycle management for applications.

Opening a live `Session` launches a whole browser, so an application that
serves many lookups (a web service, a bot, an agent tool server, ...) should not open
one per request, nor hand-roll its own sharing, reaping and shutdown logic. This module
provides that once:

* `SessionPool`: an elastic set of `Session`s for one glossary language, with per-session
  checkout counting, idle reaping and dead-browser eviction.
* `Runtime`: owns the shared local `Database` and one `SessionPool` per language, bounded
  by one browser budget (`max_sessions`) across all of them, and hands the right resources
  to each call (`Runtime.acquire`/`Runtime.session`).

```python
from slb_glossary import query
from slb_glossary.live import Runtime, SessionMode

async with Runtime(mode=SessionMode.LAZY, max_sessions=2) as runtime:
    async with runtime.acquire(query.Source.AUTO, language="en") as (db, session):
        async for result in query.search("porosity", db=db, session=session):
            print(result.value.term)
```
"""

import asyncio
import contextlib
import dataclasses
import enum
import logging
import pathlib
import time
import typing
from collections.abc import AsyncIterator, Awaitable, Callable

from slb_glossary.config import Config, DatabaseOptions, SessionOptions
from slb_glossary.errors import (
    ResourceDisabledError,
    RuntimeClosedError,
    SessionPoolClosedError,
    SessionPoolError,
    UnknownLanguageError,
)
from slb_glossary.live.browser import close_session, open_session
from slb_glossary.live.types import Session
from slb_glossary.local.connection import close_db, open_db
from slb_glossary.local.types import Database
from slb_glossary.types import Language, Source

logger = logging.getLogger(__name__)

__all__ = [
    "PoolStats",
    "PooledSession",
    "Runtime",
    "RuntimeStats",
    "SessionMode",
    "SessionPool",
    "get_db_path",
]

RECLAIM_POLL_INTERVAL = 0.5
"""Seconds a pool waiting for a browser slot waits before asking to reclaim idle sessions again."""


class SessionMode(enum.Enum):
    """Defines when a `Runtime`'s live `Session`s are opened and how long they live."""

    EAGER = "eager"
    """
    Open the default language's session when the runtime starts, before any call. Lowest
    per-call latency, at the cost of always paying for a browser launch even
    if no live lookup is ever made.
    """

    LAZY = "lazy"
    """
    Open a language's session on the first call that needs it, then reuse it. Nothing is
    launched if every call is served locally. The default.
    """

    PER_CALL = "per_call"
    """
    Open a fresh session for every call that needs one, and close it
    immediately after. Slowest and heaviest, but gives every call full
    isolation. Handy under multi-tenant setups where sessions shouldn't be
    shared across callers.
    """


def get_db_path(database_options: DatabaseOptions) -> str | None:
    """Extract the configured local database path, or `None` for the OS default."""
    if not database_options.data_dir:
        return None
    return str(pathlib.Path(database_options.data_dir) / database_options.db_filename)


class PooledSession:
    """One session inside a `SessionPool`, with its own use-count and idle clock."""

    __slots__ = ("last_used", "session", "users")

    def __init__(self, session: Session) -> None:
        self.session = session
        self.users = 0
        self.last_used = time.monotonic()

    @property
    def in_use(self) -> bool:
        """`True` while at least one caller holds a checkout on this session."""
        return self.users > 0

    @property
    def healthy(self) -> bool:
        """
        `False` once this session's browser has disconnected (crashed, was killed, ...),
        after which it can not serve anything and should be replaced.
        """
        browser = getattr(self.session, "browser", None)
        is_connected = getattr(browser, "is_connected", None)
        return True if is_connected is None else bool(is_connected())

    @property
    def free_pages(self) -> int:
        """How many more pages this session could open right now."""
        return self.session.pages.max_size - self.session.pages.size

    def has_capacity(self, requested: int = 1, tolerance: int = 0) -> bool:
        """
        Best-effort, non-blocking read on whether this session looks like
        a good enough fit for a caller wanting `requested` pages at once,
        allowing a shortfall of up to `tolerance`.

        A page can free up (or get claimed by another checkout) immediately
        after this returns. This is just a cheap signal
        used to decide whether reusing this session is worth trying
        before considering growing the pool with a new browser instance.
        """
        return self.free_pages + tolerance >= requested


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class PoolStats:
    """A point-in-time snapshot of one `SessionPool`, from `SessionPool.stats`."""

    language: Language
    """The glossary language the pool serves."""

    sessions: int
    """Number of sessions (browser instances) currently open in the pool."""

    busy_sessions: int
    """How many of those have at least one active checkout."""

    checkouts: int
    """Total active checkouts across all sessions."""

    opening: int
    """Number of callers currently waiting on, or in the middle of, growing the pool."""

    closed: bool
    """Whether the pool has been closed."""


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeStats:
    """A point-in-time snapshot of a `Runtime`, from `Runtime.stats`."""

    pools: tuple[PoolStats, ...]
    """One entry per language that currently has a pool."""

    per_call_sessions: int
    """Number of `SessionMode.PER_CALL` sessions open right now."""

    max_sessions: int
    """The browser budget shared by every pool and every `PER_CALL` session."""

    database_open: bool
    """Whether the shared local database is currently open."""

    started: bool
    closed: bool

    @property
    def open_sessions(self) -> int:
        """Browser instances open right now, pooled and per-call together."""
        return sum(pool.sessions for pool in self.pools) + self.per_call_sessions


class SessionPool:
    """
    Owns an elastic set of live `Session`s for one glossary `language`.

    A `Session` is bound to one glossary language for its whole lifetime
    (see `slb_glossary.query.validate_language`), so something serving more than
    one language needs one pool per language. `Runtime` keeps one per language requested.

    Within one language, `Session` is already designed to be driven
    concurrently. Each caller checks out its own page from `Session.pages`
    (bounded by `SessionOptions.max_pages`), so several callers
    safely share one session without their work interfering with each other.

    What this pool adds on top is elasticity for when that's not enough.
    Once an existing session looks like it can't comfortably fit a
    caller's requested capacity (see `acquire`'s `capacity` parameter),
    `acquire` opens an additional browser instance for this same
    language instead of queueing everyone behind the first one, but only
    up to `max_sessions` (a number, or a semaphore shared with other pools), which is what
    actually bounds browser use.

    When that budget is spent, a caller shares the least-loaded session already in
    the pool rather than waiting (there is no benefit in blocking until some session
    is reaped, since the existing ones are perfectly usable; they just look full). Only a
    caller that finds the pool *empty* waits for a slot, after first asking
    `reclaim` (if given) to free up sessions that are idle elsewhere.

    Checkout/release/reap track each session's own use-count and idle
    clock independently (`PooledSession`), so a pool grows under load and
    shrinks back down session by session as they go idle. A session whose browser has
    disconnected is dropped and replaced instead of being handed out again.

    Once closed, a pool refuses further `acquire`/`warm` calls
    (`SessionPoolClosedError`). `release` is the one exception. Releasing a session
    from an already-closed pool is a safe no-op, since a call that checked a session out
    before shutdown began should still be able to release it afterward without raising.
    """

    __slots__ = (
        "_closed",
        "_growth_lock",
        "_lock",
        "_opening",
        "_reclaim",
        "_semaphore",
        "_sessions",
        "_tolerance",
        "language",
        "options",
    )

    def __init__(
        self,
        language: Language,
        options: SessionOptions,
        max_sessions: int | asyncio.Semaphore = 1,
        *,
        capacity_tolerance: int = 1,
        reclaim: Callable[[], Awaitable[int]] | None = None,
    ) -> None:
        """
        Initialize the pool.

        :param language: The glossary language this pool's sessions search.
        :param options: Session options to open with. The `language` on it is overridden
            with `language` above; everything else (browser type, headless, proxy,
            page-pool size, and so on) is shared across every session this pool opens.
        :param max_sessions: The most browser sessions this pool may have open at once; the
            pool creates its own limit from it. To bound several pools together (as
            `Runtime` does for its per-language pools), pass the same `asyncio.Semaphore`
            to each instead. A slot is taken only when the pool actually launches a new
            browser, and returned only when that specific session actually closes.
        :param capacity_tolerance: How much of a shortfall in an existing
            session's free page capacity `acquire` will accept before
            growing the pool instead, when a caller specifies a `capacity`. E.g. with
            the default of `1`, a caller asking for `capacity=3` still reuses an existing
            session with only 2 free slots rather than opening a new
            browser for the sake of one slot as there's a decent chance
            one frees up in time. Has no effect when `capacity` isn't given.
        :param reclaim: Optional coroutine function called when this pool needs a browser
            slot and none is free, before waiting for one. It should close sessions that
            are idle elsewhere and return how many it closed (`Runtime` passes its own
            `close_idle_sessions`).
        :raises ValueError: If `capacity_tolerance` is negative or `max_sessions` is below 1.
        """
        if capacity_tolerance < 0:
            raise ValueError("`capacity_tolerance` must be non-negative")
        if isinstance(max_sessions, asyncio.Semaphore):
            semaphore = max_sessions
        else:
            if max_sessions < 1:
                raise ValueError("`max_sessions` must be at least 1")
            semaphore = asyncio.Semaphore(max_sessions)

        self.language = language
        self.options = options
        self._semaphore = semaphore
        self._tolerance = capacity_tolerance
        self._reclaim = reclaim
        self._sessions: list[PooledSession] = []
        self._lock = asyncio.Lock()
        self._growth_lock = asyncio.Lock()
        """
        Serializes the decision to open a new session because every
        session looks full.

        Without this, several concurrent callers that all find the pool
        full at once would each launch their own new browser instead of
        sharing the one that growth actually produces.
        """
        self._opening = 0
        """Callers currently waiting on or doing a pool growth. Keeps the pool from looking retirable."""
        self._closed = False

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(language={self.language.value!r}, "
            f"sessions={len(self._sessions)}, closed={self._closed})"
        )

    @property
    def size(self) -> int:
        """Number of sessions currently open in this pool."""
        return len(self._sessions)

    @property
    def in_use(self) -> bool:
        """`True` if any session in this pool has at least one active checkout right now."""
        return any(pooled.in_use for pooled in self._sessions)

    @property
    def empty(self) -> bool:
        """
        `True` if the pool holds no sessions and nobody is in the middle of opening one,
        i.e. it can be dropped without losing anything.
        """
        return not self._sessions and self._opening == 0

    @property
    def closed(self) -> bool:
        """`True` once `close` has run. A closed pool refuses further `acquire`/`warm` calls."""
        return self._closed

    def stats(self) -> PoolStats:
        """Return a point-in-time snapshot of this pool."""
        return PoolStats(
            language=self.language,
            sessions=len(self._sessions),
            busy_sessions=sum(1 for pooled in self._sessions if pooled.in_use),
            checkouts=sum(pooled.users for pooled in self._sessions),
            opening=self._opening,
            closed=self._closed,
        )

    def _closed_error(self) -> SessionPoolClosedError:
        return SessionPoolClosedError(
            f"Session pool for `language={self.language.value!r}` is closed."
        )

    async def _close_session(self, pooled: PooledSession) -> BaseException | None:
        """Close one session and always return its semaphore slot."""
        logger.info("Closing session for language=%s", self.language.value)
        try:
            await close_session(pooled.session)
        except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
            raise
        except BaseException as exc:
            logger.exception(
                "Failed to close live session for language=%s",
                self.language.value,
            )
            return exc
        finally:
            self._semaphore.release()
            logger.info(
                "Closed session for language=%s (pool size now %d)",
                self.language.value,
                len(self._sessions),
            )
        return None

    async def _close_sessions(self, *sessions: PooledSession) -> None:
        """Attempt to close every session, even if some closes fail."""
        if not sessions:
            return

        results = await asyncio.gather(
            *(self._close_session(pooled) for pooled in sessions),
            return_exceptions=False,
        )
        errors = [result for result in results if result is not None]
        if errors:
            raise SessionPoolError(
                f"Failed to close sessions for language={self.language.value!r}",
                errors,
            ) from errors[-1]

    async def _discard(self, dead: list[PooledSession]) -> None:
        """Close sessions detached because their browser died. Errors are expected and only logged."""
        if not dead:
            return
        logger.warning(
            "Dropping %d session(s) for language=%s whose browser disconnected",
            len(dead),
            self.language.value,
        )
        await asyncio.gather(*(self._close_session(pooled) for pooled in dead))

    def _detach_unhealthy(self) -> list[PooledSession]:
        """Remove (and return) unused sessions whose browser has disconnected. Hold `_lock`."""
        dead = [pooled for pooled in self._sessions if not pooled.in_use and not pooled.healthy]
        if dead:
            self._sessions = [pooled for pooled in self._sessions if pooled not in dead]
        return dead

    def _pick(
        self, requested: int, tolerance: int, *, any_fit: bool = False
    ) -> PooledSession | None:
        """
        Pick the healthy session with the most free pages (fewest users breaking ties)
        that fits `requested`, or, if `any_fit`, the least loaded one regardless of fit.
        Hold `_lock`.
        """
        best: PooledSession | None = None
        best_key: tuple[int, int] = (0, 0)
        for pooled in self._sessions:
            if not pooled.healthy:
                continue
            if not any_fit and not pooled.has_capacity(requested, tolerance):
                continue

            key = (pooled.free_pages, -pooled.users)
            if best is None or key > best_key:
                best, best_key = pooled, key
        return best

    @staticmethod
    def _touch(pooled: PooledSession, hold: bool) -> None:
        pooled.last_used = time.monotonic()
        if hold:
            pooled.users += 1

    async def _find(
        self, requested: int, tolerance: int, hold: bool, *, any_fit: bool = False
    ) -> tuple[PooledSession | None, list[PooledSession]]:
        """
        Find (and, if `hold`, check out) a fitting session, returning it with any dead
        sessions that were detached while looking.

        Finding a fit and bumping `users` happen under the same lock hold, so a
        concurrent `close` can never tear down the exact session this call just decided
        to use in the gap between the two.
        """
        async with self._lock:
            if self._closed:
                raise self._closed_error()

            dead = self._detach_unhealthy()
            pooled = self._pick(requested, tolerance, any_fit=any_fit)
            if pooled is not None:
                self._touch(pooled, hold)
        return pooled, dead

    async def _try_reserve_slot(self) -> bool:
        """
        Take a browser slot from the shared semaphore if one is free right now.

        Never blocks: with a free slot `Semaphore.acquire` returns without suspending,
        so the `locked()` check and the acquire can not be interleaved with another task.
        """
        if self._semaphore.locked():
            return False
        await self._semaphore.acquire()
        return True

    async def _wait_for_slot(self) -> None:
        """
        Get a browser slot: free idle sessions elsewhere via `reclaim`, else wait for one.

        While waiting, `reclaim` is retried every `RECLAIM_POLL_INTERVAL` seconds. A slot
        held by a session that is busy right now becomes reclaimable the moment its caller
        releases it, and nothing else would tell this waiter so before the idle timeout.
        """
        while True:
            if self._reclaim is not None:
                try:
                    await self._reclaim()
                except Exception:
                    logger.warning("Reclaiming idle sessions failed", exc_info=True)

            if await self._try_reserve_slot():
                return

            logger.debug(
                "No free browser slot for language=%s; waiting for one", self.language.value
            )
            if self._reclaim is None:
                await self._semaphore.acquire()
                return
            try:
                await asyncio.wait_for(self._semaphore.acquire(), timeout=RECLAIM_POLL_INTERVAL)
                return
            except asyncio.TimeoutError:
                continue

    async def _launch(self, *, hold: bool) -> PooledSession:
        """
        Launch a genuinely new browser instance and register it. The caller must already
        hold a slot from the shared semaphore, which this releases again if the launch
        fails (or the pool turns out to be closed).
        """
        try:
            opened_at = time.monotonic()
            kwargs = self.options.session_kwargs()
            kwargs["language"] = self.language
            # A pool only opens a session because a live call for `self.language`
            # is imminent or already in flight, so there is no reason to defer
            # the topics/size load further.
            kwargs["initialize"] = True
            session = await open_session(**kwargs)
        except BaseException:
            # The launch itself failed and this pool never got a browser
            # instance, so it shouldn't hold onto the slot.
            self._semaphore.release()
            logger.warning(
                "Failed to open a live session for language=%s", self.language.value, exc_info=True
            )
            raise

        pooled = PooledSession(session)
        async with self._lock:
            if not self._closed:
                self._sessions.append(pooled)
                self._touch(pooled, hold)
                logger.info(
                    "Live session opened for language=%s in %.3fs (pool size now %d)",
                    self.language.value,
                    time.monotonic() - opened_at,
                    len(self._sessions),
                )
                return pooled

        # Closed while the browser was launching. Don't hand out a session from (or
        # add it to) a pool that's supposed to be dead; close what we just opened.
        exc = await self._close_session(pooled)
        raise SessionPoolClosedError(
            f"Session pool for language={self.language.value!r} was closed while opening."
        ) from exc

    async def _checkout(self, capacity: int | None, *, hold: bool) -> PooledSession:
        """
        Find a session that fits `capacity`, growing the pool if none does.

        :param hold: Whether this is a real checkout (`acquire`: bumps the session's
            `users`) or just makes sure a session exists (`warm`).
        """
        requested = capacity if capacity is not None else 1
        tolerance = self._tolerance if capacity is not None else 0

        pooled, dead = await self._find(requested, tolerance, hold)
        await self._discard(dead)
        if pooled is not None:
            return pooled

        # Every existing session looked like too tight a fit (or there were none yet).
        # Growth happens outside `_lock` (a browser launch shouldn't block checkouts of a
        # different, better-fitting session already in this pool, or a release), but
        # under `_growth_lock`, so concurrent callers that all found the pool full
        # don't each launch their own new browser.
        self._opening += 1
        try:
            async with self._growth_lock:
                # Another caller may have already grown the pool (or a session may
                # have freed up) while we waited for `_growth_lock`.
                pooled, dead = await self._find(requested, tolerance, hold)
                await self._discard(dead)
                if pooled is not None:
                    return pooled

                if not await self._try_reserve_slot():
                    # The browser budget is spent. If this pool already has a session,
                    # share the least loaded one instead of blocking: it is perfectly
                    # usable (the page pool queues callers when it really is full), while
                    # a slot may not free up until a session goes idle, which can
                    # be minutes, or never when there is no idle timeout.
                    pooled, dead = await self._find(requested, tolerance, hold, any_fit=True)
                    await self._discard(dead)
                    if pooled is not None:
                        logger.debug(
                            "Browser budget spent; sharing a session for language=%s "
                            "(requested capacity=%d)",
                            self.language.value,
                            requested,
                        )
                        return pooled
                    await self._wait_for_slot()

                logger.debug(
                    "Growing session pool for language=%s (requested capacity=%d, current size=%d)",
                    self.language.value,
                    requested,
                    len(self._sessions),
                )
                return await self._launch(hold=hold)
        finally:
            self._opening -= 1

    async def new(self) -> PooledSession:
        """
        Open and register a new idle session in this pool, even if existing ones have room.

        This is an explicit-growth operation, and waits for a browser slot if the budget
        is spent. The returned session is owned by the pool and must not be closed directly.

        :raises SessionPoolClosedError: If this pool is closed.
        """
        async with self._lock:
            if self._closed:
                raise self._closed_error()

        self._opening += 1
        try:
            if not await self._try_reserve_slot():
                await self._wait_for_slot()
            return await self._launch(hold=False)
        finally:
            self._opening -= 1

    async def acquire(self, capacity: int | None = None) -> Session:
        """
        Check out a session for one caller, growing the pool if no
        existing session comfortably fits `capacity`.

        Pair with `release`, passing back the exact `Session` this returns, or use
        `checkout` to have that done for you.

        :param capacity: How many pages the caller expects to want at
            once, if known. Purely advisory input to the grow-or-reuse
            decision. It doesn't reserve pages; actual page-level
            concurrency is still enforced by `Session.pages` itself when
            the caller does its real work.
        :raises ValueError: If `capacity` is given but less than 1.
        :raises SessionPoolClosedError: If this pool is closed.
        """
        if capacity is not None and capacity < 1:
            raise ValueError("capacity must be at least 1")
        return (await self._checkout(capacity, hold=True)).session

    @contextlib.asynccontextmanager
    async def checkout(self, capacity: int | None = None) -> AsyncIterator[Session]:
        """
        `acquire` a session for the duration of an `async with` block, and `release` it after.

        ```python
        async with pool.checkout(capacity=3) as session:
            async for result in live.search(session, "porosity"):
                ...
        ```

        :param capacity: See `acquire`.
        """
        session = await self.acquire(capacity)
        try:
            yield session
        finally:
            await self.release(session)

    async def warm(self) -> Session:
        """
        Ensure at least one session is open in this pool, without checking one out.

        The returned session is not protected from idle reaping once it has sat unused
        for the idle timeout, so use `acquire`/`checkout` for anything beyond pre-warming.

        :returns: A session that is now open and ready for use.
        :raises SessionPoolClosedError: If this pool is closed.
        """
        return (await self._checkout(None, hold=False)).session

    async def open(self) -> Session:
        """Alias for `warm`."""
        return await self.warm()

    async def release(self, session: Session) -> None:
        """
        Release a checkout from `acquire`, refreshing that specific
        session's idle clock.

        A no-op if this pool has since been closed. A call that
        checked a session out before shutdown began can still release it
        afterward without that raising, even though the session itself
        is already gone.

        :param session: The exact `Session` object `acquire` returned.
        :raises SessionPoolError: If the pool is still open but `session` isn't
            one of its currently-tracked sessions, or its use-count would go
            negative. Either means a caller released something it never
            validly checked out from this (still-open) pool.
        """
        async with self._lock:
            if self._closed:
                return
            for pooled in self._sessions:
                if pooled.session is session:
                    if pooled.users == 0:
                        raise SessionPoolError(
                            f"Session pool for language={self.language.value!r} "
                            f"reference count went negative for a tracked session."
                        )
                    pooled.users -= 1
                    pooled.last_used = time.monotonic()
                    return
        raise SessionPoolError(
            f"Released a session not currently tracked by the language={self.language.value!r} pool "
            f"(already closed and reaped?)."
        )

    async def close_idle(self, idle_timeout: float) -> int:
        """
        Close every session in this pool that's unused and has been idle
        for at least `idle_timeout` (plus any whose browser has disconnected), shrinking
        the pool session by session.

        A no-op if this pool is already closed.

        :return: How many sessions were closed.
        """
        if self._closed:
            return 0
        # Detach the sessions to close (under `_lock`) before actually
        # closing them (outside `_lock`), so slow `close_session` calls
        # can't block a concurrent `acquire`/`release`/`warm` on this pool.
        now = time.monotonic()
        async with self._lock:
            keep: list[PooledSession] = []
            close: list[PooledSession] = []
            for pooled in self._sessions:
                if pooled.in_use or (pooled.healthy and (now - pooled.last_used) < idle_timeout):
                    keep.append(pooled)
                else:
                    close.append(pooled)
            self._sessions = keep

        if close:
            logger.info(
                "Closing %d idle live session(s) for language=%s (idle_timeout=%.1fs, pool size now %d)",
                len(close),
                self.language.value,
                idle_timeout,
                len(self._sessions),
            )
        await self._close_sessions(*close)
        return len(close)

    async def close(self) -> None:
        """
        Close every session in this pool, regardless of use, and mark it
        closed. Further `acquire`/`warm` calls raise `SessionPoolClosedError`
        (`release` stays safe; see its own docstring). For shutdown.

        Safe to call more than once. Later calls are no-ops.
        """
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            sessions = self._sessions
            self._sessions = []

        if sessions:
            logger.info(
                "Closing session pool for language=%s (%d session(s))",
                self.language.value,
                len(sessions),
            )
            await self._close_sessions(*sessions)


class Runtime:
    """
    Owns and manages the shared resources (a local `Database` and/or live `Session`s)
    for one running application.

    Live sessions are pooled per language for `EAGER`/`LAZY` mode, since a `Session`
    is bound to one glossary language for its whole lifetime. A `Runtime` asked to
    serve calls in more than one language therefore needs one `SessionPool` per language,
    not one session shared across all of them. All pools share one browser budget
    (`max_sessions`). `PER_CALL` mode does not use the pools at all. It opens and closes a
    fresh session per call regardless of language, which is its whole point (see
    `acquire`'s docstring for when that isolation is worth the extra opening cost).

    Everything an application might want to tune is an init argument, so nothing here
    depends on any particular framework's configuration. `Runtime.from_config` builds one
    straight from a `slb_glossary.config.Config`.

    A `Runtime` is an async context manager: `async with Runtime(...) as runtime:` starts it
    and guarantees it is closed again.
    """

    def __init__(
        self,
        *,
        name: str = "slb-glossary",
        session_options: SessionOptions | None = None,
        database_options: DatabaseOptions | None = None,
        mode: SessionMode | str = SessionMode.LAZY,
        local_enabled: bool | None = None,
        live_enabled: bool = True,
        idle_timeout: float | None = 300.0,
        max_sessions: int = 1,
        capacity_tolerance: int = 1,
    ) -> None:
        """
        :param name: Human-readable name used in logs and task names.
        :param session_options: Options every live session is opened with, apart from its
            `language`, which is chosen per call (defaulting to `session_options.language`).
            Defaults to `SessionOptions()`.
        :param database_options: Where the local database lives. Defaults to `DatabaseOptions()`
            (the OS-appropriate default location).
        :param mode: When live sessions open and how long they live. See `SessionMode`.
        :param local_enabled: Whether the local database may be used at all. `None` (the
            default) defers to `database_options.enabled`.
        :param live_enabled: Whether live sessions may be used at all. `False` makes this
            a local-only runtime.
        :param idle_timeout: Seconds a pooled session may sit unused before a background
            reaper closes it (a later call reopens one). `None` disables the reaper, so
            sessions live until `close`. Ignored for `PER_CALL`.
        :param max_sessions: Maximum number of browser instances open at once, across all
            languages and `PER_CALL` sessions together. Raising `SessionOptions.max_pages`
            is generally the cheaper lever to pull first: growing a session's page pool
            costs nothing extra, where growing this opens a whole new browser.
        :param capacity_tolerance: How much of a shortfall in an existing session's free
            page capacity a call's requested `capacity` tolerates before the pool opens a new
            browser instead of reusing it. See `SessionPool`.
        :raises ValueError: If `max_sessions` is below 1, `capacity_tolerance` is negative,
            `idle_timeout` is not positive, or `mode` is not a valid `SessionMode`.
        """
        if max_sessions < 1:
            raise ValueError("`max_sessions` must be at least 1")
        if capacity_tolerance < 0:
            raise ValueError("`capacity_tolerance` must be non-negative")
        if idle_timeout is not None and idle_timeout <= 0:
            raise ValueError("`idle_timeout` must be positive, or `None` to disable reaping")

        self.name = name
        self.session_options = session_options if session_options is not None else SessionOptions()
        self.database_options = (
            database_options if database_options is not None else DatabaseOptions()
        )
        self.mode = SessionMode(mode)
        self.local_enabled = (
            self.database_options.enabled if local_enabled is None else local_enabled
        )
        self.live_enabled = live_enabled
        self.idle_timeout = idle_timeout
        self.max_sessions = max_sessions
        self.capacity_tolerance = capacity_tolerance

        self._db: Database | None = None
        self._db_lock = asyncio.Lock()
        self._pools: dict[Language, SessionPool] = {}
        self._pools_lock = asyncio.Lock()
        self._session_semaphore = asyncio.Semaphore(max_sessions)
        self._per_call_open = 0
        self._reaper_task: asyncio.Task[None] | None = None
        self._started = False
        self._closed = False

    @classmethod
    def from_config(cls, config: Config, **overrides: typing.Any) -> typing.Self:
        """
        Build a `Runtime` from a `slb_glossary.config.Config` (its `session` and `local`
        sections).

        :param config: The configuration to take session and database options from.
        :param overrides: Any `Runtime` init argument, taking precedence over what
            `config` implies, e.g. `mode=SessionMode.EAGER` or `max_sessions=3`.
        """
        kwargs: dict[str, typing.Any] = {
            "session_options": config.session,
            "database_options": config.local,
        }
        kwargs.update(overrides)
        return cls(**kwargs)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}({self.name!r}, mode={self.mode.value!r}, "
            f"started={self._started}, closed={self._closed})"
        )

    async def __aenter__(self) -> typing.Self:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    @property
    def closed(self) -> bool:
        """`True` once `close` has run. A closed `Runtime` refuses further `acquire`/`open_session` calls."""
        return self._closed

    @property
    def started(self) -> bool:
        """`True` once `start` has run."""
        return self._started

    def stats(self) -> RuntimeStats:
        """Return a point-in-time snapshot of the sessions and database this runtime holds."""
        return RuntimeStats(
            pools=tuple(pool.stats() for pool in list(self._pools.values())),
            per_call_sessions=self._per_call_open,
            max_sessions=self.max_sessions,
            database_open=self._db is not None,
            started=self._started,
            closed=self._closed,
        )

    def _raise_if_closed(self) -> None:
        if self._closed:
            raise RuntimeClosedError(f"[{self.name}] Runtime is closed.")

    async def start(self) -> None:
        """
        Perform startup-time work. Opens a local DB connection (if enabled), eagerly
        opens a live session for the default language if `SessionMode.EAGER` is
        configured, and starts the idle-session reaper if `idle_timeout` is set.

        Safe to call more than once; later calls are no-ops. If startup fails, whatever it
        had already opened is closed again (so nothing leaks) and the runtime is left closed.

        :raises RuntimeClosedError: If this runtime was already closed.
        """
        self._raise_if_closed()
        if self._started:
            return
        self._started = True
        started_at = time.monotonic()

        try:
            if self.local_enabled:
                await self._open_db()

            if self.live_enabled and self.mode is SessionMode.EAGER:
                # Only the default language is warmed up here. Any other language
                # a call later asks for still gets its own pool lazily.
                await self.open_session()

            if (
                self.live_enabled
                and self.mode is not SessionMode.PER_CALL
                and self.idle_timeout is not None
            ):
                self._reaper_task = asyncio.create_task(
                    self._reap_idle_sessions(), name=f"{self.name}:session-reaper"
                )
        except BaseException:
            logger.warning("[%s] Runtime failed to start; releasing resources", self.name)
            with contextlib.suppress(Exception):
                await self.close()
            raise

        logger.info("[%s] Runtime started in %.3fs", self.name, time.monotonic() - started_at)

    async def close(self) -> None:
        """
        Tear down every resource this runtime opened. Safe to call more than once.

        Every pool and the database are closed even if some of them fail to; the first
        failure is raised only after everything has been attempted.
        """
        if self._closed:
            return
        self._closed = True
        started_at = time.monotonic()
        errors: list[BaseException] = []

        if self._reaper_task is not None:
            self._reaper_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reaper_task
            self._reaper_task = None

        async with self._pools_lock:
            pools = list(self._pools.values())
            self._pools.clear()

        results = await asyncio.gather(*(pool.close() for pool in pools), return_exceptions=True)
        errors.extend(result for result in results if isinstance(result, BaseException))

        async with self._db_lock:
            if self._db is not None:
                try:
                    await close_db(self._db)
                except Exception as exc:
                    errors.append(exc)
                self._db = None

        logger.info("[%s] Runtime closed in %.3fs", self.name, time.monotonic() - started_at)
        if errors:
            for error in errors[1:]:
                logger.error("[%s] Additional failure while closing", self.name, exc_info=error)
            raise errors[0]

    async def aclose(self) -> None:
        """Alias for `close`."""
        await self.close()

    async def _open_db(self) -> Database:
        async with self._db_lock:
            if self._db is None:
                opened_at = time.monotonic()
                self._db = await open_db(get_db_path(self.database_options))
                logger.info(
                    "[%s] Local database opened in %.3fs", self.name, time.monotonic() - opened_at
                )
            return self._db

    async def open_db(self) -> Database:
        """
        Return the shared local `Database`, opening it on first use.

        Unlike `acquire`, this does not route through `Source` resolution.
        Meant for callers that always need the local database regardless
        of which `Source` a call otherwise resolves to.

        :raises RuntimeClosedError: If this runtime is closed.
        :raises ResourceDisabledError: If local database access is disabled.
        """
        self._raise_if_closed()
        if not self.local_enabled:
            raise ResourceDisabledError(
                f"[{self.name}] This runtime has local database access disabled."
            )
        return await self._open_db()

    def resolve_language(self, language: str | Language | None) -> Language:
        """
        Resolve a per-call language request (or `None`, to use the
        default) to a `Language` member, used to pick which `SessionPool` a call is routed to.

        :param language: A caller-requested language, e.g. from a request's
            `language` argument, or `None` to use `self.session_options.language`.
        :raises UnknownLanguageError: If `language` is a string that is not a valid `Language` value.
        """
        if language is None:
            language = self.session_options.language
        if isinstance(language, Language):
            return language
        try:
            return Language(language)
        except ValueError as exc:
            choices = ", ".join(member.value for member in Language)
            raise UnknownLanguageError(
                f"[{self.name}] Unknown language {language!r}. Expected one of: {choices}."
            ) from exc

    async def get_session_pool(self, language: Language) -> SessionPool:
        """
        Return the `SessionPool` for `language`, creating it on first request.

        A pool that has since gone idle and unused is closed and dropped by the
        reaper, so a language that hasn't been asked for in a while does not keep an
        entry around forever. This just recreates it, empty, the next time it's asked for.

        :raises RuntimeClosedError: If this runtime is closed.
        """
        # Lock-free fast path. The common case is an existing, live pool.
        pool = self._pools.get(language)
        if pool is not None and not pool.closed and not self._closed:
            return pool

        async with self._pools_lock:
            self._raise_if_closed()
            pool = self._pools.get(language)
            if pool is None or pool.closed:
                pool = SessionPool(
                    language,
                    self.session_options,
                    self._session_semaphore,
                    capacity_tolerance=self.capacity_tolerance,
                    reclaim=self.close_idle_sessions_now,
                )
                self._pools[language] = pool
            return pool

    async def _acquire_pooled(
        self, language: Language, capacity: int | None
    ) -> tuple[SessionPool, Session]:
        """
        Check a session out of `language`'s pool.

        The reaper may retire the pool between looking it up and checking out of it, in
        which case checking out raises `SessionPoolClosedError`. Look it up again, which
        recreates it, rather than failing a perfectly good call.
        """
        last_error: SessionPoolClosedError | None = None
        for _ in range(3):
            pool = await self.get_session_pool(language)
            try:
                return pool, await pool.acquire(capacity)
            except SessionPoolClosedError as exc:
                self._raise_if_closed()
                last_error = exc
        assert last_error is not None
        raise last_error

    async def open_session(self, language: str | Language | None = None) -> Session:
        """
        Return `language`'s pooled session (the default if omitted), opening it on
        first use, without checking it out.

        The session is not protected from idle reaping once it has sat unused for
        the idle timeout. For anything beyond pre-warming, use `session` or `acquire`.

        :raises RuntimeClosedError: If this `Runtime` is closed.
        :raises ResourceDisabledError: If live sessions are disabled.
        """
        self._raise_if_closed()
        if not self.live_enabled:
            raise ResourceDisabledError(
                f"[{self.name}] This runtime has live glossary access disabled."
            )
        pool = await self.get_session_pool(self.resolve_language(language))
        return await pool.warm()

    async def _reap_idle_sessions(self) -> None:
        """Background task. Closes each pool's idle sessions after they've sat unused past `idle_timeout`."""
        idle_timeout = self.idle_timeout
        assert idle_timeout is not None, (
            f"[{self.name}] `_reap_idle_sessions` started with `idle_timeout=None`; "
            f"`{type(self).__name__}.start()` should never have scheduled this task in that case."
        )
        assert self.mode is not SessionMode.PER_CALL, (
            f"[{self.name}] `_reap_idle_sessions` started under `SessionMode.PER_CALL`, which never "
            f"maintains pooled sessions for it to reap; `{type(self).__name__}.start()` should never "
            f"have scheduled this task in that case."
        )
        interval = min(max(idle_timeout / 4, 1.0), 60.0)
        while True:
            await asyncio.sleep(interval)
            try:
                await self.close_idle_sessions(idle_timeout)
            except Exception:
                # One failed close must not kill the reaper for good, or idle
                # browsers would pile up for the rest of the process's life.
                logger.exception("[%s] Idle-session reaping failed; will retry", self.name)

    async def close_idle_sessions(self, idle_timeout: float | None = None) -> int:
        """
        Run one idle-session check/close cycle across every language pool.

        Each pool decides independently which of its own sessions (it may hold several)
        are unused and idle long enough to close (see `SessionPool.close_idle`). A pool
        left empty afterward is closed and dropped, so a language that's stopped being
        requested does not keep an entry around forever.

        :param idle_timeout: Seconds a session must have been idle to be closed. Defaults
            to this runtime's `idle_timeout`; `0` closes every session nobody is using.
        :return: How many sessions were closed.
        """
        if idle_timeout is None:
            idle_timeout = self.idle_timeout if self.idle_timeout is not None else 0.0
        async with self._pools_lock:
            pools = list(self._pools.items())

        closed = 0
        for language, pool in pools:
            closed += await pool.close_idle(idle_timeout)
            if pool.empty:
                async with self._pools_lock:
                    # Only drop it if it's still the exact same, still-empty pool.
                    # A concurrent `get_session_pool`/`acquire` could have replaced it
                    # (or started growing it) since the check above.
                    if self._pools.get(language) is pool and pool.empty:
                        del self._pools[language]
                        # Close it so that a caller that looked the pool up just before
                        # it was dropped is told to look again (see `_acquire_pooled`),
                        # instead of opening a session in a pool nothing tracks any more,
                        # which would never be reaped or closed.
                        await pool.close()
        return closed

    async def close_idle_sessions_now(self) -> int:
        """
        Close every session nobody is using right now, regardless of how recently it was used.

        This is what pools call to free a browser slot when the budget is spent.
        """
        return await self.close_idle_sessions(0.0)

    @contextlib.asynccontextmanager
    async def session(
        self, language: str | Language | None = None, *, capacity: int | None = None
    ) -> AsyncIterator[Session]:
        """
        Yield a live `Session` for `language` for the duration of an `async with` block.

        Shorthand for `acquire(Source.LIVE, ...)` when only the session is wanted, so
        an application can manage sessions without ever touching `Source`.

        ```python
        async with runtime.session("es", capacity=3) as session:
            async for result in live.search(session, "porosidad", concurrency=3):
                ...
        ```

        :param language: The glossary language needed; `None` for the default.
        :param capacity: See `acquire`.
        """
        async with self.acquire(Source.LIVE, language=language, capacity=capacity) as (_, session):
            assert session is not None, "`acquire(Source.LIVE)` should always yield a session"
            yield session

    @contextlib.asynccontextmanager
    async def acquire(
        self,
        source: Source = Source.AUTO,
        *,
        language: str | Language | None = None,
        capacity: int | None = None,
    ) -> AsyncIterator[tuple[Database | None, Session | None]]:
        """
        Yield the `(db, session)` pair a call needs to satisfy `source`.

        Honours `SessionMode`. For `EAGER`/`LAZY`, `language` (the
        default if omitted) selects which language's `SessionPool` this call is routed
        to (see `get_session_pool`); a session is checked out from that pool for the
        duration of the caller's `async with` block (see `SessionPool.acquire`).

        A language's pool is not limited to one session. Concurrent calls
        for the same language share whichever of that language's open
        sessions has spare page capacity (each still checks out its own
        page internally so they do not interfere with each other), and the
        pool opens an additional browser instance for that language if every
        existing one looks full, rather than queuing everyone behind a single
        session. What the checkout does guarantee, regardless of how many sessions a
        pool holds, is that the idle-session reaper can never close a
        session while any call still holds a checkout on it.

        For `PER_CALL`, a fresh session for `language` is opened for the
        duration of the `async with` block and closed on exit, bypassing
        the pool entirely. Worth it over `EAGER`/`LAZY` when callers
        shouldn't share any session state (cookies, browser identity)
        even when they happen to request the same language, e.g. a
        service used by multiple untrusted or mutually-distrusting
        callers, albeit at the cost of a fresh browser session per call instead
        of reusing one. As of this glossary's current site (no login, no
        user-specific session data), that isolation usually is not needed
        day to day, but the mode stays available for a deployment or a
        future site change that does need it.

        Either way, `max_sessions` bounds how many browser instances may be open at
        once. For `PER_CALL`, a slot is held for the whole lifetime of that call's own
        session. For `EAGER`/`LAZY`, a slot is held for as long as one specific session
        (of however many a pool holds) is open, and acquired only when a pool actually
        launches a new browser, released only when that specific session closes. If the
        budget is spent, a pooled call shares an existing session of its language instead
        of waiting, and a call for a language with no session yet frees idle sessions of
        other languages to make room.

        Caution should be taken when nesting `PER_CALL` acquires. Do not call `acquire`
        again from inside an already open `acquire` block in the same task if
        doing so might need a new browser while `max_sessions` is already exhausted
        by the outer call holding its slot. The semaphore is not reentrant, so that
        nested call would deadlock waiting on a slot its own outer call holds.

        :param source: The resolved `Source` this call needs resources for.
        :param language: Which glossary language's session this call
            needs, e.g. from a request's own `language` argument. `None`
            uses the default (`session_options.language`).
        :param capacity: How many pages this call expects to want open at
            once, if the caller knows (e.g. a call's own `concurrency`
            argument). This is purely advisory as it informs `EAGER`/`LAZY`'s
            grow-or-reuse decision (see `SessionPool.acquire`) and, for
            `PER_CALL`, raises that call's own dedicated session's
            `max_pages` to at least this, so a call that plans to do
            `capacity` things at once doesn't immediately self-block on
            its own session's page pool. `None` (the default) doesn't
            influence either.
        :yield: A `(db, session)` tuple, either of which may be `None` if
            `source` does not require it.
        :raises RuntimeClosedError: If this `Runtime` is closed.
        :raises ResourceDisabledError: If `source` needs a resource this `Runtime`
            was configured not to provide.
        :raises UnknownLanguageError: If `language` is not a valid `Language` value.
        """
        self._raise_if_closed()

        needs_db = source in (Source.LOCAL, Source.AUTO)
        needs_session = source in (Source.LIVE, Source.AUTO)

        if source is Source.LOCAL and not self.local_enabled:
            raise ResourceDisabledError(
                f"[{self.name}] This runtime has local database access disabled."
            )
        if source is Source.LIVE and not self.live_enabled:
            raise ResourceDisabledError(
                f"[{self.name}] This runtime has live glossary access disabled."
            )

        resolved_language = self.resolve_language(language)
        db = await self._open_db() if (needs_db and self.local_enabled) else None
        if not needs_session or not self.live_enabled:
            yield db, None
            return

        if self.mode is SessionMode.PER_CALL:
            async with self._session_semaphore:
                opened_at = time.monotonic()
                kwargs = self.session_options.session_kwargs()
                kwargs["language"] = resolved_language
                if capacity is not None:
                    kwargs["max_pages"] = max(kwargs.get("max_pages", 1), capacity)

                # A session opened here is about to be used for this call's
                # live fetch, so there's no reason to defer initialization further.
                kwargs["initialize"] = True
                session = await open_session(**kwargs)
                self._per_call_open += 1
                logger.debug(
                    "[%s] Per-call session opened in %.3fs (language=%s)",
                    self.name,
                    time.monotonic() - opened_at,
                    resolved_language.value,
                )
                try:
                    yield db, session
                finally:
                    self._per_call_open -= 1
                    closed_at = time.monotonic()
                    await close_session(session)
                    logger.debug(
                        "[%s] Per-call session closed in %.3fs",
                        self.name,
                        time.monotonic() - closed_at,
                    )
            return

        pool, session = await self._acquire_pooled(resolved_language, capacity)
        try:
            yield db, session
        finally:
            await pool.release(session)
