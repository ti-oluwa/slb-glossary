"""
Tests for `slb_glossary.mcp.runtime.Runtime`'s live-session wiring.

The pool's own elastic acquire/release/growth/reap behaviour is covered
in `test_session_pool.py`; these tests focus on what `Runtime` adds on
top: routing a call to the right language's pool, and `PER_CALL` mode's
separate, pool-free path.

Uses `anyio_backend_asyncio_only`: `Runtime` itself is built on raw
`asyncio.Lock`/`asyncio.Semaphore`/`asyncio.Task`, not anyio-portable
primitives.
"""

import asyncio
import typing

import pytest

from slb_glossary.mcp import runtime as runtime_module
from slb_glossary.mcp import session_pool as session_pool_module
from slb_glossary.mcp.config import LocalAccess, MCPConfig, SessionAccess, SessionMode
from slb_glossary.mcp.errors import MCPError
from slb_glossary.mcp.runtime import Runtime
from slb_glossary.query import Source
from slb_glossary.types import Language

pytestmark = [pytest.mark.unit, pytest.mark.mcp]


@pytest.fixture
def anyio_backend(
    anyio_backend_asyncio_only: tuple[str, dict[str, typing.Any]],
) -> tuple[str, dict[str, typing.Any]]:
    return anyio_backend_asyncio_only


class MockPages:
    """Stands in for `slb_glossary.live.types.Pages`; always reports spare capacity."""

    max_size = 3
    size = 0


class MockSession:
    """Stand-in for `slb_glossary.live.browser.Session`; identity is all these tests need."""

    def __init__(self) -> None:
        self.pages = MockPages()


def make_runtime(
    monkeypatch: pytest.MonkeyPatch, *, mode: SessionMode, max_sessions: int = 5
) -> tuple[Runtime, list[str]]:
    """
    Build a `Runtime` with local access disabled (so only the live-session
    path is exercised) and `open_session`/`close_session` mocked out for
    both the pooled path (`session_pool_module`) and the `PER_CALL` path
    (`runtime_module`, which opens/closes its own session directly).
    """
    calls: list[str] = []

    async def mock_open_session(**kwargs: object) -> MockSession:
        calls.append("open")
        return MockSession()

    async def mock_close_session(session: object) -> None:
        calls.append("close")

    monkeypatch.setattr(runtime_module, "open_session", mock_open_session)
    monkeypatch.setattr(runtime_module, "close_session", mock_close_session)
    monkeypatch.setattr(session_pool_module, "open_session", mock_open_session)
    monkeypatch.setattr(session_pool_module, "close_session", mock_close_session)

    config = MCPConfig(
        local=LocalAccess(enabled=False),
        session=SessionAccess(
            enabled=True, mode=mode, idle_timeout=60.0, max_sessions=max_sessions
        ),
    )
    return Runtime(config), calls


@pytest.mark.anyio
class TestLanguageRouting:
    async def test_default_language_used_when_none_requested(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runtime, calls = make_runtime(monkeypatch, mode=SessionMode.LAZY)
        async with runtime.acquire(Source.LIVE) as (_, session):
            assert session is not None
        assert calls == ["open"]
        assert Language.ENGLISH in runtime._pools

    async def test_different_languages_get_different_pools_and_sessions(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runtime, calls = make_runtime(monkeypatch, mode=SessionMode.LAZY)
        async with runtime.acquire(Source.LIVE, language="en") as (_, en_session):
            assert en_session is not None
        async with runtime.acquire(Source.LIVE, language="es") as (_, es_session):
            assert es_session is not None
        assert en_session is not es_session
        assert calls.count("open") == 2
        assert set(runtime._pools) == {Language.ENGLISH, Language.SPANISH}

    async def test_same_language_reuses_the_pool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        runtime, calls = make_runtime(monkeypatch, mode=SessionMode.LAZY)
        async with runtime.acquire(Source.LIVE, language="es"):
            pass
        async with runtime.acquire(Source.LIVE, language="es"):
            pass
        assert calls.count("open") == 1

    async def test_unknown_language_string_raises_mcp_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runtime, _calls = make_runtime(monkeypatch, mode=SessionMode.LAZY)
        with pytest.raises(MCPError, match="fr"):
            async with runtime.acquire(Source.LIVE, language="fr"):
                pass

    async def test_open_session_opens_the_requested_languages_pool(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runtime, calls = make_runtime(monkeypatch, mode=SessionMode.LAZY)
        session = await runtime.open_session(language="es")
        assert session is not None
        assert calls == ["open"]
        assert Language.SPANISH in runtime._pools
        assert not runtime._pools[Language.SPANISH].in_use


@pytest.mark.anyio
class TestReapAndSemaphoreWiring:
    async def test_reap_is_a_no_op_for_a_pool_still_in_use(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runtime, calls = make_runtime(monkeypatch, mode=SessionMode.LAZY)
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
        assert calls == ["open"], "reap closed a session while a call still held a checkout"

        release.set()
        await task
        await runtime.close_idle_sessions(idle_timeout=0.0)
        assert calls == ["open", "close"]

    async def test_emptied_pool_is_dropped_from_the_map(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runtime, calls = make_runtime(monkeypatch, mode=SessionMode.LAZY)
        async with runtime.acquire(Source.LIVE, language="es"):
            pass
        assert Language.SPANISH in runtime._pools

        await runtime.close_idle_sessions(idle_timeout=0.0)

        assert calls == ["open", "close"]
        assert Language.SPANISH not in runtime._pools

    async def test_max_sessions_bounds_total_browser_instances_across_languages(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The semaphore Runtime hands each pool is genuinely shared, not per-language."""
        runtime, calls = make_runtime(monkeypatch, mode=SessionMode.LAZY, max_sessions=1)

        async with runtime.acquire(Source.LIVE, language="en"):
            pass
        # "en"'s session is released but still open - it holds the only slot.

        open_es_task = asyncio.create_task(runtime.open_session(language="es"))
        await asyncio.sleep(0.05)
        assert not open_es_task.done(), (
            "a second language shouldn't open while the first still holds the only slot"
        )

        await runtime._pools[Language.ENGLISH].close_idle(idle_timeout=0.0)
        es_session = await asyncio.wait_for(open_es_task, timeout=1.0)

        assert es_session is not None
        assert calls == ["open", "close", "open"]

    async def test_per_call_mode_bounds_concurrency_runtime_wide(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runtime, calls = make_runtime(monkeypatch, mode=SessionMode.PER_CALL, max_sessions=2)
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

        tasks = [
            asyncio.create_task(one_call("en")),
            asyncio.create_task(one_call("en")),
            asyncio.create_task(one_call("es")),
            asyncio.create_task(one_call("es")),
        ]
        await asyncio.sleep(0.05)
        assert peak_open == 2
        release.set()
        await asyncio.gather(*tasks)
        assert calls.count("open") == 4
        assert calls.count("close") == 4


@pytest.mark.anyio
class TestPerCallMode:
    async def test_opens_a_fresh_session_and_always_closes_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runtime, calls = make_runtime(monkeypatch, mode=SessionMode.PER_CALL)
        async with runtime.acquire(Source.LIVE, language="es") as (_, session):
            assert session is not None
        assert calls == ["open", "close"]
        assert runtime._pools == {}

    async def test_exception_inside_still_closes_the_session(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runtime, calls = make_runtime(monkeypatch, mode=SessionMode.PER_CALL)
        with pytest.raises(RuntimeError):
            async with runtime.acquire(Source.LIVE):
                raise RuntimeError("boom")
        assert calls == ["open", "close"]

    async def test_repeated_calls_each_get_their_own_session(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runtime, calls = make_runtime(monkeypatch, mode=SessionMode.PER_CALL)
        async with runtime.acquire(Source.LIVE):
            pass
        async with runtime.acquire(Source.LIVE):
            pass
        assert calls == ["open", "close", "open", "close"]


@pytest.mark.anyio
class TestAclose:
    async def test_closes_every_language_pool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        runtime, calls = make_runtime(monkeypatch, mode=SessionMode.LAZY)
        async with runtime.acquire(Source.LIVE, language="en"):
            pass
        async with runtime.acquire(Source.LIVE, language="es"):
            pass

        await runtime.aclose()

        assert calls.count("open") == 2
        assert calls.count("close") == 2
        assert runtime._pools == {}
