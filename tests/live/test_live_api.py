import dataclasses

import pytest

from slb_glossary.errors import NetworkError, ParsingError
from slb_glossary.live import api as api_module
from slb_glossary.live.parsers import TermBlock
from slb_glossary.retries import DEFAULT_RETRY_POLICY
from slb_glossary.types import Language, SearchResult

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


@pytest.fixture
def anyio_backend(anyio_backend_asyncio_only):
    return anyio_backend_asyncio_only


class MockPage:
    """Stands in for `patchright.async_api.Page`; `goto`/`close` are no-ops."""

    async def goto(self, url: str, *, timeout: float | None = None, wait_until: str = "") -> None:
        pass

    async def close(self) -> None:
        pass

    def is_closed(self) -> bool:
        return False


@dataclasses.dataclass
class MockSession:
    """Stands in for `slb_glossary.live.types.Session`; only what `get_results_from_url` reads."""

    language: Language = Language.ENGLISH
    topics: dict[str, int] = dataclasses.field(default_factory=dict)
    retry = DEFAULT_RETRY_POLICY
    initialized: bool = True

    async def initialize(self) -> None:
        self.initialized = True

    async def new_page(self) -> MockPage:
        return MockPage()


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
    `ParsingError` themselves (see `tests/live/test_live_parsers.py`).
    What matters here is that `get_results_from_url` doesn't catch and
    swallow that (or any other) exception from them.
    """

    async def test_valid_result_with_content_succeeds(self, monkeypatch: pytest.MonkeyPatch):
        """The happy path: a term name and at least one definition section yields normally."""
        patch_parsers(monkeypatch, term_name="Porosity", detail_sections=[DETAIL_SECTION])
        results = [
            result
            async for result in api_module.get_results_from_url(
                MockSession(), "https://x.com/porosity", page=MockPage()
            )
        ]
        assert [r.term for r in results] == ["Porosity"]

    async def test_parsing_error_from_get_term_name_propagates(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """A `ParsingError` from `get_term_name` isn't caught into an empty result."""
        patch_parsers(
            monkeypatch,
            term_name=ParsingError("could not parse a term name"),
            detail_sections=[DETAIL_SECTION],
        )
        with pytest.raises(ParsingError, match="term name"):
            async for _ in api_module.get_results_from_url(
                MockSession(), "https://x.com/broken", page=MockPage()
            ):
                pass

    async def test_parsing_error_from_get_term_detail_blocks_propagates(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """A `ParsingError` from `get_term_detail_blocks` isn't caught into an empty result."""
        patch_parsers(
            monkeypatch,
            term_name="Porosity",
            detail_sections=ParsingError("could not parse definition sections"),
        )
        with pytest.raises(ParsingError, match="definition sections"):
            async for _ in api_module.get_results_from_url(
                MockSession(), "https://x.com/porosity", page=MockPage()
            ):
                pass

    async def test_unexpected_parser_exception_propagates_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """A genuine bug in the parser layer isn't converted into an empty result either."""
        patch_parsers(monkeypatch, term_name=ValueError("boom"), detail_sections=[DETAIL_SECTION])
        with pytest.raises(ValueError, match="boom"):
            async for _ in api_module.get_results_from_url(
                MockSession(), "https://x.com/porosity", page=MockPage()
            ):
                pass


class TestGetResultsFromUrlsConcurrentFailureHandling:
    async def test_page_level_parsing_error_is_skipped_not_fatal(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """
        With `concurrency > 1`, one URL raising `ParsingError` is logged and
        skipped; the rest of the batch still completes.
        """

        async def mock_get_results_from_url(session, url, **kwargs):
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
                MockSession(),
                ["https://x.com/broken", "https://x.com/ok"],
                concurrency=2,
            )
        ]
        assert [r.term for r in results] == ["Term for https://x.com/ok"]

    async def test_network_error_is_skipped_not_fatal(self, monkeypatch: pytest.MonkeyPatch):
        """Same as above, for `NetworkError` (a transient per-page fetch failure)."""

        async def mock_get_results_from_url(session, url, **kwargs):
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
                MockSession(),
                ["https://x.com/unreachable", "https://x.com/ok"],
                concurrency=2,
            )
        ]
        assert [r.term for r in results] == ["Term for https://x.com/ok"]

    async def test_unexpected_exception_propagates_instead_of_vanishing(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """
        An unexpected exception (not `ParsingError`/`NetworkError`) must
        reach the caller, not be swallowed as an empty/partial result.
        """

        async def mock_get_results_from_url(session, url, **kwargs):
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
                MockSession(),
                ["https://x.com/buggy", "https://x.com/ok"],
                concurrency=2,
            ):
                pass
