"""
Tests for `slb_glossary.mcp.runtime.Runtime`'s shared live-session lifecycle.

Uses `anyio_backend_asyncio_only`: `Runtime` itself is built on raw
`asyncio.Lock`/`asyncio.Semaphore`/`asyncio.Task`, not anyio-portable
primitives.
"""

import asyncio

import pytest

from slb_glossary.mcp import runtime as runtime_module
from slb_glossary.mcp.config import LocalAccess, MCPConfig, SessionAccess, SessionMode
from slb_glossary.mcp.runtime import Runtime
from slb_glossary.query import Source

pytestmark = [pytest.mark.unit, pytest.mark.mcp]


@pytest.fixture
def anyio_backend(anyio_backend_asyncio_only):
    return anyio_backend_asyncio_only


class MockSession:
    """Stand-in for `slb_glossary.live.browser.Session`; identity is all these tests need."""


def make_runtime(
    monkeypatch: pytest.MonkeyPatch, *, mode: SessionMode
) -> tuple[Runtime, list[str]]:
    """
    Build a `Runtime` with local access disabled (so only the live-session
    path is exercised) and `open_session`/`close_session` faked out.

    :return: The `Runtime`, and a list `calls` "open"/"close" append to, in
        order, for assertions.
    """
    calls: list[str] = []

    async def mock_open_session(**kwargs: object) -> MockSession:
        calls.append("open")
        return MockSession()

    async def mock_close_session(session: object) -> None:
        calls.append("close")

    monkeypatch.setattr(runtime_module, "open_session", mock_open_session)
    monkeypatch.setattr(runtime_module, "close_session", mock_close_session)

    config = MCPConfig(
        local=LocalAccess(enabled=False),
        session=SessionAccess(enabled=True, mode=mode, idle_timeout=60.0),
    )
    return Runtime(config), calls


@pytest.mark.anyio
class TestSharedSessionLock:
    async def test_reaper_does_not_close_session_while_a_call_is_in_flight(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        A reap check that runs while `acquire()` is still using the shared 
        session must not close it, no matter how stale `_session_last_used` looks.
        """
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

        # Make `_session_last_used` look arbitrarily stale, then run a reap
        # check while the call above still holds the session open. Without
        # `acquire` holding `_session_lock` for the call's duration, this
        # would see idle_for >= idle_timeout and close out from under it.
        runtime._session_last_used -= 1000.0
        await runtime._reap_once(idle_timeout=0.001)
        assert calls == ["open"], "reaper closed the session while a call was still using it"

        release.set()
        await task

        # Only after the call releases (and the reaper's own idle_timeout
        # has since elapsed) should a reap check be allowed to close it.
        await runtime._reap_once(idle_timeout=0.0)
        assert calls == ["open", "close"]

    async def test_last_used_refreshed_on_release_not_just_on_acquire(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Idle timing should start from when a call finishes, not from when it started."""
        runtime, _ = make_runtime(monkeypatch, mode=SessionMode.LAZY)

        async with runtime.acquire(Source.LIVE):
            handed_out_at = runtime._session_last_used
            await asyncio.sleep(0)

        assert runtime._session_last_used >= handed_out_at
        idle_for = asyncio.get_event_loop().time() - runtime._session_last_used
        assert idle_for < 1.0

    async def test_exception_inside_acquire_still_releases_the_lock(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A tool call that raises must not leave `_session_lock` permanently held."""
        runtime, _ = make_runtime(monkeypatch, mode=SessionMode.LAZY)

        with pytest.raises(RuntimeError):
            async with runtime.acquire(Source.LIVE):
                raise RuntimeError("boom")

        # Would hang forever if the lock leaked.
        async def acquire_lock() -> None:
            async with runtime._session_lock:
                pass

        await asyncio.wait_for(acquire_lock(), timeout=1.0)

    async def test_concurrent_calls_share_one_session_without_double_open(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Concurrent `acquire()` calls under LAZY mode serialize, but only open one session."""
        runtime, calls = make_runtime(monkeypatch, mode=SessionMode.LAZY)

        async def one_call() -> None:
            async with runtime.acquire(Source.LIVE) as (_, session):
                assert session is not None
                await asyncio.sleep(0)

        await asyncio.gather(*(one_call() for _ in range(5)))

        assert calls.count("open") == 1

    async def test_per_call_mode_still_opens_and_closes_its_own_session(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PER_CALL mode is untouched by this fix: still one open/close per call."""
        runtime, calls = make_runtime(monkeypatch, mode=SessionMode.PER_CALL)

        async with runtime.acquire(Source.LIVE) as (_, session):
            assert session is not None

        assert calls == ["open", "close"]
