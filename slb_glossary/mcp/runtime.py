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

        if self.config.local.enabled:
            await self._open_db()

        if self.config.session.enabled and self.config.session.mode is SessionMode.EAGER:
            await self._open_session()

        if (
            self.config.session.enabled
            and self.config.session.mode is not SessionMode.PER_CALL
            and self.config.session.idle_timeout is not None
        ):
            self._reaper_task = asyncio.create_task(
                self._reap_idle_session(), name=f"{self.name}:session-reaper"
            )

        logger.info("[%s] Runtime started", self.name)

    async def aclose(self) -> None:
        """Tear down every resource this runtime opened. Safe to call more than once."""
        if self._closed:
            return
        self._closed = True

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

        logger.info("[%s] Runtime closed", self.name)

    async def open_local_db(self) -> Database:
        """
        Return the shared local `Database`, opening it on first use.

        Unlike `acquire`, this doesn't route through `Source` resolution.
        Meant for callers (like the `glossary_sync` tool) that always need a
        writable local database regardless of which `Source` a call
        otherwise resolves to.

        :raises MCPError: If this runtime's `MCPConfig.local.enabled` is `False`.
        """
        if not self.config.local.enabled:
            raise MCPError(f"[{self.name}] This server has local database access disabled.")
        return await self._open_db()

    async def _open_db(self) -> Database:
        async with self._db_lock:
            if self._db is None:
                self._db = await open_db(get_db_path(self.config.local.database))
            return self._db

    async def _open_session(self) -> Session:
        async with self._session_lock:
            if self._session is None:
                kwargs = self.config.session.options.session_kwargs()
                # Runtime only ever opens a session because a live call is
                # imminent (EAGER, at startup) or already in flight (LAZY/
                # PER_CALL, on first/every use). The decision to go live at
                # all has already been made by the time we get here, so
                # there's no reason to defer the topics/size load further.
                # This overrides whatever `initialize` session_kwargs()
                # otherwise resolved to (the global lazy-by-default, which
                # exists for `slb_glossary.query`'s own local-vs-live
                # choice, not for a runtime that's already committed to a
                # live session).
                kwargs["initialize"] = True
                self._session = await open_session(**kwargs)
            self._session_last_used = time.monotonic()
            return typing.cast(Session, self._session)

    async def _reap_idle_session(self) -> None:
        """Background task. Closes the shared session after it's sat idle past `idle_timeout`."""
        idle_timeout = self.config.session.idle_timeout
        assert idle_timeout is not None, (
            f"[{self.name}] `_reap_idle_session` started with idle_timeout=None; "
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
                async with self._session_lock:
                    if self._session is None:
                        continue
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
        except asyncio.CancelledError:
            raise

    @contextlib.asynccontextmanager
    async def acquire(
        self, source: Source
    ) -> AsyncIterator[tuple[Database | None, Session | None]]:
        """
        Yield the `(db, session)` pair a tool call needs to satisfy `source`.

        Honors `SessionMode`. For `PER_CALL`, a fresh session is opened for
        the duration of the `async with` block and closed on exit (bounded
        by `SessionAccess.max_concurrent` via a semaphore); for
        `EAGER`/`LAZY`, the shared session is reused (and lazily opened on
        first use, for `LAZY`).

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
                kwargs = self.config.session.options.session_kwargs()
                # A session opened here is about to be used for this call's
                # live fetch, so there's no reason to defer initialization further.
                kwargs["initialize"] = True
                session = await open_session(**kwargs)
                try:
                    yield db, session
                finally:
                    await close_session(session)
            return

        session = await self._open_session()
        yield db, session
