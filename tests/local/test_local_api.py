"""
`local.api`: upsert (batch + incremental), `search`'s mode dispatch, term/topic
lookups (`get_term`, `get_terms_on`, `iter_terms`, `get_random_term`,
`get_terms_urls`, `get_topics`, `count`), and the `_apply_exclude`/
`fuzzy_match_topics`/`resolve_topic` helpers.
"""

import typing

import pytest

from slb_glossary.local import api
from slb_glossary.local.api import (
    _apply_exclude,
    _dump_related,
    _load_related,
    count,
    fuzzy_match_topics,
    get_random_term,
    get_term,
    get_term_definitions,
    get_terms_on,
    get_terms_urls,
    get_topics,
    iter_terms,
    resolve_topic,
    search,
    upsert_results,
    upsert_results_incrementally,
)
from slb_glossary.local.types import Database
from slb_glossary.types import SearchMode, SearchResult
from tests.factories import make_related_term, make_search_result, make_search_results

pytestmark = pytest.mark.unit


class TestDumpLoadRelated:
    def test_round_trips_related_terms(self) -> None:
        """A tuple of `RelatedTerm`s round-trips through dump then load unchanged."""
        related = (
            make_related_term(term="A", url="urlA"),
            make_related_term(term="B", url="urlB"),
        )
        assert _load_related(_dump_related(related)) == related

    def test_dump_none_or_empty_returns_none(self) -> None:
        """`None` or an empty tuple dumps to `None`, not an empty-array JSON string."""
        assert _dump_related(None) is None
        assert _dump_related(()) is None

    def test_load_none_or_empty_returns_none(self) -> None:
        """`None` or an empty string loads back to `None`."""
        assert _load_related(None) is None
        assert _load_related("") is None


class TestApplyExclude:
    def test_no_clause_added_when_exclude_is_none(self) -> None:
        """`exclude=None` leaves `sql`/`params` untouched."""
        sql, params = "SELECT * FROM terms WHERE 1=1", []
        result_sql = _apply_exclude(sql, params, None)
        assert result_sql == sql
        assert params == []

    def test_adds_url_not_in_clause(self) -> None:
        """A URL entry adds a `NOT IN` clause against `url_column`."""
        params: list = []
        sql = _apply_exclude("SELECT * FROM terms", params, ["https://example.com/porosity"])
        assert "url NOT IN" in sql
        assert params == ["https://example.com/porosity"]

    def test_adds_term_name_not_in_clause_normalized(self) -> None:
        """A term-name entry adds a case/whitespace-normalized `NOT IN` clause."""
        params: list = []
        sql = _apply_exclude("SELECT * FROM terms", params, ["  Porosity  "])
        assert "LOWER(TRIM(term)) NOT IN" in sql
        assert params == ["porosity"]

    def test_respects_custom_column_names(self) -> None:
        """Custom `url_column`/`term_column` are used in the generated clauses."""
        params: list = []
        sql = _apply_exclude(
            "SELECT * FROM terms",
            params,
            ["https://example.com/x", "Porosity"],
            url_column="terms.url",
            term_column="terms.term",
        )
        assert "terms.url NOT IN" in sql
        assert "LOWER(TRIM(terms.term)) NOT IN" in sql


class TestFuzzyMatchTopics:
    def test_empty_topic_returns_empty_string(self) -> None:
        """An empty `topic` returns `""` regardless of `topics`."""
        assert fuzzy_match_topics(["Geology"], "") == ""

    def test_empty_topics_returns_empty_string(self) -> None:
        """Empty `topics` returns `""` regardless of `topic`."""
        assert fuzzy_match_topics([], "Geology") == ""

    def test_exact_case_insensitive_match_returns_original_casing(self) -> None:
        """An exact (case-insensitive) match returns the topic in its originally stored casing."""
        assert fuzzy_match_topics(["Geology"], "geology") == "Geology"

    def test_close_misspelling_resolves_via_difflib(self) -> None:
        """A close misspelling resolves to its nearest stored topic name."""
        assert fuzzy_match_topics(["Geology", "Drilling"], "Geologyy") == "Geology"

    def test_no_close_match_is_dropped_silently(self) -> None:
        """A part with no close-enough match is dropped, not included as-is."""
        assert fuzzy_match_topics(["Geology"], "Completely Unrelated Topic") == ""

    def test_several_comma_separated_parts_resolve_independently(self) -> None:
        """Each comma-separated part of `topic` is resolved independently."""
        result = fuzzy_match_topics(["Geology", "Drilling"], "geology, drilling")
        assert result == "Geology,Drilling"

    def test_duplicate_resolved_topics_are_deduplicated(self) -> None:
        """Resolving to the same topic twice only appears once in the result."""
        result = fuzzy_match_topics(["Geology"], "geology,Geology")
        assert result == "Geology"


@pytest.mark.anyio
class TestUpsertResults:
    async def test_writes_results_and_returns_count(self, db: Database) -> None:
        """Writes every result with a `url` and returns how many rows were written."""
        results = make_search_results(3)
        written = await upsert_results(db, results)
        assert written == 3
        assert await count(db) == 3

    async def test_skips_results_with_no_url(self, db: Database) -> None:
        """A result with no `url` is skipped and does not count toward the return value."""
        results = [make_search_result(url=None), make_search_result(url="https://x.com/a")]
        written = await upsert_results(db, results)
        assert written == 1

    async def test_empty_input_writes_nothing(self, db: Database) -> None:
        """An empty `results` writes nothing and returns `0`."""
        assert await upsert_results(db, []) == 0

    async def test_conflicting_url_and_topic_upserts_in_place(self, db: Database) -> None:
        """Re-upserting the same `(url, topic)` updates the row rather than duplicating it."""
        first = make_search_result(url="https://x.com/a", topic="Geology", definition="old")
        second = make_search_result(url="https://x.com/a", topic="Geology", definition="new")
        await upsert_results(db, [first])
        await upsert_results(db, [second])
        assert await count(db) == 1
        stored = await get_term(db, "https://x.com/a")
        assert stored is not None
        assert stored.definition == "new"

    async def test_same_url_different_topic_creates_separate_rows(self, db: Database) -> None:
        """The same `url` under two different topics stores as two separate rows."""
        first = make_search_result(url="https://x.com/a", topic="Geology")
        second = make_search_result(url="https://x.com/a", topic="Drilling")
        await upsert_results(db, [first, second])
        assert await count(db) == 2

    async def test_language_override_forces_every_result_to_that_language(
        self, db: Database
    ) -> None:
        """A given `language=` overrides each result's own `.language` for storage."""
        result = make_search_result(url="https://x.com/a", language="es")
        await upsert_results(db, [result], language="en")
        stored = await get_term(db, "https://x.com/a")
        assert stored is not None
        assert stored.language == "en"

    async def test_accepts_an_async_iterable(self, db: Database) -> None:
        """`results` may be an async iterable, not just a plain list."""

        async def generate() -> typing.AsyncIterator[SearchResult]:
            for r in make_search_results(2):
                yield r

        written = await upsert_results(db, generate())
        assert written == 2


@pytest.mark.anyio
class TestUpsertResultsIncrementally:
    async def test_yields_every_result_unchanged(self, db: Database) -> None:
        """Every input result is yielded through unchanged, in order."""
        results = make_search_results(3)
        yielded = [r async for r in upsert_results_incrementally(db, results, batch_size=10)]
        assert yielded == results

    async def test_flushes_in_batches_of_batch_size(self, db: Database) -> None:
        """Results are persisted in batches of `batch_size`, not all at once at the end.

        The `len(buffer) >= batch_size` check runs when the generator is
        *resumed* (i.e. right after a `yield`), which happens only once the
        consumer asks for the *next* item - so the consumer observes each
        flush one item later than the item that triggered it. Verified
        directly: for 5 results and `batch_size=2`, the count sequence the
        consumer sees is `[0, 0, 2, 2, 4]`, not `[0, 2, 2, 4, 4]`."""
        results = make_search_results(5)
        seen_counts = []

        async def consume() -> None:
            async for _ in upsert_results_incrementally(db, results, batch_size=2):
                seen_counts.append(await count(db))

        await consume()
        assert seen_counts == [0, 0, 2, 2, 4]

    async def test_flushes_remainder_at_the_end(self, db: Database) -> None:
        """Whatever's left in the buffer once `results` ends is still flushed."""
        results = make_search_results(3)
        async for _ in upsert_results_incrementally(db, results, batch_size=10):
            pass
        assert await count(db) == 3

    async def test_stats_dict_populated_with_written_and_batches(self, db: Database) -> None:
        """`stats` is populated in place with `written`/`batches` once exhausted."""
        stats: dict[str, int] = {}
        results = make_search_results(4)
        async for _ in upsert_results_incrementally(db, results, batch_size=2, stats=stats):
            pass
        assert stats["written"] == 4
        assert stats["batches"] == 2

    async def test_persist_on_error_true_flushes_buffered_results_before_raising(
        self, db: Database
    ) -> None:
        """With `persist_on_error=True` (the default), a raise mid-stream still persists
        whatever was buffered so far."""

        async def generate() -> typing.AsyncIterator[SearchResult]:
            yield make_search_result(url="https://x.com/a", term="A")
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            async for _ in upsert_results_incrementally(db, generate(), batch_size=10):
                pass
        assert await count(db) == 1

    async def test_persist_on_error_false_discards_buffered_results(self, db: Database) -> None:
        """With `persist_on_error=False`, a raise mid-stream discards the unflushed buffer."""

        async def generate() -> typing.AsyncIterator[SearchResult]:
            yield make_search_result(url="https://x.com/a", term="A")
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            async for _ in upsert_results_incrementally(
                db, generate(), batch_size=10, persist_on_error=False
            ):
                pass
        assert await count(db) == 0

    async def test_raises_value_error_for_batch_size_below_one(self, db: Database) -> None:
        """`batch_size < 1` raises `ValueError` immediately."""
        with pytest.raises(ValueError, match="at least 1"):
            async for _ in upsert_results_incrementally(db, [], batch_size=0):
                pass


@pytest.mark.anyio
class TestSearchDispatch:
    async def test_lexical_mode_calls_lexical_search(
        self, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`mode="lexical"` (the default) dispatches to `lexical_search`."""
        called = []

        async def mock_lexical_search(*args: typing.Any, **kwargs: typing.Any) -> list:
            called.append("lexical")
            return []

        monkeypatch.setattr(api, "lexical_search", mock_lexical_search)
        await search(db, "porosity", mode="lexical")
        assert called == ["lexical"]

    async def test_semantic_mode_calls_vector_search(
        self, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`mode="semantic"` dispatches to `vector_search`."""
        called = []

        async def mock_vector_search(*args: typing.Any, **kwargs: typing.Any) -> list:
            called.append("semantic")
            return []

        monkeypatch.setattr(api, "vector_search", mock_vector_search)
        await search(db, "porosity", mode="semantic")
        assert called == ["semantic"]

    async def test_hybrid_mode_calls_hybrid_search(
        self, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`mode="hybrid"` dispatches to `hybrid_search`."""
        called = []

        async def mock_hybrid_search(*args: typing.Any, **kwargs: typing.Any) -> list:
            called.append("hybrid")
            return []

        monkeypatch.setattr(api, "hybrid_search", mock_hybrid_search)
        await search(db, "porosity", mode="hybrid")
        assert called == ["hybrid"]

    async def test_mode_accepts_searchmode_enum_member(
        self, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`mode` also accepts a `SearchMode` enum member directly, not just its string value."""
        called = []

        async def mock_lexical_search(*args: typing.Any, **kwargs: typing.Any) -> list:
            called.append("lexical")
            return []

        monkeypatch.setattr(api, "lexical_search", mock_lexical_search)
        await search(db, "porosity", mode=SearchMode.LEXICAL)
        assert called == ["lexical"]

    async def test_scored_false_strips_scores(
        self, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`scored=False` (the default) returns bare results, not `(result, score)` pairs."""
        result = make_search_result()

        async def mock_lexical_search(
            *args: typing.Any, **kwargs: typing.Any
        ) -> list[tuple[SearchResult, float]]:
            return [(result, 0.9)]

        monkeypatch.setattr(api, "lexical_search", mock_lexical_search)
        results = await search(db, "porosity", scored=False)
        assert results == [result]

    async def test_scored_true_keeps_scores(
        self, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`scored=True` returns `(result, score)` pairs unchanged."""
        result = make_search_result()

        async def mock_lexical_search(
            *args: typing.Any, **kwargs: typing.Any
        ) -> list[tuple[SearchResult, float]]:
            return [(result, 0.9)]

        monkeypatch.setattr(api, "lexical_search", mock_lexical_search)
        results = await search(db, "porosity", scored=True)
        assert results == [(result, 0.9)]


@pytest.mark.anyio
class TestResolveTopic:
    async def test_none_or_empty_topic_returns_none(self, db: Database) -> None:
        """`topic=None`/`""` resolves to `None` regardless of `fuzzy`."""
        assert await resolve_topic(db, None, False) is None
        assert await resolve_topic(db, "", True) is None

    async def test_non_fuzzy_returns_topic_unchanged(self, db: Database) -> None:
        """Without `fuzzy`, `topic` passes through unchanged (not validated against storage)."""
        assert await resolve_topic(db, "Anything At All", False) == "Anything At All"

    async def test_fuzzy_resolves_against_stored_topics(self, db: Database) -> None:
        """With `fuzzy=True`, `topic` is resolved against topics currently stored in `db`."""
        await upsert_results(db, [make_search_result(url="https://x.com/a", topic="Geology")])
        assert await resolve_topic(db, "geologyy", True) == "Geology"

    async def test_fuzzy_with_no_match_returns_none(self, db: Database) -> None:
        """With `fuzzy=True` and nothing close enough stored, resolves to `None`."""
        await upsert_results(db, [make_search_result(url="https://x.com/a", topic="Geology")])
        assert await resolve_topic(db, "Completely Unrelated", True) is None


@pytest.mark.anyio
class TestGetTermsOn:
    async def test_returns_terms_filed_under_topic(self, db: Database) -> None:
        """Returns every stored term filed under the given topic, ordered by term name."""
        await upsert_results(
            db,
            [
                make_search_result(url="https://x.com/b", term="Bravo", topic="Geology"),
                make_search_result(url="https://x.com/a", term="Alpha", topic="Geology"),
                make_search_result(url="https://x.com/c", term="Charlie", topic="Drilling"),
            ],
        )
        results = await get_terms_on(db, "Geology")
        assert [r.term for r in results] == ["Alpha", "Bravo"]

    async def test_unresolved_topic_returns_empty_list(self, db: Database) -> None:
        """A topic with nothing stored under it returns an empty list, not an error."""
        assert await get_terms_on(db, "Nonexistent Topic") == []

    async def test_respects_limit(self, db: Database) -> None:
        """`limit` caps the number of results returned."""
        await upsert_results(
            db,
            [
                make_search_result(url=f"https://x.com/{i}", term=f"T{i}", topic="Geology")
                for i in range(5)
            ],
        )
        results = await get_terms_on(db, "Geology", limit=2)
        assert len(results) == 2

    async def test_excludes_given_urls_and_names(self, db: Database) -> None:
        """`exclude` filters out matching URLs/term names before `limit` is applied."""
        await upsert_results(
            db,
            [
                make_search_result(url="https://x.com/a", term="Alpha", topic="Geology"),
                make_search_result(url="https://x.com/b", term="Bravo", topic="Geology"),
            ],
        )
        results = await get_terms_on(db, "Geology", exclude=["Alpha"])
        assert [r.term for r in results] == ["Bravo"]


@pytest.mark.anyio
class TestIterTerms:
    async def test_streams_every_term_ordered_by_name(self, db: Database) -> None:
        """With no filters, streams every stored term, ordered by term name."""
        await upsert_results(
            db,
            [
                make_search_result(url="https://x.com/b", term="Bravo"),
                make_search_result(url="https://x.com/a", term="Alpha"),
            ],
        )
        results = [r async for r in iter_terms(db)]
        assert [r.term for r in results] == ["Alpha", "Bravo"]

    async def test_respects_batch_size_across_multiple_fetches(self, db: Database) -> None:
        """A small `batch_size` still yields every matching row, across several internal fetches."""
        await upsert_results(
            db, [make_search_result(url=f"https://x.com/{i}", term=f"T{i}") for i in range(5)]
        )
        results = [r async for r in iter_terms(db, batch_size=2)]
        assert len(results) == 5

    async def test_respects_limit(self, db: Database) -> None:
        """`limit` caps the number of results streamed."""
        await upsert_results(
            db, [make_search_result(url=f"https://x.com/{i}", term=f"T{i}") for i in range(5)]
        )
        results = [r async for r in iter_terms(db, limit=2)]
        assert len(results) == 2

    async def test_filters_by_topic_start_letter_and_language(self, db: Database) -> None:
        """`topic`/`start_letter`/`language` filters combine correctly."""
        await upsert_results(
            db,
            [
                make_search_result(
                    url="https://x.com/a", term="Alpha", topic="Geology", language="en"
                ),
                make_search_result(
                    url="https://x.com/b", term="Beta", topic="Geology", language="es"
                ),
                make_search_result(
                    url="https://x.com/c", term="Charlie", topic="Drilling", language="en"
                ),
            ],
        )
        results = [
            r async for r in iter_terms(db, topic="Geology", start_letter="A", language="en")
        ]
        assert [r.term for r in results] == ["Alpha"]


@pytest.mark.anyio
class TestGetTerm:
    async def test_finds_by_exact_url(self, db: Database) -> None:
        """Looks up a stored term by its exact URL."""
        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Alpha")])
        result = await get_term(db, "https://x.com/a")
        assert result is not None
        assert result.term == "Alpha"

    async def test_finds_by_case_insensitive_term_name(self, db: Database) -> None:
        """Looks up a stored term by its (case-insensitive) exact term name."""
        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Alpha")])
        result = await get_term(db, "ALPHA")
        assert result is not None
        assert result.url == "https://x.com/a"

    async def test_returns_none_when_not_found(self, db: Database) -> None:
        """Returns `None`, not an error, when nothing matches."""
        assert await get_term(db, "Nonexistent") is None

    async def test_topic_disambiguates_multiple_definitions(self, db: Database) -> None:
        """`topic` picks a specific definition when a URL has several."""
        await upsert_results(
            db,
            [
                make_search_result(url="https://x.com/a", topic="Geology", definition="geo def"),
                make_search_result(
                    url="https://x.com/a", topic="Drilling", definition="drill def"
                ),
            ],
        )
        result = await get_term(db, "https://x.com/a", topic="Drilling")
        assert result is not None
        assert result.definition == "drill def"

    async def test_with_similar_returns_result_and_alternatives(self, db: Database) -> None:
        """`with_similar=True` returns `(result, similar)`, `similar` excluding the exact match."""
        await upsert_results(
            db,
            [
                make_search_result(url="https://x.com/a", term="Porosity"),
                make_search_result(url="https://x.com/b", term="Porosity Index"),
            ],
        )
        result, similar = await get_term(db, "Porosity", with_similar=True)
        assert result is not None
        assert result.term == "Porosity"
        assert all(candidate.url != result.url for candidate, _ in similar)

    async def test_with_similar_and_no_exact_match_still_returns_alternatives(
        self, db: Database
    ) -> None:
        """With no exact match, `with_similar=True` still returns `(None, similar)`."""
        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Porosity")])
        result, similar = await get_term(db, "Poros", with_similar=True)
        assert result is None
        assert len(similar) >= 1


@pytest.mark.anyio
class TestGetTermDefinitions:
    async def test_returns_every_definition_ordered_by_topic(self, db: Database) -> None:
        """Returns every stored definition for a term, ordered by topic."""
        await upsert_results(
            db,
            [
                make_search_result(url="https://x.com/a", topic="Geology"),
                make_search_result(url="https://x.com/a", topic="Drilling"),
            ],
        )
        results = await get_term_definitions(db, "https://x.com/a")
        assert [r.topic for r in results] == ["Drilling", "Geology"]

    async def test_returns_empty_list_when_not_found(self, db: Database) -> None:
        """Returns an empty list, not an error, when nothing matches."""
        assert await get_term_definitions(db, "Nonexistent") == []


@pytest.mark.anyio
class TestGetRandomTerm:
    async def test_returns_none_for_empty_database(self, db: Database) -> None:
        """Returns `None` when the local database has no terms at all."""
        assert await get_random_term(db) is None

    async def test_returns_a_stored_term(self, db: Database) -> None:
        """Returns some stored term when the database is not empty."""
        await upsert_results(db, make_search_results(3))
        result = await get_random_term(db)
        assert result is not None
        assert result.term in {f"Term {i}" for i in range(3)}

    async def test_respects_topic_filter(self, db: Database) -> None:
        """Restricts the pick to the given topic."""
        await upsert_results(
            db,
            [
                make_search_result(url="https://x.com/a", term="Alpha", topic="Geology"),
                make_search_result(url="https://x.com/b", term="Bravo", topic="Drilling"),
            ],
        )
        result = await get_random_term(db, topic="Geology")
        assert result is not None
        assert result.term == "Alpha"

    async def test_respects_exclude(self, db: Database) -> None:
        """Excluded terms are never picked."""
        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Alpha")])
        assert await get_random_term(db, exclude=["Alpha"]) is None


@pytest.mark.anyio
class TestGetTermsUrls:
    async def test_returns_matching_urls_without_query(self, db: Database) -> None:
        """With no `query`, returns URLs matching the other filters, ordered by term."""
        await upsert_results(
            db,
            [
                make_search_result(url="https://x.com/b", term="Bravo"),
                make_search_result(url="https://x.com/a", term="Alpha"),
            ],
        )
        urls = await get_terms_urls(db)
        assert urls == ["https://x.com/a", "https://x.com/b"]

    async def test_with_query_delegates_to_search(self, db: Database) -> None:
        """With `query` given, delegates to `search` and extracts each result's URL."""
        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Porosity")])
        urls = await get_terms_urls(db, query="porosity")
        assert urls == ["https://x.com/a"]

    async def test_excludes_results_with_no_url(self, db: Database) -> None:
        """Results with a falsy `url` never appear in the returned list."""
        # Can't upsert a no-url result (upsert_results skips it), so this
        # just documents that the final list comprehension filters falsy urls.
        await upsert_results(db, [make_search_result(url="https://x.com/a")])
        urls = await get_terms_urls(db)
        assert all(urls)


@pytest.mark.anyio
class TestGetTopics:
    async def test_returns_topic_to_term_count(self, db: Database) -> None:
        """Returns `{topic: term_count}` for every topic with at least one stored term."""
        await upsert_results(
            db,
            [
                make_search_result(url="https://x.com/a", topic="Geology"),
                make_search_result(url="https://x.com/b", topic="Geology"),
                make_search_result(url="https://x.com/c", topic="Drilling"),
            ],
        )
        assert await get_topics(db) == {"Geology": 2, "Drilling": 1}

    async def test_excludes_empty_topic(self, db: Database) -> None:
        """A topic-less definition (stored as `""`) is excluded from the result."""
        await upsert_results(db, [make_search_result(url="https://x.com/a", topic=None)])
        assert await get_topics(db) == {}

    async def test_respects_language_filter(self, db: Database) -> None:
        """`language` restricts the count to that language edition's terms."""
        await upsert_results(
            db,
            [
                make_search_result(url="https://x.com/a", topic="Geology", language="en"),
                make_search_result(url="https://x.com/b", topic="Geologia", language="es"),
            ],
        )
        assert await get_topics(db, language="en") == {"Geology": 1}


@pytest.mark.anyio
class TestCount:
    async def test_returns_zero_for_empty_database(self, db: Database) -> None:
        """Returns `0` for a freshly created database."""
        assert await count(db) == 0

    async def test_returns_total_row_count(self, db: Database) -> None:
        """Returns the total number of stored rows."""
        await upsert_results(db, make_search_results(4))
        assert await count(db) == 4
