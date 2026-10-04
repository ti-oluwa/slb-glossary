"""
Tests for `slb_glossary.live.Runtime`.

The pool's own acquire/release/growth/reap behaviour is covered in `test_session_pool.py`;
these tests focus on what `Runtime` adds on top, like routing a call to the right language's pool,
`PER_CALL` mode's separate, pool-free path, the shared browser budget, and its lifecycle.

Uses `anyio_backend_asyncio_only`: `Runtime` itself is built on raw
`asyncio.Lock`/`asyncio.Semaphore`/`asyncio.Task`, not anyio-portable primitives.
"""

import asyncio
import typing

import pytest

from slb_glossary.config import Config, DatabaseOptions
from slb_glossary.errors import (
    ResourceDisabledError,
    ResourceError,
    RuntimeClosedError,
    SessionPoolError,
    UnknownLanguageError,
)
from slb_glossary.live import Runtime, SessionMode, SessionPool
from slb_glossary.live import runtime as runtime_module
from slb_glossary.types import Language, Source
from tests.factories import make_runtime
from tests.mocks import MockLauncher, MockSession

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


@pytest.fixture
def anyio_backend(
    anyio_backend_asyncio_only: tuple[str, dict[str, typing.Any]],
) -> tuple[str, dict[str, typing.Any]]:
    return anyio_backend_asyncio_only


def pooled_languages(runtime: Runtime) -> set[Language]:
    """The languages `runtime` currently holds a session pool for."""
    return {pool.language for pool in runtime.stats().pools}


class TestConstruction:
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

    def test_local_enabled_overrides_the_database_options(self) -> None:
        runtime = Runtime(database_options=DatabaseOptions(enabled=False), local_enabled=True)
        assert runtime.local_enabled is True

    def test_from_config_uses_its_session_and_local_sections_and_takes_overrides(self) -> None:
        config = Config()
        runtime = Runtime.from_config(config, mode=SessionMode.EAGER, max_sessions=3)
        assert runtime.session_options is config.session
        assert runtime.database_options is config.local
        assert runtime.mode is SessionMode.EAGER
        assert runtime.max_sessions == 3


class TestLanguageRouting:
    async def test_default_language_used_when_none_requested(
        self, mock_launcher: MockLauncher
    ) -> None:
        runtime = make_runtime()
        async with runtime.acquire(Source.LIVE) as (_, session):
            assert session is not None
        assert mock_launcher.calls == ["open"]
        assert pooled_languages(runtime) == {Language.ENGLISH}

    async def test_different_languages_get_different_pools_and_sessions(
        self, mock_launcher: MockLauncher
    ) -> None:
        runtime = make_runtime()
        async with runtime.acquire(Source.LIVE, language="en") as (_, en_session):
            assert en_session is not None
        async with runtime.acquire(Source.LIVE, language="es") as (_, es_session):
            assert es_session is not None
        assert en_session is not es_session
        assert mock_launcher.calls.count("open") == 2
        assert pooled_languages(runtime) == {Language.ENGLISH, Language.SPANISH}

    async def test_same_language_reuses_the_pool(self, mock_launcher: MockLauncher) -> None:
        runtime = make_runtime()
        async with runtime.acquire(Source.LIVE, language="es"):
            pass
        async with runtime.acquire(Source.LIVE, language="es"):
            pass
        assert mock_launcher.calls.count("open") == 1

    async def test_unknown_language_string_raises(self, mock_launcher: MockLauncher) -> None:
        runtime = make_runtime()
        with pytest.raises(UnknownLanguageError, match="fr"):
            async with runtime.acquire(Source.LIVE, language="fr"):
                pass

    async def test_open_session_opens_the_requested_languages_pool(
        self, mock_launcher: MockLauncher
    ) -> None:
        runtime = make_runtime()
        assert await runtime.open_session(language="es") is not None
        assert mock_launcher.calls == ["open"]
        [pool] = runtime.stats().pools
        assert pool.language is Language.SPANISH
        assert pool.checkouts == 0

    async def test_session_helper_yields_a_live_session_without_source(
        self, mock_launcher: MockLauncher
    ) -> None:
        runtime = make_runtime()
        async with runtime.session("en", capacity=2) as session:
            assert session is mock_launcher.sessions[0]
            assert runtime.stats().pools[0].checkouts == 1
        assert runtime.stats().pools[0].checkouts == 0


class TestReapAndSemaphoreWiring:
    async def test_reap_is_a_no_op_for_a_pool_still_in_use(
        self, mock_launcher: MockLauncher
    ) -> None:
        runtime = make_runtime()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def long_call() -> None:
            async with runtime.acquire(Source.LIVE) as (_, session):
                assert session is not None
                entered.set()
                await release.wait()

        task = asyncio.create_task(long_call())
        await entered.wait()

        await runtime.close_idle_sessions(idle_timeout=0.0)
        assert mock_launcher.calls == ["open"], "reap closed a session a call still held"

        release.set()
        await task
        await runtime.close_idle_sessions(idle_timeout=0.0)
        assert mock_launcher.calls == ["open", "close"]

    async def test_emptied_pool_is_dropped_from_the_map(self, mock_launcher: MockLauncher) -> None:
        runtime = make_runtime()
        async with runtime.acquire(Source.LIVE, language="es"):
            pass
        assert pooled_languages(runtime) == {Language.SPANISH}

        await runtime.close_idle_sessions(idle_timeout=0.0)

        assert mock_launcher.calls == ["open", "close"]
        assert pooled_languages(runtime) == set()

    async def test_max_sessions_bounds_total_browser_instances_across_languages(
        self, mock_launcher: MockLauncher, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The semaphore `Runtime` hands each pool is genuinely shared, not per-language."""
        monkeypatch.setattr(runtime_module, "RECLAIM_POLL_INTERVAL", 0.02)
        runtime = make_runtime(max_sessions=1)

        async with runtime.acquire(Source.LIVE, language="en"):
            # "en" is busy and holds the only slot, so "es" has to wait.
            open_es_task = asyncio.create_task(runtime.open_session(language="es"))
            await asyncio.sleep(0.1)
            assert not open_es_task.done(), (
                "a second language should not open while the first still holds the only slot"
            )
            assert mock_launcher.calls == ["open"]

        # Once "en" is released it is idle, so "es" takes its slot without waiting for
        # the idle timeout to run out.
        assert await asyncio.wait_for(open_es_task, timeout=1.0) is not None
        assert mock_launcher.calls == ["open", "close", "open"]

    async def test_idle_session_of_another_language_is_reclaimed_for_a_new_language(
        self, mock_launcher: MockLauncher
    ) -> None:
        """
        Switching languages under `max_sessions=1` does not wait out the idle timeout (it
        used to, i.e. 5 minutes by default) when the old language's session is idle.
        """
        runtime = make_runtime(max_sessions=1)
        async with runtime.acquire(Source.LIVE, language="en"):
            pass

        async def acquire_spanish() -> MockSession:
            async with runtime.acquire(Source.LIVE, language="es") as (_, session):
                return typing.cast(MockSession, session)

        assert await asyncio.wait_for(acquire_spanish(), timeout=1.0) is not None
        assert mock_launcher.calls == ["open", "close", "open"]
        assert pooled_languages(runtime) == {Language.SPANISH}

    async def test_default_budget_does_not_hang_when_the_only_session_looks_full(
        self, mock_launcher: MockLauncher
    ) -> None:
        """
        Regression: with `max_sessions=1`, a second concurrent call asking for more pages
        than the session has free used to wait for the idle timeout (5 minutes by default).
        """
        mock_launcher.max_pages = 2
        runtime = make_runtime(max_sessions=1)

        async def second_call(first: object) -> object:
            async with runtime.acquire(Source.LIVE, capacity=2) as (_, second):
                return second

        async with runtime.acquire(Source.LIVE, capacity=2) as (_, first):
            mock_launcher.sessions[0].pages.fill()
            assert await asyncio.wait_for(second_call(first), timeout=1.0) is first
        assert mock_launcher.calls == ["open"]

    async def test_pool_retired_by_the_reaper_mid_lookup_is_transparently_replaced(
        self, mock_launcher: MockLauncher, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        A caller that looked a pool up just before the reaper retired it gets a fresh one
        instead of an error, and no session is opened in a pool nothing tracks any more.
        """
        runtime = make_runtime()
        stale = await runtime.get_session_pool(Language.ENGLISH)
        await runtime.close_idle_sessions(0.0)  # retires the (empty) pool
        assert stale.closed
        assert pooled_languages(runtime) == set()

        real_get = runtime.get_session_pool
        lookups = 0

        async def stale_once(language: Language) -> SessionPool:
            nonlocal lookups
            lookups += 1
            return stale if lookups == 1 else await real_get(language)

        monkeypatch.setattr(runtime, "get_session_pool", stale_once)
        async with runtime.acquire(Source.LIVE) as (_, session):
            assert session is mock_launcher.sessions[0]
        assert lookups == 2
        await runtime.close()
        assert mock_launcher.calls == ["open", "close"]  # nothing leaked


class TestPerCallMode:
    async def test_opens_a_fresh_session_and_always_closes_it(
        self, mock_launcher: MockLauncher
    ) -> None:
        runtime = make_runtime(mode=SessionMode.PER_CALL)
        async with runtime.acquire(Source.LIVE, language="es") as (_, session):
            assert session is not None
        assert mock_launcher.calls == ["open", "close"]
        assert pooled_languages(runtime) == set()

    async def test_exception_inside_still_closes_the_session(
        self, mock_launcher: MockLauncher
    ) -> None:
        runtime = make_runtime(mode=SessionMode.PER_CALL)
        with pytest.raises(RuntimeError):
            async with runtime.acquire(Source.LIVE):
                raise RuntimeError("boom")
        assert mock_launcher.calls == ["open", "close"]

    async def test_repeated_calls_each_get_their_own_session(
        self, mock_launcher: MockLauncher
    ) -> None:
        runtime = make_runtime(mode=SessionMode.PER_CALL)
        async with runtime.acquire(Source.LIVE):
            pass
        async with runtime.acquire(Source.LIVE):
            pass
        assert mock_launcher.calls == ["open", "close", "open", "close"]

    async def test_capacity_raises_the_sessions_page_pool(
        self, mock_launcher: MockLauncher
    ) -> None:
        runtime = make_runtime(mode=SessionMode.PER_CALL)
        async with runtime.acquire(Source.LIVE, capacity=7):
            pass
        assert mock_launcher.open_kwargs[0]["max_pages"] >= 7

    async def test_bounds_concurrency_runtime_wide(self, mock_launcher: MockLauncher) -> None:
        runtime = make_runtime(mode=SessionMode.PER_CALL, max_sessions=2)
        peak_open = 0
        currently_open = 0
        release = asyncio.Event()

        async def one_call(language: str) -> None:
            nonlocal peak_open, currently_open
            async with runtime.acquire(Source.LIVE, language=language):
                currently_open += 1
                peak_open = max(peak_open, currently_open)
                await release.wait()
                currently_open -= 1

        tasks = [asyncio.create_task(one_call(language)) for language in ("en", "en", "es", "es")]
        await asyncio.sleep(0.05)
        assert peak_open == 2
        assert runtime.stats().per_call_sessions == 2
        release.set()
        await asyncio.gather(*tasks)
        assert mock_launcher.calls.count("open") == 4
        assert mock_launcher.calls.count("close") == 4
        assert runtime.stats().per_call_sessions == 0


class TestLifecycle:
    async def test_async_context_manager_starts_and_closes(
        self, mock_launcher: MockLauncher
    ) -> None:
        async with Runtime(mode=SessionMode.EAGER, idle_timeout=None) as runtime:
            assert runtime.started
            assert not runtime.closed
            assert mock_launcher.calls == ["open_db", "open"]
            assert runtime.stats().open_sessions == 1
        assert runtime.closed
        assert mock_launcher.calls == ["open_db", "open", "close", "close_db"]

    async def test_close_closes_every_language_pool(self, mock_launcher: MockLauncher) -> None:
        runtime = make_runtime()
        async with runtime.acquire(Source.LIVE, language="en"):
            pass
        async with runtime.acquire(Source.LIVE, language="es"):
            pass

        await runtime.close()

        assert mock_launcher.calls.count("open") == 2
        assert mock_launcher.calls.count("close") == 2
        assert pooled_languages(runtime) == set()

    async def test_closed_runtime_refuses_work(self, mock_launcher: MockLauncher) -> None:
        runtime = make_runtime()
        await runtime.close()
        with pytest.raises(RuntimeClosedError):
            async with runtime.acquire(Source.LIVE):
                pass
        with pytest.raises(ResourceError):
            await runtime.start()

    async def test_disabled_resources_raise_resource_disabled(
        self, mock_launcher: MockLauncher
    ) -> None:
        runtime = make_runtime(local_enabled=False, live_enabled=False)
        with pytest.raises(ResourceDisabledError):
            async with runtime.acquire(Source.LOCAL):
                pass
        with pytest.raises(ResourceDisabledError):
            async with runtime.session():
                pass
        with pytest.raises(ResourceDisabledError):
            await runtime.open_db()
        async with runtime.acquire(Source.AUTO) as (db, session):
            assert db is None
            assert session is None

    async def test_close_closes_everything_even_if_a_pool_fails_to_close(
        self, mock_launcher: MockLauncher
    ) -> None:
        """One browser failing to close must not leak the other pools or the database."""
        runtime = make_runtime(local_enabled=True)
        await runtime.start()
        async with runtime.acquire(Source.AUTO, language="en"):
            pass
        async with runtime.acquire(Source.LIVE, language="es"):
            pass
        mock_launcher.close_error = RuntimeError("browser exploded")

        with pytest.raises(SessionPoolError):
            await runtime.close()
        assert mock_launcher.calls.count("close") == 2  # both pools were attempted
        assert mock_launcher.calls[-1] == "close_db"  # and the database was still closed
        assert runtime.closed

    async def test_failed_start_releases_what_it_opened(self, mock_launcher: MockLauncher) -> None:
        mock_launcher.open_error = RuntimeError("no browser")
        runtime = make_runtime(local_enabled=True, mode=SessionMode.EAGER)
        with pytest.raises(RuntimeError, match="no browser"):
            await runtime.start()
        assert runtime.closed
        assert mock_launcher.calls == ["open_db", "close_db"]

    async def test_stats_counts_pooled_and_per_call_sessions(
        self, mock_launcher: MockLauncher
    ) -> None:
        runtime = make_runtime(mode=SessionMode.PER_CALL, max_sessions=2)
        async with runtime.acquire(Source.LIVE):
            stats = runtime.stats()
            assert stats.per_call_sessions == 1
            assert stats.open_sessions == 1
        assert runtime.stats().per_call_sessions == 0


class TestReaper:
    async def test_reaper_closes_idle_sessions_and_survives_a_failing_cycle(
        self, mock_launcher: MockLauncher, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An exception in one reaping cycle must not kill the reaper for good."""
        monkeypatch.setattr(runtime_module, "MIN_REAP_INTERVAL", 0.05)
        runtime = make_runtime(idle_timeout=0.05)
        real_close_idle = runtime.close_idle_sessions
        cycles = 0

        async def flaky_close_idle(idle_timeout: float | None = None) -> int:
            nonlocal cycles
            cycles += 1
            if cycles == 1:
                raise RuntimeError("transient")
            return await real_close_idle(idle_timeout)

        monkeypatch.setattr(runtime, "close_idle_sessions", flaky_close_idle)
        closed = asyncio.Event()
        real_close = mock_launcher.close_session

        async def close_and_signal(session: object) -> None:
            await real_close(session)
            closed.set()

        monkeypatch.setattr(runtime_module, "close_session", close_and_signal)
        await runtime.start()
        async with runtime.acquire(Source.LIVE):
            pass

        # Wait for the reaper's close (with timings well above Windows' ~15 ms timer resolution).
        await asyncio.wait_for(closed.wait(), timeout=5.0)

        assert cycles > 1, "the reaper kept running after its first cycle raised"
        assert mock_launcher.calls == ["open", "close"]
        await runtime.close()
