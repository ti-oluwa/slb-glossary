"""Resource lifecycle management for `slb_glossary.mcp`'s MCP application."""

import asyncio
import contextlib
import logging
import pathlib
import time
from collections.abc import AsyncIterator

from slb_glossary.config import DatabaseOptions
from slb_glossary.live.browser import Session, close_session, open_session
from slb_glossary.local.connection import close_db, open_db
from slb_glossary.local.types import Database
from slb_glossary.mcp.config import MCPConfig, SessionMode
from slb_glossary.mcp.errors import MCPError
from slb_glossary.mcp.session_pool import SessionPool
from slb_glossary.mcp.types import NamedComponent
from slb_glossary.query import Source
from slb_glossary.types import Language

logger = logging.getLogger(__name__)

__all__ = ["Runtime"]


def get_db_path(database_config: DatabaseOptions) -> str | None:
    """Extract the configured local database path, or `None` for the OS default."""
    if not database_config.data_dir:
        return None
    return str(pathlib.Path(database_config.data_dir) / database_config.db_filename)


class Runtime(NamedComponent):
    """
    Owns and manages the shared resources (`Database` and/or `Sessions`) for
    one running MCP application.

    Live sessions are pooled per language for `EAGER`/`LAZY` mode, since a `Session`
    is bound to one glossary language for its whole lifetime

    A `Runtime` asked to serve calls in more than one language needs one pool per
    language, not one session shared across all of them. `PER_CALL` mode does not use the
    pool map at all. It opens and closes a fresh session per call regardless of language, which
    is its whole point (see `acquire`'s docstring for when that isolation is worth the extra
    session opening cost pooling avoids).
    """

    def __init__(self, config: MCPConfig) -> None:
        super().__init__(config.server.name)
        self.config = config
        self._db: Database | None = None
        self._db_lock = asyncio.Lock()
        self._pools: dict[Language, SessionPool] = {}
        self._pools_lock = asyncio.Lock()
        self._session_semaphore = asyncio.Semaphore(config.session.max_sessions)
        self._reaper_task: asyncio.Task[None] | None = None
        self._started = False
        self._closed = False

    async def start(self) -> None:
        """
        Perform startup-time work. Opens a local DB connection (if enabled), eagerly
        opens a live session for the configured default language if
        `SessionMode.EAGER` is configured, and starts the idle-session
        reaper if `idle_timeout` is set.

        Safe to call more than once; later calls are no-ops.
        """
        if self._started:
            return
        self._started = True
        started_at = time.monotonic()

        if self.config.local.enabled:
            await self._open_db()

        if self.config.session.enabled and self.config.session.mode is SessionMode.EAGER:
            # Only the configured default language is warmed up here.
            # Any other language a call later asks for still gets its own
            # pool lazily.
            await self.open_session()

        if (
            self.config.session.enabled
            and self.config.session.mode is not SessionMode.PER_CALL
            and self.config.session.idle_timeout is not None
        ):
            self._reaper_task = asyncio.create_task(
                self._reap_idle_sessions(), name=f"{self.name}:session-reaper"
            )

        logger.info("[%s] Runtime started in %.3fs", self.name, time.monotonic() - started_at)

    async def aclose(self) -> None:
        """Tear down every resource this runtime opened. Safe to call more than once."""
        if self._closed:
            return
        self._closed = True
        started_at = time.monotonic()

        if self._reaper_task is not None:
            self._reaper_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reaper_task
            self._reaper_task = None

        async with self._pools_lock:
            pools = list(self._pools.values())
            self._pools.clear()
        for pool in pools:
            await pool.close()

        async with self._db_lock:
            if self._db is not None:
                await close_db(self._db)
                self._db = None

        logger.info("[%s] Runtime closed in %.3fs", self.name, time.monotonic() - started_at)

    async def _open_db(self) -> Database:
        async with self._db_lock:
            if self._db is None:
                opened_at = time.monotonic()
                self._db = await open_db(get_db_path(self.config.local.database))
                logger.info(
                    "[%s] Local database opened in %.3fs", self.name, time.monotonic() - opened_at
                )
            return self._db

    def resolve_language(self, language: str | Language | None) -> Language:
        """
        Resolve a per-call language request (or `None`, to use the
        configured default) to a `Language` member, used to pick which
        `SessionPool` a call gets routed to.

        :param language: A caller-requested language, e.g. from a tool's
            `language` argument, or `None` to use `self.config.session.options.language`.
        :raises MCPError: If `language` is a string that is not a valid `Language` value.
        """
        if language is None:
            return Language(self.config.session.options.language)
        if isinstance(language, Language):
            return language
        try:
            return Language(language)
        except ValueError as exc:
            choices = ", ".join(member.value for member in Language)
            raise MCPError(
                f"[{self.name}] Unknown language {language!r}. Expected one of: {choices}."
            ) from exc

    async def get_pool(self, language: Language) -> SessionPool:
        """
        Return the `SessionPool` for `language`, creating it on first request.

        A pool that's since gone idle and unused is closed and dropped
        by the reaper, so a language that hasn't been asked for in a while does
        not keep an entry around forever using up memory.

        This just recreates it, empty, the next time it's asked for.
        """
        async with self._pools_lock:
            pool = self._pools.get(language)
            if pool is None:
                pool = SessionPool(language, self.config.session.options, self._session_semaphore)
                self._pools[language] = pool
            return pool

    async def _reap_idle_sessions(self) -> None:
        """Background task. Closes each language pool's idle sessions after they've sat unused past `idle_timeout`."""
        idle_timeout = self.config.session.idle_timeout
        assert idle_timeout is not None, (
            f"[{self.name}] `_reap_idle_sessions` started with `idle_timeout=None`; "
            f"`{type(self).__name__}.start()` should never have scheduled this task in that case."
        )
        assert self.config.session.mode is not SessionMode.PER_CALL, (
            f"[{self.name}] `_reap_idle_sessions` started under `SessionMode.PER_CALL`, which never "
            f"maintains pooled sessions for it to reap; `{type(self).__name__}.start()` should never have "
            f"scheduled this task in that case."
        )
        try:
            while True:
                await asyncio.sleep(max(idle_timeout / 4, 5.0))
                await self.close_idle_sessions(idle_timeout)
        except asyncio.CancelledError:
            raise

    async def close_idle_sessions(self, idle_timeout: float) -> None:
        """
        Run one idle-session check/close cycle across every language pool.

        Each pool decides independently which of its own sessions (it
        may hold several) are unused and idle long enough to close
        (see `SessionPool.close_idle`); a pool left holding zero sessions afterward
        is dropped entirely, so a language that's stopped being requested does not
        keep an empty entry around forever.
        """
        async with self._pools_lock:
            pools = list(self._pools.items())

        for language, pool in pools:
            await pool.close_idle(idle_timeout)
            if pool.size == 0:
                async with self._pools_lock:
                    # Only drop it if it's still the exact same, still-empty
                    # pool. A concurrent `get_pool`/`acquire` could have
                    # reopened (or already replaced) it since the check above.
                    if self._pools.get(language) is pool and pool.size == 0:
                        del self._pools[language]

    async def open_db(self) -> Database:
        """
        Return the shared local `Database`, opening it on first use.

        Unlike `acquire`, this does not route through `Source` resolution.
        Meant for callers that always need a writable local database regardless
        of which `Source` a call otherwise resolves to.

        :raises MCPError: If this runtime's `MCPConfig.local.enabled` is `False`.
        """
        if not self.config.local.enabled:
            raise MCPError(f"[{self.name}] This server has local database access disabled.")
        return await self._open_db()

    async def open_session(self, language: str | Language | None = None) -> Session:
        """
        Return `language`'s pooled session (the configured default if
        omitted), opening it on first use, without checking it out.
        """
        pool = await self.get_pool(self.resolve_language(language))
        return await pool.open()

    @contextlib.asynccontextmanager
    async def acquire(
        self, source: Source, *, language: str | Language | None = None
    ) -> AsyncIterator[tuple[Database | None, Session | None]]:
        """
        Yield the `(db, session)` pair a tool call needs to satisfy `source`.

        Honours `SessionMode`. For `EAGER`/`LAZY`, `language` (the
        configured default if omitted) selects which language's
        `SessionPool` this call is routed to (see `get_pool`); a session
        is checked out from that pool for the duration of the caller's
        `async with` block (see `SessionPool.acquire`).

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
        server used by multiple untrusted or mutually-distrusting
        callers, albeit at the cost of a fresh browser session per call instead
        of reusing one. As of this glossary's current site (no login, no
        user-specific session data), that isolation usually is not needed
        day to day, but the mode stays available for a deployment or a
        future site change that does need it.

        Either way, `SessionAccess.max_sessions` bounds how many browser
        instances may be open at once system-wide, via a semaphore.
        For `PER_CALL`, a slot is held for the whole lifetime of that
        call's own session. For `EAGER`/`LAZY`, a slot is held for as
        long as one specific session (of however many a pool holds) is
        open, and acquired only when a pool actually launches a new browser,
        released only when that specific session closes.

        Caution should be taken when nesting. Do not call `acquire` again
        from inside an already open `acquire` block in the same task if
        doing so might need to open a new browser instance while `max_sessions` is
        already exhausted by the outer call holding its slot.
        The session semaphore is not reentrant, so that nested call would
        deadlock waiting on a slot its own outer call holds. This is a
        risk for `PER_CALL` (always opens fresh) and for `EAGER`/`LAZY`
        whenever every session in the target language's pool is full.

        :param source: The resolved `Source` this call needs resources for.
        :param language: Which glossary language's session this call
            needs, e.g. from a tool's own `language` argument. `None`
            uses the configured default (`SessionAccess.options.language`).
        :yield: A `(db, session)` tuple, either of which may be `None` if
            `source` does not require it.
        :raises MCPError: If `source` needs a resource this `Runtime` was not
            configured to provide (`local.enabled=False` for `Source.LOCAL`,
            `session.enabled=False` for `Source.LIVE`), or `language` is not
            a valid `Language` value.
        """
        needs_db = source in (Source.LOCAL, Source.AUTO)
        needs_session = source in (Source.LIVE, Source.AUTO)

        if source is Source.LOCAL and not self.config.local.enabled:
            raise MCPError(f"[{self.name}] This server has local database access disabled.")
        if source is Source.LIVE and not self.config.session.enabled:
            raise MCPError(f"[{self.name}] This server has live glossary access disabled.")

        resolved_language = self.resolve_language(language)
        db = await self._open_db() if (needs_db and self.config.local.enabled) else None
        if not needs_session or not self.config.session.enabled:
            yield db, None
            return

        if self.config.session.mode is SessionMode.PER_CALL:
            async with self._session_semaphore:
                opened_at = time.monotonic()
                kwargs = self.config.session.options.session_kwargs()
                kwargs["language"] = resolved_language
                # A session opened here is about to be used for this call's
                # live fetch, so there's no reason to defer initialization further.
                kwargs["initialize"] = True
                session = await open_session(**kwargs)
                logger.debug(
                    "[%s] Per-call session opened in %.3fs (language=%s)",
                    self.name,
                    time.monotonic() - opened_at,
                    resolved_language.value,
                )
                try:
                    yield db, session
                finally:
                    closed_at = time.monotonic()
                    await close_session(session)
                    logger.debug(
                        "[%s] Per-call session closed in %.3fs",
                        self.name,
                        time.monotonic() - closed_at,
                    )
            return

        pool = await self.get_pool(resolved_language)
        session = await pool.acquire()
        try:
            yield db, session
        finally:
            await pool.release(session)
