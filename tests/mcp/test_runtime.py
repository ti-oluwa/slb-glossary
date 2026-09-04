"""
Tests for `slb_glossary.mcp.runtime.Runtime`'s shared live-session lifecycle.

Uses `anyio_backend_asyncio_only`: `Runtime` itself is built on raw
`asyncio.Lock`/`asyncio.Semaphore`/`asyncio.Task`, not anyio-portable
primitives.
"""

import asyncio
import typing

import pytest

from slb_glossary.mcp import runtime as runtime_module
from slb_glossary.mcp.config import LocalAccess, MCPConfig, SessionAccess, SessionMode
from slb_glossary.mcp.runtime import Runtime
from slb_glossary.query import Source

pytestmark = [pytest.mark.unit, pytest.mark.mcp]


@pytest.fixture
def anyio_backend(
    anyio_backend_asyncio_only: tuple[str, dict[str, typing.Any]],
) -> tuple[str, dict[str, typing.Any]]:
    return anyio_backend_asyncio_only


class MockSession:
    """Stand-in for `slb_glossary.live.browser.Session`; identity is all these tests need."""


def make_runtime(
    monkeypatch: pytest.MonkeyPatch, *, mode: SessionMode, max_concurrent: int = 1
) -> tuple[Runtime, list[str]]:
    """
    Build a `Runtime` with local access disabled (so only the live-session
    path is exercised) and `open_session`/`close_session` mocked out.

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
        session=SessionAccess(
            enabled=True, mode=mode, idle_timeout=60.0, max_concurrent=max_concurrent
        ),
    )
    return Runtime(config), calls


@pytest.mark.anyio
class TestSharedSessionCheckout:
    async def test_reap_is_a_no_op_while_a_call_holds_a_checkout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        The core invariant from the corrections doc: the reaper must never
        close the shared session while `_session_users > 0`, no matter how
        stale `_session_last_used` looks.
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
        assert runtime._session_users == 1

        # Make `_session_last_used` look arbitrarily stale, then reap-check
        # with an effectively-zero idle_timeout, while the call above still
        # holds its checkout. `close_idle_session` must see `_session_users > 0`
        # and bail out, rather than closing out from under the call.
        runtime._session_last_used -= 1000.0
        await runtime.close_idle_session(idle_timeout=0.0)
        assert calls == ["open"], "reaper closed the session while a call still held a checkout"

        release.set()
        await task
        assert runtime._session_users == 0

        # Only after every checkout is released (and idle_timeout has since
        # elapsed) should a reap check be allowed to close it.
        await runtime.close_idle_session(idle_timeout=0.0)
        assert calls == ["open", "close"]

    async def test_reaper_does_not_close_a_freshly_released_session_before_idle_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A session that was just released shouldn't look idle enough to reap yet."""
        runtime, calls = make_runtime(monkeypatch, mode=SessionMode.LAZY)

        async with runtime.acquire(Source.LIVE):
            pass

        await runtime.close_idle_session(idle_timeout=60.0)
        assert calls == ["open"], "reaper closed a session that hadn't been idle long enough"

    async def test_last_used_refreshed_on_release_not_just_on_acquire(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Idle timing should start from when a call finishes, not from when it started."""
        runtime, _ = make_runtime(monkeypatch, mode=SessionMode.LAZY)

        async with runtime.acquire(Source.LIVE):
            handed_out_at = runtime._session_last_used
            await asyncio.sleep(0)

        assert runtime._session_last_used >= handed_out_at

    async def test_exception_inside_acquire_still_releases_the_checkout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A tool call that raises must still decrement `_session_users`."""
        runtime, _ = make_runtime(monkeypatch, mode=SessionMode.LAZY)

        with pytest.raises(RuntimeError):
            async with runtime.acquire(Source.LIVE):
                raise RuntimeError("boom")

        assert runtime._session_users == 0

    async def test_release_below_zero_raises_instead_of_going_negative(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An extra, unpaired release must not silently corrupt the counter."""
        runtime, _ = make_runtime(monkeypatch, mode=SessionMode.LAZY)

        with pytest.raises(RuntimeError, match="negative"):
            await runtime._release_session()

        assert runtime._session_users == 0

    async def test_concurrent_calls_share_one_session_without_double_open(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        Concurrent `acquire()` calls under LAZY mode all share one session,
        genuinely overlapping rather than serializing - given enough
        `max_concurrent` headroom to let them (see `SessionAccess.max_concurrent`'s
        own docstring: it bounds concurrent shared-session checkouts too,
        not just separate `PER_CALL` sessions).
        """
        runtime, calls = make_runtime(monkeypatch, mode=SessionMode.LAZY, max_concurrent=5)
        peak_users = 0
        both_entered = asyncio.Event()

        async def one_call() -> None:
            nonlocal peak_users
            async with runtime.acquire(Source.LIVE) as (_, session):
                assert session is not None
                peak_users = max(peak_users, runtime._session_users)
                if runtime._session_users == 5:
                    both_entered.set()
                await both_entered.wait()

        await asyncio.gather(*(one_call() for _ in range(5)))

        assert calls.count("open") == 1
        # The whole point of the checkout being non-exclusive: several
        # calls genuinely overlapped inside the shared session at once,
        # rather than being serialized one at a time.
        assert peak_users == 5
        assert runtime._session_users == 0

    async def test_max_concurrent_also_bounds_shared_session_checkouts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        `max_concurrent` gates `EAGER`/`LAZY` checkouts too, not just
        separate `PER_CALL` sessions - the same semaphore now wraps both
        branches of `acquire`.
        """
        runtime, calls = make_runtime(monkeypatch, mode=SessionMode.LAZY, max_concurrent=2)
        peak_users = 0
        release = asyncio.Event()

        async def one_call() -> None:
            nonlocal peak_users
            async with runtime.acquire(Source.LIVE) as (_, session):
                assert session is not None
                peak_users = max(peak_users, runtime._session_users)
                await release.wait()

        tasks = [asyncio.create_task(one_call()) for _ in range(5)]
        await asyncio.sleep(0.05)
        # Only `max_concurrent=2` should have gotten a checkout; the rest
        # are waiting on the semaphore, never having reached `_acquire_session`.
        assert peak_users == 2
        assert calls.count("open") == 1  # still one shared session, just gated concurrency
        release.set()
        await asyncio.gather(*tasks)
        assert runtime._session_users == 0

    async def test_per_call_mode_still_opens_and_closes_its_own_session(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PER_CALL mode is untouched by this fix: still one open/close per call, no counter use."""
        runtime, calls = make_runtime(monkeypatch, mode=SessionMode.PER_CALL)

        async with runtime.acquire(Source.LIVE) as (_, session):
            assert session is not None

        assert calls == ["open", "close"]
        assert runtime._session_users == 0

    async def test_per_call_mode_bounds_concurrency_with_max_concurrent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        `max_concurrent` gates `PER_CALL` sessions too - the `PER_CALL`-specific
        case of the same semaphore `test_max_concurrent_also_bounds_shared_session_checkouts`
        exercises for `EAGER`/`LAZY`.
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
            session=SessionAccess(
                enabled=True, mode=SessionMode.PER_CALL, idle_timeout=60.0, max_concurrent=2
            ),
        )
        runtime = Runtime(config)
        peak_open = 0
        currently_open = 0
        release = asyncio.Event()

        async def one_call() -> None:
            nonlocal peak_open, currently_open
            async with runtime.acquire(Source.LIVE):
                currently_open += 1
                peak_open = max(peak_open, currently_open)
                await release.wait()
                currently_open -= 1

        tasks = [asyncio.create_task(one_call()) for _ in range(4)]
        await asyncio.sleep(0.05)
        # Only `max_concurrent=2` should have gotten in; the rest are
        # waiting on the semaphore.
        assert peak_open == 2
        release.set()
        await asyncio.gather(*tasks)
        assert calls.count("open") == 4
        assert calls.count("close") == 4
