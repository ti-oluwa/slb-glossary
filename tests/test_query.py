"""
Tests for `slb_glossary.query`: how `search` routes between the local database and the live
glossary for symbol-laden queries.

Uses `anyio_backend_asyncio_only`: the real `db` fixture is aiosqlite, which is not trio-safe.
"""

import asyncio
import typing

import pytest

from slb_glossary import live, query
from slb_glossary.local.api import upsert_results
from slb_glossary.local.types import Database
from slb_glossary.types import Source
from tests.factories import make_search_result
from tests.mocks import MockSession

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

TERMS = ["Capillary pressure", "Porous media", "Rig", "Water-cut", "Porosity"]


@pytest.fixture
def anyio_backend(
    anyio_backend_asyncio_only: tuple[str, dict[str, typing.Any]],
) -> tuple[str, dict[str, typing.Any]]:
    return anyio_backend_asyncio_only


@pytest.fixture
def live_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replaces the live search with one that finds nothing and records what it was asked for."""
    calls: list[str] = []

    async def fake_live_search(
        session: object, search_query: str, **_: typing.Any
    ) -> typing.AsyncIterator[typing.Any]:
        calls.append(search_query)
        return
        yield

    async def online() -> bool:
        return True

    monkeypatch.setattr(live, "search", fake_live_search)
    monkeypatch.setattr(query, "has_internet_connection", online)
    return calls


async def seed_terms(db: Database) -> None:
    await upsert_results(
        db,
        [
            make_search_result(url=f"https://x.com/{i}", term=term, definition=None)
            for i, term in enumerate(TERMS)
        ],
    )


async def search_terms(db: Database, text: str, *, source: Source = Source.AUTO) -> list[str]:
    return [
        result.value.term
        async for result in query.search(
            text,
            db=db,
            session=typing.cast(typing.Any, MockSession()),
            source=source,
            mode="lexical",
            persist=False,
        )
    ]


class TestSearchWithSymbols:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("capillary-", "Capillary pressure"),
            ("capillary-pressure", "Capillary pressure"),
            (":rig", "Rig"),
            ("porous?", "Porous media"),
            ("water cut", "Water-cut"),
            ("what is porosity?", "Porosity"),
        ],
    )
    async def test_is_served_locally_without_touching_live(
        self, db: Database, live_calls: list[str], text: str, expected: str
    ) -> None:
        """Symbols are noise when matching term names, so these are local hits, not live misses."""
        await seed_terms(db)
        assert (await search_terms(db, text))[0] == expected
        assert live_calls == []

    @pytest.mark.parametrize("source", [Source.AUTO, Source.LOCAL, Source.LIVE])
    async def test_symbol_only_query_yields_nothing_and_never_goes_live(
        self, db: Database, live_calls: list[str], source: Source
    ) -> None:
        """With no letters or digits there is nothing to look up, in any source."""
        await seed_terms(db)
        assert await search_terms(db, "???", source=source) == []
        assert live_calls == []

    async def test_genuinely_missing_term_still_falls_back_to_live_without_edge_symbols(
        self, db: Database, live_calls: list[str]
    ) -> None:
        await seed_terms(db)
        await search_terms(db, "permeability-")
        assert live_calls == ["permeability"]


class TestEarlyExitClosesTheLiveSearch:
    """
    A live search holds browser pages (and the base page) until its generator is closed. A
    consumer that returns early used to leave that to the garbage collector, so pages stayed
    taken, and `base_page_in_use` stayed set, for a while after the answer was already known.
    """

    @pytest.fixture
    def live_search(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, bool]:
        state = {"closed": False}

        async def fake_search(
            session: object, term: str, **kwargs: typing.Any
        ) -> typing.AsyncIterator[typing.Any]:
            try:
                yield make_search_result(term="Porosity", definition="d", url="https://x.com/p")
                await asyncio.sleep(30)  # still going when the consumer stops listening
            finally:
                state["closed"] = True

        monkeypatch.setattr(live, "search", fake_search)
        return state

    async def test_returning_an_exact_match_closes_the_search_straight_away(
        self, live_search: dict[str, bool]
    ) -> None:
        result = await query.lookup_live_term(
            typing.cast(typing.Any, MockSession()), "porosity", with_similar=False
        )
        assert result is not None
        assert live_search["closed"], (
            "closed before the call returned, not at some later garbage collection"
        )
