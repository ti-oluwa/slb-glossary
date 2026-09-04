"""
`SearchResult`, `RelatedTerm`, `Language`, `SearchMode`, `Updatable`, and `RecordLike` shape/behavior.
"""

import dataclasses
import typing

import pytest

from slb_glossary.constants import SEARCH_MODES
from slb_glossary.types import (
    Language,
    RelatedTerm,
    SearchMode,
    SearchResult,
    Updatable,
    materialize_records,
)

pytestmark = pytest.mark.unit


class TestSearchResult:
    def test_fields_matches_namedtuple_fields(self) -> None:
        """`.fields` reflects the namedtuple's actual field names, in order."""
        result = SearchResult(
            term="Porosity", definition=None, grammatical_label=None, topic=None, url=None
        )
        assert result.fields == list(result._fields)

    def test_asdict_round_trips_every_field(self) -> None:
        """`.asdict()` has one key per field, with values matching the instance."""
        result = SearchResult(
            term="Porosity",
            definition="A rock property",
            grammatical_label="Noun",
            topic="Geology",
            url="https://example.com/porosity",
        )
        as_dict = result.asdict()
        assert set(as_dict) == set(result.fields)
        for field in result.fields:
            assert as_dict[field] == getattr(result, field)

    def test_is_a_plain_namedtuple(self) -> None:
        """Indexing and unpacking both work, since `SearchResult` is a plain `NamedTuple`."""
        result = SearchResult(
            term="Porosity", definition="def", grammatical_label=None, topic=None, url=None
        )
        assert result[0] == "Porosity"
        term, definition, *_ = result
        assert term == "Porosity"
        assert definition == "def"

    def test_defaults_for_optional_fields(self) -> None:
        """`image`/`image_caption`/`related` default to `None` when omitted."""
        result = SearchResult(
            term="Porosity", definition=None, grammatical_label=None, topic=None, url=None
        )
        assert result.image is None
        assert result.image_caption is None
        assert result.related is None
        assert result.language == "en"


class TestRelatedTerm:
    def test_asdict_round_trips_both_fields(self) -> None:
        """`.asdict()`/`._asdict()` reflects both `term` and `url`."""
        related = RelatedTerm(term="Permeability", url="https://example.com/permeability")
        assert related._asdict() == {
            "term": "Permeability",
            "url": "https://example.com/permeability",
        }


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class FrozenThing(Updatable):
    a: int = 1
    b: str = "x"


@dataclasses.dataclass(slots=True, kw_only=True)
class MutableThing(Updatable):
    a: int = 1
    b: str = "x"


class TestUpdatable:
    def test_update_returns_new_instance_with_changes_applied(self) -> None:
        """`.update(x=1)` on a frozen `Updatable` subclass returns a new object, original untouched."""
        original = FrozenThing(a=1, b="x")
        updated = original.update(a=2)
        assert updated is not original
        assert updated.a == 2
        assert original.a == 1

    def test_update_with_no_changes_returns_equal_instance(self) -> None:
        """Calling `.update()` with no changes returns an equal (here, identical) instance."""
        original = FrozenThing(a=1, b="x")
        updated = original.update()
        assert updated is original
        assert updated == original

    def test_update_rejects_unknown_field(self) -> None:
        """`.update(unknown=1)` raises `TypeError` naming the bad field."""
        original = FrozenThing(a=1, b="x")
        with pytest.raises(TypeError, match="unknown"):
            original.update(unknown=1)

    def test_update_mutates_a_non_frozen_dataclass_in_place(self) -> None:
        """`.update()` on a non-frozen dataclass mutates `self` and returns it."""
        original = MutableThing(a=1, b="x")
        updated = original.update(a=5)
        assert updated is original
        assert original.a == 5

    def test_update_on_non_dataclass_raises_type_error(self) -> None:
        """`.update()` on a class that isn't a `dataclasses.dataclass` raises `TypeError`."""

        class NotADataclass(Updatable):
            pass

        with pytest.raises(TypeError):
            NotADataclass().update(a=1)


class TestLanguage:
    @pytest.mark.parametrize(
        ("member", "value"),
        [(Language.ENGLISH, "en"), (Language.SPANISH, "es")],
    )
    def test_values(self, member: Language, value: str) -> None:
        """Each `Language` member's `.value` matches its glossary URL/query language code."""
        assert member.value == value


class TestSearchMode:
    @pytest.mark.parametrize(
        "member", [SearchMode.LEXICAL, SearchMode.SEMANTIC, SearchMode.HYBRID]
    )
    def test_values(self, member: SearchMode) -> None:
        """Every `SearchMode` member's value is one of `constants.SEARCH_MODES`."""
        assert member.value in SEARCH_MODES

    def test_search_modes_constant_matches_enum_membership(self) -> None:
        """`SEARCH_MODES` has exactly one entry per `SearchMode` member."""
        assert SEARCH_MODES == {mode.value for mode in SearchMode}


@pytest.mark.anyio
async def test_materialize_records_collects_async_iterator_of_recordlike(
    anyio_backend: tuple[str, dict[str, typing.Any]],
) -> None:
    """`materialize_records` collects an async generator of `SearchResult`s into a plain list, in order."""

    async def generate() -> typing.AsyncIterator[SearchResult]:
        yield SearchResult(term="A", definition=None, grammatical_label=None, topic=None, url=None)
        yield SearchResult(term="B", definition=None, grammatical_label=None, topic=None, url=None)

    materialized = await materialize_records(generate())
    assert isinstance(materialized, list)
    assert [r.term for r in materialized] == ["A", "B"]


@pytest.mark.anyio
async def test_materialize_records_collects_sync_iterable(
    anyio_backend: tuple[str, dict[str, typing.Any]],
) -> None:
    """`materialize_records` also accepts a plain sync iterable, returning it as a list."""
    results = [
        SearchResult(term="A", definition=None, grammatical_label=None, topic=None, url=None)
    ]
    materialized = await materialize_records(results)
    assert materialized == results
    assert materialized is not results
