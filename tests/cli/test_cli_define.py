"""
Tests for `slb-glossary define`'s local-then-live routing under `--auto`.

The live lookup is faked, so no browser launches; what is checked is whether `define`
reached for it.
"""

import asyncio
import contextlib
import pathlib
import typing

import pytest
from click.testing import CliRunner

from slb_glossary import query
from slb_glossary.cli import source_options
from slb_glossary.cli.main import cli
from slb_glossary.local.api import upsert_results
from slb_glossary.local.connection import database
from slb_glossary.query import QueryResult, SimilarResult
from slb_glossary.types import SearchResult, Source
from tests.factories import make_search_result
from tests.mocks import MockSession

pytestmark = [pytest.mark.unit, pytest.mark.cli]

NEAR_MISSES = ["Drilling rate", "Drilling program", "Drilling procedure"]


@pytest.fixture
def db_path(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "test.db"


class FakeLive:
    """Controller for the `live` fixture: records the terms asked of the live site, and what it answers."""

    def __init__(self) -> None:
        self.asked: list[str] = []
        self.answer = SimilarResult(
            exact=QueryResult(
                value=make_search_result(
                    term="Drilling", definition="Boring a hole.", url="https://x.com/live/drilling"
                ),
                source=Source.LIVE,
                persisted=False,
                score=1.0,
            )
        )

    async def lookup_live_term(
        self, session: object, term: str, **kwargs: typing.Any
    ) -> QueryResult[SearchResult] | SimilarResult | None:
        self.asked.append(term)
        return self.answer if kwargs.get("with_similar") else self.answer.exact


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> FakeLive:
    """Fakes the live side of `define` (session, connectivity check, lookup); no browser launches."""
    fake = FakeLive()

    @contextlib.asynccontextmanager
    async def fake_live_session(ctx: object, params: object) -> typing.AsyncIterator[MockSession]:
        yield MockSession()

    async def online() -> bool:
        return True

    monkeypatch.setattr(source_options, "live_session", fake_live_session)
    monkeypatch.setattr(source_options, "has_internet_connection", online)
    monkeypatch.setattr(query, "lookup_live_term", fake.lookup_live_term)
    return fake


def seed(db_path: pathlib.Path, *terms: str) -> None:
    async def run() -> None:
        async with database(db_path) as db:
            await upsert_results(
                db,
                [
                    make_search_result(term=term, url=f"https://x.com/local/{i}")
                    for i, term in enumerate(terms)
                ],
            )

    asyncio.run(run())


def define(db_path: pathlib.Path, *args: str) -> typing.Any:
    return CliRunner().invoke(
        cli, ["define", "drilling", "--db-path", str(db_path), "--no-cache", *args]
    )


class TestDefineAuto:
    def test_only_near_misses_locally_asks_the_live_site(
        self, db_path: pathlib.Path, live: FakeLive
    ) -> None:
        """
        Alternatives are not an answer. Without an exact local match, `--auto` reaches
        the live site, where an exact `Drilling` may exist even though it isn't cached.
        """
        seed(db_path, *NEAR_MISSES)
        result = define(db_path)
        assert result.exit_code == 0, result.output
        assert live.asked == ["drilling"]
        assert "Boring a hole." in result.output

    def test_an_exact_local_match_never_reaches_live(
        self, db_path: pathlib.Path, live: FakeLive
    ) -> None:
        seed(db_path, *NEAR_MISSES, "Drilling")
        result = define(db_path)
        assert result.exit_code == 0, result.output
        assert live.asked == []

    def test_an_empty_local_database_asks_the_live_site(
        self, db_path: pathlib.Path, live: FakeLive
    ) -> None:
        seed(db_path)
        assert define(db_path).exit_code == 0
        assert live.asked == ["drilling"]

    def test_nothing_live_either_keeps_the_local_alternatives(
        self, db_path: pathlib.Path, live: FakeLive
    ) -> None:
        """The live site came back empty, so the suggestions already in hand are still offered."""
        seed(db_path, *NEAR_MISSES)
        live.answer = SimilarResult(exact=None)
        result = define(db_path)
        assert live.asked == ["drilling"]
        assert "drilling rate" in result.output.lower()

    def test_local_flag_stays_off_the_network(self, db_path: pathlib.Path, live: FakeLive) -> None:
        """Being explicit still works: `--local` never reaches live, even for near-misses only."""
        seed(db_path, *NEAR_MISSES)
        result = define(db_path, "--local")
        assert live.asked == []
        assert "drilling rate" in result.output.lower()

    def test_live_flag_goes_straight_to_live(self, db_path: pathlib.Path, live: FakeLive) -> None:
        seed(db_path, *NEAR_MISSES)
        assert define(db_path, "--live").exit_code == 0
        assert live.asked == ["drilling"]

    def test_without_suggestions_behaviour_is_unchanged(
        self, db_path: pathlib.Path, live: FakeLive
    ) -> None:
        seed(db_path, *NEAR_MISSES)
        assert define(db_path, "--no-suggest").exit_code == 0
        assert live.asked == ["drilling"]
