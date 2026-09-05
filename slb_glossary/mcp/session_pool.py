"""Per-language elastic pooling of live `Session`s for `slb_glossary.mcp.runtime.Runtime`."""

import asyncio
import logging
import time

from slb_glossary.config import SessionOptions
from slb_glossary.live.browser import Session, close_session, open_session
from slb_glossary.types import Language

logger = logging.getLogger(__name__)

__all__ = ["SessionPool"]


class _PooledSession:
    """One session inside a `SessionPool`, with its own use-count and idle clock."""

    __slots__ = ("last_used", "session", "users")

    def __init__(self, session: Session) -> None:
        self.session = session
        self.users = 0
        self.last_used = time.monotonic()

    @property
    def in_use(self) -> bool:
        return self.users > 0

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
        free = self.session.pages.max_size - self.session.pages.size
        return free + tolerance >= requested


class SessionPool:
    """
    Owns an elastic set of live `Session`s for one glossary `language`,
    to be used by the MCP `Runtime`.

    A `Session` is bound to one glossary language for its whole lifetime
    (see `slb_glossary.query.validate_language`), so a `Runtime` serving
    more than one language needs one pool per language. `Runtime` holds
    a `dict[Language, SessionPool]`, creating one of these per language
    actually requested.

    Within one language, `Session` is already designed to be driven
    concurrently. Each caller checks out its own page from `Session.pages`
    (bounded by `SessionOptions`' page-pool size), so several callers
    safely share one session without their work interfering with each
    other.

    What this pool adds on top is elasticity for when that's not enough.
    Once an existing session looks like it can't comfortably fit a
    caller's requested capacity (see `acquire`'s `capacity` parameter),
    `acquire` opens an additional browser instance for this same
    language instead of queueing everyone behind the first one. But only
    up to the shared, Runtime-wide `semaphore` passed in at construction
    (see `SessionAccess.max_sessions`), which is what actually gates
    total system-wide resource use, not this pool on its own.

    Checkout/release/reap track each session's own use-count and idle
    clock independently (`_PooledSession`), so a language's pool grows
    under load and shrinks back down session by session, as sessions
    individually go idle and get reaped.

    Once closed, a pool refuses further `acquire`/`open` calls
    (`RuntimeError`). `release` is the one exception.
    Releasing a session from an already-closed pool is a safe no-op,
    since a call that checked a session out before shutdown began should
    still be able to release it afterward without raising.
    """

    __slots__ = (
        "_closed",
        "_growth_lock",
        "_lock",
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
        semaphore: asyncio.Semaphore,
        *,
        capacity_tolerance: int = 1,
    ) -> None:
        """
        Initialize the pool.

        :param language: The glossary language this pool's sessions search.
        :param options: Session options to open with, e.g. from
            `MCPConfig.session.options`. The `language` on it is overridden with
            `language` above; everything else (browser type, headless,
            proxy, page-pool size, and so on) is shared across every
            session this pool opens, and every other language's pool.
        :param semaphore: Shared, Runtime-wide semaphore bounding how many
            browser sessions/instances may be open at once, across every language's
            pool (and `PER_CALL` sessions, if that mode is in use).
            A slot is acquired only when this pool actually launches a new browser,
            and released only when that specific session actually closes.
        :param capacity_tolerance: How much of a shortfall in an existing
            session's free page capacity `acquire` will accept before
            growing the pool instead, when a caller specifies a
            `capacity` (see `acquire`). E.g. with the default of `1`, a
            caller asking for `capacity=3` still reuses an existing
            session with only 2 free slots rather than opening a new
            browser for the sake of one slot as there's a decent chance
            one frees up in time, and even if not, running most of the
            request concurrently is usually better than paying for a
            whole new session. This has no effect when `capacity` isn't given.
        """
        self.language = language
        self.options = options
        self._semaphore = semaphore
        self._tolerance = capacity_tolerance
        self._sessions: list[_PooledSession] = []
        self._lock = asyncio.Lock()
        self._growth_lock = asyncio.Lock()
        """
        Serializes the decision to open a new session because every
        session looks full.

        Without this, several concurrent callers that all find the pool
        full at once would each launch their own new browser instead of
        sharing the one that growth actually produces.
        """
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
    def closed(self) -> bool:
        """`True` once `close` has run. A closed pool refuses further `acquire`/`open` calls."""
        return self._closed

    async def new(self) -> _PooledSession:
        """
        Launch a genuinely new browser instance for this language.

        Acquires one slot from the shared semaphore first. This is the
        only place a slot is acquired, and it can block here if the
        Runtime-wide budget is exhausted, until some pool's session
        (this language's or another's) closes and frees one.
        """
        await self._semaphore.acquire()
        try:
            opened_at = time.monotonic()
            kwargs = self.options.session_kwargs()
            kwargs["language"] = self.language
            # This pool only ever opens a session because a live call for
            # `self.language` is imminent or already in flight, so there's
            # no reason to defer the topics/size load further.
            kwargs["initialize"] = True
            session = await open_session(**kwargs)
        except Exception:
            # The launch itself failed and this pool never got a browser
            # instance, so it shouldn't hold onto the slot.
            self._semaphore.release()
            logger.warning(
                "Failed to open a live session for language=%s", self.language.value, exc_info=True
            )
            raise
        logger.info(
            "Live session opened for language=%s in %.3fs (pool size now %d)",
            self.language.value,
            time.monotonic() - opened_at,
            len(self._sessions) + 1,
        )
        return _PooledSession(session)

    async def _get_or_create(self, capacity: int | None = None) -> _PooledSession:
        """
        Return a session that fits `capacity`, or grow the pool with a
        new browser instance if none currently does.

        :param capacity: How many pages the caller expects to want at
            once. `None` (the default) just needs some free room.
            A caller that knows its own expected concurrency can pass it
            here so an existing, nearly-full session is still reused instead
            of growing, within `self._tolerance`.

        Doesn't bump the returned session's `users`. Callers should do that
        themselves only if this represents an actual checkout (`acquire`
        does; `open`, used for pre-warming, does not).

        :raises RuntimeError: If this pool is closed.
        """
        if self._closed:
            raise RuntimeError(f"Session pool for language={self.language.value!r} is closed.")

        requested = capacity if capacity is not None else 1
        tolerance = self._tolerance if capacity is not None else 0

        async with self._lock:
            for pooled in self._sessions:
                if pooled.has_capacity(requested, tolerance):
                    pooled.last_used = time.monotonic()
                    return pooled

        # Every existing session looked like too tight a fit (or there
        # were none yet). Growth happens outside `_lock`, so checkouts of
        # a different, better-fitting session already in this pool, or a
        # release, aren't blocked behind a potentially slow browser
        # launch; but under `_growth_lock`, so concurrent callers that
        # all found the pool full do not each launch their own new browser.
        async with self._growth_lock:
            # Re-check as another caller may have already grown the pool
            # (or a session may have freed up) while we waited for `_growth_lock`.
            async with self._lock:
                for pooled in self._sessions:
                    if pooled.has_capacity(requested, tolerance):
                        pooled.last_used = time.monotonic()
                        return pooled

            logger.debug(
                "Growing session pool for language=%s (requested capacity=%d, current size=%d)",
                self.language.value,
                requested,
                len(self._sessions),
            )
            pooled = await self.new()
            async with self._lock:
                if self._closed:
                    # Closed while we were opening. Don't hand out a
                    # session from (or add it to) a pool that's supposed
                    # to be dead. Close what we just opened instead.
                    await close_session(pooled.session)
                    self._semaphore.release()
                    raise RuntimeError(
                        f"Session pool for language={self.language.value!r} was closed while opening."
                    )
                self._sessions.append(pooled)
            return pooled

    async def acquire(self, capacity: int | None = None) -> Session:
        """
        Check out a session for one caller, growing the pool if no
        existing session comfortably fits `capacity`.

        Pair with `release`, passing back the exact `Session` this returns.

        :param capacity: How many pages the caller expects to want at
            once, if known. Purely advisory input to the grow-or-reuse
            decision. It doesn't reserve pages; actual page-level
            concurrency is still enforced by `Session.pages` itself when
            the caller does its real work.
        :raises RuntimeError: If this pool is closed.
        """
        requested = capacity if capacity is not None else 1
        tolerance = self._tolerance if capacity is not None else 0

        # Finding a fit and bumping `users` happen under the same lock
        # hold. So a concurrent `close` can never tear down the exact session
        # this call just decided to use in the gap between the two.
        async with self._lock:
            if self._closed:
                raise RuntimeError(f"Session pool for language={self.language.value!r} is closed.")
            for pooled in self._sessions:
                if pooled.has_capacity(requested, tolerance):
                    pooled.users += 1
                    pooled.last_used = time.monotonic()
                    return pooled.session

        # Every existing session looked like too tight a fit (or there
        # were none yet). Growth happens outside `_lock` (a browser
        # launch shouldn't block checkouts of a different, better-fitting
        # session already in this pool, or a release), but under
        # `_growth_lock`, so concurrent callers that all found the pool
        # full don't each launch their own new browser.
        async with self._growth_lock:
            async with self._lock:
                if self._closed:
                    raise RuntimeError(
                        f"Session pool for language={self.language.value!r} is closed."
                    )
                # Another caller may have already grown the pool (or a
                # session may have freed up) while we waited for `_growth_lock`.
                for pooled in self._sessions:
                    if pooled.has_capacity(requested, tolerance):
                        pooled.users += 1
                        pooled.last_used = time.monotonic()
                        return pooled.session

            logger.debug(
                "Growing session pool for language=%s (requested capacity=%d, current size=%d)",
                self.language.value,
                requested,
                len(self._sessions),
            )
            pooled = await self.new()
            async with self._lock:
                if self._closed:
                    # Closed while we were opening. Don't hand out a
                    # session from (or add it to) a pool that's supposed
                    # to be dead. Close what we just opened instead.
                    await close_session(pooled.session)
                    self._semaphore.release()
                    raise RuntimeError(
                        f"Session pool for language={self.language.value!r} was closed while opening."
                    )
                pooled.users += 1
                pooled.last_used = time.monotonic()
                self._sessions.append(pooled)
            return pooled.session

    async def open(self) -> Session:
        """
        Ensure at least one session is open in this pool, without
        checking one out.

        :raises RuntimeError: If this pool is closed.
        """
        pooled = await self._get_or_create()
        return pooled.session

    async def release(self, session: Session) -> None:
        """
        Release a checkout from `acquire`, refreshing that specific
        session's idle clock.

        A no-op if this pool has since been closed. A call that
        checked a session out before shutdown began can still release it
        afterward without that raising, even though the session itself
        is already gone.

        :param session: The exact `Session` object `acquire` returned.
        :raises RuntimeError: If the pool is still open but `session` isn't
            one of its currently-tracked sessions (e.g. it was already
            closed and dropped by a reap), or its use-count would go
            negative. Either means a caller released something it never
            validly checked out from this (still-open) pool.
        """
        async with self._lock:
            if self._closed:
                return
            for pooled in self._sessions:
                if pooled.session is session:
                    if pooled.users == 0:
                        raise RuntimeError(
                            f"Session pool for language={self.language.value!r} "
                            f"reference count went negative for a tracked session."
                        )
                    pooled.users -= 1
                    pooled.last_used = time.monotonic()
                    return
        raise RuntimeError(
            f"Released a session not currently tracked by the language={self.language.value!r} pool "
            f"(already closed and reaped?)."
        )

    async def close_idle(self, idle_timeout: float) -> None:
        """
        Close every session in this pool that's unused and has been idle
        for at least `idle_timeout`, shrinking the pool session by session.

        A no-op if this pool is already closed.
        """
        if self._closed:
            return
        # Detach the sessions to close (under `_lock`) before actually
        # closing them (outside `_lock`), so slow `close_session` calls
        # can't block a concurrent `acquire`/`release`/`open` on this pool.
        now = time.monotonic()
        async with self._lock:
            keep: list[_PooledSession] = []
            to_close: list[_PooledSession] = []
            for pooled in self._sessions:
                if pooled.in_use or (now - pooled.last_used) < idle_timeout:
                    keep.append(pooled)
                else:
                    to_close.append(pooled)
            self._sessions = keep

        for pooled in to_close:
            logger.info(
                "Closing idle live session for language=%s (idle_timeout=%.1fs, pool size now %d)",
                self.language.value,
                idle_timeout,
                len(self._sessions),
            )
            await close_session(pooled.session)
            self._semaphore.release()

    async def close(self) -> None:
        """
        Close every session in this pool, regardless of use, and mark it
        closed. Further `acquire`/`open` calls raise `RuntimeError`
        (`release` stays safe; see its own docstring). For shutdown.

        Safe to call more than once; later calls are no-ops.
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
        for pooled in sessions:
            await close_session(pooled.session)
            self._semaphore.release()
