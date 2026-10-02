"""Tests for `live.types`: `safe_close`, `Pages`, `PageHandle`."""

import logging
import typing

import pytest

from slb_glossary.live.types import Pages, safe_close
from tests.mocks import MockPage

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


@pytest.fixture
def anyio_backend(
    anyio_backend_asyncio_only: tuple[str, dict[str, typing.Any]],
) -> tuple[str, dict[str, typing.Any]]:
    """`Pages` uses `asyncio.Semaphore` internally, which isn't trio-safe."""
    return anyio_backend_asyncio_only


class FakeContext:
    """Minimal stand-in for `BrowserContext`: just `new_page()`, returning `MockPage`s."""

    def __init__(self) -> None:
        self.created: list[MockPage] = []
        self.next_fail_close = False

    async def new_page(self) -> MockPage:
        page = MockPage(fail_close=self.next_fail_close)
        self.next_fail_close = False
        self.created.append(page)
        return page


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
        pool = Pages(context=FakeContext(), max_size=5)  # type: ignore[arg-type]
        handle = await pool.get()
        assert pool.size == 1
        assert not handle.page.is_closed()

    async def test_closing_a_page_frees_its_pool_slot(self) -> None:
        """Closing a checked-out page (directly, not via the handle) frees its slot."""
        pool = Pages(context=FakeContext(), max_size=5)  # type: ignore[arg-type]
        handle = await pool.get()
        await handle.page.close()
        assert pool.size == 0

    async def test_max_size_blocks_until_a_slot_frees(self) -> None:
        """A `get()` beyond `max_size` waits until an existing page closes."""
        import anyio

        pool = Pages(context=FakeContext(), max_size=1)  # type: ignore[arg-type]
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
        pool = Pages(context=FakeContext(), max_size=5)  # type: ignore[arg-type]
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
        context = FakeContext()
        pool = Pages(context=context, max_size=5)  # type: ignore[arg-type]

        context.next_fail_close = True
        await pool.get()  # this page will fail to close
        good_handle = await pool.get()  # this one should still get closed

        await pool.close()  # must not raise
        assert good_handle.page.is_closed()

    async def test_get_after_close_raises(self) -> None:
        """`get()` on an already-closed pool raises rather than opening a page anyway."""
        from slb_glossary.errors import BrowserError

        pool = Pages(context=FakeContext(), max_size=5)  # type: ignore[arg-type]
        await pool.close()
        with pytest.raises(BrowserError):
            await pool.get()


class TestPageHandle:
    async def test_context_manager_closes_the_page(self) -> None:
        """Using a handle as `async with` closes the page on exit."""
        pool = Pages(context=FakeContext(), max_size=5)  # type: ignore[arg-type]
        async with await pool.get() as page:
            assert not page.is_closed()
        assert page.is_closed()

    async def test_context_manager_does_not_raise_if_close_fails(self) -> None:
        """A failure closing the page on `__aexit__` is logged, not raised."""
        context = FakeContext()
        context.next_fail_close = True
        pool = Pages(context=context, max_size=5)  # type: ignore[arg-type]

        async with await pool.get() as page:
            pass
        assert not page.is_closed()  # the close attempt failed, as configured

    async def test_context_manager_is_a_no_op_if_page_already_closed(self) -> None:
        """`__aexit__` does not attempt to close a page that's already closed."""
        pool = Pages(context=FakeContext(), max_size=5)  # type: ignore[arg-type]
        handle = await pool.get()
        await handle.page.close()
        async with handle:
            pass  # should not raise or double-close
