"""
Tests for `slb_glossary.live.SessionPool`.

Uses `anyio_backend_asyncio_only`: the pool is built on raw `asyncio.Lock`/`asyncio.Semaphore`.
"""

import asyncio
import typing

import pytest

from slb_glossary.config import SessionOptions
from slb_glossary.errors import SessionPoolClosedError, SessionPoolError
from slb_glossary.live import SessionPool
from slb_glossary.live import runtime as runtime_module
from slb_glossary.types import Language
from tests.factories import make_session_pool
from tests.mocks import MockLauncher, MockSession

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


@pytest.fixture
def anyio_backend(
    anyio_backend_asyncio_only: tuple[str, dict[str, typing.Any]],
) -> tuple[str, dict[str, typing.Any]]:
    return anyio_backend_asyncio_only


class TestBasicCheckout:
    async def test_first_acquire_opens_a_session(self, mock_launcher: MockLauncher) -> None:
        pool = make_session_pool()
        session = await pool.acquire()
        assert session is not None
        assert mock_launcher.calls == ["open"]
        assert pool.size == 1
        assert pool.in_use

    async def test_concurrent_acquires_with_spare_capacity_share_one_session(
        self, mock_launcher: MockLauncher
    ) -> None:
        """Several callers reuse the same session as long as it has room."""
        pool = make_session_pool()
        first = await pool.acquire()
        second = await pool.acquire()
        assert first is second
        assert mock_launcher.calls == ["open"]
        assert pool.size == 1

    async def test_release_does_not_close_the_session(self, mock_launcher: MockLauncher) -> None:
        pool = make_session_pool()
        session = await pool.acquire()
        await pool.release(session)
        assert mock_launcher.calls == ["open"]
        assert pool.size == 1
        assert not pool.in_use

    async def test_releasing_an_untracked_session_raises(
        self, mock_launcher: MockLauncher
    ) -> None:
        pool = make_session_pool()
        with pytest.raises(SessionPoolError, match="not currently tracked"):
            await pool.release(MockSession())  # type: ignore[arg-type]

    async def test_double_release_raises_instead_of_going_negative(
        self, mock_launcher: MockLauncher
    ) -> None:
        pool = make_session_pool()
        session = await pool.acquire()
        await pool.release(session)
        with pytest.raises(SessionPoolError, match="negative"):
            await pool.release(session)

    async def test_checkout_releases_even_if_the_body_raises(
        self, mock_launcher: MockLauncher
    ) -> None:
        pool = make_session_pool()
        with pytest.raises(ValueError, match="boom"):
            async with pool.checkout():
                assert pool.in_use
                raise ValueError("boom")
        assert not pool.in_use

    async def test_acquire_rejects_a_capacity_below_one(self, mock_launcher: MockLauncher) -> None:
        with pytest.raises(ValueError, match="capacity"):
            await make_session_pool().acquire(capacity=0)

    async def test_opens_with_its_own_language_not_the_options_default(
        self, mock_launcher: MockLauncher
    ) -> None:
        pool = make_session_pool(language=Language.SPANISH, options=SessionOptions(language="en"))
        await pool.acquire()
        assert mock_launcher.open_kwargs[0]["language"] is Language.SPANISH


class TestElasticGrowth:
    async def test_a_full_session_triggers_opening_a_second_one(
        self, mock_launcher: MockLauncher
    ) -> None:
        """Once the only session looks full, acquiring another checkout opens a new browser."""
        mock_launcher.max_pages = 1
        pool = make_session_pool()
        first = await pool.acquire()
        mock_launcher.sessions[0].pages.fill()

        second = await pool.acquire()

        assert second is not first
        assert mock_launcher.calls == ["open", "open"]
        assert pool.size == 2

    async def test_a_session_with_spare_capacity_is_reused_over_growing(
        self, mock_launcher: MockLauncher
    ) -> None:
        pool = make_session_pool()
        first = await pool.acquire()
        second = await pool.acquire()  # still room (0 < 3)

        assert second is first
        assert mock_launcher.calls == ["open"]

    async def test_growth_is_serialized_across_concurrent_full_checkouts(
        self, mock_launcher: MockLauncher
    ) -> None:
        """
        Several callers that all find the pool full at the same instant must not each
        open their own new browser: one growth happens, and the rest share what it produces.
        """
        mock_launcher.max_pages = 1
        pool = make_session_pool()
        await pool.acquire()
        mock_launcher.sessions[0].pages.fill()

        results = await asyncio.gather(*(pool.acquire() for _ in range(4)))

        assert mock_launcher.calls == ["open", "open"]
        assert pool.size == 2
        assert all(session is results[0] for session in results)

    async def test_reusing_an_open_session_does_not_touch_the_semaphore(
        self, mock_launcher: MockLauncher
    ) -> None:
        """Spare capacity on an already-open session never needs another browser slot."""
        semaphore = asyncio.Semaphore(1)
        pool = make_session_pool(max_sessions=semaphore)
        await pool.acquire()
        assert semaphore.locked()

        # Would deadlock here if this touched the semaphore again.
        await asyncio.wait_for(pool.acquire(), timeout=1.0)
        assert mock_launcher.calls == ["open"]

    async def test_full_session_is_shared_when_the_browser_budget_is_spent(
        self, mock_launcher: MockLauncher
    ) -> None:
        """
        With no free slot, a caller shares the existing (full-looking) session instead of
        blocking until it is reaped, which could be minutes or never.
        """
        mock_launcher.max_pages = 1
        pool = make_session_pool(max_sessions=1)
        first = await pool.acquire()
        mock_launcher.sessions[0].pages.fill()

        second = await asyncio.wait_for(pool.acquire(), timeout=1.0)
        assert second is first
        assert mock_launcher.calls == ["open"]

    async def test_most_free_session_is_preferred(self, mock_launcher: MockLauncher) -> None:
        """Load is spread: a caller gets the session with the most free pages, not the first."""
        pool = make_session_pool()
        await pool.acquire()
        mock_launcher.sessions[0].pages.size = 2
        spare = (await pool.new()).session

        assert await pool.acquire() is spare

    async def test_empty_pool_waits_for_a_slot_held_elsewhere(
        self, mock_launcher: MockLauncher
    ) -> None:
        """A pool with nothing to share can only wait until another pool's slot frees."""
        semaphore = asyncio.Semaphore(1)
        other = make_session_pool(max_sessions=semaphore)
        pool = make_session_pool(max_sessions=semaphore)
        held = await other.acquire()

        wait_task = asyncio.create_task(pool.acquire())
        await asyncio.sleep(0.05)
        assert not wait_task.done(), "no slot is free, so an empty pool has to wait"

        await other.release(held)
        await other.close_idle(idle_timeout=0.0)  # frees the slot by closing that session

        assert await asyncio.wait_for(wait_task, timeout=1.0) is not None
        assert mock_launcher.calls == ["open", "close", "open"]

    async def test_empty_pool_reclaims_idle_sessions_instead_of_waiting(
        self, mock_launcher: MockLauncher
    ) -> None:
        """`reclaim` is asked to free idle sessions before an empty pool waits for a slot."""
        semaphore = asyncio.Semaphore(1)
        other = make_session_pool(max_sessions=semaphore)
        idle = await other.acquire()
        await other.release(idle)

        reclaim_calls = 0

        async def reclaim() -> int:
            nonlocal reclaim_calls
            reclaim_calls += 1
            return await other.close_idle(idle_timeout=0.0)

        pool = make_session_pool(max_sessions=semaphore, reclaim=reclaim)
        assert await asyncio.wait_for(pool.acquire(), timeout=1.0) is not None
        assert reclaim_calls == 1
        assert mock_launcher.calls == ["open", "close", "open"]
        assert other.size == 0

    async def test_waiting_pool_keeps_retrying_reclaim_until_a_busy_session_is_released(
        self, mock_launcher: MockLauncher, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(runtime_module, "RECLAIM_POLL_INTERVAL", 0.02)
        semaphore = asyncio.Semaphore(1)
        other = make_session_pool(max_sessions=semaphore)
        busy = await other.acquire()

        async def reclaim() -> int:
            return await other.close_idle(idle_timeout=0.0)

        pool = make_session_pool(max_sessions=semaphore, reclaim=reclaim)
        wait_task = asyncio.create_task(pool.acquire())
        await asyncio.sleep(0.1)
        assert not wait_task.done()

        await other.release(busy)  # now reclaimable; the waiter's next retry should take it
        assert await asyncio.wait_for(wait_task, timeout=1.0) is not None

    async def test_pool_is_not_empty_while_a_session_is_opening(
        self, mock_launcher: MockLauncher, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pool mid-launch must not look retirable (the reaper would close it under the caller)."""
        gate = asyncio.Event()

        async def slow_open(**kwargs: typing.Any) -> MockSession:
            await gate.wait()
            return await mock_launcher.open_session(**kwargs)

        monkeypatch.setattr(runtime_module, "open_session", slow_open)
        pool = make_session_pool()
        task = asyncio.create_task(pool.acquire())
        await asyncio.sleep(0.02)
        assert pool.size == 0
        assert not pool.empty
        gate.set()
        await task
        assert not pool.empty


class TestMaxSessions:
    async def test_a_number_creates_the_limit_internally(
        self, mock_launcher: MockLauncher
    ) -> None:
        pool = make_session_pool(max_sessions=2, capacity_tolerance=0)
        first = (await pool.new()).session
        second = (await pool.new()).session
        assert first is not second
        assert mock_launcher.calls == ["open", "open"]

        third = asyncio.create_task(pool.new())
        await asyncio.sleep(0.05)
        assert not third.done(), "a third browser must wait while two are open"
        await pool.close()
        with pytest.raises(SessionPoolClosedError):
            await third

    async def test_default_is_one_session(self, mock_launcher: MockLauncher) -> None:
        pool = SessionPool(Language.ENGLISH, SessionOptions())
        await pool.new()
        task = asyncio.create_task(pool.new())
        await asyncio.sleep(0.05)
        assert not task.done()
        await pool.close()
        with pytest.raises(SessionPoolClosedError):
            await task

    @pytest.mark.parametrize("bad", [0, -1])
    async def test_rejects_a_non_positive_number(self, bad: int) -> None:
        with pytest.raises(ValueError, match="max_sessions"):
            make_session_pool(max_sessions=bad)


class TestReaping:
    async def test_close_idle_leaves_an_in_use_session_alone(
        self, mock_launcher: MockLauncher
    ) -> None:
        pool = make_session_pool()
        await pool.acquire()
        assert await pool.close_idle(idle_timeout=0.0) == 0
        assert mock_launcher.calls == ["open"]
        assert pool.size == 1

    async def test_close_idle_closes_a_released_session_past_timeout(
        self, mock_launcher: MockLauncher
    ) -> None:
        pool = make_session_pool()
        session = await pool.acquire()
        await pool.release(session)
        assert await pool.close_idle(idle_timeout=0.0) == 1
        assert mock_launcher.calls == ["open", "close"]
        assert pool.size == 0

    async def test_close_idle_does_not_close_before_the_timeout_elapses(
        self, mock_launcher: MockLauncher
    ) -> None:
        pool = make_session_pool()
        session = await pool.acquire()
        await pool.release(session)
        await pool.close_idle(idle_timeout=60.0)
        assert mock_launcher.calls == ["open"]
        assert pool.size == 1

    async def test_pool_shrinks_session_by_session_not_all_or_nothing(
        self, mock_launcher: MockLauncher
    ) -> None:
        """Two sessions, only one idle: only that one closes, the pool does not drop to zero."""
        mock_launcher.max_pages = 1
        pool = make_session_pool()
        await pool.acquire()
        mock_launcher.sessions[0].pages.fill()
        idle = await pool.acquire()  # triggers growth -> second session
        await pool.release(idle)

        await pool.close_idle(idle_timeout=0.0)

        assert pool.size == 1
        assert mock_launcher.calls == ["open", "open", "close"]

    async def test_close_releases_the_semaphore_slot(self, mock_launcher: MockLauncher) -> None:
        semaphore = asyncio.Semaphore(1)
        pool = make_session_pool(max_sessions=semaphore)
        await pool.acquire()
        assert semaphore.locked()

        await pool.close()

        await asyncio.wait_for(semaphore.acquire(), timeout=1.0)

    async def test_close_idle_detaches_before_awaiting_close(
        self, mock_launcher: MockLauncher, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A slow `close_session` on one session does not hold up a concurrent acquire."""
        close_started = asyncio.Event()
        release_close = asyncio.Event()

        async def slow_close_session(session: object) -> None:
            mock_launcher.calls.append("close_start")
            close_started.set()
            await release_close.wait()
            mock_launcher.calls.append("close_end")

        monkeypatch.setattr(runtime_module, "close_session", slow_close_session)

        pool = make_session_pool()
        session = await pool.acquire()
        await pool.release(session)

        close_task = asyncio.create_task(pool.close_idle(idle_timeout=0.0))
        await close_started.wait()

        assert pool.size == 0  # detached already, even though close_session is still running
        assert await asyncio.wait_for(pool.acquire(), timeout=1.0) is not None

        release_close.set()
        await close_task
        assert mock_launcher.calls == ["open", "close_start", "open", "close_end"]

    async def test_dead_browser_is_dropped_and_replaced(self, mock_launcher: MockLauncher) -> None:
        """A session whose browser crashed is never handed out again."""
        pool = make_session_pool()
        first = await pool.acquire()
        await pool.release(first)
        mock_launcher.sessions[0].browser.disconnect()

        second = await pool.acquire()
        assert second is not first
        assert mock_launcher.calls == ["open", "close", "open"]
        assert pool.size == 1

    async def test_close_idle_also_drops_dead_sessions_regardless_of_idle_time(
        self, mock_launcher: MockLauncher
    ) -> None:
        pool = make_session_pool()
        session = await pool.acquire()
        await pool.release(session)
        mock_launcher.sessions[0].browser.disconnect()
        assert await pool.close_idle(idle_timeout=3600.0) == 1
        assert pool.size == 0


class TestOpen:
    async def test_open_ensures_a_session_without_checking_it_out(
        self, mock_launcher: MockLauncher
    ) -> None:
        pool = make_session_pool()
        assert await pool.open() is not None
        assert mock_launcher.calls == ["open"]
        assert not pool.in_use

    async def test_open_reuses_an_existing_session_over_growing(
        self, mock_launcher: MockLauncher
    ) -> None:
        pool = make_session_pool()
        first = await pool.open()
        second = await pool.open()
        assert first is second
        assert mock_launcher.calls == ["open"]


class TestClosing:
    async def test_closed_pool_refuses_new_checkouts_but_not_releases(
        self, mock_launcher: MockLauncher
    ) -> None:
        pool = make_session_pool()
        session = await pool.acquire()
        await pool.close()
        assert pool.closed
        with pytest.raises(SessionPoolClosedError):
            await pool.acquire()
        with pytest.raises(SessionPoolClosedError):
            await pool.new()
        await pool.release(session)  # a call that began before shutdown may still finish

    async def test_errors_are_runtime_errors_for_backward_compatibility(
        self, mock_launcher: MockLauncher
    ) -> None:
        pool = make_session_pool()
        await pool.close()
        with pytest.raises(RuntimeError):
            await pool.acquire()

    async def test_failing_close_still_attempts_every_session_and_frees_the_slots(
        self, mock_launcher: MockLauncher
    ) -> None:
        mock_launcher.max_pages = 1
        pool = make_session_pool()
        await pool.acquire()
        mock_launcher.sessions[0].pages.fill()
        await pool.acquire()
        mock_launcher.close_error = RuntimeError("browser exploded")

        with pytest.raises(SessionPoolError):
            await pool.close()
        assert mock_launcher.calls.count("close") == 2


class TestStats:
    async def test_snapshot(self, mock_launcher: MockLauncher) -> None:
        pool = make_session_pool()
        session = await pool.acquire()
        stats = pool.stats()
        assert (stats.sessions, stats.busy_sessions, stats.checkouts) == (1, 1, 1)
        assert stats.language is Language.ENGLISH
        assert not stats.closed
        await pool.release(session)
        assert pool.stats().busy_sessions == 0
