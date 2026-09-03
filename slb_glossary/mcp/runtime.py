"""Resource lifecycle management for `slb_glossary.mcp`'s MCP application."""

import asyncio
import contextlib
import logging
import pathlib
import time
import typing
from collections.abc import AsyncIterator

from slb_glossary.config import DatabaseOptions
from slb_glossary.live.browser import Session, close_session, open_session
from slb_glossary.local.connection import close_db, open_db
from slb_glossary.local.types import Database
from slb_glossary.mcp.config import MCPConfig, SessionMode
from slb_glossary.mcp.errors import MCPError
from slb_glossary.mcp.types import NamedComponent
from slb_glossary.query import Source

logger = logging.getLogger(__name__)

__all__ = ["Runtime"]


def get_db_path(database_config: DatabaseOptions) -> str | None:
    """Extract the configured local database path, or `None` for the OS default."""
    if not database_config.data_dir:
        return None
    return str(pathlib.Path(database_config.data_dir) / database_config.db_filename)


class Runtime(NamedComponent):
    """
    Owns and manages the shared resources (`Database`/`Session`) for
    one running MCP application.
    """

    def __init__(self, config: MCPConfig) -> None:
        super().__init__(config.server.name)
        self.config = config
        self._db: Database | None = None
        self._db_lock = asyncio.Lock()
        self._session: Session | None = None
        self._session_lock = asyncio.Lock()
        self._session_semaphore = asyncio.Semaphore(config.session.max_concurrent)
        self._session_last_used: float = 0.0
        self._session_users: int = 0
        self._reaper_task: asyncio.Task[None] | None = None
        self._started = False
        self._closed = False

    async def start(self) -> None:
        """
        Perform startup-time work. Opens a local DB connection (if enabled), eagerly
        open the live session if `SessionMode.EAGER` is configured, and start the
        idle-session reaper if `idle_timeout` is set.

        Safe to call more than once; later calls are no-ops.
        """
        if self._started:
            return
        self._started = True
        started_at = time.monotonic()

        if self.config.local.enabled:
            await self._open_db()

        if self.config.session.enabled and self.config.session.mode is SessionMode.EAGER:
            await self.open_session()

        if (
            self.config.session.enabled
            and self.config.session.mode is not SessionMode.PER_CALL
            and self.config.session.idle_timeout is not None
        ):
            self._reaper_task = asyncio.create_task(
                self._reap_idle_session(), name=f"{self.name}:session-reaper"
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

        async with self._session_lock:
            if self._session is not None:
                await close_session(self._session)
                self._session = None

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

    async def _open_session(self) -> Session:
        """
        Open the shared session on first use and return it, refreshing
        `_session_last_used`.

        Callers must already hold `_session_lock`.
        """
        if self._session is None:
            opened_at = time.monotonic()
            kwargs = self.config.session.options.session_kwargs()
            # Runtime only ever opens a session because a live call is
            # imminent (`EAGER`, at startup) or already in flight (`LAZY`/
            # `PER_CALL`, on first/every use). The decision to go live at
            # all has already been made by the time we get here, so
            # there's no reason to defer the topics/size load further.
            # This overrides whatever `initialize` value `session_kwargs()`
            # otherwise resolved to (the global lazy-by-default, which
            # exists for `slb_glossary.query`'s own local-vs-live
            # choice, not for a runtime that's already committed to a
            # live session).
            kwargs["initialize"] = True
            self._session = await open_session(**kwargs)
            logger.info(
                "[%s] Live session opened in %.3fs (mode=%s)",
                self.name,
                time.monotonic() - opened_at,
                self.config.session.mode.value,
            )
        self._session_last_used = time.monotonic()
        return typing.cast(Session, self._session)

    async def _acquire_session(self) -> Session:
        """
        Open the shared session (if needed) and check it out for one caller.

        `_session_lock` is held only long enough to open the session and
        bump `_session_users`, not for the caller's whole use of it.

        `Session` is explicitly designed to be driven concurrently (each
        caller checks out its own page from `session.pages`, bounded by
        `Session.max_pages`, so holding one exclusive lock across
        every call would wrongly serialize that.

        Pair with `_release_session`.
        """
        async with self._session_lock:
            session = await self._open_session()
            self._session_users += 1
            return session

    async def _release_session(self) -> None:
        """
        Release one checkout from `_acquire_session`, refreshing `_session_last_used`.
        """
        async with self._session_lock:
            self._session_users -= 1
            self._session_last_used = time.monotonic()
            if self._session_users < 0:
                # This path should be unreachable as every `_acquire_session` is paired
                # with exactly one `_release_session` in `acquire`'s
                # `try`/`finally`. But we guard against it anyway rather than
                # letting the count go negative and permanently fool the
                # reaper into thinking the session is still in use one
                # call fewer than it really is.
                self._session_users = 0
                raise RuntimeError(f"[{self.name}] MCP session reference count went negative.")

    async def _reap_idle_session(self) -> None:
        """
        Background task.

        Closes the shared session after it's sat idle past `idle_timeout`
        with no active user.
        """
        idle_timeout = self.config.session.idle_timeout
        assert idle_timeout is not None, (
            f"[{self.name}] `_reap_idle_session` started with `idle_timeout=None`; "
            f"`{type(self).__name__}.start()` should never have scheduled this task in that case."
        )
        assert self.config.session.mode is not SessionMode.PER_CALL, (
            f"[{self.name}] `_reap_idle_session` started under `SessionMode.PER_CALL`, which never "
            f"maintains a shared session for it to reap; `{type(self).__name__}.start()` should never have "
            f"scheduled this task in that case."
        )
        try:
            while True:
                await asyncio.sleep(max(idle_timeout / 4, 5.0))
                await self.close_idle_session(idle_timeout)
        except asyncio.CancelledError:
            raise

    async def close_idle_session(self, idle_timeout: float) -> None:
        """
        Run one idle-session check/close cycle.

        A session may only be closed/reaped when it exists, is unused
        (`_session_users == 0`), and has sat idle for at least `idle_timeout`.

        All three are checked under `_session_lock`, the same lock
        `_acquire_session`/`_release_session` use to update
        `_session_users`/`_session_last_used`, so this can never observe
        a call's checkout/release half-done.
        """
        async with self._session_lock:
            if self._session is None or self._session_users > 0:
                return
            idle_for = time.monotonic() - self._session_last_used
            if idle_for >= idle_timeout:
                logger.info(
                    "[%s] Closing idle live session after %.1fs (idle_timeout=%.1fs)",
                    self.name,
                    idle_for,
                    idle_timeout,
                )
                await close_session(self._session)
                self._session = None

    async def open_db(self) -> Database:
        """
        Return the shared local `Database`, opening it on first use.

        Unlike `acquire`, this doesn't route through `Source` resolution.
        Meant for callers that always need a writable local database regardless
        of which `Source` a call otherwise resolves to.

        :raises MCPError: If this runtime's `MCPConfig.local.enabled` is `False`.
        """
        if not self.config.local.enabled:
            raise MCPError(f"[{self.name}] This server has local database access disabled.")
        return await self._open_db()

    async def open_session(self) -> Session:
        async with self._session_lock:
            return await self._open_session()

    @contextlib.asynccontextmanager
    async def acquire(
        self, source: Source
    ) -> AsyncIterator[tuple[Database | None, Session | None]]:
        """
        Yield the `(db, session)` pair a tool call needs to satisfy `source`.

        Honours `SessionMode`. For `PER_CALL`, a fresh session is opened for
        the duration of the `async with` block and closed on exit (bounded
        by `SessionAccess.max_concurrent` via a semaphore).

        For `EAGER`/`LAZY`, the shared session is reused (and lazily opened on
        first use, for `LAZY`) and checked out via `_acquire_session`/`_release_session`
        for the duration of the caller's `async with` block.

        That checkout is deliberately not exclusive as concurrent `EAGER`/`LAZY`
        calls all share the one session object, each checking out its own page
        internally (see `Session.max_pages`), the same way `PER_CALL`
        calls run concurrently against their own, separate sessions.

        What the checkout does guarantee is that the idle-session reaper
        (`_reap_idle_session`/`close_idle_session`) can never close the session
        while any call still holds a checkout on it. It only reaps when
        `_session_users == 0`.

        :param source: The resolved `Source` this call needs resources for.
        :yield: A `(db, session)` tuple, either of which may be `None` if
            `source` doesn't require it.
        :raises MCPError: If `source` needs a resource this `Runtime` wasn't
            configured to provide (`local.enabled=False` for `Source.LOCAL`,
            `session.enabled=False` for `Source.LIVE`).
        """
        needs_db = source in (Source.LOCAL, Source.AUTO)
        needs_session = source in (Source.LIVE, Source.AUTO)

        if source is Source.LOCAL and not self.config.local.enabled:
            raise MCPError(f"[{self.name}] This server has local database access disabled.")
        if source is Source.LIVE and not self.config.session.enabled:
            raise MCPError(f"[{self.name}] This server has live glossary access disabled.")

        db = await self._open_db() if (needs_db and self.config.local.enabled) else None
        if not needs_session or not self.config.session.enabled:
            yield db, None
            return

        if self.config.session.mode is SessionMode.PER_CALL:
            async with self._session_semaphore:
                opened_at = time.monotonic()
                kwargs = self.config.session.options.session_kwargs()
                # A session opened here is about to be used for this call's
                # live fetch, so there's no reason to defer initialization further.
                kwargs["initialize"] = True
                session = await open_session(**kwargs)
                logger.debug(
                    "[%s] Per-call session opened in %.3fs",
                    self.name,
                    time.monotonic() - opened_at,
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

        session = await self._acquire_session()
        try:
            yield db, session
        finally:
            await self._release_session()
