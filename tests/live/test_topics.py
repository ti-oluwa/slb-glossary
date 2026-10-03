"""
Tests for `slb_glossary.live.topics.fetch_topics`' wait for the topic list to expand.

Uses `anyio_backend_asyncio_only`: the code under test waits with `asyncio.sleep`.
"""

import asyncio
import typing

import pytest

from slb_glossary.live import topics as topics_module
from tests.mocks import MockLocator, MockPage

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


@pytest.fixture
def anyio_backend(
    anyio_backend_asyncio_only: tuple[str, dict[str, typing.Any]],
) -> tuple[str, dict[str, typing.Any]]:
    return anyio_backend_asyncio_only


@pytest.fixture
def page(monkeypatch: pytest.MonkeyPatch) -> MockPage:
    """A page whose facet header loads, with a "more" button that is clickable."""

    async def no_cookie_modal(*args: object, **kwargs: object) -> None:
        return None

    async def facet_topics(page: object) -> dict[str, int]:
        return {"Drilling": 3}

    async def glossary_size(page: object) -> int:
        return 3

    monkeypatch.setattr(topics_module, "resolve_cookie_modal", no_cookie_modal)
    monkeypatch.setattr(topics_module, "get_facet_topics", facet_topics)
    monkeypatch.setattr(topics_module, "get_glossary_size", glossary_size)

    page = MockPage()
    page.locators[topics_module.FACET_HEADER_SELECTOR] = MockLocator(text="Topics")
    page.locators[topics_module.FACET_MORE_SELECTOR] = MockLocator()
    return page


class TestFetchTopicsExpansion:
    async def test_stops_polling_once_the_facet_reports_expanded(self, page: MockPage) -> None:
        more_button = page.locators[topics_module.FACET_MORE_SELECTOR]
        more_button.evaluate_result = True

        topics, size = await topics_module.fetch_topics(
            page, base_url="https://x.com", settle_delay=60
        )

        assert (topics, size) == ({"Drilling": 3}, 3)
        assert more_button.evaluate_calls == 1

    async def test_gives_up_after_settle_delay_milliseconds_if_it_never_expands(
        self, page: MockPage
    ) -> None:
        """
        `settle_delay` is in milliseconds. It was once compared against a running total in
        seconds, so a facet that never expanded was polled for roughly 1000x too long.
        """
        more_button = page.locators[topics_module.FACET_MORE_SELECTOR]
        more_button.evaluate_result = False

        topics, size = await asyncio.wait_for(
            topics_module.fetch_topics(page, base_url="https://x.com", settle_delay=60),
            timeout=3.0,
        )

        assert (topics, size) == ({"Drilling": 3}, 3), "it still returns what it can read"
        assert 1 <= more_button.evaluate_calls <= 3

    async def test_skips_expanding_when_there_is_no_more_button(self, page: MockPage) -> None:
        more_button = page.locators[topics_module.FACET_MORE_SELECTOR]
        more_button.matches = 0

        await topics_module.fetch_topics(page, base_url="https://x.com", settle_delay=60)

        assert more_button.evaluate_calls == 0
