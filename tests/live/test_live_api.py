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
    term_name: str | None,
    detail_sections: list[list[TermBlock]],
) -> None:
    """Stub out the parser calls `get_results_from_url` makes on the page."""

    async def mock_get_term_name(page: object) -> str | None:
        return term_name

    async def mock_get_term_detail_blocks(page: object) -> list[list[TermBlock]]:
        return detail_sections

    async def mock_get_term_images(page: object) -> list[None]:
        return [None] * len(detail_sections)

    monkeypatch.setattr(api_module, "get_term_name", mock_get_term_name)
    monkeypatch.setattr(api_module, "get_term_detail_blocks", mock_get_term_detail_blocks)
    monkeypatch.setattr(api_module, "get_term_images", mock_get_term_images)


class TestGetResultsFromUrlParseFailures:
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

    async def test_missing_term_name_raises_parsing_error(self, monkeypatch: pytest.MonkeyPatch):
        """No term name heading found -> `ParsingError`, not a silent empty result."""
        patch_parsers(monkeypatch, term_name=None, detail_sections=[DETAIL_SECTION])
        with pytest.raises(ParsingError, match="term name"):
            async for _ in api_module.get_results_from_url(
                MockSession(), "https://x.com/broken", page=MockPage()
            ):
                pass

    async def test_missing_term_name_error_names_the_url(self, monkeypatch: pytest.MonkeyPatch):
        """The raised error carries the URL for diagnosis, without dumping page content."""
        patch_parsers(monkeypatch, term_name=None, detail_sections=[DETAIL_SECTION])
        with pytest.raises(ParsingError, match=r"https://x.com/broken"):
            async for _ in api_module.get_results_from_url(
                MockSession(), "https://x.com/broken", page=MockPage()
            ):
                pass

    async def test_empty_definition_sections_raises_parsing_error(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """A term name but zero definition sections -> `ParsingError`, not a silent empty result."""
        patch_parsers(monkeypatch, term_name="Porosity", detail_sections=[])
        with pytest.raises(ParsingError, match="definition sections"):
            async for _ in api_module.get_results_from_url(
                MockSession(), "https://x.com/porosity", page=MockPage()
            ):
                pass

    async def test_unexpected_parser_exception_propagates_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """A genuine bug in the parser layer isn't converted into an empty result either."""

        async def raising_get_term_name(page: object) -> str | None:
            raise ValueError("boom")

        monkeypatch.setattr(api_module, "get_term_name", raising_get_term_name)
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
