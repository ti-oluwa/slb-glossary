"""Tests for `live.types`: `safe_close`, `Pages`, `PageHandle`."""

import asyncio
import logging
import typing

import pytest

from slb_glossary.constants import constants
from slb_glossary.errors import BrowserError, PagePoolTimeoutError
from slb_glossary.live.types import Pages, safe_close
from tests.mocks import MockContext, MockPage

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


@pytest.fixture
def anyio_backend(
    anyio_backend_asyncio_only: tuple[str, dict[str, typing.Any]],
) -> tuple[str, dict[str, typing.Any]]:
    """`Pages` uses `asyncio.Semaphore` internally, which isn't trio-safe."""
    return anyio_backend_asyncio_only


class TestSafeClose:
    async def test_returns_true_on_success(self) -> None:
        """A close call that completes without raising returns `True`."""

        async def ok() -> None:
            return None

        assert await safe_close(ok(), "thing") is True

    async def test_returns_false_and_logs_a_warning_on_failure(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A close call that raises returns `False` and logs a warning, instead of propagating."""

        async def fails() -> None:
            raise RuntimeError("boom")

        with caplog.at_level(logging.WARNING, logger="slb_glossary.live.types"):
            result = await safe_close(fails(), "thing")

        assert result is False
        assert any("Failed to close thing" in record.message for record in caplog.records)
        assert any(record.levelno == logging.WARNING for record in caplog.records)


class TestPages:
    async def test_get_opens_a_page_and_tracks_it(self) -> None:
        """`get()` opens a page via the context and counts it in `size`."""
        pool = Pages(context=MockContext(), max_size=5)  # type: ignore[arg-type]
        handle = await pool.get()
        assert pool.size == 1
        assert not handle.page.is_closed()

    async def test_closing_a_page_frees_its_pool_slot(self) -> None:
        """Closing a checked-out page (directly, not via the handle) frees its slot."""
        pool = Pages(context=MockContext(), max_size=5)  # type: ignore[arg-type]
        handle = await pool.get()
        await handle.page.close()
        assert pool.size == 0

    async def test_max_size_blocks_until_a_slot_frees(self) -> None:
        """A `get()` beyond `max_size` waits until an existing page closes."""
        import anyio

        pool = Pages(context=MockContext(), max_size=1)  # type: ignore[arg-type]
        first = await pool.get()

        second_acquired = anyio.Event()

        async def acquire_second() -> None:
            await pool.get()
            second_acquired.set()

        async with anyio.create_task_group() as tg:
            tg.start_soon(acquire_second)
            await anyio.sleep(0.01)
            assert not second_acquired.is_set()  # still blocked, pool at capacity

            await first.page.close()
            with anyio.fail_after(1.0):
                await second_acquired.wait()

    async def test_close_closes_every_checked_out_page(self) -> None:
        """`Pages.close()` closes every page still checked out."""
        pool = Pages(context=MockContext(), max_size=5)  # type: ignore[arg-type]
        await pool.get()
        await pool.get()
        assert pool.size == 2
        await pool.close()
        assert pool.size == 0

    async def test_close_does_not_abort_if_one_page_fails_to_close(self) -> None:
        """
        If one page's `close()` fails, `Pages.close()` still attempts every
        other page rather than stopping partway through and leaking the rest.
        """
        context = MockContext()
        pool = Pages(context=context, max_size=5)  # type: ignore[arg-type]

        context.next_fail_close = True
        await pool.get()  # this page will fail to close
        good_handle = await pool.get()  # this one should still get closed

        await pool.close()  # must not raise
        assert good_handle.page.is_closed()

    async def test_get_times_out_when_the_pool_stays_full(self) -> None:
        """
        A `get()` that can't be served within `acquire_timeout` raises, instead of
        waiting forever, and the pool's accounting is untouched by the failed wait.
        """
        pool = Pages(context=MockContext(), max_size=1, acquire_timeout=50)  # type: ignore[arg-type]
        first = await pool.get()

        with pytest.raises(PagePoolTimeoutError, match=r"1/1 pages in use"):
            await pool.get()

        assert pool.size == 1
        await first.page.close()
        assert pool.size == 0
        await asyncio.wait_for(pool.get(), timeout=1.0)  # the slot was not lost
        assert pool.size == 1

    async def test_timeout_error_says_which_pages_were_never_used(self) -> None:
        pool = Pages(context=MockContext(), max_size=2, acquire_timeout=30)  # type: ignore[arg-type]
        used = await pool.get()
        unused = await pool.get()
        used.page.url = "https://glossary.slb.com/en/terms/p/porosity"  # type: ignore[misc]
        unused.page.url = "about:blank"  # type: ignore[misc]

        with pytest.raises(PagePoolTimeoutError) as excinfo:
            await pool.get()

        message = str(excinfo.value)
        assert "porosity" in message
        assert "1 still on about:blank" in message
        assert "never used" in message

    async def test_timeout_is_a_timeout_error_and_a_browser_error(self) -> None:
        assert issubclass(PagePoolTimeoutError, TimeoutError)
        assert issubclass(PagePoolTimeoutError, BrowserError)

    async def test_per_call_timeout_overrides_the_pools_default(self) -> None:
        pool = Pages(context=MockContext(), max_size=1, acquire_timeout=None)  # type: ignore[arg-type]
        await pool.get()
        with pytest.raises(PagePoolTimeoutError):
            await pool.get(timeout=30)

    async def test_default_timeout_comes_from_the_constant_and_zero_means_forever(self) -> None:
        default = Pages(context=MockContext(), max_size=1)  # type: ignore[arg-type]
        assert default.acquire_timeout == (constants.page_acquire_timeout or None)
        assert Pages(context=MockContext(), max_size=1, acquire_timeout=0).acquire_timeout is None  # type: ignore[arg-type]

    @pytest.mark.parametrize("timeout", [None, 5_000])
    async def test_a_cancelled_wait_does_not_lose_or_invent_a_slot(
        self, timeout: float | None
    ) -> None:
        """Cancelling a caller that is waiting for a page leaves the pool exactly as it was."""
        pool = Pages(context=MockContext(), max_size=1, acquire_timeout=timeout)  # type: ignore[arg-type]
        first = await pool.get()

        waiter = asyncio.create_task(pool.get())
        await asyncio.sleep(0.02)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter

        await first.page.close()
        second = await asyncio.wait_for(pool.get(), timeout=1.0)  # exactly one slot is free
        assert pool.size == 1
        with pytest.raises(PagePoolTimeoutError):  # and not two
            await pool.get(timeout=30)
        await second.page.close()

    async def test_a_get_cancelled_while_the_page_opens_gives_its_slot_back(self) -> None:
        """Cancellation is not an `Exception`: it used to skip the slot release and lose it for good."""

        class SlowContext(MockContext):
            async def new_page(self) -> MockPage:
                await asyncio.sleep(30)
                return await super().new_page()

        pool = Pages(context=SlowContext(), max_size=1, acquire_timeout=None)  # type: ignore[arg-type]
        opening = asyncio.create_task(pool.get())
        await asyncio.sleep(0.02)
        opening.cancel()
        with pytest.raises(asyncio.CancelledError):
            await opening

        pool.context = MockContext()  # type: ignore[assignment]
        await asyncio.wait_for(pool.get(), timeout=1.0)
        assert pool.size == 1

    async def test_get_after_close_raises(self) -> None:
        """`get()` on an already-closed pool raises rather than opening a page anyway."""
        pool = Pages(context=MockContext(), max_size=5)  # type: ignore[arg-type]
        await pool.close()
        with pytest.raises(BrowserError):
            await pool.get()


class TestPageHandle:
    async def test_context_manager_closes_the_page(self) -> None:
        """Using a handle as `async with` closes the page on exit."""
        pool = Pages(context=MockContext(), max_size=5)  # type: ignore[arg-type]
        async with await pool.get() as page:
            assert not page.is_closed()
        assert page.is_closed()

    async def test_context_manager_does_not_raise_if_close_fails(self) -> None:
        """A failure closing the page on `__aexit__` is logged, not raised."""
        context = MockContext()
        context.next_fail_close = True
        pool = Pages(context=context, max_size=5)  # type: ignore[arg-type]

        async with await pool.get() as page:
            pass
        assert not page.is_closed()  # the close attempt failed, as configured

    async def test_context_manager_is_a_no_op_if_page_already_closed(self) -> None:
        """`__aexit__` does not attempt to close a page that's already closed."""
        pool = Pages(context=MockContext(), max_size=5)  # type: ignore[arg-type]
        handle = await pool.get()
        await handle.page.close()
        async with handle:
            pass  # should not raise or double-close
