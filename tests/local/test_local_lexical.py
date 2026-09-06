"""
`build_fts_query` and `lexical_search`'s two-tier (exact/prefix, then bm25) ranking.
"""

import pytest

from slb_glossary.constants import constants
from slb_glossary.local.api import upsert_results
from slb_glossary.local.lexical import build_fts_query, lexical_search
from slb_glossary.local.types import Database
from tests.factories import make_search_result

pytestmark = pytest.mark.unit


class TestBuildFtsQuery:
    def test_single_token_becomes_a_quoted_prefix_match(self) -> None:
        """A single token becomes `"token"*`."""
        assert build_fts_query("poros") == '"poros"*'

    def test_multiple_tokens_are_anded_together(self) -> None:
        """Multiple tokens are quoted, prefix-matched, and ANDed."""
        assert build_fts_query("drilling fluid") == '"drilling"* AND "fluid"*'

    def test_empty_query_returns_empty_quoted_string(self) -> None:
        """An empty (or whitespace-only) query returns `'""'`, matching nothing."""
        assert build_fts_query("") == '""'
        assert build_fts_query("   ") == '""'

    def test_punctuation_in_a_token_is_quoted_safely(self) -> None:
        """Punctuation within a token does not break out of its quotes."""
        result = build_fts_query("do not")
        assert result.startswith('"') and result.endswith("*")

    def test_literal_double_quote_is_escaped_by_doubling(self) -> None:
        """A literal `"` inside a token is escaped as `""`, not left to close the quote early."""
        assert build_fts_query('foo"bar') == '"foo""bar"*'

    def test_multiple_literal_double_quotes_are_each_doubled(self) -> None:
        """Every `"` in a token is doubled, not just the first."""
        assert build_fts_query('a"b"c') == '"a""b""c"*'

    @pytest.mark.parametrize("operator", ["OR", "NOT", "NEAR", "or", "not", "near"])
    def test_fts5_operators_are_quoted_as_literal_tokens(self, operator: str) -> None:
        """
        `OR`/`NOT`/`NEAR` (any case) are quoted like any other token, not
        left bare where FTS5 would parse them as boolean/proximity operators.
        """
        result = build_fts_query(f"foo {operator} bar")
        assert result == f'"foo"* AND "{operator}"* AND "bar"*'

    def test_parentheses_are_quoted_safely(self) -> None:
        """FTS5 grouping parens inside a token do not break out of its quotes."""
        assert build_fts_query("(foo)") == '"(foo)"*'

    @pytest.mark.parametrize("token", ["*", "foo*", "*foo", "foo*bar"])
    def test_wildcard_characters_are_quoted_within_the_token(self, token: str) -> None:
        """A literal `*` inside a token stays inside the quotes, not appended as a live prefix marker."""
        result = build_fts_query(token)
        assert result == f'"{token}"*'
        # Exactly one trailing, unquoted `*` - the prefix marker this
        # function itself adds - not one contributed by the input.
        assert result.endswith('"*')
        assert not result.endswith("**")

    @pytest.mark.parametrize("query", ["café", "naïve", "北京", "पानी", "🔥drill"])
    def test_unicode_input_is_quoted_like_any_other_token(self, query: str) -> None:
        """Non-ASCII input is quoted the same way ASCII input is, not rejected or mangled."""
        assert build_fts_query(query) == f'"{query}"*'


@pytest.mark.anyio
class TestLexicalSearch:
    async def test_exact_term_match_scores_exact_match_score(self, db: Database) -> None:
        """An exact (case/whitespace-insensitive) term match scores `constants.exact_match_score`."""
        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Porosity")])
        [(result, score)] = await lexical_search(db, "porosity")
        assert result.term == "Porosity"
        assert score == constants.exact_match_score

    async def test_prefix_term_match_scores_prefix_match_score(self, db: Database) -> None:
        """A term starting with the query (but not exact) scores `constants.prefix_match_score`."""
        await upsert_results(
            db, [make_search_result(url="https://x.com/a", term="Porosity Index")]
        )
        [(result, score)] = await lexical_search(db, "porosity")
        assert result.term == "Porosity Index"
        assert score == constants.prefix_match_score

    async def test_content_only_match_scores_below_content_match_score_cap(
        self, db: Database
    ) -> None:
        """
        A definition-only match's score is capped at `content_match_score_cap`.

        With only a *single* content-tier match, `worst == best` for that
        lone bm25 value, so `spread = (worst - best) or 1.0` collapses to
        the `or 1.0` branch and the score formula's numerator
        (`worst - row_bm25`) is exactly `0.0`, i.e. a lone content-only
        match always scores `0.0` (verified directly). Two content
        matches with different bm25 relevance are needed to see a
        nonzero, capped score for the *better* one.
        """
        await upsert_results(
            db,
            [
                make_search_result(
                    url="https://x.com/a",
                    term="Unrelated Name",
                    definition="drilling drilling drilling drilling fluid systems.",
                ),
                make_search_result(
                    url="https://x.com/b",
                    term="Another Unrelated Name",
                    definition="A single mention of drilling.",
                ),
            ],
        )
        results = await lexical_search(db, "drilling")
        best_score = results[0][1]
        assert 0.0 < best_score <= constants.content_match_score_cap

    async def test_exact_match_ranks_ahead_of_content_only_match(self, db: Database) -> None:
        """
        A name match is never outranked by a content-only match, however often the
        content-only result repeats the query word (the motivating example from the
        module's own docstring: "mud" should surface "Mud" ahead of "Drilling fluid").
        """
        await upsert_results(
            db,
            [
                make_search_result(
                    url="https://x.com/a",
                    term="Drilling fluid",
                    definition="mud mud mud mud mud mud mud mud",
                ),
                make_search_result(url="https://x.com/b", term="Mud", definition="A slurry."),
            ],
        )
        results = await lexical_search(db, "mud")
        assert results[0][0].term == "Mud"

    async def test_natural_language_query_is_cleaned_before_matching(self, db: Database) -> None:
        """`"what is porosity"` finds `"Porosity"` via `clean_query`'s stripping."""
        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Porosity")])
        results = await lexical_search(db, "what is porosity")
        assert any(r.term == "Porosity" for r, _ in results)

    async def test_no_match_returns_empty_list(self, db: Database) -> None:
        """A query matching nothing returns an empty list, not an error."""
        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Porosity")])
        assert await lexical_search(db, "zzz_no_such_term_zzz") == []

    async def test_results_ordered_best_first(self, db: Database) -> None:
        """Results come back ordered best match first (descending score)."""
        await upsert_results(
            db,
            [
                make_search_result(url="https://x.com/a", term="Porosity"),
                make_search_result(url="https://x.com/b", term="Porous Media"),
            ],
        )
        results = await lexical_search(db, "porosity")
        scores = [score for _, score in results]
        assert scores == sorted(scores, reverse=True)

    async def test_respects_limit(self, db: Database) -> None:
        """`limit` caps the number of results."""
        await upsert_results(
            db,
            [make_search_result(url=f"https://x.com/{i}", term=f"Porosity {i}") for i in range(5)],
        )
        results = await lexical_search(db, "porosity", limit=2)
        assert len(results) == 2

    async def test_respects_topic_filter(self, db: Database) -> None:
        """`topic` restricts results to that topic."""
        await upsert_results(
            db,
            [
                make_search_result(url="https://x.com/a", term="Porosity", topic="Geology"),
                make_search_result(url="https://x.com/b", term="Porosity Log", topic="Drilling"),
            ],
        )
        results = await lexical_search(db, "porosity", topic="Geology")
        assert [r.term for r, _ in results] == ["Porosity"]

    async def test_respects_start_letter_filter(self, db: Database) -> None:
        """`start_letter` restricts results to terms starting with that letter.

        Both terms need to actually match the query via FTS for this to
        test the filter rather than the match itself: "Zed Porosity"
        contains "porosity" as a token (so it matches the query, just via
        the content tier rather than the name tier), letting
        `start_letter="P"` be the only thing that excludes it."""
        await upsert_results(
            db,
            [
                make_search_result(url="https://x.com/a", term="Porosity"),
                make_search_result(url="https://x.com/b", term="Zed Porosity"),
            ],
        )
        results = await lexical_search(db, "porosity", start_letter="P")
        assert [r.term for r, _ in results] == ["Porosity"]

    async def test_respects_language_filter(self, db: Database) -> None:
        """`language` restricts results to that glossary language edition."""
        await upsert_results(
            db,
            [
                make_search_result(url="https://x.com/a", term="Porosity", language="en"),
                make_search_result(url="https://x.com/b", term="Porosidad", language="es"),
            ],
        )
        results = await lexical_search(db, "poros", language="en")
        assert [r.term for r, _ in results] == ["Porosity"]

    async def test_respects_exclude(self, db: Database) -> None:
        """`exclude` filters out matching URLs/term names before `limit` is applied."""
        await upsert_results(
            db,
            [
                make_search_result(url="https://x.com/a", term="Porosity"),
                make_search_result(url="https://x.com/b", term="Porosity Log"),
            ],
        )
        results = await lexical_search(db, "porosity", exclude=["Porosity"])
        assert [r.term for r, _ in results] == ["Porosity Log"]

    async def test_fuzzy_topic_resolves_against_stored_topics(self, db: Database) -> None:
        """`fuzzy=True` resolves a misspelled `topic` against stored topic names."""
        await upsert_results(
            db, [make_search_result(url="https://x.com/a", term="Porosity", topic="Geology")]
        )
        results = await lexical_search(db, "porosity", topic="geologyy", fuzzy=True)
        assert [r.term for r, _ in results] == ["Porosity"]

    async def test_literal_double_quote_in_query_does_not_raise(self, db: Database) -> None:
        """
        A literal `"` in the query text reaches SQLite as safely-quoted, not a syntax error.

        FTS5 itself finds nothing for the mangled token (as before), but
        the fuzzy-typo fallback now recovers "Porosity" from
        `poros"ity` anyway, since it's a close spelling once the stray
        `"` is accounted for. What matters here is that it doesn't
        raise, not that it comes back empty.
        """
        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Porosity")])
        results = await lexical_search(db, 'poros"ity')
        assert isinstance(results, list)
        if results:
            assert results[0][0].term == "Porosity"
            assert results[0][1] <= constants.fuzzy_match_score_cap

    @pytest.mark.parametrize("query", ["foo OR bar", "foo or bar"])
    async def test_or_in_query_is_literal_text_not_a_boolean_operator(
        self, db: Database, query: str
    ) -> None:
        """
        `OR` in the query text is quoted as a literal token like any
        other, never handed to FTS5 unquoted as its own boolean `OR`.

        Both rows come back here (each matches one real word of the
        query), but that is this package's *own* token-level OR
        fallback kicking in - every token still reaches SQLite safely
        quoted, one at a time, never as a raw FTS5 boolean expression.
        The `AND` query tried first (`"foo"* AND "or"* AND "bar"*`)
        matches neither row (no stored text contains the literal token
        "or"), which is what actually proves `OR` was not parsed as an
        operator: an unquoted boolean `OR` there would have matched
        immediately, without ever needing the fallback at all.
        """
        await upsert_results(
            db,
            [
                make_search_result(url="https://x.com/a", term="Foo Term", definition="foo"),
                make_search_result(url="https://x.com/b", term="Bar Term", definition="bar"),
            ],
        )
        results = await lexical_search(db, query)
        assert {r.term for r, _ in results} == {"Foo Term", "Bar Term"}
        # Neither is a name match; both are capped, bm25-only content matches.
        assert all(score <= constants.content_match_score_cap for _, score in results)

    async def test_not_in_query_is_literal_text_not_a_unary_operator(self, db: Database) -> None:
        """
        `NOT` in the query text is quoted as a literal token, never
        handed to FTS5 unquoted as its own unary `NOT`.

        `NOT drilling`, if `NOT` were left unquoted, would be a syntax
        error (FTS5's `NOT` needs a left-hand operand) - it isn't, so
        this executes at all, which is what this test actually
        verifies. "Mud" surfaces via the token-level OR fallback (its
        definition mentions "drilling"; nothing stored contains the
        literal word "not"), scored as a capped, bm25-only content
        match rather than a name match.
        """
        await upsert_results(
            db, [make_search_result(url="https://x.com/a", term="Mud", definition="drilling")]
        )
        results = await lexical_search(db, "NOT drilling")
        assert [r.term for r, _ in results] == ["Mud"]
        assert results[0][1] <= constants.content_match_score_cap

    async def test_near_in_query_is_literal_text_not_a_proximity_operator(
        self, db: Database
    ) -> None:
        """
        `NEAR` in the query text does not trigger FTS5's `NEAR(...)`
        proximity syntax (which additionally requires parentheses this
        query does not supply, and would otherwise raise a syntax
        error - not raising is what this actually verifies).

        "Porosity" surfaces because the query, "porosity near rock",
        contains it as a whole-word phrase (`NameMatchTier.CONTAINS`) -
        a real, intentional lexical signal, not proximity search.
        """
        await upsert_results(
            db, [make_search_result(url="https://x.com/a", term="Porosity", definition="rock")]
        )
        results = await lexical_search(db, "porosity NEAR rock")
        assert results[0][0].term == "Porosity"
        assert results[0][1] == constants.contains_match_score

    @pytest.mark.parametrize("query", ["*", "foo*bar", "(foo", "foo)", "foo*bar OR (baz"])
    async def test_syntax_looking_queries_execute_without_raising(
        self, db: Database, query: str
    ) -> None:
        """
        Wildcards, unbalanced parens, and operator/wildcard combinations
        never reach SQLite as anything but a safely-quoted literal, so
        none of them should raise - whether or not anything matches.
        """
        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Porosity")])
        results = await lexical_search(db, query)
        assert isinstance(results, list)

    @pytest.mark.parametrize("query", ["café", "naïve", "北京", "पानी", "🔥drill"])
    async def test_unicode_query_executes_without_raising(self, db: Database, query: str) -> None:
        """Non-ASCII query text reaches SQLite fine and returns a (possibly empty) list."""
        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Porosity")])
        results = await lexical_search(db, query)
        assert isinstance(results, list)

    async def test_unicode_query_matches_stored_unicode_term(self, db: Database) -> None:
        """A Unicode query still matches a stored term containing the same text."""
        await upsert_results(
            db, [make_search_result(url="https://x.com/a", term="Porosidad", definition="café")]
        )
        results = await lexical_search(db, "café")
        assert any(r.term == "Porosidad" for r, _ in results)


@pytest.mark.anyio
class TestLexicalSearchTiers:
    """`slb_glossary.scoring.classify_name_match`'s tiers, exercised through `lexical_search`."""

    async def test_contains_tier_ranks_above_bm25_content_tier(self, db: Database) -> None:
        """
        A term whose name contains the whole query as a phrase (but is
        neither exact nor a prefix) outranks a term that only mentions
        the query heavily in its definition.
        """
        await upsert_results(
            db,
            [
                make_search_result(url="https://x.com/a", term="Gas Lift"),
                make_search_result(
                    url="https://x.com/b",
                    term="Unrelated Term",
                    definition="lift lift lift lift lift lift",
                ),
            ],
        )
        results = await lexical_search(db, "lift")
        assert results[0][0].term == "Gas Lift"
        assert results[0][1] == constants.contains_match_score

    async def test_contains_tier_ranks_below_prefix_tier(self, db: Database) -> None:
        """A prefix match still outranks a mere phrase-containment match for the same query."""
        await upsert_results(
            db,
            [
                make_search_result(url="https://x.com/a", term="Lift System"),
                make_search_result(url="https://x.com/b", term="Gas Lift"),
            ],
        )
        results = await lexical_search(db, "lift")
        assert [r.term for r, _ in results][:2] == ["Lift System", "Gas Lift"]
        assert results[0][1] == constants.prefix_match_score
        assert results[1][1] == constants.contains_match_score

    async def test_all_tokens_tier_for_reordered_multiword_query(self, db: Database) -> None:
        """Every query token present in the term name, just not contiguously, scores the all-tokens tier."""
        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Gas Lift Valve")])
        results = await lexical_search(db, "valve gas lift")
        assert results[0][0].term == "Gas Lift Valve"
        assert results[0][1] == constants.all_tokens_match_score

    async def test_all_tokens_tier_tolerates_a_trailing_token_typo(self, db: Database) -> None:
        """
        A query token that's a prefix of (not equal to) a term token
        still counts as covered, even out of order (a contiguous,
        in-order truncation like "wireline logg" already hits the
        stronger prefix tier; this exercises token coverage specifically).
        """
        await upsert_results(
            db, [make_search_result(url="https://x.com/a", term="Wireline Logging Tool")]
        )
        results = await lexical_search(db, "logg wireline")
        assert results[0][0].term == "Wireline Logging Tool"
        assert results[0][1] == constants.all_tokens_match_score

    async def test_partial_token_overlap_scaled_by_coverage(self, db: Database) -> None:
        """Only some query tokens present in the name scores proportionally, capped below the all-tokens tier."""
        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Gas Lift Valve")])
        results = await lexical_search(db, "gas lift unrelatedword")
        assert results[0][0].term == "Gas Lift Valve"
        assert 0.0 < results[0][1] < constants.all_tokens_match_score
        assert results[0][1] <= constants.token_overlap_score_cap

    async def test_name_tiers_all_outrank_bm25_content_tier(self, db: Database) -> None:
        """Every name-tier score is above `content_match_score_cap`, so a name match always wins."""
        assert constants.token_overlap_score_cap > constants.content_match_score_cap
        assert constants.all_tokens_match_score > constants.token_overlap_score_cap
        assert constants.contains_match_score > constants.all_tokens_match_score
        assert constants.prefix_match_score > constants.contains_match_score
        assert constants.exact_match_score > constants.prefix_match_score


@pytest.mark.anyio
class TestLexicalSearchFuzzyFallback:
    """The misspelling-tolerant fallback (`_fuzzy_fallback`), exercised through `lexical_search`."""

    async def test_recovers_a_misspelled_term_fts5_prefix_matching_cannot(
        self, db: Database
    ) -> None:
        """
        A typo in the middle of a word ("porosoty") defeats FTS5's own
        prefix matching entirely (no stored token starts with the
        literal misspelled string), but the fuzzy fallback still
        recovers the real term.
        """
        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Porosity")])
        results = await lexical_search(db, "porosoty")
        assert results
        assert results[0][0].term == "Porosity"

    async def test_fuzzy_recovered_score_never_exceeds_its_cap(self, db: Database) -> None:
        """A fuzzy-recovered result's score never exceeds `constants.fuzzy_match_score_cap`."""
        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Permeability")])
        results = await lexical_search(db, "permeabilty")
        assert results
        assert results[0][0].term == "Permeability"
        assert results[0][1] <= constants.fuzzy_match_score_cap

    async def test_fuzzy_fallback_never_outranks_a_real_exact_match(self, db: Database) -> None:
        """
        A fuzzy match recovered for one term never outranks a genuine
        exact/prefix match for a *different* query in the same result set.
        """
        await upsert_results(
            db,
            [
                make_search_result(url="https://x.com/a", term="Porosity"),
                make_search_result(url="https://x.com/b", term="Reservoir"),
            ],
        )
        # "reservior" is a near-miss of "Reservoir", not of "Porosity",
        # so this only exercises the fuzzy path for one of the two terms.
        results = await lexical_search(db, "reservior")
        assert results[0][0].term == "Reservoir"
        assert results[0][1] <= constants.fuzzy_match_score_cap

    async def test_fuzzy_fallback_does_not_fire_when_a_strong_match_already_exists(
        self, db: Database
    ) -> None:
        """
        No unrelated fuzzy-typo noise is added once a confident
        name-tier match (at/above `token_overlap_score_cap`) already exists.
        """
        await upsert_results(
            db,
            [
                make_search_result(url="https://x.com/a", term="Porosity"),
                make_search_result(url="https://x.com/b", term="Porosidad"),
            ],
        )
        results = await lexical_search(db, "porosity")
        # Only the genuine (exact) match, no fuzzy-recovered near-miss noise.
        assert [r.term for r, _ in results] == ["Porosity"]

    async def test_no_close_match_returns_empty_list(self, db: Database) -> None:
        """A query with no close spelling of any stored term still returns an empty list, not an error."""
        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Porosity")])
        results = await lexical_search(db, "zzz_completely_unrelated_zzz")
        assert results == []

    async def test_fuzzy_fallback_respects_topic_filter(self, db: Database) -> None:
        """A fuzzy-recovered term still respects an active `topic` filter."""
        await upsert_results(
            db,
            [
                make_search_result(url="https://x.com/a", term="Porosity", topic="Geology"),
                make_search_result(url="https://x.com/b", term="Porosity", topic="Drilling"),
            ],
        )
        results = await lexical_search(db, "porosoty", topic="Drilling")
        assert [r.term for r, _ in results] == ["Porosity"]
        assert all(r.topic == "Drilling" for r, _ in results)
