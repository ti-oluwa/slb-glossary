import asyncio
import typing

import pytest

from slb_glossary.errors import NetworkError, PagePoolTimeoutError, ParsingError
from slb_glossary.live import api as api_module
from slb_glossary.live.parsers import TermBlock
from slb_glossary.types import SearchResult
from tests.mocks import MockPage, MockPooledSession, MockSession

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


DETAIL_SECTION = [
    TermBlock(text="1. n. [Drilling]", links=()),
    TermBlock(text="A measure of pore space.", links=()),
]


def patch_parsers(
    monkeypatch: pytest.MonkeyPatch,
    *,
    term_name: str | Exception = "Porosity",
    detail_sections: list[list[TermBlock]] | Exception,
) -> None:
    """
    Stub out the parser calls `get_results_from_url` makes on the page.

    `term_name`/`detail_sections` can each be a plain return value, or an
    `Exception` instance to raise instead - simulating `get_term_name`/
    `get_term_detail_blocks` themselves raising `ParsingError` on a
    structural parse failure, which is where that raise actually lives
    (see `slb_glossary.live.parsers`); `get_results_from_url` itself just
    calls them and doesn't inspect what they return.
    """

    async def mock_get_term_name(page: object) -> str:
        if isinstance(term_name, Exception):
            raise term_name
        return term_name

    async def mock_get_term_detail_blocks(page: object) -> list[list[TermBlock]]:
        if isinstance(detail_sections, Exception):
            raise detail_sections
        return detail_sections

    async def mock_get_term_images(page: object) -> list[None]:
        sections = detail_sections if isinstance(detail_sections, list) else []
        return [None] * len(sections)

    monkeypatch.setattr(api_module, "get_term_name", mock_get_term_name)
    monkeypatch.setattr(api_module, "get_term_detail_blocks", mock_get_term_detail_blocks)
    monkeypatch.setattr(api_module, "get_term_images", mock_get_term_images)


class TestGetResultsFromUrlParseFailures:
    """
    `get_results_from_url` doesn't itself decide what counts as a parse
    failure anymore - `get_term_name`/`get_term_detail_blocks` raise
    `ParsingError` themselves (see `tests/live/test_parsers.py`).
    What matters here is that `get_results_from_url` doesn't catch and
    swallow that (or any other) exception from them.
    """

    async def test_valid_result_with_content_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The happy path: a term name and at least one definition section yields normally."""
        patch_parsers(monkeypatch, term_name="Porosity", detail_sections=[DETAIL_SECTION])
        results = [
            result
            async for result in api_module.get_results_from_url(
                MockSession(initialized=True), "https://x.com/porosity", page=MockPage()
            )
        ]
        assert [result.term for result in results] == ["Porosity"]

    async def test_parsing_error_from_get_term_name_propagates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A `ParsingError` from `get_term_name` isn't caught into an empty result."""
        patch_parsers(
            monkeypatch,
            term_name=ParsingError("could not parse a term name"),
            detail_sections=[DETAIL_SECTION],
        )
        with pytest.raises(ParsingError, match="term name"):
            async for _ in api_module.get_results_from_url(
                MockSession(initialized=True), "https://x.com/broken", page=MockPage()
            ):
                pass

    async def test_parsing_error_from_get_term_detail_blocks_propagates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A `ParsingError` from `get_term_detail_blocks` isn't caught into an empty result."""
        patch_parsers(
            monkeypatch,
            term_name="Porosity",
            detail_sections=ParsingError("could not parse definition sections"),
        )
        with pytest.raises(ParsingError, match="definition sections"):
            async for _ in api_module.get_results_from_url(
                MockSession(initialized=True), "https://x.com/porosity", page=MockPage()
            ):
                pass

    async def test_unexpected_parser_exception_propagates_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A genuine bug in the parser layer isn't converted into an empty result either."""
        patch_parsers(monkeypatch, term_name=ValueError("boom"), detail_sections=[DETAIL_SECTION])
        with pytest.raises(ValueError, match="boom"):
            async for _ in api_module.get_results_from_url(
                MockSession(initialized=True), "https://x.com/porosity", page=MockPage()
            ):
                pass


class TestGetResultsFromUrlsConcurrentFailureHandling:
    """`get_results_from_urls` uses raw `asyncio.create_task`, not trio-safe."""

    @pytest.fixture
    def anyio_backend(
        self, anyio_backend_asyncio_only: tuple[str, dict[str, typing.Any]]
    ) -> tuple[str, dict[str, typing.Any]]:
        return anyio_backend_asyncio_only

    async def test_page_level_parsing_error_is_skipped_not_fatal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        With `concurrency > 1`, one URL raising `ParsingError` is logged and
        skipped; the rest of the batch still completes.
        """

        async def mock_get_results_from_url(
            session: MockSession, url: str, **kwargs: typing.Any
        ) -> typing.AsyncIterator[SearchResult]:
            if url == "https://x.com/broken":
                raise ParsingError("no term name")
                yield  # pragma: no cover - makes this an async generator
            else:
                yield SearchResult(
                    term=f"Term for {url}",
                    definition="",
                    grammatical_label=None,
                    topic=None,
                    url=url,
                )

        monkeypatch.setattr(api_module, "get_results_from_url", mock_get_results_from_url)

        results = [
            result
            async for result in api_module.get_results_from_urls(
                MockSession(initialized=True),
                ["https://x.com/broken", "https://x.com/ok"],
                concurrency=2,
            )
        ]
        assert [result.term for result in results] == ["Term for https://x.com/ok"]

    async def test_network_error_is_skipped_not_fatal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same as above, for `NetworkError` (a transient per-page fetch failure)."""

        async def mock_get_results_from_url(
            session: MockSession, url: str, **kwargs: typing.Any
        ) -> typing.AsyncIterator[SearchResult]:
            if url == "https://x.com/unreachable":
                raise NetworkError("could not reach page")
                yield  # pragma: no cover
            else:
                yield SearchResult(
                    term=f"Term for {url}",
                    definition="",
                    grammatical_label=None,
                    topic=None,
                    url=url,
                )

        monkeypatch.setattr(api_module, "get_results_from_url", mock_get_results_from_url)

        results = [
            result
            async for result in api_module.get_results_from_urls(
                MockSession(initialized=True),
                ["https://x.com/unreachable", "https://x.com/ok"],
                concurrency=2,
            )
        ]
        assert [result.term for result in results] == ["Term for https://x.com/ok"]

    async def test_unexpected_exception_propagates_instead_of_vanishing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        An unexpected exception (not `ParsingError`/`NetworkError`) must
        reach the caller, not be swallowed as an empty/partial result.
        """

        async def mock_get_results_from_url(
            session: MockSession, url: str, **kwargs: typing.Any
        ) -> typing.AsyncIterator[SearchResult]:
            if url == "https://x.com/buggy":
                raise ValueError("unexpected bug")
                yield  # pragma: no cover
            else:
                yield SearchResult(
                    term=f"Term for {url}",
                    definition="",
                    grammatical_label=None,
                    topic=None,
                    url=url,
                )

        monkeypatch.setattr(api_module, "get_results_from_url", mock_get_results_from_url)

        with pytest.raises(ValueError, match="unexpected bug"):
            async for _ in api_module.get_results_from_urls(
                MockSession(initialized=True),
                ["https://x.com/buggy", "https://x.com/ok"],
                concurrency=2,
            ):
                pass


class TestGetResultsFromUrlsPagePool:
    """
    Workers and the URL source all draw pages from one bounded pool. If workers sit on
    pages they are not using while the URL source waits for a page of its own (or while
    other calls do the same), nothing can finish. These use a real `Pages` pool to prove
    that no longer happens. They use raw `asyncio` tasks, so asyncio only.
    """

    @pytest.fixture
    def anyio_backend(
        self, anyio_backend_asyncio_only: tuple[str, dict[str, typing.Any]]
    ) -> tuple[str, dict[str, typing.Any]]:
        return anyio_backend_asyncio_only

    @pytest.fixture(autouse=True)
    def stub_fetch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fetch(
            session: MockPooledSession, url: str, **kwargs: typing.Any
        ) -> typing.AsyncIterator[SearchResult]:
            await asyncio.sleep(0.01)
            yield SearchResult(
                term=url, definition="", grammatical_label=None, topic=None, url=url
            )

        monkeypatch.setattr(api_module, "get_results_from_url", fetch)

    @staticmethod
    def paging_urls(
        session: MockPooledSession, count: int, started: asyncio.Event | None = None
    ) -> typing.AsyncIterator[str]:
        """Like `get_terms_urls` when the base page is busy: holds a page of its own while it pages."""

        async def urls() -> typing.AsyncIterator[str]:
            page = await session.new_page()
            try:
                if started is not None:
                    await started.wait()
                for index in range(count):
                    yield f"https://x.com/{index}"
            finally:
                await page.close()

        return urls()

    async def test_more_concurrency_than_the_pool_can_hold_does_not_hang(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """
        `concurrency=5` against `max_pages=4` (one already held, one needed by the URL
        source) used to open workers until the pool was full and then wait forever.
        """
        session = MockPooledSession(max_pages=4)
        base = await session.new_page()  # held, like `Session.base_page`

        async def collect() -> list[SearchResult]:
            return [
                result
                async for result in api_module.get_results_from_urls(
                    session,  # type: ignore[arg-type]
                    self.paging_urls(session, 10),
                    concurrency=5,
                )
            ]

        with caplog.at_level("WARNING", logger="slb_glossary.live.api"):
            results = await asyncio.wait_for(collect(), timeout=5.0)

        assert len(results) == 10
        assert "Using 2 worker(s) instead of the requested concurrency=5" in caplog.text
        assert session.pages.size == 1, "every worker and URL-source page was closed"
        assert not base.is_closed()

    async def test_overlapping_calls_do_not_deadlock_each_other(self) -> None:
        """
        Three calls at once, each with a URL source and two workers, want nine pages from a
        pool of six. Each used to grab part of what it needed and wait on the rest.
        """
        session = MockPooledSession(max_pages=6)

        async def one_call(tag: int) -> int:
            return len(
                [
                    result
                    async for result in api_module.get_results_from_urls(
                        session,  # type: ignore[arg-type]
                        self.paging_urls(session, 6),
                        concurrency=2,
                    )
                ]
            )

        counts = await asyncio.wait_for(asyncio.gather(*(one_call(i) for i in range(3))), 5.0)

        assert counts == [6, 6, 6]
        assert session.pages.size == 0

    async def test_workers_hold_no_page_until_they_have_a_url(self) -> None:
        """No more idle `about:blank` pages taking slots while the URL source works."""
        session = MockPooledSession(max_pages=6)
        release = asyncio.Event()
        results: list[SearchResult] = []

        async def consume() -> None:
            async for result in api_module.get_results_from_urls(
                session,  # type: ignore[arg-type]
                self.paging_urls(session, 3, started=release),
                concurrency=3,
            ):
                results.append(result)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        assert session.pages.size == 1, "only the URL source's page, none for idle workers"

        release.set()
        await asyncio.wait_for(task, timeout=5.0)
        assert len(results) == 3
        assert session.pages.size == 0

    async def test_no_urls_means_no_pages_are_opened(self) -> None:
        session = MockPooledSession(max_pages=3)
        for concurrency in (1, 3):
            results = [
                result
                async for result in api_module.get_results_from_urls(
                    session,  # type: ignore[arg-type]
                    [],
                    concurrency=concurrency,
                )
            ]
            assert results == []
        assert session.context.created == []

    async def test_cancelling_the_consumer_closes_every_page(self) -> None:
        """Cancellation (Ctrl-C, a timeout) used to strand worker pages, and their slots."""
        session = MockPooledSession(max_pages=6)

        async def slow_fetch(
            session: MockPooledSession, url: str, **kwargs: typing.Any
        ) -> typing.AsyncIterator[SearchResult]:
            await asyncio.sleep(30)
            yield SearchResult(
                term=url, definition="", grammatical_label=None, topic=None, url=url
            )

        async def consume() -> None:
            async for _ in api_module.get_results_from_urls(
                session,  # type: ignore[arg-type]
                self.paging_urls(session, 20),
                concurrency=3,
            ):
                pass

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(api_module, "get_results_from_url", slow_fetch)
            task = asyncio.create_task(consume())
            await asyncio.sleep(0.1)
            assert session.pages.size > 1
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        await asyncio.sleep(0.05)
        assert session.pages.size == 0
        assert all(page.is_closed() for page in session.context.created)

    async def test_an_exhausted_pool_raises_instead_of_hanging(self) -> None:
        """If the pool truly has no page to give, the call fails with a clear error."""
        session = MockPooledSession(max_pages=1, acquire_timeout=50)
        await session.new_page()  # the only page, held by someone else

        with pytest.raises(PagePoolTimeoutError):
            await asyncio.wait_for(
                drain(
                    api_module.get_results_from_urls(
                        session,  # type: ignore[arg-type]
                        ["https://x.com/a", "https://x.com/b"],
                        concurrency=2,
                    )
                ),
                timeout=5.0,
            )


async def drain(results: typing.AsyncIterator[SearchResult]) -> None:
    async for _ in results:
        pass
