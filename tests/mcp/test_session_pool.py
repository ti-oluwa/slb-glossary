"""Tests for `slb_glossary.mcp.session_pool.SessionPool`."""

import asyncio

import pytest

from slb_glossary.config import SessionOptions
from slb_glossary.mcp import session_pool as session_pool_module
from slb_glossary.mcp.session_pool import SessionPool
from slb_glossary.types import Language

pytestmark = [pytest.mark.unit, pytest.mark.mcp]


@pytest.fixture
def anyio_backend(anyio_backend_asyncio_only):
    return anyio_backend_asyncio_only


class MockPages:
    """Stands in for `slb_glossary.live.types.Pages`; just tracks size/max_size."""

    def __init__(self, max_size: int) -> None:
        self.max_size = max_size
        self.size = 0


class MockSession:
    """Stands in for `slb_glossary.live.browser.Session`; has just enough shape for `SessionPool`."""

    def __init__(self, max_pages: int = 3) -> None:
        self.pages = MockPages(max_pages)


def make_pool(
    monkeypatch: pytest.MonkeyPatch,
    *,
    language: Language = Language.ENGLISH,
    semaphore: asyncio.Semaphore | None = None,
    max_pages: int = 3,
):
    """
    Build a `SessionPool` with `open_session`/`close_session` mocked out.

    Each mocked session tracks its own fake page count (`max_pages` each),
    adjustable via the returned `set_pages(session, n)` helper, so tests
    can simulate a session looking "full" without real browser pages.
    """
    calls: list[str] = []
    sessions: list[MockSession] = []

    async def mock_open_session(**kwargs: object) -> MockSession:
        calls.append("open")
        session = MockSession(max_pages)
        sessions.append(session)
        return session

    async def mock_close_session(session: object) -> None:
        calls.append("close")

    monkeypatch.setattr(session_pool_module, "open_session", mock_open_session)
    monkeypatch.setattr(session_pool_module, "close_session", mock_close_session)

    pool = SessionPool(language, SessionOptions(), semaphore or asyncio.Semaphore(5))
    return pool, calls, sessions


def fill(session: MockSession) -> None:
    """Make a mock session look like its page pool is at capacity."""
    session.pages.size = session.pages.max_size


def free(session: MockSession) -> None:
    """Make a mock session look like it has spare page capacity again."""
    session.pages.size = 0


@pytest.mark.anyio
class TestBasicCheckout:
    async def test_first_acquire_opens_a_session(self, monkeypatch: pytest.MonkeyPatch):
        pool, calls, _sessions = make_pool(monkeypatch)
        session = await pool.acquire()
        assert session is not None
        assert calls == ["open"]
        assert pool.size == 1
        assert pool.in_use

    async def test_concurrent_acquires_with_spare_capacity_share_one_session(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Several callers reuse the same session as long as it has room."""
        pool, calls, _sessions = make_pool(monkeypatch, max_pages=3)
        s1 = await pool.acquire()
        s2 = await pool.acquire()
        assert s1 is s2
        assert calls == ["open"]
        assert pool.size == 1

    async def test_release_does_not_close_the_session(self, monkeypatch: pytest.MonkeyPatch):
        pool, calls, _sessions = make_pool(monkeypatch)
        session = await pool.acquire()
        await pool.release(session)
        assert calls == ["open"]
        assert pool.size == 1
        assert not pool.in_use

    async def test_releasing_an_untracked_session_raises(self, monkeypatch: pytest.MonkeyPatch):
        pool, _calls, _sessions = make_pool(monkeypatch)
        with pytest.raises(RuntimeError, match="not currently tracked"):
            await pool.release(MockSession())

    async def test_double_release_raises_instead_of_going_negative(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        pool, _calls, _sessions = make_pool(monkeypatch)
        session = await pool.acquire()
        await pool.release(session)
        with pytest.raises(RuntimeError, match="negative"):
            await pool.release(session)


@pytest.mark.anyio
class TestElasticGrowth:
    async def test_a_full_session_triggers_opening_a_second_one(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Once the only session looks full, acquiring another checkout opens a new browser instance."""
        pool, calls, _sessions = make_pool(monkeypatch, max_pages=1)
        first = await pool.acquire()
        fill(first)

        second = await pool.acquire()

        assert second is not first
        assert calls == ["open", "open"]
        assert pool.size == 2

    async def test_a_session_with_spare_capacity_is_reused_over_growing(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """If an existing session still has room, acquire reuses it instead of opening another."""
        pool, calls, _sessions = make_pool(monkeypatch, max_pages=3)
        first = await pool.acquire()
        second = await pool.acquire()  # still room (2 < 3)

        assert second is first
        assert calls == ["open"]
        assert pool.size == 1

    async def test_growth_is_serialized_across_concurrent_full_checkouts(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """
        Several callers that all find the pool full at the same instant
        must not each open their own new browser - only one growth
        happens, and the rest share what it produces.
        """
        pool, calls, _sessions = make_pool(monkeypatch, max_pages=1)
        first = await pool.acquire()
        fill(first)

        results = await asyncio.gather(*(pool.acquire() for _ in range(4)))

        # Exactly one more session should have been opened for these 4
        # concurrent, simultaneously-"full" callers - not 4.
        assert calls == ["open", "open"]
        assert pool.size == 2
        assert all(s is results[0] for s in results)

    async def test_reusing_an_open_session_does_not_touch_the_semaphore(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Checking out spare capacity on an already-open session never needs another browser-instance slot."""
        semaphore = asyncio.Semaphore(1)
        pool, calls, _sessions = make_pool(monkeypatch, semaphore=semaphore, max_pages=3)
        await pool.acquire()
        assert semaphore.locked()

        # Would deadlock here if this touched the semaphore again - the
        # only slot is already held by the first (still-open) session.
        await asyncio.wait_for(pool.acquire(), timeout=1.0)
        assert calls == ["open"]

    async def test_growth_blocks_until_a_browser_instance_slot_frees(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """With no free slots anywhere, growth waits - it does not silently skip or raise."""
        semaphore = asyncio.Semaphore(1)
        pool, calls, _sessions = make_pool(monkeypatch, semaphore=semaphore, max_pages=1)
        first = await pool.acquire()
        fill(first)

        grow_task = asyncio.create_task(pool.acquire())
        await asyncio.sleep(0.05)
        assert not grow_task.done(), "growth shouldn't succeed with no free semaphore slots"

        await pool.release(first)
        await pool.close_idle(idle_timeout=0.0)  # frees the slot by actually closing session 1

        second = await asyncio.wait_for(grow_task, timeout=1.0)
        assert second is not None
        assert calls == ["open", "close", "open"]


@pytest.mark.anyio
class TestReaping:
    async def test_close_idle_leaves_an_in_use_session_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        pool, calls, _sessions = make_pool(monkeypatch)
        await pool.acquire()
        await pool.close_idle(idle_timeout=0.0)
        assert calls == ["open"]
        assert pool.size == 1

    async def test_close_idle_closes_a_released_session_past_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        pool, calls, _sessions = make_pool(monkeypatch)
        session = await pool.acquire()
        await pool.release(session)
        await pool.close_idle(idle_timeout=0.0)
        assert calls == ["open", "close"]
        assert pool.size == 0

    async def test_close_idle_does_not_close_before_the_timeout_elapses(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        pool, calls, _sessions = make_pool(monkeypatch)
        session = await pool.acquire()
        await pool.release(session)
        await pool.close_idle(idle_timeout=60.0)
        assert calls == ["open"]
        assert pool.size == 1

    async def test_pool_shrinks_session_by_session_not_all_or_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Two sessions, only one idle - only that one closes, the pool does not drop to zero."""
        pool, calls, _sessions = make_pool(monkeypatch, max_pages=1)
        busy = await pool.acquire()
        fill(busy)
        idle = await pool.acquire()  # triggers growth -> second session
        await pool.release(idle)

        await pool.close_idle(idle_timeout=0.0)

        assert pool.size == 1
        assert calls == ["open", "open", "close"]

    async def test_close_releases_the_semaphore_slot(self, monkeypatch: pytest.MonkeyPatch):
        semaphore = asyncio.Semaphore(1)
        pool, _calls, _sessions = make_pool(monkeypatch, semaphore=semaphore)
        await pool.acquire()
        assert semaphore.locked()

        await pool.close()

        await asyncio.wait_for(semaphore.acquire(), timeout=1.0)

    async def test_close_if_idle_detaches_before_awaiting_close(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """A slow `close_session` on one session does not hold up a concurrent acquire on the pool."""
        close_started = asyncio.Event()
        release_close = asyncio.Event()
        calls: list[str] = []

        async def mock_open_session(**kwargs: object) -> MockSession:
            calls.append("open")
            return MockSession()

        async def slow_close_session(session: object) -> None:
            calls.append("close_start")
            close_started.set()
            await release_close.wait()
            calls.append("close_end")

        monkeypatch.setattr(session_pool_module, "open_session", mock_open_session)
        monkeypatch.setattr(session_pool_module, "close_session", slow_close_session)

        pool = SessionPool(Language.ENGLISH, SessionOptions(), asyncio.Semaphore(5))
        session = await pool.acquire()
        await pool.release(session)

        close_task = asyncio.create_task(pool.close_idle(idle_timeout=0.0))
        await close_started.wait()

        assert pool.size == 0  # detached already, even though close_session is still running
        new_session = await asyncio.wait_for(pool.acquire(), timeout=1.0)
        assert new_session is not None

        release_close.set()
        await close_task
        assert calls == ["open", "close_start", "open", "close_end"]


@pytest.mark.anyio
class TestOpen:
    async def test_open_ensures_a_session_without_checking_it_out(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        pool, calls, _sessions = make_pool(monkeypatch)
        session = await pool.open()
        assert session is not None
        assert calls == ["open"]
        assert not pool.in_use

    async def test_open_reuses_an_existing_session_over_growing(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        pool, calls, _sessions = make_pool(monkeypatch, max_pages=3)
        first = await pool.open()
        second = await pool.open()
        assert first is second
        assert calls == ["open"]

    async def test_opens_with_its_own_language_not_the_options_default(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        seen_kwargs: dict[str, object] = {}

        async def mock_open_session(**kwargs: object) -> MockSession:
            seen_kwargs.update(kwargs)
            return MockSession()

        monkeypatch.setattr(session_pool_module, "open_session", mock_open_session)
        pool = SessionPool(Language.SPANISH, SessionOptions(language="en"), asyncio.Semaphore(5))
        await pool.acquire()
        assert seen_kwargs["language"] is Language.SPANISH
