"""`query.search` serves symbol-laden queries locally instead of reaching out to the live site."""

import types
import typing

import pytest

from slb_glossary import query
from slb_glossary.local.api import upsert_results
from slb_glossary.local.types import Database
from tests.factories import make_search_result

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


@pytest.fixture
def live_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replaces the live search with one that records the queries it was asked for."""
    calls: list[str] = []

    async def fake_live_search(
        session: typing.Any, search_query: str, **_: typing.Any
    ) -> typing.AsyncIterator[typing.Any]:
        calls.append(search_query)
        return
        yield

    async def online() -> bool:
        return True

    monkeypatch.setattr(query.live, "search", fake_live_search)
    monkeypatch.setattr(query, "has_internet_connection", online)
    return calls


async def search_terms(db: Database, text: str) -> list[str]:
    session = types.SimpleNamespace(language=types.SimpleNamespace(value="en"))
    return [
        result.value.term
        async for result in query.search(
            text,
            db=db,
            session=typing.cast(typing.Any, session),
            source=query.Source.AUTO,
            mode="lexical",
            persist=False,
        )
    ]


class TestSearchAutoWithSymbols:
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
        await upsert_results(
            db,
            [
                make_search_result(url=f"https://x.com/{i}", term=term, definition=None)
                for i, term in enumerate(
                    ["Capillary pressure", "Porous media", "Rig", "Water-cut", "Porosity"]
                )
            ],
        )
        assert (await search_terms(db, text))[0] == expected
        assert live_calls == []

    async def test_symbol_only_query_yields_nothing_and_never_goes_live(
        self, db: Database, live_calls: list[str]
    ) -> None:
        assert await search_terms(db, "???") == []
        assert live_calls == []

    async def test_genuinely_missing_term_still_falls_back_to_live(
        self, db: Database, live_calls: list[str]
    ) -> None:
        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Rig")])
        await search_terms(db, "permeability-")
        assert live_calls == ["permeability"]
