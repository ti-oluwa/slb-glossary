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

    def has_spare_capacity(self) -> bool:
        """
        Best-effort, non-blocking read on whether this session's page pool
        has room for one more concurrent checkout right now.

        This is a not a guarantee check as a page can free up (or get claimed
        by another checkout) immediately after this returns.

        This is just a cheap check used to decide whether reusing this session
        is worth trying before considering growing the pool with a new browser
        instance.
        """
        return self.session.pages.size < self.session.pages.max_size


class SessionPool:
    """
    Owns an elastic set of live `Session`s for one glossary `language`,
    to be used by the MCP `Runtime`.

    A `Session` is bound to one glossary language for its whole lifetime
    (see `slb_glossary.query.validate_language`), so a `Runtime` serving
    more than one language needs one pool per language.`Runtime` holds
    a `dict[Language, SessionPool]`, creating one of these per language
    actually requested.

    Within one language, `Session` is already designed to be driven
    concurrently. Each caller checks out its own page from `Session.pages`
    (bounded by `SessionOptions`' page-pool size), so several callers
    safely share one session without their work interfering with each
    other.

    What this pool adds on top is elasticity for when that's not enough.
    Once an existing session's page pool looks full, `acquire` opens an
    additional browser instance for this same language instead of
    queueing everyone behind the first one. But only up to the shared,
    Runtime-wide `semaphore` passed in at construction (see
    `SessionAccess.max_sessions`), which is what actually gates total
    system-wide resource use, not this pool on its own.

    Checkout/release/reap track each session's own use-count and idle
    clock independently (`_PooledSession`), so a language's pool grows
    under load and shrinks back down session by session, as sessions
    individually go idle and get reaped.
    """

    def __init__(
        self, language: Language, options: SessionOptions, semaphore: asyncio.Semaphore
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
        """
        self.language = language
        self.options = options
        self._semaphore = semaphore
        self._sessions: list[_PooledSession] = []
        self._lock = asyncio.Lock()
        self._growth_lock = asyncio.Lock()
        """
        Serializes the decision to open a new session beacuse every session look full.

        Without this, several concurrent callers that all find the pool
        full at once would each launch their own new browser instead of
        sharing the one that growth actually produces.
        """

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(language={self.language.value!r}, "
            f"sessions={len(self._sessions)})"
        )

    @property
    def size(self) -> int:
        """Number of sessions currently open in this pool."""
        return len(self._sessions)

    @property
    def in_use(self) -> bool:
        """`True` if any session in this pool has at least one active checkout right now."""
        return any(pooled.in_use for pooled in self._sessions)

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
            # instance, so it should not hold onto the slot.
            self._semaphore.release()
            raise
        logger.info(
            "Live session opened for language=%s in %.3fs (pool size now %d)",
            self.language.value,
            time.monotonic() - opened_at,
            len(self._sessions) + 1,
        )
        return _PooledSession(session)

    async def _get_or_create(self) -> _PooledSession:
        """
        Return a session with spare capacity, or grow the pool with a new
        browser instance if none currently has room.

        Doesn't bump the returned session's `users`. Callers should do that
        themselves only if this represents an actual checkout (`acquire`
        does; `open`, used for pre-warming, does not).
        """
        async with self._lock:
            for pooled in self._sessions:
                if pooled.has_spare_capacity():
                    pooled.last_used = time.monotonic()
                    return pooled

        # Every existing session looked full (or there were none yet).
        # Growth happens outside `_lock`, so checkouts of a different,
        # non-full session already in this pool, or a release, aren't
        # blocked behind a potentially slow browser launch; but under
        # `_growth_lock`, so concurrent callers that all found the pool
        # full do not each launch their own new browser.
        async with self._growth_lock:
            # Re-check as another caller may have already grown the pool
            # (or a session may have freed up) while we waited for `_growth_lock`.
            async with self._lock:
                for pooled in self._sessions:
                    if pooled.has_spare_capacity():
                        pooled.last_used = time.monotonic()
                        return pooled

            pooled = await self.new()
            async with self._lock:
                self._sessions.append(pooled)
            return pooled

    async def acquire(self) -> Session:
        """
        Check out a session for one caller, growing the pool if every
        existing session looks full.

        Pair with `release`, passing back the exact `Session` this returns.
        """
        pooled = await self._get_or_create()
        async with self._lock:
            pooled.users += 1
            pooled.last_used = time.monotonic()
        return pooled.session

    async def open(self) -> Session:
        """Ensure at least one session is open in this pool, without checking one out."""
        pooled = await self._get_or_create()
        return pooled.session

    async def release(self, session: Session) -> None:
        """
        Release a checkout from `acquire`, refreshing that specific
        session's idle clock.

        :param session: The exact `Session` object `acquire` returned.
        :raises RuntimeError: If `session` is not one of this pool's
            currently-tracked sessions (e.g. it was already closed and
            dropped by a reap), or its use-count would go negative,
            either means a caller released something it never validly
            checked out from this pool.
        """
        async with self._lock:
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
        """
        # Detach the sessions to close (under `_lock`) before actually
        # closing them (outside `_lock`), so slow `close_session` calls
        # can not block a concurrent `acquire`/`release`/`open` on this pool.
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
        """Unconditionally close every session in this pool, regardless of use. For shutdown."""
        async with self._lock:
            sessions = self._sessions
            self._sessions = []
        for pooled in sessions:
            await close_session(pooled.session)
            self._semaphore.release()
