"""Lifecycle, robustness and public-API tests for `slb_glossary.live.Runtime`/`SessionPool`."""

import asyncio
import typing

import pytest

from slb_glossary.config import Config, DatabaseOptions, SessionOptions
from slb_glossary.errors import (
    ResourceDisabledError,
    ResourceError,
    RuntimeClosedError,
    SessionPoolClosedError,
    SessionPoolError,
)
from slb_glossary.live import Runtime, SessionMode, SessionPool
from slb_glossary.live import runtime as runtime_module
from slb_glossary.types import Language, Source

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


@pytest.fixture
def anyio_backend(
    anyio_backend_asyncio_only: tuple[str, dict[str, typing.Any]],
) -> tuple[str, dict[str, typing.Any]]:
    return anyio_backend_asyncio_only


class MockPages:
    def __init__(self, max_size: int = 3) -> None:
        self.max_size = max_size
        self.size = 0


class MockBrowser:
    def __init__(self) -> None:
        self.connected = True

    def is_connected(self) -> bool:
        return self.connected


class MockSession:
    def __init__(self, max_pages: int = 3) -> None:
        self.pages = MockPages(max_pages)
        self.browser = MockBrowser()


class Harness:
    """Records mocked `open_session`/`close_session`/`open_db`/`close_db` calls."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, max_pages: int = 3) -> None:
        self.calls: list[str] = []
        self.sessions: list[MockSession] = []
        self.close_error: Exception | None = None
        self.db_close_error: Exception | None = None
        self.open_error: Exception | None = None
        self.max_pages = max_pages

        async def mock_open_session(**kwargs: object) -> MockSession:
            if self.open_error is not None:
                raise self.open_error
            self.calls.append("open")
            session = MockSession(self.max_pages)
            self.sessions.append(session)
            return session

        async def mock_close_session(session: object) -> None:
            self.calls.append("close")
            if self.close_error is not None:
                raise self.close_error

        async def mock_open_db(path: object) -> object:
            self.calls.append("open_db")
            return object()

        async def mock_close_db(db: object) -> None:
            self.calls.append("close_db")
            if self.db_close_error is not None:
                raise self.db_close_error

        monkeypatch.setattr(runtime_module, "open_session", mock_open_session)
        monkeypatch.setattr(runtime_module, "close_session", mock_close_session)
        monkeypatch.setattr(runtime_module, "open_db", mock_open_db)
        monkeypatch.setattr(runtime_module, "close_db", mock_close_db)


def make_pool(
    harness: Harness, *, semaphore: asyncio.Semaphore | None = None, tolerance: int = 1
) -> SessionPool:
    return SessionPool(
        Language.ENGLISH,
        SessionOptions(),
        semaphore or asyncio.Semaphore(5),
        capacity_tolerance=tolerance,
    )


class TestPoolRobustness:
    async def test_dead_browser_is_dropped_and_replaced(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A session whose browser crashed is never handed out again."""
        harness = Harness(monkeypatch)
        pool = make_pool(harness)
        first = await pool.acquire()
        await pool.release(first)
        first.browser.connected = False  # type: ignore[attr-defined]

        second = await pool.acquire()
        assert second is not first
        assert harness.calls == ["open", "close", "open"]
        assert pool.size == 1

    async def test_close_idle_also_drops_dead_sessions_regardless_of_idle_time(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = Harness(monkeypatch)
        pool = make_pool(harness)
        session = await pool.acquire()
        await pool.release(session)
        session.browser.connected = False  # type: ignore[attr-defined]
        assert await pool.close_idle(idle_timeout=3600.0) == 1
        assert pool.size == 0

    async def test_most_free_session_is_preferred(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Load is spread: a caller gets the session with the most free pages, not the first."""
        harness = Harness(monkeypatch, max_pages=3)
        pool = make_pool(harness)
        busy = await pool.acquire()
        busy.pages.size = 2  # type: ignore[attr-defined]
        # Force a second session to exist (explicit growth).
        spare = (await pool.new()).session

        assert await pool.acquire() is spare
        del busy

    async def test_checkout_releases_even_if_the_body_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = Harness(monkeypatch)
        pool = make_pool(harness)
        with pytest.raises(ValueError, match="boom"):
            async with pool.checkout() as session:
                assert pool.in_use
                raise ValueError("boom")
        assert not pool.in_use
        assert await pool.close_idle(idle_timeout=0.0) == 1
        del session

    async def test_stats_snapshot(self, monkeypatch: pytest.MonkeyPatch) -> None:
        harness = Harness(monkeypatch)
        pool = make_pool(harness)
        session = await pool.acquire()
        stats = pool.stats()
        assert (stats.sessions, stats.busy_sessions, stats.checkouts) == (1, 1, 1)
        assert stats.language is Language.ENGLISH and not stats.closed
        await pool.release(session)
        assert pool.stats().busy_sessions == 0

    async def test_pool_is_not_empty_while_a_session_is_opening(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pool mid-launch must not look retirable (the reaper would close it under the caller)."""
        harness = Harness(monkeypatch)
        gate = asyncio.Event()
        real_open = runtime_module.open_session

        async def slow_open(**kwargs: object) -> object:
            await gate.wait()
            return await real_open(**kwargs)

        monkeypatch.setattr(runtime_module, "open_session", slow_open)
        pool = make_pool(harness)
        task = asyncio.create_task(pool.acquire())
        await asyncio.sleep(0.02)
        assert pool.size == 0 and not pool.empty
        gate.set()
        await task
        assert not pool.empty

    async def test_errors_are_runtime_errors_for_backward_compatibility(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = Harness(monkeypatch)
        pool = make_pool(harness)
        await pool.close()
        with pytest.raises(RuntimeError):
            await pool.acquire()
        with pytest.raises(SessionPoolClosedError):
            await pool.new()
        other = make_pool(harness)
        await other.acquire()
        with pytest.raises(SessionPoolError):
            await other.release(MockSession())  # type: ignore[arg-type]


class TestRuntimeConstruction:
    def test_validates_arguments(self) -> None:
        with pytest.raises(ValueError, match="max_sessions"):
            Runtime(max_sessions=0)
        with pytest.raises(ValueError, match="capacity_tolerance"):
            Runtime(capacity_tolerance=-1)
        with pytest.raises(ValueError, match="idle_timeout"):
            Runtime(idle_timeout=0)
        with pytest.raises(ValueError):
            Runtime(mode="sometimes")

    def test_mode_accepts_its_string_value(self) -> None:
        assert Runtime(mode="per_call").mode is SessionMode.PER_CALL

    def test_local_enabled_defaults_to_the_database_options(self) -> None:
        assert Runtime(database_options=DatabaseOptions(enabled=False)).local_enabled is False
        assert Runtime(
            database_options=DatabaseOptions(enabled=False), local_enabled=True
        ).local_enabled

    def test_from_config_uses_its_session_and_local_sections_and_takes_overrides(self) -> None:
        config = Config()
        runtime = Runtime.from_config(config, mode=SessionMode.EAGER, max_sessions=3)
        assert runtime.session_options is config.session
        assert runtime.database_options is config.local
        assert runtime.mode is SessionMode.EAGER and runtime.max_sessions == 3


class TestRuntimeLifecycle:
    async def test_async_context_manager_starts_and_closes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = Harness(monkeypatch)
        async with Runtime(mode=SessionMode.EAGER, idle_timeout=None) as runtime:
            assert runtime.started and not runtime.closed
            assert harness.calls == ["open_db", "open"]
            assert runtime.stats().open_sessions == 1
        assert runtime.closed
        assert harness.calls == ["open_db", "open", "close", "close_db"]

    async def test_session_helper_yields_a_live_session_without_source(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = Harness(monkeypatch)
        runtime = Runtime(local_enabled=False, idle_timeout=None)
        async with runtime.session("en", capacity=2) as session:
            assert session is harness.sessions[0]
            assert runtime.stats().pools[0].checkouts == 1
        assert runtime.stats().pools[0].checkouts == 0
        await runtime.close()

    async def test_closed_runtime_refuses_work(self, monkeypatch: pytest.MonkeyPatch) -> None:
        Harness(monkeypatch)
        runtime = Runtime()
        await runtime.close()
        with pytest.raises(RuntimeClosedError):
            async with runtime.acquire(Source.LIVE):
                pass
        with pytest.raises(ResourceError):
            await runtime.start()

    async def test_disabled_resources_raise_resource_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Harness(monkeypatch)
        runtime = Runtime(local_enabled=False, live_enabled=False)
        with pytest.raises(ResourceDisabledError):
            async with runtime.acquire(Source.LOCAL):
                pass
        with pytest.raises(ResourceDisabledError):
            async with runtime.session():
                pass
        with pytest.raises(ResourceDisabledError):
            await runtime.open_db()
        async with runtime.acquire(Source.AUTO) as (db, session):
            assert db is None and session is None

    async def test_close_closes_everything_even_if_a_pool_fails_to_close(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One browser failing to close must not leak the other pools or the database."""
        harness = Harness(monkeypatch)
        runtime = Runtime(idle_timeout=None, max_sessions=3)
        await runtime.start()
        async with runtime.acquire(Source.AUTO, language="en"):
            pass
        async with runtime.acquire(Source.LIVE, language="es"):
            pass
        harness.close_error = RuntimeError("browser exploded")

        with pytest.raises(SessionPoolError):
            await runtime.close()
        assert harness.calls.count("close") == 2  # both pools were attempted
        assert harness.calls[-1] == "close_db"  # and the database was still closed
        assert runtime.closed

    async def test_failed_start_releases_what_it_opened(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = Harness(monkeypatch)
        harness.open_error = RuntimeError("no browser")
        runtime = Runtime(mode=SessionMode.EAGER)
        with pytest.raises(RuntimeError, match="no browser"):
            await runtime.start()
        assert runtime.closed
        assert harness.calls == ["open_db", "close_db"]

    async def test_stats_counts_pooled_and_per_call_sessions(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Harness(monkeypatch)
        runtime = Runtime(local_enabled=False, mode=SessionMode.PER_CALL, max_sessions=2)
        async with runtime.acquire(Source.LIVE):
            stats = runtime.stats()
            assert stats.per_call_sessions == 1 and stats.open_sessions == 1
        assert runtime.stats().per_call_sessions == 0
        await runtime.close()


class TestRuntimeReaper:
    async def test_reaper_survives_a_failing_cycle(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An exception in one reaping cycle must not kill the reaper for good."""
        Harness(monkeypatch)
        runtime = Runtime(local_enabled=False, idle_timeout=0.01)
        cycles = 0

        async def flaky_close_idle(idle_timeout: float | None = None) -> int:
            nonlocal cycles
            cycles += 1
            if cycles == 1:
                raise RuntimeError("transient")
            return 0

        monkeypatch.setattr(runtime, "close_idle_sessions", flaky_close_idle)
        real_sleep = asyncio.sleep

        async def instant_sleep(_: float) -> None:
            await real_sleep(0)

        monkeypatch.setattr(runtime_module.asyncio, "sleep", instant_sleep)
        task = asyncio.create_task(runtime._reap_idle_sessions())
        for _ in range(50):
            await asyncio.sleep(0)
            if cycles >= 3:
                break
        assert cycles >= 3 and not task.done()
        task.cancel()

    async def test_pool_retired_by_the_reaper_mid_lookup_is_transparently_replaced(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        A caller that looked a pool up just before the reaper retired it gets a fresh one
        instead of an error, and no session is opened in a pool nothing tracks any more.
        """
        harness = Harness(monkeypatch)
        runtime = Runtime(local_enabled=False, idle_timeout=None)
        stale = await runtime.get_session_pool(Language.ENGLISH)
        await runtime.close_idle_sessions(0.0)  # retires the (empty) pool
        assert stale.closed and Language.ENGLISH not in runtime._pools

        real_get = runtime.get_session_pool
        lookups = 0

        async def stale_once(language: Language) -> SessionPool:
            nonlocal lookups
            lookups += 1
            return stale if lookups == 1 else await real_get(language)

        monkeypatch.setattr(runtime, "get_session_pool", stale_once)
        async with runtime.acquire(Source.LIVE) as (_, session):
            assert session is harness.sessions[0]
        assert lookups == 2
        assert runtime._pools[Language.ENGLISH] is not stale
        await runtime.close()
        assert harness.calls == ["open", "close"]  # nothing leaked

    async def test_default_budget_does_not_hang_when_the_only_session_looks_full(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        Regression: with `max_sessions=1`, a second concurrent call asking for more pages
        than the session has free used to wait for the idle timeout (5 minutes by default).
        """
        harness = Harness(monkeypatch, max_pages=2)
        runtime = Runtime(local_enabled=False, max_sessions=1)
        async with runtime.acquire(Source.LIVE, capacity=2) as (_, first):
            first.pages.size = 2  # type: ignore[union-attr]
            async with asyncio.timeout(1.0) if hasattr(asyncio, "timeout") else _noop():
                async with runtime.acquire(Source.LIVE, capacity=2) as (_, second):
                    assert second is first
        assert harness.calls == ["open"]
        await runtime.close()


class _noop:
    async def __aenter__(self) -> None: ...
    async def __aexit__(self, *exc: object) -> None: ...
