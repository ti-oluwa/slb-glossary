"""
`build_fts_query` and `lexical_search`'s two-tier (exact/prefix, then bm25) ranking.
"""

import pytest

from slb_glossary.constants import constants
from slb_glossary.local.api import upsert_results
from slb_glossary.local.lexical import build_fts_query, lexical_search
from tests.factories import make_search_result

pytestmark = pytest.mark.unit


class TestBuildFtsQuery:
    def test_single_token_becomes_a_quoted_prefix_match(self):
        """A single token becomes `"token"*`."""
        assert build_fts_query("poros") == '"poros"*'

    def test_multiple_tokens_are_anded_together(self):
        """Multiple tokens are quoted, prefix-matched, and ANDed."""
        assert build_fts_query("drilling fluid") == '"drilling"* AND "fluid"*'

    def test_empty_query_returns_empty_quoted_string(self):
        """An empty (or whitespace-only) query returns `'""'`, matching nothing."""
        assert build_fts_query("") == '""'
        assert build_fts_query("   ") == '""'

    def test_punctuation_in_a_token_is_quoted_safely(self):
        """Punctuation within a token doesn't break out of its quotes."""
        result = build_fts_query("don't")
        assert result.startswith('"') and result.endswith("*")

    def test_literal_double_quote_is_escaped_by_doubling(self):
        """A literal `"` inside a token is escaped as `""`, not left to close the quote early."""
        assert build_fts_query('foo"bar') == '"foo""bar"*'

    def test_multiple_literal_double_quotes_are_each_doubled(self):
        """Every `"` in a token is doubled, not just the first."""
        assert build_fts_query('a"b"c') == '"a""b""c"*'

    @pytest.mark.parametrize("operator", ["OR", "NOT", "NEAR", "or", "not", "near"])
    def test_fts5_operators_are_quoted_as_literal_tokens(self, operator: str):
        """
        `OR`/`NOT`/`NEAR` (any case) are quoted like any other token, not
        left bare where FTS5 would parse them as boolean/proximity operators.
        """
        result = build_fts_query(f"foo {operator} bar")
        assert result == f'"foo"* AND "{operator}"* AND "bar"*'

    def test_parentheses_are_quoted_safely(self):
        """FTS5 grouping parens inside a token don't break out of its quotes."""
        assert build_fts_query("(foo)") == '"(foo)"*'

    @pytest.mark.parametrize("token", ["*", "foo*", "*foo", "foo*bar"])
    def test_wildcard_characters_are_quoted_within_the_token(self, token: str):
        """A literal `*` inside a token stays inside the quotes, not appended as a live prefix marker."""
        result = build_fts_query(token)
        assert result == f'"{token}"*'
        # Exactly one trailing, unquoted `*` - the prefix marker this
        # function itself adds - not one contributed by the input.
        assert result.endswith('"*')
        assert not result.endswith("**")

    @pytest.mark.parametrize("query", ["café", "naïve", "北京", "पानी", "🔥drill"])
    def test_unicode_input_is_quoted_like_any_other_token(self, query: str):
        """Non-ASCII input is quoted the same way ASCII input is, not rejected or mangled."""
        assert build_fts_query(query) == f'"{query}"*'


@pytest.mark.anyio
class TestLexicalSearch:
    async def test_exact_term_match_scores_exact_match_score(self, db):
        """An exact (case/whitespace-insensitive) term match scores `constants.exact_match_score`."""
        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Porosity")])
        [(result, score)] = await lexical_search(db, "porosity")
        assert result.term == "Porosity"
        assert score == constants.exact_match_score

    async def test_prefix_term_match_scores_prefix_match_score(self, db):
        """A term starting with the query (but not exact) scores `constants.prefix_match_score`."""
        await upsert_results(
            db, [make_search_result(url="https://x.com/a", term="Porosity Index")]
        )
        [(result, score)] = await lexical_search(db, "porosity")
        assert result.term == "Porosity Index"
        assert score == constants.prefix_match_score

    async def test_content_only_match_scores_below_content_match_score_cap(self, db):
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

    async def test_exact_match_ranks_ahead_of_content_only_match(self, db):
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

    async def test_natural_language_query_is_cleaned_before_matching(self, db):
        """`"what is porosity"` finds `"Porosity"` via `clean_query`'s stripping."""
        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Porosity")])
        results = await lexical_search(db, "what is porosity")
        assert any(r.term == "Porosity" for r, _ in results)

    async def test_no_match_returns_empty_list(self, db):
        """A query matching nothing returns an empty list, not an error."""
        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Porosity")])
        assert await lexical_search(db, "zzz_no_such_term_zzz") == []

    async def test_results_ordered_best_first(self, db):
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

    async def test_respects_limit(self, db):
        """`limit` caps the number of results."""
        await upsert_results(
            db,
            [make_search_result(url=f"https://x.com/{i}", term=f"Porosity {i}") for i in range(5)],
        )
        results = await lexical_search(db, "porosity", limit=2)
        assert len(results) == 2

    async def test_respects_topic_filter(self, db):
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

    async def test_respects_start_letter_filter(self, db):
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

    async def test_respects_language_filter(self, db):
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

    async def test_respects_exclude(self, db):
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

    async def test_fuzzy_topic_resolves_against_stored_topics(self, db):
        """`fuzzy=True` resolves a misspelled `topic` against stored topic names."""
        await upsert_results(
            db, [make_search_result(url="https://x.com/a", term="Porosity", topic="Geology")]
        )
        results = await lexical_search(db, "porosity", topic="geologyy", fuzzy=True)
        assert [r.term for r, _ in results] == ["Porosity"]

    async def test_literal_double_quote_in_query_does_not_raise(self, db):
        """A literal `"` in the query text reaches SQLite as safely-quoted, not a syntax error."""
        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Porosity")])
        results = await lexical_search(db, 'poros"ity')
        assert results == []

    @pytest.mark.parametrize("query", ["foo OR bar", "foo or bar"])
    async def test_or_in_query_is_literal_text_not_a_boolean_operator(self, db, query: str):
        """
        `OR` in the query text is ANDed as a literal token like any other,
        never interpreted as FTS5's boolean `OR`.

        If `OR` were left unquoted, this query would match either
        "foo"-containing or "bar"-containing rows (a boolean union). Two
        rows exist, one matching each half, and neither contains the
        literal word "or" - so a correct, literal-text implementation
        matches neither.
        """
        await upsert_results(
            db,
            [
                make_search_result(url="https://x.com/a", term="Foo Term", definition="foo"),
                make_search_result(url="https://x.com/b", term="Bar Term", definition="bar"),
            ],
        )
        results = await lexical_search(db, query)
        assert results == []

    async def test_not_in_query_is_literal_text_not_a_unary_operator(self, db):
        """
        `NOT` in the query text is ANDed as a literal token, never
        interpreted as FTS5's unary `NOT`.

        `NOT drilling`, if `NOT` were left unquoted, would be a syntax
        error (FTS5's `NOT` needs a left-hand operand) or, parsed some
        other way, could match rows that *don't* mention "drilling". A
        literal-text implementation just looks for both "not" and
        "drilling" as tokens, which matches nothing here.
        """
        await upsert_results(
            db, [make_search_result(url="https://x.com/a", term="Mud", definition="drilling")]
        )
        results = await lexical_search(db, "NOT drilling")
        assert results == []

    async def test_near_in_query_is_literal_text_not_a_proximity_operator(self, db):
        """
        `NEAR` in the query text doesn't trigger FTS5's `NEAR(...)`
        proximity syntax (which additionally requires parentheses this
        query doesn't supply, and would otherwise raise a syntax error).
        """
        await upsert_results(
            db, [make_search_result(url="https://x.com/a", term="Porosity", definition="rock")]
        )
        results = await lexical_search(db, "porosity NEAR rock")
        assert results == []

    @pytest.mark.parametrize("query", ["*", "foo*bar", "(foo", "foo)", "foo*bar OR (baz"])
    async def test_syntax_looking_queries_execute_without_raising(self, db, query: str):
        """
        Wildcards, unbalanced parens, and operator/wildcard combinations
        never reach SQLite as anything but a safely-quoted literal, so
        none of them should raise - whether or not anything matches.
        """
        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Porosity")])
        results = await lexical_search(db, query)
        assert isinstance(results, list)

    @pytest.mark.parametrize("query", ["café", "naïve", "北京", "पानी", "🔥drill"])
    async def test_unicode_query_executes_without_raising(self, db, query: str):
        """Non-ASCII query text reaches SQLite fine and returns a (possibly empty) list."""
        await upsert_results(db, [make_search_result(url="https://x.com/a", term="Porosity")])
        results = await lexical_search(db, query)
        assert isinstance(results, list)

    async def test_unicode_query_matches_stored_unicode_term(self, db):
        """A Unicode query still matches a stored term containing the same text."""
        await upsert_results(
            db, [make_search_result(url="https://x.com/a", term="Porosidad", definition="café")]
        )
        results = await lexical_search(db, "café")
        assert any(r.term == "Porosidad" for r, _ in results)
